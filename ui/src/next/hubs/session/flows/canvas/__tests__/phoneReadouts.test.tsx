// phoneReadouts.test.tsx - the run readouts and the RUN copy on every RUN
// surface, MOUNTED, graded on what the server really answers (#189 S5; spec
// 5.9 button copy, 5.10 published state and ETA, U-07).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/phoneReadouts.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FOUR SURFACES, ONE TREE. The #/next phone stage sheet, the classic
// phone MONITOR tab, the classic header and the #/next canvas toolbar are
// mounted side by side over one store, so each assertion is about all of
// them at once and a surface that reads its own source stands out.
//
// THE ANSWERS ARE THE TWO RECORDED FILES runCopy.test.ts reads (server/tests/
// fixtures/flow_progress_continue.json and sequence_state_mosaic.json), read,
// never copied. The store holds the progress answer as the slice would after
// opening the eighth Example, and the sequence state as the socket would
// write it.
//
// WHAT IS GUARDED
//
//   1. THE READOUTS, while the run is this flow's: STATE, ETA with "hops not
//      yet costed" while `hops_costed` is false, STAGE "M31 1-1 · pass 4",
//      FRAMES from the sequence state, on both phone monitors; the header's
//      and the toolbar's ETA from the same number. Across a meridian wait the
//      stage names the mosaic and the wait, with nothing site-derived, for an
//      operator and a viewer alike. Another flow's run leaves the idle values.
//   2. THE ONE COPY: every RUN surface prints "CONTINUE M31 MOSAIC (night 3,
//      194/480 subs)", with the route's numbers, not the graph's; the classic
//      phone header prints the verb and carries the whole line as its
//      accessible text.
//   3. AT 390 PX the flow's name is the part that truncates and the
//      parenthetical never is, on both full-width phone buttons.
//   4. START OVER sits under CONTINUE on both phone monitors, asks a confirm,
//      posts `fresh` on its yes and nothing on its CANCEL.
//   5. START OVER is locked wherever RUN is (the rig's refusals on all four
//      surfaces, an unsaved edit on the two #/next ones), and a locked press
//      asks nothing and posts nothing. The phone sheet's live line under its
//      title reads the same STATE and ETA as its tiles.
//   6. THE ARMED LABEL (#474): the first tap on either #/next RUN reads
//      "CONFIRM CONTINUE (night 3, 194/480 subs)", the copy's verb, night and
//      counts, and "CONFIRM RUN" with nothing to continue.
//   7. STOP IS ONE TAP on both #/next buttons: over a live run of this flow
//      neither carries an arm, and one tap posts exactly one abort.
//   8. THE EMPTY LOG (#529), on its own four surfaces (the two canvas log
//      strips and the two phone monitors): through a live run of this flow
//      it says the server publishes no log lines for a flow run, never
//      "Idle"; outside one it says "no events yet"; and a line the log was
//      given (a refused start's) is shown over either sentence.
//
// SINCE #449 the seed also holds the slice's `sessionIds`, and the fake rig
// answers the progress route with the recorded answer: the readouts ask the
// known sessions whose run it is, and the live refresh re-reads progress
// when a known run ends (S7, the fix S7-USLICE left for this file).
//
// Every mutant below was run in a private scratch copy of ui/
// (scratchpad/S5-RUNUI-mut, #254), from a byte backup of the mutated file
// restored and hash-compared after each run, and the failure it produced is
// quoted. The counts quoted as N/10 were observed before case 5 was added;
// re-run against eleven cases (scratchpad/S5-RUNUI-verify-mut), every one of
// those mutants fails the same cases with the same lines, (N+1)/11, and
// case 5's own mutants are quoted above it. Case 6 and its mutants arrived in
// S7 (scratchpad S7-URUN-mut), so every S5 count here is of eleven cases, not
// twelve. Case 7 arrived with the S7 verification (scratchpad
// S7-URUN-verify-mut), so case 6's counts, quoted as N/12, are of twelve
// cases, not thirteen. Case 8's nine cases arrived in H4 (scratchpad
// H4-ULOG-mut), so every count before case 8's is of thirteen cases or
// fewer, and case 8's own are of twenty-two.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The sheet imports `canvas.css`; Node has no loader for it.
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
// A phone, 390 px wide: the stage sheet asks the breakpoint.
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 390 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const asked: { url: string; method: string; body: any }[] = [];
/** While set, the run route refuses a start with a 409 carrying this
 *  sentence, as the server does when the engine is already running (case 8's
 *  control). */
let refuseRun: string | null = null;
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  if (refuseRun !== null && /\/run$/.test(url) && method === "POST") {
    const refusal = { detail: refuseRun };
    return {
      ok: false, status: 409, statusText: "Conflict",
      headers: { get: () => "application/json" },
      json: async () => refusal,
      text: async () => JSON.stringify(refusal),
    };
  }
  let data: any = { ok: true };
  if (/\/run$/.test(url) && method === "POST") {
    data = { started: true, flow_id: RECORD.id, frames: 120, unmapped: [] };
  } else if (url === `/api/flows/${RECORD.id}/progress`) {
    // The route's recorded answer, as the slice's live refresh re-reads it
    // when a run of a known session ends (#449): an `{ok:true}` here would be
    // a progress answer with no session, and RUN would drop to RUN mid-case.
    data = JSON.parse(JSON.stringify(PROGRESS));
  } else if (url === "/api/flows/compile") {
    data = { plan: {}, structural: [], issues: [], unmapped: [] };
  }
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
};
const runPosts = () => asked.filter((a) => a.method === "POST" && /\/run$/.test(a.url));

