// flowsDom.test.tsx - SESSION / FLOWS, MOUNTED, AFTER THE CUTOVER.
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/flowsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, WITH REAL ROWS. A list that silently produced nothing would
//      let every assertion below pass over a blank page, so the first test names
//      the screen marker AND a row built from the stubbed library.
//   2. RUN REACHES THE RUN ROUTE. Not "a request went out" - the exact path,
//      because the flow has to be OPENED before `flowsRun` has an id to post
//      against, and a RUN that posted to the wrong flow is invisible until the
//      wrong mount moves.
//   3. A 409 `unmapped` IS A QUESTION, NOT AN ERROR. The server refuses with the
//      list of graph settings the compile drops; the confirm shows them and RUN
//      ANYWAY re-fires with `accept_unmapped: true`. This is the assertion that
//      went red for as long as the guard was dead on the rig (2026-08-18: press
//      RUN, get a 409, no dialog, no way forward), so it is asserted end to end:
//      the title, the server's own detail lines, and the second request's BODY.
//   4. THE PHONE GETS INSIDE THE FLOW. Wave R7 replaced "OPEN is honest-disabled
//      because the canvas is a tablet job" with a row tap that opens the
//      `flowStages` sheet. The LIST-level OPEN THE FLOWS CANVAS keeps the reason,
//      because it really does mean the pannable surface.
//   5. A VIEWER SEES THE SAME SCREEN, LOCKED, with the capability named and no
//      request fired.
//   6. THE CANVAS IS THE REBUILT ONE. `components/flows/FlowsView` is not
//      imported, not mounted, and its own marker is absent - asserted against the
//      SOURCE TEXT as well as the DOM, because a legacy import that renders
//      nothing looks exactly like no import at all.
//   7. ONE LIBRARY. The whole point of the cutover (wave R7 section 6.1 defect
//      7): at tablet the canvas used to stack a second MY FLOWS on top of this
//      one. Counted, not eyeballed.
//   8. THE SIX SHEETS ARE REGISTERED. Four areas publish `flow*Sheets`; if one
//      is not spread into the SESSION hub's registry the route resolves to
//      `MissingSheet` with no compile error anywhere.
//   9. THE CANVAS SURVIVES ITS OWN SHEET. `nav.sheet` rebuilds the hash from the
//      params it is handed, so the stage editor clears `?open=`. Without the
//      store's `ui.screen` fallback the canvas would vanish the moment a stage
//      was opened for editing.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `NextApp` imports `next.css` and `shell/shell.css` - it is the single import
// site for both, by contract, so that the cascade order cannot depend on module
// resolution order. The rebuilt Flows areas import their own `<area>.css` the
// same way. Node has no idea what a `.css` file is, so a synchronous load hook
// answers with an empty module. This is the ONLY way to keep both facts true at
// once: the app has one style entry point per area, and those entry points are
// still mountable in a test.
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

// A real-ish matchMedia driven by one number, so the same file can render the
// phone (where the canvas is an explained absence) and the tablet (where it is
// the workspace). Starts at PHONE - the layout the design is written for.
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
const CARDS = [
  {
    id: "quick-m31", name: "Quick · M31 LRGB", folder: "My flows",
    tagline: "12 subs each of L, R, G, B", readonly: false,
    stages: 7, wires: 6, last_run: 1_757_000_000, last_result: "ok",
    updated_ts: 1_757_000_500,
  },
  {
    id: "example-m16", name: "Dusk flats + darks", folder: "Examples",
    tagline: "twilight window, 5 filters", readonly: true,
    stages: 5, wires: 4, last_run: null, last_result: "",
    updated_ts: 1_756_000_000,
  },
];

const FLOW_RECORD = {
  id: "quick-m31", name: "Quick · M31 LRGB", folder: "My flows",
  tagline: "12 subs each of L, R, G, B", readonly: false,
  graph: { nodes: [], edges: [] },
  last_run: 1_757_000_000, last_result: "ok", updated_ts: 1_757_000_500,
};

/** The server's own refusal, in the shape FastAPI actually sends it: the code
 *  and the payload NESTED under `detail` (`lib/apiError.ts` header). A flat body
 *  is the shape `flowsRun` used to look for and the server never sends, which is
 *  how the guard stayed dead. */
const UNMAPPED_409 = {
  detail: {
    detail: "parts of this flow do not survive the compile",
    code: "unmapped",
    unmapped: [
      { key: "nodes.safety", detail: "SAFETY: the monitor gate is global, not per flow", level: "warn" },
      { key: "nodes.abort", detail: "ABORT: no per-flow abort rule reaches the engine", level: "danger" },
    ],
  },
};

const asked: { url: string; method: string; body: any }[] = [];
/** Flipped by the test that has already seen the 409, so the retry succeeds. */
let acceptedOnce = false;
/** Flipped to make `GET /api/flows` fail, for the RETRY assertion. */
let libraryFails = false;
/** Flow ids whose `GET /api/flows/{id}` answers 404, for the RUN-identity test.
 *  A 404 is the ordinary case (a flow deleted from another browser, a stale
 *  bookmark) and it is the one that used to run the WRONG flow. */
