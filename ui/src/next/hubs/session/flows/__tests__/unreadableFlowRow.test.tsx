// unreadableFlowRow.test.tsx - SESSION / FLOWS draws a flow this build cannot
// open as a row that explains itself, and never opens or runs it (#153; spec
// 2026-09-23 section 3.6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/__tests__/unreadableFlowRow.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// `GET /api/flows` lists a file the store could not read as a row carrying
// `unreadable` (server `FlowStore._row`). The row reuses the honest-disabled
// path the screen already has - `openReason` and `onExplain` on the row body,
// `runReason` on the verb - so:
//
//   1. THE REASON IS READABLE on the row (its meta line) and in `title`.
//   2. THE ROW BODY EXPLAINS INSTEAD OF OPENING: a warning toast with the
//      reason, no `GET /api/flows/<id>`, and no navigation to a stage list or a
//      canvas that could only 404.
//   3. RUN AND RESUME ARE LOCKED WITH THE SAME REASON, which outranks the screen's own
//      reasons: after a run ends, or for an operator who may run, the file is
//      still unreadable, so that is the sentence that stays true.
//   4. OPEN THE FLOWS CANVAS never picks one as the flow it opens.
//   5. CONTROLS: a readable row opens and runs exactly as before.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of FlowsScreen.tsx (of
// FlowRow.tsx for the RESUME case).
//
// Convention: shell-and-tests.md section 4, harness shape from flowsDom.test.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Same reason as flowsDom.test.tsx: the rebuilt areas import their own `.css`,
// which Node cannot load. run-tests.mjs also preloads test-css-stub.mjs.
{
  const { registerHooks } = await import("node:module");
  if (typeof registerHooks === "function") {
    registerHooks({
      load(url: string, context: any, nextLoad: any) {
        if (url.endsWith(".css")) {
          return { format: "module", shortCircuit: true, source: "export default {};" };
        }
        return nextLoad(url, context);
      },
    } as any);
  }
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

let viewportW = 390;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const NEWER = "saved by a newer AstroDeck (schema 4); update to open it";

const GOOD = {
  id: "quick-m31", name: "Quick M31 LRGB", folder: "My flows",
  tagline: "12 subs each of L, R, G, B", readonly: false,
  stages: 7, wires: 6, last_run: 1_757_000_000, last_result: "ok",
  updated_ts: 1_757_000_500,
};
/** Server `FlowStore._row` for a file a newer build wrote: the card shape,
 *  read-only, no tagline, no run, plus the reason. */
const FUTURE = {
  id: "future-mosaic", name: "Mosaic NGC 7000", folder: "My flows", tagline: "",
  readonly: true, stages: 9, wires: 8, last_run: null, last_result: "",
  updated_ts: 1_758_000_000, unreadable: NEWER,
};
const CARDS: any[] = [GOOD, FUTURE];

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  const notFound = () => ({
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no flow with that id" }),
  });
  if (url === "/api/flows") return ok(CARDS);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: CARDS.length, readonly: false }]);
  if (url === "/api/flows/compile") return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  if (/^\/api\/flows\/[^/]+\/run$/.test(url) && method === "POST") {
    return ok({ started: true, flow_id: "quick-m31", frames: 48, unmapped: [] });
  }
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  // As the server: an unreadable row's id is 404 on every route but the list.
  if (one && one[1] === GOOD.id) {
    return ok({ ...GOOD, graph: { nodes: [], edges: [] }, created_ts: 1 });
  }
  return notFound();
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { runBlockedReason } = await import("../../../../../components/flows/flowRunControls");
const { resetRouterCacheForTests } = await import("../../../../router");
const { FlowsScreen, NO_CANVAS_TARGET } = await import("../FlowsScreen");
const { FlowRow } = await import("../FlowRow");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};
const toastTitles = (): string[] => useStore.getState().toasts.map((t: any) => String(t.title));
const touched = (id: string) => asked.filter((a) => a.url.startsWith(`/api/flows/${id}`));

const ADMIN = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];
const VIEWER = ["view.status", "view.preview"];

function seed(role: string, caps: string[]): void {
  act(() => {
    const f = useStore.getState().flows;
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      // A connected camera, or `runBlockedReason` refuses for a reason that has
      // nothing to do with the row under test.
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null as never,
      toasts: [] as never,
      flows: {
        ...f,
        record: null,
        run: { ...f.run, phase: "idle" },
        ui: { ...f.ui, screen: "library", query: "", folderChip: "all", highlightId: null },
      } as never,
    } as never);
  });
  asked.length = 0;
}

