"""Smoke test for the /u/{username} profile route (M3.T3).

Exercises the full request/response path against the real FastAPI
app + a tmp DB. The pytest suite already covers behaviour in
detail — this is the headless "does it actually run" smoke.

We exit 0 on success, 1 on any assertion failure so the smoke can
be invoked from a one-liner.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Set the JWT secret BEFORE importing any app module (jwt_utils
# fails-fast on import if the env var is missing).
os.environ.setdefault(
    "CODE_GYM_JWT_SECRET",
    "test-secret-do-not-use-in-prod-padded-to-32b",
)

# Repo root on sys.path so `import app...` works from any cwd.
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.auth.middleware import DB_PATH as MW_DB_PATH  # noqa: E402
from app.db import DEFAULT_DB_PATH, get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "code_gym.db"
        init_db(db_file)

        # Point every DB-touching module at the tmp file.
        DEFAULT_DB_PATH = db_file  # noqa: F841 (assignment is the seam)
        MW_DB_PATH = db_file
        # Service/route modules read DEFAULT_DB_PATH at call time
        # via the app.db module — patch it there.
        import app.db as _app_db

        _app_db.DEFAULT_DB_PATH = db_file
        import app.profile.service as _svc
        import app.profile.routes as _routes

        _svc.DEFAULT_DB_PATH = db_file
        _routes.DEFAULT_DB_PATH = db_file

        # Seed: two topics, three problems.
        with get_connection(db_file) as conn:
            conn.executescript(
                """
                INSERT INTO topics (slug, name, obi_phase, order_index)
                    VALUES ('arrays', 'Vetores', 'F1', 10);
                INSERT INTO topics (slug, name, obi_phase, order_index)
                    VALUES ('graphs', 'Grafos', 'F2', 30);
                INSERT INTO problems (slug, title, topic_id, difficulty,
                                      statement_md, created_at)
                    VALUES ('soma', 'Soma',        1, 1, 'x', '2026-08-09T00:00:00'),
                           ('max',  'Maximo',      1, 1, 'x', '2026-08-09T00:00:00'),
                           ('bfs',  'BFS basico',  2, 2, 'x', '2026-08-09T00:00:00');
                INSERT INTO users (email, password_hash, display_name, elo)
                    VALUES ('franco@froto.online', '$2b$12$p', 'franco', 1200);
                """
            )
            conn.commit()
            uid = conn.execute(
                "SELECT id FROM users WHERE display_name = 'franco'"
            ).fetchone()[0]
            soma = conn.execute(
                "SELECT id FROM problems WHERE slug = 'soma'"
            ).fetchone()[0]
            max_ = conn.execute(
                "SELECT id FROM problems WHERE slug = 'max'"
            ).fetchone()[0]
            bfs = conn.execute(
                "SELECT id FROM problems WHERE slug = 'bfs'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO submissions "
                "(user_id, problem_id, code, language, verdict, attempt_n, submitted_at) "
                "VALUES (?, ?, 'print(3)', 'python', 'AC', 1, '2026-08-09T00:00:00')",
                (uid, soma),
            )
            conn.execute(
                "INSERT INTO submissions "
                "(user_id, problem_id, code, language, verdict, attempt_n, submitted_at) "
                "VALUES (?, ?, 'print(99)', 'python', 'WA', 1, '2026-08-09T00:00:00'),"
                "       (?, ?, 'print(99)', 'python', 'WA', 2, '2026-08-09T00:00:01')",
                (uid, max_, uid, max_),
            )
            conn.execute(
                "INSERT INTO submissions "
                "(user_id, problem_id, code, language, verdict, attempt_n, submitted_at) "
                "VALUES (?, ?, 'syntax', 'cpp', 'CE', 1, '2026-08-09T00:00:00')",
                (uid, bfs),
            )
            conn.commit()

        # Use a fresh client (no shared cookie jar).
        with TestClient(app) as client:
            # Anonymous viewer, existing user.
            resp = client.get("/u/franco")
            assert resp.status_code == 200, (
                f"GET /u/franco → {resp.status_code} (expected 200)"
            )
            body = resp.text
            assert "franco" in body, "display_name missing from profile"
            assert "franco@froto.online" in body, "email missing from profile"
            assert "Soma" in body, "soma missing from grid"
            assert "Maximo" in body, "maximo missing from grid"
            assert "BFS basico" in body, "bfs missing from grid"
            assert "solved" in body, "expected 'solved' marker for soma (AC)"
            assert "problem-card--solved" in body, (
                "expected green 'problem-card--solved' class for soma (AC)"
            )
            assert "status-failed" in body, "expected red 'status-failed' for maximo (WA)"
            assert "status-error" in body, "expected yellow 'status-error' for bfs (CE)"
            assert "untouched" not in body, (
                "all three problems have submissions — 'untouched' class "
                "must not appear"
            )
            assert "ELO" not in body, "ELO must not be visualized (MVP scope)"
            assert 'href="/problems"' in body, "base.html sidebar missing"
            assert 'href="/roadmap"' in body, "base.html sidebar missing"

            # Anonymous viewer, unknown username.
            resp = client.get("/u/ghost")
            assert resp.status_code == 404, (
                f"GET /u/ghost → {resp.status_code} (expected 404)"
            )
            assert "franco" not in resp.text or "Perfil não encontrado" in resp.text, (
                "404 page must not render another user's profile"
            )

            print("SMOKE PASS: /u/{username} profile renders correctly")
            print(
                f"  200 → grid with 3 problems (soma=green, max=red, bfs=yellow)"
            )
            print("  404 → unknown username")
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())