// planFile.test.ts — pure tests for lib/planFile.ts (plan-library import
// parsing). Inline-assert harness via `npx tsx`.
import { parsePlanFile } from "../planFile";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("parses a valid export envelope", () => {
  const raw = parsePlanFile('{"schema_version": 1, "name": "P", "plan": {"name": "P"}}');
  assert(raw.name === "P", "envelope fields readable");
});

test("rejects non-JSON with a friendly message", () => {
  let msg = "";
  try { parsePlanFile("not json {"); } catch (e) { msg = (e as Error).message; }
  assert(msg === "not a JSON file", `message: ${msg}`);
});

test("rejects arrays and primitives", () => {
  for (const bad of ["[1,2]", "42", '"str"', "null"]) {
    let threw = false;
    try { parsePlanFile(bad); } catch { threw = true; }
    assert(threw, `rejected: ${bad}`);
  }
});

console.log(`planFile.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
