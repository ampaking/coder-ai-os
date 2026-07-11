# Skill: api-change-review

Trigger: a public API, schema, or contract change.

1. Enumerate consumers — find every caller of the changed surface (index `callers`/`impact`).
2. Classify: backward-compatible vs breaking. Breaking → plan migration + version bump before code.
3. Regenerate/adjust shared types, client stubs, and fixtures that mirror the contract.
4. Update the tests that pin the contract; add one for the new/changed shape.
5. Note rollback and any migration in the impact report. Flag if it touches auth/permissions.
