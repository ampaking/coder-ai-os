# Intake — how a profile is created

Three paths, one canonical overlay (`config/local.yaml`). Whichever you use, the compiler
sees the same schema, so output is deterministic.

```
manual wizard ─┐
provided YAML ─┼──→ config/local.yaml ──→ compile ──→ build/*.md ──→ install
AI interview  ─┘        (one overlay)
```

## Manual — `coder-ai-os init --interactive`
`bin/setup` asks experience, verbosity, reply format, token budget, language, strict mode,
Codex-default, CodeGraph, and apply-now, then writes the overlay and installs. `--defaults`
runs it non-interactively. Safety is never asked.

## AI — `coder-ai-os init --ai claude|codex`
Launches that tool's CLI seeded to follow [`intake/PROFILE_INTERVIEW.md`](../intake/PROFILE_INTERVIEW.md).
The contract is **draft → review → compile**, never free-form file writes:

```
interview (one compact message)
      ↓
structured DRAFT profile (shown in chat, no files written)
      ↓
you approve  ("yes" / "edit <field>")   ← review gate
      ↓
write config/local.yaml → bin/compile --validate → install.sh
```

The AI configures **preferences only**. It may not decide permission boundaries,
destructive operations, secret access, repository-wide architecture, or team policy —
those are fixed in `config/safety.yaml` or need explicit config. Any `safety:` it emits is
dropped by the compiler.

## File — `coder-ai-os init --from profile.yaml`
Apply a hand-written or exported profile: copies it to `config/local.yaml`, validates keys
+ budget, then installs. See [`config/README.md`](../config/README.md) for the schema.

## Verify
```sh
coder-ai-os doctor          # budget · drift · conflicts · health
coder-ai-os profile show    # active overlay + compiled sizes
coder-ai-os diff            # uncommitted config/ + build/ changes
```
