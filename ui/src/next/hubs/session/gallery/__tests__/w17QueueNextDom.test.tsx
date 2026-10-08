// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17QueueNextDom.test.tsx - WP-133 (#598, backlog ruling D-04, owner-approved
// 2026-09-30): the UI half of "queue a 'next' session behind a live or armed
// run".
//
//   Run directly:  npx tsx src/next/hubs/session/gallery/__tests__/w17QueueNextDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THE SCREENS OWE. A dormant session can wait behind the run that is live,
// else the one that is armed, and be armed when that one COMPLETES. The server
// decides all of it (`PATCH {queue_next}`, promotion in `_finalize_report`);
// these hold what the operator is told:
//
//   1. ARM AS NEXT is offered exactly when the server would not answer 409,
//      and where it cannot work it carries WHY, on the control (a hidden verb
//      teaches nothing). Nothing live or armed to wait behind is the case that
//      gives it its reason.
//   2. A waiting session's card says what it waits for, with a CANCEL; a
//      session whose queue has nothing left to wait behind (the one it waited
//      for was abandoned, deleted, finished, or stopped by hand) says
//      WAITING BEHIND NOTHING, with an ARM NOW, because only a completion
//      promotes and a night spent expecting it to start is the failure the
//      queue exists to prevent.
//   3. The armed session's own screens (Now, the plan editor's Sessions
//      ledger) print "next: <session>" beside it.
//
// NAMED MUTANTS (each a one-line change run from a byte backup inside this
// worktree, restored and sha256-compared, the mutant text grepped out
// afterwards; the failing assertion is quoted at each test):
//
//   "ARM AS NEXT with nothing to wait behind": cardActions.verbsFor's
//     `queueTarget == null` arm dropped.
//   "stranded queue shown as waiting": sessionsIndex.queueViewOf's
//     `goesByItself` test dropped.

/* eslint-disable @typescript-eslint/no-explicit-any */

// --------------------------------------------------------------- the css hook
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

// ------------------------------------------------------------- the fake rig
// Made-up sessions only: no real site, target coordinates or label.
function row(id: string, name: string, over: Record<string, unknown> = {}): any {
  return {
    id, name, status: "dormant", created_ts: 1_756_900_000, updated_ts: 1_757_000_000,
    nights: 1, accepted: 3, total: 9, auto_resume: false, ...over,
  };
}
let ROWS: any[] = [];
const asked: { url: string; method: string; body: any }[] = [];

