"""Exact identity gate for user-acquired official D-Link inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = (
    ROOT / "profiles" / "dlink-dcs6100lhv2-a1" / "official-inputs.json"
)


class OfficialInputError(ValueError):
    """An input is unknown, changed, or unusable for the requested purpose."""


@dataclass(frozen=True, slots=True)
class OfficialInput:
    filename: str
    version: str
    format: str
    size: int
    sha256: str
    stock_recovery_components: tuple[str, ...]
    snapshot: bytes

    @property
    def can_restore_stock_kernel_rootfs(self) -> bool:
        return {"mtd1", "mtd2"}.issubset(self.stock_recovery_components)


def _load_catalog(path: Path) -> dict[str, object]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialInputError(f"cannot read input catalog: {exc}") from exc
    if catalog.get("schema_version") != 1:
        raise OfficialInputError("unsupported official-input catalog schema")
    target = catalog.get("target")
    if target != {"model": "DCS-6100LHV2", "hardware_revision": "A1"}:
        raise OfficialInputError("official-input catalog has the wrong target")
    return catalog


def validate_official_input(
    path: Path, *, catalog_path: Path = DEFAULT_CATALOG
) -> OfficialInput:
    if path.is_symlink() or not path.is_file():
        raise OfficialInputError(f"input is not a regular file: {path}")
    snapshot = path.read_bytes()
    digest = hashlib.sha256(snapshot).hexdigest()
    catalog = _load_catalog(catalog_path)
    candidates = [
        *catalog.get("approved_inputs", []),
        *catalog.get("approved_stock_sd_packages", []),
    ]
    if not isinstance(candidates, list):
        raise OfficialInputError("official-input catalog is malformed")

    for item in candidates:
        if not isinstance(item, dict):
            raise OfficialInputError("official-input catalog entry is malformed")
        if item.get("filename") != path.name:
            continue
        if item.get("size") != len(snapshot) or item.get("sha256") != digest:
            raise OfficialInputError(
                "official input name matches, but its exact size or digest changed"
            )
        components = item.get("stock_recovery_components")
        if not isinstance(components, list) or not all(
            isinstance(component, str) for component in components
        ):
            raise OfficialInputError("official-input recovery metadata is malformed")
        return OfficialInput(
            filename=path.name,
            version=str(item["version"]),
            format=str(item["format"]),
            size=len(snapshot),
            sha256=digest,
            stock_recovery_components=tuple(components),
            snapshot=snapshot,
        )

    raise OfficialInputError("input filename is not in the approved exact catalog")


def require_stock_kernel_rootfs(source: OfficialInput) -> None:
    if not source.can_restore_stock_kernel_rootfs:
        raise OfficialInputError(
            f"approved {source.version} {source.format} input contains no "
            "restorable stock mtd1/mtd2; stock recovery cannot be built from it"
        )
