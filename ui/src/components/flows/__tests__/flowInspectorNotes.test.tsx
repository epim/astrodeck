// flowInspectorNotes.test.tsx — the inspector's two compile lists, MOUNTED.
//
//   Run directly:  npx tsx src/components/flows/__tests__/flowInspectorNotes.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `unmapped[]` carries two statements that contradict each other, and until now
// they printed as one list under one heading:
//
//   warn / danger  this will not happen
//   note           this DOES happen, by another part of the engine
//
// Every note row therefore appeared under NOT HONOURED BY A RUN, in warn amber,
// saying it was honoured — and `/api/flows/{id}/start` made the operator accept
// the lot before it would run. Three rows in the shipped campaign example are
// notes: the cloud hold releases itself, the hold shoots its own darks, and the
// scheduler advances the pool.
//
// A pure filter is not enough to pin this. The bug was never in a predicate; it
// was in the renderer, which hardcoded `level === "danger" ? bad : warn` and so
// would have inked a note amber no matter how correctly it was classified. The
// only place that is visible is the DOM, so this mounts the column.

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
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { useStore } = await import("../../../store");
const FlowInspector = (await import("../FlowInspector")).default;
type FlowUnmapped = import("../../../lib/flowsApi").FlowUnmapped;

// ------------------------------------------------------------------- fixture
// One row of each level, and the details are distinguishable so a test can say
// WHICH row landed in the wrong list rather than only that a count was off.
const LOSS: FlowUnmapped = {
  key: "instructions[on_night_end -> parkclose]",
  detail: "LOSSROW this rule will not run: the engine has no 'parkclose' action",
  level: "warn",
};
const ROOF: FlowUnmapped = {
  key: "automation.dome",
  detail: "ROOFROW nothing will close the shutter on an unsafe reading",
  level: "danger",
};
const NOTE: FlowUnmapped = {
  key: "instructions[on_target_complete -> pool.advance]",
  detail: "NOTEROW the scheduler advances the pool itself",
  level: "note",
};

const root = createRoot(win.document.getElementById("root"));
const container = win.document.getElementById("root");

function setUnmapped(unmapped: FlowUnmapped[]): void {
  act(() => {
    useStore.setState({
      flows: {
        ...useStore.getState().flows,
        sel: null,
        compiled: { issues: [], unmapped, structural: [] },
      },
    } as any);
  });
  act(() => { root.render(React.createElement(FlowInspector, {})); });
}

/** The rendered row whose text carries `tag`, or null.
 *
 *  The DEEPEST matching div, not the first: a danger row wraps its detail
 *  beside a `<span>DANGER</span>`, so "the div with no element children" finds
 *  the warn and note rows and misses exactly the one that matters most. */
function row(tag: string): any {
  const hits = [...container.querySelectorAll("div")]
    .filter((d: any) => d.textContent.includes(tag));
  return hits.length ? hits[hits.length - 1] : null;
}

/** The `.label` heading text of the section a row sits in. A row's section is
 *  its parent column; the heading is that column's first child. */
function sectionOf(tag: string): string {
  const r = row(tag);
  if (!r) throw new Error(`no row rendered for ${tag}`);
  return (r.parentElement.querySelector(".label")?.textContent ?? "").trim();
}

// --------------------------------------------------------------------- tests
test("a note is not filed under NOT HONOURED BY A RUN", () => {
  setUnmapped([LOSS, NOTE]);
  assert.equal(sectionOf("NOTEROW"), "ANSWERED ANOTHER WAY",
    "a row saying the engine DOES do this is filed under a heading saying it "
    + "does not — which is the contradiction an operator reads at 3 a.m.");
  assert.equal(sectionOf("LOSSROW"), "NOT HONOURED BY A RUN",
    "a real loss moved out of the losses list");
});

test("a note is inked dim, not warn amber", () => {
  setUnmapped([LOSS, NOTE]);
  const cls = (t: string) => row(t).getAttribute("class") ?? "";
  assert.ok(cls("NOTEROW").includes("text-dim"),
    `the note row is not dim: ${cls("NOTEROW")}`);
  assert.ok(!cls("NOTEROW").includes("text-warn"),
    "the note row still carries warn amber, so it reads as a problem");
  assert.ok(cls("LOSSROW").includes("text-warn"),
    `a real loss stopped being amber: ${cls("LOSSROW")}`);
});

test("danger keeps its ink AND its word", () => {
  // Colour is never the only channel here (the header comment on ISSUE_INK):
  // the night palette collapses warn and bad toward coral, so the WORD is what
  // actually separates them on a red-mode screen at night.
  setUnmapped([ROOF, NOTE]);
  const r = row("ROOFROW");
  assert.ok((r.getAttribute("class") ?? "").includes("text-bad"),
    "the dome row lost its bad ink");
  assert.ok(r.parentElement.textContent.includes("DANGER"),
    "the dome row lost the literal word DANGER");
});

test("a flow whose only unmapped rows are notes shows no losses heading", () => {
  setUnmapped([NOTE]);
  const headings = [...container.querySelectorAll(".label")]
    .map((e: any) => e.textContent.trim());
  assert.ok(!headings.includes("NOT HONOURED BY A RUN"),
    "an empty losses list still prints its heading, so a clean flow looks "
    + `like it has problems: ${headings.join(" | ")}`);
  assert.ok(headings.includes("ANSWERED ANOTHER WAY"),
    "the notes went missing entirely — they are worth reading, just not "
    + "worth blocking on");
});

test("no unmapped rows at all prints neither heading", () => {
  setUnmapped([]);
  const headings = [...container.querySelectorAll(".label")]
    .map((e: any) => e.textContent.trim());
  assert.ok(!headings.includes("ANSWERED ANOTHER WAY")
            && !headings.includes("NOT HONOURED BY A RUN"),
    `a flow with nothing to report printed a heading: ${headings.join(" | ")}`);
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowInspectorNotes.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
