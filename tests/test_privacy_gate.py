"""Public release-gate tests with deliberately synthetic violations."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from model_lab.release_gate import run_release_gate


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)


class PrivacyGateTests(unittest.TestCase):
    def test_staged_private_model_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _git(root, "init", "-b", "main")
            private = root / "models" / "private" / "model.yaml"
            private.parent.mkdir(parents=True)
            private.write_text("model: example\n", encoding="utf-8")
            _git(root, "add", "--force", "models/private/model.yaml")
            report = run_release_gate(root)
            self.assertFalse(report.valid)
            self.assertIn("shared_path", {issue.rule for issue in report.issues})

    def test_old_history_is_rejected_even_after_private_file_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _git(root, "init", "-b", "main")
            readme = root / "README.md"
            readme.write_text("A synthetic framework\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _git(root, "-c", "user.name=Example", "-c", "user.email=example@example.invalid", "commit", "-m", "initial")
            hidden = root / "models" / "private" / "model.yaml"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("private model\n", encoding="utf-8")
            _git(root, "add", "--force", "models/private/model.yaml")
            _git(root, "-c", "user.name=Example", "-c", "user.email=example@example.invalid", "commit", "-m", "private")
            _git(root, "rm", "models/private/model.yaml")
            _git(root, "-c", "user.name=Example", "-c", "user.email=example@example.invalid", "commit", "-m", "removed")
            report = run_release_gate(root)
            self.assertFalse(report.valid)
            self.assertIn("history_path", {issue.rule for issue in report.issues})

    def test_real_looking_point_id_in_staged_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _git(root, "init", "-b", "main")
            (root / "README.md").write_text("example: " + "PS" + "0000" + "-POINT\n", encoding="utf-8")
            _git(root, "add", "README.md")
            report = run_release_gate(root)
            self.assertIn("shared_content", {issue.rule for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
