"""Explicit test profiles; an intentional skip is never a target-runtime pass."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from importlib import metadata
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from typing import TextIO

from model_lab.runtime_contract import DEFAULT_RUNTIME_PROFILE_ID, get_runtime_profile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = ("local", "linux-offline", "linux-online", "windows-network")


def _local_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    # The repository's tests/ directory is an implicit namespace package, not
    # a directory with __init__.py. unittest.discover(top_level_dir=repo) rejects
    # it on Python 3.11, while explicit module loading uses the same public tests.
    modules = sorted((REPOSITORY_ROOT / "tests").glob("test_*.py"))
    return unittest.TestSuite(
        loader.loadTestsFromName(f"tests.{module.stem}") for module in modules
    )


@dataclass(frozen=True)
class ProfileEnvironment:
    operating_system: str
    python_minor: tuple[int, int]
    company_zip: Path | None
    real_iot: bool
    smoke_start: str | None

    @classmethod
    def current(cls) -> ProfileEnvironment:
        archive = os.environ.get("MODEL_LAB_COMPANY_IOT_ZIP")
        return cls(
            operating_system=sys.platform,
            python_minor=sys.version_info[:2],
            company_zip=Path(archive) if archive else None,
            real_iot=os.environ.get("MODEL_LAB_REAL_IOT") == "1",
            smoke_start=os.environ.get("MODEL_LAB_SMOKE_START"),
        )


@dataclass(frozen=True)
class TestProfileReport:
    profile: str
    status: str
    tests_run: int
    passed: int
    skipped: tuple[tuple[str, str], ...]
    failures: tuple[str, ...]
    errors: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def complete(self) -> bool:
        return self.status == "passed"

    @property
    def valid(self) -> bool:
        return self.status in {"passed", "partial"}

    @property
    def next_action(self) -> str:
        if self.status == "blocked":
            if any("Git worktree" in blocker for blocker in self.blockers):
                return (
                    "从包含 Git 历史的完整仓库 Clone 运行此档；"
                    "纯文件副本只能验收安装和基础入口，不能代替全库门禁。"
                )
            if any("test dependency" in blocker for blocker in self.blockers):
                return (
                    "在当前 Python 3.11 环境安装项目测试依赖："
                    f'uv pip install --python "{sys.executable}" -e ".[test]"；'
                    f"再运行 model-lab test-profile {self.profile}；不要把未运行用例计为通过。"
                )
            if self.profile == "local":
                return "切换项目 Python 3.11，再运行 model-lab test-profile local。"
            if self.profile == "windows-network":
                return (
                    "在 Windows Python 3.11 下配置 MODEL_LAB_COMPANY_IOT_ZIP、"
                    "MODEL_LAB_REAL_IOT=1 与近期 MODEL_LAB_SMOKE_START 后重试；"
                    "此诊断不能替代 Linux 验收。"
                )
            return (
                "切换目标 Linux Python 3.11，配置 MODEL_LAB_COMPANY_IOT_ZIP；"
                "在线档还需 MODEL_LAB_REAL_IOT=1 和近期 MODEL_LAB_SMOKE_START，"
                f"然后重跑 model-lab test-profile {self.profile}。"
            )
        if self.status in {"failed", "incomplete"}:
            if self.profile == "windows-network":
                return "排查 Windows 主机网络/TLS 路径；另以 Linux 档验收目标运行时，不修改模型公式来掩盖网络失败。"
            return f"先处理上方失败或跳过原因，再重跑 model-lab test-profile {self.profile}；不要将此档记为验收通过。"
        if self.profile == "local":
            return "在目标 Linux Python 3.11 配置经核验的公司包，运行 model-lab test-profile linux-offline；本机回归不能替代目标验收。"
        if self.profile == "linux-offline":
            return "若目标模型使用公司 IoT，在公司内网显式运行 model-lab test-profile linux-online；再逐模型核对基线与历史窗口。"
        if self.profile == "linux-online":
            return "对实际目标模型运行 model-lab status，并核对测点、历史窗口、行为基线和 bundle-preflight；通用网络档不能替代逐模型验收。"
        return "这只是 Windows 网络诊断；目标发布仍须在 Linux Python 3.11 完成模型专属验收。"

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "status": self.status,
            "valid": self.valid,
            "complete": self.complete,
            "tests_run": self.tests_run,
            "passed": self.passed,
            "skipped_count": self.skipped_count,
            "skipped": [
                {"test": name, "reason": reason} for name, reason in self.skipped
            ],
            "failures": list(self.failures),
            "errors": list(self.errors),
            "blockers": list(self.blockers),
            "next_action": self.next_action,
            "remote_writes": False,
        }

    def as_text(self) -> str:
        lines = [
            f"test-profile {self.profile}: {self.status.upper()}",
            f"executed={self.tests_run} passed={self.passed} skipped={self.skipped_count} "
            f"failed={len(self.failures)} errors={len(self.errors)}",
        ]
        lines.extend(f"blocked: {reason}" for reason in self.blockers)
        lines.extend(f"skipped: {name} — {reason}" for name, reason in self.skipped)
        lines.extend(f"failed: {name}" for name in self.failures)
        lines.extend(f"error: {name}" for name in self.errors)
        if self.status == "partial":
            lines.append("Local regression passed only for executed cases; target integration is unverified here.")
        lines.append(f"next: {self.next_action}")
        return "\n".join(lines)


def _blockers(profile: str, environment: ProfileEnvironment) -> tuple[str, ...]:
    problems: list[str] = []
    if environment.python_minor != (3, 11):
        problems.append("Python 3.11 is required")
    if profile.startswith("linux-") and environment.operating_system != "linux":
        problems.append("target Linux is required")
    if profile == "windows-network" and environment.operating_system != "win32":
        problems.append("Windows host is required for this optional network diagnostic")
    if profile != "local":
        if environment.company_zip is None or not environment.company_zip.is_file():
            problems.append("MODEL_LAB_COMPANY_IOT_ZIP must point to the reviewed company archive")
        if not os.environ.get("MODEL_LAB_PRIVATE_TEST_MODULE"):
            problems.append("MODEL_LAB_PRIVATE_TEST_MODULE must name a model-specific unittest module outside this framework")
    if profile in {"linux-online", "windows-network"}:
        if not environment.real_iot:
            problems.append("MODEL_LAB_REAL_IOT=1 is required to opt in to company network access")
        if not environment.smoke_start:
            problems.append("MODEL_LAB_SMOKE_START must be an explicit recent Asia/Shanghai timestamp")
    return tuple(problems)


def _test_dependency_blockers() -> tuple[str, ...]:
    profile = get_runtime_profile(DEFAULT_RUNTIME_PROFILE_ID)
    problems: list[str] = []
    for package, expected in profile.dependency_pins:
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            problems.append(f"test dependency {package}=={expected} is missing")
        else:
            if actual != expected:
                problems.append(
                    f"test dependency {package}=={expected} is required; found {actual}"
                )
    return tuple(problems)


def _repository_blockers() -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ("a Git worktree is required for the full local regression",)
    if result.returncode != 0:
        return ("a Git worktree is required for the full local regression",)
    if Path(result.stdout.strip()).resolve() != REPOSITORY_ROOT.resolve():
        return ("the project root must be its own Git worktree",)
    return ()


def _root_test_id(case: unittest.case.TestCase) -> str:
    return getattr(case, "test_case", case).id()


def run_test_profile(
    profile: str,
    *,
    environment: ProfileEnvironment | None = None,
    suite: unittest.TestSuite | None = None,
    stream: TextIO | None = None,
) -> TestProfileReport:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"unknown test profile: {profile}")
    observation = environment or ProfileEnvironment.current()
    blockers = _blockers(profile, observation)
    if blockers:
        return TestProfileReport(profile, "blocked", 0, 0, (), (), (), blockers)
    if suite is None:
        preflight_blockers = (
            _repository_blockers() if profile == "local" else ()
        ) + _test_dependency_blockers()
        if preflight_blockers:
            return TestProfileReport(
                profile, "blocked", 0, 0, (), (), (), preflight_blockers
            )
    if suite is None and not (REPOSITORY_ROOT / "tests").is_dir():
        return TestProfileReport(profile, "blocked", 0, 0, (), (), (), ("repository tests directory is absent",))
    output = stream or sys.stderr
    captured_stdout = io.StringIO()
    runner = unittest.TextTestRunner(stream=output, verbosity=1)
    # Local and offline profiles must remain offline even if the caller's shell
    # still has variables from a previous real-IoT smoke test.
    opt_in = "1" if profile in {"linux-online", "windows-network"} else "0"
    # A console-script launcher places its Scripts directory, not the current
    # checkout, on sys.path. Keep repository tests importable for both launchers.
    with patch.object(sys, "path", [str(REPOSITORY_ROOT), *sys.path]):
        if suite is None:
            loader = unittest.TestLoader()
            if profile == "local":
                suite = _local_suite(loader)
            else:
                suite = loader.loadTestsFromName(os.environ["MODEL_LAB_PRIVATE_TEST_MODULE"])
        with patch.dict(os.environ, {"MODEL_LAB_REAL_IOT": opt_in}), redirect_stdout(captured_stdout):
            result = runner.run(suite)
    if captured_stdout.getvalue():
        print(captured_stdout.getvalue(), file=output, end="")
    skipped = tuple((case.id(), reason) for case, reason in result.skipped)
    failures = tuple(case.id() for case, _ in result.failures) + tuple(
        case.id() for case in result.unexpectedSuccesses
    )
    errors = tuple(case.id() for case, _ in result.errors)
    non_passed = {
        _root_test_id(case)
        for cases in (result.skipped, result.failures, result.errors, result.expectedFailures)
        for case, _ in cases
    }
    non_passed.update(_root_test_id(case) for case in result.unexpectedSuccesses)
    passed = max(0, result.testsRun - len(non_passed))
    if not result.wasSuccessful() or result.testsRun == 0:
        status = "failed"
    elif suite is not None and profile != "local" and skipped:
        status = "incomplete"
    elif skipped:
        status = "partial"
    else:
        status = "passed"
    return TestProfileReport(profile, status, result.testsRun, passed, skipped, failures, errors, ())
