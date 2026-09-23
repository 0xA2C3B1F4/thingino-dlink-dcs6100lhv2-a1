import { createHash } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";

// The offline firmware builder supplies its pinned esbuild installation.
const { build } = createRequire(import.meta.url)(process.argv[2] || "esbuild");

await rm("dist", { recursive: true, force: true });
await mkdir("dist/assets", { recursive: true });

await build({
  bundle: true,
  entryNames: "app",
  entryPoints: ["src/main.ts"],
  format: "esm",
  legalComments: "none",
  minify: true,
  outdir: "dist/assets",
  sourcemap: false,
  target: ["es2020"],
});

const [javascript, stylesheet, sourceIndex] = await Promise.all([
  readFile("dist/assets/app.js"),
  readFile("dist/assets/app.css"),
  readFile("index.html", "utf8"),
]);
const assetVersion = createHash("sha256")
  .update(javascript)
  .update(stylesheet)
  .digest("hex")
  .slice(0, 16);
const versionedIndex = sourceIndex
  .replace("/assets/app.css", `/assets/app.css?v=${assetVersion}`)
  .replace("/assets/app.js", `/assets/app.js?v=${assetVersion}`);
await writeFile("dist/index.html", versionedIndex);
await cp("public", "dist", { recursive: true });
