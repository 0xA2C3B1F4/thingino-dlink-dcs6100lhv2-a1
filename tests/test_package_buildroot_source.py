"""Focused manifest and revision-binding tests for Buildroot source packaging."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts import package_buildroot_source as source_package


class PackageBuildrootSourceTests(unittest.TestCase):
    @staticmethod
    def _tree(root: Path, *, private_modes: bool) -> None:
        arch = root / "arch"
        arch.mkdir(parents=True)
        config = arch / "Config.in.mips"
        config.write_bytes(b"config content\n")
        tool = root / "tool.sh"
        tool.write_bytes(b"#!/bin/sh\nexit 0\n")
        (root / "config-link").symlink_to("arch/Config.in.mips")
        arch.chmod(0o700 if private_modes else 0o755)
        config.chmod(0o600 if private_modes else 0o644)
        tool.chmod(0o700 if private_modes else 0o755)

    def test_tree_inventory_accepts_private_read_write_and_directory_modes(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"])) as temporary:
            root = Path(temporary)
            upstream = root / "upstream"
            prepared = root / "prepared"
            self._tree(upstream, private_modes=False)
            self._tree(prepared, private_modes=True)
            actual = source_package.tree_inventory(upstream)
            expected = source_package.tree_inventory(prepared)
            self.assertEqual(actual, expected)
            self.assertEqual(actual["arch"], {"type": "directory"})
            self.assertIs(actual["arch/Config.in.mips"]["executable"], False)
            self.assertIs(actual["tool.sh"]["executable"], True)

    def test_tree_inventory_rejects_content_execution_and_symlink_changes(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"])) as temporary:
            root = Path(temporary)
            upstream = root / "upstream"
            prepared = root / "prepared"
            self._tree(upstream, private_modes=False)
            self._tree(prepared, private_modes=True)
            original = source_package.tree_inventory(upstream)

            config = prepared / "arch/Config.in.mips"
            config.write_bytes(b"changed content\n")
            self.assertNotEqual(original, source_package.tree_inventory(prepared))
            config.write_bytes(b"config content\n")

            tool = prepared / "tool.sh"
            tool.chmod(0o600)
            self.assertNotEqual(original, source_package.tree_inventory(prepared))
            tool.chmod(0o700)

            link = prepared / "config-link"
            link.unlink()
            link.symlink_to("tool.sh")
            self.assertNotEqual(original, source_package.tree_inventory(prepared))

    def test_manifest_describes_multiple_exact_project_revisions(self) -> None:
        manifests = []
        for project_revision in ("1" * 40, "f" * 40):
            with self.subTest(project_revision=project_revision):
                manifest = source_package.build_manifest(
                    project_revision=project_revision,
                    buildroot_pin={"revision": "a" * 40, "tree": "b" * 40,
                                   "url": "https://example.invalid/buildroot.git"},
                    thingino_revision="c" * 40,
                    source_date_epoch=1,
                    upstream_archive_members=2,
                    upstream_archive_file_bytes=12,
                    prepared_tree_entries_compared=3,
                    members={"config/buildroot.config": b"final config\n"},
                )
                self.assertEqual(manifest["project_revision"], project_revision)
                self.assertEqual(manifest["buildroot_revision"], "a" * 40)
                self.assertIn("four exact Thingino overrides", manifest["scope"])
                self.assertIn("recorded project revision", manifest["scope"])
                self.assertNotIn("a091", manifest["scope"])
                self.assertEqual(
                    manifest["members"]["config/buildroot.config"]["sha256"],
                    source_package.digest(b"final config\n"),
                )
                self.assertIn("Buildroot scope only; excludes other firmware component source",
                              manifest["limits"])
                self.assertIn(
                    "The supplied final Buildroot config is hashed; source collection and legal review are separate",
                    manifest["limits"],
                )
                self.assertIn("No package-license review or full legal approval is claimed",
                              manifest["limits"])
                self.assertIs(manifest["legal_review_approved"], False)
                self.assertIs(manifest["publication_authorized"], False)
                manifests.append(manifest)
        self.assertEqual(
            {key: value for key, value in manifests[0].items() if key != "project_revision"},
            {key: value for key, value in manifests[1].items() if key != "project_revision"},
        )

    def test_package_rejects_run_for_a_different_project_revision(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"])) as temporary:
            root = Path(temporary)
            for name in ("source", "thingino", "buildroot", "prepared", "output"):
                (root / name).mkdir()
            run_receipt = root / "run.json"
            run_receipt.write_text(json.dumps({"project_head": "a" * 40}), encoding="utf-8")
            args = argparse.Namespace(
                source=root / "source", thingino=root / "thingino",
                buildroot=root / "buildroot", prepared=root / "prepared",
                output=root / "output", archive_name="buildroot-source.tar.gz",
                project_revision="b" * 40, run_receipt=run_receipt,
            )
            with self.assertRaisesRegex(ValueError, "candidate revision differs"):
                source_package.package(args)
            self.assertEqual(list((root / "output").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
