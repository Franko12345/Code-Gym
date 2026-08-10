"""Tests for ``scripts.scrape.noic`` — M2.T3 NOIC snapshot YAML scraper.

The scraper must:

1. Write ``data/snapshots/noic-YYYY-MM-DD.yaml`` whose top-level shape
   matches the seed loader (``scripts.seed``): a ``problems`` list whose
   entries carry ``slug / title / topic_slug / difficulty / statement_md
   / input_format_md / output_format_md / examples_json / source /
   source_url / test_cases``.

2. Be callable against a hand-authored fixture (no network) per ADR-0006
   and the ticket's "best-effort" guidance — when a real NOIC source
   isn't reachable, the script copies the local fixture and exits 0.

3. Be idempotent: a second run on the same day overwrites the snapshot
   file cleanly (no duplicate test_cases, no appended garbage).

Tests observe behavior at the **public seams** (the YAML file written,
and the entry-point CLI), not internal helpers — so refactors that
keep the contract intact don't break them.
"""

from __future__ import annotations

import datetime as dt
import importlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.seed import seed_from_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "data" / "snapshots" / "noic-fixture.yaml"


@pytest.fixture()
def snapshots_dir(tmp_path: Path) -> Path:
    """A fresh ``data/snapshots`` for each test — never touches the
    real repo directory."""
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    """Minimal NOIC fixture matching the seed schema: one problem
    under ``topic_slug: matematica`` with two test cases."""
    p = tmp_path / "noic-fixture.yaml"
    p.write_text(
        "problems:\n"
        "  - slug: noic-soma\n"
        "    title: Soma (NOIC)\n"
        "    topic_slug: matematica\n"
        "    difficulty: 1\n"
        "    statement_md: |\n"
        "      Leia dois inteiros e imprima a soma.\n"
        "    input_format_md: Uma linha com dois inteiros `a` e `b`.\n"
        "    output_format_md: Uma linha com `a + b`.\n"
        "    examples_json: |\n"
        "      [{\"stdin\": \"1 2\", \"stdout\": \"3\", \"explanation\": \"\"}]\n"
        "    source: NOIC\n"
        "    source_url: https://noic.com.br/\n"
        "    test_cases:\n"
        "      - stdin: |\n"
        "          1 2\n"
        "        expected_stdout: |\n"
        "          3\n"
        "        is_sample: true\n"
        "        weight: 1\n"
        "      - stdin: |\n"
        "          10 20\n"
        "        expected_stdout: |\n"
        "          30\n"
        "        is_sample: false\n"
        "        weight: 2\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_scrape_module():
    """Import the scrape module fresh each time (tests may monkeypatch
    the fixture path)."""
    return importlib.import_module("scripts.scrape.noic")


# ---------------------------------------------------------------------------
# 1. YAML output matches the seed loader schema
# ---------------------------------------------------------------------------


def test_scrape_writes_yaml_matching_seed_schema(
    snapshots_dir: Path, fixture_path: Path
) -> None:
    """The snapshot file written by the scraper must be loadable by
    ``scripts.seed.seed_from_file`` end-to-end: topics + problems +
    test_cases all land in the DB with the expected row counts.

    This is the strongest possible contract check — schema drift in
    the scraper surfaces as a seed-load failure.
    """
    scrape = _load_scrape_module()
    db_path = snapshots_dir.parent / "test.db"

    out_path = scrape.scrape_noic(
        snapshots_dir, fixture_path=fixture_path
    )

    assert out_path.exists()
    assert out_path.parent == snapshots_dir
    assert out_path.name.startswith("noic-")
    assert out_path.name.endswith(".yaml")

    # Seed topics first (the fixture references ``matematica``).
    topics_yaml = snapshots_dir / "topics.yaml"
    topics_yaml.write_text(
        "topics:\n"
        "  - slug: matematica\n"
        "    name: Matemática\n"
        "    obi_phase: F1\n"
        "    order_index: 10\n",
        encoding="utf-8",
    )
    topics_summary = seed_from_file(db_path, topics_yaml)
    assert topics_summary["inserted"] == 1

    problems_summary = seed_from_file(db_path, out_path)
    assert problems_summary["table"] == "problems"
    assert problems_summary["inserted"] == 1
    assert problems_summary["test_cases_inserted"] == 2

    # And the on-disk YAML itself parses to the expected shape.
    data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "problems" in data
    assert len(data["problems"]) == 1
    p = data["problems"][0]
    for required_key in (
        "slug",
        "title",
        "topic_slug",
        "difficulty",
        "statement_md",
        "input_format_md",
        "output_format_md",
        "examples_json",
        "source",
        "source_url",
        "test_cases",
    ):
        assert required_key in p, f"missing key {required_key!r}"
    assert len(p["test_cases"]) == 2


# ---------------------------------------------------------------------------
# 2. Callable with a fixture, no network
# ---------------------------------------------------------------------------


def test_scrape_is_callable_with_local_fixture_no_network(
    snapshots_dir: Path, fixture_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scraper must run to completion without touching the network.

    We assert this by blocking all ``socket.create_connection`` and
    ``socket.socket`` calls — if the implementation tries to reach
    noic.com.br, the test fails. Per ADR-0006 and the ticket's
    best-effort guidance, the fixture path is the default in MVP.
    """
    import socket

    def _blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "scrape_noic attempted a network call; MVP fixture path "
            "must not hit the network"
        )

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "socket", _blocked)

    scrape = _load_scrape_module()
    out_path = scrape.scrape_noic(
        snapshots_dir, fixture_path=fixture_path
    )

    assert out_path.exists()
    assert out_path.name.startswith("noic-")
    assert out_path.name.endswith(".yaml")
    data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert data["problems"][0]["slug"] == "noic-soma"


def test_default_fixture_path_exists_in_repo() -> None:
    """The hand-authored fixture committed at ``data/snapshots/noic-fixture.yaml``
    must exist and parse to the seed schema — the scraper's default
    fixture path is the MVP source of truth for NOIC content."""
    assert FIXTURE_PATH.exists(), (
        f"expected fixture at {FIXTURE_PATH} so the scraper is runnable "
        "without a network source (ADR-0006 + ticket best-effort)"
    )
    data = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert "problems" in data
    assert isinstance(data["problems"], list)
    assert len(data["problems"]) >= 1


# ---------------------------------------------------------------------------
# 3. Second call overwrites cleanly
# ---------------------------------------------------------------------------


def test_second_call_overwrites_cleanly(
    snapshots_dir: Path, fixture_path: Path
) -> None:
    """Two scrape runs on the same day must produce one snapshot file
    (overwrite, not append) and the second run's content must still
    load into the seed loader without duplicating rows.

    The first contract (single file) guards against accidental
    per-run suffixes. The second contract (no duplicate rows) guards
    against e.g. an append-mode bug in the writer.
    """
    scrape = _load_scrape_module()

    first = scrape.scrape_noic(snapshots_dir, fixture_path=fixture_path)
    first_bytes = first.read_bytes()

    # Run again on the same day — output path should be stable.
    second = scrape.scrape_noic(snapshots_dir, fixture_path=fixture_path)
    assert second == first, (
        "scrape_noic must produce a deterministic per-day path so "
        "re-runs overwrite, not pile up"
    )

    # File still exists, single instance, same content.
    matches = list(snapshots_dir.glob("noic-*.yaml"))
    assert len(matches) == 1
    assert matches[0].read_bytes() == first_bytes

    # And the seed loader is idempotent on it (no row duplication).
    db_path = snapshots_dir.parent / "test.db"
    topics_yaml = snapshots_dir / "topics.yaml"
    topics_yaml.write_text(
        "topics:\n"
        "  - slug: matematica\n"
        "    name: Matemática\n"
        "    obi_phase: F1\n"
        "    order_index: 10\n",
        encoding="utf-8",
    )
    seed_from_file(db_path, topics_yaml)
    seed_from_file(db_path, second)
    again = seed_from_file(db_path, second)
    assert again["inserted"] == 0
    assert again["test_cases_inserted"] == 0


def test_scrape_accepts_explicit_today(
    snapshots_dir: Path, fixture_path: Path
) -> None:
    """``today`` parameter must drive the filename so tests are
    deterministic and don't depend on wall-clock time."""
    scrape = _load_scrape_module()
    fixed = dt.date(2026, 8, 10)

    out_path = scrape.scrape_noic(
        snapshots_dir,
        fixture_path=fixture_path,
        today=fixed,
    )
    assert out_path.name == "noic-2026-08-10.yaml"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_module_entry_point_runs_exits_zero(tmp_path: Path) -> None:
    """``python -m scripts.scrape.noic`` must exit 0 and write a
    snapshot file. We point SCRAPERS at a tmp snapshots dir so we
    don't touch the real ``data/snapshots/`` from a test run."""
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()

    # ``scripts/scrape/noic.py`` reads its output dir from an env var
    # (NOIC_SNAPSHOT_DIR) so tests can redirect without monkey-patching
    # the module. We rely on that env knob below.
    env = {
        "NOIC_SNAPSHOT_DIR": str(snapshots),
        "PYTHONPATH": str(REPO_ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.scrape.noic"],
        cwd=REPO_ROOT,
        env={**__import__("os").environ, **env},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"module entry point must exit 0; got {proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )

    files = list(snapshots.glob("noic-*.yaml"))
    assert files, "expected at least one noic-*.yaml snapshot to be written"
