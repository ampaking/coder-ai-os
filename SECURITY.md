# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for anything exploitable.

- Preferred: open a [private security advisory](https://github.com/ampaking/coder-ai-os/security/advisories/new).
- Or email the maintainer: **[redacted]**.

You'll get an acknowledgement within a few days. Once a fix is available, we'll credit you in the
release notes unless you prefer to stay anonymous.

## Scope

`coder-ai-os` compiles local config into files under your home directory and writes into repos you
point it at. It never transmits your config anywhere. Security-relevant areas worth scrutiny:

- The always-on **guardrails** (`config/safety.yaml`, `claude/permissions.json`) — the compiler
  strips any `safety:` override from a user overlay, so guardrails can't be weakened via config.
- The **installer** (`install.sh`, `bootstrap.sh`) — it edits files in `~` and target repos inside
  marker-guarded regions and merges JSON with `jq`; report any path that could clobber unmarked
  user content or execute untrusted input.
- Generated **hooks** — the Stop hook uses the safe `prompt` type and the git hooks only re-run the
  repo's own checked-in scripts.

## Supported versions

This is pre-1.0 software; only the latest `main` receives fixes.
