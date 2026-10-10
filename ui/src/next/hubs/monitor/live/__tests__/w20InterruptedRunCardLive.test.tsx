// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20InterruptedRunCardLive.test.tsx - the Monitor's INTERRUPTED RUN card,
// MOUNTED over the whole sequence-state union: it is offered while no run is
// live and never while one is, a PAUSED run included (#931, WP-174, wave 20).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/live/__tests__/w20InterruptedRunCardLive.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The card handed `useRecoverable` a hand-written
// `running || holding || aborting`: no "paused". `GET /api/sequence/recoverable`
// answers from the session store ("any dormant session with frames") without
// looking at the engine, and a paused run's own session is ACTIVE, so the route
// names some OLDER dormant session (last night's STOP, the usual case on a
// multi-night campaign) and the Monitor drew "RESUME FROM FRAME n/m" for it
// directly above the paused run's own RESUME. Pressing it asks the engine to
// start a second run over a live one, which it refuses. A run in flight is not
// recoverable; the card now asks `runIsLive`, the one predicate for "a run is
// on" (#821, #922).
//
// WHAT IS WORTH ASSERTING. Per state in the union: whether the card is in the
// document AND whether the route was asked at all (the hook's contract is "only
// asked while nothing is running"). The map is a `Record` over the union, so a
// new state added to the type is a compile error here until someone decides what
// the card does for it. Then the edge: a card showing at idle goes the moment
// the run pauses, and comes back (a fresh ask) when the run ends.
//
// MUTANT "paused is not a run" (`const running = runIsLive(seq);` made
// `seq.state === "running" || seq.state === "holding" || seq.state === "aborting"`,
// the unfixed text). Run from a byte backup of RecoveryCards.tsx, restored with
// a byte copy (md5sum compared): see the report for the failing lines.

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

// `GET /api/sequence/recoverable` - the one route this card reads. It answers
// "recoverable" whatever the engine is doing, exactly as the real route does,
// and every ask is recorded so a state that must not ask can be told apart from
// one that asked and had its answer hidden.
const asked: string[] = [];
g.fetch = async (url: any) => {
  asked.push(String(url));
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => ({
      recoverable: true, name: "Last night", frames_done: 7, frames_total: 80,
      end_reason: "aborted",
    }),
    text: async () => "",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { InterruptedRunCard } = await import("../RecoveryCards");
type SeqState = import("../../../../../types").SequenceState["state"];

let passed = 0;
let failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function settle(): Promise<void> { return new Promise((r) => setTimeout(r, 0)); }
async function settleAll(): Promise<void> {
  for (let i = 0; i < 4; i++) await act(async () => { await settle(); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const card = (): any => container.querySelector('[data-testid="run-interrupted"]');
function set(state: SeqState): void {
  act(() => { useStore.setState({ sequence: { state } } as never); });
}
/** A fresh mount at `state`. The previous card is unmounted BEFORE the state
 *  moves, so its own effect cannot ask the route on this case's behalf. */
async function mountAt(state: SeqState): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  asked.length = 0;
  set(state);
  await act(async () => { root.render(createElement(InterruptedRunCard)); });
  await settleAll();
}

/** Is the card offered in each state of the union? A `Record`, so a new member of
 *  the union does not compile until it is placed here. */
const OFFERED: Record<SeqState, boolean> = {
  idle: true, complete: true, aborted: true, error: true, nina_native: true,
  running: false, paused: false, holding: false, aborting: false,
};

for (const state of Object.keys(OFFERED) as SeqState[]) {
  const offered = OFFERED[state];
  await test(
    offered
      ? `${state}: nothing is live, the card is offered`
      : `${state}: a run is live, the route is not asked and no card is drawn`,
    async () => {
      await mountAt(state);
      if (offered) {
        assert(asked.length > 0, `the route was never asked while the run is ${state}`);
        assert(card() != null, `no interrupted-run card while the run is ${state}`);
        return;
      }
      assert(asked.length === 0,
        `the route was asked while the run is ${state}: ${JSON.stringify(asked)}`);
      assert(card() == null,
        `an interrupted-run card is drawn over a run that is ${state}: `
        + `"${String(card()?.textContent).slice(0, 120)}"`);
    },
  );
}

await test("the card goes the moment a run pauses and returns, re-asked, when it ends", async () => {
  await mountAt("idle");
  assert(card() != null, "precondition: the card shows at idle");
  set("paused");
  await settleAll();
  assert(card() == null, "the card stayed over a run that has just been paused");
  asked.length = 0;
  set("aborted");
  await settleAll();
  assert(asked.length === 1, `the run's end did not re-ask the route once: ${JSON.stringify(asked)}`);
  assert(card() != null, "the card did not come back when the paused run ended");
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w20InterruptedRunCardLive: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
