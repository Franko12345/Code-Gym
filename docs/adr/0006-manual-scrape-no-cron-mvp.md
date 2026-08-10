# ADR-0006 — Manual scrape runs, no cron in MVP

- **Status:** accepted
- **Date:** 2026-08-09

## Context

The plan included a weekly cron job that would scrape OBI/NOIC content
and commit snapshots. But:

- OBI runs ~2x per year (one phase each semester).
- NOIC content updates rarely.
- CP-Algoritmos is essentially static.

A weekly cron job that runs and writes the same content 51 weeks out
of 52 is a waste of system resources and review attention.

## Decision

Scrape scripts live under `scripts/scrape/` and are run **manually**:

```bash
python -m scripts.scrape.obi
python -m scripts.scrape.noic
```

Snapshots are written to `data/snapshots/obi-YYYY-MM-DD.yaml` and can
be `git add` + `git commit` when the dev wants to update content.

Cron / scheduled scrape → v0.2 (after we know the actual cadence).

## Consequences

- **Positive:** no wasted cycles.
- **Positive:** dev sees each scrape output and can review changes
  before committing.
- **Negative:** dev must remember to run scripts when new content
  drops. Mitigated by calendar reminders (OBI is on fixed dates).
- **Reversibility:** high. Adding a cron is one shell line + a
  `cronjob` skill call.

## Alternatives considered

- **Cron weekly:** wastes cycles most of the time.
- **On-access scrape (live):** fragile, slow, breaks offline use.
  Rejected earlier in spec.
- **Webhook from OBI:** OBI doesn't have one.