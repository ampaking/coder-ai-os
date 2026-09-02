<!-- coder-ai-os:generated — companion to AI_DEV_PROTOCOL.md -->
# PROTOCOL_PHASES.md — the feature/epic pipeline

Load this file **only** after `AI_DEV_PROTOCOL.md` classified the work as **feature** or
**epic**. Trivial/small work never needs it — the tiers, floor, reporting format, and hard
gates all live in `AI_DEV_PROTOCOL.md` and still apply here.

---

## 1. The phases (feature / epic)

Each phase has an **entry gate**, an **output artifact**, and an **exit gate**. Do not cross a gate until its artifact exists.

**Phase 1 — Understand Goal.** Restate the intent in your words + a Definition of Done (observable). Classify the request type (change / explain / investigate). If ambiguous, ask ≤3 blocking questions. → `plan.md` (Goal + DoD).

**Phase 2 — Repository Investigation.** Entry points, callers, dependencies, tests, config, docs. **Query, don't full-scan:** use `rg`/filename search first to locate the narrow surface; use the code index (CodeGraph `callers`/`callees`/`impact`) only when dependency tracing is needed; use `.ai/PROJECT_SNAPSHOT.md` as an orientation fallback. Open only returned files + directly related config/tests. **No code edits in this phase.** → findings appended to `plan.md`. *Exit gate: you can name every file the change will touch and why.*

> **Investigation budget — progressive. Start at Level 1; escalate only when evidence requires it.**
> - **L0 Instructions** — repo-local agent instructions + project identity (`AGENTS.md`/`CLAUDE.md`).
> - **L1 Locate** — filenames, symbols, routes, interfaces, tests related to the request.
> - **L2 Trace** — direct callers, dependencies, configuration, similar implementations.
> - **L3 Impact** — public APIs, integration points, migrations, security, regression risk.
> - **L4 Repo-wide** — broad architecture only when the change genuinely crosses subsystems or lower levels can't resolve the task.
>
> **Context rules:** read symbols/ranges, not whole files · don't re-read unchanged files · don't load all tests/docs/architecture by default · prefer index / symbol / reference / dependency queries — the code atlas (`.ai/symbols/`) answers a symbol lookup in one `rg -w` line and a unit's wiring in one map section; NEVER read a whole map or unaffected code · after a task, `--refresh` Δ lines show old vs new symbols: review only what they name, flag unintended removals and orphaned (dead) functions · keep a compact record of files·symbols·assumptions·decisions · treat tool output as temporary (summarize before continuing) · never use context from another repository.
> **Delegation:** For feature/epic work, parallelize only independent read-heavy exploration,
> tests, or review when it reduces wall time or protects the main context. Return compact evidence;
> keep writes serial. Subagents trade more total tokens for isolation and speed, so skip them for routine work.


**Phase 3 — Architecture Mapping.** Draw the **CURRENT** change graph (§4) of the affected flow. → `change-graph.md` (before).

**Phase 4 — Task Breakdown.** Split the epic into **atomic tasks, each a mini-PR** (§2). A task touches the fewest files that still leave the tree green. → `tasks/NN-<slug>.md` per task.

> **Task sizing:** one independently reviewable behavior per task · preferably **< 5 changed files** ·
> separate refactoring from behavior changes · separate schema/migration from application logic ·
> separate tests when they form a meaningful review boundary. **Implement one, validate it, then the
> next — never implement all tasks before validating the first.**

**Phase 5 — Review Plan.** Produce the **impact report** (§6) + the **AFTER** change graph (§4). For wide-blast-radius, destructive, billing, or policy changes: **stop and get approval** (plan-first). → `impact-report.md`, `change-graph.md` (after).

**Phase 6 — Implement ONE task.** Smallest complete change for exactly one task. **State the reason** in the task file (why this change exists). No batching multiple tasks into one diff.

**Phase 7 — Validate (this task).** Run lint / typecheck / tests (and benchmark if perf-sensitive) for the task. Never claim success without an observed run. A passing test is evidence only when it could detect the defect: derive cases from acceptance criteria, contracts, invariants, and failure modes—not from the implementation. For a bug, prefer an observed failing regression before the fix, then green after. Cover relevant boundaries and negative/error paths; do not mock the unit under test, duplicate its algorithm in assertions, assert only execution, or use snapshots as the sole behavioral proof. If a high-risk regression test was necessarily added after the fix, perform a safe temporary fault/mutation sensitivity check, observe the expected failure, restore immediately, and rerun green. → check-marks plus red/green or sensitivity evidence in the task file.

> **Fault-injection safety:** before injecting any temporary fault, write the exact
> file/lines and the revert edit into the task file; restore **before doing anything else**
> and rerun green. If the session is interrupted, the resuming session applies that recorded
> revert first — an injected fault must never survive in the tree.

