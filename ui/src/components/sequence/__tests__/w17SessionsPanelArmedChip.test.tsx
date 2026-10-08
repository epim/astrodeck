// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17SessionsPanelArmedChip.test.tsx - the CLASSIC Sessions panel warns about
// "auto-resume armed without a safety monitor" only for a session that
// auto-resume could start (#838, the sibling of the #/next card's chip). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/w17SessionsPanelArmedChip.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #838 named the #/next SessionCard; this panel is the same claim on the
// classic UI, which is the root UI. It drew the line for ANY row with
// `auto_resume` and no monitor. `start()` arms every run, so every completed
// session on disk from before the engine began clearing the flag at completion
// (#838) still carries it, and each read as a standing hazard on a night that
// is over. `armed()` asks for a DORMANT session, so that is the only status the
// line is true of; a live session has already started.
//
// THE ROW IS THE ROUTE'S, NOT TYPED HERE: `sessionsListNights.recorded.json`
// (GET /api/sessions as recorded by server/tests/test_h4_sessions_list_counts_
// nights.py) holds a dormant, armed row; the cases override only `status` and
// `owed`, the two fields a finished or live session changes.
//
// NAMED MUTANT (run from a byte backup, restored byte-identically with sha256
// compared): "chip on any armed row" (SessionsPanel.tsx: `r.auto_resume &&
// r.status === "dormant" && noMonitor` made `r.auto_resume && noMonitor`). Run
// 2026-10-07 by the wave 17 verifier: "w17SessionsPanelArmedChip: 2/4 passed",
//   x a complete row that still carries auto_resume is not warned about: a
//     COMPLETE session was warned about starting in bad weather
//   x a live row is not warned about: a LIVE session was warned about starting
//     in bad weather

/* eslint-disable @typescript-eslint/no-explicit-any */

import { readFileSync } from "node:fs";

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ----------------------------------------------------------- the recording
// A missing or unreadable file FAILS the whole file, never skips it.
const RECORDED = "./sessionsListNights.recorded.json";
let recorded: any;
try {
  recorded = JSON.parse(readFileSync(new URL(RECORDED, import.meta.url), "utf8") as string);
} catch (e) {
  throw new Error(`cannot read ${RECORDED}, the route's row this panel is graded `
    + `against: ${(e as Error).message}`);
}
const BASE = recorded?.one_night?.row;
if (!BASE || BASE.status !== "dormant" || BASE.auto_resume !== true) {
  throw new Error("the recording does not hold a dormant, armed row");
}

// ------------------------------------------------------------- the fake rig
let served: any[] = [];
g.fetch = async (url: string) => {
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (url === "/api/sessions") return json({ sessions: served });
  const hit = served.find((r) => url === `/api/sessions/${r.id}`);
  if (hit) return json({ id: hit.id, name: hit.name, plan: { name: hit.name, targets: [] }, frames: [] });
  return json({ detail: "not in this test" }, 404);
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const SessionsPanel = (await import("../SessionsPanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

const WARNING = "auto-resume armed without a safety monitor";

/** Mount the panel with `row` as the only session and the safety monitor
 *  `connected` or not; answer whether the warning is on the page, after
 *  proving the card itself rendered (a blank page is not a clean one). */
async function warned(row: any, connected: boolean): Promise<boolean> {
  served = [row];
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({
      principal: { role: "operator", email: null, caps: ["view.status", "control.mount", "control.capture"] },
      sequence: { state: "idle" },
      safety: { connected, reading: null, streak: 0 },
      weather: null,
      confirm: null,
    } as never);
  });
  await act(async () => { root.render(createElement(SessionsPanel)); });
  await settle();
  assert(container.querySelectorAll('[data-testid="session-counts"]').length === 1,
    "precondition: the session's card did not render");
  return (container.textContent as string).includes(WARNING);
}

// ==================================================================== tests

await testAsync("control: a dormant armed row with no monitor IS warned about", async () => {
  assert(await warned(BASE, false),
    "the warning is gone from the one case it is true of (the cases below prove nothing)");
});

await testAsync("a dormant armed row with a monitor connected is not warned about", async () => {
  assert(!(await warned(BASE, true)), "the warning shows with a safety monitor connected");
});

await testAsync("a complete row that still carries auto_resume is not warned about", async () => {
  const done = { ...BASE, status: "complete", owed: 0, accepted: BASE.total };
  assert(!(await warned(done, false)),
    "a COMPLETE session was warned about starting in bad weather");
});

await testAsync("a live row is not warned about", async () => {
  const live = { ...BASE, status: "active" };
  assert(!(await warned(live, false)),
    "a LIVE session was warned about starting in bad weather");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w17SessionsPanelArmedChip: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
