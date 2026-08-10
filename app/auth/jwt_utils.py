"""JWT encode/decode for the ``cg_session`` cookie (M1.T2).

Why JWT (not server-side sessions):
the app is single-instance, no Redis, and the auth state we need
to carry is exactly one integer (the user id). A signed JWT gives
us stateless verification at zero ops cost — the cookie itself
*is* the session.

Why HS256:
we control both ends (encode and decode live in this repo), there's
no need to involve an external KMS, and HS256 is what PyJWT defaults
to. Algorithm is pinned in every call so a token forged with a
different algorithm ("alg":"none" or RS256) is rejected.

Why fail-fast on a missing secret:
a misconfigured deploy that signs tokens with ``""`` or a default
is indistinguishable from a working deploy — until somebody forges
a token. Crashing at import time makes the misconfig loud.

Why 30-day expiry:
a balance between UX (users stay logged in across visits) and
blast radius (a stolen cookie is good for at most 30 days). The
MVP has no refresh-token flow; longer sessions land in v0.2 if
needed.
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


def _load_secret() -> str:
    """Return the JWT secret from the env, or raise.

    A missing or empty secret is a deployment bug, not a runtime
    condition: refuse to start so the bug can't ship as a deploy
    that silently accepts forged tokens.
    """
    value = os.environ.get(_SECRET_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{_SECRET_ENV_VAR} env var is required for JWT signing. "
            "Set it to a long random string before starting the app."
        )
    return value


# Loaded at import. Tests reload this module after clearing the env
# var to exercise the fail-fast path.
SECRET: Final[str] = _load_secret()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHM: Final[str] = "HS256"
# Cookie validity window. 30 days, matching the spec. Configurable
# per call via ``expires_in_seconds`` (the seam the expired-token
# test uses to avoid sleeping through real time).
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

    Raises:
        TypeError: if ``user_id`` is not an ``int`` — the middleware
            looks up by id and a non-int would always miss.
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        # ``bool`` is a subclass of ``int`` in Python; reject it so
        # ``encode_jwt(True)`` doesn't silently encode ``1``.
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