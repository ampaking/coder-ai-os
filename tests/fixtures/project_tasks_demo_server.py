#!/usr/bin/env python3
"""Deterministic rich-data Project Tasks server used only by responsive visual tests."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from coderai.project_tasks.demo import seed_demo
from coderai.project_tasks.server import serve

TOKEN = "val-capture-token-2026"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="project-tasks-demo-") as directory:
        project = Path(directory).resolve()
        seed_demo(project)
        serve(project, port=int(os.environ.get("PORT", "0")), launch_browser=False, access_token=TOKEN)


if __name__ == "__main__":
    main()
