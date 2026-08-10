"""Tests for app.db.init_db.

Per ticket #3 (M1.T1), the app must initialize its SQLite schema at
startup. The `users` table is the first table created; it holds the
auth principal for the multi-user app. Per ADR-0003 there is no public
signup — users are inserted only via the CLI — but the table must still
exist so login can look them up.

Seam: `app.db.init_db(db_path) -> None`. After running, the SQLite file
at `db_path` contains a `users` table with the documented columns.
Tests use `tmp_path` so the real `data/code_gym.db` is never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import DEFAULT_DB_PATH, init_db


# ---------------------------------------------------------------------------
# Default path constant
# ---------------------------------------------------------------------------


def test_default_db_path_is_data_code_gym_db() -> None:
    """The default DB path must resolve to <repo>/data/code_gym.db (ADR-0001)."""
    from app.db import REPO_ROOT
    assert DEFAULT_DB_PATH == REPO_ROOT / "data" / "code_gym.db"


# ---------------------------------------------------------------------------
# init_db creates the users table
# ---------------------------------------------------------------------------


def test_init_db_creates_users_table(tmp_path: Path) -> None:
    """init_db must create a `users` table in the SQLite file at db_path."""
    db = tmp_path / "test.db"
    init_db(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "users" in tables


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Calling init_db twice must not raise — uses CREATE TABLE IF NOT EXISTS."""
    db = tmp_path / "test.db"
    init_db(db)
    # Second call must be a clean no-op.
    init_db(db)

    with sqlite3.connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='users'"
        ).fetchone()[0]
    assert count == 1, "users table must exist exactly once after re-init"


def test_init_db_creates_parent_directory(tmp_path: Path) -> None:
    """init_db must auto-create the parent directory of db_path.

    The default path lives at `<repo>/data/code_gym.db`; on a fresh
    clone that directory doesn't exist yet.
    """
    db = tmp_path / "nested" / "deeper" / "test.db"
    assert not db.parent.exists()

    init_db(db)

    assert db.parent.is_dir()
    assert db.is_file()


# ---------------------------------------------------------------------------
# users table schema
# ---------------------------------------------------------------------------


def _users_columns(db: Path) -> dict[str, dict]:
    """Return {column_name: {type, notnull, default, pk}} for the users table."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute("PRAGMA table_info(users)").fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {
        row[1]: {"type": row[2], "notnull": bool(row[3]), "default": row[4], "pk": bool(row[5])}
        for row in rows
    }


def test_users_table_has_required_columns(tmp_path: Path) -> None:
    """users must have: id, email, password_hash, display_name, elo, created_at."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    expected = {"id", "email", "password_hash", "display_name", "elo", "created_at"}
    assert expected.issubset(cols.keys()), (
        f"missing columns: {expected - cols.keys()}; got {sorted(cols.keys())}"
    )


def test_users_id_is_autoincrement_primary_key(tmp_path: Path) -> None:
    """`id` must be the primary key (INTEGER PRIMARY KEY auto-increments in SQLite)."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    assert cols["id"]["pk"] is True, "id must be the PRIMARY KEY"
    assert "INT" in cols["id"]["type"].upper(), (
        f"id must be INTEGER, got {cols['id']['type']!r}"
    )


def test_users_email_is_unique(tmp_path: Path) -> None:
    """`email` must be UNIQUE so two accounts can't share an email."""
    db = tmp_path / "test.db"
    init_db(db)

    with sqlite3.connect(db) as conn:
        indexes = conn.execute("PRAGMA index_list(users)").fetchall()
        # PRAGMA index_list: seq, name, unique, origin, partial
        unique_indexes = [row for row in indexes if row[2] == 1]

        # SQLite expresses UNIQUE on a column as an auto-created index.
        # Confirm at least one unique index covers the email column.
        email_in_unique_index = False
        for idx_row in unique_indexes:
            index_name = idx_row[1]
            indexed_cols = conn.execute(
                f"PRAGMA index_info({index_name})"
            ).fetchall()
            # PRAGMA index_info: seqno, cid, name
            if any(col[2] == "email" for col in indexed_cols):
                email_in_unique_index = True
                break

    assert email_in_unique_index, (
        "email must have a UNIQUE constraint (auto-index); "
        f"found unique indexes: {[r[1] for r in unique_indexes]}"
    )


def test_users_password_hash_is_not_null(tmp_path: Path) -> None:
    """`password_hash` must be NOT NULL — a user without a hash can't log in."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    assert cols["password_hash"]["notnull"] is True


def test_users_email_is_not_null(tmp_path: Path) -> None:
    """`email` must be NOT NULL — it's the login identifier."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    assert cols["email"]["notnull"] is True


def test_users_elo_has_default_zero(tmp_path: Path) -> None:
    """`elo` must default to 0 — new users start at the bottom of the ladder."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    # DEFAULT may render as int 0 or str '0' depending on the driver.
    default = cols["elo"]["default"]
    assert default is not None, "elo must have a DEFAULT"
    assert int(default) == 0, f"elo default must be 0, got {default!r}"


def test_users_created_at_defaults_to_current_timestamp(tmp_path: Path) -> None:
    """`created_at` must default to CURRENT_TIMESTAMP so we don't need to set it."""
    db = tmp_path / "test.db"
    init_db(db)

    cols = _users_columns(db)
    default = cols["created_at"]["default"]
    assert default is not None, "created_at must have a DEFAULT"
    assert "CURRENT_TIMESTAMP" in str(default).upper(), (
        f"created_at default must be CURRENT_TIMESTAMP, got {default!r}"
    )


# ---------------------------------------------------------------------------
# Functional roundtrip (uses the seam, not internals)
# ---------------------------------------------------------------------------


def test_users_table_accepts_a_row_with_all_columns(tmp_path: Path) -> None:
    """Inserting a row with the documented columns must succeed.

    This is the seam-level check: after init_db, the schema is usable
    for the upcoming create-user CLI (M1.T2+).
    """
    db = tmp_path / "test.db"
    init_db(db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO users "
            "(email, password_hash, display_name, elo, created_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ("franco@froto.online", "$2b$12$placeholder", "Franco", 0),
        )
        row = conn.execute(
            "SELECT email, password_hash, display_name, elo FROM users"
        ).fetchone()

    assert row is not None
    assert row[0] == "franco@froto.online"
    assert row[1] == "$2b$12$placeholder"
    assert row[2] == "Franco"
    assert row[3] == 0


def test_users_email_unique_constraint_is_enforced(tmp_path: Path) -> None:
    """Inserting a duplicate email must raise IntegrityError — UNIQUE works."""
    db = tmp_path / "test.db"
    init_db(db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("dup@froto.online", "$2b$12$a"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                ("dup@froto.online", "$2b$12$b"),
            )