const openFails = new Set<string>();

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });

  if (url === "/api/flows") {
    if (libraryFails) {
      return {
        ok: false, status: 503, statusText: "Service Unavailable",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "the flow store is not mounted" }),
      };
    }
    return ok(CARDS);
  }
  if (url === "/api/flows/folders") {
    return ok([{ name: "My flows", count: 1, readonly: false }]);
  }
  if (url === "/api/flows/compile") {
    return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  }
  if (/^\/api\/flows\/[^/]+\/run$/.test(url) && method === "POST") {
    if (body?.accept_unmapped || acceptedOnce) {
      return ok({ started: true, flow_id: "quick-m31", frames: 48, unmapped: [] });
    }
    return {
      ok: false, status: 409, statusText: "Conflict",
      headers: { get: () => "application/json" },
      json: async () => UNMAPPED_409,
    };
  }
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  if (one) {
    const id = one[1];
    // PUT is `flowsApi.save` - the request the whole save path exists to make.
    // It echoes what it was handed, as the server does.
    if (method === "PUT") return ok({ ...FLOW_RECORD, ...(body?.flow ?? {}), id });
    if (openFails.has(id)) {
      return {
        ok: false, status: 404, statusText: "Not Found",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "no flow with that id" }),
      };
    }
    // THE RECORD CARRIES THE ID THAT WAS ASKED FOR. A stub that answered with
    // one fixed record no matter which flow was requested could not tell a
    // correct open from the defect under test.
    const card = CARDS.find((c) => c.id === id);
    return ok({
      ...FLOW_RECORD, id,
      name: card?.name ?? FLOW_RECORD.name,
      readonly: card?.readonly ?? false,
    });
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { readFileSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");
const { dirname, join } = await import("node:path");

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { runBlockedReason } = await import("../../../../../components/flows/flowRunControls");
const { buildHash, nav, resetRouterCacheForTests } = await import("../../../../router");
const { FLOW_OPEN_FAILED } = await import("../openFlow");
const { RUN_IN_PROGRESS_REASON } = await import("../FlowsScreen");
const {
  FlowsScreen, CANVAS_PHONE_REASON, FLOWS_FOOTER, NO_MATCH_HINT, FILTER_PLACEHOLDER,
} = await import("../FlowsScreen");
const { JUST_SAVED } = await import("../FlowRow");
const { FLOW_STAGES_SHEET } = await import("../canvas/FlowStagesPhoneSheet");
const { flowCanvasSheets } = await import("../canvas/sheets");
const { flowInspectorSheets } = await import("../inspector/sheets");
const { flowTonightSheets } = await import("../tonight");
const { flowCreateSheets } = await import("../create");
const { SHEET_ENTRIES } = await import("../../../index");

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
let root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const typeInto = (el: any, value: string): void => {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
const runs = () => asked.filter((a) => a.method === "POST" && /\/run$/.test(a.url));

function seed(role: string, caps: string[]): void {
  act(() => {
    const f = useStore.getState().flows;
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      // A connected camera, or `runBlockedReason` refuses for a reason that has
      // nothing to do with the capability under test.
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null as never,
      // `flowsRun` writes `run.phase` optimistically and nothing on the server
      // ever clears it, so a test that ran one flow would find every later row
      // showing LIVE. `ui.screen` is the same shape of leak: `flowsOpen` sets it
      // to "editor" and `FlowsScreen` reads it to keep the canvas up under a
      // sheet, so a stale one would open the canvas over the list. Reset both
      // with the rest of the fixture.
      flows: {
        ...f,
        record: null,
        run: { ...f.run, phase: "idle" },
        ui: { ...f.ui, screen: "library", query: "", folderChip: "all", highlightId: null },
      } as never,
    } as never);
  });
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

/** Remount from a named hash. `useRoute` caches its parse, so a test that
 *  assigns `location.hash` without this reads the previous route. */
async function mountAt(hash: string): Promise<void> {
  win.location.hash = hash;
  resetRouterCacheForTests();
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

const ADMIN = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];

// ========================================================== 1. it rendered

seed("admin", ADMIN);
await mount();

test("precondition: the list rendered with rows from the stubbed library", () => {
  assert(tid("session-flows") != null, "no session-flows marker - the fixture is wrong, not the screen");
  assert(tid("flow-row-quick-m31") != null, "the first flow never rendered");
  assert(tid("flow-row-example-m16") != null, "the second flow never rendered");
  assert(/Quick · M31 LRGB/.test(tid("flow-row-quick-m31").textContent),
    "the row carries the flow's name");
});

test("the header counts what the library actually returned", () => {
  const s = tid("flows-summary");
  assert(s != null, "no summary line");
  assert(/2 saved · quick sessions land here/.test(s.textContent),
    `the proto's line, got "${s.textContent}"`);
});

test("the meta line carries the server's own status word, not invented copy", () => {
  const meta = tid("flow-meta-quick-m31").textContent as string;
  assert(/7 stages · 6 wires/.test(meta), `stages and wires, got "${meta}"`);
  assert(/completed clean/.test(meta), `the "ok" word, got "${meta}"`);
  assert(/never run/.test(tid("flow-meta-example-m16").textContent),
    "a flow that never ran must say so, not read as a clean one");
});

test("with the folders mixed the row says which folder it came from", () => {
  // The legacy library grouped the cards under folder headings; one flat list is
  // the point of the collapse, so the folder rides in the meta line. Without it
  // ALL is a list an operator cannot tell apart.
  assert(/^My flows · /.test(tid("flow-meta-quick-m31").textContent),
    "a row under ALL does not say which folder it is in");
  assert(/^Examples · /.test(tid("flow-meta-example-m16").textContent),
    "the example flow is not marked as one");
});

test("no row claims to be busy while nothing is running", () => {
  // `busyId` and a non-campaign row's `sessionId` are BOTH null, so
  // `busyId === r.sessionId` was true for every ordinary row: every RUN in the
  // list wore a spinner and reported `aria-busy` from first paint. Found by
  // looking at the probe screenshot at 820, which is the only place it showed.
  for (const id of ["quick-m31", "example-m16"]) {
    eq(tid(`flow-verb-${id}`).getAttribute("aria-busy"), null,
      `${id} claims aria-busy with no run in flight`);
  }
});

test("the footer states what RUN does and why the canvas is elsewhere", () => {
  assert(container.textContent.includes(FLOWS_FOOTER), "the proto's footer note is missing");
});

// ================================================ 2. RUN reaches the run route

await testAsync("RUN opens the flow, then posts to that flow's run route", async () => {
  const before = runs().length;
  click(tid("flow-verb-quick-m31"));
  await settle();

  // The open is the local re-compile the footer promises.
  assert(asked.some((a) => a.url === "/api/flows/quick-m31" && a.method === "GET"),
    "RUN must open the flow before it can post against it");
  assert(asked.some((a) => a.url === "/api/flows/compile" && a.method === "POST"),
    "opening a flow re-compiles it");

  const sent = runs();
  eq(sent.length, before + 1, "RUN must issue exactly one run request");
  eq(sent[sent.length - 1].url, "/api/flows/quick-m31/run", "and against THIS flow");
});

// ============================================ 3. the 409 is a question, not an error

await testAsync("a 409 unmapped opens the compile confirm and RUN ANYWAY re-fires accepted", async () => {
  const req = useStore.getState().confirm;
  assert(req != null, "the 409 raised no confirm - RUN silently did nothing");
  eq(req?.title as string, "Parts of this flow do not survive the compile",
    "the title is the server's own refusal sentence");

  // The list is rendered from `u.detail`, so the operator sees what the engine
  // will drop rather than the word "unmapped".
  const scratch = win.document.createElement("div");
  win.document.body.appendChild(scratch);
  const probe = createRoot(scratch);
  await act(async () => { probe.render(req?.body as any); });
  assert(/the monitor gate is global, not per flow/.test(scratch.textContent),
    "every unmapped detail the server sent has to be on screen");
  assert(/no per-flow abort rule reaches the engine/.test(scratch.textContent),
    "a second refusal must not collapse into the first");
  await act(async () => { probe.unmount(); });

  const before = runs().length;
  await act(async () => { useStore.getState().resolveConfirm(true); });
  await settle();

  const sent = runs();
  eq(sent.length, before + 1, "RUN ANYWAY must re-issue the run");
  eq(sent[sent.length - 1].body?.accept_unmapped, true,
    "and it must carry accept_unmapped - without it the server refuses again");
  acceptedOnce = true;
});

await testAsync("CANCEL on that confirm sends nothing", async () => {
  acceptedOnce = false;
  seed("admin", ADMIN);
  await settle();
  click(tid("flow-verb-quick-m31"));
  await settle();
  assert(useStore.getState().confirm != null, "precondition: the 409 confirm is up again");
  const before = runs().length;
  await act(async () => { useStore.getState().resolveConfirm(false); });
  await settle();
  eq(runs().length, before, "CANCEL must be a real no");
  acceptedOnce = true;
});

// ================================================ 4. the phone gets inside the flow

await testAsync("a phone row tap opens the flowStages sheet on THAT flow", async () => {
  seed("admin", ADMIN);
  await mountAt("#/session/flows");

  const open = tid("flow-open-quick-m31");
  assert(open != null, "the OPEN control must still be rendered on a phone");
  eq(open.getAttribute("aria-disabled"), null,
    "the phone is no longer refused: the row opens the stage list");
  assert(/stage list/.test(open.getAttribute("aria-label") ?? ""),
    "the accessible name still promises the CANVAS, which this phone will never draw - "
    + "the promise a row makes has to be the screen the press produces");

  click(open);
  await settle();
  eq(win.location.hash, "#/session/flows/flowStages?open=quick-m31",
    "the stage list is route state, so it survives a reload and names its flow");
});

await testAsync("OPEN THE FLOWS CANVAS keeps its reason on a phone and reaches nothing", async () => {
  await mountAt("#/session/flows");
  const canvas = tid("flows-open-canvas");
  assert(canvas != null, "OPEN THE FLOWS CANVAS must still be rendered");
  eq(canvas.getAttribute("aria-disabled"), "true", "it must be honest-disabled, never `disabled`");
  eq(canvas.getAttribute("title"), CANVAS_PHONE_REASON, "and name the reason");
  assert(container.textContent.includes(CANVAS_PHONE_REASON),
    "the reason has to be readable without a hover a touch screen cannot perform");

  const before = asked.length;
  const hash = win.location.hash;
  click(canvas);
  await settle();
  eq(asked.length, before, "a locked control must not fetch");
  eq(win.location.hash, hash, "and must not navigate to a canvas the phone cannot draw");
});

test("the phone still has the SKY route to a new flow, and it says what SKY adds", () => {
  const row = tid("flows-create-phone");
  assert(row != null, "the phone must not simply lose the SKY creation path");
  assert(/created from a target in SKY/.test(row.textContent),
    "and the row has to say where it happens");
  assert(tid("flows-quick") != null,
    "QUICK FLOW is a sheet now, so it works on a phone too and must be offered");
});

// ============================================== 5. the viewer sees it locked

await testAsync("a viewer sees the same rows, locked, and fires nothing", async () => {
  seed("viewer", ["view.status", "view.preview"]);
  await settle();

  assert(tid("flow-row-quick-m31") != null,
    "a viewer must see the same list, not an empty screen");

  const verb = tid("flow-verb-quick-m31");
  eq(verb.getAttribute("aria-disabled"), "true", "RUN must be honest-disabled for a viewer");
  eq(verb.getAttribute("title"), "Running a flow needs operator or admin access.",
    "and name the capability, from accessPhrase - never a hand-written role");
  assert(container.textContent.includes("Running a flow needs operator or admin access."),
    "the reason has to be on the screen, not only in a tooltip");

  const before = asked.length;
  const confirmBefore = useStore.getState().confirm;
  click(verb);
  await settle();
  eq(asked.length, before, "a locked RUN must not reach the network");
  eq(useStore.getState().confirm, confirmBefore, "and must not raise a dialog");

  eq(tid("flows-new").getAttribute("aria-disabled"), "true",
    "creating a flow needs control.capture, which a viewer does not hold");
  eq(tid("flows-quick").getAttribute("aria-disabled"), "true",
    "and so does the quick flow");
});

// ================================== 6. the library controls the canvas gave up

await testAsync("the filter narrows the list and writes flows.ui.query", async () => {
  seed("admin", ADMIN);
  await mountAt("#/session/flows");

  const box = tid("flows-filter");
  assert(box != null, "the filter box did not come across from FlowLibrary");
  eq(box.getAttribute("placeholder"), FILTER_PLACEHOLDER, "the proto's placeholder");

  typeInto(box, "dusk");
  await settle();
  eq(useStore.getState().flows.ui.query, "dusk", "the filter must write the shared slice field");
  assert(tid("flow-row-example-m16") != null, "the matching flow is gone");
  assert(tid("flow-row-quick-m31") == null, "a non-matching flow is still listed");
  assert(/1 of 2 shown/.test(tid("flows-summary").textContent),
    "the summary still claims the library total while a filter is on");
  // The folder counts are of what is VISIBLE, which is the whole reason the
  // legacy library counted its own cards instead of the server's folder totals:
  // a chip reading 7 over a list of 1 makes the operator count rows to find out
  // which number is lying.
  eq(tid("flows-folder-all").textContent, "ALL1",
    "the folder chips count the library, not the filtered list");
  eq(tid("flows-folder-mine").textContent, "MINE0",
    "and MINE still claims a card the filter removed");
});

await testAsync("a filter that matches nothing says so, with the query in it", async () => {
  typeInto(tid("flows-filter"), "zzzz");
  await settle();
  const empty = tid("flows-no-match");
  assert(empty != null, "no empty state - a filter with no hits rendered a blank list");
  assert(/zzzz/.test(empty.textContent), "the empty state must quote what was searched for");
  assert(empty.textContent.includes(NO_MATCH_HINT), "and say what to do about it");
  assert(!/—/.test(empty.textContent), "em-dash in UI copy");

  typeInto(tid("flows-filter"), "");
  await settle();
});

await testAsync("the folder chips count VISIBLE cards and switch the folder", async () => {
  const all = tid("flows-folder-all");
  const mine = tid("flows-folder-mine");
  const examples = tid("flows-folder-examples");
  assert(all != null && mine != null && examples != null, "the three folder chips are missing");
  eq(all.textContent, "ALL2", "ALL counts every card that matches the filter");
  eq(mine.textContent, "MINE1", "MINE counts the cards in writable folders");
  eq(examples.textContent, "EXAMPLES1", "EXAMPLES counts the read-only ones");

  click(examples);
  await settle();
  eq(useStore.getState().flows.ui.folderChip, "examples", "the chip must write the shared slice field");
  assert(tid("flow-row-quick-m31") == null, "a chip that filters nothing is decoration");
  assert(tid("flow-row-example-m16") != null, "and it filtered out the wrong side");

  click(tid("flows-folder-all"));
  await settle();
});

await testAsync("the two creation cells open their sheets", async () => {
  click(tid("flows-new"));
  await settle();
  eq(win.location.hash, "#/session/flows/flowNew", "+ NEW FLOW must open the wizard sheet");

  await mountAt("#/session/flows");
  click(tid("flows-quick"));
  await settle();
  eq(win.location.hash, "#/session/flows/flowQuick", "QUICK FLOW must open the quick sheet");
});

await testAsync("a library that could not be read says so and offers a RETRY that refetches", async () => {
  libraryFails = true;
  seed("admin", ADMIN);
  await mountAt("#/session/flows");

  const err = tid("flows-error");
  assert(err != null, "a failed library load rendered no error line");
  assert(/the flow store is not mounted/.test(err.textContent),
    "the server's own message, not a generic one");

  libraryFails = false;
  const before = asked.filter((a) => a.url === "/api/flows").length;
  click(tid("flows-retry"));
  await settle();
  eq(asked.filter((a) => a.url === "/api/flows").length, before + 1,
    "RETRY must actually re-ask - the slice leaves libraryLoaded false so nothing retries on its own");
  assert(tid("flow-row-quick-m31") != null, "and the rows must come back");
});

await testAsync("a just-saved flow is named in words, not by a ring", async () => {
  await act(async () => { useStore.getState().flowsSetUi({ highlightId: "quick-m31" }); });
  await settle();
  const mark = tid("flow-new-quick-m31");
  assert(mark != null,
    "flows.ui.highlightId is written by the QUICK FLOW sheet (create/quick.tsx, both the save "
    + "and the save-and-run path) and read by nothing - the wizard navigates to the flow instead");
  eq(mark.textContent, JUST_SAVED, "the highlight has to carry a word, never a colour alone");
  await act(async () => { useStore.getState().flowsSetUi({ highlightId: null }); });
});

// ======================================= 7. the tablet gets the REBUILT canvas

await testAsync("at 768 px and up OPEN unlocks and routes to the canvas", async () => {
  seed("admin", ADMIN);
  viewportW = 1024;
  await mountAt("#/session/flows");

  const open = tid("flow-open-quick-m31");
  assert(open != null, "precondition: the row is on screen at tablet width");
  eq(open.getAttribute("aria-disabled"), null, "OPEN must be live where the canvas exists");

  click(open);
  await settle();
  eq(win.location.hash, "#/session/flows?open=quick-m31",
    "OPEN puts the flow in the route, so the canvas survives a reload and a share");

  assert(tid("session-flows-canvas") != null, "the hub body never switched to the canvas");
  assert(tid("flow-toolbar") != null, "the rebuilt canvas toolbar is not mounted");
  assert(tid("flows-canvas") != null, "the rebuilt canvas surface is not mounted");
  assert(asked.some((a) => a.url === "/api/flows/quick-m31" && a.method === "GET"),
    "opening the canvas on a flow loads that flow");
});

test("the LEGACY canvas is not mounted anywhere on that screen", () => {
  // `FlowsView`'s own outermost marker. Asserting only our own wrapper would
  // pass over either implementation; this is the half that says WHICH one.
  assert(container.querySelector('[data-view="flows"]') == null,
    "components/flows/FlowsView is still mounted - the cutover did not happen");
  assert(container.querySelector('[data-flows-tab="library"]') == null,
    "the legacy FlowLibrary is still rendering, which is the second MY FLOWS this wave removed");
});

const myFlowsTitles = (): number => Array.from(container.querySelectorAll("*"))
  .filter((el: any) => (el.textContent ?? "").trim() === "MY FLOWS").length;

await testAsync("exactly one MY FLOWS renders at tablet width", async () => {
  // Wave R7 section 6.1 defect 7, counted rather than eyeballed. Exact trimmed
  // text, so an ancestor whose textContent merely CONTAINS the words does not
  // count, the canvas's `< MY FLOWS` back button does not count (it carries the
  // chevron), and the folder chip deliberately reads MINE so it cannot collide.
  //
  // A clean fixture first, because `flowsOpen` leaves `ui.screen` on "editor"
  // and the screen honours that (see `FlowsScreen`'s canvas branch): the test
  // before this one opened a flow. Order matters - seeding while the canvas
  // route is still live re-renders the host, whose effect re-opens the flow it
  // was told to show.
  await mountAt("#/session/flows");
  seed("admin", ADMIN);
  await settle();
  eq(myFlowsTitles(), 1,
    `two screens with one title is the defect this cutover closes; found ${myFlowsTitles()}`);

  // And the route that USED to produce the second one. `?open=library` opened
  // the legacy canvas on its own MY FLOWS grid, stacked on this screen's list.
  await mountAt("#/session/flows?open=library");
  eq(myFlowsTitles(), 0, "the canvas is drawing a library again");
  assert(tid("flows-list") == null, "a second list of flows rendered on the canvas route");
  assert(tid("flows-canvas-no-flow") != null,
    "the canvas opened on nothing and said nothing - it has to name what it wants");
});

await testAsync("desktop docks the palette rail and the inspector column", async () => {
  viewportW = 1440;
  await mountAt("#/session/flows?open=quick-m31");
  assert(tid("flows-canvas") != null, "precondition: the canvas is on screen at desktop width");
  assert(tid("flow-palette") != null, "the palette rail is not docked at desktop");
  assert(tid("flow-inspector") != null, "the inspector column is not docked at desktop");
});

// ======================= 8. the canvas does not vanish under its own sheet

await testAsync("opening a stage sheet does not close the canvas", async () => {
  // `nav.sheet` rebuilds the hash from the params it is HANDED, so the pencil on
  // a node card - which passes `{ node }` - clears `?open=`. The canvas has to
  // survive that, and the URL has to be put back, or the address bar claims the
  // list is showing.
  await mountAt("#/session/flows?open=quick-m31");
  assert(tid("session-flows-canvas") != null, "precondition: the canvas is up");
  eq(useStore.getState().flows.ui.screen, "editor", "precondition: the slice says the editor is showing");

  await act(async () => { nav.sheet("flowNode", { node: "n-1" }); });
  await settle();

  assert(tid("session-flows-canvas") != null,
    "the canvas closed when a stage was opened for editing - the sheet would sit over MY FLOWS");
  assert(/open=quick-m31/.test(win.location.hash),
    `?open= was not restored, so the URL claims the list is showing: ${win.location.hash}`);
});

await testAsync("BACK from the canvas really lands on the list", async () => {
  await mountAt("#/session/flows?open=quick-m31");
  click(tid("flows-canvas-back"));
  await settle();
  eq(useStore.getState().flows.ui.screen, "library",
    "BACK left ui.screen on `editor`, so the list re-opens the canvas and is unreachable");
  assert(tid("session-flows") != null, "BACK did not reach MY FLOWS");
  assert(tid("session-flows-canvas") == null, "the canvas is still up after BACK");
});

// ============================== 8b. every way out of the editor SAVES first

/** Edit the open flow the way the palette does: one new stage, so the PUT that
 *  follows carries a graph that is visibly not the one the server sent. */
async function editOpenFlow(type: string): Promise<void> {
  await act(async () => {
    useStore.getState().flowsAddNode(type as never, { x: 40, y: 40 });
  });
  await settle();
  eq(useStore.getState().flows.dirty, true,
    "precondition: the store did not record the edit, so there is nothing for BACK to save");
}

await testAsync("BACK saves the edit, and the PUT lands BEFORE the library reload", async () => {
  // THE P0. Nothing under `next/**` called `flowsSave` or `flowsCloseEditor`:
  // the canvas had no save path at all, so BACK, the FLOWS chip and a reload
  // each dropped the graph silently. The ORDER is half the assertion - the
  // legacy header's own sentence is "saves first, then reloads the library", and
  // a reload that overtook the save would repaint the list from the flow as it
  // was before the edit.
  viewportW = 1024;
  seed("admin", ADMIN);
  await mountAt("#/session/flows?open=quick-m31");
  assert(tid("session-flows-canvas") != null, "precondition: the canvas is not up on the flow");
  await editOpenFlow("target");

  const from = asked.length;
  click(tid("flows-canvas-back"));
  await settle();
  const after = asked.slice(from);

  const put = after.findIndex((a) => a.method === "PUT" && a.url === "/api/flows/quick-m31");
  const list = after.findIndex((a) => a.method === "GET" && a.url === "/api/flows");
  assert(put >= 0,
    `BACK discarded the edit: no PUT /api/flows/quick-m31 went out, only ${
      JSON.stringify(after.map((a) => `${a.method} ${a.url}`))}`);
  assert(list >= 0, "the library was never reloaded, so MY FLOWS still shows the pre-edit card");
  assert(put < list,
    "the library reload overtook the save, so the list is drawn from the flow as it was before the edit");
  eq((after[put].body?.flow?.graph?.nodes ?? []).length, 1,
    "the PUT went out with the graph the server already had - the edit was not in it");
  eq(useStore.getState().flows.dirty, false, "the store still calls the flow dirty after a save");
  assert(tid("session-flows") != null, "BACK did not reach MY FLOWS");
});

await testAsync("the FLOWS sub-nav chip is a way out too, and it saves on the way", async () => {
  viewportW = 1024;
  seed("admin", ADMIN);
  await mountAt("#/session/flows?open=quick-m31");
  await editOpenFlow("slew");

  const from = asked.length;
  // EXACTLY what a chip press builds (`shell/SubNav.tsx:36`): the hub and the
  // sub, no sheets and no params. The chip lives in the shell, so this screen
  // cannot intercept the press - it can only notice the route it produced.
  await act(async () => {
    nav.replace(buildHash({ hub: "session", sub: "flows", sheets: [], params: {} }));
  });
  await settle();

  assert(asked.slice(from).some((a) => a.method === "PUT" && a.url === "/api/flows/quick-m31"),
    "the FLOWS chip cleared ?open= and threw the graph away - it is a door out of the editor");
  assert(tid("session-flows") != null,
    "the chip did not reach the list: the canvas stayed up on flows.ui.screen alone, over a URL "
    + "that claims the list is showing");
  eq(useStore.getState().flows.record, null, "the editor is still holding the flow after leaving it");
});

// ======================== 8b2. the phone has the same way out

await testAsync("a phone that loses its stage sheet to the chip still stores the edit", async () => {
  // THE PHONE'S HALF OF THE SAME P0. Its editor is the `flowStages` SHEET, and
  // when a FLOWS chip press or the browser's Back button pops that sheet,
  // nothing of it is mounted to notice - the canvas host that catches this at
  // 768 px and up never mounts here. The list is the only thing left on screen,
  // so the list is what has to save.
  viewportW = 390;
  seed("admin", ADMIN);
  await mountAt("#/session/flows/flowStages?open=quick-m31");
  await act(async () => { await useStore.getState().flowsOpen("quick-m31"); });
  await settle();

  const from = asked.length;
  await act(async () => {
    useStore.getState().flowsAddNode("slew" as never, { x: 1, y: 1 });
  });
  await settle();
  assert(!asked.slice(from).some((a) => a.method === "PUT"),
    "the flow was stored mid-edit, while the editor is still open and the operator still typing");

  await mountAt("#/session/flows");
  assert(asked.slice(from).some((a) => a.method === "PUT" && a.url === "/api/flows/quick-m31"),
    "the phone was left holding an edited flow with no editor on screen and never stored it");
  eq(useStore.getState().flows.dirty, false, "and the store still calls it dirty afterwards");
});

// ============================ 8c. RUN on a row runs the flow on that row

await testAsync("a row whose flow will not open starts NOTHING", async () => {
  // THE SECOND P0. `flowsOpen` swallows its failure and leaves the previously
  // loaded record in place, so `await flowsOpen(B); act()` posted
  // `/api/flows/A/run` - a press on one row starting another flow. Invisible
  // until the wrong mount moves.
  viewportW = 390;
  seed("admin", ADMIN);
  await mountAt("#/session/flows");
  await act(async () => { await useStore.getState().flowsOpen("quick-m31"); });
  await settle();
  eq(useStore.getState().flows.record?.id, "quick-m31",
    "precondition: flow A is not the loaded one, so the test cannot see the wrong-flow run");

  openFails.add("example-m16");
  const before = runs().length;
  click(tid("flow-verb-example-m16"));
  await settle();
  openFails.delete("example-m16");

  eq(runs().length, before,
    `a press on B started a flow anyway: ${JSON.stringify(runs().map((r) => r.url))}`);
  eq(useStore.getState().flows.record?.id, "quick-m31",
    "the failed open left a record that is neither flow");
  const toast = useStore.getState().toasts.find((t: any) => String(t.title) === FLOW_OPEN_FAILED);
  assert(toast != null,
    `a press that does nothing has to say why: ${
      JSON.stringify(useStore.getState().toasts.map((t: any) => t.title))}`);
  assert(/no flow with that id/.test(String(toast?.detail)),
    `the server's own words, got "${String(toast?.detail)}"`);
});

await testAsync("while a run is live the other rows are refused by name", async () => {
  seed("admin", ADMIN);
  await mountAt("#/session/flows");
  await act(async () => {
    const f = useStore.getState().flows;
    useStore.setState({ flows: { ...f, run: { ...f.run, phase: "running" } } } as never);
  });
  await settle();

  const verb = tid("flow-verb-example-m16");
  eq(verb.getAttribute("aria-disabled"), "true",
    "a second RUN while one is live silently navigated instead of refusing");
  eq(verb.getAttribute("title"), RUN_IN_PROGRESS_REASON, "and it has to name the blocker");
  assert(container.textContent.includes(RUN_IN_PROGRESS_REASON),
    "the reason has to be readable without a hover a touch screen cannot perform");

  const before = runs().length;
  click(verb);
  await settle();
  eq(runs().length, before, "a locked row still reached the run route");

  seed("admin", ADMIN);
  await settle();
});

// ================================ 9. the six sheets are actually registered

await testAsync("the docked inspector stands down while its own sheet shows the same editor", async () => {
  // At desktop the `flowNode` sheet IS the right-hand panel, beside the docked
  // inspector column - and both render `FlowInspectorColumn` with the same
  // calibration slot inside it. Two mounts means two `GET
  // /api/calibration/health` on one press of one pencil, and two copies of one
  // editor on screen to disagree the moment either is mid-edit.
  viewportW = 1440;
  seed("admin", ADMIN);
  await mountAt("#/session/flows?open=quick-m31");
  assert(tid("flow-inspector") != null,
    "precondition: the inspector column is not docked at desktop, so this proves nothing");

  await act(async () => { nav.sheet("flowNode", { open: "quick-m31", node: "n-1" }); });
  await settle();
  assert(tid("flow-inspector") == null,
    "the docked column is still rendering the editor the sheet beside it is already showing");
  assert(tid("flows-canvas") != null,
    "the canvas itself has to stay up under the sheet - standing the column down is not closing the graph");

  await act(async () => { nav.closeSheet(); });
  await settle();
  assert(tid("flow-inspector") != null, "and the column has to come back when the sheet closes");
});

test("every sheet the four Flows areas register resolves in the composed registry", () => {
  const mine: Record<string, string> = {};
  for (const reg of [flowCanvasSheets, flowInspectorSheets, flowTonightSheets, flowCreateSheets]) {
    for (const [name, entry] of Object.entries(reg)) mine[name] = entry.id;
  }
  eq(Object.keys(mine).sort().join(","),
    "flowNew,flowNode,flowPalette,flowQuick,flowStages,flowTonight",
    "the four areas do not publish the six names the cutover composes");

  for (const [name, id] of Object.entries(mine)) {
    const entry = SHEET_ENTRIES[name];
    assert(entry != null,
      `"${name}" is not in SHEET_ENTRIES - the hash would render SheetHost's "not built yet" pane`);
    eq(entry.id, id, `"${name}" composed under a different module than the area registered:`);
    eq(typeof entry.load, "function", `"${name}" has no loader`);
  }
});

test("no two registered sheets share a name under two module ids", () => {
  // `hubs/index.ts` throws on this at module load in dev and under the test
  // runner, so reaching this line at all is half the assertion. The other half
  // is that the six new names did not quietly overwrite six existing screens.
  const ids = Object.values(SHEET_ENTRIES).map((e) => e.id);
  const names = Object.keys(SHEET_ENTRIES);
  assert(names.length >= 30, `only ${names.length} sheets composed - the registry is not loading`);
  eq(new Set(names).size, names.length, "a name is registered twice in the composed map");
  // Two names may share a module (the inspector's two sheets live in one file),
  // so ids are not required to be unique - but a module registered under six
  // names would mean five screens collapsed onto one.
  assert(new Set(ids).size >= names.length - 2,
    "too many sheet names point at one module - screens have collapsed onto each other");
});

test("the registry key and the sheet module's own constant are the same word", () => {
  // The registry may not IMPORT the sheet module (that would put the phone stage
  // list in the entry chunk), so the name is spelled twice. A silent
  // disagreement is a route that opens nothing.
  eq(FLOW_STAGES_SHEET, "flowStages", "FlowStagesPhoneSheet renamed itself");
  assert(flowCanvasSheets[FLOW_STAGES_SHEET] != null,
    "canvas/sheets.ts registers a name the sheet module does not answer to");
});

// ==================================== 10. the source text, not just the DOM

/** Every module specifier a file imports from. Comments are not scanned - this
 *  file's own header names `components/flows/FlowsView` in prose, and a test
 *  that could not tell prose from an import would be unwritable. */
function specifiers(src: string): string[] {
  return Array.from(src.matchAll(/from\s+"([^"]+)"/g)).map((m) => m[1]);
}

test("FlowsCanvasHost does not import the legacy FlowsView", () => {
  // The `shellCss.test.ts` idiom: some facts are properties of the FILE, and a
  // legacy import that happens to render nothing on the fixture looks exactly
  // like no legacy import at all.
  const here = dirname(fileURLToPath(import.meta.url));
  const host = specifiers(readFileSync(join(here, "..", "FlowsCanvasHost.tsx"), "utf8"));
  const legacy = host.filter((sp) => sp.includes("components/flows/"));
  eq(legacy.join(","), "",
    "FlowsCanvasHost.tsx still imports a legacy flows component - the cutover did not happen");
  assert(host.includes("./canvas") && host.includes("./inspector"),
    "FlowsCanvasHost.tsx composes neither rebuilt area - it cannot be the new canvas");

  // The screen may still import the LOGIC modules (`cardMeta`, `cardStatus`,
  // `runBlockedReason` - wave R7 section 2.1), and must not import either
  // legacy PRESENTATION component it replaced.
  const screen = specifiers(readFileSync(join(here, "..", "FlowsScreen.tsx"), "utf8"));
  for (const banned of ["components/flows/FlowLibrary", "components/flows/FlowsView"]) {
    assert(!screen.includes(`../../../../${banned}`),
      `FlowsScreen.tsx imports ${banned}, which is the screen it replaced`);
  }
});

// ============================= 11. the one additive edit to a legacy module

test("runBlockedReason's classic three-argument call is unchanged", () => {
  // T-U7a-G's finding: the sentence hard-coded "flow", so a saved PLAN offered
  // through the same gate refused by naming something not on the row. The noun
  // is now a parameter - and its DEFAULT is what every existing caller relies
  // on, in `#/classic` as much as here.
  eq(runBlockedReason(false, true, false), "Running a flow needs operator or admin access.",
    "the default noun changed, so every classic caller's sentence changed with it");
  eq(runBlockedReason(true, false, false),
    "No camera is connected, so there is nothing to run this flow on.",
    "the camera sentence's default noun changed");
  eq(runBlockedReason(false, true, true), "Stopping a run needs operator or admin access.",
    "STOP never named the thing, and must not start");
  eq(runBlockedReason(true, true, false), null, "an operator with a camera can still run");
  eq(runBlockedReason(false, true, false, "plan"), "Running a plan needs operator or admin access.",
    "the whole point of the parameter");
});

// ================== 12. the canvas has a height of its own (the P1 this closes)

/** The area stylesheet, comments stripped. jsdom loads no CSS and computes no
 *  layout, so a DOM test cannot see a zero-height pane - which is exactly why
 *  the blank tablet canvas shipped past a green suite. What a jsdom test CAN
 *  do is grade the contract: the surface carries the class, and the class has a
 *  rule that does not read a sibling. Both halves are needed. The class alone
 *  is a promise with nothing behind it; the rule alone is a rule on nothing. */
const canvasCss = (): string =>
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "canvas", "canvas.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");

