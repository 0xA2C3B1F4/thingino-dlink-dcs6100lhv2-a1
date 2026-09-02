from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import runtime_candidate


def mips_elf(fill: int) -> bytes:
    raw = bytearray([fill] * 96)
    raw[:6] = b"\x7fELF\x01\x01"
    raw[16:18] = (3).to_bytes(2, "little")
    raw[18:20] = (8).to_bytes(2, "little")
    raw[36:40] = (0x70001007).to_bytes(4, "little")
    raw[64:77] = b"/lib/ld.so.1\0"
    return bytes(raw)


class RuntimeCandidateTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        rootfs = root / "candidate.squashfs"
        rootfs.write_bytes(b"hsqs" + b"x" * 92)
        provenance = root / "final-root.private.json"
        provenance.write_text(
            json.dumps(
                {
                    "policies": {
                        "automatic_default_route": False,
                        "generic_updater_removed": True,
                        "media_runtime": "source-built-prudynt-global-glibc-c1-closure",
                        "media_start": "automatic-S31prudynt",
                        "native_media": "hash-locked-c1-tx-isp-sensor-iq",
                        "polluted_module_paths_removed": True,
                        "sensor_unknown_fallback": "json-os02g10",
                    },
                    "schema_version": 1,
                    "status": "private final-root input; not an install authorization",
                    "system": {
                        "filename": "system.private.squashfs",
                        "sha256": hashlib.sha256(rootfs.read_bytes()).hexdigest(),
                        "size": rootfs.stat().st_size,
                    },
                }
            ),
            encoding="utf-8",
        )
        return rootfs, provenance

    def test_package_contains_only_fixed_runtime_members(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            rootfs, provenance = self._inputs(Path(name))
            prudynt = mips_elf(1)
            control = mips_elf(2)
            uhttpd = mips_elf(3)

            def extract(*, relative: str, **_: object) -> bytes:
                return {
                    "usr/bin/prudynt": prudynt,
                    "usr/sbin/thingino-controld": control,
                    "usr/bin/uhttpd": uhttpd,
                }[relative]

            with (
                mock.patch.object(runtime_candidate, "_unsquashfs", return_value=Path("/tool")),
                mock.patch.object(runtime_candidate, "_extract_member", side_effect=extract),
            ):
                archive, metadata = runtime_candidate.build_runtime_candidate_package(
                    rootfs_path=rootfs,
                    provenance_path=provenance,
                )
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as package:
                self.assertEqual(
                    package.getnames(),
                    [
                        "activate.sh",
                        "usr/bin/prudynt",
                        "usr/sbin/thingino-controld",
                        "usr/bin/uhttpd",
                        "candidate.sha256",
                    ],
                )
                manifest = package.extractfile("candidate.sha256")
                assert manifest is not None
                text = manifest.read().decode("ascii")
            self.assertIn(hashlib.sha256(prudynt).hexdigest(), text)
            self.assertIn(hashlib.sha256(control).hexdigest(), text)
            self.assertIn(hashlib.sha256(uhttpd).hexdigest(), text)
            self.assertEqual(metadata["nor_writes"], False)

    def test_changed_rootfs_is_rejected_by_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            rootfs, provenance = self._inputs(Path(name))
            rootfs.write_bytes(rootfs.read_bytes() + b"changed")
            with self.assertRaisesRegex(
                runtime_candidate.RuntimeCandidateError,
                "does not bind",
            ):
                runtime_candidate.build_runtime_candidate_package(
                    rootfs_path=rootfs,
                    provenance_path=provenance,
                    unsquashfs=Path("/bin/sh"),
                )

    def test_stage_selects_preflight_host_and_requires_exact_candidate_hashes(self) -> None:
        metadata = {
            "prudynt_sha256": "1" * 64,
            "control_sha256": "2" * 64,
            "uhttpd_sha256": "4" * 64,
            "nor_writes": False,
        }
        active = (
            "schema=1\nstate=active\n"
            f"prudynt_sha256={metadata['prudynt_sha256']}\n"
            f"control_sha256={metadata['control_sha256']}\n"
            f"uhttpd_sha256={metadata['uhttpd_sha256']}\n"
        ).encode("ascii")
        expected_mtd3 = "3" * 64
        with (
            mock.patch.object(
                runtime_candidate,
                "build_runtime_candidate_package",
                return_value=(b"archive", metadata),
            ),
            mock.patch.object(
                runtime_candidate,
                "load_host_session",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                runtime_candidate,
                "resolve_recovery_ap_station_candidates",
                return_value=(
                    "camera.local",
                    ("192.0.2.20", "192.0.2.100"),
                ),
            ),
            mock.patch.object(
                runtime_candidate,
                "probe_recovery_ap",
                return_value={"mtd3_sha256": expected_mtd3},
            ),
            mock.patch.object(
                runtime_candidate,
                "_exchange",
                side_effect=[b"schema=1\nstate=absent\n", b"received\n", active],
            ) as exchange,
        ):
            result = runtime_candidate.stage_runtime_candidate(
                session_dir=Path("session"),
                host="192.0.2.20",
                rootfs_path=Path("rootfs"),
                provenance_path=Path("provenance"),
                expected_mtd3_sha256=expected_mtd3,
            )
        self.assertEqual(result["state"], "active")
        self.assertTrue(result["rollback_required"])
        self.assertEqual(exchange.call_count, 3)

    def test_response_rejects_duplicate_or_malformed_fields(self) -> None:
        with self.assertRaisesRegex(
            runtime_candidate.RuntimeCandidateError,
            "duplicate",
        ):
            runtime_candidate._parse_state(
                b"schema=1\nstate=active\nstate=active\n",
                {"active"},
            )

    def test_inactive_response_accepts_legacy_two_binary_digests(self) -> None:
        response = runtime_candidate._parse_state(
            (
                "schema=1\nstate=rolled-back\n"
                f"prudynt_sha256={'1' * 64}\n"
                f"control_sha256={'2' * 64}\n"
            ).encode("ascii"),
            {"rolled-back"},
        )
        self.assertEqual(response["state"], "rolled-back")

    def test_active_response_rejects_legacy_two_binary_digests(self) -> None:
        with self.assertRaisesRegex(
            runtime_candidate.RuntimeCandidateError,
            "field set",
        ):
            runtime_candidate._parse_state(
                (
                    "schema=1\nstate=active\n"
                    f"prudynt_sha256={'1' * 64}\n"
                    f"control_sha256={'2' * 64}\n"
                ).encode("ascii"),
                {"active"},
            )

    def test_stage_refuses_to_replace_an_active_runtime_candidate(self) -> None:
        metadata = {
            "prudynt_sha256": "1" * 64,
            "control_sha256": "2" * 64,
            "uhttpd_sha256": "4" * 64,
            "nor_writes": False,
        }
        active = (
            "schema=1\nstate=active\n"
            f"prudynt_sha256={metadata['prudynt_sha256']}\n"
            f"control_sha256={metadata['control_sha256']}\n"
            f"uhttpd_sha256={metadata['uhttpd_sha256']}\n"
        ).encode("ascii")
        expected_mtd3 = "3" * 64
        with (
            mock.patch.object(
                runtime_candidate,
                "build_runtime_candidate_package",
                return_value=(b"archive", metadata),
            ),
            mock.patch.object(
                runtime_candidate,
                "load_host_session",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                runtime_candidate,
                "resolve_recovery_ap_station_candidates",
                return_value=("camera.local", ("192.0.2.20",)),
            ),
            mock.patch.object(
                runtime_candidate,
                "probe_recovery_ap",
                return_value={"mtd3_sha256": expected_mtd3},
            ),
            mock.patch.object(
                runtime_candidate,
                "_exchange",
                return_value=active,
            ) as exchange,
        ):
            with self.assertRaisesRegex(
                runtime_candidate.RuntimeCandidateError,
                "state is invalid",
            ):
                runtime_candidate.stage_runtime_candidate(
                    session_dir=Path("session"),
                    host="192.0.2.20",
                    rootfs_path=Path("rootfs"),
                    provenance_path=Path("provenance"),
                    expected_mtd3_sha256=expected_mtd3,
                )
        exchange.assert_called_once()

    def test_stage_rechecks_station_host_before_transfer(self) -> None:
        metadata = {
            "prudynt_sha256": "1" * 64,
            "control_sha256": "2" * 64,
            "nor_writes": False,
        }
        with (
            mock.patch.object(
                runtime_candidate,
                "build_runtime_candidate_package",
                return_value=(b"archive", metadata),
            ),
            mock.patch.object(
                runtime_candidate,
                "load_host_session",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                runtime_candidate,
                "resolve_recovery_ap_station_candidates",
                return_value=("camera.local", ("192.0.2.21",)),
            ),
            mock.patch.object(runtime_candidate, "probe_recovery_ap") as probe,
            mock.patch.object(runtime_candidate, "_exchange") as exchange,
        ):
            with self.assertRaisesRegex(
                runtime_candidate.RuntimeCandidateError,
                "current session station",
            ):
                runtime_candidate.stage_runtime_candidate(
                    session_dir=Path("session"),
                    host="192.0.2.20",
                    rootfs_path=Path("rootfs"),
                    provenance_path=Path("provenance"),
                    expected_mtd3_sha256="3" * 64,
                )
        probe.assert_not_called()
        exchange.assert_not_called()

    def test_rollback_requires_stable_verified_target_state(self) -> None:
        rolled_back = b"schema=1\nstate=rolled-back\n"
        with (
            mock.patch.object(
                runtime_candidate,
                "load_host_session",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                runtime_candidate,
                "_exchange",
                side_effect=[rolled_back, b"schema=1\nstate=absent\n"],
            ) as exchange,
        ):
            with self.assertRaisesRegex(
                runtime_candidate.RuntimeCandidateError,
                "did not remain stable",
            ):
                runtime_candidate.rollback_runtime_candidate(
                    session_dir=Path("session"),
                    host="192.0.2.20",
                )
        self.assertEqual(exchange.call_count, 2)

    def test_target_activator_has_valid_shell_syntax(self) -> None:
        completed = subprocess.run(
            ["sh", "-n", str(runtime_candidate.TEMPLATE_PATH)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_target_activator_owns_candidate_storage_worker_lifecycle(self) -> None:
        source = runtime_candidate.TEMPLATE_PATH.read_text(encoding="utf-8")
        for contract in (
            "WORKER_PIDFILE=/run/thingino-storage-worker.pid",
            "WORKER_SOCKET=/run/thingino-control/storage-v1.sock",
            "--storage-worker",
            "start_candidate_worker",
            "stop_candidate_worker",
            "candidate_services_running",
            "start_candidate_services",
        ):
            self.assertIn(contract, source)
        self.assertIn(
            "mount -o bind \"$DIR/usr/bin/uhttpd\" \"$UHTTPD\"\n\tstart_candidate_services",
            source,
        )

    def test_target_rollback_never_marks_success_after_unmount_failure(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "baseline.sha256").write_text("baseline\n", encoding="ascii")
            program = r'''
script_path=$1
selftest_dir=$2
set -- status
. "$script_path" >/dev/null
DIR=$selftest_dir
STATE=$DIR/state
CONTROL=/usr/sbin/thingino-controld
PRUDYNT=/usr/bin/prudynt
stop_services() { return 0; }
mounted_on() { return 0; }
umount() { return 1; }
sha256sum() { return 0; }
start_services() { return 0; }
set +e
rollback_core
rollback_status=$?
set -e
[ "$rollback_status" -ne 0 ]
[ ! -e "$STATE" ]
'''
            completed = subprocess.run(
                ["sh", "-c", program, "sh", str(runtime_candidate.TEMPLATE_PATH), name],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())


if __name__ == "__main__":
    unittest.main()
