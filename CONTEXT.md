# Code-Gym — Context

> The one doc loaded on every session that touches this repo. Keep
> it current. **Glossary only** — no specs, no implementation
> details. For those, see the ADRs and the plan at
> `.hermes/plans/2026-08-09_221134-code-gym-v0.1.0-mvp.md`.

## Milestone status

Target: **v0.1.0 MVP**. Status as of `main @ f05d014` (v0.1.0 tagged below).

- **M1: Auth** — ✅ done. Tickets: #3 (M1.T1), #4 (M1.T2),
  #5 (M1.T3), #6 (M1.T4). PRs: #22, #24, #25, #29.
- **M2: Content** — ✅ done. Tickets: #7 (M2.T1), #8 (M2.T2),
  #9 (M2.T3). PRs: #21, #31, #30.
- **M3: Navigation** — ✅ done. Tickets: #10 (M3.T1),
  #11 (M3.T2), #12 (M3.T3). PRs: #20, #26, #28.
- **M4: Editor + Sandbox + Submission** — ✅ done. Tickets:
  #13 (M4.T1 → #23), #14 (M4.T3 → #18), #15 (M4.T2 → #27),
  #16 (M4.T4 → #32), #17 (M4.T5 → #33).
- **Prefactor P.1** — ✅ done. Ticket: #2. PR: #19.

**16 of 16 tickets merged. v0.1.0 milestone complete.**

## What this is

A personal training app for **DSI** (Data Structures & Algorithms)
aimed at OBI (Olimpíada Brasileira de Informática) and competitive
programming marathons. Inspired visually by NeetCode; focused on a
small, curated set of sources (OBI past problems, NOIC, CP-Algoritmos).

## Glossary

> Terms used throughout the codebase. When you invent a new one,
> add it here.

### User

A registered person with login credentials. Created via the CLI,
not via public signup (see ADR-0003).

### Topic

A category of DSI knowledge: "Grafos", "Programação Dinâmica",
"Estruturas de Dados", etc. Topics are the unit of progress in the
roadmap. Each topic belongs to an OBI phase (F1, F2, F3, UNI) or
no phase (auxiliary topics).

### Problem

A single DSI exercise with statement, input/output format, and
test cases. Each problem belongs to exactly one topic and has a
difficulty rating (1..5). Comes from one source (OBI, NOIC,
CP-Algoritmos, curated).

### Submission

An attempt by a user on a problem. Stores the code submitted,
the language used, the verdict, and the attempt number (1, 2, 3...).
Multiple submissions per (user, problem) are expected — that's the
point.

### Verdict

The result of running user code against test cases. One of:

- **AC** — accepted, all test cases passed
- **WA** — wrong answer, output didn't match
- **TLE** — time limit exceeded (2s default)
- **RE** — runtime error (segfault, exception, etc.)
- **CE** — compilation error (C++ only; Python is interpreted)

### Roadmap

The grid of topics × problems shown to the user. Topic order
follows OBI F1 → F2 → F3 → Universitário. Within a topic, problems
are listed in difficulty order.

### Progress

How far a user has gotten through the roadmap. Measured per topic
(solved / total) and overall (sum). Rendered as NeetCode-style
bars and grid colors.

### ELO

An internal score per user, updated when a submission happens.
Stored in the database but **not visualized** in the MVP UI. Kept
so we don't have to invent it later.

### Sandbox

The isolated execution environment for user code. See ADR-0002 for
the security model. Runs as a dedicated `sandbox` user.

### Curated

Hand-picked content. Distinguishes the MVP from "scraped
everything we could find". The roadmap and seed YAML files are
curated; scrape scripts can extend them but don't replace them.

## Scope boundaries

- **In scope (v0.1.0):** OBI F1+F2 problems, NOIC content,
  CP-Algoritmos as study material reference, C++ + Python, local
  sandbox, invite-only accounts.
- **Out of scope:** real-time collaborative editing, public
  leaderboards, paid tiers, mobile-responsive editor, languages
  beyond C++/Python.

## Future (registered, not built)

See ADRs and the plan file. Quick recap:

- **v0.2:** Revisão espaçada, cron for scrapers, C language,
  mobile breakpoints.
- **v0.3:** Judge externo (Codeforces API), Rust + JS sandbox,
  ELO visualization.
- **Never:** SaaS multi-tenant, Playwright UI tests, React/Svelte.

## Anti-patterns

- Don't add a public signup route. (ADR-0003.)
- Don't add a language to the sandbox without an ADR. (ADR-0005.)
- Don't add cron jobs without first confirming the scrape has
  meaningful cadence. (ADR-0006.)
- Don't build a React/Svelte frontend. HTMX + thin JS only.
  (ADR-0004.)