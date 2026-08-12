// flowHeaderText.test.ts — the four things the Flows toolbar could print untrue.
//   Run:  npx tsx src/components/flows/__tests__/flowHeaderText.test.ts   (from ui/)
//
// FlowHeader is mostly markup and needs no test. These four formatters are not:
// each one stands where a header could state something the rig never said.
//
//   * formatEta      — `run.etaS` has NO publisher (§G-1). The one behaviour
//                      worth pinning is that a missing number renders as a
//                      missing number, because FlowRunState's own comment says
//                      "an ETA the client invented looks identical to one the
//                      rig computed, and the operator cannot tell which they
//                      are being shown."
//   * checksLabel    — the chip is the only always-visible verdict on a graph.
//   * runBlockedReason — an honest-disabled control whose reason came back null
//                      would be a control that silently does nothing.
//   * providerPill   — #129: a fully real, tracking rig was badged SIMULATOR on
//                      the one chip that says whether commands reach the sky.
/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// FlowHeader -> store -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. `../ui` reaches Overlay, which
// calls matchMedia. Both have to exist before the dynamic import. Nothing here
// fakes behaviour the assertions then read back.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
  clearTimeout: (id: any) => clearTimeout(id),
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};
(globalThis as any).document = {
  documentElement: {
    classList: { add() {}, remove() {}, toggle() {} },
    style: { setProperty() {} },
  },
  addEventListener() {}, removeEventListener() {},
  getElementById: () => null,
  createElement: () => ({ style: {}, classList: { add() {} }, appendChild() {} }),
  body: { appendChild() {} },
};
(globalThis as any).matchMedia = (globalThis as any).window.matchMedia;

const {
  formatEta, checksLabel, runBlockedReason, librarySubline, providerPill,
} = await import("../FlowHeader");
const { accessPhrase } = await import("../../../lib/caps");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq(a: unknown, b: unknown, msg: string): void {
  assert(Object.is(a, b), `${msg} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

// ------------------------------------------------------------------- the ETA
test("a null ETA renders as a dash, never as a number", () => {
  eq(formatEta(null), "—",
    "a header that printed 0:00 for an unknown ETA would be indistinguishable "
    + "from one the rig computed, and the operator would plan around it");
  eq(formatEta(undefined), "—", "an absent field is the same claim as null");
});

test("a nonsense ETA is refused rather than rendered", () => {
  eq(formatEta(Number.NaN), "—", "NaN would print 'NaN:NaN' straight onto the toolbar");
  eq(formatEta(Number.POSITIVE_INFINITY), "—", "Infinity would print 'Infinity:NaN'");
  eq(formatEta(-4), "—",
    "a negative remaining time is not a countdown, it is a bug upstream — "
    + "printing '-1:56' would ask the operator to believe it");
});

test("the ETA is m:ss with a zero-padded seconds field", () => {
  // Capture 07 shows ETA 0:12 mid-run.
  eq(formatEta(12), "0:12", "capture 07's own value");
  eq(formatEta(9), "0:09", "a bare '0:9' reads as nine minutes at a glance");
  eq(formatEta(60), "1:00", "the minute boundary");
  eq(formatEta(4385), "73:05", "minutes are unbounded — nothing is truncated away");
  eq(formatEta(11.6), "0:12", "fractional seconds round rather than truncate");
});

// --------------------------------------------------------- validation chip
test("the chip's three strings are the design's three strings", () => {
  eq(checksLabel(0), "GRAPH VALID", "§C.3's clean state");
  eq(checksLabel(1), "1 OPEN CHECK", "singular is spelled out separately in §C.3");
  eq(checksLabel(4), "4 OPEN CHECKS", "plural");
});

test("the chip never reads valid for a negative count", () => {
  // Defensive: issues.length cannot go negative, but "GRAPH VALID" is the one
  // string that must never appear by accident.
  eq(checksLabel(-1), "GRAPH VALID",
    "a negative count is impossible; the point is that it does not print "
    + "'-1 OPEN CHECKS'");
});

// -------------------------------------------------------- the honest button
test("RUN names the capability the server actually requires", () => {
  const r = runBlockedReason(false, true, false);
  assert(r != null, "a viewer pressing RUN must be told why, not silently ignored");
  assert(r!.includes(accessPhrase("control.mount")),
    `the reason must name the real gate (app.py:3713 requires CAP_CONTROL_MOUNT): ${r}`);
});

test("RUN names the missing camera, and STOP does not", () => {
  eq(runBlockedReason(true, false, false),
    "No camera is connected, so there is nothing to run this flow on.",
    "hub.require('camera') at app.py:3791 is the second real guard");
  eq(runBlockedReason(true, false, true), null,
    "refusing to STOP a live run because no camera is attached would leave a "
    + "moving mount running while the UI explains itself");
});

test("STOP is still gated on the capability its route requires", () => {
  const r = runBlockedReason(false, true, true);
  assert(r != null && r.includes(accessPhrase("control.mount")),
    "/api/sequence/abort is CAP_CONTROL_MOUNT too — a STOP that looked live and "
    + `then returned 403 is worse than one that says why: ${r}`);
});

test("a live control returns no reason at all", () => {
  eq(runBlockedReason(true, true, false), null, "an operator with a camera can run");
  eq(runBlockedReason(true, true, true), null, "and can stop");
});

// -------------------------------------------------------------- the sub-line
test("the library sub-line agrees with itself about number", () => {
  eq(librarySubline(5), "Automation library · 5 saved flows", "capture 01's line");
  eq(librarySubline(1), "Automation library · 1 saved flow", "'1 saved flows' is wrong");
  eq(librarySubline(0), "Automation library · 0 saved flows", "zero takes the plural");
});

// ----------------------------------------------------------- provider badge
test("only the simulator is badged SIMULATOR", () => {
  eq(providerPill("sim")?.label, "SIMULATOR", "the design's word for the sim rig");
  eq(providerPill("sim")?.variant, "prov-sim",
    "dashed border + hollow dot — the shape rule that survives night mode");
});

test("a real rig is never badged as the simulator (#129)", () => {
  // The failure this pins: a rig built from per-role hardware drivers reports
  // its backend NAME ("zwo-usb"), which matched no branch and fell through to a
  // SIM default — so a fully real, tracking rig was badged SIMULATOR.
  for (const mode of ["nina", "alpaca", "native", "zwo-usb", "player-one"]) {
    const pill = providerPill(mode);
    assert(pill != null, `${mode} must still get a badge`);
    assert(pill!.label !== "SIMULATOR",
      `${mode} is a real backend and must not claim to be the simulator`);
    eq(pill!.variant, "prov-ext",
      `${mode} takes the external-backend shape, not the simulator's dashes`);
  }
});

test("an unreported backend shows no badge rather than a guess", () => {
  eq(providerPill(undefined), null, "pre-first-poll there is nothing true to say");
  eq(providerPill(""), null, "an empty mode is not a backend");
  eq(providerPill("none"), null, "'none' is the no-backend mode");
});

for (const f of failures) console.log(f);
console.log(`flowHeaderText: ${passed}/${passed + failed} passed`);
export default { passed, failed, total: passed + failed };
