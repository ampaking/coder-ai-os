---
name: api-change-review
description: Review a public API, schema, or contract change for consumers, compatibility, generated types, tests, migration, security, and rollback impact.
---
<!-- coder-ai-os:generated -->

# API change review

1. Enumerate consumers — find every caller of the changed surface: `rg -w '<name>' .ai/symbols/` returns its map row (enriched maps carry a used-by column: exact import-confirmed call sites); `.ai/symbols/INDEX.md` shows which packages depend on the owner. Fall back to the code index (`callers`/`impact`) for proof-grade graphs. Never scan the repo for this.
2. Classify: backward-compatible vs breaking. Breaking → plan migration + version bump before code.
3. Regenerate/adjust shared types, client stubs, and fixtures that mirror the contract.
4. Update the tests that pin the contract; add one for the new/changed shape.
5. Note rollback and any migration in the impact report. Flag if it touches auth/permissions.
