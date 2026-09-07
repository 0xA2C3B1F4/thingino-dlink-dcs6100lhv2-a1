"""Host characterization of the original stock-restore file transactions.

All payloads are synthetic, all paths live below TemporaryDirectory, and no
builder, removable media probe or device is invoked. These tests exercise
set.py itself; fake_mtd tests and C source assertions do not execute init.c's
MIPS syscalls. In particular, a host pass does not prove NOR readback or O32 ABI.
"""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from installer.fake_mtd import StockRestoreImages
from installer.media import MediaPreflight
from installer.stock_restore import activation, passivation, staging
from installer.stock_restore import set as subject


class InjectedFailure(OSError):
    pass


def prepared_set(bootstrap: bool):
    # Transaction entrypoints receive an already inspected set. Small synthetic
    # images deliberately avoid constructing a firmware or invoking its builder.
    images = StockRestoreImages(
        mtd1=b"one", mtd2=b"two", mtd3=b"three",
        protected=(b"zero", b"four", b"five"), write_set=(3, 2, 1),
    )
    files = (
        {subject.BOOTSTRAP_PRIVATE_NAME: b"synthetic inert package"}
        if bootstrap else
        {subject.KERNEL_SD_NAME: b"kernel", subject.MODE_WRAPPERS["stock-restore"]: b"wrapper"}
    )
    files.update(dict(zip(subject.PAYLOAD_NAMES.values(), (
        b"zero", b"one", b"two", b"three", b"four", b"five",
    ))))
    files[subject.AUTH_PRIVATE_NAME] = b"T" * 32
    if bootstrap:
        return subject.StockRestoreBootstrapSet(images, b"T" * 32, files)
    return subject.StockRestoreRamSet(mock.sentinel.live_plan, images, b"T" * 32, files)


class FileTrace(ExitStack):
    """Observe real temporary-file IO, with a one-shot failure before an event."""

    def __init__(self, root: Path, fault=None):
        super().__init__()
        self.events = []
        self.fault = fault
        self.failure = InjectedFailure("injected transaction failure")
        self.triggered = False
        write = subject._write_exclusive
        read = subject._read_regular
        read_bytes = Path.read_bytes
        replace = os.replace
        fsync = os.fsync

        def relative(path):
            return str(Path(path).relative_to(root))

        def event(*parts):
            self.events.append(parts)
            if parts == self.fault and not self.triggered:
                self.triggered = True
                raise self.failure

        def traced_write(path, raw, mode=0o600):
            event("write", relative(path))
            return write(path, raw, mode)

        def traced_read(path, size):
            event("read", relative(path))
            return read(path, size)

        def traced_read_bytes(path):
            event("read", relative(path))
            return read_bytes(path)

        def traced_replace(source, destination):
            event("rename", relative(source), relative(destination))
            return replace(source, destination)

        def traced_fsync(descriptor):
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                # RAM staging calls os.fsync directly; other transactions use
                # _fsync_directory. Observe both without replacing either flow.
                directory = "." if metadata.st_ino == root.stat().st_ino else subject.PASSIVE_DIR_NAME
                event("sync", directory)
            return fsync(descriptor)

        for owner in (subject, staging, activation, passivation):
            self.enter_context(mock.patch.object(owner, "_write_exclusive", traced_write))
            self.enter_context(mock.patch.object(owner, "_read_regular", traced_read))
        for owner, name, replacement in (
            (Path, "read_bytes", traced_read_bytes),
            (os, "replace", traced_replace),
            (os, "fsync", traced_fsync),
        ):
            self.enter_context(mock.patch.object(owner, name, replacement))


class StockRestoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="stock-transactions-", dir=os.environ.get("TMPDIR"),
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preflight = MediaPreflight(
            physical_device="synthetic-device", model="host fixture",
            capacity_bytes=128_000_000, filesystem="fat32",
            mount_root=self.root.resolve(),
        )
        self.arguments = dict(
            root=self.root, preflight=self.preflight,
            confirmed_physical_device=self.preflight.physical_device,
        )
        self.prepared = prepared_set(True)
        self.staged = {
            name: raw for name, raw in self.prepared.files.items()
            if name != subject.AUTH_PRIVATE_NAME
        }
        (self.root / "unrelated.txt").write_bytes(b"preserve")

    def stage(self, bootstrap=True):
        prepared = prepared_set(bootstrap)
        function = subject.stage_stock_restore_bootstrap_set if bootstrap else subject.stage_stock_restore_ram_set
        return function(prepared=prepared, **self.arguments)

    def authorize(self):
        return subject.authorize_stock_restore_bootstrap(
            prepared=self.prepared, **self.arguments,
            confirmation=subject.expected_bootstrap_confirmation(self.preflight),
        )

    def handoff(self):
        return subject.deactivate_stock_restore_bootstrap(
            prepared=self.prepared, **self.arguments,
            confirmation=subject.expected_handoff_confirmation(self.preflight),
        )

    def retry(self):
        return subject.reauthorize_interrupted_stock_restore(
            prepared=self.prepared, **self.arguments,
            confirmation=subject.expected_retry_confirmation(self.preflight),
        )

    def complete(self):
        # A synthetic terminal state, not a claim that a camera restored.
        self.stage()
        (self.root / subject.RUN_SD_NAME).write_bytes(subject.RUN_STATUS)
        (self.root / subject.COMPLETE_SD_NAME).write_bytes(subject.COMPLETE_STATUS)

    def passivate(self):
        return subject.passivate_completed_stock_restore(prepared=self.prepared, **self.arguments)

    def assert_unrelated(self):
        self.assertEqual((self.root / "unrelated.txt").read_bytes(), b"preserve")

    def assert_fault(self, trace, operation):
        with self.assertRaises(InjectedFailure) as caught:
            operation()
        self.assertIs(caught.exception, trace.failure)
        self.assertTrue(trace.triggered)

    def test_staging_writes_reads_renames_then_syncs_and_reads_complete_set(self):
        for bootstrap in (False, True):
            with self.subTest(bootstrap=bootstrap):
                prepared = prepared_set(bootstrap)
                names = [n for n in prepared.files if n != subject.AUTH_PRIVATE_NAME]
                expected = []
                for name in names:
                    expected.extend([
                        ("write", f".{name}.part"), ("read", f".{name}.part"),
                        ("rename", f".{name}.part", name),
                    ])
                expected += [("sync", "."), *(("read", n) for n in names)]
                with FileTrace(self.root) as trace:
                    self.assertEqual(self.stage(bootstrap), tuple(names))
                self.assertEqual(trace.events, expected)
                self.assertFalse((self.root / subject.AUTH_SD_NAME).exists())
                self.assertFalse((self.root / subject.BOOTSTRAP_ACTIVE_NAME).exists())
                for name in names:
                    (self.root / name).unlink()
                self.assert_unrelated()

    def test_staging_failure_at_each_file_boundary_cleans_owned_files_without_rollback_sync(self):
        for bootstrap in (False, True):
            names = [n for n in prepared_set(bootstrap).files if n != subject.AUTH_PRIVATE_NAME]
            faults = [("sync", ".")]
            for name in names:
                faults.extend([
                    ("write", f".{name}.part"), ("read", f".{name}.part"),
                    ("rename", f".{name}.part", name), ("read", name),
                ])
            for fault in faults:
                with self.subTest(bootstrap=bootstrap, fault=fault):
                    with FileTrace(self.root, fault) as trace:
                        self.assert_fault(trace, lambda: self.stage(bootstrap))
                    self.assertEqual({p.name for p in self.root.iterdir()}, {"unrelated.txt"})
                    # Characterizes the current cleanup durability gap.
                    self.assertEqual(trace.events[-1], fault)
                    self.assert_unrelated()

    def test_ram_staging_failure_preserves_reused_kernel(self):
        kernel = self.root / subject.KERNEL_SD_NAME
        kernel.write_bytes(b"kernel")
        with FileTrace(self.root, ("read", "STOCK3.BIN")) as trace:
            self.assert_fault(trace, lambda: self.stage(False))
        self.assertEqual({p.name for p in self.root.iterdir()}, {"unrelated.txt", kernel.name})
        self.assertEqual(kernel.read_bytes(), b"kernel")

    def test_partial_stage_write_is_removed(self):
        def partial(path, raw, mode=0o600):
            path.write_bytes(raw[:1])
            raise InjectedFailure("partial")

        for bootstrap in (False, True):
            with self.subTest(bootstrap=bootstrap), mock.patch.object(staging, "_write_exclusive", partial):
                with self.assertRaisesRegex(InjectedFailure, "partial"):
                    self.stage(bootstrap)
            self.assertEqual({p.name for p in self.root.iterdir()}, {"unrelated.txt"})

    def test_marker_activation_reads_temporary_before_rename_and_final_after_sync(self):
        name = subject.AUTH_SD_NAME
        with FileTrace(self.root) as trace:
            self.assertTrue(subject._activate_exact_status(self.root, name, b"token"))
        self.assertEqual(trace.events, [
            ("write", f".{name}.part"), ("read", f".{name}.part"),
            ("rename", f".{name}.part", name), ("sync", "."), ("read", name),
        ])
        with FileTrace(self.root) as trace:
            self.assertFalse(subject._activate_exact_status(self.root, name, b"token"))
        self.assertEqual(trace.events, [("read", name)])

    def test_marker_failure_at_each_boundary_removes_marker_and_syncs_rollback(self):
        name = subject.AUTH_SD_NAME
        faults = [
            ("write", f".{name}.part"), ("read", f".{name}.part"),
            ("rename", f".{name}.part", name), ("sync", "."), ("read", name),
        ]
        for fault in faults:
            with self.subTest(fault=fault), FileTrace(self.root, fault) as trace:
                self.assert_fault(trace, lambda: subject._activate_exact_status(self.root, name, b"token"))
            self.assertEqual(trace.events[-1], ("sync", "."))
            self.assertEqual({p.name for p in self.root.iterdir()}, {"unrelated.txt"})

    def test_marker_rollback_sync_failure_reports_uncertainty_with_cause(self):
        cause = InjectedFailure("directory sync")
        with mock.patch.object(activation, "_fsync_directory", side_effect=cause):
            with self.assertRaisesRegex(subject.StockRestoreSetError, "rollback is uncertain") as caught:
                subject._activate_exact_status(self.root, subject.AUTH_SD_NAME, b"token")
        self.assertIs(caught.exception.__cause__, cause)
        self.assertFalse((self.root / subject.AUTH_SD_NAME).exists())

    def test_bootstrap_authorization_commits_and_reads_token_before_selector(self):
        self.stage()
        with FileTrace(self.root) as trace:
            self.authorize()
        token_read = trace.events.index(("read", subject.AUTH_SD_NAME))
        selector = trace.events.index(("rename", subject.BOOTSTRAP_PRIVATE_NAME, subject.BOOTSTRAP_ACTIVE_NAME))
        self.assertLess(token_read, selector)
        self.assertEqual(trace.events[selector + 1:], [
            ("sync", "."), ("read", subject.BOOTSTRAP_ACTIVE_NAME),
        ])
        with FileTrace(self.root) as trace:
            self.authorize()
        self.assertTrue(all(event[0] == "read" for event in trace.events))

    def test_authorization_preflight_and_confirmation_fail_before_io(self):
        self.stage()
        for change in (
            {"confirmed_physical_device": "wrong-device"},
            {"confirmation": "wrong-confirmation"},
        ):
            with self.subTest(change=change), FileTrace(self.root) as trace:
                arguments = dict(self.arguments, confirmation=subject.expected_bootstrap_confirmation(self.preflight))
                arguments.update(change)
                with self.assertRaises(subject.StockRestoreSetError):
                    subject.authorize_stock_restore_bootstrap(prepared=self.prepared, **arguments)
            self.assertEqual(trace.events, [])

    def test_selector_failure_restores_inert_package_but_retains_new_authorization(self):
        self.stage()
        faults = [
            ("rename", subject.BOOTSTRAP_PRIVATE_NAME, subject.BOOTSTRAP_ACTIVE_NAME),
            ("read", subject.BOOTSTRAP_ACTIVE_NAME),
        ]
        for fault in faults:
            with self.subTest(fault=fault), FileTrace(self.root, fault) as trace:
                self.assert_fault(trace, self.authorize)
            self.assertFalse((self.root / subject.BOOTSTRAP_ACTIVE_NAME).exists())
            self.assertEqual((self.root / subject.AUTH_SD_NAME).read_bytes(), self.prepared.authorization)
            self.assertEqual((self.root / subject.BOOTSTRAP_PRIVATE_NAME).read_bytes(), self.staged[subject.BOOTSTRAP_PRIVATE_NAME])
            self.assertEqual(trace.events[-2:], [("sync", "."), ("read", subject.BOOTSTRAP_PRIVATE_NAME)])
            (self.root / subject.AUTH_SD_NAME).unlink()

    def test_selector_sync_failure_rolls_back_and_validates_inert_readback(self):
        self.stage()
        with FileTrace(self.root, ("sync", ".")) as trace:
            self.assert_fault(trace, lambda: subject._activate_bootstrap_selector(self.root, self.staged[subject.BOOTSTRAP_PRIVATE_NAME]))
        self.assertEqual(trace.events[-3:], [
            ("rename", subject.BOOTSTRAP_ACTIVE_NAME, subject.BOOTSTRAP_PRIVATE_NAME),
            ("sync", "."), ("read", subject.BOOTSTRAP_PRIVATE_NAME),
        ])

    def test_selector_rollback_failure_is_reported_as_uncertain(self):
        self.stage()
        original = os.replace
        rollback = InjectedFailure("rollback rename")

        def replace(source, destination):
            if source.name == subject.BOOTSTRAP_ACTIVE_NAME:
                raise rollback
            return original(source, destination)

        with mock.patch.object(os, "replace", replace), mock.patch.object(activation, "selected_update_filename", return_value=None):
            with self.assertRaisesRegex(subject.StockRestoreSetError, "rollback is uncertain") as caught:
                self.authorize()
        self.assertIs(caught.exception.__cause__, rollback)
        self.assertTrue((self.root / subject.BOOTSTRAP_ACTIVE_NAME).exists())
        self.assertTrue((self.root / subject.AUTH_SD_NAME).exists())

    def test_handoff_readback_failure_reactivates_original_selector(self):
        self.stage()
        self.authorize()
        with FileTrace(self.root, ("read", subject.BOOTSTRAP_PRIVATE_NAME)) as trace:
            self.assert_fault(trace, self.handoff)
        self.assertEqual(trace.events[-2:], [
            ("rename", subject.BOOTSTRAP_PRIVATE_NAME, subject.BOOTSTRAP_ACTIVE_NAME),
            ("sync", "."),
        ])
        self.assertTrue((self.root / subject.BOOTSTRAP_ACTIVE_NAME).exists())
        self.assertFalse((self.root / subject.BOOTSTRAP_PRIVATE_NAME).exists())
        self.assertEqual((self.root / subject.AUTH_SD_NAME).read_bytes(), self.prepared.authorization)

    def test_handoff_rollback_failure_propagates_raw_error(self):
        self.stage()
        self.authorize()
        original = os.replace
        rollback = InjectedFailure("handoff rollback")

        def replace(source, destination):
            if source.name == subject.BOOTSTRAP_PRIVATE_NAME:
                raise rollback
            return original(source, destination)

        with FileTrace(self.root, ("read", subject.BOOTSTRAP_PRIVATE_NAME)), mock.patch.object(os, "replace", replace):
            with self.assertRaises(InjectedFailure) as caught:
                self.handoff()
        self.assertIs(caught.exception, rollback)
        self.assertTrue((self.root / subject.BOOTSTRAP_PRIVATE_NAME).exists())
        self.assertFalse((self.root / subject.BOOTSTRAP_ACTIVE_NAME).exists())

    def test_retry_checks_run_and_payloads_before_reauthorizing_and_activates_last(self):
        self.stage()
        (self.root / subject.RUN_SD_NAME).write_bytes(subject.RUN_STATUS)
        with FileTrace(self.root) as trace:
            self.retry()
        run_read = trace.events.index(("read", subject.RUN_SD_NAME))
        token_write = trace.events.index(("write", f".{subject.AUTH_SD_NAME}.part"))
        selector = trace.events.index(("rename", subject.BOOTSTRAP_PRIVATE_NAME, subject.BOOTSTRAP_ACTIVE_NAME))
        self.assertLess(run_read, token_write)
        self.assertLess(trace.events.index(("read", subject.AUTH_SD_NAME)), selector)
        for name in self.staged:
            self.assertLess(trace.events.index(("read", name)), token_write)
        self.assertEqual(trace.events[-1], ("read", subject.BOOTSTRAP_ACTIVE_NAME))

    def test_retry_with_complete_marker_never_recreates_authorization(self):
        self.complete()
        with FileTrace(self.root) as trace:
            with self.assertRaisesRegex(subject.StockRestoreSetError, "not retryable"):
                self.retry()
        self.assertTrue(all(event[0] == "read" for event in trace.events))
        self.assertFalse((self.root / subject.AUTH_SD_NAME).exists())

    def test_passivation_moves_status_first_reads_every_move_and_writes_marker_last(self):
        self.complete()
        order = [subject.RUN_SD_NAME, subject.COMPLETE_SD_NAME, *sorted(self.staged)]
        passive = subject.PASSIVE_DIR_NAME
        with FileTrace(self.root) as trace:
            result = self.passivate()
        self.assertEqual(result.moved_files, tuple(order))
        self.assertEqual(result.preserved_unrelated_entries, 1)
        renames = [event for event in trace.events if event[0] == "rename"]
        self.assertEqual(renames, [("rename", name, f"{passive}/{name}") for name in order])
        for event in renames:
            index = trace.events.index(event)
            self.assertEqual(trace.events[index - 1], ("read", event[1]))
            self.assertEqual(trace.events[index + 1:index + 4], [
                ("sync", passive), ("sync", "."), ("read", event[2]),
            ])
        marker = f"{passive}/{subject.PASSIVE_MARKER_NAME}"
        marker_write = trace.events.index(("write", marker))
        self.assertGreater(marker_write, trace.events.index(renames[-1]) + 3)
        self.assertEqual(trace.events[marker_write + 1:marker_write + 4], [
            ("sync", passive), ("sync", "."), ("read", marker),
        ])
        with FileTrace(self.root) as trace:
            self.assertTrue(self.passivate().already_passivated)
        self.assertTrue(all(event[0] == "read" for event in trace.events))
        self.assert_unrelated()

    def test_passivation_failure_keeps_moved_prefix_and_resumes_without_rollback(self):
        self.complete()
        passive = subject.PASSIVE_DIR_NAME
        fault = ("read", f"{passive}/{subject.COMPLETE_SD_NAME}")
        with FileTrace(self.root, fault) as trace:
            self.assert_fault(trace, self.passivate)
        self.assertEqual(trace.events[-1], fault)
        self.assertFalse((self.root / passive / subject.PASSIVE_MARKER_NAME).exists())
        for name in (subject.RUN_SD_NAME, subject.COMPLETE_SD_NAME):
            self.assertFalse((self.root / name).exists())
            self.assertTrue((self.root / passive / name).exists())
        result = self.passivate()
        self.assertEqual(result.moved_files, tuple(sorted(self.staged)))
        self.assertFalse(result.already_passivated)
        self.assert_unrelated()

    def test_passivation_marker_readback_failure_leaves_marker_and_completed_moves(self):
        self.complete()
        marker = f"{subject.PASSIVE_DIR_NAME}/{subject.PASSIVE_MARKER_NAME}"
        with FileTrace(self.root, ("read", marker)) as trace:
            self.assert_fault(trace, self.passivate)
        self.assertEqual((self.root / marker).read_bytes(), subject.PASSIVE_STATUS)
        self.assertTrue(self.passivate().already_passivated)

    def test_partial_passivation_marker_blocks_resume_until_separately_resolved(self):
        self.complete()
        write = subject._write_exclusive

        def partial(path, raw, mode=0o600):
            if path.name == subject.PASSIVE_MARKER_NAME:
                path.write_bytes(raw[:1])
                raise InjectedFailure("partial passive marker")
            return write(path, raw, mode)

        with mock.patch.object(passivation, "_write_exclusive", partial):
            with self.assertRaisesRegex(InjectedFailure, "partial passive marker"):
                self.passivate()
        with FileTrace(self.root) as trace:
            with self.assertRaisesRegex(subject.StockRestoreSetError, "not exact"):
                self.passivate()
        self.assertTrue(all(event[0] == "read" for event in trace.events))
        self.assert_unrelated()


if __name__ == "__main__":
    unittest.main()
