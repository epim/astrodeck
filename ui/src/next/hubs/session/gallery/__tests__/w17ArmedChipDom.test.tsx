// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17ArmedChipDom.test.tsx - the "armed without a safety monitor" chip is for a
// session auto-resume can still START (#838).
//
//   Run directly:  npx tsx src/next/hubs/session/gallery/__tests__/w17ArmedChipDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The chip says "auto-resume armed without a safety monitor - rig may start in
// bad weather". That is a claim about a DORMANT session: `armed()` asks for
// dormant, and auto-resume starts it. `start()` arms every run, so a run that
// finished the plan used to leave a COMPLETE session carrying the flag, and the
// shelf drew a warning about a night that was over. The engine now clears the
// flag where a night completes (`_finalize_report`, pinned by
// test_w17_complete_clears_auto_resume.py); a file from before that, or a
// session read off another rig, can still carry it, so the card does not trust
// the flag alone.
//
// WHAT IS WORTH ASSERTING, on the mounted shelf with NO monitor connected:
//   a dormant armed card shows the chip (it is the case the chip exists for);
//   a complete card with autoResume true shows none;
//   a live (active) card shows none: the run has started, and it is the
//   Monitor's business, not a warning that it MAY start;
//   a dormant UNARMED card shows none (the flag is what arms it).
// and the same dormant card shows no chip once a monitor IS connected, so the
// test cannot pass by a chip that never renders.
//
// MUTANT "chip on any armed card" (SessionCard.tsx: `card.autoResume &&
// card.status === "dormant" && !monitorConnected` made `card.autoResume &&
// !monitorConnected`). Run from a byte backup in this worktree, restored
// byte-identically (sha256 compared): the result is quoted at the case below.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.WebSocket = class { static OPEN = 1; readyState = 0; close() {} addEventListener() {} send() {} };
win.scrollTo = () => {};

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

function row(id: string, name: string, over: Record<string, unknown> = {}): any {
  return {
    id, name, status: "dormant", created_ts: 1_756_900_000, updated_ts: 1_757_000_000,
    nights: 1, accepted: 3, total: 9, auto_resume: false, ...over,
  };
}
let ROWS: any[] = [];
g.fetch = async (url: any) => {
  const u = String(url);
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (u === "/api/sessions") return json({ sessions: ROWS });
  const one = /^\/api\/sessions\/([^/]+)$/.exec(u);
  if (one) {
    const r = ROWS.find((x) => x.id === one[1]) ?? row(one[1], one[1]);
    return json({
      id: r.id, schema_version: 1, name: r.name, created_ts: r.created_ts,
      updated_ts: r.updated_ts, status: r.status, nights: [], auto_resume: r.auto_resume,
      plan: { name: r.name, targets: [] }, frames: [],
    });
  }
  if (u.startsWith("/api/reports")) return json([]);
  if (u.startsWith("/api/gallery/nights")) return json({ current: "2026-09-08", nights: [], truncated: false });
  if (u.startsWith("/api/sequence/stack")) {
    return json({
      enabled: false, target: "", seq: 0, channels: [], frames: 0, integrated_s: 0,
      rejected: 0, mode: null, downsample: 2, has_image: false, render_age_s: null,
      backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
    });
  }
  if (u.startsWith("/api/plans")) return json([]);
  if (u.startsWith("/api/flows")) return json([]);
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { resetSessionsIndex } = await import("../sessionsIndex");
const { GalleryScreen } = await import("../GalleryScreen");
const { ARMED_WITHOUT_MONITOR_CHIP } = await import("../cardActions");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

function seed(monitor: boolean): void {
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "view.preview", "view.media", "control.mount", "control.capture"] } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: { connected: monitor, reading: null, streak: 0 } as never,
      status: null, equipConnected: true, wsPhase: "up", toasts: [],
    } as never);
  });
}
async function mountShelf(rows: any[]): Promise<void> {
  ROWS = rows;
  await act(async () => { root.render(createElement("div")); });
  resetSessionsIndex();
  await act(async () => { root.render(createElement(GalleryScreen as any)); });
  await settle();
}
const hasChip = (id: string): boolean => {
  const c = tid(`session-card-${id}`);
  assert(c != null, `no card ${id}: the fixture is wrong, not the component`);
  return (c.textContent || "").includes(ARMED_WITHOUT_MONITOR_CHIP);
};

const ROWS_ALL = [
  row("sd", "Dormant armed", { auto_resume: true }),
  row("sc", "Complete armed", { status: "complete", auto_resume: true }),
  row("sa", "Live armed", { status: "active", auto_resume: true }),
  row("su", "Dormant unarmed"),
];

seed(false);
await mountShelf(ROWS_ALL);

await testAsync("precondition: the shelf rendered the four cards", async () => {
  for (const id of ["sd", "sc", "sa", "su"]) assert(tid(`session-card-${id}`) != null, `no card ${id}`);
});

await testAsync("a dormant armed card with no monitor shows the chip", async () => {
  assert(hasChip("sd"), "the chip is gone from the case it exists for: an armed dormant session");
});

// MUTANT "chip on any armed card": observed from a byte backup (restored,
// sha256 compared): "w17ArmedChipDom: 3/5 passed" with this case and the live
// one red - "x a complete card with autoResume true and no monitor shows no
// chip: a COMPLETE session was warned about starting in bad weather" and "x a
// live card shows no chip: the run has started, the chip says it MAY: a live
// session was warned about starting in bad weather".
await testAsync("a complete card with autoResume true and no monitor shows no chip", async () => {
  assert(!hasChip("sc"), "a COMPLETE session was warned about starting in bad weather");
});

await testAsync("a live card shows no chip: the run has started, the chip says it MAY", async () => {
  assert(!hasChip("sa"), "a live session was warned about starting in bad weather");
});

await testAsync("an unarmed dormant card shows no chip, and a connected monitor removes it from the armed one", async () => {
  assert(!hasChip("su"), "an unarmed session was warned about being armed");
  seed(true);
  await mountShelf(ROWS_ALL);
  assert(!hasChip("sd"), "the chip survived a connected monitor");
});

act(() => { root.unmount(); });

console.log(`w17ArmedChipDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
