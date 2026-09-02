from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import local_build_acquire


IMAGE_ID = "sha256:" + "a" * 64
LOCK_SHA256 = "b" * 64
ARCHIVE_SHA256 = hashlib.sha256(b"locked archive").hexdigest()


def _add_bytes(bundle: tarfile.TarFile, name: str, value: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(value)
    bundle.addfile(member, io.BytesIO(value))


class LocalBuildAcquireTests(unittest.TestCase):
    def test_archive_acquisition_is_exact_and_revalidates_cached_bytes(self) -> None:
        source = {
            "url": "https://static.rust-lang.org/dist/locked.tar.xz",
            "sha256": ARCHIVE_SHA256,
        }
        with tempfile.TemporaryDirectory() as name:
            cache = Path(name) / "downloads"
            cache.mkdir(mode=0o700)

            def download(
                *, url: str, destination: Path, expected_sha256: str
            ) -> tuple[int, str]:
                self.assertEqual(url, source["url"])
                self.assertEqual(expected_sha256, ARCHIVE_SHA256)
                destination.write_bytes(b"locked archive")
                return len(b"locked archive"), ARCHIVE_SHA256

            with mock.patch.object(
                local_build_acquire,
                "_download",
                side_effect=download,
            ) as fetch:
                first = local_build_acquire.acquire_archive(
                    cache_root=cache,
                    name="rust_source",
                    source=source,
                )
                second = local_build_acquire.acquire_archive(
                    cache_root=cache,
                    name="rust_source",
                    source=source,
                )
            self.assertEqual(first, second)
            fetch.assert_called_once()

            Path(str(first["archive"])).write_bytes(b"changed")
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "cached rust_source archive changed",
            ):
                local_build_acquire.acquire_archive(
                    cache_root=cache,
                    name="rust_source",
                    source=source,
                )

    def test_archive_acquisition_rejects_symlinked_cache_children(self) -> None:
        source = {
            "url": "https://static.rust-lang.org/dist/locked.tar.xz",
            "sha256": ARCHIVE_SHA256,
        }
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            cache = root / "downloads"
            outside = root / "outside"
            cache.mkdir(mode=0o700)
            outside.mkdir(mode=0o700)
            (cache / "archives").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "not a real directory",
            ):
                local_build_acquire.acquire_archive(
                    cache_root=cache,
                    name="rust_source",
                    source=source,
                )

    def test_tar_validation_rejects_traversal_duplicate_and_escaping_link(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            traversal = root / "traversal.tar"
            with tarfile.open(traversal, "w") as bundle:
                _add_bytes(bundle, "../outside", b"x")
            duplicate = root / "duplicate.tar"
            with tarfile.open(duplicate, "w") as bundle:
                _add_bytes(bundle, "tree/file", b"one")
                _add_bytes(bundle, "tree/file", b"two")
            link = root / "link.tar"
            with tarfile.open(link, "w") as bundle:
                member = tarfile.TarInfo("tree/link")
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                bundle.addfile(member)

            for archive, message in (
                (traversal, "unsafe path"),
                (duplicate, "duplicate path"),
                (link, "symlink escapes"),
            ):
                with self.subTest(archive=archive.name):
                    with self.assertRaisesRegex(
                        local_build_acquire.LocalBuildAcquireError,
                        message,
                    ):
                        local_build_acquire._validate_tar(
                            archive,
                            expected_top="tree",
                        )

    def test_tar_validation_rejects_case_alias_receipt_and_parent_collision(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            case_alias = root / "case-alias.tar"
            with tarfile.open(case_alias, "w") as bundle:
                _add_bytes(bundle, "tree/File", b"one")
                _add_bytes(bundle, "tree/file", b"two")
            receipt = root / "receipt.tar"
            with tarfile.open(receipt, "w") as bundle:
                _add_bytes(
                    bundle,
                    f"tree/{local_build_acquire.EXTRACTION_RECEIPT}",
                    b"{}",
                )
            collision = root / "collision.tar"
            with tarfile.open(collision, "w") as bundle:
                _add_bytes(bundle, "tree/parent", b"file")
                _add_bytes(bundle, "tree/parent/child", b"child")
            reverse_collision = root / "reverse-collision.tar"
            with tarfile.open(reverse_collision, "w") as bundle:
                _add_bytes(bundle, "tree/parent/child", b"child")
                _add_bytes(bundle, "tree/parent", b"file")

            for archive, message in (
                (case_alias, "duplicate path"),
                (receipt, "reserved receipt path"),
                (collision, "parent type collision"),
                (reverse_collision, "parent type collision"),
            ):
                with self.subTest(archive=archive.name):
                    with self.assertRaisesRegex(
                        local_build_acquire.LocalBuildAcquireError,
                        message,
                    ):
                        local_build_acquire._validate_tar(
                            archive,
                            expected_top="tree",
                        )

    def test_receipt_loader_rejects_duplicate_json_fields(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            receipt = Path(name) / "receipt.json"
            receipt.write_text('{"schema_version":1,"schema_version":1}\n')
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "duplicate fields",
            ):
                local_build_acquire._load_json_object(receipt, "test receipt")

    def test_extraction_receipt_detects_cached_tree_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive = root / "safe.tar"
            output = root / "output"
            output.mkdir(mode=0o700)
            with tarfile.open(archive, "w") as bundle:
                directory = tarfile.TarInfo("tree")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                bundle.addfile(directory)
                _add_bytes(bundle, "tree/file", b"content")
            archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

            first = local_build_acquire.extract_archive(
                archive=archive,
                expected_sha256=archive_sha256,
                output_parent=output,
                expected_top="tree",
            )
            second = local_build_acquire.extract_archive(
                archive=archive,
                expected_sha256=archive_sha256,
                output_parent=output,
                expected_top="tree",
            )
            self.assertEqual(first, second)
            self.assertTrue(
                (first / local_build_acquire.EXTRACTION_RECEIPT).is_file()
            )

            (first / "file").write_bytes(b"changed")
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "cached extracted archive changed",
            ):
                local_build_acquire.extract_archive(
                    archive=archive,
                    expected_sha256=archive_sha256,
                    output_parent=output,
                    expected_top="tree",
                )

    def test_selected_extraction_normalizes_only_allowlisted_case_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive = root / "selected.tar"
            output = root / "output"
            output.mkdir(mode=0o700)
            with tarfile.open(archive, "w") as bundle:
                for directory_name in (
                    "tree",
                    "tree/library",
                    "tree/vendor",
                    "tree/vendor/crate",
                    "tree/tests",
                    "tree/tests/ABCD",
                    "tree/tests/abcd",
                ):
                    directory = tarfile.TarInfo(directory_name)
                    directory.type = tarfile.DIRTYPE
                    directory.mode = 0o755
                    bundle.addfile(directory)
                _add_bytes(bundle, "tree/library/Cargo.toml", b"[workspace]\n")
                _add_bytes(bundle, "tree/vendor/crate/README.md", b"same\n")
                _add_bytes(bundle, "tree/vendor/crate/readme.md", b"same\n")
                _add_bytes(bundle, "tree/tests/ABCD/mod.rs", b"A")
                _add_bytes(bundle, "tree/tests/abcd/mod.rs", b"a")
            archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
            alias = frozenset(
                {
                    ("tree", "vendor", "crate", "README.md"),
                    ("tree", "vendor", "crate", "readme.md"),
                }
            )

            extracted = local_build_acquire.extract_archive(
                archive=archive,
                expected_sha256=archive_sha256,
                output_parent=output,
                expected_top="tree",
                included_roots=frozenset({"library", "vendor"}),
                allowed_casefold_aliases=frozenset({alias}),
                skipped_members=frozenset({"tree/vendor/crate/readme.md"}),
            )

            self.assertEqual(
                (extracted / "vendor/crate/README.md").read_bytes(),
                b"same\n",
            )
            self.assertEqual(
                sorted(path.name for path in (extracted / "vendor/crate").iterdir()),
                ["README.md"],
            )
            self.assertFalse((extracted / "tests").exists())

    def test_allowlisted_case_alias_still_requires_matching_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / "mismatch.tar"
            with tarfile.open(archive, "w") as bundle:
                _add_bytes(bundle, "tree/vendor/README.md", b"one")
                _add_bytes(bundle, "tree/vendor/readme.md", b"two")
            alias = frozenset(
                {
                    ("tree", "vendor", "README.md"),
                    ("tree", "vendor", "readme.md"),
                }
            )
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "duplicate path",
            ):
                with tarfile.open(archive, "r") as bundle:
                    local_build_acquire._validate_tar_members(
                        bundle,
                        expected_top="tree",
                        included_roots=frozenset({"vendor"}),
                        allowed_casefold_aliases=frozenset({alias}),
                    )

    def test_ingenic_archive_preserves_case_distinct_git_paths(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / "ingenic.tar"
            with tarfile.open(archive, "w") as bundle:
                for member_name in sorted(local_build_acquire.INGENIC_ARCHIVE_REQUIRED):
                    _add_bytes(bundle, member_name, b"required")
                _add_bytes(
                    bundle,
                    f"{local_build_acquire.INGENIC_ARCHIVE_TOP}/include/xt_MARK.h",
                    b"upper",
                )
                _add_bytes(
                    bundle,
                    f"{local_build_acquire.INGENIC_ARCHIVE_TOP}/include/xt_mark.h",
                    b"lower",
                )

            local_build_acquire._validate_ingenic_archive(archive)

    def test_rust_toolchain_install_is_bound_to_image_and_archives(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            cache = root / "downloads"
            distribution = root / "rust"
            source_component = root / "rust-src"
            cache.mkdir(mode=0o700)
            distribution.mkdir()
            source_component.mkdir()

            def install(
                arguments: list[str],
                *,
                label: str,
                timeout: int = 1800,
            ) -> str:
                if label == "Rust toolchain installation":
                    mount = next(
                        item for item in arguments if item.endswith(":/output")
                    )
                    temporary = Path(mount.removesuffix(":/output"))
                    (temporary / "bin").mkdir(parents=True)
                    (temporary / "bin/rustc").write_bytes(b"rustc")
                    (temporary / "bin/cargo").write_bytes(b"cargo")
                    source = temporary / "lib/rustlib/src/rust/library"
                    source.mkdir(parents=True)
                    (source / "Cargo.toml").write_text(
                        "[workspace]\n",
                        encoding="utf-8",
                    )
                    return ""
                self.assertEqual(label, "Rust toolchain identity check")
                return (
                    f"release: {local_build_acquire.EXPECTED_RUST_RELEASE}\n"
                    f"commit-hash: {local_build_acquire.EXPECTED_RUST_COMMIT}\n"
                    f"cargo {local_build_acquire.EXPECTED_RUST_RELEASE} (locked)"
                )

            with mock.patch.object(
                local_build_acquire,
                "_run",
                side_effect=install,
            ) as run:
                first = local_build_acquire._install_rust_toolchain(
                    cache_root=cache,
                    builder_image_id=IMAGE_ID,
                    rust_distribution=distribution,
                    rust_distribution_sha256="d" * 64,
                    rust_source_component=source_component,
                    rust_source_component_sha256="e" * 64,
                    identity="c" * 64,
                )
                second = local_build_acquire._install_rust_toolchain(
                    cache_root=cache,
                    builder_image_id=IMAGE_ID,
                    rust_distribution=distribution,
                    rust_distribution_sha256="d" * 64,
                    rust_source_component=source_component,
                    rust_source_component_sha256="e" * 64,
                    identity="c" * 64,
                )
            self.assertEqual(first, second)
            self.assertEqual(run.call_count, 3)

            toolchain = first[0]
            (toolchain / "bin/cargo").write_bytes(b"changed")
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "toolchain identity changed",
            ):
                local_build_acquire._install_rust_toolchain(
                    cache_root=cache,
                    builder_image_id=IMAGE_ID,
                    rust_distribution=distribution,
                    rust_distribution_sha256="d" * 64,
                    rust_source_component=source_component,
                    rust_source_component_sha256="e" * 64,
                    identity="c" * 64,
                )

    def test_public_input_manifest_contains_only_locked_build_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            build_root = Path(name) / "build"
            downloads = build_root / "cache/downloads"
            sources_cache = build_root / "cache/sources"
            root.mkdir()
            downloads.mkdir(parents=True, mode=0o700)
            sources_cache.mkdir(mode=0o700)
            (root / "sources.lock.json").write_text("{}\n", encoding="utf-8")
            lock = {
                "sources": {
                    **{
                        item: {
                            "url": f"https://static.rust-lang.org/{item}.tar.xz",
                            "sha256": "e" * 64,
                        }
                        for item in local_build_acquire.ARCHIVE_NAMES
                    },
                    "ingenic_glibc216_toolchain": {
                        "url": "https://github.com/example/toolchain.git",
                        "revision": "f" * 40,
                        "tree": "1" * 40,
                    },
                    "thingino_firmware": {
                        "url": "https://github.com/example/thingino.git",
                        "revision": "2" * 40,
                        "tree": "3" * 40,
                    },
                    "thingino_build_toolchain_aarch64": {
                        "acquisition": "source-build",
                        "archive": "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz",
                        "archive_format": "gnu-tar-sort-name-source-date-epoch-gzip-n",
                        "sha256": "7" * 64,
                    },
                }
            }
            builder_receipt = build_root / "cache/images/builder.json"
            builder_receipt.parent.mkdir()
            builder_receipt.write_text("{}\n", encoding="utf-8")
            rust_source = downloads / "rust-source"
            rust_source.mkdir()
            (rust_source / local_build_acquire.EXTRACTION_RECEIPT).write_text(
                json.dumps({"tree_sha256": "4" * 64}),
                encoding="utf-8",
            )
            rust_toolchain = downloads / "rust-toolchain"
            rust_toolchain.mkdir()
            ingenic_archive = downloads / "ingenic.tar"
            ingenic_archive.write_bytes(b"ingenic")
            rust_receipt = {
                "builder_image_id": IMAGE_ID,
                "identity": "5" * 64,
                "schema_version": 1,
                "tree_sha256": "6" * 64,
            }
            (rust_toolchain / local_build_acquire.RUST_RECEIPT).write_text(
                json.dumps(rust_receipt),
                encoding="utf-8",
            )

            def receipt(
                *,
                cache_root: Path,
                name: str,
                source: dict[str, object],
            ) -> dict[str, object]:
                return {
                    "archive": str(downloads / f"{name}.tar.xz"),
                    "name": name,
                    "schema_version": 1,
                    "sha256": source["sha256"],
                    "size": 1,
                    "url": source["url"],
                }

            with (
                mock.patch.object(
                    local_build_acquire,
                    "bootstrap_public_build_inputs",
                    return_value={
                        "build_root": str(build_root),
                        "builder_image": {
                            "id": IMAGE_ID,
                            "receipt": str(builder_receipt),
                            "tag": "builder:locked",
                        },
                        "source_checkout": str(sources_cache / "thingino"),
                        "source_date_epoch": 1234,
                        "sources_lock_sha256": LOCK_SHA256,
                    },
                ),
                mock.patch.object(
                    local_build_acquire,
                    "_project_root",
                    return_value=root,
                ),
                mock.patch.object(
                    local_build_acquire,
                    "load_source_lock_snapshot",
                    return_value=(lock, LOCK_SHA256),
                ),
                mock.patch.object(
                    local_build_acquire,
                    "acquire_archive",
                    side_effect=receipt,
                ),
                mock.patch.object(
                    local_build_acquire,
                    "extract_archive",
                    side_effect=[
                        rust_source,
                        downloads / "rust-distribution",
                        downloads / "rust-src",
                    ],
                ),
                mock.patch.object(
                    local_build_acquire,
                    "_install_rust_toolchain",
                    return_value=(rust_toolchain, rust_receipt),
                ),
                mock.patch.object(
                    local_build_acquire,
                    "_acquire_ingenic_toolchain",
                    return_value=(
                        ingenic_archive,
                        {
                            "archive": str(ingenic_archive),
                            "archive_sha256": "8" * 64,
                            "archive_size": len(b"ingenic"),
                            "revision": "f" * 40,
                            "schema_version": 1,
                            "tree": "1" * 40,
                            "url": "https://github.com/example/toolchain",
                        },
                    ),
                ),
            ):
                result = local_build_acquire.acquire_locked_public_inputs(
                    build_root=build_root
                )
            manifest = json.loads(Path(str(result["manifest"])).read_text())
            self.assertEqual(manifest["builder_image"]["id"], IMAGE_ID)
            self.assertEqual(
                manifest["next_action"],
                "build-source-locked-thingino-toolchain",
            )
            self.assertEqual(
                manifest["thingino_toolchain"]["acquisition"],
                "source-build",
            )
            self.assertNotIn(
                "thingino_build_toolchain_aarch64",
                manifest["archives"],
            )
            rendered = json.dumps(manifest)
            self.assertNotIn("credential", rendered)
            self.assertNotIn("vendor_bundle", rendered)

    def test_acquisition_stops_if_source_lock_changes_after_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            build_root = Path(name) / "build"
            root.mkdir()
            (root / "sources.lock.json").write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(
                    local_build_acquire,
                    "bootstrap_public_build_inputs",
                    return_value={
                        "build_root": str(build_root),
                        "sources_lock_sha256": "a" * 64,
                    },
                ),
                mock.patch.object(
                    local_build_acquire,
                    "_project_root",
                    return_value=root,
                ),
                mock.patch.object(
                    local_build_acquire,
                    "load_source_lock_snapshot",
                    return_value=({"sources": {}}, "b" * 64),
                ),
                mock.patch.object(
                    local_build_acquire,
                    "acquire_archive",
                ) as acquire,
            ):
                with self.assertRaisesRegex(
                    local_build_acquire.LocalBuildAcquireError,
                    "source lock changed",
                ):
                    local_build_acquire.acquire_locked_public_inputs(
                        build_root=build_root
                    )
            acquire.assert_not_called()

    def test_download_policy_rejects_non_https_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(
                local_build_acquire.LocalBuildAcquireError,
                "approved HTTPS policy",
            ):
                local_build_acquire._download(
                    url="http://static.rust-lang.org/input.tar.xz",
                    destination=Path(name) / "download",
                    expected_sha256="0" * 64,
                )


if __name__ == "__main__":
    unittest.main()
