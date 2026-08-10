# ADR-0003 — No public signup, invite-only via CLI

- **Status:** accepted
- **Date:** 2026-08-09

## Context

The app will be deployed via Cloudflare Tunnel on `code-gym.froto.online`,
which means it is publicly reachable from the internet. The user model
says "multi-user from MVP", but exposing a `POST /signup` route
publicly invites:

- **Spam signups** (account creation scripts targeting the endpoint)
- **Password brute-force** against weak passwords
- **Scrape abuse** (any user can hammer the sandbox)

The original spec said bcrypt + JWT + multi-user. That's still correct
— we keep the schema and middleware for multi-user. We just remove the
public surface that creates users.

## Decision

User creation happens **only via a CLI command**:

```bash
python -m app.cli create-user franco@froto.online 'senha123' 'Franco'
```

No `POST /signup` route. No "create account" link in the UI. Login is
the only public auth endpoint.

Middleware (bcrypt + JWT cookie) stays intact, so the data model and
auth code paths are unchanged. We're just gating account creation.

## Consequences

- **Positive:** zero attack surface for spam/abuse.
- **Positive:** admin (the dev) decides who has access.
- **Positive:** still multi-user — multiple humans can share the app
  if the dev creates accounts for them.
- **Negative:** no self-service onboarding. A friend can't sign up
  themselves; the dev has to run a command.
- **Negative:** no email verification flow needed (admin runs the CLI).
- **Reversibility:** low. Adding public signup later requires
  re-introducing the route + email verification + rate-limiting.

## Alternatives considered

- **Public signup + rate limiting + email verification:** YAGNI for
  single-admin deployment.
- **Public signup + invite codes:** still requires the route + code
  generation, more code, no benefit over CLI for current scope.
- **Single-user only (one hardcoded user):** loses the multi-user
  benefit without good reason.