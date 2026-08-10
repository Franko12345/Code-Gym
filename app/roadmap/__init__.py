"""Roadmap module — M3.T2.

Provides the /roadmap route and the per-user progress query. The
auth seam (``current_user``) is local until M1.T2 (JWT middleware)
merges; the route shape stays the same and only the dep body changes.
"""
