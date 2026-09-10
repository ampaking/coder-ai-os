# Delivery workflow (`coder-ai ship`)

Some projects are `make test`. Others are a fixed sequence that must be followed in order:

```sh
git fetch origin
git rebase origin/develop
make verify-local QUALITY_BASE_REF=origin/develop
git push -u origin fix/google-model-latest-with-file-support
gh pr create --base develop
```

`coder-ai ship` runs **that project's** steps, in that order, and refuses to deliver unverified work.

---

## The contract

`.coder-ai/delivery.yaml` — the one file in `.coder-ai/` a team shares; setup keeps the
rest of that folder clone-local:

```yaml
delivery:
  base: origin/develop
  branch_pattern: "^(fix|feat|chore)/[a-z0-9._-]+$"
  steps:
    - {name: sync,   run: git fetch origin}
    - {name: rebase, run: git rebase origin/develop}
    - {name: verify, run: make verify-local QUALITY_BASE_REF=origin/develop}
    - {name: push,   run: git push -u origin ${branch}}
    - {name: pr,     run: gh pr create --base develop --fill}
```

`${branch}`, `${base}`, `${remote}` and `${title}` substitute as **single arguments** — a branch
name containing a space or a semicolon stays one token and never reaches a shell.

## Enabling it

```sh
coder-ai sync            # proposes a workflow from your Makefile / CI / package scripts, then asks
coder-ai ship enable     # or decide explicitly
coder-ai ship status     # what this project declares, and whether it is enabled here
```

The contract is committed; **whether it may run on your machine is not**. That decision is
machine-local, in `.coder-ai/local/delivery.json`, so cloning a repository whose file says
`enabled: true` grants
nothing until you say so. A non-interactive install never enables it.

## Running it

```sh
coder-ai ship --dry-run                        # the exact commands, none of them run
coder-ai ship --message "fix(worker): prevent duplicate retry execution"
coder-ai ship --steps verify,push              # narrow the run
coder-ai ship --json                           # machine-readable result
```

Steps run in order and stop at the first failure, with the observed output. `--steps` can narrow a
run but **cannot skip a check that precedes a push** — skipping verification to push faster is the
failure this exists to prevent.

## What it will and will not do

| Permitted | Refused, whatever the declaration says |
|---|---|
| create a branch matching the pattern | push to `main`, `master`, `develop`, or the base |
| commit what you staged | force push, `--force-with-lease`, `+refspec` |
| run the declared build commands | delete refs, push tags |
| rebase onto the declared base | rebase onto anything else, or interactively |
| push the feature branch, non-force | merge, `gh pr merge`, `gh pr close` |
| open an issue and a PR against the base | deploy, release, read secrets |
| | any program that is not a recognised build tool |

A declaration chooses **which** permitted operations run and in what order. It cannot widen the
matrix — the same rule that already holds for repository instructions in PR automation.

## The evidence gate

Before the first mutating step, `coder-ai ship` runs [`coder-ai prove`](evidence.md). If a required check has
no evidence — a UI file changed and no visual run was observed, tests never ran — delivery stops
and says which check and how to satisfy it:

```
  ✓ sync       git fetch origin                        5ms
  ✓ verify     make verify-local ...                   1.2s
  ✗ push       git push -u origin fix/retry
      refusing to deliver unverified work: 1 check(s) unverified, starting with
      'visual' (UNVERIFIED) — web/Card.tsx changed · run: .coder-ai/val/run run --task <task>

  stopped at 'push' — blocked
```

`--allow-unverified` overrides it. It is refused when the caller is an agent.

## The agent's part

An agent never runs git writes. It gets exactly one permission — `Bash(coder-ai ship:*)` — and the
harness does the writing, in the declared order. The agent decides *when* to ship, what to stage,
and what the commit message says.

Commit messages carry no `AI-Agent:` or `AI-Model:` trailers; provider detail lives in the audit log.
