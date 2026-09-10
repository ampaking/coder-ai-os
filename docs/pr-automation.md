# PR Automation (`coder-ai pr`)

Autonomous supervision of one Pull Request: coder-ai-os watches, the AI engineers.

```text
coder-ai pr 1420 -- claude
  → resolve the PR → isolated worktree → watch GitHub cheaply
  → meaningful change? → wake the AI → understand · verify · fix · validate
                         · self-review · commit · push · reply → AI exits
  → keep watching → merged / closed / stopped / watch window expired
```

An idle pull request costs **zero** model calls, repository scans, and test runs.

---

## Command

```sh
coder-ai pr <pr | branch | PR-url> [coder-ai options] -- <provider> [provider args...]
```

Everything after `--` is the provider's own command, passed through untouched.

```sh
coder-ai pr 1420 -- claude --model claude-fable-5
coder-ai pr feature/worker-retry --watch 3h -- codex --model gpt-5.6-sol
coder-ai pr https://github.com/org/repo/pull/1420 -- claude
coder-ai pr 1420 --bg --watch 4h -- claude
```

A branch is only a locator: coder-ai-os resolves it to the open PR and supervises **the pull request**.

| Option | Meaning |
|---|---|
| `--watch 1h` | watch window (default `1h`); accepts `s`/`m`/`h`/`d` |
| `--watch 0` | process the current state once, then exit |
| `--watch until-close` | watch until the PR is merged or closed |
| `--bg` | detach and supervise in the background |
| `--dry-run` | rehearse: show what a session would do, and do none of it |

## Try it safely first

```sh
coder-ai pr 1420 --dry-run -- claude
```

Resolves the PR, collects one snapshot, and prints the plan — the branch it would use, the worktree
it would create, where it would push, the findings it would track, and the first action it would
take. It creates no worktree, writes no session state, installs no guard, makes no write call to
GitHub, and never invokes a model. Use it for first contact with a real pull request.

## Managing sessions

```sh
coder-ai pr status          # every supervised PR on this machine
coder-ai pr status 1420     # one PR: state, CI, open findings, last action
coder-ai pr attach 1420     # stream a running session (detaching never stops it)
coder-ai pr log 1420        # the session's audit trail
coder-ai pr stop 1420       # stop at the next safe boundary — never mid-push
```

Requires the GitHub CLI (`gh`), authenticated.

---

## What the AI is allowed to do

Elevation exists **only** inside a live `coder-ai pr` session. A normal `claude` or `codex` session
keeps the standard guardrails — no `git commit`, no `git push`, ever.

| Allowed in a PR session | Refused by the environment |
|---|---|
| edit the isolated worktree | push any other branch |
| run the repository's own checks | force push, delete refs, push tags |
| `git add` / `git commit` | merge the PR, rewrite history |
| non-force push of the exact PR head branch | deploy, read secrets |
| reply to PR discussion | change branch protection |
| resolve a thread it actually fixed | approve or request changes as a reviewer |

"Refused by the environment" is literal, not a prompt instruction. Three independent layers:

1. a `git`/`gh` shim first on the session's `PATH` that decides and explains every command,
2. a `pre-push` hook scoped to the automation worktree — it also catches a git invoked by
   absolute path, and never touches the engineer's own checkout,
3. a post-run check that only the PR head ref moved, and moved forward.

A repository can direct engineering through its own `AGENTS.md`, `Makefile`, or CI workflow. It
**cannot** widen this boundary. When the session ends — merged, closed, stopped, or the window
expires — the elevation disappears; a later `claude` or `codex` session inherits nothing.

## Isolation

```text
~/work/aiila                                    your checkout — never touched
~/.coder-ai/pr-worktrees/org/aiila/pr-1420/  the automation workspace
~/.coder-ai/pr-sessions/org/aiila/1420/      session.json · snapshot.json
                                                findings.json · audit.jsonl · runs/
```

You stay free to switch branches, run Docker, and run your own agents while coder-ai-os works. Each
repository and each PR gets its own session, worktree, and lock; nothing bleeds between them.

Stopping coder-ai-os never destroys context — `coder-ai pr 1420 --watch 2h -- claude` later resumes and
processes only what is new.

## When the AI wakes

coder-ai-os wakes it only when engineering judgment is needed, and one snapshot change is one wake —
five comments plus a submitted review is a single triage, not six.

| Event | Wakes the AI? |
|---|---|
| new review comment, inline thread, or submitted review | yes |
| CI failed (a required check) | yes, with the failing job's log attached |
| new HEAD pushed by a human | yes — the in-flight run is invalidated, never force-pushed over |
| CI passed, CI still running | no |
| its own push or its own reply | no |
| thread resolved, comment withdrawn | no — recorded in the ledger |
| PR merged or closed | no — the session ends |

Repeated identical CI failures stop after three attempts rather than looping.

## The findings ledger

Every concern gets a stable id and a lifecycle, so nothing is analysed twice:

```text
OPEN → VERIFYING → VERIFIED → FIXING → FIXED
     ↘ REJECTED   ↘ RESOLVED  ↘ OUTDATED  ↘ CONFLICTED  ↘ HUMAN_REQUIRED
```

coder-ai-os reads resolution exactly as GitHub reports it, and tells the AI who did what:

| Signal | Result |
|---|---|
| reviewer resolved the thread | `RESOLVED` |
| **PR author** resolved their own thread | `RESOLVED`, flagged as a claim rather than confirmation |
| thread re-opened | back to `OPEN` |
| thread deleted / comment withdrawn | `RESOLVED` |
| anchored code gone after a push | `OUTDATED` |
| reviewer later approved, or their review was dismissed | their change request is withdrawn |

Judgment transitions (`VERIFIED`, `REJECTED`, `CONFLICTED`) are only ever written from an AI
decision. coder-ai-os never interprets what a reviewer meant.

## How the AI is told to work

The behavior lives in the compiled `pr-engineer` skill, not in coder-ai-os's code:

- discover this repository's own workflow from evidence — read the `Makefile`'s actual targets and
  `package.json`'s actual scripts; never invent a command the repository already defines
- an AI reviewer's comment is a **claim to verify**, not an order; a trusted human's request
  carries intent authority
- polite or indirect wording — especially Japanese — is not automatically optional
- conflicting human requirements, or a product/architecture decision → `HUMAN_NEEDED`
- validate narrow-to-broad, separate pre-existing failures from new ones, self-review the diff
- choose commit boundaries and messages by repository convention, with **no** `AI-Agent:` or
  `AI-Model:` trailers — provider and model detail live in the session audit log

Each wake ends in one state: `NO_ACTION`, `WAIT`, `REPLY_NEEDED`, `VERIFY_NEEDED`, `FIX_NEEDED`,
`REVIEW_NEEDED`, `HUMAN_NEEDED`, or `DONE`. Then the AI exits.

## Configuration

`config/pr_automation.yaml` — watch defaults, poll intervals, commit identity, provider routing.
The allow/deny matrix is deliberately **not** configurable; it is code, so that no configuration
file (or a repository editing its own instructions) can widen the boundary.

## Known limits

- **Fork PRs are supervised read-only.** The head branch lives in another repository, so pushes are
  refused; triage and replies still work.
- **`gh` is required** and must be authenticated.
- **Adaptive polling, not webhooks** — 30–60 s while CI is active, up to 15 min when idle.
- **Residual guard gap**: an agent that deliberately runs an absolute-path `git push --no-verify`
  skips the shim and the hook. The shim denies the flag, the provider profile denies absolute-path
  git, and post-push verification still detects it after the fact.
