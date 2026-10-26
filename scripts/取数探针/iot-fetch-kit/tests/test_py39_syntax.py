"""py3.9 语法兼容 + 包元数据/导出契约。

包声明支持 3.9，所以每个模块都必须能被 3.9 的语法解析器接受
（ast.parse 的 feature_version 就是 3.9 的语法边界），且用 PEP 563 延迟注解。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
PKG = KIT / "iot_fetch"
PUBLIC_API = (
    "IotConfig",
    "IotClient",
    "TokenProvider",
    "make_nodes",
    "load_nodes",
    "parse_signal_window",
    "month_slices",
    "fetch_range",
)


class Py39SyntaxTest(unittest.TestCase):
    def test_every_module_parses_under_py39(self) -> None:
        modules = sorted(PKG.glob("*.py"))
        self.assertGreaterEqual(len(modules), 10)
        for path in modules:
            with self.subTest(module=path.name):
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path), feature_version=(3, 9))

    def test_future_annotations_present(self) -> None:
        for path in sorted(PKG.glob("*.py")):
            with self.subTest(module=path.name):
                head = path.read_text(encoding="utf-8").splitlines()[:30]
                self.assertIn("from __future__ import annotations", head)

    def test_public_api_exports(self) -> None:
        import iot_fetch

        self.assertEqual(iot_fetch.__version__, "1.0.0")
        for name in PUBLIC_API:
            self.assertTrue(hasattr(iot_fetch, name), "缺少导出：" + name)


class MetadataTest(unittest.TestCase):
    def test_pyproject_contract(self) -> None:
        text = (KIT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "iot-fetch-kit"', text)
        self.assertIn('version = "1.0.0"', text)
        self.assertIn('requires-python = ">=3.9"', text)
        self.assertIn('dependencies = ["requests>=2.28"]', text)
        self.assertIn('parquet = ["pandas>=2.0", "pyarrow>=14"]', text)
        self.assertIn('ledger = ["pandas>=2.0", "openpyxl>=3.1"]', text)
        self.assertIn('iot-fetch = "iot_fetch.cli:main"', text)
        self.assertIn('include = ["iot_fetch*"]', text)

    def test_readme_mentions_key_behaviors(self) -> None:
        text = (KIT / "README.md").read_text(encoding="utf-8")
        for keyword in (
            "iot-fetch",
            "IOT_CLIENT_SECRET",
            "doctor",
            "--step-seconds",
            "IOT_FETCH_FORCE_JSONL",
            "parquet",
        ):
            self.assertIn(keyword, text, "README 缺少：" + keyword)
