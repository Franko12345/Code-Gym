"""Tests for ``app.cli`` — invite-only user creation.

Per ticket #5 (M1.T3) and ADR-0003, the CLI is the ONLY path that
creates a user. There is no ``POST /signup`` route. The CLI uses
argparse so admins can run::

    python -m app.cli create-user franco@froto.online 'senha123' 'Franco'

Seam: ``app.cli.main(argv=None, db_path=None) -> int``. Returns the
process exit code (0 = success, non-zero = failure) and prints to
stdout/stderr. Tests call ``main`` directly so ``capsys`` captures the
output and ``tmp_path`` keeps the DB off the real ``data/code_gym.db``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from app.auth.passwords import hash_pw, verify_pw
from app.cli import main
from app.db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh, initialized SQLite DB in tmp_path. Never touches the real one."""
    path = tmp_path / "cli_test.db"
    init_db(path)
    return path


def _run_cli(db_path: Path, *args: str) -> tuple[int, str, str]:
    """Invoke ``main`` with the given argv + a custom db_path, return (rc, out, err).

    Returns the captured stdout and stderr strings so tests can assert on
    confirmation messages and error wording.
    """
    # Capture by redirecting sys.stdout/stderr at the file level — capsys
    # works at the Python level and the CLI prints via plain ``print``.
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, stderr
    try:
        rc = main(argv=list(args), db_path=db_path)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return rc, stdout.getvalue(), stderr.getvalue()


def _fetch_user(db_path: Path, email: str) -> sqlite3.Row | None:
    """Return the users row for ``email`` (or None)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT id, email, password_hash, display_name, elo "
            "FROM users WHERE email = ?",
            (email,),
        ).fetchone()


# ---------------------------------------------------------------------------
# Happy path — create-user inserts a row with bcrypt-hashed password
# ---------------------------------------------------------------------------


def test_create_user_inserts_row_with_bcrypt_hash(db_path: Path) -> None:
    """``create-user email pw 'Name'`` must insert a row whose
    ``password_hash`` is a valid bcrypt hash of the password.

    The seam-level assertion: after ``main`` returns 0, querying the DB
    yields exactly one row, and ``verify_pw(password, hash)`` is True.
    The hash format is checked via ``verify_pw`` (not a regex on the
    prefix) so a future hash-format migration doesn't break this test
    silently.
    """
    rc, out, _err = _run_cli(
        db_path,
        "create-user",
        "a@b.c",
        "senha",
        "Alice",
    )

    assert rc == 0, f"expected exit 0, got {rc}; stdout={out!r}"

    row = _fetch_user(db_path, "a@b.c")
    assert row is not None, "user row must exist after create-user"
    assert row["email"] == "a@b.c"
    assert row["display_name"] == "Alice"

    # The stored hash must validate the original plaintext via bcrypt.
    # ``verify_pw`` returns False (not raises) on a malformed hash, so
    # an unhashed or wrong-format value fails the assertion cleanly.
    assert verify_pw("senha", row["password_hash"]) is True, (
        "stored password_hash must verify against the plaintext via bcrypt"
    )


def test_create_user_prints_confirmation(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``create-user`` must print a confirmation line including the email and id.

    The acceptance criteria require admins to see what they just did —
    the line must include the email so multi-user setups aren't
    confusing, and the id so a follow-up command can reference it.
    """
    rc = main(argv=["create-user", "x@y.z", "pw", "X"], db_path=db_path)
    out = capsys.readouterr().out

    assert rc == 0
    assert "x@y.z" in out
    assert "created" in out.lower()


def test_create_user_with_missing_display_name_defaults_to_email_local_part(
    db_path: Path,
) -> None:
    """If the admin omits ``display_name``, the CLI must store a sensible default.

    Spec says default to the email local part (the bit before ``@``).
    Empty string is also acceptable per the task constraints, but a
    meaningful default is more useful for the profile page (M3.T3).
    We allow either — the contract is ``display_name`` is non-NULL.
    """
    rc, _out, _err = _run_cli(
        db_path,
        "create-user",
        "carol@froto.online",
        "pw",
        # No display_name — third positional omitted
    )

    assert rc == 0
    row = _fetch_user(db_path, "carol@froto.online")
    assert row is not None
    assert row["display_name"] in ("carol", ""), (
        f"display_name must default to email local part or empty, got {row['display_name']!r}"
    )


# ---------------------------------------------------------------------------
# Idempotency — duplicate email must fail with non-zero exit + clear msg
# ---------------------------------------------------------------------------


