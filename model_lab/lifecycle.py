from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml  # type: ignore[import-untyped]

from model_lab.decisions import DecisionRegister, InteractionDecision, load_decision_register
from model_lab.draft_upgrade import DRAFT_UPGRADE_TEMPLATE_ID, validate_draft_upgrade_lineage
from model_lab.online_acceptance import verify_online_acceptance
from model_lab.manifest import ManifestError, validate_manifest
from model_lab.maintenance import CONTRACT_UPGRADE_WORKFLOW_ID, MAINTENANCE_WORKFLOW_ID, validate_maintenance_lineage
from model_lab.path_safety import (
    first_link_or_junction_component,
    is_link_or_junction,
)
from model_lab.release_gate import off_flow_issues, scan_workspace_tree
from model_lab.input_contract import validate_inputs
from model_lab.runtime_contract import (
    RuntimeCheckReport,
    check_build_environment,
    verify_mlflow_bundle,
)
from model_lab.scaffolding import (
    TEMPLATE_VERSION,
    WORKFLOW_ID,
    WorkspaceError,
    ensure_local_filesystem_path,
)


_HOST_MISMATCH_ISSUE_CODES = frozenset(
    {
        "build_python_mismatch",
        "build_platform_mismatch",
        "native_runtime_abi_mismatch",
    }
)


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    passed: bool
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.gate_id,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True)
class LifecycleReport:
    model_id: str
    manifest: str
    current_state: str
    next_state: str | None
    can_advance: bool
    applied: bool
    outcome: str
    gates: tuple[GateResult, ...]
    build_environment: RuntimeCheckReport
    decision_register: DecisionRegister
    open_questions: tuple[InteractionDecision, ...]
    pending_confirmations: tuple[InteractionDecision, ...]

    @property
    def next_command(self) -> str:
        """One command the operator should run next."""

        if self.can_advance:
            return f"model-lab advance {Path(self.manifest).parent} --apply"
        return f"model-lab status {Path(self.manifest).parent}"

    @property
    def build_host_status(self) -> str:
        if not self.build_environment.issues:
            return "ready"
        if all(
            issue.code in _HOST_MISMATCH_ISSUE_CODES
            for issue in self.build_environment.issues
        ):
            return "advisory"
        return "blocked"

    def as_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "manifest": self.manifest,
            "current_state": self.current_state,
            "next_state": self.next_state,
            "can_advance": self.can_advance,
            "applied": self.applied,
            "outcome": self.outcome,
            "status_scope": "local_lifecycle_only",
            "gates": [gate.as_dict() for gate in self.gates],
            "open_questions": [item.as_dict() for item in self.open_questions],
            "pending_confirmations": [
                item.as_dict() for item in self.pending_confirmations
            ],
            "decision_issues": [
                issue.as_dict() for issue in self.decision_register.issues
            ],
            "decision_summary": self.decision_register.summary(),
            "build_environment": self.build_environment.as_dict(),
            "build_host_status": self.build_host_status,
            "next_command": self.next_command,
            "safety": {"remote_access": False, "remote_writes": False},
        }

    def as_text(self) -> str:
        next_state = self.next_state or "<none>"
        lines = [
            f"{self.model_id}: {self.outcome.upper()}",
            f"Current state: {self.current_state}",
            f"Next state: {next_state}",
            f"Can advance: {'yes' if self.can_advance else 'no'}",
        ]
        if self.next_state is not None:
            lines.append(
                f"Decision scope: {self.current_state} -> {self.next_state}; "
                "later-stage decisions remain in decisions.yaml until relevant."
            )
        if self.current_state == "releasable" and self.outcome == "manual":
            lines.append(
                "Status scope: local archive only; this command does not check "
                "platform publication. Business-platform task invocation and "
                "automatic scheduling are not MLflow upload or local archive gates."
            )
        lines.extend(
            f"- [{'PASS' if gate.passed else 'BLOCK'}] {gate.gate_id}: {gate.message}"
            for gate in self.gates
        )
        lines.append(f"Next command: {self.next_command}")
        lines.append("Open questions:")
        if self.open_questions:
            for item in self.open_questions:
                lines.append(f"- {item.decision_id}: {item.question}")
                if item.recommendation:
                    lines.append(f"  Recommended: {item.recommendation}")
        else:
            lines.append("- none")
        lines.append("Pending confirmations:")
        if self.pending_confirmations:
            for item in self.pending_confirmations:
                lines.append(f"- {item.decision_id}: {item.question}")
                if item.recommendation:
                    lines.append(f"  Recommended: {item.recommendation}")
        else:
            lines.append("- none")
        if self.decision_register.issues:
            lines.append("Decision register issues:")
            lines.extend(
                f"- {issue.code}: {issue.message}"
                for issue in self.decision_register.issues
            )
        if self.current_state in {"releasable", "released"}:
            summary = self.decision_register.summary()
            lines.append(f"Decision summary SHA-256: {summary['sha256'] or '<invalid>'}")
        environment_text = self.build_environment.as_text()
        if self.build_host_status == "advisory":
            environment_text = environment_text.replace(
                "Runtime environment: BLOCK",
                "Current build host: ADVISORY",
                1,
            )
            environment_text += (
                "\nThis host mismatch does not block model design; build the deployment "
                "bundle on the declared target platform."
            )
        lines.append(environment_text)
        lines.append(
            "Safety: local manifest update only; no remote writes"
            if self.applied
            else "Safety: read-only; no local or remote writes"
        )
        return "\n".join(lines)


def _workflow_document(workflow_id: str = WORKFLOW_ID) -> Mapping[str, Any]:
    if workflow_id not in {WORKFLOW_ID, MAINTENANCE_WORKFLOW_ID, CONTRACT_UPGRADE_WORKFLOW_ID}:
        raise WorkspaceError("workflow_invalid", "unsupported lifecycle workflow")
    resource = files("model_lab").joinpath("workflows", f"{workflow_id}.yaml")
    try:
        document = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise WorkspaceError(
            "workflow_unavailable", "model lifecycle workflow cannot be loaded"
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("states"), Mapping
    ):
        raise WorkspaceError(
            "workflow_invalid", "model lifecycle workflow has an invalid shape"
        )
    return document


