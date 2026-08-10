"""Code-Gym problems module (M4.T4 — ticket #16).

Provides the ``POST /problems/{slug}/submit`` endpoint that:

* 302s to ``/login`` when the request has no valid session cookie
  (auth gate per ADR-0003).
* 400s on any language outside the ADR-0005 whitelist (only
  ``python`` and ``cpp`` accepted).
* 404s on an unknown problem slug.
* 200s with an HTMX-friendly HTML partial that renders the verdict.

The actual judging work (loop over test cases, persist submission,
update ELO) lives in ``app.problems.service``. This module is the
seam to FastAPI.
"""

from app.problems.routes import router

__all__: tuple[str, ...] = ("router",)