g.fetch = async (url: any, init?: any) => {
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  const u = String(url);
  asked.push({ url: u, method, body });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (u === "/api/sessions") return json({ sessions: ROWS });
  const one = /^\/api\/sessions\/([^/]+)$/.exec(u);
  if (one && method === "PATCH") {
    return json({
      id: one[1], status: "dormant", auto_resume: false, remaining: {},
      queued_behind: { id: "sa", name: "Tonight" },
    });
  }
  if (one) {
    const r = ROWS.find((x) => x.id === one[1]) ?? row(one[1], one[1]);
    return json({
      id: r.id, schema_version: 1, name: r.name, created_ts: r.created_ts,
      updated_ts: r.updated_ts, status: r.status, nights: [], auto_resume: r.auto_resume,
      plan: { name: r.name, targets: [] }, frames: [],
    });
  }
  if (u.startsWith("/api/reports")) return json([]);
  if (u.startsWith("/api/gallery/nights")) {
    return json({ current: "2026-09-08", nights: [], truncated: false });
  }
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
const patches = () => asked.filter((a) => a.method === "PATCH");

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { verbsFor } = await import("../cardActions");
const {
  buildCards, queuedNextOf, queueTargetFor, queueViewOf, resetSessionsIndex, strandedQueue,
} = await import("../sessionsIndex");
const { GalleryScreen } = await import("../GalleryScreen");
const { NowEmpty } = await import("../../now/NowEmpty");
const { PlanSessionsSection } = await import("../../plan/sessions/PlanSessionsSection");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const cards = (rows: any[]) => buildCards(rows, []);
const card = (rows: any[], id: string): any => {
  const c = cards(rows).find((x) => x.id === id);
  if (!c) throw new Error(`no card ${id}`);
  return c;
};
const queueReason = (rows: any[], id: string, canControl = true): string | null => {
  const cs = cards(rows);
  const c = card(rows, id);
  const v = verbsFor(c, canControl, queueTargetFor(cs, c)).find((x) => x.id === "queueNext");
  if (!v) throw new Error("no queueNext verb");
  return v.reason;
};

// ===================================================== 1. the verb and its why

const A_ARMED = row("sa", "Tonight", { auto_resume: true });
const A_LIVE = row("sa", "Tonight", { status: "active", auto_resume: true });
const B = row("sb", "Mosaic");

test("ARM AS NEXT is live when a session is armed to wait behind", () => {
  eq(queueReason([A_ARMED, B], "sb"), null, "a dormant session with an armed one to wait behind");
});

test("ARM AS NEXT is live behind a LIVE run, which is not armed() on the server", () => {
  eq(queueReason([A_LIVE, B], "sb"), null, "a dormant session with a live run to wait behind");
});

test("ARM AS NEXT with nothing live or armed is locked, and says what to do instead", () => {
  const why = queueReason([B], "sb");
  assert(why != null, "ARM AS NEXT offered with nothing to wait behind: the server answers 409");
  assert(/Nothing else is running or armed to wait behind/.test(why as string),
    `the reason must say what is missing, got "${why}"`);
  assert(/AUTO-RESUME/.test(why as string), `and name the verb that arms it instead, got "${why}"`);
});
// MUTANT "ARM AS NEXT with nothing to wait behind" (`verbsFor`'s
// `queueTarget == null ? ... : null` arm replaced by `null`) turned the test
// above red, run from a byte backup and restored and sha256-verified
// afterwards:
//   x ARM AS NEXT with nothing live or armed is locked, and says what to do
//     instead: ARM AS NEXT offered with nothing to wait behind: the server
//     answers 409

test("the session that is itself the only armed one has nothing else to wait behind", () => {
  const armedB = row("sb", "Mosaic", { auto_resume: true });
  const why = queueReason([armedB], "sb");
  assert(why != null && /already armed/.test(why), `got "${why}"`);
});

test("only a dormant session can wait; a queued one is cancelled from its chip", () => {
  const done = row("sc", "Old run", { status: "complete" });
  assert(/Only a dormant session/.test(queueReason([A_ARMED, done], "sc") ?? ""),
    "a complete session must not be offered ARM AS NEXT");
  const queued = row("sb", "Mosaic", { queued_behind: "sa" });
  assert(/already waits for another one/.test(queueReason([A_ARMED, queued], "sb") ?? ""),
    "a session already waiting must say so, not offer to wait again");
});

test("a viewer's ARM AS NEXT carries the capability sentence, not the queue's", () => {
  assert(/operator or admin access/.test(queueReason([A_ARMED, B], "sb", false) ?? ""),
    "a locked write verb names the capability it wants");
});

test("a session never waits behind itself, and a live run beats an armed one", () => {
  const armedB = row("sb", "Mosaic", { auto_resume: true });
  eq(queueTargetFor(cards([armedB]), card([armedB], "sb")), null, "itself is not a target");
  const other = row("sd", "Other", { auto_resume: true });
  const t = queueTargetFor(cards([other, A_LIVE, B]), card([other, A_LIVE, B], "sb"));
  eq(t?.id, "sa", "the live run is the one the server waits behind");
});

// ==================================================== 2. what the chip says

test("the server's queued_behind reaches the card", () => {
  eq(card([A_ARMED, row("sb", "Mosaic", { queued_behind: "sa" })], "sb").queuedBehind, "sa",
    "the marker is carried");
  eq(card([A_ARMED, B], "sb").queuedBehind, null, "a row without the key waits for nothing");
});

test("a session waiting behind an armed or live one reads as waiting", () => {
  const q = row("sb", "Mosaic", { queued_behind: "sa" });
  for (const a of [A_ARMED, A_LIVE]) {
    const v = queueViewOf(cards([a, q]), card([a, q], "sb"));
    eq(v?.kind, "waiting", `behind a ${a.status} session`);
  }
});

test("a queue with nothing to wait behind reads as stranded, never as waiting", () => {
  const q = row("sb", "Mosaic", { queued_behind: "sa" });
  const cases: Array<[string, any[]]> = [
    ["the session is gone", [q]],
    ["it was abandoned", [row("sa", "Tonight", { status: "abandoned" }), q]],
    ["it finished", [row("sa", "Tonight", { status: "complete" }), q]],
    ["it was stopped by hand (dormant, disarmed)", [row("sa", "Tonight"), q]],
  ];
  for (const [why, rows] of cases) {
    eq(queueViewOf(cards(rows), card(rows, "sb"))?.kind, "stranded", why);
  }
});
// MUTANT "stranded queue shown as waiting" (`queueViewOf` no longer asks
// `goesByItself`, so any session that exists counts as something to wait
// behind) turned the test above red on its second case, run from a byte
// backup and restored and sha256-verified afterwards:
//   x a queue with nothing to wait behind reads as stranded, never as waiting:
//     it was abandoned
//     expected stranded
//     got      waiting

test("an armed session shows no wait even with a marker left on it", () => {
  const armedQ = row("sb", "Mosaic", { queued_behind: "sa", auto_resume: true });
  eq(queueViewOf(cards([A_ARMED, armedQ]), card([A_ARMED, armedQ], "sb")), null,
    "it starts in its own right, so 'waits for' would be false");
});

test("the armed session's next is the one really waiting; strays are listed apart", () => {
  const q = row("sb", "Mosaic", { queued_behind: "sa" });
  const stray = row("sx", "Stray", { queued_behind: "gone" });
  const cs = cards([A_ARMED, q, stray]);
  eq(queuedNextOf(cs, "sa")?.name, "Mosaic", "the next session");
  eq(queuedNextOf(cs, "gone"), null, "a stranded marker is not anyone's next");
  eq(strandedQueue(cs).map((c) => c.name).join(","), "Stray", "the stranded list");
});

// ================================================ 3. the Gallery card, mounted

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const docTid = (t: string): any => win.document.querySelector(`[data-testid="${t}"]`);
const click = async (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};
const OPERATOR_CAPS = ["view.status", "view.preview", "view.media", "view.site_derived", "control.mount", "control.capture"];
function seed(role: string, caps: string[], over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: { connected: true, reading: null, streak: 0 } as never,
      status: null, equipConnected: true, wsPhase: "up", toasts: [],
      ...over,
    } as never);
  });
}
async function mountGallery(rows: any[]): Promise<void> {
  ROWS = rows;
  // Blank first, THEN reset: the reset publishes to every subscriber, and one
  // still mounted would be updated outside `act`.
  await act(async () => { root.render(createElement("div")); });
  resetSessionsIndex();
  await act(async () => { root.render(createElement(GalleryScreen as any)); });
  await settle();
}
const toastTitles = (): string[] =>
  ((useStore.getState() as any).toasts as Array<{ title: string }>).map((t) => t.title);

