"""Public profile module — M3.T3, ticket #12.

Exposes ``GET /u/{username_or_id}`` for viewing any user's progress
grid. The page is public (no login required) per ADR-0003 spirit —
the user creation gate is invite-only, but viewing progress is
free. ELO is stored in the DB but NOT rendered here (per MVP scope).

Seam: ``service.py`` returns one row per problem (across all topics)
with a per-problem best-verdict classification the template renders
as a NeetCode-style colored grid.
"""