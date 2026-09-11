// planEditorDom.test.tsx - the rebuilt PLAN EDITOR sheet, MOUNTED, with a
// stubbed rig.
//
//   Run directly:  npx tsx src/next/hubs/session/plan/__tests__/planEditorDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE FIVE PROMISES THIS SHEET MAKES THAT COULD SILENTLY BREAK
//
//   1. It rendered at all, with real content. Every assertion below is
//      worthless over a blank page, so the first test asserts the sheet marker
//      AND a target row AND the library AND the checklist - the
//      verify-on-the-real-thing guard.
//   2. START STARTS THE RUN. The one control that commits the rig for six
//      hours must reach `POST /api/sequence/start`, and it must carry the plan.
//      An armed button whose second tap does nothing looks identical to one
//      whose first tap did.
//   3. A VIEWER SEES THE SAME SCREEN, LOCKED, AND FIRES NOTHING. Not a hidden
//      button, not a native `disabled` that drops the reason out of the
//      accessibility tree: dim, `aria-disabled`, still focusable, and the
//      reason derived from `accessPhrase` rather than a hand-written role name.
//   4. A LIVE RUN LOCKS THE STRUCTURE AND SAYS SO. The note claims that nothing
//      here adds or removes a target or a step and that the catalog search is
//      off. A note describing a lock the code does not keep is worse than no
//      note, because it is what the user believes instead of looking.
//   5. THE PHONE DOOR IS HONEST. Below 768 px the sheet renders the reason and
//      no editor - and the reason is the SAME constant every other locked door
//      to this editor quotes.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// This area's root imports its own `plan.css` (wave R7's rule: `next.css`
// belongs to one task, every other area ships its own stylesheet), and the
// composed sections import theirs. Node has no idea what a `.css` file is, so a
// load hook answers with an empty module - the same stub `shellDom.test.tsx`
// uses for `next.css`.
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
  { url: "http://local/#/session/flows/planEditor", pretendToBeVisual: true },
);
const win = dom.window as any;

/** Flipped by the tests: the editor renders only at tablet and above, so a
 *  media stub that answers `false` to everything (the repo default) would test
 *  the phone door and nothing else. */
let WIDE = true;
win.matchMedia = (q: string) => ({
  matches: WIDE && /min-width:\s*768px/.test(q),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "URL", "URLSearchParams",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig

const asked: { url: string; method: string; body: any }[] = [];

const NIGHT = {
  date: "2026-09-10",
  transit_unix: 1_757_000_000,
  transit_alt: 62,
  transit_in_daylight: false,
  dark_start_unix: 1_756_990_000,
  dark_end_unix: 1_757_020_000,
  darkness_kind: "astronomical",
  samples: [
    { t_unix: 1_756_990_000, alt: 20, az: 90 },
    { t_unix: 1_757_000_000, alt: 62, az: 180 },
    { t_unix: 1_757_020_000, alt: 25, az: 270 },
  ],
  moon: { alt: -10, illum: 0.2, sep_deg: 90 },
  best_window: null,
  alt_limit_deg: 30,
  never_rises_above_limit: false,
};

/** One dormant session, so the ledger inside the editor really has a row and
 *  the viewer assertion on `plan-session-delete-s1` is not vacuous. */
const SESSION_ROW = {
  id: "s1", name: "NGC 7331 Ha", status: "dormant",
  created_ts: 1_757_000_000, updated_ts: 1_757_090_000,
  nights: 2, accepted: 31, total: 60, auto_resume: false,
};

const SESSION_DETAIL = {
  ...SESSION_ROW, schema_version: 1,
  nights: ["2026-09-08", "2026-09-09"],
  plan: {
    name: "NGC 7331 Ha",
    targets: [{
      id: "t1", name: "NGC 7331", ra_hours: 22.6, dec_deg: 34.4,
      center: true, autofocus_first: true, calibration: false,
      steps: [{
        id: "st1", filter: "Ha", exposure_s: 300, gain: 100, offset: 30,
        binning: 1, count: 60, frame_type: "Light",
      }],
    }],
  },
  frames: [],
};

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body ?? null; }
  asked.push({ url, method, body });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK", json: async () => data,
  });
  const miss = () => ({
    ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }),
  });

  if (url.startsWith("/api/plans")) return json([]);
  if (url.startsWith("/api/sequence/recoverable")) return json({ recoverable: false });
  if (url.endsWith("/api/sequence/start")) return json({ started: true });
  if (url.startsWith("/api/sequence/preflight")) return json({ verdict: "ok", alt: 55 });
  if (url.startsWith("/api/visibility")) return json(NIGHT);
  if (url.startsWith("/api/calibration/masters")) return json([]);
  if (url === "/api/sessions") return json({ sessions: [SESSION_ROW] });
  if (/^\/api\/sessions\/[^/]+$/.test(url)) return json(SESSION_DETAIL);
  if (url.startsWith("/api/sessions")) return json({ sessions: [] });
  if (url.startsWith("/api/reports")) return json([]);
  return miss();
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore, defaultSchedule } = await import("../../../../../store");
const { accessPhrase } = await import("../../../../../lib/caps");
const { resetRouterCacheForTests } = await import("../../../../router");
const { PlanEditorSheet, PLAN_EDITOR_PHONE_REASON } = await import("../../sheets/planEditor");
const { structureLockNote, resetTargetSparkCacheForTests } = await import("../index");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const all = (t: string): any[] =>
  Array.from(container.querySelectorAll(`[data-testid="${t}"]`));
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const text = (): string => container.textContent ?? "";
/** Open a `Disclosure` head, or leave it alone if it is already open: one group
 *  is open at a time and a second click on the open one CLOSES it, which would
 *  un-mount the very control the next assertion is about. */
