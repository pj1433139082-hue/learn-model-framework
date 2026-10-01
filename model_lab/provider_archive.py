"""Read only the reviewed Python 3.11 company IoT files from one supplied ZIP.

The ZIP is never extracted wholesale, executed, or copied into Git-tracked paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct

from model_lab.bounded_archive import (
    BoundedArchiveError,
    read_archive_bytes,
    read_bounded_members,
)
from model_lab.path_safety import is_link_or_junction
from model_lab.point_serving import ProviderArchiveSpec


_PREFIX = "infra_model_deploy/resources/infra/311/"
_ENTRIES = {
    "module": _PREFIX + "mlops_deploy_iot_armor2_311.py",
    "runtime_init": _PREFIX + "pyarmor_runtime_000000/__init__.py",
    "native": _PREFIX + "pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
}


class ProviderArchiveError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_provider_archive(path: str | Path, spec: ProviderArchiveSpec) -> dict[str, bytes]:
    archive = Path(path)
    if is_link_or_junction(archive) or not archive.is_file():
        raise ProviderArchiveError("point_provider_archive_missing", "provider ZIP must be a bounded regular local file")
    try:
        archive_bytes = read_archive_bytes(archive)
    except BoundedArchiveError as error:
        if error.reason in {"not_regular_file", "archive_too_large"}:
            raise ProviderArchiveError("point_provider_archive_missing", "provider ZIP must be a bounded regular local file") from error
        raise ProviderArchiveError("point_provider_archive_invalid", "provider ZIP cannot be read") from error
    if hashlib.sha256(archive_bytes).hexdigest() != spec.archive_sha256:
        raise ProviderArchiveError("point_provider_archive_digest_mismatch", "provider ZIP SHA-256 differs from model.yaml")
    try:
        members = read_bounded_members(archive_bytes, _ENTRIES.values())
    except BoundedArchiveError as error:
        if error.reason == "duplicate_entry":
            raise ProviderArchiveError("point_provider_archive_duplicate", "provider ZIP contains duplicate entries") from error
        if error.reason == "member_missing":
            raise ProviderArchiveError("point_provider_component_missing", f"provider ZIP lacks {error.member}") from error
        if error.reason in {"member_invalid", "member_too_large", "compressed_member_too_large"}:
            name = error.member or "requested member"
            raise ProviderArchiveError("point_provider_component_invalid", f"provider component {name} is not a bounded file") from error
        raise ProviderArchiveError("point_provider_archive_invalid", "provider ZIP cannot be read") from error

    contents: dict[str, bytes] = {}
    for label, name in _ENTRIES.items():
        content = members[name]
        if hashlib.sha256(content).hexdigest() != getattr(spec, f"{label}_sha256"):
            raise ProviderArchiveError("point_provider_component_digest_mismatch", f"provider component {name} SHA-256 differs from model.yaml")
        contents[label] = content
    native = contents["native"]
    if len(native) < 20 or native[:5] != b"\x7fELF\x02" or native[5] != 1 or struct.unpack_from("<H", native, 18)[0] != 62:
        raise ProviderArchiveError("point_provider_native_platform_mismatch", "provider native runtime must be 64-bit little-endian Linux x86_64 ELF")
    return contents


__all__ = ["ProviderArchiveError", "read_provider_archive"]
