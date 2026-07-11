# AI_DEV_PROTOCOL.md

A **model-agnostic development protocol** for any coding agent — Claude Code, Codex,
Cursor, Gemini CLI, Aider. The rule that makes it work:

> **No code is written until the repository is understood.**
> The goal is a *smarter workflow*, not a smarter model. Every step has a narrow
> objective and a reviewable artifact, so even small models stay reliable.

---

## 0. How to use this file

1. **Classify the work into a tier** (§1) and announce it in one line.
2. Run the phases for that tier (§2). Trivial work skips almost everything; features run the full pipeline.
3. Write the required **artifacts** to `.ai/<epic-slug>/` (§4) so every change is explainable — living documentation.
4. Never merge implementation and approval in one head: the **reviewer roles** (§6) only review; they never write code.

This file is the standard; the agent's own global rules only *point* here.

---

## 1. Right-size first — complexity tiers

Classify **before** doing anything. State the tier + one-line reason.

| Tier | Looks like | Pipeline |
|---|---|---|
| **trivial** | typo, one-liner, rename, comment, obvious config value | **Fast path**: edit → validate → report. No planning artifacts. |
| **small** | single-file logic, isolated bug fix, one function | **Lite**: Understand → Investigate → Implement (1 change, with reason) → Validate → Self-review. |
| **feature** | multi-file, new endpoint/component, changes a contract | **Full 10 phases**, one epic, atomic tasks. |
| **epic** | cross-cutting, migration, refactor across packages, perf/security-sensitive | **Full + integration + regression audit**, may span multiple epics. |

> The tier sets the *ceiling*, not a mandate to inflate. Never run 10 gates for a one-liner; never ship a feature as one giant diff.

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

## 2. The phases (feature / epic)

Each phase has an **entry gate**, an **output artifact**, and an **exit gate**. Do not cross a gate until its artifact exists.

**Phase 1 — Understand Goal.** Restate the intent in your words + a Definition of Done (observable). Classify the request type (change / explain / investigate). If ambiguous, ask ≤3 blocking questions. → `plan.md` (Goal + DoD).

**Phase 2 — Repository Investigation.** Entry points, callers, dependencies, tests, config, docs. **Query, don't full-scan:** use the code index if present (CodeGraph MCP — `search`/`callers`/`callees`/`context`/`impact`), else read `.ai/PROJECT_SNAPSHOT.md` for orientation, then open only the returned files + directly related config/tests. **No code edits in this phase.** → findings appended to `plan.md`. *Exit gate: you can name every file the change will touch and why.*

> **Investigation budget — progressive. Start at Level 1; escalate only when evidence requires it.**
> - **L0 Instructions** — repo-local agent instructions + project identity (`AGENTS.md`/`CLAUDE.md`).
> - **L1 Locate** — filenames, symbols, routes, interfaces, tests related to the request.
> - **L2 Trace** — direct callers, dependencies, configuration, similar implementations.
> - **L3 Impact** — public APIs, integration points, migrations, security, regression risk.
> - **L4 Repo-wide** — broad architecture only when the change genuinely crosses subsystems or lower levels can't resolve the task.
>
> **Context rules:** read symbols/ranges, not whole files · don't re-read unchanged files · don't load all tests/docs/architecture by default · prefer index / symbol / reference / dependency queries · keep a compact record of files·symbols·assumptions·decisions · treat tool output as temporary (summarize before continuing) · never use context from another repository.

**Phase 3 — Architecture Mapping.** Draw the **CURRENT** change graph (§5) of the affected flow. → `change-graph.md` (before).

**Phase 4 — Task Breakdown.** Split the epic into **atomic tasks, each a mini-PR** (§3). A task touches the fewest files that still leave the tree green. → `tasks/NN-<slug>.md` per task.

> **Task sizing:** one independently reviewable behavior per task · preferably **< 5 changed files** ·
> separate refactoring from behavior changes · separate schema/migration from application logic ·
> separate tests when they form a meaningful review boundary. **Implement one, validate it, then the
> next — never implement all tasks before validating the first.**

**Phase 5 — Review Plan.** Produce the **impact report** (§7) + the **AFTER** change graph (§5). For wide-blast-radius, destructive, billing, or policy changes: **stop and get approval** (plan-first). → `impact-report.md`, `change-graph.md` (after).

**Phase 6 — Implement ONE task.** Smallest complete change for exactly one task. **State the reason** in the task file (why this change exists). No batching multiple tasks into one diff.

**Phase 7 — Validate (this task).** Run lint / typecheck / tests (and benchmark if perf-sensitive) for the task. Never claim success without an observed run. → check-marks in the task file.

**Phase 8 — Review (this task).** Self-review + specialized reviewer roles (§6), review-only. → `reviews/NN-<role>.md`.

> **Loop phases 6–8 per task.** One task in flight at a time.

**Phase 9 — Integration Review.** Do the finished tasks compose? Contracts consistent, no drift from the plan, no unrelated files touched. → `integration.md`.

**Phase 10 — Regression + Final Audit.** Full test suite. Diff vs. the original goal: dropped requirements? fabricated scope? → `final-report.md` (flow-graph, §8).

---

## 3. Task = mini-PR (template)

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

## 4. Artifacts layout (per project)

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

Commit `.ai/` with the feature — it is the *why* history that diffs cannot capture.

---

## 5. Change graph (required for feature/epic)

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

## 6. Reviewer roles (review-only — never write code)

After the developer implements, run **separate reviewer passes**. A reviewer proposes findings; it does **not** edit. Order: Reviewer → Security → Performance → Architecture.

| Role | Looks for |
|---|---|
| **Reviewer** | correctness, edge cases, dropped requirements vs the task Goal, test adequacy |
| **Security** | injection, authz, secret handling, unsafe input, dependency risk |
| **Performance** | N+1, unnecessary work, allocations, hot-path regressions, big-O |
| **Architecture** | layering/contract violations, coupling, does it match the plan & repo conventions |

**Per tool (hybrid):**
- **Claude Code** — spawn a real subagent per role, or run `/code-review` (adversarial multi-lens verify). Reviewers are separate contexts.
- **Codex / Gemini CLI / Aider / Cursor** — run each role as a fresh pass with the role's checklist as the prompt: *"You are the {role} reviewer. You may not write code. Report findings only."*

Each role writes `.ai/<epic>/reviews/NN-<role>.md` with: findings (severity + file:line), or "no findings".

---

## 7. Impact report (before touching code — Phase 5)

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

---

## 8. Reporting format (every phase + final)

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

## 9. Hard gates (never cross these)

1. **No code before Phase 2 is complete** (repo understood, files named).
2. **One task at a time** — no multi-task mega-diffs; no touching files outside the task's declared Files.
3. **No "done" without an observed validation run.**
4. **Implementer never approves its own work** — reviewer roles are separate passes.
5. **Safety boundaries always hold** — no `.env`/secrets, no git writes (`commit/push/pull/merge/…`), no `sudo`/deploys. These are never relaxed by any tier or phase.
6. **The project's own `AGENTS.md` / `CLAUDE.md` win** for its quality gates, architecture, and conventions.
