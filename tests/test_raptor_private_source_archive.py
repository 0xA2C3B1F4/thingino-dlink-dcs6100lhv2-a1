"""Focused tests for the private deterministic Raptor source archive."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from installer.raptor_source import FULL_MEDIA_SOURCES
from scripts import raptor_private_source_archive as archive


ROOT = Path(__file__).resolve().parents[1]


def _git(checkout: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *arguments],
        cwd=checkout,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


class PrivateSourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path(os.environ["TMPDIR"]).resolve(strict=True)
        self._temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name) / "repository"
        self.cache = Path(self._temporary.name) / "cache"
        (self.root / "components/raptor").mkdir(parents=True)
        (self.root / "patches/raptor-full-source").mkdir(parents=True)
        self.cache.mkdir(mode=0o700)
        self._make_sources()

    def _make_sources(self) -> None:
        source_specs: dict[str, dict[str, object]] = {}
        for name in sorted(FULL_MEDIA_SOURCES - {"ingenic-headers"}):
            source_specs[name] = self._make_source(name, notice=False, patch=name == "compy")
        headers = self._make_source("ingenic-headers", notice=True, patch=False)

        headers_payload = {
            "schema_version": 1,
            "url": "https://github.com/example/ingenic-headers.git",
            "base": headers["base"],
            "base_tree": headers["base_tree"],
            "tree": headers["tree"],
            "patches": [],
            "notice": "README.md",
            "notice_sha256": headers["license_sha256"],
            "license_status": "unspecified",
            "redistribution": archive.REDISTRIBUTION_STATUS,
        }
        (self.root / "components/raptor/headers-input.json").write_text(
            json.dumps(headers_payload, indent=2, sort_keys=True) + "\n"
        )
        (self.root / "components/raptor/source-build-lock.json").write_text(
            json.dumps({"schema_version": 1, "sources": source_specs}, indent=2, sort_keys=True)
            + "\n"
        )

    def _make_source(
        self, name: str, *, notice: bool, patch: bool
    ) -> dict[str, object]:
        source = self.cache / name
        source.mkdir()
        _git(source, "init", "--quiet")
        _git(source, "config", "user.email", "archive-tests@example.invalid")
        _git(source, "config", "user.name", "Archive Tests")
        (source / ".gitignore").write_text("secret.txt\nbuild-output/\n")
        evidence = source / ("README.md" if notice else "LICENSE")
        evidence.write_text(f"{name} technical evidence\n")
        (source / f"{name}.txt").write_text(f"base {name}\n")
        if name == "raptor":
            (source / "safe-link").symlink_to("raptor.txt")
        _git(source, "add", "--all")
        _git(source, "commit", "--quiet", "-m", "base")
        base = _git(source, "rev-parse", "HEAD")
        base_tree = _git(source, "rev-parse", "HEAD^{tree}")

        patches: list[dict[str, str]] = []
        if patch:
            (source / f"{name}.txt").write_text(f"base {name}\npatched\n")
            patch_data = subprocess.run(
                ["git", "diff", "--binary"],
                cwd=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout
            patch_path = self.root / "patches/raptor-full-source" / f"{name}.patch"
            patch_path.write_bytes(patch_data)
            _git(source, "reset", "--hard", "--quiet", "HEAD")
            _git(source, "apply", "--index", str(patch_path))
            final_tree = _git(source, "write-tree")
            patches.append(
                {
                    "path": f"patches/raptor-full-source/{name}.patch",
                    "sha256": hashlib.sha256(patch_data).hexdigest(),
                }
            )
        else:
            final_tree = base_tree

        return {
            "url": f"https://github.com/example/{name}.git",
            "base": base,
            "base_tree": base_tree,
            "tree": final_tree,
            "patches": patches,
            "license": "README.md" if notice else "LICENSE",
            "license_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }

    def _output(self, name: str = "source.tar") -> Path:
        return Path(self._temporary.name) / name

    def _load_members(self, output: Path) -> tuple[list[tarfile.TarInfo], dict[str, object]]:
        with tarfile.open(output, "r:") as bundle:
            members = bundle.getmembers()
            manifest = json.loads(bundle.extractfile("manifest.json").read())
        return members, manifest

    def _rewrite_tree(self, name: str, tree: str) -> None:
        path = self.root / "components/raptor/source-build-lock.json"
        lock = json.loads(path.read_text())
        lock["sources"][name]["tree"] = tree
        path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")

    def test_output_is_deterministic_and_contains_only_locked_inputs(self) -> None:
        for name in sorted(FULL_MEDIA_SOURCES):
            source = self.cache / name
            (source / "secret.txt").write_text("must not enter archive\n")
            (source / "build-output").mkdir()
            (source / "build-output/generated.bin").write_bytes(b"build output")

        first = self._output("first.tar")
        second = self._output("second.tar")
        archive.build_private_source_archive(self.root, self.cache, first)
        for name in sorted(FULL_MEDIA_SOURCES):
            source = self.cache / name
            (source / "secret.txt").write_text("changed ignored secret\n")
            (source / "build-output/generated.bin").write_bytes(b"changed build output")
            (source / "build-output/second-generated.bin").write_bytes(b"another ignored file")
        archive.build_private_source_archive(self.root, self.cache, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)

        members, manifest = self._load_members(first)
        names = [member.name for member in members]
        self.assertEqual(manifest["source_count"], 13)
        self.assertEqual(manifest["scope"], "raptor-13-source-closure")
        self.assertFalse(manifest["firmware_corresponding_source_complete"])
        self.assertEqual(manifest["redistribution"], archive.REDISTRIBUTION_STATUS)
        self.assertNotIn("secret.txt", "\n".join(names))
        self.assertNotIn("build-output", "\n".join(names))
        self.assertFalse(any("/.git/" in name or name.endswith("/.git") for name in names))
        for member in members:
            self.assertEqual(member.uid, 0)
            self.assertEqual(member.gid, 0)
            self.assertEqual(member.mtime, 0)
            self.assertEqual(member.uname, "")
            self.assertEqual(member.gname, "")

        lock_member = "repository/components/raptor/source-build-lock.json"
        with tarfile.open(first, "r:") as bundle:
            self.assertEqual(
                bundle.extractfile(lock_member).read(),
                (self.root / "components/raptor/source-build-lock.json").read_bytes(),
            )
            self.assertIn("sources/raptor/safe-link", names)
            self.assertEqual(bundle.getmember("sources/raptor/safe-link").linkname, "raptor.txt")

    def test_archive_uses_canonical_git_blob_bytes(self) -> None:
        source = self.cache / "raptor"
        _git(source, "config", "core.autocrlf", "true")
        (source / "raptor.txt").write_bytes(b"base raptor\r\n")
        self.assertEqual(_git(source, "diff", "--name-only"), "")

        output = self._output()
        archive.build_private_source_archive(self.root, self.cache, output)
        _, manifest = self._load_members(output)
        file_record = next(
            item
            for item in manifest["sources"]["raptor"]["files"]
            if item["path"] == "raptor.txt"
        )
        with tarfile.open(output, "r:") as bundle:
            self.assertEqual(
                bundle.extractfile("sources/raptor/raptor.txt").read(),
                b"base raptor\n",
            )
        self.assertEqual(
            file_record["sha256"], hashlib.sha256(b"base raptor\n").hexdigest()
        )

    def test_published_readback_failure_removes_new_output(self) -> None:
        output = self._output()
        original_publish = archive._publish_exclusive

        def publish_then_corrupt(temporary: Path, destination: Path) -> None:
            original_publish(temporary, destination)
            with destination.open("ab") as handle:
                handle.write(b"corruption")

        with mock.patch.object(archive, "_publish_exclusive", side_effect=publish_then_corrupt):
            with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "readback mismatch"):
                archive.build_private_source_archive(self.root, self.cache, output)
        self.assertFalse(output.exists())

    def test_git_replace_refs_cannot_change_archived_blob(self) -> None:
        source = self.cache / "raptor"
        original = _git(source, "rev-parse", "HEAD:raptor.txt")
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        replacement = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=source,
            env=environment,
            input=b"replacement\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.decode("ascii").strip()
        subprocess.run(
            ["git", "replace", original, replacement],
            cwd=source,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        output = self._output()
        archive.build_private_source_archive(self.root, self.cache, output)
        with tarfile.open(output, "r:") as bundle:
            self.assertEqual(
                bundle.extractfile("sources/raptor/raptor.txt").read(),
                b"base raptor\n",
            )

    def test_gitlinks_are_recorded_without_following_them(self) -> None:
        source = self.cache / "datatype99"
        commit = "39081c9c42768ab5e8321127a7494ad1647c6a2f"
        (source / "run-clang-format").mkdir()
        _git(source, "update-index", "--add", "--cacheinfo", f"160000,{commit},run-clang-format")
        self._rewrite_tree("datatype99", _git(source, "write-tree"))

        output = self._output()
        archive.build_private_source_archive(self.root, self.cache, output)
        _, manifest = self._load_members(output)
        gitlinks = manifest["sources"]["datatype99"]["gitlinks"]
        entries = archive._index_entries(source)
        gitlink_mode, gitlink_commit, _stage, gitlink_path = next(
            entry for entry in entries if entry[0] == "160000"
        )
        self.assertEqual(
            (gitlink_mode, gitlink_commit, gitlink_path),
            ("160000", commit, "run-clang-format"),
        )
        self.assertEqual(
            gitlinks,
            [
                {
                    "commit": commit,
                    "delivery": "not-included-development-formatter-scope-unreviewed",
                    "path": "run-clang-format",
                }
            ],
        )
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "unclassified source gitlink"):
            archive._gitlink_delivery("datatype99", "development-link")
        with tarfile.open(output, "r:") as bundle:
            self.assertNotIn("sources/datatype99/run-clang-format", bundle.getnames())

    def test_separate_gitlink_must_match_locked_target_base(self) -> None:
        source = self.cache / "mbedtls"
        commit = _git(source, "rev-parse", "HEAD")
        (source / "framework").mkdir()
        _git(source, "update-index", "--add", "--cacheinfo", f"160000,{commit},framework")
        self._rewrite_tree("mbedtls", _git(source, "write-tree"))

        output = self._output()
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "locked target base"):
            archive.build_private_source_archive(self.root, self.cache, output)
        self.assertFalse(output.exists())

    def test_existing_or_symlink_destination_is_never_replaced(self) -> None:
        existing = self._output("existing.tar")
        existing.write_bytes(b"keep this evidence")
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "already exists"):
            archive.build_private_source_archive(self.root, self.cache, existing)
        self.assertEqual(existing.read_bytes(), b"keep this evidence")

        target = self._output("target.tar")
        target.write_bytes(b"target")
        linked = self._output("linked.tar")
        linked.symlink_to(target)
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "already exists"):
            archive.build_private_source_archive(self.root, self.cache, linked)
        self.assertEqual(target.read_bytes(), b"target")

    def test_dirty_or_wrong_tree_cache_is_rejected_without_output(self) -> None:
        (self.cache / "raptor/raptor.txt").write_text("dirty\n")
        output = self._output()
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "integrity check failed: raptor"):
            archive.build_private_source_archive(self.root, self.cache, output)
        self.assertFalse(output.exists())

        _git(self.cache / "raptor", "reset", "--hard", "--quiet", "HEAD")
        lock_path = self.root / "components/raptor/source-build-lock.json"
        lock = json.loads(lock_path.read_text())
        lock["sources"]["raptor"]["tree"] = "0" * 40
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "integrity check failed: raptor"):
            archive.build_private_source_archive(self.root, self.cache, output)
        self.assertFalse(output.exists())

    def test_escaping_symlink_is_rejected_and_leaves_no_final_output(self) -> None:
        source = self.cache / "raptor"
        (source / "safe-link").unlink()
        (source / "safe-link").symlink_to("../../outside")
        _git(source, "add", "safe-link")
        self._rewrite_tree("raptor", _git(source, "write-tree"))

        output = self._output()
        with self.assertRaisesRegex(archive.PrivateSourceArchiveError, "escapes its tree"):
            archive.build_private_source_archive(self.root, self.cache, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
