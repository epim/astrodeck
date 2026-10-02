// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sessionColumnFlowRun.test.tsx - the #/next desktop SESSION COLUMN over a
// flow's mosaic run, MOUNTED, graded on what the server really answers (#468;
// spec 5.10 published state and ETA, U-07; the ui-copy-must-carry-information
// rule).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/sessionColumnFlowRun.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT #468 SAW. After a rotating 2x2 flow of 80 subs of 10 s had run and been
// stopped with one sub banked, the column on every desktop hub read "M31 1-2"
// over "quick session", "0s of 0s planned" and "FILES · 1 SUBS", its LIVE STACK
// chips printed over the card's own heading, and its FLIP tile ran past the
// edge. While the run was live it read "M31 1-2 · 2 of 4" over "night plan"
// and "10s of 3m planned": the plan index of a panel a rotating mosaic visits
// every pass, and one panel's shutter for four panels' work. The kind and the
// count came from the STORE'S Plan (the classic editor's, which a flow run
// never touches) whenever the session was not in hand, and the planned figure
// deduped the four panels as if they were a pool.
//
// THE ANSWERS ARE THE TWO RECORDED FILES, read by KEY, never copied:
// server/tests/fixtures/sequence_state_mosaic.json (`states.shooting`,
// `states.meridian_wait_operator`, `states.meridian_wait_viewer`; S7-URUNHOLD
// adds states to it, so nothing here reads it by position) and
// flow_progress_continue.json (`response`). They are two recordings under ONE
// session id, not one plan: the state is a 2x2 night on the clocked simulator,
// the progress answer is the eighth Example's 2x3 with 194 subs banked over two
// nights. Each surface is graded on the answer it reads: the header on the
// state's `group`, INTEGRATION on the session ledger, which is built here from
// the progress answer's panels, steps and banked counts
// (`GET /api/sessions/{id}` returns the frozen plan and one frame row per
// banked sub, as the store writes them).
//
// The store's Plan is seeded with a DECOY under the run's own `plan_name`, one
// target of 2 x 300 s, so any surface that still reads it (the old
// name-match fallback included) says "quick session" or "10m planned". One
// target cannot show a COUNT read off it, so the case that grades the target
// count swaps in three targets under the same name.
//
// WHAT IS GUARDED
//
//   1. KIND AND COUNT from the sequence state and the session's plan: the
//      state's group makes the kind "mosaic" with its pass, the current panel
//      reads "M31 1-1 · one of 4 panels", and across a meridian wait the line
//      names the mosaic and no panel, for an operator and a viewer alike
//      (runStage's rule, spec 6.9), as it does before the group's first visit
//      (`panel` null). A run outside a group still reads "night plan" and
//      "2 of 3" off its session (control), and a run that has ended, when the
//      engine has cleared both `group` and `session`, guesses no kind, takes
//      no count off a store Plan that shares its name, and names the run by
//      its `plan_name`.
//   2. INTEGRATION is the session plan's planned shutter, every panel in full
//      (a mosaic is the opposite of a pool: server flows/tonight.py `_budget`),
//      while a pool beside it keeps the pool cut (control), a classic Plan-UI
//      mosaic (a `mosaic_group` with no group entry) counts as a pool, and a
//      panel dedupes no other target's step; all against what the session
//      has banked over all its nights. With no
//      ledger (an ended run) it counts subs and prints no seconds it does not
//      have: "0s of 0s planned" came from a terminal progress block that has
//      no exposure in it.
//   3. SUBS ARE COUNTED: "1 sub", on the ended column's current line, on the
//      FILES sheet's summary and on its DOWNLOAD button.
//   4. THE LIVE STACK draws no chips over its empty face (nothing is stacked,
//      so there is nothing for them to describe, and the card says it in
//      words), and the card sits in the frame's flow rather than centred
//      under them; over a real picture the chips stay (control).
//   5. THE VITALS' FOUR TILES WRAP in the column instead of scrolling past its
//      edge: the grid the panel variant uses, whose 140 px floor in the
//      column's 336 px content box is two across (read from the stylesheets,
//      not assumed).
//
// Layout is graded for real on the page (the task report records the
// before/after at 1440x900 with the probe's text_intact and reachable_all);
// jsdom has no layout, so 4 and 5 here hold the STRUCTURE that layout rests on.
//
// NAMED MUTANTS, each run in a private copy of ui/ (scratchpad
// S7-UCOLUMN-mut, #254) from a byte backup restored and hash-compared after
// each run; the failure each produced is quoted at the test it turned red.
//   K  "kind from the store's Plan"      RunHeader's kind reads usePlan().targets
//   P  "planned from the store's Plan"   IntegrationBar plans off usePlan()
//   U  "unpluralised"                    subsPhrase always says "subs"
//   D  "panels deduped as a pool"        IntegrationBar plans with plannedByFilter
//   T  "banked tonight only"             IntegrationBar counts tonight's night alone
//   W  "panel named across the wait"     the target line prints seq.target in a wait
//   C  "chips over the empty face"       LiveStack draws its chips with no picture
//   V  "vitals scroll"                   cells=4 back in the overflow-x scroller
//   A  "pass across the wait"            the kind line prints the pass in a wait
//   E  "ended run unnamed"               no plan_name when the kind is unknown
//   S  "seconds without a ledger"        the no-ledger total guesses seconds again
//   F  "fixed-height empty frame"        the empty face keeps the fixed 180 px
//   L  "kind line cut"                   the kind line back on one ellipsised line
// Every one of the thirteen was seen red against the first thirteen cases, and
// the "n/13" in their quotes is of that file; the finish clocks in the quotes
// are the wall clock of that run.
//
// The S7-UCOLUMN verifier ran these five in its own private copy
// (scratchpad S7-UCOLUMN-verify-mut), found each passed all thirteen cases,
// and added the three cases marked "Added by the S7-UCOLUMN verifier"; each
// was then seen red against the sixteen, quoted at its case:
//   N  "count from the store's Plan"     the old name-match count put back
//   D2 "every target in full"            plannedShutter makes no pool cut
//   G  "any mosaic_group is a panel"     a group entry no longer required
//   X  "a panel dedupes a pool"          a target sharing a panel's step dropped
//   Q  "a null panel is named"           panelNamed without its panel check
//
// H4-UMON (#488) added the case on the wait line across a meridian wait, seen
// red under this mutant (scratchpad H4-UMON-mut), and re-pinned mutant A's
// quote, whose wait line was that defect:
//   FW "fall through to the window sentence"  scheduleStatus.ts waitingText
//                                        words every reason that is not an
//                                        altitude gate as the window

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
// A desktop: the column is the desktop's.
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 1440 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// -------------------------------------------------------------- the fixtures
const { readFileSync } = await import("node:fs");
const fixture = (name: string): any => JSON.parse(readFileSync(
  new URL(`../../../../../../../server/tests/fixtures/${name}`, import.meta.url), "utf8") as string);
