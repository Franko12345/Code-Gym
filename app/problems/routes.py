"""HTTP routes for the problems module (M4.T4, ticket #16).

Exposes ``POST /problems/{slug}/submit`` \u2014 the single seam where
user-submitted code enters the system.

Auth gate
---------
Per ADR-0003, submission requires a valid session cookie. The
``AuthMiddleware`` (M1.T2) sets ``request.state.user`` to a
``UserView`` (id + email + display_name) when the JWT cookie
verifies; ``None`` otherwise. The route reads
``request.state.user`` directly \u2014 no extra dependency, same
pattern as ``app.profile.routes``.

HTMX contract
-------------
The response is HTML (the ``partials/submission_result.html``
template). The submission form on the problem page (M4.T5, not
yet built) will ``hx-post`` to this URL and swap the partial into
a target div. Returning HTML keeps the seam uniform: the user
sees the verdict without a full page reload.

Constraints honoured
--------------------
* ADR-0002: we **never** ``eval()`` user code in the FastAPI
  process. The runner (``app.sandbox.runner.run``) is the only
  path that touches user code; it runs as the ``sandbox`` UID
  with RLIMITs in a tmpfs working dir.
* ADR-0005: only ``python`` and ``cpp`` accepted; anything else
  is a 400 *before* the runner is touched.
* ADR-0003: no public submit \u2014 anonymous requests 302 to ``/login``.
"""

from __future__ import annotations

import asyncio
from typing import Iterable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db import DEFAULT_DB_PATH
from app.problems.service import (
    ACCEPTED_LANGUAGES,
    SubmissionResult,
    judge_submission,
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


__all__: Iterable[str] = (
    "LOGIN_PATH",
    "router",
    "submit_solution",
)
