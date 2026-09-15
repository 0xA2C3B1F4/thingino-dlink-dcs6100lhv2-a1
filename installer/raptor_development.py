"""Explicit admission of two private, host-reviewed full-media candidates.

This is not a general Raptor release profile. Only the exact personalized
candidates may use the retained kernel, and its persistent data must be kept.
"""

from __future__ import annotations

import hashlib

from .mtd3_split import derive_final_layout, final_kernel_command_line

PROFILE = "full-raptor-development-20260910-256k"
SYSTEM_SHA256 = "2e3e796f0839ac07a123301fa549ec4e9d6d06c95ac510e78c82711ec1f0c830"
SYSTEM_SIZE = 6328320
SERVICES_PROFILE = "full-raptor-development-20260911-services"
PROFILES = {
    PROFILE: (SYSTEM_SHA256, SYSTEM_SIZE),
    SERVICES_PROFILE: ("df7f104157bf80fbbc23e58f1817e012a5dff6482e4cab7ffa9e4d0604ee86d5", 6397952),
}
KERNEL_SHA256 = "0ee4ba541e7872af9dccce64b934c5ae8ad048622105b9eb9691d8eb9c521b98"
INSTALLER_KERNEL_SHA256 = "79d65eb99f67411ac6fe746966a2c1074bfb1878ec7c3383c4c8ff2e435bd42b"
MMC_SHA256 = "edb69b5279f978932c5e04e15b4b324493b5a0ff036a9e2b9995060c830e2239"
LINUX_CONFIG_SHA256 = "e94e1929062a70966e57178f968a9fd65f3cae1138830766e7616bd637032bc5"


def require_digest(raw: bytes, expected: str, label: str) -> None:
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"development Raptor {label} differs from the reviewed candidate")


def validate_candidate(*, profile: str, kernel: bytes, system: bytes, data_mode: str) -> str:
    if profile not in PROFILES:
        raise ValueError("unknown development media profile")
    if data_mode != "preserve":
        raise ValueError("development Raptor requires preserved data")
    system_sha256, system_size = PROFILES[profile]
    if len(system) != system_size:
        raise ValueError("development Raptor system size changed")
    require_digest(system, system_sha256, "system")
    require_digest(kernel, KERNEL_SHA256, "kernel")
    layout = derive_final_layout(len(system))
    command_line = final_kernel_command_line(layout)
    memory = "mem=42M@0x0 rmem=22M@0x2a00000"
    if command_line.count(memory) != 1:
        raise ValueError("default kernel profile changed; review the development profile")
    return command_line.replace(memory, memory + " ipv6.disable=1")


def validate_build_inputs(*, installer_kernel: bytes, linux_config: bytes, mmc: bytes) -> None:
    require_digest(installer_kernel, INSTALLER_KERNEL_SHA256, "installer kernel")
    require_digest(linux_config, LINUX_CONFIG_SHA256, "effective Linux config")
    require_digest(mmc, MMC_SHA256, "MMC module")
