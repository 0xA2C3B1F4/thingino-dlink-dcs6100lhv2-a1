"""Pure member-policy validation for locked local-build archives."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import PurePosixPath
from typing import Callable


ErrorFactory = Callable[[str], Exception]


def normalize_parts(path: PurePosixPath) -> tuple[str, ...] | None:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return tuple(parts)


def validate_tar_members(
    bundle: tarfile.TarFile,
    *,
    expected_top: str,
    max_members: int,
    max_extracted_bytes: int,
    reserved_root_name: str,
    error: ErrorFactory,
    included_roots: frozenset[str] | None = None,
    allowed_casefold_aliases: frozenset[
        frozenset[tuple[str, ...]]
    ] = frozenset(),
    enforce_casefold_uniqueness: bool = True,
) -> None:
    allowed_alias_names: set[tuple[str, ...]] = set()
    for alias in allowed_casefold_aliases:
        if (
            len(alias) != 2
            or len(
                {
                    tuple(part.casefold() for part in name)
                    for name in alias
                }
            )
            != 1
        ):
            raise error("archive case alias policy is invalid")
        allowed_alias_names.update(alias)
    total = 0
    count = 0
    names: set[tuple[str, ...]] = set()
    folded_names: dict[tuple[str, ...], tarfile.TarInfo] = {}
    alias_payloads: dict[tuple[str, ...], bytes] = {}
    kinds: dict[tuple[str, ...], str] = {}
    required_directories: set[tuple[str, ...]] = set()
    for member in bundle:
        count += 1
        if count > max_members:
            raise error("archive has too many members")
        path = PurePosixPath(member.name)
        normalized = normalize_parts(path)
        if (
            path.is_absolute()
            or normalized is None
            or not normalized
            or normalized[0] != expected_top
            or tuple(path.parts) != normalized
        ):
            raise error("archive contains an unsafe path")
        if normalized == (expected_top, reserved_root_name):
            raise error("archive contains the reserved receipt path")
        folded = tuple(part.casefold() for part in normalized)
        if normalized in names:
            raise error("archive contains a duplicate path")
        selected = (
            included_roots is None
            or len(normalized) == 1
            or normalized[1] in included_roots
        )
        if normalized in allowed_alias_names and member.isreg():
            try:
                payload = bundle.extractfile(member)
                if payload is None:
                    raise OSError
                alias_payloads[normalized] = hashlib.sha256(payload.read()).digest()
            except (OSError, tarfile.TarError) as exc:
                raise error("archive case alias cannot be verified") from exc
        previous = (
            folded_names.get(folded)
            if selected and enforce_casefold_uniqueness
            else None
        )
        if previous is not None:
            previous_normalized = normalize_parts(PurePosixPath(previous.name))
            alias = (
                frozenset({previous_normalized, normalized})
                if previous_normalized is not None
                else frozenset()
            )
            if (
                alias not in allowed_casefold_aliases
                or not previous.isreg()
                or not member.isreg()
                or previous.size != member.size
                or previous.mode != member.mode
                or alias_payloads.get(previous_normalized)
                != alias_payloads.get(normalized)
            ):
                raise error("archive contains a duplicate path")
        for length in range(1, len(normalized)):
            if kinds.get(normalized[:length]) not in {None, "directory"}:
                raise error("archive contains a parent type collision")
            required_directories.add(normalized[:length])
        if normalized in required_directories and not member.isdir():
            raise error("archive contains a parent type collision")
        names.add(normalized)
        if selected and enforce_casefold_uniqueness and previous is None:
            folded_names[folded] = member
        if member.isreg():
            kind = "file"
            if member.size < 0:
                raise error("archive member size is invalid")
            total += member.size
            if total > max_extracted_bytes:
                raise error("archive expands beyond its size policy")
        elif member.isdir():
            kind = "directory"
        elif member.issym():
            kind = "link"
            target = normalize_parts(path.parent / member.linkname)
            if target is None or not target or target[0] != expected_top:
                raise error("archive symlink escapes its root")
        else:
            raise error("archive contains an unsupported member type")
        kinds[normalized] = kind
