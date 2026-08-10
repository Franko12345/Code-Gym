"""Roadmap routes — M3.T2.

Exposes ``GET /roadmap`` which:

- 302 redirects to ``/login`` when no valid session cookie is present.
- Renders ``app/templates/roadmap.html`` (which extends ``base.html``)
  with one card per topic + a NeetCode-style progress bar.

Auth seam
---------
The cookie name is ``cg_user`` and its value is the user's email —
plain text, NOT signed. This is a deliberate, documented shortcut:
M1.T2 (JWT cookie middleware) lands later and the JWT-decoded
``current_user`` dependency will replace this local one. The route
signature stays identical; only ``current_user`` body changes.

The brief explicitly allows this: "If M1.T2 (JWT middleware) is not
yet merged in this branch, you'll need to integrate the middleware
locally OR test with a manually-injected cookie. Either is fine;
document the choice." We chose **manually-injected cookie** because
it has the smallest diff and zero overlap with M1.T2's scope.
"""

from __future__ import annotations

from typing import Iterable, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.db import DEFAULT_DB_PATH, get_connection
from app.roadmap.service import TopicProgress, list_topics_with_progress


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

# Reuse the same Jinja2Templates instance the rest of the app uses.
# We resolve it lazily (once, on first call) to avoid a circular
# import with ``app.main`` — ``main`` imports this router, so this
# module can't import ``main`` at top level. Caching on the function
# attribute avoids re-resolving on every request.
def _templates() -> Jinja2Templates:
    cached = getattr(_templates, "_cache", None)
    if cached is None:
        from app.main import templates

        cached = templates
        _templates._cache = cached  # type: ignore[attr-defined]
    return cached


# ---------------------------------------------------------------------------
# Auth seam
# ---------------------------------------------------------------------------

# Cookie name. Pinned as a module constant so tests + future JWT
# middleware can reference it without string drift.
SESSION_COOKIE: str = "cg_user"

# Login route — the redirect target for anonymous requests. M3.T2
# doesn't own this route; it just needs the URL to point at. The
# actual login form lands with M1.T2.
LOGIN_PATH: str = "/login"


class CurrentUser(BaseModel):
    """Minimal user payload injected into request handlers via ``Depends``.

    Field shape mirrors what M1.T2 will eventually decode from the
    JWT — id + email + display_name. Keep this stable so swapping the
    dep body later is a no-op for the handlers.
    """

    id: int
    email: str
    display_name: Optional[str] = None


def current_user(request: Request) -> Optional[CurrentUser]:
    """Resolve the current user from the ``cg_user`` cookie.

    Returns None if the cookie is missing or the email doesn't exist
    in the ``users`` table. Callers that need a hard auth gate
    redirect to ``LOGIN_PATH`` on None.
    """
    email = request.cookies.get(SESSION_COOKIE)
    if not email:
        return None
    with get_connection(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, email, display_name FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return CurrentUser(
        id=int(row["id"]),
        email=str(row["email"]),
        display_name=row["display_name"],  # nullable
    )


# Type alias for FastAPI dependency injection in handlers. Use
# ``Optional[CurrentUser]`` as the dep annotation so FastAPI passes
# through None when the cookie is missing — the handler then decides
# whether to redirect.
CurrentUserDep = Optional[CurrentUser]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


def _require_login(user: Optional[CurrentUser]) -> Optional[RedirectResponse]:
    """Return a 302 to ``/login`` if no user is signed in, else None.

    Pulled into a tiny helper so the route body stays linear.
    """
    if user is None:
        return RedirectResponse(url=LOGIN_PATH, status_code=302)
    return None


@router.get("/roadmap", response_class=HTMLResponse)
async def roadmap(
    request: Request,
    user: CurrentUserDep = Depends(current_user),
) -> HTMLResponse:
    """Render the NeetCode-style topic grid for the current user."""
    redirect = _require_login(user)
    if redirect is not None:
        # Returning a RedirectResponse from an HTMLResponse-decorated
        # endpoint is allowed by FastAPI/Starlette — the response
        # class hint is the *default*; the actual returned object
        # wins at runtime.
        return redirect  # type: ignore[return-value]

    # ``user`` is non-None past this point (per redirect above).
    assert user is not None
    topics: list[TopicProgress] = list_topics_with_progress(user_id=user.id)

    return _templates().TemplateResponse(
        request,
        "roadmap.html",
        {
            "page_title": "Roadmap",
            "topics": topics,
            "user": user,
        },
    )


__all__: Iterable[str] = (
    "LOGIN_PATH",
    "SESSION_COOKIE",
    "CurrentUser",
    "current_user",
    "router",
)
