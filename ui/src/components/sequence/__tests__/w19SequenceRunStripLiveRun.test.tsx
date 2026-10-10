// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19SequenceRunStripLiveRun.test.tsx - the Sequence view's sticky run strip,
// MOUNTED: it shows for EVERY live run state, including a cloud hold (#922,
// WP-161, wave 19).
//
//   Run directly:  npx tsx src/components/sequence/__tests__/w19SequenceRunStripLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The strip's own predicate was `running || paused || aborting`. It
// left out "holding", so on a cloudy night, when the run sits in a hold for
// hours, the glance line the strip exists to give ("is it running, which frame
// of how many, how long left") vanished exactly while the operator most wanted
// it. `lib/lastSessionFrame.ts` documented the omission as "its own defect in
// its own file".
//
// WHAT IS WORTH ASSERTING. Per state in the union: the strip is in the document
// with the word the operator reads for that state, and where the run is not live
// there is no strip at all (the control row, which a predicate that is always
// true fails). A hold keeps the progress it carries.
//
// MUTANT "no holding" (`runIsLive(seq)` made `seq.state === "running" ||
// seq.state === "paused" || seq.state === "aborting"`, the unfixed text). Run
// from a byte backup, restored byte-identically (md5sum compared): see the
// report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const SequenceRunStrip = (await import("../SequenceRunStrip")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mountWith(sequence: Record<string, unknown>): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  act(() => { useStore.setState({ sequence } as never); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(SequenceRunStrip)); });
}
const strip = (): any => container.querySelector("[data-sequence-strip]");

const PROGRESS = {
  frames_done: 2, frames_total: 24, percent: 9, elapsed_s: 300, rejected: 0,
  eta_s: 5400, eta_confident: true,
};

/** The whole `SequenceState["state"]` union: the word the strip reads, or null
 *  where the run is not live and there is no strip. */
const STATES: Array<[string, RegExp | null]> = [
  ["running", /M 31/],
  ["paused", /paused/i],
  ["holding", /holding/i],
  ["aborting", /stopping/i],
  ["idle", null], ["complete", null], ["aborted", null], ["error", null],
  ["nina_native", null],
];

for (const [state, word] of STATES) {
  test(`run ${state}: ${word ? `the strip reads ${word}` : "there is no strip"}`, () => {
    mountWith({ state, target: "M 31", plan_name: "night1", progress: PROGRESS });
    if (word === null) {
      assert(strip() == null, `a strip is drawn for a run that is ${state}`);
      return;
    }
    assert(strip() != null, `no strip while the run is ${state}: the glance line is gone`);
    const t = String(strip().textContent);
    assert(word.test(t), `the strip does not say ${word} while the run is ${state}: "${t}"`);
    assert(/frame 3\/24/.test(t), `the strip lost the frame ordinal while the run is ${state}: "${t}"`);
    assert(/9%/.test(t), `the strip lost the percentage while the run is ${state}: "${t}"`);
  });
}

test("a hold does not read as paused: the operator did not pause it", () => {
  mountWith({ state: "holding", target: "M 31", progress: PROGRESS });
  assert(strip() != null, "no strip during the hold");
  assert(!/paused/i.test(String(strip().textContent)),
    `the hold is labelled paused: "${strip().textContent}"`);
});

test("a strip mounted before a hold stays when the hold begins and goes when the run ends", () => {
  mountWith({ state: "running", target: "M 31", progress: PROGRESS });
  assert(strip() != null, "precondition: the strip shows while running");
  act(() => { useStore.setState({ sequence: { state: "holding", progress: PROGRESS } } as never); });
  assert(strip() != null, "the strip vanished when the run went into a cloud hold");
  act(() => { useStore.setState({ sequence: { state: "complete", progress: PROGRESS } } as never); });
  assert(strip() == null, "the strip outlived the run");
});

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19SequenceRunStripLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
