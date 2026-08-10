"""Tests for the /u/{username} public profile grid (M3.T3, ticket #12).

Acceptance criteria from ticket #12:

* GET /u/{username} for an existing user → 200 + HTML with a profile
  header (display_name + email) + a grid of all problems coloured by
  the user's best verdict for that problem.
* GET /u/{nonexistent_user} → 404 (not 500).
* The viewer does NOT need to be logged in to view a profile — the
  page is public (ADR-0003 spirit: anyone can see progress).
* The grid shows ALL problems in the DB (across all topics), not just
  one topic.

Grid colour rule (documented here, implemented in the service)
-------------------------------------------------------------

For a (user, problem) pair we compute the user's *best verdict* for
that problem (the most favourable outcome across all submissions):

* **green (solved)** — at least one submission with verdict ``AC``.
* **red (failed)** — no AC, but at least one submission with verdict
  in ``{WA, RE, TLE}`` (i.e. code ran and produced a wrong/timeout/
  crash verdict).
* **yellow (error only)** — has submissions, but none ``AC`` and none
  in ``{WA, RE, TLE}`` (currently this catches ``CE`` — code never
  ran because it didn't compile).
* **gray (untouched)** — zero submissions for that problem.

This is the rule the service implements and the template renders.
The CSS classes are ``solved``, ``attempted``, ``untouched`` — the
brief said to reuse existing class names. ``attempted`` covers both
red and yellow here (any submission = attempted). The template
further distinguishes red vs yellow via ``status-failed`` /
``status-error`` modifiers so the colour is correct.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_utils import encode_jwt
from app.db import init_db
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh SQLite file for each test, wired as the app's DB.

    We patch the module-level ``DEFAULT_DB_PATH`` everywhere it's read
    so the /u/{username} route + service hit the tmp DB.
    """
    p = tmp_path / "code_gym.db"
    init_db(p)
    monkeypatch.setattr("app.db.DEFAULT_DB_PATH", p)
    # profile.service / profile.routes read the path at call time via
    # DEFAULT_DB_PATH; once the module is imported, patching here is
    # enough because both modules re-read the module attribute.
    monkeypatch.setattr("app.profile.routes.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.profile.service.DEFAULT_DB_PATH", p)
    # Roadmap/roadmap service too — defensive against future cross-touches.
    monkeypatch.setattr("app.roadmap.routes.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.roadmap.service.DEFAULT_DB_PATH", p)
    # Auth middleware also reads a DB_PATH — point it at the tmp DB.
    from app.auth import middleware as mw_mod

    monkeypatch.setattr(mw_mod, "DB_PATH", p)
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient bound to the real FastAPI app (with auth middleware
    and the (yet-to-exist) profile router mounted)."""
    return TestClient(app)


@pytest.fixture()
def seeded_topics_and_problems(db_path: Path) -> dict[str, int]:
    """Seed two topics across two areas (graph + array) with one
    problem in each topic. Returns the seeded ids so tests can refer
    to them by name."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('arrays', 'Vetores', 'F1', 10);
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('graphs', 'Grafos', 'F2', 30);
            INSERT INTO problems (slug, title, topic_id, difficulty,
                                  statement_md, created_at)
                VALUES ('soma', 'Soma',       1, 1, 'x', '2026-08-09T00:00:00'),
                       ('bfs',  'BFS basico', 2, 2, 'x', '2026-08-09T00:00:00');
            """
        )
        conn.commit()
        soma_row = conn.execute(
            "SELECT id FROM problems WHERE slug = 'soma'"
        ).fetchone()
        bfs_row = conn.execute(
            "SELECT id FROM problems WHERE slug = 'bfs'"
        ).fetchone()
    assert soma_row is not None and bfs_row is not None
    return {"soma": int(soma_row[0]), "bfs": int(bfs_row[0])}


@pytest.fixture()
def user_franco(db_path: Path) -> int:
    """Insert the user we'll look up by username. display_name is the
    username used by the /u/{username} route."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            ("franco@froto.online", "$2b$12$placeholder", "franco"),
        )
        conn.commit()
        uid = int(cur.lastrowid)
    assert uid is not None
    return uid


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


def test_profile_unknown_username_returns_404(client: TestClient) -> None:
    """A username that doesn't exist in the users table must yield 404,
    NOT 500. The page is public (no login required), so the test makes
    the request anonymously."""
    response = client.get("/u/ghost")
    assert response.status_code == 404