const expand = (head: any) => {
  if (head && head.getAttribute("aria-expanded") !== "true") click(head);
};

const CAPS_ADMIN = ["view.status", "view.preview", "view.media", "control.mount", "control.capture"];
const CAPS_VIEWER = ["view.status", "view.preview"];

/** A rig that passes every blocking check: camera and mount connected, no
 *  safety monitor (skipped), no guiding, no cooling. The horizon row lands on
 *  `disabled` because the site is default, which is a WARN and not a block -
 *  exactly the state a fresh rig is in. */
const READY_STATUS = {
  connected: {
    camera: { connected: true },
    telescope: { connected: true },
  },
  mount: { parked: false, ra_hours: 0.7, dec_deg: 41 },
  filterwheel: { position: 0, names: ["L", "Ha", "Dark"], opaque: [false, false, true] },
};

const PLAN = {
  name: "M31 LRGB",
  guide: false,
  dither_every: 0,
  dither_pixels: null,
  autofocus_every: 0,
  cool_to: null,
  cool_timeout_s: null,
  apply_filter_offsets: null,
  refocus_on_temp_delta_c: null,
  meridian_flip: false,
  recover_guiding: null,
  hfr_reject_factor: null,
  park_when_done: false,
  warm_cooler_when_done: false,
  safety_check: false,
  targets: [{
    id: "t1",
    name: "M31",
    ra_hours: 0.712,
    dec_deg: 41.27,
    center: true,
    autofocus_first: false,
    calibration: false,
    schedule: defaultSchedule(),
    steps: [{
      id: "st-L", filter: "L", exposure_s: 120, gain: 100, offset: 10,
      binning: 1, count: 20, frame_type: "Light",
    }],
  }],
};

function seed(caps: string[], sequence: unknown): void {
  act(() => {
    useStore.setState({
      principal: { role: caps.includes("control.mount") ? "admin" : "viewer", email: null, caps },
      status: READY_STATUS,
      equipConnected: true,
      wsPhase: "up",
      plan: JSON.parse(JSON.stringify(PLAN)),
      sequence,
      site: null,
      masters: [],
      toasts: [],
    } as never);
  });
}

async function mount(params: Record<string, string> = {}): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  resetTargetSparkCacheForTests();
  await act(async () => {
    root.render(createElement(PlanEditorSheet as any, { params, depth: 0 }));
  });
  await settle();
}

// ============================================== 1. it rendered, with content

WIDE = true;
seed(CAPS_ADMIN, { state: "idle" });
await mount();

