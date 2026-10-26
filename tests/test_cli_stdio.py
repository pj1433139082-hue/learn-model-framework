from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CliStdioTests(unittest.TestCase):
    def test_cli_forces_utf8_output_under_gbk_console(self) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "gbk"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "from model_lab.cli import _configure_utf8_stdio; "
                    "_configure_utf8_stdio(); print('m³/日')"
                ),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("m³/日", result.stdout)


if __name__ == "__main__":
    unittest.main()
