"""Tests for deploy/setup-sandbox-user.sh.

Per ADR-0002 (sandbox security model), the `sandbox` system user must be
provisioned as: --system --shell /usr/sbin/nologin --no-create-home
--uid 32768. The provision script is idempotent and lives at
deploy/setup-sandbox-user.sh.

These tests verify the SCRIPT (lint, exec bit, expected content, idempotency
when the user already exists). They do NOT invoke `useradd` — CI must never
create a system user. If the `sandbox` user already exists on the host, the
script should detect it and exit cleanly (no-op).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "deploy" / "setup-sandbox-user.sh"


# ---------------------------------------------------------------------------
# Script file shape
# ---------------------------------------------------------------------------


def test_script_exists() -> None:
    """The provision script must exist at deploy/setup-sandbox-user.sh."""
    assert SCRIPT_PATH.is_file(), f"missing script: {SCRIPT_PATH}"


def test_script_is_executable() -> None:
    """The provision script must be marked executable for `sudo ./...` to work."""
    assert SCRIPT_PATH.is_file(), f"missing script: {SCRIPT_PATH}"
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & 0o111, f"script not executable (mode={oct(mode & 0o777)})"


def test_script_has_shebang_and_strict_mode() -> None:
    """Bash script must start with #!/usr/bin/env bash and enable strict mode."""
    content = SCRIPT_PATH.read_text()
    assert content.startswith("#!/usr/bin/env bash\n") or content.startswith(
        "#!/usr/bin/env bash "
    ), "script must use #!/usr/bin/env bash shebang"
    assert re.search(r"^set\s+-euo\s+pipefail", content, re.MULTILINE), (
        "script must enable `set -euo pipefail` for safe execution"
    )


def test_script_calls_useradd_with_adr0002_flags() -> None:
    """The useradd invocation must match ADR-0002 exactly.

    Per ADR-0002 the sandbox user must be:
      --system --shell /usr/sbin/nologin --no-create-home --uid 32768 sandbox
    """
    content = SCRIPT_PATH.read_text()
    # Normalize whitespace: collapse newlines + indentation so we can match
    # a multi-line `useradd \` invocation as a single logical call.
    normalized = re.sub(r"\s+", " ", content)

    assert re.search(r"\buseradd\b", normalized), "script must call useradd"
    assert "--system" in normalized, "useradd must pass --system"
    assert "--shell" in normalized and "/usr/sbin/nologin" in normalized, (
        "useradd must set --shell /usr/sbin/nologin"
    )
    assert "--no-create-home" in normalized, (
        "useradd must pass --no-create-home"
    )
    assert "--uid" in normalized and re.search(r"--uid\s+32768", normalized), (
        "useradd must pin --uid 32768 (ADR-0002)"
    )
    assert re.search(r"\buseradd\b.*\bsandbox\b", normalized), (
        "useradd must target the `sandbox` username"
    )


# ---------------------------------------------------------------------------
# Idempotency (real shell run, but the script is no-op when sandbox exists)
# ---------------------------------------------------------------------------


def _sandbox_user_exists() -> bool:
    return shutil.which("id") is not None and subprocess.run(
        ["id", "sandbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


@pytest.mark.skipif(
    not _sandbox_user_exists(),
    reason="`sandbox` user is not provisioned on this host; skipping live run. "
    "On a host where `sudo deploy/setup-sandbox-user.sh` has been executed, "
    "this test verifies the script is a clean no-op on re-run.",
)
def test_script_is_idempotent_when_user_exists() -> None:
    """Running the script when `sandbox` already exists must be a no-op
    (exit 0, no useradd invocation)."""
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"script failed on re-run (rc={result.returncode}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "already exists" in result.stdout.lower(), (
        f"expected 'already exists' message on no-op run, got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Live user attributes (only meaningful when the user is actually provisioned)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _sandbox_user_exists(),
    reason="`sandbox` user is not provisioned on this host",
)
def test_sandbox_user_has_nologin_shell() -> None:
    """The `sandbox` user must have /usr/sbin/nologin as its login shell."""
    result = subprocess.run(
        ["getent", "passwd", "sandbox"], capture_output=True, text=True, check=True
    )
    # /etc/passwd fields: user:x:uid:gid:gecos:home:shell
    fields = result.stdout.strip().split(":")
    shell = fields[-1]
    assert shell == "/usr/sbin/nologin", (
        f"sandbox shell must be /usr/sbin/nologin, got {shell!r}"
    )


@pytest.mark.skipif(
    not _sandbox_user_exists(),
    reason="`sandbox` user is not provisioned on this host",
)
def test_sandbox_user_has_no_home_directory() -> None:
    """The `sandbox` user must have no home directory (--no-create-home)."""
    result = subprocess.run(
        ["getent", "passwd", "sandbox"], capture_output=True, text=True, check=True
    )
    fields = result.stdout.strip().split(":")
    home = fields[-2]
    assert home in ("", "/nonexistent"), (
        f"sandbox home must be empty/nonexistent, got {home!r}"
    )


@pytest.mark.skipif(
    not _sandbox_user_exists(),
    reason="`sandbox` user is not provisioned on this host",
)
def test_sandbox_user_uid_is_32768() -> None:
    """The `sandbox` user must be provisioned with the pinned UID 32768
    so the runner can hard-code it (ADR-0002)."""
    result = subprocess.run(
        ["id", "-u", "sandbox"], capture_output=True, text=True, check=True
    )
    uid = int(result.stdout.strip())
    assert uid == 32768, f"sandbox UID must be 32768, got {uid}"


@pytest.mark.skipif(
    not _sandbox_user_exists(),
    reason="`sandbox` user is not provisioned on this host",
)
def test_sandbox_user_is_system_account() -> None:
    """The `sandbox` user must be a system account (UID < 1000 on Debian/Ubuntu)."""
    result = subprocess.run(
        ["id", "-u", "sandbox"], capture_output=True, text=True, check=True
    )
    uid = int(result.stdout.strip())
    assert uid < 1000, (
        f"sandbox must be a system account (uid < 1000), got uid={uid}"
    )