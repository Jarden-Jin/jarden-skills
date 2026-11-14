from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
BUILDER_PATH = REPO / "tools" / "build_release.py"
@unittest.skipUnless(BUILDER_PATH.is_file(), "schema 4 发行声明测试只在源码仓库运行")
class Schema4ReleaseBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        specification = importlib.util.spec_from_file_location("build_release_schema4", BUILDER_PATH)
        assert specification and specification.loader
        cls.builder = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.builder)

    def test_schema4_declares_legacy_and_asset_writes(self) -> None:
        self.assertEqual(
            self.builder.project_schema_of(b"SCHEMA_VERSION = 4\n"),
            {"read": [1, 2, 3, 4], "write": [3, 4]},
        )

    def test_annotated_schema4_is_supported(self) -> None:
        self.assertEqual(
            self.builder.project_schema_of(b"SCHEMA_VERSION: int = 4\n"),
            {"read": [1, 2, 3, 4], "write": [3, 4]},
        )

    def test_schema4_source_is_parsed_without_execution(self) -> None:
        source = b"SCHEMA_VERSION = 4\nraise RuntimeError('must not execute the release source')\n"
        self.assertEqual(self.builder.project_schema_of(source), {"read": [1, 2, 3, 4], "write": [3, 4]})

    def test_legacy_declarations_are_unchanged(self) -> None:
        for source, expected in (
            (None, {"read": [1, 2], "write": [2]}),
            (b"SCHEMA_VERSION = 2\n", {"read": [1, 2], "write": [2]}),
            (b"SCHEMA_VERSION = 3\n", {"read": [1, 2, 3], "write": [3]}),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.builder.project_schema_of(source), expected)

    def test_schema4_returns_detached_lists(self) -> None:
        first = self.builder.project_schema_of(b"SCHEMA_VERSION = 4\n")
        first["read"].append(99)
        first["write"].clear()
        self.assertEqual(
            self.builder.project_schema_of(b"SCHEMA_VERSION = 4\n"),
            {"read": [1, 2, 3, 4], "write": [3, 4]},
        )

    def test_ambiguous_and_nonliteral_schema4_is_rejected(self) -> None:
        for source in (
            b"SCHEMA_VERSION = 3\nSCHEMA_VERSION = 4\n",
            b"SCHEMA_VERSION = 4\nSCHEMA_VERSION += 1\n",
            b"SCHEMA_VERSION = 4.0\n",
            b"SCHEMA_VERSION = '4'\n",
            b"SCHEMA_VERSION = True\n",
            b"SCHEMA_VERSION = 3 + 1\n",
            b"def later():\n    SCHEMA_VERSION = 4\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(self.builder.BuildError):
                    self.builder.project_schema_of(source)

    def test_schema5_remains_unsupported(self) -> None:
        with self.assertRaises(self.builder.BuildError):
            self.builder.project_schema_of(b"SCHEMA_VERSION = 5\n")


if __name__ == "__main__":
    unittest.main()
