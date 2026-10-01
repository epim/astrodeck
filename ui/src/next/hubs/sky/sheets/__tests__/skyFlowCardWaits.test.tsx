// skyFlowCardWaits.test.tsx - the Sky flow card waits for the flow it names
// and never draws another flow's stages; the quick sheet opens that card only
// on the flow it saved (#499, the Sky flow card half; spec 2026-09-23 flows
// mosaic, 2.1 and section 8's S6/S7).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowsOpen(id)` swallows its own failure and leaves the record
// that was open before in place, and since #450 it also REFUSES to replace a
// flow whose unsaved edits its save did not keep (openFlow.ts "THE WAY IN").
// The card's deep-link effect called `flowsOpen(id)` and then drew
// `record.name`, `laneCards(graph)`, `ruleRows(graph)` and the compile's drops
// from whatever record was open. So after a failed open the card titled for
// flow B showed flow A's name, stages and rules, with RUN locked for good; and
// `quick.tsx` navigated to that card after an unchecked `await flowsOpen(id)`.
//
// The real store and the real `createFlowsActions` open and save are mounted;
// only the network is a fake, and every request it saw is asserted on.
//
// On the unfixed code (2026-09-29) the four card cases then written were red
// at the lane assertion, `got ["DUSK WINDOW","TARGET","AUTOFOCUS","FILTER
// CYCLE"]` (flow A's lane under flow B's card), and both quick cases at
// `the sheet opened the flow card over a flow that did not open:
// "#/sky/quick/flow?id=flow-b"`; the two controls were green.
//
// MUTATION RECORD, 2026-09-29, each run in a private scratch copy of ui/ (the
// session scratchpad's H4-USKYFLOW-mut, never the shared tree, #254), from the
// pristine bytes and restored to them by sha256. Output verbatim.
//
//   MUTANT "card draws laneCards of the open record" (flow.tsx: the body's
//   `waiting !== null ?` gate made `false ?`, so the lane, rules, drops and
//   footer of the record OPEN are drawn while the card waits). 4/11; the
//   control and the three quick cases stay green:
//     x card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A: the card drew stages while flow B was not the flow open:
//       expected []
//       got      ["DUSK WINDOW","TARGET","AUTOFOCUS","FILTER CYCLE"]
//     (the same three lines for the #450 refusal, the 200 carrying flow A's
//     record, the slow earlier answer, the load and the link with no id), and
//     x card: once flow A is saved and closed, the card asks again, says it is loading, and draws flow B: premise: the refusal is not on the card
//
//   MUTANT "quick navigates after an unchecked open" (quick.tsx: the
//   `if (!(await openFlowById(id))) { toast; return; }` block made a bare
//   `await openFlowById(id);`). 9/11; the quick control stays green:
//     x quick: the new flow's read answers 404, and the sheet stays and says so: the sheet opened the flow card over a flow that did not open: "#/sky/quick/flow?id=flow-b"
//       expected "quick"
//       got      "flow"
//     x quick: flow A's edit does not save (#450), and the sheet stays and says why: the sheet opened the flow card over a flow that did not open: "#/sky/quick/flow?id=flow-b"
//       expected "quick"
//       got      "flow"
//
//   MUTANT "RUN unlocked while waiting" (flow.tsx: `?? waiting?.run ?? null`
//   made `?? null`). 6/11:
//     x card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A: a press on flow B's card posted a run:
//       expected []
//       got      ["POST /api/flows/flow-a/run"]
//     (the same three lines for the #450 refusal, the 200 carrying flow A's
//     record, the load and the link with no id)
//
//   MUTANT "card titles the open record" (flow.tsx: the title's
//   `waiting !== null` made `false`). 5/11:
//     x card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A: the card printed flow A's words under flow B's card:
//       expected []
//       got      ["M31 LRGB"]
//     (the same for the five other waiting cases)
//
//   MUTANT "chip vouches for the open record's compile" (flow.tsx: the chip
//   always `doctorChip(compiled, compiling)`). 5/11:
//     x card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A: the card vouched for flow A's compile under flow B's card: GRAPH VALID
//     (the same for the five other waiting cases)
//
//   MUTANT "a failed open is never said" (flow.tsx: `setFailure` behind
//   `false &&`, so the card says loading for good). 7/11:
//     x card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A: the card does not say the flow did not open: "OPENING THIS FLOWThis flow's stages show once it has loaded."
//     x card: flow A's edit does not save (#450), the open is refused, and the card draws nothing of flow A: the card does not say why flow B did not open: "OPENING THIS FLOWThis flow's stages show once it has loaded."
//     x card: flow B's read answers 200 with flow A's record, and the card says so and draws nothing of flow A: the card does not say the server answered with another flow: "OPENING THIS FLOWThis flow's stages show once it has loaded."
//     x card: once flow A is saved and closed, the card asks again, says it is loading, and draws flow B: premise: the refusal is not on the card
//
//   MUTANT "a late answer is not dropped" (flow.tsx: `current &&` removed
//   from the answer's test). 10/11:
//     x card: a slow answer to an earlier ask is not said over the ask now out: the card said a superseded ask's failure over the ask still out: "THAT FLOW DID NOT OPENno flow named flow-b"
//
//   MUTANT "a failure outlives the attempt it was for" (flow.tsx:
//   `&& failure.from === recordId` removed). 10/11:
//     x card: once flow A is saved and closed, the card asks again, says it is loading, and draws flow B: the card kept an earlier attempt's failure over the open now out: "THAT FLOW DID NOT OPENThe flow open in the editor has edits that did not save, and opening this one would drop them. Save or close that flow first."
//
//   MUTANT "no id waits as loading" (flow.tsx: the `id === ""` branch of
//   `waiting` made `false`). 10/11:
//     x card: a link that names no flow says so and draws nothing of flow A: the card does not say the link names no flow: "OPENING THIS FLOWThis flow's stages show once it has loaded."
//
//   MUTANT "no id opens anyway" (flow.tsx: the effect's `!id ||` removed).
//   10/11:
//     x card: a link that names no flow says so and draws nothing of flow A: a card with no id opened a flow:
//       expected []
//       got      ["GET /api/flows/","GET /api/flows/"]
//
//   MUTANT "card never shows the flow" (flow.tsx: `const mine = false;`), the
//   controls' own mutant. 7/11:
//     x control: flow B opens, and the card shows flow B and runs it once: a waiting card over a flow that has landed
//     x card: while flow B loads it says so and draws nothing of flow A, and draws flow B once it lands: flow B's lane, once it landed:
//       expected ["TARGET","SLEW + CENTER"]
//       got      []
//     (the same three lines for the close-then-retry and the slow earlier
//     answer cases)
//
//   MUTANT "quick never opens the card" (quick.tsx: `nav.sheet("flow", { id })`
//   made `void id;`), the quick control's own mutant. 10/11:
//     x quick control: the new flow opens, and the sheet opens its card: GENERATE FLOW did not open the flow card: "#/sky/quick?target=m33"
//       expected "flow"
//       got      "quick"
//
//   MUTANT "lane cards drawn without their summaries" (flow.tsx: the lane
//   card's `{c.sum}` made `{""}`), added by the verifier: the control's
//   TARGET check searched the whole page for "M33", which the title carries,
//   so it was 11/11 under this mutant. Read off the TARGET card, 10/11:
//     x control: flow B opens, and the card shows flow B and runs it once: flow B's TARGET is not summarised on its card: "TARGET"

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick/flow?id=flow-b", pretendToBeVisual: true },
);
const win = dom.window as any;

