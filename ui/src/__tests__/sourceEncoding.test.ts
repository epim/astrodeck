import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const problems: string[] = [];
function visit(dir: string) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) visit(path);
    else if (/\.(tsx?|css)$/.test(path)) {
      const bytes = readFileSync(path);
      const text = bytes.toString("utf8");
      if (bytes.includes(0)) problems.push(`${relative(root, path)}: literal NUL`);
      if (bytes.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])))
        problems.push(`${relative(root, path)}: UTF-8 BOM`);
      if (text.includes("\u00e2\u20ac"))
        problems.push(`${relative(root, path)}: UTF-8 decoded as Windows ANSI`);
    }
  }
}
visit(root);
assert.deepEqual(problems, [], problems.join("\n"));
console.log("PASS UI source encoding: no literal NUL, BOM, or smart-punctuation mojibake");
export const result = { passed: 1, failed: 0, total: 1 };