const STATES = fixture("sequence_state_mosaic.json").states;
const PROGRESS = fixture("flow_progress_continue.json").response;
const SHOOTING = STATES.shooting;
const WAIT_OPERATOR = STATES.meridian_wait_operator;
const WAIT_VIEWER = STATES.meridian_wait_viewer;
if (!SHOOTING?.group || !WAIT_OPERATOR?.group?.meridian_wait || !WAIT_VIEWER?.group?.meridian_wait) {
  throw new Error("sequence_state_mosaic.json no longer holds the shooting and meridian-wait states");
}
if ("panel" in WAIT_VIEWER.group || "pass" in WAIT_VIEWER.group) {
  throw new Error("the viewer's meridian wait carries a panel or pass: the fixture is not what a viewer is served");
}
const BLOCK = (PROGRESS.blocks as any[]).find((b) => b.kind === "target" && Array.isArray(b.panels));
if (!BLOCK || PROGRESS.session?.id !== SHOOTING.session?.id) {
  throw new Error("flow_progress_continue.json no longer holds a mosaic block under the state's session id");
}
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

// -------------------------------------------- the session ledger, from the route
// Two nights banked (the progress answer's `nights`), and tonight's night,
// which the engine appends at run start (filters.ts `tonightNightKey`) and in
// which nothing has landed yet: so a figure read off tonight alone is 0.
const NIGHTS = Array.from({ length: PROGRESS.session.nights }, (_, i) => `M31-night-${i + 1}`);
const TONIGHT = "M31-tonight";
const SESSION_ID: string = PROGRESS.session.id;
let frameNo = 0;
const LEDGER_FRAMES: any[] = [];
for (const p of BLOCK.panels) {
  for (const s of p.steps) {
    for (let i = 0; i < s.banked; i++) {
      LEDGER_FRAMES.push({
        id: `f${++frameNo}`, ts: frameNo, night: NIGHTS[i % NIGHTS.length],
        target_id: p.target_id, step_id: s.step_id, thumb: null, metrics: {},
        auto_accepted: true, override: null,
      });
    }
  }
}
const planOf = (name: string, targets: any[], groups: any[] = []): any => ({
  name, guide: true, dither_every: 1, dither_pixels: null, autofocus_every: 0,
  cool_to: null, cool_timeout_s: null, apply_filter_offsets: null,
  refocus_on_temp_delta_c: null, meridian_flip: true, recover_guiding: null,
  hfr_reject_factor: null, park_when_done: true, warm_cooler_when_done: true,
  count_mode: PROGRESS.session.count_mode, targets, groups,
});
const SESSION = {
  id: SESSION_ID, schema_version: 3, name: SHOOTING.plan_name,
  created_ts: 1_788_000_000, updated_ts: 1_788_300_000, status: "active",
  nights: [...NIGHTS, TONIGHT], auto_resume: true,
  origin: "flow", origin_id: PROGRESS.flow_id,
  plan: planOf(SHOOTING.plan_name, BLOCK.panels.map((p: any) => ({
    id: p.target_id, name: p.name, ra_hours: 0.71, dec_deg: 41.27,
    center: true, autofocus_first: false, calibration: false,
    mosaic_group: BLOCK.group_id, panel_row: p.row, panel_col: p.col,
    steps: p.steps.map((s: any) => ({
      id: s.step_id, filter: s.filter, exposure_s: s.exposure_s, gain: 100,
      offset: 30, binning: 1, count: s.count, frame_type: s.frame_type,
    })),
  })), [{
    id: BLOCK.group_id, name: BLOCK.name, kind: "mosaic", mode: "rotate",
    visit_passes: 1, visit_min_s: 0, order: "least_complete", require_centred: true,
    max_failed_visits: 3, pa_deg: null, rotate: false, angle_tolerance_deg: null,
    skipped_ids: [], geometry: {},
  }]),
  frames: LEDGER_FRAMES,
};
// What INTEGRATION must read, derived from the route rather than typed in.
let plannedS = 0;
let bankedS = 0;
let dedupedS = 0;
const seenSig = new Set<string>();
for (const p of BLOCK.panels) {
  for (const s of p.steps) {
    plannedS += s.count * s.exposure_s;
    bankedS += s.banked * s.exposure_s;
    const sig = `${s.filter}|${s.exposure_s}|${s.count}`;
    if (!seenSig.has(sig)) { seenSig.add(sig); dedupedS += s.count * s.exposure_s; }
  }
}
if (bankedS !== BLOCK.banked * BLOCK.panels[0].steps[0].exposure_s) {
  throw new Error("the fold below does not reproduce the route's own banked count");
}

