from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


class DocumentationTests(unittest.TestCase):
    def test_local_build_quickstart_uses_one_workspace_variable(self) -> None:
        docs_root = ROOT / "production"
        if not docs_root.is_dir():
            docs_root = ROOT
        readme = (docs_root / "README.md").read_text(encoding="utf-8")
        build = (docs_root / "docs/build.md").read_text(encoding="utf-8")
        for source in (readme, build):
            self.assertIn("DCS6100_BUILD_ROOT", source)
            self.assertIn("thingino-dlink local-build build-universal", source)
            self.assertIn("thingino-dlink universal init-session", source)
            self.assertIn("thingino-dlink universal configure", source)
            self.assertIn("thingino-dlink universal handoff", source)
            self.assertNotIn(
                '--expected-wpa-config "${DCS6100_BUILD_ROOT}-private/expected-wpa.conf"',
                source,
            )
            self.assertNotIn("/path/to/external", source)
        self.assertIn("Windows PowerShell", readme)
        self.assertIn("Linux uses a whole-disk node", readme)
        self.assertIn("macOS example:", readme)

    def test_host_tool_quickstart_uses_an_isolated_python(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m venv .venv", readme)
        self.assertIn(". .venv/bin/activate", readme)
        self.assertIn("python -m pip install -e .", readme)
        self.assertNotIn("python3 -m pip install -e .", readme)
        self.assertIn("externally-managed system Python", readme)

    def test_current_public_markdown_links_pass(self) -> None:
        checked = DOCS.validate()
        self.assertIn(Path("README.md"), checked)
        self.assertIn(Path("docs/index.md"), checked)

    def test_rejects_missing_local_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("[missing](docs/missing.md)\n", encoding="utf-8")
            with self.assertRaisesRegex(DOCS.DocsError, "missing local link"):
                DOCS.validate_paths([source], root=root)

    def test_ignores_external_and_fragment_only_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text(
                "[external](https://example.invalid/) [section](#section)\n",
                encoding="utf-8",
            )
            self.assertEqual(DOCS.validate_paths([source], root=root), [Path("README.md")])

    def test_rejects_link_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("[escape](../outside.md)\n", encoding="utf-8")
            with self.assertRaisesRegex(DOCS.DocsError, "escapes repository"):
                DOCS.validate_paths([source], root=root)


if __name__ == "__main__":
    unittest.main()
