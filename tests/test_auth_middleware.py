"""Tests for app.auth.middleware.

Per ticket #4 (M1.T2), the auth middleware reads the ``cg_session``
cookie on every request, decodes the JWT, and populates
``request.state.user`` with the matching ``users`` row (or leaves it
as ``None`` if the cookie is absent/invalid/expired).

The middleware **must never crash** on a missing or malformed cookie —
that would 500 every unauthenticated visitor and break the public
``/login`` route. These tests pin that contract.

Seam: a minimal FastAPI app that mounts the middleware plus a single
``/whoami`` test route that echoes back ``request.state.user`` as JSON.
This keeps the test isolated from the real ``app/main.py`` (which
mounts templates + static) and from any future route additions
(M1.T4 will add /login + /logout — those tests live elsewhere).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.middleware import AuthMiddleware
from app.auth.jwt_utils import encode_jwt
from app.auth.passwords import hash_pw
from app.db import get_connection, init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """An isolated SQLite file with the full schema applied."""
    db = tmp_path / "test.db"
    init_db(db)
    return db


@pytest.fixture
def user_id(db_path: Path) -> int:
    """Insert a real ``users`` row via the documented seam and return
    its id. Uses ``app.auth.passwords.hash_pw`` so the row is exactly
    what the future ``create-user`` CLI will produce.
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            ("franco@froto.online", hash_pw("senha123"), "Franco"),
        )
        uid = cur.lastrowid
    assert uid is not None
    return uid


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """A TestClient bound to a minimal FastAPI app that mounts the
    auth middleware and exposes a single ``/whoami`` route that
    reports ``request.state.user``.

    The middleware reads the DB at request time; we point it at the
    test DB by patching ``app.auth.middleware.DB_PATH`` before each
    request via FastAPI's dependency-override machinery — see below.
    """
    from app.auth import middleware as mw_mod

    # Point the middleware at our tmp DB for the lifetime of the test.
    original_db_path = mw_mod.DB_PATH
    mw_mod.DB_PATH = db_path
    try:
        test_app = FastAPI()
        test_app.add_middleware(AuthMiddleware)

        @test_app.get("/whoami")
        async def whoami(request: Request) -> dict:
            user = request.state.user
            if user is None:
                return {"user": None}
            return {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "display_name": user.display_name,
                }
            }

        with TestClient(test_app) as c:
            yield c
    finally:
        mw_mod.DB_PATH = original_db_path


# ---------------------------------------------------------------------------
# No cookie → request.state.user is None
# ---------------------------------------------------------------------------


def test_no_cookie_yields_no_user(client: TestClient) -> None:
    """GET /whoami with no cookie at all must return ``{"user": None}``.

    This is the "unauthenticated visitor" path — it must never crash,
    even on the public login page.
    """
    response = client.get("/whoami")
    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_empty_cookie_yields_no_user(client: TestClient) -> None:
    """A request carrying an empty ``cg_session`` cookie must also
    return ``user: None`` (the cookie is set but empty)."""
    response = client.get("/whoami", cookies={"cg_session": ""})
    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_garbage_cookie_yields_no_user(client: TestClient) -> None:
    """A request with a malformed ``cg_session`` cookie must not 500.

    The middleware must swallow decode errors and leave ``user``
    as ``None`` so the request continues to the route handler.
    """
    response = client.get("/whoami", cookies={"cg_session": "not-a-jwt"})
    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_expired_cookie_yields_no_user(
    client: TestClient, user_id: int
) -> None:
    """An expired JWT (in the past) must yield ``user: None``, not a 500."""
    token = encode_jwt(user_id, expires_in_seconds=-1)
    response = client.get("/whoami", cookies={"cg_session": token})
    assert response.status_code == 200
    assert response.json() == {"user": None}


# ---------------------------------------------------------------------------
# Valid cookie → request.state.user is the User row from DB
# ---------------------------------------------------------------------------


def test_valid_cookie_populates_user(
    client: TestClient, user_id: int, db_path: Path
) -> None:
    """A valid JWT carrying an existing user id must populate
    ``request.state.user`` with the corresponding ``users`` row.

    The test compares ``user.id`` and ``user.email`` against the
    values inserted by the fixture — independent of the
    middleware's internal lookup code.
    """
    token = encode_jwt(user_id)
    response = client.get("/whoami", cookies={"cg_session": token})
    assert response.status_code == 200
    body = response.json()
    assert body["user"] is not None
    assert body["user"]["id"] == user_id
    assert body["user"]["email"] == "franco@froto.online"
    assert body["user"]["display_name"] == "Franco"


def test_valid_cookie_with_unknown_user_id_yields_no_user(
    client: TestClient, db_path: Path
) -> None:
    """A valid JWT carrying a user id that doesn't exist in the DB
    must yield ``user: None`` (the middleware looks up by id, and
    the lookup is the gate — don't trust the JWT alone)."""
    token = encode_jwt(999_999)  # No user with this id
    response = client.get("/whoami", cookies={"cg_session": token})
    assert response.status_code == 200
    assert response.json() == {"user": None}


# ---------------------------------------------------------------------------
# Cookie name + security attributes
# ---------------------------------------------------------------------------


def test_middleware_reads_cg_session_cookie_name(
    client: TestClient, user_id: int
) -> None:
    """The middleware must read the cookie name ``cg_session``
    specifically — not the FastAPI default ``session``.

    Locking the name down in the test makes the cookie contract
    explicit so the future login route can set the right name.
    """
    # A valid token under the wrong cookie name must be ignored.
    token = encode_jwt(user_id)
    response = client.get("/whoami", cookies={"session": token})
    assert response.status_code == 200
    assert response.json() == {"user": None}

    # Same token under the right name works.
    response = client.get("/whoami", cookies={"cg_session": token})
    assert response.status_code == 200
    assert response.json()["user"]["id"] == user_id