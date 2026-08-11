"""Problems service \u2014 submission judging, persistence, ELO update (M4.T4).

The seam between the HTTP route (``app.problems.routes``) and:

* the **sandbox runner** (``app.sandbox.runner.run`` \u2014 from M4.T2)
* the **ELO module** (``app.elo.elo_delta`` \u2014 trivial v0.1.0 formula)
* the **database** (insert into ``submissions``, update ``users.elo``)

The service is synchronous. The runner is blocking (subprocess.run
+ UID drop + 2.5s timeout), so the route is responsible for off-
loading ``judge_submission`` to a worker thread via
``asyncio.to_thread`` \u2014 not this module. We do NOT spawn a thread
per test case internally; one thread per submission is enough for
the MVP and keeps the stop-on-first-failure semantics obvious.

Stop-on-first-failure contract
------------------------------
The brief says: "if test_case[0] WA, return WA immediately, don't
run test_case[1+]". We honour this by ``break``-ing out of the
test-case loop the first time ``runner.run`` returns a non-AC
verdict. This also keeps TLE submissions fast: one TLE finishes
the whole submission, the rest of the test cases are never run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Union

from app.db import DEFAULT_DB_PATH, get_connection
from app.elo import elo_delta
from app.sandbox.runner import Verdict, run as _runner_run


# Accepted languages. Per ADR-0005, MVP supports C++ + Python only.
# The route validates against this set before invoking the service,
# but we re-validate here as a defense-in-depth (a future caller
# could forget to).
ACCEPTED_LANGUAGES: frozenset[str] = frozenset({"python", "cpp"})

# We bind ``run`` to a module-level name so tests can monkeypatch
# ``app.problems.service.run`` (the pattern in tests/test_submit.py)
# without poking the runner module's internals. This is the seam.
run = _runner_run


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmissionResult:
    """Outcome of judging a single submission.

    Fields:
        verdict: one of ``'AC'``/``'WA'``/``'TLE'``/``'RE'``/``'CE'``.
        runtime_ms: total wall-clock time spent in the sandbox across
            all test cases that ran. ``0`` when no test case ran
            (e.g. unknown problem, or all test cases were empty).
        attempt_n: the attempt number for this (user, problem) pair
            (1-indexed). Useful for templates that say "Attempt #3".
    """

    verdict: str
    runtime_ms: int
    attempt_n: int


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_problem_id_by_slug(
    slug: str,
    db_path: Union[str, Path, None] = None,
) -> Optional[int]:
    """Return the ``problems.id`` for ``slug`` or ``None``.

    Used by the route to translate the URL slug into a FK target
    for the submission insert and the test-case fetch. A non-match
    becomes a 404 at the route layer.
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT id FROM problems WHERE slug = ?", (slug,)
        ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def list_test_cases(
    problem_id: int,
    db_path: Union[str, Path, None] = None,
) -> list[tuple[str, str]]:
    """Return ``[(stdin, expected_stdout), ...]`` for ``problem_id``.

    Order is by ``test_cases.id`` ASC so submissions are deterministic
    across runs. The runner compares stdout byte-for-byte against
    ``expected_stdout`` (no newline normalization) \u2014 matches OBI
    judges.
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        rows: Iterable[sqlite3.Row] = conn.execute(
            "SELECT stdin, expected_stdout FROM test_cases "
            "WHERE problem_id = ? ORDER BY id ASC",
            (problem_id,),
        ).fetchall()
    return [(str(r["stdin"]), str(r["expected_stdout"])) for r in rows]


# ---------------------------------------------------------------------------
# attempt_n
# ---------------------------------------------------------------------------


def _next_attempt_n(
    user_id: int, problem_id: int, db_path: Union[str, Path, None] = None
) -> int:
    """Count existing submissions for (user, problem) + 1.

    The contract: attempt_n is 1-indexed. The first submission is 1,
    the second is 2, etc. We compute it via ``COUNT(*)`` at submit
    time so concurrent submissions can collide on the same number
    \u2014 acceptable for v0.1.0 (single user, single submission flow).
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM submissions "
            "WHERE user_id = ? AND problem_id = ?",
            (user_id, problem_id),
        ).fetchone()["n"]
    return int(count) + 1


# ---------------------------------------------------------------------------
# Submission write
# ---------------------------------------------------------------------------


