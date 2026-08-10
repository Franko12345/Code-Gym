"""Profile service — user lookup + per-problem best verdict.

The profile view shows one card per problem across ALL topics,
coloured by the user's best verdict on that problem. The colour rule
is the contract — documented here AND in the test file so it has
exactly one source of truth (the test file) and one implementation
(this module).

Best-verdict classification (per (user, problem)):

* ``STATUS_SOLVED``   ("solved", green)  — at least one submission
                                          with verdict ``AC``.
* ``STATUS_FAILED``   ("failed", red)    — no AC, but at least one
                                          submission with verdict in
                                          ``{WA, RE, TLE}`` (code ran
                                          and produced a wrong/timeout/
                                          crash verdict).
* ``STATUS_ERROR``    ("error", yellow)  — has submissions, but none
                                          ``AC`` and none in
                                          ``{WA, RE, TLE}`` (currently
                                          catches ``CE`` — code never
                                          ran because it didn't
                                          compile).
* ``STATUS_UNTOUCHED``("untouched", gray) — zero submissions.

The verdict sets are kept as module constants so future additions
(e.g. ``PE`` for presentation error) are a one-line change.

A single SQL pass computes both the problem list AND each user's
best verdict via conditional aggregation. This avoids an N+1 query
that would otherwise walk every submission per problem.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union

from app.db import DEFAULT_DB_PATH, get_connection


# ---------------------------------------------------------------------------
# Verdict sets (the colour rule)
# ---------------------------------------------------------------------------

# A problem is "solved" iff at least one submission has verdict = 'AC'.
STATUS_SOLVED: str = "solved"

# "Failed" — code ran but produced a wrong/timeout/crash verdict, and
# there is no AC for this problem. This is the red bucket.
STATUS_FAILED: str = "failed"

# "Error" — user submitted something but it never produced a runtime
# verdict (e.g. compilation error). Currently only CE lives here.
# Yellow bucket.
STATUS_ERROR: str = "error"

# No submissions at all.
STATUS_UNTOUCHED: str = "untouched"

# Verdicts that mark a problem as "failed" (red). Kept as a tuple
# (immutable) so the membership check below is cheap and the set is
# trivially extensible.
FAILED_VERDICTS: tuple[str, ...] = ("WA", "RE", "TLE")

# All verdicts we count as an attempt (anything else = untouched).
ALL_ATTEMPT_VERDICTS: tuple[str, ...] = ("AC", "WA", "RE", "TLE", "CE")


# ---------------------------------------------------------------------------
# View dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileUser:
    """A small read-only view of a ``users`` row for the profile header.

    Mirrors the fields the header template renders. ELO is included
    in the row (so a future "show ELO" toggle is a one-line template
    change) but the template deliberately does NOT display it in the
    MVP.
    """

    id: int
    email: str
    display_name: str  # never None — the route uses it as the username
    elo: int


@dataclass(frozen=True)
class ProblemStatus:
    """One row in the grid: the problem + the user's best verdict.

    ``status`` is one of ``STATUS_SOLVED``, ``STATUS_FAILED``,
    ``STATUS_ERROR``, ``STATUS_UNTOUCHED``. The template maps it to
    a CSS class via a small {% if %} ladder.
    """

    problem_id: int
    problem_slug: str
    problem_title: str
    topic_slug: str
    topic_name: str
    difficulty: int
    status: str
    # Best-verdict text for the tooltip ("AC", "WA", "RE", "TLE", "CE",
    # or "" for untouched). Empty string keeps the tooltip absent for
    # untouched problems — no spurious "(never tried)" hover text.
    best_verdict: str


# ---------------------------------------------------------------------------
# Service entry points
# ---------------------------------------------------------------------------


def get_profile_user_by_username(
    username_or_id: str | int,
    db_path: Union[str, Path, None] = None,
) -> ProfileUser | None:
    """Look up a user by display_name (the username shown in /u/{name})
    OR by integer id. Returns ``None`` if the user doesn't exist.

    The brief uses ``username`` in the URL (``/u/{username}``), so
    the primary lookup is by ``display_name``. We also accept a bare
    integer so a future ``/u/{id}`` URL is a no-op upgrade.

    display_name is the public identity shown in the URL; email is
    private. A user without a display_name cannot be addressed via
    this route — they'd need to be referenced by id. (Every account
    created via the CLI in M1.T3 sets a display_name, so this is a
    safe default.)
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    # Accept both integer ids and display_name strings so future
    # `/u/{id}` routes work without a service-layer change.
    sql_by_id = (
        "SELECT id, email, display_name, elo "
        "FROM users WHERE id = ?"
    )
    sql_by_name = (
        "SELECT id, email, display_name, elo "
        "FROM users WHERE display_name = ?"
    )
    with get_connection(path) as conn:
        if isinstance(username_or_id, int) or (
            isinstance(username_or_id, str) and username_or_id.isdigit()
        ):
            row = conn.execute(sql_by_id, (int(username_or_id),)).fetchone()
        else:
            row = conn.execute(sql_by_name, (username_or_id,)).fetchone()
        if row is None:
            return None
        # display_name is NOT NULL in spirit but nullable in the schema
        # (M1.T1 didn't enforce a default). If a user slipped through
        # with NULL display_name, we refuse to serve the profile —
        # the /u/{username} URL wouldn't make sense.
        if row["display_name"] is None:
            return None
        return ProfileUser(
            id=int(row["id"]),
            email=str(row["email"]),
            display_name=str(row["display_name"]),
            elo=int(row["elo"] or 0),
        )


