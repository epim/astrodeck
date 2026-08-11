// captureStall.test.ts — #206: the stall alarm must time CAPTURE, not previews.
//
//   Run directly:  npx tsx src/lib/__tests__/captureStall.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Reported from the rig 2026-08-09 02:12: a run saving a frame every 71 s,
// 70/150 done, 0 rejected, 0 events cost — reading CAPTURE STALLED. The check
// was timing `lastFrameAtMs`, stamped in the `preview` bus handler, so it
// measured the arrival of preview JPEGs at the browser. It was worst exactly
// where it mattered least: over the relay a preview JPEG is the heaviest
// payload and the first thing a slow link drops, so the alarm fired because
// the network was slow, not because capture was.
//
// These drive the STORE's real sequence handler, because the defect was never
// in stallLevel() — that function was always correct about the number it was
// given. It was in which number it was given, and that is decided in the store.

/* eslint-disable @typescript-eslint/no-explicit-any */

// jsdom, because store.ts touches the document at import time (touch sizing).
// Hand-rolled stubs got three errors deep before this was obviously cheaper.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
                      { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {},
                          removeEventListener() {}, addListener() {},
                          removeListener() {} });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

// A CONTROLLED CLOCK, and it is not a nicety. The first version of this file
// let the real clock run, and the sabotage check — re-stamping the capture
// clock from the `preview` handler, i.e. re-introducing the exact bug — PASSED
// 8/8, because both events landed in the same millisecond and the equality held
// either way. A test that cannot fail is worse than no test: it reports the bug
// as fixed. Every stamp below now lands on a distinct, chosen instant.
let CLOCK = 1_700_000_000_000;
Date.now = () => CLOCK;
const advance = (ms: number) => { CLOCK += ms; };

const { useStore } = await import("../../store");
const { stallLevel } = await import("../eta");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const eq = (a: unknown, b: unknown, m: string) =>
  assert(Object.is(a, b), `${m}: expected ${String(b)}, got ${String(a)}`);

/** Feed the store one `sequence` bus event, the way the socket would. */
function seqEvent(state: string, framesDone: number | null): void {
  useStore.getState().handleEvent({
    type: "sequence",
    data: {
      state, plan_name: "n", detail: "",
      progress: framesDone == null ? undefined : {
        frames_done: framesDone, total: 150, percent: 0, rejected: 0,
      },
    },
  } as any);
}
function previewEvent(): void {
  useStore.getState().handleEvent({
    type: "preview", data: { id: 1, stats: {}, exposure_s: 60 },
  } as any);
}
function reset(): void {
  useStore.setState({
    sequence: { state: "idle" } as any,
    lastFrameAtMs: null, lastCaptureAtMs: null, lastFramesDone: null,
    runBanner: null,
  } as never);
}

// ------------------------------------------------------- the reported defect
test("THE 2026-08-09 CASE: previews stop, frames keep landing, no alarm", () => {
  // The rig: a frame every 71 s, 70/150 done. The link: no preview JPEG has
  // arrived for six minutes. The old check timed the preview and screamed.
  reset();
  seqEvent("running", 69);
  advance(71_000);
  seqEvent("running", 70);            // a frame LANDED — the capture clock moves
  const anchor = useStore.getState().lastCaptureAtMs as number;

  advance(360_000);                   // six minutes of no previews at all
  const captureAgeS = (Date.now() - anchor) / 1000;
  eq(captureAgeS, 360, "seconds since the last FRAME");
  // ...but the rig published progress throughout, and frames_done advanced.
  seqEvent("running", 71);
  const freshAgeS = (Date.now() - (useStore.getState().lastCaptureAtMs as number)) / 1000;
  eq(freshAgeS, 0, "seconds since the last frame, after one landed");
  eq(stallLevel("running", freshAgeS, 60), "none", "a rig that is capturing reads as");
});