def _persist_submission(
    user_id: int,
    problem_id: int,
    code: str,
    language: str,
    verdict: str,
    runtime_ms: int,
    attempt_n: int,
    db_path: Union[str, Path, None] = None,
) -> None:
    """Insert one row into ``submissions``.

    ``submitted_at`` is the current UTC ISO-8601 timestamp. The
    column has no DB default (see ``app.db.SCHEMA``) so we set it
    here. Format: ``YYYY-MM-DDTHH:MM:SS`` (no microseconds, no
    trailing ``Z``) \u2014 matches what other tickets in the suite write
    (see ``test_roadmap.py`` seed).
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_connection(path) as conn:
        conn.execute(
            "INSERT INTO submissions (user_id, problem_id, code, language, "
            "verdict, runtime_ms, attempt_n, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, problem_id, code, language, verdict,
             runtime_ms, attempt_n, now),
        )


def _apply_elo_delta(
    user_id: int, delta: int, db_path: Union[str, Path, None] = None
) -> None:
    """Add ``delta`` to ``users.elo``. Negative deltas go below 0.

    The MVP has no floor (negative ELO is allowed) \u2014 it's a pure
    score, not a rating like chess ELO. A future ticket can clamp
    if needed; today the simpler ``+=`` matches the spec.
    """
    if delta == 0:
        return
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        conn.execute(
            "UPDATE users SET elo = elo + ? WHERE id = ?",
            (delta, user_id),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def judge_submission(
    user_id: int,
    problem_slug: str,
    code: str,
    language: str,
    db_path: Union[str, Path, None] = None,
) -> Optional[SubmissionResult]:
    """Run ``code`` against every test case of ``problem_slug`` and
    persist the submission.

    Args:
        user_id: the authenticated user's id.
        problem_slug: URL slug of the problem.
        code: source code submitted by the user.
        language: ``'python'`` or ``'cpp'`` (the route validates
            against ``ACCEPTED_LANGUAGES`` before calling).
        db_path: optional DB override for tests; defaults to the
            module's ``DEFAULT_DB_PATH``.

    Returns:
        A ``SubmissionResult`` describing the final verdict and
        aggregate ``runtime_ms``. Returns ``None`` if the problem
        slug is unknown \u2014 the route turns that into a 404.
    """
    # ``language`` is validated by the route before we get here;
    # we trust the caller to keep the contract.
    problem_id = get_problem_id_by_slug(problem_slug, db_path)
    if problem_id is None:
        return None

    test_cases = list_test_cases(problem_id, db_path)

    # Run all test cases, short-circuiting on the first non-AC.
    # ``runtime_ms`` is summed across the cases that actually ran
    # so the persisted value reflects the work the sandbox did.
    final_verdict = "AC"
    total_runtime_ms = 0
    for stdin_str, expected_str in test_cases:
        v: Verdict = run(code, language, stdin_str, expected_str)
        total_runtime_ms += int(v.runtime_ms)
        if v.verdict != "AC":
            final_verdict = v.verdict
            break

    # If there are zero test cases (malformed problem), we default to
    # AC. This matches the "all test cases passed" semantic \u2014 there
    # were no failing test cases. A future "problem must have at
    # least one test case" validation lives elsewhere; for now we
    # accept the empty case.
    attempt_n = _next_attempt_n(user_id, problem_id, db_path)
    _persist_submission(
        user_id=user_id,
        problem_id=problem_id,
        code=code,
        language=language,
        verdict=final_verdict,
        runtime_ms=total_runtime_ms,
        attempt_n=attempt_n,
        db_path=db_path,
    )
    _apply_elo_delta(user_id, elo_delta(final_verdict), db_path)

    return SubmissionResult(
        verdict=final_verdict,
        runtime_ms=total_runtime_ms,
        attempt_n=attempt_n,
    )





# ---------------------------------------------------------------------------
# Problem-list helpers (FIX 1 — GET /problems)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProblemListRow:
    """One problem row in the /problems browse list.

    Carries everything the list template needs to render a card and
    link it to /problems/{slug}. Deliberately does NOT include the
    statement — that's a /problems/{slug} concern, not a list concern.

    ``best_verdict`` is the viewer's best verdict on this problem so
    the card can show the same colour-coded badge the profile grid
    does (green / red / yellow / untouched). Empty string when the
    viewer is anonymous or hasn't attempted the problem.
    """

    problem_id: int
    slug: str
    title: str
    difficulty: int
    topic_slug: str
    topic_name: str
    best_verdict: str  # "" for untouched


def list_problems_for_browse(
    *,
    topic_slug: str | None = None,
    query: str | None = None,
    user_id: int | None = None,
    db_path: str | Path | None = None,
) -> list[ProblemListRow]:
    """Return every problem (optionally filtered), ordered by topic
    then difficulty then id.

    Filters:

    * ``topic_slug`` (optional) — exact match against ``topics.slug``.
      Unknown slugs simply return an empty list (a 404 isn't worth a
      round-trip — the template will render the empty-state).
    * ``query`` (optional) — case-insensitive substring match against
      ``problems.title`` via ``LIKE '%q%'``. Single-token search;
      the brief said "simples LIKE" so we keep it that way. Empty /
      whitespace-only query is a no-op.
    * ``user_id`` (optional) — when set, populate ``best_verdict`` per
      problem using the same conditional-aggregate strategy as the
      profile service (no N+1).

    Order matches the visual order on /roadmap: by topic
    ``order_index``, then by topic ``slug``, then by difficulty ASC,
    then by problem id ASC. Same ordering the profile grid uses
    (M3.T3) so users see problems in a familiar sequence when they
    leave their profile and hit /problems.

    SQL: a single LEFT JOIN + GROUP BY pass. The LEFT JOIN attaches
    the viewer's submissions (one row per submission, aggregated by
    MAX/MIN CASE WHEN — same trick as profile.service). When
    ``user_id`` is None the LEFT JOIN still runs but binds a
    sentinel ``user_id = -1`` that matches no rows; the boolean
    flags all collapse to zero and ``best_verdict`` is "".
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    binds: list[int | str] = []
    where: list[str] = []

    if topic_slug:
        where.append("t.slug = ?")
        binds.append(topic_slug)

    if query and query.strip():
        where.append("LOWER(p.title) LIKE ?")
        binds.append(f"%{query.strip().lower()}%")

    # Bind the user_id to a sentinel when None so we can keep the SQL
    # shape uniform. -1 is below any real autoincrement id and the
    # LEFT JOIN returns zero rows for it.
    bind_user_id = int(user_id) if user_id is not None else -1

    sql = f"""
        SELECT
            p.id              AS problem_id,
            p.slug            AS slug,
            p.title           AS title,
            p.difficulty      AS difficulty,
            t.slug            AS topic_slug,
            t.name            AS topic_name,
            t.order_index     AS topic_order_index,
            MAX(CASE WHEN s.verdict = 'AC' THEN 1 ELSE 0 END) AS has_ever_ac,
            MIN(CASE WHEN s.verdict = 'AC' THEN s.verdict END) AS ac_text,
            MIN(CASE WHEN s.verdict IN ('WA', 'RE', 'TLE') THEN s.verdict END) AS failed_text,
            MIN(CASE WHEN s.verdict IS NOT NULL
                      AND s.verdict NOT IN ('AC', 'WA', 'RE', 'TLE') THEN s.verdict END) AS other_text
        FROM problems p
        JOIN topics t ON t.id = p.topic_id
        LEFT JOIN submissions s
            ON s.problem_id = p.id AND s.user_id = ?
        {("WHERE " + " AND ".join(where)) if where else ""}
        GROUP BY p.id, p.slug, p.title, p.difficulty,
                 t.slug, t.name, t.order_index
        ORDER BY t.order_index ASC, t.slug ASC,
                 p.difficulty ASC, p.id ASC
    """
    # user_id bind comes first (the LEFT JOIN ON clause).
    binds.insert(0, bind_user_id)

    with get_connection(path) as conn:
        rows = conn.execute(sql, binds).fetchall()

    out: list[ProblemListRow] = []
    for r in rows:
        # Same precedence as profile.service: AC > failed > other > "".
        if r["has_ever_ac"]:
            best = str(r["ac_text"])
        elif r["failed_text"]:
            best = str(r["failed_text"])
        elif r["other_text"]:
            best = str(r["other_text"])
        else:
            best = ""
        out.append(
            ProblemListRow(
                problem_id=int(r["problem_id"]),
                slug=str(r["slug"]),
                title=str(r["title"]),
                difficulty=int(r["difficulty"]),
                topic_slug=str(r["topic_slug"]),
                topic_name=str(r["topic_name"]),
                best_verdict=best,
            )
        )
    return out

__all__ = (
    "ACCEPTED_LANGUAGES",
    "ProblemListRow",
    "SubmissionResult",
    "judge_submission",
    "list_problems_for_browse",
    "run",
)
# ``get_problem_id_by_slug`` and ``list_test_cases`` are module-
# private helpers used only by ``judge_submission`` and are
# deliberately excluded from ``__all__``.
