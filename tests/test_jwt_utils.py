"""Tests for app.auth.jwt_utils.

Per ticket #4 (M1.T2), JWT utilities provide the cookie token format
the auth middleware consumes:

* ``encode_jwt(user_id, ...) -> str`` — returns a signed HS256 JWT
  with a 30-day expiry.
* ``decode_jwt(token) -> int | None`` — returns the user id when the
  token is valid + not expired, otherwise ``None`` (never raises).

Seam: ``encode_jwt``/``decode_jwt``. Tests poke the public functions
with representative inputs (real user ids, garbage strings,
expired tokens) and check the documented return shapes.

The secret is read from the env var ``CODE_GYM_JWT_SECRET`` at import
time and must be present — missing secrets fail fast so a misconfigured
deploy can never serve an unsigned token.
"""

from __future__ import annotations

import importlib
import os

import pytest


# ---------------------------------------------------------------------------
# Fail-fast on missing env secret
# ---------------------------------------------------------------------------


def test_missing_secret_raises_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """``app.auth.jwt_utils`` must raise at import time if the env
    secret is missing.

    We delete the env var, force a reload, and expect ``RuntimeError``
    (or a subclass) so a misconfigured deploy cannot silently accept
    forged tokens.
    """
    monkeypatch.delenv("CODE_GYM_JWT_SECRET", raising=False)

    # The module raises on import. Catch broadly because the exact
    # exception type (RuntimeError vs. a custom subclass) is an
    # implementation detail — the contract is "fail fast, not
    # silently default to ''".
    with pytest.raises(Exception):
        import app.auth.jwt_utils as mod

        importlib.reload(mod)


def test_secret_is_loaded_from_env_on_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module exposes a ``SECRET`` constant that equals the env var
    value when import is clean (already loaded by conftest-time env)."""
    from app.auth import jwt_utils

    expected = os.environ["CODE_GYM_JWT_SECRET"]
    assert jwt_utils.SECRET == expected


# ---------------------------------------------------------------------------
# encode_jwt shape
# ---------------------------------------------------------------------------


def test_encode_jwt_returns_non_empty_string() -> None:
    """``encode_jwt(user_id)`` returns a non-empty ``str``."""
    from app.auth.jwt_utils import encode_jwt

    token = encode_jwt(42)
    assert isinstance(token, str)
    assert token, "encoded JWT must be a non-empty string"


def test_encode_jwt_returns_distinct_tokens_for_distinct_ids() -> None:
    """Different user ids must produce different tokens (otherwise the
    user id isn't actually encoded)."""
    from app.auth.jwt_utils import encode_jwt

    assert encode_jwt(1) != encode_jwt(2)


# ---------------------------------------------------------------------------
# decode_jwt roundtrip
# ---------------------------------------------------------------------------


def test_decode_jwt_roundtrip_returns_user_id() -> None:
    """``decode_jwt(encode_jwt(42))`` must return ``42``."""
    from app.auth.jwt_utils import decode_jwt, encode_jwt

    assert decode_jwt(encode_jwt(42)) == 42


def test_decode_jwt_roundtrip_for_various_ids() -> None:
    """Roundtrip must work for any positive int (the data type we
    store in ``users.id``)."""
    from app.auth.jwt_utils import decode_jwt, encode_jwt

    for uid in (1, 7, 100, 99_999):
        assert decode_jwt(encode_jwt(uid)) == uid


# ---------------------------------------------------------------------------
# decode_jwt error handling — never raises, always returns None on bad input
# ---------------------------------------------------------------------------


def test_decode_jwt_returns_none_for_garbage_string() -> None:
    """``decode_jwt('garbage')`` returns ``None`` without raising.

    The middleware will call this on every request; a raise here
    would 500 every unauthenticated visitor.
    """
    from app.auth.jwt_utils import decode_jwt

    assert decode_jwt("garbage") is None


def test_decode_jwt_returns_none_for_empty_string() -> None:
    """``decode_jwt('')`` returns ``None`` (the cookie is set but empty)."""
    from app.auth.jwt_utils import decode_jwt

    assert decode_jwt("") is None


def test_decode_jwt_returns_none_for_token_with_wrong_signature() -> None:
    """A token signed with a different secret must be rejected (``None``)."""
    import jwt as pyjwt

    from app.auth.jwt_utils import decode_jwt

    forged = pyjwt.encode({"sub": "42"}, "wrong-secret", algorithm="HS256")
    assert decode_jwt(forged) is None


def test_decode_jwt_returns_none_for_expired_token() -> None:
    """A token whose ``exp`` claim is in the past must return ``None``.

    We force expiry by passing a negative lifetime to ``encode_jwt``
    (the implementation seam — kept stable so the test doesn't have
    to sleep through real time).
    """
    from app.auth.jwt_utils import decode_jwt, encode_jwt

    # Negative lifetime ⇒ ``exp`` is in the past on decode.
    expired = encode_jwt(42, expires_in_seconds=-1)
    assert decode_jwt(expired) is None


def test_decode_jwt_returns_none_for_missing_subject_claim() -> None:
    """A token without a ``sub`` claim (or with a non-numeric one)
    must return ``None`` — we can't map it to a user row."""
    import jwt as pyjwt

    from app.auth.jwt_utils import SECRET, decode_jwt

    no_sub = pyjwt.encode({"exp": 9_999_999_999}, SECRET, algorithm="HS256")
    assert decode_jwt(no_sub) is None

    non_numeric_sub = pyjwt.encode(
        {"sub": "not-an-int", "exp": 9_999_999_999},
        SECRET,
        algorithm="HS256",
    )
    assert decode_jwt(non_numeric_sub) is None