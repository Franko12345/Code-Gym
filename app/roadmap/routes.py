"""Roadmap routes — M3.T2.

Exposes ``GET /roadmap`` which:

- 302 redirects to ``/login`` when no valid session is present.
- Renders ``app/templates/roadmap.html`` (which extends ``base.html``)
  with one card per topic + a NeetCode-style progress bar.

Auth seam
---------
Auth is provided by ``AuthMiddleware`` (M1.T2), which reads the
``cg_session`` JWT cookie and exposes the resolved user on
``request.state.user`` (an ``app.auth.middleware.UserView``).

``current_user`` here is a thin wrapper:

1.  Read ``request.state.user`` (the canonical path — populated by
    the middleware on every request). If non-None, return it wrapped
    as ``CurrentUser``.
2.  Fallback: read the legacy ``cg_user`` plain-text email cookie.
    Kept as a transitional seam so in-flight curl/dev scripts that
    were written before the JWT middleware landed keep working. The
    legacy cookie is **dead in production** — ``/login`` (M1.T4)
    only issues ``cg_session`` — but accepting it here means we
    don't break callers mid-migration. New code should rely on the
    middleware path (1).
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

# Legacy plain-text email cookie. Kept private — it is NOT a supported
# auth path; the canonical path is the JWT cookie read by AuthMiddleware.
# See the module docstring for why it still exists as a fallback.
_LEGACY_EMAIL_COOKIE: str = "cg_user"

# Login route — the redirect target for anonymous requests. M3.T2
# doesn't own this route; it just needs the URL to point at. The
# actual login form lands with M1.T2.
LOGIN_PATH: str = "/login"


class CurrentUser(BaseModel):
    """Minimal user payload injected into request handlers via ``Depends``.

    Field shape mirrors what ``AuthMiddleware`` populates on
    ``request.state.user`` (an ``app.auth.middleware.UserView``): id +
    email + display_name. Kept here so the route signature is
    unchanged when we swap the auth seam again — handlers always
    receive a ``CurrentUser``.
    """

    id: int
    email: str
    display_name: Optional[str] = None


def _wrap(user: object) -> Optional[CurrentUser]:
    """Adapt a ``request.state.user`` value (``UserView`` or ``None``)
    into the ``CurrentUser`` shape the handlers consume.

    Returns ``None`` when the input is ``None``; returns a
    ``CurrentUser`` otherwise. Centralised so the lookup logic in
    ``current_user`` stays readable.
    """
    if user is None:
        return None
    # UserView is a frozen dataclass with id/email/display_name —
    # structurally identical to CurrentUser, so attribute copy is safe.
    return CurrentUser(
        id=int(getattr(user, "id")),
        email=str(getattr(user, "email")),
        display_name=getattr(user, "display_name", None),
    )


def current_user(request: Request) -> Optional[CurrentUser]:
    """Resolve the current user.

    Canonical path (M1.T2 middleware): ``request.state.user`` is set
    by ``AuthMiddleware`` from the ``cg_session`` JWT cookie. If it
    is non-None, that's the answer.

    Legacy fallback: the ``cg_user`` plain-text email cookie, which
    M3.T2 originally relied on before the JWT middleware existed.
    Kept so dev / migration callers don't break in flight. Returns
    ``None`` if neither path yields a valid user — callers that need
    a hard auth gate redirect to ``LOGIN_PATH`` on ``None``.
    """
    # 1. Canonical: what the middleware computed.
    state_user = getattr(request.state, "user", None)
    wrapped = _wrap(state_user)
    if wrapped is not None:
        return wrapped

    # 2. Legacy fallback: plain-text email cookie. Only consulted
    #    when the middleware saw no JWT — i.e. the visitor is either
    #    anonymous or relying on the pre-M1.T2 manual cookie seam.
    email = request.cookies.get(_LEGACY_EMAIL_COOKIE)
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
    "CurrentUser",
    "current_user",
    "router",
)