seed("operator", OPERATOR_CAPS);
await mountGallery([A_ARMED, B, row("sc", "Old run", { status: "complete" })]);

await testAsync("precondition: the shelf rendered the three cards", async () => {
  for (const id of ["sa", "sb", "sc"]) assert(tid(`session-card-${id}`) != null, `no card ${id}`);
  assert(tid("session-queue-sb") == null, "a session waiting for nothing shows no chip");
});

await testAsync("ARM AS NEXT on a dormant session PATCHes queue_next and says A keeps its switch", async () => {
  await click(tid("session-more-sb"));
  const v = docTid("session-verb-queueNext-sb");
  assert(v != null, "the ARM AS NEXT verb never rendered");
  eq(v.getAttribute("aria-disabled"), null, "with an armed session to wait behind it must be live");
  eq(/ARM AS NEXT/.test(v.textContent), true, "its label");
  const before = patches().length;
  await click(v);
  const sent = patches();
  eq(sent.length, before + 1, "one PATCH");
  eq(sent[sent.length - 1].url, "/api/sessions/sb", "against the session being queued");
  eq(JSON.stringify(sent[sent.length - 1].body), JSON.stringify({ queue_next: true }),
    "asking to queue, and touching no auto-resume");
  assert(toastTitles().some((t) => /"Mosaic" is next/.test(t)), `a toast says so: ${toastTitles()}`);
});

