// framingReadoutsFixture.test.ts - the compile route's recorded readouts,
// through the modal's reader, to the RUN lines it prints (#189 S4 item 1;
// spec 2026-09-23 flows mosaic, 2.4 RUN, 3.2).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingReadoutsFixture.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A FIXTURE, AND WHY THIS PATH. The RUN numbers are the server's ("every
// number comes from the server compile, never computed in the client"), and
// they reach the modal as JSON under a TypeScript type the server has never
// seen. The two drift the day a field is renamed on one side: the modal then
// prints "6 panels x undefined filters", or nothing, and every test that
// builds its readouts by hand still passes. So this file takes the route's
// OWN recorded answer, server/tests/fixtures/flow_readouts_m31.json (written
// by the server's `readouts` and `rig_readout` and pinned against the route
// by test_flows_readouts.py), READ, never copied, wraps it as the compile
// answer the store holds, and runs it through the path the sheet uses:
// framingApi `compiledReadouts` / `compiledRig`, then framingModel
// `runLines`. The lines must be the spec's worked RUN lines for the eighth
// Example's 3x2.
//
// framingModel.test.ts holds `runLines` to hand-built records and the key
// lists to this fixture; this file holds the READER, whose key table is the
// one `satisfies` ties to the type, and the whole path together.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-umodal-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run
// again against the current tree in s4-umodal-r2-mut (2026-09-27), and
// each was red with the failure quoted.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

// framingApi imports lib/api -> lib/base, which reads `window.location` AT
// MODULE SCOPE. No request is made.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { compiledReadouts, compiledRig, READOUT_KINDS, RIG_KINDS } = await import("../framingApi");
const { runLines } = await import("../framingModel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

const FIXTURE_REL = "../../../../../../server/tests/fixtures/flow_readouts_m31.json";
function readFixture(): any {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the compile route's recorded readouts: ${(e as Error).message}`);
  }
  return JSON.parse(text);
}
const FX = readFixture();
/** The compile answer as the store holds it: the four lists plus the two
 *  keys the route added in S4. */
const COMPILED = { plan: {}, structural: [], issues: [], unmapped: [], readouts: FX.readouts, rig: FX.rig };

// The spec's worked RUN lines (2.4), for the eighth Example's 3x2 of the
// four-filter cycle at 20 passes, on a rig that has timed no hop, flipping
// with 6.1 min of idle, with neither focus trigger armed.
const EIGHTH_EXAMPLE = [
  "6 panels x 4 filters x 20 = 480 subs",
  "2.67 h per panel, 16 h in all",
  "120 visits at 1 pass per visit",
  "hop: not measured on this rig yet",
  "meridian: up to 6.1 min idle before the flip",
  "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools",
];

// MUTANT "readouts fixture drifts from the TS type" (`steps` renamed
// `filters` in framingModel's RunReadouts, in framingApi's READOUT_KINDS and
// in runLines together, so `tsc -b` passes and only the server's answer
// disagrees). Observed:
//   x the compile route's recorded readouts render the spec's worked RUN lines through the sheet's
//     reader: the route's answer does not read as RunReadouts: readouts.n2 has no filters
//   (All three tests went red; `tsc -b` exited 0 with the mutant in place.)
test("the compile route's recorded readouts render the spec's worked RUN lines through the sheet's reader", () => {
  const read = compiledReadouts(COMPILED as any, "n2");
  assert(read !== null, "the reader found no readout for n2 in the route's answer");
  if (!read!.ok) throw new Error(`the route's answer does not read as RunReadouts: ${read!.why}`);
  const rig = compiledRig(COMPILED as any);
  assert(rig !== null, "the route's rig block does not read as RigBlock");
  eq(runLines(read!.value, rig), EIGHTH_EXAMPLE, "the RUN lines");
});