// The phone layout: every media query answers false.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));

// ------------------------------------------------------------- the fake rig
const A = "flow-a";
const B = "flow-b";

/** Flow A, the one left open: a lane of four stages and one event rule, each
 *  of which the card must never draw under flow B's card. */
function recordA(): any {
  return {
    id: A, name: "M31 LRGB", folder: "My flows", tagline: "", readonly: false,
    created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
    graph: {
      nodes: [
        { id: "a1", type: "dusk", x: 30, y: 60, params: {} },
        { id: "a2", type: "target", x: 258, y: 60, params: { name: "M31 - Andromeda" } },
        { id: "a3", type: "autofocus", x: 486, y: 60, params: {} },
        { id: "a4", type: "cycle", x: 714, y: 60, params: { plan: "L 60, R 60, G 60, B 60", cycles: 51 } },
        { id: "a8", type: "cloudwatch", x: 30, y: 380, params: {} },
        { id: "a9", type: "holdresume", x: 280, y: 380, params: {} },
      ],
      edges: [
        { id: "ae0", from: "a1", fromPort: "window", to: "a2", toPort: "arm" },
        { id: "ae1", from: "a2", fromPort: "target", to: "a3", toPort: "run" },
        { id: "ae2", from: "a3", fromPort: "focused", to: "a4", toPort: "run" },
        { id: "ae6", from: "a8", fromPort: "in", to: "a9", toPort: "pause" },
      ],
    },
  };
}