await testAsync("a session already armed, and a complete one, show ARM AS NEXT locked with a reason", async () => {
  await click(tid("session-more-sa"));
  const armed = docTid("session-verb-queueNext-sa");
  eq(armed.getAttribute("aria-disabled"), "true", "an armed session has nothing to wait for");
  assert(/already armed/.test(armed.getAttribute("title") ?? ""), "and says why");
  await click(tid("session-more-sc"));
  const done = docTid("session-verb-queueNext-sc");
  eq(done.getAttribute("aria-disabled"), "true", "a complete session cannot wait");
});

await mountGallery([B]);
await testAsync("with nothing live or armed, ARM AS NEXT is locked on the control with its reason and fires nothing", async () => {
  await click(tid("session-more-sb"));
  const v = docTid("session-verb-queueNext-sb");
  eq(v.getAttribute("aria-disabled"), "true", "locked");
  assert(/Nothing else is running or armed to wait behind/.test(v.getAttribute("title") ?? ""),
    `the reason is on the control, got "${v.getAttribute("title")}"`);
  const before = patches().length;
  await click(v);
  eq(patches().length, before, "a locked verb reaches no network");
});

await mountGallery([A_ARMED, row("sb", "Mosaic", { queued_behind: "sa" })]);
await testAsync("a waiting session's card says what it waits for, and CANCEL clears it", async () => {
  const chip = tid("session-queue-sb");
  assert(chip != null, "no queue chip on the waiting card");
  assert(/NEXT: waits for Tonight/.test(chip.textContent), `the chip names it, got "${chip.textContent}"`);
  const before = patches().length;
  await click(tid("session-queue-cancel-sb"));
  const sent = patches();
  eq(sent.length, before + 1, "one PATCH");
  eq(JSON.stringify(sent[sent.length - 1].body), JSON.stringify({ queue_next: false }), "cancel");
});

await mountGallery([row("sa", "Tonight"), row("sb", "Mosaic", { queued_behind: "sa" })]);
await testAsync("a queue behind a session stopped by hand says WAITING BEHIND NOTHING, and ARM NOW arms it", async () => {
  const chip = tid("session-queue-sb");
  assert(chip != null, "no chip");
  assert(/WAITING BEHIND NOTHING/.test(chip.textContent), `got "${chip.textContent}"`);
  assert(!/waits for/.test(chip.textContent), "it must not claim to wait for the stopped session");
  const before = patches().length;
  await click(tid("session-queue-armnow-sb"));
  const sent = patches();
  eq(sent.length, before + 1, "one PATCH");
  eq(JSON.stringify(sent[sent.length - 1].body), JSON.stringify({ auto_resume: true, queue_next: false }),
    "arm it in its own right and drop the dead marker, in one request");
});

seed("viewer", ["view.status", "view.preview"]);
await mountGallery([A_ARMED, row("sb", "Mosaic", { queued_behind: "sa" })]);
await testAsync("a viewer sees the chip, with CANCEL locked and naming the capability", async () => {
  const cancel = tid("session-queue-cancel-sb");
  assert(cancel != null, "a viewer must see the same chip");
  eq(cancel.getAttribute("aria-disabled"), "true", "CANCEL is a write");
  assert(/operator or admin access/.test(cancel.getAttribute("title") ?? ""), "and names the access it wants");
  const before = patches().length;
  await click(cancel);
  eq(patches().length, before, "and reaches no network");
});

act(() => { root.unmount(); });

// ============================================== 4. Now: "next: <session>"

