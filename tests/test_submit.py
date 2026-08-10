"""Tests for POST /problems/{slug}/submit (M4.T4, ticket #16).

Acceptance criteria from the ticket:

* POST /problems/{slug}/submit without auth → 302 redirect to /login.
* POST with valid auth + correct code → 200 with verdict='AC' rendered.
* POST with wrong code → 200 with verdict='WA'.
* POST with infinite-loop code → 200 with verdict='TLE'.
* POST with invalid language → 400.
* Submission persisted to DB (verdict, code, language, attempt_n, runtime_ms).
* ELO updated after submission (verify via DB).
* Stop at first failing test case (don't run test_case[1+]).
* Per ADR-0005: only 'python' and 'cpp' accepted.
* Per ADR-0003: requires auth — no public submit.

Seam: real FastAPI app via TestClient. The runner is **monkeypatched**
in the test fixtures (sandbox user is not provisioned in the test
environment, and we don't want every test to fork a subprocess). This
mirrors the pattern in ``test_sandbox_runner.py`` where the runner is
tested in isolation with a patched ``pwd.getpwnam``. Here we patch
``app.problems.service.run`` — the symbol the route calls into — so
the tests pin the *integration* contract (route → service → DB)
without depending on the host environment.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_utils import encode_jwt
from app.db import init_db
from app.main import app


def _connect(path: Path) -> sqlite3.Connection:
    """Open ``path`` with ``row_factory = sqlite3.Row`` for column access.

    ``sqlite3.connect`` defaults to tuple rows; this helper pins
    Row access so test assertions read like the production code
    (``row["verdict"]`` not ``row[3]``).
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh SQLite file for each test, wired as the app's DB.

    Patches every module-level reference to the DB path that gets
    captured at import time:

    * ``app.db.DEFAULT_DB_PATH`` — the source of truth
    * ``app.auth.middleware.DB_PATH`` — the middleware reads this
      directly (not via ``DEFAULT_DB_PATH`` at request time) to look
      up the user behind the ``cg_session`` cookie
    * ``app.problems.routes.DEFAULT_DB_PATH`` and
      ``app.problems.service.DEFAULT_DB_PATH`` — the new module

    Patching all five keeps the request flow consistent: middleware
    reads user A, route calls service, service writes submission
    against user A — all on the same tmp DB.
    """
    p = tmp_path / "code_gym.db"
    init_db(p)
    monkeypatch.setattr("app.db.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.auth.middleware.DB_PATH", p)
    monkeypatch.setattr("app.problems.routes.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.problems.service.DEFAULT_DB_PATH", p)
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient bound to the real FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def seeded_user(db_path: Path) -> dict:
    """Insert a user; return ``{id, email, display_name}``.

    Initial ELO is 0 (the schema default). The submit tests exercise
    the +5 / -2 / 0 deltas from this baseline.
    """
    email = "franco@froto.online"
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            (email, "$2b$12$placeholder", "Franco"),
        )
        user_id = int(cur.lastrowid)
    return {"id": user_id, "email": email, "display_name": "Franco"}


@pytest.fixture()
def seeded_problem(db_path: Path) -> dict:
    """Insert one topic + one problem with two test cases.

    Test case 1 expects ``"42\n"``, test case 2 expects ``"hello\n"``.
    A correct submission matches both; a submission that only matches
    test 1 fails on test 2 → WA (and the runner is not called for
    test 3+).
    """
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO topics (slug, name, obi_phase, order_index) "
            "VALUES ('arrays', 'Vetores', 'F1', 10)"
        )
        cur = conn.execute(
            "INSERT INTO problems (slug, title, topic_id, difficulty, "
            "statement_md, created_at) "
            "VALUES ('soma', 'Soma', 1, 1, 'x', '2026-08-10T00:00:00')"
        )
        problem_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO test_cases (problem_id, stdin, expected_stdout, "
            "is_sample, weight) VALUES (?, ?, ?, 0, 1)",
            (problem_id, "", "42\n"),
        )
        conn.execute(
            "INSERT INTO test_cases (problem_id, stdin, expected_stdout, "
            "is_sample, weight) VALUES (?, ?, ?, 0, 1)",
            (problem_id, "hello", "hello\n"),
        )
        conn.commit()
    return {"slug": "soma", "id": problem_id}


@pytest.fixture()
def runner_mock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Replace ``app.problems.service.run`` with a stub.

    The stub returns ``Verdict(verdict='AC', runtime_ms=5, stderr='')``
    for every call. Tests that need a different verdict patch
    individual call return values via the returned ``set_verdict``
    helper.
    """
    from app.problems import service as service_mod
    from app.sandbox.runner import Verdict

    state: dict[str, str] = {"verdict": "AC"}

    def _fake_run(code: str, language: str, test_case_stdin: str, test_case_expected: str) -> Verdict:
        return Verdict(verdict=state["verdict"], runtime_ms=5, stderr="")

    monkeypatch.setattr(service_mod, "run", _fake_run)

    def _set(verdict: str) -> None:
        state["verdict"] = verdict

    yield _set


# Cookie name pinned in app.auth.middleware.
SESSION_COOKIE = "cg_session"


