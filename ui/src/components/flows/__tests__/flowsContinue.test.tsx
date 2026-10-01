// flowsContinue.test.tsx - CONTINUE's three questions and START OVER, on both
// run paths (#189 S1-17, spec 5.9).
//
//   Run directly (from ui/):
//     node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowsContinue.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Since S1, `POST /api/flows/{id}/run` CONTINUES the flow's own dormant
// session, and before it changes what that session's ledger counts it asks,
// with a 409 (server `_continue_flow_session`):
//
//   adopt          the session was saved before flows kept their step ids
//   recount        the compile counts subs differently from the session
//   dropped_steps  steps that hold frames are gone from the flow
//
// Each is lifted only by its own flag on the NEXT request, and they come one
// at a time, so an operator who says ADOPT is then asked about the dropped
// steps. THE DEFECT THIS FILE EXISTS FOR is the re-post that forgets the
// first answer: the server then asks ADOPT again, forever, or - worse - a
// re-post without `accept_unmapped` is refused on the graph instead. So the
// assertions are on the BODY of every request, not on a dialog appearing.
//
// And a question is not a failure. Today's `flowsRun` turned every 409 it did
// not recognise into "could not start: ..." on the log; for these three that
// reads as the rig refusing a flow it is only asking about.
//
// Three parts: the slice on its own (the request bodies and the answer shape),
// the shared RUN hook mounted under both confirm hosts (classic `ConfirmHost`
// and the new UI's `ConfirmCard`), and the #/next Sky flow sheet.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// `lib/base.ts` reads `window.location` at module scope and the store opens a
// WebSocket, so every global has to exist before the first dynamic import.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick/flow?id=f1", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ the fake server
// The run route answers from a SCRIPT, one entry per request, so a test says
// exactly which questions the server asks and in what order. Anything else
// answers 200 {} - the sheet fetches a few things on mount that no assertion
// here is about.
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
type Reply = () => any;
let script: Reply[] = [];

const okReply = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});
/** The server's own shape: FastAPI nests the payload under `detail`. */
const conflict = (detail: Record<string, unknown>): Reply => () => ({
  ok: false, status: 409, statusText: "Conflict", json: async () => ({ detail }),
});
/** `unmapped` echoes the compile's list, as `run_flow`'s success answer does. */
const started = (session?: Record<string, unknown>, unmapped: unknown[] = []): Reply => () => okReply({
  started: true, flow_id: "f1", frames: 120, unmapped,
  ...(session ? { session } : {}),
});
const serverError: Reply = () => ({
  ok: false, status: 500, statusText: "Server Error",
  json: async () => ({ detail: "boom" }),
});

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  if (method === "POST" && u.includes("/api/flows/f1/run")) {
    const next = script.shift();
    if (!next) {
      return {
        ok: false, status: 500, statusText: "Server Error",
        json: async () => ({ detail: "the test's script ran out of answers" }),
      };
    }
    return next();
  }
  return okReply({});
};
const runPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/f1/run"));
function serve(...replies: Reply[]): void {
  asks.length = 0;
  script = replies;
}

/** The six booleans the run body carries, in the server's names. */
const FLAG_KEYS = ["accept_unmapped", "force", "fresh", "adopt", "accept_dropped", "accept_recount"];
/** The flags a body sends TRUE, as one comparable string. */
const onIn = (body: any): string => FLAG_KEYS.filter((k) => body?.[k] === true).join(",");

// ------------------------------------------------------------ the 409s, verbatim
// Each sentence is what server/astrodeck/flows/continuation.py builds for these
// numbers (adopt_detail(52, 40), recount_detail("attempts", "accepted", 412,
// 371), dropped_detail(212)). The UI must print them as they come, so the
// fixtures are the server's words and not a paraphrase.
const ADOPT_SENTENCE =
  "this flow's session holds 52 subs under step ids no compile produces any "
  + "more (it was saved before flows kept their ids). ADOPT re-keys the 40 that "
  + "match exactly one step of this flow and leaves 12 as they are; START OVER "
  + "begins a new session and leaves this one on disk";
const RECOUNT_SENTENCE =
  "this session counted every sub taken (412); counting accepted subs makes it 371";
const DROPPED_SENTENCE =
  "212 subs belong to steps this flow no longer has; they stay on disk";
const CHANGED_SENTENCE =
  "this flow's session changed while the run was being prepared: it is now "
  + "active, so it was not continued and nothing was written. Press Run again.";