// ------------------------------------------------------------------ fixtures
// Eight levels up from this file is the repository root. A missing file FAILS
// the whole file: a skipped fixture reads as green readouts.
const { readFileSync } = await import("node:fs");
const FIXTURES = "../../../../../../../../server/tests/fixtures/";
function readFixture(name: string): Record<string, any> {
  const rel = FIXTURES + name;
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer these surfaces are graded `
      + `against: ${(e as Error).message}`);
  }
}
const PROGRESS = readFixture("flow_progress_continue.json").response;
const STATES = readFixture("sequence_state_mosaic.json").states;
const SHOOTING = STATES.shooting;
const WAIT_OPERATOR = STATES.meridian_wait_operator;
const WAIT_VIEWER = STATES.meridian_wait_viewer;
if (!PROGRESS?.session || !SHOOTING?.group || !WAIT_OPERATOR?.group || !WAIT_VIEWER?.group) {
  throw new Error("the recorded fixtures do not hold the progress answer and the three states");
}

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { FlowStagesPhoneSheet } = await import("../FlowStagesPhoneSheet");
const { FlowCanvasToolbar } = await import("../FlowCanvasToolbar");
const { default: FlowPhoneMonitor } = await import("../../../../../../components/flows/FlowPhoneMonitor");
const { default: FlowHeader } = await import("../../../../../../components/flows/FlowHeader");
const { HOPS_NOT_COSTED, START_OVER_TITLE } = await import("../../../../../../components/flows/runCopy");
const {
  default: ClassicLogStrip, EMPTY_LOG_TEXT, RUN_EMPTY_LOG_TEXT,
} = await import("../../../../../../components/flows/FlowLogStrip");
const { FlowLogStrip: NextLogStrip } = await import("../FlowLogStrip");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const within = (surface: string, t: string): any =>
  container.querySelector(`[data-surface="${surface}"] [data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const text = (el: any): string => String(el?.textContent ?? "");
/** An element that must not be on screen. Says what WAS there, rather than
 *  handing a DOM node to JSON.stringify. */
function absent(el: any, msg: string): void {
  if (el != null) throw new Error(`${msg}: found ${el.tagName} reading "${text(el)}"`);
}

// ------------------------------------------------------------------ the flow
// The eighth Example as the editor holds it after an open, with ONE
// difference that the copy must not see: its graph on screen was edited to 30
// subs a panel, 180 in all, while the saved flow the route counts (and RUN
// starts) holds 480. The name is the one the recorded run carries.
const FLOW_NAME: string = SHOOTING.session.name;
const P = (type: string, extra: Record<string, unknown> = {}): any =>
  ({ ...(NODE_DEFS as any)[type].params, ...extra });
const GRAPH = {
  nodes: [
    { id: "n2", type: "target", x: 0, y: 0, params: P("target", { name: "M31", rows: 2, cols: 3 }) },
    { id: "n3", type: "capture", x: 200, y: 0, params: P("capture", { count: 30 }) },
  ],
  edges: [{ id: "e1", from: "n2", fromPort: "target", to: "n3", toPort: "run" }],
};
const RECORD = {
  id: PROGRESS.flow_id, name: FLOW_NAME, folder: "Examples", tagline: "",
  graph: GRAPH, created_ts: 1, updated_ts: 2, last_run: null, last_result: "", readonly: false,
};

/** The line the four surfaces must print, from the route's numbers. */
const NIGHT = PROGRESS.session.nights + 1;
const BANKED = PROGRESS.blocks.reduce((n: number, b: any) => n + b.banked, 0);
const TOTAL = PROGRESS.blocks.reduce((n: number, b: any) => n + b.total, 0);
const LINE = `CONTINUE ${FLOW_NAME.toUpperCase()} (night ${NIGHT}, ${BANKED}/${TOTAL} subs)`;

const ADMIN_CAPS = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];

function seed(sequence: any, flows: Record<string, unknown> = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      confirm: null,
      sequence: JSON.parse(JSON.stringify(sequence)),
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify(GRAPH)),
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null, countsNote: null,
        progress: JSON.parse(JSON.stringify(PROGRESS)),
        // As the slice holds it once that answer has landed: the answer's
        // session noted in the same write (flowsSlice `fetchProgress`), which
        // is whose run the readouts ask about since #449.
        sessionIds: [PROGRESS.session.id],
        run: { ...s.flows.run, phase: "idle", etaS: null, curStage: "—", frames: 0, frameGoal: null },
        ui: { ...s.flows.ui, screen: "editor" },
        ...flows,
      } as never,
    } as never);
  });
}

/** The four surfaces, each in a box named for it. `headerTier` picks the
 *  classic header's layout: "tablet" for the full line and START OVER,
 *  "phone" for README section 5's compact row. */
async function mount(headerTier: "tablet" | "phone" = "tablet"): Promise<void> {
  const box = (surface: string, child: any) =>
    createElement("div", { key: surface, "data-surface": surface }, child);
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(Fragment, null,
      box("next-phone", createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 })),
      box("classic-monitor", createElement(FlowPhoneMonitor as any)),
      box("classic-header", createElement(FlowHeader as any, { tier: headerTier })),
      box("next-toolbar", createElement(FlowCanvasToolbar as any)),
    ));
  });
  await settle();
}

/** A tile's value (and its sub-line), on either monitor. */
function tile(surface: "next-phone" | "classic-monitor", which: string): { value: string; sub: string } {
  const id = surface === "next-phone" ? `flow-stages-${which}` : `flow-monitor-${which}`;
  const el = within(surface, id);
  assert(el != null, `${surface}: no ${which} tile`);
  if (surface === "next-phone") {
    return {
      value: text(el.querySelector(".nx-readout-value")),
      sub: text(el.querySelector(".nx-readout-sub")),
    };
  }
  const spans = el.querySelectorAll(":scope > span");
  return { value: text(spans[1]), sub: text(spans[2]) };
}

const MONITORS = ["next-phone", "classic-monitor"] as const;

/** The phone stage sheet's live line, the one under its title: STATE and ETA
 *  again, in the sheet's own header (`Sheet`'s `live`). */
const liveLine = (): string =>
  text(container.querySelector('[data-surface="next-phone"] .nx-sheet-live'));

/** The run line one surface prints: its verb, name and parenthetical. */
function lineOf(surface: string): string {
  const verb = within(surface, "run-copy-verb");
  if (!verb) return text(within(surface, "run-copy-text")) || "(no run copy)";
  return [verb, within(surface, "run-copy-name"), within(surface, "run-copy-detail")]
    .filter(Boolean).map(text).join(" ");
}

// ========================================================= 1. the readouts

// MUTANT "hops_costed ignored" (runCopy.ts runReadouts' etaNote always
// null). Observed, phoneReadouts.test 9/10:
//   x a panel being shot: both monitors, the header and the toolbar read the sequence state: next-phone: the ETA's note
//   expected "hops not yet costed"
//   got      ""
// MUTANT "running from the phase alone" (flowRunControls.tsx: `running =
// isRunPhaseLive(phase) || ours` made `isRunPhaseLive(phase)`, so a run this
// page did not start never shows its ETA slot or STOP). Observed,
// phoneReadouts.test 8/10:
//   x a panel being shot: both monitors, the header and the toolbar read the sequence state: the header's ETA, with its note
//   expected "ETA 288:48 · hops not yet costed"
//   got      ""
//   x a costed clock carries no note, on any of the four: the header's ETA alone
//   expected "ETA 288:48"
//   got      ""
// MUTANT "STOP forgotten" (runCopy.ts `if (live) return plain("STOP")`
// removed). Observed, phoneReadouts.test 9/10:
//   x a panel being shot: both monitors, the header and the toolbar read the sequence state: the phone sheet's button
//   expected "STOP"
//   got      "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
// MUTANT "the phone sheet's live line from flows.run" (FlowStagesPhoneSheet
// .tsx: the live line under the sheet's title printing the idle values in
// place of the readouts). Observed, scratchpad S5-RUNUI-verify-mut,
// phoneReadouts.test 10/11:
//   x a panel being shot: both monitors, the header and the toolbar read the sequence state: the phone sheet's live line, under its title
//   expected "RUNNING · ETA 288:48"
//   got      "IDLE · ETA -"
await test("a panel being shot: both monitors, the header and the toolbar read the sequence state", async () => {
  // The recorded shooting state since the H4 rewrite of the night (a
  // centring miss is counted at its pass boundary, #534, so the state is
  // picked a pass later): 16752 s left, pass 4, 9 frames. Before it the pins
  // read 288:48 (17328 s), pass 3 and 6 / 120; the mutant records in this
  // file quote those, as observed then. RED against the file restored from
  // its pre-H4 bytes, run by the H4 integration (observed, 20/22):
  //   x a panel being shot: both monitors, the header and the toolbar read the sequence state: next-phone: ETA, the rig's 16752 s
  //   expected "279:12"
  //   got      "288:48"
  seed(SHOOTING);
  await mount();
  for (const m of MONITORS) {
    eq(tile(m, "state").value, "RUNNING", `${m}: STATE`);
    eq(tile(m, "eta").value, "279:12", `${m}: ETA, the rig's 16752 s`);
    eq(tile(m, "eta").sub, HOPS_NOT_COSTED, `${m}: the ETA's note`);
    eq(tile(m, "stage").value, "M31 1-1 · pass 4", `${m}: STAGE`);
    eq(tile(m, "frames").value, "9 / 120", `${m}: FRAMES`);
  }
  eq(liveLine(), "RUNNING · ETA 279:12", "the phone sheet's live line, under its title");
  eq(text(within("classic-header", "flow-header-eta")), "ETA 279:12 · hops not yet costed",
    "the header's ETA, with its note");
  const bar = within("next-toolbar", "flow-eta");
  assert(bar != null && text(bar).includes("279:12"), `the toolbar's ETA: ${text(bar)}`);
  eq(text(within("next-toolbar", "flow-eta-note")), HOPS_NOT_COSTED, "the toolbar's note");
  // The run is live and this flow's, so every RUN surface offers STOP.
  eq(text(within("next-phone", "flow-stages-run")), "STOP", "the phone sheet's button");
  eq(text(within("next-toolbar", "flow-run")), "STOP", "the toolbar's button");
});

