// cspBootstrap.test.ts — OPEN-012: the SPA shell carries NO inline script, so
// the server CSP can drop script-src 'unsafe-inline'. The pre-paint anti-flash
// code lives in public/bootstrap.js and is loaded as an external blocking
// <script>. Inline-assert harness (mirrors base.test.ts); cwd is ui/.
import { readFileSync, existsSync } from "node:fs";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const html = readFileSync("index.html", "utf8");

test("index.html has no inline <script> body", () => {
  // every <script ...> open tag must carry a src= (no inline executable body)
  const openTags = html.match(/<script\b[^>]*>/g) ?? [];
  const inline = openTags.filter((t) => !/\bsrc=/.test(t));
  assert(inline.length === 0,
    `inline <script> tag(s) present: ${JSON.stringify(inline)}`);
});

test("index.html loads the external bootstrap", () => {
  assert(/<script\s+src="\.\/bootstrap\.js">/.test(html),
    "index.html does not load ./bootstrap.js as an external script");
});

test("public/bootstrap.js exists and holds the anti-flash IIFE", () => {
  assert(existsSync("public/bootstrap.js"), "public/bootstrap.js is missing");
  const js = readFileSync("public/bootstrap.js", "utf8");
  assert(js.includes("astrodeck-night") && js.includes("--screen-brightness"),
    "bootstrap.js is not the anti-flash bootstrap");
});

console.log(`cspBootstrap.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