def _manifest_path(path: str | Path) -> Path:
    candidate = ensure_local_filesystem_path(path, "workspace")
    try:
        component = first_link_or_junction_component(candidate)
        if component is not None:
            raise WorkspaceError(
                "unsafe_workspace_path",
                "workspace path must not pass through a link or junction: "
                f"{component}",
            )
        manifest = candidate / "model.yaml" if candidate.is_dir() else candidate
        if not manifest.is_file():
            raise WorkspaceError(
                "manifest_not_found", f"workspace manifest does not exist: {manifest}"
            )
        component = first_link_or_junction_component(manifest)
        if component is not None:
            raise WorkspaceError(
                "unsafe_workspace_path",
                "workspace path must not pass through a link or junction: "
                f"{component}",
            )
        return manifest
    except OSError as error:
        raise WorkspaceError(
            "workspace_unreadable", "workspace path cannot be inspected safely"
        ) from error


def _load_document(manifest: Path) -> Mapping[str, Any]:
    try:
        # Run the manifest module's strict YAML loader first so duplicate keys
        # cannot be silently collapsed by the convenience parse below.
        validate_manifest(manifest)
    except ManifestError as error:
        raise WorkspaceError(error.code, str(error)) from error
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise WorkspaceError(
            "manifest_unreadable", f"workspace manifest cannot be read: {manifest}"
        ) from error
    if not isinstance(document, Mapping):
        raise WorkspaceError(
            "manifest_invalid", "workspace manifest must be a YAML object"
        )
    return document


def _mapping_path(document: object, dotted_path: str) -> object | None:
    value = document
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _safe_workspace_file(workspace: Path, relative: str) -> Path | None:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    try:
        workspace_root = workspace.resolve()
        candidate = (workspace / relative_path).resolve(strict=False)
    except OSError:
        return None
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        return None
    return candidate


def _gate_manifest_valid(manifest: Path) -> GateResult:
    try:
        report = validate_manifest(manifest)
    except ManifestError as error:
        return GateResult("manifest-valid", False, f"{error.code}: {error}")
    if report.valid:
        return GateResult("manifest-valid", True, "asset contract is valid")
    first = report.issues[0]
    return GateResult(
        "manifest-valid",
        False,
        f"{len(report.issues)} issue(s); first is {first.code} at {first.path}",
    )


def _gate_list_non_empty(
    document: Mapping[str, Any], gate: Mapping[str, Any]
) -> GateResult:
    paths = gate.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        return GateResult("manifest-list-non-empty", False, "workflow gate is invalid")
    missing = [
        path
        for path in paths
        if not isinstance(_mapping_path(document, path), list)
        or not _mapping_path(document, path)
    ]
    if missing:
        return GateResult(
            "manifest-list-non-empty",
            False,
            "complete non-empty manifest lists: " + ", ".join(missing),
        )
    return GateResult(
        "manifest-list-non-empty", True, "required manifest lists are complete"
    )


def _gate_placeholders_absent(workspace: Path, gate: Mapping[str, Any]) -> GateResult:
    relative = gate.get("file")
    markers = gate.get("markers")
    if (
        not isinstance(relative, str)
        or not isinstance(markers, list)
        or not all(isinstance(marker, str) for marker in markers)
    ):
        return GateResult("placeholders-absent", False, "workflow gate is invalid")
    target = _safe_workspace_file(workspace, relative)
    if target is None or not target.is_file() or target.is_symlink():
        return GateResult(
            "placeholders-absent",
            False,
            f"required evidence file is missing: {relative}",
        )
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return GateResult(
            "placeholders-absent", False, f"evidence file is unreadable: {relative}"
        )
    if not content.strip():
        return GateResult(
            "placeholders-absent", False, f"evidence file is empty: {relative}"
        )
    remaining = [marker for marker in markers if f"MODEL-LAB-TODO:{marker}" in content]
    if remaining:
        return GateResult(
            "placeholders-absent",
            False,
            f"complete {relative}: " + ", ".join(remaining),
        )
    return GateResult(
        "placeholders-absent", True, f"required evidence is complete: {relative}"
    )


_HTML_COMMENT = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_PLACEHOLDER_TEXT = re.compile(
    r"\b(?:TODO|TBD|PLACEHOLDER)\b|待补|占位",
    flags=re.IGNORECASE,
)


def _read_evidence(
    workspace: Path,
    relative: str,
    gate_id: str,
) -> tuple[str | None, GateResult | None]:
    target = _safe_workspace_file(workspace, relative)
    if (
        target is None
        or is_link_or_junction(target)
        or not target.is_file()
    ):
        return None, GateResult(
            gate_id, False, f"required evidence file is missing: {relative}"
        )
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, GateResult(
            gate_id, False, f"evidence file is unreadable: {relative}"
        )
    if not content.strip():
        return None, GateResult(gate_id, False, f"evidence file is empty: {relative}")
    return content, None


def _markdown_section(content: str, heading: str) -> str | None:
    lines = content.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    if start is None:
        return None
    heading_level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip()
        if not stripped.startswith("#"):
            continue
        next_level = len(stripped) - len(stripped.lstrip("#"))
        if next_level <= heading_level:
            end = index
            break
    body = "\n".join(lines[start + 1 : end])
    return _HTML_COMMENT.sub("", body).strip()


def _gate_markdown_sections_complete(
    workspace: Path,
    gate: Mapping[str, Any],
) -> GateResult:
    relative = gate.get("file")
    sections = gate.get("sections")
    minimum = gate.get("minimum_characters", 10)
    if (
        not isinstance(relative, str)
        or not isinstance(sections, list)
        or not all(isinstance(section, str) for section in sections)
        or not isinstance(minimum, int)
        or minimum < 1
    ):
        return GateResult(
            "markdown-sections-complete", False, "workflow gate is invalid"
        )
    content, error = _read_evidence(workspace, relative, "markdown-sections-complete")
    if error is not None or content is None:
        assert error is not None
        return error
    incomplete: list[str] = []
    for heading in sections:
        body = _markdown_section(content, heading)
        if (
            body is None
            or len(body) < minimum
            or _PLACEHOLDER_TEXT.search(body) is not None
        ):
            incomplete.append(heading)
    if incomplete:
        return GateResult(
            "markdown-sections-complete",
            False,
            f"complete substantive sections in {relative}: " + ", ".join(incomplete),
        )
    return GateResult(
        "markdown-sections-complete",
        True,
        f"required evidence sections are complete: {relative}",
    )


def _raise_name(node: ast.Raise) -> str | None:
    exception = node.exc
    if isinstance(exception, ast.Call):
        exception = exception.func
    if isinstance(exception, ast.Name):
        return exception.id
    if isinstance(exception, ast.Attribute):
        return exception.attr
    return None


