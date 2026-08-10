"""Tests for /login + /logout routes (M1.T4).

Acceptance criteria from ticket #6:

- GET /login → 200 + HTML form (email + password fields,
  action='/login' method='POST')
- POST /login with valid email+password → 302 redirect to /roadmap
  + Set-Cookie header with cg_session JWT
- POST /login with WRONG password → 200 (NOT 302) + error message
  + NO Set-Cookie
- POST /login with unknown email → 200 + SAME error message
  (don't leak which is wrong — security-guardian pitfall)
- POST /logout → 302 redirect to /login + clears cookie
  (Set-Cookie with max-age=0 or empty value)
- Cookie attributes on Set-Cookie: HttpOnly, SameSite=lax,
  Max-Age=30d (or Expires), Secure (in prod)

These tests use the real ``app.main:app`` (full FastAPI app),
patching the DB path to a tmp file so the real
``data/code_gym.db`` is never touched. The cookie contract is
pinned via the constants exported by ``app.auth.middleware`` —
same source the route uses to set the cookie, so the test can
read what the route set without string drift.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.middleware import (
    COOKIE_HTTPONLY,
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
)
from app.db import init_db
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh SQLite file for each test, wired as the app's DB.

    We patch the module-level ``DEFAULT_DB_PATH`` in every module
    that captured it at import time — ``app.db`` (the source of
    truth) and ``app.auth.routes`` (which does the user lookup
    on POST /login). Mirrors the pattern in ``test_roadmap.py``.
    """
    p = tmp_path / "code_gym.db"
    init_db(p)
    monkeypatch.setattr("app.db.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.auth.routes.DEFAULT_DB_PATH", p)
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient bound to the real FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def seeded_user(db_path: Path) -> dict:
    """Insert a real ``users`` row with a bcrypt-hashed password.

    Returns the dict ``{id, email, password, display_name}`` so each
    test can reference the password it used to seed the row. The
    password is intentionally NOT the same as the email — the wrong
    password tests need a distinct value to send.
    """
    from app.auth.passwords import hash_pw

    email = "franco@froto.online"
    password = "senha-correta-123"
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            (email, hash_pw(password), "Franco"),
        )
        conn.commit()
        user_id = int(cur.lastrowid)
    return {
        "id": user_id,
        "email": email,
        "password": password,
        "display_name": "Franco",
    }


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


def test_get_login_renders_form(client: TestClient) -> None:
    """GET /login must return 200 with an HTML form.

    The form must POST to /login (not GET — credentials never go
    on the URL) and must have email + password inputs. The exact
    field ``name`` attributes are pinned so future template drift
    is caught.
    """
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text

    # Form structure — action, method, fields.
    assert 'action="/login"' in body, "form action must be /login"
    assert 'method="post"' in body, "form must POST credentials"
    assert 'name="email"' in body, "missing email field"
    assert 'name="password"' in body, "missing password field"
    # No "remember me" — YAGNI per ticket constraints.
    assert "remember" not in body.lower(), (
        "login form must not include 'remember me' (YAGNI)"
    )


def test_get_login_does_not_set_session_cookie(client: TestClient) -> None:
    """A GET /login is the login page, not a login attempt. No
    session cookie should be set in response."""
    response = client.get("/login")
    assert response.status_code == 200
    # The Set-Cookie header on the login page render should not
    # carry our session cookie (the page is anonymous).
    set_cookie = response.headers.get("set-cookie", "")
    assert COOKIE_NAME not in set_cookie, (
        f"GET /login must not set {COOKIE_NAME!r} cookie, "
        f"got: {set_cookie!r}"
    )


# ---------------------------------------------------------------------------
# POST /login — happy path
# ---------------------------------------------------------------------------


