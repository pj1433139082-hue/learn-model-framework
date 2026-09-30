"""Public CLI checks using synthetic private workspaces only."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from model_lab.cli import main
from model_lab.codex_hook import handle_event
from model_lab.environment import framework_root


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


class OnboardingCliTests(unittest.TestCase):
    def create_workspace(self, root):
        code, stdout, stderr = invoke(
            "new", "synthetic-cli", "--name", "Synthetic CLI",
            "--owner", "example", "--models-root", str(root), "--apply",
            "--format", "json",
        )
        self.assertEqual(code, 0, stderr)
        return Path(json.loads(stdout)["destination"])

    def test_context_uses_real_framework_absolute_docs_and_records_creation_source(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.create_workspace(Path(directory) / "arbitrary-private-assets")
            code, stdout, stderr = invoke("context", str(workspace), "--format", "json")
            self.assertEqual(code, 0, stderr)
            record = json.loads(stdout)["models"][0]
            self.assertEqual(Path(record["framework"]["root"]), framework_root())
            for doc in record["doc_paths"]:
                self.assertTrue(Path(doc).is_absolute())
                self.assertTrue(Path(doc).is_file(), doc)
                self.assertTrue(Path(doc).is_relative_to(framework_root()))
            metadata = yaml.safe_load((workspace / "model-lab.yaml").read_text(encoding="utf-8"))
            self.assertEqual(metadata["framework"]["version"], record["framework"]["version"])
            self.assertIsInstance(metadata["framework"]["git_dirty"], bool)

    def test_task_cli_preview_save_restore_preserves_mode_not_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.create_workspace(Path(directory) / "private-assets")
            base = ("task-plan", str(workspace), "--task-id", "audit-example", "--format", "json")
            code, stdout, stderr = invoke(*base)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["status"], "not_recorded")
            self.assertFalse((workspace / ".model-lab").exists())
            changes = ("--mode", "assisted", "--intent", "data-audit", "--next-action",
                       "Inspect the synthetic inputs", "--pending", "data-policy")
            code, stdout, stderr = invoke(*base, *changes)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["status"], "planned")
            self.assertFalse((workspace / ".model-lab").exists())
            code, stdout, stderr = invoke(*base, *changes, "--apply")
            self.assertEqual(code, 0, stderr)
            stored = json.loads(stdout)["task"]
            code, stdout, stderr = invoke(*base)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["task"], stored)
            self.assertEqual(stored["mode"], "assisted")
            self.assertEqual(stored["pending_decision_ids"], ["data-policy"])
            self.assertNotIn("authorization", stored)

    def test_public_mutations_refuse_framework_workspace_before_read_or_build(self):
        forbidden = str(framework_root() / "not-a-private-workspace")
        commands = (
            ("new", "forbidden-cli", "--name", "Synthetic", "--owner", "example",
             "--models-root", forbidden, "--apply"),
            ("advance", forbidden, "--apply"),
            ("maintain", forbidden, "--version", "1.0.1", "--apply"),
            ("draft-fork", forbidden, "--version", "0.2.0", "--apply"),
            ("bundle-build", forbidden, "--sample-csv", "synthetic-missing.csv", "--apply"),
            ("context", forbidden, "--write"),
            ("task-plan", forbidden, "--task-id", "example-task", "--mode", "assisted",
             "--intent", "build", "--next-action", "Inspect synthetic inputs", "--apply"),
        )
        for command in commands:
            with self.subTest(command=command[0]):
                code, _, stderr = invoke(*command)
                self.assertNotEqual(code, 0)
                self.assertIn("outside", stderr)
                self.assertFalse(Path(forbidden).exists())

    def test_setup_cli_passes_apply_update_and_explains_skill_hook_fallback(self):
        result = {
            "status": "ready", "valid": True,
            "framework": {"root": "synthetic-framework", "version": "0.2.0",
                          "git_commit": "a" * 40, "git_dirty": False},
            "models_root": "synthetic-private-root", "next_action": "Run doctor",
            "skills": {"status": "direct_source_available"},
            "hooks": {"state": "trust_in_client_unverified"}, "update": None,
        }
        with patch("model_lab.cli.setup_workspace", return_value=result) as setup:
            code, stdout, stderr = invoke("setup", "--apply", "--update")
        self.assertEqual(code, 0, stderr)
        setup.assert_called_once_with(None, apply=True, update=True)
        self.assertIn("direct_source_available", stdout)
        self.assertIn("trust_in_client_unverified", stdout)

    def test_release_gate_defaults_to_loaded_framework_not_callers_directory(self):
        with patch("model_lab.cli.run_release_gate") as gate:
            gate.return_value.valid = True
            gate.return_value.as_dict.return_value = {"valid": True}
            code, _, stderr = invoke("release-gate", "--format", "json")
        self.assertEqual(code, 0, stderr)
        gate.assert_called_once_with(str(framework_root()))

    def test_hook_reuses_ignored_local_configuration_without_models_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private-assets"
            workspace = self.create_workspace(root)
            with patch.dict("os.environ", {}, clear=True), patch(
                "model_lab.environment.read_local_settings", return_value={"models_root": str(root)}
            ):
                response = handle_event(
                    {"hook_event_name": "UserPromptSubmit", "session_id": "synthetic-session",
                     "prompt": "检查 synthetic-cli 0.1.0"},
                    framework_root(), cache_root=Path(directory) / "session-cache",
                )
            context = response["hookSpecificOutput"]["additionalContext"]
            self.assertIn("synthetic-cli 0.1.0", context)
            self.assertIn(str(framework_root()), context)
            self.assertIn(str(framework_root() / "docs"), context)
            self.assertTrue((workspace / "model.yaml").is_file())
