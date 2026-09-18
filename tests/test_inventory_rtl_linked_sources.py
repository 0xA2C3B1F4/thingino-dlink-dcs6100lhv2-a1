from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/inventory_rtl_linked_sources.py"
SPEC = importlib.util.spec_from_file_location("inventory_rtl_linked_sources", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to import {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_fixture(root: Path) -> Path:
    workspace = root / "workspace"
    package = workspace / "thingino-output/build" / MODULE.PACKAGE
    core = package / "core"
    core.mkdir(parents=True)
    recorded = MODULE.RECORDED_ROOT.as_posix()
    objects = ("core/alpha.o", "core/beta.o")
    command = "ld -r -o {root}/8188fu.o {inputs}\n".format(
        root=recorded,
        inputs=" ".join(f"{recorded}/{obj}" for obj in objects),
    )
    (package / ".8188fu.o.cmd").write_text("cmd_8188fu.o := " + command, encoding="utf-8")
    for obj_name in objects:
        obj = package / obj_name
        obj.write_bytes((obj_name + " object\n").encode())
        source = obj.with_suffix(".c")
        source.write_text(
            "/* Copyright fixture */\n/* BSD fixture */\nint fixture(void) { return 0; }\n",
            encoding="utf-8",
        )
        record = obj.with_name("." + obj.name + ".cmd")
        record.write_text(
            f"cmd_{obj.name} := cc -c {recorded}/{source.relative_to(package)}\n"
            f"source_{obj.name} := {recorded}/{source.relative_to(package)}\n",
            encoding="utf-8",
        )

    (package / "8188fu.ko").write_bytes(b"built module\n")
    (package / "8188fu.mod.c").write_bytes(b"generated module source\n")
    config = workspace / "thingino-output/build/linux-3.10.14/.config"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"CONFIG_8188FU=m\n")
    installed = workspace / "thingino-output/target/lib/modules/3.10.14/8188fu.ko"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"installed module\n")
    return workspace


def rewrite_link_record(workspace: Path, inputs: list[str]) -> None:
    package = workspace / "thingino-output/build" / MODULE.PACKAGE
    recorded = MODULE.RECORDED_ROOT.as_posix()
    command = "ld -r -o {root}/8188fu.o {inputs}\n".format(
        root=recorded,
        inputs=" ".join(f"{recorded}/{obj}" for obj in inputs),
    )
    (package / ".8188fu.o.cmd").write_text("cmd_8188fu.o := " + command, encoding="utf-8")


class InventoryRtlLinkedSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        import os

        self.temp = tempfile.TemporaryDirectory(
            prefix="inventory-rtl-linked-sources-", dir=os.environ.get("TMPDIR")
        )
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parses_kbuild_link_and_source_bindings(self) -> None:
        workspace = make_fixture(self.root)
        result = MODULE.inventory(workspace)

        self.assertEqual(result["translation_unit_count"], 2)
        self.assertEqual(
            [item["object"] for item in result["translation_units"]],
            ["core/alpha.o", "core/beta.o"],
        )
        self.assertEqual(
            [item["source"] for item in result["translation_units"]],
            ["core/alpha.c", "core/beta.c"],
        )
        self.assertEqual(result["scope"], "linked-driver-translation-units-not-full-corresponding-source")
        self.assertFalse(result["firmware_release_gate_closed"])
        self.assertEqual(result["license_review_status"], "not-assessed")
        for item in result["translation_units"]:
            self.assertTrue(item["object_identity"]["sha256"])
            self.assertTrue(item["compile_record_identity"]["sha256"])
            self.assertEqual(item["compile_record"], f"core/.{Path(item['object']).name}.cmd")

    def test_rejects_empty_link_inputs(self) -> None:
        workspace = make_fixture(self.root)
        rewrite_link_record(workspace, [])

        with self.assertRaisesRegex(ValueError, "empty or repeated"):
            MODULE.inventory(workspace)

    def test_accepts_exact_merged_usr_library_alias(self) -> None:
        workspace = make_fixture(self.root)
        before = MODULE.inventory(workspace)
        target = workspace / "thingino-output/target"
        (target / "usr").mkdir()
        (target / "lib").rename(target / "usr/lib")
        (target / "lib").symlink_to("usr/lib", target_is_directory=True)
        self.assertEqual(MODULE.inventory(workspace), before)

    def test_rejects_other_library_alias(self) -> None:
        workspace = make_fixture(self.root)
        target = workspace / "thingino-output/target"
        (target / "lib").rename(target / "other-lib")
        (target / "lib").symlink_to("other-lib", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unexpected target library symlink"):
            MODULE.inventory(workspace)

    def test_rejects_duplicate_link_inputs(self) -> None:
        workspace = make_fixture(self.root)
        rewrite_link_record(workspace, ["core/alpha.o", "core/alpha.o"])

        with self.assertRaisesRegex(ValueError, "empty or repeated"):
            MODULE.inventory(workspace)

    def test_rejects_parent_traversal_in_link_input(self) -> None:
        workspace = make_fixture(self.root)
        rewrite_link_record(workspace, ["core/../escape.o"])

        with self.assertRaisesRegex(ValueError, "unsafe recorded source path"):
            MODULE.inventory(workspace)

    def test_rejects_symlinked_ancestors_for_object_and_source(self) -> None:
        workspace = make_fixture(self.root)
        package = workspace / "thingino-output/build" / MODULE.PACKAGE
        real_core = package / "core-real"
        (package / "core").rename(real_core)
        (package / "core").symlink_to(real_core, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlink path component"):
            MODULE.inventory(workspace)

        workspace = make_fixture(self.root / "source-case")
        package = workspace / "thingino-output/build" / MODULE.PACKAGE
        real_sources = package / "source-real"
        real_sources.mkdir()
        for name in ("alpha.c", "beta.c"):
            (package / "core" / name).rename(real_sources / name)
        source_link = package / "source"
        source_link.symlink_to(real_sources, target_is_directory=True)
        recorded = MODULE.RECORDED_ROOT.as_posix()
        for name in ("alpha", "beta"):
            (package / "core" / f".{name}.o.cmd").write_text(
                f"source_{name}.o := {recorded}/source/{name}.c\n", encoding="utf-8"
            )

        with self.assertRaisesRegex(ValueError, "symlink path component"):
            MODULE.inventory(workspace)

    def test_rejects_symlinked_workspace_ancestor(self) -> None:
        workspace = make_fixture(self.root)
        workspace_alias = self.root / "workspace-alias"
        workspace_alias.symlink_to(workspace, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlink path component"):
            MODULE.inventory(workspace_alias)


if __name__ == "__main__":
    unittest.main()
