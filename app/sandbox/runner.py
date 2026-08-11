"""Sandbox runner — UID-isolated subprocess executor with RLIMITs.

Per ADR-0002 (sandbox security model) and security-guardian:

- User code runs as the unprivileged system user ``sandbox``
  (uid 32768, provisioned by ``deploy/setup-sandbox-user.sh``).
  The runner looks up the UID via ``pwd.getpwnam("sandbox")``; if
  the user is missing on the host it returns
  ``Verdict('RE', 0, '...sandbox user not provisioned...')`` instead
  of crashing. Tests rely on this to skip gracefully on
  developer/CI machines without the user provisioned.
- Every subprocess is wrapped in ``resource.setrlimit`` for
  CPU/AS/NPROC/FSIZE and a hard ``subprocess.run(timeout=2.5)``
  kill. The working dir is a ``tempfile.TemporaryDirectory`` under
  ``/dev/shm`` (tmpfs).
- All command construction uses argv lists from
  ``app.sandbox.languages`` (``build_cpp_cmd`` / ``build_python_cmd``).
  ``shell=False`` is hard-coded; ``subprocess.run`` is always called
  with a ``list[str]``.

Per ADR-0005, MVP supports C++ and Python only. Adding a new language
is one ``elif`` branch here + one builder in ``languages.py``.

Public API:
    Verdict(verdict, runtime_ms, stderr) — NamedTuple.
    run(code, language, test_case_stdin, test_case_expected) -> Verdict

The runner never raises for adversarial input — it always returns
a Verdict. The only exception is programmer errors (e.g. an
unrecognized language), which raise ``ValueError`` because they
indicate a bug in the caller, not a runtime condition we should
swallow.
"""

from __future__ import annotations

import os
import pwd
import resource
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import NamedTuple

from app.sandbox.languages import (
    build_cpp_cmd,
    build_python_cmd,
)


# Hard timeout per ADR-0002. Slightly above RLIMIT_CPU=2s so the OS
# SIGXCPU has time to fire before the Python-level TimeoutExpired
# reaps the process. Tests verify wall-clock < 3s.
_RUN_TIMEOUT_S: float = 2.5

# RLIMIT values per ADR-0002 (sandwiched between timeout layers).
_RLIMIT_CPU_S: int = 2
_RLIMIT_AS_BYTES: int = 256 * 1024 * 1024  # 256 MB virtual memory
_RLIMIT_NPROC: int = 8  # allow gcc to spawn cc1plus etc.
_RLIMIT_FSIZE_BYTES: int = 1 * 1024 * 1024  # 1 MB max file write

# tmpfs-backed working dir per ADR-0002 (RAM, no disk fill).
_TMPFS_DIR: str = "/dev/shm"

# Filenames inside the tmpfs working dir.
_CPP_SRC_NAME = "submission.cpp"
_CPP_BIN_NAME = "submission.bin"
_PY_SRC_NAME = "submission.py"


class Verdict(NamedTuple):
    """Outcome of a single test-case run.

    Fields:
        verdict: one of ``'AC'``, ``'WA'``, ``'TLE'``, ``'RE'``,
            ``'CE'``. ``'CE'`` is compile error (C++ only).
        runtime_ms: wall-clock execution time in milliseconds.
            ``0`` when the runner short-circuits (missing sandbox
            user, compile failure).
        stderr: best-effort stderr excerpt for debugging. May be
            truncated for long output.
    """

    verdict: str
    runtime_ms: int
    stderr: str


def _sandbox_pw_entry() -> pwd.struct_passwd:
    """Resolve the ``sandbox`` system user. Raises ``KeyError`` if
    the user is not provisioned.

    The caller (``run``) wraps this in try/except to return a safe
    ``Verdict('RE', ...)`` instead of crashing.
    """
    return pwd.getpwnam("sandbox")


