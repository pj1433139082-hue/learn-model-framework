"""Offline privacy and source-history gate for the shared framework repository.

Private model workspaces live outside this checkout. This gate deliberately has
no model count, plant inventory, or platform publication requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import re
import subprocess

from model_lab import __version__
from model_lab.path_safety import is_link_or_junction


_ROOT_FILES = frozenset({
    ".gitattributes", ".gitignore", ".python-version", "AGENTS.md", "README.md", "CONTEXT.md",
    "CHANGELOG.md", "pyproject.toml",
})
_SOURCE_ROOTS = frozenset({
    ".codex", ".github", ".skillshare", ".vscode", "docs", "model_lab",
    "model_lab_service_runtime", "scripts", "tests",
})
_FORBIDDEN_SUFFIXES = frozenset({
    ".7z", ".arrow", ".avro", ".bin", ".bz2", ".ckpt", ".csv",
    ".dll", ".exe", ".feather", ".gz", ".h5", ".joblib", ".key",
    ".keras", ".onnx", ".orc", ".p12", ".parquet", ".pem", ".pfx",
    ".pickle", ".pkl", ".pt", ".pth", ".pyd", ".pyc", ".pyo",
    ".rar", ".safetensors", ".so", ".tar", ".tgz", ".tflite",
    ".tsv", ".whl", ".xls", ".xlsx", ".xz", ".zip", ".zst",
})
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b"),
    re.compile(rb"(?im)\b(?:password|passwd|client_secret|api_key)\s*[:=]\s*[\"'][^\"'\r\n]{12,}[\"']"),
)
_PRIVATE_IDENTIFIERS = (
    re.compile(rb"\bPS[0-9]{4}(?:[-_]|\b)", re.IGNORECASE),
    re.compile(rb"\bMLModel-[0-9]{3,}(?:_V[0-9]+)?\b", re.IGNORECASE),
    re.compile(rb"[A-Za-z]:[\\/]Users[\\/][^\\/\s]{2,}[\\/]", re.IGNORECASE),
)
_MAX_BLOB = 10_000_000
_MAX_SCAN = 256_000_000


@dataclass(frozen=True)
class GateIssue:
    rule: str
    subject: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "subject": self.subject, "message": self.message}


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: str
    details: tuple[tuple[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, **dict(self.details)}


@dataclass(frozen=True)
class ReleaseGateReport:
    repository_root: str
    checks: tuple[GateCheck, ...]
    issues: tuple[GateIssue, ...]
    model_count: int = 0
    release_snapshot_count: int = 0
    offline: bool = True
    remote_writes: bool = False

    @property
    def valid(self) -> bool:
        return not self.issues and all(check.status == "passed" for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid, "version": __version__,
            "repository_root": self.repository_root,
            "model_count": self.model_count,
            "release_snapshot_count": self.release_snapshot_count,
            "offline": self.offline, "remote_writes": self.remote_writes,
            "checks": [check.as_dict() for check in self.checks],
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def as_text(self) -> str:
        lines = [f"framework release-gate: {'PASSED' if self.valid else 'FAILED'}"]
        lines.extend(f"- [{item.status.upper()}] {item.name}" for item in self.checks)
        lines.extend(f"- [{item.rule}] {item.subject}: {item.message}" for item in self.issues)
        return "\n".join(lines)


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update({
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    return subprocess.run(
        ("git", "-C", str(root), *arguments), capture_output=True,
        check=False, env=environment,
    )


def _path_issue(relative: str, *, framework: bool) -> str | None:
    path = PurePosixPath(relative.replace("\\", "/"))
    parts = tuple(part.casefold() for part in path.parts)
    if path.is_absolute() or ".." in parts or not parts:
        return "unsafe path"
    if not framework and parts[:2] == ("release-bundle", "service"):
        return None
    if framework and path.name not in _ROOT_FILES and parts[0] not in _SOURCE_ROOTS:
        return "private model or unreviewed top-level path is not part of the shared framework"
    if any(part in {"models", "data", "artifacts", "__pycache__", ".venv", "venv"}
           or part.startswith((".venv-", "venv-")) for part in parts):
        return "private models, datasets, artifacts, and virtual environments are forbidden"
    if path.name.casefold() in {".env", "credentials.json", "id_rsa", "id_ed25519"}:
        return "credential file is forbidden"
    if path.suffix.casefold() in _FORBIDDEN_SUFFIXES:
        if not framework and "release-bundle" in parts and "service" in parts:
            return None
        return "binary, archive, tabular data, credential, or generated artifact is forbidden"
    return None


def _content_issue(content: bytes) -> str | None:
    if b"\x00" in content:
        return "binary content is forbidden in shared source"
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        return "credential material detected"
    if any(pattern.search(content) for pattern in _PRIVATE_IDENTIFIERS):
        return "private identifier or personal path detected"
    return None


def scan_workspace_tree(workspace: str | Path) -> tuple[GateIssue, ...]:
    """Inspect a private model workspace before a lifecycle transition."""
    root = Path(workspace)
    if not root.is_dir() or is_link_or_junction(root):
        return (GateIssue("workspace_unreadable", ".", "workspace must be a real directory"),)
    issues: list[GateIssue] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if is_link_or_junction(path):
            issues.append(GateIssue("workspace_link", relative, "links are forbidden"))
            continue
        path_problem = _path_issue(relative, framework=False)
        if path_problem:
            issues.append(GateIssue("workspace_forbidden_file", relative, path_problem))
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_BLOB:
                issues.append(GateIssue("workspace_file_too_large", relative, "file exceeds 10 MB"))
                continue
            content = path.read_bytes()
        except OSError:
            issues.append(GateIssue("workspace_unreadable", relative, "file cannot be read"))
            continue
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            issues.append(GateIssue("workspace_secret_content", relative, "credential material detected"))
    return tuple(issues)


def _index_entries(root: Path) -> tuple[list[tuple[bytes, bytes, str]], list[GateIssue]]:
    """Read paths and object IDs from the actual index, preserving unusual names."""
    result = _git(root, "ls-files", "--stage", "-z")
    if result.returncode != 0:
        return [], [GateIssue("index_unavailable", "git", "could not list index entries")]
    entries: list[tuple[bytes, bytes, str]] = []
    issues: list[GateIssue] = []
    for record in result.stdout.split(b"\x00"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            issues.append(GateIssue("index_unreadable", "git", "index entry cannot be parsed"))
            continue
        mode, object_id, _stage = fields
        entries.append((mode, object_id, os.fsdecode(raw_path)))
    return entries, issues


def _index_issues(root: Path) -> tuple[int, int, list[GateIssue]]:
    entries, issues = _index_entries(root)
    seen_paths: set[str] = set()
    seen_objects: set[bytes] = set()
    scanned = 0
    total_bytes = 0
    for mode, object_id, relative in entries:
        if relative not in seen_paths:
            seen_paths.add(relative)
            problem = _path_issue(relative, framework=True)
            if problem:
                issues.append(GateIssue("index_path", relative, problem))
        if mode == b"120000":
            issues.append(GateIssue("index_path", relative, "symbolic links are forbidden"))
        if mode == b"160000":
            issues.append(GateIssue("index_unreadable", relative, "submodule entries are not source blobs"))
            continue
        if object_id and not object_id.strip(b"0"):
            # Intent-to-add entries have no index blob yet; the worktree scan covers their content.
            continue
        if object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        if not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_id):
            issues.append(GateIssue("index_unreadable", relative, "index object ID is invalid"))
            continue
        object_name = object_id.decode("ascii")
        size_result = _git(root, "cat-file", "-s", object_name)
        try:
            size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
        except ValueError:
            size = -1
        if size < 0:
            issues.append(GateIssue("index_unreadable", relative, "index blob size cannot be read"))
            continue
        total_bytes += size
        if size > _MAX_BLOB or total_bytes > _MAX_SCAN:
            issues.append(GateIssue("index_budget", relative, "index content exceeds scan safety budget"))
            break
        blob = _git(root, "cat-file", "blob", object_name)
        if blob.returncode != 0:
            issues.append(GateIssue("index_unreadable", relative, "index blob cannot be read"))
            continue
        scanned += 1
        problem = _content_issue(blob.stdout)
        if problem:
            issues.append(GateIssue("index_content", relative, problem))
    return len(entries), scanned, issues


def _worktree_issues(root: Path) -> tuple[list[str], list[GateIssue]]:
    """Inspect tracked working-tree files separately from the staged index blobs."""
    result = _git(root, "ls-files", "--cached", "-z")
    if result.returncode != 0:
        return [], [GateIssue("worktree_unavailable", "git", "could not list tracked paths")]
    paths = [os.fsdecode(item) for item in result.stdout.split(b"\x00") if item]
    issues: list[GateIssue] = []
    for relative in paths:
        problem = _path_issue(relative, framework=True)
        if problem:
            issues.append(GateIssue("worktree_path", relative, problem))
            continue
        path = root / relative
        if not path.is_file() or is_link_or_junction(path):
            issues.append(GateIssue("worktree_file_unreadable", relative, "file is absent or linked"))
            continue
        try:
            if path.stat().st_size > _MAX_BLOB:
                issues.append(GateIssue("worktree_file_too_large", relative, "file exceeds 10 MB"))
                continue
            problem = _content_issue(path.read_bytes())
        except OSError:
            problem = "file cannot be read"
        if problem:
            issues.append(GateIssue("worktree_content", relative, problem))
    return paths, issues


def _history_issues(root: Path) -> tuple[int, int, list[GateIssue]]:
    count = _git(root, "rev-list", "--count", "--all")
    if count.returncode != 0:
        return 0, 0, [GateIssue("history_unavailable", "git", "Git history cannot be counted")]
    try:
        commit_count = int(count.stdout.strip())
    except ValueError:
        commit_count = 0
    if commit_count == 0:
        return 0, 0, [GateIssue("history_empty", "git", "commit the audited framework before release")]
    if _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true":
        return commit_count, 0, [GateIssue("history_shallow", "git", "complete history is required")]
    common = _git(root, "rev-parse", "--git-common-dir")
    if common.returncode != 0:
        return commit_count, 0, [GateIssue("history_unavailable", "git", "Git common directory cannot be resolved")]
    common_path = Path(common.stdout.decode("utf-8", errors="replace").strip())
    if not common_path.is_absolute():
        common_path = root / common_path
    grafts = common_path / "info" / "grafts"
    if grafts.is_file() and grafts.stat().st_size:
        return commit_count, 0, [GateIssue("history_grafts", "git", "Git grafts prevent complete history verification")]
    historical_paths = _git(
        root, "log", "--all", "--format=", "--name-only", "-z",
        "--no-renames", "-m", "--root",
    )
    if historical_paths.returncode != 0 or len(historical_paths.stdout) > 32_000_000:
        return commit_count, 0, [GateIssue("history_unavailable", "git", "historical paths cannot be fully listed")]
    listing = _git(root, "rev-list", "--objects", "--all")
    if listing.returncode != 0 or len(listing.stdout) > 32_000_000:
        return commit_count, 0, [GateIssue("history_unavailable", "git", "Git objects cannot be fully listed")]
    issues: list[GateIssue] = []
    for raw_path in historical_paths.stdout.split(b"\x00"):
        relative = os.fsdecode(raw_path)
        if not relative:
            continue
        problem = _path_issue(relative, framework=True)
        if problem:
            issues.append(GateIssue("history_path", relative, problem))
    checked: set[bytes] = set()
    scanned = 0
    total_bytes = 0
    for line in listing.stdout.splitlines():
        object_id = line.partition(b" ")[0]
        if not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_id):
            continue
        if object_id in checked:
            continue
        checked.add(object_id)
        object_name = object_id.decode("ascii")
        object_type = _git(root, "cat-file", "-t", object_name)
        if object_type.returncode != 0:
            issues.append(GateIssue("history_unreadable", "git", "Git object type cannot be read"))
            break
        if object_type.stdout.strip() != b"blob":
            continue
        size_result = _git(root, "cat-file", "-s", object_name)
        try:
            size = int(size_result.stdout.strip())
        except ValueError:
            issues.append(GateIssue("history_unreadable", "git", "Git blob size cannot be read"))
            break
        total_bytes += size
        if size > _MAX_BLOB or total_bytes > _MAX_SCAN:
            issues.append(GateIssue("history_budget", "git", "source history exceeds scan safety budget"))
            break
        blob = _git(root, "cat-file", "blob", object_name)
        if blob.returncode != 0:
            issues.append(GateIssue("history_unreadable", "git", "Git blob cannot be read"))
            break
        scanned += 1
        problem = _content_issue(blob.stdout)
        if problem:
            issues.append(GateIssue("history_content", object_id[:12].decode("ascii"), problem))
    return commit_count, scanned, issues


def run_release_gate(repository_root: str | Path) -> ReleaseGateReport:
    root = Path(repository_root).resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.decode("utf-8", errors="replace").strip()).resolve() != root:
        issue = GateIssue("repository_unavailable", ".", "select the root of a standalone Git checkout")
        return ReleaseGateReport(str(root), (GateCheck("git_checkout", "failed"),), (issue,))
    index_entries, index_blobs, index_issues = _index_issues(root)
    worktree_paths, worktree_issues = _worktree_issues(root)
    commits, blobs, history_issues = _history_issues(root)
    checks = (
        GateCheck(
            "shared_index_allowlist",
            "failed" if index_issues else "passed",
            (("index_entries", index_entries), ("scanned_blobs", index_blobs)),
        ),
        GateCheck(
            "shared_worktree_allowlist",
            "failed" if worktree_issues else "passed",
            (("tracked_files", len(worktree_paths)),),
        ),
        GateCheck("complete_git_history", "failed" if history_issues else "passed", (("commits", commits), ("scanned_blobs", blobs))),
    )
    return ReleaseGateReport(
        str(root), checks, tuple(index_issues + worktree_issues + history_issues)
    )
