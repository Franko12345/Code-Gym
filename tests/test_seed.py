"""Tests for the content schema (topics, problems, test_cases) and the
seed loader (``scripts.seed``).

Per M2.T1: ``app/db.py.init_db()`` must create the three tables defined
in the v0.1.0 plan with ``PRAGMA foreign_keys=ON``. ``scripts.seed``
loads ``topics.yaml`` / ``obi_problems.yaml`` style files and is
idempotent — running it twice with the same file produces the same row
count.

These tests run entirely on a temporary SQLite file (``tmp_path``), so
they never touch the real ``data/code_gym.db``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import init_db, get_connection
from scripts.seed import load_yaml_file, seed_from_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a fresh, empty SQLite path for each test."""
    return tmp_path / "code_gym.db"


@pytest.fixture()
def initialized_db(db_path: Path):
    """Run ``init_db(db_path)`` and return the path."""
    init_db(db_path)
    return db_path


@pytest.fixture()
def topics_yaml(tmp_path: Path) -> Path:
    """Minimal topics fixture — two OBI F1 topics in display order."""
    p = tmp_path / "topics.yaml"
    p.write_text(
        "topics:\n"
        "  - slug: arrays\n"
        "    name: Vetores\n"
        "    obi_phase: F1\n"
        "    order_index: 10\n"
        "  - slug: graphs\n"
        "    name: Grafos\n"
        "    obi_phase: F2\n"
        "    order_index: 20\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def problems_yaml(tmp_path: Path) -> Path:
    """Minimal problems fixture — one problem with two test cases
    (one sample, one graded)."""
    p = tmp_path / "obi_problems.yaml"
    p.write_text(
        "problems:\n"
        "  - slug: soma-simples\n"
        "    title: Soma Simples\n"
        "    topic_slug: arrays\n"
        "    difficulty: 1\n"
        "    statement_md: |\n"
        "      Leia dois inteiros e imprima a soma.\n"
        "    input_format_md: Uma linha com dois inteiros `a` e `b`.\n"
        "    output_format_md: Uma linha com `a + b`.\n"
        "    examples_json: |\n"
        "      [{\"stdin\": \"1 2\", \"stdout\": \"3\", \"explanation\": \"\"}]\n"
        "    source: OBI 2019 F1\n"
        "    source_url: https://example.com/obi/2019/f1/soma\n"
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
# Schema creation
# ---------------------------------------------------------------------------


def test_init_db_creates_topics_table(initialized_db: Path) -> None:
    """``init_db`` must create the ``topics`` table."""
    with get_connection(initialized_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='topics'"
        ).fetchall()
    assert rows, "expected topics table to exist after init_db"


def test_init_db_creates_problems_table(initialized_db: Path) -> None:
    """``init_db`` must create the ``problems`` table."""
    with get_connection(initialized_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='problems'"
        ).fetchall()
    assert rows, "expected problems table to exist after init_db"


def test_init_db_creates_test_cases_table(initialized_db: Path) -> None:
    """``init_db`` must create the ``test_cases`` table."""
    with get_connection(initialized_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='test_cases'"
        ).fetchall()
    assert rows, "expected test_cases table to exist after init_db"


def test_init_db_enables_foreign_keys(initialized_db: Path) -> None:
    """``init_db`` must enable ``PRAGMA foreign_keys=ON`` per ADR."""
    with get_connection(initialized_db) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1, "expected PRAGMA foreign_keys=ON"


def test_problems_topic_id_references_topics(initialized_db: Path) -> None:
    """``problems.topic_id`` must reference ``topics(id)`` — inserting a
    problem with a missing topic must raise an IntegrityError."""
    with get_connection(initialized_db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO problems (slug, title, topic_id, difficulty, "
                "statement_md, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("orphan", "Orphan", 9999, 1, "x", "2026-08-09T00:00:00"),
            )


def test_test_cases_problem_id_references_problems(initialized_db: Path) -> None:
    """``test_cases.problem_id`` must reference ``problems(id)``."""
    with get_connection(initialized_db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO test_cases (problem_id, stdin, expected_stdout) "
                "VALUES (?, ?, ?)",
                (9999, "1\n", "1\n"),
            )


def test_init_db_is_idempotent(db_path: Path) -> None:
    """Calling ``init_db`` twice must not raise (CREATE IF NOT EXISTS)."""
    init_db(db_path)
    init_db(db_path)  # must not raise
    with get_connection(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('topics', 'problems', 'test_cases')"
        ).fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def test_load_yaml_file_returns_dict(topics_yaml: Path) -> None:
    """``load_yaml_file`` must return a dict with the expected keys."""
    data = load_yaml_file(topics_yaml)
    assert isinstance(data, dict)
    assert "topics" in data
    assert isinstance(data["topics"], list)
    assert len(data["topics"]) == 2


# ---------------------------------------------------------------------------
# Seed loader — topics
# ---------------------------------------------------------------------------


def test_seed_topics_inserts_rows(
    initialized_db: Path, topics_yaml: Path
) -> None:
    """Seeding topics.yaml must insert exactly the rows in the file."""
    summary = seed_from_file(initialized_db, topics_yaml)
    assert summary["table"] == "topics"
    assert summary["inserted"] == 2

    with get_connection(initialized_db) as conn:
        rows = conn.execute(
            "SELECT slug, name, obi_phase, order_index FROM topics "
            "ORDER BY order_index"
        ).fetchall()
    # Convert sqlite3.Row to tuples for stable equality across Python
    # versions (Row's __eq__ with tuples is brittle).
    assert [tuple(r) for r in rows] == [
        ("arrays", "Vetores", "F1", 10),
        ("graphs", "Grafos", "F2", 20),
    ]


def test_seed_topics_is_idempotent(
    initialized_db: Path, topics_yaml: Path
) -> None:
    """Re-running the seed on the same YAML must not duplicate rows."""
    seed_from_file(initialized_db, topics_yaml)
    summary = seed_from_file(initialized_db, topics_yaml)
    assert summary["inserted"] == 0

    with get_connection(initialized_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert count == 2


def test_seed_topics_with_optional_obi_phase(initialized_db: Path, tmp_path: Path) -> None:
    """``obi_phase`` is optional (NULL allowed for auxiliary topics)."""
    p = tmp_path / "topics_aux.yaml"
    p.write_text(
        "topics:\n"
        "  - slug: misc\n"
        "    name: Miscelânea\n"
        "    order_index: 99\n",
        encoding="utf-8",
    )
    seed_from_file(initialized_db, p)
    with get_connection(initialized_db) as conn:
        phase = conn.execute(
            "SELECT obi_phase FROM topics WHERE slug = ?", ("misc",)
        ).fetchone()[0]
    assert phase is None


# ---------------------------------------------------------------------------
# Seed loader — problems + test_cases
# ---------------------------------------------------------------------------


def test_seed_problems_inserts_problem_and_test_cases(
    initialized_db: Path, topics_yaml: Path, problems_yaml: Path
) -> None:
    """Seeding problems.yaml must insert the problem AND its test cases."""
    seed_from_file(initialized_db, topics_yaml)
    summary = seed_from_file(initialized_db, problems_yaml)
    assert summary["table"] == "problems"
    assert summary["inserted"] == 1
    assert summary["test_cases_inserted"] == 2

    with get_connection(initialized_db) as conn:
        problem = conn.execute(
            "SELECT slug, title, topic_id, difficulty FROM problems"
        ).fetchone()
        assert problem[0] == "soma-simples"
        assert problem[1] == "Soma Simples"
        assert problem[2] == 1  # FK to arrays topic (id=1)
        assert problem[3] == 1

        cases = conn.execute(
            "SELECT stdin, expected_stdout, is_sample, weight "
            "FROM test_cases ORDER BY id"
        ).fetchall()
    assert [tuple(r) for r in cases] == [
        ("1 2\n", "3\n", 1, 1),
        ("10 20\n", "30\n", 0, 2),
    ]


def test_seed_problems_raises_on_unknown_topic(
    initialized_db: Path, tmp_path: Path
) -> None:
    """A problem whose ``topic_slug`` is not in the DB must raise loudly.

    The seed loader validates the FK target up front and raises
    ``ValueError`` with a helpful message — louder than a SQLite
    ``IntegrityError`` which would not name the missing slug.
    """
    p = tmp_path / "broken.yaml"
    p.write_text(
        "problems:\n"
        "  - slug: orphan\n"
        "    title: Orphan\n"
        "    topic_slug: does-not-exist\n"
        "    difficulty: 1\n"
        "    statement_md: x\n"
        "    input_format_md: ''\n"
        "    output_format_md: ''\n"
        "    examples_json: ''\n"
        "    source: ''\n"
        "    source_url: ''\n"
        "    test_cases: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does-not-exist"):
        seed_from_file(initialized_db, p)


def test_seed_problems_is_idempotent(
    initialized_db: Path, topics_yaml: Path, problems_yaml: Path
) -> None:
    """Re-running the seed on problems.yaml must not duplicate rows or
    test cases."""
    seed_from_file(initialized_db, topics_yaml)
    seed_from_file(initialized_db, problems_yaml)
    summary = seed_from_file(initialized_db, problems_yaml)
    assert summary["inserted"] == 0
    assert summary["test_cases_inserted"] == 0

    with get_connection(initialized_db) as conn:
        p_count = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        tc_count = conn.execute(
            "SELECT COUNT(*) FROM test_cases"
        ).fetchone()[0]
    assert p_count == 1
    assert tc_count == 2


# ---------------------------------------------------------------------------
# CLI smoke (the entry point promised in the plan)
# ---------------------------------------------------------------------------


def test_cli_runs_seed_via_module(
    initialized_db: Path,
    topics_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m scripts.seed --file <path>`` must populate the DB.

    We invoke the module's ``main`` with ``sys.argv`` patched.
    """
    from scripts import seed as seed_mod

    monkeypatch.setattr(
        "sys.argv", ["scripts.seed", "--file", str(topics_yaml)]
    )
    # Allow the CLI to find the test DB instead of the production path
    monkeypatch.setattr(seed_mod, "DEFAULT_DB_PATH", initialized_db)

    seed_mod.main()

    with get_connection(initialized_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert count == 2
