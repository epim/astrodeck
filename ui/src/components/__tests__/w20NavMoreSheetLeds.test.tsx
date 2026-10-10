// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20NavMoreSheetLeds.test.tsx - the phone's More sheet, MOUNTED: the only LED
// any of its rows carries is Guide's, and no row keeps a private copy of the
// run states (#930, WP-174, wave 20).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/__tests__/w20NavMoreSheetLeds.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `OverflowRow` computed its dot with a rule for a row whose id is
// "sequence": error is bad, running is busy, paused is warn, everything else is
// off. That row was the Plan entry, which left the sheet in #239 stage B, so the
// rule could never render. It was also the hand-written `running || paused`
// that #922 retired everywhere else: a cloud hold or an abort's wind-down read
// as no run at all. Brought back as a row it would have drawn a run that is
// holding or stopping as off. The branch is deleted; a row that wants the run's
// state calls `runIsLive` and brings its own test.
//
// WHAT CAN AND CANNOT BE SEEN. With the shipped row list the deletion changes
// nothing a user sees, so tests 1 and 2 are CONTROLS that pass before and after:
// they pin that the deletion took nothing else with it (Guide's dot follows the
// guider, every other dot is off whatever the engine does). The test that
// fails on the old text is test 3, which puts a row back under the retired id
// and reads its dot over the whole state union. It restores the list in a
// `finally`.
//
// MUTANT "the dead branch is back" (`OverflowRow`'s one-line `led` selector
// replaced by the unfixed text, byte for byte, from `git show HEAD:`). Run from
// a byte backup of NavMoreSheet.tsx, restored with a byte copy (md5sum
// compared): see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// A phone: the sheet renders null at >= 640 px.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const NavMoreSheetModule = await import("../NavMoreSheet");
const NavMoreSheet = NavMoreSheetModule.default;
const { OVERFLOW_VIEWS } = NavMoreSheetModule;
type SeqState = import("../../types").SequenceState["state"];

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

/** Every state of the union. A `Record`, so a new member does not compile until
 *  it is placed here. */
const STATES: Record<SeqState, true> = {
  idle: true, running: true, paused: true, holding: true, aborting: true,
  complete: true, aborted: true, error: true, nina_native: true,
};
const ALL_STATES = Object.keys(STATES) as SeqState[];

/** The sheet, open on a phone, with the engine in `state` and the guider
 *  `guiding` or not. Returns each row's label and the LED variant it draws. */
function rowsAt(state: SeqState, guiding: boolean): Array<{ label: string; led: string }> {
  act(() => {
    useStore.setState({
      view: "monitor",
      sequence: { state },
      status: { guider: { guiding, name: "native" } },
    } as never);
  });
  act(() => {
    root.render(createElement(NavMoreSheet, { open: true, onClose: () => {} }));
  });
  const rows: Array<{ label: string; led: string }> = [];
  for (const b of Array.from(win.document.querySelectorAll("#ad-overlay-root button")) as any[]) {
    const led = b.querySelector(".led");
    if (!led) continue;
    const cls = Array.from(led.classList as Iterable<string>).find((c) => c.startsWith("led-"));
    rows.push({ label: String(b.textContent).trim(), led: cls ?? "(no variant)" });
  }
  act(() => { root.render(createElement("div")); });
  return rows;
}

// ------------------------------------------------- 1 and 2: the controls
test("the sheet draws one row per overflow view, each with a dot", () => {
  const rows = rowsAt("idle", false);
  assert(rows.length === OVERFLOW_VIEWS.length,
    `${rows.length} rows with a dot for ${OVERFLOW_VIEWS.length} views: ${JSON.stringify(rows)}`);
});

test("no row's dot follows the run: every one is off in every state, the guider idle", () => {
  for (const state of ALL_STATES) {
    const lit = rowsAt(state, false).filter((r) => r.led !== "led-off");
    assert(lit.length === 0,
      `a dot is lit while the run is ${state} and the guider is idle: ${JSON.stringify(lit)}`);
  }
});

test("Guide's dot follows the guider whatever the run does, and no other dot lights", () => {
  for (const state of ALL_STATES) {
    const rows = rowsAt(state, true);
    const lit = rows.filter((r) => r.led !== "led-off");
    assert(lit.length === 1 && /Guide/.test(lit[0].label) && lit[0].led === "led-on",
      `while the run is ${state} and the guider is guiding, the lit dots are ${JSON.stringify(lit)}`);
  }
});

// ------------------------------------------- 3: the retired row, put back
test("a row under the retired 'sequence' id has no dot rule of its own: off in every state", () => {
  const retired = { id: "sequence" as const, label: "RetiredPlan", icon: "guide" as const };
  OVERFLOW_VIEWS.push(retired);
  try {
    for (const state of ALL_STATES) {
      const row = rowsAt(state, false).find((r) => /RetiredPlan/.test(r.label));
      assert(row != null, `the injected row never rendered while the run is ${state} - the harness is wrong`);
      assert(row!.led === "led-off",
        `a row keyed "sequence" draws ${row!.led} while the run is ${state}: the sheet kept a `
        + "private copy of the run states. A row that wants the run calls runIsLive.");
    }
  } finally {
    OVERFLOW_VIEWS.splice(OVERFLOW_VIEWS.indexOf(retired), 1);
  }
  assert(!OVERFLOW_VIEWS.some((v) => v.id === ("sequence" as string)), "the injected row was not removed");
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w20NavMoreSheetLeds: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
