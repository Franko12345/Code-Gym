"""Repo-root conftest: make the ``app`` package importable from tests.

Without this, ``from app.db import init_db`` fails because pytest
only adds ``tests/`` to ``sys.path``. The ``app/`` package lives at
the repo root, so we add the repo root to ``sys.path`` here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))