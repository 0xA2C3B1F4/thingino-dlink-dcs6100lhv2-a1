"""Fixed stock-restorer source assembly and validation/compile byte ownership."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from installer.stock_restore import build


class StockRestoreSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="stock-source-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "init.c"
        self.names = ("runtime.inc", "verify.inc", "flash.inc", "authorization.inc")
        self.prefix = b'#include "generated_contract.h"\ntypedef unsigned int u32;\n#include "freestanding_sha256.h"\n'
        self.suffix = b"void _start(void) {}\n"
        self.parts = {name: f"/* synthetic {name} */\n\n".encode() for name in self.names}
        self.source.write_bytes(self.prefix + b"".join(f'#include "{name}"\n'.encode() for name in self.names) + self.suffix)
        for name, raw in self.parts.items():
            (self.root / name).write_bytes(raw)

    def test_exact_bytes_and_external_includes_survive_without_delimiters(self):
        self.assertIsInstance(build.SOURCE, Path)
        self.assertEqual(build.read_restorer_source(self.source), self.prefix + b"".join(self.parts.values()) + self.suffix)
        # Neither generated_contract nor the shared SHA header is read by the
        # assembler. The real C preprocessor still owns those external inputs.
        self.assertFalse((self.root / "generated_contract.h").exists())
        self.assertFalse((self.root / "freestanding_sha256.h").exists())

    def test_missing_source_or_module_is_rejected(self):
        for name in ("init.c", *self.names):
            with self.subTest(name=name):
                path = self.root / name
                raw = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(build.StockRestoreBuildError, "not regular"):
                    build.read_restorer_source(self.source)
                path.write_bytes(raw)

    def test_unknown_duplicate_missing_or_reordered_include_is_rejected(self):
        original = self.source.read_bytes()
        mutations = {
            "unknown": original.replace(b'"verify.inc"', b'"unknown.inc"'),
            "duplicate": original.replace(b'#include "flash.inc"\n', b'#include "flash.inc"\n#include "flash.inc"\n'),
            "missing": original.replace(b'#include "runtime.inc"\n', b""),
            "order": original.replace(b'#include "runtime.inc"\n#include "verify.inc"\n', b'#include "verify.inc"\n#include "runtime.inc"\n'),
            "external-order": original.replace(b'"generated_contract.h"', b'"freestanding_sha256.h"'),
            "absolute": original.replace(b'"verify.inc"', b'"/unopened/verify.inc"'),
            "parent": original.replace(b'"verify.inc"', b'"../verify.inc"'),
            "include-next": original + b'#include_next "outside.inc"\n',
            "macro-include": original + b'#include OUTSIDE\n',
            "conditional": original + b'#if 1\n#endif\n',
        }
        for name, raw in mutations.items():
            with self.subTest(name=name):
                self.source.write_bytes(raw)
                with self.assertRaisesRegex(build.StockRestoreBuildError, "include names or order"):
                    build.read_restorer_source(self.source)

    def test_source_and_each_module_symlink_are_rejected(self):
        for name in ("init.c", *self.names):
            with self.subTest(name=name):
                path = self.root / name
                target = self.root / "synthetic-target"
                path.replace(target)
                path.symlink_to(target)
                with self.assertRaisesRegex(build.StockRestoreBuildError, "not regular"):
                    build.read_restorer_source(self.source)
                path.unlink()
                target.replace(path)

    def test_nested_module_include_is_rejected_without_resolving_it(self):
        for directive in (b'#include "unknown.inc"\n', b'#include "generated_contract.h"\n',
                          b'#include_next "outside.inc"\n', b'#/**/include "outside.inc"\n',
                          b'%:include "outside.inc"\n'):
            with self.subTest(directive=directive):
                (self.root / "runtime.inc").write_bytes(directive)
                with self.assertRaisesRegex(build.StockRestoreBuildError, "module contains an include"):
                    build.read_restorer_source(self.source)

    def test_hidden_directives_in_root_and_fragments_are_rejected(self):
        original = self.source.read_bytes()
        variants = (
            b'/* leading comment */ #include "unreviewed.inc"\n',
            b'\f#include "unreviewed.inc"\n',
            b'\v#include "unreviewed.inc"\n',
            b'/* leading\ncomment */ #include "unreviewed.inc"\n',
            b'/* leading comment */ %:include "unreviewed.inc"\n',
            b'\f%:include "unreviewed.inc"\n',
            b'??=include "unreviewed.inc"\n',
            b'/* leading comment */ ??=include "unreviewed.inc"\n',
            b'#inc\\\nlude "unreviewed.inc"\n',
            b'#define DISGUISED /*\n*/ #include "unreviewed.inc"\n',
            b'#define DISGUISED #include "unreviewed.inc"\n',
            b'%\\\n:include "unreviewed.inc"\n',
            b'%??/\n:include "unreviewed.inc"\n',
        ) + tuple(
            whitespace + b'#include "unreviewed.inc"\n'
            for whitespace in (b" ", b"\t", b"\n", b"\v", b"\f", b"\r", b"\r\n", b" \t\v\f\r\n")
        )
        for directive in variants:
            for location in ("init.c", *self.names):
                with self.subTest(location=location, directive=directive):
                    path = self.root / location
                    raw = path.read_bytes()
                    path.write_bytes(raw + directive)
                    with self.assertRaises(build.StockRestoreBuildError):
                        build.read_restorer_source(self.source)
                    path.write_bytes(raw)
        self.assertEqual(self.source.read_bytes(), original)

    def test_forbidden_token_in_any_fragment_reaches_source_validation(self):
        for name in self.names:
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(self.parts[name] + b'const char *forbidden = "/bin/sh";\n')
                assembled = build.read_restorer_source(self.source)
                with self.assertRaisesRegex(build.StockRestoreBuildError, "forbidden token: /bin/sh"):
                    build.validate_restorer_source(assembled)
                path.write_bytes(self.parts[name])

    def test_build_validates_and_writes_the_same_single_assembled_snapshot(self):
        raw = build.read_restorer_source()
        stop = RuntimeError("stop before actual compiler or rootfs tools")

        def inspect_compile(arguments, label, *, env=None):
            self.assertEqual(label, "stock restorer PID 1 compilation")
            self.assertEqual(Path(arguments[-1]).read_bytes(), raw)
            self.assertEqual(Path(arguments[-1]).name, "init.c")
            raise stop

        with (
            mock.patch.object(build, "read_restorer_source", return_value=raw) as assemble,
            mock.patch.object(build, "validate_restorer_source", wraps=build.validate_restorer_source) as validate,
            mock.patch.object(build, "render_contract", return_value=b"/* synthetic contract */\n"),
            mock.patch.object(build, "_resolve_llvm_pair", return_value=(self.root / "clang", self.root / "ld.lld")),
            mock.patch.object(build, "_resolve", return_value=self.root / "unused-squashfs-tool"),
            mock.patch.object(build, "_run", side_effect=inspect_compile) as run,
        ):
            with self.assertRaises(RuntimeError) as caught:
                build.build_stock_restore_root(
                    images=mock.sentinel.images, mmc_module=b"synthetic", authorization=b"T" * 32,
                    output=self.root / "never-built.squashfs",
                )
        self.assertIs(caught.exception, stop)
        assemble.assert_called_once_with()
        validate.assert_called_once_with(raw)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / "never-built.squashfs").exists())

    def test_build_stops_before_tools_when_assembled_module_is_forbidden(self):
        (self.root / "runtime.inc").write_bytes(b'const char *forbidden = "/bin/sh";\n')
        raw = build.read_restorer_source(self.source)
        with (
            mock.patch.object(build, "read_restorer_source", return_value=raw),
            mock.patch.object(build, "render_contract") as contract,
            mock.patch.object(build, "_run") as run,
        ):
            with self.assertRaisesRegex(build.StockRestoreBuildError, "forbidden token"):
                build.build_stock_restore_root(
                    images=mock.sentinel.images, mmc_module=b"synthetic", authorization=b"T" * 32,
                    output=self.root / "never-built.squashfs",
                )
        contract.assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
