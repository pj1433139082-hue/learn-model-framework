"""Validate the human input inventory against the mixed-source contract.

The machine-readable schema stays in model.yaml. This module checks inputs.md
for the provenance fields that cannot be derived from the contract alone:
non-IoT values must say how they are supplied and how stale values are
detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


_NON_IOT = frozenset({"化验", "人工", "上游系统", "上游", "lab", "manual", "upstream"})
_SUPPLY = frozenset({"请求直接传", "请求", "request", "aux_dict", "aux-dict"})
_TIME_WORDS = ("时间", "时点", "采样", "stale", "时效", "质量", "quality")


@dataclass(frozen=True)
class InputContractIssue:
    code: str
    input_name: str
    detail: str


@dataclass(frozen=True)
class InputContractReport:
    issues: tuple[InputContractIssue, ...]
    rows: int

    @property
    def valid(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "rows": self.rows,
            "issues": [
                {"code": issue.code, "input": issue.input_name, "detail": issue.detail}
                for issue in self.issues
            ],
        }


def _cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def validate_inputs(workspace: str | Path) -> InputContractReport:
    path = Path(workspace) / "inputs.md"
    if not path.is_file() or path.is_symlink():
        return InputContractReport((), 0)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return InputContractReport(
            (InputContractIssue("inputs_unreadable", "", "inputs.md cannot be read"),), 0
        )

    header_index = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("|") and "输入" in line),
        None,
    )
    if header_index is None:
        return InputContractReport(
            (InputContractIssue("inputs_header_missing", "", "inputs.md has no Markdown input table"),), 0
        )
    headers = [cell.casefold() for cell in _cells(lines[header_index])]
    if header_index + 1 >= len(lines) or not _is_separator(_cells(lines[header_index + 1])):
        return InputContractReport(
            (InputContractIssue("inputs_table_invalid", "", "inputs.md table has no separator row"),), 0
        )

    def column(*names: str) -> int | None:
        for index, header in enumerate(headers):
            if any(name.casefold() in header for name in names):
                return index
        return None

    input_col = column("输入", "input", "字段")
    source_col = column("来源", "source")
    supply_col = column("供数方式", "供数", "supply")
    time_col = column("时间口径", "时间", "质量", "stale", "freshness")
    if input_col is None or source_col is None:
        return InputContractReport(
            (InputContractIssue("inputs_columns_missing", "", "inputs.md needs 输入 and 来源 columns"),), 0
        )

    issues: list[InputContractIssue] = []
    rows = 0
    for line in lines[header_index + 2:]:
        if not line.lstrip().startswith("|"):
            if rows:
                break
            continue
        cells = _cells(line)
        if _is_separator(cells):
            continue
        rows += 1
        name = cells[input_col] if input_col < len(cells) else ""
        source = cells[source_col].casefold() if source_col < len(cells) else ""
        if not name or not source or source not in _NON_IOT:
            continue
        supply = cells[supply_col] if supply_col is not None and supply_col < len(cells) else ""
        if not supply:
            issues.append(InputContractIssue("non_iot_supply_missing", name, "非 IoT 输入必须声明供数方式（请求直接传或 aux_dict）"))
        elif supply.casefold() not in _SUPPLY:
            issues.append(InputContractIssue("non_iot_supply_invalid", name, f"不支持的供数方式：{supply}"))
        timing = cells[time_col] if time_col is not None and time_col < len(cells) else ""
        if not timing:
            issues.append(InputContractIssue("non_iot_timing_missing", name, "非 IoT 输入必须声明采样时间或 stale 上限"))
        elif not any(word in timing.casefold() for word in _TIME_WORDS):
            issues.append(InputContractIssue("non_iot_timing_invalid", name, "时间口径/质量列没有时间或 stale 说明"))
    return InputContractReport(tuple(issues), rows)