def _constant_truth(value: ast.expr) -> bool | None:
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
        operand = _constant_truth(value.operand)
        return None if operand is None else not operand
    if isinstance(value, ast.BoolOp):
        operands = [_constant_truth(operand) for operand in value.values]
        if isinstance(value.op, ast.And):
            if False in operands:
                return False
            return True if all(operand is True for operand in operands) else None
        if isinstance(value.op, ast.Or):
            if True in operands:
                return True
            return False if all(operand is False for operand in operands) else None
    if isinstance(value, ast.Compare):
        try:
            left = ast.literal_eval(value.left)
            comparators = [ast.literal_eval(item) for item in value.comparators]
            results: list[bool] = []
            for operation, right in zip(value.ops, comparators, strict=True):
                if isinstance(operation, ast.Eq):
                    result = left == right
                elif isinstance(operation, ast.NotEq):
                    result = left != right
                elif isinstance(operation, ast.Lt):
                    result = left < right
                elif isinstance(operation, ast.LtE):
                    result = left <= right
                elif isinstance(operation, ast.Gt):
                    result = left > right
                elif isinstance(operation, ast.GtE):
                    result = left >= right
                elif isinstance(operation, ast.In):
                    result = left in right
                elif isinstance(operation, ast.NotIn):
                    result = left not in right
                elif isinstance(operation, ast.Is):
                    result = left is right
                elif isinstance(operation, ast.IsNot):
                    result = left is not right
                else:
                    return None
                results.append(result)
                left = right
            return all(results)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return None
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if isinstance(
        literal,
        (bool, int, float, complex, str, bytes, tuple, list, set, dict, type(None)),
    ):
        return bool(literal)
    return None


_FLOW_NORMAL = "normal"
_FLOW_EXIT = "exit"
_FLOW_BREAK = "break"
_FLOW_CONTINUE = "continue"


def _block_flow(
    statements: list[ast.stmt],
) -> tuple[tuple[ast.stmt, ...], frozenset[str]]:
    reachable: list[ast.stmt] = []
    outcomes: set[str] = set()
    falls_through = True
    for statement in statements:
        if not falls_through:
            break
        statement_reachable, statement_outcomes = _statement_flow(statement)
        reachable.extend(statement_reachable)
        outcomes.update(statement_outcomes - {_FLOW_NORMAL})
        falls_through = _FLOW_NORMAL in statement_outcomes
    if falls_through:
        outcomes.add(_FLOW_NORMAL)
    return tuple(reachable), frozenset(outcomes)


def _statement_flow(
    statement: ast.stmt,
) -> tuple[tuple[ast.stmt, ...], frozenset[str]]:
    if isinstance(statement, (ast.Return, ast.Raise)):
        return (statement,), frozenset({_FLOW_EXIT})
    if isinstance(statement, ast.Assert) and _constant_truth(statement.test) is False:
        return (statement,), frozenset({_FLOW_EXIT})
    if isinstance(statement, ast.Break):
        return (statement,), frozenset({_FLOW_BREAK})
    if isinstance(statement, ast.Continue):
        return (statement,), frozenset({_FLOW_CONTINUE})
    if isinstance(statement, ast.If):
        truth = _constant_truth(statement.test)
        if truth is True:
            return _block_flow(statement.body)
        if truth is False:
            return _block_flow(statement.orelse)
        body, body_outcomes = _block_flow(statement.body)
        alternative, alternative_outcomes = _block_flow(statement.orelse)
        return body + alternative, body_outcomes | alternative_outcomes
    if isinstance(statement, ast.While):
        truth = _constant_truth(statement.test)
        if truth is False:
            return _block_flow(statement.orelse)
        body, body_outcomes = _block_flow(statement.body)
        propagated = {outcome for outcome in body_outcomes if outcome == _FLOW_EXIT}
        if _FLOW_BREAK in body_outcomes:
            propagated.add(_FLOW_NORMAL)
        if truth is True:
            return body, frozenset(propagated)
        alternative, alternative_outcomes = _block_flow(statement.orelse)
        propagated.update(alternative_outcomes)
        return body + alternative, frozenset(propagated)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        body, body_outcomes = _block_flow(statement.body)
        alternative, alternative_outcomes = _block_flow(statement.orelse)
        propagated = {outcome for outcome in body_outcomes if outcome == _FLOW_EXIT}
        if _FLOW_BREAK in body_outcomes:
            propagated.add(_FLOW_NORMAL)
        propagated.update(alternative_outcomes)
        return body + alternative, frozenset(propagated)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return _block_flow(statement.body)
    if isinstance(statement, ast.Try):
        body, body_outcomes = _block_flow(statement.body)
        reachable = list(body)
        outcomes = set(body_outcomes - {_FLOW_NORMAL})
        if _FLOW_NORMAL in body_outcomes:
            alternative, alternative_outcomes = _block_flow(statement.orelse)
            reachable.extend(alternative)
            outcomes.update(alternative_outcomes)
        for handler in statement.handlers:
            handler_body, handler_outcomes = _block_flow(handler.body)
            reachable.extend(handler_body)
            outcomes.update(handler_outcomes)
        final, final_outcomes = _block_flow(statement.finalbody)
        reachable.extend(final)
        if statement.finalbody:
            final_abrupt = final_outcomes - {_FLOW_NORMAL}
            if _FLOW_NORMAL not in final_outcomes:
                outcomes = set(final_abrupt)
            else:
                outcomes.update(final_abrupt)
        return tuple(reachable), frozenset(outcomes)
    return (statement,), frozenset({_FLOW_NORMAL})


def _reachable_block(
    statements: list[ast.stmt],
) -> tuple[tuple[ast.stmt, ...], bool]:
    reachable, outcomes = _block_flow(statements)
    return reachable, _FLOW_NORMAL not in outcomes


def _reachable_statements(statements: list[ast.stmt]) -> tuple[ast.stmt, ...]:
    return _reachable_block(statements)[0]


def _is_assertion_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Assert)
        and _constant_truth(statement.test) is not False
    ) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr.startswith("assert")
    )


def _is_guaranteed_failure_statement(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Raise) or (
        isinstance(statement, ast.Assert) and _constant_truth(statement.test) is False
    )


def _function_has_implementation(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    require_non_none_return: bool,
    require_assertion: bool,
) -> bool:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    reachable = _reachable_statements(body)
    if not any(
        not isinstance(statement, (ast.Pass, ast.Raise)) for statement in reachable
    ):
        return False
    if require_non_none_return and not any(
        isinstance(statement, ast.Return)
        and statement.value is not None
        and not (
            isinstance(statement.value, ast.Constant) and statement.value.value is None
        )
        for statement in reachable
    ):
        return False
    if require_assertion and not any(map(_is_assertion_statement, reachable)):
        return False
    return True


