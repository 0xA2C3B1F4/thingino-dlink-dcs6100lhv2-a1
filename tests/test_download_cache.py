from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from installer import download_cache


def inventory_digest(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
    return digest.hexdigest()


class DownloadCacheTests(unittest.TestCase):
    EPOCH = 1786006608

    def bind_metadata(self, member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.mtime = self.EPOCH
        member.uid = 0
        member.gid = 0
        return member

    def test_validates_exact_archive_without_extracting_it(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "downloads.tar"
            payload = b"locked-source"
            with tarfile.open(archive_path, mode="w") as archive:
                directory = self.bind_metadata(tarfile.TarInfo("dl/package"))
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                archive.addfile(directory)
                file = self.bind_metadata(
                    tarfile.TarInfo("dl/package/source.tar.xz")
                )
                file.mode = 0o644
                file.size = len(payload)
                archive.addfile(file, io.BytesIO(payload))
            entries = [
                {"mode": 0o755, "path": "package", "type": "directory"},
                {
                    "mode": 0o644,
                    "path": "package/source.tar.xz",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "type": "file",
                },
            ]
            policy = root / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "archive_root": "dl",
                        "inventory_entries": len(entries),
                        "inventory_sha256": inventory_digest(entries),
                        "schema_version": 1,
                        "source_date_epoch": 1786006608,
                    }
                ),
                encoding="utf-8",
            )

            result = download_cache.validate_download_cache_archive(
                archive_path,
                policy_path=policy,
            )

            self.assertEqual(result["inventory_entries"], 2)
            self.assertEqual(result["inventory_sha256"], inventory_digest(entries))

    def test_rejects_hard_links_and_escaping_members(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            policy = root / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "archive_root": "dl",
                        "inventory_entries": 1,
                        "inventory_sha256": "0" * 64,
                        "schema_version": 1,
                        "source_date_epoch": 1786006608,
                    }
                ),
                encoding="utf-8",
            )
            for member, message in (
                (self.bind_metadata(tarfile.TarInfo("../escape")), "unsafe"),
                (
                    self.bind_metadata(tarfile.TarInfo("dl/hard-link")),
                    "hard links",
                ),
            ):
                archive_path = root / f"{message.replace(' ', '-')}.tar"
                if "hard" in message:
                    member.type = tarfile.LNKTYPE
                    member.linkname = "dl/target"
                with tarfile.open(archive_path, mode="w") as archive:
                    archive.addfile(member)
                with self.subTest(message=message), self.assertRaisesRegex(
                    download_cache.DownloadCacheError,
                    message,
                ):
                    download_cache.validate_download_cache_archive(
                        archive_path,
                        policy_path=policy,
                    )

    def test_can_forbid_unhashed_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "downloads.tar"
            member = self.bind_metadata(tarfile.TarInfo("dl/package/.git/HEAD"))
            member.size = 4
            with tarfile.open(archive_path, mode="w") as archive:
                archive.addfile(member, io.BytesIO(b"main"))
            policy = root / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "archive_root": "dl",
                        "inventory_entries": 1,
                        "inventory_sha256": "0" * 64,
                        "schema_version": 1,
                        "source_date_epoch": self.EPOCH,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                download_cache.DownloadCacheError,
                "Git metadata is forbidden",
            ):
                download_cache.validate_download_cache_archive(
                    archive_path,
                    policy_path=policy,
                    forbid_git_metadata=True,
                )


if __name__ == "__main__":
    unittest.main()
