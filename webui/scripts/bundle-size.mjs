import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import { gzipSync } from "node:zlib";

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

let raw = 0;
let gzip = 0;
for (const path of await files("dist")) {
  const content = await readFile(path);
  const compressed = gzipSync(content, { level: 9, mtime: 0 });
  raw += content.length;
  gzip += compressed.length;
  console.log(`${relative("dist", path)}\t${content.length}\t${compressed.length}`);
}
console.log(`TOTAL\t${raw}\t${gzip}`);