// A run OUTSIDE a group (the control): one session, three targets.
const CONTROL_ID = "ctl-night-plan";
const CONTROL_SESSION = {
  ...SESSION, id: CONTROL_ID, name: "Autumn trio", origin: "plan", origin_id: "",
  nights: [TONIGHT], frames: [],
  plan: planOf("Autumn trio", ["NGC 7331", "NGC 891", "M33"].map((name, i) => ({
    id: `c${i}`, name, ra_hours: 22 + i, dec_deg: 30, center: true, autofocus_first: false,
    calibration: false,
    steps: [{ id: `cs${i}`, filter: "L", exposure_s: 60, gain: 100, offset: 30, binning: 1, count: 10, frame_type: "Light" }],
  }))),
};

// The store's Plan: the classic editor's, under the run's own name.
const DECOY_PLAN = planOf(SHOOTING.plan_name, [{
  id: "decoy", name: "Decoy", ra_hours: 5, dec_deg: 5, center: false, autofocus_first: false,
  calibration: false,
  steps: [{ id: "ds", filter: "L", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 2, frame_type: "Light" }],
}]);
// The one-target decoy cannot show a COUNT read off it ("1 of 1" is never
// printed), so the case that grades the count swaps in a three-target decoy
// under the same name, the old name-match fallback's exact condition.
const DECOY_TRIO = planOf(SHOOTING.plan_name, ["Decoy A", "Decoy B", "Decoy C"].map((name, i) => ({
  id: `decoy${i}`, name, ra_hours: 5 + i, dec_deg: 5, center: false, autofocus_first: false,
  calibration: false,
  steps: [{ id: `ds${i}`, filter: "L", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 2, frame_type: "Light" }],
})));
let STORE_PLAN: any = DECOY_PLAN;

// A MIXED plan, to grade the rule that sorts a target into a panel or a pool:
// the recorded mosaic's panels (their group is in `plan.groups`), a pool of two
// candidates sharing one Ha step (one copy counts), a lone target whose step
// has exactly the signature of a panel's first step (it counts on its own: a
// panel neither is deduped against another target's step nor dedupes one),
// and a classic Plan-UI mosaic, whose `mosaic_group` names no group entry and
// so is a pool as far as the planned figure goes (models.py: "a Plan-UI mosaic
// sets `mosaic_group` with no group entry and keeps today's behaviour").
const MIXED_ID = "mixed-pool-and-mosaic";
const step = (id: string, filter: string, exposure_s: number, count: number): any => ({
  id, filter, exposure_s, gain: 100, offset: 30, binning: 1, count, frame_type: "Light",
});
const loneTarget = (id: string, name: string, steps: any[], extra: any = {}): any => ({
  id, name, ra_hours: 21, dec_deg: 44, center: true, autofocus_first: false, calibration: false,
  steps, ...extra,
});
const PANEL_STEP = BLOCK.panels[0].steps[0];
const POOL_HA = { exposure_s: 300, count: 10 };
const CLASSIC_OIII = { exposure_s: 600, count: 5 };
const MIXED_SESSION = {
  ...SESSION, id: MIXED_ID, frames: [],
  plan: planOf(SHOOTING.plan_name, [
    ...SESSION.plan.targets,
    loneTarget("pool-a", "NGC 7000 a", [step("pa", "Ha", POOL_HA.exposure_s, POOL_HA.count)]),
    loneTarget("pool-b", "NGC 7000 b", [step("pb", "Ha", POOL_HA.exposure_s, POOL_HA.count)]),
    loneTarget("twin", "M33", [step("tw", PANEL_STEP.filter, PANEL_STEP.exposure_s, PANEL_STEP.count)]),
    ...["M45 1-1", "M45 1-2"].map((name, i) => loneTarget(`m45-${i}`, name,
      [step(`o${i}`, "OIII", CLASSIC_OIII.exposure_s, CLASSIC_OIII.count)],
      { mosaic_group: "classic-m45" })),
  ], SESSION.plan.groups),
};
// Panels in full, ONE copy of the pool's Ha, the twin on its own, ONE copy of
// the classic mosaic's OIII.
const MIXED_PLANNED_S = plannedS
  + POOL_HA.exposure_s * POOL_HA.count
  + PANEL_STEP.exposure_s * PANEL_STEP.count
  + CLASSIC_OIII.exposure_s * CLASSIC_OIII.count;

// ------------------------------------------------------------ the fake rig
const EMPTY_STACK = {
  enabled: false, target: "", seq: 0, channels: [], frames: 0, integrated_s: 0, rejected: 0,
  mode: null, downsample: 2, has_image: false, render_age_s: null,
  backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 1 },
};
const ONE_SUB_STACK = {
  ...EMPTY_STACK, enabled: true, target: "M31", seq: 3,
  channels: [{ channel: "L", frames: 1, integrated_s: 120, rejected: 0 }],
  frames: 1, integrated_s: 120, mode: "mono", has_image: true, render_age_s: 2,
};
let STACK: any = EMPTY_STACK;