class _ModuleBindingFinder(ast.NodeVisitor):
    def __init__(self, name: str) -> None:
        self.name = name
        self.found = False

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == self.name and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.found = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name == self.name:
            self.found = True

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        if node.name == self.name:
            self.found = True

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if node.name == self.name:
            self.found = True

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_alias(self, node: ast.alias) -> None:
        bound = node.asname or node.name.split(".", maxsplit=1)[0]
        if bound == self.name:
            self.found = True

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.name == self.name:
            self.found = True
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:  # noqa: N802
        if node.name == self.name:
            self.found = True
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:  # noqa: N802
        if node.name == self.name:
            self.found = True

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:  # noqa: N802
        if node.rest == self.name:
            self.found = True
        self.generic_visit(node)


def _statement_may_bind_module_name(statement: ast.stmt, name: str) -> bool:
    finder = _ModuleBindingFinder(name)
    finder.visit(statement)
    return finder.found


def _gate_python_ready(workspace: Path, gate: Mapping[str, Any]) -> GateResult:
    relative = gate.get("file")
    required_functions = gate.get("required_functions", [])
    required_prefix = gate.get("required_function_prefix")
    forbidden_raises = gate.get("forbidden_raises", [])
    require_non_none_return = gate.get("require_non_none_return", False)
    require_assertion = gate.get("require_assertion", False)
    if (
        not isinstance(relative, str)
        or not isinstance(required_functions, list)
        or not all(isinstance(name, str) for name in required_functions)
        or (required_prefix is not None and not isinstance(required_prefix, str))
        or not isinstance(forbidden_raises, list)
        or not all(isinstance(name, str) for name in forbidden_raises)
        or not isinstance(require_non_none_return, bool)
        or not isinstance(require_assertion, bool)
    ):
        return GateResult("python-ready", False, "workflow gate is invalid")
    content, error = _read_evidence(workspace, relative, "python-ready")
    if error is not None or content is None:
        assert error is not None
        return error
    if _PLACEHOLDER_TEXT.search(content) is not None:
        return GateResult("python-ready", False, f"remove placeholders from {relative}")
    try:
        tree = ast.parse(content, filename=relative)
    except SyntaxError:
        return GateResult("python-ready", False, f"Python is invalid: {relative}")
    candidates: dict[
        str,
        tuple[int, ast.FunctionDef | ast.AsyncFunctionDef],
    ] = {}
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates[node.name] = (index, node)
    functions = {
        name: node
        for name, (index, node) in candidates.items()
        if not any(
            _statement_may_bind_module_name(statement, name)
            for statement in tree.body[index + 1 :]
        )
    }
    missing = [
        name
        for name in required_functions
        if name not in functions
        or not _function_has_implementation(
            functions[name],
            require_non_none_return=require_non_none_return,
            require_assertion=require_assertion,
        )
    ]
    if required_prefix is not None and not any(
        name.startswith(required_prefix)
        and _function_has_implementation(
            node,
            require_non_none_return=require_non_none_return,
            require_assertion=require_assertion,
        )
        for name, node in functions.items()
    ):
        missing.append(f"{required_prefix}*")
    raised_names: set[str] = set()
    for walked in ast.walk(tree):
        if not isinstance(walked, ast.Raise):
            continue
        name = _raise_name(walked)
        if name is not None and name in forbidden_raises:
            raised_names.add(name)
    raised = sorted(raised_names)
    if missing or raised:
        details = []
        if missing:
            details.append("implement " + ", ".join(missing))
        if raised:
            details.append("remove placeholder raises " + ", ".join(raised))
        return GateResult("python-ready", False, f"{relative}: " + "; ".join(details))
    return GateResult("python-ready", True, f"Python evidence is ready: {relative}")


def _gate_notebook_ready(workspace: Path, gate: Mapping[str, Any]) -> GateResult:
    relative = gate.get("file")
    if not isinstance(relative, str):
        return GateResult("notebook-ready", False, "workflow gate is invalid")
    content, error = _read_evidence(workspace, relative, "notebook-ready")
    if error is not None or content is None:
        assert error is not None
        return error
    if _PLACEHOLDER_TEXT.search(content) is not None:
        return GateResult(
            "notebook-ready", False, f"remove placeholders from {relative}"
        )
    try:
        document = json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        return GateResult(
            "notebook-ready", False, f"Notebook JSON is invalid: {relative}"
        )
    cells = document.get("cells") if isinstance(document, Mapping) else None
    code_cells: list[str] = []
    if isinstance(cells, list):
        for cell in cells:
            if not isinstance(cell, Mapping) or cell.get("cell_type") != "code":
                continue
            source = cell.get("source")
            if isinstance(source, list):
                source = "".join(item for item in source if isinstance(item, str))
            if isinstance(source, str) and source.strip():
                code_cells.append(source)
    if not code_cells:
        return GateResult(
            "notebook-ready", False, f"Notebook needs a non-empty code cell: {relative}"
        )
    meaningful = False
    for source in code_cells:
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError:
            return GateResult(
                "notebook-ready", False, f"Notebook contains invalid Python: {relative}"
            )
        reachable = _reachable_statements(tree.body)
        if any(
            (
                _function_has_implementation(
                    statement,
                    require_non_none_return=False,
                    require_assertion=False,
                )
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                else not isinstance(
                    statement,
                    (ast.Import, ast.ImportFrom, ast.Pass),
                )
                and not _is_guaranteed_failure_statement(statement)
                and not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                )
            )
            for statement in reachable
        ):
            meaningful = True
    if not meaningful:
        return GateResult(
            "notebook-ready",
            False,
            f"Notebook code cells contain no executable analysis: {relative}",
        )
    return GateResult("notebook-ready", True, f"Notebook evidence is ready: {relative}")


def _gate_scaffold_metadata_valid(workspace: Path) -> GateResult:
    relative = "model-lab.yaml"
    content, error = _read_evidence(workspace, relative, "scaffold-metadata-valid")
    if error is not None or content is None:
        assert error is not None
        return error
    try:
        document = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError):
        return GateResult(
            "scaffold-metadata-valid", False, "model-lab.yaml is invalid YAML"
        )
    template = document.get("template") if isinstance(document, Mapping) else None
    workflow = document.get("workflow") if isinstance(document, Mapping) else None
    if (
        not isinstance(template, Mapping)
        or template.get("id") not in {"new-model", "maintenance-model", "contract-upgrade-model", DRAFT_UPGRADE_TEMPLATE_ID}
        or template.get("version") != TEMPLATE_VERSION
        or not isinstance(workflow, Mapping)
        or workflow.get("id") != {
            "new-model": WORKFLOW_ID,
            "maintenance-model": MAINTENANCE_WORKFLOW_ID,
            "contract-upgrade-model": CONTRACT_UPGRADE_WORKFLOW_ID,
            DRAFT_UPGRADE_TEMPLATE_ID: WORKFLOW_ID,
        }.get(template.get("id"))
    ):
        return GateResult(
            "scaffold-metadata-valid",
            False,
            "model-lab.yaml does not match the supported template and workflow",
        )
    return GateResult(
        "scaffold-metadata-valid",
        True,
        "scaffold template and workflow versions are supported",
    )


