import assert from "node:assert/strict";
import test from "node:test";
import { decodeFileList, decodeSdStatus } from "../src/api/decode";
import { routes } from "../src/api/routes";

test("recording listing preserves read-only entries and an explicit truncation flag", () => {
  const path = "/mnt/mmcblk0p1/raptor/stream1/2026-09-11/12-00-00.mp4";
  const value = decodeFileList({ directory: "/", parent: "/", breadcrumbs: [], truncated: true, entries: [{ name: "12-00-00.mp4", path, size: "16", perm: "0640", time: "0", is_dir: false, is_link: false, link_target: "", deletable: false }] });
  assert.equal(value.truncated, true);
  assert.equal(value.entries[0]?.deletable, false);
  assert.equal(new URL(routes.media.file(path, "play"), "https://fixture").searchParams.get("path"), path);
  assert.throws(() => decodeFileList({ directory: "/", parent: "/", breadcrumbs: [], entries: [], truncated: "yes" }));
});

test("Raptor SD status can report a present read-only mount with unknown usage and no formatter", () => {
  const value = decodeSdStatus({ ok: true, data: { has_sdcard: true, device: { name: "mmcblk0p1", node: "/dev/mmcblk0p1", vendor: "", model: "", size_bytes: null }, reports: { partitions_b64: "", mounts_b64: "" }, format: { supported: false, options: [], status: "idle", last_output_b64: "" }, filesystems: [{ device: "/dev/mmcblk0p1", mountpoint: "/mnt/mmcblk0p1", filesystem: "vfat", writable: false, total_kib: null, used_kib: null, free_kib: null }], messages: { format_warning: "Unsupported", not_present: "Absent" }, debug: { detection: "device-node" } } });
  assert.equal(value.data.device?.size_bytes, null);
  assert.equal(value.data.filesystems[0]?.total_kib, null);
  assert.equal(value.data.filesystems[0]?.writable, false);
  assert.equal(value.data.format.supported, false);
});


test("recording continuation is typed and encoded without changing legacy requests", () => {
  const value = { directory: "/", parent: "/", breadcrumbs: [], entries: [], truncated: true, next_cursor: "abcd-1" };
  assert.equal(decodeFileList(value).next_cursor, "abcd-1");
  assert.equal(decodeFileList({ ...value, next_cursor: null }).next_cursor, null);
  assert.throws(() => decodeFileList({ ...value, next_cursor: "../other" }));
  const query = new URL(routes.files.list("/mnt/a b", value.next_cursor), "https://fixture").searchParams;
  assert.equal(query.get("cd"), "/mnt/a b");
  assert.equal(query.get("cursor"), value.next_cursor);
  assert.equal(routes.files.list("/"), "/api/v1/files?cd=%2F");
});