/** One rule's declaration block, by exact selector. */
function ruleBody(css: string, selector: string): string | null {
  const at = css.indexOf(selector + " {");
  if (at < 0) return null;
  const open = css.indexOf("{", at);
  const close = css.indexOf("}", open);
  return close < 0 ? null : css.slice(open + 1, close);
}

await testAsync("the canvas surface carries the fill class at tablet width", async () => {
  // THE DEFECT, IN ONE ASSERTION. Before this, the surface's only class was
  // `.nx-flow-canvas`, whose rule set `flex: 1` and `min-height: 0` - a height
  // borrowed entirely from an ancestor chain that does not supply one, so the
  // measured box at 820 was 716 x 0 with all sixteen stage cards inside it.
  viewportW = 820;
  await mountAt("#/session/flows?open=quick-m31");
  const surface = tid("flows-canvas");
  assert(surface != null, "precondition: the canvas surface is not mounted at tablet width");
  const classes = String(surface.className).split(/\s+/);
  assert(classes.includes("nx-flow-fill"),
    `the surface has no height class, so its height is whatever an ancestor happens to give it: ${surface.className}`);
});

test("the fill class really sets a height, and reads no sibling to do it", () => {
  const css = canvasCss();
  const body = ruleBody(css, ".nx-flow-fill");
  assert(body != null, ".nx-flow-fill is emitted by the surface and no rule defines it");
  const minH = /min-height:\s*([^;]+);/.exec(body as string);
  assert(minH != null, ".nx-flow-fill sets no min-height, so it is not a height floor");
  const value = (minH as RegExpExecArray)[1].trim();
  assert(!/^(0|auto|0px)$/.test(value),
    `.nx-flow-fill's min-height is "${value}", which is the same as having none`);
  assert(/dvh|vh|px|%/.test(value),
    `.nx-flow-fill's min-height is "${value}" - a height floor has to be a length`);
  // A percentage would put the ancestor chain back in the answer, which is the
  // whole bug: a `%` height resolves against a parent whose own height is auto,
  // so it computes to auto and the pane is blank again.
  assert(!value.includes("%"),
    `.nx-flow-fill's min-height is "${value}" - a percentage resolves against the ancestor that has no height`);

  // And it is the LAST word on the surface's min-height: two single-class rules
  // setting the same property would make the answer depend on file order.
  const canvas = ruleBody(css, ".nx-flow-canvas");
  assert(canvas != null, "precondition: .nx-flow-canvas has no rule");
  assert(!/min-height:/.test(canvas as string),
    ".nx-flow-canvas sets min-height again, so which one wins depends on where it sits in the file");
});

