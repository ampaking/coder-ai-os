# Evidence, not claims (`coder-ai prove`)

An agent can write "implemented the API and the UI, all tests pass". That sentence costs nothing
and proves nothing. This makes the claim checkable — and makes shipping an unchecked claim
impossible.

The principle: **required evidence is derived from the diff, never from the claim; the report is
generated from what was observed; unverified work cannot be delivered.**

---

## What you see

```
RESULT  partial — 1 of 4 verified

  item                                        status       evidence
  API: POST /subscriptions returns 201        ✓ verified   make test
  UI: the plan card shows the renewal date    UNVERIFIED   ui work is unverified
  UI: mobile 375px layout does not overflow   NOT DONE     not marked complete
  i18n: ja and en strings for the card        NOT DONE     not marked complete

  3 item(s) are not verified. Say so plainly rather than reporting success.
```

| Status | Meaning |
|---|---|
| `✓ verified` | a command was observed to pass, after the last edit of the files it covers |
| `UNVERIFIED` | claimed or required, with no evidence behind it |
| `NOT DONE` | an acceptance item with no work in the diff |
| `STALE` | evidence exists, but predates the last edit — it proves nothing about the change |

## How evidence is recorded

A `PostToolUse` hook writes every command and its outcome to `.coder-ai/evidence/<task>.jsonl` —
command text, exit code, and which files were edited when. Never output, never prompts, never
secrets, and never a sentence a model wrote. If the hook is not installed, the whole table reads
`UNVERIFIED` and says so, rather than quietly passing.

```sh
coder-ai prove start subscriptions   # begin a task; earlier evidence does not count for it
coder-ai prove                       # the table
coder-ai prove --json                # for an agent to read
```

## What gets required

Derived from the diff, so the model never defines its own bar:

| You changed | It requires |
|---|---|
| a file matching your VAL `watchGlobs` | a visual run covering it |
| source code | the project's own discovered test command |
| a migration or schema | its contract check |

A command the repository does not document is reported as `UNKNOWN — human confirmation required`
and does not block. Nothing is invented.

## What "done" means

Write `.ai/<task>/acceptance.md` before implementing — one checkbox per observable outcome:

```markdown
- [ ] API: POST /subscriptions returns 201
- [ ] UI: the plan card shows the renewal date
- [ ] UI: mobile 375px layout does not overflow
- [ ] i18n: ja and en strings for the card
```

The item set is fingerprinted when first seen. Removing or materially rewording an item afterwards
is reported; adding items is free, because discovering more work is honest. A list written after
the work began is flagged as retrospective.

## Work you did not think of

The repository already knows what a complete feature looks like here. `coder-ai prove` finds the
existing feature most like the new one and names what it is made of:

```
new:      api/subscriptions.py
analogue: api/invoices.py

  UI surface     web/SubscriptionCard.tsx          — because web/InvoiceCard.tsx exists
  test           web/__tests__/SubscriptionCard…   — because its test exists
  translations   locales/ja/subscriptions.json     — because locales/ja/invoices.json exists
```

Every expectation cites the file that justifies it, so a wrong analogy can be dismissed rather than
obeyed. In a headless service it infers no UI. With no credible analogue it says nothing, and on a
change of more than twenty added files it does not guess at all.

When the work crosses surfaces, it says so — a 41 000-line change has no provable parts until it
is divided:

```
  This work crosses 3 surfaces — consider splitting it:
    api      2 item(s)
    ui       2 item(s), 1 inferred
    i18n     1 inferred
```

## Can UI even be verified here?

The failure that motivates this whole feature was a visual loop that was installed, hooked, and
unreachable — so nothing looked at the UI and nothing said so. `coder-ai doctor` now checks the chain:

```
  ● config           1 app(s), 3 watch glob(s)
  ✗ watch globs      match nothing here — the loop can never fire
    → point watchGlobs at where the UI actually lives
  ● wrapper          .coder-ai/val/run
  ● edit hook        marks UI validation pending after a matching edit
  ✗ evidence hook    not installed — claims cannot be verified here
    → run: coder-ai sync
  ● permission       the agent can run it without a prompt
```

An observed visual run outranks this inspection: if the loop demonstrably ran, the configuration's
opinion about whether it *could* is moot.

## The consequence

`coder-ai ship` runs `coder-ai prove` before its first mutating step and refuses to deliver unverified work.
See [delivery](delivery.md).