await testAsync("precondition: the sheet, a target row, the library and the checks all rendered",
  async () => {
    assert(tid("session-plan-editor") != null,
      "no session-plan-editor marker - the probe route and every assertion below are vacuous");
    assert(tid("plan-editor-body") != null, "the editor body never rendered");
    assert(tid("plan-targets") != null, "the targets panel never rendered");
    eq(all("plan-target-row").length, 1, "the one target in the plan did not render a row");
    assert(tid("plan-library") != null, "the plan library never rendered");
    assert(tid("plan-preflight") != null, "the pre-flight checks never rendered");
    assert(tid("plan-start") != null, "START never rendered");
    assert(/M31/.test(text()), "the target's name never reached the screen");
    // The plan name is an editable field, so it lives in `value`, not in text.
    eq(tid("plan-name").value, "M31 LRGB", "the plan's name never reached its field");
  });

await testAsync("the three sections T-R7-6 owns are really composed, not stubbed", async () => {
  // The composition contract, asserted rather than assumed: a root that imports
  // three index files but renders none of them compiles, ships, and silently
  // loses the automation column, the when/then rules and the session ledger -
  // which is the entire right-hand column of the legacy editor.
  assert(tid("plan-automation") != null,
    "PlanAutomationSection is not on screen - the twenty automation controls are lost");
  assert(tid("plan-instructions") != null,
    "PlanInstructionsSection is not on screen - the when/then rules are lost");
  assert(tid("plan-sessions") != null,
    "PlanSessionsSection is not on screen - the past-sessions ledger is lost");
});

await testAsync("the step row carries the editable fields, not a read-only summary", async () => {
  const step = tid("plan-step-row");
  assert(step != null, "no step row rendered");
  eq(tid("plan-step-exposure-0").value, "120", "the exposure field does not hold the step's value");
  eq(tid("plan-step-count-0").value, "20", "the count field does not hold the step's value");
  // A blackout slot is a light block, not a filter, so it is never offered.
  assert(tid("plan-step-filter-0-L") != null, "the wheel's L filter is not offered");
  assert(tid("plan-step-filter-0-Dark") == null,
    "a blackout slot was offered as a filter choice");
});

// ================================================ 2. START starts the run

await testAsync("START posts /api/sequence/start with the plan, on the second tap", async () => {
  const before = asked.filter((a) => a.url.endsWith("/api/sequence/start")).length;
  const start = tid("plan-start");
  assert(start.getAttribute("aria-disabled") == null,
    `START must be live for an admin on a ready rig, reason: "${start.getAttribute("title")}"`);
  // Two taps: the design's confirm for a control that commits the rig for the
  // night. The FIRST must not fire.
  click(start);
  await settle();
  eq(asked.filter((a) => a.url.endsWith("/api/sequence/start")).length, before,
    "the first tap of an armed START fired the run");
  click(tid("plan-start"));
  await settle();
  const posts = asked.filter((a) => a.url.endsWith("/api/sequence/start"));
  eq(posts.length, before + 1, "the second tap did not reach POST /api/sequence/start");
  eq(posts[posts.length - 1].method, "POST", "the start must be a POST");
  eq(posts[posts.length - 1].body?.name, "M31 LRGB",
    "the start body must carry the plan the screen is showing");
});

// ========================================= 3. a viewer sees it, locked, silent

await testAsync("a viewer sees START honest-disabled with the derived phrase and fires nothing",
  async () => {
    seed(CAPS_VIEWER, { state: "idle" });
    await settle();
    const start = tid("plan-start");
    assert(start != null, "a viewer must see the same screen, not one with START removed");
    eq(start.tagName, "BUTTON", "START must stay a real button for a viewer");
    eq(start.getAttribute("aria-disabled"), "true",
      "START must be honest-disabled for a viewer, never absent");
    eq(start.hasAttribute("disabled"), false,
      "the native disabled attribute drops the reason out of the accessibility tree");
    const phrase = accessPhrase("control.mount");
    assert((start.getAttribute("title") ?? "").includes(phrase),
      `the reason must come from accessPhrase, got "${start.getAttribute("title")}"`);
    assert(tid("plan-view-only-badge") != null, "the VIEW ONLY badge never rendered");
    assert(tid("plan-start-lock") != null, "the lock note never rendered");
    assert(new RegExp(phrase).test(tid("plan-start-lock").textContent ?? ""),
      "the lock note must quote the same phrase the button does");

    const before = asked.filter((a) => a.url.endsWith("/api/sequence/start")).length;
    click(start);
    await settle();
    click(tid("plan-start"));
    await settle();
    eq(asked.filter((a) => a.url.endsWith("/api/sequence/start")).length, before,
      "a locked press must not reach the network, armed or not");
  });