**Phase 8 — Review (this task).** Self-review + specialized reviewer roles (§5), review-only. → `reviews/NN-<role>.md`.

> **Loop phases 6–8 per task.** One task in flight at a time.

**Phase 9 — Integration Review.** Do the finished tasks compose? Contracts consistent, no drift from the plan, no unrelated files touched. → `integration.md`.

**Phase 10 — Regression + Final Audit.** Full test suite. Diff vs. the original goal: dropped requirements? fabricated scope? → `final-report.md` (flow-graph, `AI_DEV_PROTOCOL.md` §2).

---

## 2. Task = mini-PR (template)

Every task file (`.ai/<epic>/tasks/NN-<slug>.md`) uses:

```
# Task NN — <title>

Reason      Why this change must exist (the problem, not the solution).
Goal        Observable outcome of this task.
Files       path — role in this change
Dependencies  tasks/APIs this relies on or unblocks
Risk        what could break + blast radius
Expected Output  what the code/behavior looks like after
Validation  [ ] unit  [ ] integration  [ ] typecheck  [ ] lint  [ ] benchmark
Done Criteria  the check that proves this task is complete
```

Every code change carries its **Reason** — that is what turns diffs into living documentation.

---

## 3. Artifacts layout (per project)

```
.ai/<epic-slug>/
├── plan.md            Phase 1–2  (goal, DoD, investigation findings)
├── change-graph.md    Phase 3 & 5 (before / after)
├── impact-report.md   Phase 5
├── tasks/NN-<slug>.md Phase 4 (one file per atomic task)
├── reviews/NN-<role>.md Phase 8 (per task, per reviewer role)
├── integration.md     Phase 9
└── final-report.md    Phase 10
```

Committing `.ai/` depends on the repo's **memory scope** — read it from the
`.ai/PROJECT_SNAPSHOT.md` header before touching git state:
- **SHARED** (`.ai/` tracked): commit `.ai/` updates with the feature — it is the *why*
  history that diffs cannot capture, and the team's cross-machine resume state.
- **LOCAL-ONLY** (`.ai/` git-ignored, or the developer chooses not to push it): never
  `git add` it and never force past the ignore; treat checkpoints as machine-local, assume
  teammates/CI cannot see them, and on a fresh clone reconstruct from code + git log.
- **Untracked so far**: whether to share is the human's decision — surface it once, don't decide.

---

## 4. Change graph (required for feature/epic)

Show architecture **before → after**, not lines. A graph reviews in seconds; a 300-line diff does not.

```
CURRENT                         AFTER
API                             API
 │                               │
 ▼                               ▼
Retriever                       Retriever
 │                               ├──────────────┐
 ▼                               ▼              ▼
Pinecone                        Metadata      Pinecone
 │                               └──────┬───────┘
 ▼                                      ▼
Response                            Reranker
                                        ▼
                                    Response
```

Rules: mark **added / removed / changed** nodes; keep it to the touched subsystem; if the graph doesn't change, say so (pure internal change).

---

## 5. Reviewer roles (review-only — never write code)

Review depth follows risk; a reviewer proposes findings and does **not** edit. Trivial: self-review.
Small: one correctness pass. Feature: correctness plus only the specialist roles implicated by the
changed surface. High-risk/public-contract/security/performance work: all applicable roles, preferably
with an independent provider. Never spend four model passes on a change with no corresponding risk.

| Role | Looks for |
|---|---|
| **Reviewer** | correctness, edge cases, dropped requirements vs the task Goal, test adequacy |
| **Security** | injection, authz, secret handling, unsafe input, dependency risk |
| **Performance** | N+1, unnecessary work, allocations, hot-path regressions, big-O |
| **Architecture** | layering/contract violations, coupling, does it match the plan & repo conventions |

**Per tool (hybrid):**
- **Claude Code** — spawn a real subagent per role, or run `/code-review` (adversarial multi-lens verify). Reviewers are separate contexts.
- **Codex / Gemini CLI / Aider / Cursor** — run each role as a fresh pass with the role's checklist as the prompt: *"You are the {role} reviewer. You may not write code. Report findings only."*

Each selected role writes `.ai/<epic>/reviews/NN-<role>.md` with findings (severity + file:line), or "no findings".

---

## 6. Impact report (before touching code — Phase 5)

```
Feature         <name>
Affected files  <n>   (list)
Affected APIs   <n>   (list — flag any contract change → regenerate types)
Tests           <n>   (existing to update + new to add)
Possible bugs   <n>   (the ways this breaks: long input, empty/0, i18n, concurrency…)
Performance     none | low | medium | high risk (why)
Security        none | low | medium | high risk (why)
Rollback        how to revert safely
```

If it changes a public contract (API response, shared type, DB schema), that is called out here and drives type regeneration / migration planning.
