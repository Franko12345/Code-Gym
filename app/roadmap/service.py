"""Roadmap service — topic list + per-user progress.

The roadmap view is one row per topic, each row carrying the user's
"solved count" and the topic's "total problem count". A problem is
considered solved iff at least one submission by this user for that
problem has verdict = 'AC'.

Order: ``topics.order_index`` ascending, ties broken by slug
ascending. This matches the NeetCode-style grid and the OBI
F1 → F2 → F3 → UNI progression pinned by the plan.

Empty progress is always 0 (never None, never NaN, never blank) so
the template can render ``{{ percent }}%`` without conditional
arithmetic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union

from app.db import DEFAULT_DB_PATH, get_connection


# Accepted-verdict set. Kept as a module constant so a future
# addition (e.g. 'PE' for presentation error) is a one-line change.
SOLVED_VERDICT: str = "AC"


@dataclass(frozen=True)
class TopicProgress:
    """One topic row + the current user's progress through it."""

    slug: str
    name: str
    obi_phase: str | None
    order_index: int
    total_problems: int
    solved_problems: int

    @property
    def percent(self) -> int:
        """Integer percentage (0..100). Empty topics always return 0."""
        if self.total_problems <= 0:
            return 0
        # Round-half-up so 1/3 = 33, 2/3 = 67 (matches user
        # intuition for narrow bars).
        return (self.solved_problems * 100 + self.total_problems // 2) // self.total_problems


def list_topics_with_progress(
    db_path: Union[str, Path, None] = None,
    user_id: int | None = None,
) -> list[TopicProgress]:
    """Return topics ordered by ``order_index ASC, slug ASC``.

    ``user_id`` may be None (e.g. an internal caller); the solved
    count then collapses to 0 for every topic.
    """
    path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    with get_connection(path) as conn:
        rows: Iterable[sqlite3.Row] = conn.execute(
            """
            SELECT
                t.slug           AS slug,
                t.name           AS name,
                t.obi_phase      AS obi_phase,
                t.order_index    AS order_index,
                COUNT(DISTINCT p.id)                                   AS total_problems,
                COUNT(DISTINCT CASE WHEN s.verdict = ? THEN p.id END)  AS solved_problems
            FROM topics t
            LEFT JOIN problems p ON p.topic_id = t.id
            LEFT JOIN submissions s
                ON s.problem_id = p.id AND s.user_id = ?
            GROUP BY t.id, t.slug, t.name, t.obi_phase, t.order_index
            ORDER BY t.order_index ASC, t.slug ASC
            """,
            (SOLVED_VERDICT, user_id),
        ).fetchall()
    return [
        TopicProgress(
            slug=str(r["slug"]),
            name=str(r["name"]),
            obi_phase=r["obi_phase"],  # nullable
            order_index=int(r["order_index"]),
            total_problems=int(r["total_problems"] or 0),
            solved_problems=int(r["solved_problems"] or 0),
        )
        for r in rows
    ]


__all__: Iterable[str] = (
    "SOLVED_VERDICT",
    "TopicProgress",
    "list_topics_with_progress",
)
