// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7InterruptedTitle.test.tsx - the Interrupted card's title follows D-13
// (#487, owner-approved 2026-09-30) through the shared `resumeTitle` helper,
// not a literal string of its own.
//
//   Run:  node --import tsx src/next/hubs/session/now/__tests__/w7InterruptedTitle.test.tsx   (from ui/)
//
// This card already had its cause SENTENCE right (test_h4_recoverable_says_why
// / nowDom.test.tsx section 12); this file is only about the TITLE above it,
// which until this WP was the literal "RESUME INTERRUPTED RUN" no matter what
// `end_reason` said - so a STOP after two banked subs was titled the same as a
// crash.
//
// NAMED MUTANT, run from a byte backup of Interrupted.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   M1 "title hardcoded again" (`{resumeTitle(rec.end_reason)}` reverted to the
//      literal "RESUME INTERRUPTED RUN")

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.scrollTo = () => {};

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

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { Interrupted } = await import("../Interrupted");

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function settle(): Promise<void> {
  return new Promise((r) => setTimeout(r, 0));
}

/** The card's title text, mounted fresh over one recoverable answer. A fresh
 *  root each time: the fetch runs once on mount, and this card has no
 *  interval to pick up a later change to `RECOVERABLE`. */
async function titleFor(endReason: string | null | undefined): Promise<string> {
  RECOVERABLE = endReason === undefined
    ? { recoverable: true, session_id: "s1", name: "M31", frames_done: 2, frames_total: 80, ts: 1_757_000_000 }
    : {
      recoverable: true, session_id: "s1", name: "M31", frames_done: 2, frames_total: 80,
      ts: 1_757_000_000, end_reason: endReason,
    };
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({ principal: OPERATOR, sequence: { state: "idle" } } as never);
  });
  await act(async () => { root.render(createElement(Interrupted)); });
  await settle();
  await settle();
  const card = container.querySelector('[data-testid="now-interrupted"]') as any;
  assert(card != null, "the interrupted card never rendered for end_reason "
    + `${JSON.stringify(endReason)} - the fixture is wrong, not the title`);
  const label = card.querySelector(".nx-label") as any;
  assert(label != null, "the card rendered with no Label element at all");
  return (label.textContent ?? "").trim();
}

async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

await test("an operator's own STOP titles the card RESUME STOPPED RUN", async () => {
  const title = await titleFor("aborted");
  assert(title === "RESUME STOPPED RUN",
    `a STOP after two banked subs was not titled as a stop: "${title}"`);
});

// CONTROL: a restart is still titled as an interruption, same as before this
// WP - the split only pulls "aborted" out, it does not touch the rest.
await test("control: a restart still titles the card RESUME INTERRUPTED RUN", async () => {
  const title = await titleFor("restart");
  assert(title === "RESUME INTERRUPTED RUN", `a restart's title changed: "${title}"`);
});

// A WP-44 shutdown is a restart in D-13's sense: the operator did not choose
// it, so it must not read as a STOP just because something is in end_reason.
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

// ------------------------------------------------------------------ tally
const total = passed + failed;
console.log(`w7InterruptedTitle: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