// The FILES sheet's answers: the first banked sub of the flow's first panel,
// its first step as the route records it.
const FIRST_STEP = BLOCK.panels[0].steps[0];
const FILES_INDEX = {
  target: SHOOTING.plan_name,
  totals: { frames: 1, accepted: 1, bytes: 0, integration_s: FIRST_STEP.exposure_s },
  by_filter: [{
    filter: FIRST_STEP.filter, count: 1, accepted: 1, exposure_s: FIRST_STEP.exposure_s,
    bytes: 0, integration_s: FIRST_STEP.exposure_s,
    frames: [{ id: "f1", ts: 1, bytes: 0, accepted: true, override: null, hfr: 2.1, stars: 400, guide_rms: 0.5, thumb: null }],
  }],
};
const LIB_FRAME = {
  path: `M31/${FIRST_STEP.filter}_0001.fits`, name: `${FIRST_STEP.filter}_1.fits`, folder: "M31",
  night: TONIGHT, ts: 1_788_000_001, local_date: "2026-09-03", local_clock: "22:10",
  target: BLOCK.panels[0].name, filter: FIRST_STEP.filter, frame_type: "Light",
  exposure_s: FIRST_STEP.exposure_s, bytes: 10 * 1024 * 1024, mtime: 1_788_000_001,
};

function answer(url: string): { status: number; body: unknown } {
  const ok = (body: unknown) => ({ status: 200, body });
  if (url.includes(`/api/sessions/${SESSION_ID}/files`)) return ok(FILES_INDEX);
  if (url.includes(`/api/sessions/${SESSION_ID}`)) return ok(SESSION);
  if (url.includes(`/api/sessions/${CONTROL_ID}`)) return ok(CONTROL_SESSION);
  if (url.includes(`/api/sessions/${MIXED_ID}`)) return ok(MIXED_SESSION);
  if (url.includes("/api/sessions")) {
    return ok({ sessions: [{
      id: SESSION_ID, name: SHOOTING.plan_name, status: "active", created_ts: 1, updated_ts: 2,
      nights: 3, accepted: 1, total: 480, auto_resume: true,
    }] });
  }
  if (url.includes("/api/sequence/stack")) return ok(STACK);
  if (url.includes("/api/sequence/recoverable")) return ok({ recoverable: false });
  if (url.includes("/tonight")) return ok({ ok: false, reason: "No observatory site is set." });
  if (url.includes("/api/flows")) return ok([]);
  if (url.includes("/api/plans")) return ok([]);
  if (url.includes("/api/reports")) return ok([]);
  if (url.includes("/api/gallery/nights")) {
    return ok({ current: TONIGHT, nights: [{ night: TONIGHT, frames: 1, bytes: LIB_FRAME.bytes }], truncated: false });
  }
  if (url.includes("/api/gallery/frames")) {
    return ok({ frames: [LIB_FRAME], total: 1, bytes: LIB_FRAME.bytes, offset: 0, limit: 500, truncated: false, scan_ms: 1 });
  }
  if (url.includes("/api/remote/status")) {
    return ok({ enabled: false, home_id: "", relay_host: null, connected: false, last_error: null, since_unix: null, gen: 0, via: "direct" });
  }
  return ok({ ok: true });
}
g.fetch = async (url: any) => {
  const a = answer(String(url));
  return {
    ok: a.status < 400, status: a.status, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => a.body,
    text: async () => JSON.stringify(a.body),
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { SessionColumn } = await import("../../../../shell/SessionColumn");
const { FilesSheet } = await import("../../sheets/files");
const { fmtIntegration } = await import("../../../../../api/sessionStack");
const { fmtDuration } = await import("../../../../../lib/eta");
const { resetSessionDataForTests } = await import("../sessionData");
const { resetSessionStackStateForTests } = await import("../stackView");
const { subsPhrase } = await import("../LiveStack");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const textOf = (id: string): string => (byId(id)?.textContent ?? "") as string;

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const NOW = Date.now();

function seed(sequence: any, principal: unknown = OPERATOR): void {
  useStore.setState({
    principal,
    plan: clone(STORE_PLAN),
    equipConnected: true, wsPhase: "up", wsConnected: true, telemetryStale: false,
    wsLastEvent: NOW, lastCaptureAtMs: NOW, toasts: [],
    safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: false, ts: NOW / 1000 } },
    weather: null, resumeArm: null, focus: null, mountOp: null, lastAutofocusResult: null,
    guide: null, preview: { id: 4, hfr: 2.37 },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false, busy_lanes: ["capture"],
      disk: { free_gb: 210, low: false, critical: false },
      camera: {
        temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 100,
        cooler: { on: true, power: 42, target_c: -10, at_target: true, can_report_power: true },
      },
      meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
    },
    sequence,
    flows: { ...(useStore.getState() as any).flows, cards: [], libraryLoaded: true, libraryError: null },
  } as never);
}

/** Mount the column afresh over `sequence`: the session and stack caches are
 *  module-level, so each case starts from an unmounted tree and empty caches. */
async function mountColumn(sequence: any, principal: unknown = OPERATOR): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  resetSessionDataForTests();
  resetSessionStackStateForTests();
  await act(async () => { seed(sequence, principal); });
  await act(async () => { root.render(createElement(SessionColumn)); });
  await settle();
  assert(byId("session-column") != null, "no session-column marker: the fixture is wrong, not the component");
  assert(byId("now-run-header") != null, "the column drew no run header");
}

