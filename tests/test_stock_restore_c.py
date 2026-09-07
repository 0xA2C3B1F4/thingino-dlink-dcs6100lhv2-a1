"""Execute the production restorer's C flow against memory-only syscalls.

The generated host translation unit substitutes four assembly wrapper bodies,
not the restore algorithm. Its LP64 ABI is not the production MIPS O32 ABI.
No fixture is a firmware, and no target path can reach a host/device syscall.
Set STOCK_RESTORE_C_SANITIZE=1 to run the same cases with ASan and UBSan.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from installer.stock_restore.build import read_restorer_source


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/fixtures/stock_restore_syscalls.c"
SIZES = (0x40000, 0x1C0000, 0x480000, 0x7C0000, 0x180000, 0x40000)
WRAPPERS = re.compile(
    r"static __attribute__\(\(noinline\)\) long (call[1235])"
    r"(\([^{}]+?\))\n\{\n.*?^\}", re.MULTILINE | re.DOTALL,
)


def host_source(source: str) -> str:
    """Replace exactly the four architecture-specific definitions, fail closed."""
    matches = list(WRAPPERS.finditer(source))
    if [match[1] for match in matches] != ["call1", "call2", "call3", "call5"]:
        raise ValueError("stock restorer syscall wrapper boundary changed")
    return WRAPPERS.sub(lambda match: f"static long {match[1]}{match[2]};", source)


def synthetic_contract() -> str:
    """Independent SHA256 oracle for the fake's deterministic full-size bytes."""
    images = {}
    for index, size in enumerate(SIZES):
        raw = bytearray([0x10 + index]) * size
        if index == 0:
            raw[223235:223243] = b"6100LHV2"
        images[f"MTD{index}" if 1 <= index <= 3 else f"KEEP{index}"] = raw
    images["MMC_MODULE"] = b"\x67" * 4096
    images["AUTH"] = b"T" * 32
    lines = ['#define MMC_MODULE_PATH "/modules/jzmmc_v12.ko"']
    for name, raw in images.items():
        digest = ",".join(f"0x{byte:02x}" for byte in hashlib.sha256(raw).digest())
        lines += [f"#define {name}_SIZE {len(raw)}U",
                  f"static const unsigned char {name}_SHA256[32] = {{{digest}}};"]
    return "\n".join(lines) + "\n"


class StockRestoreCFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not compiler or os.name != "posix":
            raise unittest.SkipTest("stock restorer C flow needs a POSIX host C compiler")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="stock-c-", dir=os.environ.get("TMPDIR"),
        )
        cls.addClassCleanup(cls.temporary.cleanup)
        workspace = Path(cls.temporary.name)
        (workspace / "generated_contract.h").write_text(synthetic_contract())
        (workspace / "init_host.inc").write_text(host_source(read_restorer_source().decode("utf-8")))
        cls.executable = workspace / "stock-restore-host"
        sanitize = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"] if os.environ.get("STOCK_RESTORE_C_SANITIZE") == "1" else []
        result = subprocess.run(
            [compiler, "-std=c11", "-O2", "-fno-builtin", "-Wall", "-Wextra", "-Werror",
             *sanitize, "-I", str(workspace), "-I", str(ROOT / "installer"),
             str(HARNESS), "-o", str(cls.executable)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.success = cls.run_case()

    @classmethod
    def run_case(cls, fault=0, mode="error", short_io=False, scenario="normal"):
        result = subprocess.run(
            [str(cls.executable), str(fault), mode, str(int(short_io)), scenario],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode or result.stderr:
            raise AssertionError(f"C harness exit {result.returncode}: {result.stderr}")
        return json.loads(result.stdout)

    def select(self, op, path=None, region=None):
        return [event for event in self.success["events"]
                if event[2] == op and (path is None or event[3] == path)
                and (region is None or event[7] == region)]

    def assert_terminal_failure(self, result, *, after_activation=False):
        self.assertEqual(result["fault_hit"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["paused"], 1)
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["mutations_after_fault"], 0)
        self.assertEqual(result["forbidden_writes"], 0)
        self.assertEqual(result["unsafe_authorization"], 0)
        self.assertEqual(result["unsafe_mutation"], 0)
        self.assertEqual(result["protected_equal"], 1)
        self.assertFalse(any(
            event[0] > result["fault_step"] and event[2] in {"WRITE", "ERASE", "RENAME"}
            and event[3].startswith(("/card/", "/dev/mtd"))
            for event in result["events"]
        ))
        if not after_activation:
            self.assertEqual(result["ok_exists"], 0)

    def test_host_substitution_changes_only_four_syscall_wrappers(self):
        source = read_restorer_source().decode("utf-8")
        generated = host_source(source)
        # Reinsert exact original definitions; every other byte must survive.
        for match in WRAPPERS.finditer(source):
            prototype = f"static long {match[1]}{match[2]};"
            self.assertEqual(generated.count(prototype), 1)
            generated = generated.replace(prototype, match[0], 1)
        self.assertEqual(generated, source)
        for changed in (source.replace("long call3(", "long other_call3("), source + "\n" + WRAPPERS.search(source)[0]):
            with self.assertRaisesRegex(ValueError, "wrapper boundary"):
                host_source(changed)

    def test_full_start_restores_exact_bytes_and_only_writable_partitions(self):
        result = self.success
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["paused"], 1)
        self.assertEqual(result["restored"], [1, 1, 1])
        self.assertEqual(result["protected_equal"], 1)
        self.assertEqual(result["forbidden_writes"], 0)
        self.assertEqual(result["unsafe_authorization"], 0)
        self.assertEqual(result["unsafe_mutation"], 0)
        self.assertEqual(result["go_exists"], 0)
        self.assertEqual(result["run_durable"], 1)
        self.assertEqual(result["ok_durable"], 1)
        self.assertEqual(self.select("MLOCK")[0][5], 3)
        self.assertLess(self.select("MLOCK")[0][0], self.select("INFO")[0][0])
        self.assertEqual([event[3] for event in self.select("INFO")], [f"/dev/mtd{i}" for i in range(6)])
        self.assertEqual({event[3] for event in self.select("ERASE")}, {"/dev/mtd1", "/dev/mtd2", "/dev/mtd3"})

    def test_durable_run_and_activated_readback_precede_go_consumption(self):
        rename = self.select("RENAME", "/card/RESTORE.RUN")[0][0]
        temporary_read = self.select("READ", "/card/.RESTORE.RUN.part")[0][0]
        active_read = self.select("READ", "/card/RESTORE.RUN")[0][0]
        unlink = self.select("UNLINK", "/card/RESTORE.GO")[0][0]
        syncs = [event[0] for event in self.select("SYNC")]
        self.assertLess(temporary_read, rename)
        self.assertTrue(any(rename < step < active_read for step in syncs))
        self.assertLess(active_read, unlink)
        self.assertTrue(any(unlink < step < self.select("ERASE")[0][0] for step in syncs))

    def test_partition_readbacks_and_kernel_preactivation_precede_activation_erase(self):
        erases = self.select("ERASE")
        self.assertEqual([(event[3], event[7]) for event in erases], [
            ("/dev/mtd3", -1), ("/dev/mtd2", -1), ("/dev/mtd1", 1), ("/dev/mtd1", 0),
        ])
        self.assertEqual([event[5] for event in erases], [SIZES[3], SIZES[2], SIZES[1] - 0x10000, 0x10000])
        self.assertEqual(erases[-1][6], 2)
        self.assertEqual(erases[-2][4], 0x10000)
        for partition, before, after in ((3, erases[0][1], erases[1][0]), (2, erases[1][1], erases[2][0])):
            self.assertTrue(any(before < event[0] < after for event in self.select("READ", f"/dev/mtd{partition}")))
        tail_reads = self.select("READ", "/dev/mtd1", 1)
        self.assertTrue(any(erases[2][1] < event[0] < erases[3][0] for event in tail_reads))
        preactivation = self.select("EMIT", "RESTORE preactivation_kernel_verified")[0][0]
        self.assertLess(preactivation, erases[3][0])
        for partition in (1, 2, 3, 0, 4, 5):
            self.assertTrue(any(erases[3][1] < event[0] < self.select("RENAME", "/card/RESTORE.OK")[0][0]
                                for event in self.select("READ", f"/dev/mtd{partition}")))

    def test_startup_layout_and_module_failures_never_mutate_nor(self):
        events = [*self.select("MLOCK"), *self.select("INFO"), *self.select("MODULE"), *self.select("MOUNT")]
        for event in events:
            with self.subTest(op=event[2], path=event[3]):
                result = self.run_case(event[0])
                self.assert_terminal_failure(result)
                self.assertEqual(result["mutations"], 0)

    def test_run_commit_failure_at_every_status_boundary_preserves_go(self):
        unlink = self.select("UNLINK", "/card/RESTORE.GO")[0][0]
        events = [event for event in self.success["events"] if event[0] < unlink and (
            event[2] in {"WRITE", "READ", "CLOSE", "RENAME"}
            and event[3] in {"/card/.RESTORE.RUN.part", "/card/RESTORE.RUN"}
            or event[2] == "SYNC")]
        self.assertGreaterEqual(len(events), 8)
        for event in events:
            with self.subTest(op=event[2], step=event[0]):
                result = self.run_case(event[0])
                self.assert_terminal_failure(result)
                self.assertEqual(result["mutations"], 0)
                self.assertEqual(result["go_exists"], 1)

    def test_go_unlink_and_following_sync_failures_stop_before_first_erase(self):
        unlink = self.select("UNLINK", "/card/RESTORE.GO")[0][0]
        sync = next(event[0] for event in self.select("SYNC") if event[0] > unlink)
        for step in (unlink, sync):
            with self.subTest(step=step):
                result = self.run_case(step)
                self.assert_terminal_failure(result)
                self.assertEqual(result["mutations"], 0)

    def test_erase_failures_and_torn_erases_stop_each_partition_and_activation_block(self):
        for event in self.select("ERASE"):
            for step in sorted({event[0], event[1]}):
                for mode in ("error", "partial"):
                    with self.subTest(path=event[3], region=event[7], step=step, mode=mode):
                        self.assert_terminal_failure(self.run_case(step, mode))

    def test_page_write_errors_zero_and_torn_writes_stop_each_mutation_region(self):
        for path, region in (("/dev/mtd3", -1), ("/dev/mtd2", -1), ("/dev/mtd1", 1), ("/dev/mtd1", 0)):
            events = self.select("WRITE", path, region)
            for step in (events[0][0], events[-1][1]):
                for mode in ("error", "zero", "partial"):
                    with self.subTest(path=path, region=region, step=step, mode=mode):
                        self.assert_terminal_failure(self.run_case(step, mode))

    def test_physical_readback_corruption_stops_before_later_writes_or_ok(self):
        for path, region in (("/dev/mtd3", -1), ("/dev/mtd2", -1), ("/dev/mtd1", 1), ("/dev/mtd1", 0)):
            first_erase = self.select("ERASE", path, region)[0][0]
            events = [event for event in self.select("READ", path, region) if event[0] > first_erase]
            for event in (events[0], events[-1]):
                with self.subTest(path=path, region=region, step=event[0]):
                    self.assert_terminal_failure(self.run_case(event[0], "corrupt"))

    def test_protected_physical_comparisons_run_before_and_after_mutation(self):
        first_erase = self.select("ERASE")[0][0]
        last_write = max(event[1] for event in self.select("WRITE") if event[3].startswith("/dev/mtd"))
        for index in (0, 4, 5):
            reads = self.select("READ", f"/dev/mtd{index}")
            # mtd0 also has the model-marker read; compare reads start at zero.
            comparisons = [event for event in reads if event[4] == 0]
            self.assertEqual(len(comparisons), 2)
            self.assertLess(comparisons[0][0], first_erase)
            self.assertGreater(comparisons[1][0], last_write)
            for event in comparisons:
                with self.subTest(partition=index, step=event[0]):
                    result = self.run_case(event[0], "corrupt")
                    self.assert_terminal_failure(result)
                    if event[0] < first_erase:
                        self.assertEqual(result["mutations"], 0)

    def test_activation_buffer_and_physical_tail_digest_gate_activation_erase(self):
        tail_verified = self.select("EMIT", "RESTORE mtd1_tail_physical_readback_verified")[0][0]
        preactivation = self.select("EMIT", "RESTORE preactivation_kernel_verified")[0][0]
        activation_erase = self.select("ERASE", "/dev/mtd1", 0)[0][0]
        reads = [event for event in self.success["events"]
                 if event[2] == "READ" and tail_verified < event[0] < preactivation]
        self.assertEqual({event[3] for event in reads}, {"/card/STOCK1.BIN", "/dev/mtd1"})
        for event in reads:
            with self.subTest(path=event[3], step=event[0]):
                result = self.run_case(event[0], "corrupt")
                self.assert_terminal_failure(result)
                self.assertFalse(any(item[2] == "ERASE" and item[3] == "/dev/mtd1" and item[7] == 0
                                     for item in result["events"]))
                self.assertLess(event[0], activation_erase)

    def test_nor_sync_seek_and_close_failures_are_terminal(self):
        first_erase = self.select("ERASE")[0][0]
        ok_write = self.select("WRITE", "/card/.RESTORE.OK.part")[0][0]
        events = [event for event in self.select("SYNC") if first_erase < event[0] < ok_write]
        self.assertEqual(len(events), 4)
        for path in ("/dev/mtd1", "/dev/mtd2", "/dev/mtd3"):
            for op in ("SEEK", "CLOSE"):
                candidates = [event for event in self.select(op, path) if event[0] > first_erase]
                events.extend((candidates[0], candidates[-1]))
        for event in events:
            with self.subTest(op=event[2], path=event[3], step=event[0]):
                self.assert_terminal_failure(self.run_case(event[0]))

    def test_completion_marker_failure_does_not_report_success_or_resume_nor_writes(self):
        start = self.select("WRITE", "/card/.RESTORE.OK.part")[0][0]
        events = [event for event in self.success["events"] if event[0] >= start and (
            event[2] in {"WRITE", "READ", "CLOSE", "RENAME"}
            and event[3] in {"/card/.RESTORE.OK.part", "/card/RESTORE.OK"}
            or event[2] == "SYNC")]
        self.assertGreaterEqual(len(events), 8)
        for event in events:
            with self.subTest(op=event[2], step=event[0]):
                result = self.run_case(event[0])
                # The original code leaves an activated OK in place if its
                # subsequent sync/readback fails. It still emits FAIL, not COMPLETE.
                self.assert_terminal_failure(result, after_activation=True)
                self.assertEqual(result["restored"], [1, 1, 1])

    def test_short_positive_reads_and_writes_complete_exactly(self):
        result = self.run_case(short_io=True)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["restored"], [1, 1, 1])
        self.assertEqual(result["protected_equal"], 1)
        self.assertEqual(result["unsafe_mutation"], 0)

    def test_existing_run_and_exact_run_temporary_resume_from_start(self):
        for scenario in ("retry", "run-temp"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario=scenario)
                self.assertEqual(result["completed"], 1)
                self.assertEqual(result["restored"], [1, 1, 1])
                self.assertEqual(result["unsafe_authorization"], 0)
                self.assertEqual(result["unsafe_mutation"], 0)
                self.assertEqual(result["go_exists"], 0)
                self.assertEqual(next(event[3] for event in result["events"] if event[2] == "ERASE"), "/dev/mtd3")

    def test_invalid_run_temporary_auth_or_existing_ok_stop_without_writes(self):
        for scenario in ("bad-run-temp", "bad-auth", "complete"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario=scenario)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["completed"], 0)
                self.assertEqual(result["mutations"], 0)
                self.assertEqual(result["go_exists"], 1)
                self.assertEqual(result["protected_equal"], 1)


if __name__ == "__main__":
    unittest.main()
