from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "val-post-edit.py"


class ValPostEditTest(unittest.TestCase):
    def test_only_matching_ui_edit_marks_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            app = project / ".coder-ai/val/apps/admin"
            app.mkdir(parents=True)
            (app / "config.json").write_text(json.dumps({
                "watchGlobs": ["packages/admin/**/*.{tsx,css}"],
            }), encoding="utf-8")
            for path, expected in (("api/service.py", False), ("packages/admin/page.tsx", True)):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT)], cwd=project,
                    input=json.dumps({"tool_input": {"file_path": path}}), text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual((app / "ui-validation.pending").exists(), expected)

    def test_malformed_input_is_fail_open(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], input="not-json", text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