test("a preview arriving does NOT move the capture clock", () => {
  reset();
  seqEvent("running", 10);
  const anchor = useStore.getState().lastCaptureAtMs;
  advance(5_000);                     // a DISTINCT instant — see the clock note
  previewEvent();
  eq(useStore.getState().lastCaptureAtMs, anchor,
    "a preview moved the capture clock — that is the bug this file exists for");
  eq(useStore.getState().lastFrameAtMs, Date.now(),
    "the preview clock did not move; the LIVE badge needs it");
});

test("...and conversely, a frame landing does not move the PREVIEW clock", () => {
  reset();
  previewEvent();
  const previewAt = useStore.getState().lastFrameAtMs;
  advance(5_000);
  seqEvent("running", 11);
  eq(useStore.getState().lastFrameAtMs, previewAt,
    "frames_done advancing forged a preview arrival");
});

// ------------------------------------------------------- the alarm still works
test("a genuinely wedged capture DOES still raise the alarm", () => {
  reset();
  seqEvent("running", 70);
  // frames_done never advances again. At 3x exposure + margin the level is red,
  // and nothing about the fix may have made the alarm unreachable — that is the
  // failure mode a bound-that-cannot-fire produces.
  eq(stallLevel("running", 60 * 3 + 60, 60), "red", "a wedged 60s capture reads as");
  eq(stallLevel("running", 60 * 2 + 1, 60), "amber", "past 2x reads as");
});

test("frames_done going BACKWARDS does not move the clock", () => {
  // A resumed session seeds done-counts from the ledger; a re-publish with a
  // lower count must not read as a fresh frame.
  reset();
  seqEvent("running", 70);
  const anchor = useStore.getState().lastCaptureAtMs;
  advance(5_000);
  seqEvent("running", 12);
  eq(useStore.getState().lastCaptureAtMs, anchor, "a lower frames_done moved the clock");
});

test("an unchanged frames_done does not move the clock either", () => {
  // Progress republishes on a cadence (ETA, exposure countdown) with the same
  // frames_done. If those refreshed the clock, a wedged capture could never be
  // detected — the alarm would be reset by the very payload proving nothing
  // had happened. This is the bound-that-cannot-fire shape.
  reset();
  seqEvent("running", 70);
  const anchor = useStore.getState().lastCaptureAtMs;
  advance(120_000);
  seqEvent("running", 70);
  seqEvent("running", 70);
  eq(useStore.getState().lastCaptureAtMs, anchor,
    "a progress refresh with no new frame reset the stall clock");
  const ageS = (Date.now() - anchor!) / 1000;
  eq(stallLevel("running", ageS, 30), "red", "120s with no frame on a 30s exposure");
});

// ------------------------------------------------------------ no-run states
test("an idle or complete run has NO capture clock, so nothing can be overdue", () => {
  for (const st of ["idle", "complete", "aborted", "error"]) {
    reset();
    seqEvent("running", 5);
    seqEvent(st, 5);
    eq(useStore.getState().lastCaptureAtMs, null, `${st} left a capture clock`);
  }
});

test("a PAUSED run keeps its clock — the gap is explained by the pause", () => {
  reset();
  seqEvent("running", 5);
  const anchor = useStore.getState().lastCaptureAtMs;
  seqEvent("paused", 5);
  eq(useStore.getState().lastCaptureAtMs, anchor, "pausing dropped the anchor");
  // ...and stallLevel refuses to escalate on a paused run anyway.
  eq(stallLevel("paused", 99999, 60), "none", "a paused run reads as");
});

test("the first frame of a run has an anchor to be late against", () => {
  // Seeded on the rising edge into `running`: without it, frames_done has never
  // advanced, lastCaptureAtMs is null, and a run that never takes its FIRST
  // frame would never trip the alarm.
  reset();
  seqEvent("running", 0);
  assert(useStore.getState().lastCaptureAtMs != null,
    "a run that has taken no frames yet cannot be detected as stuck");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncaptureStall.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
