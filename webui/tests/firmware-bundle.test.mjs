import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { verifyBundle } from "../scripts/firmware-bundle.mjs";

test("offline firmware build matches the reviewed current WebUI, including icons", async () => {
  const task = await mkdtemp(join(tmpdir(), "webui-firmware-"));
  const root = join(task, "dcs6100-webui");
  try {
    await mkdir(root);
    for (const name of ["src", "public", "scripts", "firmware-bundle.json", "index.html", "tsconfig.json"]) {
      await cp(new URL(`../${name}`, import.meta.url), join(root, name), { recursive: true });
    }
    // No node_modules in this source tree, just like the prepared firmware source.
    execFileSync(process.execPath, [join(root, "scripts/build.mjs"), process.env.WEBUI_ESBUILD_MODULE || createRequire(import.meta.url).resolve("esbuild")], { cwd: root });
    const dist = join(root, "dist");
    const bundle = await verifyBundle(dist);
    assert.equal(Object.keys(bundle.files).length, 12);
    assert.equal(Object.keys(bundle.files).filter(name => name.endsWith(".svg")).length, 7);
    const patch = await readFile(new URL("../../patches/thingino/0011-install-static-dlink-webui.patch", import.meta.url), "utf8");
    const install = patch.split("\n").filter(line => line.startsWith("+") && !line.startsWith("+++")).map(line => line.slice(1)).join("\n");
    const target = join(task, "target");
    const installed = join(target, "var/www");
    await mkdir(installed, { recursive: true });
    await writeFile(join(installed, "old.cgi"), "obsolete package output");
    execFileSync("sh", ["-eu", "-c", install], {
      env: { ...process.env, BR2_EXTERNAL: task, TARGET_DIR: target, SOURCE_DATE_EPOCH: "1786006608" },
    });
    assert.deepEqual(await verifyBundle(installed), bundle);
    for (const name of Object.keys(bundle.files)) {
      const info = await stat(join(installed, name));
      assert.equal(info.mode & 0o777, 0o644);
      assert.equal(info.mtimeMs, 1786006608000);
    }
    for (const name of ["assets/app.css", "assets/app.js", "index.html", "icons/video.svg", "icons/LICENSE.txt"]) {
      const path = join(dist, name);
      const original = await readFile(path);
      const changed = Buffer.from(original);
      changed[0] ^= 1; // Same length: the digest check, not just size, must fail.
      await writeFile(path, changed);
      await assert.rejects(verifyBundle(dist), /differs from the reviewed/);
      await rm(path);
      await assert.rejects(verifyBundle(dist), /differs from the reviewed/);
      await writeFile(path, original);
    }
    await writeFile(join(dist, "unexpected.txt"), "extra");
    await assert.rejects(verifyBundle(dist), /differs from the reviewed/);
    await rm(join(dist, "unexpected.txt"));
    await symlink("index.html", join(dist, "linked.html"));
    await assert.rejects(verifyBundle(dist), /WebUI symlink/);
  } finally {
    await rm(task, { recursive: true, force: true });
  }
});
