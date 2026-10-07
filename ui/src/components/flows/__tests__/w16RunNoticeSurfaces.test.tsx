// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RunNoticeSurfaces.test.tsx - RUN's Off-flow notice reaches the screen
// (#195, backlog WP-118; wave 16 integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16RunNoticeSurfaces.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WP-118 built `runCopy`'s `notice` and tested it as a function
// (w16RunCopyOff.test.ts) but no surface printed it: `useFlowRunControls`
// called `runCopy(name, progress, running)` without the fourth argument, so
// the notice was "" everywhere. This file MOUNTS the three RUN surfaces over
// one store and grades what they print, so the function cannot be right
// while the screen says nothing (the "built, tested, unreachable" class).
//
// WHAT IS GUARDED
//
//   1. A flow whose DUSK WINDOW has Automatic resume Off: the #/next canvas
//      toolbar and the #/next phone stage sheet each carry the sentence as
//      a line (flow-canvas-run-notice, flow-stages-run-notice), and the
//      classic header's RUN button carries it as its hover text, on RUN and
//      on CONTINUE alike.
//   2. A flow that resumes (the default) carries it on none of them.
//   3. STOP carries it on none of them (it starts nothing).
//   4. The notice follows the graph ON THE CANVAS, not the saved record: an
//      unsaved edit of Automatic resume raises it, and takes it down again,
//      with no save and no reopen.
//   5. The button's own words do not move with the notice.
//
// NAMED MUTANTS (each run from a byte backup in the worktree, restored and
// sha256-compared; each leaves 2/6 passed except the last, 4/6; the first
// failure of each is quoted, the sentence being RESUME_OFF_LINE and "" a
// surface that printed nothing):
//
//   "resumes not passed": flowRunControls.tsx `runCopy(name, progress,
//     running, resumes)` made `runCopy(name, progress, running)`. RED:
//       x an Off flow on RUN: the toolbar, the phone sheet and the classic
//         RUN button all say it: Off, no session: RUN
//         got {"classic":"","toolbar":"","phone":""}
//   "toolbar line not mounted": FlowCanvasToolbar.tsx's flow-canvas-run-notice
//     BannerCard removed. RED, same case:
//         got {"classic":"<sentence>","toolbar":"","phone":"<sentence>"}
//   "phone line not mounted": FlowStagesPhoneSheet.tsx's
//     flow-stages-run-notice BannerCard removed. RED, same case: the phone
//     entry is "".
//   "classic title dropped": FlowHeader.tsx's RUN `title={copy.notice ...}`
//     made `title={undefined}`. RED, same case:
//         got {"classic":"","toolbar":"<sentence>","phone":"<sentence>"}
//   "graph read from the record": flowRunControls.tsx
//     `graphResumes(s.flows.graph)` made `graphResumes(s.flows.record?.graph)`
//     (the saved flow, not the canvas). RED, 4/6:
//       x the notice follows the graph on the canvas, not the saved record:
//         an unsaved edit to Off raises the notice
//         got {"classic":"","toolbar":"","phone":""}

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";
import type { FlowProgress } from "../../../lib/flowsApi";

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 390 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v !== undefined) Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ fixture
// The progress route's recorded answer for a DORMANT session (so the button
// reads CONTINUE); a missing file FAILS the whole file.
const FIXTURE = "../../../../../server/tests/fixtures/flow_progress_continue.json";
let PROGRESS: FlowProgress;
try {
  PROGRESS = JSON.parse(readFileSync(new URL(FIXTURE, import.meta.url), "utf8") as string).response;
} catch (e) {
  throw new Error(`cannot read ${FIXTURE}, the recorded answer the CONTINUE cases use: `
    + `${(e as Error).message}`);
}
if (PROGRESS?.session?.status !== "dormant") throw new Error(`${FIXTURE}: premise: a dormant session`);

let answer: FlowProgress | null = PROGRESS;
g.fetch = async (url: string) => {
  let data: any = { ok: true };
  if (/\/progress$/.test(url)) data = JSON.parse(JSON.stringify(answer));
  else if (url === "/api/flows/compile") data = { plan: {}, structural: [], issues: [], unmapped: [] };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { createParams } = await import("../nodeDefs");
const { RESUME_OFF_LINE } = await import("../runCopy");
const { default: FlowHeader } = await import("../FlowHeader");
const { FlowCanvasToolbar } = await import("../../../next/hubs/session/flows/canvas/FlowCanvasToolbar");
const { FlowStagesPhoneSheet } = await import("../../../next/hubs/session/flows/canvas/FlowStagesPhoneSheet");

(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
useStore.setState({ flowsFetchCalHealth: async () => {}, flowsFetchTonight: async () => {} } as any);

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
  }
}

// -------------------------------------------------------------------- graphs
const graphWith = (autoResume: string | null) => ({
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0,
      params: { ...createParams("dusk"), ...(autoResume === null ? {} : { autoResume }) } },
    { id: "n2", type: "target", x: 240, y: 0, params: { ...createParams("target"), name: "M31" } },
    { id: "n3", type: "capture", x: 480, y: 0, params: { ...createParams("capture") } },
  ],
  edges: [{ id: "e1", from: "n2", fromPort: "target", to: "n3", toPort: "run" }],
});
const RECORD_ID = PROGRESS.flow_id;

/** `canvas` is the graph on screen, `saved` the record's: they differ only
 *  in the case that grades which one the notice follows. */
