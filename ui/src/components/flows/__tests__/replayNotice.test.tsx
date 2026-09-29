// replayNotice.test.tsx - the editor's line that an armed auto-resume will
// replay the version its session froze (#473, S7 orchestrator ruling 1; spec
// 5.9), graded on what the progress route really answers, then MOUNTED on
// the three surfaces that carry it.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/replayNotice.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ANSWER IS THE RECORDED FILE, READ, NEVER COPIED:
// server/tests/fixtures/flow_progress_continue.json, the progress route's
// answer for the eighth Example as S7-SESSION re-recorded it, with the
// session's `armed` true and `plan_saved_ts` null (an Example is never saved,
// so the route freezes no time for one). Every other case is that answer with
// one or two of the session's keys changed, and says which.
//
// WHAT IS GUARDED
//
//   1. THE RULE (ruling 1): the line shows only while `session.armed` is true
//      AND the record's `updated_ts` is later than `plan_saved_ts`; a null
//      `plan_saved_ts`, as recorded, shows nothing.
//   2. THE WORDS: "the armed session will replay the version from <date>;
//      press CONTINUE to apply your edits", <date> being `plan_saved_ts`'s
//      LOCAL calendar date, YYYY-MM-DD.
//   3. THE SURFACES: the classic editor (beside FlowCountsLine, every tier),
//      under the #/next canvas toolbar and on the #/next phone stage list,
//      each right after its counts line, and none of them without the rule.
//   4. A save that moves the record's saved time raises the line on all
//      three without a reopen.
//
// THE ZONE IS PINNED. "Local" is only testable where local and UTC differ,
// and CI runs on UTC, so this file sets its own process's zone before any
// date is made: Asia/Tokyo, UTC+9 with no daylight saving, picked because it
// is nobody's rig. The saved instant below is 05:30 there, which is the
// evening before in UTC, so a date read in UTC names the wrong day.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S7-URUN-mut, #254), from a byte backup of the mutated file restored and
// SHA-256 compared after each run, and the failure it produced is quoted.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  no @types/node guaranteed; tsx supplies process at runtime
(globalThis as any).process.env.TZ = "Asia/Tokyo";

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";
import type { FlowProgress } from "../../../lib/flowsApi";

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// A phone, 390 px wide, for the stage sheet's breakpoint; the classic editor
// takes its tier as a prop.
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 390 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v !== undefined) Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ fixture
// A missing or unreadable file FAILS the whole file: a skipped fixture would
// read as a line correctly withheld.
const FIXTURE = "../../../../../server/tests/fixtures/flow_progress_continue.json";
let PROGRESS: FlowProgress;
try {
  PROGRESS = JSON.parse(readFileSync(new URL(FIXTURE, import.meta.url), "utf8") as string).response;
} catch (e) {
  throw new Error(`cannot read ${FIXTURE}, the recorded answer the replay line is graded against: `
    + `${(e as Error).message}`);
}
if (!PROGRESS?.session) throw new Error(`${FIXTURE} holds no session`);