// THE COMPOSED MOUNT, not the sections on their own. Each of the three sections
// has its own DOM test, and each of those handed the section a `lockedReason` -
// so what was graded was a string the test supplied. The PRODUCTION caller is
// this file's `PlanEditorSheet`, and it passed `lockedReason={null}` to all
// three: a viewer got a live twenty-control automation column, a live when/then
// rule editor and five live destructive session verbs, and learned the rule from
// a 403 (or, for the draft-only halves, never - the refusal arrived at START
// after the editing was done).
//
// SABOTAGE: put `lockedReason={null}` back on any of the three sections in
// `PlanEditor.tsx`, or drop `useCanControlMount()` from `PlanSessionsSection`,
// and one of the three assertions below goes red.
await testAsync("a viewer's composed editor locks all three sections, not just START",
  async () => {
    const phrase = accessPhrase("control.mount");
    // Disclosures do not mount their children while closed, so each section is
    // opened before its control is looked for. A missing element here is a
    // failure, not a pass.
    const heads = Array.from(container.querySelectorAll(
      '[data-testid="plan-automation-group"] button[aria-expanded]')) as any[];
    assert(heads.length > 0, "no automation groups rendered - the assertions below are vacuous");
    expand(heads[0]);
    const guide = tid("plan-guard-guide");
    assert(guide != null, "the guiding switch never rendered inside the composed editor");
    eq(guide.getAttribute("aria-disabled"), "true",
      "a viewer can toggle GUIDE in the plan editor: the automation section was "
      + "mounted with no reason at all");
    assert((guide.getAttribute("title") ?? "").includes(phrase),
      `the automation lock must quote accessPhrase, got "${guide.getAttribute("title")}"`);

    const rules = tid("plan-instructions-rules");
    assert(rules != null, "the when/then rules section never rendered");
    expand(rules.querySelector("button[aria-expanded]"));
    const add = tid("plan-instructions-add");
    assert(add != null, "ADD RULE never rendered");
    eq(add.getAttribute("aria-disabled"), "true",
      "a viewer can add a conditional rule to the plan draft with nothing to save it with");

    const del = tid("plan-session-delete-s1");
    assert(del != null,
      "the session row never rendered - the ledger fixture is wrong, not the lock");
    eq(del.getAttribute("aria-disabled"), "true",
      "a viewer's DELETE on a multi-night session ledger is LIVE and reaches "
      + "DELETE /api/sessions/s1");
    eq(del.hasAttribute("disabled"), false,
      "the native disabled attribute drops the reason out of the accessibility tree");
    assert((del.getAttribute("title") ?? "").includes(phrase),
      `the session verb's reason must come from accessPhrase, got "${del.getAttribute("title")}"`);
  });

// ...and the same three are LIVE for an operator, so the assertions above are
// grading a lock rather than three controls that are always dim.
await testAsync("an admin's composed editor leaves all three sections live", async () => {
  seed(CAPS_ADMIN, { state: "idle" });
  await settle();
  const heads = Array.from(container.querySelectorAll(
    '[data-testid="plan-automation-group"] button[aria-expanded]')) as any[];
  expand(heads[0]);
  eq(tid("plan-guard-guide").getAttribute("aria-disabled"), null,
    "GUIDE is locked for an admin on an idle rig");
  expand(tid("plan-instructions-rules").querySelector("button[aria-expanded]"));
  eq(tid("plan-instructions-add").getAttribute("aria-disabled"), null,
    "ADD RULE is locked for an admin on an idle rig");
  eq(tid("plan-session-delete-s1").getAttribute("aria-disabled"), null,
    "DELETE is locked for an admin on a dormant session");
});

