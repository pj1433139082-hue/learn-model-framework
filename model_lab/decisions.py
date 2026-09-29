from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

from model_lab.path_safety import is_link_or_junction


_MAX_REGISTER_BYTES: Final = 512 * 1024
_LEVELS: Final = frozenset({"ASK", "CONFIRM"})
_STATUSES: Final = frozenset({"open", "confirmed", "superseded"})
_TRANSITIONS: Final = frozenset({"specified", "built", "validated", "releasable"})
_SHORTHAND_ANSWERS: Final = frozenset({"按推荐", "全部按推荐"})


class _DuplicateDecisionKey(yaml.YAMLError):
    pass


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a decision mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise _DuplicateDecisionKey(str(key))
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class DecisionIssue:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class InteractionDecision:
    decision_id: str
    stage: str
    blocks_transition: str
    level: str
    required: bool
    question: str
    recommendation: str | None
    status: str
    answer: str | None
    confirmed_by: str | None
    confirmed_at: str | None
    evidence: str
    superseded_by: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.decision_id,
            "stage": self.stage,
            "blocks_transition": self.blocks_transition,
            "level": self.level,
            "required": self.required,
            "question": self.question,
            "recommendation": self.recommendation,
            "status": self.status,
            "answer": self.answer,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "evidence": self.evidence,
        }
        if self.superseded_by is not None:
            payload["superseded_by"] = self.superseded_by
        return payload


