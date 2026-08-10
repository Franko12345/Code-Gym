"""Tests for the OBI scraper (``scripts.scrape.obi``).

Per M2.T2: ``scripts/scrape/obi.py`` fetches OBI problem statements +
sample test cases from the public static pages at
``https://olimpiada.ic.unicamp.br/pratique/p1/<year>/<phase>/<slug>/``
and writes a snapshot YAML matching the M2.T1 schema (loadable by
``scripts.seed.seed_from_file``).

These tests MUST NOT hit the network. They inject a fetcher that
reads from local fixture HTML files, so they run offline in CI.

Per ADR-0006: best-effort. The scraper logs failures and exits 0
even on partial success. Tests exercise both the happy path and the
failure path (a problem that 404s during fetch must not stop the
rest of the snapshot).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scripts.scrape import obi as obi_scrape
from scripts.scrape.obi import (
    OUTPUT_DIRNAME,
    SnapshotSummary,
    parse_problem_page,
    scrape_obi,
    snapshot_filename,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
# Committed alongside the tests (NOT under the gitignored ``data/``) so a
# clean CI checkout has them. Captured from the live site on 2026-08-10.
FIXTURE_INDEX = FIXTURE_DIR / "obi-p1-index.html"  # the /pratique/p1/ index page
FIXTURE_IDADE = (
    FIXTURE_DIR / "obi-p1-2019-f1-idade.html"
)  # the /pratique/p1/2019/f1/idade/ page


def _file_fetcher(url: str) -> str:
    """Fake fetcher that returns the contents of a local fixture file
    based on which URL fragment it sees.

    - Any URL ending in ``/pratique/p1/`` returns the index fixture.
    - Any URL containing ``/2019/f1/idade/`` returns the problem fixture.
    - Any URL containing ``/2019/f1/broken/`` raises as if the page 404'd.
    """
    if url.rstrip("/").endswith("/pratique/p1"):
        return FIXTURE_INDEX.read_text(encoding="utf-8")
    if "/2019/f1/idade/" in url:
        return FIXTURE_IDADE.read_text(encoding="utf-8")
    if "/2019/f1/broken/" in url:
        raise FileNotFoundError(f"404: {url}")
    raise FileNotFoundError(f"no fixture mapped for {url}")


@pytest.fixture()
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> dt.date:
    """Freeze the scraper's idea of 'today' so snapshot filenames are
    deterministic across test runs."""
    today = dt.date(2026, 8, 10)
    monkeypatch.setattr(obi_scrape, "_today", lambda: today)
    return today


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------


def test_parse_problem_page_extracts_title_and_examples() -> None:
    """``parse_problem_page`` must pull the title, input/output
    formats, and the example (stdin, expected_stdout) pairs from a
    real OBI problem page."""
    html = FIXTURE_IDADE.read_text(encoding="utf-8")
    parsed = parse_problem_page(
        html,
        source="OBI 2019 F1",
        source_url="https://olimpiada.ic.unicamp.br/pratique/p1/2019/f1/idade/",
    )

    # Title comes from <h1 class="center">
    assert parsed["title"] == "A idade de Dona Mônica"
    # Input format comes from the <h3>Entrada</h3> section
    assert "idade de dona Mônica" in parsed["input_format_md"]
    # Output format comes from the <h3>Saída</h3> section
    assert "idade do filho mais velho" in parsed["output_format_md"]
    # Statement is the prose before <h3>Entrada</h3>
    assert "três filhos" in parsed["statement_md"]
    # Two examples extracted (52/14/18 -> 20, 47/21/9 -> 21)
    assert len(parsed["examples"]) == 2
    assert parsed["examples"][0][0].strip() == "52\n14\n18"
    assert parsed["examples"][0][1].strip() == "20"
    assert parsed["examples"][1][0].strip() == "47\n21\n9"
    assert parsed["examples"][1][1].strip() == "21"


def test_parse_problem_page_returns_expected_keys() -> None:
    """The dict returned by ``parse_problem_page`` must contain all
    the fields ``scripts.seed._seed_problems`` expects to read
    (minus topic_slug which the scraper fills separately)."""
    html = FIXTURE_IDADE.read_text(encoding="utf-8")
    parsed = parse_problem_page(
        html,
        source="OBI 2019 F1",
        source_url="https://example.com/x",
    )
    expected_keys = {
        "title",
        "statement_md",
        "input_format_md",
        "output_format_md",
        "examples",
        "source",
        "source_url",
    }
    assert expected_keys.issubset(parsed.keys()), (
        f"missing keys: {expected_keys - parsed.keys()}"
    )


# ---------------------------------------------------------------------------
# Snapshot filename
# ---------------------------------------------------------------------------


def test_snapshot_filename_uses_today_date(fixed_today: dt.date) -> None:
    """The snapshot path must be ``data/snapshots/obi-YYYY-MM-DD.yaml``
    so it sorts chronologically and is idempotent within a day."""
    path = snapshot_filename(fixed_today)
    assert path.name == "obi-2026-08-10.yaml"
    assert path.parent.name == OUTPUT_DIRNAME


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_write_snapshot_is_idempotent(
    tmp_path: Path, fixed_today: dt.date
) -> None:
    """Writing the snapshot twice with the same date must overwrite
    the existing file (no exception, same path)."""
    payload = {
        "problems": [
            {
                "slug": "idade",
                "title": "A idade de Dona Mônica",
                "topic_slug": "misc",
                "difficulty": 1,
                "statement_md": "x",
                "input_format_md": "",
                "output_format_md": "",
                "examples_json": "",
                "source": "OBI 2019 F1",
                "source_url": "https://example.com/idade",
                "test_cases": [],
            }
        ]
    }
    out_dir = tmp_path / "data" / "snapshots"
    # Write into tmp_path, never the real REPO_ROOT: this test exercises
    # the lower-level writer directly, so no OUTPUT_DIR patching is needed.
    out_path_1 = out_dir / "obi-2026-08-10.yaml"
    out_path_2 = out_dir / "obi-2026-08-10.yaml"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_snapshot(payload, out_path_1)
    size_1 = out_path_1.stat().st_size
    write_snapshot(payload, out_path_2)  # must NOT raise
    size_2 = out_path_2.stat().st_size
    assert size_1 == size_2
    assert out_path_1 == out_path_2


def test_second_call_same_date_overwrites(
    tmp_path: Path,
    fixed_today: dt.date,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling ``scrape_obi`` twice in one day must overwrite the
    snapshot, not append or fail."""
    # Redirect output to tmp_path so we don't touch the real REPO
    monkeypatch.setattr(obi_scrape, "OUTPUT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(
        obi_scrape, "_default_fetcher", _file_fetcher
    )

    snap1 = scrape_obi(fetcher=_file_fetcher, year=2019, phase="f1")
    snap2 = scrape_obi(fetcher=_file_fetcher, year=2019, phase="f1")
    assert snap1.snapshot_path == snap2.snapshot_path
    assert Path(snap1.snapshot_path).exists()


