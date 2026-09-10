// flowsDom.test.tsx - SESSION / FLOWS, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/flowsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
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
//   4. THE PHONE IS TOLD, NOT BLOCKED. OPEN is `aria-disabled` carrying the
//      canvas reason and reaches the network not at all - and RUN, on the same
//      row, still works, which is what the reason claims.
//   5. A VIEWER SEES THE SAME SCREEN, LOCKED, with the capability named and no
//      request fired.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `NextApp` imports `next.css` and `shell/shell.css` - it is the single import
// site for both, by contract, so that the cascade order cannot depend on module
// resolution order. Node has no idea what a `.css` file is, so a synchronous
// load hook answers with an empty module. This is the ONLY way to keep both
// facts true at once: the app has one style entry point, and that entry point
// is still mountable in a test.
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

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });

  if (url === "/api/flows") return ok(CARDS);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
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
  if (/^\/api\/flows\/[^/]+$/.test(url)) return ok(FLOW_RECORD);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FlowsScreen, CANVAS_PHONE_REASON, FLOWS_FOOTER } = await import("../FlowsScreen");

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
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const runs = () => asked.filter((a) => a.method === "POST" && /\/run$/.test(a.url));

function seed(role: string, caps: string[]): void {
  act(() => {
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
      // `flowsRun` writes this optimistically and nothing on the server ever
      // clears it, so a test that ran one flow would find every later row
      // showing LIVE. Reset with the rest of the fixture.
      flows: { ...useStore.getState().flows, record: null, run: { ...useStore.getState().flows.run, phase: "idle" } } as never,
    } as never);
  });
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

// ========================================================== 1. it rendered

seed("admin", ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"]);
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
  seed("admin", ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"]);
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

// ================================================ 4. the phone is told, not blocked

test("OPEN is honest-disabled on a phone, carrying the canvas reason", () => {
  const open = tid("flow-open-quick-m31");
  assert(open != null, "the OPEN control must still be rendered on a phone");
  eq(open.getAttribute("aria-disabled"), "true", "it must be honest-disabled, never `disabled`");
  eq(open.getAttribute("title"), CANVAS_PHONE_REASON, "and name the reason");
  assert(container.textContent.includes(CANVAS_PHONE_REASON),
    "the reason has to be readable without a hover a touch screen cannot perform");

  const canvas = tid("flows-open-canvas");
  assert(canvas != null, "OPEN THE FLOWS CANVAS must still be rendered");
  eq(canvas.getAttribute("aria-disabled"), "true", "the list-level control is locked the same way");
});

await testAsync("a locked OPEN reaches neither the network nor the router", async () => {
  const before = asked.length;
  const hash = win.location.hash;
  click(tid("flow-open-quick-m31"));
  click(tid("flows-open-canvas"));
  await settle();
  eq(asked.length, before, "a locked OPEN must not fetch");
  eq(win.location.hash, hash, "and must not navigate to a canvas the phone cannot draw");
});

test("the phone still has a way to make a flow, and it says where", () => {
  const row = tid("flows-create-phone");
  assert(row != null, "the phone must not simply lose flow creation");
  assert(/created from a target in SKY/.test(row.textContent),
    "and the row has to say where it happens");
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
});

// ======================================= 6. the tablet gets the actual canvas

await testAsync("at 768 px and up OPEN unlocks and routes to the canvas", async () => {
  seed("admin", ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"]);
  viewportW = 1024;
  await act(async () => { root.unmount(); });
  const root2 = createRoot(container);
  await act(async () => { root2.render(createElement(FlowsScreen as any)); });
  await settle();

  const open = tid("flow-open-quick-m31");
  assert(open != null, "precondition: the row is on screen at tablet width");
  eq(open.getAttribute("aria-disabled"), null, "OPEN must be live where the canvas exists");

  click(open);
  await settle();
  eq(win.location.hash, "#/session/flows?open=quick-m31",
    "OPEN puts the flow in the route, so the canvas survives a reload and a share");

  // ...and the route param mounts the real thing: `FlowsView`'s own outermost
  // marker, which is what `scripts/flows_visual_check.py` waits for. Asserting
  // our own wrapper alone would pass over an empty box.
  assert(tid("session-flows-canvas") != null, "the hub body never switched to the canvas");
  assert(container.querySelector('[data-view="flows"]') != null,
    "components/flows/FlowsView is not mounted - the canvas is a re-implementation, not the real one");
  assert(asked.some((a) => a.url === "/api/flows/quick-m31" && a.method === "GET"),
    "opening the canvas on a flow loads that flow");

  await act(async () => { root2.unmount(); });
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
