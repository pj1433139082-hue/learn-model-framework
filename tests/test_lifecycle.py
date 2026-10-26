from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml  # type: ignore[import-untyped]

import model_lab.lifecycle as lifecycle_module
from model_lab.deployment import build_dry_run_plan
from model_lab.lifecycle import advance_workspace, workspace_status
from model_lab.runtime_contract import RuntimeObservation
from model_lab.scaffolding import ScaffoldRequest, WorkspaceError, scaffold_model


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_TODO_MARKERS = (
    "MODEL-LAB-TODO:purpose",
    "MODEL-LAB-TODO:users",
    "MODEL-LAB-TODO:frequency",
    "MODEL-LAB-TODO:boundaries",
    "MODEL-LAB-TODO:acceptance",
    "MODEL-LAB-TODO:formula",
    "MODEL-LAB-TODO:parameters",
    "MODEL-LAB-TODO:measurements",
    "MODEL-LAB-TODO:exception-strategy",
)


def run_model_lab(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "model_lab", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def create_workspace(models_root: Path, model_id: str = "lifecycle-reference") -> Path:
    report = scaffold_model(
        ScaffoldRequest(
            model_id=model_id,
            name="Lifecycle reference",
            owner="model-lab-tests",
            target="cpu-iot",
        ),
        models_root,
        apply=True,
    )
    return Path(report.destination)


def load_manifest(workspace: Path) -> dict[str, object]:
    document = yaml.safe_load((workspace / "model.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("scaffolded model.yaml must contain an object")
    return document


def write_manifest(workspace: Path, document: dict[str, object]) -> None:
    (workspace / "model.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def set_lifecycle_state(workspace: Path, state: str) -> None:
    document = load_manifest(workspace)
    model = document.get("model")
    if not isinstance(model, dict):
        raise AssertionError("scaffolded model.yaml must contain model metadata")
    model["lifecycle_state"] = state
    write_manifest(workspace, document)


def lifecycle_state(workspace: Path) -> object:
    document = load_manifest(workspace)
    model = document.get("model")
    if not isinstance(model, dict):
        raise AssertionError("scaffolded model.yaml must contain model metadata")
    return model.get("lifecycle_state")


def confirm_decisions(workspace: Path, transition: str) -> None:
    path = workspace / "decisions.yaml"
    register = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(register, dict) or not isinstance(register.get("decisions"), list):
        raise AssertionError("scaffolded decisions.yaml must contain decisions")
    for decision in register["decisions"]:
        if not isinstance(decision, dict):
            raise AssertionError("decision entries must be objects")
        if decision.get("blocks_transition") != transition:
            continue
        recommendation = decision.get("recommendation")
        if not isinstance(recommendation, str) or not recommendation.strip():
            recommendation = f"Accept the reviewed answer for {decision['id']}."
        decision["recommendation"] = recommendation
        decision["status"] = "confirmed"
        decision["answer"] = recommendation
        decision["confirmed_by"] = "model-lab-tests"
        decision["confirmed_at"] = "2026-09-23T00:00:00+08:00"
    path.write_text(
        yaml.safe_dump(register, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def complete_specification_evidence(workspace: Path, *, confirm: bool = True) -> None:
    document = load_manifest(workspace)
    document["contract"] = {
        "inputs": [
            {"name": "flow_m3h", "type": "number", "unit": "m3/h"},
        ],
        "outputs": [
            {"name": "result", "type": "number", "unit": "dimensionless"},
        ],
    }
    write_manifest(workspace, document)
    (workspace / "model-spec.md").write_text(
        """# Lifecycle reference specification

## 1. 需求

### 一句话用途
Produce a deterministic reference result for lifecycle tests.

### 使用者
Model-lab maintainers use it on demand during offline validation.

### 执行频率
The model is evaluated on demand during offline validation runs.

### 范围与非目标
The reference covers one finite numeric input and excludes remote execution.

### 可验证的验收标准
One finite flow input produces one finite dimensionless output.

## 2. 设计

### 公式或模型逻辑
The reference formula is identity and has no adjustable parameters.

### 参数、单位、来源与可调性
The identity model has no adjustable parameters or hidden defaults.

### 测点及数据口径
The flow input is measured in cubic metres per hour at source frequency.

### 缺失、异常和边界策略
The input is measured in m3/h; missing or non-finite input is rejected.
""",
        encoding="utf-8",
    )
    if confirm:
        confirm_decisions(workspace, "specified")


def complete_build_evidence(workspace: Path) -> None:
    document = load_manifest(workspace)
    document["artifacts"] = [
        {
            "name": "reference-model",
            "kind": "model",
            "uri": "artifact://lifecycle-reference/model.bin",
            "sha256": "1" * 64,
        }
    ]
    write_manifest(workspace, document)
    with (workspace / "model-spec.md").open("a", encoding="utf-8") as handle:
        handle.write(
            """

## 3. 构建

### 数据质量结论
The frozen reference window passed missing-value, finite-value and physical-bound checks.

### 构建证据
Source, notebook, tests, artifact digest and reproducible local commands are recorded.
"""
        )
    (workspace / "src" / "model.py").write_text(
        ("def predict(inputs: object) -> object:\n    return {'result': inputs}\n"),
        encoding="utf-8",
    )
    (workspace / "tests" / "test_model.py").write_text(
        (
            "from src.model import predict\n\n"
            "def test_predict_returns_result() -> None:\n"
            "    assert predict(1) == {'result': 1}\n"
        ),
        encoding="utf-8",
    )
    (workspace / "notebooks" / "model.ipynb").write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": ["result = {'reference': 1}\n"],
                        "outputs": [],
                        "execution_count": None,
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    confirm_decisions(workspace, "built")


def complete_validation_evidence(workspace: Path) -> None:
    (workspace / "validation" / "validation-report.md").write_text(
        """# Lifecycle reference validation

## 契约与数据质量

The frozen input window, contract fields, missing values, physical bounds and deterministic result were reviewed and passed locally.

## 结果可视化审查

The reference output plot was reviewed by model-lab-tests; no unresolved visual anomaly or release risk remains.
""",
        encoding="utf-8",
    )
    confirm_decisions(workspace, "validated")


def write_mlflow_bundle(workspace: Path) -> None:
    bundle = workspace / "release-bundle" / "service"
    artifacts = bundle / "artifacts"
    artifacts.mkdir(parents=True)
    (bundle / "MLmodel").write_text(
        yaml.safe_dump(
            {
                "flavors": {
                    "python_function": {
                        "loader_module": "deploy",
                        "artifacts": {
                            "logic_bundle": {"path": "artifacts/logic_bundle.pkl"},
                            "custom_sources": {"path": "artifacts/custom_sources.pkl"},
                            "model_file": {"path": "artifacts/model.bin"},
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bundle / "python_env.yaml").write_text(
        'python: "3.11.9"\n', encoding="utf-8"
    )
    dependencies = [
        "mlflow==2.22.2",
        "cloudpickle==3.1.2",
        "numpy==2.3.3",
        "pandas==2.3.3",
    ]
    (bundle / "conda.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "mlflow-env",
                "dependencies": ["python=3.11.9", {"pip": dependencies}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bundle / "requirements.txt").write_text(
        "\n".join(dependencies) + "\n", encoding="utf-8"
    )
    (artifacts / "logic_bundle.pkl").write_bytes(b"opaque logic")
    (artifacts / "custom_sources.pkl").write_bytes(b"opaque sources")
    (artifacts / "model.bin").write_bytes(b"opaque model")


def complete_release_preparation_evidence(
    workspace: Path,
    *,
    include_bundle: bool = True,
) -> None:
    document = load_manifest(workspace)

    def digest(relative: str) -> str:
        text = (workspace / relative).read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    document["source"] = {
        "digest_mode": "utf8-lf",
        "entrypoint": "src/model.py",
        "entrypoint_sha256": digest("src/model.py"),
        "notebook": "notebooks/model.ipynb",
        "notebook_sha256": digest("notebooks/model.ipynb"),
        "documentation": "model-spec.md",
        "documentation_sha256": digest("model-spec.md"),
    }
    write_manifest(workspace, document)
    with (workspace / "model-spec.md").open("a", encoding="utf-8") as handle:
        handle.write(
            """

## 4. 发布准备

### 部署计划
Use the cpu-iot adapter and the recorded immutable artifact digest in a local dry-run only.

### 回滚计划
Keep the previous released asset active and restore its immutable manifest if a future release fails.

### 调度、监测、告警与后续维护
Run on demand, monitor contract failures and output bounds, and open a new version for every change.
"""
        )
    document = load_manifest(workspace)
    source = document.get("source")
    if not isinstance(source, dict):
        raise AssertionError("source metadata must be an object")
    source["documentation_sha256"] = digest("model-spec.md")
    write_manifest(workspace, document)
    if include_bundle:
        write_mlflow_bundle(workspace)
    confirm_decisions(workspace, "releasable")


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def state_value(value: object) -> object:
    return getattr(value, "value", value)


class WorkspaceLifecycleTests(unittest.TestCase):
    def test_status_reports_current_open_questions_and_pending_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")

            status = workspace_status(workspace)

        payload = status.as_dict()
        open_questions = payload["open_questions"]
        pending_confirmations = payload["pending_confirmations"]
        self.assertIn(
            "requirements-context",
            {item["id"] for item in open_questions},
        )
        self.assertIn(
            "requirements-acceptance",
            {item["id"] for item in pending_confirmations},
        )
        self.assertTrue(all(item["blocks_transition"] == "specified" for item in open_questions))
        self.assertTrue(
            all(
                item["blocks_transition"] == "specified"
                for item in pending_confirmations
            )
        )
        rendered = status.as_text()
        self.assertIn(
            "Decision scope: draft -> specified; later-stage decisions remain in "
            "decisions.yaml until relevant.",
            rendered,
        )
        self.assertIn("Open questions:", rendered)
        self.assertIn("Pending confirmations:", rendered)

    def test_status_human_output_shows_recommendations_for_open_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            register_path = workspace / "decisions.yaml"
            register = yaml.safe_load(register_path.read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in register["decisions"]}
            by_id["requirements-context"]["recommendation"] = "仅做一次手动演示"
            by_id["requirements-acceptance"]["recommendation"] = "误差不超过 1e-9"
            register_path.write_text(
                yaml.safe_dump(register, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )

            result = run_model_lab("status", str(workspace))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("requirements-context:", result.stdout)
        self.assertIn("Recommended: 仅做一次手动演示", result.stdout)
        self.assertIn("requirements-acceptance:", result.stdout)
        self.assertIn("Recommended: 误差不超过 1e-9", result.stdout)

    def test_open_required_decisions_block_the_next_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace, confirm=False)
            before = tree_bytes(workspace)

            status = workspace_status(workspace)
            attempted = advance_workspace(workspace, apply=True)

            self.assertFalse(status.can_advance)
            gate = next(
                gate
                for gate in status.gates
                if gate.gate_id == "interaction-decisions-confirmed"
            )
            self.assertFalse(gate.passed)
            self.assertIn("requirements-context", gate.message)
            self.assertFalse(attempted.applied)
            self.assertEqual(tree_bytes(workspace), before)

    def test_duplicate_decision_keys_are_reported_without_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            decisions_path = workspace / "decisions.yaml"
            content = decisions_path.read_text(encoding="utf-8")
            decisions_path.write_text(
                'schema_version: "1.0"\n' + content,
                encoding="utf-8",
            )

            status = workspace_status(workspace)

        self.assertFalse(status.decision_register.valid)
        self.assertIn(
            "decision_register_duplicate_key",
            {issue.code for issue in status.decision_register.issues},
        )

    def test_new_workspace_requires_environment_contract_and_dependency_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            document = load_manifest(workspace)
            target = document["target"]
            assert isinstance(target, dict)
            target.pop("environment")
            write_manifest(workspace, document)

            status = workspace_status(workspace)

        gate = next(gate for gate in status.gates if gate.gate_id == "runtime-contract-valid")
        self.assertFalse(gate.passed)
        self.assertFalse(status.can_advance)

    def test_status_reports_host_mismatch_without_using_it_as_a_transition_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)
            with mock.patch.object(
                RuntimeObservation,
                "current",
                return_value=RuntimeObservation(
                    "windows", "x86_64", "3.12.13", "cp312"
                ),
            ):
                status = workspace_status(workspace)
            after = tree_bytes(workspace)

        self.assertTrue(status.can_advance, status.as_dict())
        self.assertFalse(status.build_environment.valid)
        self.assertIn(
            "build_python_mismatch",
            {issue.code for issue in status.build_environment.issues},
        )
        self.assertIn("Current build host: ADVISORY", status.as_text())
        self.assertNotIn("Runtime environment: BLOCK", status.as_text())
        self.assertEqual(after, before)

    def test_validated_workspace_requires_verified_mlflow_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            self.assertTrue(advance_workspace(workspace, apply=True).applied)
            complete_build_evidence(workspace)
            self.assertTrue(advance_workspace(workspace, apply=True).applied)
            complete_validation_evidence(workspace)
            self.assertTrue(advance_workspace(workspace, apply=True).applied)
            complete_release_preparation_evidence(workspace, include_bundle=False)

            status = workspace_status(workspace)

        gate = next(gate for gate in status.gates if gate.gate_id == "mlflow-bundle-valid")
        self.assertFalse(gate.passed)
        self.assertFalse(status.can_advance)

    def test_missing_specification_evidence_blocks_advance_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            spec = (workspace / "model-spec.md").read_text(encoding="utf-8")
            self.assertTrue(all(marker in spec for marker in SPEC_TODO_MARKERS))
            before = tree_bytes(workspace)

            status = workspace_status(workspace)
            attempted = advance_workspace(workspace, apply=True)

            self.assertEqual(state_value(status.current_state), "draft")
            self.assertEqual(state_value(status.next_state), "specified")
            self.assertFalse(status.can_advance)
            self.assertFalse(status.applied)
            self.assertTrue(status.gates)
            payload = status.as_dict()
            gates = payload["gates"]
            if not isinstance(gates, list):
                raise AssertionError("lifecycle report gates must be an array")
            self.assertTrue(
                any(
                    isinstance(gate, dict) and gate.get("passed") is False
                    for gate in gates
                )
            )
            self.assertFalse(attempted.can_advance)
            self.assertFalse(attempted.applied)
            self.assertEqual(tree_bytes(workspace), before)

    def test_advance_plan_is_read_only_and_apply_moves_exactly_one_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            metadata_before = (workspace / "model-lab.yaml").read_bytes()
            before = tree_bytes(workspace)

            planned = advance_workspace(workspace, apply=False)

            self.assertTrue(planned.can_advance, planned.as_dict())
            self.assertFalse(planned.applied)
            self.assertEqual(state_value(planned.current_state), "draft")
            self.assertEqual(state_value(planned.next_state), "specified")
            self.assertEqual(tree_bytes(workspace), before)

            applied = advance_workspace(workspace, apply=True)
            after = workspace_status(workspace)

            self.assertTrue(applied.applied)
            self.assertEqual(state_value(applied.current_state), "specified")
            self.assertEqual(state_value(after.current_state), "specified")
            self.assertEqual(state_value(after.next_state), "built")
            self.assertFalse(after.can_advance)
            self.assertTrue((workspace / "tests" / "model_test_template.py").is_file())
            self.assertFalse((workspace / "tests" / "test_model.py").exists())
            self.assertEqual(
                (workspace / "model-lab.yaml").read_bytes(), metadata_before
            )

            once_advanced = tree_bytes(workspace)
            blocked = advance_workspace(workspace, apply=True)
            self.assertFalse(blocked.applied)
            self.assertEqual(tree_bytes(workspace), once_advanced)
            self.assertEqual(lifecycle_state(workspace), "specified")

    def test_complete_local_sop_reaches_releasable_and_builds_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(
                Path(temporary) / "models",
                "complete-lifecycle-reference",
            )
            complete_specification_evidence(workspace)

            specified = advance_workspace(workspace, apply=True)
            self.assertTrue(specified.applied)
            self.assertEqual(lifecycle_state(workspace), "specified")

            complete_build_evidence(workspace)
            built = advance_workspace(workspace, apply=True)
            self.assertTrue(built.applied, built.as_dict())
            self.assertEqual(lifecycle_state(workspace), "built")

            complete_validation_evidence(workspace)
            validated = advance_workspace(workspace, apply=True)
            self.assertTrue(validated.applied, validated.as_dict())
            self.assertEqual(lifecycle_state(workspace), "validated")

            complete_release_preparation_evidence(workspace)
            releasable = advance_workspace(workspace, apply=True)
            self.assertTrue(releasable.applied, releasable.as_dict())
            self.assertEqual(lifecycle_state(workspace), "releasable")

            status = workspace_status(workspace)
            plan = build_dry_run_plan(workspace / "model.yaml")
            self.assertEqual(state_value(status.current_state), "releasable")
            self.assertEqual(status.outcome, "manual")
            self.assertFalse(status.can_advance)
            self.assertEqual(status.as_dict()["status_scope"], "local_lifecycle_only")
            self.assertIn("local archive only", status.as_text())
            self.assertIn("not MLflow upload or local archive gates", status.as_text())
            self.assertEqual(plan.model_id, "complete-lifecycle-reference")
            self.assertEqual(plan.lifecycle_state, "releasable")
            self.assertEqual(plan.target, "cpu-iot")
            self.assertTrue(plan.plan_id)
            self.assertTrue(all(step.effect for step in plan.steps))

    def test_releasable_status_exposes_a_deterministic_decision_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            for transition in ("specified", "built", "validated", "releasable"):
                confirm_decisions(workspace, transition)
            set_lifecycle_state(workspace, "releasable")

            first = workspace_status(workspace).as_dict()["decision_summary"]
            second = workspace_status(workspace).as_dict()["decision_summary"]

        self.assertEqual(first, second)
        self.assertTrue(first["valid"])
        self.assertEqual(first["open_count"], 0)
        self.assertGreater(first["confirmed_count"], 0)
        self.assertEqual(len(first["sha256"]), 64)

    def test_missing_scaffold_metadata_blocks_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            (workspace / "model-lab.yaml").unlink()

            status = workspace_status(workspace)

            metadata_gate = next(
                gate
                for gate in status.gates
                if gate.gate_id == "scaffold-metadata-valid"
            )
            self.assertFalse(metadata_gate.passed)
            self.assertFalse(status.can_advance)

    def test_noop_source_test_and_notebook_do_not_satisfy_build_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            advanced = advance_workspace(workspace, apply=True)
            self.assertTrue(advanced.applied)

            document = load_manifest(workspace)
            document["artifacts"] = [
                {
                    "name": "reference-model",
                    "kind": "model",
                    "uri": "artifact://reference/model.bin",
                    "sha256": "1" * 64,
                }
            ]
            write_manifest(workspace, document)
            with (workspace / "model-spec.md").open("a", encoding="utf-8") as handle:
                handle.write(
                    """

## 3. 构建

### 数据质量结论
Missing values and physical bounds were checked on the frozen input window.

### 构建证据
The source, notebook, tests, artifact digest and commands are recorded here.
"""
                )
            (workspace / "src" / "model.py").write_text(
                (
                    "def predict(inputs: object) -> object:\n"
                    "    if True:\n"
                    "        raise RuntimeError('unreachable return')\n"
                    "    return 1\n"
                ),
                encoding="utf-8",
            )
            (workspace / "tests" / "test_model.py").write_text(
                (
                    "def test_behavior() -> None:\n"
                    "    if True:\n"
                    "        return\n"
                    "    assert False\n"
                ),
                encoding="utf-8",
            )
            (workspace / "notebooks" / "model.ipynb").write_text(
                json.dumps(
                    {
                        "cells": [
                            {
                                "cell_type": "code",
                                "metadata": {},
                                "source": [
                                    "if True:\n",
                                    "    raise RuntimeError('stop')\n",
                                    "result = 1\n",
                                ],
                                "outputs": [],
                                "execution_count": None,
                            }
                        ],
                        "metadata": {},
                        "nbformat": 4,
                        "nbformat_minor": 5,
                    }
                ),
                encoding="utf-8",
            )

            status = workspace_status(workspace)

            python_gates = [
                gate for gate in status.gates if gate.gate_id == "python-ready"
            ]
            notebook_gate = next(
                gate for gate in status.gates if gate.gate_id == "notebook-ready"
            )
            self.assertEqual(len(python_gates), 2)
            self.assertTrue(all(not gate.passed for gate in python_gates))
            self.assertFalse(notebook_gate.passed)
            self.assertFalse(status.can_advance)

            reachability_cases = (
                (
                    "negated constant",
                    (
                        "def predict(inputs: object) -> object:\n"
                        "    if not False:\n"
                        "        raise RuntimeError('unreachable return')\n"
                        "    return 1\n"
                    ),
                    (
                        "def test_behavior() -> None:\n"
                        "    if not False:\n"
                        "        return\n"
                        "    assert False\n"
                    ),
                    ("if not False:\n    raise RuntimeError('stop')\nresult = 1\n"),
                ),
                (
                    "infinite loop",
                    (
                        "def predict(inputs: object) -> object:\n"
                        "    while True:\n"
                        "        pass\n"
                        "    return 1\n"
                    ),
                    (
                        "def test_behavior() -> None:\n"
                        "    while True:\n"
                        "        pass\n"
                        "    assert False\n"
                    ),
                    "while True:\n    pass\nresult = 1\n",
                ),
                (
                    "guaranteed assertion failure",
                    (
                        "def predict(inputs: object) -> object:\n"
                        "    assert False\n"
                        "    return 1\n"
                    ),
                    "def test_behavior() -> None:\n    assert False\n",
                    "assert False\nresult = 1\n",
                ),
                (
                    "constant comparison assertion failure",
                    (
                        "def predict(inputs: object) -> object:\n"
                        "    assert 1 == 2\n"
                        "    return 1\n"
                    ),
                    "def test_behavior() -> None:\n    assert 1 == 2\n",
                    "assert 1 == 2\nresult = 1\n",
                ),
                (
                    "functions hidden in unreachable module scope",
                    (
                        "if False:\n"
                        "    def predict(inputs: object) -> object:\n"
                        "        return 1\n"
                    ),
                    (
                        "if False:\n"
                        "    def test_behavior() -> None:\n"
                        "        assert True\n"
                    ),
                    "if False:\n    result = 1\n",
                ),
            )
            for label, source_code, test_code, notebook_code in reachability_cases:
                with self.subTest(label=label):
                    (workspace / "src" / "model.py").write_text(
                        source_code,
                        encoding="utf-8",
                    )
                    (workspace / "tests" / "test_model.py").write_text(
                        test_code,
                        encoding="utf-8",
                    )
                    (workspace / "notebooks" / "model.ipynb").write_text(
                        json.dumps(
                            {
                                "cells": [
                                    {
                                        "cell_type": "code",
                                        "metadata": {},
                                        "source": notebook_code.splitlines(True),
                                        "outputs": [],
                                        "execution_count": None,
                                    }
                                ],
                                "metadata": {},
                                "nbformat": 4,
                                "nbformat_minor": 5,
                            }
                        ),
                        encoding="utf-8",
                    )

                    variant_status = workspace_status(workspace)
                    variant_python = [
                        gate
                        for gate in variant_status.gates
                        if gate.gate_id == "python-ready"
                    ]
                    variant_notebook = next(
                        gate
                        for gate in variant_status.gates
                        if gate.gate_id == "notebook-ready"
                    )
                    self.assertTrue(all(not gate.passed for gate in variant_python))
                    self.assertFalse(variant_notebook.passed)
                    self.assertFalse(variant_status.can_advance)

            (workspace / "src" / "model.py").write_text(
                (
                    "def predict(inputs: object) -> object:\n"
                    "    return 1\n"
                    "predict = None\n"
                ),
                encoding="utf-8",
            )
            (workspace / "tests" / "test_model.py").write_text(
                (
                    "def test_behavior() -> None:\n"
                    "    assert True\n"
                    "test_behavior = None\n"
                ),
                encoding="utf-8",
            )
            (workspace / "notebooks" / "model.ipynb").write_text(
                json.dumps(
                    {
                        "cells": [
                            {
                                "cell_type": "code",
                                "metadata": {},
                                "source": ["result = 1\n"],
                                "outputs": [],
                                "execution_count": None,
                            }
                        ],
                        "metadata": {},
                        "nbformat": 4,
                        "nbformat_minor": 5,
                    }
                ),
                encoding="utf-8",
            )
            rebound_status = workspace_status(workspace)
            rebound_python = [
                gate for gate in rebound_status.gates if gate.gate_id == "python-ready"
            ]
            rebound_notebook = next(
                gate
                for gate in rebound_status.gates
                if gate.gate_id == "notebook-ready"
            )
            self.assertTrue(all(not gate.passed for gate in rebound_python))
            self.assertTrue(rebound_notebook.passed)
            self.assertFalse(rebound_status.can_advance)

    def test_mutation_at_atomic_write_entry_cannot_advance_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_write = lifecycle_module._atomic_write_manifest

            def mutate_then_write(
                manifest: Path,
                document: dict[str, object],
                *,
                expected_fingerprint: str,
            ) -> None:
                (workspace / "model-spec.md").write_text("", encoding="utf-8")
                original_write(
                    manifest,
                    document,
                    expected_fingerprint=expected_fingerprint,
                )

            with (
                mock.patch(
                    "model_lab.lifecycle._atomic_write_manifest",
                    side_effect=mutate_then_write,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "workspace_changed")
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_mutation_at_replace_is_detected_and_state_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_publish = lifecycle_module._publish_noreplace
            mutated = False

            def mutate_then_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal mutated
                if not mutated and Path(destination) == workspace / "model.yaml":
                    mutated = True
                    (workspace / "model-spec.md").write_text("", encoding="utf-8")
                original_publish(Path(source), Path(destination))

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=mutate_then_publish,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "workspace_changed")
            self.assertTrue(mutated)
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_manifest_mutation_during_claim_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_replace = lifecycle_module.os.replace
            mutated = False

            def mutate_then_replace(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal mutated
                if not mutated and Path(source) == workspace / "model.yaml":
                    mutated = True
                    concurrent = load_manifest(workspace)
                    concurrent["concurrent_writer"] = "must-survive"
                    write_manifest(workspace, concurrent)
                original_replace(source, destination)

            with (
                mock.patch(
                    "model_lab.lifecycle.os.replace",
                    side_effect=mutate_then_replace,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            preserved = load_manifest(workspace)
            self.assertEqual(raised.exception.code, "workspace_changed")
            self.assertTrue(mutated)
            self.assertEqual(preserved.get("concurrent_writer"), "must-survive")
            model = preserved.get("model")
            self.assertIsInstance(model, dict)
            assert isinstance(model, dict)
            self.assertEqual(model.get("lifecycle_state"), "draft")

    def test_interrupt_after_manifest_claim_restores_original_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)
            original_publish = lifecycle_module._publish_noreplace
            interrupted = False

            def interrupt_manifest_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal interrupted
                if not interrupted and Path(destination) == workspace / "model.yaml":
                    interrupted = True
                    raise KeyboardInterrupt
                original_publish(Path(source), Path(destination))

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=interrupt_manifest_publish,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                advance_workspace(workspace, apply=True)

            self.assertTrue(interrupted)
            self.assertEqual(tree_bytes(workspace), before)
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_interrupt_during_post_commit_cleanup_keeps_committed_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_cleanup = lifecycle_module._best_effort_unlink
            interrupted = False

            def cleanup_then_interrupt(path: Path | None) -> None:
                nonlocal interrupted
                if path is not None and not interrupted and "-previous." in path.name:
                    original_cleanup(path)
                    interrupted = True
                    raise KeyboardInterrupt
                original_cleanup(path)

            with (
                mock.patch(
                    "model_lab.lifecycle._best_effort_unlink",
                    side_effect=cleanup_then_interrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                advance_workspace(workspace, apply=True)

            self.assertTrue(interrupted)
            self.assertEqual(lifecycle_state(workspace), "specified")
            self.assertFalse(
                any("-previous." in path.name for path in workspace.parent.iterdir())
            )

    def test_post_publish_read_failure_rolls_back_incomplete_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)
            original_publish = lifecycle_module._publish_noreplace
            original_read_bytes = Path.read_bytes
            published = False
            read_failed = False

            def mark_manifest_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal published
                original_publish(Path(source), Path(destination))
                if Path(destination) == workspace / "model.yaml":
                    published = True

            def fail_first_published_read(path: Path) -> bytes:
                nonlocal read_failed
                if path == workspace / "model.yaml" and published and not read_failed:
                    read_failed = True
                    raise OSError("simulated transient read denial")
                return original_read_bytes(path)

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=mark_manifest_publish,
                ),
                mock.patch("pathlib.Path.read_bytes", new=fail_first_published_read),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "manifest_write_failed")
            self.assertTrue(published)
            self.assertTrue(read_failed)
            self.assertEqual(tree_bytes(workspace), before)
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_concurrent_manifest_creation_wins_conditional_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_publish = lifecycle_module._publish_noreplace
            concurrent = load_manifest(workspace)
            concurrent["concurrent_writer"] = "publish-winner"
            mutated = False

            def create_then_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal mutated
                if not mutated and Path(destination) == workspace / "model.yaml":
                    mutated = True
                    write_manifest(workspace, concurrent)
                original_publish(Path(source), Path(destination))

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=create_then_publish,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            preserved = load_manifest(workspace)
            self.assertEqual(raised.exception.code, "workspace_changed")
            self.assertTrue(mutated)
            self.assertEqual(preserved.get("concurrent_writer"), "publish-winner")
            model = preserved.get("model")
            self.assertIsInstance(model, dict)
            assert isinstance(model, dict)
            self.assertEqual(model.get("lifecycle_state"), "draft")

    def test_concurrent_manifest_change_during_rollback_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_publish = lifecycle_module._publish_noreplace
            original_replace = lifecycle_module.os.replace
            link_mutated = False
            manifest_claims = 0

            def mutate_evidence_then_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal link_mutated
                if not link_mutated and Path(destination) == workspace / "model.yaml":
                    link_mutated = True
                    (workspace / "model-spec.md").write_text("", encoding="utf-8")
                original_publish(Path(source), Path(destination))

            def mutate_rollback_then_replace(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal manifest_claims
                if Path(source) == workspace / "model.yaml":
                    manifest_claims += 1
                    if manifest_claims == 2:
                        concurrent = load_manifest(workspace)
                        concurrent["concurrent_writer"] = "rollback-winner"
                        model = concurrent.get("model")
                        if not isinstance(model, dict):
                            raise AssertionError("model metadata must be an object")
                        model["lifecycle_state"] = "draft"
                        write_manifest(workspace, concurrent)
                original_replace(source, destination)

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=mutate_evidence_then_publish,
                ),
                mock.patch(
                    "model_lab.lifecycle.os.replace",
                    side_effect=mutate_rollback_then_replace,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            preserved = load_manifest(workspace)
            self.assertEqual(raised.exception.code, "manifest_recovery_required")
            self.assertTrue(link_mutated)
            self.assertEqual(manifest_claims, 2)
            self.assertEqual(preserved.get("concurrent_writer"), "rollback-winner")
            model = preserved.get("model")
            self.assertIsInstance(model, dict)
            assert isinstance(model, dict)
            self.assertEqual(model.get("lifecycle_state"), "draft")

    def test_unsupported_conditional_publish_fails_before_manifest_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)
            original_publish = lifecycle_module._publish_noreplace
            denied = False

            def deny_workspace_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal denied
                if Path(destination).parent == workspace:
                    denied = True
                    raise OSError("workspace no-overwrite publish unavailable")
                original_publish(Path(source), Path(destination))

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=deny_workspace_publish,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "atomic_update_unsupported")
            self.assertTrue(denied)
            self.assertEqual(tree_bytes(workspace), before)
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_publish_failure_restores_manifest_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)
            original_publish = lifecycle_module._publish_noreplace
            denied = 0

            def deny_first_manifest_publish(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal denied
                if Path(destination) == workspace / "model.yaml" and denied == 0:
                    denied += 1
                    raise OSError("model.yaml conditional publish failed")
                original_publish(Path(source), Path(destination))

            with (
                mock.patch(
                    "model_lab.lifecycle._publish_noreplace",
                    side_effect=deny_first_manifest_publish,
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "manifest_write_failed")
            self.assertEqual(denied, 1)
            self.assertEqual(tree_bytes(workspace), before)
            self.assertEqual(lifecycle_state(workspace), "draft")

    def test_post_publish_cleanup_failure_does_not_report_false_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            original_unlink = Path.unlink
            cleanup_failed = False
            stage_prefix = f".{workspace.name}-model.yaml."

            def fail_stage_cleanup_once(
                path: Path,
                missing_ok: bool = False,
            ) -> None:
                nonlocal cleanup_failed
                if (
                    not cleanup_failed
                    and path.parent == workspace.parent
                    and path.name.startswith(stage_prefix)
                ):
                    cleanup_failed = True
                    raise OSError("simulated cleanup failure")
                original_unlink(path, missing_ok=missing_ok)

            with mock.patch("pathlib.Path.unlink", new=fail_stage_cleanup_once):
                report = advance_workspace(workspace, apply=True)

            self.assertTrue(cleanup_failed)
            self.assertTrue(report.applied)
            self.assertEqual(lifecycle_state(workspace), "specified")
            self.assertFalse(
                any(
                    path.name.startswith(stage_prefix)
                    for path in workspace.parent.iterdir()
                )
            )

    def test_network_workspace_path_is_rejected_before_access(self) -> None:
        with self.assertRaises(WorkspaceError) as raised:
            workspace_status(r"\\server\share\model")

        self.assertEqual(raised.exception.code, "unsafe_workspace_path")

    def test_deleting_todo_markers_without_evidence_still_blocks_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            document = load_manifest(workspace)
            document["contract"] = {
                "inputs": [{"name": "flow", "type": "number", "unit": "m3/h"}],
                "outputs": [
                    {"name": "result", "type": "number", "unit": "dimensionless"}
                ],
            }
            write_manifest(workspace, document)
            spec_path = workspace / "model-spec.md"
            content = spec_path.read_text(encoding="utf-8")
            for marker in SPEC_TODO_MARKERS:
                content = content.replace(marker, "")
            spec_path.write_text(content, encoding="utf-8")

            status = workspace_status(workspace)

            evidence_gate = next(
                gate
                for gate in status.gates
                if gate.gate_id == "markdown-sections-complete"
            )
            self.assertFalse(evidence_gate.passed)
            self.assertFalse(status.can_advance)

    def test_releasable_workspace_cannot_advance_to_released_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            set_lifecycle_state(workspace, "releasable")
            before = tree_bytes(workspace)

            status = workspace_status(workspace)
            attempted = advance_workspace(workspace, apply=True)

            self.assertEqual(state_value(status.current_state), "releasable")
            self.assertFalse(status.can_advance)
            self.assertFalse(attempted.applied)
            self.assertEqual(tree_bytes(workspace), before)
            self.assertEqual(lifecycle_state(workspace), "releasable")

    def test_python_bytecode_cache_does_not_block_workspace_status(self) -> None:
        """运行本机单测产生的 Python 缓存不应被 workspace-safe 当成模型产物。"""

        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            cache = workspace / "src" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "model.cpython-311.pyc").write_bytes(b"bytecode")
            pytest_cache = workspace / ".pytest_cache"
            pytest_cache.mkdir()
            (pytest_cache / "state").write_text("cache", encoding="utf-8")

            status = workspace_status(workspace)
            safety_gate = next(
                gate for gate in status.gates if gate.gate_id == "workspace-safe"
            )
            self.assertTrue(safety_gate.passed, safety_gate.message)


    def test_current_workspace_secret_blocks_status_gate_and_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            secret = "sk-" + "a" * 40
            (workspace / "local-notes.txt").write_text(
                f"OPENAI_API_KEY={secret}\n",
                encoding="utf-8",
            )
            before = tree_bytes(workspace)

            status = workspace_status(workspace)
            attempted = advance_workspace(workspace, apply=True)

            safety_gate = next(
                gate for gate in status.gates if gate.gate_id == "workspace-safe"
            )
            self.assertFalse(safety_gate.passed)
            self.assertIn("workspace_secret_content", safety_gate.message)
            self.assertFalse(status.can_advance)
            self.assertFalse(attempted.applied)
            self.assertEqual(tree_bytes(workspace), before)

    def test_concurrent_workspace_change_aborts_before_manifest_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models")
            complete_specification_evidence(workspace)
            before = tree_bytes(workspace)

            with (
                mock.patch(
                    "model_lab.lifecycle._workspace_fingerprint",
                    side_effect=("before", "after"),
                ),
                self.assertRaises(WorkspaceError) as raised,
            ):
                advance_workspace(workspace, apply=True)

            self.assertEqual(raised.exception.code, "workspace_changed")
            self.assertEqual(tree_bytes(workspace), before)

    def test_status_and_advance_cli_return_machine_readable_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = create_workspace(Path(temporary) / "models", "cli-lifecycle")
            complete_specification_evidence(workspace)

            status = run_model_lab("status", str(workspace), "--format", "json")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stderr, "")
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["current_state"], "draft")
            self.assertEqual(status_payload["next_state"], "specified")
            self.assertTrue(status_payload["can_advance"])
            self.assertFalse(status_payload["applied"])

            planned = run_model_lab("advance", str(workspace), "--format", "json")
            self.assertEqual(planned.returncode, 0, planned.stderr)
            planned_payload = json.loads(planned.stdout)
            self.assertTrue(planned_payload["can_advance"])
            self.assertFalse(planned_payload["applied"])
            self.assertEqual(lifecycle_state(workspace), "draft")

            applied = run_model_lab(
                "advance",
                str(workspace),
                "--apply",
                "--format",
                "json",
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_payload = json.loads(applied.stdout)
            self.assertTrue(applied_payload["applied"])
            self.assertEqual(lifecycle_state(workspace), "specified")


if __name__ == "__main__":
    unittest.main()
