#!/usr/bin/env bash
# Idempotent: creates 'sandbox' system user for Code-Gym local code execution.
# Per ADR-0002. Safe to run multiple times.
set -euo pipefail
if id sandbox >/dev/null 2>&1; then
    echo "sandbox user already exists (uid=$(id -u sandbox)), nothing to do."
    exit 0
fi
useradd --system \
        --shell /usr/sbin/nologin \
        --no-create-home \
        --uid 32768 \
        sandbox
echo "sandbox user created (uid=32768, no shell, no home)."