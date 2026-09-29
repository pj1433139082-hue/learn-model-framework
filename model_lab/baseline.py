"""Generic baseline protocol; model formulas belong to private extension packages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points


@dataclass(frozen=True)
class BaselineProblem:
    code: str
    path: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class BaselineReplay:
    problems: tuple[BaselineProblem, ...]
    case_count: int
    kind: str
    aggregation: str
    parameters: Mapping[str, float] | None = None
    rounding: Mapping[str, int] | None = None

    def check_details(self) -> tuple[tuple[str, object], ...]:
        return (
            ("kind", self.kind),
            ("cases", self.case_count),
            ("aggregation", self.aggregation),
            ("parameters", dict(self.parameters or {})),
            ("rounding", dict(self.rounding or {})),
        )


@dataclass(frozen=True)
class BaselineRunner:
    kind: str
    target_kind: str
    inputs: tuple[tuple[str, str, str], ...]
    outputs: tuple[tuple[str, str, str], ...]
    replay: Callable[[object, object], BaselineReplay]

    def expected_contract(self) -> dict[str, list[dict[str, str]]]:
        def fields(items: tuple[tuple[str, str, str], ...]) -> list[dict[str, str]]:
            return [
                {"name": name, "type": field_type, "unit": unit}
                for name, field_type, unit in items
            ]

        return {"inputs": fields(self.inputs), "outputs": fields(self.outputs)}


def get_baseline_runner(kind: str) -> BaselineRunner | None:
    """Resolve a model-owned replay from an installed private package.

    No real formula, plant parameter, or archived sample digest is built into
    the shared framework. An unavailable or malformed extension fails closed.
    """
    if not kind:
        return None
    candidates = entry_points(group="model_lab.baseline_runners")
    for candidate in candidates:
        if candidate.name != kind:
            continue
        try:
            runner = candidate.load()
        except (ImportError, AttributeError, ValueError):
            return None
        if isinstance(runner, BaselineRunner) and runner.kind == kind:
            return runner
    return None
