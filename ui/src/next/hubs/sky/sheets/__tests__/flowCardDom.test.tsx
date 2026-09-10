// flowCardDom.test.tsx - the flow card, MOUNTED, and RUN NOW pressed into a 409.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/flowCardDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ONE BEHAVIOUR THAT IS WORTH A DOM TEST: a 409 carrying `unmapped` is the
// server asking a QUESTION, not reporting a failure. This exact read was DEAD
// for as long as it existed on the Flows surface - the guard looked for
// `err.unmapped`, ApiError carried neither that nor the decoded body, and since
// almost every real graph carries a loss, RUN silently did nothing on the rig
// for months. So the assertion here is not "a dialog appeared": it is that
// accepting fires a SECOND request and that the second one carries
// `accept_unmapped: true`.
//
// The lane is asserted from a REAL quick-flow graph (dusk -> target -> slew ->
// autofocus -> guide -> cycle -> report), because the order comes from
// `autoLayout.flowOrder` over flow-kind edges only and the failure mode is an
// event wire drawn as if it were a stage in the night.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick/flow?id=f1", pretendToBeVisual: true },
);
const win = dom.window as any;

// Phone by default - every query answers false, which is what jsdom's absent
// matchMedia means and the layout the design is written for.
let wide = false;
win.matchMedia = (q: string) => ({
  matches: wide && /min-width:\s*768px/.test(q),
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
const UNMAPPED = [
  { key: "slew.tries", detail: "SLEW + CENTER: 3 tries is not carried into the plan.", level: "warn" },
  { key: "report.dest", detail: "SESSION REPORT: the destination is fixed by the engine.", level: "note" },
];
let runCalls = 0;

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/flows/f1/run")) {
    runCalls += 1;
    if (runCalls === 1) {
      // The server's own shape: FastAPI nests the payload under `detail`.
      return {
        ok: false, status: 409, statusText: "Conflict",
        json: async () => ({
          detail: {
            code: "unmapped",
            detail: "parts of this flow do not survive the compile",
            unmapped: UNMAPPED,
          },
        }),
      };
    }
    return ok({ started: true, flow_id: "f1", frames: 255, unmapped: UNMAPPED });
  }
  return ok({});
};
const runPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/f1/run"));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FlowCardSheet } = await import("../flow");
const { ConfirmCard } = await import("../../../../shell/ConfirmCard");
const { UNMAPPED_TITLE } = await import("../quickCopy");
const { FLOWS_NEEDS_WIDTH } = await import("../quickCopy");

// ------------------------------------------------------------------- harness
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
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];

// ------------------------------------------------------------------ fixtures
/** The lane `wizard.generate` builds for a quick flow, plus one EVENT wire so
 *  the RULES section has something real to draw. */
const GRAPH = {
  nodes: [
    { id: "n1", type: "dusk", x: 30, y: 60, params: {} },
    { id: "n2", type: "target", x: 258, y: 60, params: { name: "M31 - Andromeda" } },
    { id: "n3", type: "slew", x: 486, y: 60, params: {} },
    { id: "n4", type: "autofocus", x: 714, y: 60, params: {} },
    { id: "n5", type: "guide", x: 942, y: 60, params: {} },
    { id: "n6", type: "cycle", x: 1170, y: 60, params: { plan: "L 60, R 60, G 60, B 60", cycles: 51 } },
    { id: "n7", type: "report", x: 1398, y: 60, params: {} },
    { id: "n8", type: "cloudwatch", x: 30, y: 380, params: {} },
    { id: "n9", type: "holdresume", x: 280, y: 380, params: {} },
  ],
  edges: [
    { id: "we0", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
    { id: "we1", from: "n2", fromPort: "target", to: "n3", toPort: "run" },
    { id: "we2", from: "n3", fromPort: "centered", to: "n4", toPort: "run" },
    { id: "we3", from: "n4", fromPort: "focused", to: "n5", toPort: "run" },
    { id: "we4", from: "n5", fromPort: "guiding", to: "n6", toPort: "run" },
    { id: "we5", from: "n6", fromPort: "complete", to: "n7", toPort: "session" },
    { id: "we6", from: "n8", fromPort: "in", to: "n9", toPort: "pause" },
  ],
};

const RECORD = {
  id: "f1", name: "Quick session: M31", folder: "My flows", tagline: "",
  graph: GRAPH, created_ts: 0, updated_ts: 0, last_run: null,
  last_result: "" as const, readonly: false,
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};

function seed(over: Record<string, unknown> = {}): void {
  const flows = useStore.getState().flows;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    status: { connected: { camera: { connected: true, name: "sim" } } },
    framing: null,
    flows: {
      ...flows,
      record: RECORD,
      graph: GRAPH,
      compiled: { plan: {}, structural: [], issues: [], unmapped: UNMAPPED },
      compiling: false,
      run: { ...flows.run, phase: "idle" },
    },
    ...over,
  } as never);
}

function Screen(): any {
  return createElement(
    Fragment,
    null,
    createElement(FlowCardSheet, { params: { id: "f1" }, depth: 1 as const }),
    createElement(ConfirmCard),
  );
}

seed();
let root = createRoot(container);
await act(async () => { root.render(Screen()); });
await settle();

// ------------------------------------------------------------ 1. the marker
test("the card rendered its lane and its doctor chip", () => {
  assert(q('[data-testid="sky-flow"]') != null,
    "no sky-flow marker: the fixture is wrong, not the component");
  const chip = q('[data-testid="flow-doctor"]');
  assert(chip != null, "no validation chip");
  eq(chip.textContent, "GRAPH VALID", "no structural problems and no issues in the fixture");
  assert(q('[data-lane-card="SESSION REPORT"]') != null,
    "no SESSION REPORT card - the lane did not render and nothing below asserts anything");
});

