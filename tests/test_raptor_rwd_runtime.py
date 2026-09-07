from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import raptor_rwd_runtime


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "components/raptor-rwd/S13prudynt-rwd"


def artifact_bytes(*, extra: bool = False) -> bytes:
    files = {
        name: (SUPERVISOR.read_bytes() if name == "S13prudynt-rwd" else name.encode())
        for name in raptor_rwd_runtime.REGULAR_MEMBERS
        if name != "SHA256SUMS"
    }
    files["SHA256SUMS"] = (
        "\n".join(
            f"{hashlib.sha256(payload).hexdigest()}  {name}"
            for name, payload in sorted(files.items())
        )
        + "\n"
    ).encode("ascii")
    if extra:
        files["unexpected"] = b"no"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, payload in sorted(files.items()):
            member = tarfile.TarInfo(f"raptor-rwd/{name}")
            member.mode = (
                0o755
                if name
                in {
                    "S13prudynt-rwd",
                    "S95thingino-control",
                    "usr/bin/prudynt",
                    "usr/bin/rwd",
                    "usr/bin/uhttpd",
                    "usr/sbin/thingino-controld",
                }
                else 0o644
            )
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        for name, target in sorted(raptor_rwd_runtime.SYMLINK_MEMBERS.items()):
            member = tarfile.TarInfo(f"raptor-rwd/{name}")
            member.type = tarfile.SYMTYPE
            member.linkname = target
            archive.addfile(member)
    return gzip.compress(output.getvalue(), mtime=0)


class RaptorRwdRuntimeTests(unittest.TestCase):
    def test_validates_and_normalizes_the_exact_artifact(self) -> None:
        raw = artifact_bytes()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "raptor-rwd.tar.gz"
            path.write_bytes(raw)
            payload, checksums = raptor_rwd_runtime.validate_artifact(
                path,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                supervisor=SUPERVISOR,
            )
        self.assertLess(len(payload), raptor_rwd_runtime.MAX_PAYLOAD_BYTES)
        self.assertEqual(set(checksums), raptor_rwd_runtime.REGULAR_MEMBERS)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            self.assertIn("usr/bin/prudynt", archive.getnames())
            self.assertNotIn("raptor-rwd/usr/bin/prudynt", archive.getnames())

    def test_rejects_changed_digest_and_extra_member(self) -> None:
        raw = artifact_bytes(extra=True)
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "raptor-rwd.tar.gz"
            path.write_bytes(raw)
            with self.assertRaisesRegex(
                raptor_rwd_runtime.RaptorRwdRuntimeError, "digest"
            ):
                raptor_rwd_runtime.validate_artifact(
                    path,
                    expected_sha256="0" * 64,
                    supervisor=SUPERVISOR,
                )
            with self.assertRaisesRegex(
                raptor_rwd_runtime.RaptorRwdRuntimeError, "member set"
            ):
                raptor_rwd_runtime.validate_artifact(
                    path,
                    expected_sha256=hashlib.sha256(raw).hexdigest(),
                    supervisor=SUPERVISOR,
                )

    def test_baseline_requires_media_and_eight_megabytes_available(self) -> None:
        snapshot = {
            "memory": {"available_proxy_kib": 8191},
            "prudynt": {"media_ready": True},
            "processes": {"prudynt": {"pid": 1}},
        }
        with (
            mock.patch.object(
                raptor_rwd_runtime, "collect_runtime_snapshot", return_value=snapshot
            ),
            mock.patch.object(
                raptor_rwd_runtime, "validate_runtime_snapshot", return_value=snapshot
            ),
            self.assertRaisesRegex(
                raptor_rwd_runtime.RaptorRwdRuntimeError, "baseline"
            ),
        ):
            raptor_rwd_runtime.baseline(Path("session"), "192.0.2.123")

    def test_remote_preflight_and_stop_require_ui_mount_cleanup(self) -> None:
        camera = mock.Mock()
        raptor_rwd_runtime.remote_preflight(camera)
        preflight = camera.run.call_args.args[0]
        self.assertIn("raptor-rwd-ui-binaries.mounted", preflight)
        self.assertIn("/usr/bin/uhttpd", preflight)
        self.assertIn("/usr/sbin/thingino-controld", preflight)

        camera.reset_mock()
        raptor_rwd_runtime.stop(camera)
        stop = camera.run.call_args.args[0]
        self.assertIn("raptor-rwd-ui-binaries.mounted", stop)
        self.assertIn("/usr/bin/uhttpd", stop)
        self.assertIn("/usr/sbin/thingino-controld", stop)


if __name__ == "__main__":
    unittest.main()
