"""Admin CLI for invite-only user management.

Per ADR-0003 this is the **only** path that creates users. There is no
``POST /signup`` route, no admin UI for account creation. An admin
provisions access by running::

    python -m app.cli create-user franco@froto.online 'senha123' 'Franco'

Design notes
------------

- **argparse, no third-party CLI lib.** Stdlib keeps the dep tree tiny
  (per ADR-0001 / YAGNI) and the CLI surface is small.

- **Seam for tests.** ``main(argv=None, db_path=None) -> int`` is the
  public seam. Tests pass a custom ``argv`` and ``db_path`` so the real
  ``data/code_gym.db`` is never touched; the module-level
  ``__main__`` block sets up defaults for real invocations.

- **Idempotency: fail loudly.** Re-running with the same email returns
  exit code ``2`` and a clear error. The unique constraint on
  ``users.email`` is the actual guard — we just translate
  ``IntegrityError`` into a human-readable message.

- **Email validation is intentionally basic** (contains ``@`` +
  non-empty local/domain). Per the task constraints: "reject obvious
  garbage". Full RFC-5322 validation belongs in a separate library and
  is YAGNI for an invite-only CLI.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from app.auth.passwords import hash_pw
from app.db import DEFAULT_DB_PATH, get_connection, init_db


# Exit codes — keep small, conventional, and stable so external scripts
# can branch on them. ``argparse`` uses ``2`` for usage errors; we use
# the same code for our own validation errors so callers can treat any
# non-zero as "user input was bad".
EXIT_OK = 0
EXIT_USAGE = 2  # argparse default for bad args / our validation
EXIT_DB = 3  # unexpected DB / integrity error


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------


def _is_valid_email(value: str) -> bool:
    """Return True iff ``value`` looks like an email.

    Basic format check: exactly one ``@``, non-empty local part,
    non-empty domain part. Deliberately loose — RFC-5322 is overkill
    for an invite-only CLI where the admin types addresses they own.
    """
    if not value or "@" not in value:
        return False
    # Reject multiple @ — covers "a@b@c" which the basic check would miss.
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain:
        return False
    # Domain must contain a dot — "a@b" is technically deliverable but
    # we treat it as obvious garbage per the task constraints.
    if "." not in domain:
        return False
    return True


# ---------------------------------------------------------------------------
# Core operation
# ---------------------------------------------------------------------------


def create_user(
    email: str,
    password: str,
    display_name: str,
    *,
    db_path: Path | str,
) -> int:
    """Insert a user row and print a confirmation. Return the exit code.

    On success: prints ``User <email> created (id=<n>)`` to stdout.
    On duplicate email: prints error to stderr, returns ``EXIT_USAGE``.
    On any other DB error: prints error to stderr, returns ``EXIT_DB``.
    """
    # Ensure the schema exists — first-time invocation on a fresh
    # checkout shouldn't require a separate ``init_db`` call.
    init_db(db_path)

    password_hash = hash_pw(password)

    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES (?, ?, ?)",
                (email, password_hash, display_name),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        # Unique constraint on users.email is the only expected source
        # of IntegrityError here (FK constraints don't apply to users).
        # We don't introspect the message to decide — the constraint
        # is the gate, and the message below tells the admin why.
        print(
            f"error: user with email {email!r} already exists; "
            "no overwrite (idempotent-fail).",
            file=sys.stderr,
        )
        # Debug-only context — IntegrityError.args[0] typically reads
        # ``UNIQUE constraint failed: users.email``. Print it only when
        # the admin asks for verbose output? For v0.1.0 keep it silent
        # (YAGNI); if we ever need it, gate on a --verbose flag.
        _ = exc  # explicit unused-binding mark for future debug
        return EXIT_USAGE

    print(f"User {email} created (id={user_id})")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser. Extracted for testability."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Code-Gym admin CLI. The ONLY path to create users "
            "(see ADR-0003 — no public signup route)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    # create-user — the only subcommand in MVP. Future tickets may
    # add ``reset-password`` / ``deactivate``; the subparser pattern
    # is already in place for them.
    create = subparsers.add_parser(
        "create-user",
        help="Create a user (invite-only). Hashes the password with bcrypt.",
        description=(
            "Create a user with a bcrypt-hashed password. The email "
            "must be unique; re-running with the same email returns a "
            "non-zero exit code and does NOT overwrite the existing row."
        ),
    )
    create.add_argument(
        "email",
        help="User's email address (used as the login identifier).",
    )
    create.add_argument(
        "password",
        help="Plaintext password. Hashed with bcrypt (cost=12) before storage.",
    )
    create.add_argument(
        "display_name",
        nargs="?",
        default="",
        help="Optional display name. Defaults to the email local part if omitted.",
    )
    return parser


def main(argv: Sequence[str] | None = None, db_path: Path | str | None = None) -> int:
    """Entry point. Parse argv, dispatch, return exit code.

    Parameters
    ----------
    argv:
        Argument vector. ``None`` means ``sys.argv[1:]`` (production).
        Tests pass an explicit list to keep the surface deterministic.
    db_path:
        SQLite path. ``None`` means ``app.db.DEFAULT_DB_PATH``. Tests
        pass ``tmp_path`` so the real DB is never touched.

    Returns
    -------
    int
        Process exit code: 0 on success, non-zero on any failure.
    """
    parser = _build_parser()
    # argparse calls ``sys.exit`` on ``--help`` (exit 0) and on bad args
    # (exit 2). The ``main(...)`` seam returns an int instead, so we
    # translate those ``SystemExit`` calls back into our return value.
    # The side effect (printing help / usage error to stderr) still
    # happens via argparse's internals; we just don't kill the process.
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return EXIT_OK if exc.code in (None, 0) else EXIT_USAGE

    # No subcommand → print help + exit 0 (acceptance criterion).
    if args.command is None:
        parser.print_help()
        return EXIT_OK

    # db_path resolution: tests pass None/Path; production gets the
    # default. We resolve once and pass to create_user so the function
    # has a single source of truth.
    resolved_db_path: Path | str = db_path if db_path is not None else DEFAULT_DB_PATH

    if args.command == "create-user":
        # Email validation up front — fail loud before any DB work so a
        # typo doesn't accidentally create a row with a bad identifier.
        if not _is_valid_email(args.email):
            print(
                f"error: {args.email!r} is not a valid email "
                "(must contain '@' with non-empty local and domain parts).",
                file=sys.stderr,
            )
            return EXIT_USAGE

        # Password must be non-empty — bcrypt of "" is technically valid
        # but a user with no password is a security hole.
        if not args.password:
            print("error: password must not be empty.", file=sys.stderr)
            return EXIT_USAGE

        # Display name: default to the email local part so the profile
        # page (M3.T3) has something to show. Empty string would also
        # satisfy the schema; meaningful default is more useful.
        display_name = args.display_name or args.email.split("@", 1)[0]

        return create_user(
            email=args.email,
            password=args.password,
            display_name=display_name,
            db_path=resolved_db_path,
        )

    # Unreachable in practice — argparse would have rejected an unknown
    # subcommand. Defensive return so we never accidentally fall through.
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