/** Flow B, the one pressed: a TARGET and a SLEW + CENTER, both of which the
 *  control must see drawn. The TARGET carries the node vocabulary's shipped
 *  rotation, so the quick sheet's save-back is a real PUT. */
function recordB(): any {
  return {
    id: B, name: "M33 Ha", folder: "My flows", tagline: "", readonly: false,
    created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
    graph: {
      nodes: [
        { id: "b1", type: "target", x: 30, y: 60,
          params: { name: "M33 - Triangulum", ra: "01h 33m 51s", dec: "+30° 39′ 37″", rotation: 23.4 } },
        { id: "b2", type: "slew", x: 258, y: 60, params: {} },
      ],
      edges: [{ id: "be1", from: "b1", fromPort: "target", to: "b2", toPort: "run" }],
    },
  };
}

/** The compile the store holds for flow A: one drop, whose sentence is flow
 *  A's too and must not be printed under flow B's card. */
const A_DROP = "SLEW + CENTER: 3 tries is not carried into the plan (flow A).";
const COMPILED_A = {
  plan: {}, structural: [], issues: [],
  unmapped: [{ key: "slew.tries", detail: A_DROP, level: "warn" }],
};

/** What flow A's words and stages look like on a page, every one of them. */
const A_WORDS = ["M31 LRGB", "M31 - Andromeda", "M31 - ANDROMEDA", A_DROP, "DUSK WINDOW", "CLOUD WATCH"];

/** The server's own words for a flow it does not have. */
const NO_SUCH_FLOW = "no flow named flow-b";

const M33 = {
  id: "m33", name: "Triangulum Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 1.564, dec_deg: 30.66, mag: 5.7, size_arcmin: 70, difficulty: "easy",
};

/** Every request, in order, as "METHOD url". */
let asked: string[] = [];
/** When set, a PUT of that flow is refused with a 500. */
let refusePut: string | null = null;
/** When true, a GET of flow B answers 404. */
let bMissing = false;
/** When set, a GET of flow B waits for this promise before it answers. */
let bHeld: Promise<void> | null = null;
/** When true, a GET of flow B answers 200 with flow A's record. */
let bAnswersA = false;
/** When true, each GET of flow B waits until the test answers it, in any
 *  order, through `pendingB` (with 200 or 404). */
let queueB = false;
const pendingB: ((status: number) => void)[] = [];

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u.includes("/api/catalog?q=")) return reply(200, { results: [M33], notes: [] });
  if (u.includes("/api/visibility")) return reply(200, {});
  if (u === "/api/flows/quick") return reply(200, { flow: recordB(), started: false });
  if (/\/api\/flows\/[^/]+\/run$/.test(u)) {
    return reply(200, { started: true, flow_id: B, frames: 10, unmapped: [] });
  }
  if (/\/api\/flows\/[^/]+\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  if (/\/api\/flows\/[^/]+\/tonight$/.test(u)) return reply(200, { ok: false, reason: "No observatory site is set." });
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  const one = /^\/api\/flows\/([^/?]+)$/.exec(u);
  if (one) {
    const id = one[1];
    if (method === "PUT") {
      if (refusePut === id) return reply(500, { detail: "the flow store is not writable" });
      const body = init?.body ? JSON.parse(init.body) : {};
      const base = id === A ? recordA() : recordB();
      return reply(200, { ...base, ...(body.flow ?? body), id });
    }
    if (id === B) {
      if (queueB) {
        const status = await new Promise<number>((r) => { pendingB.push(r); });
        return status === 404 ? reply(404, { detail: NO_SUCH_FLOW }) : reply(200, recordB());
      }
      if (bHeld) await bHeld;
      if (bMissing) return reply(404, { detail: NO_SUCH_FLOW });
      if (bAnswersA) return reply(200, recordA());
      return reply(200, recordB());
    }
    if (id === A) return reply(200, recordA());
  }
  if (u.includes("/api/flows")) return reply(200, []);
  return reply(200, {});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FLOWS_INIT, FLOW_OPEN_OVER_UNSAVED } = await import("../../../../../components/flows/flowsSlice");