// ============================================== 1. a mosaic run, as served live
await testAsync("precondition: the live mosaic column rendered every block this file grades", async () => {
  await mountColumn(clone(SHOOTING));
  for (const id of ["now-target-line", "now-kind-line", "now-live-stack", "now-vitals", "now-integration"]) {
    assert(byId(id) != null, `no ${id} in the column`);
  }
});

// Mutant K ("kind from the store's Plan"; RunHeader's kind from
// usePlan().targets.length alone) turned this red, 8/13:
//   x the kind and the count are the state's group, not the store's Plan: the
//     kind line is not the mosaic's: "quick session · pass 3 · 3m 0s · finishes ~23:42"
// and with it the wrap case below (its first part is no longer "mosaic"), the
// viewer's wait ("the viewer's kind line: "quick session · 22m 30s · finishes
// ~21:40""), the control and the ended run.
await testAsync("the kind and the count are the state's group, not the store's Plan", async () => {
  await mountColumn(clone(SHOOTING));
  const gp = SHOOTING.group;
  eq(textOf("now-target-line"), `${SHOOTING.target} · one of ${gp.panels_total} panels`,
    "the target line does not read as a panel of the mosaic:");
  const kind = textOf("now-kind-line");
  assert(kind.startsWith("mosaic"), `the kind line is not the mosaic's: "${kind}"`);
  assert(kind.includes(`pass ${gp.pass}`), `the kind line lost the pass the engine published: "${kind}"`);
  assert(!/quick session|night plan/.test(kind), `a plan kind was read off something else: "${kind}"`);
});

// The kind line in the column sits beside the phase pill, about 218 px wide:
// "mosaic · pass 1 · 1m 13s · finishes ~19:43" does not fit, and an ellipsis
// there cut the finish time off, the part the line exists for (the page's
// text_intact, 2026-09-28: clipped 22px). So it wraps between its parts.
// Mutant L ("kind line cut"; the nowrap + ellipsis container back) turned this
// red, 12/13:
//   x the kind line wraps between its parts, and cuts none of them: the kind
//     line is cut to one line by DIV (white-space: nowrap; overflow: hidden;
//     text-overflow: ellipsis;)
await testAsync("the kind line wraps between its parts, and cuts none of them", async () => {
  await mountColumn(clone(SHOOTING));
  const line = byId("now-kind-line");
  for (let el = line; el && el !== byId("now-run-header"); el = el.parentElement) {
    assert(el.style.textOverflow !== "ellipsis" && el.style.whiteSpace !== "nowrap",
      `the kind line is cut to one line by ${el.tagName} (${el.getAttribute("style")})`);
  }
  const parts = [...line.querySelectorAll("span span")].map((sp: any) => sp.textContent);
  assert(parts.length >= 3 && parts[0] === "mosaic" && parts.some((p: string) => p.startsWith("finishes")),
    `the kind line's parts are not kept whole: ${JSON.stringify(parts)}`);
});

// Each of these turned it red, 12/13, as "x INTEGRATION is the session plan's
// shutter, every panel in full, against all it has banked: the INTEGRATION
// total: expected 6h 28m of 16h 0m planned", and then:
//   Mutant P ("planned from the store's Plan"):  got 4m of 10m planned
//   Mutant D ("panels deduped as a pool"):       got 2h 40m of 2h 40m planned
//   Mutant T ("banked tonight only"):            got 0s of 16h 0m planned
await testAsync("INTEGRATION is the session plan's shutter, every panel in full, against all it has banked", async () => {
  await mountColumn(clone(SHOOTING));
  eq(textOf("integration-total"), `${fmtIntegration(bankedS)} of ${fmtIntegration(plannedS)} planned`,
    "the INTEGRATION total:");
  assert(plannedS !== dedupedS, "the fixture cannot tell a pooled plan from a mosaic: the test is vacuous");
});

// The rule that makes a target a panel, and its CONTROL: the pool beside the
// mosaic keeps the pool cut. Added by the S7-UCOLUMN verifier, after the
// verifier's mutants D2, G and X each passed all thirteen cases above (none of
// them had a target outside the mosaic). Each turned this red, 15/16, as "x a
// mixed plan counts its panels in full and its pools once, and a panel dedupes
// nothing: the mixed plan's INTEGRATION total: expected 0s of 18h 20m planned",
// and then:
//   Mutant D2 ("every target in full"; no pool cut at all):  got 0s of 20h 0m planned
//   Mutant G  ("any mosaic_group is a panel"; the classic mosaic counted per
//             target):                                         got 0s of 19h 10m planned
//   Mutant X  ("a panel dedupes a pool"; the twin dropped as a copy of the
//             panel's step):                                   got 0s of 17h 40m planned
await testAsync("a mixed plan counts its panels in full and its pools once, and a panel dedupes nothing", async () => {
  const seq = clone(SHOOTING);
  seq.session = { ...seq.session, id: MIXED_ID };
  await mountColumn(seq);
  eq(textOf("integration-total"), `${fmtIntegration(0)} of ${fmtIntegration(MIXED_PLANNED_S)} planned`,
    "the mixed plan's INTEGRATION total:");
});

