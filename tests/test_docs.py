from __future__ import annotations

import importlib.util
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "production" if (ROOT / "production").is_dir() else ROOT
MODULE_PATH = ROOT / "scripts" / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


def readme_cli_examples(source: str) -> list[list[str]]:
    """Tokenize documented commands only; never execute shell or CLI handlers."""
    commands = []
    for block in re.findall(r"```bash\n(.*?)\n```", source, flags=re.DOTALL):
        for line in block.replace("\\\n", " ").splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens and tokens[0] == "thingino-dlink":
                commands.append(tokens)
            elif tokens[:3] in (
                ["python", "-m", "installer"],
                ["python", "-m", "installer.user_cli"],
            ):
                commands.append(tokens)
    return commands


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
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m venv .venv", readme)
        self.assertIn(". .venv/bin/activate", readme)
        self.assertIn("python -m pip install -e .", readme)
        self.assertNotIn("python3 -m pip install -e .", readme)
        self.assertIn("externally-managed system Python", " ".join(readme.split()))
        self.assertIn('export PATH="$(brew --prefix openssl@3)/bin:$PATH"', readme)

    def test_current_public_markdown_links_pass(self) -> None:
        checked = DOCS.validate()
        self.assertIn(Path("README.md"), checked)
        self.assertIn(Path("docs/index.md"), checked)

    def test_acceptance_links_target_existing_status_headings(self) -> None:
        status = (DOCS_ROOT / "docs/status.md").read_text(encoding="utf-8")
        headings = re.findall(r"^#{1,6} (.+)$", status, flags=re.MULTILINE)
        anchors = {
            re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
            for heading in headings
        }
        sources = [DOCS_ROOT / "README.md", *sorted((DOCS_ROOT / "docs").glob("*.md"))]
        checked = 0
        for path in sources:
            source = path.read_text(encoding="utf-8")
            for anchor in re.findall(r"\]\((?:docs/)?status\.md#([^)]+)\)", source):
                with self.subTest(path=path.relative_to(DOCS_ROOT), anchor=anchor):
                    self.assertIn(anchor, anchors)
                checked += 1
        self.assertGreater(checked, 0)

    def test_readme_install_commands_parse_without_executing_handlers(self) -> None:
        from installer.cli import build_parser as artifact_parser
        from installer.user_cli import build_parser as user_parser

        parsers = {"installer": artifact_parser(), "installer.user_cli": user_parser()}
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        examples = readme_cli_examples(readme)
        self.assertGreaterEqual(len(examples), 18)
        for tokens in examples:
            if tokens[0] == "thingino-dlink":
                parser, args = parsers["installer.user_cli"], tokens[1:]
            else:
                parser, args = parsers[tokens[2]], tokens[3:]
            if args == ["--help"]:
                continue
            with self.subTest(command=tokens):
                parsed = parser.parse_args(args)
                self.assertTrue(callable(parsed.handler))

    def test_readme_keeps_one_complete_universal_sequence(self) -> None:
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        commands = readme_cli_examples(readme)
        universal = [tokens for tokens in commands if tokens[1:2] == ["universal"]]
        self.assertEqual(
            [tokens[2] for tokens in universal],
            ["init-session", "configure", "provision", "authorize", "stage", "handoff", "verify"],
        )
        for tokens in universal:
            self.assertNotIn("--recovery-dir", tokens)
        stage, handoff = universal[4:6]
        for flag in (
            "--functional-recovery-dir", "--preserved-readback-dir",
            "--install-set-dir", "--universal-public-key", "--provisioning",
            "--provisioning-data", "--authorization-dir", "--authorization-public-key",
            "--session-dir", "--whole-device", "--mount-root", "--confirm-physical-device",
        ):
            self.assertEqual(stage[stage.index(flag) + 1], handoff[handoff.index(flag) + 1])
        self.assertIn("STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3", stage)
        self.assertIn("MTD1-MTD2-WRITTEN", handoff)
        self.assertFalse(any(tokens[1:2] == ["stage-install-set"] for tokens in commands))
        self.assertIn("--raptor-rwd-artifact", readme)
        self.assertIn("does not build that archive automatically", readme)

    def test_agent_skill_removed_without_removing_low_level_cli(self) -> None:
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Use with a coding agent", readme)
        self.assertNotIn("skills/dcs6100-thingino", readme)
        self.assertFalse((ROOT / "skills/dcs6100-thingino/SKILL.md").exists())
        policy = (ROOT / "policy/public-tree.json").read_text(encoding="utf-8")
        export_policy = ROOT / "policy/production-export.json"
        if export_policy.is_file():
            excluded = json.loads(export_policy.read_text(encoding="utf-8"))["excluded_prefixes"]
            self.assertIn("skills/", excluded)
            self.assertIn(".agents/", excluded)
        else:
            self.assertNotIn('"skills/', policy)
        self.assertIn("dcs6100-thingino", readme)

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