def _login(client: TestClient, user_id: int) -> None:
    """Set the ``cg_session`` cookie on the client to authenticate as ``user_id``.

    TestClient's per-request ``cookies=`` kwarg is deprecated
    ("Setting per-request cookies=<...> is being deprecated, because
    the expected behaviour on cookie persistence is ambiguous").
    We use the documented path: set cookies on the client instance.
    """
    client.cookies.set(SESSION_COOKIE, encode_jwt(user_id))


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_submit_without_auth_redirects_to_login(
    client: TestClient, seeded_problem: dict, runner_mock: Callable[[str], None]
) -> None:
    """Anonymous POST to /problems/{slug}/submit must 302 to /login.

    Per ADR-0003: submit is auth-required. The middleware sets
    ``request.state.user = None`` for unauthenticated requests, so
    the route must redirect.
    """
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(42)", "language": "python"},
        follow_redirects=False,
    )
    assert response.status_code == 302, (
        f"expected 302 redirect to /login, got {response.status_code}: {response.text!r}"
    )
    assert response.headers.get("location", "").endswith("/login"), (
        f"redirect target must be /login, got: {response.headers.get('location')!r}"
    )


# ---------------------------------------------------------------------------
# Happy path: AC
# ---------------------------------------------------------------------------


def test_submit_with_correct_code_renders_ac_verdict(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """A submission that the runner judges AC must render the AC verdict.

    The route is HTMX-friendly: it returns HTML (the
    ``submission_result.html`` partial). The test asserts the verdict
    text appears in the body — the exact CSS class is a render detail
    and lives in the template.
    """
    runner_mock("AC")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(42)", "language": "python"},
    )
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text!r}"
    )
    body = response.text
    assert "AC" in body, f"verdict 'AC' must appear in response, body={body!r}"


# ---------------------------------------------------------------------------
# WA path
# ---------------------------------------------------------------------------


def test_submit_with_wrong_code_renders_wa_verdict(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """A submission judged WA must render the WA verdict."""
    runner_mock("WA")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(43)", "language": "python"},
    )
    assert response.status_code == 200
    assert "WA" in response.text, (
        f"verdict 'WA' must appear in response, body={response.text!r}"
    )


# ---------------------------------------------------------------------------
# TLE path
# ---------------------------------------------------------------------------


def test_submit_with_infinite_loop_renders_tle_verdict(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """A submission that hits the timeout must render TLE."""
    runner_mock("TLE")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "while True: pass", "language": "python"},
    )
    assert response.status_code == 200
    assert "TLE" in response.text, (
        f"verdict 'TLE' must appear in response, body={response.text!r}"
    )


# ---------------------------------------------------------------------------
# Invalid language
# ---------------------------------------------------------------------------


def test_submit_with_invalid_language_returns_400(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """A non-ADR-0005 language (e.g. 'rust', 'javascript', 'php') must
    return 400 — the route validates before invoking the runner.

    Per ADR-0005, MVP supports C++ and Python only. Anything else
    must be rejected with 400 so the runner is never asked to execute
    a language the sandbox doesn't support.
    """
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "fn main() {}", "language": "rust"},
    )
    assert response.status_code == 400, (
        f"unsupported language must return 400, got {response.status_code}: "
        f"{response.text!r}"
    )


def test_submit_accepts_python_and_cpp_languages(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """Both 'python' and 'cpp' must be accepted (ADR-0005 whitelist)."""
    runner_mock("AC")
    _login(client, seeded_user["id"])
    for lang in ("python", "cpp"):
        response = client.post(
            f"/problems/{seeded_problem['slug']}/submit",
            data={"code": "x = 1", "language": lang},
        )
        assert response.status_code == 200, (
            f"language {lang!r} must be accepted, got {response.status_code}: "
            f"{response.text!r}"
        )


# ---------------------------------------------------------------------------
# Submission persistence
# ---------------------------------------------------------------------------


def test_submit_persists_submission_row(
    client: TestClient,
    db_path: Path,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """The submission must be written to the ``submissions`` table.

    The contract: after a POST, exactly one row exists for this user
    + problem, with the right verdict/code/language/attempt_n/
    runtime_ms. attempt_n for the first submission is 1.
    """
    runner_mock("AC")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(42)", "language": "python"},
    )
    assert response.status_code == 200

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT code, language, verdict, attempt_n, runtime_ms "
            "FROM submissions WHERE user_id = ? AND problem_id = ?",
            (seeded_user["id"], seeded_problem["id"]),
        ).fetchall()

    assert len(rows) == 1, f"expected exactly 1 submission row, got {len(rows)}"
    row = rows[0]
    assert row["code"] == "print(42)", f"code mismatch: {row['code']!r}"
    assert row["language"] == "python", f"language mismatch: {row['language']!r}"
    assert row["verdict"] == "AC", f"verdict mismatch: {row['verdict']!r}"
    assert row["attempt_n"] == 1, f"attempt_n should be 1, got {row['attempt_n']}"
    # runtime_ms is whatever the runner reported — must be a non-negative int.
    assert isinstance(row["runtime_ms"], int) and row["runtime_ms"] >= 0, (
        f"runtime_ms must be non-negative int, got {row['runtime_ms']!r}"
    )


