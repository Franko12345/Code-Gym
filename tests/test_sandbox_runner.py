"""Tests for app/sandbox/runner.py — UID-isolated subprocess runner.

Per ADR-0002 (sandbox security model) and security-guardian:

- ``run()`` MUST execute user code under the unprivileged ``sandbox``
  UID (uid 32768), with RLIMITs (CPU/AS/NPROC/FSIZE), inside a tmpfs
  working dir (``/dev/shm``), with a hard ``subprocess`` timeout.
- ``run()`` MUST use argv lists from ``app.sandbox.languages`` —
  **never** ``shell=True``, **never** an ``f"..."`` shell string.
- ``run()`` MUST return a ``Verdict`` with ``(verdict, runtime_ms,
  stderr)``. The runner never crashes — adversarial input produces
  ``verdict='RE'``.

Per ADR-0005, MVP supports C++ and Python only.

These tests exercise the 5 scenarios required by the spec
(``M4.T2``) plus the shape contract (return type). When the sandbox
user is not provisioned on the host (``id sandbox`` fails), the
tests skip with a clear reason — we still verify the public API
shape and the "missing sandbox user" path returns ``RE`` instead of
crashing.
"""

from __future__ import annotations

import os
import pwd
import shutil
import sys
from pathlib import Path

import pytest

# `app/` is a package; the package ships with the project. The repo
# layout puts the package source at the project root, so adding the
# repo root to sys.path lets `from app.sandbox.runner import ...`
# resolve without an installed editable package.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.sandbox.runner import Verdict, run  # noqa: E402  (path setup above)
from app.sandbox.languages import (  # noqa: E402
    CPP_COMPILER,
    PYTHON_INTERPRETER,
)


# ---------------------------------------------------------------------------
# Helpers / capability gates
# ---------------------------------------------------------------------------


def _has_binary(path: str) -> bool:
    return shutil.which(path) is not None or Path(path).is_file()


def _sandbox_user_available() -> bool:
    try:
        pwd.getpwnam("sandbox")
    except KeyError:
        return False
    return True


# A single skipif that all runner scenarios share. We need:
#   1. Python interpreter (always on Linux test hosts)
#   2. tmpfs at /dev/shm (always on Linux)
#   3. The `sandbox` system user (provisioned in deploy; not on CI
#      without the setup-sandbox-user.sh script run)
#
# When the sandbox user is missing, all runner tests skip — but the
# shape-of-API test (test_verdict_is_namedtuple_or_dataclass) still
# runs because it only inspects the imported symbol.
PY_REQUIRED = _has_binary(PYTHON_INTERPRETER)
SANDBOX_USER_OK = _sandbox_user_available()
TMPFS_OK = Path("/dev/shm").is_dir()

_runner_skip = pytest.mark.skipif(
    not (PY_REQUIRED and SANDBOX_USER_OK and TMPFS_OK),
    reason=(
        f"runner requires python3={PY_REQUIRED}, sandbox user="
        f"{SANDBOX_USER_OK}, /dev/shm={TMPFS_OK}; skipping"
    ),
)


# ---------------------------------------------------------------------------
# Shape: Verdict type
# ---------------------------------------------------------------------------


def test_verdict_is_namedtuple_or_dataclass() -> None:
    """Verdict must be a NamedTuple or dataclass with the contract fields.

    Per the spec the runner returns ``(verdict, runtime_ms, stderr)``.
    Either a typing.NamedTuple or a @dataclass satisfies this — we
    accept either, but the three field names must exist and be
    accessible as attributes (so callers can use ``v.verdict``).
    """
    # Construct from kwargs (works for both NamedTuple and dataclass).
    v = Verdict(verdict="AC", runtime_ms=12, stderr="")
    assert v.verdict == "AC"
    assert v.runtime_ms == 12
    assert v.stderr == ""


def test_run_returns_verdict_instance() -> None:
    """``run()`` must return a Verdict (NamedTuple or dataclass), not a
    bare tuple/dict — callers rely on attribute access.

    Skip when the runner can't actually execute (no sandbox user, no
    python). The shape-of-API contract is partially covered by
    test_verdict_is_namedtuple_or_dataclass above.
    """
    if not (PY_REQUIRED and SANDBOX_USER_OK and TMPFS_OK):
        pytest.skip(
            "runner shape cannot be verified end-to-end without "
            "sandbox user / python / /dev/shm"
        )
    v = run("print(1)", "python", test_case_stdin="", test_case_expected="1\n")
    assert isinstance(v, Verdict), (
        f"run() must return Verdict, got {type(v).__name__}"
    )
    assert v.verdict in {"AC", "WA", "TLE", "RE", "CE"}, (
        f"verdict must be one of AC/WA/TLE/RE/CE, got {v.verdict!r}"
    )
    assert isinstance(v.runtime_ms, int)
    assert isinstance(v.stderr, str)