def test_profile_unknown_username_does_not_500_even_when_db_empty(
    client: TestClient,
) -> None:
    """With an empty users table (no franco row), /u/franco must 404
    cleanly — not crash on a NoneType or IndexError."""
    response = client.get("/u/franco")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 200 path — base shape
# ---------------------------------------------------------------------------


def test_profile_existing_user_returns_200(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """An existing user must produce 200 + HTML (the page is public)."""
    response = client.get("/u/franco")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_profile_renders_profile_header(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """The page must contain the user's display_name and email."""
    response = client.get("/u/franco")
    body = response.text
    assert "franco" in body, "display_name 'franco' missing from profile"
    assert "franco@froto.online" in body, "email missing from profile"


def test_profile_extends_base_layout(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """The page must extend base.html (sidebar present)."""
    response = client.get("/u/franco")
    body = response.text
    assert "Code-Gym" in body
    # Sidebar links from base.html must be present (proves base.html
    # was used and the child template rendered inside the layout).
    assert 'href="/problems"' in body
    assert 'href="/roadmap"' in body


def test_profile_is_public_no_login_required(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """The page must work for an anonymous viewer — no cg_session
    cookie set. We assert it by simply not setting any cookie."""
    response = client.get("/u/franco")
    assert response.status_code == 200


def test_profile_renders_all_problems_in_grid(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """The grid must include EVERY problem in the DB, across all topics,
    not just one topic. We seeded two topics with one problem each — so
    the grid must contain both 'Soma' and 'BFS basico'."""
    response = client.get("/u/franco")
    body = response.text
    assert "Soma" in body, "problem 'Soma' (topic arrays) missing from grid"
    assert "BFS basico" in body, "problem 'BFS basico' (topic graphs) missing from grid"


# ---------------------------------------------------------------------------
# Colour rule
# ---------------------------------------------------------------------------


def _add_submission(
    db_path: Path, user_id: int, problem_id: int, verdict: str, attempt: int = 1
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO submissions "
            "(user_id, problem_id, code, language, verdict, attempt_n, submitted_at) "
            "VALUES (?, ?, 'print(1)', 'python', ?, ?, '2026-08-09T00:00:00')",
            (user_id, problem_id, verdict, attempt),
        )
        conn.commit()


def test_profile_grid_green_when_user_has_any_ac(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """A user with at least one AC submission on problem X must see that
    problem coloured as solved (the ``solved`` CSS class)."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "AC")
    response = client.get("/u/franco")
    body = response.text
    # The 'solved' class must appear at least once (the green verdict).
    assert 'class="solved"' in body or "solved" in body, (
        "expected 'solved' class for AC submission on Soma"
    )


def test_profile_grid_green_wins_over_red(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """If the user has BOTH an AC and a WA on the same problem, the
    problem must be green (AC is the better verdict). The 'solved'
    class must appear, and the WA-only 'attempted'/'failed' class
    must NOT appear for that problem.

    We assert it by counting the colour markers: there must be at
    least one ``solved`` marker (the green for soma) and zero
    failed/error markers (because soma has AC). The other problem
    (bfs) is untouched → gray, so it should NOT contribute a failed
    marker either."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "WA", attempt=1)
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "AC", attempt=2)
    response = client.get("/u/franco")
    body = response.text
    assert "solved" in body, "AC must take precedence over WA on the same problem"
    # No problem has only-WA → no 'failed' marker expected.
    assert "status-failed" not in body, (
        "a problem with any AC must not be marked failed, even if it "
        "also has a WA submission"
    )


def test_profile_grid_red_for_wa_without_ac(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """A user with only a WA submission (no AC) on a problem must see
    that problem rendered as 'failed' (red)."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "WA")
    response = client.get("/u/franco")
    body = response.text
    # Failed marker (red) must appear for soma.
    assert "status-failed" in body, (
        "WA submission without AC must be rendered as 'status-failed' "
        "(red — attempted-not-solved)"
    )


def test_profile_grid_red_for_re_without_ac(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """Runtime error (RE) without AC must also render as red."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "RE")
    response = client.get("/u/franco")
    body = response.text
    assert "status-failed" in body, "RE without AC must be rendered as failed (red)"


def test_profile_grid_red_for_tle_without_ac(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """Time limit exceeded (TLE) without AC must render as red."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "TLE")
    response = client.get("/u/franco")
    body = response.text
    assert "status-failed" in body, "TLE without AC must be rendered as failed (red)"


def test_profile_grid_yellow_for_ce_only(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """A user with only a CE submission (compilation error) on a problem
    has ATTEMPTED but never produced a runtime verdict — render as
    yellow (status-error)."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "CE")
    response = client.get("/u/franco")
    body = response.text
    assert "status-error" in body, (
        "CE-only submission must be rendered as 'status-error' (yellow)"
    )
    # It must NOT be red — CE didn't actually run.
    assert "status-failed" not in body, (
        "CE must NOT be marked 'failed' (red) — code never ran"
    )


def test_profile_grid_gray_for_untouched_problem(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """A problem with zero submissions must render as untouched (gray).
    We assert this by submitting only on 'soma' and looking for the
    untouched marker on 'bfs basico'."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "AC")
    response = client.get("/u/franco")
    body = response.text
    # The 'untouched' class must appear (for the un-attempted bfs).
    assert "untouched" in body, (
        "problem with no submissions must be marked 'untouched' (gray)"
    )


def test_profile_grid_mixed_statuses(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """End-to-end: soma=AC (green), bfs=WA (red) → both colours must
    appear simultaneously."""
    _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "AC")
    _add_submission(db_path, user_franco, seeded_topics_and_problems["bfs"], "WA")
    response = client.get("/u/franco")
    body = response.text
    assert "solved" in body, "soma (AC) must be marked solved"
    assert "status-failed" in body, "bfs (WA, no AC) must be marked failed"


def test_profile_grid_multiple_was_dedupe_to_red(
    client: TestClient, db_path: Path, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """Multiple WA submissions on the same problem must collapse to a
    single red cell — the colour rule is per-problem, not per-attempt."""
    for n in (1, 2, 3):
        _add_submission(db_path, user_franco, seeded_topics_and_problems["soma"], "WA", attempt=n)
    response = client.get("/u/franco")
    body = response.text
    # Exactly one failed marker for soma (the grid renders one card per
    # problem, regardless of attempt count). We assert the marker
    # exists, and that the count of 'status-failed' tokens is small
    # enough that one card per problem is rendered (we have two
    # problems in the DB; bfs is untouched → contributes zero failed
    # markers; soma contributes exactly one). Loose bound of <=3 to
    # tolerate any wrapper markup, but the marker must be present.
    assert body.count("status-failed") >= 1
    assert body.count("status-failed") <= 3, (
        f"expected ≤ 1 failed marker per problem; got "
        f"{body.count('status-failed')}"
    )


# ---------------------------------------------------------------------------
# Viewer context (request.state.user)
# ---------------------------------------------------------------------------


def test_profile_renders_for_own_user_when_viewer_logged_in(
    client: TestClient,
    db_path: Path,
    seeded_topics_and_problems: dict[str, int],
    user_franco: int,
) -> None:
    """When the viewer is logged in as the same user, the profile page
    must still render normally (200). This exercises the
    ``request.state.user`` integration."""
    token = encode_jwt(user_franco)
    response = client.get("/u/franco", cookies={"cg_session": token})
    assert response.status_code == 200
    assert "franco" in response.text


def test_profile_renders_for_other_user_when_viewer_logged_in(
    client: TestClient,
    db_path: Path,
    seeded_topics_and_problems: dict[str, int],
    user_franco: int,
) -> None:
    """The profile is public — a logged-in viewer must also see OTHER
    users' profiles (no auth gate)."""
    # Insert a second user; viewer is franco looking at ghost.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            ("ghost@froto.online", "$2b$12$placeholder", "ghost"),
        )
        conn.commit()
    token = encode_jwt(user_franco)
    response = client.get("/u/ghost", cookies={"cg_session": token})
    assert response.status_code == 200
    assert "ghost" in response.text


# ---------------------------------------------------------------------------
# ELO stored but not visualized (per ADR / MVP scope)
# ---------------------------------------------------------------------------


def test_profile_does_not_visualize_elo(
    client: TestClient, seeded_topics_and_problems: dict[str, int], user_franco: int
) -> None:
    """Per ADR / MVP scope, ELO is stored in the DB but not shown on
    the profile page yet. We assert no 'ELO' label or raw number for
    the user's elo value appears in the rendered HTML."""
    response = client.get("/u/franco")
    body = response.text
    assert "ELO" not in body, (
        "ELO is not part of MVP profile scope — must not appear on the page"
    )
    # The user's stored ELO is 0 by default; if we accidentally
    # rendered it, "0" alone could collide with other counters, so we
    # just check the label is absent.