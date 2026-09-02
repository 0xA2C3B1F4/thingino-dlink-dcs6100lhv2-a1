from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.collector import build
from installer.collector.build import CollectorBuildError


class CollectorBuildTests(unittest.TestCase):
    def test_contract_binds_module_and_exact_vendor_manifest(self) -> None:
        module = b"reviewed-mmc-module"
        contract = build.render_contract(mmc_module=module).decode("ascii")
        self.assertIn(f"#define MMC_MODULE_SIZE {len(module)}U", contract)
        self.assertIn(f"0x{hashlib.sha256(module).digest()[0]:02x}", contract)
        for symbol, size in (
            ("LIBIMP", 1_146_852),
            ("LIBALOG", 36_044),
            ("LIBSYSUTILS", 30_020),
            ("LIBAUDIOPROCESS", 697_757),
        ):
            self.assertIn(f"#define {symbol}_SIZE {size}U", contract)
        self.assertIn("VENDOR_MANIFEST_JSON", contract)
        self.assertNotIn("VENDOR_MANIFEST_OPTIONAL_JSON", contract)
        self.assertIn("DEVICE_LAYOUT_JSON", contract)
        self.assertNotIn("mtd5.bin", contract)

    def test_contract_rejects_noninteger_or_out_of_partition_catalog_sizes(self) -> None:
        document = json.loads(build.CATALOG_PATH.read_text(encoding="utf-8"))
        for invalid in ("1146852); injected", True, 0, 0x7C0001):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as name:
                document["files"][0]["size"] = invalid
                catalog = Path(name) / "catalog.json"
                catalog.write_text(json.dumps(document), encoding="utf-8")
                with mock.patch.object(build, "CATALOG_PATH", catalog):
                    with self.assertRaisesRegex(CollectorBuildError, "invalid size"):
                        build.render_contract(mmc_module=b"reviewed-mmc-module")

    def test_complete_backup_mode_is_compile_time_read_only_and_reads_all_twice(self) -> None:
        contract = build.render_contract(
            mmc_module=b"reviewed-mmc-module",
            capture_mode="complete-backup",
        ).decode("ascii")
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn("#define FULL_BACKUP_CAPTURE 1", contract)
        self.assertIn("FULL_BACKUP_LAYOUT_JSON", contract)
        for mtd in range(6):
            self.assertEqual(
                source.count(f'copy_mtd_with_sd_readback("/dev/mtd{mtd}"'),
                2,
            )
        self.assertIn("duplicate_reads=complete", source)
        self.assertNotIn("MEMERASE", source)

    def test_functional_uartless_mode_is_distinct_from_original_backup(self) -> None:
        contract = build.render_contract(
            mmc_module=b"reviewed-mmc-module",
            capture_mode="functional-uartless",
        ).decode("ascii")
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn("#define FUNCTIONAL_CAPTURE 1", contract)
        self.assertIn("FUNCTIONAL_LAYOUT_JSON", contract)
        self.assertIn('FULL_BACKUP_ROOT "/card/DCS6100F"', source)
        self.assertIn("functional_duplicate_reads=complete", source)
        self.assertIn("pre_capture_writes=mtd1,mtd2", source)

    def test_protected_readback_mode_does_not_depend_on_stock_vendor_mount(self) -> None:
        contract = build.render_contract(
            mmc_module=b"reviewed-mmc-module",
            capture_mode="protected-readback",
        ).decode("ascii")
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn("#define FULL_BACKUP_CAPTURE 0", contract)
        self.assertIn("#define PROTECTED_CAPTURE 1", contract)
        protected_start = source.index("static void capture_protected_readback")
        protected_end = source.index("#endif", protected_start)
        protected = source[protected_start:protected_end]
        for mtd in (0, 4, 5):
            self.assertIn(f'"/dev/mtd{mtd}"', protected)
        for mtd in (1, 2, 3):
            self.assertNotIn(f'"/dev/mtd{mtd}"', protected)
        self.assertNotIn("/stock", protected)
        self.assertIn("partition_storage_readback_verified", protected)
        self.assertNotIn("sha256", protected)

    def test_source_has_no_nor_mutation_or_general_purpose_interface(self) -> None:
        source = build.SOURCE.read_bytes()
        build.validate_collector_source(source)
        text = source.decode("utf-8")
        for token in (
            "MEMERASE",
            "O_RDWR",
            "SYSCALL_EXECVE",
            "SYSCALL_UNLINK",
            "SYSCALL_RENAME",
            "/bin/sh",
        ):
            self.assertNotIn(token, text)
        self.assertEqual(text.count("O_WRONLY | O_CREAT | O_EXCL"), 3)
        for mtd in range(6):
            self.assertIn(f'verify_mtd("/dev/mtd{mtd}"', text)
        self.assertIn('"/dev/mtd6", O_RDONLY', text)
        self.assertIn(
            'mount_checked("/dev/mtdblock3", "/stock", "jffs2",', text
        )
        self.assertIn("MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC", text)

    def test_source_gate_rejects_a_write_capable_mtd_open_mode(self) -> None:
        changed = build.SOURCE.read_bytes().replace(
            b"O_RDONLY = 0,", b"O_RDWR = 2,", 1
        )
        with self.assertRaisesRegex(CollectorBuildError, "O_RDWR"):
            build.validate_collector_source(changed)

    def test_default_mac_toolchain_uses_one_homebrew_llvm_distribution(self) -> None:
        clang = Path("/opt/homebrew/opt/llvm/bin/clang")
        lld = Path("/opt/homebrew/bin/ld.lld")
        with (
            mock.patch.object(Path, "is_file", autospec=True, return_value=True),
            mock.patch.object(build.shutil, "which", return_value=str(lld)),
        ):
            selected = build._resolve_llvm_pair(None, None)
        self.assertEqual(selected, (clang.resolve(), lld.resolve()))

    def test_completion_record_does_not_disclose_preserved_hashes(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        records = []
        for audio in ("true",):
            prefix = f'"{{\\"audio_process_archived\\":{audio}'
            start = source.index(prefix) + 1
            end = source.index('\\n"', start) + 2
            encoded = source[start:end]
            decoded = bytes(encoded, "utf-8").decode("unicode_escape")
            records.append(json.loads(decoded))
        for record in records:
            self.assertEqual(record["mode"], "existing-verified-same-device-pair")
            self.assertFalse(record["nor_writes"])
            self.assertNotIn("sha256", record)
            self.assertNotIn("mtd5", record)


if __name__ == "__main__":
    unittest.main()