const { FLOW_OPEN_FAILED, FLOW_OPEN_MISMATCH } = await import("../../../session/flows/openFlow");
const { FlowCardSheet, FLOW_CARD_LOADING, FLOW_CARD_NO_ID, FLOW_CARD_RUN_NOT_OPENED } = await import("../flow");
const { QuickSessionSheet, QUICK_SAVED_NOT_OPENED } = await import("../quick");
const { ConfirmCard } = await import("../../../../shell/ConfirmCard");
const { parseHash } = await import("../../../../router");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const settle = async (n = 8) => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const byId = (id: string) => q(`[data-testid="${id}"]`);
async function press(id: string): Promise<void> {
  const el = byId(id);
  assert(el != null, `no ${id} to press: the fixture is wrong, not the component`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}
const runs = () => asked.filter((a) => a.startsWith("POST ") && a.endsWith("/run"));
const toastsTitled = (title: string) =>
  ((useStore.getState() as any).toasts as any[]).filter((t) => t.title === title);
const laneLabels = () => qa("[data-lane-card]").map((el) => el.getAttribute("data-lane-card"));
const ruleLabels = () => qa("[data-rule]").map((el) => el.getAttribute("data-rule"));
const route = () => parseHash(String(win.location.hash));

/** The store with flow A open, EDITED (a moved node, `dirty`) when `edited`,
 *  as a hub switch leaves it, and with flow A's compile beside it. */
function seed(edited: boolean): void {
  const a = recordA();
  const graph = edited
    ? { ...a.graph, nodes: a.graph.nodes.map((n: any) => (n.id === "a2" ? { ...n, x: 300 } : n)) }
    : a.graph;
  useStore.setState({
    principal: OPERATOR,
    authGate: "open",
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    toasts: [],
    confirm: null,
    framing: null,
    weather: null,
    site: { name: "Back lawn", latitude: 10, longitude: 20, elevation_m: 50, is_default: false, horizon_min_deg: 20 },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      filterwheel: {
        position: 0, names: ["L", "R", "G", "B"],
        opaque: [false, false, false, false], narrowband: [false, false, false, false],
        exposures: [60, 60, 60, 60],
      },
      looping: false, busy_lanes: [],
    },
    frameSettings: {
      capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
      focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
      solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
      guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
    },
    sequence: { state: "idle" },
    flows: {
      ...FLOWS_INIT,
      ui: { ...FLOWS_INIT.ui },
      libraryLoaded: true, libraryError: null,
      record: a, graph, dirty: edited,
      compiled: COMPILED_A, compiling: false,
    },
  } as never);
}

/** The flow card for `id` (or for no id at all), over the store `seed` left. */
async function mountCard(edited: boolean, params: Record<string, string> = { id: B }): Promise<void> {
  await act(async () => { root.render(null); });
  asked = [];
  win.location.hash = params.id ? `#/sky/quick/flow?id=${params.id}` : "#/sky/quick/flow";
  seed(edited);
  await act(async () => {
    root.render(createElement(
      Fragment, null,
      createElement(FlowCardSheet, { params, depth: 1 as const }),
      createElement(ConfirmCard),
    ));
  });
  await settle();
  assert(byId("sky-flow") != null, "no sky-flow marker: the fixture is wrong, not the component");
}

/** Nothing of flow A is on the card: not its name, stages, rules or drops. */
function drawsNothingOfA(): void {
  eq(laneLabels(), [], "the card drew stages while flow B was not the flow open:");
  eq(ruleLabels(), [], "the card drew rules while flow B was not the flow open:");
  const text = String(container.textContent);
  const seen = A_WORDS.filter((w) => text.includes(w));
  eq(seen, [], "the card printed flow A's words under flow B's card:");
  assert(byId("flow-doctor") == null
    || byId("flow-doctor").getAttribute("data-state") === "unknown",
    `the card vouched for flow A's compile under flow B's card: ${byId("flow-doctor")?.textContent}`);
}

/** A press on RUN posts nothing, and RUN is locked. The press comes first:
 *  an unlocked RUN posts against the record OPEN, which is flow A's, and that
 *  post is the danger this card exists to refuse. */
