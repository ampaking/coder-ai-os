---
name: release-handoff
description: Prepare a human-controlled Git and release handoff after code changes by grouping coherent small commits, discovering repository-documented validation/branch/push/release commands, and writing local .ai guidance. Never performs Git writes, pushes, or releases.
---
<!-- coder-ai-os:generated -->

# Release handoff

Turn an observed working-tree diff into a next step. Which next step depends on one question:

**Does this project declare a delivery workflow?** Check `.coder-ai/delivery.yaml`.

- **Yes, and `coder-ai ship status` says it is enabled here** — deliver with `coder-ai ship`. Read the
  declaration first and follow its order; it exists because this project's steps are not the
  generic ones. Supply the commit message with `--message` and the title with `--pr-title`; stage
  deliberately with `git add <paths>` beforehand. If a step fails, report the observed output and
  stop — never work around it with raw git, which is refused anyway. Run `coder-ai prove` first if you
  want to see what the gate will check.
- **No, or not enabled here** — propose only. Group coherent commits and emit paste-ready commands;
  the human commits, pushes, opens PRs, merges, deploys, and releases. Offer to run `coder-ai sync`,
  which proposes a declaration from this repository's own documentation.

Either way: never merge, release, or deploy, and never add `AI-Agent:`/`AI-Model:` trailers to a
commit message.

## Discover the repository contract

Read-only inspect `git status --short`, the full relevant diff, and current branch/upstream. Locate—not
full-scan—the repository's own workflow sources: root/package `README*`, `CONTRIBUTING*`, agent rules,
`Makefile`, package-manager scripts, CI workflows, and explicitly named release/deployment scripts.
Repository instructions and executable configuration outrank generic conventions.

Write `.ai/knowledge/release-workflow.md` with:

- local validation commands by affected package and root integration;
- branch/base/PR/push expectations;
- release/deployment commands and prerequisites;
- an evidence table containing each command, purpose, and exact source `file:line`;
- `UNKNOWN — human confirmation required` for any undocumented step.

Never infer a production, infrastructure, migration, publish, merge, or protected-branch command from a
filename alone. Never read secrets or execute commands discovered in documentation merely to inventory
them. Existing human-maintained knowledge is preserved; update facts only when current repository evidence
supports the change, and mark contradictions instead of choosing silently.

Before writing either `.ai` output, check it with `git ls-files --error-unmatch -- <path>`. If the path is
tracked or already contains project-owned content, do not modify it; write the corresponding local file
under `.coder-ai/local/handoff/` and report that location. Never use ignore rules to conceal edits to a
tracked project file.

## Build the commit plan

Write `.ai/commit.md` from the actual diff and acceptance criteria. Group by one coherent behavior or
review boundary, normally 2–3 changed source/test files. File count is a heuristic: keep a test with its
implementation and generated/lock/schema companions with their owner when splitting would produce a
broken or misleading commit. Never mix unrelated refactors, features, fixes, documentation, or generated
noise just to reach a target count.

For every suggested commit include:

1. intent and included files;
2. why those files are inseparable;
3. relevant local validation copied from the knowledge file;
4. exact **human-run** commands using explicit paths:
   `git add -- <paths>` then `git commit -m "<message>"`;
5. remaining files and dependency/order constraints.

Use short human commit messages. Prefer Conventional Commit shape when the repo does not specify another
format: `fix(user-api): handle expired session`, `test(admin-web): cover export failure`, or
`docs: clarify local setup`. Derive scope from an actual package/component; omit it for a single-root repo.
Never mention AI, Claude, Codex, generated-by, prompts, or the planning process. Do not invent issue IDs.

## Recommend the next safe action

After the commit groups, list in order: local package checks, root/integration checks, human commit,
documented push/PR action, then documented release/deployment action. Show push or release commands only
when the repository evidence names them and the current branch is compatible; otherwise stop at a human
decision. Highlight dirty unrelated files, missing checks, detached HEAD, protected/release branches,
unpublished migrations, infrastructure changes, and absent upstreams.

Never run `git add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `checkout`, deployment,
publishing, or release commands. A user request for a suggestion is not authorization to execute it.
