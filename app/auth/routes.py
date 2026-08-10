"""Auth routes — ``/login`` + ``/logout`` (M1.T4).

Public auth surface per ADR-0003:

* No ``/signup`` route. User creation is CLI-only
  (``python -m app.cli create-user ...``).
* Login is the only public way to obtain a session cookie.
* Logout is POST-only — a plain GET (or prefetch) cannot log a
  user out (per the brief; the sidebar in ``base.html`` already
  renders a ``<form method="post" action="/logout">``).

Cookie contract
---------------
The ``cg_session`` cookie attributes are **pinned in
``app.auth.middleware``** and re-used here verbatim. Do not
redefine the values locally — any drift would silently break
the middleware (which reads the same name) and the test
suite (which asserts against the constants).

Failure response shape
----------------------
On invalid credentials (wrong password OR unknown email) we
return the **same HTML body** with a generic error message.
Per security-guardian: never let an attacker distinguish
"email not found" from "wrong password" — that would let
them enumerate registered emails.
"""

from __future__ import annotations

from typing import Iterable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth.jwt_utils import encode_jwt
from app.auth.middleware import (
    COOKIE_HTTPONLY,
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
)
from app.auth.passwords import verify_pw
from app.db import DEFAULT_DB_PATH, get_connection


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

# Reuse the same Jinja2Templates instance the rest of the app uses.
# Resolved lazily (once) to avoid a circular import with ``app.main``
# (which imports this router). Cached on the function attribute.
def _templates() -> Jinja2Templates:
    cached = getattr(_templates, "_cache", None)
    if cached is None:
        from app.main import templates

        cached = templates
        _templates._cache = cached  # type: ignore[attr-defined]
    return cached


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Where successful logins go. Pinned so tests can reference it
# without string drift; matches the auth gate in
# ``app.roadmap.routes``.
ROADMAP_PATH: str = "/roadmap"

# Generic error message — shown for BOTH wrong-password and
# unknown-email cases. Per security-guardian: identical wording
# prevents user-enumeration attacks. The text is in Portuguese
# to match the rest of the UI; the exact wording is not load-
# bearing (it never leaks which of the two was wrong).
GENERIC_LOGIN_ERROR: str = "E-mail ou senha incorretos."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lookup_user_by_email(email: str) -> Optional[dict]:
    """Return the user row as a plain dict, or None.

    We don't expose the password hash outside this module — the
    route only needs ``id`` to mint the JWT. Returning a dict
    keeps the helper self-contained (no SQLAlchemy / Pydantic
    model just for one row).
    """
    if not email:
        return None
    with get_connection(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, display_name "
            "FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "password_hash": str(row["password_hash"]),
        "display_name": row["display_name"],
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    """Render the login form.

    Anonymous-only surface — also reachable by already-logged-in
    users (e.g. if a session cookie expired mid-session). The
    form posts to ``/login`` (see ``login_post``).
    """
    return _templates().TemplateResponse(
        request,
        "login.html",
        {
            "page_title": "Login",
            "error": None,
            "email": "",
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
) -> Response:
    """Verify credentials and either set the session cookie or
    re-render the form with a generic error.

    The two failure paths (unknown email / wrong password)
    collapse into the same response — see
    ``_render_login_error`` below.
    """
    # Normalise: strip whitespace, treat empty email as a
    # "no credentials" failure (no DB read needed, same error
    # message, prevents needless timing oracle).
    email_norm = (email or "").strip()
    password_norm = password or ""

    if not email_norm or not password_norm:
        return _render_login_error(request)

    row = _lookup_user_by_email(email_norm)
    if row is None or not verify_pw(password_norm, row["password_hash"]):
        # CRITICAL: same code path, same response for both cases.
        return _render_login_error(request)

    # Success — mint the JWT and set the cookie.
    token = encode_jwt(row["id"])
    response = RedirectResponse(url=ROADMAP_PATH, status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=COOKIE_SECURE,
        httponly=COOKIE_HTTPONLY,
        # Starlette's set_cookie types samesite as
        # ``Literal['lax', 'strict', 'none']``; our module-pinned
        # constant is the string ``"lax"`` and Starlette accepts
        # it at runtime. The cast keeps Pyright happy without
        # weakening the cookie contract (the constant is still
        # the single source of truth).
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
    )
    return response


def _render_login_error(request: Request) -> HTMLResponse:
    """Re-render the login form with the generic error.

    Called for: missing fields, unknown email, wrong password.

    Security note (ponytail / security-guardian): the response
    body MUST be byte-identical across all three cases so an
    attacker cannot enumerate registered emails. The ``email``
    field is intentionally **not** pre-filled (not even with an
    empty string — the template defaults it to ``""``). This
    keeps the wrong-password and unknown-email bodies
    interchangeable.
    """
    return _templates().TemplateResponse(
        request,
        "login.html",
        {
            "page_title": "Login",
            "error": GENERIC_LOGIN_ERROR,
            "email": "",
        },
        status_code=200,
    )


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the session cookie and redirect to ``/login``.

    POST-only (per ADR-0003 spirit + the brief): a plain GET
    must never log a user out, because prefetchers / link
    scanners will issue GETs. The sidebar in ``base.html``
    renders a ``<form method="post" action="/logout">``.

    We **always** clear the cookie on POST /logout, even if
    the cookie was missing or invalid — the user clicked
    "logout" and the safest response is "you're logged out".
    """
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
    )
    return response


__all__: Iterable[str] = (
    "GENERIC_LOGIN_ERROR",
    "ROADMAP_PATH",
    "login_get",
    "login_post",
    "logout",
    "router",
)