"""Synthetic sum replay; real formulas and data belong in private packages."""

from __future__ import annotations

from collections.abc import Mapping
import math

from model_lab.baseline import BaselineProblem, BaselineReplay, BaselineRunner


KIND = "synthetic-sum-v1"
_ABSOLUTE_TOLERANCE = 0.0


def _problem(code: str, path: str, message: str) -> BaselineProblem:
    return BaselineProblem(
        code=code,
        path=path,
        message=message,
        suggestion="修正合成输入或独立期望结果后重新回放",
    )


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _replay(input_document: object, expected_document: object) -> BaselineReplay:
    problems: list[BaselineProblem] = []
    if not isinstance(input_document, Mapping) or set(input_document) != {"cases"}:
        problems.append(_problem("synthetic_input_shape", "$", "输入必须只包含 cases 列表"))
    if not isinstance(expected_document, Mapping) or set(expected_document) != {"cases"}:
        problems.append(_problem("synthetic_expected_shape", "$", "期望结果必须只包含 cases 列表"))
    if problems:
        return BaselineReplay(tuple(problems), 0, KIND, "casewise-sum", {}, {"sum": 0})

    input_cases = input_document["cases"]
    expected_cases = expected_document["cases"]
    if not isinstance(input_cases, list) or not isinstance(expected_cases, list):
        problems.append(_problem("synthetic_cases_shape", "cases", "cases 必须是 JSON 列表"))
        return BaselineReplay(tuple(problems), 0, KIND, "casewise-sum", {}, {"sum": 0})
    if len(input_cases) != len(expected_cases):
        problems.append(
            _problem("synthetic_case_count", "cases", "输入和独立期望结果的用例数量不同")
        )
        return BaselineReplay(
            tuple(problems), len(input_cases), KIND, "casewise-sum", {}, {"sum": 0}
        )

    for index, (input_row, expected_row) in enumerate(zip(input_cases, expected_cases)):
        path = f"cases[{index}]"
        if not isinstance(input_row, Mapping) or set(input_row) != {"left", "right"}:
            problems.append(
                _problem("synthetic_input_row", path, "每个输入用例必须只包含 left 和 right")
            )
            continue
        if not isinstance(expected_row, Mapping) or set(expected_row) != {"total"}:
            problems.append(
                _problem("synthetic_expected_row", path, "每个期望用例必须只包含 total")
            )
            continue
        left, right, expected_total = (
            input_row["left"],
            input_row["right"],
            expected_row["total"],
        )
        if not all(_number(value) for value in (left, right, expected_total)):
            problems.append(
                _problem("synthetic_non_finite_number", path, "用例只接受有限的 JSON 数字")
            )
            continue

        actual_total = left + right
        if not math.isclose(
            actual_total,
            expected_total,
            rel_tol=0.0,
            abs_tol=_ABSOLUTE_TOLERANCE,
        ):
            problems.append(
                _problem(
                    "synthetic_sum_mismatch",
                    f"{path}.total",
                    "加法结果与独立期望值不一致",
                )
            )

    return BaselineReplay(
        problems=tuple(problems),
        case_count=len(input_cases),
        kind=KIND,
        aggregation="casewise-sum",
        parameters={"absolute_tolerance": _ABSOLUTE_TOLERANCE},
        rounding={"sum": 0},
    )


RUNNER = BaselineRunner(
    kind=KIND,
    target_kind="synthetic",
    inputs=(
        ("left", "number", "1"),
        ("right", "number", "1"),
    ),
    outputs=(("total", "number", "1"),),
    replay=_replay,
)
