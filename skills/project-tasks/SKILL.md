---
name: project-tasks
description: Inspect or manage the local Project Tasks dashboard when the user explicitly asks for tasks, status, a map, review, ideas, notifications, settings, or project-task setup.
---

# Project Tasks

Project Tasks is project-local task memory enabled by setup/sync. It is separate from `.ai/` task plans and
agent instructions. Never copy raw prompts, source contents, secrets, `.ai/` documents, or chat
transcripts into it.

Normal coding work must not invoke `coder-ai tasks status/event/collect`. Installed lifecycle hooks
perform content-free synchronization without an agent shell command. Use this skill and its CLI commands
only when the user explicitly requests Project Tasks information or management.

1. Run `coder-ai tasks status --json`. If not configured or disabled, do not record anything.
   Mention `coder-ai tasks enable` without interrupting routine work; project init/setup/sync
   normally enables it automatically.
2. When enabled, classify the current request as start, continue, pause, block, validate, fail,
   complete, or end (when the agent session ends). Record the smallest structured summary with `coder-ai tasks event <action> --json
   '<object>'`. The object includes title and summary; add taskId, theme, agent, model,
   nativeSessionId, and resumeSupported only when known. Never invent an agent/session ID.
3. Reuse the returned taskId and sessionId during the same work. A completion event without a
   passing validation remains `needs_validation`; validate, recheck likely edge cases, then record
   completion. If task identity, completion, or a blocker cannot be inferred reliably, ask one
   short question instead of guessing.
4. Before unrelated work, briefly surface unfinished work without blocking the new request.
5. Use `coder-ai tasks brief` for a bounded onboarding view and preserve original project
   identifiers/language. Use `shape` when the user asks for prompt help.
6. Run `coder-ai tasks git` only after the user enabled Git metadata. It records hashes,
   timestamps, parents, file counts, and aggregate line counts—not paths, messages, diffs, authors,
   or source. Treat generated links as candidates until the user confirms or rejects them.
7. The UI is strictly on demand. Run `coder-ai tasks open` only when the user asks to see it or
   explicitly accepts an agent suggestion. Never launch it on a schedule, at startup, after a
   task, or as a background service.
8. Recommendations must cite structured evidence and must not rank people or infer productivity,
   health, belief, or engineering value. Git activity is supporting evidence only.
