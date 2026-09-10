"""Entry point: `python -m coderai.pr_automation`.

Deliberately a separate module. Running `python -m coderai.pr_automation.cli`
executes cli.py a second time as `__main__`, producing duplicate class objects —
which silently broke dispatch. Importing it here keeps one identity.
"""

from __future__ import annotations

import sys

from coderai.pr_automation.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
