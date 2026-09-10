// galleryDom.test.tsx - the SESSION / GALLERY shelf, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/gallery/__tests__/galleryDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, WITH REAL CARDS. A grid that silently produced nothing
//      would make every assertion below pass over an empty page, so the first
//      test names the screen marker AND a card built from the stubbed rig.
//   2. DELETE SAYS WHAT IT DOES, THEN DOES IT. The confirm body is verbatim
//      because it carries the one fact the word "delete" hides: the saved FITS
//      frames are NOT removed. The test asserts that sentence and then that
//      confirming actually issues `DELETE /api/sessions/{id}` - a dialog whose
//      OK does nothing is its own kind of lie.
//   3. A VIEWER SEES THE SAME SCREEN, LOCKED. Not a hidden menu: every verb is
//      present, `aria-disabled`, carrying the capability it wants, and none of
//      them reaches the network.
//
// The verbs live in a `Popover`, which portals to `document.body` rather than
// into the test's container - so they are queried through `win.document`.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// --------------------------------------------------------------- the css hook
// The Archive sheet this screen links to now pulls in `gallery/frames/`, whose
// barrel imports `frames.css`, and Node has no idea what to do with a
// stylesheet. A stub for it is cheaper than routing around a real import in the
// module under test - the same hook `next/__tests__/shellDom.test.tsx` and
// `hubMeta.test.ts` install for the same reason.
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const ROW = {
  id: "s1", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
  nights: 2, accepted: 41, total: 120, auto_resume: false,
};

const SESSION = {
  id: "s1", schema_version: 1, name: "M31 LRGB",
  created_ts: ROW.created_ts, updated_ts: ROW.updated_ts, status: "dormant",
  nights: ["2026-09-07", "2026-09-08"], auto_resume: false,
  plan: { name: "M31 LRGB", targets: [] },
  frames: [
    { id: "a", ts: 5, night: "2026-09-08", target_id: "t1", step_id: "st-L", thumb: "a.jpg", metrics: {}, auto_accepted: true, override: null },
  ],
};

const asked: { url: string; method: string }[] = [];

g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK", json: async () => data,
  });
  if (url === "/api/sessions") return json({ sessions: [ROW] });
  if (/^\/api\/sessions\/[^/]+$/.test(url) && method === "DELETE") return json({ deleted: "s1" });
  if (/^\/api\/sessions\/[^/]+$/.test(url)) return json(SESSION);
  if (url.startsWith("/api/reports")) {
    return json([{
      id: "M31 LRGB-20260908-201500", plan_name: "M31 LRGB",
      started_at: 1_756_990_000, ended_at: 1_757_000_000, end_reason: "complete",
      frames_captured: 45, frames_rejected: 4, integration_s: 7200,
    }]);
  }
  if (url.startsWith("/api/gallery/nights")) {
    return json({
      current: "2026-09-08",
      nights: [{ night: "2026-09-08", frames: 41, bytes: 41 * 1024 * 1024 * 1024 }],
      truncated: false,
    });
  }
  if (url.startsWith("/api/sequence/stack")) {
    return json({
      enabled: false, target: "", seq: 0, channels: [], frames: 0, integrated_s: 0,
      rejected: 0, mode: null, downsample: 2, has_image: false, render_age_s: null,
      backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
    });
  }
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }) };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CONFIRM_DELETE } = await import("../cardActions");
const { GalleryScreen } = await import("../GalleryScreen");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
/** The popover portals to document.body, outside the test container. */
const docTid = (t: string): any => win.document.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

function seed(role: string, caps: string[]): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: { connected: true, reading: null, streak: 0 } as never,
      status: null, equipConnected: true, wsPhase: "up",
    } as never);
  });
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(GalleryScreen as any)); });
  await settle();
}

// ========================================================== 1. it rendered

seed("admin", ["view.status", "view.preview", "view.media", "control.mount", "control.capture"]);
await mount();