def _build_preexec(pw_entry: pwd.struct_passwd):
    """Return a ``preexec_fn`` that drops privileges and sets RLIMITs.

    This runs in the **child** process after ``fork()`` but before
    ``exec()``. Order matters:
      1. ``setgroups([pw_gid])`` first — must precede ``setuid``
         because non-root can only setgroups to its own gid.
      2. ``setuid(pw_uid)`` — drop to the sandbox UID.
      3. ``setrlimit`` — now we're non-root, the rlimits stick for
         the duration of the exec'd program.
    """

    def _preexec() -> None:
        os.setgroups([pw_entry.pw_gid])
        os.setuid(pw_entry.pw_uid)
        resource.setrlimit(resource.RLIMIT_CPU, (_RLIMIT_CPU_S, _RLIMIT_CPU_S))
        resource.setrlimit(
            resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES)
        )
        resource.setrlimit(resource.RLIMIT_NPROC, (_RLIMIT_NPROC, _RLIMIT_NPROC))
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (_RLIMIT_FSIZE_BYTES, _RLIMIT_FSIZE_BYTES)
        )

    return _preexec


def _measure_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _stderr_excerpt(raw: bytes, limit: int = 2000) -> str:
    """Decode stderr to text and truncate. Keeps the verdict tuple
    small in logs."""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = repr(raw)
    if len(text) > limit:
        text = text[:limit] + "...<truncated>"
    return text


def run(
    code: str,
    language: str,
    test_case_stdin: str,
    test_case_expected: str,
) -> Verdict:
    """Run ``code`` (in ``language``) against a single test case.

    Args:
        code: source code submitted by the user.
        language: ``'python'`` or ``'cpp'`` (per ADR-0005).
        test_case_stdin: bytes/str piped to the program's stdin.
        test_case_expected: bytes/str compared to the program's
            stdout byte-for-byte. Newlines matter — we compare
            exactly without normalization (matches OBI judges).

    Returns:
        ``Verdict(verdict, runtime_ms, stderr)``. ``verdict`` is one
        of ``'AC'``/``'WA'``/``'TLE'``/``'RE'``/``'CE'``.

    Raises:
        ValueError: ``language`` is not ``'python'`` or ``'cpp'``.
            (Programmer error, not a runtime condition.)
    """
    lang = language.lower().strip()

    # Short-circuit on missing sandbox user — never crash on a
    # developer's box without the user provisioned.
    try:
        pw_entry = _sandbox_pw_entry()
    except KeyError:
        return Verdict(
            verdict="RE",
            runtime_ms=0,
            stderr="sandbox user not provisioned (run deploy/setup-sandbox-user.sh)",
        )

    if lang == "python":
        return _run_python(code, test_case_stdin, test_case_expected, pw_entry)
    if lang == "cpp":
        return _run_cpp(code, test_case_stdin, test_case_expected, pw_entry)
    raise ValueError(
        f"unsupported language {language!r} (per ADR-0005 MVP supports 'python' and 'cpp')"
    )


def _run_python(
    code: str,
    stdin_str: str,
    expected_str: str,
    pw_entry: pwd.struct_passwd,
) -> Verdict:
    """Execute Python source under sandbox UID + RLIMITs."""
    with tempfile.TemporaryDirectory(dir=_TMPFS_DIR) as tmp:
        work = Path(tmp)
        # tempfile default perms are 0o700 (owner-only). Sandbox user
        # (uid 32768) needs o+rx to enter the dir and o+r to read the
        # source file.
        work.chmod(0o755)
        src_path = work / _PY_SRC_NAME
        src_path.write_text(code, encoding="utf-8")
        src_path.chmod(0o644)

        argv = build_python_cmd(src_path)
        stdin_bytes = stdin_str.encode("utf-8")
        expected_bytes = expected_str.encode("utf-8")

        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 — argv list, shell=False
                argv,
                input=stdin_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_RUN_TIMEOUT_S,
                preexec_fn=_build_preexec(pw_entry),
                check=False,
                cwd=str(work),
            )
        except subprocess.TimeoutExpired:
            return Verdict(
                verdict="TLE",
                runtime_ms=_measure_ms(start),
                stderr=f"hard timeout after {_RUN_TIMEOUT_S}s",
            )
        except OSError as exc:
            # PermissionError (a subclass) is covered here too — e.g.
            # setuid/RLIMIT raising in preexec_fn.
            return Verdict(
                verdict="RE",
                runtime_ms=_measure_ms(start),
                stderr=f"{type(exc).__name__}: {exc}",
            )

        runtime_ms = _measure_ms(start)
        stderr_text = _stderr_excerpt(proc.stderr)

        if proc.returncode != 0:
            return Verdict(verdict="RE", runtime_ms=runtime_ms, stderr=stderr_text)
        if proc.stdout == expected_bytes:
            return Verdict(verdict="AC", runtime_ms=runtime_ms, stderr=stderr_text)
        return Verdict(verdict="WA", runtime_ms=runtime_ms, stderr=stderr_text)