@dataclass(frozen=True)
class DecisionRegister:
    valid: bool
    decisions: tuple[InteractionDecision, ...]
    issues: tuple[DecisionIssue, ...]

    def open_for(
        self, transition: str | None, *, level: str
    ) -> tuple[InteractionDecision, ...]:
        if transition is None:
            return ()
        return tuple(
            decision
            for decision in self.decisions
            if decision.blocks_transition == transition
            and decision.level == level
            and decision.status == "open"
        )

    def summary(self) -> dict[str, object]:
        canonical = json.dumps(
            [
                decision.as_dict()
                for decision in sorted(
                    self.decisions, key=lambda item: item.decision_id
                )
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "valid": self.valid,
            "confirmed_count": sum(
                decision.status == "confirmed" for decision in self.decisions
            ),
            "open_count": sum(
                decision.status == "open" for decision in self.decisions
            ),
            "superseded_count": sum(
                decision.status == "superseded" for decision in self.decisions
            ),
            "sha256": hashlib.sha256(canonical).hexdigest() if self.valid else None,
        }


def _non_empty_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _confirmed_at_valid(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _decision_from_mapping(
    value: Mapping[str, Any], index: int
) -> tuple[InteractionDecision | None, tuple[DecisionIssue, ...]]:
    prefix = f"decisions[{index}]"
    issues: list[DecisionIssue] = []
    strings: dict[str, str | None] = {
        field: _non_empty_string(value.get(field))
        for field in (
            "id",
            "stage",
            "blocks_transition",
            "level",
            "question",
            "status",
            "evidence",
        )
    }
    for field, parsed in strings.items():
        if parsed is None:
            issues.append(
                DecisionIssue(
                    "decision_field_invalid",
                    f"{prefix}.{field} must be a non-empty string",
                )
            )
    required = value.get("required")
    if not isinstance(required, bool):
        issues.append(
            DecisionIssue(
                "decision_field_invalid", f"{prefix}.required must be boolean"
            )
        )
    level = strings["level"]
    if level is not None and level not in _LEVELS:
        issues.append(
            DecisionIssue(
                "decision_level_invalid",
                f"{prefix}.level must be ASK or CONFIRM",
            )
        )
    transition = strings["blocks_transition"]
    if transition is not None and transition not in _TRANSITIONS:
        issues.append(
            DecisionIssue(
                "decision_transition_invalid",
                f"{prefix}.blocks_transition is unsupported: {transition}",
            )
        )
    status = strings["status"]
    if status is not None and status not in _STATUSES:
        issues.append(
            DecisionIssue(
                "decision_status_invalid",
                f"{prefix}.status is unsupported: {status}",
            )
        )

    recommendation = _non_empty_string(value.get("recommendation"))
    answer = _non_empty_string(value.get("answer"))
    confirmed_by = _non_empty_string(value.get("confirmed_by"))
    confirmed_at = _non_empty_string(value.get("confirmed_at"))
    superseded_by = _non_empty_string(value.get("superseded_by"))
    if status in {"confirmed", "superseded"}:
        if recommendation is None:
            issues.append(
                DecisionIssue(
                    "decision_confirmation_incomplete",
                    f"{prefix}.recommendation is required after confirmation",
                )
            )
        if answer is None or answer in _SHORTHAND_ANSWERS:
            issues.append(
                DecisionIssue(
                    "decision_confirmation_incomplete",
                    f"{prefix}.answer must contain the resolved answer, not shorthand",
                )
            )
        if confirmed_by is None:
            issues.append(
                DecisionIssue(
                    "decision_confirmation_incomplete",
                    f"{prefix}.confirmed_by is required after confirmation",
                )
            )
        if confirmed_at is None or not _confirmed_at_valid(confirmed_at):
            issues.append(
                DecisionIssue(
                    "decision_confirmation_incomplete",
                    f"{prefix}.confirmed_at must be an ISO 8601 timestamp with timezone",
                )
            )
    if status == "superseded" and superseded_by is None:
        issues.append(
            DecisionIssue(
                "decision_supersession_invalid",
                f"{prefix}.superseded_by is required for superseded decisions",
            )
        )
    if issues:
        return None, tuple(issues)
    assert all(parsed is not None for parsed in strings.values())
    assert isinstance(required, bool)
    return (
        InteractionDecision(
            decision_id=strings["id"] or "",
            stage=strings["stage"] or "",
            blocks_transition=strings["blocks_transition"] or "",
            level=strings["level"] or "",
            required=required,
            question=strings["question"] or "",
            recommendation=recommendation,
            status=strings["status"] or "",
            answer=answer,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
            evidence=strings["evidence"] or "",
            superseded_by=superseded_by,
        ),
        (),
    )


def load_decision_register(
    workspace: Path,
    *,
    expected_model_id: str,
    required: bool,
) -> DecisionRegister:
    path = workspace / "decisions.yaml"
    if not path.exists():
        if required:
            issue = DecisionIssue(
                "decision_register_missing",
                "draft workspace must contain decisions.yaml",
            )
            return DecisionRegister(False, (), (issue,))
        return DecisionRegister(True, (), ())
    if is_link_or_junction(path):
        issue = DecisionIssue(
            "decision_register_unsafe", "decisions.yaml must not be a link or junction"
        )
        return DecisionRegister(False, (), (issue,))
    try:
        if path.stat().st_size > _MAX_REGISTER_BYTES:
            issue = DecisionIssue(
                "decision_register_too_large", "decisions.yaml exceeds 512 KiB"
            )
            return DecisionRegister(False, (), (issue,))
        document = yaml.load(
            path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader
        )
    except _DuplicateDecisionKey:
        issue = DecisionIssue(
            "decision_register_duplicate_key",
            "decisions.yaml contains a duplicate mapping key",
        )
        return DecisionRegister(False, (), (issue,))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError):
        issue = DecisionIssue(
            "decision_register_invalid", "decisions.yaml is unreadable or invalid YAML"
        )
        return DecisionRegister(False, (), (issue,))
    if not isinstance(document, Mapping):
        issue = DecisionIssue(
            "decision_register_invalid", "decisions.yaml must contain an object"
        )
        return DecisionRegister(False, (), (issue,))

    issues: list[DecisionIssue] = []
    if document.get("schema_version") != "1.0":
        issues.append(
            DecisionIssue(
                "decision_schema_unsupported",
                "decisions.yaml schema_version must be 1.0",
            )
        )
    if document.get("model_id") != expected_model_id:
        issues.append(
            DecisionIssue(
                "decision_model_mismatch",
                "decisions.yaml model_id must match model.yaml",
            )
        )
    if _non_empty_string(document.get("owner")) is None:
        issues.append(
            DecisionIssue(
                "decision_owner_invalid", "decisions.yaml owner must be a non-empty string"
            )
        )
    raw_decisions = document.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        issues.append(
            DecisionIssue(
                "decision_list_invalid", "decisions.yaml decisions must be a non-empty list"
            )
        )
        return DecisionRegister(False, (), tuple(issues))

    decisions: list[InteractionDecision] = []
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, Mapping):
            issues.append(
                DecisionIssue(
                    "decision_entry_invalid", f"decisions[{index}] must be an object"
                )
            )
            continue
        decision, entry_issues = _decision_from_mapping(raw, index)
        issues.extend(entry_issues)
        if decision is not None:
            decisions.append(decision)
    ids = [decision.decision_id for decision in decisions]
    if len(ids) != len(set(ids)):
        issues.append(
            DecisionIssue(
                "decision_id_duplicate", "decisions.yaml decision ids must be unique"
            )
        )
    known_ids = set(ids)
    for decision in decisions:
        if decision.status == "superseded" and (
            decision.superseded_by not in known_ids
            or decision.superseded_by == decision.decision_id
        ):
            issues.append(
                DecisionIssue(
                    "decision_supersession_invalid",
                    f"{decision.decision_id} must reference a different existing decision",
                )
            )
    return DecisionRegister(not issues, tuple(decisions), tuple(issues))
