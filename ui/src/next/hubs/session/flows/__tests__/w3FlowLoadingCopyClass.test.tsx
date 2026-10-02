// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3FlowLoadingCopyClass.test.tsx - the #592 class, in the two places WP-61
// did not reach (WP-61 new defect, backlog wave 3 integration).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/w3FlowLoadingCopyClass.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. #592 fixed the Sky flow card's loading copy
// (`sky/sheets/flow.tsx`'s `FLOW_CARD_LOADING`/`FLOW_CARD_RUN_LOADING`,
// `w3FlowCardLoadingCopy.test.tsx`): while a card waits for the open it asked
// for, its hint must name a cause the title does not already show, and a
// locked RUN/SAVE reason must say what is missing, not just repeat "loading".
// Two siblings built on the same "waiting for ?open=" pattern still carried
// the pre-#592 shape:
//
//   * `FlowStagesPhoneSheet.tsx`'s `FLOW_STAGES_LOADING` hint ("This flow's
//     stages show once it has loaded") and `FLOW_STAGES_LOADING_REASON` RUN/
//     SAVE reason ("Loading this flow...") both repeated the "OPENING THIS
//     FLOW" title or said nothing about what RUN/SAVE lacked.
//   * `FlowsCanvasHost.tsx`'s waiting-card hint was inlined as "This flow's
//     canvas shows once it has loaded" - the same repetition.
//
// THE FIX, in the shape WP-61 used: a hint names the cause (the open this
// screen asked for has not answered yet), and the locked reason says what is
// missing (nothing here to run or save).
//
// Convention: mounting pattern from `w1FlowStagesWaitForOpen.test.tsx` and
// `w1FlowsCanvasHostWaitForOpen.test.tsx`; the "never answers" fetch double
// from `w3FlowCardLoadingCopy.test.tsx`, to stay in the STILL LOADING branch
// of `waiting` rather than its failed-open branch.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every request hangs forever: both screens under test are the ones still
// waiting for their open to answer, and a resolved fetch would carry either
// one past that state before the assertions run.
g.fetch = () => new Promise(() => {});

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FlowStagesPhoneSheet, FLOW_STAGES_LOADING } = await import("../canvas/FlowStagesPhoneSheet");
const { FlowsCanvasHost, CANVAS_LOADING_HINT } = await import("../FlowsCanvasHost");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const settle = async (n = 6) => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];
function seedStore(): void {
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

// ===================================================== 1. FlowStagesPhoneSheet

await test(
  "FlowStagesPhoneSheet: the loading hint names the open, not the title, as the reason",
  async () => {
    seedStore();
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(FlowStagesPhoneSheet as any,
        { params: { open: "flow-never-answers" }, depth: 0 }));
    });
    await settle();
    try {
      const waiting = tid("flow-stages-waiting");
      assert(waiting != null, "no waiting card for a flow whose open never answers");
      eq(FLOW_STAGES_LOADING,
        "The open this sheet asked for has not answered yet, so there is "
        + "nothing here to show.",
        "hint text:");
      assert(String(waiting.textContent).includes(FLOW_STAGES_LOADING),
        `the waiting card does not render FLOW_STAGES_LOADING as its hint: "${waiting.textContent}"`);
    } finally {
      await act(async () => { root.unmount(); });
    }
  },
);

await test(
  "FlowStagesPhoneSheet: RUN and SAVE's locked reason says what is missing, not 'loading'",
  async () => {
    seedStore();
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(FlowStagesPhoneSheet as any,
        { params: { open: "flow-never-answers" }, depth: 0 }));
    });
    await settle();
    try {
      const WANT = "This flow has not loaded yet, so there is nothing here to run or save.";
      const run = tid("flow-stages-run");
      assert(run != null, "no RUN button: the fixture is wrong, not the component");
      eq(run.getAttribute("aria-disabled"), "true", "RUN must be locked while the open is still out");
      eq(run.getAttribute("title"), WANT, "RUN's reason:");
      const save = tid("flow-stages-save");
      assert(save != null, "no SAVE button: the fixture is wrong, not the component");
      eq(save.getAttribute("aria-disabled"), "true", "SAVE must be locked too");
      eq(save.getAttribute("title"), WANT, "SAVE's reason:");
    } finally {
      await act(async () => { root.unmount(); });
    }
  },
);

// ========================================================= 2. FlowsCanvasHost

await test(
  "FlowsCanvasHost: the loading hint names the open, not the title, as the reason",
  async () => {
    seedStore();
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(FlowsCanvasHost as any, { open: "flow-never-answers" }));
    });
    await settle();
    try {
      const waiting = tid("flows-canvas-waiting");
      assert(waiting != null, "no waiting card for a flow whose open never answers");
      eq(CANVAS_LOADING_HINT,
        "The open this host asked for has not answered yet, so there is "
        + "nothing here to show.",
        "hint text:");
      assert(String(waiting.textContent).includes(CANVAS_LOADING_HINT),
        `the waiting card does not render CANVAS_LOADING_HINT as its hint: "${waiting.textContent}"`);
    } finally {
      await act(async () => { root.unmount(); });
    }
  },
);

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3FlowLoadingCopyClass.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
