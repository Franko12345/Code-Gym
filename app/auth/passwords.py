"""Password hashing utilities.

Wraps the ``bcrypt`` package with a tiny seam so the rest of the
codebase (CLI create-user, future login route) deals only in
``str`` -> ``str`` and ``str, str`` -> ``bool``. The lower-level
bytes API is contained here.

Design choices (pinned by module constants + tests):

* ``bcrypt`` not ``passlib`` — passlib has been unmaintained since
  2020 and emits warnings on modern bcrypt. The ``bcrypt`` pkg is the
  reference implementation and is already in ``pyproject.toml``.

* cost = 12 (OWASP 2024 Password Storage Cheat Sheet recommendation).
  Cost 12 takes ~250 ms on a modern CPU — slow enough to deter
  brute-force, fast enough that a single-user CLI create-user is
  still snappy. Lower (10) is too cheap on commodity GPUs; higher
  (14+) hurts legitimate users without a proportional security gain.

* No plaintext ever lives longer than the function call. The hash is
  the only artifact stored in SQLite.
"""

from __future__ import annotations

import bcrypt

# bcrypt cost factor. Pinned here + asserted by tests so a future
# bump is a conscious decision (not a silent regression).
BCRYPT_ROUNDS: int = 12


def hash_pw(plain: str) -> str:
    """Hash ``plain`` with bcrypt at the pinned cost and return a str.

    The returned value starts with ``$2b$12$`` followed by the 22-char
    base64-encoded salt and the 31-char base64-encoded digest. Two
    calls with the same plaintext return different strings (the salt
    is regenerated every call).
    """
    # bcrypt only accepts bytes. The package validates UTF-8 internally
    # for $2b$; raising here on bad input is fine for v0.1.0.
    digest: bytes = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return digest.decode("ascii")


def verify_pw(plain: str, hashed: str) -> bool:
    """Return True iff ``plain`` matches the bcrypt hash ``hashed``.

    Returns False (never raises) for a malformed hash — the caller's
    only signal is "this hash does not validate this plaintext".
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash string (truncated, non-ASCII, etc.).
        return False