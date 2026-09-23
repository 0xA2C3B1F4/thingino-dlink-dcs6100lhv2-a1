from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from installer import runtime_candidate


def mips_elf() -> bytes:
    raw = bytearray(96)
    raw[:6] = b"\x7fELF\x01\x01"
    raw[16:18] = (3).to_bytes(2, "little")
    raw[18:20] = (8).to_bytes(2, "little")
    raw[36:40] = (0x70001007).to_bytes(4, "little")
    raw[64:77] = b"/lib/ld.so.1\0"
    return bytes(raw)


class RuntimeCandidateTests(unittest.TestCase):
    def test_shared_mips_elf_validator_remains_available_to_raptor_components(self) -> None:
        runtime_candidate._validate_mips_elf(mips_elf(), "rvd")
        invalid = bytearray(mips_elf())
        invalid[18:20] = (3).to_bytes(2, "little")
        with self.assertRaisesRegex(runtime_candidate.RuntimeCandidateError, "MIPS"):
            runtime_candidate._validate_mips_elf(bytes(invalid), "rvd")

    def test_operations_are_retired_and_fail_closed(self) -> None:
        operations = (
            lambda: runtime_candidate.build_runtime_candidate_package(
                rootfs_path=Path("rootfs"), provenance_path=Path("provenance")
            ),
            lambda: runtime_candidate.stage_runtime_candidate(
                session_dir=Path("session"), host="192.0.2.20",
                rootfs_path=Path("rootfs"), provenance_path=Path("provenance"),
                expected_mtd3_sha256="a" * 64,
            ),
            lambda: runtime_candidate.runtime_candidate_status(
                session_dir=Path("session"), host="192.0.2.20"
            ),
            lambda: runtime_candidate.rollback_runtime_candidate(
                session_dir=Path("session"), host="192.0.2.20"
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(runtime_candidate.RuntimeCandidateError, "retired"):
                    operation()

    def test_retired_template_has_valid_shell_syntax_and_no_legacy_owner(self) -> None:
        completed = subprocess.run(
            ["sh", "-n", str(runtime_candidate.TEMPLATE_PATH)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        source = runtime_candidate.TEMPLATE_PATH.read_text(encoding="utf-8").lower()
        self.assertIn("full raptor", source)
        self.assertNotIn("prudynt", source)

    def test_module_has_no_legacy_media_owner_logic(self) -> None:
        source = Path(runtime_candidate.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("prudynt", source)
        self.assertNotIn("thingino-runtime-candidate", source)


if __name__ == "__main__":
    unittest.main()