// ------------------------------------------------------------- the fake rig
// Every route the surfaces ask is answered; the progress route answers what
// the store was seeded with, so a live re-read cannot swap the case's answer.
let answer: FlowProgress = PROGRESS;
g.fetch = async (url: string) => {
  let data: any = { ok: true };
  if (/\/progress$/.test(url)) data = JSON.parse(JSON.stringify(answer));
  else if (url === "/api/flows/compile") data = { plan: {}, structural: [], issues: [], unmapped: [] };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { createParams } = await import("../nodeDefs");
const { replayNotice } = await import("../replayNotice");
const { default: FlowEditor } = await import("../FlowEditor");
const { FlowCanvasToolbar } = await import("../../../next/hubs/session/flows/canvas/FlowCanvasToolbar");
const { FlowStagesPhoneSheet } = await import("../../../next/hubs/session/flows/canvas/FlowStagesPhoneSheet");

(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
useStore.setState({ flowsFetchCalHealth: async () => {}, flowsFetchTonight: async () => {} } as any);

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ instants
/** 05:30 on 2026-09-22 on this process's (pinned) calendar: the evening of
 *  the 21st in UTC. The version the armed session froze was saved then. */
const SAVED = new Date(2026, 8, 22, 5, 30, 0).getTime() / 1000;
/** A save an hour later: the operator's edits since. */
const LATER = SAVED + 3600;
const LINE = "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits";

/** The recorded answer with its session's keys patched. */
function withSession(patch: Record<string, unknown>): FlowProgress {
  return { ...PROGRESS, session: { ...PROGRESS.session!, ...patch } as FlowProgress["session"] };
}
const rec = (updated_ts: unknown): any => ({ updated_ts });

// ======================================================== 1. the pure rule

// MUTANT "null read as zero" (replayNotice.ts: `const frozen =
// session.plan_saved_ts` made `Number(session.plan_saved_ts)`, so null is 0
// and every save is later than it). Observed, replayNotice.test 5/8:
//   x the recorded answer: armed, with a null plan_saved_ts, shows nothing: a null plan_saved_ts shows nothing, whatever the record says
//     expected null
//     got      "the armed session will replay the version from 1970-01-01; press CONTINUE to apply your edits"
//   x armed but not saved since: nothing: a frozen time that is not a number
//     expected null
//     got      "the armed session will replay the version from 2026-09-21; press CONTINUE to apply your edits"
//   x the recorded answer, and an unarmed session, put the line on no surface: as recorded: classic: a replay line reading "the armed session will replay the version from 1970-01-01; press CONTINUE to apply your edits"
await test("the recorded answer: armed, with a null plan_saved_ts, shows nothing", () => {
  eq([PROGRESS.session!.armed, PROGRESS.session!.plan_saved_ts], [true, null],
    "premise: the route recorded an armed session and no frozen time (an Example is never saved)");
  eq(replayNotice(PROGRESS, rec(LATER)), null, "a null plan_saved_ts shows nothing, whatever the record says");
});

// MUTANT "UTC date" (replayNotice.ts localDate answering
// `d.toISOString().slice(0, 10)`). Observed, replayNotice.test 5/8:
//   x armed and saved since: the line, with the frozen version's LOCAL date: the line
//     expected "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//     got      "the armed session will replay the version from 2026-09-21; press CONTINUE to apply your edits"
//   x armed and saved since: all three surfaces carry the line, each beside its counts line: desktop: classic: the line
//     (the same expected and got)
//   x a save that moves the record's saved time raises the line on all three, without a reopen: classic: after the save
//     (the same expected and got)
await test("armed and saved since: the line, with the frozen version's LOCAL date", () => {
  eq(new Date(SAVED * 1000).toISOString().slice(0, 10), "2026-09-21",
    "premise: the saved instant is the day before in UTC, so a UTC date names the wrong day");
  eq(replayNotice(withSession({ plan_saved_ts: SAVED }), rec(LATER)), LINE, "the line");
  // One second later is later.
  eq(replayNotice(withSession({ plan_saved_ts: SAVED }), rec(SAVED + 1)), LINE, "a save one second later");
});

// MUTANT "notice while unarmed" (replayNotice.ts: the `session?.armed !==
// true` test made `!session`). Observed, replayNotice.test 6/8:
//   x not armed: nothing, however new the save: armed false
//     expected null
//     got      "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//   x the recorded answer, and an unarmed session, put the line on no surface: unarmed: classic: a replay line reading "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
await test("not armed: nothing, however new the save", () => {
  // CONTROLS: disarmed by a PATCH (the frozen time survives a disarm on the
  // server), a server older than S7 that sends no `armed`, and a value that
  // is not exactly true.
  eq(replayNotice(withSession({ plan_saved_ts: SAVED, armed: false }), rec(LATER)), null, "armed false");
  const old = withSession({ plan_saved_ts: SAVED });
  delete (old.session as any).armed;
  eq(replayNotice(old, rec(LATER)), null, "no armed key (a server older than S7)");
  eq(replayNotice(withSession({ plan_saved_ts: SAVED, armed: "true" }), rec(LATER)), null,
    "a string is not true");
  eq(replayNotice({ ...PROGRESS, session: null }, rec(LATER)), null, "no session");
  eq(replayNotice(null, rec(LATER)), null, "no progress answer yet");
});

// MUTANT "notice without a newer save" (replayNotice.ts: `!(saved >
// frozen)` removed from the refusal). Observed, replayNotice.test 6/8:
//   x armed but not saved since: nothing: saved at the frozen time: the same version
//     expected null
//     got      "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//   x a save that moves the record's saved time raises the line on all three, without a reopen: classic: premise: no line before the save
// MUTANT "the same save counts as newer" (`saved > frozen` made `saved >=
// frozen`). Observed, replayNotice.test 6/8, the same two:
//   x armed but not saved since: nothing: saved at the frozen time: the same version
//     expected null
//     got      "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//   x a save that moves the record's saved time raises the line on all three, without a reopen: classic: premise: no line before the save
await test("armed but not saved since: nothing", () => {
  // CONTROLS: the version on screen IS the frozen one, or older, or the
  // record carries no saved time.
  const armed = withSession({ plan_saved_ts: SAVED });
  eq(replayNotice(armed, rec(SAVED)), null, "saved at the frozen time: the same version");
  eq(replayNotice(armed, rec(SAVED - 60)), null, "saved before it");
  eq(replayNotice(armed, rec(undefined)), null, "a record with no saved time");
  eq(replayNotice(armed, null), null, "no record");
  eq(replayNotice(withSession({ plan_saved_ts: "1790000000" }), rec(LATER)), null,
    "a frozen time that is not a number");
});

// MUTANT "no calendar guard" (replayNotice.ts localDate's
// `Number.isNaN(d.getTime())` check removed). Observed, replayNotice.test
// 7/8:
//   x a frozen time the calendar cannot place shows nothing, never NaN-NaN-NaN: a time past the calendar
//     expected null
//     got      "the armed session will replay the version from NaN-NaN-NaN; press CONTINUE to apply your edits"
await test("a frozen time the calendar cannot place shows nothing, never NaN-NaN-NaN", () => {
  // Finite, and past the ±8.64e12 s that a Date can hold.
  eq(replayNotice(withSession({ plan_saved_ts: 1e13 }), rec(2e13)), null, "a time past the calendar");
});

// ====================================================== 2. on the surfaces

/** A TARGET that counts every sub taken, so the counts line stands too and
 *  the replay line can be seen beside it, and one capture. */
const GRAPH = {
  nodes: [
    { id: "n2", type: "target", x: 0, y: 0,
      params: { ...createParams("target"), name: "M31", counts: "Every sub taken" } },
    { id: "n3", type: "capture", x: 240, y: 0, params: { ...createParams("capture") } },
  ],
  edges: [{ id: "e1", from: "n2", fromPort: "target", to: "n3", toPort: "run" }],
};
const RECORD_ID = PROGRESS.flow_id;

function seed(progress: FlowProgress, updated_ts: number): void {
  answer = progress;
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.mount", "control.capture"] },
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      confirm: null,
      config: null,
      site: null,
      sequence: { state: "idle" } as never,
      flows: {
        ...FLOWS_INIT,
        record: { id: RECORD_ID, name: "M31 mosaic", folder: "My flows", tagline: "",
          graph: JSON.parse(JSON.stringify(GRAPH)), created_ts: 1, updated_ts,
          last_run: null, last_result: "", readonly: false },
        graph: JSON.parse(JSON.stringify(GRAPH)),
        progress: JSON.parse(JSON.stringify(progress)),
        sessionIds: progress.session ? [progress.session.id] : [],
        ui: { ...FLOWS_INIT.ui, screen: "editor" },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
/** The classic editor at `tier`, the #/next toolbar and the #/next phone
 *  stage sheet, each in a box named for it, over one store. */
async function mount(tier: "desktop" | "tablet" | "phone"): Promise<void> {
  const box = (surface: string, child: any) =>
    createElement("div", { key: surface, "data-surface": surface }, child);
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(Fragment, null,
      box("classic", createElement(FlowEditor as any, { tier })),
      box("next-toolbar", createElement(FlowCanvasToolbar as any)),
      box("next-phone", createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD_ID }, depth: 0 })),
    ));
  });
  await settle();
}
const inBox = (surface: string, sel: string): any =>
  container.querySelector(`[data-surface="${surface}"] ${sel}`);