async function runLockedAndInert(): Promise<void> {
  assert(byId("flow-run") != null, "no RUN button: the fixture is wrong, not the component");
  await press("flow-run");
  eq(runs(), [], "a press on flow B's card posted a run:");
  eq(byId("flow-run")?.getAttribute("aria-disabled"), "true",
    "RUN is not locked while flow B is not the flow open:");
}

// ================================================================ the card

await test("card: flow B's read answers 404, and the card says it did not open and draws nothing of flow A", async () => {
  bMissing = true;
  try {
    await mountCard(false);
    assert(asked.includes(`GET /api/flows/${B}`),
      `premise: the card never asked for flow B; requests: ${asked.join(", ")}`);
    eq((useStore.getState() as any).flows.record?.id, A, "premise: the failed read left flow A open");
    drawsNothingOfA();
    const waiting = byId("flow-card-waiting");
    assert(waiting != null, "no waiting card while flow B is not the flow open");
    const said = String(waiting.textContent);
    assert(said.includes(FLOW_OPEN_FAILED.toUpperCase()),
      `the card does not say the flow did not open: "${said}"`);
    assert(said.includes(NO_SUCH_FLOW), `the card does not carry the server's reason: "${said}"`);
    await runLockedAndInert();
    eq(byId("flow-run").getAttribute("title"), FLOW_CARD_RUN_NOT_OPENED, "RUN's reason:");
  } finally {
    bMissing = false;
  }
});

await test("card: flow A's edit does not save (#450), the open is refused, and the card draws nothing of flow A", async () => {
  refusePut = A;
  try {
    await mountCard(true);
    assert(asked.includes(`PUT /api/flows/${A}`),
      `premise: flow A's edit was never saved first; requests: ${asked.join(", ")}`);
    assert(!asked.includes(`GET /api/flows/${B}`),
      `premise: the refusal still read flow B; requests: ${asked.join(", ")}`);
    const f = (useStore.getState() as any).flows;
    eq([f.record?.id, f.dirty], [A, true], "premise: the refusal left flow A open with its edit");
    drawsNothingOfA();
    const waiting = byId("flow-card-waiting");
    assert(waiting != null, "no waiting card while flow B is not the flow open");
    assert(String(waiting.textContent).includes(FLOW_OPEN_OVER_UNSAVED),
      `the card does not say why flow B did not open: "${waiting.textContent}"`);
    await runLockedAndInert();
  } finally {
    refusePut = null;
  }
});

await test("card: flow B's read answers 200 with flow A's record, and the card says so and draws nothing of flow A", async () => {
  bAnswersA = true;
  try {
    await mountCard(false);
    assert(asked.includes(`GET /api/flows/${B}`),
      `premise: the card never asked for flow B; requests: ${asked.join(", ")}`);
    eq((useStore.getState() as any).flows.record?.id, A, "premise: the answer put flow A's record open");
    drawsNothingOfA();
    const waiting = byId("flow-card-waiting");
    assert(waiting != null, "no waiting card while flow B is not the flow open");
    assert(String(waiting.textContent).includes(FLOW_OPEN_MISMATCH),
      `the card does not say the server answered with another flow: "${waiting.textContent}"`);
    await runLockedAndInert();
  } finally {
    bAnswersA = false;
  }
});

await test("card: once flow A is saved and closed, the card asks again, says it is loading, and draws flow B", async () => {
  // The #450 refusal first, so a failure is on the card; then the operator
  // does what it asks ("Save or close that flow first"): flow A's PUT now
  // answers and the editor closes it. The record changing is a new attempt,
  // and the failure recorded under the old one must not be said over it.
  refusePut = A;
  let release: () => void = () => {};
  try {
    await mountCard(true);
    assert(String(byId("flow-card-waiting")?.textContent).includes(FLOW_OPEN_OVER_UNSAVED),
      "premise: the refusal is not on the card");
    refusePut = null;
    bHeld = new Promise<void>((r) => { release = r; });
    await act(async () => { await useStore.getState().flowsCloseEditor(); });
    await settle();
    eq((useStore.getState() as any).flows.record, null, "premise: flow A was not closed");
    eq(asked.filter((a) => a === `GET /api/flows/${B}`).length, 1,
      "premise: the card did not ask for flow B again once flow A had closed:");
    const waiting = byId("flow-card-waiting");
    assert(waiting != null, "no waiting card while flow B loads");
    const said = String(waiting.textContent);
    assert(said.includes(FLOW_CARD_LOADING) && !said.includes(FLOW_OPEN_OVER_UNSAVED),
      `the card kept an earlier attempt's failure over the open now out: "${said}"`);
    release();
    await settle();
    eq(laneLabels(), ["TARGET", "SLEW + CENTER"], "flow B's lane, once it landed:");
  } finally {
    refusePut = null;
    release();
    bHeld = null;
  }
});

