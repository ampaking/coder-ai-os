# Project Tasks

Project Tasks is a local record of work performed with coding agents. `coder-ai setup`, `sync`,
and direct project installation enable it together with content-free automatic observations.
It stores structured summaries and AI-session metadata in `.coder-ai/tasks/tasks.sqlite3`, which
setup adds to the project's `.coder-ai/.gitignore`. Raw prompts, source contents, secrets, commit
messages, file paths, authors, and employee scores are not collected.

```sh
coder-ai tasks enable                  # enable tasks + safe automatic collection
coder-ai tasks status --json
coder-ai tasks brief --depth quick     # 30-second onboarding; working/deep are also available
coder-ai tasks review                  # evidence for the last seven days
coder-ai tasks open                    # launch the black localhost UI only when requested
coder-ai tasks open --demo             # disposable rich preview; real database unchanged
coder-ai tasks close                   # stop this project's dashboard from another terminal
coder-ai tasks git-links               # review optional task/commit candidates
coder-ai tasks doctor                  # read-only SQLite integrity check
coder-ai tasks repair --yes            # preserve corruption, then create clean local state
coder-ai tasks export                  # safe structured export; session IDs excluded
coder-ai tasks collect disable         # stop automatic hooks until the next sync
coder-ai tasks disable                 # disable tasks until the next setup/sync; preserve history
coder-ai tasks delete --yes            # delete this project's task state
```

The compiled instructions give every supported agent the same local-only boundary; Claude Code and
Codex additionally receive the native `project-tasks` skill with the full lifecycle contract. Claude's
global Stop hook records only that a response ended; it never blocks another response or infers task
completion. Explicit task and validation events own lifecycle state. `tasks open` starts a loopback-only
server for that request and stops on exit. Notifications use an explicitly installed, short-lived local scheduler:
the operating system wakes one bounded analysis/delivery command at 18:00 local time, then it exits.
Optional Git collection is off by default and contains only hashes, parents, timestamps, file counts,
and aggregate additions/deletions; candidates require confirmation or rejection in the UI or CLI.
It is supporting evidence, never a measure of engineering value. The default UI theme is black,
keyboard-accessible, and responsive; theme emulation never changes the stored project preference.
The Now, Map, and Review period navigator selects Day, Week, Month, or Year, shows the exact date
range, and moves backward through project history.
The Personal Project Guide explains structured evidence without storing questions. Optional
Codex and Claude buttons run only after one visible confirmation and open the selected agent in an
external terminal using read-only/plan-only modes; the dashboard does not store model responses.

