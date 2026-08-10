"""Tests for app/sandbox/languages.py — argv-list command builders.

Per ADR-0002 (sandbox security model) and security-guardian, builders
MUST return argv lists (`list[str]`), never shell strings, so the caller
can hand them to `subprocess.run(argv, shell=False)` without risk of
shell injection.

Per ADR-0005, MVP supports C++ and Python only.

These tests verify SHAPE only (which argv list is returned). They do
NOT execute the compiler/interpreter — the runner (M4.T2) does that
under UID isolation + RLIMITs. If g++/python3 is missing on the host,
the shape tests are skipped (we still verify the module imports and
exposes the public functions/constants).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# `app/` is a package; the package ships with the project. The repo
# layout puts the package source at the project root, so adding the
# repo root to sys.path lets `from app.sandbox.languages import ...`
# resolve without an installed editable package.
import os
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.sandbox.languages import (  # noqa: E402  (path setup above)
    CPP_COMPILER,
    PYTHON_INTERPRETER,
    build_cpp_cmd,
    build_python_cmd,
)


# ---------------------------------------------------------------------------
# Constants exist with sensible defaults
# ---------------------------------------------------------------------------


def test_cpp_compiler_constant_is_string() -> None:
    """CPP_COMPILER must be a string (the absolute path to the compiler)."""
    assert isinstance(CPP_COMPILER, str), (
        f"CPP_COMPILER must be str, got {type(CPP_COMPILER).__name__}"
    )
    assert CPP_COMPILER, "CPP_COMPILER must not be empty"


def test_python_interpreter_constant_is_string() -> None:
    """PYTHON_INTERPRETER must be a string (the absolute path to python3)."""
    assert isinstance(PYTHON_INTERPRETER, str), (
        f"PYTHON_INTERPRETER must be str, got {type(PYTHON_INTERPRETER).__name__}"
    )
    assert PYTHON_INTERPRETER, "PYTHON_INTERPRETER must not be empty"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_binary(path: str) -> bool:
    return shutil.which(path) is not None or Path(path).is_file()


# ---------------------------------------------------------------------------
# build_cpp_cmd
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_binary(CPP_COMPILER),
    reason=f"C++ compiler not available at {CPP_COMPILER!r}; skipping shape test",
)
def test_build_cpp_cmd_returns_argv_list_with_expected_flags() -> None:
    """build_cpp_cmd must return an argv list with compiler, src, flags, output.

    Per ADR-0002 the runner pipes compiler output to a tmpfs path. The
    MVP default is `-O2 -std=c++17` and writes the binary next to the
    source with a `.bin` suffix. The caller may override `out`; the
    default must be deterministic (same source → same binary path).
    """
    src = Path("/tmp/x.cpp")
    cmd = build_cpp_cmd(src)

    assert isinstance(cmd, list), (
        f"build_cpp_cmd must return list[str], got {type(cmd).__name__}"
    )
    assert all(isinstance(a, str) for a in cmd), (
        "all argv entries must be strings"
    )
    assert len(cmd) >= 6, (
        f"cpp argv must have at least 6 entries (compiler+src+flags), got {len(cmd)}"
    )
    assert cmd[0] == CPP_COMPILER, (
        f"argv[0] must be the C++ compiler ({CPP_COMPILER!r}), got {cmd[0]!r}"
    )
    assert cmd[1] == str(src), (
        f"argv[1] must be the source path string ({src!r}), got {cmd[1]!r}"
    )
    assert "-O2" in cmd, "cpp argv must include -O2 (per ADR-0005 default)"
    assert "-std=c++17" in cmd, (
        "cpp argv must pin -std=c++17 (per ADR-0005 default)"
    )

    # Find the -o flag and verify the binary path comes right after it.
    out_idx = cmd.index("-o")
    assert out_idx + 1 < len(cmd), "argv must have a path after -o"
    out_path = Path(cmd[out_idx + 1])
    assert out_path.suffix == ".bin", (
        f"binary path must end in .bin, got {out_path!r}"
    )


def test_build_cpp_cmd_default_output_path_is_deterministic() -> None:
    """Two calls with the same source must yield the same binary path."""
    src = Path("/tmp/deterministic.cpp")
    cmd1 = build_cpp_cmd(src)
    cmd2 = build_cpp_cmd(src)
    assert cmd1 == cmd2, "build_cpp_cmd must be deterministic for the same src"


def test_build_cpp_cmd_uses_provided_out_path() -> None:
    """Caller-supplied `out` must override the default binary path."""
    src = Path("/tmp/x.cpp")
    out = Path("/tmp/x.bin")
    cmd = build_cpp_cmd(src, out=out)
    out_idx = cmd.index("-o")
    assert cmd[out_idx + 1] == str(out), (
        f"caller-supplied out path must appear after -o, got {cmd[out_idx + 1]!r}"
    )


def test_build_cpp_cmd_accepts_str_source() -> None:
    """build_cpp_cmd must accept src as either Path or str."""
    cmd = build_cpp_cmd("/tmp/string_src.cpp")
    assert isinstance(cmd, list)
    assert "/tmp/string_src.cpp" in cmd, "string source must be included in argv"


def test_build_cpp_cmd_never_returns_string() -> None:
    """Defense-in-depth: result must NEVER be a str (caller would shell-out)."""
    src = Path("/tmp/x.cpp")
    result = build_cpp_cmd(src)
    assert not isinstance(result, str), (
        "build_cpp_cmd must NEVER return a shell string — "
        "argv list only (per security-guardian)"
    )


# ---------------------------------------------------------------------------
# build_python_cmd
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_binary(PYTHON_INTERPRETER),
    reason=f"Python interpreter not available at {PYTHON_INTERPRETER!r}; "
    "skipping shape test",
)
def test_build_python_cmd_returns_argv_list() -> None:
    """build_python_cmd must return [interpreter, source] as a list[str]."""
    src = Path("/tmp/x.py")
    cmd = build_python_cmd(src)

    assert isinstance(cmd, list), (
        f"build_python_cmd must return list[str], got {type(cmd).__name__}"
    )
    assert all(isinstance(a, str) for a in cmd), (
        "all argv entries must be strings"
    )
    assert cmd[0] == PYTHON_INTERPRETER, (
        f"argv[0] must be the interpreter ({PYTHON_INTERPRETER!r}), got {cmd[0]!r}"
    )
    assert cmd[1] == str(src), (
        f"argv[1] must be the source path ({src!r}), got {cmd[1]!r}"
    )
    assert len(cmd) == 2, (
        f"python argv must be exactly [interpreter, src], got {cmd!r}"
    )


def test_build_python_cmd_accepts_str_source() -> None:
    """build_python_cmd must accept src as either Path or str."""
    cmd = build_python_cmd("/tmp/string_src.py")
    assert isinstance(cmd, list)
    assert "/tmp/string_src.py" in cmd


def test_build_python_cmd_never_returns_string() -> None:
    """Defense-in-depth: result must NEVER be a str (caller would shell-out)."""
    src = Path("/tmp/x.py")
    result = build_python_cmd(src)
    assert not isinstance(result, str), (
        "build_python_cmd must NEVER return a shell string — "
        "argv list only (per security-guardian)"
    )
