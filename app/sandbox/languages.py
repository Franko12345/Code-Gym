"""Sandbox language command builders — argv lists only.

Per ADR-0002 (sandbox security model) and security-guardian, every
helper in this module returns a `list[str]` argv list. **Never** a shell
string. The runner (`app/sandbox/runner.py`, M4.T2) hands the list
straight to `subprocess.run(argv, shell=False, ...)` under UID isolation
+ RLIMITs.

Per ADR-0005, MVP supports **C++ and Python only**. Adding a new
language is a one-function entry — but we don't add it yet (YAGNI).

Why a list, not a string:
  - `subprocess.run("g++ x.cpp -o x.bin", shell=True)` would let a
    filename containing `; rm -rf /` execute that suffix. Argv lists
    make every entry a single literal argument with no shell parsing.
  - Even when *we* trust the source path, downstream callers might
    concatenate user input into it; the list shape guarantees safety.
"""

from __future__ import annotations

from pathlib import Path

# Absolute paths so PATH lookup is skipped at exec time. The runner
# drops into UID `sandbox` whose PATH may differ from the FastAPI
# process; pinning the path is one less moving part.
CPP_COMPILER: str = "/usr/bin/g++"
PYTHON_INTERPRETER: str = "/usr/bin/python3"

# C++ compile flags for the MVP. `-O2` matches OBI expectations;
# `-std=c++17` is the de-facto community standard and the floor of
# CodeMirror's C++ mode highlight coverage.
_CPP_OPT_FLAG: str = "-O2"
_CPP_STD_FLAG: str = "-std=c++17"

# Default output extension for compiled binaries. The runner puts the
# binary in the same dir as the source (within the tmpfs working dir).
_CPP_BIN_SUFFIX: str = ".bin"


def build_cpp_cmd(
    src: Path | str,
    out: Path | str | None = None,
) -> list[str]:
    """Build the argv list to compile a C++ source file.

    Args:
        src: Path to the C++ source (e.g. `/tmp/submission.cpp`).
        out: Optional output binary path. Defaults to `src.with_suffix(".bin")`.

    Returns:
        argv list suitable for `subprocess.run(argv, shell=False, ...)`.
        **Never** a string.

    Example:
        >>> build_cpp_cmd("/tmp/x.cpp")
        ['/usr/bin/g++', '/tmp/x.cpp', '-O2', '-std=c++17', '-o', '/tmp/x.bin']
    """
    src_path = Path(src)
    out_path = Path(out) if out is not None else src_path.with_suffix(_CPP_BIN_SUFFIX)
    return [
        CPP_COMPILER,
        str(src_path),
        _CPP_OPT_FLAG,
        _CPP_STD_FLAG,
        "-o",
        str(out_path),
    ]


def build_python_cmd(src: Path | str) -> list[str]:
    """Build the argv list to run a Python source file directly.

    Args:
        src: Path to the Python source (e.g. `/tmp/submission.py`).

    Returns:
        argv list suitable for `subprocess.run(argv, shell=False, ...)`.
        **Never** a string.

    Example:
        >>> build_python_cmd("/tmp/x.py")
        ['/usr/bin/python3', '/tmp/x.py']
    """
    return [PYTHON_INTERPRETER, str(Path(src))]