def test_submit_attempt_n_increments_per_submission(
    client: TestClient,
    db_path: Path,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """Two submissions for the same (user, problem) must have
    attempt_n=1 then attempt_n=2."""
    runner_mock("AC")
    _login(client, seeded_user["id"])
    for expected_n in (1, 2):
        response = client.post(
            f"/problems/{seeded_problem['slug']}/submit",
            data={"code": f"v{expected_n}", "language": "python"},
        )
        assert response.status_code == 200

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT attempt_n FROM submissions WHERE user_id = ? AND "
            "problem_id = ? ORDER BY id ASC",
            (seeded_user["id"], seeded_problem["id"]),
        ).fetchall()

    attempt_ns = [r["attempt_n"] for r in rows]
    assert attempt_ns == [1, 2], (
        f"attempt_n should increment per submission, got {attempt_ns}"
    )


# ---------------------------------------------------------------------------
# ELO update
# ---------------------------------------------------------------------------


def test_submit_updates_elo_on_ac(
    client: TestClient,
    db_path: Path,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """ELO must increase by 5 on an AC submission (per the v0.1.0
    simple formula). Baseline is the schema default 0; after one AC
    the user's elo must be 5.
    """
    runner_mock("AC")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(42)", "language": "python"},
    )
    assert response.status_code == 200

    with _connect(db_path) as conn:
        elo = conn.execute(
            "SELECT elo FROM users WHERE id = ?", (seeded_user["id"],)
        ).fetchone()["elo"]
    assert int(elo) == 5, f"ELO should be 5 after AC, got {elo}"


def test_submit_updates_elo_on_wa(
    client: TestClient,
    db_path: Path,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """ELO must decrease by 2 on a WA submission (per the v0.1.0
    simple formula)."""
    runner_mock("WA")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(99)", "language": "python"},
    )
    assert response.status_code == 200

    with _connect(db_path) as conn:
        elo = conn.execute(
            "SELECT elo FROM users WHERE id = ?", (seeded_user["id"],)
        ).fetchone()["elo"]
    assert int(elo) == -2, f"ELO should be -2 after WA, got {elo}"


def test_submit_elo_unchanged_on_tle(
    client: TestClient,
    db_path: Path,
    seeded_user: dict,
    seeded_problem: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """ELO must be unchanged on TLE/RE/CE (delta=0 per the v0.1.0
    formula)."""
    runner_mock("TLE")
    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "while True: pass", "language": "python"},
    )
    assert response.status_code == 200

    with _connect(db_path) as conn:
        elo = conn.execute(
            "SELECT elo FROM users WHERE id = ?", (seeded_user["id"],)
        ).fetchone()["elo"]
    assert int(elo) == 0, f"ELO should be 0 after TLE (no change), got {elo}"


# ---------------------------------------------------------------------------
# Stop at first failing test case (per ticket constraint)
# ---------------------------------------------------------------------------


def test_submit_stops_at_first_failing_test_case(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per the brief: "Stop on first failure: if test_case[0] WA, return
    WA immediately, don't run test_case[1+]." The service must short
    circuit the loop.

    We assert this by recording every call to the runner. The
    seeded problem has 2 test cases; we return WA on the first and
    AC on the second. The runner must be called once, not twice.
    """
    from app.problems import service as service_mod
    from app.sandbox.runner import Verdict

    call_log: list[tuple[str, str]] = []

    def _fake_run(code: str, language: str, test_case_stdin: str, test_case_expected: str) -> Verdict:
        call_log.append((test_case_stdin, test_case_expected))
        # First call → WA, second would be AC but we should never reach it.
        if len(call_log) == 1:
            return Verdict(verdict="WA", runtime_ms=3, stderr="wrong")
        return Verdict(verdict="AC", runtime_ms=3, stderr="")

    monkeypatch.setattr(service_mod, "run", _fake_run)

    _login(client, seeded_user["id"])
    response = client.post(
        f"/problems/{seeded_problem['slug']}/submit",
        data={"code": "print(0)", "language": "python"},
    )
    assert response.status_code == 200
    assert "WA" in response.text
    assert len(call_log) == 1, (
        f"runner must be called only once when test[0] fails, got {len(call_log)} "
        f"calls: {call_log}"
    )


# ---------------------------------------------------------------------------
# Unknown problem
# ---------------------------------------------------------------------------


def test_submit_unknown_problem_returns_404(
    client: TestClient,
    seeded_user: dict,
    runner_mock: Callable[[str], None],
) -> None:
    """A submission for a non-existent problem slug must 404.

    The brief doesn't pin 404 explicitly, but a wrong slug is a
    bad request — not a silent no-op, not a 500. 404 keeps the
    response obvious to the client.
    """
    _login(client, seeded_user["id"])
    response = client.post(
        "/problems/does-not-exist/submit",
        data={"code": "print(1)", "language": "python"},
    )
    assert response.status_code == 404, (
        f"unknown problem must 404, got {response.status_code}: {response.text!r}"
    )
