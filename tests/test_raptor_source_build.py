"""Source identity, component contract and project/build selection regressions."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import struct
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import ExitStack
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import raptor_source as source
from installer import raptor_component as component
from installer import (
    raptor_build,
    local_build,
    local_build_run,
    user_cli,
    user_cli_project,
)
from installer import install_project as projects

ROOT = Path(__file__).resolve().parents[1]


def elf(dependency="libc.so.6", alignment=4096, extra_tag=0):
    raw = bytearray(1024)
    raw[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<HHI", raw, 16, 3, 8, 1)
    struct.pack_into("<I", raw, 28, 52)
    struct.pack_into("<I", raw, 36, 0x70001000)
    raw[700:713] = b"/lib/ld.so.1\0"
    struct.pack_into("<HHH", raw, 40, 52, 32, 2)
    struct.pack_into("<8I", raw, 52, 1, 0, 0, 0, 1024, 1024, 5, alignment)
    struct.pack_into("<8I", raw, 84, 2, 256, 256, 256, 32, 32, 6, 4)
    struct.pack_into("<8I", raw, 256, 5, 512, 1, 0, extra_tag, 0, 0, 0)
    value = dependency.encode() + b"\0"
    raw[512 : 512 + len(value)] = value
    return bytes(raw)


class ComponentTests(unittest.TestCase):
    def test_overlay_requires_loader_and_atomic_inside_target_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/lib").mkdir()
            (root / "lib").symlink_to("usr/lib")
            for relative in (
                "usr/bin/rwd",
                "usr/lib/librss_common.so",
                "usr/lib/librss_ipc.so",
            ):
                (root / relative).write_bytes(elf(dependency="libatomic.so.1"))
            (root / "usr/lib/ld-real.so").write_bytes(elf())
            (root / "usr/lib/ld.so.1").symlink_to("/usr/lib/ld-real.so")
            atomic = root / "usr/lib/libatomic.so.1"
            atomic.write_bytes(elf())
            component.validate_runtime_dependencies(root)
            atomic.unlink()
            with self.assertRaisesRegex(ValueError, "libatomic.so.1"):
                component.validate_runtime_dependencies(root)
            atomic.write_bytes(elf())
            (root / "usr/lib/ld-real.so").unlink()
            with self.assertRaisesRegex(ValueError, "ld.so.1"):
                component.validate_runtime_dependencies(root)
            (root / "usr/lib/ld.so.1").unlink()
            (root / "usr/lib/ld.so.1").symlink_to("../../../outside")
            with self.assertRaisesRegex(ValueError, "escapes image root"):
                component.validate_runtime_dependencies(root)

    def test_elf_rejects_rpath_alignment_and_tls_or_hal_dependencies(self):
        self.assertEqual(component.audit_elf(elf(), "rwd")["load_alignment"], 4096)
        from installer.media_closure import PROVEN_IDENTITIES

        for dependency in ("ld.so.1", "libatomic.so.1"):
            self.assertIn("lib/" + dependency, PROVEN_IDENTITIES)
            self.assertEqual(
                component.audit_elf(elf(dependency=dependency), "rwd")["needed"],
                [dependency],
            )
        for kwargs in (
            {"alignment": 65536},
            {"extra_tag": 15},
            {"extra_tag": 29},
            {"dependency": "libmbedtls.so.21"},
            {"dependency": "libimp.so"},
            {"dependency": "libraptor_hal.so"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                component.audit_elf(elf(**kwargs), "rwd")

    def make_archive(self, path, *, change=None):
        files = {
            "raptor-lock.json": (
                ROOT / "components/raptor-rwd/raptor-lock.json"
            ).read_bytes(),
            "etc/raptor.conf": (
                ROOT / "components/raptor-rwd/raptor.conf"
            ).read_bytes(),
            "usr/bin/rwd": elf(),
            "usr/lib/librss_common.so": elf(),
            "usr/lib/librss_ipc.so": elf(),
        }
        manifest = {
            "schema_version": 1,
            "kind": "raptor-rwd-static-component",
            "recipe": source.recipe_identity(ROOT),
            "builder_image_id": "sha256:" + "a" * 64,
            "toolchain_sha256": json.loads((ROOT / "sources.lock.json").read_bytes())[
                "sources"
            ]["thingino_build_toolchain_aarch64"]["sha256"],
            "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
        }
        files["component.json"] = json.dumps(manifest).encode()
        if change:
            change(files)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name.startswith("usr/") else 0o644
                archive.addfile(member, io.BytesIO(data))
        path.write_bytes(gzip.compress(output.getvalue()))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_component_accepts_only_current_small_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "component.tar.gz"
            sha = self.make_archive(path)
            raw, hashes = component.validate_persistent_artifact(
                path,
                expected_sha256=sha,
                supervisor=ROOT / "components/raptor-rwd/S13prudynt-rwd",
            )
            self.assertIn("usr/bin/rwd", hashes)
            self.assertNotIn("usr/bin/prudynt", hashes)
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                self.assertEqual(set(archive.getnames()), component.MEMBERS)
            for change in (
                lambda f: f.pop("usr/lib/librss_ipc.so"),
                lambda f: f.update({"usr/bin/prudynt": elf()}),
                lambda f: f.update({"component.json": b"[]"}),
                lambda f: f.update({"component.json": b"{"}),
                lambda f: f.update({"usr/bin/rwd": elf(dependency="libimp.so")}),
            ):
                sha = self.make_archive(path, change=change)
                with self.assertRaises(ValueError):
                    component.validate_persistent_artifact(
                        path,
                        expected_sha256=sha,
                        supervisor=ROOT / "components/raptor-rwd/S13prudynt-rwd",
                    )

    def test_bounded_gzip_does_not_fall_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.tar.gz"
            path.write_bytes(b"\x1f\x8b")
            with self.assertRaises(ValueError):
                component.validate_persistent_artifact(
                    path,
                    expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    supervisor=ROOT / "components/raptor-rwd/S13prudynt-rwd",
                )

    def test_gzip_checksum_failure_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "component.tar.gz"
            self.make_archive(path)
            raw = bytearray(path.read_bytes())
            raw[-8] ^= 1
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "encoding or structure"):
                component.validate_component(
                    path, expected_sha256=hashlib.sha256(raw).hexdigest(), root=ROOT
                )


class SourceTests(unittest.TestCase):
    def test_lock_and_patch_hashes_bind_original_trees(self):
        lock = source.source_lock(ROOT)
        expected = {
            "raptor": "ad92a10d7b9ec25bf66505e39b1f459e7e172080",
            "raptor-ipc": "9f1d9a6abd93f08b4e368341caad0285b92a2068",
            "compy": "b7d88f71ed9d3b0019efd82a19832a01a8b8c837",
        }
        for name, tree in expected.items():
            self.assertEqual(lock["sources"][name]["tree"], tree)
        self.assertEqual(
            lock["runtime_tree"], "2b1cc48de65d698e0453c469fb28009648f67018"
        )
        self.assertEqual(len(lock["sources"]), 10)

    def test_missing_or_changed_source_patch_fails_before_fetch(self):
        with mock.patch.object(source, "digest", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "patch identity"):
                source.source_lock(ROOT)
        with mock.patch.object(
            source, "digest", side_effect=ValueError("missing patch")
        ):
            with self.assertRaisesRegex(ValueError, "missing patch"):
                source.source_lock(ROOT)

    def test_real_git_tree_and_working_copy_revalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)

            def git(*args):
                return (
                    subprocess.check_output(
                        ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL
                    )
                    .decode()
                    .strip()
                )

            git("init", "--quiet")
            (path / "LICENSE").write_text("fixture license\n")
            (path / "file.c").write_text("int fixture;\n")
            git("add", ".")
            git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "fixture",
            )
            spec = {
                "base": git("rev-parse", "HEAD"),
                "license": "LICENSE",
                "license_sha256": source.digest(path / "LICENSE"),
            }
            tree = git("write-tree")
            before = source.verify_source(path, spec, tree=tree)
            self.assertEqual(len(before), 64)
            (path / "file.c").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "working tree changed"):
                source.verify_source(path, spec, tree=tree)
            git("add", "file.c")
            with self.assertRaisesRegex(ValueError, "Git identity"):
                source.verify_source(path, spec, tree=tree)


class CacheTests(unittest.TestCase):
    make_archive = ComponentTests.make_archive

    def test_timeout_and_interrupt_cleanup_only_owned_container(self):
        token = "f" * 32
        container_id = "a" * 64
        for failure in (
            local_build_run.LocalBuildRunError("client timeout"),
            KeyboardInterrupt(),
        ):
            for owner in (token, "another-owner"):
                with (
                    self.subTest(failure=type(failure).__name__, owner=owner),
                    mock.patch.object(
                        raptor_build.uuid,
                        "uuid4",
                        return_value=SimpleNamespace(hex=token),
                    ),
                    mock.patch.object(
                        raptor_build, "_run", side_effect=failure
                    ) as launch,
                    mock.patch.object(
                        raptor_build.subprocess,
                        "run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout=f"{container_id} {owner}"
                        ),
                    ) as docker,
                ):
                    with self.assertRaises(type(failure)) as caught:
                        raptor_build.run_container(
                            ["docker", "run", "--rm", "--network=none", "fixture"],
                            label="fixture",
                            log_path=Path("unused.log"),
                        )
                    self.assertIs(caught.exception, failure)
                    self.assertIn(f"dcs6100-raptor-{token}", launch.call_args.args[0])
                    self.assertEqual(docker.call_count, 2 if owner == token else 1)
                    if owner == token:
                        self.assertEqual(
                            docker.call_args.args[0],
                            ["docker", "container", "rm", "--force", container_id],
                        )

    def test_raptor_caches_preserve_existing_workspace_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name in (
                "cache",
                "runs",
                "cache/sources",
                "cache/downloads",
                "cache/images",
            ):
                (root / name).mkdir(mode=0o700)
            manifest = {
                "schema_version": local_build.SCHEMA_VERSION,
                "project": local_build.PROJECT_ID,
                "build_root": str(root),
                "build_count": 1,
                "cache_policy": "public-inputs-only",
                "created_at": "fixture",
                "created_from_head": "a" * 40,
                "sources_lock_sha256": "b" * 64,
            }
            (root / local_build.WORKSPACE_NAME).write_text(json.dumps(manifest))
            source._private_child_directory(root / "cache", "sources", "raptor-sources")
            raptor_build._private_child_directory(
                root / "cache", "downloads", "raptor-artifacts"
            )
            self.assertEqual(local_build._validate_owned_tree(root), manifest)
            (root / "cache/raptor-artifacts").mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "unexpected entries"):
                local_build._validate_owned_tree(root)

    def test_offline_workspace_command_boundary_and_compile_failure(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            (root / "cache").mkdir(mode=0o700)
            (root / "runs").mkdir(mode=0o700)
            toolchain = root / "sdk.tar.gz"
            toolchain.write_bytes(b"fixture SDK")
            boot = {
                "build_root": str(root),
                "source_checkout": str(root),
                "builder_image": {"id": "sha256:" + "a" * 64},
            }
            stack.enter_context(
                mock.patch.object(
                    raptor_build, "bootstrap_public_build_inputs", return_value=boot
                )
            )
            stack.enter_context(
                mock.patch.object(
                    raptor_build,
                    "acquire_sources",
                    return_value=(root, {"sources": {}}),
                )
            )
            stack.enter_context(
                mock.patch.object(raptor_build, "project_git", return_value="e" * 40)
            )
            stack.enter_context(
                mock.patch.object(
                    raptor_build,
                    "_prepare_thingino_toolchain",
                    return_value=(toolchain, {}),
                )
            )
            calls = []

            def stop_at_compile(arguments, **kwargs):
                calls.append(arguments)
                if len(calls) == 2:
                    raise ValueError("compile boundary failure")

            stack.enter_context(
                mock.patch.object(
                    raptor_build, "run_container", side_effect=stop_at_compile
                )
            )
            with self.assertRaisesRegex(ValueError, "compile boundary failure"):
                raptor_build.build_raptor_component(build_root=root)
            self.assertEqual(len(calls), 2)
            for arguments in calls:
                self.assertIn("--network=none", arguments)
                self.assertNotIn("--device", arguments)
            compile_command = calls[1]
            self.assertIn("--privileged", compile_command)
            self.assertIn("mount -o loop /workspace.ext4 /work", compile_command[-1])
            self.assertIn("trap 'umount /work' EXIT", compile_command[-1])
            self.assertIn("trap 'exit 130' HUP INT TERM", compile_command[-1])
            for destination in ("/inputs", "/toolchain.tar.gz", "/build.sh"):
                mounts = [
                    arg for arg in compile_command if f"dst={destination}," in arg
                ]
                self.assertEqual(len(mounts), 1)
                self.assertTrue(mounts[0].endswith(",readonly"))
            self.assertFalse(
                list((root / "cache/downloads/raptor-artifacts").glob("*/build.json"))
            )
            runs = list((root / "runs").iterdir())
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "OWNER.md").is_file())
            self.assertTrue((runs[0] / "FAILED").is_file())
            self.assertIn(
                "runuser -u builder",
                (ROOT / "scripts/container_build_raptor.sh").read_text(),
            )
            script = (ROOT / "scripts/container_build_raptor.sh").read_text()
            # Configure the actual TLS source before compilation and installation,
            # so RWD sees the same SRTP API that the static library implements.
            configure = script.index("mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP")
            self.assertLess(configure, script.index("cmake -S /work/src/mbedtls"))
            self.assertLess(
                script.index("cmake --install /work/mbedtls-build"),
                script.index("make -C /work/src/raptor"),
            )

    def test_failed_publication_and_concurrent_publisher_preserve_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            candidate = cache / "candidate.tar.gz"
            candidate.write_bytes(b"fixture")
            destination = cache / "generation"
            real_write = raptor_build.atomic_write

            def fail_receipt(path, *args, **kwargs):
                if path.name == "build.json":
                    raise OSError("interrupted receipt write")
                return real_write(path, *args, **kwargs)

            with mock.patch.object(
                raptor_build, "atomic_write", side_effect=fail_receipt
            ):
                with self.assertRaisesRegex(OSError, "receipt write"):
                    raptor_build.publish_artifact(
                        cache=cache,
                        destination=destination,
                        candidate=candidate,
                        receipt={},
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(cache.iterdir()), [candidate])
            first = raptor_build.publish_artifact(
                cache=cache,
                destination=destination,
                candidate=candidate,
                receipt={"valid": True},
            )
            before = first.read_bytes()
            candidate.write_bytes(b"second writer")
            with self.assertRaisesRegex(ValueError, "another build"):
                raptor_build.publish_artifact(
                    cache=cache,
                    destination=destination,
                    candidate=candidate,
                    receipt={},
                )
            self.assertEqual(first.read_bytes(), before)
            self.assertFalse(list(cache.glob(".publish-*")))

    def test_cache_revalidation_and_generation_identity(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            (root / "runs").mkdir(mode=0o700)
            recipe = source.recipe_identity(ROOT)
            metadata = json.loads((ROOT / "sources.lock.json").read_bytes())["sources"][
                "thingino_build_toolchain_aarch64"
            ]
            boot = {
                "build_root": str(root),
                "source_checkout": str(root),
                "builder_image": {"id": "sha256:" + "a" * 64},
            }
            identity = {
                "recipe": recipe,
                "builder_image_id": boot["builder_image"]["id"],
                "toolchain_sha256": metadata["sha256"],
            }
            key = hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest()
            parent = raptor_build._private_child_directory(
                cache, "downloads", "raptor-artifacts"
            )
            destination = parent / key
            destination.mkdir(mode=0o700)
            artifact = destination / "raptor-rwd-component.tar.gz"
            sha = self.make_archive(artifact)
            receipt = {
                "identity": identity,
                "sha256": sha,
                "size": artifact.stat().st_size,
            }
            receipt_path = destination / "build.json"
            receipt_path.write_text(json.dumps(receipt))
            stack.enter_context(
                mock.patch.object(
                    raptor_build, "bootstrap_public_build_inputs", return_value=boot
                )
            )
            stack.enter_context(
                mock.patch.object(
                    raptor_build,
                    "acquire_sources",
                    return_value=(root, {"sources": {}}),
                )
            )
            compile_call = stack.enter_context(mock.patch.object(raptor_build, "_run"))
            stack.enter_context(
                mock.patch.object(raptor_build, "project_git", return_value="e" * 40)
            )
            toolchain = stack.enter_context(
                mock.patch.object(
                    raptor_build,
                    "_prepare_thingino_toolchain",
                    side_effect=ValueError("new generation"),
                )
            )
            result = raptor_build.build_raptor_component(build_root=root)
            self.assertTrue(result["cached"])
            self.assertEqual(result["raptor_rwd_artifact"], str(artifact))
            compile_call.assert_not_called()
            toolchain.assert_not_called()
            for field, value in (("sha256", "0" * 64), ("size", 1), ("identity", {})):
                receipt_path.write_text(json.dumps({**receipt, field: value}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    raptor_build.build_raptor_component(build_root=root)
            receipt_path.write_text(json.dumps(receipt))
            original = artifact.read_bytes()
            artifact.write_bytes(original + b"changed")
            with self.assertRaises(ValueError):
                raptor_build.build_raptor_component(build_root=root)
            artifact.write_bytes(original)
            for change in ("image", "recipe", "toolchain"):
                with self.subTest(change=change), ExitStack() as variant:
                    if change == "image":
                        variant.enter_context(
                            mock.patch.object(
                                raptor_build,
                                "bootstrap_public_build_inputs",
                                return_value={
                                    **boot,
                                    "builder_image": {"id": "sha256:" + "b" * 64},
                                },
                            )
                        )
                    elif change == "recipe":
                        variant.enter_context(
                            mock.patch.object(
                                raptor_build,
                                "recipe_identity",
                                return_value={**recipe, "changed": "digest"},
                            )
                        )
                    else:
                        lock = json.loads((ROOT / "sources.lock.json").read_bytes())
                        lock["sources"]["thingino_build_toolchain_aarch64"][
                            "sha256"
                        ] = ("c" * 64)
                        variant.enter_context(
                            mock.patch.object(
                                raptor_build,
                                "load_source_lock_snapshot",
                                return_value=(lock, "d" * 64),
                            )
                        )
                    with self.assertRaisesRegex(ValueError, "new generation"):
                        raptor_build.build_raptor_component(build_root=root)
            self.assertEqual(list(destination.parent.iterdir()), [destination])
            self.assertEqual(len(list((root / "runs").glob("*/FAILED"))), 3)
            compile_call.assert_not_called()


class SelectionTests(unittest.TestCase):
    def test_project_completion_generation_change_and_failure(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            root = Path(tmp).resolve()
            path = root / "project.json"
            project = projects.init_project(
                path,
                name="fixture",
                selections=projects.default_selections(path, root / "build"),
            )
            command = "local-build build-raptor"
            for number in (1, 2):
                artifact = root / f"component-{number}.tar.gz"
                artifact.write_bytes(f"generation {number}".encode())
                project = projects.load_project(path)
                active = projects.begin_operation(
                    project, command, project.selections[command]
                )
                projects.finish_operation(
                    active,
                    {"ok": True, "result": {"raptor_rwd_artifact": str(artifact)}},
                )
                project = projects.load_project(path)
                self.assertEqual(
                    projects.project_status(project)["records"][command]["state"],
                    "completed",
                )
                self.assertEqual(
                    project.selections["local-build build-universal"][
                        "raptor-rwd-artifact"
                    ],
                    str(artifact),
                )
            before = project.selections["local-build build-universal"].copy()
            with (
                mock.patch.object(
                    raptor_build,
                    "build_raptor_component",
                    side_effect=local_build_run.LocalBuildRunError("missing source"),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                code = user_cli.main(
                    [
                        "--project",
                        str(path),
                        "local-build",
                        "build-raptor",
                        "--non-interactive",
                        "--json",
                    ]
                )
            self.assertNotEqual(code, 0)
            project = projects.load_project(path)
            self.assertEqual(project.selections["local-build build-universal"], before)
            self.assertEqual(project.records[command]["state"], "started")
            self.assertNotIn("artifacts", project.records[command])

    def test_universal_source_repeat_records_current_generated_artifact(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            root = Path(tmp).resolve()
            path = root / "project.json"
            project = projects.init_project(
                path,
                name="fixture",
                selections=projects.default_selections(path, root / "build"),
            )
            vendor = root / "vendor"
            vendor.mkdir()
            projects.link_inputs(project, {"vendor-bundle-dir": str(vendor)})
            projects.save_project(project)
            for number in (1, 2):
                artifact = root / f"component-{number}.tar.gz"
                artifact.write_bytes(f"generation {number}".encode())
                output = root / f"set-{number}"
                output.mkdir()
                (output / "thingino-universal.tgb").write_bytes(b"fixture bundle")
                raw = [
                    "--project",
                    str(path),
                    "local-build",
                    "build-universal",
                    "--webrtc",
                ]
                args = user_cli.build_parser().parse_args(user_cli_project.expand(raw))
                active = user_cli_project.begin(args)
                self.assertNotIn("raptor-rwd-artifact", active[2])
                projects.finish_operation(
                    active,
                    {
                        "ok": True,
                        "result": {
                            "raptor_rwd_artifact": str(artifact),
                            "install_set_dir": str(output),
                            "raptor_rwd_source_build": True,
                        },
                    },
                )
                project = projects.load_project(path)
                self.assertEqual(
                    projects.project_status(project)["records"][
                        "local-build build-universal"
                    ]["state"],
                    "completed",
                )
                self.assertEqual(
                    project.selections["universal stage"]["install-set-dir"],
                    str(output),
                )

    def test_failed_component_stops_universal_build(self):
        with (
            mock.patch.object(
                raptor_build,
                "build_raptor_component",
                side_effect=ValueError("missing source"),
            ),
            mock.patch.object(local_build_run, "_build_local_install_set") as firmware,
        ):
            with self.assertRaisesRegex(ValueError, "missing source"):
                local_build_run.build_local_universal_install_set(
                    build_root=Path("build"),
                    vendor_bundle_dir=Path("vendor"),
                    signing_key=Path("signer"),
                    webrtc=True,
                )
            firmware.assert_not_called()

    def test_source_selection_links_exact_artifact(self):
        with (
            mock.patch.object(
                raptor_build,
                "build_raptor_component",
                return_value={"raptor_rwd_artifact": "/component.tar.gz"},
            ),
            mock.patch.object(
                local_build_run, "_build_local_install_set", return_value={"ok": True}
            ) as firmware,
        ):
            result = local_build_run.build_local_universal_install_set(
                build_root=Path("build"),
                vendor_bundle_dir=Path("vendor"),
                signing_key=Path("signer"),
                webrtc=True,
            )
            self.assertEqual(
                firmware.call_args.kwargs["raptor_rwd_artifact"],
                Path("/component.tar.gz"),
            )
            self.assertEqual(result["raptor_rwd_artifact"], "/component.tar.gz")

    def test_explicit_webrtc_overrides_remembered_archive_only(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            root = Path(tmp).resolve()
            path = root / "project.json"
            project = projects.init_project(
                path,
                name="fixture",
                selections=projects.default_selections(path, root / "build"),
            )
            artifact = root / "component.tar.gz"
            artifact.write_bytes(b"fixture")
            projects.link_inputs(
                project,
                {
                    "raptor-rwd-artifact": str(artifact),
                    "vendor-bundle-dir": str(root / "vendor"),
                },
            )
            projects.save_project(project)
            raw = ["--project", str(path), "local-build", "build-universal", "--webrtc"]
            for _ in range(2):
                expanded = user_cli_project.expand(raw)
                self.assertNotIn("--raptor-rwd-artifact", expanded)
                args = user_cli.build_parser().parse_args(expanded)
                self.assertTrue(args.webrtc)
            explicit = [*raw, "--raptor-rwd-artifact", str(artifact)]
            with self.assertRaises(user_cli.ArgumentParsingError):
                user_cli.build_parser().parse_args(user_cli_project.expand(explicit))
