"""Bounded byte access to selected members of a ZIP archive.

The caller owns path, digest, platform, and domain-specific validation. This
module reads one bounded archive snapshot, rejects ambiguous member names, and
returns only the exact requested members without extracting or executing them.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable
import zlib
from zipfile import BadZipFile, ZipFile


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class BoundedArchiveError(ValueError):
    """A bounded archive could not provide the requested member bytes."""

    def __init__(self, reason: str, member: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.member = member


def read_archive_bytes(path: str | Path) -> bytes:
    """Read one regular archive snapshot, stopping at the shared byte limit."""

    archive = Path(path)
    try:
        if not archive.is_file():
            raise BoundedArchiveError("not_regular_file")
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BoundedArchiveError("archive_too_large")
        contents = bytearray()
        with archive.open("rb") as source:
            while len(contents) <= MAX_ARCHIVE_BYTES:
                requested = min(
                    _READ_CHUNK_BYTES,
                    MAX_ARCHIVE_BYTES + 1 - len(contents),
                )
                chunk = source.read(requested)
                if not chunk:
                    break
                contents.extend(chunk)
        if len(contents) > MAX_ARCHIVE_BYTES:
            raise BoundedArchiveError("archive_too_large")
        return bytes(contents)
    except BoundedArchiveError:
        raise
    except OSError as error:
        raise BoundedArchiveError("archive_unreadable") from error


def read_bounded_members(
    archive_bytes: bytes, requested_members: Iterable[str]
) -> dict[str, bytes]:
    """Read exact requested members with actual decompressed-byte limits."""

    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise BoundedArchiveError("archive_too_large")
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise BoundedArchiveError("duplicate_entry")

            contents: dict[str, bytes] = {}
            for name in requested_members:
                try:
                    info = archive.getinfo(name)
                except KeyError as error:
                    raise BoundedArchiveError("member_missing", name) from error
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    raise BoundedArchiveError("member_invalid", name)
                if info.compress_size > MAX_ARCHIVE_BYTES:
                    raise BoundedArchiveError("compressed_member_too_large", name)

                content = bytearray()
                with archive.open(info, "r") as member:
                    while True:
                        requested = min(
                            _READ_CHUNK_BYTES,
                            MAX_MEMBER_BYTES + 1 - len(content),
                        )
                        chunk = member.read(requested)
                        if not chunk:
                            break
                        content.extend(chunk)
                        if len(content) > MAX_MEMBER_BYTES:
                            raise BoundedArchiveError("member_too_large", name)
                if len(content) != info.file_size:
                    raise BoundedArchiveError("archive_invalid", name)
                contents[name] = bytes(content)
            return contents
    except BoundedArchiveError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError, zlib.error, NotImplementedError) as error:
        raise BoundedArchiveError("archive_invalid") from error


__all__ = [
    "BoundedArchiveError",
    "MAX_ARCHIVE_BYTES",
    "MAX_MEMBER_BYTES",
    "read_archive_bytes",
    "read_bounded_members",
]