const ADOPT_409 = {
  code: "adopt", detail: ADOPT_SENTENCE,
  adopt: {
    session_id: "s-old", frames: 52, matched: 40,
    unmatched: [{ step_id: "u1", target: "M31", frame_type: "light", filter: "Ha",
                  exposure_s: 300, gain: 100, binning: 1, frames: 12,
                  reason: "no step in this flow matches it" }],
  },
};
const RECOUNT_409 = {
  code: "recount", detail: RECOUNT_SENTENCE, before: 412, after: 371, session_id: "s-old",
};
const DROPPED_409 = {
  code: "dropped_steps", detail: DROPPED_SENTENCE, dropped_frames: 212, session_id: "s-old",
};
const UNMAPPED = [
  { key: "slew.tries", detail: "SLEW + CENTER: 3 tries is not carried into the plan.", level: "warn" },
];
const UNMAPPED_409 = {
  code: "unmapped", detail: "parts of this flow do not survive the compile", unmapped: UNMAPPED,
};
const CHANGED_409 = {
  code: "session_changed", detail: CHANGED_SENTENCE, session_id: "s-old", status: "active",
};

const CONTINUED_3 = { id: "s-old", night: 3, continued: true, kept: 7, new: 0, dropped: 0 };
const FRESH_4 = { id: "s-new", night: 1, continued: false, kept: 0, new: 4, dropped: 0 };

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------ imports, AFTER the globals are set
const { createFlowsActions, FLOWS_INIT, nextRunFlags, sessionLogLine } =
  await import("../flowsSlice");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowRunAnswer = import("../flowsSlice").FlowRunAnswer;
type FlowRunAcceptance = import("../flowsSlice").FlowRunAcceptance;

const RECORD = {
  id: "f1", name: "M31 mosaic", folder: "My flows", tagline: "",
  graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
  last_run: null, last_result: "" as const, readonly: false,
};

/** The slice alone, behind a miniature store: the same set/get contract
 *  zustand hands it (flowsSlice.test.ts uses the same one). */
function slice() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT, record: RECORD as never } } as FlowsHost;
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}

/** Press RUN, then say yes to each question in turn, the way both callers do:
 *  every re-post is `nextRunFlags(<the flags the question came back with>, yes)`.
 *  Returns the last answer. */
async function answerInTurn(
  s: ReturnType<typeof slice>, yeses: FlowRunAcceptance[],
): Promise<FlowRunAnswer | null> {
  let ans = await s.a.flowsRun();
  for (const yes of yeses) {
    assert(ans !== null, `the server asked fewer questions than the test answers (stopped before "${yes}")`);
    ans = await s.a.flowsRun(nextRunFlags(ans.flags, yes));
  }
  return ans;
}

const couldNotStart = (s: ReturnType<typeof slice>) =>
  s.flows.logs.filter((l) => /could not start/.test(l.msg));

// ========================================================= 1. THE SLICE
/** MUTANT "flowsApi.run drops the four flags" (flowsApi.ts: the body sends only
 *  accept_unmapped and force) - observed:
 *    x the first post sends all six flags, every one of them false: the body
 *      does not carry "fresh" as a boolean (expected boolean, got undefined)
 */
await testAsync("the first post sends all six flags, every one of them false", async () => {
  serve(started(CONTINUED_3));
  const s = slice();
  await s.a.flowsRun();
  eq(runPosts().length, 1, "one post");
  const body = runPosts()[0].body;
  for (const k of FLAG_KEYS) {
    eq(typeof body[k], "boolean", `the body does not carry "${k}" as a boolean`);
  }
  eq(onIn(body), "", "a first press pre-accepts nothing and never starts over");
});

/** MUTANT "adopt falls through to could not start" (flowsSlice.ts: "adopt"
 *  taken out of CONTINUE_CODES, so the 409 reaches the log line) - observed:
 *    x a 409 adopt is a QUESTION: its code, the server's sentence verbatim,
 *      the session id: expected a continue question, got null
 *  MUTANT "adopt session id not read" (flowsSlice.ts: only the top-level
 *  session_id is read) - observed:
 *    x a 409 adopt is a QUESTION: its code, the server's sentence verbatim,
 *      the session id: adopt carries its session id under `adopt` (expected
 *      s-old, got null)
 */
await testAsync("a 409 adopt is a QUESTION: its code, the server's sentence verbatim, the session id", async () => {
  serve(conflict(ADOPT_409));
  const s = slice();
  const ans = await s.a.flowsRun();
  assert(ans !== null && ans.kind === "continue",
    `expected a continue question, got ${JSON.stringify(ans)}`);
  eq(ans.question.code, "adopt", "the code");
  eq(ans.question.detail, ADOPT_SENTENCE, "the server's sentence, verbatim");
  eq(ans.question.sessionId, "s-old", "adopt carries its session id under `adopt`");
  eq(couldNotStart(s).length, 0,
    "a question was written to the log as a refusal - 'could not start' over a flow the server is only asking about");
  eq(s.flows.run.phase, "idle", "a question is not a start");
});

