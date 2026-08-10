"""Tests for the /roadmap endpoint (M3.T2).

Acceptance criteria from ticket #11:

- GET /roadmap without auth → 302 redirect to /login (or 401)
- GET /roadmap with valid cookie → 200 + HTML containing topic
  names + progress bars
- Topic order: by order_index ascending (F1 first), seed via fixture
- Progress = (problems solved by user) / (total problems in topic);
  0% when no submissions

Auth seam choice
----------------
M1.T2 (JWT cookie middleware) and M1.T3 (CLI create-user) are not
yet merged into ``main`` when this ticket ships. The /roadmap route
therefore reads a plain ``cg_user`` cookie (the user's email) and
looks the user up by email — see ``app.roadmap.routes``. When the
JWT middleware lands in M1.T2, this dependency is swapped for the
JWT-decoded ``current_user`` dep. The route shape stays identical;
only the dep implementation changes.

Tests cover BOTH the 302 redirect (no cookie) and the 200 path
(cookie set via the same TestClient, exercising the real seam).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh SQLite file for each test, wired as the app's DB.

    We patch the module-level ``DEFAULT_DB_PATH`` in ``app.db`` AND
    init the schema so the /roadmap route can query topics/problems.
    """
    p = tmp_path / "code_gym.db"
    init_db(p)
    monkeypatch.setattr("app.db.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.roadmap.routes.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.roadmap.service.DEFAULT_DB_PATH", p)
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient bound to the (un-rewired) FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def seeded_topics(db_path: Path) -> None:
    """Seed three topics with non-monotonic insertion order so we can
    assert the SELECT ORDER BY order_index ASC.

    Insert order in the DB:
        1. graphs (order 30, F2)
        2. arrays (order 10, F1)
        3. dp     (order 20, F1)  — same phase as arrays, ties broken by slug
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('graphs', 'Grafos', 'F2', 30);
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('arrays', 'Vetores', 'F1', 10);
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('dp',     'Programação Dinâmica', 'F1', 20);
            """
        )
        conn.commit()


@pytest.fixture()
def user(db_path: Path) -> int:
    """Insert a test user and return its id. The cookie will hold the
    email (``franco@froto.online``) and the route looks up by email."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            ("franco@froto.online", "$2b$12$placeholder", "Franco"),
        )
        conn.commit()
        return int(cur.lastrowid)


# Cookie name used by the local auth seam (see module docstring).
CG_USER_COOKIE = "cg_user"


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_roadmap_without_cookie_redirects_to_login(
    client: TestClient, seeded_topics: None
) -> None:
    """Anonymous request to /roadmap must redirect to /login (302)."""
    response = client.get("/roadmap", follow_redirects=False)
    assert response.status_code == 302
    # Location header must point at the login page.
    location = response.headers.get("location", "")
    assert location.endswith("/login"), f"unexpected redirect: {location!r}"


def test_roadmap_with_invalid_cookie_redirects_to_login(
    client: TestClient, seeded_topics: None
) -> None:
    """A cookie whose email isn't in the users table must NOT grant
    access — redirects just like the anonymous case."""
    client.cookies.set(CG_USER_COOKIE, "ghost@froto.online")
    response = client.get("/roadmap", follow_redirects=False)
    client.cookies.clear()
    assert response.status_code == 302
    assert response.headers.get("location", "").endswith("/login")


# ---------------------------------------------------------------------------
# 200 path — base shape
# ---------------------------------------------------------------------------


def test_roadmap_with_valid_cookie_returns_200(
    client: TestClient, seeded_topics: None, user: int
) -> None:
    """With a valid user cookie, /roadmap must render 200 HTML."""
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_roadmap_extends_base_layout(
    client: TestClient, seeded_topics: None, user: int
) -> None:
    """The page must extend base.html (sidebar present)."""
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    assert "Code-Gym" in body
    # Sidebar links from base.html must be present (proves base.html
    # was used and the child template rendered inside the layout).
    assert 'href="/problems"' in body
    assert 'href="/profile"' in body


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_roadmap_lists_topics_in_order_index_ascending(
    client: TestClient, seeded_topics: None, user: int
) -> None:
    """Topics must be rendered in order_index ASC. The seed inserts
    them in the order graphs, arrays, dp — but the response must
    show arrays first (F1, order 10), then dp (F1, order 20), then
    graphs (F2, order 30)."""
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    arrays_pos = body.find("Vetores")
    dp_pos = body.find("Programação Dinâmica")
    graphs_pos = body.find("Grafos")

    assert arrays_pos != -1, "Vetores topic name missing from /roadmap"
    assert dp_pos != -1, "Programação Dinâmica topic name missing from /roadmap"
    assert graphs_pos != -1, "Grafos topic name missing from /roadmap"

    assert arrays_pos < dp_pos, (
        f"arrays (pos {arrays_pos}) must precede dp (pos {dp_pos})"
    )
    assert dp_pos < graphs_pos, (
        f"dp (pos {dp_pos}) must precede graphs (pos {graphs_pos})"
    )


# ---------------------------------------------------------------------------
# Progress: empty + populated
# ---------------------------------------------------------------------------


def test_roadmap_renders_zero_progress_when_no_submissions(
    client: TestClient, seeded_topics: None, user: int
) -> None:
    """With no submissions, every topic shows 0% (not blank, not NaN)."""
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    # The CSS class `progress-bar` (and an inline width) is what the
    # template renders. We assert both class presence and the literal
    # "0%" / "0 / N" so a future refactor that drops the percentage
    # text gets flagged.
    assert "progress-bar" in body, (
        "expected progress-bar CSS class to appear in /roadmap HTML"
    )
    assert "0%" in body, "expected 0% to appear when user has no submissions"
    assert "0 /" in body, "expected '0 / N' counter text in /roadmap HTML"


def test_roadmap_renders_partial_progress(
    client: TestClient, db_path: Path, user: int
) -> None:
    """User solved 1 of 2 problems in 'arrays' and 0 of 1 in 'graphs'.
    Expect arrays to show 50% and graphs to show 0%, with counters
    '1 / 2' and '0 / 1' respectively."""

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('arrays', 'Vetores', 'F1', 10);
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('graphs', 'Grafos', 'F2', 30);
            INSERT INTO problems (slug, title, topic_id, difficulty,
                                  statement_md, created_at)
                VALUES ('soma',   'Soma',          1, 1, 'x', '2026-08-09T00:00:00'),
                       ('max',    'Máximo',        1, 1, 'x', '2026-08-09T00:00:00'),
                       ('bfs',    'BFS básico',    2, 2, 'x', '2026-08-09T00:00:00');
            """
        )
        conn.execute(
            "INSERT INTO submissions (user_id, problem_id, code, language, "
            "verdict, attempt_n, submitted_at) "
            "VALUES (?, 1, 'print(3)', 'python', 'AC', 1, '2026-08-09T00:00:00')",
            (user,),
        )
        conn.commit()

    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    # 50% must appear (arrays: 1/2), 0% must appear (graphs: 0/1).
    assert "50%" in body, (
        "expected 50% progress for arrays topic (1 of 2 solved)"
    )
    assert "1 / 2" in body, "expected '1 / 2' counter for arrays"
    assert "0 / 1" in body, "expected '0 / 1' counter for graphs"

    # And the WA verdict for the same problem must NOT count it as solved.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO submissions (user_id, problem_id, code, language, "
            "verdict, attempt_n, submitted_at) "
            "VALUES (?, 2, 'print(99)', 'python', 'WA', 1, '2026-08-09T00:00:00')",
            (user,),
        )
        conn.commit()
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    # A WA on max does not count toward solved → arrays stays at 1/2 = 50%.
    assert "1 / 2" in body, (
        "WA verdict must NOT count toward solved; arrays should remain 1 / 2"
    )