test("the canvas row shrinks to the host rather than growing to the palette rail", () => {
  // The desktop half of the same defect: the row is a flex ROW whose tallest
  // child is the 192 px palette rail, and the rail's content is long. Without
  // `min-height: 0` the row grows to the rail (measured: 2648 px inside a
  // 1016 px hub body) instead of bounding it, and the rail's own
  // `overflow-y: auto` never engages.
  const css = canvasCss();
  const row = ruleBody(css, ".nx-flow-row");
  assert(row != null, ".nx-flow-row is emitted by FlowsCanvasHost and no rule defines it");
  assert(/min-height:\s*0/.test(row as string),
    ".nx-flow-row has no `min-height: 0`, so it grows to its tallest child and overflows the hub body");
  assert(/flex:\s*1/.test(row as string), ".nx-flow-row does not fill the host column");

  // The host is the column the row fills, and it is only a column if something
  // above it is one. That rule names whatever CONTAINS the host, not another
  // area's element, which is what keeps this fix area-local.
  const host = ruleBody(css, ".nx-flow-host");
  assert(host != null, ".nx-flow-host is emitted by FlowsCanvasHost and no rule defines it");
  assert(/flex-direction:\s*column/.test(host as string), ".nx-flow-host is not a column");
  assert(css.includes(":has(> .nx-flow-host)"),
    "nothing makes the host's own container a flex column, so the row below it has no height to fill");
});