def test_post_login_with_valid_credentials_redirects_and_sets_cookie(
    client: TestClient, seeded_user: dict
) -> None:
    """POST /login with the right email + password must:

    * redirect to /roadmap (302)
    * set the cg_session cookie (with HttpOnly + SameSite=lax +
      Max-Age=30d attributes; Secure only in prod)
    """
    response = client.post(
        "/login",
        data={"email": seeded_user["email"], "password": seeded_user["password"]},
        follow_redirects=False,
    )

    assert response.status_code == 302, (
        f"expected 302 redirect, got {response.status_code}: {response.text!r}"
    )
    location = response.headers.get("location", "")
    assert location.endswith("/roadmap"), f"redirect target: {location!r}"

    # Set-Cookie assertions. The header is the raw combined string
    # when multiple Set-Cookie headers exist; Starlette joins with
    # ``,`` which can fight with Expires commas. To be safe, we
    # match on substring presence + parse a single Set-Cookie value.
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie, "POST /login must set the session cookie"

    # Cookie value must be present and non-empty.
    assert COOKIE_NAME in set_cookie
    # HttpOnly must be set (security-guardian: no JS access).
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower(), (
        f"cookie missing HttpOnly: {set_cookie!r}"
    )
    # SameSite must be lax.
    assert f"SameSite={COOKIE_SAMESITE}" in set_cookie or (
        COOKIE_SAMESITE.lower() in set_cookie.lower()
    ), f"cookie missing SameSite={COOKIE_SAMESITE!r}: {set_cookie!r}"
    # Max-Age must be the pinned 30-day value (or Expires fallback).
    assert f"Max-Age={COOKIE_MAX_AGE}" in set_cookie or "Expires=" in set_cookie, (
        f"cookie missing Max-Age={COOKIE_MAX_AGE}: {set_cookie!r}"
    )
    # Secure attribute mirrors COOKIE_SECURE (off in dev, on in prod).
    if COOKIE_SECURE:
        assert "Secure" in set_cookie, (
            f"COOKIE_SECURE=True but cookie missing Secure: {set_cookie!r}"
        )
    else:
        # In dev we explicitly do NOT send Secure — leaving it
        # would break local http://localhost testing.
        assert "Secure" not in set_cookie, (
            f"COOKIE_SECURE=False but cookie set Secure: {set_cookie!r}"
        )


def test_post_login_cookie_value_is_a_valid_jwt_for_the_user(
    client: TestClient, seeded_user: dict
) -> None:
    """The cookie value must be a JWT whose ``sub`` claim matches
    the seeded user's id. This pins the full pipeline: bcrypt
    verification → encode_jwt → cookie."""
    from app.auth.jwt_utils import decode_jwt

    response = client.post(
        "/login",
        data={"email": seeded_user["email"], "password": seeded_user["password"]},
        # follow_redirects=False so we land on the 302 (and inspect
        # Set-Cookie) rather than the 200 the /roadmap gate returns
        # after the redirect chain.
        follow_redirects=False,
    )
    assert response.status_code == 302

    set_cookie = response.headers.get("set-cookie", "")
    # Extract the cookie value: ``cg_session=<value>; ...``
    cookie_value = ""
    for chunk in set_cookie.split(","):
        if chunk.strip().startswith(f"{COOKIE_NAME}="):
            cookie_value = chunk.strip().split("=", 1)[1].split(";", 1)[0]
            break
    # The first segment (before any comma) is also the cookie value
    # when only one Set-Cookie is set. Fall back to that.
    if not cookie_value:
        first_segment = set_cookie.split(";", 1)[0]
        cookie_value = first_segment.split("=", 1)[1]

    assert cookie_value, f"no cookie value parsed from: {set_cookie!r}"
    user_id = decode_jwt(cookie_value)
    assert user_id == seeded_user["id"], (
        f"JWT subject {user_id!r} != seeded user id {seeded_user['id']!r}"
    )


# ---------------------------------------------------------------------------
# POST /login — failure paths
# ---------------------------------------------------------------------------


