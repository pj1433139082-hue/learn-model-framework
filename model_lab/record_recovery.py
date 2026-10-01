"""Recover a derived index without replaying an already completed operation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shlex
from collections.abc import Sequence

import yaml

from model_lab.workflow_state import write_snapshot
from model_lab.environment import framework_root


def framework_recovery_command(*arguments: str) -> tuple[str, ...]:
    """Use the loaded framework's isolated launcher, never a PATH model-lab alias."""
    scripts = framework_root() / "scripts"
    if os.name == "nt":
        return (
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(scripts / "model-lab.ps1"), *arguments,
        )
    return ("bash", str(scripts / "model-lab.sh"), *arguments)


@dataclass(frozen=True)
class LocalRecordResult:
    status: str
    snapshot_attempts: int = 0
    failed_part: str | None = None
    error_type: str | None = None

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def details(self, *, action: str, command: Sequence[str]) -> dict[str, object]:
        """Keep ordinary success output unchanged; explain recovery or incompleteness."""
        if self.complete and self.snapshot_attempts < 2:
            return {}
        return {
            "completion": {
                "operation": "completed",
                "local_record": self.status,
                "repeat_operation": False,
            },
            "local_record": {
                "failed_part": self.failed_part,
                "snapshot_attempts": self.snapshot_attempts,
                "error_type": self.error_type,
            },
            "recovery": {
                "action": "none" if self.complete else action,
                "command": [] if self.complete else list(command),
                "remote_writes": False,
            },
        }

    def as_text(self, *, command: Sequence[str]) -> str:
        if self.complete:
            return "操作已完成；生成索引已通过一次安全补写恢复。"
        hint = (
            "路径或模型事实未通过校验，未自动重试；先检查目标路径、清单和证据。\n"
            if self.failed_part == "snapshot" and self.snapshot_attempts == 1
            else "先排查本地路径、文件权限或磁盘问题，保留原记录。\n"
        )
        rendered = (
            "& " + " ".join("'" + value.replace("'", "''") + "'" for value in command)
            if os.name == "nt" else shlex.join(command)
        )
        return (
            f"操作已完成，但本地记录不完整（{self.failed_part}: {self.error_type}）。\n"
            "不要重复创建、推进或上传；保留已完成的事实。\n"
            + hint
            + f"修复本地问题后，用固定框架启动器执行：{rendered}"
        )


def refresh_snapshot(path: str | Path) -> LocalRecordResult:
    """At most one retry of an I/O failure; each attempt revalidates the workspace.

    Invalid facts and unsafe destinations are not retryable. No manifest,
    publication receipt or remote operation is recreated here.
    """
    for attempt in (1, 2):
        try:
            write_snapshot(path)
        except OSError as error:
            if attempt == 1:
                continue
            return LocalRecordResult("incomplete", attempt, "snapshot", type(error).__name__)
        except (ValueError, yaml.YAMLError) as error:
            return LocalRecordResult("incomplete", attempt, "snapshot", type(error).__name__)
        else:
            return LocalRecordResult("complete", attempt)
    raise AssertionError("unreachable snapshot recovery state")