# ---------------------------------------------------------------------------
# scrape_obi() — driven by injected fetcher (no real network)
# ---------------------------------------------------------------------------


def test_scrape_obi_produces_yaml_in_seed_schema(
    fixed_today: dt.date,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The snapshot YAML produced by ``scrape_obi`` MUST be loadable
    by ``scripts.seed.seed_from_file`` end-to-end. This is the
    round-trip integration test."""
    from app.db import init_db, get_connection
    from scripts.seed import seed_from_file

    monkeypatch.setattr(obi_scrape, "OUTPUT_DIR", tmp_path / "snapshots")

    result = scrape_obi(fetcher=_file_fetcher, year=2019, phase="f1")
    yaml_path = Path(result.snapshot_path)
    assert yaml_path.exists(), "snapshot YAML must be written"

    # Seed it into a fresh DB. Topics first (seed requires them to
    # exist before problems can be inserted because of FK).
    topics_yaml = tmp_path / "topics.yaml"
    topics_yaml.write_text(
        "topics:\n"
        "  - slug: misc\n"
        "    name: Miscelânea\n"
        "    obi_phase: F1\n"
        "    order_index: 0\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "code_gym.db"
    init_db(db_path)
    t_summary = seed_from_file(db_path, topics_yaml)
    assert t_summary["inserted"] == 1, "topic must seed"

    p_summary = seed_from_file(db_path, yaml_path)
    assert p_summary["table"] == "problems"
    assert p_summary["inserted"] >= 1
    assert p_summary["test_cases_inserted"] >= 2  # at least the 2 examples

    # Verify rows actually landed.
    with get_connection(db_path) as conn:
        slugs = [
            r[0]
            for r in conn.execute(
                "SELECT slug FROM problems ORDER BY id"
            ).fetchall()
        ]
    assert "idade" in slugs


def test_scrape_obi_skips_failed_problems(
    fixed_today: dt.date,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per ADR-0006 (best-effort), a transient fetch failure on ONE
    problem must NOT abort the whole run. The other problems still
    land in the snapshot, and the script reports the failure."""

    # Patch the index fixture so it lists two problems: one that
    # resolves (idade) and one that 404s (broken).
    html = FIXTURE_INDEX.read_text(encoding="utf-8")
    html = html.replace(
        'href="/pratique/p1/2019/f1/idade/"',
        'href="/pratique/p1/2019/f1/idade/" '
        'data-test="ok" '
        'data-broken="https://olimpiada.ic.unicamp.br/pratique/p1/2019/f1/broken/"',
    )

    def _broken_fetcher(url: str) -> str:
        if url.rstrip("/").endswith("/pratique/p1"):
            # Inject an extra broken link into the HTML so the index
            # parser sees two problems.
            return html.replace(
                "pratique/p1/2019/f1/amigos/",
                "pratique/p1/2019/f1/amigos/ "
                "obi-broken-href=\"https://olimpiada.ic.unicamp.br/pratique/p1/2019/f1/broken/\"",
            ) + '<li><a href="/pratique/p1/2019/f1/broken/">Broken</a></li>'
        if "/2019/f1/idade/" in url:
            return FIXTURE_IDADE.read_text(encoding="utf-8")
        # Anything else (incl. the broken link) -> raise
        raise FileNotFoundError(f"simulated 404: {url}")

    monkeypatch.setattr(obi_scrape, "OUTPUT_DIR", tmp_path / "snapshots")
    summary = scrape_obi(fetcher=_broken_fetcher, year=2019, phase="f1")
    # The idade problem survived; broken one was logged.
    assert summary.problems_fetched >= 1
    assert summary.problems_failed >= 1
    # Snapshot file still written.
    assert Path(summary.snapshot_path).exists()


def test_scrape_obi_returns_summary_dataclass(
    fixed_today: dt.date,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``scrape_obi`` must return a ``SnapshotSummary`` so callers
    can inspect what happened without re-parsing logs."""
    monkeypatch.setattr(obi_scrape, "OUTPUT_DIR", tmp_path / "snapshots")
    result = scrape_obi(fetcher=_file_fetcher, year=2019, phase="f1")
    assert isinstance(result, SnapshotSummary)
    assert isinstance(result.problems_fetched, int)
    assert isinstance(result.problems_failed, int)
    assert isinstance(result.failed_slugs, list)
    assert result.snapshot_path.endswith("obi-2026-08-10.yaml")


# ---------------------------------------------------------------------------
# CLI smoke (the entry point promised in the plan)
# ---------------------------------------------------------------------------


def test_cli_runs_via_module(
    fixed_today: dt.date,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m scripts.scrape.obi`` must write a snapshot and
    exit 0. We invoke ``main`` directly with ``sys.argv`` patched,
    and a custom fetcher so no network is touched."""

    from scripts.scrape import obi as obi_mod

    monkeypatch.setattr(obi_scrape, "OUTPUT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr("sys.argv", ["scripts.scrape.obi"])
    monkeypatch.setattr(obi_mod, "_default_fetcher", _file_fetcher)

    rc = obi_mod.main()
    # main() returns 0 on success and on partial-success (best-effort)
    assert rc == 0
    snap = tmp_path / "snapshots" / "obi-2026-08-10.yaml"
    assert snap.exists()
