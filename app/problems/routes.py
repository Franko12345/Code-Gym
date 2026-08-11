"""HTTP routes for the problems module.

* M4.T4 (ticket #16) — ``POST /problems/{slug}/submit``: the single
  seam where user-submitted code enters the system.
* M4.T5 (ticket #17) — ``GET /problems/{slug}``: the problem detail
  page (statement + examples on the left, CodeMirror editor +
  language selector + submit form on the right).

Both routes are auth-gated per ADR-0003 — anonymous requests 302
to ``/login``. The submit route additionally validates language
against the ADR-0005 whitelist (``python``/``cpp``) before
invoking the sandbox runner; the page route is a pure HTML
render and never touches the runner.

Page-route contract (M4.T5)
---------------------------
The page is a single full-HTML document that extends ``base.html``.
Per ADR-0004 the editor is the ONLY place with non-HTMX JS —
CodeMirror 5 loaded from jsDelivr with **real sha384 SRI hashes**
(documented in the template). The vanilla JS adapter handles:

* load saved code from ``localStorage[cg-code-{slug}-{lang}]``
  on mount
* save to the same key on keyup (debounced 500 ms)
* swap CodeMirror mode on language change
* copy CM content into a hidden ``<textarea name="code">`` before
  the form posts to ``/problems/{slug}/submit`` (the M4.T4 route)

Auth seam
---------
The ``AuthMiddleware`` (M1.T2) sets ``request.state.user`` to a
``UserView`` (id + email + display_name) when the JWT cookie
verifies; ``None`` otherwise. Both routes read
``request.state.user`` directly — no extra dependency, same
pattern as ``app.profile.routes``.

HTMX contract (submit)
---------------------
The submit response is HTML (the ``partials/submission_result.html``
template). The form on this page is a plain POST — the JS just
copies the editor buffer into the form field before submit.
Returning HTML keeps the seam uniform: the user sees the verdict
without a full page reload.

Constraints honoured
--------------------
* ADR-0002: we **never** ``eval()`` user code in the FastAPI
  process. The runner (``app.sandbox.runner.run``) is the only
  path that touches user code; it runs as the ``sandbox`` UID
  with RLIMITs in a tmpfs working dir.
* ADR-0005: only ``python`` and ``cpp`` accepted; anything else
  is a 400 *before* the runner is touched.
* ADR-0003: no public submit — anonymous requests 302 to ``/login``.
* ADR-0004: CodeMirror is the only non-HTMX JS on the page. Every
  external script carries a real ``integrity`` attribute (sha384);
  no ``integrity=""`` ever ships.
"""

from __future__ import annotations

import asyncio
import json
from typing import Iterable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import DEFAULT_DB_PATH, get_connection
from app.problems.service import (
    ACCEPTED_LANGUAGES,
    ProblemListRow,
    SubmissionResult,
    judge_submission,
    list_problems_for_browse,
)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _templates() -> Jinja2Templates:
    """Lazy resolution of the shared Jinja2Templates instance.

    Same dance as ``app.roadmap.routes`` and ``app.profile.routes``:
    ``app.main`` imports this router, so we can't
    ``from app.main import templates`` at module load. Cached on
    the function attribute.
    """
    cached = getattr(_templates, "_cache", None)
    if cached is None:
        from app.main import templates

        cached = templates
        _templates._cache = cached  # type: ignore[attr-defined]
    return cached


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Login route \u2014 the redirect target for unauthenticated requests.
LOGIN_PATH: str = "/login"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


# ---------------------------------------------------------------------------
# Problem page helpers (M4.T5)
# ---------------------------------------------------------------------------


