"""Recovery instructions stay tied to the actual framework, not a PATH alias."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from model_lab.cli import main
from model_lab.environment import framework_root


class RecoveryLauncherTests(unittest.TestCase):
    def test_incomplete_creation_provides_executable_isolated_recovery_arguments(self):
        with tempfile.TemporaryDirectory(prefix="model-lab-recovery-launcher-") as directory:
            root = Path(directory) / "private's assets $not_expanded"
            original_replace = os.replace

            def fail_index(source, destination):
                if Path(destination).name == "workflow-state.json":
                    raise OSError("synthetic index I/O failure")
                return original_replace(source, destination)

            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("os.replace", side_effect=fail_index), redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([
                    "new", "recovery-launcher", "--name", "Synthetic Recovery Launcher",
                    "--owner", "example", "--models-root", str(root), "--apply", "--format", "json",
                ])
            self.assertEqual(code, 2)
            result = json.loads(stdout.getvalue())
            command = result["recovery"]["command"]
            script = framework_root() / "scripts" / ("model-lab.ps1" if os.name == "nt" else "model-lab.sh")
            self.assertIn(str(script), command)
            self.assertTrue(script.is_file())
            self.assertIn(result["destination"], command)
            if os.name == "nt":
                self.assertIn("private''s assets $not_expanded", stderr.getvalue())
            # Execute the exact argv list, with no shell string or PATH model-lab alias.
            recovery = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(recovery.returncode, 0, recovery.stderr)
            payload = json.loads(recovery.stdout)
            self.assertTrue(payload["valid"])
            self.assertTrue(payload["models"][0]["snapshot_in_sync"])
            self.assertEqual(len(list(root.rglob("model.yaml"))), 1)