await test("a costed clock carries no note, on any of the four", async () => {
  // CONTROL: the note is the flag's.
  seed({ ...SHOOTING, progress: { ...SHOOTING.progress, hops_costed: true } });
  await mount();
  for (const m of MONITORS) eq(tile(m, "eta").sub, "", `${m}: no note once a hop is costed`);
  eq(text(within("classic-header", "flow-header-eta")), "ETA 279:12", "the header's ETA alone");
  absent(within("next-toolbar", "flow-eta-note"), "the toolbar's note is gone");
});

// MUTANT "the panel through the meridian wait" (runCopy.ts runStage's
// meridian_wait branch removed). Observed, phoneReadouts.test 9/10:
//   x across a meridian wait the stage names the mosaic and the wait, and nothing site-derived: operator next-phone: STAGE
//   expected "M31 · waiting for the meridian"
//   got      "M31 2-1 · pass 17"
await test("across a meridian wait the stage names the mosaic and the wait, and nothing site-derived", async () => {
  const eta = WAIT_OPERATOR.live.meridian_eta_s;
  assert(typeof eta === "number" && eta > 0, "premise: the recorded wait carries a meridian countdown");
  for (const [who, st] of [["operator", WAIT_OPERATOR], ["viewer", WAIT_VIEWER]] as const) {
    seed(st);
    await mount();
    for (const m of MONITORS) {
      eq(tile(m, "stage").value, "M31 · waiting for the meridian", `${who} ${m}: STAGE`);
      eq(tile(m, "frames").value, "45 / 120", `${who} ${m}: FRAMES`);
      const all = text(container.querySelector(`[data-surface="${m}"] [data-testid$="monitor"]`)
        ?? container.querySelector(`[data-surface="${m}"]`));
      assert(!all.includes("2-1") && !/pass \d/.test(all),
        `${who} ${m}: the waiting panel or pass reached the monitor: ${all}`);
    }
    for (const s of ["next-phone", "classic-monitor", "classic-header", "next-toolbar"]) {
      const all = text(container.querySelector(`[data-surface="${s}"]`));
      // 813 s is "13:33" in either surface's m:ss.
      assert(!all.includes("13:33") && !all.includes(String(eta)),
        `${who} ${s}: the meridian countdown reached the screen: ${all}`);
    }
  }
});

// MUTANT "readouts fed by any run" (runCopy.ts runReadouts asking
// runIsLive(sequence) in place of flowRunLive(progress, sequence)). Observed,
// phoneReadouts.test 8/10:
//   x another flow's run leaves both monitors and both ETA slots on the idle values: next-phone: STATE
//   expected "IDLE"
//   got      "RUNNING"
//   x an ETA for a run this flow started, beside another flow's live run, is not the other's: the header's ETA
//   expected "ETA —"
//   got      "ETA 288:48 · hops not yet costed"
await test("another flow's run leaves both monitors and both ETA slots on the idle values", async () => {
  seed({ ...SHOOTING, session: { ...SHOOTING.session, id: "another-flows-session" } });
  await mount();
  const want: Record<string, [string, string, string, string]> = {
    "next-phone": ["IDLE", "-", "-", "0"],
    "classic-monitor": ["IDLE", "—", "—", "0"],
  };
  for (const m of MONITORS) {
    const [state, eta, stage, frames] = want[m];
    eq(tile(m, "state").value, state, `${m}: STATE`);
    eq(tile(m, "eta").value, eta, `${m}: ETA`);
    eq(tile(m, "eta").sub, "", `${m}: no note`);
    eq(tile(m, "stage").value, stage, `${m}: STAGE`);
    eq(tile(m, "frames").value, frames, `${m}: FRAMES`);
  }
  eq(liveLine(), "IDLE · ETA -", "the phone sheet's live line keeps the idle values too");
  // Not this flow's run, and this flow started nothing: no ETA slot at all,
  // and RUN still offers to continue this flow's own session.
  absent(within("classic-header", "flow-header-eta"), "the header shows no ETA");
  absent(within("next-toolbar", "flow-eta"), "the toolbar shows no ETA");
  eq(lineOf("next-toolbar"), LINE, "RUN still reads this flow's CONTINUE");
});

await test("an ETA for a run this flow started, beside another flow's live run, is not the other's", async () => {
  // `flows.run.phase` is "running" (this page pressed RUN; the slice writes
  // it optimistically) while the live run is another flow's: the ETA slots
  // show, and say the rig has not reported this run's time.
  //
  // `startedAt: Date.now()` alongside `phase` (#647, W5 integration): the
  // real `flowsRun` always stamps both together, and `useFlowRunControls`'s
  // `running` now trusts an optimistic "running" phase only within
  // `RUN_PHASE_BRIDGE_MS` of its OWN `startedAt` - a phase seeded with no
  // fresh timestamp reads as a STALE latch (correctly ignored), not as a
  // page that just pressed RUN, and this fixture means the latter.
  seed({ ...SHOOTING, session: { ...SHOOTING.session, id: "another-flows-session" } },
    { run: { ...useStore.getState().flows.run, phase: "running", startedAt: Date.now(),
             frames: 0, frameGoal: 48 } });
  await mount();
  eq(text(within("classic-header", "flow-header-eta")), "ETA —", "the header's ETA");
  assert(text(within("next-toolbar", "flow-eta")).includes("-")
    && !text(within("next-toolbar", "flow-eta")).includes("279:12"),
  `the toolbar's ETA: ${text(within("next-toolbar", "flow-eta"))}`);
  eq(tile("next-phone", "frames").value, "0 / 48", "the run route's own goal, not the other run's 120");
});

// ========================================================= 2. the one copy

