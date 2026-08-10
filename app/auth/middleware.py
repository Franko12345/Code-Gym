"""Auth middleware — populates ``request.state.user`` from the
``cg_session`` JWT cookie (M1.T2).

Contract:

* On every request, read the ``cg_session`` cookie.
* Decode the JWT; on success, look up the user in the DB.
* Set ``request.state.user`` to a small read-only view of the row,
  or ``None`` if the cookie is absent/invalid/expired/unknown.

* **Never crash.** A missing or malformed cookie is a normal
  unauthenticated visit; raising here would 500 the public
  ``/login`` page. All failure paths are swallowed.

The middleware is intentionally **read-only**: it does not set
cookies. Cookie issuance belongs to the login route (M1.T4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth.jwt_utils import decode_jwt
from app.db import DEFAULT_DB_PATH, get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cookie name. Locked in via test (``test_middleware_reads_cg_session_cookie_name``).
COOKIE_NAME: str = "cg_session"

# Pinned cookie attributes — the contract M1.T4 (/login) reads when
# issuing the cookie. Centralised here so any future endpoint that
# reads or writes the session cookie stays in sync.
COOKIE_HTTPONLY: bool = True
COOKIE_SAMESITE: str = "lax"
# Secure is off in dev (http://localhost); flip on in prod via env.
COOKIE_SECURE: bool = os.environ.get("CODE_GYM_ENV", "dev") == "prod"
COOKIE_MAX_AGE: int = 30 * 24 * 60 * 60  # 30 days, matching DEFAULT_EXPIRES_IN_SECONDS

# The DB the middleware reads from. Tests point this at a tmp
# SQLite file; production leaves it at the default. Kept as a
# module attribute (not a constant) so tests can swap it.
DB_PATH: Path = DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# User view — small, immutable, exposes only what routes need
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserView:
    """A read-only view of a ``users`` row exposed to route handlers.

    Kept as a dataclass (not the ORM row or a Pydantic model) so:

    * it's fast to construct and hashable
    * route code can't accidentally mutate it
    * the contract is the attribute names — no SQL leaks across
      the middleware seam
    """

    id: int
    email: str
    display_name: Optional[str]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Populate ``request.state.user`` on every request.

    Behaviour:

    1.  Read the ``cg_session`` cookie. Absent or empty → leave
        ``request.state.user`` as ``None`` and continue.
    2.  ``decode_jwt`` the value. Any decode failure (bad signature,
        expired, missing sub) → leave as ``None`` and continue.
    3.  Look up the user by id in the DB. Missing row → leave as
        ``None`` and continue. (The DB is the source of truth —
        a valid JWT for a deleted user is treated as logged out.)
    4.  Set ``request.state.user`` to a ``UserView`` of the row.

    DB errors during the lookup are swallowed — better to render
    the page unauthenticated than 500 every request because the DB
    is briefly locked.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.user = self._resolve_user(request)
        return await call_next(request)

    # ----- internals -----------------------------------------------------

    def _resolve_user(self, request: Request) -> Optional[UserView]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None

        user_id = decode_jwt(token)
        if user_id is None:
            return None

        return self._lookup_user(user_id)

    @staticmethod
    def _lookup_user(user_id: int) -> Optional[UserView]:
        """Read the user row. Returns ``None`` on miss or DB error."""
        try:
            with get_connection(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT id, email, display_name FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
        except Exception:
            # DB locked, file missing, etc. — don't crash the request.
            return None

        if row is None:
            return None

        return UserView(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
        )


__all__ = (
    "AuthMiddleware",
    "COOKIE_HTTPONLY",
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "COOKIE_SAMESITE",
    "COOKIE_SECURE",
    "DB_PATH",
    "UserView",
)