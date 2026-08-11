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

Lookup contract for ``{username}``
-----------------------------------
``{username}`` is matched against the ``users`` table in three ways,
in order, first match wins:

1.  **id** — if the URL segment is a bare integer, look up by id.
2.  **email** — exact (case-sensitive) match against ``email``.
3.  **display_name** — case-insensitive match against ``display_name``.

This lets callers address a user via their stable id, via email
(e.g. ``/u/franco@froto.online`` — useful when the viewer has no
display_name handy), or via their public display_name. If none of
the three matches, the route renders a small 404 page.

The 404 contract
----------------
A username that doesn't exist in the users table yields 404, NOT
500. The route returns ``HTMLResponse(status_code=404, ...)`` so the
test can pin the status without depending on FastAPI's default
exception handler (which would emit an ugly HTML traceback).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import DEFAULT_DB_PATH, get_connection
from app.profile.service import (
    ProblemStatus,
    ProfileUser,
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
# User lookup
# ---------------------------------------------------------------------------


def _row_to_profile_user(row: object) -> ProfileUser:
    """Materialise a sqlite3 ``users`` row into a ``ProfileUser``.

    Shared by the three lookup branches so the column list stays in
    one place. The ``display_name IS NULL`` guard mirrors the
    service-layer behaviour: a user without a display_name is
    unaddressable via ``/u/{username}`` and is silently treated as a
    miss — falling through to the next branch.
    """
    if row is None:
        raise ValueError("expected a sqlite3 row, got None")
    if row["display_name"] is None:
        raise ValueError("user has no display_name — unaddressable via /u/")
    return ProfileUser(
        id=int(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        elo=int(row["elo"] or 0),
    )


def _resolve_profile_user(username: str) -> ProfileUser | None:
    """Look up the user addressed by ``/u/{username}``.

    Tries, in order: id (if numeric) → email (exact) → display_name
    (case-insensitive). Returns the first match, or ``None`` when
    none of the three match.

    The id branch fires only when the URL segment is a bare integer;
    this keeps display_names like ``"42``" (a string of digits)
    addressable via the display_name branch.

    The display_name lookup uses SQLite's default ``NOCASE`` collation
    so ``/u/franco``, ``/u/Franco``, and ``/u/FRANCO`` all resolve to
    the same row. Email is matched exactly (case-sensitive) because
    email local-parts are case-sensitive in practice — the login
    route treats them as such, and we want to stay consistent.
    """
    # 1. id (only when the segment is a pure integer).
    if username.isdigit():
        try:
            with get_connection(DEFAULT_DB_PATH) as conn:
                row = conn.execute(
                    "SELECT id, email, display_name, elo "
                    "FROM users WHERE id = ?",
                    (int(username),),
                ).fetchone()
            if row is not None and row["display_name"] is not None:
                return _row_to_profile_user(row)
        except Exception:
            # Don't 500 — fall through to the next branch.
            pass

    # 2. email (exact match).
    try:
        with get_connection(DEFAULT_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, email, display_name, elo "
                "FROM users WHERE email = ?",
                (username,),
            ).fetchone()
        if row is not None and row["display_name"] is not None:
            return _row_to_profile_user(row)
    except Exception:
        pass

    # 3. display_name (case-insensitive).
    try:
        with get_connection(DEFAULT_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, email, display_name, elo "
                "FROM users WHERE display_name = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if row is not None and row["display_name"] is not None:
            return _row_to_profile_user(row)
    except Exception:
        pass

    return None


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

    profile_user: ProfileUser | None = _resolve_profile_user(username)
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





# ---------------------------------------------------------------------------
# /profile — redirect to the viewer's own profile (FIX 2)
# ---------------------------------------------------------------------------


@router.get("/profile", include_in_schema=False)
async def profile_redirect(request: Request):
    """Redirect ``/profile`` to the viewer's own ``/u/{username}`` page.

    Status codes:

    * **302** — viewer is authenticated → ``/u/{user.id}`` (the
      primary key is the most stable identifier; the display_name
      could be renamed later, the id never changes).
    * **302** — viewer is anonymous → ``/login``. The login page
      already handles the "return to where you came from" via the
      existing post-login flow (no special-casing needed here).
    * **404** — *should not happen* while AuthMiddleware is wired:
      if ``request.state.user`` is set the middleware guarantees a
      real users row. We defend with a 404 rather than 500 if the
      invariant ever breaks.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    # Redirect by id (stable). /u/{id} is one of the three branches
    # in _resolve_profile_user (id → email → display_name), so this
    # always lands on the same profile page regardless of which
    # branch fires.
    return RedirectResponse(url=f"/u/{int(user.id)}", status_code=302)


__all__ = ("router",)