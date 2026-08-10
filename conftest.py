"""Repo-root conftest — ensures ``import app`` / ``import scripts`` work
when pytest is invoked from any cwd.

Pytest doesn't auto-add ``rootdir`` to ``sys.path`` unless the rootdir
contains an ``__init__.py`` (or a ``conftest.py`` at the rootdir). This
empty conftest makes the repo root a "rootdir import root" so tests
under ``tests/`` can ``from app.db import ...`` and
``from scripts.seed import ...`` without path hacks.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root = parent of this conftest.py
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
