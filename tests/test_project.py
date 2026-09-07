from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTests(unittest.TestCase):
    def test_python_package_exposes_the_stable_cli_entry_point(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], "0.1.0")
        self.assertEqual(project["project"]["license"], "MIT")
        self.assertEqual(project["project"]["requires-python"], ">=3.11")
        self.assertEqual(
            project["project"]["scripts"]["dcs6100-thingino"],
            "installer.cli:main",
        )

    def test_ci_actions_are_pinned_to_immutable_commits(self) -> None:
        workflow = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertEqual(len(uses), 7)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_ci_runs_the_complete_webui_and_browser_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        self.assertIn("run: make webui", workflow)
        self.assertIn("run: npx playwright install --with-deps chromium", workflow)
        self.assertIn("run: npm run test:browser", workflow)

    def test_ci_runs_linux_and_windows_host_contracts(self) -> None:
        workflow = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        self.assertIn("host-platforms:", workflow)
        self.assertIn("os: [ubuntu-24.04, windows-2022]", workflow)
        self.assertIn("tests.test_linux_media_preflight", workflow)
        self.assertIn("tests.test_windows_media_preflight", workflow)
        self.assertIn("test_install_set_activates_stage2_before_bootstrap", workflow)

    def test_documentation_has_one_top_level_index_and_status(self) -> None:
        self.assertTrue((ROOT / "docs/index.md").is_file())
        self.assertTrue((ROOT / "docs/status.md").is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/index.md", readme)
        self.assertIn("docs/status.md", readme)


if __name__ == "__main__":
    unittest.main()