await testAsync("recount and dropped_steps are questions too, with their top-level session id", async () => {
  for (const [body, code, sentence] of [
    [RECOUNT_409, "recount", RECOUNT_SENTENCE],
    [DROPPED_409, "dropped_steps", DROPPED_SENTENCE],
  ] as const) {
    serve(conflict(body));
    const s = slice();
    const ans = await s.a.flowsRun();
    assert(ans !== null && ans.kind === "continue", `${code}: expected a question, got ${JSON.stringify(ans)}`);
    eq(ans.question.code, code, `${code}: the code`);
    eq(ans.question.detail, sentence, `${code}: the sentence, verbatim`);
    eq(ans.question.sessionId, "s-old", `${code}: the session id`);
    eq(couldNotStart(s).length, 0, `${code}: logged as a refusal`);
  }
});

/** MUTANT "flowsApi.run drops the four flags" - observed:
 *    x each answer re-posts with its own flag, and only that flag: adopt: the
 *      re-post body (expected adopt, got )
 */
await testAsync("each answer re-posts with its own flag, and only that flag", async () => {
  const cases: [Record<string, unknown>, FlowRunAcceptance, string][] = [
    [ADOPT_409, "adopt", "adopt"],
    [DROPPED_409, "dropped_steps", "accept_dropped"],
    [RECOUNT_409, "recount", "accept_recount"],
    [ADOPT_409, "fresh", "fresh"],
    [DROPPED_409, "fresh", "fresh"],
  ];
  for (const [first, yes, flag] of cases) {
    serve(conflict(first), started(CONTINUED_3));
    const s = slice();
    await answerInTurn(s, [yes]);
    eq(runPosts().length, 2, `${yes}: one question, one re-post`);
    eq(onIn(runPosts()[1].body), flag, `${yes}: the re-post body`);
  }
});

/** MUTANT "drop earlier flags on re-post" (flowsSlice.ts nextRunFlags returns
 *  `{ [flag]: true }` instead of `{ ...asked, [flag]: true }`) - observed:
 *    x ADOPT then CONTINUE on dropped steps sends BOTH flags on the last post:
 *      the third post carries every flag already accepted (expected
 *      adopt,accept_dropped, got accept_dropped)
 */
await testAsync("ADOPT then CONTINUE on dropped steps sends BOTH flags on the last post", async () => {
  serve(conflict(ADOPT_409), conflict(DROPPED_409), started(CONTINUED_3));
  const s = slice();
  const last = await answerInTurn(s, ["adopt", "dropped_steps"]);
  eq(last, null, "the third post started the run");
  eq(runPosts().length, 3, "three posts");
  eq(onIn(runPosts()[1].body), "adopt", "the second post");
  eq(onIn(runPosts()[2].body), "adopt,accept_dropped",
    "the third post carries every flag already accepted");
  eq(s.flows.run.phase, "running", "and the engine took it");
});

/** MUTANT "drop earlier flags on re-post" - observed here too:
 *    x every question in one night: the unmapped acceptance rides along to the
 *      end: post 3 (expected accept_unmapped,adopt, got adopt)
 */
await testAsync("every question in one night: the unmapped acceptance rides along to the end", async () => {
  // The server asks the graph question FIRST (run_flow checks losses before it
  // looks for a session). A continue re-post without `accept_unmapped` would
  // be refused on the graph again - the operator would answer RUN ANYWAY twice.
  serve(conflict(UNMAPPED_409), conflict(ADOPT_409), conflict(RECOUNT_409),
        conflict(DROPPED_409), started(CONTINUED_3, UNMAPPED));
  const s = slice();
  await answerInTurn(s, ["unmapped", "adopt", "recount", "dropped_steps"]);
  const bodies = runPosts().map((p) => onIn(p.body));
  eq(bodies.length, 5, "five posts");
  eq(bodies[1], "accept_unmapped", "post 2");
  eq(bodies[2], "accept_unmapped,adopt", "post 3");
  eq(bodies[3], "accept_unmapped,adopt,accept_recount", "post 4");
  eq(bodies[4], "accept_unmapped,adopt,accept_dropped,accept_recount", "post 5");
  eq(s.flows.run.acceptedUnmapped.length, UNMAPPED.length,
    "the run banner still knows which losses were accepted");
});

/** MUTANT "drop earlier flags on re-post" - observed:
 *    x START OVER after RUN ANYWAY keeps the acceptance and adds fresh: a fresh
 *      start without the graph acceptance is refused on the graph again
 *      (expected accept_unmapped,fresh, got fresh)
 */
