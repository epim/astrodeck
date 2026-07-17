// planLibrary.test.ts — pure tests for lib/planLibrary.ts (unified Plan panel
// helpers: saved-row metadata chip + saved/unsaved ownership cue). Inline-assert
// harness via `npx tsx` (idiom: planFile.test.ts).
//   Run:  npx tsx src/lib/__tests__/planLibrary.test.ts   (from ui/)
import { planRowSummary, planSavedCue } from "../planLibrary";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// -------------------------------------------------------------- planRowSummary
test("planRowSummary formats t/f/m with · separators", () => {
  assert(
    planRowSummary({ targets: 6, frames: 300, integration_min: 480 }) === "6t · 300f · 480m",
    "canonical row",
  );
});

test("planRowSummary rounds fractional integration minutes to whole", () => {
  assert(
    planRowSummary({ targets: 2, frames: 130, integration_min: 139.6 }) === "2t · 130f · 140m",
    "round up",
  );
  assert(
    planRowSummary({ targets: 1, frames: 10, integration_min: 20.4 }) === "1t · 10f · 20m",
    "round down",
  );
});

test("planRowSummary handles a zero/empty plan", () => {
  assert(
    planRowSummary({ targets: 0, frames: 0, integration_min: 0 }) === "0t · 0f · 0m",
    "all zeros",
  );
});

// --------------------------------------------------------------- planSavedCue
test("planSavedCue: dirty ALWAYS reads 'Unsaved changes' (warn), saved or not", () => {
  const a = planSavedCue(true, true);
  assert(a.label === "Unsaved changes" && a.tone === "warn", `dirty+saved: ${JSON.stringify(a)}`);
  const b = planSavedCue(true, false);
  assert(b.label === "Unsaved changes" && b.tone === "warn", `dirty+draft: ${JSON.stringify(b)}`);
});

test("planSavedCue: clean + tied to a library plan reads 'Saved' (dim)", () => {
  const c = planSavedCue(false, true);
  assert(c.label === "Saved" && c.tone === "dim", JSON.stringify(c));
});

test("planSavedCue: clean local draft (no library plan) reads 'Not saved yet' (dim)", () => {
  const d = planSavedCue(false, false);
  assert(d.label === "Not saved yet" && d.tone === "dim", JSON.stringify(d));
});

test("planSavedCue: viewer (canWrite=false) dirty cue keeps its label but drops to dim", () => {
  // No Save/Save-as for a viewer → never a warn-toned alarm with no action.
  const v = planSavedCue(true, true, false);
  assert(v.label === "Unsaved changes" && v.tone === "dim", `dirty+saved viewer: ${JSON.stringify(v)}`);
  const w = planSavedCue(true, false, false);
  assert(w.label === "Unsaved changes" && w.tone === "dim", `dirty+draft viewer: ${JSON.stringify(w)}`);
});

test("planSavedCue: canWrite only affects the dirty tone — clean cues unchanged for viewers", () => {
  const s = planSavedCue(false, true, false);
  assert(s.label === "Saved" && s.tone === "dim", `clean+saved viewer: ${JSON.stringify(s)}`);
  const n = planSavedCue(false, false, false);
  assert(n.label === "Not saved yet" && n.tone === "dim", `clean+draft viewer: ${JSON.stringify(n)}`);
  // default (omitted) canWrite stays the writer behavior
  const d = planSavedCue(true, false);
  assert(d.tone === "warn", `default canWrite=true keeps warn: ${JSON.stringify(d)}`);
});

console.log(`planLibrary.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
