"""Read only the reviewed Python 3.11 company IoT files from one supplied ZIP.

The ZIP is never extracted wholesale, executed, or copied into Git-tracked paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
from zipfile import BadZipFile, ZipFile

from model_lab.path_safety import is_link_or_junction
from model_lab.point_serving import ProviderArchiveSpec


_PREFIX = "infra_model_deploy/resources/infra/311/"
_ENTRIES = {
    "module": _PREFIX + "mlops_deploy_iot_armor2_311.py",
    "runtime_init": _PREFIX + "pyarmor_runtime_000000/__init__.py",
    "native": _PREFIX + "pyarmor_runtime_000000/linux_x86_64/pyarmor_runtime.so",
}
_MAX_ZIP_BYTES = 16 * 1024 * 1024
_MAX_ENTRY_BYTES = 2 * 1024 * 1024


class ProviderArchiveError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_provider_archive(path: str | Path, spec: ProviderArchiveSpec) -> dict[str, bytes]:
    archive = Path(path)
    if is_link_or_junction(archive) or not archive.is_file() or archive.stat().st_size > _MAX_ZIP_BYTES:
        raise ProviderArchiveError("point_provider_archive_missing", "provider ZIP must be a bounded regular local file")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec.archive_sha256:
        raise ProviderArchiveError("point_provider_archive_digest_mismatch", "provider ZIP SHA-256 differs from model.yaml")
    try:
        with ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)):
                raise ProviderArchiveError("point_provider_archive_duplicate", "provider ZIP contains duplicate entries")
            contents: dict[str, bytes] = {}
            for label, name in _ENTRIES.items():
                try:
                    info = bundle.getinfo(name)
                except KeyError as error:
                    raise ProviderArchiveError("point_provider_component_missing", f"provider ZIP lacks {name}") from error
                if info.is_dir() or info.file_size > _MAX_ENTRY_BYTES or info.compress_size > _MAX_ZIP_BYTES:
                    raise ProviderArchiveError("point_provider_component_invalid", f"provider component {name} is not a bounded file")
                content = bundle.read(info)
                if hashlib.sha256(content).hexdigest() != getattr(spec, f"{label}_sha256"):
                    raise ProviderArchiveError("point_provider_component_digest_mismatch", f"provider component {name} SHA-256 differs from model.yaml")
                contents[label] = content
    except (BadZipFile, OSError) as error:
        raise ProviderArchiveError("point_provider_archive_invalid", "provider ZIP cannot be read") from error
    native = contents["native"]
    if len(native) < 20 or native[:5] != b"\x7fELF\x02" or native[5] != 1 or struct.unpack_from("<H", native, 18)[0] != 62:
        raise ProviderArchiveError("point_provider_native_platform_mismatch", "provider native runtime must be 64-bit little-endian Linux x86_64 ELF")
    return contents


__all__ = ["ProviderArchiveError", "read_provider_archive"]