# ---------------------------------------------------------------------------
# Missing-sandbox-user path (no skip — the runner must not crash)
# ---------------------------------------------------------------------------


def test_run_returns_re_when_sandbox_user_missing() -> None:
    """When ``pwd.getpwnam('sandbox')`` raises KeyError, ``run()`` must
    return ``Verdict('RE', 0, <explanation>)`` — never raise.

    We simulate the missing user by monkeypatching ``pwd.getpwnam``
    inside the runner module. This is the path tests rely on when
    the real sandbox user is not provisioned locally.
    """
    # Always run this test (it's the no-provisioning safety net).
    # Patch the runner's reference to pwd.getpwnam, not the global.
    import app.sandbox.runner as runner_mod

    real_getpwnam = runner_mod.pwd.getpwnam

    def _raise(_name: str):
        raise KeyError("sandbox")

    runner_mod.pwd.getpwnam = _raise  # type: ignore[assignment]
    try:
        v = run("print(1)", "python", test_case_stdin="", test_case_expected="1\n")
    finally:
        runner_mod.pwd.getpwnam = real_getpwnam  # type: ignore[assignment]

    assert isinstance(v, Verdict)
    assert v.verdict == "RE"
    assert v.runtime_ms == 0
    assert "sandbox" in v.stderr.lower()


# ---------------------------------------------------------------------------
# 5 ADR-0002 scenarios (all skip without sandbox user)
# ---------------------------------------------------------------------------


@_runner_skip
def test_scenario_ac_python_prints_expected() -> None:
    """AC: code prints expected output byte-for-byte → verdict='AC'."""
    code = "print(42)"
    v = run(code, "python", test_case_stdin="", test_case_expected="42\n")
    assert v.verdict == "AC", (
        f"expected AC, got {v.verdict} (stderr={v.stderr!r})"
    )
    # Runtime must be reported and sane (< 3s for a trivial print).
    assert 0 <= v.runtime_ms < 3000


@_runner_skip
def test_scenario_wa_python_prints_wrong_output() -> None:
    """WA: code prints wrong output → verdict='WA'."""
    code = "print(43)"  # expected says 42
    v = run(code, "python", test_case_stdin="", test_case_expected="42\n")
    assert v.verdict == "WA", (
        f"expected WA, got {v.verdict} (stderr={v.stderr!r})"
    )


@_runner_skip
def test_scenario_tle_infinite_loop_killed_under_3s() -> None:
    """TLE: ``while True: pass`` → verdict='TLE' within 3s hard kill."""
    code = "while True: pass"
    v = run(code, "python", test_case_stdin="", test_case_expected="")
    assert v.verdict == "TLE", (
        f"expected TLE, got {v.verdict} (stderr={v.stderr!r})"
    )
    # Hard kill at timeout=2.5s + small grace for reaping.
    assert v.runtime_ms < 3000, (
        f"TLE must kill in <3s, got {v.runtime_ms}ms"
    )


@_runner_skip
def test_scenario_oom_large_alloc_re_under_3s() -> None:
    """OOM: huge allocation → verdict='RE' within 3s (RLIMIT_AS)."""
    code = "x = [0] * (10**8)"  # ~800MB of pointers — past 256MB cap
    v = run(code, "python", test_case_stdin="", test_case_expected="")
    assert v.verdict == "RE", (
        f"expected RE (OOM-killed), got {v.verdict} (stderr={v.stderr!r})"
    )
    assert v.runtime_ms < 3000, (
        f"OOM must be caught in <3s, got {v.runtime_ms}ms"
    )


@_runner_skip
def test_scenario_escape_attempt_does_not_write_outside_sandbox() -> None:
    """Escape: ``os.system('echo pwned > /tmp/escape_test')`` must NOT
    create /tmp/escape_test on the host.

    Per ADR-0002 the sandbox UID has no write access outside the
    tmpfs working dir. Either ``os.system`` raises (RE), or it
    silently fails to write — the file must not exist after the run.
    """
    escape_path = "/tmp/escape_test"
    # Defensive cleanup: remove a stale marker from a previous failed run.
    if os.path.exists(escape_path):
        os.remove(escape_path)

    code = (
        "import os\n"
        "os.system('echo pwned > /tmp/escape_test')\n"
    )
    v = run(code, "python", test_case_stdin="", test_case_expected="")

    # Either verdict is acceptable (RE if shell call fails; AC if it
    # somehow exits 0), but the marker MUST NOT exist on the host.
    assert v.verdict in {"RE", "AC", "WA", "TLE"}, (
        f"verdict must be a known value, got {v.verdict!r}"
    )
    assert not os.path.exists(escape_path), (
        f"escape attempt succeeded — {escape_path} was created on host "
        f"(verdict={v.verdict}, stderr={v.stderr!r})"
    )