// MUTANT "reader checks nothing" (readAgainst returns `{ ok: true, value }`
// without walking the table). Observed:
//   x the reader names the key a drifted answer lacks, and a missing block is no readout, not an
//     error: a renamed field: expected {"ok":false,"why":"readouts.n2 has no steps"}, got {"ok":true,"
//     value":{"node_id":"n2","mode":"rotate","panels":6,"rounds":20,"subs_per_panel":80,"subs_total":4
//     80,"pass_s":480,"panel_s":9600,"total_s":57600,"passes":1,"visit_min_s":0,"visit_passes":1,"visi
//     ts_per_panel":20,"visits_total":120,"hop_s":150,"hop_measured":false,"preflip_idle_s":365.2,"ang
//     le_tolerance_deg":6.071,"focus":"once","autofocus_every":0,"refocus_delta_c":0,"filters":4}}
test("the reader names the key a drifted answer lacks, and a missing block is no readout, not an error", () => {
  const drifted = JSON.parse(JSON.stringify(FX.readouts));
  drifted.n2.filters = drifted.n2.steps;
  delete drifted.n2.steps;
  const r = compiledReadouts({ ...COMPILED, readouts: drifted } as any, "n2");
  eq(r, { ok: false, why: "readouts.n2 has no steps" }, "a renamed field");
  const wrongType = JSON.parse(JSON.stringify(FX.readouts));
  wrongType.n2.mode = "rotating";
  eq(compiledReadouts({ ...COMPILED, readouts: wrongType } as any, "n2"),
    { ok: false, why: "readouts.n2.mode is not a mode" }, "a value outside the vocabulary");
  // `efficiency` is present only with a measured hop: absent reads, a
  // present non-number does not.
  const eff = JSON.parse(JSON.stringify(FX.readouts));
  eff.n2.efficiency = "83%";
  eq(compiledReadouts({ ...COMPILED, readouts: eff } as any, "n2"),
    { ok: false, why: "readouts.n2.efficiency is not a number?" }, "an efficiency that is not a number");
  eq(compiledReadouts(COMPILED as any, "n9"), null, "a block the compile has no readout for");
  eq(compiledReadouts({ ...COMPILED, readouts: undefined } as any, "n2"), null, "an older server's answer");
  const badRig = { ...FX.rig, hop_samples: null };
  eq(compiledRig({ ...COMPILED, rig: badRig } as any), null, "a rig block that is not RigBlock's shape");
});

test("the reader's key tables are the fixture's keys, both ways", () => {
  const keys = (o: object) => Object.keys(o).sort();
  // `efficiency` is absent from a rig with no measured hop, so the table has
  // exactly one key the fixture lacks, and it is that one.
  eq(keys(READOUT_KINDS).filter((k) => !(k in FX.readouts.n2)), ["efficiency"], "table keys the route did not send");
  eq(keys(FX.readouts.n2).filter((k) => !(k in READOUT_KINDS)), [], "route keys the table does not check");
  eq(keys(RIG_KINDS), keys(FX.rig), "the rig table against the route's rig block");
});

// ------------------------------------------------- a single target's answer
// (#409)
//
// The fixture above is a mosaic, so every value in it is a number or a
// boolean. A single target's group fields are null, `hop_measured` among
// them (server test_a_single_target_has_no_group_numbers), and the reader
// once demanded a boolean there: every single-target TARGET with a stage,
// five of the eight shipped Examples among them, printed "the compile's RUN
// numbers could not be read: readouts.n2.hop_measured is not a boolean" in
// place of its RUN lines, while framingModel.test.ts's hand-built single
// record said `false` and passed. So this reads the route's own answer for
// the `example-cycle` Example, server/tests/fixtures/flow_readouts_single.json
// (written by the server's `readouts` and pinned against the route by
// test_flows_readouts.py `TestASingleTargetExamplesFixture`).
const SINGLE_REL = "../../../../../../server/tests/fixtures/flow_readouts_single.json";
function readSingle(): any {
  try {
    return JSON.parse(readFileSync(new URL(SINGLE_REL, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${SINGLE_REL}, the route's single-target readouts: ${(e as Error).message}`);
  }
}

// MUTANT "hop_measured must be a boolean" (READOUT_KINDS `hop_measured:
// "boolean"`, the reader as S4 first built it), run in a private copy of ui/
// (scratchpad s4spec-review-mut). Observed:
//   x a single target's recorded readouts read, and render its RUN lines, with no hop line:
//     a single target's answer does not read as RunReadouts: readouts.n2.hop_measured is not a
//     boolean
// CONTROL: the mosaic fixture's tests above stay green under it, which is
// why they could not see it.
test("a single target's recorded readouts read, and render its RUN lines, with no hop line", () => {
  const fx = readSingle();
  const compiled = { plan: {}, structural: [], issues: [], unmapped: [], readouts: fx.readouts, rig: fx.rig };
  assert(fx.readouts.n2.hop_measured === null, "premise: the route sends null for a single target's hop_measured");
  const read = compiledReadouts(compiled as any, "n2");
  assert(read !== null, "the reader found no readout for n2 in the route's answer");
  if (!read!.ok) throw new Error(`a single target's answer does not read as RunReadouts: ${read!.why}`);
  eq(runLines(read!.value, compiledRig(compiled as any)), [
    "1 panel x 7 filters x 45 = 315 subs",
    "9.75 h per panel, 9.75 h in all",
    "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools",
  ], "the RUN lines of a single target, which makes no hop and has no visit bound");
  // The same null is still refused where a boolean is owed: the rig block's
  // flag, which decides the hop line.
  eq(compiledRig({ ...compiled, rig: { ...fx.rig, hop_measured: null } } as any), null,
    "a rig block whose hop_measured is null");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`framingReadoutsFixture.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
