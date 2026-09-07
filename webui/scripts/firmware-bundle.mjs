import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export async function inspectBundle(root) {
  const files = {};
  async function visit(relative = "") {
    for (const entry of await readdir(join(root, relative), { withFileTypes: true })) {
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      assert(!entry.isSymbolicLink(), `WebUI symlink: ${name}`);
      if (entry.isDirectory()) await visit(name);
      else {
        assert(entry.isFile(), `WebUI non-regular file: ${name}`);
        const bytes = await readFile(join(root, name));
        files[name] = { size: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
      }
    }
  }
  await visit();
  return {
    contract: "dlink-static-webui-v1",
    total_size: Object.values(files).reduce((total, file) => total + file.size, 0),
    files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => a.localeCompare(b, "en"))),
  };
}

export async function verifyBundle(root) {
  const expected = JSON.parse(await readFile(new URL("../firmware-bundle.json", import.meta.url), "utf8"));
  const actual = await inspectBundle(root);
  assert.deepEqual(actual, expected, "WebUI firmware bundle differs from the reviewed firmware-bundle.json");
  return actual;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  console.log(JSON.stringify(await verifyBundle(process.argv[2] || "dist")));
}
