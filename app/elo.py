"""Simple ELO delta function for Code-Gym v0.1.0 (ticket #16).

This is a **placeholder** until v0.2 lands a real ELO system (one
based on problem difficulty + the user's prior solving history).

The v0.1.0 formula is intentionally trivial:

* ``+5`` on an accepted submission (``AC``)
* ``-2`` on a wrong answer (``WA``)
* ``0`` on every other verdict (``TLE``, ``RE``, ``CE``)

Why these specific numbers? Two reasons:

1. **Asymmetry** \u2014 solving is rarer than failing, so the +5 / -2
   ratio lets a user trend upward with a moderate solve rate.
2. **Zero on the other buckets** \u2014 we don't punish timeouts, crashes,
   or compile errors the same way we punish wrong answers. CE is
   almost always a typo; TLE is a missing optimisation; neither
   tells us much about the user's overall skill yet.

If you want a real ELO system (with opponent ratings, K-factor
calibration, problem-difficulty weighting), see v0.2 ticket queue.
For now: small, predictable, easy to test.
"""

from __future__ import annotations

from typing import Final

# Positive delta on an Accepted submission.
ELO_DELTA_AC: Final[int] = 5

# Negative delta on a Wrong Answer submission.
ELO_DELTA_WA: Final[int] = -2

# Every other verdict (TLE / RE / CE) keeps ELO unchanged.
ELO_DELTA_OTHER: Final[int] = 0


def elo_delta(verdict: str) -> int:
    """Return the ELO delta to apply for a submission verdict.

    Args:
        verdict: one of ``'AC'``, ``'WA'``, ``'TLE'``, ``'RE'``,
            ``'CE'`` (case-insensitive). Unknown values are treated
            as "other" (no change) so a future verdict addition
            (e.g. ``'PE'``) is safe by default.

    Returns:
        The integer delta to add to the user's ELO. ``+5`` for AC,
        ``-2`` for WA, ``0`` for everything else.
    """
    v = verdict.upper().strip()
    if v == "AC":
        return ELO_DELTA_AC
    if v == "WA":
        return ELO_DELTA_WA
    return ELO_DELTA_OTHER


__all__: tuple[str, ...] = (
    "ELO_DELTA_AC",
    "ELO_DELTA_OTHER",
    "ELO_DELTA_WA",
    "elo_delta",
)