// MUTANT "copy from the graph" (flowRunControls.tsx: the copy's blocks built
// from the graph on screen, each TARGET's rows x cols x its CAPTUREs' counts,
// banked 0, in place of the progress route's). Observed,
// phoneReadouts.test 5/10:
//   x another flow's run leaves both monitors and both ETA slots on the idle values: RUN still reads this flow's CONTINUE
//   expected "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
//   got      "CONTINUE M31 MOSAIC (night 3, 0/180 subs)"
//   x every RUN surface prints the one copy, from the route's numbers: next-phone
//   expected "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
//   got      "CONTINUE M31 MOSAIC (night 3, 0/180 subs)"
//   x the classic phone header prints the verb, and the whole line is its accessible text: the accessible line is the one copy
//   expected "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
//   got      "CONTINUE M31 MOSAIC (night 3, 0/180 subs)"
//   x at 390 px the flow's name truncates and the parenthetical never does, on both phone buttons: next-phone: the parenthetical
//   expected "(night 3, 194/480 subs)"
//   got      "(night 3, 0/180 subs)"
//   x START OVER on both phone monitors asks first, posts fresh on its yes and nothing on CANCEL: next-phone: the confirm says what the session holds: START OVER begins a new session that counts from 0 and leaves this one on disk, where CONTINUE will not go back to it. It holds 0 of 180 subs.
// The copy's own two mutants turn the same cases red here: "night = nights"
// (6/10, "got "CONTINUE M31 MOSAIC (night 2, 194/480 subs)"") and "banked
// summed over the panels twice" (5/10, "got "CONTINUE M31 MOSAIC (night 3,
// 388/480 subs)""); runCopy.test.ts quotes them in full.
await test("every RUN surface prints the one copy, from the route's numbers", async () => {
  seed({ state: "idle" });
  await mount("tablet");
  eq(LINE, "CONTINUE M31 MOSAIC (night 3, 194/480 subs)", "premise: the spec's line for the recorded answer");
  for (const s of ["next-phone", "classic-monitor", "classic-header", "next-toolbar"]) {
    eq(lineOf(s), LINE, s);
  }
  // The whole button reads as the line, spaces and all, which is the text a
  // screen reader names it by: the gaps on screen are layout, not text.
  eq(text(within("next-phone", "flow-stages-run")), LINE, "the phone sheet's button text");
  eq(text(within("next-toolbar", "flow-run")), LINE, "the toolbar's button text");
  eq(text(within("classic-monitor", "run-copy")), `▶ ${LINE}`, "the classic monitor's button text");
  eq(text(within("classic-header", "run-copy")), `▶ ${LINE}`, "the classic header's button text");
  // START OVER beside CONTINUE, on every surface that has the room.
  for (const [s, id] of [["next-phone", "flow-stages-start-over"], ["classic-monitor", "flow-start-over"],
    ["classic-header", "flow-start-over"], ["next-toolbar", "flow-start-over"]] as const) {
    assert(within(s, id) != null, `${s}: no START OVER beside CONTINUE`);
  }
});

await test("the classic phone header prints the verb, and the whole line is its accessible text", async () => {
  seed({ state: "idle" });
  await mount("phone");
  const sr = within("classic-header", "run-copy-text");
  assert(sr != null, "the compact button carries no accessible line");
  eq(text(sr), LINE, "the accessible line is the one copy");
  assert(String(sr.className).includes("sr-only"), "and it is for a screen reader, not the 390 px row");
  const button = sr.closest("button");
  eq(text(button).replace(LINE, "").trim(), "▶ CONTINUE", "the visible words");
  absent(within("classic-header", "flow-start-over"),
    "no START OVER in the phone header: README section 5 has no room, the MONITOR tab has it");
});

// MUTANT "CONTINUE over any session" (runCopy.ts: the `status !==
// "dormant"` test dropped). Observed, phoneReadouts.test 9/10:
//   x RUN, not CONTINUE, when the route names no dormant session: complete: the phone sheet
//   expected "RUN"
//   got      "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
// MUTANT "RUN through the CONTINUE row" (FlowHeader.tsx RunWords' plain
// branch taken for STOP alone). Observed, phoneReadouts.test 9/10:
//   x RUN, not CONTINUE, when the route names no dormant session: no session: the classic monitor draws no CONTINUE: found SPAN reading "▶ RUN"
await test("RUN, not CONTINUE, when the route names no dormant session", async () => {
  // CONTROLS: never run or abandoned (null), complete, active and not live.
  for (const session of [null, { ...PROGRESS.session, status: "complete" },
    { ...PROGRESS.session, status: "active" }]) {
    seed({ state: "idle" }, { progress: { ...PROGRESS, session } });
    await mount("tablet");
    const what = session ? session.status : "no session";
    eq(text(within("next-phone", "flow-stages-run")), "RUN", `${what}: the phone sheet`);
    eq(text(within("next-toolbar", "flow-run")), "RUN", `${what}: the toolbar`);
    absent(within("classic-monitor", "run-copy"), `${what}: the classic monitor draws no CONTINUE`);
    assert(/▶ RUN$/.test(text(within("classic-monitor", "flow-monitor-frames")?.closest("[data-flows-phone-tab]")
      ?.querySelector("button"))), `${what}: the classic monitor's button reads ▶ RUN`);
    for (const s of ["next-phone", "classic-monitor", "classic-header", "next-toolbar"]) {
      absent(container.querySelector(`[data-surface="${s}"] [data-testid$="start-over"]`),
        `${what}: ${s} offers START OVER with nothing to continue`);
    }
  }
});

// =========================================================== 3. at 390 px

// MUTANT "the whole line in one span" (FlowCanvasToolbar.tsx RunCopyWords:
// the parenthetical moved into the name's truncating span). Observed,
// phoneReadouts.test 9/10:
//   x at 390 px the flow's name truncates and the parenthetical never does, on both phone buttons: next-phone: no parenthetical of its own
// MUTANT "the parenthetical truncates" (FlowHeader.tsx RunWords: the
// parenthetical's `shrink-0 whitespace-nowrap` made `min-w-0 truncate`).
// Observed, phoneReadouts.test 9/10:
//   x at 390 px the flow's name truncates and the parenthetical never does, on both phone buttons: classic-monitor: the parenthetical may be cut: min-w-0 truncate
await test("at 390 px the flow's name truncates and the parenthetical never does, on both phone buttons", async () => {
  seed({ state: "idle" });
  await mount("phone");
  // #/next: inline styles, the chrome's own rule for one-offs.
  const nextButton = within("next-phone", "flow-stages-run");
  const nName = nextButton?.querySelector('[data-testid="run-copy-name"]');
  const nDetail = nextButton?.querySelector('[data-testid="run-copy-detail"]');
  assert(nName != null, "next-phone: no name of its own");
  assert(nDetail != null, "next-phone: no parenthetical of its own");
  eq(text(nName), "M31 MOSAIC", "next-phone: the name");
  eq(text(nDetail), `(night ${NIGHT}, ${BANKED}/${TOTAL} subs)`, "next-phone: the parenthetical");
  const ns = nName.style;
  assert(ns.overflow === "hidden" && ns.textOverflow === "ellipsis" && ns.whiteSpace === "nowrap"
    && ns.minWidth === "0px",
  `next-phone: the name must be the part that gives up width: ${nName.getAttribute("style")}`);
  const ds = nDetail.style;
  assert(ds.flexShrink === "0" && ds.whiteSpace === "nowrap" && ds.overflow === "" && ds.textOverflow === "",
    `next-phone: the parenthetical may be cut: ${nDetail.getAttribute("style")}`);
  const cut = [...nextButton.querySelectorAll('[data-testid="run-copy"] *')]
    .filter((el: any) => el.style?.textOverflow === "ellipsis");
  eq(cut.length, 1, "next-phone: exactly one part of the line may be cut");

  // Classic: Tailwind's `truncate` is the cut, `shrink-0 whitespace-nowrap` the guard.
  const monitor = container.querySelector('[data-surface="classic-monitor"]');
  const cName = monitor.querySelector('[data-testid="run-copy-name"]');
  const cDetail = monitor.querySelector('[data-testid="run-copy-detail"]');
  assert(cName != null && cDetail != null, "classic-monitor: the name and the parenthetical are one span");
  const cn = String(cName.className).split(/\s+/);
  const cd = String(cDetail.className).split(/\s+/);
  assert(cn.includes("truncate") && cn.includes("min-w-0"),
    `classic-monitor: the name must be the part that gives up width: ${cName.className}`);
  assert(cd.includes("shrink-0") && cd.includes("whitespace-nowrap") && !cd.includes("truncate")
    && !cd.includes("min-w-0"),
  `classic-monitor: the parenthetical may be cut: ${cDetail.className}`);
  eq(text(cDetail), `(night ${NIGHT}, ${BANKED}/${TOTAL} subs)`, "classic-monitor: the parenthetical");
  const box = cName.parentElement;
  assert(String(box.className).includes("flex") && String(box.className).includes("min-w-0"),
    `classic-monitor: the line is a row that can narrow: ${box.className}`);
});

