from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import local_build_bootstrap


HEAD = "a" * 40
LOCK_SHA256 = "b" * 64
IMAGE_ID = "sha256:" + "c" * 64


class LocalBuildBootstrapTests(unittest.TestCase):
    def test_builder_image_is_built_once_and_bound_to_immutable_id(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            build_root = Path(name) / "build"
            (root / "containers").mkdir(parents=True)
            (build_root / "cache/images").mkdir(parents=True)
            containerfile = root / "containers/thingino-builder-arm64.Containerfile"
            containerfile.write_text("FROM pinned\n", encoding="utf-8")
            with (
                mock.patch.object(
                    local_build_bootstrap.platform,
                    "machine",
                    return_value="arm64",
                ),
                mock.patch.object(
                    local_build_bootstrap,
                    "_docker_image_id",
                    side_effect=[None, IMAGE_ID],
                ) as inspect,
                mock.patch.object(local_build_bootstrap, "_run") as run,
            ):
                result = local_build_bootstrap._builder_image(
                    root=root,
                    build_root=build_root,
                    head=HEAD,
                    lock_sha256=LOCK_SHA256,
                )
            self.assertEqual(result["id"], IMAGE_ID)
            self.assertEqual(inspect.call_count, 2)
            run.assert_called_once()
            receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["image_id"], IMAGE_ID)
            self.assertEqual(receipt["project_head"], HEAD)
            self.assertEqual(receipt["sources_lock_sha256"], LOCK_SHA256)

            with (
                mock.patch.object(
                    local_build_bootstrap.platform,
                    "machine",
                    return_value="arm64",
                ),
                mock.patch.object(
                    local_build_bootstrap,
                    "_docker_image_id",
                    return_value=IMAGE_ID,
                ),
                mock.patch.object(local_build_bootstrap, "_run") as rerun,
            ):
                repeated = local_build_bootstrap._builder_image(
                    root=root,
                    build_root=build_root,
                    head=HEAD,
                    lock_sha256=LOCK_SHA256,
                )
            self.assertEqual(result, repeated)
            rerun.assert_not_called()

    def test_builder_image_rejects_unowned_existing_tag(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            build_root = Path(name) / "build"
            (root / "containers").mkdir(parents=True)
            (build_root / "cache/images").mkdir(parents=True)
            (root / "containers/thingino-builder-arm64.Containerfile").write_text(
                "FROM pinned\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    local_build_bootstrap.platform,
                    "machine",
                    return_value="arm64",
                ),
                mock.patch.object(
                    local_build_bootstrap,
                    "_docker_image_id",
                    return_value=IMAGE_ID,
                ),
            ):
                with self.assertRaisesRegex(
                    local_build_bootstrap.LocalBuildBootstrapError,
                    "unowned builder image tag",
                ):
                    local_build_bootstrap._builder_image(
                        root=root,
                        build_root=build_root,
                        head=HEAD,
                        lock_sha256=LOCK_SHA256,
                    )

    def test_bootstrap_fetches_then_reverifies_the_exact_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            build_root = Path(name) / "build"
            (root / "sources.lock.json").parent.mkdir(parents=True)
            (root / "sources.lock.json").write_text("{}\n", encoding="utf-8")
            (build_root / "cache/sources").mkdir(parents=True)
            (build_root / "cache/images").mkdir(parents=True)
            workspace_manifest = build_root / "local-build-workspace.json"
            workspace_manifest.write_text(
                json.dumps({"sources_lock_sha256": LOCK_SHA256}),
                encoding="utf-8",
            )
            status = {
                "build_root": str(build_root),
                "current_head": HEAD,
                "ready_to_build": True,
                "safe_next_action": "prepare-local-build-inputs",
            }
            verification = {"source_date_epoch": 1234}
            with (
                mock.patch.object(
                    local_build_bootstrap,
                    "local_build_workspace_status",
                    return_value=status,
                ),
                mock.patch.object(
                    local_build_bootstrap,
                    "_project_root",
                    return_value=root,
                ),
                mock.patch.object(
                    local_build_bootstrap,
                    "load_source_lock_snapshot",
                    return_value=({"schema_version": 2}, LOCK_SHA256),
                ),
                mock.patch.object(
                    local_build_bootstrap.source_checkout,
                    "fetch_checkout",
                ) as fetch,
                mock.patch.object(
                    local_build_bootstrap.source_checkout,
                    "verify_checkout",
                    return_value=verification,
                ) as verify,
                mock.patch.object(
                    local_build_bootstrap,
                    "_builder_image",
                    return_value={"id": IMAGE_ID, "receipt": "receipt", "tag": "tag"},
                ),
            ):
                result = local_build_bootstrap.bootstrap_public_build_inputs(
                    build_root=build_root
                )
            self.assertTrue(result["checkout_created"])
            fetch.assert_called_once()
            verify.assert_called_once()
            self.assertEqual(result["source_date_epoch"], 1234)

    def test_bootstrap_stops_before_network_when_workspace_is_not_ready(self) -> None:
        status = {
            "ready_to_build": False,
            "safe_next_action": "free-build-volume-space",
        }
        with (
            mock.patch.object(
                local_build_bootstrap,
                "local_build_workspace_status",
                return_value=status,
            ),
            mock.patch.object(
                local_build_bootstrap.source_checkout,
                "fetch_checkout",
            ) as fetch,
        ):
            with self.assertRaisesRegex(
                local_build_bootstrap.LocalBuildBootstrapError,
                "free-build-volume-space",
            ):
                local_build_bootstrap.bootstrap_public_build_inputs(
                    build_root=Path("/external/build")
                )
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