await testAsync("START OVER after RUN ANYWAY keeps the acceptance and adds fresh", async () => {
  serve(conflict(UNMAPPED_409), conflict(DROPPED_409), started(FRESH_4));
  const s = slice();
  await answerInTurn(s, ["unmapped", "fresh"]);
  eq(onIn(runPosts()[2].body), "accept_unmapped,fresh",
    "a fresh start without the graph acceptance is refused on the graph again");
});

await testAsync("CONTROL: a 409 unmapped is still today's answer - the list, not a continue question", async () => {
  serve(conflict(UNMAPPED_409));
  const s = slice();
  const ans = await s.a.flowsRun();
  assert(ans !== null && ans.kind === "unmapped", `expected the unmapped list, got ${JSON.stringify(ans)}`);
  eq(ans.unmapped.length, 1, "the server's list");
  eq(ans.unmapped[0].detail, UNMAPPED[0].detail, "verbatim");
  eq(couldNotStart(s).length, 0, "not logged as a refusal");
});

/** MUTANT "every run banner claims its losses were accepted" (flowsSlice.ts:
 *  `acceptedUnmapped: true ? ...` in place of `flags.acceptUnmapped ? ...`) -
 *  observed:
 *    x CONTROL: an ADOPT is not a RUN ANYWAY - the run banner claims no
 *      accepted losses: the banner lists losses nobody accepted (expected 0,
 *      got 1)
 */
await testAsync("CONTROL: an ADOPT is not a RUN ANYWAY - the run banner claims no accepted losses", async () => {
  // `run_flow`'s success answer echoes the compile's WHOLE list, note-level
  // entries included, whether or not anything was accepted. Only a request
  // that said RUN ANYWAY may put that list on the banner as accepted; a
  // re-post that answered a CONTINUE question said nothing about the graph.
  const NOTE = [{ key: "cool.note", detail: "COOL: the rig's standing setpoint is used.", level: "note" }];
  serve(conflict(ADOPT_409), started(CONTINUED_3, NOTE));
  const s = slice();
  await answerInTurn(s, ["adopt"]);
  eq(s.flows.run.phase, "running", "the adopt re-post started the run");
  eq(s.flows.run.acceptedUnmapped.length, 0, "the banner lists losses nobody accepted");
});

await testAsync("CONTROL: session_changed and a 500 are refusals, logged, not questions", async () => {
  // session_changed tells the operator to press Run again; it has no flag to
  // answer with, so asking would offer a CONTINUE that changes nothing.
  serve(conflict(CHANGED_409));
  let s = slice();
  eq(await s.a.flowsRun(), null, "session_changed is not a question");
  eq(couldNotStart(s).length, 1, "it is said on the log");
  eq(couldNotStart(s)[0].msg, `could not start: ${CHANGED_SENTENCE}`, "in the server's words");
  serve(serverError);
  s = slice();
  eq(await s.a.flowsRun(), null, "a 500 is not a question");
  eq(couldNotStart(s).length, 1, "and it is said on the log");
});

/** MUTANT "session block not logged" (flowsSlice.ts: the success path skips
 *  the session line) - observed:
 *    x a continue says so on the flow log, in words: the session block on the
 *      log (expected continued night 3: 7 steps kept, 0 new, got undefined)
 */
await testAsync("a continue says so on the flow log, in words", async () => {
  serve(started(CONTINUED_3));
  const s = slice();
  await s.a.flowsRun();
  const lines = s.flows.logs.map((l) => l.msg);
  eq(lines.find((m) => m.startsWith("continued")), "continued night 3: 7 steps kept, 0 new",
    "the session block on the log");
  eq(s.flows.logs.find((l) => l.msg.startsWith("continued"))?.tone, "info", "said plainly, not as a warning");
});

await testAsync("the session line, word for word, for each shape the server sends", async () => {
  eq(sessionLogLine(CONTINUED_3), "continued night 3: 7 steps kept, 0 new", "the example");
  eq(sessionLogLine({ ...CONTINUED_3, kept: 1, new: 2 }), "continued night 3: 1 step kept, 2 new",
    "one step is a step");
  eq(sessionLogLine({ ...CONTINUED_3, kept: 5, dropped: 2 }),
    "continued night 3: 5 steps kept, 0 new, 2 dropped (their subs stay on disk)",
    "dropped steps are named, with where their frames went");
  eq(sessionLogLine(FRESH_4), "started a new session: 4 steps", "a fresh start");
  eq(sessionLogLine({ ...FRESH_4, new: 1 }), "started a new session: 1 step", "one step");
  eq(sessionLogLine({
    ...CONTINUED_3, kept: 3, new: 1,
    adopted: { matched: 40, unmatched: [{ frames: 12 }] },
  }), "continued night 3: 3 steps kept, 1 new; adopted 40 subs from the earlier session, "
      + "12 left as they were", "an adoption says how many subs it re-keyed and how many it left");
  eq(sessionLogLine({ ...CONTINUED_3, adopted: { matched: 1, unmatched: [] } }),
    "continued night 3: 7 steps kept, 0 new; adopted 1 sub from the earlier session",
    "nothing left over, nothing said about it");
});