// ============================================================ 4. START OVER

// MUTANT "START OVER without a confirm" (flowRunControls.tsx startOver: the
// pushConfirm and its early return removed, so the press posts fresh at
// once). Observed, phoneReadouts.test 9/10:
//   x START OVER on both phone monitors asks first, posts fresh on its yes and nothing on CANCEL: next-phone: START OVER asked no confirm
//   expected "Start this flow over?"
//   got      undefined
await test("START OVER on both phone monitors asks first, posts fresh on its yes and nothing on CANCEL", async () => {
  for (const [s, id] of [["next-phone", "flow-stages-start-over"], ["classic-monitor", "flow-start-over"]] as const) {
    seed({ state: "idle" });
    await mount("phone");
    const press = (): void => {
      const el = within(s, id);
      assert(el != null, `${s}: no START OVER`);
      click(el.closest("button") ?? el);
    };
    const before = runPosts().length;
    press();
    await settle();
    const req = useStore.getState().confirm;
    eq(req?.title, START_OVER_TITLE, `${s}: START OVER asked no confirm`);
    eq(runPosts().length, before, `${s}: a request went out before the operator answered`);
    assert(String(req?.body).includes(`${BANKED} of ${TOTAL} subs`),
      `${s}: the confirm says what the session holds: ${String(req?.body)}`);

    await act(async () => { useStore.getState().resolveConfirm(false); });
    await settle();
    eq(runPosts().length, before, `${s}: CANCEL started a run`);

    press();
    await settle();
    await act(async () => { useStore.getState().resolveConfirm(true); });
    await settle();
    const sent = runPosts();
    eq(sent.length, before + 1, `${s}: the yes posts exactly one run`);
    eq(sent[sent.length - 1].url, `/api/flows/${RECORD.id}/run`, `${s}: against this flow`);
    eq(sent[sent.length - 1].body?.fresh, true, `${s}: and it is a fresh session`);
  }
});

// START OVER IS LOCKED WHEREVER RUN IS. It starts the stored flow as RUN
// does, so the rig's refusals (here, no camera) lock it on all four surfaces,
// and on the two #/next ones so does an unsaved edit: the press would start
// the SAVED graph and walk away from the session the copy names while the
// operator is looking at a graph that is not the one being started. A locked
// press explains itself; it asks no confirm and posts nothing.
//
// MUTANT "START OVER unlocked on the toolbar" (FlowCanvasToolbar.tsx: its
// START OVER's `lockedReason={runReason}` removed). Observed, verifier's
// scratchpad S5-RUNUI-verify-mut, phoneReadouts.test 10/11:
//   x START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing: no camera: next-toolbar: START OVER is live while RUN is locked
// MUTANT "START OVER unlocked on the phone sheet" (FlowStagesPhoneSheet.tsx:
// its START OVER's `lockedReason={runReason}` removed). Observed, 10/11:
//   x START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing: no camera: next-phone: START OVER is live while RUN is locked
// MUTANT "START OVER unlocked on the classic monitor" (FlowPhoneMonitor.tsx:
// its START OVER's `reason={reason}` made `reason={null}`). Observed, 10/11:
//   x START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing: no camera: classic-monitor: START OVER is live while RUN is locked
// MUTANT "START OVER unlocked in the classic header" (FlowHeader.tsx: its
// START OVER's `reason={runReason}` made `reason={null}`). Observed, 10/11:
//   x START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing: no camera: classic-header: START OVER is live while RUN is locked
// MUTANT "START OVER past an unsaved edit" (FlowCanvasToolbar.tsx: its START
// OVER locked by `hookRunReason`, the rig's refusals alone, in place of
// `runReason`). Observed, 10/11:
//   x START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing: unsaved edits: next-toolbar: START OVER is live while RUN is locked
await test("START OVER is locked wherever RUN is, and a locked press asks nothing and posts nothing", async () => {
  const START_OVER: Record<string, string> = {
    "next-phone": "flow-stages-start-over", "classic-monitor": "flow-start-over",
    "classic-header": "flow-start-over", "next-toolbar": "flow-start-over",
  };
  const RUN: Record<string, () => any> = {
    "next-phone": () => within("next-phone", "flow-stages-run"),
    "classic-monitor": () => within("classic-monitor", "run-copy")?.closest("button"),
    "classic-header": () => within("classic-header", "run-copy")?.closest("button"),
    "next-toolbar": () => within("next-toolbar", "flow-run"),
  };
  const locked = (el: any): boolean => el?.getAttribute("aria-disabled") === "true";
  const cases: [string, () => void, string[]][] = [
    ["no camera", () => {
      seed({ state: "idle" });
      act(() => { useStore.setState({ status: { connected: { camera: { connected: false } } } } as never); });
    }, ["next-phone", "classic-monitor", "classic-header", "next-toolbar"]],
    ["unsaved edits", () => { seed({ state: "idle" }, { dirty: true }); }, ["next-phone", "next-toolbar"]],
  ];
  for (const [why, arrange, surfaces] of cases) {
    arrange();
    await mount("tablet");
    for (const s of surfaces) {
      const el = within(s, START_OVER[s]);
      assert(el != null, `${why}: ${s}: no START OVER beside CONTINUE`);
      const button = el.closest("button");
      assert(locked(RUN[s]()), `${why}: ${s}: premise: RUN is locked`);
      assert(locked(button), `${why}: ${s}: START OVER is live while RUN is locked`);
      const before = runPosts().length;
      click(button);
      await settle();
      eq(useStore.getState().confirm, null, `${why}: ${s}: a locked START OVER asked a confirm`);
      eq(runPosts().length, before, `${why}: ${s}: a locked START OVER posted a run`);
    }
  }
  // CONTROL: with the camera back and nothing unsaved, all four are live.
  seed({ state: "idle" });
  await mount("tablet");
  for (const s of Object.keys(START_OVER)) {
    assert(!locked(within(s, START_OVER[s])?.closest("button")), `${s}: START OVER locked with nothing to refuse`);
  }
});