await test("card: a slow answer to an earlier ask is not said over the ask now out", async () => {
  // Each change of the open record is a new ask (the effect's dependency), so
  // A open, then closed, then opened again puts three reads of flow B out.
  // The first answers while the other two are out: 404, with flow A open
  // again by then. That
  // answer belongs to an ask the card has moved on from, and the ask now out
  // was made with the same record open, so only dropping the late answer
  // keeps the card saying it is loading.
  queueB = true;
  pendingB.length = 0;
  try {
    await mountCard(false);
    eq(pendingB.length, 1, "premise: the card did not ask for flow B:");
    await act(async () => { await useStore.getState().flowsCloseEditor(); });
    await settle();
    await act(async () => { await useStore.getState().flowsOpen(A); });
    await settle();
    eq((useStore.getState() as any).flows.record?.id, A, "premise: flow A is open again");
    eq(pendingB.length, 3, "premise: the card did not ask again each time the record changed:");
    await act(async () => { pendingB[0](404); });
    await settle();
    drawsNothingOfA();
    const said = String(byId("flow-card-waiting")?.textContent);
    assert(said.includes(FLOW_CARD_LOADING) && !said.includes(FLOW_OPEN_FAILED.toUpperCase()),
      `the card said a superseded ask's failure over the ask still out: "${said}"`);
    await act(async () => { pendingB[1](200); pendingB[2](200); });
    await settle();
    eq(laneLabels(), ["TARGET", "SLEW + CENTER"], "flow B's lane, once it landed:");
  } finally {
    queueB = false;
    for (const r of pendingB.splice(0)) r(404);
    await settle();
  }
});

await test("card: while flow B loads it says so and draws nothing of flow A, and draws flow B once it lands", async () => {
  let release: () => void = () => {};
  bHeld = new Promise<void>((r) => { release = r; });
  try {
    await mountCard(false);
    assert(asked.includes(`GET /api/flows/${B}`),
      `premise: the card never asked for flow B; requests: ${asked.join(", ")}`);
    drawsNothingOfA();
    const waiting = byId("flow-card-waiting");
    assert(waiting != null, "no waiting card while flow B loads");
    assert(String(waiting.textContent).includes(FLOW_CARD_LOADING),
      `the card does not say flow B is loading: "${waiting.textContent}"`);
    assert(!String(waiting.textContent).includes(FLOW_OPEN_FAILED.toUpperCase()),
      `the card called an open still in flight a failure: "${waiting.textContent}"`);
    await runLockedAndInert();
    release();
    await settle();
    eq((useStore.getState() as any).flows.record?.id, B, "premise: flow B landed");
    eq(laneLabels(), ["TARGET", "SLEW + CENTER"], "flow B's lane, once it landed:");
    assert(byId("flow-card-waiting") == null, "the waiting card outlived flow B's arrival");
  } finally {
    // Released here too, so a case that fails early leaves no read out to
    // land in the next one.
    release();
    await settle();
    bHeld = null;
  }
});

await test("card: a link that names no flow says so and draws nothing of flow A", async () => {
  await mountCard(false, {});
  eq(asked.filter((a) => a.startsWith("GET /api/flows/")), [], "a card with no id opened a flow:");
  drawsNothingOfA();
  const waiting = byId("flow-card-waiting");
  assert(waiting != null, "no waiting card for a link that names no flow");
  assert(String(waiting.textContent).includes(FLOW_CARD_NO_ID),
    `the card does not say the link names no flow: "${waiting.textContent}"`);
  await runLockedAndInert();
});