await testAsync("CONTROL: an older server sends no session block, and nothing is said", async () => {
  serve(started());
  const s = slice();
  await s.a.flowsRun();
  eq(s.flows.logs.length, 0, "a line was invented out of a missing block");
  for (const junk of [null, undefined, "x", 3, {}, { continued: true }]) {
    eq(sessionLogLine(junk), null, `${JSON.stringify(junk)} produced a line`);
  }
});

// ===================================================== 2. MOUNTED CALLERS
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { useFlowRunControls, START_OVER_LABEL } = await import("../flowRunControls");
const { ConfirmHost } = await import("../../ConfirmDialog");
const { ConfirmCard } = await import("../../../next/shell/ConfirmCard");
const { FlowCardSheet } = await import("../../../next/hubs/sky/sheets/flow");

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};

function seed(): void {
  const flows = useStore.getState().flows;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    framing: null,
    flows: {
      ...flows,
      record: RECORD,
      graph: RECORD.graph,
      compiled: { plan: {}, structural: [], issues: [], unmapped: [] },
      compiling: false,
      logs: [],
      run: { ...flows.run, phase: "idle", acceptedUnmapped: [] },
    },
  } as never);
}

/** The shared RUN hook, with nothing around it but a button: every surface
 *  that offers RUN (the classic header and phone MONITOR, the new canvas
 *  toolbar, Session - Now) calls exactly this `act`. */
function Probe(): any {
  const c = useFlowRunControls();
  return createElement("button", { "data-testid": "probe-run", onClick: c.act }, "RUN");
}

