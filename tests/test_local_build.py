from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import local_build


ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40


class LocalBuildWorkspaceTests(unittest.TestCase):
    def test_configure_rejects_mismatched_wifi_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            private = root / "private"
            private.mkdir(mode=0o700)
            validated = {
                "build_root": root / "build",
                "credential": b"a" * 64 + b"\n",
                "media_closure_dir": root / "media",
                "private_root": private,
                "session_dir": root / "session",
                "vendor_bundle_dir": root / "vendor",
            }
            with mock.patch.object(
                local_build,
                "validate_local_build_setting_inputs",
                return_value=validated,
            ):
                with self.assertRaisesRegex(local_build.LocalBuildError, "does not match"):
                    local_build.configure_local_build_settings(
                        build_root=root / "build",
                        work_dir=root / "state",
                        private_root=private,
                        vendor_bundle_dir=root / "vendor",
                        media_closure_dir=root / "media",
                        session_dir=root / "session",
                        data_mode="initialize",
                        ssid="one",
                        passphrase="password-one",
                        confirmation_ssid="two",
                        confirmation_passphrase="password-two",
                    )
            self.assertEqual(list(private.iterdir()), [])

    def test_configure_writes_secret_free_private_settings_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            private = root / "private"
            private.mkdir(mode=0o700)
            session = root / "session"
            (session / "host").mkdir(parents=True)
            (session / "host/identity.pub").write_bytes(b"test-public-key\n")
            validated = {
                "build_root": root / "build",
                "credential": b"a" * 64 + b"\n",
                "media_closure_dir": root / "media",
                "private_root": private,
                "session_dir": session,
                "vendor_bundle_dir": root / "vendor",
            }

            def generate(*, output_dir: Path, **_: object) -> None:
                output_dir.mkdir(mode=0o700)

            with (
                mock.patch.object(
                    local_build,
                    "validate_local_build_setting_inputs",
                    return_value=validated,
                ),
                mock.patch(
                    "installer.private_config.generate_private_config",
                    side_effect=generate,
                ),
                mock.patch("installer.private_config.load_private_config_for_session"),
                mock.patch("installer.private_config.load_private_wpa_config"),
            ):
                result = local_build.configure_local_build_settings(
                    build_root=root / "build",
                    work_dir=root / "state",
                    private_root=private,
                    vendor_bundle_dir=root / "vendor",
                    media_closure_dir=root / "media",
                    session_dir=session,
                    data_mode="preserve",
                    ssid="private-test-ssid",
                    passphrase="private-test-passphrase",
                    confirmation_ssid="private-test-ssid",
                    confirmation_passphrase="private-test-passphrase",
                )
            settings = Path(str(result["settings_path"]))
            rendered = settings.read_text(encoding="utf-8")
            self.assertNotIn("private-test-ssid", rendered)
            self.assertNotIn("private-test-passphrase", rendered)
            self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
            loaded = local_build.load_local_build_settings(
                settings_path=None,
                work_dir=root / "state",
            )
            self.assertEqual(loaded["data_mode"], "preserve")
            self.assertEqual(loaded["settings_path"], str(settings))

    def _usage(self, free_gib: int = 512) -> SimpleNamespace:
        return SimpleNamespace(
            total=1024 * local_build.BYTES_PER_GIB,
            used=(1024 - free_gib) * local_build.BYTES_PER_GIB,
            free=free_gib * local_build.BYTES_PER_GIB,
        )

    def _git(self, _root: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return HEAD
        if arguments == ("status", "--short", "--untracked-files=all"):
            return ""
        raise AssertionError(arguments)

    def _patches(self, *, free_gib: int = 512, docker_ready: bool = True):
        return (
            mock.patch.object(local_build, "_project_root", return_value=ROOT),
            mock.patch.object(local_build, "_git", side_effect=self._git),
            mock.patch.object(
                local_build.shutil,
                "disk_usage",
                return_value=self._usage(free_gib),
            ),
            mock.patch.object(
                local_build,
                "_docker_status",
                return_value={
                    "executable": "/usr/local/bin/docker" if docker_ready else None,
                    "ready": docker_ready,
                    "reason": None if docker_ready else "docker-command-missing",
                    "server_version": "27.1.1" if docker_ready else None,
                },
            ),
            mock.patch.object(local_build, "host_platform", return_value="macos"),
        )

    def test_prepare_creates_private_workspace_and_exact_rerun_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "new/location/build"
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                first = local_build.prepare_local_build_workspace(
                    build_root=build_root,
                    build_count=2,
                )
                before = {
                    path.relative_to(build_root): (
                        path.stat().st_mtime_ns,
                        path.read_bytes() if path.is_file() else None,
                    )
                    for path in build_root.rglob("*")
                }
                second = local_build.prepare_local_build_workspace(
                    build_root=build_root,
                    build_count=2,
                )
                after = {
                    path.relative_to(build_root): (
                        path.stat().st_mtime_ns,
                        path.read_bytes() if path.is_file() else None,
                    )
                    for path in build_root.rglob("*")
                }
            self.assertEqual(before, after)
            self.assertEqual(first["build_root"], str(build_root))
            self.assertEqual(first, second)
            self.assertTrue(first["ready_to_build"])
            self.assertEqual(first["required_free_gib"], 160)
            self.assertEqual(
                stat.S_IMODE(build_root.stat().st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(
                    (build_root / local_build.WORKSPACE_NAME).stat().st_mode
                ),
                0o600,
            )
            self.assertEqual(
                {
                    path.relative_to(build_root).as_posix()
                    for path in build_root.rglob("*")
                },
                {
                    "cache",
                    "cache/downloads",
                    "cache/images",
                    "cache/sources",
                    "local-build-workspace.json",
                    "runs",
                },
            )

    def test_status_does_not_modify_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "build"
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                local_build.prepare_local_build_workspace(build_root=build_root)
                before = {
                    path: path.stat().st_mtime_ns for path in build_root.rglob("*")
                }
                result = local_build.local_build_workspace_status(
                    build_root=build_root
                )
                after = {
                    path: path.stat().st_mtime_ns for path in build_root.rglob("*")
                }
            self.assertEqual(before, after)
            self.assertTrue(result["ready_to_build"])
            self.assertEqual(result["required_free_gib"], 80)

    def test_prepare_does_not_create_workspace_without_required_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "build"
            patches = self._patches(free_gib=159)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaisesRegex(
                    local_build.LocalBuildError,
                    "less than 160 GiB",
                ):
                    local_build.prepare_local_build_workspace(
                        build_root=build_root,
                        build_count=2,
                    )
            self.assertFalse(build_root.exists())

    def test_prepare_rejects_unowned_nonempty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "build"
            build_root.mkdir()
            os.chmod(build_root, 0o700)
            (build_root / "unrelated.txt").write_text("keep", encoding="utf-8")
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaisesRegex(
                    local_build.LocalBuildError,
                    "manifest is missing",
                ):
                    local_build.prepare_local_build_workspace(
                        build_root=build_root,
                        build_count=2,
                    )
            self.assertEqual(
                (build_root / "unrelated.txt").read_text(encoding="utf-8"),
                "keep",
            )

    def test_prepare_rejects_symlinked_path_component(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaisesRegex(
                    local_build.LocalBuildError,
                    "symbolic link",
                ):
                    local_build.prepare_local_build_workspace(
                        build_root=link / "build",
                        build_count=1,
                    )

    def test_missing_docker_is_a_host_action_not_a_workspace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "build"
            patches = self._patches(docker_ready=False)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                result = local_build.prepare_local_build_workspace(
                    build_root=build_root,
                    build_count=1,
                )
            self.assertFalse(result["ready_to_build"])
            self.assertEqual(result["safe_next_action"], "install-or-start-docker")
            self.assertTrue(build_root.is_dir())

    def test_changed_source_lock_requires_a_new_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            build_root = Path(name) / "build"
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                local_build.prepare_local_build_workspace(
                    build_root=build_root,
                    build_count=1,
                )
                manifest_path = build_root / local_build.WORKSPACE_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["sources_lock_sha256"] = "0" * 64
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.chmod(manifest_path, 0o600)
                result = local_build.local_build_workspace_status(
                    build_root=build_root
                )
            self.assertFalse(result["ready_to_build"])
            self.assertEqual(
                result["safe_next_action"],
                "create-a-new-workspace-for-this-source-lock",
            )

    def test_prepare_pointer_resolves_without_repeating_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            build_root = root / "build"
            work_dir = root / "state"
            patches = self._patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                local_build.prepare_local_build_workspace(
                    build_root=build_root,
                    build_count=2,
                )
                pointer = local_build.remember_local_build_workspace(
                    build_root=build_root,
                    work_dir=work_dir,
                )
                resolved = local_build.resolve_local_build_workspace(
                    build_root=None,
                    work_dir=work_dir,
                    environment={},
                )
            self.assertEqual(resolved, build_root)
            self.assertEqual(pointer, work_dir / local_build.WORKSPACE_POINTER_NAME)
            self.assertEqual(stat.S_IMODE(pointer.stat().st_mode), 0o600)

    def test_environment_workspace_is_non_secret_and_must_be_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work_dir = Path(name) / "state"
            expected = Path(name) / "build"
            resolved = local_build.resolve_local_build_workspace(
                build_root=None,
                work_dir=work_dir,
                environment={local_build.BUILD_ROOT_ENVIRONMENT: str(expected)},
            )
            self.assertEqual(resolved, expected)
            with self.assertRaisesRegex(local_build.LocalBuildError, "absolute"):
                local_build.resolve_local_build_workspace(
                    build_root=None,
                    work_dir=work_dir,
                    environment={local_build.BUILD_ROOT_ENVIRONMENT: "relative/build"},
                )

    def test_explicit_and_environment_workspaces_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaisesRegex(local_build.LocalBuildError, "different"):
                local_build.resolve_local_build_workspace(
                    build_root=root / "one",
                    work_dir=root / "state",
                    environment={
                        local_build.BUILD_ROOT_ENVIRONMENT: str(root / "two")
                    },
                )


if __name__ == "__main__":
    unittest.main()