/** Each surface's replay line and the counts line it must stand after. */
const SURFACES: [string, string, string][] = [
  ["classic", "[data-flows-replay]", "[data-flows-counts]"],
  ["next-toolbar", '[data-testid="flow-canvas-replay"]', '[data-testid="flow-canvas-counts"]'],
  ["next-phone", '[data-testid="flow-stages-replay"]', '[data-testid="flow-stages-counts"]'],
];
const text = (el: any): string => String(el?.textContent ?? "").trim();

// MUTANT "classic line not mounted" (FlowEditor.tsx: `<FlowReplayLine />`
// removed). Observed, replayNotice.test 6/8:
//   x armed and saved since: all three surfaces carry the line, each beside its counts line: desktop: classic: no replay line
//   x a save that moves the record's saved time raises the line on all three, without a reopen: classic: after the save
//     expected "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//     got      ""
// MUTANT "toolbar line not mounted" (FlowCanvasToolbar.tsx: the
// `flow-canvas-replay` BannerCard removed). Observed, replayNotice.test 6/8:
//   x armed and saved since: all three surfaces carry the line, each beside its counts line: tablet: next-toolbar: no replay line
//   x a save that moves the record's saved time raises the line on all three, without a reopen: next-toolbar: after the save
//     expected "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//     got      ""
// MUTANT "phone line not mounted" (FlowStagesPhoneSheet.tsx: the
// `flow-stages-replay` BannerCard removed). Observed, replayNotice.test 6/8:
//   x armed and saved since: all three surfaces carry the line, each beside its counts line: tablet: next-phone: no replay line
//   x a save that moves the record's saved time raises the line on all three, without a reopen: next-phone: after the save
//     expected "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//     got      ""
await test("armed and saved since: all three surfaces carry the line, each beside its counts line", async () => {
  for (const tier of ["desktop", "tablet", "phone"] as const) {
    seed(withSession({ plan_saved_ts: SAVED }), LATER);
    await mount(tier);
    for (const [surface, line, counts] of SURFACES) {
      if (surface !== "classic" && tier !== "tablet") continue; // the #/next pair once is enough
      const el = inBox(surface, line);
      assert(el != null, `${tier}: ${surface}: no replay line`);
      eq(text(el), LINE, `${tier}: ${surface}: the line`);
      const beside = inBox(surface, counts);
      assert(beside != null, `${tier}: ${surface}: premise: the counts line stands`);
      eq(el.previousElementSibling === beside, true, `${tier}: ${surface}: the replay line follows the counts line`);
    }
  }
});

