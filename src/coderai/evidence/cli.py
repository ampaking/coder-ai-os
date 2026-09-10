"""`coder-ai prove` — command surface for the evidence gate."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from coderai.evidence import ledger as ledger_module
from coderai.evidence.prove import prove, render

USAGE = """usage:
  coder-ai prove [--json] [--task <id>]     what is actually verified in this task
  coder-ai prove start <task-id>            begin a task: nothing before it counts

Exit codes: 0 everything verified · 1 something is not · 2 bad arguments."""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0

    project = Path.cwd()
    if args and args[0] == "start":
        if len(args) < 2:
            print("coder-ai prove: start needs a task id", file=sys.stderr)
            return 2
        try:
            ledger_module.start(project, args[1])
        except ledger_module.LedgerError as error:
            print(f"coder-ai prove: {error}", file=sys.stderr)
            return 2
        print(f"coder-ai: recording evidence for '{args[1]}' — earlier evidence does not count")
        return 0

    task = None
    if "--task" in args:
        index = args.index("--task")
        if index + 1 >= len(args):
            print("coder-ai prove: --task needs an id", file=sys.stderr)
            return 2
        task = args[index + 1]

    verdict = prove(project, task)
    if "--json" in args:
        payload = {
            "task": verdict.task, "summary": verdict.summary,
            "observed": verdict.observed, "verified": verdict.verified,
            "lines": [asdict(line) for line in verdict.lines],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render(verdict), end="")
    return 0 if verdict.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
