// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_2MosaicChipDom.test.tsx - a session card on the Gallery shelf says which
// mosaics the night shot (WP 3.2, #188).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/gallery/__tests__/w3_2MosaicChipDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The session's list row carries its mosaics (`SessionRow.mosaics`, one per
// group, keyed by the group's id), and the card draws a chip from them: how many
// mosaics and how many panels. Two groups that share a name are two mosaics, and
// a card that counted by name would say one. The chip is read off the ROW: the
// per-session read the thumbnail pass makes answers a plan with no groups here,
// so a chip that came from it would be missing.
//
// WHAT IS WORTH ASSERTING, on the mounted shelf:
//   a session with one 2x2 group carries "MOSAIC: 4 PANELS";
//   two groups named alike carry "2 MOSAICS: 4 PANELS", not "1 MOSAIC";
//   a session of single targets carries no chip, and neither does a report-only
//   night (its card has no list row of mosaics);
//   the hover line names each mosaic with its panels' labels.
//
// MUTANT "mosaic by name" (SessionCard.tsx: `mosaicChipOf` counts distinct
// names, `new Set(groups.map((g) => g.name)).size`, where it counts groups):
// the two-groups case reds, quoted at the case below.

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
    id, name, status: "complete", created_ts: 1_756_900_000, updated_ts: 1_757_000_000,
    nights: 1, accepted: 3, total: 9, auto_resume: false, ...over,
  };
}

const VEIL_A = { id: "group-a", name: "Veil", panels: ["1-1", "1-2", "2-1", "2-2"] };
const VEIL_B = { id: "group-b", name: "Veil", panels: ["1-1", "1-2"] };

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
  if (u.startsWith("/api/reports")) {
    return json([{
      id: "rep-only-20260101-000000", plan_name: "Report only", started_at: 1_700_000_000,
      ended_at: 1_700_003_600, end_reason: "complete", frames_captured: 4, frames_rejected: 0,
      integration_s: 1200,
    }]);
  }
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
const { mosaicChipOf } = await import("../SessionCard");

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
  for (let i = 0; i < 12; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "view.preview", "view.media", "control.mount", "control.capture"] } as never,
    authGate: "open",
    sequence: { state: "idle" } as never,
    safety: { connected: true, reading: null, streak: 0 } as never,
    status: null, equipConnected: true, wsPhase: "up", toasts: [],
  } as never);
});

ROWS = [
  row("one", "One mosaic", { mosaics: [VEIL_A] }),
  row("two", "Two mosaics", { mosaics: [{ ...VEIL_A, panels: VEIL_A.panels.slice(0, 2) }, VEIL_B] }),
  row("singles", "Singles"),
];
resetSessionsIndex();
await act(async () => { root.render(createElement(GalleryScreen as any)); });
await settle();

const chip = (id: string): any => tid(`session-mosaic-${id}`);

await testAsync("precondition: the shelf rendered the cards", async () => {
  for (const id of ["one", "two", "singles"]) assert(tid(`session-card-${id}`) != null, `no card ${id}`);
});

await testAsync("a session with one 2x2 group carries a one-mosaic, four-panel chip", async () => {
  const c = chip("one");
  assert(c != null, "no mosaic chip on a night that shot a 2x2 mosaic");
  assert((c.textContent || "").includes("MOSAIC: 4 PANELS"), `chip reads ${JSON.stringify(c.textContent)}`);
  assert(!(c.textContent || "").includes("MOSAICS"), "one mosaic read as several");
});

// MUTANT "mosaic by name": observed from a byte backup (restored, sha256
// compared): "x two groups named alike are two mosaics on the chip: chip reads
// "MOSAIC: 4 PANELS": two groups that share a name were counted as one".
await testAsync("two groups named alike are two mosaics on the chip", async () => {
  const c = chip("two");
  assert(c != null, "no mosaic chip");
  const text = c.textContent || "";
  assert(text.includes("2 MOSAICS: 4 PANELS"),
    `chip reads ${JSON.stringify(text)}: two groups that share a name were counted as one`);
});

await testAsync("the hover line names each mosaic with its panels' labels", async () => {
  const title = chip("two")?.getAttribute("title") || "";
  assert(title.split("\n").length === 2, `hover reads ${JSON.stringify(title)}`);
  assert(title.includes("Veil: 2 panels (1-1, 1-2)"), `hover reads ${JSON.stringify(title)}`);
});

await testAsync("a night of single targets and a report-only night carry no chip", async () => {
  assert(chip("singles") == null, "a night with no mosaic carries a mosaic chip");
  const cards = [...container.querySelectorAll('[data-testid^="session-mosaic-"]')];
  assert(cards.length === 2, `${cards.length} chips on a shelf with two mosaic nights`);
});

await testAsync("mosaicChipOf: nothing for no groups, singular for one panel", async () => {
  assert(mosaicChipOf([]) === null && mosaicChipOf(undefined) === null, "a chip for no mosaic");
  const one = mosaicChipOf([{ id: "g", name: "Solo", panels: ["1-1"] }]);
  assert(one?.word === "MOSAIC: 1 PANEL", `reads ${JSON.stringify(one?.word)}`);
});

act(() => { root.unmount(); });

console.log(`w3_2MosaicChipDom: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
