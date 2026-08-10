# ADR-0001 — Stack choices

- **Status:** accepted
- **Date:** 2026-08-09

## Context

Building a personal DSI training app (OBI + maratona). The repo was
empty; we had to pick a stack from scratch. Constraints: solo developer,
single-server deployment on Proxmox LXC via Cloudflare Tunnel, focus on
OBI/maratona training (not a LeetCode clone).

## Decision

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Python 3.11 | Solo dev already writes Python (neural-network repo). Type hints + Pydantic reduce UI bugs. |
| Templates | Jinja2 | Built into FastAPI ecosystem. No build step. |
| Frontend interactivity | HTMX 2.x | HTML-over-the-wire, no SPA, no build, fits "CRUD-heavy" app shape. |
| Code editor | CodeMirror 6 (CDN) | Best OSS browser editor, language modes for C++/Python, no npm install needed. |
| Database | SQLite (stdlib) | Single-user workload, single-file backup, WAL mode handles concurrent reads. No migration framework needed (just SQL strings on startup). |
| Auth | bcrypt + JWT (cookie httpOnly) | No third-party identity provider. Cookie httpOnly over HTTPS (Cloudflare Tunnel) is safe. |
| Sandbox | subprocess + RLIMIT + UID isolation | Single-user trust boundary + RLIMIT_AS/CPU/NPROC/FSIZE + dedicated `sandbox` user (no shell, no home). |
| Logging | stdlib `logging` + RotatingFileHandler | Zero deps, structured JSON formatter is one function. |
| Dev | Docker Compose | Reproducible local + same image shape as prod. |
| Prod | systemd unit | One LXC, one service. No Kubernetes in scope. |
| Tunnel | Cloudflare Tunnel | No port-forwarding on Proxmox, automatic HTTPS, free. |

## Consequences

- **Positive:** zero npm dependencies, zero build step, ~50MB RAM idle.
- **Positive:** full-text editor works in browser without any JS framework.
- **Negative:** editor page has non-HTMX JS (CodeMirror). Documented as the one boundary.
- **Negative:** SQLite means horizontal scaling is impossible. Acceptable — single-user.
- **Reversibility:** high. Each piece can be swapped (HTMX → vanilla JS, SQLite → Postgres, JWT → session). The biggest lock-in is FastAPI itself.