function seed(opts: {
  canvas: string | null; saved?: string | null; progress: FlowProgress | null; live?: boolean;
}): void {
  answer = opts.progress;
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.mount", "control.capture"] },
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      confirm: null,
      config: null,
      site: null,
      sequence: { state: "idle" } as never,
      flows: {
        ...FLOWS_INIT,
        record: { id: RECORD_ID, name: "M31 mosaic", folder: "My flows", tagline: "",
          graph: graphWith(opts.saved === undefined ? opts.canvas : opts.saved),
          created_ts: 1, updated_ts: 2, last_run: null, last_result: "", readonly: false },
        graph: graphWith(opts.canvas),
        progress: opts.progress === null ? null : JSON.parse(JSON.stringify(opts.progress)),
        sessionIds: opts.progress?.session ? [opts.progress.session.id] : [],
        run: opts.live
          ? { ...FLOWS_INIT.run, phase: "running", startedAt: Date.now() }
          : FLOWS_INIT.run,
        ui: { ...FLOWS_INIT.ui, screen: "editor" },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function mount(): Promise<void> {
  const box = (surface: string, child: any) =>
    createElement("div", { key: surface, "data-surface": surface }, child);
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(Fragment, null,
      box("classic", createElement(FlowHeader as any, { tier: "desktop" })),
      box("next-toolbar", createElement(FlowCanvasToolbar as any)),
      box("next-phone", createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD_ID }, depth: 0 })),
    ));
  });
  await settle();
}
const text = (el: any): string => String(el?.textContent ?? "").trim();
const inBox = (surface: string, sel: string): any =>
  container.querySelector(`[data-surface="${surface}"] ${sel}`);

/** What each surface prints of the notice: the classic RUN button's hover
 *  text (its title when that is the notice), and the two #/next lines. */
function printed(): { classic: string; toolbar: string; phone: string } {
  const btns = Array.from(container.querySelectorAll('[data-surface="classic"] button')) as any[];
  const run = btns.find((b) => /^(▶|■)\s*(RUN|STOP|CONTINUE)/.test(text(b)));
  assert(run != null, "premise: the classic header has a RUN button");
  const title = String(run.getAttribute("title") ?? "");
  return {
    classic: title === RESUME_OFF_LINE ? title : "",
    toolbar: text(inBox("next-toolbar", '[data-testid="flow-canvas-run-notice"]')),
    phone: text(inBox("next-phone", '[data-testid="flow-stages-run-notice"]')),
  };
}
const NONE = { classic: "", toolbar: "", phone: "" };
const ALL = { classic: RESUME_OFF_LINE, toolbar: RESUME_OFF_LINE, phone: RESUME_OFF_LINE };

// ============================================================== the cases

// MUTANT "resumes not passed" (flowRunControls.tsx: the fourth argument of
// runCopy dropped). Observed, w16RunNoticeSurfaces.test 2/6: see the header.
// MUTANT "toolbar line not mounted", "phone line not mounted", "classic title
// dropped": the one surface goes blank, the other two keep the sentence.
await test("an Off flow on RUN: the toolbar, the phone sheet and the classic RUN button all say it", async () => {
  seed({ canvas: "Off", progress: null });
  await mount();
  eq(printed(), ALL, "Off, no session: RUN");
  eq(text(inBox("next-toolbar", '[data-testid="flow-run"]')), "RUN", "the button is still just RUN");
});

await test("an Off flow on CONTINUE: the same sentence, and the button's own words do not move", async () => {
  seed({ canvas: "Off", progress: PROGRESS });
  await mount();
  eq(printed(), ALL, "Off, dormant session: CONTINUE");
  const words = text(inBox("next-toolbar", '[data-testid="flow-run"]'));
  assert(/^CONTINUE\b/.test(words), `premise: the toolbar button reads CONTINUE, got "${words}"`);
  assert(!words.includes("Automatic resume"), `the notice is beside the button, not in it: "${words}"`);
});

await test("a flow that resumes carries the notice on no surface, on RUN or on CONTINUE", async () => {
  for (const [what, progress] of [["RUN", null], ["CONTINUE", PROGRESS]] as const) {
    for (const canvas of [null, "On"]) {
      seed({ canvas, progress });
      await mount();
      eq(printed(), NONE, `autoResume ${canvas ?? "unset"}: ${what}`);
    }
  }
});

await test("STOP carries no notice, whatever the flow says", async () => {
  seed({ canvas: "Off", progress: PROGRESS, live: true });
  await mount();
  eq(printed(), NONE, "Off, live run: STOP starts nothing");
  assert(/STOP/.test(text(inBox("next-toolbar", '[data-testid="flow-run"]'))),
    "premise: the toolbar button reads STOP");
});

// MUTANT "graph read from the record" (graphResumes over the saved record's
// graph). Observed: the first cases below go red, the saved flow resumes.
await test("the notice follows the graph on the canvas, not the saved record", async () => {
  // Saved On, edited to Off and not saved: the notice stands now.
  seed({ canvas: "Off", saved: "On", progress: null });
  await mount();
  eq(printed(), ALL, "an unsaved edit to Off raises the notice");
  // Saved Off, edited back to On and not saved: the notice is gone now.
  seed({ canvas: "On", saved: "Off", progress: null });
  await mount();
  eq(printed(), NONE, "an unsaved edit back to On takes it down");
});

await test("editing Automatic resume in place raises and lowers the notice with no reopen", async () => {
  seed({ canvas: "On", progress: null });
  await mount();
  eq(printed(), NONE, "premise: On says nothing");
  act(() => {
    const s = useStore.getState();
    useStore.setState({ flows: { ...s.flows, graph: graphWith("Off") } } as never);
  });
  await settle();
  eq(printed(), ALL, "after the edit to Off");
  act(() => {
    const s = useStore.getState();
    useStore.setState({ flows: { ...s.flows, graph: graphWith("On") } } as never);
  });
  await settle();
  eq(printed(), NONE, "after the edit back to On");
});

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w16RunNoticeSurfaces.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
