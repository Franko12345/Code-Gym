# ADR-0002 — Sandbox security model

- **Status:** accepted
- **Date:** 2026-08-09

## Context

User-submitted code (C++/Python) runs on the same LXC that hosts the
FastAPI app and the developer's SSH session. A naive implementation
(`subprocess.run(cmd, timeout=2)`) exposes the host to:

- **OOM kill** by infinite-allocation code → entire LXC OOMs.
- **Fork bomb** → RLIMIT_NPROC stops it, but only after CPU spike.
- **Disk fill** → `> /tmp/big` → fills `/var` → systemd unhappy.
- **Compiler escape** → RLIMIT_AS is per-process; a clever exploit
  could pivot.
- **Resource starvation** → user code competes with FastAPI event loop.

A reviewer on the v0.1.0 spec flagged this as the single biggest risk.
RLIMIT alone was deemed insufficient.

## Decision

Run user code as a **dedicated unprivileged system user** (`sandbox`),
with strict RLIMITs, in a throwaway tmpfs working dir.

```bash
# deploy/setup-sandbox-user.sh (idempotent)
useradd --system --shell /usr/sbin/nologin --no-create-home \
        --uid 32768 sandbox
```

The runner (`app/sandbox/runner.py`) executes:

```python
import os, resource, subprocess, tempfile
pwd = pwd.getpwnam("sandbox")

def preexec():
    os.setgroups([pwd.pw_gid])
    os.setuid(pwd.pw_uid)
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, 256 * 1024 * 1024, 256 * 1024 * 1024)
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, 1 * 1024 * 1024, 1 * 1024 * 1024)

with tempfile.TemporaryDirectory(dir="/dev/shm") as tmp:
    subprocess.run(
        argv, stdin=PIPE, stdout=PIPE, stderr=PIPE,
        timeout=2.5, preexec_fn=preexec, check=False, cwd=tmp,
    )
```

Four layers of defense:
1. **UID isolation** — `sandbox` has no shell, no home, no write to
   `/home/hermes`.
2. **Resource limits** — CPU 2s, memory 256MB, 1 process, 1MB file
   writes.
3. **tmpfs working dir** — `/dev/shm` is RAM-backed; no disk fill.
4. **Hard timeout** — `subprocess.run(timeout=2.5)` sends SIGKILL.

## Consequences

- **Positive:** even a `g++` compiler bug can't pivot to the host.
- **Positive:** OOM stays inside the sandbox UID (which has no swap
  priority, so the OOM killer targets it first).
- **Negative:** setup script must run as root (`useradd`). One-time
  cost. Documented in deploy README.
- **Negative:** no network namespace in MVP. A program that does
  `socket()` would still work. Mitigation: C++ stdlib has no
  networking without explicit headers; Python `socket` requires
  `import` which we don't forbid. Single-user trust boundary is the
  reason this is acceptable. If we ever open the app to other users,
  add `network_namespace` (see ADR-0003).
- **Reversibility:** low. Swapping UID model for full Docker would
  require reworking runner.py.

## Alternatives considered

- **RLIMIT only:** insufficient per reviewer feedback.
- **Docker per submission:** would consume Docker host RAM (already
  hosting 6 stacks via Portainer). Rejected.
- **Judge0 self-hosted:** too heavy, no benefit over local sandbox
  for single-user.