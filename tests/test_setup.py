from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from model_lab.environment import (
    EnvironmentError,
    configured_models_root,
    ensure_external_workspace,
    read_local_settings,
)
from model_lab.setup import setup_workspace


SKILL_NAMES = (
    "model-build-validate",
    "model-data-audit",
    "model-lab-router",
    "model-lab-setup",
    "model-maintenance",
    "model-requirements-grill",
    "model-runtime-release",
)


class SetupHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.framework = self.base / "framework"
        self.framework.mkdir()
        self.models_root = self.base / "private-models"
        (self.framework / ".gitignore").write_text(
            ".model-lab.local.json\n.model-lab/\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "--quiet"], cwd=self.framework, check=True)
        source_root = self.framework / ".skillshare" / "skills"
        for name in SKILL_NAMES:
            skill = source_root / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                f"---\nname: {name}\n---\n\nProject source for {name}.\n",
                encoding="utf-8",
            )
        hook_directory = self.framework / ".codex" / "hooks"
        hook_directory.mkdir(parents=True)
        (self.framework / ".codex" / "hooks.json").write_text("{}\n", encoding="utf-8")
        (hook_directory / "context.py").write_text("# optional\n", encoding="utf-8")
        self.identity = {
            "version": "0.1.0",
            "root": str(self.framework),
            "module": str(self.framework / "model_lab" / "__init__.py"),
            "git_commit": "a" * 40,
            "git_branch": "codex/setup-test",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def patched_framework(self):  # type: ignore[no-untyped-def]
        return (
            patch("model_lab.environment.framework_root", return_value=self.framework),
            patch("model_lab.setup.framework_root", return_value=self.framework),
            patch(
                "model_lab.setup.framework_identity", side_effect=lambda: self.identity
            ),
        )

    def test_flag_environment_and_local_configuration_precedence(self) -> None:
        config_path = self.framework / ".model-lab.local.json"
        config_path.write_text(
            json.dumps({"version": 1, "models_root": str(self.base / "from-config")}),
            encoding="utf-8",
        )
        flag_path = self.base / "from-flag"
        environment_path = self.base / "from-environment"

        with self.patched_framework()[0], self.patched_framework()[1]:
            with patch.dict(
                os.environ, {"MODEL_LAB_MODELS_ROOT": str(environment_path)}
            ):
                self.assertEqual(
                    configured_models_root(str(flag_path)), flag_path.resolve()
                )
                self.assertEqual(configured_models_root(), environment_path.resolve())
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    configured_models_root(), (self.base / "from-config").resolve()
                )

    def test_invalid_flag_does_not_fall_back_to_environment(self) -> None:
        with self.patched_framework()[0], self.patched_framework()[1]:
            with patch.dict(
                os.environ,
                {"MODEL_LAB_MODELS_ROOT": str(self.base / "valid-environment")},
            ):
                with self.assertRaises(EnvironmentError) as raised:
                    configured_models_root(str(self.framework / "models"))
        self.assertEqual(raised.exception.code, "models_root_inside_framework")

    def test_local_settings_reject_secrets_unknown_keys_and_duplicate_keys(
        self,
    ) -> None:
        path = self.framework / ".model-lab.local.json"
        with self.patched_framework()[0]:
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "models_root": str(self.models_root),
                        "token": "must-not-be-accepted",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(EnvironmentError) as raised:
                read_local_settings(self.framework)
            self.assertEqual(raised.exception.code, "local_settings_invalid")

            path.write_text(
                '{"version":1,"models_root":"first","models_root":"second"}',
                encoding="utf-8",
            )
            with self.assertRaises(EnvironmentError) as duplicate:
                read_local_settings(self.framework)
            self.assertEqual(duplicate.exception.code, "local_settings_invalid")

    def test_existing_external_company_zip_is_preserved_as_a_file_path(self) -> None:
        company_zip = self.base / "company-package.zip"
        company_zip.write_bytes(b"local package placeholder")
        config_path = self.framework / ".model-lab.local.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "models_root": str(self.models_root),
                    "company_iot_zip": str(company_zip),
                }
            ),
            encoding="utf-8",
        )
        with self.patched_framework()[0], self.patched_framework()[1]:
            settings = read_local_settings(self.framework)
        self.assertEqual(settings["company_iot_zip"], str(company_zip.resolve()))

        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with patch("model_lab.setup.shutil.which", return_value=None):
                result = setup_workspace(apply=True)
        self.assertTrue(result["valid"])
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["company_iot_zip"], str(company_zip.resolve()))

    def test_workspace_accepts_manifest_and_rejects_framework_paths(self) -> None:
        manifest = self.models_root / "wind-model" / "model.yaml"
        with self.patched_framework()[0]:
            self.assertEqual(
                ensure_external_workspace(manifest), manifest.parent.resolve()
            )
            with self.assertRaises(EnvironmentError) as raised:
                ensure_external_workspace(self.framework / "workspace")
        self.assertEqual(raised.exception.code, "models_root_inside_framework")

    def test_workspace_rejects_symlink_ancestor(self) -> None:
        target = self.base / "real-local-parent"
        target.mkdir()
        link = self.base / "linked-parent"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks are unavailable: {error}")
        with self.patched_framework()[0]:
            with self.assertRaises(EnvironmentError) as raised:
                ensure_external_workspace(link / "new-workspace")
        self.assertEqual(raised.exception.code, "unsafe_workspace_path")

    def test_setup_plan_and_apply_are_idempotent_and_use_direct_source_fallback(
        self,
    ) -> None:
        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with patch("model_lab.setup.shutil.which", return_value=None):
                plan = setup_workspace(self.models_root, apply=False)
                self.assertEqual(plan["status"], "planned")
                self.assertFalse((self.framework / ".model-lab.local.json").exists())

                first = setup_workspace(self.models_root, apply=True)
                second = setup_workspace(self.models_root, apply=True)

        self.assertTrue(first["valid"])
        self.assertTrue(second["valid"])
        self.assertEqual(first["skills"]["status"], "direct_source_available")  # type: ignore[index]
        self.assertFalse((self.framework / ".agents").exists())
        self.assertFalse(
            self.models_root.exists(), "setup must not create a model root"
        )
        self.assertFalse((self.framework / "models").exists())
        self.assertEqual(first["hooks"]["state"], "trust_in_client_unverified")  # type: ignore[index]
        self.assertIsNone(first["hooks"]["enabled"])  # type: ignore[index]

        config_path = self.framework / ".model-lab.local.json"
        config_text = config_path.read_text(encoding="utf-8")
        config = json.loads(config_text)
        self.assertEqual(
            config, {"models_root": str(self.models_root.resolve()), "version": 1}
        )
        self.assertEqual(config_text, config_path.read_text(encoding="utf-8"))
        state = json.loads(
            (self.framework / ".model-lab" / "setup-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["last_setup_commit"], "a" * 40)

    def test_update_records_previous_and_current_commit_without_touching_model_root(
        self,
    ) -> None:
        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with patch("model_lab.setup.shutil.which", return_value=None):
                initial = setup_workspace(self.models_root, apply=True)
                self.assertTrue(initial["valid"])
                self.identity["git_commit"] = "b" * 40
                updated = setup_workspace(self.models_root, apply=True, update=True)
                repeated = setup_workspace(self.models_root, apply=True, update=True)

        self.assertTrue(updated["valid"])
        self.assertEqual(
            updated["update"],
            {
                "previous_commit": "a" * 40,
                "current_commit": "b" * 40,
                "status": "complete",
            },
        )
        self.assertFalse(self.models_root.exists())
        self.assertEqual(repeated["update"], updated["update"])
        state = json.loads(
            (self.framework / ".model-lab" / "setup-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["last_update"]["previous_commit"], "a" * 40)
        self.assertEqual(state["last_update"]["current_commit"], "b" * 40)
        self.assertEqual(state["last_update"]["status"], "complete")

    def test_failed_update_retry_keeps_the_last_successful_commit_baseline(
        self,
    ) -> None:
        original_run = subprocess.run

        def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
            if command[0] == "skillshare-test":
                if self.sync_should_succeed:
                    root = Path(kwargs["cwd"])
                    target_root = root / ".agents" / "skills"
                    target_root.mkdir(parents=True, exist_ok=True)
                    for name in SKILL_NAMES:
                        shutil.copytree(
                            root / ".skillshare" / "skills" / name,
                            target_root / name,
                            dirs_exist_ok=True,
                        )
                return subprocess.CompletedProcess(command, 0, "", "")
            return original_run(command, **kwargs)

        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with patch("model_lab.setup.shutil.which", return_value=None):
                initial = setup_workspace(self.models_root, apply=True)
            self.assertTrue(initial["valid"])
            self.identity["git_commit"] = "b" * 40
            self.sync_should_succeed = False
            with (
                patch("model_lab.setup.shutil.which", return_value="skillshare-test"),
                patch("model_lab.setup.subprocess.run", side_effect=fake_run),
            ):
                failed = setup_workspace(self.models_root, apply=True, update=True)

            failed_state = json.loads(
                (self.framework / ".model-lab" / "setup-state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(failed_state["last_setup_commit"], "a" * 40)
            self.assertEqual(
                failed_state["last_update"],
                {
                    "previous_commit": "a" * 40,
                    "current_commit": "b" * 40,
                    "status": "partial",
                },
            )

            self.sync_should_succeed = True
            with (
                patch("model_lab.setup.shutil.which", return_value="skillshare-test"),
                patch("model_lab.setup.subprocess.run", side_effect=fake_run),
            ):
                retried = setup_workspace(self.models_root, apply=True, update=True)

        self.assertFalse(failed["valid"])
        self.assertEqual(failed["update"]["status"], "partial")  # type: ignore[index]
        self.assertTrue(retried["valid"])
        self.assertEqual(
            retried["update"],
            {
                "previous_commit": "a" * 40,
                "current_commit": "b" * 40,
                "status": "complete",
            },
        )
        final_state = json.loads(
            (self.framework / ".model-lab" / "setup-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(final_state["last_setup_commit"], "b" * 40)
        self.assertEqual(final_state["last_update"]["previous_commit"], "a" * 40)
        self.assertEqual(final_state["last_update"]["current_commit"], "b" * 40)
        self.assertEqual(final_state["last_update"]["status"], "complete")
        self.assertFalse(self.models_root.exists())

    def test_skillshare_apply_verifies_target_hashes(self) -> None:
        original_run = subprocess.run

        def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
            if command[0] == "skillshare-test":
                root = Path(kwargs["cwd"])
                target_root = root / ".agents" / "skills"
                target_root.mkdir(parents=True)
                for name in SKILL_NAMES:
                    shutil.copytree(
                        root / ".skillshare" / "skills" / name,
                        target_root / name,
                    )
                return subprocess.CompletedProcess(command, 0, "", "")
            return original_run(command, **kwargs)

        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with (
                patch("model_lab.setup.shutil.which", return_value="skillshare-test"),
                patch("model_lab.setup.subprocess.run", side_effect=fake_run),
            ):
                result = setup_workspace(self.models_root, apply=True)

        self.assertTrue(result["valid"])
        self.assertEqual(result["skills"]["status"], "synced_verified")  # type: ignore[index]
        self.assertTrue(result["skills"]["verified"])  # type: ignore[index]

    def test_skillshare_success_without_matching_target_is_not_claimed_as_synced(
        self,
    ) -> None:
        original_run = subprocess.run

        def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
            if command[0] == "skillshare-test":
                return subprocess.CompletedProcess(command, 0, "", "")
            return original_run(command, **kwargs)

        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with (
                patch("model_lab.setup.shutil.which", return_value="skillshare-test"),
                patch("model_lab.setup.subprocess.run", side_effect=fake_run),
            ):
                result = setup_workspace(self.models_root, apply=True)

        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["skills"]["status"], "sync_failed_source_available")  # type: ignore[index]

    def test_local_configuration_must_be_ignored_before_apply(self) -> None:
        (self.framework / ".gitignore").write_text(".model-lab/\n", encoding="utf-8")
        with (
            self.patched_framework()[0],
            self.patched_framework()[1],
            self.patched_framework()[2],
        ):
            with patch("model_lab.setup.shutil.which", return_value=None):
                result = setup_workspace(self.models_root, apply=True)
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "local_paths_not_ignored")
        self.assertFalse((self.framework / ".model-lab.local.json").exists())


if __name__ == "__main__":
    unittest.main()
