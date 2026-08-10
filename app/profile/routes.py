"""Profile routes — M3.T3 (ticket #12).

Exposes ``GET /u/{username}`` — a public profile view that renders a
NeetCode-style grid of all problems in the DB, coloured by the
viewed user's best verdict on each problem.

Auth gate
---------
The page is **public** per ADR-0003 spirit: anyone can see anyone's
progress. The viewer (logged in or not) is read off
``request.state.user`` (populated by AuthMiddleware from the
``cg_session`` JWT cookie) so a future "edit your own profile" UI
can tell whether the viewer is the owner. M3.T3 does not gate any
edit surface — read-only.

The 404 contract
----------------
A username that doesn't exist in the users table yields 404, NOT
500. The route returns ``HTMLResponse(status_code=404, ...)`` so the
test can pin the status without depending on FastAPI's default
exception handler (which would emit an ugly HTML traceback).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import DEFAULT_DB_PATH
from app.profile.service import (
    ProblemStatus,
    ProfileUser,
    get_profile_user_by_username,
    list_problem_statuses_for_user,
)

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _templates() -> Jinja2Templates:
    """Lazy resolution of the shared Jinja2Templates instance.

    Cached on the function attribute to avoid re-resolving on every
    request AND to dodge the circular import: ``app.main`` imports
    this router, so we cannot ``from app.main import templates`` at
    module load time.
    """
    cached = getattr(_templates, "_cache", None)
    if cached is None:
        from app.main import templates

        cached = templates
        _templates._cache = cached  # type: ignore[attr-defined]
    return cached


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/u/{username}", response_class=HTMLResponse)
async def profile(
    request: Request,
    username: str,
) -> HTMLResponse:
    """Render the public profile for ``username``.

    Status codes:

    * 200 — user exists; render the grid.
    * 404 — username not found; render a small "not found" page so the
      user can tell the URL was wrong (vs. a 500 from a crash).

    The viewer is exposed to the template via ``viewer`` — useful for
    a future "is this my profile? edit display name" link. Today it's
    unused but the data is plumbed through so the seam stays stable.
    """
    # ``request.state.user`` is set by AuthMiddleware (already wired
    # in app/main.py). For the public profile, the viewer is just
    # context — we don't gate the route on it.
    viewer = getattr(request.state, "user", None)

    profile_user: ProfileUser | None = get_profile_user_by_username(
        username, DEFAULT_DB_PATH
    )
    if profile_user is None:
        return _not_found(request, username)

    statuses: list[ProblemStatus] = list_problem_statuses_for_user(
        profile_user.id, DEFAULT_DB_PATH
    )

    return _templates().TemplateResponse(
        request,
        "profile.html",
        {
            "page_title": f"@{profile_user.display_name}",
            "profile_user": profile_user,
            "statuses": statuses,
            "viewer": viewer,
            # Group statuses by topic for the template's per-topic
            # section header. Computed in the route (not the
            # template) so the Jinja stays a thin renderer.
            "topics": _group_by_topic(statuses),
        },
    )


def _group_by_topic(
    statuses: list[ProblemStatus],
) -> list[dict[str, object]]:
    """Group problem rows by topic for the per-topic section header.

    Returns a list of dicts ``[{topic_slug, topic_name, problems:
    [ProblemStatus, ...]}, ...]`` — ordered to match
    ``list_problem_statuses_for_user``'s ordering (OBI F1 first,
    then by topic slug).
    """
    grouped: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for ps in statuses:
        if ps.topic_slug not in grouped:
            grouped[ps.topic_slug] = {
                "topic_slug": ps.topic_slug,
                "topic_name": ps.topic_name,
                "problems": [],
            }
            order.append(ps.topic_slug)
        problems = grouped[ps.topic_slug]["problems"]
        assert isinstance(problems, list)
        problems.append(ps)
    return [grouped[slug] for slug in order]


def _not_found(request: Request, username: str) -> HTMLResponse:
    """Render a small, friendly 404 page for an unknown username.

    Uses the shared base.html so the layout/sidebar are consistent.
    We don't redirect — a redirect would imply the URL is alive
    elsewhere, which it isn't.
    """
    return _templates().TemplateResponse(
        request,
        "profile.html",
        {
            "page_title": "Perfil não encontrado",
            "profile_user": None,
            "statuses": [],
            "topics": [],
            "viewer": getattr(request.state, "user", None),
            "missing_username": username,
        },
        status_code=404,
    )


__all__ = ("router",)