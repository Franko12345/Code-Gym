"""SQLite database access for the Code-Gym app.

Per ADR-0001, the runtime database lives at ``data/code_gym.db`` relative
to the repo root. ``init_db`` is idempotent and must be safe to call at
app startup and from tests.

The schema created here is the minimum needed for ticket #3 (M1.T1):
the ``users`` table. Future tickets (M1.T2+ CLI create-user, M1.T3
login) will read from / insert into this table without touching its
shape — so the column choices below are load-bearing.

Why we expose ``db_path`` as a parameter instead of hard-coding it:
tests must be able to point init_db at ``tmp_path`` without ever
writing to the real ``data/code_gym.db``. The CLI / app startup pass
``DEFAULT_DB_PATH`` explicitly (or rely on the default) to get the
production file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Production database path. Tests must never write here; pass an
# explicit ``db_path`` instead.
DEFAULT_DB_PATH = Path("data/code_gym.db")

# DDL for the users table. SQLite-specific syntax (INTEGER PRIMARY KEY
# auto-increments; UNIQUE on a column creates an auto-index that the
# tests assert on via PRAGMA index_list).
_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT,
    elo           INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Initialize the SQLite schema at ``db_path``.

    Creates the parent directory if it does not exist (the default
    ``data/`` directory may be absent on a fresh clone). Idempotent —
    safe to call on every startup.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.executescript(_USERS_DDL)
        conn.commit()