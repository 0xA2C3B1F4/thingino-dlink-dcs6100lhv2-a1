from __future__ import annotations

import importlib.util
import argparse
import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "production" if (ROOT / "production").is_dir() else ROOT
MODULE_PATH = ROOT / "scripts" / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


def bash_blocks(source: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", source, flags=re.DOTALL)


def documented_cli_examples(source: str) -> list[list[str]]:
    """Tokenize documented commands only; never execute shell or CLI handlers."""
    commands = []
    for block in bash_blocks(source):
        for line in block.replace("\\\n", " ").splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens and tokens[0] == "thingino-dlink":
                commands.append(tokens)
            elif (
                tokens[:1] in (["python"], ["python3"])
                and tokens[1:3] in (
                    ["-m", "installer"],
                    ["-m", "installer.user_cli"],
                )
            ):
                commands.append(tokens)
    return commands


def inline_cli_examples(source: str) -> list[list[str]]:
    prose = re.sub(r"```.*?```", "", source, flags=re.DOTALL)
    commands = []
    for snippet in re.findall(r"(?<!`)`([^`]+)`(?!`)", prose):
        tokens = shlex.split(snippet)
        if tokens[:1] in (["local-build"], ["universal"], ["stock-recovery"], ["project"]):
            tokens.insert(0, "thingino-dlink")
        if tokens[:1] == ["thingino-dlink"] or (tokens[:1] in (["python"], ["python3"])
                and tokens[1:3] in (["-m", "installer"], ["-m", "installer.user_cli"])):
            commands.append(tokens)
    return commands


def allow_command_references(parser):
    """Inline references may omit required inputs, but supplied flags must exist."""
    for action in parser._actions:
        action.required = False
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                allow_command_references(child)
    for group in parser._mutually_exclusive_groups:
        group.required = False


def project_example_arguments(args):
    from installer.install_project import default_selections, ROLE_COMMANDS, link_inputs, Project
    path = Path("/documented camera/project.json")
    project = Project(path, "documented", default_selections(path, path.parent / "build"), {})
    link_inputs(project, {role: str(path.parent / role) for role in ROLE_COMMANDS if role != "recovery-dir"})
    command = " ".join(args[:2])
    present = {value.split("=", 1)[0] for value in args if value.startswith("--")}
    return args + [part for key, value in project.selections.get(command, {}).items()
                   if "--" + key not in present for part in ("--" + key, value)]


def without_secrets_redirect(tokens: list[str]) -> list[str]:
    """Remove only the matching shell input redirect, not installer arguments."""
    if "--secrets-fd" not in tokens:
        return tokens
    descriptor = tokens[tokens.index("--secrets-fd") + 1]
    prefix = f"{descriptor}<"
    if int(descriptor) < 3 or not tokens[-1].startswith(prefix):
        return tokens
    if not tokens[-1][len(prefix):]:
        raise ValueError("documented secrets redirect has no input path")
    return tokens[:-1]


class DocumentationTests(unittest.TestCase):
    def test_project_recovery_examples_use_linked_assets_and_default_destination(self):
        from installer import install_project as project, user_cli, user_cli_project
        from installer.install_results import document
        source = (DOCS_ROOT / "docs/installer-projects.md").read_text()
        section = source.split("## Capture functional recovery with the project\n", 1)[1].split("\n## ", 1)[0]
        commands = documented_cli_examples(section)
        self.assertEqual([tokens[2] for tokens in commands],
                         ["uartless-prepare", "uartless-authorize", "uartless-handoff", "uartless-validate"])
        with tempfile.TemporaryDirectory(prefix="documented recovery ") as directory:
            root = Path(directory).resolve()
            path = root / "project.json"
            state = project.init_project(path, name="fixture", build_root=root / "build")
            package, manifest = root / "capture.bin", root / "capture.manifest.json"
            package.write_bytes(b"accepted recovery-assets output")
            manifest.write_text("{}")
            active = project.begin_operation(state, "local-build recovery-assets",
                                               dict(state.selections["local-build recovery-assets"]))
            project.finish_operation(active, document("local-build recovery-assets", ok=True, phase="assets-ready",
                result={"files": {"uartless_package": str(package), "uartless_package_manifest": str(manifest)}}))
            original = path.read_bytes()
            with mock.patch.dict("os.environ", {"DCS6100_PROJECT": str(path)}):
                for tokens in commands:
                    parsed = user_cli.build_parser().parse_args(user_cli_project.expand(tokens[1:]))
                    self.assertEqual(parsed.package, package)
                    self.assertIsNone(parsed.whole_device)
                    self.assertIsNone(parsed.mount_root)
                    if parsed.stock_command == "uartless-validate":
                        self.assertEqual(parsed.output_dir, root / "project-private/recovery")
                        self.assertIsNone(parsed.confirm_output_dir)
                    else:
                        self.assertEqual(parsed.package_manifest, manifest)
                        self.assertIsNone(parsed.confirm_plan)
                with self.assertRaises(project.ProjectError) as error:
                    user_cli_project.expand([*commands[-1][1:], "--output-dir", str(root / "functional-recovery")])
                self.assertEqual(error.exception.code, "project_conflict")
            self.assertEqual(path.read_bytes(), original)
        self.assertIn("Stop here", section)
        self.assertIn("Stop again", section)
        self.assertIn("do not copy their path overrides", section)

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

    def test_install_commands_parse_without_executing_handlers(self) -> None:
        from installer.cli import build_parser as artifact_parser
        from installer.user_cli import build_parser as user_parser

        parsers = {"installer": artifact_parser(), "installer.user_cli": user_parser()}
        for filename, minimum in (("README.md", 18), ("docs/installation.md", 11),
                                  ("docs/build.md", 8), ("docs/installer-projects.md", 12)):
            source = (DOCS_ROOT / filename).read_text(encoding="utf-8")
            examples = documented_cli_examples(source)
            self.assertGreaterEqual(len(examples), minimum)
            for tokens in examples:
                tokens = without_secrets_redirect(tokens)
                if tokens[0] == "thingino-dlink":
                    parser, args = parsers["installer.user_cli"], tokens[1:]
                else:
                    parser, args = parsers[tokens[2]], tokens[3:]
                if args == ["--help"]:
                    continue
                if filename == "docs/installer-projects.md":
                    args = project_example_arguments(args)
                with self.subTest(filename=filename, command=tokens):
                    parsed = parser.parse_args(args)
                    self.assertTrue(callable(parsed.handler))

    def test_inline_install_commands_and_help_examples_accept_only_real_options(self):
        from installer.cli import build_parser as artifact_parser
        from installer.user_cli import build_parser as user_parser
        parsers = {"installer": artifact_parser(), "installer.user_cli": user_parser()}
        for parser in parsers.values():
            allow_command_references(parser)
        examples = []
        for filename in ("README.md", "docs/installation.md", "docs/build.md", "docs/installer-projects.md"):
            examples.extend((filename, command) for command in inline_cli_examples((DOCS_ROOT / filename).read_text()))
        def help_examples(parser):
            if parser.epilog:
                for command in re.findall(r"(?:Interactive|Automation): (thingino-dlink .*?)\.(?: |$)", parser.epilog):
                    examples.append(("CLI help", shlex.split(command)))
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        help_examples(child)
        help_examples(parsers["installer.user_cli"])
        self.assertGreater(len(examples), 30)
        for source, tokens in examples:
            parser, args = (parsers["installer.user_cli"], tokens[1:]) if tokens[0] == "thingino-dlink" else (parsers[tokens[2]], tokens[3:])
            with self.subTest(source=source, command=tokens), contextlib.redirect_stdout(io.StringIO()):
                if args == ["--help"] or "--help" in args:
                    with self.assertRaises(SystemExit) as result:
                        parser.parse_args(args)
                    self.assertEqual(result.exception.code, 0)
                else:
                    parser.parse_args(args)
        from installer.user_cli import ArgumentParsingError
        with self.assertRaises(ArgumentParsingError):
            parsers["installer.user_cli"].parse_args(["universal", "verify", "--host", "192.0.2.10"])

    def test_example_tokenizer_preserves_quoted_paths_and_secrets_fd(self) -> None:
        source = '''```bash
python3 -m installer.user_cli universal configure \\
  --session-dir "/volume with spaces/session" \\
  --output-dir "/volume with spaces/config" \\
  --secrets-fd 3 3<"/volume with spaces/input.json"
```'''
        [command] = documented_cli_examples(source)
        self.assertIn("/volume with spaces/session", command)
        self.assertEqual(command[-1], "3</volume with spaces/input.json")
        self.assertEqual(without_secrets_redirect(command), command[:-1])
        self.assertEqual(without_secrets_redirect(command)[-2:], ["--secrets-fd", "3"])
        mismatched = [*command[:-1], "4</wrong-descriptor.json"]
        self.assertEqual(without_secrets_redirect(mismatched), mismatched)
        unrelated = ["thingino-dlink", "--help", "3<input.json"]
        self.assertEqual(without_secrets_redirect(unrelated), unrelated)

    def test_install_paths_are_assigned_once_and_quoted_in_commands(self) -> None:
        sources = [
            (DOCS_ROOT / filename).read_text(encoding="utf-8")
            for filename in ("README.md", "docs/installation.md", "docs/build.md", "docs/installer-projects.md")
        ]
        assignments = set(re.findall(r"^export (DCS6100_\w+)=", "\n".join(sources), re.M))
        references = set()
        for source in sources:
            for block in bash_blocks(source):
                for line in block.replace("\\\n", " ").splitlines():
                    if line.startswith("export "):
                        continue
                    with self.subTest(line=line):
                        self.assertNotRegex(line, r"/path/(?:to|from)/|/dev/diskN|/dev/cu\.usbserial-N")
                        quoted = re.findall(r'"[^"\n]*"', line)
                        variables = re.findall(r"\$(DCS6100_\w+)", line)
                        self.assertEqual(
                            variables,
                            re.findall(r"\$(DCS6100_\w+)", " ".join(quoted)),
                        )
                        references.update(variables)
        self.assertFalse(references - assignments, references - assignments)
        self.assertGreater(len(references), 10)

    def test_install_shell_examples_have_valid_syntax_without_execution(self) -> None:
        shell = shutil.which("bash")
        if shell is None:
            self.skipTest("bash is unavailable")
        for filename in ("README.md", "docs/installation.md", "docs/build.md", "docs/installer-projects.md"):
            source = (DOCS_ROOT / filename).read_text(encoding="utf-8")
            for index, block in enumerate(bash_blocks(source)):
                with self.subTest(filename=filename, block=index):
                    result = subprocess.run(
                        [shell, "--noprofile", "--norc", "-n"],
                        input=block, text=True, capture_output=True, check=False,
                        env={"PATH": "/usr/bin:/bin"}, timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_saved_settings_keep_secrets_and_device_selection_separate(self) -> None:
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        setup = readme.split("#### Set paths once\n", 1)[1]
        [first, *_] = bash_blocks(setup)
        self.assertEqual(
            re.findall(r"^export (\w+)=", first, re.M),
            ["DCS6100_DATA_VOLUME", "DCS6100_BUILD_ROOT", "DCS6100_CAMERA_ROOT", "DCS6100_RECOVERY_ROOT"],
        )
        self.assertIn('DCS6100_BUILD_ROOT="$DCS6100_DATA_VOLUME/', first)
        self.assertIn('DCS6100_CAMERA_ROOT="$DCS6100_DATA_VOLUME/', first)
        self.assertIn('DCS6100_RECOVERY_ROOT="$DCS6100_CAMERA_ROOT/functional-recovery"', first)
        self.assertIn('chmod 600 "$DCS6100_CAMERA_ROOT/install-env.sh"', readme)
        self.assertIn("Keep the SD and UART device selections out of this file", readme)
        installation = (DOCS_ROOT / "docs/installation.md").read_text(encoding="utf-8")
        self.assertIn("../README.md#set-paths-once", installation)
        self.assertIn("../README.md#save-paths-for-another-terminal", installation)
        self.assertIn('3<"$DCS6100_CAMERA_ROOT/confirmed-wifi.json"', installation)
        commands = documented_cli_examples(installation)
        original = next(tokens for tokens in commands if "backup-validate" in tokens)
        functional = next(tokens for tokens in commands if "uartless-validate" in tokens)
        self.assertIn("$DCS6100_BACKUP_ROOT/complete-backup", original)
        self.assertNotIn("$DCS6100_RECOVERY_ROOT", original)
        self.assertIn("$DCS6100_RECOVERY_ROOT", functional)
        self.assertNotIn("$DCS6100_BACKUP_ROOT/complete-backup", functional)

    def test_readme_keeps_one_complete_universal_sequence(self) -> None:
        readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
        commands = documented_cli_examples(readme)
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
