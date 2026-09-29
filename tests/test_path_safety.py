from __future__ import annotations

from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model_lab.path_safety import is_link_or_junction


class _Python311PathStub:
    def __init__(self, *, reparse_tag: int) -> None:
        self._reparse_tag = reparse_tag

    def is_symlink(self) -> bool:
        return False

    def lstat(self) -> SimpleNamespace:
        return SimpleNamespace(st_reparse_tag=self._reparse_tag)


class PathSafetyTests(unittest.TestCase):
    def test_python311_fallback_detects_windows_junction_reparse_tag(self) -> None:
        mount_tag = 0xA0000003
        junction = _Python311PathStub(reparse_tag=mount_tag)
        with patch.object(stat, "IO_REPARSE_TAG_MOUNT_POINT", mount_tag, create=True):
            self.assertTrue(is_link_or_junction(junction))  # type: ignore[arg-type]

    def test_ordinary_and_missing_paths_are_not_links_or_junctions(self) -> None:
        self.assertFalse(is_link_or_junction(Path(__file__).parent))
        self.assertFalse(is_link_or_junction(Path(__file__).with_suffix(".missing")))


if __name__ == "__main__":
    unittest.main()
