from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "compact-current.py"


class CompactCurrentTest(unittest.TestCase):
    def test_oversized_history_keeps_newest_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "CURRENT.md"
            current.write_text("# Current\n\n# Newest\nkeep\n\n# Older\n" + "old\n" * 3000, encoding="utf-8")
            subprocess.run([sys.executable, str(SCRIPT), str(current)], check=True)
            result = current.read_text(encoding="utf-8")
            self.assertIn("# Newest\nkeep", result)
            self.assertNotIn("# Older", result)
            self.assertEqual(len(list((current.parent / "history").glob("*.md"))), 1)

    def test_small_active_checkpoint_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "CURRENT.md"
            current.write_text("# Current\n\n## Goal\nkeep exactly\n", encoding="utf-8")
            before = current.read_bytes()
            subprocess.run([sys.executable, str(SCRIPT), str(current)], check=True)
            self.assertEqual(current.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