const host2 = win.document.createElement("div");
win.document.body.appendChild(host2);
const root2 = createRoot(host2);
const ARMED = { id: "sa", name: "Tonight", owed: 5, accepted: 0, total: 5, origin: "", origin_id: "" };
async function mountNow(rows: any[]): Promise<void> {
  ROWS = rows;
  await act(async () => { root2.render(createElement("div")); });
  resetSessionsIndex();
  seed("operator", OPERATOR_CAPS, { resumeArm: { armed: ARMED, hold: null } });
  await act(async () => { root2.render(createElement(NowEmpty as any)); });
  await settle();
  await settle();
}
const nowTid = (t: string): any => host2.querySelector(`[data-testid="${t}"]`);

await mountNow([A_ARMED, row("sb", "Mosaic", { queued_behind: "sa" })]);
await testAsync("Now prints 'next: <session>' beside the armed one", async () => {
  const el = nowTid("now-next-session");
  assert(el != null, "no next line beside the armed session");
  eq(el.textContent, "next: Mosaic", "the line");
});

await mountNow([A_ARMED, row("sb", "Mosaic")]);
await testAsync("Now prints no next line when nothing waits", async () => {
  assert(nowTid("now-next-session") == null, "a next line with no queue");
  assert(nowTid("now-queue-stranded") == null, "a stray warning");
});

await mountNow([row("sb", "Mosaic", { queued_behind: "gone" })]);
await testAsync("Now warns that a queue with nothing to wait behind will not start by itself", async () => {
  const el = nowTid("now-queue-stranded");
  assert(el != null, "no warning for a stranded queue");
  assert(/will not start by itself/.test(el.textContent) && /Mosaic/.test(el.textContent),
    `names it and says so, got "${el.textContent}"`);
});
act(() => { root2.unmount(); });

// ======================================= 5. the plan editor's Sessions ledger

async function mountLedger(rows: any[]): Promise<any> {
  ROWS = rows;
  seed("operator", OPERATOR_CAPS);
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const r = createRoot(host);
  await act(async () => {
    r.render(createElement(PlanSessionsSection as any, { lockedReason: null, onExplain: () => {} }));
  });
  await settle();
  await settle();
  return { host, unmount: () => act(() => { r.unmount(); }) };
}

{
  const m = await mountLedger([A_ARMED, row("sb", "Mosaic", { queued_behind: "sa" })]);
  const q = (t: string): any => m.host.querySelector(`[data-testid="${t}"]`);
  await testAsync("the ledger prints 'next: <session>' on the armed row and the wait on the queued one", async () => {
    eq(q("plan-session-next-sa")?.textContent, "next: Mosaic", "the armed row's next line");
    assert(/NEXT: waits for Tonight/.test(q("plan-session-queue-sb")?.textContent ?? ""),
      "the queued row names what it waits for");
    assert(q("plan-session-queue-cancel-sb") != null, "with a CANCEL");
  });
  await testAsync("the ledger's ARM AS NEXT is on every row and live only where it can work", async () => {
    eq(q("plan-session-queue-next-sa").getAttribute("aria-disabled"), "true", "the armed row");
    assert(/already armed/.test(q("plan-session-queue-next-sa").getAttribute("title") ?? ""), "and says why");
    eq(q("plan-session-queue-next-sb").getAttribute("aria-disabled"), "true", "an already-waiting row");
  });
  m.unmount();
}
{
  const m = await mountLedger([A_ARMED, B]);
  const q = (t: string): any => m.host.querySelector(`[data-testid="${t}"]`);
  await testAsync("the ledger's ARM AS NEXT fires queue_next for a dormant row with an armed one to wait behind", async () => {
    eq(q("plan-session-queue-next-sb").getAttribute("aria-disabled"), null, "live");
    const before = patches().length;
    await click(q("plan-session-queue-next-sb"));
    const sent = patches();
    eq(sent.length, before + 1, "one PATCH");
    eq(JSON.stringify(sent[sent.length - 1].body), JSON.stringify({ queue_next: true }), "queue it");
  });
  m.unmount();
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w17QueueNextDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
