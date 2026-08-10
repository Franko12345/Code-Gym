# Code-Gym

Personal DSI training app for OBI + maratona. NeetCode-inspired,
single-user effective, multi-user capable via invite-only CLI.

**Always read [`CONTEXT.md`](CONTEXT.md) first** — it has the
glossary and scope boundaries.

**Full spec:** [`.hermes/plans/2026-08-09_221134-code-gym-v0.1.0-mvp.md`](.hermes/plans/2026-08-09_221134-code-gym-v0.1.0-mvp.md)

**Decisions:** [`docs/adr/`](docs/adr/) — 6 ADRs covering stack,
sandbox, auth, frontend boundary, languages, and scrape cadence.

## Quick start

```bash
# 1. Install deps (uv-managed, Python 3.11)
uv sync

# 2. Provision sandbox user (once, requires sudo — see ADR-0002)
sudo deploy/setup-sandbox-user.sh

# 3. Seed content (load topics first, then problem snapshots)
python -m scripts.seed --file path/to/topics.yaml
python -m scripts.seed --file data/snapshots/noic-fixture.yaml

# 4. Create first user (invite-only, see ADR-0003)
python -m app.cli create-user you@example.com 'senha' 'You'

# 5. Run dev server
uvicorn app.main:app --reload
```

App runs on `http://localhost:8000`. Production: `code-gym.froto.online`
via Cloudflare Tunnel.

## Project structure

```
app/         FastAPI app: auth, db, elo, sandbox, roadmap,
             profile, problems, static, templates, main.py, cli.py
scripts/     seed.py (YAML → DB), scrape/ (obi.py, noic.py — snapshot YAML)
deploy/      setup-sandbox-user.sh (one-shot host provisioning)
tests/       pytest suite (154 passed, 11 skipped across 17 files)
docs/adr/    6 ADRs (stack, sandbox, auth, frontend, languages, cadence)
data/        SQLite DB + scraped snapshot YAMLs
```

## Tests

```bash
pytest tests/ -v
```

## Verified by

- **pytest:** 154 passed, 11 skipped (165 collected, 17 test files).
- **ADRs (6):** stack choices, sandbox security model, no public
  signup (CLI only), HTMX + CodeMirror boundary, C++/Python MVP
  languages, manual scrape (no cron) for MVP.

## Status

**MVP shipped (15/16 tickets).** M1, M2, M3 done. M4 done
except **M4.T5 (#17)** — Problem page + CodeMirror editor — in
flight. v0.1.0 closes when #17 merges. See `CONTEXT.md` for the
per-milestone ticket → PR mapping.