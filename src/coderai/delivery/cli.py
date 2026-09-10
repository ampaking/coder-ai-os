"""`coder-ai ship` — command surface."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from coderai.delivery import declaration as declaration_module
from coderai.delivery.optin import propose_and_record
from coderai.delivery.ship import ShipError, ship, validate_message

USAGE = """usage:
  coder-ai ship [options]                run this project's declared delivery workflow
  coder-ai ship --dry-run                show the exact commands; run none of them
  coder-ai ship --steps verify,push      run only these (a preceding check is never skipped)
  coder-ai ship status                   what this project declares, and whether it is enabled
  coder-ai ship enable                   propose a workflow and turn it on for this machine

options:
  --branch <name>       the feature branch to use (default: the current one)
  --message "<text>"    commit message for a declared commit step
  --pr-title "<text>"   title for a declared pull-request step
  --json                machine-readable result
  --allow-unverified    human override of the evidence gate; refused for an agent

Exit: 0 delivered · 1 stopped · 2 cannot start."""


def _value(args: list[str], name: str) -> str:
    if name in args:
        index = args.index(name)
        if index + 1 < len(args):
            return args[index + 1]
    for item in args:
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0

    project = Path.cwd()
    if args and args[0] == "status":
        delivery = declaration_module.load(project)
        if delivery is None:
            print("coder-ai ship: this project declares no delivery workflow.")
            print("  run `coder-ai ship enable` to propose one from its own documentation.")
            return 1
        enabled = declaration_module.is_enabled(project)
        print(f"delivery — {delivery.path}")
        print(f"  base            {delivery.base}")
        print(f"  branch pattern  {delivery.branch_pattern}")
        print(f"  enabled here    {'yes' if enabled else 'no — run `coder-ai ship enable` to confirm'}")
        print("  steps")
        for step in delivery.steps:
            print(f"    {step.name:<10} {step.text()}")
        return 0 if enabled else 1

    if args and args[0] == "enable":
        # An explicit `enable` always asks again, even if this machine answered before.
        return propose_and_record(project, assume_yes="--yes" in args, reconsider=True)

    try:
        message = _value(args, "--message")
        if message:
            message = validate_message(message)
        report = ship(
            project,
            branch=_value(args, "--branch"),
            message=message,
            title=_value(args, "--pr-title"),
            only=[item for item in _value(args, "--steps").split(",") if item] or None,
            dry_run="--dry-run" in args,
            allow_unverified="--allow-unverified" in args,
        )
    except ShipError as error:
        print(f"coder-ai ship: {error}", file=sys.stderr)
        return 2

    if "--json" in args:
        print(json.dumps({"started": report.started, "ok": report.ok,
                          "reason": report.reason,
                          "steps": [asdict(item) for item in report.results]},
                         indent=2, sort_keys=True))
    else:
        print(report.render(), end="")
    if not report.started:
        return 2
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