def _gate_paths_exist(workspace: Path, gate: Mapping[str, Any]) -> GateResult:
    paths = gate.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        return GateResult("paths-exist", False, "workflow gate is invalid")
    missing: list[str] = []
    for relative in paths:
        target = _safe_workspace_file(workspace, relative)
        if target is None or not target.is_file() or target.is_symlink():
            missing.append(relative)
    if missing:
        return GateResult(
            "paths-exist", False, "required files are missing: " + ", ".join(missing)
        )
    return GateResult("paths-exist", True, "required files exist")


def _gate_sections_present(
    document: Mapping[str, Any], gate: Mapping[str, Any]
) -> GateResult:
    paths = gate.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        return GateResult(
            "manifest-sections-present", False, "workflow gate is invalid"
        )
    missing = [
        path
        for path in paths
        if not isinstance(_mapping_path(document, path), Mapping)
        or not _mapping_path(document, path)
    ]
    if missing:
        return GateResult(
            "manifest-sections-present",
            False,
            "complete manifest sections: " + ", ".join(missing),
        )
    return GateResult(
        "manifest-sections-present", True, "required manifest sections are complete"
    )


def _gate_artifacts_resolved(document: Mapping[str, Any]) -> GateResult:
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return GateResult("artifacts-resolved", False, "record at least one artifact")
    unresolved: list[str] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            unresolved.append(str(index))
            continue
        uri = artifact.get("uri")
        digest = artifact.get("sha256")
        archive_status = artifact.get("archive_status")
        if (
            not isinstance(uri, str)
            or "pending-archive" in uri
            or archive_status == "pending-archive"
            or not isinstance(digest, str)
            or digest == "0" * 64
        ):
            unresolved.append(str(index))
    if unresolved:
        return GateResult(
            "artifacts-resolved",
            False,
            "resolve artifact URI and digest at indexes: " + ", ".join(unresolved),
        )
    return GateResult("artifacts-resolved", True, "all artifacts are resolved")


def _gate_artifact_digests_recorded(document: Mapping[str, Any]) -> GateResult:
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return GateResult(
            "artifact-digests-recorded", False, "record at least one artifact"
        )
    unresolved: list[str] = []
    for index, artifact in enumerate(artifacts):
        digest = artifact.get("sha256") if isinstance(artifact, Mapping) else None
        if not isinstance(digest, str) or len(digest) != 64 or digest == "0" * 64:
            unresolved.append(str(index))
    if unresolved:
        return GateResult(
            "artifact-digests-recorded",
            False,
            "record non-placeholder artifact digests at indexes: "
            + ", ".join(unresolved),
        )
    return GateResult(
        "artifact-digests-recorded", True, "artifact digests are recorded"
    )


def _gate_workspace_safe(workspace: Path, document: Mapping[str, Any]) -> GateResult:
    model = document.get("model")
    state = model.get("lifecycle_state") if isinstance(model, Mapping) else ""
    issues = scan_workspace_tree(workspace) + (off_flow_issues(workspace, str(state)) if isinstance(state, str) else ())
    if issues:
        first = issues[0]
        safe_subject = first.as_dict()["subject"]
        return GateResult(
            "workspace-safe",
            False,
            f"{len(issues)} unsafe workspace item(s); first is "
            f"{first.rule} at {safe_subject}",
        )
    return GateResult(
        "workspace-safe",
        True,
        "current workspace contains no forbidden files or credential material",
    )




def _gate_mixed_inputs(workspace: Path) -> GateResult:
    report = validate_inputs(workspace)
    if report.issues:
        first = report.issues[0]
        return GateResult(
            "mixed-inputs-declared",
            False,
            f"{len(report.issues)} mixed-input issue(s); first is {first.code} at {first.input_name}: {first.detail}",
        )
    return GateResult(
        "mixed-inputs-declared",
        True,
        f"inputs.md mixed-source declarations are valid ({report.rows} rows)",
    )
