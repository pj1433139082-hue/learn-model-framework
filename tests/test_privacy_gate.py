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


def _init_repository(root: Path) -> None:
    _git(root, "init", "-b", "main")


def _commit(root: Path, message: str) -> None:
    _git(
        root, "-c", "user.name=Example", "-c",
        "user.email=example@example.invalid", "commit", "-m", message,
    )


def _synthetic_credential_line() -> str:
    name = "api" + "_key"
    value = "synthetic-" + ("x" * 20)
    return f'{name} = "{value}"\n'


def _check_status(report, name: str) -> str:
    return next(check.status for check in report.checks if check.name == name)


class PrivacyGateTests(unittest.TestCase):
    def test_staged_private_model_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            private = root / "models" / "private" / "model.yaml"
            private.parent.mkdir(parents=True)
            private.write_text("model: example\n", encoding="utf-8")
            _git(root, "add", "--force", "models/private/model.yaml")
            report = run_release_gate(root)
            self.assertFalse(report.valid)
            self.assertIn("index_path", {issue.rule for issue in report.issues})

    def test_staged_index_blob_is_scanned_when_worktree_is_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            readme = root / "README.md"
            readme.write_text("Synthetic framework\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "initial")

            unusual = root / "docs" / "review notes [synthetic].md"
            unusual.parent.mkdir()
            unusual.write_text(_synthetic_credential_line(), encoding="utf-8")
            _git(root, "add", "--", "docs/review notes [synthetic].md")
            unusual.write_text("safe reviewed content\n", encoding="utf-8")

            report = run_release_gate(root)
            self.assertIn("index_content", {issue.rule for issue in report.issues})
            self.assertEqual(_check_status(report, "shared_index_allowlist"), "failed")
            self.assertEqual(_check_status(report, "shared_worktree_allowlist"), "passed")
            self.assertEqual(_check_status(report, "complete_git_history"), "passed")

    def test_staged_safe_blob_does_not_hide_worktree_credential(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            readme = root / "README.md"
            readme.write_text("safe reviewed content\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "initial")
            readme.write_text(_synthetic_credential_line(), encoding="utf-8")

            report = run_release_gate(root)
            self.assertIn("worktree_content", {issue.rule for issue in report.issues})
            self.assertEqual(_check_status(report, "shared_index_allowlist"), "passed")
            self.assertEqual(_check_status(report, "shared_worktree_allowlist"), "failed")
            self.assertEqual(_check_status(report, "complete_git_history"), "passed")

    def test_old_history_is_rejected_even_after_private_file_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            readme = root / "README.md"
            readme.write_text("A synthetic framework\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "initial")
            hidden = root / "models" / "private" / "model.yaml"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("private model\n", encoding="utf-8")
            _git(root, "add", "--force", "models/private/model.yaml")
            _commit(root, "private")
            _git(root, "rm", "models/private/model.yaml")
            _commit(root, "removed")

            report = run_release_gate(root)
            self.assertFalse(report.valid)
            self.assertIn("history_path", {issue.rule for issue in report.issues})

    def test_merge_only_private_path_is_found_after_exact_rename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            readme = root / "README.md"
            readme.write_text("base\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "initial")

            _git(root, "checkout", "-b", "side")
            readme.write_text("base\nside change\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "side change")

            _git(root, "checkout", "main")
            notes = root / "docs" / "notes.md"
            notes.parent.mkdir()
            notes.write_text("main change\n", encoding="utf-8")
            _git(root, "add", "docs/notes.md")
            _commit(root, "main change")
            _git(root, "merge", "--no-commit", "--no-ff", "side")

            hidden = root / "models" / "private" / "model.yaml"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("safe synthetic text\n", encoding="utf-8")
            _git(root, "add", "--force", "models/private/model.yaml")
            _commit(root, "resolve merge")
            _git(root, "mv", "models/private/model.yaml", "docs/review.md")
            _commit(root, "rename review document")

            report = run_release_gate(root)
            self.assertEqual(_check_status(report, "shared_index_allowlist"), "passed")
            self.assertEqual(_check_status(report, "shared_worktree_allowlist"), "passed")
            self.assertIn(
                ("history_path", "models/private/model.yaml"),
                {(issue.rule, issue.subject) for issue in report.issues},
            )

    def test_untracked_generated_private_config_is_not_blanket_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="framework-gate-") as directory:
            root = Path(directory)
            _init_repository(root)
            readme = root / "README.md"
            readme.write_text("A synthetic framework\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _commit(root, "initial")
            local_config = root / "models" / "cache" / "runtime-local.ini"
            local_config.parent.mkdir(parents=True)
            local_config.write_text("generated locally\n", encoding="utf-8")

            report = run_release_gate(root)
            self.assertTrue(report.valid, report.as_text())


if __name__ == "__main__":
    unittest.main()