test("FlowsCanvasHost styles its boxes from the area stylesheet", () => {
  // The inline `HOST`/`ROW` objects this replaced could not express a fallback
  // height, and an inline style cannot be graded by `r7Css.test.ts` either.
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(join(here, "..", "FlowsCanvasHost.tsx"), "utf8");
  assert(src.includes('className="nx-flow-host"'), "the host root lost its area class");
  assert(src.includes('className="nx-flow-row"'), "the canvas row lost its area class");
  assert(specifiers(src).includes("./canvas/canvas.css") || src.includes('import "./canvas/canvas.css"'),
    "FlowsCanvasHost.tsx emits canvas.css classes without importing the sheet");
});

// ========== 10. a clean flow does not read as a failing build, end to end
//
// Both halves of the screen at once, because that is how the defect was seen
// (2026-09-11, on the rig): the inspector column printed ten amber WARNING rows
// while the toolbar said GRAPH VALID and the canvas put an amber `!` on five
// cards. Each surface is guarded in its own file; this one asserts they agree.
//
// SABOTAGE CHECKS:
//   * let a note through `isLoss` -> the `!` count and the `data-loss` count go
//     red here and in canvasDom.
//   * file notes under NOT HONOURED BY A RUN again -> the `flow-losses` and
//     `data-level="warn"` assertions go red here and in inspectorDom.

