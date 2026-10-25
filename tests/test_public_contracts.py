"""Public-framework contracts that keep onboarding docs aligned with shipped code."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from unittest.mock import patch

from model_lab.test_profiles import run_test_profile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")


class PublicDocsContractTests(unittest.TestCase):
    def test_entry_docs_declare_private_only_checks(self) -> None:
        for relative in (
            "docs/00-入口层/03-模型任务分流与加载矩阵.md",
            "docs/00-入口层/07-模型状态快照与Hook.md",
        ):
            with self.subTest(relative=relative):
                text = _read(relative)
                self.assertIn("本仓是公开框架分发仓", text)
                self.assertIn("step-check", text)

    def test_setup_skill_requires_external_models_root(self) -> None:
        text = _read(".skillshare/skills/model-lab-setup/SKILL.md")
        self.assertIn("models_root_inside_framework", text)
        self.assertNotIn("唯一合法根是本仓库", text)

    def test_public_docs_do_not_use_inside_repo_models_root(self) -> None:
        for relative in (
            "docs/00-入口层/03-模型任务分流与加载矩阵.md",
            "docs/00-入口层/08-框架全景流程与发布核对清单.md",
        ):
            with self.subTest(relative=relative):
                text = _read(relative)
                self.assertNotIn("models-root models", text)
                self.assertNotIn("仓库内私有模型根", text)

    def test_adr_0050_is_marked_private_source_only(self) -> None:
        text = _read("docs/adr/0050-私有模型根限定在本仓库.md")
        self.assertIn("私有源仓", text)
        self.assertNotIn("唯一合法根是本仓库", text)

    def test_generated_cache_is_gitignored(self) -> None:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "check-ignore",
                "--quiet",
                "--no-index",
                ".cache/skillshare/version-check.json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, ".cache/ must be Git-ignored")


class LocalProfilePreflightTests(unittest.TestCase):
    def test_local_profile_blocks_when_tempdir_is_unwritable(self) -> None:
        with patch(
            "model_lab.test_profiles.tempfile.TemporaryDirectory",
            side_effect=PermissionError("denied"),
        ):
            report = run_test_profile("local", suite=unittest.TestSuite())
        self.assertEqual(report.status, "blocked")
        self.assertTrue(
            any("temp" in blocker.lower() for blocker in report.blockers),
            report.blockers,
        )
