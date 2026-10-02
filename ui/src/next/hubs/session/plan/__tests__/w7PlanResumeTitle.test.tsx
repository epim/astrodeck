// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7PlanResumeTitle.test.tsx - the plan editor's resume card titles itself by
// D-13 (#487, owner-approved 2026-09-30), the same helper Interrupted.tsx and
// RecoveryCards.tsx use.
//
//   Run:  node --import tsx src/next/hubs/session/plan/__tests__/w7PlanResumeTitle.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// GAP CLOSED (W7 follow-on, WP-52, D-13, owner-approved 2026-09-30):
// `planModel.ts`'s `useRecoverable()` now reads `end_reason` off
// `GET /api/sequence/recoverable` and puts it on `Recoverable` itself, so
// this card's `rec.end_reason` is the server's real value in production,
// not always `undefined` as it was before (`w7PlanResumeMapping.test.ts`
// tests that mapping, through `useRecoverable()`, not a hand-built `rec`).
// The cases below still construct `rec` objects directly, to prove THIS
// component's own title logic is correct in isolation from the hook, plus
// a control pinning the no-`end_reason` fallback (an older server, or a
// caller that cannot answer the field) so that fallback does not silently
// start reading wrong.
//
// NAMED MUTANT, run from a byte backup of PlanResume.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   M1 "title hardcoded again" (`{resumeTitle(rec.end_reason)}` reverted to
//      the literal "RESUME INTERRUPTED RUN")

/* eslint-disable @typescript-eslint/no-explicit-any */

// `PlanResume.tsx` imports `sendControl` from `../now` (the SESSION/NOW
// barrel), which pulls in modules that import `now.css`. Node has no idea what
// a `.css` file is, so a load hook answers with an empty module - the same
// stub `nowDom.test.tsx` and `sessionSheets.test.tsx` use.
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
const { useStore } = await import("../../../../../store");
const { PlanResume } = await import("../PlanResume");

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};

let passed = 0;
let failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

act(() => { useStore.setState({ principal: OPERATOR } as never); });

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

/** A made-up recoverable record - not the real site (project rule) - with
 *  whatever `end_reason` the case under test wants to see threaded through. */
function rec(over: Record<string, unknown> = {}): any {
  return {
    sessionId: "sess-1", name: "M31 2x2 desk", frames_done: 2, frames_total: 80,
    ts: 1_757_000_000, ...over,
  };
}

function titleFor(r: any): string {
  act(() => { root.render(createElement(PlanResume, { rec: r, onDone: () => {} })); });
  const card = container.querySelector('[data-testid="plan-resume-card"]') as any;
  assert(card != null, "the plan-resume card never rendered - the fixture is wrong, not the title");
  const label = card.querySelector(".nx-label") as any;
  assert(label != null, "the card rendered with no Label element at all");
  return (label.textContent ?? "").trim();
}

test("given end_reason, an operator's own STOP titles the card RESUME STOPPED RUN", () => {
  const title = titleFor(rec({ end_reason: "aborted" }));
  assert(title === "RESUME STOPPED RUN", `a STOP was not titled as a stop: "${title}"`);
});

// The case WP-44 exists for: once this card's data carries end_reason, a
// polite server shutdown must still read as an interruption, not a stop.
test("given end_reason, a WP-44 shutdown titles the card RESUME INTERRUPTED RUN", () => {
  const title = titleFor(rec({ end_reason: "shutdown" }));
  assert(title === "RESUME INTERRUPTED RUN",
    `a polite server shutdown was titled as an operator STOP: "${title}"`);
});

test("given end_reason, a restart titles the card RESUME INTERRUPTED RUN", () => {
  const title = titleFor(rec({ end_reason: "restart" }));
  assert(title === "RESUME INTERRUPTED RUN", `a restart's title changed: "${title}"`);
});

// CONTROL: a `rec` with no `end_reason` at all (an older server, or a caller
// that still cannot answer the field) falls back to INTERRUPTED, never
// STOPPED - resumeTitle.ts's own rule, pinned here against this component.
test("control: no end_reason at all still titles the card RESUME INTERRUPTED RUN", () => {
  const title = titleFor(rec());
  assert(title === "RESUME INTERRUPTED RUN",
    `the fallback for today's real (end_reason-less) data changed: "${title}"`);
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w7PlanResumeTitle: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
