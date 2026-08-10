"""SQLite connection helpers + schema bootstrap.

Per ADR-0001 the DB is a single SQLite file (``data/code_gym.db``)
with WAL mode. ``init_db`` creates the tables for the current
milestone using ``CREATE TABLE IF NOT EXISTS`` so the call is
idempotent and safe to run on every startup.

M2.T1: topics + problems + test_cases. Other tables (``users``,
``submissions``, ``review_schedule``) are added by later tickets.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# The repo's data/ directory holds seed YAML and runtime DBs (per
# ADR-0001 + .gitignore). We resolve relative to the repo root, not
# CWD, so the app behaves the same no matter where you invoke it.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "code_gym.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Each statement is idempotent. Order matters for FK targets:
# ``topics`` must exist before ``problems``, ``problems`` before
# ``test_cases``. ``PRAGMA foreign_keys=ON`` is connection-scoped and
# must be re-issued on every connection (it's not persisted in the
# file). The ``get_connection`` context manager does this.
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        obi_phase TEXT,
        order_index INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS problems (
        id INTEGER PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        topic_id INTEGER NOT NULL REFERENCES topics(id),
        difficulty INTEGER NOT NULL,
        statement_md TEXT NOT NULL,
        input_format_md TEXT,
        output_format_md TEXT,
        examples_json TEXT,
        source TEXT,
        source_url TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_cases (
        id INTEGER PRIMARY KEY,
        problem_id INTEGER NOT NULL REFERENCES problems(id),
        stdin TEXT NOT NULL,
        expected_stdout TEXT NOT NULL,
        is_sample INTEGER DEFAULT 0,
        weight INTEGER DEFAULT 1
    )
    """,
)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


class get_connection:
    """Context manager that yields a sqlite3 connection with PRAGMA
    foreign_keys=ON and row factory set to sqlite3.Row.

    Usage:
        with get_connection(path) as conn:
            conn.execute(...)

    The caller is responsible for committing/rolling back; the
    context manager only handles open/close.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def __enter__(self) -> sqlite3.Connection:
        # ``check_same_thread=False`` so the same connection can be
        # passed across threads (FastAPI's async-to-thread helpers,
        # scripts that reuse the connection). WAL mode keeps readers
        # safe; writers serialize. We do NOT enable ``isolation_level``
        # autocommit — sqlite3's default (deferred) is fine.
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # Per task constraints: FK enforcement must be ON.
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL mode improves concurrent reads (per ADR-0001). Safe to
        # call repeatedly; no-op if already set.
        conn.execute("PRAGMA journal_mode = WAL")
        self._conn = conn
        return conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def init_db(db_path: Path | str | None = None) -> None:
    """Create all tables defined in ``SCHEMA`` on ``db_path``.

    Idempotent: ``CREATE TABLE IF NOT EXISTS``. Safe to call on every
    process startup.

    If ``db_path`` is None we use ``DEFAULT_DB_PATH`` and create the
    parent ``data/`` directory if missing.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if path.parent and path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(path) as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)


__all__: Iterable[str] = (
    "DEFAULT_DB_PATH",
    "get_connection",
    "init_db",
    "SCHEMA",
)