def test_roadmap_progress_dedupes_per_problem(
    client: TestClient, db_path: Path, user: int
) -> None:
    """Two AC submissions for the same problem must count as ONE solved
    (a problem is solved iff at least one AC verdict exists)."""

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('arrays', 'Vetores', 'F1', 10);
            INSERT INTO problems (slug, title, topic_id, difficulty,
                                  statement_md, created_at)
                VALUES ('soma', 'Soma', 1, 1, 'x', '2026-08-09T00:00:00');
            """
        )
        # First AC, then a WA, then another AC — problem is solved.
        conn.execute(
            "INSERT INTO submissions (user_id, problem_id, code, language, "
            "verdict, attempt_n, submitted_at) VALUES "
            "(?, 1, 'a', 'python', 'AC', 1, '2026-08-09T00:00:00'),"
            "(?, 1, 'b', 'python', 'WA', 2, '2026-08-09T00:00:01'),"
            "(?, 1, 'c', 'python', 'AC', 3, '2026-08-09T00:00:02')",
            (user, user, user),
        )
        conn.commit()

    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    assert "1 / 1" in body, "duplicate AC submissions must count once"
    assert "100%" in body, "1/1 must render as 100%"


# ---------------------------------------------------------------------------
# Per-topic problem count — independent of submissions
# ---------------------------------------------------------------------------


def test_roadmap_total_problem_count_reflects_topic_problems(
    client: TestClient, db_path: Path, user: int
) -> None:
    """A topic with 3 problems and 0 solved shows '0 / 3'."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO topics (slug, name, obi_phase, order_index)
                VALUES ('arrays', 'Vetores', 'F1', 10);
            INSERT INTO problems (slug, title, topic_id, difficulty,
                                  statement_md, created_at)
                VALUES ('p1', 'P1', 1, 1, 'x', '2026-08-09T00:00:00'),
                       ('p2', 'P2', 1, 1, 'x', '2026-08-09T00:00:00'),
                       ('p3', 'P3', 1, 1, 'x', '2026-08-09T00:00:00');
            """
        )
        conn.commit()

    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    assert "0 / 3" in body, "topic with 3 problems must show '0 / 3'"


# ---------------------------------------------------------------------------
# Card link placeholder
# ---------------------------------------------------------------------------


def test_roadmap_topic_card_has_placeholder_link(
    client: TestClient, seeded_topics: None, user: int
) -> None:
    """Each topic card must link somewhere — M3.T3 will replace the
    placeholder ``href='#'`` with the real per-topic problem list.
    The brief accepts ``href='#'`` for now; we assert the card is a
    link at all so the placeholder swap is a one-line change later.

    The topic name appears inside the link body but wrapped by
    ``<header><h2>...</h2></header>`` (the card layout adds nesting).
    We use a permissive parser — any ``<a ...>...</a>`` block whose
    contents include the topic name counts."""
    client.cookies.set(CG_USER_COOKIE, "franco@froto.online")
    response = client.get("/roadmap")
    client.cookies.clear()
    body = response.text
    # Find every <a ...>...</a> block and check that each topic name
    # appears inside one of them. We do this with a simple state
    # machine rather than a regex (regex fails on nested tags).
    import re

    anchor_blocks = re.findall(r"<a\b[^>]*>(.*?)</a>", body, flags=re.DOTALL)
    for topic_name in ("Vetores", "Grafos"):
        assert any(topic_name in block for block in anchor_blocks), (
            f"topic {topic_name!r} is not wrapped in an anchor tag; "
            f"found anchors: {anchor_blocks}"
        )
