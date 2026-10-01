"""Public lifecycle commands do not replay facts when their index cannot be saved."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from model_lab.cli import main
from tests.test_lifecycle import complete_build_evidence, complete_specification_evidence


def invoke(*arguments):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def create_draft(root, model_id):
    code, output, error = invoke("new", model_id, "--name", "Synthetic Lifecycle Recovery",
                                 "--owner", "example", "--models-root", str(root), "--apply", "--format", "json")
    if code:
        raise AssertionError(error)
    return Path(json.loads(output)["destination"])


class LifecycleRecordRecoveryTests(unittest.TestCase):
    def assert_index_failure_and_recovery(self, command, root):
        original_replace = os.replace
        attempts = []

        def fail_index(source, destination):
            if Path(destination).name == "workflow-state.json":
                attempts.append(Path(destination))
                raise OSError("synthetic persistent lifecycle index I/O failure")
            return original_replace(source, destination)

        with patch("os.replace", side_effect=fail_index):
            code, output, error = invoke(*command, "--apply", "--format", "json")
        self.assertEqual(code, 2, error)
        self.assertTrue(output, error)
        result = json.loads(output)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result["completion"], {"operation": "completed", "local_record": "incomplete", "repeat_operation": False})
        self.assertEqual(result["local_record"]["snapshot_attempts"], 2)
        self.assertEqual(result["recovery"]["action"], "refresh_snapshot")
        self.assertIn("不要重复创建、推进或上传", error)
        workspace = Path(result.get("destination") or result["manifest"]).resolve()
        if workspace.name == "model.yaml":
            workspace = workspace.parent
        manifest = workspace / "model.yaml"
        committed = manifest.read_bytes()
        inventory = sorted(str(path) for path in root.rglob("model.yaml"))
        code, output, error = invoke("context", str(workspace), "--write", "--format", "json")
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)["models"][0]["snapshot_in_sync"])
        self.assertEqual(manifest.read_bytes(), committed)
        self.assertEqual(sorted(str(path) for path in root.rglob("model.yaml")), inventory)
        return result, workspace

    def test_maintenance_fork_preserves_parent_when_index_writes_fail(self):
        with tempfile.TemporaryDirectory(prefix="model-lab-maintenance-recovery-") as directory:
            root = Path(directory) / "private-assets"
            fixture = Path(__file__).parent / "fixtures" / "minimal-deployable" / "model.yaml"
            document = yaml.safe_load(fixture.read_text(encoding="utf-8"))
            document["model"]["version"] = "1.0.0"
            document["model"]["lifecycle_state"] = "released"
            document["model"]["maintenance_status"] = "active"
            document["release"] = {"current": "releases/current.yaml"}
            parent = root / document["model"]["id"]
            (parent / "releases").mkdir(parents=True)
            source_text = "Synthetic archived asset; not a deployed model.\n"
            (parent / "source.txt").write_text(source_text, encoding="utf-8")
            digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            document["source"] = {"digest_mode": "utf8-lf", "entrypoint": "source.txt", "entrypoint_sha256": digest,
                                  "notebook": "source.txt", "notebook_sha256": digest,
                                  "documentation": "source.txt", "documentation_sha256": digest}
            document["platform"] = {"provider": "mlflow", "experiment": "MLModel-1_V001",
                                    "run_id": "1" * 32, "deployment_status": "finished"}
            document["migration"] = {"legacy_entrypoint": "source.txt", "strategy": "synthetic fixture", "behavior_change": "none"}
            document["artifacts"][0].update(archive_status="resolved", uri=f"artifact://mlflow/MLModel-1_V001/{'1' * 32}/model.bin")
            snapshot = {"snapshot_version": "1.0", "model_id": document["model"]["id"], "model_version": "1.0.0",
                        "lifecycle_state": "released", "captured_on": "2026-10-01",
                        **{key: document[key] for key in ("target", "contract", "source", "platform", "migration")},
                        "artifact": document["artifacts"][0]}
            (parent / "releases/current.yaml").write_text(yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (parent / "model.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in parent.rglob("*") if path.is_file()}
            result, child = self.assert_index_failure_and_recovery(("maintain", str(parent), "--version", "1.0.1"), root)
            self.assertEqual(result["status"], "created")
            self.assertEqual(yaml.safe_load((child / "model.yaml").read_text(encoding="utf-8"))["model"]["version"], "1.0.1")
            self.assertEqual(len(list(root.rglob("model.yaml"))), 2)
            self.assertEqual({path.name: path.read_bytes() for path in parent.rglob("*") if path.is_file()}, before)

    def test_draft_contract_fork_preserves_frozen_parent_when_index_writes_fail(self):
        with tempfile.TemporaryDirectory(prefix="model-lab-draft-fork-recovery-") as directory:
            root = Path(directory) / "private-assets"
            parent = create_draft(root, "draft-recovery")
            complete_specification_evidence(parent)
            complete_build_evidence(parent)
            document = yaml.safe_load((parent / "model.yaml").read_text(encoding="utf-8"))
            document["model"]["lifecycle_state"] = "releasable"
            document["source"] = {"digest_mode": "utf8-lf"}
            for role, relative in (("entrypoint", "src/model.py"), ("notebook", "notebooks/model.ipynb"), ("documentation", "model-spec.md")):
                text = (parent / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
                document["source"].update({role: relative, f"{role}_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
            (parent / "model.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
            register = yaml.safe_load((parent / "decisions.yaml").read_text(encoding="utf-8"))
            for decision in register["decisions"]:
                decision.update(status="confirmed", recommendation="Synthetic fixture only", answer="Synthetic fixture only",
                                confirmed_by="example", confirmed_at="2026-10-01T00:00:00+08:00")
            (parent / "decisions.yaml").write_text(yaml.safe_dump(register, allow_unicode=True, sort_keys=False), encoding="utf-8")
            before = {path.relative_to(parent).as_posix(): path.read_bytes() for path in parent.rglob("*") if path.is_file()}
            result, child = self.assert_index_failure_and_recovery(("draft-fork", str(parent), "--version", "0.2.0"), root)
            self.assertEqual(result["status"], "created")
            self.assertEqual(yaml.safe_load((child / "model.yaml").read_text(encoding="utf-8"))["model"]["version"], "0.2.0")
            self.assertEqual(len(list(root.rglob("model.yaml"))), 2)
            self.assertEqual({path.relative_to(parent).as_posix(): path.read_bytes() for path in parent.rglob("*") if path.is_file()}, before)

    def test_advance_commits_only_one_state_when_index_writes_fail(self):
        with tempfile.TemporaryDirectory(prefix="model-lab-advance-recovery-") as directory:
            root = Path(directory) / "private-assets"
            workspace = create_draft(root, "advance-recovery")
            complete_specification_evidence(workspace)
            result, recovered = self.assert_index_failure_and_recovery(("advance", str(workspace)), root)
            self.assertTrue(result["applied"])
            self.assertEqual(recovered, workspace)
            self.assertEqual(yaml.safe_load((workspace / "model.yaml").read_text(encoding="utf-8"))["model"]["lifecycle_state"], "specified")
            self.assertEqual(len(list(root.rglob("model.yaml"))), 1)
