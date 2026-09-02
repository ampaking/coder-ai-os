---
name: native-orchestration
description: Coordinate a feature or epic from a normal Claude or Codex session using native subagents and direct Claude-to-Codex or Codex-to-Claude CLI handoffs, with atomic tasks, cross-provider review, repair, validation, and durable checkpoints.
---
<!-- coder-ai-os:generated -->

# Native orchestration

The interactive CLI the user opened is the controller. Preserve its selected/default model; do not
restart the parent merely to route a task. `coder-ai-os` is setup and instruction generation only:
never require `coder-ai-os run`, a daemon, or an MCP bridge for execution.

## Plan and route

1. Derive observable acceptance criteria from the original prompt. Split feature/epic work into ordered,
   independently verifiable tasks; keep exactly one write task active.
2. Record the plan and current task in the repository's existing AI_DEV_PROTOCOL artifacts and
   `.ai/memory/CURRENT.md`. Project Tasks synchronization belongs to installed hooks; do not run
   `coder-ai-os tasks` during normal work unless the user explicitly requests Project Tasks.
3. Route by capability and risk. Use the current provider's native subagent surface for same-provider
   exploration or review. Use the other installed CLI directly when its capability is a better fit or
   for independent cross-provider review. Do not hardcode a model name that the local CLI does not expose;
   omit `--model` to use that CLI's configured default, or use a user/configured model known to be available.
4. Never run two writing agents concurrently in the same working tree. A delegated worker receives only:
   objective, acceptance criteria, declared files/scope, relevant checkpoint, constraints, validation,
   and unresolved findings. It must not receive secrets or unrelated conversation history.

## Direct native handoff

From Claude, invoke Codex through the Bash tool with the task prompt on stdin:

```text
codex exec --cd <project> --sandbox workspace-write --output-schema <schema> -
```

For review, use `--sandbox read-only`; `codex exec review` is also valid when its native diff target
matches the review scope. Add `--model <configured-model>` only after availability is known. Capture the
returned session/thread identifier when emitted; continue with `codex exec resume <id> -` when a repair
follow-up belongs to that same delegated context.

From Codex, invoke Claude through the shell tool with the task prompt on stdin:

```text
claude -p --permission-mode auto --output-format json
```

For review, use `--permission-mode plan` and disallow Edit/Write tools. Add `--model
<configured-model>` only after availability is known. Capture `session_id` from JSON and continue with
`claude -p --resume <id> --output-format json` when the same delegated context should continue.
When a known Claude fallback model is configured, print-mode handoffs may also use
`--fallback-model <configured-model>` for overload recovery; it is not evidence that quota/auth failures
can fall back. Codex has no equivalent assumed here—use the explicit ladder below.

Use a schema/contract requiring: status, summary, changed files, validation commands and observed results,
findings with file:line, blockers, session id when available, and next action. Treat malformed output,
non-zero exit, missing executable/authentication, or unobserved validation as failure evidence—not success.

## Failure classification and fallback

Cross-provider work is optional unless the user explicitly requires that provider. Before every native
handoff, write a compact checkpoint to `CURRENT.md`: objective, active atomic task, completed criteria,
changed files, observed checks, unresolved findings, attempted provider/model, and next action. Never put
the prompt, model response, terminal output, or secrets in the checkpoint.

Classify the handoff result from its exit status and bounded stderr/structured response:

- **transient** — timeout, overload, 429/rate limit, or temporary transport failure;
- **capacity** — usage/quota exhausted, context limit, or requested model unavailable;
- **setup** — executable missing, authentication required, workspace untrusted, or permission denied;
- **work failure** — valid response reports blocker, malformed result, repeated reasoning failure, or
  validation failure.

Apply this deterministic order; never loop indefinitely:

1. Transient: retry the same handoff once. Do not retry quota/auth/model-not-found failures.
2. If a configured alternate model for that CLI is known available, start one fresh delegated session
   with `--model`; never guess model names and never change the interactive controller's selected model.
3. If the opposite provider fails or the user opted out of it, continue under the original controller:
   use a fresh same-provider native subagent/context for review or the remaining bounded task.
4. If same-provider delegation is unavailable, the controller performs deterministic validation and the
   tier-appropriate review itself, recording that independent model review was unavailable.
5. Only mark blocked when the failed provider is an explicit user requirement, all allowed execution paths
   fail, or a genuine human/security boundary remains. Reviewer unavailability alone is not a blocker.

Do not switch provider/model merely because a response ended. Switch only on a classified failure, two
matching work failures, a checkpointed context-pressure handoff, or an explicit user/configured preference.
Never retry the same failure signature more than twice across all routes.

A user prompt such as “use `<model>` for review” is an explicit delegated-model preference, not permission
to change the controller. Pass it to the delegated CLI once. If that model is unavailable, record the
failure and retry with that CLI's configured default unless the user said the model itself is required.
The human can change the interactive controller through its native model picker/command; agents must not
silently do that on the human's behalf.

### Controller context or quota exhaustion

The current native CLI cannot replace its own parent process after that process has already exhausted its
context or provider quota. Reduce loss by checkpointing after every atomic task and before long review or
validation phases. When the host exposes context pressure, checkpoint and compact/start a fresh session
before exhaustion. If the controller terminates, the next normal `claude` or `codex` session resumes from
`CURRENT.md`; do not claim an automatic in-process failover occurred.

## Review and repair loop

After each atomic implementation task:

1. Run relevant deterministic validation in the controller session.
2. Refresh the atlas and inspect changed symbols, full diff, and direct callers.
3. Use a fresh correctness review. Add opposite-provider review for high-risk/cross-contract work or when
   explicitly requested; add specialist lenses only when their risk is present.
4. Send actionable findings to the implementing context, repair serially, rerun validation, then obtain a
   fresh review. Bound repeated identical failures: after two matching failures, checkpoint the evidence and
   switch provider/model or report a genuine blocker.
5. Continue to the next planned task only when the current task's criteria and checks pass.

If the other CLI is unavailable, apply the fallback ladder above and say which review path was actually
observed; never claim cross-provider or independent review that did not occur.

## Human decisions

Pause only for security, credentials, destructive operations, production, auth/data/API/schema/migration
choices, or two materially different product outcomes. A foreground CLI cannot wait and also time out by
itself. For noncritical ambiguity, record the safest reversible assumption and continue. Never silently
time out a hard-boundary decision.

Completion belongs to the controller, not a worker response. It requires all acceptance criteria,
validation, resolved review findings, atlas/diff/caller inspection, CURRENT.md synchronization, observed
installed-hook outcome when available, and a final concise evidence report for human review.
Then load `release-handoff` to prepare small coherent human-run commit suggestions and repository-evidenced
next actions; never execute Git writes, pushes, releases, or deployments.
