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

from app.auth.passwords import BCRYPT_PREFIX, BCRYPT_ROUNDS, hash_pw, verify_pw


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


def test_bcrypt_prefix_is_2b() -> None:
    """The exported bcrypt prefix must be $2b$ (the modern variant).

    bcrypt has three prefix variants:
      $2a$ — original, has a minor bug for non-ASCII
      $2b$ — fixed version of $2a (the one OpenBSD ships)
      $2y$ — PHP's notation for $2b$
    We use $2b$ because the Python `bcrypt` pkg defaults to it and it's
    what the JWT login flow expects to see.
    """
    assert BCRYPT_PREFIX == "$2b$"


# ---------------------------------------------------------------------------
# hash_pw shape
# ---------------------------------------------------------------------------


def test_hash_pw_returns_string() -> None:
    """hash_pw must return a `str`, not bytes — the rest of the codebase
    stores the hash as TEXT in SQLite."""
    result = hash_pw("foo")
    assert isinstance(result, str)


def test_hash_pw_starts_with_bcrypt_2b_12_prefix() -> None:
    """hash_pw('foo') must start with the documented $2b$12$ prefix."""
    result = hash_pw("foo")
    assert result.startswith(f"{BCRYPT_PREFIX}{BCRYPT_ROUNDS}$"), (
        f"expected prefix {BCRYPT_PREFIX}{BCRYPT_ROUNDS}$, got {result[:7]!r}"
    )


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


def test_hash_pw_produces_unique_hashes_across_many_calls() -> None:
    """100 hashes of 'x' must all differ pairwise.

    Probabilistic sanity check: with a 16-byte salt the birthday-bound
    collision probability is negligible, so any duplicate is a bug.
    """
    hashes = {hash_pw("x") for _ in range(100)}
    assert len(hashes) == 100, "expected 100 unique hashes; got a duplicate"


# ---------------------------------------------------------------------------
# verify_pw accepts only the matching hash
# ---------------------------------------------------------------------------


def test_verify_pw_works_against_each_individually_generated_hash() -> None:
    """For every distinct hash of the same plaintext, verify_pw must
    return True when given the matching plaintext.

    This guards against a subtle bug where a verifier caches one hash
    and silently rejects valid others.
    """
    for _ in range(10):
        h = hash_pw("same")
        assert verify_pw("same", h) is True


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


def test_hash_pw_handles_long_plaintext() -> None:
    """Long plaintexts (well under bcrypt's 72-byte limit) must roundtrip.

    bcrypt silently truncates input at 72 bytes; this is a known
    limitation we accept for v0.1.0. The test stays well under that.
    """
    long_plain = "a" * 70
    h = hash_pw(long_plain)
    assert verify_pw(long_plain, h) is True