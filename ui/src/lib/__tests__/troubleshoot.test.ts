// Unit tests for the NOV-9 in-app help content cores: the glossary (help.ts,
// T1) and the failure→diagnosis map + troubleshooting page content
// (lib/troubleshoot.ts, T2). Both are pure, so this is the same tiny
// inline-assert harness as eta.test.ts — no jsdom/vitest wired into this UI.
// Run directly with a TS-aware runner:
//   npx tsx src/lib/__tests__/troubleshoot.test.ts

import {
  diagnoseFailure, isUserAbort, runFailureLog, TROUBLESHOOTING, getTroubleshootEntry,
} from "../troubleshoot";
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
// ==================================================== UX-2026-07-26 #23
// A run the operator held ABORT to stop is not a fault, and the failure card
// must not quote a PREVIOUS run's log lines back as if they explained it.
test("abort: user hold-to-abort → no fault, no advisory, no topic", () => {
  const d = diagnoseFailure("sequence aborted", {
    state: "aborted", framesDone: 15, framesTotal: 18 });
  assert(d.userInitiated === true, "flagged user-initiated");
  assert(d.cause.includes("15/18 frames"), `frame counts in copy: ${d.cause}`);
  eq(d.fix, "");            // nothing to fix
  eq(d.topic, null);        // no wild-goose Help deep-link
  assert(!/interrupted|couldn't continue/i.test(d.cause), "no invented fault");
});
test("abort: user abort with no progress block still reads as deliberate", () => {
  const d = diagnoseFailure("sequence aborted", { state: "aborted" });
  assert(d.userInitiated === true, "flagged");
  assert(!/\d+\s*\/\s*\d+|\bafter \d+\b/.test(d.cause), `no fabricated counts: ${d.cause}`);
});
test("abort: empty detail on an aborted run is still the user's abort", () =>
  assert(diagnoseFailure(undefined, { state: "aborted" }).userInitiated === true, "flagged"));
test("abort: UNSAFE teardown is a fault, not a user abort", () => {
  const d = diagnoseFailure("clouds: safety monitor unsafe",
    { state: "aborted", endReason: "unsafe" });
  assert(!d.userInitiated, "not user-initiated");
  assert(d.fix.length > 0, "keeps an advisory");
});
test("abort: a fault detail on an aborted run keeps its diagnosis", () => {
  const d = diagnoseFailure("guiding lost", { state: "aborted" });
  assert(!d.userInitiated, "not user-initiated");
  eq(d.topic, "guiding-lost");
});
// The engine never CLEARS end_reason at run start, so last night's terminal
// reason can still be sitting on tonight's abort. Only a reason that describes
// an ENGINE-initiated abort may veto; dawn_cutoff/cooling_skip/quality all end
// state="complete" and are therefore always stale here.
test("abort: a stale non-fault end_reason does not un-do a user abort", () => {
  for (const stale of ["dawn_cutoff", "cooling_skip", "quality", "complete"]) {
    const d = diagnoseFailure("sequence aborted", {
      state: "aborted", endReason: stale, framesDone: 4, framesTotal: 9 });
    assert(d.userInitiated === true, `stale ${stale} must not invent a fault`);
    assert(d.cause.includes("4/9 frames"), `frame counts kept for ${stale}`);
  }
});
test("abort: state error is never a user abort", () =>
  assert(!diagnoseFailure("sequence aborted", { state: "error" }).userInitiated, "error"));
test("abort: no context → unchanged legacy behaviour (generic)", () => {
  const d = diagnoseFailure("sequence aborted");
  assert(!d.userInitiated, "no ctx, no claim");
  assert(d.fix.length > 0, "generic advisory kept");
});
test("isUserAbort: exported predicate agrees with the diagnosis", () => {
  assert(isUserAbort("sequence aborted", { state: "aborted" }), "user");
  assert(!isUserAbort("sequence aborted", {}), "no state");
  assert(!isUserAbort("disk full", { state: "aborted" }), "fault detail");
});

// --- runFailureLog: only THIS run's lines, only error/warning
const line = (ts: number, level: string, message: string) =>
  ({ type: "log", ts, data: { level, message, source: "sequence" } });
test("runFailureLog: drops lines from before the run started", () => {
  const logs = [line(100, "error", "old run cloud alert"),
                line(150, "warning", "old run guide loss"),
                line(210, "error", "this run: camera dropped")];
  const out = runFailureLog(logs, 200);
  eq(out.length, 1); eq(out[0].data.message, "this run: camera dropped");
});
test("runFailureLog: unknown run start → no excerpt at all", () =>
  eq(runFailureLog([line(100, "error", "whose line is this?")], null).length, 0));
test("runFailureLog: info lines never quoted; tail capped", () => {
  const logs = [line(210, "info", "frame 1 saved"),
                ...[1, 2, 3, 4, 5, 6].map((i) => line(210 + i, "warning", `w${i}`))];
  const out = runFailureLog(logs, 200);
  eq(out.length, 5); eq(out[0].data.message, "w2"); eq(out[4].data.message, "w6");
});
test("runFailureLog: boundary ts == started_at is IN this run", () =>
  eq(runFailureLog([line(200, "error", "at the boundary")], 200).length, 1));
test("runFailureLog: empty/absent logs are safe", () => {
  eq(runFailureLog([], 200).length, 0); eq(runFailureLog(undefined, 200).length, 0);
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
