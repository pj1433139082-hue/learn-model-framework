from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from model_lab.cli import main


def invoke(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class PublicPathCliSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "redirect-target"
        self.target.mkdir()
        self.link = self.root / "redirect"
        self._is_junction = False

        try:
            self.link.symlink_to(self.target, target_is_directory=True)
        except OSError as symlink_error:
            if os.name != "nt":
                self.skipTest(
                    "directory symlink creation is unavailable: "
                    f"{symlink_error}"
                )
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell is None:
                self.skipTest(
                    "Windows directory symlink creation is unavailable and no "
                    "PowerShell executable is available to create a junction: "
                    f"{symlink_error}"
                )
            junction_environment = os.environ.copy()
            junction_environment["MODEL_LAB_TEST_LINK"] = str(self.link)
            junction_environment["MODEL_LAB_TEST_TARGET"] = str(self.target)
            junction = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "New-Item -ItemType Junction -Path $env:MODEL_LAB_TEST_LINK "
                    "-Target $env:MODEL_LAB_TEST_TARGET | Out-Null",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=junction_environment,
            )
            if junction.returncode != 0 or not self.link.exists():
                if self.link.exists():
                    os.rmdir(self.link)
                self.skipTest(
                    "directory symlink creation is unavailable and temporary "
                    "junction creation failed; "
                    f"symlink={symlink_error}; junction={junction.stderr.strip()}"
                )
            self._is_junction = True

        self.addCleanup(self._remove_link)

    def _remove_link(self) -> None:
        if self._is_junction:
            os.rmdir(self.link)
        elif self.link.is_symlink():
            self.link.unlink()

    def test_public_write_commands_reject_linked_workspace_ancestors(self) -> None:
        commands = (
            (
                "new",
                "synthetic-path-check",
                "--name",
                "Synthetic path check",
                "--owner",
                "tests",
                "--models-root",
                str(self.link / "models"),
                "--apply",
                "--format",
                "json",
            ),
            ("advance", str(self.link / "advance-workspace"), "--apply", "--format", "json"),
            (
                "maintain",
                str(self.link / "released-workspace"),
                "--version",
                "1.0.1",
                "--apply",
                "--format",
                "json",
            ),
            (
                "draft-fork",
                str(self.link / "draft-workspace"),
                "--version",
                "0.2.0",
                "--apply",
                "--format",
                "json",
            ),
            ("context", str(self.link / "context-workspace"), "--write", "--format", "json"),
            (
                "task-plan",
                str(self.link / "task-workspace"),
                "--task-id",
                "path-safety",
                "--mode",
                "assisted",
                "--intent",
                "build",
                "--next-action",
                "Inspect synthetic inputs",
                "--apply",
                "--format",
                "json",
            ),
            (
                "bundle-build",
                str(self.link / "bundle-workspace" / "model.yaml"),
                "--sample-csv",
                "synthetic-missing.csv",
                "--apply",
                "--format",
                "json",
            ),
        )

        for command in commands:
            with self.subTest(command=command[0]):
                code, stdout, stderr = invoke(*command)
                diagnostic = (stdout + stderr).casefold()
                self.assertNotEqual(code, 0)
                self.assertTrue(
                    "link" in diagnostic or "junction" in diagnostic,
                    diagnostic,
                )

        self.assertEqual(tuple(self.target.iterdir()), ())

    def test_cli_checks_link_before_parent_segment_can_hide_it(self) -> None:
        workspace = self.link / ".." / "outside-workspace"

        code, stdout, stderr = invoke(
            "context", str(workspace), "--write", "--format", "json"
        )

        diagnostic = (stdout + stderr).casefold()
        self.assertNotEqual(code, 0)
        self.assertTrue("link" in diagnostic or "junction" in diagnostic)
        self.assertFalse((self.root / "outside-workspace").exists())


if __name__ == "__main__":
    unittest.main()
