"""Synthetic checks for private roots, task plans, and path-safe state writes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from model_lab import environment
from model_lab.draft_upgrade import fork_draft_contract, validate_draft_upgrade_lineage
from model_lab.maintenance import (
    fork_maintenance,
    validate_maintenance_lineage,
)
from model_lab.path_safety import is_link_or_junction
from model_lab.scaffolding import ScaffoldRequest, WorkspaceError, scaffold_model
from model_lab.task_plan import TaskPlanError, read_task_plan, write_task_plan
from model_lab.workflow_state import (
    SNAPSHOT_NAME,
    build_snapshot,
    task_context,
    write_publication_receipt,
    write_snapshot,
)


@dataclass(frozen=True)
class _ValidManifest:
    valid: bool = True
    issues: tuple[object, ...] = ()
    model_id: str = "external-state-model"


def _scaffold(models_root: Path, model_id: str = "external-state-model") -> Path:
    report = scaffold_model(
        ScaffoldRequest(model_id=model_id, name="External State Model", owner="tests"),
        models_root,
        apply=True,
    )
    return Path(report.destination)


def _write_yaml(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _released_parent(models_root: Path) -> tuple[Path, bytes, bytes, bytes]:
    draft = _scaffold(models_root)
    parent = models_root / "external-state-model"
    draft.rename(parent)
    input_bytes = b'{"flow_m3h": 1}\n'
    expected_bytes = b'{"result": 1}\n'
    input_path = parent / "baselines" / "input.json"
    expected_path = parent / "baselines" / "expected.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(input_bytes)
    expected_path.write_bytes(expected_bytes)
    snapshot_bytes = b"release evidence retained verbatim\n"
    (parent / "releases" / "current.yaml").parent.mkdir(parents=True, exist_ok=True)
    (parent / "releases" / "current.yaml").write_bytes(snapshot_bytes)
    manifest = yaml.safe_load((parent / "model.yaml").read_text(encoding="utf-8"))
    manifest["model"]["version"] = "1.0.0"
    manifest["model"]["lifecycle_state"] = "released"
    manifest["contract"] = {
        "inputs": [{"name": "flow_m3h", "type": "number", "unit": "m3/h"}],
        "outputs": [{"name": "result", "type": "number", "unit": "dimensionless"}],
    }
    manifest["baseline"] = {
        "digest_mode": "utf8-lf",
        "kind": "synthetic-test-baseline",
        "input": "baselines/input.json",
        "input_sha256": hashlib.sha256(input_bytes.replace(b"\r\n", b"\n")).hexdigest(),
        "expected": "baselines/expected.json",
        "expected_sha256": hashlib.sha256(expected_bytes.replace(b"\r\n", b"\n")).hexdigest(),
    }
    manifest["release"] = {"current": "releases/current.yaml"}
    _write_yaml(parent / "model.yaml", manifest)
    return parent, input_bytes, expected_bytes, snapshot_bytes


class ExternalStateTests(unittest.TestCase):
    def test_maintenance_and_draft_fork_accept_arbitrary_external_root_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-private-assets-") as directory:
            root = Path(directory) / "private-assets-2026"
            parent, input_bytes, expected_bytes, snapshot_bytes = _released_parent(root)
            before_manifest = (parent / "model.yaml").read_bytes()
            before_snapshot = (parent / "releases" / "current.yaml").read_bytes()

            with patch("model_lab.maintenance.validate_manifest", return_value=_ValidManifest()):
                patch_report = fork_maintenance(parent, "1.0.1", apply=True)
                self.assertEqual(patch_report.status, "created")
                valid, message, lineage = validate_maintenance_lineage(Path(patch_report.destination))
            self.assertTrue(valid, message)
            self.assertEqual(lineage, parent / "model.yaml")
            patch_manifest = yaml.safe_load(
                (Path(patch_report.destination) / "model.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(patch_manifest["model"]["version"], "1.0.1")
            self.assertEqual(
                (Path(patch_report.destination) / "baselines/base/input.json").read_bytes(),
                input_bytes,
            )
            self.assertEqual(
                (Path(patch_report.destination) / "baselines/base/expected.json").read_bytes(),
                expected_bytes,
            )
            self.assertEqual((parent / "model.yaml").read_bytes(), before_manifest)
            self.assertEqual((parent / "releases/current.yaml").read_bytes(), before_snapshot)
            self.assertEqual(before_snapshot, snapshot_bytes)

            with patch("model_lab.maintenance.validate_manifest", return_value=_ValidManifest()):
                upgrade_report = fork_maintenance(
                    parent, "1.1.0", change_kind="contract-upgrade", apply=True
                )
                valid, message, _ = validate_maintenance_lineage(Path(upgrade_report.destination))
            self.assertTrue(valid, message)
            upgrade = Path(upgrade_report.destination)
            self.assertEqual((upgrade / "baselines/base/input.json").read_bytes(), input_bytes)
            self.assertNotIn("baseline:", (upgrade / "model.yaml").read_text(encoding="utf-8"))

            (parent / "model.yaml").write_bytes(before_manifest)
            document = yaml.safe_load(before_manifest)
            document["serving"] = {"mode": "point-code", "binding": "input-bindings.yaml"}
            _write_yaml(parent / "model.yaml", document)
            with patch("model_lab.maintenance.validate_manifest", return_value=_ValidManifest()):
                with self.assertRaises(WorkspaceError) as unsupported:
                    fork_maintenance(parent, "1.0.2")
            self.assertEqual(unsupported.exception.code, "maintenance_serving_unsupported")

    def test_draft_contract_fork_accepts_arbitrary_external_root_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-draft-assets-") as directory:
            root = Path(directory) / "private-registry"
            parent = root / "_drafts" / "external-state-model"
            parent.mkdir(parents=True)
            (parent / "decisions.yaml").write_text(
                yaml.safe_dump({"decisions": []}, sort_keys=False), encoding="utf-8"
            )
            (parent / "model-lab.yaml").write_text("schema_version: '1.0'\n", encoding="utf-8")
            (parent / "model-spec.md").write_text("# Parent draft\n", encoding="utf-8")
            source = b"def predict(value):\n    return value\n"
            tests = b"def test_predict():\n    assert True\n"
            (parent / "src").mkdir()
            (parent / "tests").mkdir()
            (parent / "src/model.py").write_bytes(source)
            (parent / "tests/test_model.py").write_bytes(tests)
            document = {
                "model": {
                    "id": "external-state-model", "name": "External State Model",
                    "owner": "tests", "version": "1.0.0", "lifecycle_state": "releasable",
                },
                "target": {"kind": "cpu-iot"},
                "contract": {
                    "inputs": [{"name": "flow_m3h", "type": "number", "unit": "m3/h"}],
                    "outputs": [{"name": "result", "type": "number", "unit": "dimensionless"}],
                },
                "source": {"entrypoint_sha256": hashlib.sha256(source).hexdigest()},
            }
            _write_yaml(parent / "model.yaml", document)
            digests = {name: f"digest-{index}" for index, name in enumerate((
                "model.yaml", "model-lab.yaml", "decisions.yaml", "model-spec.md",
                "validation/validation-report.md", "notebooks/model.ipynb",
                "runtime/requirements.lock.txt", "src/model.py", "tests/test_model.py",
            ))}
            for relative in (
                "validation/validation-report.md", "notebooks/model.ipynb",
                "runtime/requirements.lock.txt",
            ):
                path = parent / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")
            base_files = {
                name: (parent / name).read_text(encoding="utf-8")
                for name in ("model-lab.yaml", "validation/validation-report.md", "notebooks/model.ipynb", "runtime/requirements.lock.txt")
            }
            digests = {
                **digests,
                "model.yaml": "synthetic-manifest-digest",
                "model-lab.yaml": "synthetic-metadata-digest",
            }
            with patch("model_lab.draft_upgrade._verified_parent", return_value=(document, digests)):
                report = fork_draft_contract(parent, "1.1.0", apply=True)
                valid, message, _ = validate_draft_upgrade_lineage(Path(report.destination))
            self.assertEqual(report.status, "created")
            self.assertTrue(valid, message)
            child = Path(report.destination)
            self.assertEqual((child / "baselines/base/model.py").read_bytes(), source)
            self.assertEqual((child / "baselines/base/test_model.py").read_bytes(), tests)
            self.assertEqual((parent / "src/model.py").read_bytes(), source)
            self.assertEqual(base_files["model-lab.yaml"], (parent / "model-lab.yaml").read_text(encoding="utf-8"))

    def test_task_plan_is_scoped_minimal_and_read_does_not_create(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-task-plan-") as directory:
            root = Path(directory) / "archive-of-models"
            workspace = _scaffold(root)
            self.assertIsNone(read_task_plan(workspace, "turn-2026-10-01"))
            self.assertFalse((workspace / ".model-lab").exists())

            path = write_task_plan(
                workspace,
                "turn-2026-10-01",
                mode="assisted",
                intent="maintain",
                next_action="Compare the released baseline and current online signature.",
                pending_decisions=("maintenance-behavior-scope", "validation-outcome"),
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(value), {
                "schema_version", "task_id", "mode", "intent", "next_action",
                "pending_decision_ids", "model", "framework",
            })
            self.assertEqual(value["model"], {"id": "external-state-model", "version": "0.1.0"})
            self.assertEqual(set(value["framework"]), {"commit", "version", "dirty"})
            self.assertEqual(value["pending_decision_ids"], [
                "maintenance-behavior-scope", "validation-outcome",
            ])
            self.assertEqual(read_task_plan(workspace, "turn-2026-10-01"), value)
            serialized = path.read_text(encoding="utf-8").casefold()
            for forbidden in ("authorization", "secret", "token", "artifact_uri", "git_branch", "module", "root"):
                self.assertNotIn(forbidden, serialized)
            self.assertTrue(path.is_relative_to(workspace))

            waiting_path = write_task_plan(
                workspace,
                "turn-data-audit",
                mode="manual-review",
                intent="data-audit",
                next_action="Wait for the user's authorization before any upload.",
            )
            self.assertEqual(read_task_plan(workspace, "turn-data-audit")["mode"], "manual-review")
            auto_path = write_task_plan(
                workspace,
                "turn-full-auto",
                mode="full-auto",
                intent="new",
                next_action="Build and validate the local draft.",
            )
            self.assertEqual(read_task_plan(workspace, "turn-full-auto")["mode"], "full-auto")
            self.assertNotEqual(waiting_path, auto_path)
            waiting_path.write_text('{"schema_version":"1.0","schema_version":"1.0"}\n', encoding="utf-8")
            with self.assertRaises(TaskPlanError):
                read_task_plan(workspace, "turn-data-audit")

    def test_task_plan_rejects_unsafe_fields_and_link_redirects(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-task-plan-unsafe-") as directory:
            root = Path(directory) / "separate-models-root"
            workspace = _scaffold(root)
            invalid_arguments = (
                {"task_id": "../escape", "mode": "assisted", "intent": "maintain", "next_action": "Review baseline"},
                {"task_id": "task-a", "mode": "silent", "intent": "maintain", "next_action": "Review baseline"},
                {"task_id": "task-b", "mode": "assisted", "intent": "retire", "next_action": "Review baseline"},
                {"task_id": "task-c", "mode": "assisted", "intent": "maintain", "next_action": "Use API key: abc123"},
                {"task_id": "task-d", "mode": "assisted", "intent": "maintain", "next_action": "Keep token: synthetic-example private"},
                {"task_id": "task-e", "mode": "assisted", "intent": "maintain", "next_action": "Use auth_token=synthetic-example"},
            )
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments), self.assertRaises(TaskPlanError):
                    task_id = arguments.pop("task_id")
                    write_task_plan(workspace, task_id, **arguments)
            self.assertFalse((workspace / ".model-lab").exists())

            external = Path(directory) / "redirect-target"
            external.mkdir()
            metadata = workspace / ".model-lab"
            def link_detector(path: Path) -> bool:
                return Path(path) == metadata or is_link_or_junction(Path(path))

            with patch("model_lab.scaffolding.is_link_or_junction", side_effect=link_detector):
                with self.assertRaises(ValueError):
                    write_task_plan(
                        workspace, "task-link", mode="assisted", intent="maintain",
                        next_action="Review the baseline.",
                    )
            self.assertFalse((external / "tasks").exists())

    def test_context_identity_stays_out_of_hash_pinned_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="model-lab-context-") as directory:
            workspace = _scaffold(Path(directory) / "private-context-root")
            context = task_context(workspace, task="maintain")
            self.assertEqual(context["docs"][0], "docs/00-入口层/00-模型开发总览.md")
            framework = context["framework"]
            self.assertEqual(Path(framework["root"]).resolve(), environment.framework_root().resolve())
            self.assertEqual(len(context["docs"]), len(context["doc_paths"]))
            self.assertTrue(all(Path(path).is_absolute() for path in context["doc_paths"]))
            snapshot = build_snapshot(workspace)
            serialized = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn(str(environment.framework_root()), serialized)
            self.assertNotIn("git_commit", serialized)

    def test_snapshot_and_publication_receipt_writes_reject_framework_workspaces(self) -> None:
        internal = Path(__file__).resolve().parent / "fixtures" / "minimal-model" / "model.yaml"
        with self.assertRaises(ValueError):
            write_snapshot(internal)
        with self.assertRaises(ValueError):
            write_publication_receipt(
                internal,
                experiment="EXAMPLE_V001",
                experiment_id="1",
                run_id="0123456789abcdef0123456789abcdef",
                owner="test",
                bundle_sha256="0" * 64,
                plan_id="test-plan",
            )
        self.assertFalse((internal.parent / SNAPSHOT_NAME).exists())

    def test_maintenance_and_draft_fork_reject_framework_workspaces(self) -> None:
        internal = Path(__file__).resolve().parent / "fixtures" / "minimal-model"
        with self.assertRaises(ValueError):
            fork_maintenance(internal, "1.0.1")
        with self.assertRaises(ValueError):
            fork_draft_contract(internal, "1.1.0")
        with self.assertRaises(ValueError):
            write_task_plan(
                internal, "internal-task", mode="assisted", intent="status",
                next_action="Inspect the framework fixture.",
            )


if __name__ == "__main__":
    unittest.main()
