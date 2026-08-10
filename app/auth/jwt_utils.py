"""JWT encode/decode for the ``cg_session`` cookie (M1.T2).

Why HS256:
we control both ends (encode and decode live in this repo), there's
no need to involve an external KMS, and HS256 is what PyJWT defaults
to. Algorithm is pinned in every call so a token forged with a
different algorithm ("alg":"none" or RS256) is rejected.

Why fail-fast on a missing secret:
a misconfigured deploy that signs tokens with ``""`` or a default
is indistinguishable from a working deploy — until somebody forges
a token. Crashing at import time makes the misconfig loud.
"""

from __future__ import annotations

import os
import time
from typing import Final

import jwt as pyjwt

# ---------------------------------------------------------------------------
# Secret — fail-fast at import time
# ---------------------------------------------------------------------------

_SECRET_ENV_VAR: Final[str] = "CODE_GYM_JWT_SECRET"


_SECRET: str = os.environ.get(_SECRET_ENV_VAR) or ""
if not _SECRET:
    raise RuntimeError(
        f"{_SECRET_ENV_VAR} env var is required for JWT signing. "
        "Set it to a long random string before starting the app."
    )

# Tests reload this module after clearing the env var to exercise the fail-fast path.
SECRET: Final[str] = _SECRET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHM: Final[str] = "HS256"
DEFAULT_EXPIRES_IN_SECONDS: Final[int] = 30 * 24 * 60 * 60  # 30 days

# Claim name carrying the user id. ``sub`` is the standard JWT
# subject claim — using it keeps third-party JWT inspectors happy.
SUBJECT_CLAIM: Final[str] = "sub"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode_jwt(user_id: int, *, expires_in_seconds: int = DEFAULT_EXPIRES_IN_SECONDS) -> str:
    """Return a signed HS256 JWT whose ``sub`` claim is ``user_id``.

    The token is valid for ``expires_in_seconds`` from ``now`` (default
    30 days). The expiry lives in the standard ``exp`` claim so any
    PyJWT-based decoder can read it.
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        # bool is a subclass of int; reject it so encode_jwt(True) doesn't encode 1.
        raise TypeError(f"user_id must be int, got {type(user_id).__name__}")

    now = int(time.time())
    payload = {
        SUBJECT_CLAIM: str(user_id),
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    return pyjwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_jwt(token: str) -> int | None:
    """Return the ``sub`` (user id) from a JWT, or ``None``.

    Returns ``None`` (never raises) for any of:

    * malformed token (bad base64, missing segments)
    * signature mismatch (wrong secret, tampered payload)
    * expired (``exp`` claim in the past)
    * missing or non-numeric ``sub`` claim
    * empty / non-string input

    The middleware calls this on every request, so a raise here
    would 500 every unauthenticated visitor.
    """
    if not isinstance(token, str) or not token:
        return None

    try:
        payload = pyjwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except pyjwt.InvalidTokenError:
        # Covers ExpiredSignatureError, InvalidSignatureError,
        # DecodeError, InvalidAlgorithmError, etc. — all "this token
        # is not acceptable" signals.
        return None

    sub = payload.get(SUBJECT_CLAIM)
    if sub is None:
        return None

    # JWT ``sub`` is a string by RFC 7519; we encode str(int) and
    # accept both str(int) and the bare int for forward-compat with
    # libraries that might serialise it differently.
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


__all__ = (
    "ALGORITHM",
    "DEFAULT_EXPIRES_IN_SECONDS",
    "SECRET",
    "SUBJECT_CLAIM",
    "decode_jwt",
    "encode_jwt",
)