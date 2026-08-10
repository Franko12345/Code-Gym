"""Tests for app.auth.passwords.

Per ticket #3 (M1.T1), password hashing is a pure helper: it accepts
a plaintext string and returns a bcrypt hash, and verifies a plaintext
against a stored hash.

Seam: `hash_pw(plain) -> str` and `verify_pw(plain, hashed) -> bool`.
The bcrypt cost is pinned at 12 (OWASP 2024 recommendation for bcrypt;
matches ADR-0003's "bcrypt + JWT" requirement). These functions must
work as pure utilities — no DB, no request context — so they can be
reused by the CLI create-user command and the future login route.
"""

from __future__ import annotations

import pytest

from app.auth.passwords import BCRYPT_ROUNDS, hash_pw, verify_pw


# ---------------------------------------------------------------------------
# Module constants — pin the design choices
# ---------------------------------------------------------------------------


def test_bcrypt_rounds_is_12() -> None:
    """OWASP 2024 password-storage cheat sheet recommends bcrypt cost 12.

    Cost 12 ≈ 250ms on a modern CPU — slow enough to deter brute-force,
    fast enough that a CLI create-user is still snappy. Lower (10) is
    too cheap on commodity GPUs; higher (14+) hurts legitimate users.
    """
    assert BCRYPT_ROUNDS == 12


# ---------------------------------------------------------------------------
# hash_pw shape
# ---------------------------------------------------------------------------


def test_hash_pw_returns_string() -> None:
    """hash_pw must return a `str`, not bytes — the rest of the codebase
    stores the hash as TEXT in SQLite."""
    result = hash_pw("foo")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# verify_pw roundtrip
# ---------------------------------------------------------------------------


def test_verify_pw_returns_true_for_correct_password() -> None:
    """verify_pw('foo', hash_pw('foo')) must be True (acceptance criterion)."""
    h = hash_pw("foo")
    assert verify_pw("foo", h) is True


def test_verify_pw_returns_false_for_wrong_password() -> None:
    """verify_pw('bar', hash_pw('foo')) must be False (acceptance criterion)."""
    h = hash_pw("foo")
    assert verify_pw("bar", h) is False


# ---------------------------------------------------------------------------
# Salt uniqueness — every hash of the same plaintext differs
# ---------------------------------------------------------------------------


def test_hash_pw_produces_unique_hashes_for_same_plaintext() -> None:
    """Two consecutive hash_pw('x') calls must yield different strings.

    bcrypt salts each hash with 16 random bytes; if two calls return
    the same string, the salt is broken (reused) and precomputed
    rainbow tables become viable.
    """
    a = hash_pw("x")
    b = hash_pw("x")
    assert a != b, (
        "salt must differ between calls; "
        "got identical hashes which would enable rainbow-table attacks"
    )


# ---------------------------------------------------------------------------
# verify_pw accepts only the matching hash
# ---------------------------------------------------------------------------


def test_verify_pw_rejects_mangled_hash() -> None:
    """Tampering with the hash (one byte flipped) must produce False, not crash."""
    h = hash_pw("foo")
    # Flip a character in the middle of the hash (avoid the prefix
    # which bcrypt can validate cheaply).
    mangled = h[:30] + ("A" if h[30] != "A" else "B") + h[31:]
    assert verify_pw("foo", mangled) is False


# ---------------------------------------------------------------------------
# Unicode + edge cases — bcrypt must encode to UTF-8 internally
# ---------------------------------------------------------------------------


def test_hash_pw_handles_unicode_plaintext() -> None:
    """Non-ASCII plaintext must roundtrip — bcrypt only accepts bytes."""
    h = hash_pw("senhaçõéê")
    assert verify_pw("senhaçõéê", h) is True