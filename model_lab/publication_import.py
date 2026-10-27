"""Import one already-verified group deployment receipt.

The company kit performs the upload. This module only records the resulting
Run ID and digests in validation/publications/<run_id>.json. It never
contacts MLflow, executes a kit, or claims that a Linux fixed bundle was
validated.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PublicationImportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / "model.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PublicationImportError("manifest_unreadable", f"model.yaml is not readable: {error}") from error
    if not isinstance(document, dict):
        raise PublicationImportError("manifest_invalid", "model.yaml must contain an object")
    model = document.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not isinstance(model.get("version"), str):
        raise PublicationImportError("manifest_invalid", "model.yaml must declare model.id and model.version")
    return document


def import_group_publication(
    workspace: str | Path,
    *,
    run_id: str,
    experiment: str,
    company_package: str | Path,
    deploy_kit: str | Path | None = None,
) -> dict[str, object]:
    """Write a schema 1.1 group receipt after checking local digests."""

    workspace = Path(workspace).resolve()
    model = _manifest(workspace)["model"]
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise PublicationImportError("invalid_run_id", "run_id must be a plain MLflow Run ID")
    if not isinstance(experiment, str) or not experiment.strip():
        raise PublicationImportError("invalid_experiment", "experiment must not be empty")
    package = Path(company_package).resolve()
    if not package.is_file() or package.is_symlink():
        raise PublicationImportError("company_package_unreadable", "company package must be a regular file")
    company_sha = _sha256_file(package)
    if not _SHA256.fullmatch(company_sha):
        raise PublicationImportError("company_package_unreadable", "company package digest is invalid")

    kit_sha = ""
    if deploy_kit is not None:
        kit = Path(deploy_kit).resolve()
        if not kit.is_dir() or kit.is_symlink():
            raise PublicationImportError("deploy_kit_unreadable", "deploy kit must be a real directory")
        kit_sha = _sha256_tree(kit)

    receipt = {
        "schema_version": "1.1",
        "channel": "group",
        "status": "verified_group_receipt",
        "model_id": model["id"],
        "model_version": model["version"],
        "experiment": experiment.strip(),
        "run_id": run_id,
        "company_package_sha256": company_sha,
        "deploy_kit_sha256": kit_sha,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "group_publication_receipt_not_repository_bundle_or_linux_target",
    }
    directory = workspace / "validation" / "publications"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise PublicationImportError("receipt_conflict", "publication receipt directory is not a real directory")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{run_id}.json"
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PublicationImportError("receipt_conflict", "existing receipt is not valid JSON") from error
        if existing != receipt:
            raise PublicationImportError("receipt_conflict", "existing receipt differs from this import")
        return {"status": "already_present", "receipt": str(destination.relative_to(workspace)), "run_id": run_id}
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "imported",
        "receipt": str(destination.relative_to(workspace)),
        "run_id": run_id,
        "company_package_sha256": company_sha,
        "deploy_kit_sha256": kit_sha,
    }