def test_post_login_with_wrong_password_renders_form_with_error(
    client: TestClient, seeded_user: dict
) -> None:
    """POST /login with the right email but the wrong password must:

    * NOT redirect (return 200, re-render the login form)
    * include an error message
    * NOT set the session cookie
    """
    response = client.post(
        "/login",
        data={"email": seeded_user["email"], "password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 200, (
        f"wrong password should re-render the form, got {response.status_code}"
    )
    body = response.text
    # The form is still there (so the user can retry).
    assert 'action="/login"' in body
    # An error message of some kind is shown.
    assert "error" in body.lower() or "inv" in body.lower(), (
        f"no error message in response: {body!r}"
    )
    # No session cookie.
    set_cookie = response.headers.get("set-cookie", "")
    assert COOKIE_NAME not in set_cookie, (
        f"wrong password must not set {COOKIE_NAME!r}: {set_cookie!r}"
    )


def test_post_login_with_unknown_email_renders_form_with_same_error(
    client: TestClient, seeded_user: dict
) -> None:
    """POST /login with an email that doesn't exist must return the
    SAME error message as the wrong-password case. Per
    security-guardian, the response must not leak which of the two
    is wrong (otherwise an attacker can enumerate registered emails).
    """
    response = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "anything"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    body = response.text
    assert 'action="/login"' in body
    set_cookie = response.headers.get("set-cookie", "")
    assert COOKIE_NAME not in set_cookie


def test_post_login_wrong_password_and_unknown_email_have_identical_bodies(
    client: TestClient, seeded_user: dict
) -> None:
    """The two failure paths (wrong password / unknown email) must
    produce byte-identical response bodies. This is the
    security-guardian invariant: the attacker cannot tell which
    of the two was wrong.

    Stripped of timing variance (which we don't test here) and
    Set-Cookie headers, the HTML payload should match. We
    normalise whitespace to keep this robust against minor
    formatting drift.
    """
    wrong_pw = client.post(
        "/login",
        data={"email": seeded_user["email"], "password": "wrong-password"},
    )
    unknown = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "anything"},
    )
    assert wrong_pw.status_code == 200
    assert unknown.status_code == 200
    # Normalise: collapse all whitespace to single spaces.
    def _norm(s: str) -> str:
        return " ".join(s.split())

    assert _norm(wrong_pw.text) == _norm(unknown.text), (
        "wrong-password and unknown-email responses must be identical\n"
        f"--- wrong password ---\n{wrong_pw.text}\n"
        f"--- unknown email ---\n{unknown.text}\n"
    )


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------


def test_post_logout_redirects_to_login_and_clears_cookie(
    client: TestClient, seeded_user: dict
) -> None:
    """POST /logout must:

    * redirect to /login (302)
    * clear the session cookie (Set-Cookie with the cookie name,
      max-age=0 or expires in the past, or empty value)
    """
    # First, log in so we have a cookie to clear.
    login_response = client.post(
        "/login",
        data={"email": seeded_user["email"], "password": seeded_user["password"]},
        # follow_redirects=False — we want the 302 from /login, not
        # the 200 from /roadmap that the redirect chain would land on.
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 302
    location = logout_response.headers.get("location", "")
    assert location.endswith("/login"), f"logout redirect: {location!r}"

    set_cookie = logout_response.headers.get("set-cookie", "")
    assert set_cookie, "POST /logout must include a clearing Set-Cookie"
    assert COOKIE_NAME in set_cookie, (
        f"clearing cookie must target {COOKIE_NAME!r}: {set_cookie!r}"
    )
    # The clearing directive: either an empty value, an explicit
    # ``Max-Age=0``, or an ``Expires`` in the past. Any of these
    # tells the browser to drop the cookie.
    clears = (
        "Max-Age=0" in set_cookie
        or "max-age=0" in set_cookie.lower()
        or "Expires=" in set_cookie
    )
    # Some clients also accept an empty cookie value as a clear.
    cookie_value_segment = ""
    for chunk in set_cookie.split(","):
        if chunk.strip().startswith(f"{COOKIE_NAME}="):
            cookie_value_segment = chunk.strip().split("=", 1)[1].split(";", 1)[0]
            break
    if not cookie_value_segment:
        first_segment = set_cookie.split(";", 1)[0]
        cookie_value_segment = first_segment.split("=", 1)[1] if "=" in first_segment else ""

    clears = clears or cookie_value_segment == ""
    assert clears, (
        f"logout must clear cookie via Max-Age=0 / Expires= / empty value: {set_cookie!r}"
    )


# ---------------------------------------------------------------------------
# Cookie contract — attribute pinning (paranoia test, not tied to POST)
# ---------------------------------------------------------------------------


def test_cookie_constants_match_brief() -> None:
    """Pins the cookie contract constants exported by
    ``app.auth.middleware``. If a future ticket bumps them, this
    test fails loud — the contract change is a conscious decision,
    not silent drift.
    """
    assert COOKIE_NAME == "cg_session"
    assert COOKIE_HTTPONLY is True
    assert COOKIE_SAMESITE == "lax"
    # 30 days in seconds.
    assert COOKIE_MAX_AGE == 30 * 24 * 60 * 60
    # Secure is a bool (off in dev, on in prod).
    assert isinstance(COOKIE_SECURE, bool)
