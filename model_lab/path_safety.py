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


def first_link_or_junction_component(path: Path) -> Path | None:
    """Return the first redirected component without resolving path segments.

    Existing components are inspected in the order supplied by *path*. In
    particular, a link before ``..`` is reported before the parent segment can
    make a resolved path appear harmless.
    """

    absolute = path if path.is_absolute() else Path.cwd() / path
    component = Path(absolute.anchor)
    if is_link_or_junction(component):
        return component

    for part in absolute.parts[1:]:
        if part in {"", "."}:
            continue
        component = component.parent if part == ".." else component / part
        if is_link_or_junction(component):
            return component
    return None


__all__ = ["first_link_or_junction_component", "is_link_or_junction"]
