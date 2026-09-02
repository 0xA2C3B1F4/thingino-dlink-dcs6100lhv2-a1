"""Build the fixed offline stage-1 root and its two-file SD install set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from installer.artifacts import (
    validate_squashfs,
    validate_uimage_command_line,
)
from installer.final_bundle import (
    derive_final_layout,
    final_kernel_command_line,
    validate_final_kernel_config,
)
from installer.layout import TARGET
from installer.media_closure import PROVEN_IDENTITIES
from installer.runtime_policy import (
    DROPBEAR_KEY_ONLY_ARGUMENTS,
    EMPTY_RESOLVER_POLICY,
    LOOPBACK_INTERFACE,
    NETWORK_DEFAULT_ROUTE_GUARD,
    NETWORK_INTERFACES,
    UDHCPC_NO_DEFAULT,
    WLAN_DHCP_NO_DEFAULT,
    normalize_ed25519_authorized_key,
)
from installer.sd_package import atomic_write, generate_bootstrap, parse_package, validate_bootstrap
from installer.stage2 import DATA_MODES
from installer.stage2 import FILENAME as STAGE2_FILENAME
from installer.stage2 import build_stage2, validate_stage2
from installer.stage1.squashfs_listing import listing_modes, listing_paths


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("init.c")
LINKER = Path(__file__).with_name("linker.ld")
BOOTSTRAP_FILENAME = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
FINAL_WEBUI_PATHS = {
    "var/www",
    "var/www/assets",
    "var/www/assets/app.css",
    "var/www/assets/app.js",
    "var/www/index.html",
    "var/www/manifest.webmanifest",
}
SOURCE_BUILT_PRUDYNT_MARKERS = (
    b"D-Link 1080p encoder receive path is ready",
    b"/run/prudynt-dlink-media.ready",
    b"/etc/sensor/os02g10-t31.bin",
    b"IMP_OSD_SetPoolSize",
    b"/snapshot",
    b"loopback ingress required",
)
SOURCE_BUILT_INIT_IDENTITIES = {
    "etc/init.d/F01datetime": (
        "f812fa67cb012aa754f5e4b7e4df00157403c049d4f853f0a0f0da77551d4a56"
    ),
    "etc/init.d/S06ircut": (
        "87d0af58ad1ac0024f1944553db57bdccd0d93b7bcbae2486814632220f2c469"
    ),
    "etc/init.d/S10daynightd": (
        "bb23cfcc0de379bfa1124301d57833925970a9b0add1636a901957727846ce91"
    ),
    "etc/init.d/S11modules": (
        "8c8abfde296e546c262cc4bbfb179d14f75bd321a14f99e68140f7f7d9129bb4"
    ),
    "etc/init.d/S31prudynt": (
        "491761453de5c8dc9eee17d1cf5775530f683ab5df4ca8894bd910882c51a861"
    ),
}
PRUDYNT_START = b'start-stop-daemon -S -b -m -p "$PIDFILE" -x "$DAEMON"\n'
PRUDYNT_RING_START = (
    b'PRUDYNT_RAPTOR_RING=1 start-stop-daemon -S -b -m -p "$PIDFILE" '
    b'-x "$DAEMON"\n'
)
RAPTOR_RWD_PROVENANCE = {
    "consumer": "raptor-rwd",
    "media_owner": "prudynt-only",
    "service": "S96rwd",
    "streams": [0, 1],
    "tls": "static-rwd",
}


def _valid_raptor_rwd_provenance(value: object) -> bool:
    if value == RAPTOR_RWD_PROVENANCE:
        return True
    if not isinstance(value, dict):
        return False
    expected_keys = set(RAPTOR_RWD_PROVENANCE) | {"source_provenance_sha256"}
    source_digest = value.get("source_provenance_sha256")
    return (
        set(value) == expected_keys
        and all(value.get(key) == expected for key, expected in RAPTOR_RWD_PROVENANCE.items())
        and isinstance(source_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", source_digest) is not None
    )


class Stage1BuildError(ValueError):
    """A tool or input violates the fixed offline installer build contract."""


FINAL_REQUIRED_PATHS = {
    "etc/dcs6100-personal-image.json",
    "etc/network/interfaces",
    "etc/network/interfaces.d/lo",
    "etc/init.d/S30dropbear",
    "etc/init.d/S40network",
    "etc/init.d/S05dlink-media-preconditions",
    "etc/init.d/S55installer-health",
    "etc/init.d/S31prudynt",
    "etc/init.d/S60uhttpd",
    "etc/init.d/S94onvif-httpd",
    "etc/init.d/S95thingino-control",
    "etc/init.d/dlink-media-acceptance",
    "etc/default/resolv.conf",
    "etc/network/interfaces.d/wlan0",
    "etc/init.d/S38wpa_supplicant",
    "etc/onvif.json",
    "etc/prudynt.json",
    "etc/shadow",
    "etc/thingino.json",
    "etc/thingino-api.key",
    "etc/dlink-media-closure.private.json",
    "etc/wpa_supplicant.conf",
    "init",
    "root/.ssh/authorized_keys",
    "usr/share/udhcpc/default.script",
    "usr/share/onvif",
    "usr/bin/uhttpd",
    "usr/sbin/onvif-httpd",
    "usr/sbin/thingino-controld",
    "usr/sbin/thingino-health",
    "usr/sbin/dlink-media-verify",
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/extra/8188fu.ko",
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/kernel/drivers/mmc/host/jzmmc_v12.ko",
} | FINAL_WEBUI_PATHS
FINAL_FORBIDDEN_PATHS = {
    "etc/init.d/S40wired-gateway",
    "etc/init.d/S41ifplugd",
    "etc/network/interfaces.d/eth0",
    "etc/profile.d/chpasswd",
    "etc/init.d/S97sysupgrade",
    "etc/init.d/S14volatile-config",
    "etc/init.d/S15thingino-button",
    "etc/init.d/S07dusk2dawn",
    "etc/init.d/S10mdev",
    "etc/init.d/S12prudynt",
    "etc/init.d/S13dlink-media-ready",
    "etc/init.d/S56prudynt",
    "etc/init.d/dlink-media-start",
    "etc/modules.d/gpio-userkeys",
    "etc/thingino-button.conf",
    "opt/dlink-media/closure",
    "opt/dlink-media/runtime",
    "usr/sbin/flash_eraseall",
    "usr/sbin/flashcp",
    "usr/sbin/fw_printenv",
    "usr/sbin/fw_setenv",
    "usr/sbin/sysupgrade",
    "usr/sbin/sysupgrade-stage2",
    "usr/sbin/thingino-agentd",
    "usr/sbin/thingino-agent-rs",
    "usr/sbin/thingino-agentctl",
    "usr/libexec/thingino-agent/lib.sh",
    "usr/libexec/thingino-agent/adapters/null.sh",
    "usr/libexec/thingino-agent/adapters/prudynt.sh",
    "usr/libexec/thingino-agent/listener",
    "usr/libexec/thingino-agent/tls-proxy",
    "etc/init.d/S95thingino-agent",
    "usr/libexec/thingino-webui",
    "usr/sbin/recordmgr",
    "usr/sbin/daynight",
    "usr/sbin/dusk2dawn",
    "etc/init.d/S95recordmgr",
    "etc/init.d/S48webui-config",
    "etc/init.d/S91mqttsub",
    "usr/sbin/mqtt-sub-dispatcher",
    "usr/sbin/telegram-cam-register",
    "usr/sbin/telegram-cam-agent",
}


def installer_kernel_command_line(system_size: int) -> str:
    layout = derive_final_layout(system_size)
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=42M@0x0",
            "rmem=22M@0x2a00000",
            "ipv6.disable=1",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            "mtdparts=jz_sfc:256k(boot)ro,1792k(kernel),"
            "4608k(bootstrap)ro,"
            f"{layout.system_span // 1024}k(system),"
            f"{layout.data_span // 1024}k(data),"
            "1536k(vendor)ro,256k(factory)ro",
        )
    )


def render_installer_kernel_fragment(system_size: int) -> bytes:
    """Render the fixed stage-1 kernel policy from the validated system size."""

    command_line = installer_kernel_command_line(system_size)
    fragment = "\n".join(
        (
            "# Generated for the fixed offline stage-1 installer.",
            "CONFIG_CMDLINE_BOOL=y",
            f"CONFIG_CMDLINE={json.dumps(command_line)}",
            "CONFIG_CMDLINE_OVERRIDE=y",
            "CONFIG_MTD=y",
            "CONFIG_MTD_CMDLINE_PARTS=y",
            "CONFIG_MTD_BLOCK=y",
            "CONFIG_MTD_JZ_SFC=y",
            "CONFIG_MTD_JZ_SFC_NOR=y",
            "CONFIG_SQUASHFS=y",
            "CONFIG_JFFS2_FS=y",
            "CONFIG_OVERLAYFS_FS=y",
            "",
        )
    ).encode("ascii")
    validate_final_kernel_config(fragment, command_line)
    return fragment


def _digest_array(name: str, value: bytes) -> str:
    body = ", ".join(f"0x{byte:02x}" for byte in value)
    return f"static const unsigned char {name}[32] = {{{body}}};"


def render_contract(
    *,
    stage2: bytes,
    final_kernel: bytes,
    system: bytes,
    mmc_module: bytes,
) -> bytes:
    parsed = validate_stage2(stage2)
    lines = [
        "#ifndef DCS6100_STAGE1_GENERATED_CONTRACT_H",
        "#define DCS6100_STAGE1_GENERATED_CONTRACT_H",
        f'#define STAGE2_PATH "/card/{STAGE2_FILENAME}"',
        f'#define BOOTSTRAP_PATH "/card/{BOOTSTRAP_FILENAME}"',
        f"#define STAGE2_EXPECTED_SIZE {len(stage2)}U",
        f"#define STAGE2_KERNEL_OFFSET {parsed.kernel_offset}U",
        f"#define STAGE2_SYSTEM_OFFSET {parsed.system_offset}U",
        f"#define FINAL_KERNEL_SIZE {len(final_kernel)}U",
        f"#define SYSTEM_SIZE {len(system)}U",
        f"#define SYSTEM_FLASH_SPAN {parsed.system_flash_span}U",
        f"#define DATA_FLASH_SPAN {parsed.data_flash_span}U",
        "#define DATA_ACTION_INITIALIZE 0U",
        "#define DATA_ACTION_PRESERVE 1U",
        "#define DATA_ACTION_FACTORY_RESET 2U",
        f"#define DATA_ACTION {DATA_MODES[parsed.data_mode]}U",
        '#define MMC_MODULE_PATH "/modules/jzmmc_v12.ko"',
        f"#define MMC_MODULE_SIZE {len(mmc_module)}U",
        _digest_array("STAGE2_EXPECTED_SHA256", hashlib.sha256(stage2).digest()),
        _digest_array("FINAL_KERNEL_SHA256", hashlib.sha256(final_kernel).digest()),
        _digest_array("SYSTEM_SHA256", hashlib.sha256(system).digest()),
        _digest_array("MMC_MODULE_SHA256", hashlib.sha256(mmc_module).digest()),
        "#endif",
        "",
    ]
    return "\n".join(lines).encode("ascii")


def validate_mmc_module(raw: bytes) -> None:
    if not 4096 <= len(raw) <= 65536:
        raise Stage1BuildError("MMC module size is outside its fixed stage-1 cap")
    if raw[:6] != b"\x7fELF\x01\x01" or raw[18:20] != b"\x08\x00":
        raise Stage1BuildError("MMC module is not little-endian ELF32/MIPS")
    for marker in (
        b"jzmmc_v12",
        b"cd_gpio_pin",
        b"3.10.14__isvp_swan_1.0__",
    ):
        if marker not in raw:
            raise Stage1BuildError("MMC module identity marker is missing")


def _resolve(executable: str | None, fallback: str) -> Path:
    candidate = executable or shutil.which(fallback)
    if not candidate:
        raise Stage1BuildError(f"required build tool is missing: {fallback}")
    path = Path(candidate).resolve()
    if not path.is_file():
        raise Stage1BuildError(f"build tool is not a regular file: {path}")
    return path


def _run(arguments: list[str], label: str, *, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode:
        tail = result.stderr.decode("utf-8", "replace")[-1200:]
        raise Stage1BuildError(f"{label} failed: {tail}")
    return result.stdout


def _listing_paths(listing: str) -> set[str]:
    return listing_paths(listing)


def _listing_modes(listing: str) -> dict[str, str]:
    return listing_modes(listing)


def validate_final_root_contents(
    *,
    paths: set[str],
    shadow: bytes,
    authorized_keys: bytes,
    dropbear_init: bytes,
    network_init: bytes,
    interfaces_config: bytes,
    loopback_config: bytes,
    wlan_config: bytes,
    dhcp_script: bytes,
    resolver_policy: bytes,
    wpa_config: bytes,
    prudynt_config: bytes,
    thingino_config: bytes,
    onvif_config: bytes,
) -> None:
    from .final_root_validation import validate_final_root_contents as _impl

    return _impl(sys.modules[__name__], paths=paths, shadow=shadow, authorized_keys=authorized_keys, dropbear_init=dropbear_init, network_init=network_init, interfaces_config=interfaces_config, loopback_config=loopback_config, wlan_config=wlan_config, dhcp_script=dhcp_script, resolver_policy=resolver_policy, wpa_config=wpa_config, prudynt_config=prudynt_config, thingino_config=thingino_config, onvif_config=onvif_config)


def validate_final_root(
    raw: bytes, *, unsquashfs: Path, temporary_parent: Path
) -> None:
    from .final_root_validation import validate_final_root as _impl

    return _impl(sys.modules[__name__], raw, unsquashfs=unsquashfs, temporary_parent=temporary_parent)


def build_stage1_root(
    *,
    contract: bytes,
    mmc_module: bytes,
    output: Path,
    clang: Path,
    lld: Path,
    mksquashfs: Path,
    unsquashfs: Path,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dcs6100-stage1-", dir=output.parent) as name:
        workspace = Path(name)
        source = workspace / "init.c"
        linker = workspace / "linker.ld"
        header = workspace / "generated_contract.h"
        executable = workspace / "init"
        root = workspace / "root"
        source.write_bytes(SOURCE.read_bytes())
        linker.write_bytes(LINKER.read_bytes())
        header.write_bytes(contract)
        env = {**os.environ, "PATH": f"{lld.parent}:{os.environ.get('PATH', '')}"}
        _run(
            [
                str(clang),
                "--target=mipsel-linux-gnu",
                "-march=mips32",
                "-mabi=32",
                "-G0",
                "-mno-abicalls",
                "-fno-pic",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-stack-protector",
                "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Oz",
                "-nostdlib",
                "-static",
                "-fuse-ld=lld",
                "-Wl,-e,_start",
                f"-Wl,-T,{linker}",
                "-Wl,--build-id=none",
                "-Wl,-z,max-page-size=4096",
                "-Wl,-s",
                "-I",
                str(workspace),
                "-o",
                str(executable),
                str(source),
            ],
            "stage-1 PID 1 compilation",
            env=env,
        )
        init = executable.read_bytes()
        if init[:4] != b"\x7fELF" or STAGE2_FILENAME.encode() not in init:
            raise Stage1BuildError("compiled stage-1 PID 1 lost its fixed identity")
        for token in (b"/bin/sh", b"telnet", b"dropbear", b"saveenv", b"fw_setenv"):
            if token in init:
                raise Stage1BuildError("compiled stage-1 PID 1 gained a forbidden interface")

        for relative in ("card", "dev", "modules", "newroot", "proc", "sbin", "sys"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "sbin/init").write_bytes(init)
        (root / "sbin/init").chmod(0o500)
        (root / "modules/jzmmc_v12.ko").write_bytes(mmc_module)
        (root / "modules/jzmmc_v12.ko").chmod(0o400)
        (root / "linuxrc").symlink_to("sbin/init")
        for path in root.iterdir():
            if path.is_dir():
                path.chmod(0o555)
        root.chmod(0o555)
        _run(
            [
                str(mksquashfs),
                str(root),
                str(output),
                "-comp",
                "xz",
                "-noappend",
                "-all-root",
                "-no-xattrs",
                "-no-progress",
                "-repro-time",
                "0",
            ],
            "stage-1 SquashFS build",
        )
        listing = _run(
            [str(unsquashfs), "-lln", str(output)],
            "stage-1 SquashFS inventory",
        ).decode("utf-8")
        expected = {
            "card",
            "dev",
            "linuxrc",
            "modules",
            "modules/jzmmc_v12.ko",
            "newroot",
            "proc",
            "sbin",
            "sbin/init",
            "sys",
        }
        if _listing_paths(listing) != expected or "linuxrc -> sbin/init" not in listing:
            raise Stage1BuildError("stage-1 SquashFS allowlist changed")
        if _run(
            [str(unsquashfs), "-cat", str(output), "sbin/init"],
            "stage-1 init extraction",
        ) != init:
            raise Stage1BuildError("stage-1 init changed inside SquashFS")
    raw = output.read_bytes()
    validate_squashfs(raw, partition_limit=TARGET.partition(2).size)
    return raw


def _build_install_set(
    *,
    installer_kernel: bytes,
    final_kernel: bytes,
    final_linux_config: bytes,
    system: bytes,
    mmc_module: bytes,
    output_dir: Path,
    clang: Path,
    lld: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    data_mode: str,
) -> dict[str, object]:
    os.chmod(output_dir, 0o700)
    validate_squashfs(system)
    validate_final_root(
        system, unsquashfs=unsquashfs, temporary_parent=output_dir.parent
    )
    layout = derive_final_layout(len(system))
    validate_uimage_command_line(
        installer_kernel,
        installer_kernel_command_line(len(system)),
        partition_limit=TARGET.partition(1).size,
        expected_entry=None,
    )
    final_command_line = final_kernel_command_line(layout)
    validate_uimage_command_line(
        final_kernel,
        final_command_line,
        partition_limit=TARGET.partition(1).size,
        expected_entry=None,
    )
    validate_final_kernel_config(final_linux_config, final_command_line)
    validate_mmc_module(mmc_module)
    stage2 = build_stage2(
        final_kernel=final_kernel,
        system_rootfs=system,
        data_mode=data_mode,
    )
    contract = render_contract(
        stage2=stage2,
        final_kernel=final_kernel,
        system=system,
        mmc_module=mmc_module,
    )
    stage1_root_path = output_dir / "stage1-bootstrap.squashfs"
    stage1_root = build_stage1_root(
        contract=contract,
        mmc_module=mmc_module,
        output=stage1_root_path,
        clang=clang,
        lld=lld,
        mksquashfs=mksquashfs,
        unsquashfs=unsquashfs,
    )
    bootstrap = generate_bootstrap(installer_kernel, stage1_root)
    validate_bootstrap(parse_package(bootstrap, require_project_header=True))
    bootstrap_path = output_dir / BOOTSTRAP_FILENAME
    stage2_path = output_dir / STAGE2_FILENAME
    atomic_write(bootstrap_path, bootstrap)
    atomic_write(stage2_path, stage2)
    manifest = {
        "schema_version": 2,
        "status": "host-built install set; generation does not authorize live use",
        "target": {"model": TARGET.model, "hardware_revision": TARGET.hardware_revision},
        "layout": {
            "abi": "dcs6100lhv2-a1-mtd3-split-v1",
            "parent_physical_mtd": 3,
            "parent_offset": TARGET.partition(3).offset,
            "parent_span": TARGET.partition(3).size,
            "system_offset": layout.system_offset,
            "system_span": layout.system_span,
            "data_offset": layout.data_offset,
            "data_span": layout.data_span,
            "preserved_physical_mtd": [0, 4, 5],
            "data_mode": data_mode,
        },
        "region_policy": {
            "system": {
                "filesystem": "squashfs",
                "write": "erase-write-readback",
                "sha256": hashlib.sha256(system).hexdigest(),
                "payload_size": len(system),
            },
            "data": {
                "filesystem": "jffs2",
                "initialize": "explicit-erased-region",
                "preserve": "before-and-after-complete-region-sha256",
                "factory_reset": "explicit-data-only-erase",
                "corrupt": "preserve-and-require-explicit-recovery",
            },
            "activation": {
                "region": "kernel-first-64KiB",
                "written_last": True,
            },
        },
        "stock_userdata_backup": {
            "filename": "STOCKM3.BIN",
            "size": TARGET.partition(3).size,
            "existing_file_policy": (
                "reuse only after exact current-NOR comparison and exact length"
            ),
            "privacy": "same-device private; never publish",
        },
        "interrupted_install_checkpoint": {
            "filename": "STOCKM3.OK",
            "size": 80,
            "magic": "DCS6RC01",
            "bindings": [
                "stock backup size and SHA-256",
                "exact stage-2 size and SHA-256",
            ],
            "policy": (
                "permits only re-running the same bounded final install after "
                "NOR diverges from the verified backup"
            ),
            "authentication": (
                "consistency and corruption gate; not authentication against "
                "malicious removable media"
            ),
            "stock_restore": False,
            "evidence": "host-modeled and host-built only",
        },
        "visible_status": {
            "evidence": "host-built and host-tested only",
            "green_gpio": 52,
            "red_gpio": 54,
            "active_low": True,
            "validation_and_backup": "green",
            "final_flash_writes": "green+red",
            "terminal_failure": "red",
            "verified_before_reboot": "green",
        },
        "inputs": {
            name: {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in (
                ("installer_kernel", installer_kernel),
                ("final_kernel", final_kernel),
                ("final_linux_config", final_linux_config),
                ("system_rootfs", system),
                ("mmc_module", mmc_module),
            )
        },
        "artifacts": {
            path.name: {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (bootstrap_path, stage2_path, stage1_root_path)
        },
        "boot_sequence": [
            "stock U-Boot flashes only mtd1 and mtd2 from the matching file",
            "host deactivates the verified matching filename before the stage-1 reboot",
            "stage 1 verifies THINGINO2.BIN and confirms its matching bootstrap is absent",
            "installer exports and verifies STOCKM3.BIN, or exactly verifies and reuses it, before final writes",
            "installer writes and reads back STOCKM3.OK before the first final write",
            "green+red LEDs identify the final flash-write phase",
            "installer verifies, writes, reads back, activates, and reboots",
            "permanent mtd2 bootstrap verifies and switch-roots into Thingino",
        ],
    }
    atomic_write(
        output_dir / "install-set.manifest.json",
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    )
    return manifest


def build_install_set(
    *,
    installer_kernel: bytes,
    final_kernel: bytes,
    final_linux_config: bytes,
    system: bytes,
    mmc_module: bytes,
    output_dir: Path,
    clang: Path,
    lld: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    data_mode: str = "initialize",
) -> dict[str, object]:
    if output_dir.exists():
        raise Stage1BuildError("refusing to reuse an install-set output directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    working = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        manifest = _build_install_set(
            installer_kernel=installer_kernel,
            final_kernel=final_kernel,
            final_linux_config=final_linux_config,
            system=system,
            mmc_module=mmc_module,
            output_dir=working,
            clang=clang,
            lld=lld,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
            data_mode=data_mode,
        )
        os.replace(working, output_dir)
    except BaseException:
        shutil.rmtree(working, ignore_errors=True)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer-kernel", type=Path, required=True)
    parser.add_argument("--final-kernel", type=Path, required=True)
    parser.add_argument("--final-linux-config", type=Path, required=True)
    parser.add_argument("--system-rootfs", type=Path, required=True)
    parser.add_argument("--mmc-module", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--clang")
    parser.add_argument("--lld")
    parser.add_argument("--mksquashfs")
    parser.add_argument("--unsquashfs")
    parser.add_argument(
        "--data-mode",
        choices=("initialize", "preserve", "factory-reset"),
        default="initialize",
    )
    arguments = parser.parse_args()
    try:
        manifest = build_install_set(
            installer_kernel=arguments.installer_kernel.read_bytes(),
            final_kernel=arguments.final_kernel.read_bytes(),
            final_linux_config=arguments.final_linux_config.read_bytes(),
            system=arguments.system_rootfs.read_bytes(),
            mmc_module=arguments.mmc_module.read_bytes(),
            output_dir=arguments.output_dir,
            clang=_resolve(arguments.clang, "clang"),
            lld=_resolve(arguments.lld, "ld.lld"),
            mksquashfs=_resolve(arguments.mksquashfs, "mksquashfs"),
            unsquashfs=_resolve(arguments.unsquashfs, "unsquashfs"),
            data_mode=arguments.data_mode,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        return 2
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