// ======================================================= 6. the armed label

// THE ARMED LABEL KEEPS THE COPY (#474). Both #/next RUN buttons are two-tap
// arms, and the first tap swaps the whole label for the arm's. It was a fixed
// "CONFIRM RUN", so over a dormant session the night and the counts vanished
// at the moment of commitment and RUN sat beside START OVER. Now one helper
// (`runArm`) arms both with the copy's verb and parenthetical. The literal is
// the recorded answer's line; the first tap posts nothing.
//
// MUTANT "fixed CONFIRM RUN" (S7, scratchpad S7-URUN-mut; FlowCanvasToolbar
// .tsx `runArm` answering `{ label: "CONFIRM RUN" }` for every verb but STOP).
// Observed, phoneReadouts.test 11/12:
//   x the first tap on either #/next RUN arms it with CONTINUE's verb, night and counts: next-phone: the armed label
//   expected "CONFIRM CONTINUE (night 3, 194/480 subs)"
//   got      "CONFIRM RUN"
// (canvasDom.test.tsx goes red under the same mutant, on the toolbar.)
// MUTANT "the phone sheet keeps its own arm" (S7; FlowStagesPhoneSheet.tsx's
// RUN back to `arm={running ? undefined : { label: "CONFIRM RUN" }}`, the
// toolbar still on `runArm`). Observed, phoneReadouts.test 11/12:
//   x the first tap on either #/next RUN arms it with CONTINUE's verb, night and counts: next-phone: the armed label
//   expected "CONFIRM CONTINUE (night 3, 194/480 subs)"
//   got      "CONFIRM RUN"
await test("the first tap on either #/next RUN arms it with CONTINUE's verb, night and counts", async () => {
  const ARMED = `CONFIRM CONTINUE (night ${NIGHT}, ${BANKED}/${TOTAL} subs)`;
  eq(ARMED, "CONFIRM CONTINUE (night 3, 194/480 subs)", "premise: the recorded answer's armed line");
  for (const [s, id] of [["next-phone", "flow-stages-run"], ["next-toolbar", "flow-run"]] as const) {
    seed({ state: "idle" });
    await mount("tablet");
    const before = runPosts().length;
    const button = within(s, id);
    assert(button != null, `${s}: no RUN button`);
    eq(button.getAttribute("data-armed"), "false", `${s}: RUN is a two-tap arm`);
    click(button);
    await settle();
    const armed = within(s, id);
    eq(armed.getAttribute("data-armed"), "true", `${s}: the first tap armed it`);
    eq(text(armed.querySelector(".nx-btn-label")), ARMED, `${s}: the armed label`);
    eq(runPosts().length, before, `${s}: the first tap posted a run`);
  }
  // CONTROL: with nothing to continue the arm still reads CONFIRM RUN.
  for (const [s, id] of [["next-phone", "flow-stages-run"], ["next-toolbar", "flow-run"]] as const) {
    seed({ state: "idle" }, { progress: { ...PROGRESS, session: null } });
    await mount("tablet");
    click(within(s, id));
    await settle();
    eq(text(within(s, id).querySelector(".nx-btn-label")), "CONFIRM RUN", `${s}: no session, CONFIRM RUN`);
  }
});

// ============================================== 7. STOP is one tap, on both

// STOP NEVER ARMS, ON EITHER #/next BUTTON. Both RUN buttons take their arm
// from `runArm` since #474, and `runArm` gives STOP none: emergency motion
// stops stay single-tap, the house rule for STOP / HALT / polar-STOP alike.
// canvasDom.test.tsx holds that on the toolbar alone, and nothing held it on
// the phone stage sheet, whose RUN S7 moved onto the helper. Over a live run
// of this flow (the recorded `shooting` state, under the recorded session's
// id) each button reads STOP, carries no arm, and one tap posts exactly one
// abort. Added by the S7 verification of S7-URUN.
//
// Each mutant below was run in the private copy scratchpad
// S7-URUN-verify-mut, from a byte backup restored and SHA-256 compared.
// MUTANT "the phone sheet arms STOP" (FlowStagesPhoneSheet.tsx
// `arm={runArm(copy)}` made `arm={runArm(copy) ?? { label: "CONFIRM STOP"
// }}`). Observed, phoneReadouts.test 12/13:
//   x STOP on either #/next button is one tap: no arm, one abort: next-phone: STOP is armed - an emergency motion stop must never need a second tap
//   expected null
//   got      "false"
// MUTANT "STOP armed" (FlowCanvasToolbar.tsx `runArm`'s `if (copy.verb ===
// "STOP") return undefined;` removed, so both buttons arm STOP). Observed,
// phoneReadouts.test 12/13, the same failure on next-phone.
// MUTANT "the toolbar arms STOP" (FlowCanvasToolbar.tsx's own
// `arm={runArm(copy)}` made `arm={runArm(copy) ?? { label: "CONFIRM STOP"
// }}`, the phone sheet untouched, so the loop's second leg is reached).
// Observed, phoneReadouts.test 12/13:
//   x STOP on either #/next button is one tap: no arm, one abort: next-toolbar: STOP is armed - an emergency motion stop must never need a second tap
//   expected null
//   got      "false"
// MUTANT "the phone STOP posts nothing" (FlowStagesPhoneSheet.tsx RUN's
// `onPress={act}` made `onPress={running ? () => {} : act}`). Observed,
// phoneReadouts.test 12/13:
//   x STOP on either #/next button is one tap: no arm, one abort: next-phone: one tap on STOP must post exactly one abort
//   expected 1
//   got      0
await test("STOP on either #/next button is one tap: no arm, one abort", async () => {
  const aborts = (): number =>
    asked.filter((a) => a.method === "POST" && a.url === "/api/sequence/abort").length;
  for (const [s, id] of [["next-phone", "flow-stages-run"], ["next-toolbar", "flow-run"]] as const) {
    seed(SHOOTING);
    await mount("tablet");
    const stop = within(s, id);
    assert(stop != null, `${s}: no RUN button`);
    eq(text(stop), "STOP", `${s}: premise: a live run of this flow offers STOP`);
    eq(stop.getAttribute("data-armed"), null,
      `${s}: STOP is armed - an emergency motion stop must never need a second tap`);
    const before = aborts();
    click(stop);
    await settle();
    eq(aborts(), before + 1, `${s}: one tap on STOP must post exactly one abort`);
  }
});

// ========================================= 8. the empty log, on all four (#529)

// THE LOG'S FOUR SURFACES, ONE TREE: the classic canvas's log rail, the
// classic phone MONITOR tab, the #/next canvas's log strip and the #/next
// phone stage sheet's LOG. Nothing writes `flows.logs` from the rig (there is
// no `flow.log` topic), so through a live run of the flow each shows its
// empty sentence, and until H4 every one of them said "Idle — no events yet"
// (the #/next pair with a hyphen) directly under STATE RUNNING: the S7
// probe's rotating 2x2 walk saw it on the classic phone MONITOR tab. Each
// surface is its own case, so a surface that keeps the old words, or asks
// its own question about the run, is the case that goes red.
const LOG_SURFACES = ["classic-strip", "classic-monitor", "next-strip", "next-phone"] as const;
type LogSurface = typeof LOG_SURFACES[number];