def _get_problem_for_page(
    slug: str, db_path: Optional[object] = None
) -> Optional[dict]:
    """Return the problem row + parsed examples for the detail page.

    The page needs statement, title, difficulty, topic name, and
    examples list. Returning a plain dict keeps the template
    rendering trivial and avoids leaking ORM rows across the
    route boundary. Returns ``None`` if the slug is unknown
    (the caller turns that into a 404).
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT p.id, p.slug, p.title, p.statement_md,
                   p.difficulty, p.examples_json,
                   t.name AS topic_name
            FROM problems p
            JOIN topics t ON t.id = p.topic_id
            WHERE p.slug = ?
            """,
            (slug,),
        ).fetchone()
    if row is None:
        return None
    examples: list[dict] = []
    raw = row["examples_json"]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                examples = [e for e in parsed if isinstance(e, dict)]
        except json.JSONDecodeError:
            # Malformed examples_json: render the page without examples
            # rather than 500 — a future ticket can validate the seed.
            examples = []
    return {
        "id": int(row["id"]),
        "slug": str(row["slug"]),
        "title": str(row["title"]),
        "statement_md": str(row["statement_md"]),
        "difficulty": int(row["difficulty"]),
        "topic_name": str(row["topic_name"]),
        "examples": examples,
    }


@router.get("/problems/{slug}", response_class=HTMLResponse)
async def problem_page(
    request: Request,
    slug: str,
) -> HTMLResponse:
    """Render ``app/templates/problems/detail.html``.

    Status codes:

    * **302** — anonymous request, redirected to ``/login``.
    * **404** — the ``slug`` doesn't match any problem in the DB.
    * **200** — the page renders.

    The form on the page POSTs to ``/problems/{slug}/submit`` (the
    M4.T4 route); the verdict is swapped in via HTMX.
    """
    # ---- Auth gate (ADR-0003) --------------------------------------------
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse(url=LOGIN_PATH, status_code=302)  # type: ignore[return-value]

    problem = _get_problem_for_page(slug)
    if problem is None:
        return _page_not_found(request, slug)

    # Default language is Python — the easier ramp for a first
    # submission on a fresh problem page. The user can switch
    # via the <select> which fires a CM mode-swap.
    return _templates().TemplateResponse(
        request,
        "problems/detail.html",
        {
            "page_title": problem["title"],
            "problem": problem,
            "default_language": "python",
            "accepted_languages": sorted(ACCEPTED_LANGUAGES),
        },
    )


def _page_not_found(request: Request, slug: str) -> HTMLResponse:
    """Render a small "problem not found" page with status 404.

    Kept as a full HTML page (not a partial) because the user
    reached this URL by GET-typing it — they need a real page,
    not a fragment that'd render as plain text in the address bar.
    """
    return _templates().TemplateResponse(
        request,
        "problems/not_found.html",
        {"page_title": "Problema não encontrado", "slug": slug},
        status_code=404,
    )


@router.post("/problems/{slug}/submit", response_class=HTMLResponse)
async def submit_solution(
    request: Request,
    slug: str,
    code: str = Form(""),
    language: str = Form(""),
) -> HTMLResponse:
    """Run the submitted code against the problem's test cases and
    return an HTMX-friendly verdict partial.

    Status codes:

    * **302** \u2014 anonymous request, redirected to ``/login``.
    * **400** \u2014 ``language`` is outside the ADR-0005 whitelist
      (``python``/``cpp`` only). The runner is never invoked.
    * **404** \u2014 the ``slug`` doesn't match any problem in the DB.
    * **200** \u2014 the submission ran; the body is the verdict partial
      (HTML), suitable for an HTMX swap.
    """
    # ---- Auth gate (ADR-0003) --------------------------------------------
    user = getattr(request.state, "user", None)
    if user is None:
        # 302, not 401: matches the rest of the app (login is the
        # only public auth surface; there's no "API" consumer that
        # would handle 401 differently).
        return RedirectResponse(url=LOGIN_PATH, status_code=302)  # type: ignore[return-value]

    # ---- Language validation (ADR-0005) ---------------------------------
    language_norm = language.lower().strip()
    if language_norm not in ACCEPTED_LANGUAGES:
        return HTMLResponse(
            (
                f"<p class=\"submission-error\">"
                f"Linguagem não suportada: {language!r}. "
                f"Code-Gym aceita apenas: {', '.join(sorted(ACCEPTED_LANGUAGES))}."
                f"</p>"
            ),
            status_code=400,
        )

    # ---- Unknown problem (404) ------------------------------------------
    # The service returns None for an unknown slug; we render the
    # 404 ourselves so the test can pin the status code without
    # depending on FastAPI's default exception handler.
    #
    # The runner is blocking (subprocess.run + UID drop + RLIMITs +
    # 2.5s timeout). Calling it directly here would freeze the
    # FastAPI event loop for the duration of every submission.
    # ``asyncio.to_thread`` runs it in the default thread pool so
    # other requests keep flowing.
    result: Optional[SubmissionResult] = await asyncio.to_thread(
        judge_submission,
        user_id=user.id,
        problem_slug=slug,
        code=code,
        language=language_norm,
        db_path=DEFAULT_DB_PATH,
    )
    if result is None:
        return _not_found(request, slug)

    # ---- Render the verdict partial (HTMX swap target) ------------------
    return _templates().TemplateResponse(
        request,
        "partials/submission_result.html",
        {
            "page_title": "Resultado",
            "result": result,
            "problem_slug": slug,
            "language": language_norm,
        },
    )


