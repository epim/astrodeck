// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5_5MaxRunCopy.test.tsx - the classic Schedule editor says what happens when
// a target's "max run" is reached (#1207, WP 5.5, owner ruling option b).
// MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/w5_5MaxRunCopy.test.tsx
//
// THE CLAIMS, each a row of the engine (`SequenceEngine._schedule_loop`,
// `_enforce_stop_boundary`): the cap counts from the target's first attempt,
// setup included (`_first_attempt`); at the cap the target's window closes and
// it is SKIPPED (`WindowClosed` is a `StopTarget`, caught as "skipped" and
// removed from the run's remaining targets); the run goes on to the next
// target, which gets its own allowance (`test_w9c_max_run_min_first_attempt.py`).
// "For the rest of the run", not "of the night": a restart or an auto-resume
// the same night starts a new run, which counts the target afresh. The
// sentence says nothing of how the run is later labelled: that label is #1239.
//
// NAMED MUTANT "sentence removed": the max-run note deleted from
// SchedulePanel.tsx. RED, observed:
//   x the max-run row says nothing about what the cap does: no note on the row

/* eslint-disable @typescript-eslint/no-explicit-any */

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
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { defaultSchedule } = await import("../../../store");
const SchedulePanel = (await import("../SchedulePanel")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function openPanel(): void {
  act(() => {
    root.render(createElement(SchedulePanel, {
      schedule: defaultSchedule(), disabled: false, onChange: () => {},
    }));
  });
  const toggle = container.querySelector('button[aria-label^="Edit schedule"]');
  assert(toggle, "the schedule disclosure never rendered");
  act(() => { toggle.click(); });
}

test("the max-run row says what the cap does when it is reached", () => {
  openPanel();
  assert(container.querySelector('[aria-label="Start mode"]') != null,
    "premise: the editor did not open");
  const note: string = container.querySelector('[data-testid="schedule-max-run-note"]')
    ?.textContent ?? "";
  assert(note !== "", "the max-run row says nothing about what the cap does: no note on the row");
  assert(note.includes("first attempt, so setup counts"),
    `the note does not say where the count starts: ${note}`);
  assert(note.includes("skipped for the rest of the run"),
    `the note does not say the target is skipped for the rest of the run: ${note}`);
  assert(note.includes("goes on to the next target"),
    `the note does not say the run goes on: ${note}`);
  assert(!/dawn/i.test(note),
    `the note teaches the dawn-cutoff end label, which is #1239's defect: ${note}`);
});

act(() => { root.unmount(); });
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw5_5MaxRunCopy.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
