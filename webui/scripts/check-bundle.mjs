import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { verifyBundle } from "./firmware-bundle.mjs";

await verifyBundle("dist");

const forbidden = [
  { label: "extension CGI route", pattern: /\/x\/[^\s"']*\.cgi/i },
  { label: "historical gateway", pattern: /agent\.cgi/i },
  { label: "historical service name", pattern: /thingino[-_ ]agent/i },
  { label: "shell runtime", pattern: /(?:\/bin\/(?:ba)?sh|child_process|shell script)/i },
  { label: "innerHTML assignment", pattern: /\.innerHTML\s*=/ },
  { label: "outerHTML assignment", pattern: /\.outerHTML\s*=/ },
  { label: "insertAdjacentHTML sink", pattern: /\.insertAdjacentHTML\s*\(/ },
  { label: "document.write sink", pattern: /\bdocument\.write(?:ln)?\s*\(/ },
  { label: "eval sink", pattern: /\beval\s*\(/ },
  { label: "Function constructor", pattern: /\bnew\s+Function\s*\(/ },
];

async function files(root) {
  const entries = await readdir(root, { withFileTypes: true });
  const result = [];
  for (const entry of entries) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) result.push(...(await files(path)));
    else result.push(path);
  }
  return result;
}

let failures = 0;
for (const path of await files("dist")) {
  const content = await readFile(path, "utf8").catch(() => "");
  for (const rule of forbidden) {
    if (rule.pattern.test(content)) {
      console.error(`${path}: forbidden ${rule.label}`);
      failures += 1;
    }
  }
}

const [javascript, stylesheet, index] = await Promise.all([
  readFile("dist/assets/app.js"),
  readFile("dist/assets/app.css"),
  readFile("dist/index.html", "utf8"),
]);
const assetVersion = createHash("sha256")
  .update(javascript)
  .update(stylesheet)
  .digest("hex")
  .slice(0, 16);
for (const asset of ["app.css", "app.js"]) {
  if (!index.includes(`/assets/${asset}?v=${assetVersion}`)) {
    console.error(`dist/index.html: missing content-bound version for ${asset}`);
    failures += 1;
  }
}
if (/<script(?![^>]*\bsrc=)[^>]*>/i.test(index)) {
  console.error("dist/index.html: inline script violates the production CSP");
  failures += 1;
}

if (failures) process.exitCode = 1;
else console.log("production bundle route scan: pass");