def _evaluate_gates(
    manifest: Path,
    document: Mapping[str, Any],
    gate_definitions: object,
    decision_register: DecisionRegister,
) -> tuple[GateResult, ...]:
    if not isinstance(gate_definitions, list):
        return (GateResult("workflow", False, "state gate list is invalid"),)
    workspace = manifest.parent
    results: list[GateResult] = []
    for gate in gate_definitions:
        if not isinstance(gate, Mapping) or not isinstance(gate.get("check"), str):
            results.append(GateResult("workflow", False, "workflow gate is invalid"))
            continue
        check = gate["check"]
        if check == "workspace-safe":
            results.append(_gate_workspace_safe(workspace, document))
        elif check == "mixed-inputs-declared":
            results.append(_gate_mixed_inputs(workspace))
        elif check == "scaffold-metadata-valid":
            results.append(_gate_scaffold_metadata_valid(workspace))
        elif check == "manifest-valid":
            results.append(_gate_manifest_valid(manifest))
        elif check == "manifest-list-non-empty":
            results.append(_gate_list_non_empty(document, gate))
        elif check == "placeholders-absent":
            results.append(_gate_placeholders_absent(workspace, gate))
        elif check == "paths-exist":
            results.append(_gate_paths_exist(workspace, gate))
        elif check == "manifest-sections-present":
            results.append(_gate_sections_present(document, gate))
        elif check == "history-feature-adapter-ready":
            serving = document.get("serving")
            enabled = (
                isinstance(serving, Mapping)
                and serving.get("mode") == "point-code"
                and serving.get("feature_adapter") == "source-history-v1"
            )
            results.append(GateResult(
                check, enabled,
                "point-code history adapter is explicitly selected"
                if enabled else "contract upgrade requires serving.mode=point-code and feature_adapter=source-history-v1",
            ))
        elif check == "artifacts-resolved":
            results.append(_gate_artifacts_resolved(document))
        elif check == "artifact-digests-recorded":
            results.append(_gate_artifact_digests_recorded(document))
        elif check == "markdown-sections-complete":
            results.append(_gate_markdown_sections_complete(workspace, gate))
        elif check == "python-ready":
            results.append(_gate_python_ready(workspace, gate))
        elif check == "notebook-ready":
            results.append(_gate_notebook_ready(workspace, gate))
        elif check == "interaction-decisions-confirmed":
            transition = gate.get("transition")
            if not isinstance(transition, str):
                results.append(
                    GateResult(check, False, "workflow decision gate is invalid")
                )
            elif not decision_register.valid:
                decision_issue = decision_register.issues[0]
                results.append(
                    GateResult(
                        check,
                        False,
                        f"{decision_issue.code}: {decision_issue.message}",
                    )
                )
            else:
                blocking = tuple(
                    decision
                    for decision in decision_register.decisions
                    if decision.blocks_transition == transition
                    and decision.status == "open"
                    and (decision.level == "CONFIRM" or decision.required)
                )
                if blocking:
                    blocking_decision = blocking[0]
                    results.append(
                        GateResult(
                            check,
                            False,
                            (
                                f"{blocking_decision.decision_id}: "
                                f"{blocking_decision.question}"
                            ),
                        )
                    )
                else:
                    results.append(
                        GateResult(
                            check,
                            True,
                            f"required decisions for {transition} are confirmed",
                        )
                    )
        elif check == "runtime-contract-valid":
            runtime_report = check_build_environment(manifest)
            runtime_blocking = tuple(
                issue
                for issue in runtime_report.issues
                if issue.code not in _HOST_MISMATCH_ISSUE_CODES
            )
            if runtime_blocking:
                results.append(
                    GateResult(
                        check,
                        False,
                        (
                            f"{runtime_blocking[0].code}: "
                            f"{runtime_blocking[0].message}"
                        ),
                    )
                )
            else:
                message = "target environment and dependency lock are valid"
                if runtime_report.issues:
                    message += "; current build host mismatch is informational"
                results.append(GateResult(check, True, message))
        elif check == "mlflow-bundle-valid":
            bundle_report = verify_mlflow_bundle(
                manifest,
                workspace / "release-bundle" / "service",
            )
            serving = document.get("serving")
            if bundle_report.valid and isinstance(serving, Mapping) and serving.get("mode") == "point-code":
                online_valid, online_message = verify_online_acceptance(workspace, document)
                results.append(
                    GateResult(
                        check,
                        online_valid,
                        online_message,
                    )
                )
            elif bundle_report.valid:
                results.append(
                    GateResult(check, True, "offline MLflow bundle is complete")
                )
            else:
                bundle_issue = bundle_report.issues[0]
                results.append(
                    GateResult(
                        check,
                        False,
                        f"{bundle_issue.code}: {bundle_issue.message}",
                    )
                )
        else:
            results.append(
                GateResult(str(check), False, "workflow uses an unsupported gate")
            )
    return tuple(results)


def _workspace_report(path: str | Path) -> LifecycleReport:
    manifest = _manifest_path(path)
    document = _load_document(manifest)
    model = document.get("model")
    if not isinstance(model, Mapping):
        raise WorkspaceError("manifest_invalid", "manifest.model must be an object")
    model_id = model.get("id")
    state = model.get("lifecycle_state")
    if not isinstance(model_id, str) or not model_id.strip():
        raise WorkspaceError("manifest_invalid", "manifest.model.id is missing")
    if not isinstance(state, str):
        raise WorkspaceError(
            "manifest_invalid", "manifest.model.lifecycle_state is missing"
        )

    workflow_id = WORKFLOW_ID
    metadata_path = manifest.parent / "model-lab.yaml"
    if metadata_path.is_file() and not is_link_or_junction(metadata_path):
        try:
            metadata_for_workflow = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            metadata_for_workflow = None
        metadata_template = metadata_for_workflow.get("template") if isinstance(metadata_for_workflow, Mapping) else None
        if isinstance(metadata_template, Mapping) and metadata_template.get("id") == "maintenance-model":
            workflow_id = MAINTENANCE_WORKFLOW_ID
        elif isinstance(metadata_template, Mapping) and metadata_template.get("id") == "contract-upgrade-model":
            workflow_id = CONTRACT_UPGRADE_WORKFLOW_ID
    workflow = _workflow_document(workflow_id)
    states = workflow["states"]
    state_definition = states.get(state) if isinstance(states, Mapping) else None
    if not isinstance(state_definition, Mapping):
        raise WorkspaceError(
            "unsupported_lifecycle_state", f"workflow does not support state: {state}"
        )
    next_state_value = state_definition.get("next")
    next_state = next_state_value if isinstance(next_state_value, str) else None
    manual = state_definition.get("manual", False) is True
    decision_register = load_decision_register(
        manifest.parent,
        expected_model_id=model_id,
        required=manifest.parent.parent.name == "_drafts",
    )
    open_questions = decision_register.open_for(next_state, level="ASK")
    pending_confirmations = decision_register.open_for(next_state, level="CONFIRM")
    gates = _evaluate_gates(
        manifest,
        document,
        state_definition.get("gates", []),
        decision_register,
    )
    metadata_path = manifest.parent / "model-lab.yaml"
    if metadata_path.is_file() and not is_link_or_junction(metadata_path):
        try:
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            metadata = None
        template = metadata.get("template") if isinstance(metadata, Mapping) else None
        has_maintenance_template = isinstance(template, Mapping) and template.get("id") in {"maintenance-model", "contract-upgrade-model"}
        has_draft_upgrade_template = isinstance(template, Mapping) and template.get("id") == DRAFT_UPGRADE_TEMPLATE_ID
        has_active_identity = False
        if manifest.parent.parent.name == "_drafts":
            models_root = manifest.parent.parent.parent
            try:
                for sibling in models_root.iterdir():
                    if sibling.name == "_drafts" or not sibling.is_dir() or is_link_or_junction(sibling):
                        continue
                    active_manifest = sibling / "model.yaml"
                    if active_manifest.is_file() and not is_link_or_junction(active_manifest):
                        active_report = validate_manifest(active_manifest)
                        if not active_report.valid:
                            has_active_identity = True
                            break
                        if active_report.model_id.casefold() == model_id.casefold():
                            has_active_identity = True
                            break
            except (OSError, ManifestError):
                has_active_identity = True
        versioned_maintenance_name = manifest.parent.name == f"{model_id}-{model.get('version')}"
        if has_draft_upgrade_template:
            valid_lineage, message, _ = validate_draft_upgrade_lineage(manifest.parent)
            gates = (*gates, GateResult("draft-upgrade-lineage-valid", valid_lineage, message))
        elif has_maintenance_template or has_active_identity or versioned_maintenance_name:
            valid_lineage, message, _ = validate_maintenance_lineage(manifest.parent)
            gates = (*gates, GateResult("maintenance-lineage-valid", valid_lineage, message))
    build_environment = check_build_environment(manifest)
    gates_pass = all(gate.passed for gate in gates)
    can_advance = next_state is not None and not manual and gates_pass
    if next_state is None:
        outcome = "terminal"
    elif manual:
        outcome = "manual"
    elif can_advance:
        outcome = "ready"
    else:
        outcome = "blocked"
    try:
        resolved_manifest = str(manifest.resolve())
    except OSError as error:
        raise WorkspaceError(
            "workspace_unreadable", "workspace manifest path cannot be resolved"
        ) from error
    return LifecycleReport(
        model_id=model_id,
        manifest=resolved_manifest,
        current_state=state,
        next_state=next_state,
        can_advance=can_advance,
        applied=False,
        outcome=outcome,
        gates=gates,
        build_environment=build_environment,
        decision_register=decision_register,
        open_questions=open_questions,
        pending_confirmations=pending_confirmations,
    )