def _run_cpp(
    code: str,
    stdin_str: str,
    expected_str: str,
    pw_entry: pwd.struct_passwd,
) -> Verdict:
    """Compile C++ source (sandboxed) then run the binary (sandboxed).

    Returns ``Verdict('CE', ...)`` if compile fails. After compile
    success, the binary runs under the same UID/RLIMIT envelope as
    Python.
    """
    with tempfile.TemporaryDirectory(dir=_TMPFS_DIR) as tmp:
        work = Path(tmp)
        # tempfile default perms are 0o700 (owner-only). Sandbox user
        # (uid 32768) needs o+rx to enter and read. For C++ the
        # linker also writes the binary here, so we need o+w (0o777
        # with sticky bit on /tmp keeps it safe).
        work.chmod(0o777)
        src_path = work / _CPP_SRC_NAME
        bin_path = work / _CPP_BIN_NAME
        src_path.write_text(code, encoding="utf-8")
        src_path.chmod(0o644)
        # Pre-create bin file as root (we own it now) so the linker
        # just opens+truncates, instead of creating. Without this
        # the linker's open(O_CREAT) fails because the dir perms
        # change for sandbox isn't inherited at create-time.
        bin_path.touch()
        bin_path.chmod(0o666)

        compile_argv = build_cpp_cmd(src_path, out=bin_path)

        start = time.monotonic()
        try:
            compile_proc = subprocess.run(  # noqa: S603 — argv list, shell=False
                compile_argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_RUN_TIMEOUT_S,
                preexec_fn=_build_preexec(pw_entry),
                check=False,
                cwd=str(work),
            )
        except subprocess.TimeoutExpired:
            return Verdict(
                verdict="CE",
                runtime_ms=_measure_ms(start),
                stderr=f"compile timeout after {_RUN_TIMEOUT_S}s",
            )
        except OSError as exc:
            return Verdict(
                verdict="CE",
                runtime_ms=_measure_ms(start),
                stderr=f"OSError during compile: {exc}",
            )

        if compile_proc.returncode != 0:
            return Verdict(
                verdict="CE",
                runtime_ms=_measure_ms(start),
                stderr=_stderr_excerpt(compile_proc.stderr),
            )
        # Compiled binary is owned by the sandbox uid (compile ran
        # under preexec_fn). It needs o+x for the sandbox uid to
        # execute it. chmod here.
        bin_path.chmod(0o755)

        # Compile OK — run the binary.
        stdin_bytes = stdin_str.encode("utf-8")
        expected_bytes = expected_str.encode("utf-8")
        run_start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 — argv list, shell=False
                [str(bin_path)],
                input=stdin_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_RUN_TIMEOUT_S,
                preexec_fn=_build_preexec(pw_entry),
                check=False,
                cwd=str(work),
            )
        except subprocess.TimeoutExpired:
            return Verdict(
                verdict="TLE",
                runtime_ms=_measure_ms(run_start),
                stderr=f"hard timeout after {_RUN_TIMEOUT_S}s",
            )
        except OSError as exc:
            return Verdict(
                verdict="RE",
                runtime_ms=_measure_ms(run_start),
                stderr=f"OSError: {exc}",
            )

        runtime_ms = _measure_ms(run_start)
        stderr_text = _stderr_excerpt(proc.stderr)

        if proc.returncode != 0:
            return Verdict(verdict="RE", runtime_ms=runtime_ms, stderr=stderr_text)
        if proc.stdout == expected_bytes:
            return Verdict(verdict="AC", runtime_ms=runtime_ms, stderr=stderr_text)
        return Verdict(verdict="WA", runtime_ms=runtime_ms, stderr=stderr_text)