const NOTE_GRAPH = {
  nodes: [
    { id: "n-slew", type: "slew", x: 0, y: 0, params: {} },
    { id: "n-guide", type: "guide", x: 300, y: 0, params: {} },
  ],
  edges: [{ id: "e-1", from: "n-slew", fromPort: "centered", to: "n-guide", toPort: "run" }],
};

const NOTES_E2E = [
  {
    key: "nodes.slew", level: "note",
    detail: "the SLEW node's settings do not reach the run",
    carried: ["presence: the run centres on the target"],
    ignored: ["solver ASTAP"], source: "Settings > Standards",
  },
  {
    key: "nodes.guide", level: "note",
    detail: "the GUIDE node's settings do not reach the run",
    carried: ["presence: the night guides"],
    ignored: ["settle 1.5 s", "dither 3 px", "provider PHD2"], source: "Rig > Guider",
  },
];

await testAsync("a flow whose only findings are notes reads as clean on every surface", async () => {
  viewportW = 1440;
  await mountAt("#/session/flows?open=quick-m31");
  assert(tid("flows-canvas") != null, "precondition: the canvas is not on screen at desktop width");

  act(() => {
    const s = useStore.getState();
    useStore.setState({
      flows: {
        ...s.flows,
        graph: JSON.parse(JSON.stringify(NOTE_GRAPH)),
        compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES_E2E },
      } as never,
    } as never);
  });
  await settle();

  const nodes = [...container.querySelectorAll('[data-testid="flow-node"]')];
  eq(nodes.length, 2,
    "precondition: the seeded graph never drew, so the canvas assertions would pass over nothing");

  // ONE panel, in the note vocabulary, and no loss heading anywhere.
  const panel = tid("flow-notes");
  assert(panel != null, "the inspector column has no FROM THE RIG panel");
  assert(String(panel.textContent).includes("FROM THE RIG"),
    `the panel is not headed in the note vocabulary, got "${String(panel.textContent).slice(0, 60)}"`);
  eq(container.querySelectorAll('[data-testid="flow-note-row"]').length, 2,
    "one row per note, and every one of them on screen");
  eq(tid("flow-losses"), null, "a note was filed under NOT HONOURED BY A RUN");

  // No amber, anywhere on the screen.
  eq(container.querySelectorAll(".nx-flow-node-loss").length, 0,
    "a clean flow still puts the amber ! on its cards");
  eq(container.querySelectorAll('[data-loss="warn"], [data-loss="danger"]').length, 0,
    "a clean flow still outlines its cards in amber");
  eq(container.querySelectorAll('.nx-flowins-line[data-level="warn"]').length, 0,
    "the inspector still prints an amber line on a flow with no warnings");

  // And the verdict pill agrees with them.
  const pill = container.querySelector('[data-testid="flow-toolbar"] [data-testid="flow-checks"]');
  assert(pill != null, "the toolbar's verdict pill is missing");
  eq(String(pill.textContent), "GRAPH VALID",
    "the pill disagrees with the two panels below it about whether this graph is valid");
  eq(pill.getAttribute("data-tone"), "good", "the verdict went off green over settings the rig owns");
});

// Unmount before the tally, the way `canvasDom.test.tsx` does. jsdom was built
// with `pretendToBeVisual: true`, so a tree left mounted keeps a requestAnimation
// Frame loop alive and the process never exits - `npx tsx <file>` then prints
// nothing at all, because the tally below is buffered behind a pipe that never
// sees EOF. Running this file directly is the documented convention, so it has
// to end.
await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