async function mountAt(hash: string): Promise<void> {
  win.location.hash = hash;
  resetRouterCacheForTests();
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

// ============================================================ 1. the reason

// MUTANT "ignore unreadable" (FlowsScreen's `const unreadable =
// unreadableReason(card);` in the rows memo made `= null`, so the row gets the
// readable card's meta, `openReason={null}` and the list's run reason).
// Observed, 6/10 - this case and the next three:
//   x an unreadable row carries its reason as text, and its body is locked with
//     it: the reason is not readable on the row
//   x the row body explains instead of opening: a toast, no request, no
//     navigation: the press navigated to a stage list that could only 404
//     expected #/session/flows
//     got      #/session/flows/flowStages?open=future-mosaic
//   x RUN on an unreadable row is locked with the same reason and starts
//     nothing: RUN is live on a flow that cannot be read
//     expected true
//     got      null
//
// MUTANT "open not locked" (`openReason={r.unreadable}` made `{null}`).
// Observed, 8/10:
//   x an unreadable row carries its reason as text, and its body is locked with
//     it: the row body is not honest-disabled
//     expected true
//     got      null
//   x the row body explains instead of opening: a toast, no request, no
//     navigation: the press navigated to a stage list that could only 404
await test("an unreadable row carries its reason as text, and its body is locked with it", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(tid("flow-row-future-mosaic") != null, "precondition: the unreadable row rendered");
  const meta = tid("flow-meta-future-mosaic").textContent as string;
  assert(meta.includes(NEWER), "the reason is not readable on the row");
  const open = tid("flow-open-future-mosaic");
  eq(open.getAttribute("aria-disabled"), "true", "the row body is not honest-disabled");
  eq(open.getAttribute("title"), NEWER, "the row body's hover does not name the reason");
});

await test("the row body explains instead of opening: a toast, no request, no navigation", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const hash = win.location.hash;
  await click(tid("flow-open-future-mosaic"));
  eq(touched("future-mosaic").length, 0, "the press asked the server for a flow it listed as unreadable");
  eq(win.location.hash, hash, "the press navigated to a stage list that could only 404");
  assert(toastTitles().includes(NEWER), `the press was silent - toasts: ${JSON.stringify(toastTitles())}`);
});

await test("RUN on an unreadable row is locked with the same reason and starts nothing", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const verb = tid("flow-verb-future-mosaic");
  eq(verb.getAttribute("aria-disabled"), "true", "RUN is live on a flow that cannot be read");
  eq(verb.getAttribute("title"), NEWER, "RUN's lock does not carry the row's reason");
  await click(verb);
  eq(touched("future-mosaic").length, 0, "RUN opened or posted against an unreadable flow");
  assert(toastTitles().includes(NEWER), "the locked RUN was silent");
});

// MUTANT "screen reasons first" (`runReason={listRunReason ?? r.unreadable}`).
// Observed, 9/10:
//   x for a viewer the row's own reason outranks the capability one: a viewer is
//     told to ask for access to run a flow nobody can run
//     expected saved by a newer AstroDeck (schema 4); update to open it
//     got      Running a flow needs operator or admin access.
await test("for a viewer the row's own reason outranks the capability one", async () => {
  seed("viewer", VIEWER);
  viewportW = 390;
  await mountAt("#/session/flows");
  const capability = runBlockedReason(false, true, false);
  assert(capability != null, "precondition: a viewer's RUN is refused for a capability");
  eq(tid("flow-verb-future-mosaic").getAttribute("title"), NEWER,
    "a viewer is told to ask for access to run a flow nobody can run");
  eq(tid("flow-verb-quick-m31").getAttribute("title"), capability,
    "control: the readable row still carries the capability reason");
});

