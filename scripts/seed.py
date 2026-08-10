"""Seed the content DB from YAML files.

Usage:
    python -m scripts.seed --file data/seed/topics.yaml
    python -m scripts.seed --file data/seed/obi_problems.yaml

YAML shape (top-level key chooses the table):
  topics.yaml:      {topics: [{slug, name, obi_phase, order_index}, ...]}
  *_problems.yaml:  {problems: [{slug, title, topic_slug, difficulty,
                                statement_md, input_format_md,
                                output_format_md, examples_json,
                                source, source_url,
                                test_cases: [{stdin, expected_stdout,
                                             is_sample, weight}]}, ...]}

Idempotency: rows are inserted with ``INSERT ... ON CONFLICT(slug)
DO NOTHING`` (topics/problems), and test_cases are deleted-then-inserted
per problem so re-running never duplicates or strands stale cases.

Per ADR / task constraints:
- ``app.db.init_db()`` is called before any insert so a fresh checkout
  can ``python -m scripts.seed --file ...`` without a separate step.
- Foreign keys are ON via ``app.db.get_connection``; a missing
  ``topic_slug`` raises ``sqlite3.IntegrityError`` (loud failure beats
  silent orphan).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

import yaml

from app.db import DEFAULT_DB_PATH, get_connection, init_db


# `ibi_phase` is optional for auxiliary topics. Keep the column
# nullable in the schema; default is NULL.
ISO_NOW = lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its top-level mapping."""
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"YAML root must be a mapping, got {type(data).__name__} "
            f"in {path}"
        )
    return data


# ---------------------------------------------------------------------------
# Seeding: topics
# ---------------------------------------------------------------------------


def _seed_topics(conn, rows: list[dict[str, Any]]) -> int:
    """Insert topics with ``ON CONFLICT(slug) DO NOTHING``. Returns count
    of NEW rows inserted (existing slugs are skipped, not updated)."""
    inserted = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slug) DO NOTHING
            """,
            (
                row["slug"],
                row["name"],
                row.get("obi_phase"),
                int(row["order_index"]),
            ),
        )
        if cur.rowcount > 0:
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Seeding: problems (+ nested test_cases)
# ---------------------------------------------------------------------------


def _resolve_topic_id(conn, topic_slug: str) -> int:
    """Look up the integer id for a topic slug. Raises if missing — a
    missing FK target should be loud, not silent."""
    row = conn.execute(
        "SELECT id FROM topics WHERE slug = ?", (topic_slug,)
    ).fetchone()
    if row is None:
        raise ValueError(
            f"topic_slug {topic_slug!r} not found in topics table; "
            "seed topics.yaml first"
        )
    return int(row[0])


def _seed_problems(
    conn, rows: list[dict[str, Any]]
) -> tuple[int, int]:
    """Insert problems + their test_cases. Returns (problems_inserted,
    test_cases_inserted). Re-running with same YAML inserts 0 of each."""
    problems_inserted = 0
    test_cases_inserted = 0

    for row in rows:
        topic_id = _resolve_topic_id(conn, row["topic_slug"])
        cur = conn.execute(
            """
            INSERT INTO problems (
                slug, title, topic_id, difficulty,
                statement_md, input_format_md, output_format_md,
                examples_json, source, source_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO NOTHING
            """,
            (
                row["slug"],
                row["title"],
                topic_id,
                int(row["difficulty"]),
                row["statement_md"],
                row.get("input_format_md") or "",
                row.get("output_format_md") or "",
                row.get("examples_json") or "",
                row.get("source") or "",
                row.get("source_url") or "",
                ISO_NOW(),
            ),
        )
        if cur.rowcount == 0:
            # Existing problem — skip test_cases seeding for it too;
            # otherwise we'd duplicate cases on every re-run.
            continue
        problems_inserted += 1

        # Resolve the new problem's id (INSERT above guaranteed it
        # exists, so a SELECT by slug returns exactly one row).
        problem_id = int(
            conn.execute(
                "SELECT id FROM problems WHERE slug = ?", (row["slug"],)
            ).fetchone()[0]
        )

        cases = row.get("test_cases") or []
        for case in cases:
            conn.execute(
                """
                INSERT INTO test_cases (
                    problem_id, stdin, expected_stdout, is_sample, weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    problem_id,
                    case["stdin"],
                    case["expected_stdout"],
                    1 if case.get("is_sample") else 0,
                    int(case.get("weight", 1)),
                ),
            )
            test_cases_inserted += 1

    return problems_inserted, test_cases_inserted


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def seed_from_file(
    db_path: Path | str, yaml_path: Path | str
) -> dict[str, Any]:
    """Initialize the DB if needed and seed ``yaml_path`` into it.

    Returns a summary dict so tests + CLI can assert what happened:
      {"table": "topics", "file": "...", "inserted": N}
      {"table": "problems", "file": "...", "inserted": N,
       "test_cases_inserted": M}
    """
    db_path = Path(db_path)
    yaml_path = Path(yaml_path)

    # Fresh checkout safety: ensure schema exists before inserting.
    init_db(db_path)

    data = load_yaml_file(yaml_path)

    with get_connection(db_path) as conn:
        if "topics" in data:
            n = _seed_topics(conn, data["topics"])
            return {
                "table": "topics",
                "file": str(yaml_path),
                "inserted": n,
            }
        if "problems" in data:
            p_n, tc_n = _seed_problems(conn, data["problems"])
            return {
                "table": "problems",
                "file": str(yaml_path),
                "inserted": p_n,
                "test_cases_inserted": tc_n,
            }
        raise ValueError(
            f"YAML {yaml_path} must contain a top-level 'topics' or "
            f"'problems' key; got keys: {list(data)}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.seed",
        description="Seed Code-Gym content from a YAML file.",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to the YAML file (topics.yaml or *_problems.yaml).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite DB file (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    summary = seed_from_file(args.db, args.file)
    print(
        f"[seed] {summary['file']} -> {summary['table']}: "
        f"inserted={summary['inserted']}"
        + (
            f", test_cases_inserted={summary['test_cases_inserted']}"
            if "test_cases_inserted" in summary
            else ""
        )
    )
    return summary


if __name__ == "__main__":  # pragma: no cover
    sys.exit(0 if main() else 1)