// Mutant W ("panel named across the wait"; the target line prints
// seq.target through a meridian wait) turned both of these red, 11/13:
//   x across a meridian wait the line names the mosaic and no panel
//     (operator): the target line across the wait:
//     expected M31 · 4 panels
//     got      M31 2-1 · 4 panels
// and the same two lines for the viewer.
// Mutant A ("pass across the wait") turned the operator's red, 12/13:
//   x across a meridian wait the line names the mosaic and no panel
//     (operator): the header names the panel or pass an operator is served
//     across the wait: "M31 · 4 panelsmosaic · pass 17 · 22m 30s · finishes
//     ~21:40WAITINGWaiting for the observing window to open."
// RE-PINNED BY H4-UMON (#488), deliberately: that quote's wait line was the
// defect #488 reports, the meridian wait worded as the window. Mutant A re-run
// then over the fixed formatter, in a private copy of ui/ (scratchpad
// H4-UMON-mut), observed (16/17; the finish clock is that run's wall clock):
//   x across a meridian wait the line names the mosaic and no panel
//     (operator): the header names the panel or pass an operator is served
//     across the wait: "M31 · 4 panelsmosaic · pass 17 · 22m 30s · finishes
//     ~12:04WAITINGWaiting for the meridian, so the mosaic changes pier side once."
await testAsync("across a meridian wait the line names the mosaic and no panel (operator)", async () => {
  await mountColumn(clone(WAIT_OPERATOR));
  const gp = WAIT_OPERATOR.group;
  eq(textOf("now-target-line"), `${gp.name} · ${gp.panels_total} panels`, "the target line across the wait:");
  const header = textOf("now-run-header");
  assert(!header.includes(gp.panel) && !/pass \d/.test(header),
    `the header names the panel or pass an operator is served across the wait: "${header}"`);
});
await testAsync("across a meridian wait the line names the mosaic and no panel (viewer)", async () => {
  await mountColumn(clone(WAIT_VIEWER), VIEWER);
  const gp = WAIT_VIEWER.group;
  eq(textOf("now-target-line"), `${gp.name} · ${gp.panels_total} panels`, "the target line across the wait:");
  assert(textOf("now-kind-line").startsWith("mosaic"), `the viewer's kind line: "${textOf("now-kind-line")}"`);
});

// A MERIDIAN WAIT READS AS ONE (#488; spec 5.7, 5.10, 6.9). Both recorded
// waits carry the engine's `schedule: {state: "waiting", reason: "the mosaic
// waits for the meridian, so it changes pier side once", eta_s: 0}`, beside a
// live meridian countdown. The header's wait line read "Waiting for the
// observing window to open." over them (the pre-#488 quote under mutant A
// above), with the window open. It now says the mosaic waits for the meridian,
// with no countdown and no clock, for an operator and a viewer alike.
// Added by H4-UMON (#488). Mutant FW ("fall through to the window sentence";
// scheduleStatus.ts's waitingText without its meridian branch, every reason
// that is not an altitude gate worded as the window, the pre-#488 code), run
// in a private copy of ui/ (scratchpad H4-UMON-mut) from a byte backup
// restored and hash-compared, observed (16/17):
//   x across a meridian wait the wait line says the mosaic waits for the
//     meridian, with no countdown and no clock: the operator's wait line
//     across the meridian wait:
//     expected Waiting for the meridian, so the mosaic changes pier side once.
//     got      Waiting for the observing window to open.
// and scheduleStatus.test.ts's mutant M ("meridian branch dropped"; the
// meridian reason printed as the server's words) turned it red the same way,
// 16/17, with "got      Waiting: the mosaic waits for the meridian, so it
// changes pier side once."
await testAsync("across a meridian wait the wait line says the mosaic waits for the meridian, with no countdown and no clock", async () => {
  const cases: Array<[string, any, unknown]> = [
    ["operator", WAIT_OPERATOR, OPERATOR],
    ["viewer", WAIT_VIEWER, VIEWER],
  ];
  for (const [who, state, principal] of cases) {
    assert(state.schedule?.state === "waiting" && /meridian/.test(state.schedule?.reason ?? ""),
      `precondition: the recorded ${who} wait no longer carries the engine's meridian schedule block`);
    await mountColumn(clone(state), principal);
    const line = textOf("now-wait-line");
    eq(line, "Waiting for the meridian, so the mosaic changes pier side once.",
      `the ${who}'s wait line across the meridian wait:`);
    assert(!/\d{1,2}:\d{2}|\babout\b|under a minute|\bmin\b|\bhr\b/.test(line),
      `a countdown or clock in the ${who}'s wait line: "${line}"`);
  }
});

// Before its first visit a group has no current panel: `panel` is null (spec
// 5.10), and the line names the mosaic, as across a wait. Derived from
// `states.shooting` with only `panel` nulled. Added by the S7-UCOLUMN verifier:
// mutant Q ("a null panel is named"; `panelNamed` without its panel check)
// passed all thirteen cases above, and turned this red, 15/16:
//   x before the first visit the line names the mosaic, not a panel: the
//     target line before the first visit:
//     expected M31 · 4 panels
//     got      M31 1-1 · one of 4 panels
await testAsync("before the first visit the line names the mosaic, not a panel", async () => {
  const seq = clone(SHOOTING);
  seq.group = { ...seq.group, panel: null };
  await mountColumn(seq);
  eq(textOf("now-target-line"), `${seq.group.name} · ${seq.group.panels_total} panels`,
    "the target line before the first visit:");
});

