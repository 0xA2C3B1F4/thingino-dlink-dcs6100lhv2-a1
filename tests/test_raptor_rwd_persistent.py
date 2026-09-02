from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "components/raptor-rwd/build_persistent.py"
SPEC = importlib.util.spec_from_file_location("prudynt_rwd_persistent", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
persistent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(persistent)


class RaptorRwdPersistentTests(unittest.TestCase):
    @staticmethod
    def _base_owned_archive(root: Path) -> bytes:
        (root / "usr/lib").mkdir(parents=True, exist_ok=True)
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            base_owned = persistent.BASE_OWNED_RUNTIME
            for artifact_name in sorted(
                persistent.INSTALL_REGULAR | {"S95thingino-control"}
            ):
                relative = base_owned.get(artifact_name)
                payload = f"artifact:{artifact_name}".encode()
                if relative is not None:
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(f"current:{relative}".encode())
                info = tarfile.TarInfo(artifact_name)
                info.size = len(payload)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(payload))
        return archive_bytes.getvalue()

    def test_persistent_overlay_preserves_base_runtime_from_stale_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            artifact = self._base_owned_archive(root)
            identities = persistent._base_owned_runtime_identities(root)
            self.assertEqual(set(identities), set(persistent.BASE_OWNED_RUNTIME.values()))
            current_control = root / "usr/sbin/thingino-controld"
            current_control.write_bytes(b"current-control")
            persistent._install_artifact(root, artifact, static_rwd_tls=True)
            self.assertEqual(current_control.read_bytes(), b"current-control")
            self.assertNotIn(
                "usr/sbin/thingino-controld",
                persistent._install_artifact(
                    root, artifact, static_rwd_tls=True
                ),
            )

    def test_split_packer_uses_fragment_tailends_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "root"
            root.mkdir()
            output = Path(name) / "output.squashfs"

            def capture(arguments: list[str], _label: str) -> bytes:
                self.assertIn("-tailends", arguments)
                self.assertNotIn("-no-tailends", arguments)
                raise RuntimeError("captured")

            with mock.patch.object(persistent, "_run", side_effect=capture):
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    persistent._pack(
                        tool=Path("mksquashfs"),
                        root=root,
                        output=output,
                        pack_tailends=True,
                    )

    def test_prudynt_init_always_enables_ring_after_restart(self) -> None:
        source = b"before\n\t" + persistent.PRUDYNT_START + b"after\n"
        patched = persistent.patch_prudynt_init(source)
        lines = [line.lstrip(b"\t") for line in patched.splitlines(keepends=True)]
        self.assertEqual(lines.count(persistent.PRUDYNT_START), 0)
        self.assertEqual(lines.count(persistent.PRUDYNT_RING_START), 1)
        with self.assertRaisesRegex(persistent.PersistentCandidateError, "baseline"):
            persistent.patch_prudynt_init(patched)

    def test_service_waits_for_owner_and_stops_before_ring_owner(self) -> None:
        service = (ROOT / "components/raptor-rwd/S96rwd").read_text()
        self.assertIn("1080p-started", service)
        self.assertIn("/dev/shm/rss_ring_main", service)
        self.assertIn("signaling", (ROOT / "components/raptor-rwd/raptor.conf").read_text())
        self.assertNotIn("S31prudynt stop", service)
        self.assertNotIn('rm -f "$RING"', service)
        self.assertIn('rm -f "$PIDFILE" "$RUNTIME_CONFIG"', service)
        self.assertIn("LD_LIBRARY_PATH=/usr/lib/raptor:/usr/lib", service)
        self.assertGreater("S96rwd", "S31prudynt")

    def test_rwd_mbedtls_closure_is_namespaced_away_from_uhttpd(self) -> None:
        self.assertEqual(
            persistent.artifact_destination("usr/lib/libmbedtls.so.21"),
            "usr/lib/raptor/libmbedtls.so.21",
        )
        self.assertEqual(
            persistent.artifact_destination("usr/lib/libmbedcrypto.so.3.6.6"),
            "usr/lib/raptor/libmbedcrypto.so.3.6.6",
        )
        self.assertEqual(
            persistent.artifact_destination("usr/lib/librss_ipc.so"),
            "usr/lib/librss_ipc.so",
        )

    def test_artifact_install_preserves_global_uhttpd_tls_library(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for member in persistent.INSTALL_REGULAR | {"S95thingino-control"}:
                relative = "etc/init.d/S95thingino-control" if member == "S95thingino-control" else persistent.artifact_destination(member)
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
            for member in persistent.INSTALL_SYMLINKS:
                (root / persistent.artifact_destination(member)).parent.mkdir(parents=True, exist_ok=True)
            global_tls = root / "usr/lib/libmbedtls.so.3.6.6"
            global_tls.write_bytes(b"accepted-uhttpd-abi")

            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
                for member in sorted(persistent.INSTALL_REGULAR | {"S95thingino-control"}):
                    payload = b"raptor-tls" if member == "usr/lib/libmbedtls.so.3.6.6" else member.encode()
                    info = tarfile.TarInfo(member)
                    info.size = len(payload)
                    info.mode = 0o755
                    archive.addfile(info, io.BytesIO(payload))
                for member in sorted(persistent.INSTALL_SYMLINKS):
                    info = tarfile.TarInfo(member)
                    info.type = tarfile.SYMTYPE
                    info.linkname = member.rsplit("/", 1)[-1].replace(".so.21", ".so.3.6.6").replace(".so.16", ".so.3.6.6").replace(".so.7", ".so.3.6.6")
                    archive.addfile(info)

            persistent._install_artifact(root, archive_bytes.getvalue())
            self.assertEqual(global_tls.read_bytes(), b"accepted-uhttpd-abi")
            self.assertEqual((root / "usr/lib/raptor/libmbedtls.so.3.6.6").read_bytes(), b"raptor-tls")

    def test_static_rwd_install_omits_private_shared_tls_closure(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "usr/lib").mkdir(parents=True)
            for member in (
                persistent.INSTALL_REGULAR - persistent.RAPTOR_TLS_REGULAR
            ) | {"S95thingino-control"}:
                relative = (
                    "etc/init.d/S95thingino-control"
                    if member == "S95thingino-control"
                    else persistent.artifact_destination(member)
                )
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
                for member in sorted(persistent.INSTALL_REGULAR | {"S95thingino-control"}):
                    payload = member.encode()
                    info = tarfile.TarInfo(member)
                    info.size = len(payload)
                    info.mode = 0o755
                    archive.addfile(info, io.BytesIO(payload))
                for member in sorted(persistent.INSTALL_SYMLINKS):
                    info = tarfile.TarInfo(member)
                    info.type = tarfile.SYMTYPE
                    info.linkname = member.rsplit("/", 1)[-1]
                    archive.addfile(info)

            installed = persistent._install_artifact(
                root,
                archive_bytes.getvalue(),
                static_rwd_tls=True,
            )
            self.assertFalse((root / "usr/lib/raptor/libmbedtls.so.3.6.6").exists())
            self.assertFalse((root / "usr/lib/raptor").exists())
            self.assertTrue((root / "usr/bin/rwd").is_file())
            self.assertFalse(any(path.startswith("usr/lib/raptor/") for path in installed))

    def test_reset_conflicts_are_removed_without_following_directories(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for relative in persistent.RESET_CONFLICTS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old")
            persistent._remove_reset_conflicts(root)
            self.assertTrue(all(not (root / path).exists() for path in persistent.RESET_CONFLICTS))

            changed = root / persistent.RESET_CONFLICTS[0]
            changed.mkdir(parents=True)
            with self.assertRaisesRegex(persistent.PersistentCandidateError, "changed type"):
                persistent._remove_reset_conflicts(root)

    def test_embedded_static_webrtc_provenance_binds_both_streams(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path = root / "etc/dlink-media-closure.private.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"prudynt":{},"source_built_init":['
                '{"destination":"/etc/init.d/S31prudynt"}]}'
            )
            persistent._update_embedded_media_provenance(
                root,
                b"prudynt",
                b"init",
                static_rwd_tls=True,
                source_provenance={"lock_sha256": "a" * 64},
            )
            document = json.loads(path.read_text())
            self.assertEqual(document["raptor_rwd"]["streams"], [0, 1])
            self.assertEqual(document["raptor_rwd"]["tls"], "static-rwd")
            expected = persistent.hashlib.sha256(
                b'{"lock_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
            ).hexdigest()
            self.assertEqual(
                document["raptor_rwd"]["source_provenance_sha256"], expected
            )

    def test_component_provenance_binds_config_lock_and_raw_patches(self) -> None:
        lock = (ROOT / "components/raptor-rwd/raptor-lock.json").read_bytes()
        config = (ROOT / "components/raptor-rwd/raptor.conf").read_bytes()
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for name, payload in (("raptor-lock.json", lock), ("etc/raptor.conf", config)):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

        provenance = persistent._validate_component_provenance(archive_bytes.getvalue())
        self.assertEqual(
            set(provenance["sources"]),
            {"compy", "mbedtls", "raptor", "raptor-common", "raptor-ipc"},
        )
        self.assertEqual(provenance["excluded_sources"], ["raptor-hal"])
        self.assertEqual(
            [entry["path"] for entry in provenance["patches"]],
            [f"patches/raptor/{name}" for name in persistent.RAPTOR_PATCHES],
        )

        changed = io.BytesIO()
        with tarfile.open(fileobj=changed, mode="w") as archive:
            for name, payload in (("raptor-lock.json", lock), ("etc/raptor.conf", config + b"\n")):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(persistent.PersistentCandidateError, "configuration"):
            persistent._validate_component_provenance(changed.getvalue())


if __name__ == "__main__":
    unittest.main()
