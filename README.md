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

# 2. Provision sandbox user (once, requires sudo)
sudo deploy/setup-sandbox-user.sh

# 3. Initialize DB
python -m scripts.seed --file data/seed/topics.yaml

# 4. Create first user (invite-only)
python -m app.cli create-user you@example.com 'senha' 'You'

# 5. Run dev server
docker compose up   # or: uvicorn app.main:app --reload
```

App runs on `http://localhost:8000`. Production: `code-gym.froto.online`
via Cloudflare Tunnel.

## Tests

```bash
pytest tests/ -v
```

## Status

Empty repo. Spec is final, plan is in `.hermes/plans/`, ADRs are
written. Next: open M1.T1 as the first PR.