await test("the recorded answer, and an unarmed session, put the line on no surface", async () => {
  // CONTROLS: the file as recorded (a null plan_saved_ts), and a session
  // saved since but not armed.
  for (const [what, progress] of [["as recorded", PROGRESS],
    ["unarmed", withSession({ plan_saved_ts: SAVED, armed: false })]] as const) {
    seed(progress, LATER);
    await mount("tablet");
    for (const [surface, line, counts] of SURFACES) {
      assert(inBox(surface, counts) != null, `${what}: ${surface}: premise: the counts line stands`);
      const el = inBox(surface, line);
      assert(el == null, `${what}: ${surface}: a replay line reading "${text(el)}"`);
    }
  }
});

// MUTANT "classic line read once" (FlowEditor.tsx FlowReplayLine reading
// `useState(() => replayNotice(useStore.getState().flows.progress,
// useStore.getState().flows.record))[0]` in place of the store selector).
// Observed, replayNotice.test 7/8:
//   x a save that moves the record's saved time raises the line on all three, without a reopen: classic: after the save
//     expected "the armed session will replay the version from 2026-09-22; press CONTINUE to apply your edits"
//     got      ""
await test("a save that moves the record's saved time raises the line on all three, without a reopen", async () => {
  seed(withSession({ plan_saved_ts: SAVED }), SAVED);
  await mount("tablet");
  for (const [surface, line] of SURFACES) {
    assert(inBox(surface, line) == null, `${surface}: premise: no line before the save`);
  }
  // What `flowsSave` writes: the server's answer, with its new saved time.
  act(() => {
    const s = useStore.getState();
    useStore.setState({ flows: { ...s.flows, record: { ...s.flows.record!, updated_ts: LATER } } } as never);
  });
  await settle();
  for (const [surface, line] of SURFACES) eq(text(inBox(surface, line)), LINE, `${surface}: after the save`);
});

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`replayNotice.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
