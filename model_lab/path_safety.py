from __future__ import annotations

from pathlib import Path
import stat


def is_link_or_junction(path: Path) -> bool:
    """Return whether *path* redirects through a symlink or Windows junction.

    ``Path.is_junction`` was added in Python 3.12.  Python 3.11 on Windows
    already exposes the reparse tag through ``lstat()``, so use that as the
    compatibility path without following the target.
    """

    if path.is_symlink():
        return True

    junction_check = getattr(path, "is_junction", None)
    if junction_check is not None:
        return bool(junction_check())

    try:
        metadata = path.lstat()
    except (OSError, ValueError):
        return False
    reparse_tag = getattr(metadata, "st_reparse_tag", 0)
    mount_point_tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return mount_point_tag is not None and reparse_tag == mount_point_tag


__all__ = ["is_link_or_junction"]