// CONTROL: a run outside a group is counted off its session exactly as before.
await testAsync("control: a run outside a group reads 'night plan' and '2 of 3' off its session", async () => {
  const seq = clone(SHOOTING);
  delete seq.group;
  seq.target = "NGC 891";
  seq.target_index = 1;
  seq.session = { ...seq.session, id: CONTROL_ID, name: "Autumn trio", target: "NGC 891" };
  seq.plan_name = "Autumn trio";
  await mountColumn(seq);
  eq(textOf("now-target-line"), "NGC 891 · 2 of 3", "the control's target line:");
  assert(textOf("now-kind-line").startsWith("night plan"), `the control's kind: "${textOf("now-kind-line")}"`);
});

// ================================================== 2. the same run, ended
// What the engine serves after the run stops, derived from `states.shooting`
// the way the engine derives it (engine.py): every terminal publish passes
// `session=None`, `group` goes with `_group_active` before it, and the
// terminal progress block carries counts and no exposure (measured on the
// real page, 2026-09-28: {frames_done, frames_total, percent, elapsed_s,
// rejected}). One sub banked, as in #468.
function endedFrom(live: any): any {
  const seq = clone(live);
  delete seq.group;
  delete seq.session;
  delete seq.schedule;
  delete seq.live;
  seq.state = "aborted";
  seq.end_reason = "aborted";
  seq.detail = "sequence aborted";
  seq.running = false;
  const p = live.progress;
  seq.progress = { frames_done: 1, frames_total: p.frames_total, percent: 1, elapsed_s: p.elapsed_s, rejected: 0 };
  return seq;
}

// Mutant K turned this red too:
//   x an ended run guesses no kind and names itself by the run's plan_name:
//     the ended kind line:
//     expected M31 mosaic · 3m 0s
//     got      quick session · 3m 0s
// Mutant E ("ended run unnamed"), 12/13: the same, got 3m 0s
await testAsync("an ended run guesses no kind and names itself by the run's plan_name", async () => {
  await mountColumn(endedFrom(SHOOTING));
  eq(textOf("now-target-line"), SHOOTING.target, "the ended target line:");
  // Exact: the run's own name and its clock, and no kind word before them
  // (the name itself may hold one - "M31 mosaic" does).
  eq(textOf("now-kind-line"), `${SHOOTING.plan_name} · ${fmtDuration(SHOOTING.progress.elapsed_s)}`,
    "the ended kind line:");
});

// The COUNT half of the same rule. The one-target decoy above cannot show a
// count read off the store's Plan, so this case seeds three targets under the
// run's own name. Added by the S7-UCOLUMN verifier: mutant N ("count from the
// store's Plan"; the old `runningTargetCount` name-match fallback put back
// for the target line) passed all thirteen cases above, and turned this red,
// 15/16:
//   x an ended run takes no count off the store's Plan, though it carries the
//     run's name: the ended target line:
//     expected M31 1-1
//     got      M31 1-1 · 1 of 3
// and the verifier's K2 (the old kind fallback: the session's count, else the
// store's Plan) turned its kind line red, 14/16 with the case above:
//     the ended kind line over a three-target store Plan:
//     expected M31 mosaic · 3m 0s
//     got      night plan · 3m 0s
await testAsync("an ended run takes no count off the store's Plan, though it carries the run's name", async () => {
  STORE_PLAN = DECOY_TRIO;
  try {
    await mountColumn(endedFrom(SHOOTING));
    eq(textOf("now-target-line"), SHOOTING.target, "the ended target line:");
    eq(textOf("now-kind-line"), `${SHOOTING.plan_name} · ${fmtDuration(SHOOTING.progress.elapsed_s)}`,
      "the ended kind line over a three-target store Plan:");
  } finally {
    STORE_PLAN = DECOY_PLAN;
  }
});

// Mutant U ("unpluralised") turned this red, 10/13 with the two below:
//   x with no ledger INTEGRATION counts subs, and the current line counts one
//     sub: the ended current line:
//     expected stopped · 1 sub
//     got      stopped · 1 subs
// Mutant S ("seconds without a ledger"), 12/13: the ended INTEGRATION total:
//     expected 1 of 120 subs
//     got      0s of 0s planned
// which is #468's own line, off the terminal progress block's missing exposure.
//
// The column's FILES button (RunControls, #468 item 4, finished by the S7
// integration: S7-UCOLUMN did not own RunControls.tsx) is graded here too.
// Mutant "FILES button unpluralised" (RunControls's label back to
// `FILES · {subs} SUBS`), observed in the integration's private copy
// (scratchpad S7-INTEG-mut), 15/16:
//   x with no ledger INTEGRATION counts subs, and the current line and the
//     FILES button count one sub: the column's FILES button:
//     expected FILES · 1 SUB
//     got      FILES · 1 SUBS
await testAsync("with no ledger INTEGRATION counts subs, and the current line and the FILES button count one sub", async () => {
  await mountColumn(endedFrom(SHOOTING));
  eq(textOf("integration-total"), `1 of ${SHOOTING.progress.frames_total} subs`,
    "the ended INTEGRATION total:");
  eq(textOf("live-stack-current"), "stopped · 1 sub", "the ended current line:");
  eq(textOf("now-files"), "FILES · 1 SUB", "the column's FILES button:");
});

