# AI_DEV_PROTOCOL.md

A **model-agnostic development protocol** for any coding agent — Claude Code, Codex,
Cursor, Gemini CLI, Aider. The rule that makes it work:

> **No code is written until the repository is understood.**
> The goal is a *smarter workflow*, not a smarter model. Every step has a narrow
> objective and a reviewable artifact, so even small models stay reliable.

This file is deliberately small: tiers, floor, reporting, and hard gates — what **every**
task needs. The full feature/epic pipeline (10 phases, task template, change graph,
reviewer roles, impact report) lives in the companion **`PROTOCOL_PHASES.md`**
(installed at `.coder-ai/PROTOCOL_PHASES.md`). Load it **only** for feature/epic work —
never defensively.

---

## 0. How to use this file

1. **Classify the work into a tier** (§1) and announce it in one line.
2. **trivial / small** — run the pipeline named in the tier table. This file is all you need.
3. **feature / epic** — load `.coder-ai/PROTOCOL_PHASES.md`, run the phases, and write the
   required artifacts to `.ai/<epic-slug>/` so every change is explainable — living documentation.
4. Never merge implementation and approval in one head: reviewer roles only review; they never write code.

This file is the standard; the agent's own global rules only *point* here.

---

## 1. Right-size first — complexity tiers

Classify **before** doing anything. State the tier + one-line reason.

| Tier | Looks like | Pipeline |
|---|---|---|
| **trivial** | typo, one-liner, rename, comment, obvious config value | **Fast path**: floor (§1a) → edit → validate → report. No planning artifacts. |
| **small** | single-file logic, isolated bug fix, one function | **Lite**: Understand → Investigate → Implement (1 change, with reason) → Validate → Self-review. |
| **feature** | multi-file, new endpoint/component, changes a contract | **Full 10 phases** (`PROTOCOL_PHASES.md`), one epic, atomic tasks. |
| **epic** | cross-cutting, migration, refactor across packages, perf/security-sensitive | **Full + integration + regression audit** (`PROTOCOL_PHASES.md`), may span multiple epics. |

> The tier sets the *ceiling*, not a mandate to inflate. Never run 10 gates for a one-liner; never ship a feature as one giant diff.

### 1a. Understand-first floor — no tier drops below this

Most "simple-task" failures are misclassified one-liners: the *obvious* config value read by
three scripts, the *simple* rename that is a public API, the "fix X, it's broken" where X
isn't broken for that reason. The floor makes those impossible to hit blind, at a cost of
seconds — it is why the trivial fast path is safe to keep fast.

1. **Restate intent in one line before the first edit** — a misread is free to fix *before*
   the change, expensive after.
2. **Never edit a file you haven't read.** Read the target (at least the surrounding range)
   and check its direct consumers with one `rg` for the symbol/key you're changing.
3. **Verify claimed behavior before changing it.** If the request asserts how the code
   currently behaves ("X is broken", "X returns Y"), observe that behavior first (run it,
   run the failing test, or read the code path that proves it). Never fix an unverified claim.
4. **Escalate on contradicting evidence.** If anything found during 1–3 contradicts the tier
   or the request's assumption (more consumers than expected, a contract surface, a different
   root cause) — **stop, reclassify the tier, announce the new tier + reason**, and run the
   bigger tier's pipeline. Never absorb surprise scope silently.
5. **Validation floor.** Even trivial ends with the narrowest *observed* check (lint/typecheck
   of the touched file, the nearest test, or re-running the reproduced command) — and the diff
   touches only the files named in the intent line.

### Clarify by exception (all tiers) — don't ask first, don't scan blindly

Alongside the tier, judge **interpretation risk**. Investigate cheaply first; ask only when uncertainty *materially changes the result*. Triage the request into: desired outcome · current problem · expected behavior · constraints · likely affected area · evidence that proves it done.

| Class | Meaning | Action |
|---|---|---|
| **CLEAR** | intent, scope, expected result sufficiently known | proceed |
| **PARTIALLY CLEAR** | goal known, minor details need reasonable assumptions | proceed — **state the assumptions** in the plan |
| **AMBIGUOUS** | ≥2 materially different implementations possible | ask **one** concise question |
| **HIGH RISK** | may affect security, data, public APIs, migrations, billing, auth, production, or destructive ops | ask / **plan-first** before code |

**Proceed without asking** when: one obvious interpretation · repo conventions resolve the gaps · the change is local & reversible · acceptance criteria are inferable from existing tests/behavior · uncertainty doesn't change architecture or user-visible behavior.

**Ask one concise question** when: ≥2 materially different outcomes are plausible · requirements conflict · product behavior can't be inferred · a public API / schema / migration / permission model / security boundary may change · proceeding risks significant rework or destructive behavior · the required information doesn't exist in the repo.

Do **not** ask what repository inspection can answer. Do **not** ask to confirm routine implementation decisions. State important assumptions briefly in the task plan and continue.

**Ask once, not again and again.** If clarification is truly needed, batch **all** blocking
questions into a **single** message — never drip them one per turn, never re-ask what you
already inferred or the user answered. After that one round, proceed on stated assumptions;
do not circle back for reconfirmation. Repeated questioning is itself a failure mode.

---

## 2. Reporting format (every phase + final)

Report in a **flow-graph, under ~500 tokens**. Lead with the graph, not prose.

```
PHASE <n> — <name>     RESULT: done | blocked
<the phase artifact, or a one-line pointer to its .ai/ file>
NEXT: <the next gate>
```

Final report:
```
RESULT: completed | partial | blocked
INTENT: interpreted goal · assumptions made
CURRENT → NEW   (change graph)
TASKS: NN done / NN total
CHANGES: file — reason
REVIEWS: role → findings resolved
VALIDATION: command → pass/fail (observed)
BUGS/RISKS · NOT PERFORMED
```

---

## 3. Hard gates (never cross these)

1. **No code before investigation is complete** (repo understood, files named — Phase 2 of
   `PROTOCOL_PHASES.md` for feature/epic) — and no edit in *any* tier before the §1a floor
   (intent stated, target read, claims verified).
2. **One task at a time** — no multi-task mega-diffs; no touching files outside the task's declared Files.
3. **No "done" without an observed validation run and evidence that new/changed tests can fail for the defect they claim to cover.**
4. **Implementer never approves its own work** — reviewer roles are separate passes.
5. **Safety boundaries always hold** — no `.env`/secrets, no git writes (`commit/push/pull/merge/…`), no `sudo`/deploys. These are never relaxed by any tier or phase.
6. **The project's own `AGENTS.md` / `CLAUDE.md` win** for its quality gates, architecture, and conventions.
