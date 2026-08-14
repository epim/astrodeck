// cyclePlanRows.test.ts — the FILTER CYCLE slot editor's row model.
//
// The thing under test is a rule the 2026-08-14 export states as a prohibition:
// "one row per filter in the RIG'S WHEEL (from the equipment panel - never a
// hand-typed filter name)". A prohibition is only kept if something checks it,
// and the failure it prevents is silent — a cycle naming `OIII` on a wheel whose
// slot reads `Oiii` looks completely fine on screen and asks for a filter that
// does not exist once the sky is dark.
//
// Run directly:  npx tsx src/components/flows/__tests__/cyclePlanRows.test.ts
import {
  FALLBACK_WHEEL, cyclePlanRows, defaultExposureFor, resolveWheel,
  setSlotExposure, toggleSlot,
} from "../cyclePlanRows";
import { parseCyclePlan } from "../nodeDefs";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { const A = JSON.stringify(a), B = JSON.stringify(b); if (A !== B) throw new Error(`${m} expected ${B}, got ${A}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

/** This rig's actual wheel (verified slot map), including its blackout slot. */
const RIG_NAMES = ["L", "R", "G", "B", "S", "Ha", "Oiii", "DARK"];
const RIG_OPAQUE = [false, false, false, false, false, false, false, true];

// ------------------------------------------------------- which rows are offered

test("the rows are the rig's own wheel, in slot order", () => {
  const { filters, fromRig } = resolveWheel(RIG_NAMES, RIG_OPAQUE);
  eq(filters, ["L", "R", "G", "B", "S", "Ha", "Oiii"], "wheel filters");
  ok(fromRig, "these came off the rig and the UI should say so");
});

test("the blackout slot is never offered", () => {
  // #240 is eighteen frames shot through the blackout slot and labelled
  // IMAGETYP=Light. A cycle row for it would be a supported way to do that to a
  // whole channel, all night, unattended.
  const { filters } = resolveWheel(RIG_NAMES, RIG_OPAQUE);
  ok(!filters.includes("DARK"), `the opaque slot is on offer: ${filters.join(",")}`);
});

test("unnamed slots are not offered either", () => {
  const { filters } = resolveWheel(["L", "Slot 2", "  ", "Ha"], undefined);
  eq(filters, ["L", "Ha"], "placeholder names dropped");
});

test("no wheel falls back to the handoff's set, and admits it", () => {
  const { filters, fromRig } = resolveWheel(undefined, undefined);
  eq(filters, FALLBACK_WHEEL.map((s) => s.filter), "fallback wheel");
  ok(!fromRig, "a fallback must not be presented as the rig's own");
});

test("a wheel with nothing usable falls back rather than showing an empty list", () => {
  // An attached wheel reporting eight unnamed slots is the case that matters:
  // "connected" is true and the answer is still not the rig's.
  const { filters, fromRig } = resolveWheel(["Slot 1", "Slot 2"], undefined);
  eq(filters.length, FALLBACK_WHEEL.length, "fell back");
  ok(!fromRig, "an empty-after-filtering wheel is not the rig's list");
});

// ------------------------------------------------------------ reading the plan

const WHEEL = ["L", "R", "G", "B", "S", "Ha", "Oiii"];

test("a stored plan ticks its own rows and leaves the rest off", () => {
  const rows = cyclePlanRows(WHEEL, "L 60, Ha 180");
  eq(rows.filter((r) => r.on).map((r) => `${r.filter}:${r.exposure}`),
     ["L:60", "Ha:180"], "ticked rows");
  eq(rows.filter((r) => !r.on).map((r) => r.exposure).join("|"), "||||",
     "an unticked row shows no exposure");
});

test("row order follows the WHEEL, not the stored string", () => {
  // The row order is the shooting order. If the stored string could reorder the
  // editor, an operator would re-plan the night by opening a saved flow.
  eq(cyclePlanRows(WHEEL, "Ha 180, L 60").map((r) => r.filter), WHEEL, "row order");
});

test("a plan naming a filter this wheel lacks simply has no row", () => {
  // A flow built on another rig. Showing the row would offer a filter this rig
  // cannot select; hiding it is what makes the next write drop it.
  const rows = cyclePlanRows(WHEEL, "L 60, SII 180");
  eq(rows.map((r) => r.filter), WHEEL, "no extra row appeared");
  eq(rows.filter((r) => r.on).map((r) => r.filter), ["L"], "only the real one is on");
});

// -------------------------------------------------------------- editing it

test("ticking a broadband slot arms it at 60s, narrowband at 180s", () => {
  eq(defaultExposureFor("L"), 60, "L");
  eq(defaultExposureFor("Ha"), 180, "Ha");
  eq(toggleSlot(WHEEL, "", "L"), "L 60", "first tick");
  eq(toggleSlot(WHEEL, "", "Oiii"), "Oiii 180", "narrowband tick");
});

test("narrowband is recognised across the spellings wheels actually use", () => {
  // This rig writes `Oiii` and `S`; the prototype writes `OIII` and `SII`. A
  // matcher that knew one spelling would hand a narrowband filter the 60s
  // broadband default, and 60s of Ha is an empty frame.
  for (const f of ["Ha", "HA", "H-alpha", "OIII", "Oiii", "o3", "SII", "S2"]) {
    eq(defaultExposureFor(f), 180, `${f} should default narrowband`);
  }
  // The SHO single-letter convention. This rig's own wheel names its SII slot
  // "S", which no multi-letter prefix catches - and that is not hypothetical,
  // it is what the last assertion in this file found.
  for (const f of ["S", "H", "O", "s"]) {
    eq(defaultExposureFor(f), 180, `${f} should default narrowband (SHO wheel)`);
  }
  for (const f of ["L", "R", "G", "B", "Lum", "Red", "Sloan g"]) {
    eq(defaultExposureFor(f), 60, `${f} should default broadband`);
  }
});

test("unticking removes the slot and keeps the others in wheel order", () => {
  eq(toggleSlot(WHEEL, "L 60, R 60, Ha 180", "R"), "L 60, Ha 180", "R removed");
});

test("ticking always writes in wheel order, whatever order the ticks came in", () => {
  let plan = "";
  for (const f of ["Oiii", "L", "Ha", "R"]) plan = toggleSlot(WHEEL, plan, f);
  eq(plan, "L 60, R 60, Ha 180, Oiii 180", "written in wheel order");
});

test("an exposure edit changes one slot and nothing else", () => {
  eq(setSlotExposure(WHEEL, "L 60, Ha 180", "Ha", "300"), "L 60, Ha 300", "Ha only");
});

test("mid-keystroke junk leaves the stored plan exactly as it was", () => {
  // Clearing "60" to type "180" passes through "". Committing a zero there would
  // produce a slot the compiler refuses — a run-time failure from a graph that
  // looks correct, which is the worst place to find out.
  const plan = "L 60, Ha 180";
  for (const raw of ["", " ", "-", "abc", "0", "-5"]) {
    eq(setSlotExposure(WHEEL, plan, "L", raw), plan, `raw ${JSON.stringify(raw)}`);
  }
});

test("what the editor writes is what the parser reads back", () => {
  // The round trip is the whole contract between this file and the server:
  // `formatCyclePlan` here, `parse_cycle_plan` there.
  const plan = toggleSlot(WHEEL, toggleSlot(WHEEL, "", "L"), "Oiii");
  eq(parseCyclePlan(plan), [{ filter: "L", exposure_s: 60 },
                            { filter: "Oiii", exposure_s: 180 }], "round trip");
});

test("the user's stated night is expressible in one pass of ticks", () => {
  // "60s x 1 L, R, G, B; 180s x 1 S, Ha, O3" - the request this node exists for.
  let plan = "";
  for (const f of WHEEL) plan = toggleSlot(WHEEL, plan, f);
  eq(plan, "L 60, R 60, G 60, B 60, S 180, Ha 180, Oiii 180",
     "every slot ticked, correct defaults, wheel order");
});

console.log(`cyclePlanRows.test: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