// THE RESUME HALF, at the row. The screen cannot be driven to a RESUME verb on
// an unreadable row: a campaign is read from the flow's own `/tonight` route,
// which answers 404 for such an id, so no campaign is ever built for one. The
// screen hands the same `runReason` to whichever verb the row shows, so what is
// pinned here is the row's half - RESUME honours that reason as RUN does.
//
// MUTANT "row locks RUN only" (FlowRow's `const reason = live ? null :
// runReason;` made `verb === "run" ? runReason : null`). Observed, 9/10:
//   x a RESUME verb is locked by the same reason, and its press resumes nothing:
//     RESUME is live on a row whose run is refused
//     expected true
//     got      null
await test("a RESUME verb is locked by the same reason, and its press resumes nothing", async () => {
  seed("admin", ADMIN);
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const probe = createRoot(host);
  let resumed = 0;
  await act(async () => {
    probe.render(createElement(FlowRow, {
      card: FUTURE as any, meta: NEWER, dotColor: "#7683a5", verb: "resume",
      runReason: NEWER, openReason: NEWER,
      onRun: () => {}, onResume: () => { resumed++; }, onLive: () => {}, onOpen: () => {},
      onExplain: (r: string) => useStore.getState().enqueueToast({ level: "warning", title: r }),
    }));
  });
  const verb = host.querySelector('[data-testid="flow-verb-future-mosaic"]') as any;
  assert(/RESUME/.test(verb?.textContent ?? ""), "precondition: the verb reads RESUME");
  eq(verb.getAttribute("aria-disabled"), "true", "RESUME is live on a row whose run is refused");
  eq(verb.getAttribute("title"), NEWER, "RESUME's lock does not carry the reason");
  await act(async () => {
    verb.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  eq(resumed, 0, "the locked RESUME resumed a session");
  assert(toastTitles().includes(NEWER), "the locked RESUME was silent");
  await act(async () => { probe.unmount(); });
  host.remove();
});

// ====================================================== 2. the list-level OPEN

// MUTANT "canvas target ignores unreadable" (`canvasTarget` falls back to
// `visible[0]?.id` again). Observed, 9/10:
//   x OPEN THE FLOWS CANVAS never picks an unreadable row as its flow: with only
//     an unreadable row showing, OPEN THE FLOWS CANVAS is live
//     expected true
//     got      null
await test("OPEN THE FLOWS CANVAS never picks an unreadable row as its flow", async () => {
  seed("admin", ADMIN);
  viewportW = 1024;
  await mountAt("#/session/flows");
  // Filter down to the unreadable row alone, the one case in which it is first.
  await act(async () => { useStore.getState().flowsSetUi({ query: "NGC 7000" }); });
  await settle();
  assert(tid("flow-row-future-mosaic") != null && tid("flow-row-quick-m31") == null,
    "precondition: only the unreadable row is showing");
  const canvas = tid("flows-open-canvas");
  eq(canvas.getAttribute("aria-disabled"), "true",
    "with only an unreadable row showing, OPEN THE FLOWS CANVAS is live");
  eq(canvas.getAttribute("title"), NO_CANVAS_TARGET, "and it does not say why");
  const hash = win.location.hash;
  await click(canvas);
  eq(win.location.hash, hash, "OPEN THE FLOWS CANVAS navigated to an unreadable flow");
  eq(touched("future-mosaic").length, 0, "and asked the server for it");
});

// ================================================================ controls

await test("control: the readable row's meta, open and RUN are unlocked", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  const meta = tid("flow-meta-quick-m31").textContent as string;
  assert(/7 stages · 6 wires/.test(meta) && /completed clean/.test(meta),
    `the readable row's meta changed: "${meta}"`);
  eq(tid("flow-open-quick-m31").getAttribute("aria-disabled"), null, "the readable row body is locked");
  eq(tid("flow-verb-quick-m31").getAttribute("aria-disabled"), null, "the readable row's RUN is locked");
  eq(tid("flows-run-reason"), null, "a list-level run reason appeared for an admin with a camera");
});

await test("control: a phone tap on the readable row opens its stage list", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  await click(tid("flow-open-quick-m31"));
  eq(win.location.hash, "#/session/flows/flowStages?open=quick-m31",
    "the readable row no longer opens its stage list");
  eq(toastTitles().length, 0, "the readable row's open raised a toast");
});

await test("control: RUN on the readable row opens it and posts to its run route", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  await click(tid("flow-verb-quick-m31"));
  assert(asked.some((a) => a.url === "/api/flows/quick-m31" && a.method === "GET"),
    "RUN no longer opens the readable flow");
  eq(asked.filter((a) => a.method === "POST" && /\/run$/.test(a.url)).map((a) => a.url).join(","),
    "/api/flows/quick-m31/run", "RUN no longer posts exactly one run for the readable flow");
});

await test("control: the header count includes the unreadable row", async () => {
  seed("admin", ADMIN);
  viewportW = 390;
  await mountAt("#/session/flows");
  assert(/2 saved/.test(tid("flows-summary").textContent),
    `the header does not count the row it draws: "${tid("flows-summary").textContent}"`);
});

// Unmount before the tally: a mounted jsdom tree keeps a rAF loop alive and the
// process never exits (flowsDom.test.tsx, foot).
await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`unreadableFlowRow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