def workspace_status(path: str | Path) -> LifecycleReport:
    """Report the current lifecycle state and the next stage gates."""

    return _workspace_report(path)


def _is_draft_workspace(manifest: Path) -> bool:
    return manifest.parent.parent.name == "_drafts"


def _best_effort_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _publish_noreplace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
        return
    os.link(source, destination)
    _best_effort_unlink(source)


def _verify_conditional_publish_support(
    root: Path,
    destination_root: Path,
    workspace_name: str,
) -> None:
    descriptor: int | None = None
    source: Path | None = None
    destination: Path | None = None
    try:
        descriptor, source_name = tempfile.mkstemp(
            prefix=f".{workspace_name}-hardlink-probe-source.",
            suffix=".tmp",
            dir=str(root),
        )
        os.close(descriptor)
        descriptor = None
        source = Path(source_name)
        descriptor, destination_name = tempfile.mkstemp(
            prefix=f".{workspace_name}-hardlink-probe-destination.",
            suffix=".tmp",
            dir=str(destination_root),
        )
        os.close(descriptor)
        descriptor = None
        destination = Path(destination_name)
        destination.unlink()
        _publish_noreplace(source, destination)
    except OSError as error:
        raise WorkspaceError(
            "atomic_update_unsupported",
            "the local filesystem does not support the no-overwrite operation "
            "required for a safe lifecycle update",
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _best_effort_unlink(destination)
        _best_effort_unlink(source)


def _atomic_write_manifest(
    manifest: Path,
    document: Mapping[str, Any],
    *,
    expected_fingerprint: str,
) -> None:
    content = yaml.safe_dump(
        dict(document),
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    descriptor: int | None = None
    temporary: Path | None = None
    claimed_original: Path | None = None
    try:
        if _workspace_fingerprint(manifest.parent) != expected_fingerprint:
            raise WorkspaceError(
                "workspace_changed",
                "workspace changed before the lifecycle update; retry",
            )
        temporary_root = manifest.parent.parent
        _verify_conditional_publish_support(
            temporary_root,
            manifest.parent,
            manifest.parent.name,
        )
        original_content = manifest.read_bytes()
        evidence_fingerprint = _workspace_fingerprint(
            manifest.parent,
            excluded=frozenset({"model.yaml"}),
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{manifest.parent.name}-{manifest.name}.",
            suffix=".tmp",
            dir=str(temporary_root),
        )
        os.close(descriptor)
        descriptor = None
        temporary = Path(temporary_name)
        temporary.write_bytes(content)
        if _workspace_fingerprint(manifest.parent) != expected_fingerprint:
            raise WorkspaceError(
                "workspace_changed",
                "workspace changed during the lifecycle update; retry",
            )
        _manifest_path(manifest)
        if _workspace_fingerprint(manifest.parent) != expected_fingerprint:
            raise WorkspaceError(
                "workspace_changed",
                "workspace changed before the atomic lifecycle write; retry",
            )

        descriptor, claimed_name = tempfile.mkstemp(
            prefix=f".{manifest.parent.name}-{manifest.name}-previous.",
            suffix=".tmp",
            dir=str(temporary_root),
        )
        os.close(descriptor)
        descriptor = None
        claimed_original = Path(claimed_name)
        os.replace(manifest, claimed_original)

        if claimed_original.read_bytes() != original_content:
            _restore_claimed_manifest(manifest, claimed_original)
            claimed_original = None
            raise WorkspaceError(
                "workspace_changed",
                "model.yaml changed while the lifecycle update was claiming it; "
                "the concurrent content was preserved",
            )
        if (
            _workspace_fingerprint(
                manifest.parent,
                excluded=frozenset({"model.yaml"}),
            )
            != evidence_fingerprint
        ):
            _restore_claimed_manifest(manifest, claimed_original)
            claimed_original = None
            raise WorkspaceError(
                "workspace_changed",
                "workspace evidence changed before the lifecycle manifest write; "
                "the original manifest was restored",
            )

        try:
            _publish_noreplace(temporary, manifest)
        except FileExistsError as error:
            recovery = claimed_original
            claimed_original = None
            raise WorkspaceError(
                "workspace_changed",
                "a concurrent writer recreated model.yaml; its content was preserved "
                f"and the previous manifest remains at {recovery}",
            ) from error
        _best_effort_unlink(temporary)
        temporary = None

        if manifest.read_bytes() != content:
            recovery = claimed_original
            claimed_original = None
            raise WorkspaceError(
                "workspace_changed",
                "model.yaml changed immediately after the lifecycle write; the "
                f"concurrent content was preserved and the previous manifest remains at {recovery}",
            )
        try:
            evidence_unchanged = (
                _workspace_fingerprint(
                    manifest.parent,
                    excluded=frozenset({"model.yaml"}),
                )
                == evidence_fingerprint
            )
        except WorkspaceError:
            evidence_unchanged = False
        if not evidence_unchanged:
            _rollback_claimed_manifest(
                manifest,
                claimed_original,
                installed_content=content,
                temporary_root=temporary_root,
            )
            claimed_original = None
            raise WorkspaceError(
                "workspace_changed",
                "workspace evidence changed during the atomic lifecycle write; "
                "the state update was rolled back",
            )
        obsolete_manifest = claimed_original
        claimed_original = None
        _best_effort_unlink(obsolete_manifest)
        _best_effort_unlink(temporary)
        temporary = None
    except WorkspaceError as error:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        if claimed_original is not None and error.code not in {
            "manifest_recovery_required",
            "manifest_rollback_failed",
        }:
            try:
                if manifest.exists():
                    _rollback_claimed_manifest(
                        manifest,
                        claimed_original,
                        installed_content=content,
                        temporary_root=manifest.parent.parent,
                    )
                else:
                    _restore_claimed_manifest(manifest, claimed_original)
                claimed_original = None
            except WorkspaceError as recovery_error:
                raise recovery_error from error
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        if claimed_original is not None:
            try:
                if manifest.exists():
                    _rollback_claimed_manifest(
                        manifest,
                        claimed_original,
                        installed_content=content,
                        temporary_root=manifest.parent.parent,
                    )
                else:
                    _restore_claimed_manifest(manifest, claimed_original)
                claimed_original = None
            except WorkspaceError as recovery_error:
                raise recovery_error from error
        recovery_note = (
            f"; previous manifest preserved at {claimed_original}"
            if claimed_original is not None
            else ""
        )
        raise WorkspaceError(
            "manifest_write_failed",
            f"lifecycle state could not be updated: {error}{recovery_note}",
        ) from error
    except BaseException as error:
        if descriptor is not None:
            os.close(descriptor)
        _best_effort_unlink(temporary)
        if claimed_original is not None:
            try:
                if manifest.exists():
                    _rollback_claimed_manifest(
                        manifest,
                        claimed_original,
                        installed_content=content,
                        temporary_root=manifest.parent.parent,
                    )
                else:
                    _restore_claimed_manifest(manifest, claimed_original)
                claimed_original = None
            except WorkspaceError as recovery_error:
                raise recovery_error from error
        raise


def _restore_claimed_manifest(manifest: Path, claimed: Path) -> None:
    try:
        _publish_noreplace(claimed, manifest)
    except FileExistsError as error:
        raise WorkspaceError(
            "manifest_recovery_required",
            "model.yaml was recreated concurrently; it was preserved and the "
            f"previous manifest remains at {claimed}",
        ) from error
    except OSError as error:
        raise WorkspaceError(
            "manifest_rollback_failed",
            "lifecycle state recovery failed; the previous manifest remains at "
            f"{claimed}",
        ) from error


def _rollback_claimed_manifest(
    manifest: Path,
    claimed_original: Path,
    *,
    installed_content: bytes,
    temporary_root: Path,
) -> None:
    descriptor: int | None = None
    displaced: Path | None = None
    try:
        descriptor, displaced_name = tempfile.mkstemp(
            prefix=f".{manifest.parent.name}-{manifest.name}-rollback.",
            suffix=".tmp",
            dir=str(temporary_root),
        )
        os.close(descriptor)
        descriptor = None
        displaced = Path(displaced_name)
        os.replace(manifest, displaced)
        if displaced.read_bytes() != installed_content:
            _restore_claimed_manifest(manifest, displaced)
            displaced = None
            raise WorkspaceError(
                "manifest_recovery_required",
                "model.yaml changed during rollback; the concurrent content was "
                f"preserved and the previous manifest remains at {claimed_original}",
            )
        _restore_claimed_manifest(manifest, claimed_original)
        _best_effort_unlink(displaced)
        displaced = None
    except WorkspaceError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        recovery_note = (
            f"; displaced manifest preserved at {displaced}"
            if displaced is not None
            else ""
        )
        raise WorkspaceError(
            "manifest_rollback_failed",
            "lifecycle state rollback failed; the previous manifest remains at "
            f"{claimed_original}{recovery_note}",
        ) from error


def _workspace_fingerprint(
    workspace: Path,
    *,
    excluded: frozenset[str] = frozenset(),
) -> str:
    digest = hashlib.sha256()
    try:
        pending = [workspace]
        paths: list[Path] = []
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                if is_link_or_junction(path):
                    raise WorkspaceError(
                        "unsafe_workspace_path",
                        "workspace changed to include a link or junction",
                    )
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    paths.append(path)
                else:
                    raise WorkspaceError(
                        "unsafe_workspace_path",
                        "workspace changed to include an unsupported file type",
                    )
        paths.sort(key=lambda path: path.relative_to(workspace).as_posix())
        for path in paths:
            relative_text = path.relative_to(workspace).as_posix()
            if relative_text in excluded:
                continue
            relative = relative_text.encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            content = path.read_bytes()
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    except OSError as error:
        raise WorkspaceError(
            "workspace_unreadable", "workspace snapshot cannot be read safely"
        ) from error
    return digest.hexdigest()


def advance_workspace(
    path: str | Path,
    *,
    apply: bool = False,
) -> LifecycleReport:
    """Plan or apply exactly one permitted lifecycle transition."""

    if apply:
        from model_lab.environment import ensure_external_workspace
        try:
            ensure_external_workspace(path)
        except ValueError as error:
            raise WorkspaceError(getattr(error, "code", "unsafe_workspace_path"), str(error)) from error

    report = _workspace_report(path)
    manifest = Path(report.manifest)
    if not _is_draft_workspace(manifest):
        gate = GateResult(
            "draft-workspace-only",
            False,
            "automatic lifecycle changes are limited to models/_drafts workspaces",
        )
        return replace(
            report,
            can_advance=False,
            outcome="blocked",
            gates=report.gates + (gate,),
        )
    if not report.can_advance or not apply:
        return report

    fingerprint = _workspace_fingerprint(manifest.parent)
    confirmed = _workspace_report(manifest)
    if (
        confirmed.current_state != report.current_state
        or confirmed.next_state != report.next_state
        or not confirmed.can_advance
        or _workspace_fingerprint(manifest.parent) != fingerprint
    ):
        raise WorkspaceError(
            "workspace_changed",
            "workspace changed while lifecycle gates were being evaluated; retry",
        )

    document = _load_document(manifest)
    if _workspace_fingerprint(manifest.parent) != fingerprint:
        raise WorkspaceError(
            "workspace_changed",
            "workspace changed before the lifecycle update; retry",
        )
    model = document.get("model")
    assert isinstance(model, Mapping)
    mutable_document = dict(document)
    mutable_model = dict(model)
    mutable_model["lifecycle_state"] = confirmed.next_state
    mutable_document["model"] = mutable_model
    _atomic_write_manifest(
        manifest,
        mutable_document,
        expected_fingerprint=fingerprint,
    )
    advanced = _workspace_report(manifest)
    return replace(advanced, applied=True, outcome="advanced")
