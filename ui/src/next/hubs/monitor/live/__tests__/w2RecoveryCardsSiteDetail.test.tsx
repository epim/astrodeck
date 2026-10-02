// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2RecoveryCardsSiteDetail.test.tsx - WP-17 (b) on the #/next Monitor's RUN
// ARMED card (#258).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/live/__tests__/w2RecoveryCardsSiteDetail.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/sequence/resume-arm`'s `hold.site_detail` carries the numbers
// behind a words-only `hold.reason` (#233). `RunArmedCard`'s NEXT row rendered
// `reason` alone, so an operator entitled to the numbers (the server already
// withholds the key from a viewer) never saw them here either.
//
// NAMED MUTANT, run from a byte copy of RecoveryCards.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   R1 "show reason alone"   the site_detail branch removed from NEXT

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
win.fetch = () => Promise.reject(new Error("offline in this test"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
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
const { RunArmedCard } = await import("../RecoveryCards");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};
// A made-up target and made-up numbers - not the real site (project rule).
const SITE_DETAIL =
  "M31 is at 12 deg, below its 20 deg start floor (it reaches 20 deg in about 2 h)";

function arm(over: Record<string, unknown> = {}): any {
  return { armed: ARMED, hold: null, recovering: false, recovery: null, ...over };
}
function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
set({ sequence: { state: "idle" }, resumeArm: arm(), armedBannerDismissed: null });
act(() => { root.render(createElement(RunArmedCard)); });

const card = () => container.querySelector('[data-testid="run-armed"]') as any;
function row(key: string): string | null {
  const c = card();
  if (!c) return null;
  for (const r of c.querySelectorAll(".nx-incident-row")) {
    if (r.querySelector(".nx-incident-key")?.textContent === key) {
      return r.querySelector(".nx-incident-val")?.textContent ?? "";
    }
  }
  return null;
}

test("precondition: the RUN ARMED card is up for an armed session", () => {
  assert(card() != null, "no RUN ARMED card - the fixture is wrong, not the component");
});

test("control: a stale hold with no site_detail shows NEXT with the reason alone", () => {
  set({ resumeArm: arm({ hold: STALE_HOLD }) });
  assert(card() != null, "precondition: the card went away");
  const next = row("NEXT") ?? "";
  assert(next === `Holding: ${STALE_HOLD.reason}. It starts by itself when that clears.`,
    `the plain hold (no site_detail) changed shape: "${next}"`);
});

test("a hold's site_detail is carried in NEXT, for an entitled principal (#258)", () => {
  // R1 "show reason alone", observed:
  //   x a hold's site_detail is carried in NEXT, for an entitled principal
  //   (#258): site_detail is missing from NEXT even though the server sent it
  set({ resumeArm: arm({ hold: { ...STALE_HOLD, site_detail: SITE_DETAIL } }) });
  const next = row("NEXT") ?? "";
  assert(next === `Holding: ${STALE_HOLD.reason} - ${SITE_DETAIL}. It starts by itself when that clears.`,
    `site_detail is missing from NEXT even though the server sent it: "${next}"`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w2RecoveryCardsSiteDetail: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
