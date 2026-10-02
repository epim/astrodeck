// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7RecoveryCardsTitle.test.tsx - the Monitor's InterruptedRunCard titles
// itself by D-13 (#487, owner-approved 2026-09-30), the same helper
// Interrupted.tsx and PlanResume.tsx use.
//
//   Run:  node --import tsx src/next/hubs/monitor/live/__tests__/w7RecoveryCardsTitle.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Before this WP, `useRecoverable` (this file's own, local to the Monitor - a
// third, independent fetch of the same route Interrupted.tsx and PlanResume.tsx
// each read on their own) never read `end_reason` off the answer at all, so
// the title could not have been anything but the literal "RESUME INTERRUPTED
// RUN" it was. Both the plumbing (the fetch mapping) and the title itself
// needed fixing here; the two mutants below cover each.
//
// NAMED MUTANTS, run from a byte backup of RecoveryCards.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   M1 "end_reason dropped on the floor" (`end_reason: r.end_reason ?? null,`
//      in useRecoverable's mapping changed to the constant `null`, so
//      `rec.end_reason` never carries the server's answer)
//   M2 "title hardcoded again" (`title: resumeTitle(rec.end_reason)` reverted
//      to the literal "RESUME INTERRUPTED RUN")

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

// `GET /api/sequence/recoverable` - the one route this card reads. Mutable so
// each case can put a different `end_reason` on the answer.
let RECOVERABLE: any = { recoverable: false };
g.fetch = async (_url: any) => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => RECOVERABLE,
  text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { InterruptedRunCard } = await import("../RecoveryCards");

function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

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

/** The card's incident title, mounted fresh over one recoverable answer (no
 *  interval here either - the hook re-reads only on the `running` edge, and a
 *  fresh mount starts from `running: false`). */
async function titleFor(endReason: string | null | undefined): Promise<string> {
  RECOVERABLE = endReason === undefined
    ? { recoverable: true, name: "M31", frames_done: 2, frames_total: 80 }
    : { recoverable: true, name: "M31", frames_done: 2, frames_total: 80, end_reason: endReason };
  set({ sequence: { state: "idle" } });
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(InterruptedRunCard)); });
  await settle();
  await settle();
  const card = container.querySelector('[data-testid="run-interrupted"]') as any;
  assert(card != null, "the interrupted card never rendered for end_reason "
    + `${JSON.stringify(endReason)} - the fixture is wrong, not the title`);
  const title = card.querySelector(".nx-incident-title") as any;
  assert(title != null, "the card rendered with no title element at all");
  return (title.textContent ?? "").trim();
}

await test("an operator's own STOP titles the card RESUME STOPPED RUN", async () => {
  const title = await titleFor("aborted");
  assert(title === "RESUME STOPPED RUN", `a STOP was not titled as a stop: "${title}"`);
});

await test("control: a restart still titles the card RESUME INTERRUPTED RUN", async () => {
  const title = await titleFor("restart");
  assert(title === "RESUME INTERRUPTED RUN", `a restart's title changed: "${title}"`);
});

// The case this file exists for: WP-44's shutdown word must not be confused
// with an operator's STOP just because the Monitor now reads SOME end_reason.
await test("a WP-44 shutdown titles the card RESUME INTERRUPTED RUN, not stopped", async () => {
  const title = await titleFor("shutdown");
  assert(title === "RESUME INTERRUPTED RUN",
    `a polite server shutdown was titled as an operator STOP: "${title}"`);
});

await test("control: no end_reason at all still titles the card RESUME INTERRUPTED RUN", async () => {
  const title = await titleFor(undefined);
  assert(title === "RESUME INTERRUPTED RUN", `an older server's answer changed the title: "${title}"`);
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w7RecoveryCardsTitle: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