async function mountLogs(): Promise<void> {
  const box = (surface: LogSurface, child: any) =>
    createElement("div", { key: surface, "data-surface": surface }, child);
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(Fragment, null,
      box("classic-strip", createElement(ClassicLogStrip as any)),
      box("classic-monitor", createElement(FlowPhoneMonitor as any)),
      box("next-strip", createElement(NextLogStrip as any)),
      box("next-phone", createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 })),
    ));
  });
  await settle();
}

/** The words one surface's log shows: the collapsed bar's message on the two
 *  canvas strips (the empty ring's sentence, or the newest line), the log
 *  panel on the two phone monitors.
 *
 *  The bar's MESSAGE, never the whole bar: the bar reads "LOG" and then the
 *  message with no space between them, so its text is "LOGIdle - no events
 *  yet", where `\bIdle\b` finds no word boundary. Read that way, the Idle
 *  check below passed on both strips under "Idle kept" (the first run of the
 *  mutants, scratchpad H4-ULOG-mut), a check that could not fail. */
function logOf(surface: LogSurface): string {
  const box = container.querySelector(`[data-surface="${surface}"]`);
  const el = surface === "classic-strip" ? box?.querySelector("button[aria-expanded] > span:nth-of-type(2)")
    : surface === "next-strip" ? box?.querySelector('[data-testid="flow-log-toggle"] .nx-flow-log-last')
      : surface === "classic-monitor" ? box?.querySelector('[role="log"]')
        : box?.querySelector('[data-testid="flow-stages-log"]');
  assert(el != null, `${surface}: no log on the surface`);
  return text(el);
}

/** Both monitors' STATE, the readouts the log's sentence must agree with. */
const states = (): string =>
  MONITORS.map((m) => `${m} ${tile(m, "state").value}`).join(", ");

// Each mutant below was run in the private copy scratchpad H4-ULOG-mut, from
// a byte backup of the mutated file restored and SHA-256 compared. Each
// per-surface mutant turns that surface's two cases red, this one and the
// next, and no other surface's.
// The rule lives twice, word for word: `emptyLogText` in the classic strip,
// which the classic rail and MONITOR print, and its restatement in the
// #/next strip, which the #/next strip and stage sheet print (r7Parity keeps
// the #/next tree off the classic presentation module). These cases compare
// all four with the CLASSIC module's words, so a restatement that drifts is
// red here, where canvasDom.test.tsx, reading the #/next words, stays green.
// Word for word, not by `includes`: with only the `includes` checks, a
// restatement that grew a suffix passed every case here (the H4-ULOG
// verification, scratchpad H4-ULOG-verify-mut), so each case ends on an
// exact comparison, placed last so every failure recorded below is still
// the first one its case reports.
// MUTANT "the #/next restatement grows a suffix, outside a run"
// (canvas/FlowLogStrip.tsx's EMPTY_LOG_TEXT made "no events yet on this
// rig"; canvasDom.test.tsx stays 36/36 under it). Observed,
// phoneReadouts.test 20/22:
//   x outside a live run of this flow, the empty log says no events yet: next-strip: next-strip, another flow's live run: the empty log outside a run of this flow, word for word
//   expected "no events yet"
//   got      "no events yet on this rig"
//   x outside a live run of this flow, the empty log says no events yet: next-phone: next-phone, another flow's live run: the empty log outside a run of this flow, word for word
//   expected "no events yet"
//   got      "no events yet on this rig"
// MUTANT "the #/next restatement grows a suffix, during a run"
// (canvas/FlowLogStrip.tsx's RUN_EMPTY_LOG_TEXT given a trailing " yet";
// canvasDom.test.tsx stays 36/36 under it). Observed, phoneReadouts.test
// 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-strip: next-strip: the empty log during a live run of this flow, word for word
//   expected "no log lines: the server publishes none for a flow run"
//   got      "no log lines: the server publishes none for a flow run yet"
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-phone: next-phone: the empty log during a live run of this flow, word for word
//   expected "no log lines: the server publishes none for a flow run"
//   got      "no log lines: the server publishes none for a flow run yet"
// MUTANT "Idle kept (the classic rule)" (components/flows/FlowLogStrip.tsx
// `emptyLogText` answering the old "Idle — no events yet" whatever the
// run). Observed, phoneReadouts.test 18/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: classic-strip: classic-strip: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle — no events yet"
//   x while this flow's run is live, the empty log says the server publishes no log lines: classic-monitor: classic-monitor: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle — no events yet"
//   x outside a live run of this flow, the empty log says no events yet: classic-strip: classic-strip, another flow's live run: the log says Idle: "Idle — no events yet"
//   x outside a live run of this flow, the empty log says no events yet: classic-monitor: classic-monitor, another flow's live run: the log says Idle: "Idle — no events yet"
// MUTANT "Idle kept (the #/next rule)" (canvas/FlowLogStrip.tsx
// `emptyLogText` answering "Idle - no events yet" whatever the run).
// Observed, phoneReadouts.test 18/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-strip: next-strip: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle - no events yet"
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-phone: next-phone: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle - no events yet"
//   x outside a live run of this flow, the empty log says no events yet: next-strip: next-strip, another flow's live run: the log says Idle: "Idle - no events yet"
//   x outside a live run of this flow, the empty log says no events yet: next-phone: next-phone, another flow's live run: the log says Idle: "Idle - no events yet"
// MUTANT "the #/next restatement drifts" (canvas/FlowLogStrip.tsx's
// RUN_EMPTY_LOG_TEXT reworded "no log lines from the server during a flow
// run"; canvasDom.test.tsx stays 36/36 under it). Observed,
// phoneReadouts.test 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-strip: next-strip: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "no log lines from the server during a flow run"
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-phone: next-phone: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "no log lines from the server during a flow run"
// MUTANT "Idle kept on the classic rail" (FlowLogStrip.tsx's bar printing
// "Idle — no events yet" in place of `emptyLogText(live)`). Observed,
// phoneReadouts.test 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: classic-strip: classic-strip: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle — no events yet"
//   x outside a live run of this flow, the empty log says no events yet: classic-strip: classic-strip, another flow's live run: the log says Idle: "Idle — no events yet"
// MUTANT "Idle kept on the classic MONITOR" (FlowPhoneMonitor.tsx printing
// "Idle — no events yet" in place of `emptyLogText(r.fed)`). Observed,
// phoneReadouts.test 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: classic-monitor: classic-monitor: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle — no events yet"
//   x outside a live run of this flow, the empty log says no events yet: classic-monitor: classic-monitor, another flow's live run: the log says Idle: "Idle — no events yet"
// MUTANT "Idle kept on the #/next strip" (canvas/FlowLogStrip.tsx printing
// "Idle - no events yet" in place of `emptyLogText(live)`). Observed,
// phoneReadouts.test 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-strip: next-strip: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle - no events yet"
//   x outside a live run of this flow, the empty log says no events yet: next-strip: next-strip, another flow's live run: the log says Idle: "Idle - no events yet"
// MUTANT "Idle kept on the #/next stage sheet" (FlowStagesPhoneSheet.tsx
// printing "Idle - no events yet" in place of `emptyLogText(readouts.fed)`).
// Observed, phoneReadouts.test 20/22:
//   x while this flow's run is live, the empty log says the server publishes no log lines: next-phone: next-phone: during a live run of this flow the empty log must say the server publishes no log lines for a flow run, got "Idle - no events yet"
//   x outside a live run of this flow, the empty log says no events yet: next-phone: next-phone, another flow's live run: the log says Idle: "Idle - no events yet"
for (const s of LOG_SURFACES) {
  await test(`while this flow's run is live, the empty log says the server publishes no log lines: ${s}`, async () => {
    seed(SHOOTING);
    await mountLogs();
    eq(states(), "next-phone RUNNING, classic-monitor RUNNING",
      "premise: the recorded run is this flow's and live, so the readouts are fed");
    const log = logOf(s);
    assert(log.includes(RUN_EMPTY_LOG_TEXT),
      `${s}: during a live run of this flow the empty log must say the server publishes `
      + `no log lines for a flow run, got "${log}"`);
    assert(!/\bIdle\b/i.test(log), `${s}: the log says Idle beside STATE RUNNING: "${log}"`);
    // The whole of it, not a part: `includes` above lets a restatement that
    // only grows a prefix or a suffix through (the mutants "the #/next
    // restatement grows a suffix", above).
    eq(log, RUN_EMPTY_LOG_TEXT, `${s}: the empty log during a live run of this flow, word for word`);
  });
}

