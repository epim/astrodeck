// Unit tests for the NOV-9 in-app help content cores: the glossary (help.ts,
// T1) and the failure→diagnosis map + troubleshooting page content
// (lib/troubleshoot.ts, T2). Both are pure, so this is the same tiny
// inline-assert harness as eta.test.ts — no jsdom/vitest wired into this UI.
// Run directly with a TS-aware runner:
//   npx tsx src/lib/__tests__/troubleshoot.test.ts

import { diagnoseFailure, TROUBLESHOOTING, getTroubleshootEntry } from "../troubleshoot";
import { HELP, helpText } from "../../help";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ==================================================================== T1
// help.ts — the 7 new beginner keys + regression on the existing ones.
const NEW_KEYS = ["gain", "exposure", "darks", "flats", "bias", "plateSolve", "guiding"] as const;
test("help: new beginner keys are present + non-empty", () => {
  for (const k of NEW_KEYS) {
    assert(typeof (HELP as Record<string, string>)[k] === "string", `${k} missing`);
    assert((HELP as Record<string, string>)[k].trim().length > 20, `${k} too short`);
  }
});
test("help: existing keys survive (regression)", () => {
  for (const k of ["offset", "hfr", "binning", "coolTo"]) assert(!!helpText(k), `${k} lost`);
});
test("help: unknown key → undefined", () => { eq(helpText("nope"), undefined); });

// ==================================================================== T2
// --- diagnoseFailure keyword routing
test("diagnose: plate-solve → wont-solve", () => {
  const d = diagnoseFailure("Plate solve failed after 3 attempts");
  eq(d.topic, "wont-solve"); assert(d.cause.length > 0 && d.fix.length > 0, "copy");
});
test("diagnose: camera → camera-offline", () => eq(diagnoseFailure("Camera not responding").topic, "camera-offline"));
test("diagnose: cool → cooler-stuck", () => eq(diagnoseFailure("Cooler failed to reach -10C").topic, "cooler-stuck"));
test("diagnose: focus → autofocus-failed", () => eq(diagnoseFailure("Autofocus failed").topic, "autofocus-failed"));
test("diagnose: nina → nina-error", () => eq(diagnoseFailure("NINA HTTP 500").topic, "nina-error"));
test("diagnose: case-insensitive", () => eq(diagnoseFailure("MOUNT SLEW ABORTED").topic, "mount-move-failed"));
// --- ordering: guid beats camera when both present
test("diagnose: guiding wins over camera when both present", () =>
  eq(diagnoseFailure("guiding lost, camera busy").topic, "guiding-lost"));
// --- fallback
test("diagnose: unknown → generic, topic null", () => {
  const d = diagnoseFailure("totally novel xyz"); eq(d.topic, null); assert(d.title.length > 0, "title");
});
test("diagnose: empty/undefined → generic", () => {
  eq(diagnoseFailure("").topic, null); eq(diagnoseFailure(undefined).topic, null);
});
// --- referential integrity: every non-null topic a rule can emit has a page entry
test("integrity: every diagnosed topic has a TROUBLESHOOTING entry", () => {
  const probes = ["plate solve", "cooler", "guiding", "camera", "slew", "mount", "focus", "nina"];
  for (const p of probes) {
    const t = diagnoseFailure(p).topic;
    if (t) assert(!!getTroubleshootEntry(t), `no entry for ${t}`);
  }
});
// --- page content shape
test("entries: unique topics, non-empty symptom/cause, >=1 step", () => {
  const seen = new Set<string>();
  for (const e of TROUBLESHOOTING) {
    assert(!seen.has(e.topic), `dup ${e.topic}`); seen.add(e.topic);
    assert(e.symptom.length > 0 && e.cause.length > 0 && e.steps.length >= 1, e.topic);
  }
});
test("entries: seeAlso keys all resolve in HELP", () => {
  for (const e of TROUBLESHOOTING) for (const k of e.seeAlso ?? [])
    assert(!!helpText(k), `seeAlso ${k} missing from HELP`);
});
test("getTroubleshootEntry: null/undefined → undefined", () => {
  eq(getTroubleshootEntry(null), undefined); eq(getTroubleshootEntry(undefined), undefined);
});
// --- privacy guard (Global Constraints)
test("privacy: no real coords/label in help content", () => {
  const blob = JSON.stringify(HELP) + JSON.stringify(TROUBLESHOOTING);
  for (const bad of ["37.348", "121.801", "My Backyard"]) assert(!blob.includes(bad), `leaked ${bad}`);
});

// ---------------------------------------------------------------- summary
const total = passed + failed;
console.log(`\ntroubleshoot.test: ${passed}/${total} passed`);
if (failures.length) {
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