await test("control: flow B opens, and the card shows flow B and runs it once", async () => {
  await mountCard(false);
  eq((useStore.getState() as any).flows.record?.id, B, "premise: flow B landed");
  assert(byId("flow-card-waiting") == null, "a waiting card over a flow that has landed");
  eq(q(".nx-sheet-title")?.textContent, "M33 HA", "the card's title is flow B's name:");
  eq(laneLabels(), ["TARGET", "SLEW + CENTER"], "flow B's lane:");
  // Read off the TARGET card itself: the title above already says "M33 HA",
  // so a page-wide search for "M33" passed with every lane summary blanked
  // (verifier, 2026-09-29: flow.tsx `{c.sum}` made `{""}` left this 11/11).
  const targetCard = q('[data-lane-card="TARGET"]');
  assert(String(targetCard?.textContent).includes("M33 - Triangulum"),
    `flow B's TARGET is not summarised on its card: "${targetCard?.textContent}"`);
  const run = byId("flow-run");
  eq(run.getAttribute("aria-disabled"), null, "RUN is locked over the flow the card names");
  await press("flow-run");
  eq(runs(), [`POST /api/flows/${B}/run`], "the press did not start flow B, once:");
});

// ============================================================ the quick sheet

async function mountQuick(edited: boolean): Promise<void> {
  await act(async () => { root.render(null); });
  asked = [];
  win.location.hash = "#/sky/quick?target=m33";
  seed(edited);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m33" }, depth: 0 as const }));
  });
  await settle();
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await settle();
  const cta = byId("quick-generate");
  assert(cta != null, "no quick-generate: the fixture is wrong, not the component");
  assert(cta.getAttribute("aria-disabled") == null,
    `GENERATE FLOW is locked (${cta.getAttribute("title")}): the fixture is wrong, not the component`);
}
const topSheet = () => { const s = route().sheets; return s[s.length - 1]; };

await test("quick: the new flow's read answers 404, and the sheet stays and says so", async () => {
  bMissing = true;
  try {
    await mountQuick(false);
    await press("quick-generate");
    assert(asked.includes("POST /api/flows/quick"),
      `premise: GENERATE FLOW posted nothing; requests: ${asked.join(", ")}`);
    assert(asked.includes(`GET /api/flows/${B}`),
      `premise: the new flow was never opened; requests: ${asked.join(", ")}`);
    eq(topSheet(), "quick", `the sheet opened the flow card over a flow that did not open: "${win.location.hash}"`);
    const said = toastsTitled(FLOW_OPEN_FAILED);
    assert(said.some((t) => String(t.detail).includes(NO_SUCH_FLOW)
      && String(t.detail).includes(QUICK_SAVED_NOT_OPENED)),
      `the sheet did not say the new flow did not open, and that it is saved: ${JSON.stringify(said)}`);
    eq(runs(), [], "GENERATE FLOW posted a run:");
  } finally {
    bMissing = false;
  }
});

await test("quick: flow A's edit does not save (#450), and the sheet stays and says why", async () => {
  refusePut = A;
  try {
    await mountQuick(true);
    await press("quick-generate");
    assert(asked.includes(`PUT /api/flows/${A}`),
      `premise: flow A's edit was never saved first; requests: ${asked.join(", ")}`);
    eq((useStore.getState() as any).flows.record?.id, A, "premise: the refusal left flow A open");
    eq(topSheet(), "quick", `the sheet opened the flow card over a flow that did not open: "${win.location.hash}"`);
    // The store says the refusal itself (flowsSlice FLOW_NOT_OPENED, the same
    // title), and the sheet's toast of that title coalesces onto it (the
    // store's x2 rule for a toast still on screen): the reason is read once.
    const said = toastsTitled(FLOW_OPEN_FAILED);
    assert(said.some((t) => String(t.detail).includes(FLOW_OPEN_OVER_UNSAVED)),
      `nothing said why the new flow did not open: ${JSON.stringify(said)}`);
    eq(runs(), [], "GENERATE FLOW posted a run:");
  } finally {
    refusePut = null;
  }
});

await test("quick control: the new flow opens, and the sheet opens its card", async () => {
  await mountQuick(false);
  await press("quick-generate");
  eq((useStore.getState() as any).flows.record?.id, B, "premise: the new flow landed");
  eq(topSheet(), "flow", `GENERATE FLOW did not open the flow card: "${win.location.hash}"`);
  eq(route().params.id, B, "the card opened on another flow:");
  eq(toastsTitled(FLOW_OPEN_FAILED), [], "a toast said the flow did not open:");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`skyFlowCardWaits.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
