# ADR-0005 — Languages in MVP: C++ and Python only

- **Status:** accepted
- **Date:** 2026-08-09

## Context

The original spec mentioned C++, Python, C, Rust, JavaScript in the
sandbox. Each language adds:

- A binary dependency (`g++`, `python3`, `rustc`, `node`, `gcc`)
- A compile/run command builder in `sandbox/languages.py`
- A CodeMirror mode file (CDN-loaded, but tracked)
- Distinct stderr formats (g++ vs clang vs rustc)
- Different RLIMIT_AS pressure (Rust binaries are heavy)

For a single-developer MVP, this multiplies the sandbox test surface
without a corresponding benefit.

## Decision

**MVP ships with C++ and Python only.** C, Rust, JavaScript → v0.2+.

The `sandbox/languages.py` module is structured so adding a new
language is one dict entry — but we don't add the entries yet.

## Consequences

- **Positive:** sandbox test surface is ~5 tests, not 20+.
- **Positive:** `languages.py` is small (~30 LOC).
- **Positive:** matches OBI reality — most Brazilian OBI competitors
  use C++ or Python.
- **Negative:** a user who wants to train in Rust can't yet.
- **Reversibility:** high. Adding a language is one entry in a dict.

## Alternatives considered

- **All 5 languages in MVP:** YAGNI per ponytail principle.
- **Python only:** too narrow; OBI community standard is C++.
- **C++ only:** misses Python's role as the "easy mode" for beginners.