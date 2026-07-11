---
name: monorepo-change
description: Use when — monorepo change spanning packages · API/contract change affecting other packages
---
<!-- coder-ai-os:generated -->

# Skill: monorepo-change

Trigger: you are in a monorepo and a change touches a shared API, schema, or contract — or a
fix may span more than one package/app/service.

Work **owner-first, isolated per package, integrate at the root**. Never fan out edits across
packages at once.

1. **Locate the owning package.** Map the change to the package that owns the surface — e.g. an
   API change belongs in `services/user-api` or `services/admin-api`, not in a web app. Use the
   repo layout / index (callers, imports, workspace deps) to decide. Announce the owner.
2. **Fix the owner in isolation.** Change only that package's files. Run *that package's own*
   tests/lint/typecheck from its directory. Do not touch sibling packages yet. Leave the tree green there.
3. **Trace cross-package impact.** From the changed surface, find every dependent: query
   `callers`/`impact`, workspace `dependencies`, and generated clients/types. Produce a list:
   each affected package + *why* (e.g. `apps/user-web` — calls the changed endpoint;
   `packages/sdk` — mirrors the type).
4. **Fix each dependent in its OWN package, one at a time.** Enter the package, make the smallest
   complete change, validate with that package's own checks, then move to the next. Keep each
   package independently green. Report per package as you go.
5. **Return to the root.** Run the workspace/root harness: full build, cross-package typecheck,
   and integration/e2e tests — per the project's own harness (`AGENTS.md`/`CLAUDE.md` at root,
   `turbo`/`nx`/`pnpm -r`, CI config). Confirm the whole dependency graph is green together.
6. **Report as a graph.** Owner → dependents, what changed in each, and the root check result.
   Flag any contract/migration that needs a version bump or coordinated deploy.

Rules: one package in flight at a time; a package's own `AGENTS.md`/`CLAUDE.md` wins for its
conventions; do not "fix" unrelated packages you merely passed through — report them separately.
