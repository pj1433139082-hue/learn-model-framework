"""Synthetic, public-entry tests for a first-time framework user."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_lab.cli import main


class FrameworkFlowTests(unittest.TestCase):
    def _call(self, *arguments: str) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = main(arguments)
        return result, output.getvalue(), errors.getvalue()

    def test_new_requires_explicit_private_root(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            code, _, errors = self._call(
                "new", "example-flow", "--name", "Example", "--owner", "example",
            )
        self.assertEqual(code, 2)
        self.assertIn("MODEL_LAB_MODELS_ROOT", errors)

    def test_new_rejects_framework_checkout_as_models_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        code, _, errors = self._call(
            "new", "example-flow", "--name", "Example", "--owner", "example",
            "--models-root", str(root / "models"), "--apply", "--format", "json",
        )
        self.assertEqual(code, 2)
        self.assertIn("models_root_inside_framework", errors)
        self.assertFalse((root / "models").exists())

    def test_external_private_root_new_context_and_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-external-") as directory:
            root = Path(directory) / "models"
            code, output, errors = self._call(
                "new", "example-flow", "--name", "Example Flow", "--owner", "example",
                "--models-root", str(root), "--format", "json",
            )
            self.assertEqual((code, errors), (0, ""))
            planned = json.loads(output)
            self.assertEqual(planned["status"], "planned")
            self.assertFalse(root.exists())

            code, output, errors = self._call(
                "new", "example-flow", "--name", "Example Flow", "--owner", "example",
                "--models-root", str(root), "--apply", "--format", "json",
            )
            self.assertEqual((code, errors), (0, ""))
            created = json.loads(output)
            self.assertEqual(created["status"], "created")
            manifest = root / "_drafts" / "example-flow" / "model.yaml"
            self.assertTrue(manifest.is_file())

            code, output, errors = self._call("context", str(manifest.parent), "--format", "json")
            self.assertEqual((code, errors), (0, ""))
            context = json.loads(output)
            self.assertTrue(context["valid"])
            self.assertEqual(context["models"][0]["model_id"], "example-flow")

            code, _, errors = self._call("status", str(manifest.parent), "--format", "json")
            self.assertEqual((code, errors), (0, ""))

    def test_context_all_requires_external_root(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            code, _, errors = self._call("context", "--all")
        self.assertEqual(code, 2)
        self.assertIn("MODEL_LAB_MODELS_ROOT", errors)


if __name__ == "__main__":
    unittest.main()
