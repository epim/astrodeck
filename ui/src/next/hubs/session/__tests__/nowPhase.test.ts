// nowPhase.test.ts - the phase pill's ladder, row by row.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/nowPhase.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS IS A PURE TEST AND WHY IT IS EXHAUSTIVE. The pill is the one thing on
// the screen that is read at 3am to decide whether to go back to bed, and every
// way it can be wrong is a way of lying about a rig nobody is watching:
//
//   * a pill that says CAPTURING through a COOLING gate says the night is
//     proceeding while the shutter has not opened;
//   * a pill that says RUNNING through a cloud HOLD hides the incident entirely,
//     which is the 2026-08-12 defect in a different place;
//   * a pill that pulses on a terminal state says the rig is still working.
//
// The ladder is ORDERED, so each row is asserted with the rows above it made
// false - otherwise a test could pass because an earlier rule matched.

import { laneOf, phaseOf, PHASE_COLOR, type PhaseInput } from "../now/phase";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const BASE: PhaseInput = {
  state: "running",
  detail: "",
  scheduleState: null,
  busyLanes: [],
  focusRunning: false,
  frameStartedAtMs: null,
  incident: null,
};

const at = (over: Partial<PhaseInput>): PhaseInput => ({ ...BASE, ...over });

// ------------------------------------------------------------- the ladder

test("goto lane -> SLEWING", () => {
  eq(laneOf(at({ busyLanes: ["goto"] })).text, "SLEWING", "lane:");
  eq(laneOf(at({ busyLanes: ["goto"] })).color, PHASE_COLOR.step, "colour:");
});

test("detail 'slewing to ...' -> SLEWING even with no lane", () => {
  eq(laneOf(at({ detail: "slewing to M31" })).text, "SLEWING");
});

test("detail 're-centring...' -> SLEWING (the engine's own re-centre)", () => {
  eq(laneOf(at({ detail: "re-centring after guiding loss" })).text, "SLEWING");
});

test("autofocus lane -> FOCUSING", () => {
  eq(laneOf(at({ busyLanes: ["autofocus"] })).text, "FOCUSING");
});

test("the focus EVENT alone -> FOCUSING, before the lane shows on a 2 s poll", () => {
  eq(laneOf(at({ focusRunning: true })).text, "FOCUSING");
});

test("solve lane -> SOLVING", () => {
  eq(laneOf(at({ busyLanes: ["solve"] })).text, "SOLVING");
});

test("detail 'starting guiding' -> GUIDING", () => {
  eq(laneOf(at({ detail: "starting guiding" })).text, "GUIDING");
});

test("detail 'cooling to -10C' -> COOLING, NOT CAPTURING", () => {
  const cooling = at({ detail: "cooling to -10.0C", frameStartedAtMs: 1_700_000_000_000 });
  // The frame marker is set AND the cooling gate is closed: the gate wins, or
  // the pill claims a shutter the engine has not opened.
  eq(laneOf(cooling).text, "COOLING", "a cooling gate must outrank the frame marker:");
});

test("schedule 'waiting' -> WAITING, dim", () => {
  const w = laneOf(at({ scheduleState: "waiting" }));
  eq(w.text, "WAITING");
  eq(w.color, PHASE_COLOR.dim, "WAITING is not an accent state:");
});

test("capture lane -> CAPTURING", () => {
  eq(laneOf(at({ busyLanes: ["capture"] })).text, "CAPTURING");
});

test("a frame in flight -> CAPTURING with no lane at all", () => {
  eq(laneOf(at({ frameStartedAtMs: 1_700_000_000_000 })).text, "CAPTURING");
});

test("nothing matched -> RUNNING", () => {
  eq(laneOf(BASE).text, "RUNNING");
});

test("the ladder is ORDERED: goto beats autofocus beats solve", () => {
  eq(laneOf(at({ busyLanes: ["goto", "autofocus", "solve", "capture"] })).text, "SLEWING");
  eq(laneOf(at({ busyLanes: ["autofocus", "solve", "capture"] })).text, "FOCUSING");
  eq(laneOf(at({ busyLanes: ["solve", "capture"] })).text, "SOLVING");
});

// ------------------------------------------------------------- the top rules

test("an incident OWNS the pill, whatever the lanes say", () => {
  const p = phaseOf(at({
    busyLanes: ["capture"],
    frameStartedAtMs: 1,
    incident: { pill: "HOLDING", color: "#ffb454" },
  }));
  eq(p.text, "HOLDING", "the incident pill did not win:");
  eq(p.color, "#ffb454", "the incident colour did not win:");
  eq(p.pulse, true, "a live incident must pulse:");
});

test("paused beats the lanes but not an incident", () => {
  eq(phaseOf(at({ state: "paused", busyLanes: ["capture"] })).text, "PAUSED");
  eq(phaseOf(at({ state: "paused", incident: { pill: "STALE", color: "#7683a5" } })).text, "STALE");
});

test("the terminal states, and which of them pulse", () => {
  const rows: [PhaseInput["state"], string, boolean][] = [
    ["complete", "DONE", false],
    ["aborted", "STOPPED", false],
    ["error", "ERROR", false],
    ["aborting", "STOPPING", true],       // still a LIVE run: the rig is moving
    ["nina_native", "NINA IS DRIVING", true],
    ["idle", "IDLE", false],
  ];
  for (const [state, text, pulse] of rows) {
    const p = phaseOf(at({ state }));
    eq(p.text, text, `${state}:`);
    eq(p.pulse, pulse, `${state} pulse:`);
  }
});

test("a holding publish with no incident still resolves, and does not crash", () => {
  const p = phaseOf(at({ state: "holding", busyLanes: ["capture"] }));
  eq(p.text, "CAPTURING");
});

test("every pill carries a tone as well as a hex - night mode flattens the hex", () => {
  const seen = new Set<string>();
  for (const state of ["running", "paused", "complete", "aborted", "aborting", "nina_native"] as const) {
    const p = phaseOf(at({ state }));
    assert(typeof p.tone === "string" && p.tone.length > 0, `${state} has no tone`);
    seen.add(p.tone);
  }
  assert(seen.size > 1, "every state resolved to the same tone - the ladder is not distinguishing them");
});

const total = passed + failed;
console.log(`nowPhase.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