type Where = "hook-classic" | "hook-card" | "sheet";
const container = win.document.getElementById("root") as any;
let root: any = null;
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function mount(where: Where): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  win.location.hash = "#/sky/quick/flow?id=f1";
  seed();
  root = createRoot(container);
  const tree = where === "sheet"
    ? [createElement(FlowCardSheet, { key: "a", params: { id: "f1" }, depth: 1 as const }),
       createElement(ConfirmCard, { key: "b" })]
    : [createElement(Probe, { key: "a" }),
       createElement(where === "hook-classic" ? ConfirmHost : ConfirmCard, { key: "b" })];
  await act(async () => { root.render(createElement(Fragment, null, ...tree)); });
  await settle();
}
async function press(el: any, what: string): Promise<void> {
  assert(el != null, `no ${what} to press`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}
// ConfirmHost portals to <body>, so every read is off the whole document.
const body = () => win.document.body;
const byText = (label: string) =>
  Array.from(body().querySelectorAll("button")).find(
    (b: any) => (b.textContent ?? "").trim() === label) as any;
const startOverBtn = () => body().querySelector('[data-testid="confirm-start-over"]') as any;
const asking = () => useStore.getState().confirm !== null;
const occurrences = (text: string) => body().textContent.split(text).length - 1;
async function pressRun(where: Where): Promise<void> {
  await press(body().querySelector(where === "sheet" ? '[data-testid="flow-run"]'
                                                     : '[data-testid="probe-run"]'), "RUN");
}

/** MUTANTS observed on this test. Each line below is the [hook-classic] case,
 *  verbatim after the shared prefix "x [hook-classic] adopt: the server's
 *  sentence once, ADOPT or START OVER, and START OVER posts fresh:"; the
 *  [hook-card] case failed identically, and [sheet] did too except where
 *  noted.
 *   "hook does not ask the continue question" (flowRunControls.tsx: the hook
 *   hands the loop `async () => null` for the continue question; [sheet]
 *   stayed green, it asks for itself):
 *     no question was asked on a 409 adopt
 *   "sheet does not ask the continue question" (flow.tsx, same change; only
 *   [sheet] went red):
 *     x [sheet] adopt: ...: no question was asked on a 409 adopt
 *   "adopt falls through to could not start" (flowsSlice.ts):
 *     no question was asked on a 409 adopt
 *   "START OVER not recorded" (askContinue's button never sets the choice):
 *     START OVER re-posts (expected 2, got 1)
 *   "sentence in title and body" (askContinue's title is the server sentence):
 *     the server's sentence is shown exactly once (expected 1, got 2)
 *   "START OVER note on the adopt question too" (the note loses its
 *   `q.code !== "adopt"` guard):
 *     the adopt sentence already says what START OVER does; the UI said it
 *     again (expected 1, got 2)
 *   "START OVER maps to the question's own flag" (askContinue answers
 *   q.code for START OVER):
 *     START OVER posts fresh, and nothing else (expected fresh, got adopt)
 *   "flowsApi.run drops the four flags":
 *     START OVER posts fresh, and nothing else (expected fresh, got )
 *   "session block not logged" (flowsSlice.ts):
 *     the fresh session was not said on the flow log: []
 */
for (const where of ["hook-classic", "hook-card", "sheet"] as const) {
  await testAsync(`[${where}] adopt: the server's sentence once, ADOPT or START OVER, and START OVER posts fresh`, async () => {
    serve(conflict(ADOPT_409), started(FRESH_4));
    await mount(where);
    await pressRun(where);
    eq(runPosts().length, 1, "one post so far");
    assert(asking(), "no question was asked on a 409 adopt");
    eq(occurrences(ADOPT_SENTENCE), 1, "the server's sentence is shown exactly once");
    eq(occurrences("START OVER begins a new session"), 1,
      "the adopt sentence already says what START OVER does; the UI said it again");
    assert(byText("ADOPT") != null, "no ADOPT verb");
    assert(byText("CANCEL") != null, "no CANCEL");
    eq(startOverBtn()?.textContent, START_OVER_LABEL, "the START OVER choice");
    assert(byText("CONTINUE") == null, "adopt is not a CONTINUE question");

    await press(startOverBtn(), "START OVER");
    eq(runPosts().length, 2, "START OVER re-posts");
    eq(onIn(runPosts()[1].body), "fresh", "START OVER posts fresh, and nothing else");
    assert(!asking(), "the question is still up after it was answered");
    eq(useStore.getState().flows.run.phase, "running", "the engine took it");
    const lines = useStore.getState().flows.logs.map((l) => l.msg);
    assert(lines.includes("started a new session: 4 steps"),
      `the fresh session was not said on the flow log: ${JSON.stringify(lines)}`);
    if (where === "sheet") {
      assert(win.location.hash.startsWith("#/session/now"),
        `a started run lands on the running night, got ${win.location.hash}`);
    }
  });
}

/** MUTANT "START OVER note dropped" (flowRunControls.tsx: the note line is
 *  removed) - observed:
 *    x [hook-classic] dropped_steps: CONTINUE posts accept_dropped, and says
 *      what START OVER leaves: a dropped-steps question offers START OVER
 *      without saying what it leaves behind
 */
await testAsync("[hook-classic] dropped_steps: CONTINUE posts accept_dropped, and says what START OVER leaves", async () => {
  serve(conflict(DROPPED_409), started(CONTINUED_3));
  await mount("hook-classic");
  await pressRun("hook-classic");
  assert(asking(), "no question on a 409 dropped_steps");
  eq(occurrences(DROPPED_SENTENCE), 1, "the server's sentence, once");
  assert(startOverBtn() != null, "no START OVER on a dropped-steps question");
  // The server's dropped sentence does not say what START OVER does with this
  // session; the adopt sentence does, so only this one gets the line.
  assert(/START OVER begins a new session and leaves this one on disk/.test(body().textContent),
    "a dropped-steps question offers START OVER without saying what it leaves behind");
  await press(byText("CONTINUE"), "CONTINUE");
  eq(runPosts().length, 2, "CONTINUE re-posts");
  eq(onIn(runPosts()[1].body), "accept_dropped", "with its own flag");
  assert(useStore.getState().flows.logs.some((l) => l.msg === "continued night 3: 7 steps kept, 0 new"),
    "the continue was not said on the flow log");
});

/** MUTANT "cancel starts over" (askContinue answers "fresh" whenever the
 *  confirm resolves false) - observed:
 *    x [hook-classic] CANCEL on a continue question starts nothing: CANCEL must
 *      not re-post (expected 1, got 2)
 *    x [sheet] CANCEL on a continue question starts nothing: CANCEL must not
 *      re-post (expected 1, got 2)
 *  MUTANT "sheet toasts a cancel" (flow.tsx: `if (cancelled) return;` removed)
 *  - observed:
 *    x [sheet] CANCEL on a continue question starts nothing: a cancel was
 *      reported as a failure: ["The flow did not start"]
 *  MUTANT "recount loses the START OVER note" (flowRunControls.tsx: the note's
 *  guard `q.code !== "adopt"` narrowed to `q.code === "dropped_steps"`) -
 *  observed:
 *    x [hook-classic] CANCEL on a continue question starts nothing: a recount
 *      question offers START OVER without saying what it leaves behind
 *      (expected 1, got 0)
 *    x [sheet] CANCEL on a continue question starts nothing: (the same line)
 */
for (const where of ["hook-classic", "sheet"] as const) {
  await testAsync(`[${where}] CANCEL on a continue question starts nothing`, async () => {
    serve(conflict(RECOUNT_409), started(FRESH_4));
    await mount(where);
    await pressRun(where);
    assert(asking(), "no question on a 409 recount");
    assert(byText("CONTINUE") != null, "recount offers CONTINUE");
    assert(startOverBtn() != null, "recount offers START OVER");
    // The recount sentence, like the dropped one, does not say what START
    // OVER does with this session, so the question says it - once.
    eq(occurrences("START OVER begins a new session and leaves this one on disk."), 1,
      "a recount question offers START OVER without saying what it leaves behind");
    await press(byText("CANCEL"), "CANCEL");
    eq(runPosts().length, 1, "CANCEL must not re-post");
    assert(!asking(), "the question did not close");
    eq(useStore.getState().flows.run.phase, "idle", "nothing started");
    const toasts = (useStore.getState() as any).toasts ?? [];
    assert(!toasts.some((t: any) => t.level === "error"),
      `a cancel was reported as a failure: ${JSON.stringify(toasts.map((t: any) => t.title))}`);
  });
}

/** MUTANT "sheet does not ask the continue question" - observed:
 *    x [sheet] ADOPT, then CONTINUE on the dropped steps: the last post carries
 *      both: no ADOPT question
 *  MUTANT "loop re-posts without the question's flags" (flowRunControls.tsx
 *  runAnsweringQuestions re-posts `nextRunFlags({}, yes)`) - observed:
 *    x [sheet] ADOPT, then CONTINUE on the dropped steps: the last post carries
 *      both: the third post carries both answers (expected adopt,accept_dropped,
 *      got accept_dropped)
 *  MUTANT "drop earlier flags on re-post" (flowsSlice.ts) - observed, the same
 *  line as the loop mutant above.
 */
await testAsync("[sheet] ADOPT, then CONTINUE on the dropped steps: the last post carries both", async () => {
  serve(conflict(ADOPT_409), conflict(DROPPED_409), started(CONTINUED_3));
  await mount("sheet");
  await pressRun("sheet");
  assert(byText("ADOPT") != null, "no ADOPT question");
  await press(byText("ADOPT"), "ADOPT");
  eq(runPosts().length, 2, "ADOPT re-posts");
  eq(onIn(runPosts()[1].body), "adopt", "the second post");
  assert(asking(), "the dropped-steps question never came");
  eq(occurrences(DROPPED_SENTENCE), 1, "the second question's sentence, once");
  eq(occurrences(ADOPT_SENTENCE), 0, "the first question's sentence is gone");
  await press(byText("CONTINUE"), "CONTINUE");
  eq(runPosts().length, 3, "CONTINUE re-posts");
  eq(onIn(runPosts()[2].body), "adopt,accept_dropped", "the third post carries both answers");
  assert(win.location.hash.startsWith("#/session/now"),
    `a started run lands on the running night, got ${win.location.hash}`);
});

await testAsync("CONTROL [hook-classic]: a 409 unmapped still asks RUN ANYWAY, and it re-posts accept_unmapped", async () => {
  serve(conflict(UNMAPPED_409), started(CONTINUED_3));
  await mount("hook-classic");
  await pressRun("hook-classic");
  assert(asking(), "no question on a 409 unmapped");
  assert(body().textContent.includes(UNMAPPED[0].detail), "the loss is not listed");
  assert(startOverBtn() == null, "a graph question offers START OVER - that is a session answer");
  await press(byText("RUN ANYWAY"), "RUN ANYWAY");
  eq(runPosts().length, 2, "RUN ANYWAY re-posts");
  eq(onIn(runPosts()[1].body), "accept_unmapped", "with the acceptance and nothing else");
});

await testAsync("CONTROL [sheet]: a 409 unmapped still asks RUN ANYWAY, and it re-posts accept_unmapped", async () => {
  serve(conflict(UNMAPPED_409), started(CONTINUED_3));
  await mount("sheet");
  await pressRun("sheet");
  assert(asking(), "no question on a 409 unmapped");
  assert(startOverBtn() == null, "a graph question offers START OVER");
  await press(byText("RUN ANYWAY"), "RUN ANYWAY");
  eq(runPosts().length, 2, "RUN ANYWAY re-posts");
  eq(onIn(runPosts()[1].body), "accept_unmapped", "with the acceptance and nothing else");
});

// ------------------------------------------- the sheet's two failure toasts
// The card leaves for Session - Now only when the engine took the run, so a
// refusal keeps the operator on the card with a toast. After an answer, that
// toast has to say the answer was not the obstacle, and after a CONTINUE
// answer it must not blame "the losses" - the operator was never asked about
// any. `RunOutcome.answered` exists for exactly this choice.
const errorToasts = () =>
  (((useStore.getState() as any).toasts ?? []) as { level: string; title: string; detail?: string }[])
    .filter((t) => t.level === "error");

/** MUTANT "the loop never records an answer" (flowRunControls.tsx
 *  runAnsweringQuestions: `answered = yes;` removed) - observed:
 *    x [sheet] ADOPT, then a refusal: 'still did not start', and the answer is
 *      what it clears: after an answer, the toast says the answer was not the
 *      obstacle (expected The flow still did not start, got The flow did not
 *      start)
 *    x CONTROL [sheet]: RUN ANYWAY, then a refusal, still names the losses:
 *      the title (expected The flow still did not start, got The flow did not
 *      start)
 *  MUTANT "sheet blames the losses after a continue answer" (flow.tsx:
 *  `detail: answered === "unmapped"` -> `detail: true`) - observed:
 *    x [sheet] ADOPT, then a refusal: 'still did not start', and the answer is
 *      what it clears: a CONTINUE answer is not an acceptance of losses
 *      (expected Answering the question was not what was blocking it - the
 *      reason is in the flow log., got Accepting the losses was not what was
 *      blocking it - the reason is in the flow log.)
 */
await testAsync("[sheet] ADOPT, then a refusal: 'still did not start', and the answer is what it clears", async () => {
  serve(conflict(ADOPT_409), conflict(CHANGED_409));
  await mount("sheet");
  await pressRun("sheet");
  await press(byText("ADOPT"), "ADOPT");
  eq(runPosts().length, 2, "ADOPT re-posts");
  assert(!asking(), "a refusal is not another question");
  assert(!win.location.hash.startsWith("#/session/now"),
    `a refused run left the card for the running night: ${win.location.hash}`);
  const errs = errorToasts();
  eq(errs.length, 1, "one failure toast");
  eq(errs[0].title, "The flow still did not start",
    "after an answer, the toast says the answer was not the obstacle");
  eq(errs[0].detail,
    "Answering the question was not what was blocking it - the reason is in the flow log.",
    "a CONTINUE answer is not an acceptance of losses");
  assert(useStore.getState().flows.logs.some((l) => l.msg === `could not start: ${CHANGED_SENTENCE}`),
    "the reason the toast points at is not on the flow log");
});

/** MUTANT "sheet blames the question after RUN ANYWAY" (flow.tsx:
 *  `detail: answered === "unmapped"` -> `detail: false`) - observed:
 *    x CONTROL [sheet]: RUN ANYWAY, then a refusal, still names the losses:
 *      today's words for a refused RUN ANYWAY (expected Accepting the losses
 *      was not what was blocking it - the reason is in the flow log., got
 *      Answering the question was not what was blocking it - the reason is in
 *      the flow log.)
 */
await testAsync("CONTROL [sheet]: RUN ANYWAY, then a refusal, still names the losses", async () => {
  serve(conflict(UNMAPPED_409), serverError);
  await mount("sheet");
  await pressRun("sheet");
  await press(byText("RUN ANYWAY"), "RUN ANYWAY");
  eq(runPosts().length, 2, "RUN ANYWAY re-posts");
  const errs = errorToasts();
  eq(errs.length, 1, "one failure toast");
  eq(errs[0].title, "The flow still did not start", "the title");
  eq(errs[0].detail,
    "Accepting the losses was not what was blocking it - the reason is in the flow log.",
    "today's words for a refused RUN ANYWAY");
});

/** MUTANT "a first refusal says still" (flow.tsx: `enqueueToast(answered ===
 *  null` -> `enqueueToast(false`) - observed:
 *    x CONTROL [sheet]: a first press refused outright asks nothing and says
 *      'did not start': nothing was answered, so nothing is 'still' (expected
 *      The flow did not start, got The flow still did not start)
 */
await testAsync("CONTROL [sheet]: a first press refused outright asks nothing and says 'did not start'", async () => {
  serve(serverError);
  await mount("sheet");
  await pressRun("sheet");
  eq(runPosts().length, 1, "one post");
  assert(!asking(), "a 500 is not a question");
  const errs = errorToasts();
  eq(errs.length, 1, "one failure toast");
  eq(errs[0].title, "The flow did not start", "nothing was answered, so nothing is 'still'");
});

if (root) await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`flowsContinue.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