test("the lane is the RUN ORDER, and an event wire is not a stage in it", () => {
  const labels = qa("[data-lane-card]").map((el) => el.getAttribute("data-lane-card"));
  eq(labels.join(" > "),
    "DUSK WINDOW > TARGET > SLEW + CENTER > AUTOFOCUS > GUIDE > FILTER CYCLE > SESSION REPORT",
    "the flow-lane order");
  assert(!labels.includes("CLOUD WATCH"),
    "an event source was drawn as a stage - the run cursor never travels an event wire");
  assert(!labels.includes("HOLD / RESUME"), "same, for the rule's target");
});

test("the event wire IS drawn, as a rule", () => {
  const rule = q('[data-rule="CLOUD WATCH"]');
  assert(rule != null, "the cloud-watch rule is missing from RULES · EVENT WIRES");
  assert(/HOLD \/ RESUME/.test(rule.textContent), `the rule does not name what it does: ${rule.textContent}`);
});

test("a `note` is not rendered as a warning", () => {
  const rows = qa('[data-testid="flow-unmapped"] p');
  eq(rows.length, 2, "both dropped settings are listed");
  const note = rows.find((p) => p.getAttribute("data-level") === "note");
  assert(note != null, "the note lost its level");
  // `note` says the thing IS honoured, by another part of the engine than the
  // wire names. Amber on it would report a working feature as a defect.
  assert(!/ffb454/.test(note.getAttribute("style") ?? ""), "a note must not be amber");
});

test("the footer's check count is the compiler's, not a fixture number", () => {
  assert(!/13 checks/.test(container.textContent),
    "the prototype's fixture count reached production");
  assert(/The doctor found nothing to fix\./.test(container.textContent),
    "the footer does not carry the compiler's own answer");
});

// ----------------------------------------------------- 2. the 409 question
await testAsync("RUN NOW into a 409 asks, and RUN ANYWAY re-runs with accept_unmapped", async () => {
  eq(runPosts().length, 0, "precondition: nothing has been run yet");
  await act(async () => {
    q('[data-testid="flow-run"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();

  eq(runPosts().length, 1, "one attempt so far");
  eq(runPosts()[0].body.accept_unmapped, false, "the first attempt does not pre-accept anything");

  const scrim = q('[data-testid="confirm-scrim"]');
  assert(scrim != null, "a 409 `unmapped` was treated as an error - no question was asked");
  eq(q(".nx-confirm-title").textContent, UNMAPPED_TITLE, "the question's title");
  for (const u of UNMAPPED) {
    assert(scrim.textContent.includes(u.detail), `the question does not list "${u.key}"`);
  }

  const yes = q('[data-testid="confirm-yes"]');
  eq(yes.textContent, "RUN ANYWAY", "the accepting verb");
  await act(async () => {
    yes.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();

  eq(runPosts().length, 2, "accepting fires a SECOND request");
  eq(runPosts()[1].body.accept_unmapped, true, "and that one carries the acceptance");
  eq(useStore.getState().flows.run.phase, "running", "the engine took it");
  assert(win.location.hash.startsWith("#/session/now"),
    `on a start the operator lands on the running night, got ${win.location.hash}`);
});

await testAsync("CANCEL on the question runs nothing", async () => {
  win.location.hash = "#/sky/quick/flow?id=f1";
  runCalls = 0;
  asks.length = 0;
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed();
  await act(async () => { root.render(Screen()); });
  await settle();

  await act(async () => {
    q('[data-testid="flow-run"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  assert(q('[data-testid="confirm-scrim"]') != null, "no question was asked");
  await act(async () => {
    q('[data-testid="confirm-keep"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  eq(runPosts().length, 1, "declining must not fire a second request");
});

// -------------------------------------------------------- 3. OPEN IN FLOWS
test("OPEN IN FLOWS is honest-disabled on a phone, and names the WIDTH", () => {
  const btn = q('[data-testid="flow-open-in-flows"]');
  assert(btn != null, "no OPEN IN FLOWS button");
  eq(btn.getAttribute("aria-disabled"), "true", "the canvas does not fit a phone");
  eq(btn.getAttribute("title"), FLOWS_NEEDS_WIDTH, "and the reason must not name a role");
});

await testAsync("OPEN IN FLOWS is live at tablet width and opens THIS flow", async () => {
  wide = true;
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed();
  await act(async () => { root.render(Screen()); });
  await settle();

  const btn = q('[data-testid="flow-open-in-flows"]');
  assert(btn.getAttribute("aria-disabled") == null, "at 768px the canvas fits");
  await act(async () => {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(win.location.hash, "#/session/flows?open=f1", "the editor opens on the flow that was just built");
  wide = false;
});

// ------------------------------------------------------------- 4. the viewer
await testAsync("a viewer sees every stage and cannot start it", async () => {
  win.location.hash = "#/sky/quick/flow?id=f1";
  asks.length = 0;
  runCalls = 0;
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  seed({ principal: { role: "viewer", email: null, caps: ["view.status"] } });
  await act(async () => { root.render(Screen()); });
  await settle();

  // Positive control first: the read-only screen is the WHOLE screen.
  assert(q('[data-lane-card="SESSION REPORT"]') != null, "a viewer must see the lane");
  const btn = q('[data-testid="flow-run"]');
  eq(btn.getAttribute("aria-disabled"), "true", "RUN NOW is refused");
  const why = btn.getAttribute("title") ?? "";
  assert(/Running a flow needs/.test(why), `the reason must say what is missing, got ${why}`);
  await act(async () => {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(runPosts().length, 0, "a viewer's press must not reach /api/flows/f1/run");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`flowCardDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
