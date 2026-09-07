import { readdir, readFile } from "node:fs/promises";

// Source-level contract checks cover the facade and every extracted domain.
export async function readConfigSource(): Promise<string> {
  const domains = (await readdir("src/pages/config")).filter((name) => name.endsWith(".ts")).sort();
  const paths = ["src/pages/config.ts", ...domains.map((name) => `src/pages/config/${name}`)];
  return (await Promise.all(paths.map((path) => readFile(path, "utf8")))).join("\n");
}
