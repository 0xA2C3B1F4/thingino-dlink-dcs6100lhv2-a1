from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "components/thingino-control/scripts/run_native_uhttpd_proxy.py"
SPEC = importlib.util.spec_from_file_location("run_native_uhttpd_proxy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class NativeUhttpdProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def _git(self, repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _prepared_source(self) -> tuple[Path, str]:
        source = self.root / "source"
        source.mkdir()
        (source / "file.c").write_text("canonical\n", encoding="utf-8")
        self._git(source, "init", "--quiet")
        self._git(source, "add", "--all")
        return source, self._git(source, "write-tree")

    def test_accepts_exact_staged_source_tree_without_a_commit(self) -> None:
        source, tree = self._prepared_source()
        RUNNER.verify_source_tree(source, tree)

    def test_source_verification_does_not_lock_read_only_source_index(self) -> None:
        source, tree = self._prepared_source()
        git_directory = source / ".git"
        git_directory.chmod(0o555)
        self.addCleanup(git_directory.chmod, 0o755)
        RUNNER.verify_source_tree(source, tree)

    def test_rejects_modified_or_untracked_source(self) -> None:
        source, tree = self._prepared_source()
        (source / "file.c").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RUNNER.RunnerError, "prepared index"):
            RUNNER.verify_source_tree(source, tree)
        self._git(source, "checkout", "--", "file.c")
        (source / "extra.c").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(RUNNER.RunnerError, "prepared index"):
            RUNNER.verify_source_tree(source, tree)

    def test_rejects_wrong_source_tree(self) -> None:
        source, _ = self._prepared_source()
        with self.assertRaisesRegex(RUNNER.RunnerError, "canonical ingress"):
            RUNNER.verify_source_tree(source, "0" * 40)

    def test_resolves_every_manifest_patch_in_order_and_checks_hashes(self) -> None:
        repository_patches = self.root / "repository-patches"
        upstream_patches = self.root / "upstream-patches"
        repository_patches.mkdir()
        upstream_patches.mkdir()
        upstream = upstream_patches / "0004-upstream.patch"
        local = repository_patches / "0007-local.patch"
        upstream.write_bytes(b"upstream\n")
        local.write_bytes(b"local\n")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "patches": [
                {"name": upstream.name, "sha256": digest(upstream)},
                {"name": local.name, "sha256": digest(local)},
            ]
        }
        with mock.patch.object(RUNNER, "REPOSITORY_PATCHES", repository_patches):
            chain = RUNNER.resolve_patch_chain(manifest, upstream_patches)
        self.assertEqual([path.name for path, _ in chain], [upstream.name, local.name])

        local.write_bytes(b"changed\n")
        with mock.patch.object(RUNNER, "REPOSITORY_PATCHES", repository_patches):
            with self.assertRaisesRegex(RUNNER.RunnerError, "hash changed"):
                RUNNER.resolve_patch_chain(manifest, upstream_patches)

    def test_rejects_repository_patch_absent_from_manifest(self) -> None:
        repository_patches = self.root / "repository-patches"
        upstream_patches = self.root / "upstream-patches"
        repository_patches.mkdir()
        upstream_patches.mkdir()
        (repository_patches / "unexpected.patch").write_text("diff\n", encoding="utf-8")
        with mock.patch.object(RUNNER, "REPOSITORY_PATCHES", repository_patches):
            with self.assertRaisesRegex(RUNNER.RunnerError, "absent"):
                RUNNER.resolve_patch_chain({"patches": []}, upstream_patches)

    def test_rejects_patch_offset_diagnostic(self) -> None:
        source = self.root / "source"
        logs = self.root / "logs"
        source.mkdir()
        logs.mkdir()
        patch = self.root / "test.patch"
        patch.write_text("diff\n", encoding="utf-8")
        with mock.patch.object(RUNNER, "_run_logged", return_value="Hunk #1 succeeded at 9 (offset 2 lines).\n"):
            with self.assertRaisesRegex(RUNNER.RunnerError, "strictly"):
                RUNNER.apply_patch_chain(source, [(patch, "0" * 64)], logs)

    def test_rejects_existing_or_overlapping_output(self) -> None:
        source = self.root / "source"
        prefix = self.root / "prefix"
        upstream = self.root / "upstream"
        repository = self.root / "repository"
        for directory in (source, prefix / "include/libubox", prefix / "lib", upstream, repository):
            directory.mkdir(parents=True)
        (prefix / "lib/libubox.so").write_bytes(b"")
        (prefix / "lib/libustream-ssl.so").write_bytes(b"")
        output = self.root / "output"
        output.mkdir()
        with mock.patch.object(RUNNER, "ROOT", repository):
            with self.assertRaisesRegex(RUNNER.RunnerError, "must not already exist"):
                RUNNER.validate_paths(source, prefix, upstream, output)
            with self.assertRaisesRegex(RUNNER.RunnerError, "must not overlap"):
                RUNNER.validate_paths(source, prefix, upstream, source / "output")

    def test_cmake_configuration_keeps_dependency_prefix_read_only(self) -> None:
        command = RUNNER.cmake_configure_command(
            Path("/work/source"), Path("/work/build"), Path("/work/install"), Path("/deps/prefix")
        )
        self.assertIn("-DCMAKE_INSTALL_PREFIX=/work/install", command)
        self.assertIn("-DCMAKE_PREFIX_PATH=/deps/prefix", command)
        self.assertIn("-DTLS_SUPPORT=ON", command)
        self.assertIn("-DLUA_SUPPORT=OFF", command)
        self.assertIn("-DUBUS_SUPPORT=OFF", command)
        self.assertIn("-DUCODE_SUPPORT=OFF", command)

    def test_timeout_log_decodes_captured_bytes(self) -> None:
        log = self.root / "timeout.log"
        expired = subprocess.TimeoutExpired(["tool"], 1, output=b"partial output\n")
        with mock.patch.object(RUNNER.subprocess, "run", side_effect=expired):
            with self.assertRaisesRegex(RUNNER.RunnerError, "timed out"):
                RUNNER._run_logged(
                    ["tool"], cwd=self.root, env={}, log=log, timeout=1
                )
        self.assertEqual(log.read_text(encoding="utf-8"), "partial output\n\ncommand timed out\n")


if __name__ == "__main__":
    unittest.main()
