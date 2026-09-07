"""Kernel contract for the permanent IPv4-only mtd2 installer bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from installer.artifacts import ArtifactError, UImageInfo, validate_uimage_command_line
from installer.collector.kernel import _config_values
from installer.layout import TARGET


FRAGMENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "profiles/dlink-dcs6100lhv2-a1/recovery-ap-kernel.fragment"
)


class RecoveryApKernelError(ValueError):
    """The RAM-only AP kernel violates its fixed artifact contract."""


@dataclass(frozen=True, slots=True)
class RecoveryApKernel:
    command_line: str
    image: UImageInfo


def recovery_ap_kernel_command_line() -> str:
    partitions = ",".join(
        f"{partition.size // 1024}k({partition.name})"
        f"{'ro' if partition.mtd != 3 else ''}"
        for partition in TARGET.partitions
    )
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=42M@0x0",
            "rmem=22M@0x2a00000",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            f"mtdparts=jz_sfc:{partitions}",
        )
    )


def validate_recovery_ap_kernel_config(raw: bytes) -> None:
    values = _config_values(raw)
    required = {
        "CONFIG_CMDLINE": f'"{recovery_ap_kernel_command_line()}"',
        "CONFIG_CMDLINE_OVERRIDE": "y",
        "CONFIG_GPIO_SYSFS": "y",
        "CONFIG_INET": "y",
        "CONFIG_IPV6": "y",
        "CONFIG_JFFS2_FS": "y",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MAC80211": "y",
        "CONFIG_MMC": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_NET": "y",
        "CONFIG_NETDEVICES": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_USB": "y",
        "CONFIG_USB_DWC2_HOST_ONLY": "y",
        "CONFIG_USB_JZ_DWC2": "y",
        "CONFIG_WIRELESS": "y",
    }
    for key, expected in required.items():
        if values.get(key) != expected:
            raise RecoveryApKernelError(
                f"effective recovery-AP Linux config does not enforce {key}={expected}"
            )


def render_recovery_ap_kernel_fragment() -> bytes:
    fragment = FRAGMENT_PATH.read_bytes()
    validate_recovery_ap_kernel_config(fragment)
    return fragment


def validate_recovery_ap_kernel(
    *, kernel: bytes, linux_config: bytes
) -> RecoveryApKernel:
    validate_recovery_ap_kernel_config(linux_config)
    command_line = recovery_ap_kernel_command_line()
    try:
        image = validate_uimage_command_line(
            kernel,
            command_line,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
    except ArtifactError as exc:
        raise RecoveryApKernelError(str(exc)) from exc
    return RecoveryApKernel(command_line=command_line, image=image)
