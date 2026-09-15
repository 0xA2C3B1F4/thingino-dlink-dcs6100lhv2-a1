"""Canonical physical-write declaration for model-universal installation."""

from __future__ import annotations

from .layout import ERASE_BLOCK_SIZE, TARGET
from .mtd3_split import DATA_FLASH_SPAN, SYSTEM_FLASH_SPAN


def universal_physical_write_policy(data_action: str | None = None) -> dict[str, object]:
    """Return the universal boundary, specialized when an action is already bound.

    The action-neutral final bundle retains the maximum initialize declaration.
    Install sets and camera authorizations bind the selected action explicitly.
    """

    if data_action not in {None, "initialize", "preserve"}:
        raise ValueError("universal physical write policy data action is invalid")
    provisioning = {
        "action": "erase-write-readback",
        "offset_in_physical_mtd": SYSTEM_FLASH_SPAN,
        "physical_mtd": 3,
        "size": DATA_FLASH_SPAN,
    }
    if data_action == "preserve":
        provisioning.update(
            {
                "action": "preserve",
                "verification": "before-and-after-complete-region-sha256",
            }
        )

    return {
        "activation": {
            "physical_mtd": 1,
            "offset": 0,
            "size": ERASE_BLOCK_SIZE,
            "written_last": True,
        },
        "preserved_physical_mtd": [0, 4, 5],
        "provisioning": provisioning,
        "stage1_final_write_physical_mtd": [1, 3],
        "stock_bootstrap_write_physical_mtd": [1, 2],
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
        },
    }