// OUTSIDE A LIVE RUN OF THIS FLOW, three ways: another flow's run live on the
// rig (the readouts idle while the rig RUNS, the case the live sentence must
// not reach, since the server's silence is about a run this flow is not
// making), this flow's run once complete, and no run at all.
//
// MUTANT "live copy shown outside a run (the classic rule)"
// (components/flows/FlowLogStrip.tsx `emptyLogText` answering the run
// sentence whatever it is handed). Observed, phoneReadouts.test 20/22:
//   x outside a live run of this flow, the empty log says no events yet: classic-strip: classic-strip, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
//   x outside a live run of this flow, the empty log says no events yet: classic-monitor: classic-monitor, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
// MUTANT "live copy shown outside a run (the #/next rule)"
// (canvas/FlowLogStrip.tsx `emptyLogText` the same way). Observed,
// phoneReadouts.test 20/22:
//   x outside a live run of this flow, the empty log says no events yet: next-strip: next-strip, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
//   x outside a live run of this flow, the empty log says no events yet: next-phone: next-phone, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
// The two strips draw no readouts, so each asks whether the run is live for
// itself, of `flowRunLive` over the known sessions, the readouts' own rule;
// the two phone monitors read the readouts' `fed`.
// MUTANT "the classic strip asks whether any run is live"
// (components/flows/FlowLogStrip.tsx `useFlowRunFed` answering whether the
// sequence state is one of runIsLive's four states, whoever's run it is).
// Observed, phoneReadouts.test 21/22:
//   x outside a live run of this flow, the empty log says no events yet: classic-strip: classic-strip, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
// MUTANT "the #/next strip asks whether any run is live"
// (canvas/FlowLogStrip.tsx's `live` selector the same way). Observed,
// phoneReadouts.test 21/22:
//   x outside a live run of this flow, the empty log says no events yet: next-strip: next-strip, another flow's live run: the empty log must say "no events yet" and nothing about a run, got "no log lines: the server publishes none for a flow run"
const OUTSIDE: [string, any][] = [
  ["another flow's live run", { ...SHOOTING, session: { ...SHOOTING.session, id: "another-flows-session" } }],
  ["this flow's run once complete", { ...SHOOTING, state: "complete" }],
  ["no run at all", { state: "idle" }],
];
for (const s of LOG_SURFACES) {
  await test(`outside a live run of this flow, the empty log says no events yet: ${s}`, async () => {
    for (const [why, sequence] of OUTSIDE) {
      seed(sequence);
      await mountLogs();
      eq(states(), "next-phone IDLE, classic-monitor IDLE", `premise, ${why}: the readouts are not fed`);
      const log = logOf(s);
      assert(log.includes(EMPTY_LOG_TEXT) && !log.includes(RUN_EMPTY_LOG_TEXT),
        `${s}, ${why}: the empty log must say "${EMPTY_LOG_TEXT}" and nothing about a run, got "${log}"`);
      assert(!/\bIdle\b/i.test(log), `${s}, ${why}: the log says Idle: "${log}"`);
      eq(log, EMPTY_LOG_TEXT, `${s}, ${why}: the empty log outside a run of this flow, word for word`);
    }
  });
}

// CONTROL: A LINE THE LOG WAS GIVEN IS SHOWN, IN AND OUT OF A RUN. RUN pressed
// on the classic MONITOR while another flow's run holds the rig, and the run
// route refusing it with the engine's own sentence, is the one start the
// slice logs (`flowsRun`'s "could not start: ..."). Every surface shows that
// line, and neither empty sentence; and it stays shown once this flow's run
// is live, where the empty sentence would otherwise say the server sends
// none.
//
// This case passed before the fix as well as after it (the lines were always
// shown), which is what a control is for. Neither change of sentence may
// reach a log that holds a line:
// MUTANT "the empty sentence over the lines" (FlowPhoneMonitor.tsx's
// `lines.length === 0 ?` made `true ?`, so its log prints the empty sentence
// whatever the ring holds). Observed, phoneReadouts.test 21/22:
//   x a refused start's line still shows on all four, in and out of a run: classic-monitor, beside another flow's run: the refused start's line is not shown: "no events yet"
// MUTANT "the #/next strip's bar ignores the ring" (canvas/FlowLogStrip.tsx's
// bar printing `emptyLogText(live)` in place of the newest line). Observed,
// phoneReadouts.test 21/22:
//   x a refused start's line still shows on all four, in and out of a run: next-strip, beside another flow's run: the refused start's line is not shown: "no events yet"
await test("a refused start's line still shows on all four, in and out of a run", async () => {
  const REFUSAL = "a sequence is already running";
  const LINE_SAID = `could not start: ${REFUSAL}`;
  seed(OUTSIDE[0][1]);
  await mountLogs();
  const verb = within("classic-monitor", "run-copy-verb");
  const press = verb?.closest("button");
  assert(press != null && text(verb) === "CONTINUE",
    `premise: the classic MONITOR offers CONTINUE beside another flow's run, got "${text(verb)}"`);
  const before = runPosts().length;
  refuseRun = REFUSAL;
  try {
    click(press);
    await settle();
  } finally {
    refuseRun = null;
  }
  eq(runPosts().length, before + 1, "premise: the press posted one start, which the rig refused");
  const check = (when: string): void => {
    for (const s of LOG_SURFACES) {
      const log = logOf(s);
      assert(log.includes(LINE_SAID), `${s}, ${when}: the refused start's line is not shown: "${log}"`);
      assert(!log.includes(EMPTY_LOG_TEXT) && !log.includes(RUN_EMPTY_LOG_TEXT),
        `${s}, ${when}: an empty sentence is shown over a line the log holds: "${log}"`);
    }
  };
  check("beside another flow's run");
  // This flow's run goes live, as the socket would write it: the log keeps
  // the line it holds.
  act(() => { useStore.setState({ sequence: JSON.parse(JSON.stringify(SHOOTING)) } as never); });
  await settle();
  eq(states(), "next-phone RUNNING, classic-monitor RUNNING", "premise: this flow's run is now live");
  check("once this flow's run is live");
});

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`phoneReadouts.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
