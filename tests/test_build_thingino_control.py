from __future__ import annotations

import os
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_thingino_control as builder


DATA_SCRATCH = Path(tempfile.gettempdir())
MODULE_PATH = Path(builder.__file__)


class BuildFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rust = root / "rust"
        self.rust_source = root / "rust-source"
        self.ingenic = root / "ingenic"
        self.project = root / "project"
        self.scratch = root / "scratch"
        self.outputs = root / "outputs"
        for path in (
            self.rust / "bin",
            self.rust / "lib/rustlib/src/rust/library/std",
            self.rust_source / "library/std",
            self.rust_source / "vendor/compiler_builtins",
            self.ingenic / "bin",
            self.ingenic / "mips-linux-gnu/libc/lib",
            self.project / "src",
            self.scratch,
            self.outputs,
        ):
            path.mkdir(parents=True, exist_ok=True)

        for executable in (
            self.rust / "bin/rustc",
            self.rust / "bin/cargo",
            self.ingenic / "bin/mips-linux-gnu-gcc",
            self.ingenic / "bin/mips-linux-gnu-strip",
            self.ingenic / "bin/mips-linux-gnu-ar",
            root / "readelf",
        ):
            executable.write_bytes(b"#!/bin/sh\nexit 99\n")
            executable.chmod(0o755)
        self.readelf = root / "readelf"

        anchors = {
            "Cargo.toml": b"[workspace]\n",
            "Cargo.lock": b"version = 4\n",
            "std/Cargo.toml": b"[package]\nname = \"std\"\n",
        }
        embedded = self.rust / "lib/rustlib/src/rust/library"
        supplied = self.rust_source / "library"
        for relative, content in anchors.items():
            (embedded / relative).write_bytes(content)
            (supplied / relative).write_bytes(content)
        (self.rust_source / "vendor/compiler_builtins/.cargo-checksum.json").write_text(
            '{"files":{},"package":"fixture"}\n', encoding="ascii"
        )

        (self.ingenic / "VERSION").write_text(
            "mips32r2 linux release 2.3.3_gcc4.7.2\n", encoding="ascii"
        )
        (self.ingenic / ".SOURCE").write_text(
            "glibc-2.16-2012.09/\n", encoding="ascii"
        )
        for name in ("ld-2.16.so", "libc-2.16.so", "libgcc_s.so.1"):
            (self.ingenic / "mips-linux-gnu/libc/lib" / name).write_bytes(
                b"sysroot-fixture"
            )

        self.manifest = self.project / "Cargo.toml"
        self.manifest.write_text(
            "[package]\n"
            'name = "thingino-control"\n'
            'version = "0.1.0"\n'
            'edition = "2024"\n\n'
            '[[bin]]\n'
            'name = "thingino-controld"\n'
            'path = "src/main.rs"\n',
            encoding="ascii",
        )
        (self.project / "Cargo.lock").write_text(
            "version = 4\n\n"
            "[[package]]\n"
            'name = "thingino-control"\n'
            'version = "0.1.0"\n',
            encoding="ascii",
        )
        (self.project / "src/main.rs").write_text("fn main() {}\n", encoding="ascii")
        self.output = self.outputs / "thingino-controld"
        self.inputs = builder.BuildInputs(
            rust_toolchain_root=self.rust,
            rustc_source_root=self.rust_source,
            ingenic_toolchain_root=self.ingenic,
            manifest=self.manifest,
            readelf=self.readelf,
            output=self.output,
            scratch_root=self.scratch,
            data_volume_root=self.root,
        )
        self.cargo_calls: list[tuple[list[str], dict[str, str]]] = []
        self.version_output = "Name: GLIBC_2.4\nName: GLIBC_2.16\n"

    def command(self, arguments, *, label, environment=None, cwd=None) -> bytes:
        argv = [str(argument) for argument in arguments]
        executable = Path(argv[0]).name
        if executable == "rustc" and argv[1:] == ["--version", "--verbose"]:
            return (
                b"rustc 1.95.0 (59807616e 2026-08-17)\n"
                b"release: 1.95.0\n"
                b"commit-hash: 59807616e1fa2540724bfbac14d7976d7e4a3860\n"
            )
        if executable == "rustc" and argv[1:] == ["--print", "sysroot"]:
            return f"{self.rust}\n".encode()
        if executable == "cargo" and argv[1:] == ["--version"]:
            return b"cargo 1.95.0 (fixture 2026-08-17)\n"
        if executable == "mips-linux-gnu-gcc" and argv[1:] == ["-dumpmachine"]:
            return b"mips-linux-gnu\n"
        if executable == "mips-linux-gnu-gcc" and argv[1:] == ["-dumpversion"]:
            return b"4.7.2\n"
        if executable == "cargo" and argv[1] == "build":
            assert environment is not None
            copied_environment = dict(environment)
            self.cargo_calls.append((argv, copied_environment))
            candidate = (
                Path(copied_environment["CARGO_TARGET_DIR"])
                / builder.TARGET
                / "release/thingino-controld"
            )
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"fixture-mips-control")
            candidate.chmod(0o755)
            return b""
        if executable == "mips-linux-gnu-strip":
            return b""
        if executable == "readelf":
            return self.readelf_output(argv[1]).encode()
        raise AssertionError(f"unexpected command for {label}: {argv}")

    def readelf_output(self, option: str) -> str:
        outputs = {
            "-hW": (
                "Class: ELF32\n"
                "Data: 2's complement, little endian\n"
                "Machine: MIPS R3000\n"
                "Flags: 0x70001007, noreorder, pic, cpic, o32, mips32r2\n"
            ),
            "-AW": (
                "ISA: MIPS32r2\n"
                "GPR size: 32\n"
                "FP ABI: Hard float (32-bit CPU, Any FPU)\n"
            ),
            "-lW": "[Requesting program interpreter: /lib/ld.so.1]\n",
            "-dW": (
                "0x00000001 (NEEDED) Shared library: [libgcc_s.so.1]\n"
                "0x00000001 (NEEDED) Shared library: [libc.so.6]\n"
            ),
            "-VW": self.version_output,
        }
        return outputs[option]


class ThinginoControlBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="thingino-control-builder-test-", dir=DATA_SCRATCH
        )
        self.fixture = BuildFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_build_is_offline_validated_and_atomically_published(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AWS_SECRET_ACCESS_KEY": "must-not-enter-build",
                    "HTTPS_PROXY": "http://must-not-enter-build.invalid",
                },
            ),
            mock.patch.object(builder, "_run", side_effect=self.fixture.command),
        ):
            result = builder.build(self.fixture.inputs)

        self.assertEqual(self.fixture.output.read_bytes(), b"fixture-mips-control")
        self.assertEqual(self.fixture.output.stat().st_mode & 0o777, 0o755)
        self.assertEqual(result["dt_needed"], ["libc.so.6", "libgcc_s.so.1"])
        self.assertEqual(result["features"], [])
        self.assertEqual(result["glibc_versions"], ["2.4", "2.16"])
        self.assertEqual(len(self.fixture.cargo_calls), 1)
        argv, environment = self.fixture.cargo_calls[0]
        self.assertEqual(
            argv[1:],
            [
                "build",
                "--manifest-path",
                str(self.fixture.manifest),
                "--target",
                builder.TARGET,
                "--release",
                "--locked",
                "--offline",
                "-Z",
                "build-std=std,panic_abort",
            ],
        )
        self.assertEqual(environment["CARGO_NET_OFFLINE"], "true")
        self.assertEqual(environment["CARGO_INCREMENTAL"], "0")
        self.assertEqual(environment["CARGO_BUILD_JOBS"], "2")
        self.assertEqual(environment["RUSTC_BOOTSTRAP"], "1")
        self.assertEqual(
            environment["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS"],
            "-Clink-arg=-fuse-ld=bfd",
        )
        self.assertEqual(environment["RUST_SRC_PATH"], str(self.fixture.rust_source / "library"))
        self.assertIn("-Ctarget-cpu=mips32r2", environment["RUSTFLAGS"])
        self.assertIn("-Cpanic=abort", environment["RUSTFLAGS"])
        self.assertIn("-Cforce-unwind-tables=no", environment["RUSTFLAGS"])
        self.assertIn("-mhard-float", MODULE_PATH.read_text(encoding="utf-8"))
        linker = next(self.fixture.scratch.glob(".thingino-control-build-*/mips-linker"), None)
        self.assertIsNone(linker, "successful builds must clean their temporary linker")
        self.assertIn(str(self.fixture.rust / "bin"), environment["PATH"])
        self.assertIn(str(self.fixture.ingenic / "bin"), environment["PATH"])
        for name in builder.NETWORK_ENVIRONMENT:
            self.assertNotIn(name, environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
        self.assertEqual(list(self.fixture.scratch.iterdir()), [])

    def test_newer_glibc_is_rejected_without_publishing_output(self) -> None:
        self.fixture.version_output = "Name: GLIBC_2.4\nName: GLIBC_2.17\n"
        with (
            mock.patch.object(builder, "_run", side_effect=self.fixture.command),
            self.assertRaisesRegex(builder.ControlBuildError, "newer than 2.16"),
        ):
            builder.build(self.fixture.inputs)
        self.assertFalse(self.fixture.output.exists())
        self.assertEqual(list(self.fixture.scratch.iterdir()), [])

    def test_manifest_symlink_is_rejected(self) -> None:
        real_manifest = self.fixture.project / "Cargo.real.toml"
        self.fixture.manifest.rename(real_manifest)
        self.fixture.manifest.symlink_to(real_manifest)
        with (
            mock.patch.object(builder, "_run", side_effect=self.fixture.command),
            self.assertRaisesRegex(builder.ControlBuildError, "cannot open Cargo manifest"),
        ):
            builder.build(self.fixture.inputs)
        self.assertFalse(self.fixture.output.exists())

    def test_wrong_rust_commit_is_rejected_before_build(self) -> None:
        def wrong_rust(arguments, *, label, environment=None, cwd=None):
            argv = [str(argument) for argument in arguments]
            if Path(argv[0]).name == "rustc" and argv[1:] == ["--version", "--verbose"]:
                return (
                    b"release: 1.95.0\n"
                    b"commit-hash: 0000000000000000000000000000000000000000\n"
                )
            return self.fixture.command(
                arguments, label=label, environment=environment, cwd=cwd
            )

        with (
            mock.patch.object(builder, "_run", side_effect=wrong_rust),
            self.assertRaisesRegex(builder.ControlBuildError, "commit identity"),
        ):
            builder.build(self.fixture.inputs)
        self.assertEqual(self.fixture.cargo_calls, [])
        self.assertFalse(self.fixture.output.exists())

    def test_build_script_is_rejected_to_keep_compile_inputs_inert(self) -> None:
        (self.fixture.project / "build.rs").write_text("fn main() {}\n", encoding="ascii")
        with (
            mock.patch.object(builder, "_run", side_effect=self.fixture.command),
            self.assertRaisesRegex(builder.ControlBuildError, "build scripts"),
        ):
            builder.build(self.fixture.inputs)
        self.assertEqual(self.fixture.cargo_calls, [])
        self.assertFalse(self.fixture.output.exists())

    def test_raptor_feature_is_explicit_and_recorded(self) -> None:
        with self.fixture.manifest.open("a") as stream:
            stream.write(
                '\n[features]\ndefault = ["raptor-backend"]\nraptor-backend = []\n'
            )
        with mock.patch.object(builder, "_run", side_effect=self.fixture.command):
            result = builder.build(replace(self.fixture.inputs, raptor_backend=True))
        self.assertEqual(result["features"], ["raptor-backend"])
        self.assertEqual(len(result["cargo_lock_sha256"]), 64)
        argv, _ = self.fixture.cargo_calls[0]
        self.assertEqual(argv[-3:], ["--no-default-features", "--features", "raptor-backend"])

    def test_raptor_feature_rejects_missing_or_implicitly_enabled_feature(self) -> None:
        original = self.fixture.manifest.read_text()
        for extra in [
            '',
            '\n[features]\ndefault = []\nraptor-backend = []\n',
            '\n[features]\ndefault = ["raptor-backend"]\nraptor-backend = ["other"]\n',
        ]:
            self.fixture.manifest.write_text(original + extra)
            with (mock.patch.object(builder, "_run", side_effect=self.fixture.command),
                  self.assertRaisesRegex(builder.ControlBuildError, "only default")):
                builder.build(replace(self.fixture.inputs, raptor_backend=True))
            self.assertFalse(self.fixture.output.exists())
        self.assertEqual(self.fixture.cargo_calls, [])

    def test_elf_contract_rejects_wrong_abi_interpreter_and_dependency(self) -> None:
        candidate = self.fixture.root / "candidate"
        candidate.write_bytes(b"fixture-mips-agent")
        candidate.chmod(0o755)
        cases = (
            ("-AW", "ISA: MIPS32r2\nGPR size: 32\nFP ABI: Soft float\n", "hard-float"),
            ("-lW", "[Requesting program interpreter: /lib/ld-uClibc.so.0]\n", "interpreter"),
            (
                "-dW",
                "(NEEDED) Shared library: [libc.so.6]\n"
                "(NEEDED) Shared library: [libstdc++.so.6]\n",
                "DT_NEEDED",
            ),
            ("-VW", "Version symbols section is empty\n", "no versioned glibc"),
        )
        for changed_option, changed_output, message in cases:
            with self.subTest(option=changed_option):
                def fake_run(arguments, *, label, environment=None, cwd=None):
                    option = str(arguments[1])
                    output = changed_output if option == changed_option else self.fixture.readelf_output(option)
                    return output.encode()

                with (
                    mock.patch.object(builder, "_run", side_effect=fake_run),
                    self.assertRaisesRegex(builder.ControlBuildError, message),
                ):
                    builder.validate_elf(candidate, readelf=self.fixture.readelf)

    def test_elf_contract_accepts_pinned_ingenic_readelf_output(self) -> None:
        candidate = self.fixture.root / "candidate"
        candidate.write_bytes(b"fixture-mips-control")
        candidate.chmod(0o755)

        def pinned_readelf(arguments, *, label, environment=None, cwd=None):
            option = str(arguments[1])
            if option == "-AW":
                return b"Tag_GNU_MIPS_ABI_FP: Hard float (double precision)\n"
            return self.fixture.readelf_output(option).encode()

        with mock.patch.object(builder, "_run", side_effect=pinned_readelf):
            evidence = builder.validate_elf(candidate, readelf=self.fixture.readelf)
        self.assertEqual(evidence["size"], len(b"fixture-mips-control"))

    def test_elf_contract_rejects_legacy_output_without_mips32r2_flag(self) -> None:
        candidate = self.fixture.root / "candidate"
        candidate.write_bytes(b"fixture-mips-control")
        candidate.chmod(0o755)

        def wrong_isa(arguments, *, label, environment=None, cwd=None):
            option = str(arguments[1])
            if option == "-hW":
                return (
                    b"Class: ELF32\n"
                    b"Data: 2's complement, little endian\n"
                    b"Machine: MIPS R3000\n"
                    b"Flags: 0x50001007, noreorder, pic, cpic, o32, mips32\n"
                )
            if option == "-AW":
                return b"Tag_GNU_MIPS_ABI_FP: Hard float (double precision)\n"
            return self.fixture.readelf_output(option).encode()

        with (
            mock.patch.object(builder, "_run", side_effect=wrong_isa),
            self.assertRaisesRegex(builder.ControlBuildError, "MIPS32r2"),
        ):
            builder.validate_elf(candidate, readelf=self.fixture.readelf)

    def test_binary_size_limit_accepts_boundary_and_reports_oversize(self) -> None:
        candidate = self.fixture.root / "bounded-candidate"
        candidate.write_bytes(b"x" * builder.MAX_BINARY_INPUT)
        with mock.patch.object(builder, "_run", side_effect=self.fixture.command):
            evidence = builder.validate_elf(candidate, readelf=self.fixture.readelf)
            self.assertEqual(evidence["size"], builder.MAX_BINARY_INPUT)
            with candidate.open("ab") as stream:
                stream.write(b"x")
            with self.assertRaisesRegex(
                builder.ControlBuildError,
                rf"built Thingino Control exceeds size limit: "
                rf"{builder.MAX_BINARY_INPUT + 1} bytes; maximum {builder.MAX_BINARY_INPUT} bytes",
            ):
                builder.validate_elf(candidate, readelf=self.fixture.readelf)

    def test_output_must_be_outside_repository_and_on_approved_volume(self) -> None:
        inside = builder.BuildInputs(
            **{
                **self.fixture.inputs.__dict__,
                "output": builder.ROOT / "forbidden-agent-output",
                "data_volume_root": Path("/"),
            }
        )
        with self.assertRaisesRegex(builder.ControlBuildError, "outside the repository"):
            builder._validate_data_destination(inside)

        off_volume = builder.BuildInputs(
            **{
                **self.fixture.inputs.__dict__,
                "scratch_root": Path("/var/tmp"),
            }
        )
        with self.assertRaisesRegex(builder.ControlBuildError, "scratch root must be"):
            builder._validate_data_destination(off_volume)

        explicit_volume = builder.BuildInputs(
            **{
                **self.fixture.inputs.__dict__,
                "data_volume_root": self.fixture.root,
            }
        )
        builder._validate_data_destination(explicit_volume)

    def test_atomic_publication_never_overwrites_a_collision(self) -> None:
        self.fixture.output.write_bytes(b"existing-output")
        with self.assertRaisesRegex(builder.ControlBuildError, "appeared during publication"):
            builder._publish_atomic(b"new-output", self.fixture.output)
        self.assertEqual(self.fixture.output.read_bytes(), b"existing-output")
        self.assertEqual(
            list(self.fixture.outputs.glob(f".{self.fixture.output.name}.*")), []
        )


if __name__ == "__main__":
    unittest.main()
