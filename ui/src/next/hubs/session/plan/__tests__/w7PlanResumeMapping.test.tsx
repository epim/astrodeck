// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7PlanResumeMapping.test.tsx - planModel.ts's useRecoverable() carries
// end_reason off GET /api/sequence/recoverable onto Recoverable, the same
// field RecoveryCards.tsx's local hook already carried (W7 follow-on,
// WP-52, D-13, owner-approved 2026-09-30). w7PlanResumeTitle.test.tsx holds
// PlanResume's own title logic against a hand-built `rec`; this file holds
// the MAPPING itself, through the real hook, so a regression that drops
// end_reason on the floor in the fetch mapping (rather than in the title
// logic) cannot pass silently.
//
//   Run:  node --import tsx src/next/hubs/session/plan/__tests__/w7PlanResumeMapping.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// NAMED MUTANT, run from a byte backup of planModel.ts and restored
// byte-identical afterwards (sha256 checked). The observed failure is
// quoted at the test it turns red.
//   M1 "end_reason dropped on the floor" (`end_reason: r.end_reason ?? null,`
//      in useRecoverable's mapping changed to the constant `null`, so
//      `rec.end_reason` never carries the server's answer)

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

// `GET /api/sequence/recoverable` - the one route this hook reads. Mutable
// so each case can put a different `end_reason` on the answer.
let RECOVERABLE: any = { recoverable: false };
g.fetch = async (_url: any) => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => RECOVERABLE,
  text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useRecoverable } = await import("../planModel");
const { resumeTitle } = await import("../../now/resumeTitle");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function settle(): Promise<void> { return new Promise((r) => setTimeout(r, 0)); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

/** A tiny harness: calls the real hook and writes its `end_reason` and the
 *  title `resumeTitle` gives it onto the DOM, so the test reads planModel's
 *  own mapping rather than a hand-built `rec`. */
function Harness(): any {
  const { rec } = useRecoverable(false);
  return createElement("div", {
    "data-testid": "mapping",
    "data-end-reason": rec ? JSON.stringify(rec.end_reason) : "none",
    "data-title": rec ? resumeTitle(rec.end_reason) : "",
  });
}

/** The hook's mapped `end_reason` and the title it drives, mounted fresh
 *  over one recoverable answer (no interval here either: the hook re-reads
 *  only on the `running` edge, and a fresh mount starts from `false`). */
async function mappingFor(endReason: string | null | undefined): Promise<{
  endReason: unknown; title: string;
}> {
  RECOVERABLE = endReason === undefined
    ? { recoverable: true, name: "M31", frames_done: 2, frames_total: 80 }
    : { recoverable: true, name: "M31", frames_done: 2, frames_total: 80, end_reason: endReason };
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(Harness)); });
  await settle();
  await settle();
  const el = container.querySelector('[data-testid="mapping"]') as any;
  assert(el != null, `the harness never rendered a recoverable record for end_reason `
    + `${JSON.stringify(endReason)} - the fixture is wrong, not the mapping`);
  return { endReason: JSON.parse(el.getAttribute("data-end-reason")), title: el.getAttribute("data-title") };
}

await test("useRecoverable carries the server's end_reason onto Recoverable", async () => {
  const { endReason } = await mappingFor("aborted");
  assert(endReason === "aborted", `end_reason was not carried through: ${JSON.stringify(endReason)}`);
});

await test("an operator's own STOP, read through the real mapping, titles RESUME STOPPED RUN", async () => {
  const { title } = await mappingFor("aborted");
  assert(title === "RESUME STOPPED RUN", `a STOP was not titled as a stop: "${title}"`);
});

await test("a WP-44 shutdown, read through the real mapping, titles RESUME INTERRUPTED RUN", async () => {
  const { title } = await mappingFor("shutdown");
  assert(title === "RESUME INTERRUPTED RUN",
    `a polite server shutdown was titled as an operator STOP: "${title}"`);
});

await test("no end_reason at all maps to null and titles RESUME INTERRUPTED RUN", async () => {
  const { endReason, title } = await mappingFor(undefined);
  assert(endReason === null, `a missing end_reason should map to null, not ${JSON.stringify(endReason)}`);
  assert(title === "RESUME INTERRUPTED RUN", `an older server's answer changed the title: "${title}"`);
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w7PlanResumeMapping: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
