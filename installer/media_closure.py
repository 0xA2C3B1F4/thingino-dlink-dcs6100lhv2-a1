"""Compatibility boundary for the retired private media-closure input.

The public build consumes the source-built Raptor support layer and never loads
a private runtime closure.  The small import surface remains only so retained
stage-1 and guided-CLI modules fail closed instead of reintroducing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn


# Retained only for the stage-1 module's import compatibility; no loader uses
# these identities in the Raptor export.
PROVEN_IDENTITIES = {
    "lib/libaudioProcess.so": "f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273",
    "lib/libimp.so": "14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe",
}


class MediaClosureError(ValueError):
    """A retired private media closure was requested."""


class MediaClosure:
    """Placeholder type retained for callers that only import the old name."""


def _retired() -> NoReturn:
    raise MediaClosureError(
        "private media closures are retired; use the source-built full Raptor image"
    )


def load_media_closure(directory: Path) -> MediaClosure:
    del directory
    _retired()