test("precondition: the shelf rendered with the session card from the stub", () => {
  assert(tid("session-gallery") != null, "no session-gallery marker - the fixture is wrong, not the screen");
  assert(tid("session-card-s1") != null, "the session card never rendered");
  assert(/M31 LRGB/.test(tid("session-card-s1").textContent), "the card carries the session name");
  assert(/41 SUBS/.test(tid("session-card-s1").textContent), "and its accepted count");
});

test("the summary counts sessions and prices the disk from the night index", () => {
  const s = tid("gallery-summary");
  assert(s != null, "no summary line");
  assert(/1 session/.test(s.textContent), `session count, got "${s.textContent}"`);
  assert(/41\.0 GB on the rig/.test(s.textContent), `disk bytes, got "${s.textContent}"`);
});

test("the report is folded into the session card, not shown twice", () => {
  const cards = container.querySelectorAll('[data-testid^="session-card-"]');
  eq(cards.length, 1, "a report inside the session's window must not become a second card");
  assert(/2h 0m/.test(cards[0].textContent), "and its integration reaches the meta line");
});

test("what survives a reboot is stated on the screen, not in a help page", () => {
  assert(/survive a restart of the rig/.test(container.textContent),
    "the reboot note is missing");
  assert(!/clear them from the tablet/.test(container.textContent),
    "the proto's last clause is false now that DELETE is on the card (deviation D10)");
});

// ================================================== 2. DELETE names the cost

await testAsync("DELETE confirms with the verbatim body, then issues the DELETE", async () => {
  click(tid("session-more-s1"));
  await settle();
  const del = docTid("session-verb-delete-s1");
  assert(del != null, "the DELETE verb never rendered in the overflow");
  assert(del.getAttribute("aria-disabled") == null, "an admin's DELETE must be live");

  click(del);
  await settle();

  const req = useStore.getState().confirm;
  assert(req != null, "no confirm was raised - delete must never be one tap");
  eq(req?.body as string, CONFIRM_DELETE, "the confirm body must be the verbatim sentence");
  assert(/Saved FITS frames are NOT deleted/.test(String(req?.body)),
    "the one fact the word 'delete' hides has to be in it");

  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { useStore.getState().resolveConfirm(true); });
  await settle();
  const sent = asked.filter((a) => a.method === "DELETE");
  eq(sent.length, before + 1, "confirming must actually issue the request");
  eq(sent[sent.length - 1].url, "/api/sessions/s1", "and against this session");
});

await testAsync("cancelling the confirm sends nothing", async () => {
  click(tid("session-more-s1"));
  await settle();
  click(docTid("session-verb-delete-s1"));
  await settle();
  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { useStore.getState().resolveConfirm(false); });
  await settle();
  eq(asked.filter((a) => a.method === "DELETE").length, before, "KEEP must be a real no");
});

// ============================================== 3. the viewer sees it locked

await testAsync("a viewer sees the card and every write verb locked, and fires nothing", async () => {
  seed("viewer", ["view.status", "view.preview"]);
  await settle();
  assert(tid("session-card-s1") != null, "a viewer must see the same card, not an empty screen");

  click(tid("session-more-s1"));
  await settle();

  for (const verb of ["resume", "update", "autoResume", "abandon", "delete"]) {
    const el = docTid(`session-verb-${verb}-s1`);
    assert(el != null, `the ${verb} verb must still be rendered for a viewer`);
    eq(el.getAttribute("aria-disabled"), "true", `${verb} must be honest-disabled`);
    assert(/operator or admin access/.test(el.getAttribute("title") ?? ""),
      `${verb} must name the capability it wants, got "${el.getAttribute("title")}"`);
  }
  // REPORT is a read: it stays live even for a viewer, and that is correct.
  const report = docTid("session-verb-report-s1");
  assert(report != null && report.getAttribute("aria-disabled") == null,
    "reading the report needs no capability");

  const before = asked.length;
  click(docTid("session-verb-delete-s1"));
  await settle();
  eq(useStore.getState().confirm, null, "a locked verb must not even raise a confirm");
  eq(asked.length, before, "and must not reach the network");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`galleryDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