// ================================================ 3. the FILES sheet counts
// Mutant U turned this red:
//   x the FILES sheet says '1 sub' on its summary and its DOWNLOAD button: the
//     FILES summary does not count one sub: "‹ BACKFILES · M31 mosaic ·
//     TONIGHT1 subs · 2m · 10.0 MB on the rigTONIGHTA PAST SESSION..."
await testAsync("the FILES sheet says '1 sub' on its summary and its DOWNLOAD button", async () => {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { seed(clone(SHOOTING), { ...OPERATOR, caps: [...OPERATOR.caps, "view.media"] }); });
  await act(async () => { root.render(createElement(FilesSheet as any, { params: { src: SESSION_ID }, depth: 0 })); });
  await settle();
  assert(byId("session-files") != null, "the FILES sheet did not render");
  const sheet = textOf("session-files");
  assert(sheet.includes(`1 sub · ${fmtIntegration(FIRST_STEP.exposure_s)}`),
    `the FILES summary does not count one sub: "${sheet.slice(0, 200)}"`);
  assert(/DOWNLOAD 1 SUB ·/.test(sheet), `the DOWNLOAD button does not count one sub: "${sheet.slice(0, 400)}"`);
  assert(!/\b1 subs\b|\b1 SUBS\b/.test(sheet), `"1 subs" is still on the sheet: "${sheet.slice(0, 400)}"`);
  eq(subsPhrase(2), "2 subs", "the plural (control):");
  eq(subsPhrase(1284), "1,284 subs", "the grouped plural (control):");
});

// =============================================== 4. the LIVE STACK's empty face
// Mutant C ("chips over the empty face") turned this red, 12/13:
//   x with nothing stacked no chip is drawn over the empty face, and the card
//     is in flow: a chip is drawn over the empty face: "LIVE STACK · 0 subs · 0s"
// Mutant F ("fixed-height empty frame"), 12/13: ... the empty frame has a
//     fixed height (180px), so a taller card is clipped
await testAsync("with nothing stacked no chip is drawn over the empty face, and the card is in flow", async () => {
  STACK = EMPTY_STACK;
  await mountColumn(clone(SHOOTING));
  const empty = byId("live-stack-empty");
  assert(empty != null, "the empty face is not up with nothing stacked: the case is vacuous");
  for (const id of ["live-stack-badge", "live-stack-mode"]) {
    assert(byId(id) == null, `a chip is drawn over the empty face: "${textOf(id)}"`);
  }
  const frame = byId("live-stack-frame");
  assert(frame != null && frame.contains(empty), "the empty card is not inside the stack's frame");
  for (let el = empty; el && el !== frame; el = el.parentElement) {
    assert(el.style.position !== "absolute",
      `the empty card is still laid over the frame (${el.tagName} is position: absolute)`);
  }
  assert(!frame.style.height && frame.style.minHeight !== "",
    `the empty frame has a fixed height (${frame.style.height}), so a taller card is clipped`);
});

// CONTROL: over a real picture the chips describe it, and count its one sub.
// Mutant U turned it red: the picture's badge:
//     expected LIVE STACK · 1 sub · 2m
//     got      LIVE STACK · 1 subs · 2m
await testAsync("control: over a stacked picture the chips stay, counting its one sub", async () => {
  STACK = ONE_SUB_STACK;
  await mountColumn(clone(SHOOTING));
  assert(byId("live-stack-image") != null, "no picture with one sub stacked");
  eq(textOf("live-stack-badge"), `LIVE STACK · 1 sub · ${fmtIntegration(120)}`, "the picture's badge:");
  STACK = EMPTY_STACK;
});

// ================================================== 5. the vitals wrap
// The column's content box and the wrap grid's floor, read from the sheets
// the browser gets, so the "two across" below is computed, not assumed.
const SHELL_CSS = readFileSync(new URL("../../../../shell/shell.css", import.meta.url), "utf8");
const NEXT_CSS = readFileSync(new URL("../../../../next.css", import.meta.url), "utf8");
function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`no rule for \`${selector}\``);
  return css.slice(at, css.indexOf("}", at));
}
const px = (block: string, prop: string): number => {
  const m = new RegExp(`(?:^|[\\s;{])${prop}:\\s*([\\d.]+)px`).exec(block);
  if (!m) throw new Error(`no ${prop} in px in: ${block}`);
  return Number(m[1]);
};

// Mutant V ("vitals scroll"; cells=4 back in the overflow-x flex row) turned
// this red, 12/13:
//   x the column's four vitals sit in the wrap grid, two across its content
//     box: the four tiles are not in the wrap grid
await testAsync("the column's four vitals sit in the wrap grid, two across its content box", async () => {
  await mountColumn(clone(SHOOTING));
  const band = byId("now-vitals");
  const grid = band?.querySelector(".nx-readouts.nx-readouts-wrap[data-cols='4']");
  assert(grid != null, "the four tiles are not in the wrap grid");
  const tiles = [...grid.children].map((c: any) => c.getAttribute("data-testid"));
  eq(tiles.join(","), "vital-eta,vital-guide,vital-sensor,vital-flip", "the grid's tiles, in order:");
  const scrollers = [...band.querySelectorAll("*")].filter((el: any) => el.style?.overflowX === "auto");
  eq(scrollers.length, 0, "a sideways scroller is still in the band:");

  const col = rule(SHELL_CSS, ".nx-session-col");
  const content = px(col, "width") - 2 * px(col, "padding");
  const wrap = rule(NEXT_CSS, ".nx-readouts-wrap[data-cols]");
  const floor = Number(/minmax\((\d+)px/.exec(wrap)?.[1]);
  const gap = px(rule(NEXT_CSS, ".nx-readouts"), "gap");
  const across = Math.floor((content + gap) / (floor + gap));
  eq(across, 2, `the wrap grid's ${floor}px floor in the column's ${content}px content box:`);
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`sessionColumnFlowRun.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
