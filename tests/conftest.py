"""Test-only fixtures for the Code-Gym test suite.

Two cross-cutting concerns:

1.  ``CODE_GYM_JWT_SECRET`` must be set before any test imports
    ``app.auth.jwt_utils`` — the module fails fast at import time
    if the env var is missing. We set a deterministic test value
    at *module load* (before pytest starts collecting tests) so
    production modules like ``app.main`` can be imported by
    existing tests without breaking.

2.  The default DB path points at ``<repo>/data/code_gym.db``.
    Tests must never write to that file. Individual tests use
    ``tmp_path`` and pass it to ``init_db(db)`` explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# JWT secret — must be set BEFORE pytest collects test modules, because
# production modules (app.main, app.auth.middleware) eagerly import
# app.auth.jwt_utils at import time.
# ---------------------------------------------------------------------------

os.environ.setdefault(
    "CODE_GYM_JWT_SECRET",
    "test-secret-do-not-use-in-prod-padded-to-32b",
)

# A reference value the per-test autouse fixture reinstalls after
# individual tests clear the env (the fail-fast test).
_TEST_JWT_SECRET = os.environ["CODE_GYM_JWT_SECRET"]


@pytest.fixture(autouse=True)
def _ensure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reinstall ``CODE_GYM_JWT_SECRET`` for every test unless the
    test explicitly removed it (the ``test_missing_secret_raises_on_import``
    case reloads the module after deletion)."""
    monkeypatch.setenv("CODE_GYM_JWT_SECRET", _TEST_JWT_SECRET, prepend=False)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """A throwaway SQLite file under ``tmp_path``."""
    return tmp_path / "code_gym.db"