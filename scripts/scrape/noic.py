"""NOIC problem snapshot writer.

Per M2.T3 and ADR-0006:

- ``python -m scripts.scrape.noic`` is the canonical entry point.
- Writes ``data/snapshots/noic-YYYY-MM-DD.yaml`` whose top-level
  ``problems`` list matches the ``scripts.seed`` schema (one row per
  problem, with nested ``test_cases``).
- Idempotent: re-running on the same day overwrites the same file;
  re-loading via ``scripts.seed.seed_from_file`` does not duplicate
  rows.
- Best-effort, exit 0 on every reachable code path: a real NOIC
  source isn't always available (the site is gated/JS-heavy), so the
  MVP behavior is to copy a hand-authored fixture from
  ``data/snapshots/noic-fixture.yaml``. The fixture path is the
  source of truth until ``fetch_from_network()`` is implemented.

Why a fixture and not a live scrape (yet)?

The NOIC public site (https://noic.com.br/) is login-walled and
heavily JavaScript-driven. A live scrape in MVP would need a
browser stack and credential handling that ADR-0006 explicitly
defers (manual runs, no infra). The fixture gives us:

1. A runnable script on every developer machine, with zero network
   dependency.
2. A seed schema sanity check — if the seed loader accepts the
   snapshot, M2.T3's contract is met.
3. A clean seam to swap ``fetch_from_network()`` in later without
   touching the public ``scrape_noic`` signature.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Resolve the repo root once, lazily — works under both ``python -m
# scripts.scrape.noic`` (which sets CWD to repo root) and direct
# ``python scripts/scrape/noic.py`` invocations.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"
DEFAULT_FIXTURE_PATH = DEFAULT_SNAPSHOTS_DIR / "noic-fixture.yaml"


# ---------------------------------------------------------------------------
# Public seam
# ---------------------------------------------------------------------------


def scrape_noic(
    snapshots_dir: Path | str,
    *,
    fixture_path: Path | str | None = None,
    today: dt.date | None = None,
) -> Path:
    """Write today's NOIC snapshot to ``snapshots_dir``.

    Parameters
    ----------
    snapshots_dir:
        Directory to write ``noic-YYYY-MM-DD.yaml`` into. Created
        if missing.
    fixture_path:
        Optional override for the local fixture. Defaults to
        ``data/snapshots/noic-fixture.yaml``. Tests pass a tmp path
        here to keep the real fixture untouched.
    today:
        Override for the date used in the filename. Defaults to
        ``dt.date.today()`` in the local timezone. Tests pass a fixed
        date for determinism.

    Returns
    -------
    Path
        Absolute path to the written snapshot file.

    Notes
    -----
    This function deliberately does not touch the network — see the
    module docstring for the rationale. ``fetch_from_network`` below
    is the seam where a real implementation will plug in once we
    finalize the NOIC source.
    """
    snapshots_dir = Path(snapshots_dir)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    fixture = Path(fixture_path) if fixture_path is not None else DEFAULT_FIXTURE_PATH
    if not fixture.exists():
        raise FileNotFoundError(
            f"NOIC fixture not found at {fixture}. Either create "
            "data/snapshots/noic-fixture.yaml or pass fixture_path="
        )

    payload = _load_fixture_payload(fixture)

    target_date = today or dt.date.today()
    out_path = snapshots_dir / f"noic-{target_date.isoformat()}.yaml"

    _write_snapshot(out_path, payload)
    return out_path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_fixture_payload(fixture_path: Path) -> dict[str, Any]:
    """Read the fixture YAML and validate the top-level shape.

    We don't validate each problem row here — the seed loader does
    that, and a stricter check here would duplicate its contract.
    We only enforce the absolute minimum so the writer doesn't
    silently emit a broken file.
    """
    with fixture_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"NOIC fixture {fixture_path} must be a YAML mapping, "
            f"got {type(data).__name__}"
        )
    if "problems" not in data:
        raise ValueError(
            f"NOIC fixture {fixture_path} must contain a top-level "
            "'problems' key (matching scripts.seed schema)"
        )
    if not isinstance(data["problems"], list):
        raise ValueError(
            f"NOIC fixture {fixture_path} 'problems' must be a list"
        )
    return data


def _write_snapshot(out_path: Path, payload: dict[str, Any]) -> None:
    """Write the snapshot YAML atomically (write-to-temp + rename).

    Atomicity matters because a half-written snapshot would break
    the seed loader (YAML parse error) and the file is the only
    handoff between scraper and seeder.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                payload,
                f,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        # ``os.replace`` is atomic on POSIX for same-filesystem renames,
        # which is guaranteed here because both paths share ``out_path.parent``.
        os.replace(tmp_path, out_path)
    except Exception:
        # Clean up the half-written temp file so a retry doesn't trip
        # over stale state. ``out_path`` itself is either unchanged
        # (the previous good snapshot) or absent.
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def fetch_from_network(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Future seam for a live NOIC scrape.

    TODO: implement when source is finalized.

    Per the M2.T3 ticket, the MVP defaults to the fixture path so the
    script is runnable without scraping. A live implementation should:

    1. Use the ``browser-automation`` or
       ``scraping-br-residential-proxy`` skill (see code-gym-workflow).
    2. Return a dict matching the seed schema (``{"problems": [...]}``).
    3. Be best-effort: on any error, fall back to the fixture so the
       snapshot is always written and ``scrape_noic`` always exits 0.

    Keeping this stub distinct from ``scrape_noic`` makes the seam
    obvious for the next agent who picks this ticket up.
    """
    raise NotImplementedError(
        "fetch_from_network is the future live-scrape seam; until it's "
        "implemented, scrape_noic uses the local fixture per ADR-0006 "
        "and the ticket's best-effort guidance."
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _default_snapshots_dir() -> Path:
    """Resolve the snapshots dir from the environment or fall back to
    the repo default.

    Tests redirect output with the ``NOIC_SNAPSHOT_DIR`` env var so
    they don't litter the real ``data/snapshots/`` on every run.
    """
    env = os.environ.get("NOIC_SNAPSHOT_DIR")
    return Path(env) if env else DEFAULT_SNAPSHOTS_DIR


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — ``python -m scripts.scrape.noic``.

    Best-effort: any uncaught error is logged to stderr and the
    script still exits 0 if a snapshot was written, so an empty or
    partial scrape never blocks CI or local runs.
    """
    snapshots_dir = _default_snapshots_dir()
    try:
        out_path = scrape_noic(snapshots_dir)
    except FileNotFoundError as exc:
        # No fixture AND no network — we have nothing to write. This
        # is the only path that legitimately exits non-zero: the
        # repo is in an unrecoverable state.
        print(f"[scrape.noic] FATAL: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover — defensive net
        print(f"[scrape.noic] WARN: falling back silently: {exc}", file=sys.stderr)
        return 0

    print(f"[scrape.noic] wrote {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
