"""Code-Gym FastAPI application.

M3.T1 — minimal stub. Provides:
- `/` test route that renders `app/templates/base.html`
- `/static` mount serving `app/static/`
- Jinja2 templates wired via `fastapi.templating.Jinja2Templates`
- HTMX 2.x loaded from jsDelivr CDN with a real SRI hash (ADR-0004)

M3.T2 — `/roadmap` router attached (see `app.roadmap.routes`).

No DB writes yet — those land in M1 and M4 follow-ups. This file is
the seam where future modules attach.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.middleware import AuthMiddleware
from app.roadmap.routes import router as roadmap_router

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Code-Gym", version="0.1.0")

# Auth middleware — populates request.state.user from the cg_session
# cookie on every request. See app/auth/middleware.py for the contract.
# Must be added before any route handlers mount (FastAPI applies
# middleware in reverse-add order, so the last add_middleware runs
# outermost — i.e. this is the outermost middleware for now).
app.add_middleware(AuthMiddleware)

# Serve /static/style.css (and future favicon, CodeMirror CDN refs, etc.)
# StaticFiles is mounted at /static; files in app/static/ become
# /static/<relative-path>.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# M3.T2 — attach the roadmap router. The router is responsible for
# its own auth gate (302 → /login if no session cookie).
app.include_router(roadmap_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    """Minimal test route for M3.T1 — renders `index.html`, a child
    template that extends `base.html` and overrides the `content`
    block. Will be replaced by `/login` (unauthenticated) and
    `/roadmap` (authenticated) in later tickets."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {"page_title": "Code-Gym"},
    )