// ================================= 4. a live run locks the structure and says so

await testAsync("a running sequence prints the structure lock and freezes every structural control",
  async () => {
    seed(CAPS_ADMIN, {
      state: "running",
      plan_name: "M31 LRGB",
      target: "M31",
      target_index: 0,
      progress: {
        frames_done: 3, frames_total: 20, percent: 15, elapsed_s: 600, rejected: 0,
        current_exposure_s: 120, frame_started_at_ms: null, server_now_ms: null,
        eta_s: null, eta_confident: true,
      },
    });
    await settle();

    const note = tid("plan-structure-lock");
    assert(note != null, "the run's structure-lock note never rendered");
    // The wording is the ONE copy in planModel, not a second sentence here.
    eq(note.textContent, `Read-only - ${structureLockNote("M31 LRGB")}`,
      "the note on screen is not the one sentence planModel holds");

    // Every clause of that note is enforced.
    assert(tid("plan-search-locked") != null,
      "the catalog search is still live during a run - the note claims it is off");
    eq(tid("plan-add-step").getAttribute("aria-disabled"), "true",
      "ADD STEP is live during a run - the note claims no step can be added");
    eq(tid("plan-target-delete").getAttribute("aria-disabled"), "true",
      "DELETE is live during a run - the note claims no target can be removed");
    eq(tid("plan-target-schedule").getAttribute("aria-disabled"), "true",
      "per-target scheduling is live during a run - the note claims it is frozen");
    eq(tid("plan-order-tonight").getAttribute("aria-disabled"), "true",
      "reordering is live during a run");

    // ...and the half that stays editable really does.
    eq(tid("plan-step-exposure-0").hasAttribute("readonly"), false,
      "exposure was frozen; the note says it stays editable and applies to the next run");

    // The run header is up, with the state as a WORD.
    assert(tid("plan-run-header") != null, "the run header never rendered over a live run");
    assert(/RUNNING/.test(text()), "the run state never reached the screen as a word");
    assert(tid("plan-stop") != null, "STOP never rendered over a live run");
    // And START says which run is in the way, by name.
    assert(/M31 LRGB/.test(tid("plan-start").getAttribute("title") ?? ""),
      `START's reason must name the run that is blocking it, got "${tid("plan-start").getAttribute("title")}"`);
  });

// ==================================== 5. the phone door, and only the phone door

await testAsync("on a phone the sheet states the reason and renders no editor", async () => {
  WIDE = false;
  seed(CAPS_ADMIN, { state: "idle" });
  await mount();
  assert(tid("session-plan-editor") != null, "the sheet itself must still render on a phone");
  assert(tid("plan-editor-phone") != null, "the phone door never rendered");
  assert(text().includes(PLAN_EDITOR_PHONE_REASON),
    "the phone door must quote the same constant every other locked door to this editor uses");
  assert(tid("plan-editor-body") == null,
    "the dense editor rendered on a 390 px screen - that is how a quota is set on the wrong plan");
  assert(tid("plan-start") == null,
    "START rendered on a phone, where nothing above it can be reviewed");
});

// ============================ 6. the schedule layer is its own sheet, by route

await testAsync("the schedule param opens the per-target editor as a whole sheet", async () => {
  WIDE = true;
  seed(CAPS_ADMIN, { state: "idle" });
  await mount({ schedule: "t1" });
  assert(tid("session-plan-schedule") != null, "the schedule layer never rendered");
  assert(/M31/.test(text()), "the schedule layer must say which target it is editing");
  assert(tid("session-plan-editor") == null,
    "the editor rendered underneath - a sheet inside a sheet gives two headers and two BACKs");
  assert(tid("plan-schedule-start-mode") != null, "the start-mode control never rendered");
  assert(tid("plan-schedule-min-alt") != null, "the minimum-altitude gate never rendered");
  assert(tid("plan-schedule-on-missed") != null, "the missed-window choice never rendered");
});

act(() => { root.unmount(); });
resetRouterCacheForTests();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`planEditorDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
