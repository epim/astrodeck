// profileFile.test.ts — pure tests for lib/profileFile.ts (profile-library
// import/export, F7 #5b). Inline-assert harness via `npx tsx` (planFile.test.ts
// precedent).
import { parseProfileFile, profileExportFilename } from "../profileFile";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("parses a valid profile file", () => {
  const raw = parseProfileFile('{"id":"a","name":"Backyard SCT","devices":[]}');
  assert(raw.name === "Backyard SCT", "name readable");
  assert(Array.isArray(raw.devices), "devices readable");
});

test("rejects non-JSON with a friendly message", () => {
  let msg = "";
  try { parseProfileFile("not json {"); } catch (e) { msg = (e as Error).message; }
  assert(msg === "not a JSON file", `message: ${msg}`);
});

test("rejects arrays, primitives, and objects missing the profile shape", () => {
  for (const bad of ["[1,2]", "42", '"str"', "null", "{}", '{"name":"x"}', '{"devices":[]}']) {
    let threw = false;
    try { parseProfileFile(bad); } catch { threw = true; }
    assert(threw, `rejected: ${bad}`);
  }
});

// -------------------------------------------------------- profileExportFilename
test("profileExportFilename mirrors planExportFilename's sanitize + suffix", () => {
  assert(profileExportFilename("Backyard SCT") === "Backyard SCT.astroprofile.json", "spaces kept");
  assert(profileExportFilename("Rig/2:Test") === "Rig_2_Test.astroprofile.json", "punctuation -> _");
});

test("profileExportFilename falls back to 'profile' for a blank name", () => {
  assert(profileExportFilename("") === "profile.astroprofile.json", "empty string");
  assert(profileExportFilename("   ") === "profile.astroprofile.json", "whitespace-only (trims to empty)");
});

console.log(`profileFile.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