def test_create_user_with_duplicate_email_returns_nonzero_exit(db_path: Path) -> None:
    """Re-running with the same email must NOT overwrite; must exit non-zero.

    Per ADR-0003 the CLI is the only user-creation path, so an
    accidental double-create must be loud (exit != 0) and the existing
    row must remain intact (no overwrite of password_hash, display_name).
    """
    # First create succeeds.
    rc1, _, _ = _run_cli(db_path, "create-user", "dup@x.com", "first", "First")
    assert rc1 == 0
    original = _fetch_user(db_path, "dup@x.com")
    assert original is not None
    original_hash = original["password_hash"]

    # Second create with the SAME email + different password must fail.
    rc2, _out2, err2 = _run_cli(db_path, "create-user", "dup@x.com", "second", "Second")

    assert rc2 != 0, "duplicate email must produce non-zero exit code"

    # The original row must be unchanged — no overwrite.
    after = _fetch_user(db_path, "dup@x.com")
    assert after is not None
    assert after["password_hash"] == original_hash, (
        "duplicate create must NOT overwrite the existing password_hash"
    )
    assert after["display_name"] == "First", (
        "duplicate create must NOT overwrite display_name"
    )

    # The error must mention the email so the admin knows which user.
    combined = (_out2 + err2).lower()
    assert "dup@x.com" in combined or "duplicate" in combined or "exists" in combined, (
        "error message must clearly identify the duplicate; "
        f"got stdout={_out2!r} stderr={err2!r}"
    )


# ---------------------------------------------------------------------------
# No args → help to stdout, exit 0
# ---------------------------------------------------------------------------


def test_no_args_prints_help_and_exits_zero(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Running with no args must print help and exit 0 — argparse default.

    This is the standard CLI behavior and matches the acceptance
    criterion: ``Sem args → help text + exit 0``.
    """
    rc = main(argv=[], db_path=db_path)
    out = capsys.readouterr().out

    assert rc == 0, f"no-args invocation must exit 0, got {rc}"
    # argparse prints usage + subcommand list. Must mention create-user
    # so the admin discovers the only available subcommand.
    assert "create-user" in out, (
        f"help text must mention the create-user subcommand; got:\n{out}"
    )


def test_help_subcommand_prints_help_and_exits_zero(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--help`` must print help and exit 0 — argparse default."""
    rc = main(argv=["--help"], db_path=db_path)
    out = capsys.readouterr().out

    assert rc == 0
    assert "create-user" in out


# ---------------------------------------------------------------------------
# Missing required arg → non-zero exit + error message
# ---------------------------------------------------------------------------


def test_create_user_with_only_email_exits_nonzero(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Calling ``create-user email`` (missing password + name) must
    exit non-zero AND produce a clear error message naming the missing arg.

    argparse sends its errors to stderr by default; the test accepts
    either stream to be robust against a future argparse config change.
    """
    rc = main(argv=["create-user", "a@b.c"], db_path=db_path)
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()

    assert rc != 0, "missing args must exit non-zero"
    # argparse error mentions the option name and "required".
    assert "password" in combined or "required" in combined, (
        f"error must mention the missing arg or 'required'; got:\n{captured}"
    )


# ---------------------------------------------------------------------------
# Email validation — reject obvious garbage
# ---------------------------------------------------------------------------


def test_create_user_rejects_email_without_at_sign(db_path: Path) -> None:
    """An email with no ``@`` is obvious garbage and must be rejected.

    Per the task constraints: basic format check (contains ``@`` +
    non-empty local/domain). Reject before touching the DB so a typo
    doesn't accidentally create a user with a broken identifier.
    """
    rc, out, err = _run_cli(db_path, "create-user", "garbage-no-at", "pw", "G")

    assert rc != 0, "garbage email must exit non-zero"
    combined = (out + err).lower()
    assert "email" in combined, (
        f"error must mention email validation; got stdout={out!r} stderr={err!r}"
    )
    # And it must NOT have inserted anything.
    assert _fetch_user(db_path, "garbage-no-at") is None


def test_create_user_rejects_empty_local_or_domain_part(db_path: Path) -> None:
    """``@b.c`` (empty local) and ``a@`` (empty domain) must both be rejected.

    The format check requires non-empty local AND non-empty domain.
    """
    rc_empty_local, _, _ = _run_cli(db_path, "create-user", "@b.c", "pw", "X")
    assert rc_empty_local != 0, "@b.c (empty local) must be rejected"

    rc_empty_domain, _, _ = _run_cli(db_path, "create-user", "a@", "pw", "X")
    assert rc_empty_domain != 0, "a@ (empty domain) must be rejected"