def list_problem_statuses_for_user(
    user_id: int,
    db_path: Union[str, Path, None] = None,
) -> list[ProblemStatus]:
    """Return one ``ProblemStatus`` per problem in the DB, with the
    user's best-verdict classification for that problem.

    Order: by topic.order_index ASC, then topic.slug ASC, then
    problem.difficulty ASC, then problem.id ASC. This matches the
    roadmap visual order (OBI F1 → F2 → F3 → UNI, easier-first within
    each topic). Empty topics contribute no rows.

    SQL strategy: a single LEFT JOIN with conditional aggregation.
    For each problem we compute:
        * has_ac:   count of submissions with verdict='AC'      (>0 → solved)
        * has_fail: count of submissions with verdict in {WA,RE,TLE}
                                                              (>0 → failed)
        * has_any:  count of submissions (any verdict)        (>0 → attempted)
    then post-process in Python to apply the precedence rule
    (solved > failed > error > untouched).
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    # Build the IN clause for FAILED_VERDICTS — kept as a tuple bound
    # by the length so SQL injection is impossible (the values are
    # hardcoded module constants).
    failed_clause = ",".join("?" for _ in FAILED_VERDICTS)
    sql = f"""
        SELECT
            p.id              AS problem_id,
            p.slug            AS problem_slug,
            p.title           AS problem_title,
            p.difficulty      AS difficulty,
            t.slug            AS topic_slug,
            t.name            AS topic_name,
            t.order_index     AS topic_order_index,
            -- best_verdict = 'AC' if any AC, else first failed if any
            -- failed, else first non-empty of (any verdict) if any
            -- submission, else NULL. We pick the first attempt (lowest
            -- attempt_n) for "best" so the tooltip is stable.
            CASE
                WHEN SUM(CASE WHEN s.verdict = 'AC'              THEN 1 ELSE 0 END) > 0
                    THEN 'AC'
                WHEN SUM(CASE WHEN s.verdict IN ({failed_clause}) THEN 1 ELSE 0 END) > 0
                    THEN (SELECT verdict FROM submissions
                          WHERE user_id = ? AND problem_id = p.id
                            AND verdict IN ({failed_clause})
                          ORDER BY attempt_n ASC LIMIT 1)
                WHEN SUM(CASE WHEN s.verdict IS NOT NULL          THEN 1 ELSE 0 END) > 0
                    THEN (SELECT verdict FROM submissions
                          WHERE user_id = ? AND problem_id = p.id
                          ORDER BY attempt_n ASC LIMIT 1)
                ELSE NULL
            END              AS best_verdict
        FROM problems p
        JOIN topics t ON t.id = p.topic_id
        LEFT JOIN submissions s
            ON s.problem_id = p.id AND s.user_id = ?
        GROUP BY p.id, p.slug, p.title, p.difficulty,
                 t.slug, t.name, t.order_index
        ORDER BY t.order_index ASC, t.slug ASC,
                 p.difficulty ASC, p.id ASC
    """
    params: list[Union[str, int]] = [
        *FAILED_VERDICTS,
        user_id,
        *FAILED_VERDICTS,
        user_id,
        user_id,
    ]
    with get_connection(path) as conn:
        rows: Iterable[sqlite3.Row] = conn.execute(sql, params).fetchall()

    out: list[ProblemStatus] = []
    for r in rows:
        best = r["best_verdict"]
        best_str = str(best) if best is not None else ""
        out.append(
            ProblemStatus(
                problem_id=int(r["problem_id"]),
                problem_slug=str(r["problem_slug"]),
                problem_title=str(r["problem_title"]),
                topic_slug=str(r["topic_slug"]),
                topic_name=str(r["topic_name"]),
                difficulty=int(r["difficulty"]),
                status=_classify(best_str),
                best_verdict=best_str,
            )
        )
    return out


def _classify(best_verdict: str) -> str:
    """Apply the colour-rule precedence to a best-verdict string."""
    if best_verdict == "AC":
        return STATUS_SOLVED
    if best_verdict in FAILED_VERDICTS:
        return STATUS_FAILED
    if best_verdict != "":
        # any other non-empty verdict (currently CE) — attempted but
        # never produced a runtime verdict.
        return STATUS_ERROR
    return STATUS_UNTOUCHED


__all__: Iterable[str] = (
    "ALL_ATTEMPT_VERDICTS",
    "FAILED_VERDICTS",
    "ProfileUser",
    "ProblemStatus",
    "STATUS_ERROR",
    "STATUS_FAILED",
    "STATUS_SOLVED",
    "STATUS_UNTOUCHED",
    "get_profile_user_by_username",
    "list_problem_statuses_for_user",
)