def _not_found(request: Request, slug: str) -> HTMLResponse:
    """Render a small "problem not found" partial with status 404.

    Returned from the partial-template pipeline so the HTMX swap
    shows the user a clear message instead of a blank target.
    """
    return _templates().TemplateResponse(
        request,
        "partials/submission_result.html",
        {
            "page_title": "Problema não encontrado",
            "result": None,
            "problem_slug": slug,
            "language": "",
        },
        status_code=404,
    )





# ---------------------------------------------------------------------------
# Problem list page (FIX 1 — GET /problems)
# ---------------------------------------------------------------------------


@router.get("/problems", response_class=HTMLResponse, name="problems_list")
async def problems_list(
    request: Request,
    topic: Optional[str] = None,
    q: Optional[str] = None,
) -> HTMLResponse:
    """Render ``app/templates/problems/list.html``.

    Status codes:

    * **302** — anonymous request, redirected to ``/login`` (auth gate
      per ADR-0003 — no public browse of the problem set).
    * **200** — the list renders. May be empty (filters matched
      nothing, or the DB is empty); the template handles both.

    Query params:

    * ``topic`` — exact match against ``topics.slug``. Unknown slugs
      render an empty-state (no 404; the user can just edit the URL).
    * ``q`` — case-insensitive substring match against
      ``problems.title`` (simple LIKE per the brief). Trimmed; empty
      string is a no-op (returns everything).

    The viewer (``request.state.user``) is plumbed through to the
    template as ``viewer`` so each card can show the viewer's
    colour-coded badge (same colour rule as /u/{username}). When the
    viewer is anonymous the cards render without badges — no
    colour leakage.
    """
    # ---- Auth gate (ADR-0003) --------------------------------------------
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse(url=LOGIN_PATH, status_code=302)  # type: ignore[return-value]

    user_id = int(user.id) if user is not None else None

    problems: list[ProblemListRow] = list_problems_for_browse(
        topic_slug=topic,
        query=q,
        user_id=user_id,
    )

    # Pre-compute the title for the page header. Keeps the template a
    # thin renderer.
    if topic and q:
        page_heading = f"Resultados para '{q}' em {topic}"
    elif topic:
        page_heading = f"Problemas — {topic}"
    elif q:
        page_heading = f"Resultados para '{q}'"
    else:
        page_heading = "Todos os problemas"

    return _templates().TemplateResponse(
        request,
        "problems/list.html",
        {
            "page_title": "Problemas",
            "viewer": user,
            "problems": problems,
            "filter_topic": topic or "",
            "filter_q": q or "",
            "page_heading": page_heading,
        },
    )


# ``ProblemListRow`` is imported above so the type checker is happy
# when the route signature uses it indirectly through the service
# return type. ``name="problems_list"`` lets ``url_for`` point
# other templates (e.g. roadmap topic cards) at this route.
_ = ProblemListRow  # silence unused-import warnings


__all__: Iterable[str] = (
    "LOGIN_PATH",
    "problems_list",
    "router",
    "submit_solution",
)
