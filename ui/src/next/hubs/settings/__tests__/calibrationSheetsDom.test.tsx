// calibrationSheetsDom.test.tsx - Settings > MORE > CALIBRATION and > SKY ATLAS
// OFFLINE PACK after the rebuild (wave R7, T-R7-14; plan sections 3.F15, 3.F16,
// 3.F17), MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/calibrationSheetsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// FIVE THINGS THIS FILE HOLDS, EACH OF WHICH IS A DEFECT IF IT DRIFTS.
//
//   1. The sheets render the REBUILT editors, not a header. A marker test that
//      names one element passes on a page that drew a title and nothing else,
//      so each sheet is asked for its own controls by name - including all five
//      tolerance fields, because losing one silently is exactly the failure
//      mode a 77-component restyle has.
//   2. REBUILD posts ONCE and the three numbers it answers with stay on screen.
//      The legacy panel put them in a toast that left after 2.8 s; a rebuild
//      that found nothing to do is indistinguishable from one that rebuilt
//      everything unless those numbers are readable.
//   3. DELETING A MASTER GOES THROUGH THE CONFIRM, and nothing is sent while
//      the confirm is open or after it is cancelled. A destructive route that
//      fires before the answer is the one bug a confirm exists to prevent.
//   4. THE PACK PROGRESS BAR IS ABSENT BEFORE THE FIRST STATUS - not a bar at
//      0 %. A 0 % bar says "a download is running and has achieved nothing",
//      which on a cold open is false in both halves.
//   5. A VIEWER SEES EVERY CONTROL, LOCKED, WITH THE REASON, AND FIRES NOTHING.
//      Both capabilities are asserted by name (`control.capture` for the
//      library, `config.site_optics` for the tolerances and the pack), because
//      naming the wrong role in a lock note is the defect `accessPhrase()`
//      exists to prevent.
//
// Convention: jsdom by hand, createRoot + act, native events, a recording fetch
// stub, store seeding via useStore.setState, printed tally + the
// `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The calibration area's root (`tuning/calibration/index.ts`) imports
// `calibration.css`, which Node cannot load. The same synchronous hook
// `shellDom.test.tsx` installs answers with an empty module. It has to run
// BEFORE the first `await import` of anything that reaches that module, which
// is why every import in this file is dynamic.
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
  { url: "http://local/#/settings/general/calibration", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (qs: string) => ({
  matches: false, media: qs,
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  // NOT "performance": this jsdom's Performance delegates to the global one, so
  // copying it onto globalThis makes performance.now() call itself until the
  // stack blows (primitivesDom.test.tsx's note).
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
const asked: string[] = [];
const posts: { url: string; method: string; body: unknown }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

const MASTERS = [
  {
    id: "m-dark-300", frame_type: "Dark", exposure_s: 300, gain: 100, offset: 30,
    temp_c: -10, binning: 1, filter: "", frame_count: 20, built_ts: 1_757_000_000,
  },
  {
    id: "m-flat-L", frame_type: "Flat", exposure_s: 2, gain: 100, offset: 30,
    temp_c: null, binning: 1, filter: "L", frame_count: 30, built_ts: 1_757_000_100,
  },
];
const REPORT = { masters_built: 2, frames_indexed: 50, buckets: 3 };

const PACK_IDLE = {
  present: false, slug: "", survey: "", order: null, bytes: null,
  tile_count: null, fetched_at: null, fetching: null,
};
const PACK_FETCHING = {
  ...PACK_IDLE, fetching: { done: 3, total: 10, failed: 0 },
};
const PACK_PRESENT = {
  present: true, slug: "DSS2", survey: "DSS2", order: 4, bytes: 262_144_000,
  tile_count: 3072, fetched_at: 1_756_000_000, fetching: null,
};

// `let`, not `const`: the tests swap these to drive the panels' tri-states.
// `g.fetch` closes over the BINDINGS, so a swap is seen on the next request.
let PACK: any = PACK_IDLE;
let MASTERS_RESPONSE: { status: number; body: unknown } = { status: 200, body: MASTERS };

// Mirrors whatever the test seeded, so a panel's own `loadConfig()` re-GET of
// `/api/config` reads back the shape it started from rather than wiping it.
let currentConfig: any = null;

const fail = (status: number, detail: string) => ({
  ok: false, status, statusText: "Error", json: async () => ({ detail }),
});

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (init?.body != null) {
    let body: unknown = init.body;
    try { body = JSON.parse(init.body); } catch { /* leave as text */ }
    posts.push({ url: u, method, body });
  } else if (method !== "GET") {
    posts.push({ url: u, method, body: undefined });
  }
  // Longest paths first: "/api/survey/pack/fetch" contains "/api/survey/pack".
  if (u.includes("/api/survey/pack/fetch")) return ok({ started: true });
  if (u.includes("/api/survey/pack")) {
    return method === "DELETE" ? ok({ deleted: true }) : ok(PACK);
  }
  if (u.includes("/api/config/survey")) return ok(currentConfig ?? {});
  if (u.includes("/api/config/calibration")) return ok(currentConfig ?? {});
  if (u.includes("/api/calibration/build")) return ok(REPORT);
  if (u.includes("/api/calibration/masters/")) return ok({ deleted: true });
  if (u.includes("/api/calibration/masters")) {
    return MASTERS_RESPONSE.status === 200
      ? ok(MASTERS_RESPONSE.body)
      : fail(MASTERS_RESPONSE.status, String(MASTERS_RESPONSE.body));
  }
  if (u.includes("/api/config")) return ok(currentConfig ?? {});
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");
const { masterRowSummary } = await import("../../../../lib/calibrationLibrary");

const { CalibrationSheet } = await import("../sheets/CalibrationSheet");
const { SkyPackSheet } = await import("../sheets/SkyPackSheet");
const {
  buildReportLine, binWarning, groupHeading, libraryLoadError, PACK_RETRY_HINT,
  TOL_BIN_REASON, TOL_CLEAN_REASON, TOLERANCE_FIELDS,
} = await import("../tuning/calibration");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
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

const SP: { params: Record<string, string>; depth: 0 | 1 } = { params: {}, depth: 0 };

function mount(Comp: (p: typeof SP) => any): { host: any; root: any } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(createElement(Comp, SP)); });
  return { host, root };
}
function unmount(m: { host: any; root: any }): void {
  act(() => { m.root.unmount(); });
  m.host.remove();
}

const q = (host: any, sel: string): any => host.querySelector(sel);
const qa = (host: any, sel: string): any[] => Array.from(host.querySelectorAll(sel));
const tid = (host: any, name: string): any => q(host, `[data-testid="${name}"]`);
const tids = (host: any, name: string): any[] => qa(host, `[data-testid="${name}"]`);

/** Type into a `NumberField` without committing: it holds its own text and
 *  parses only at blur or Enter, which is the whole point of the primitive. */
const typeInto = async (el: any, text: string): Promise<void> => {
  await act(async () => {
    el.value = text;
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};

/** Blur it for real: React 18 delegates `onBlur` from the bubbling `focusout`,
 *  not from the non-bubbling `blur`, so a `blur` event reaches no handler and a
 *  test that dispatched one would grade nothing. */
const blur = async (el: any): Promise<void> => {
  await act(async () => {
    el.dispatchEvent(new win.Event("focusout", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
};

const click = async (el: any): Promise<void> => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
  await settle();
};

const CALIBRATION = {
  exposure_tol_pct: 5, temp_tol_c: 2, temp_bin_c: 5, stack_sigma: 3, max_stack_frames: 100,
};

const ADMIN = {
  role: "admin", email: "admin@example.test",
  caps: [
    "view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather",
    "control.capture", "control.mount", "control.guide", "control.power",
    "config.safety", "config.solar_override", "config.backend",
    "config.site_optics", "config.alerts", "admin.users", "system.update",
  ],
};
const VIEWER = { role: "viewer", email: "viewer@example.test", caps: ["view.status", "view.preview"] };

function seed(principal: unknown, over: Record<string, unknown> = {}): void {
  const config = {
    version: 1, calibration: CALIBRATION, survey: { online_fetch: false },
    ...((over.config as object | undefined) ?? {}),
  };
  currentConfig = config;
  useStore.setState({
    principal,
    // `wsPhase: "up"` matters: `lockReason()` answers "the rig is not
    // reachable" FIRST, and a test that left the link down would grade that
    // sentence instead of the capability one it means to.
    wsPhase: "up",
    wsConnected: true,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    masters: [],
    confirm: null,
    toasts: [],
    config,
    ...over,
  } as never);
}
const seedAdmin = (over: Record<string, unknown> = {}) => seed(ADMIN, over);
const seedViewer = (over: Record<string, unknown> = {}) => seed(VIEWER, over);

const reset = (): void => { asked.length = 0; posts.length = 0; };
const count = (needle: string): number => asked.filter((a) => a.includes(needle)).length;

// ========================================================== 1. PRECONDITIONS
// Vacuity guards. Each sheet is asked for the controls the rebuild owes, by
// name. Sabotage: unmount either editor from its sheet and the corresponding
// half goes red rather than a title-only page passing.

await test("CalibrationSheet: marker, both rebuilt editors, and all five tolerance fields", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  assert(tid(m.host, "settings-calibration") != null, "no settings-calibration marker");
  assert(tid(m.host, "cal-library") != null, "the rebuilt master library is not mounted");
  assert(tid(m.host, "cal-build") != null, "no cal-build button");
  assert(tid(m.host, "cal-tolerances") != null, "the rebuilt tolerances editor is not mounted");
  for (const f of TOLERANCE_FIELDS) {
    assert(tid(m.host, `cal-tolerance-${f.key}`) != null,
      `tolerance field ${f.key} is missing from the rebuild`);
  }
  eq(TOLERANCE_FIELDS.length, 5, "the parity table lost a tolerance field");
  // The legacy panels are NOT mounted any more: their `Panel` chrome would
  // bring `.panel-title` with it.
  eq(qa(m.host, ".panel-title").length, 0, "a legacy Panel is still mounted inside the sheet");
  unmount(m);
});

await test("CalibrationSheet: the masters GET lands in the shared slice and renders grouped rows", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  eq(count("GET /api/calibration/masters"), 1, "the library did not fetch the masters exactly once");
  eq(tids(m.host, "cal-master-row").length, 2, "the two masters did not render as rows");
  assert(m.host.textContent.includes(groupHeading("Dark", 1)), "no DARKS (1) group heading");
  assert(m.host.textContent.includes(groupHeading("Flat", 1)), "no FLATS (1) group heading");
  assert(m.host.textContent.includes(masterRowSummary(MASTERS[0] as never)),
    "the dark's summary line (exposure, gain, temperature, binning, count) is not on screen");
  eq((useStore.getState() as any).masters.length, 2,
    "the masters did not land in the SHARED store slice the pre-flight row reads");
  unmount(m);
});

await test("SkyPackSheet: marker, the online toggle, the status line and the download button", async () => {
  PACK = PACK_IDLE;
  seedAdmin();
  reset();
  const m = mount(SkyPackSheet);
  await settle();
  assert(tid(m.host, "settings-skyPack") != null, "no settings-skyPack marker");
  assert(tid(m.host, "pack-online") != null, "no online-survey-fetch toggle");
  assert(tid(m.host, "pack-status") != null, "no pack status readout");
  assert(tid(m.host, "pack-download") != null, "no download button");
  assert(/DSS2 imagery/.test(m.host.textContent), "the DSS2 attribution line is not rendered");
  eq(qa(m.host, ".panel-title").length, 0, "the legacy SkyAtlasPanel is still mounted");
  unmount(m);
});

// ================================================================== 2. BUILD
// Sabotage: drop `setReport(rep)` (or render the toast instead) and the report
// assertion goes red; remove the `if (building) return` guard and re-press, and
// the "exactly once" count goes to 2.

await test("BUILD posts once and renders the report the server returned", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  assert(tid(m.host, "cal-build-report") == null, "a build report is on screen before any build");

  await click(tid(m.host, "cal-build"));

  eq(count("POST /api/calibration/build"), 1, "BUILD did not post exactly once");
  const line = tid(m.host, "cal-build-report");
  assert(line != null, "the build report is not on screen after a build");
  eq(line.textContent, buildReportLine(REPORT as never),
    "the build report does not carry the numbers the server returned");
  // The three numbers are the point: a rebuild that found nothing must not read
  // the same as one that rebuilt everything.
  assert(/2 masters/.test(line.textContent) && /50 frames/.test(line.textContent)
    && /3 matching groups/.test(line.textContent),
    "the report dropped one of the three numbers the route answers with");
  // And it refetched, so the list on screen is what the build produced.
  eq(count("GET /api/calibration/masters"), 2, "BUILD did not refresh the library afterwards");
  unmount(m);
});

// ================================================================= 3. DELETE
// Sabotage: replace `if (!ok) return;` with an unconditional delete and the
// "nothing sent on cancel" assertion goes red; delete before awaiting the
// confirm and the "nothing sent while the confirm is open" one goes red too.

await test("deleting a master goes through the confirm, and only posts on confirm", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  const del = () => tids(m.host, "cal-master-delete")[0];

  // Press: a confirm is raised and NOTHING is sent yet.
  await click(del());
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "deleting a master did not raise a confirm");
  eq(req.title, "Delete this master?", "the confirm does not name what it is about to delete");
  assert(String(req.body).includes(masterRowSummary(MASTERS[0] as never)),
    "the confirm body does not identify WHICH master");
  eq(count("DELETE /api/calibration/masters/"), 0,
    "the DELETE fired while the confirm was still open");

  // KEEP: still nothing.
  await act(async () => { (useStore.getState() as any).resolveConfirm(false); });
  await settle();
  eq(count("DELETE /api/calibration/masters/"), 0, "cancelling the confirm still deleted the master");

  // Confirm: exactly one DELETE, on the right id, then a refresh.
  await click(del());
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  eq(count("DELETE /api/calibration/masters/"), 1, "confirming did not delete exactly once");
  assert(asked.some((a) => a === "DELETE /api/calibration/masters/m-dark-300"),
    `the DELETE did not carry the row's id - asked: ${asked.join(" | ")}`);
  unmount(m);
});

// ============================================================ 4. PACK STATUS
// Sabotage: render the bar unconditionally with `packProgressPct(f ?? {done:0,
// total:0,failed:0})` and the "no bar before the first status" assertion goes
// red - which is the whole point, because a 0 % bar on a cold open claims a
// download is running.

await test("the pack progress bar renders nothing before the first status, not 0 per cent", async () => {
  PACK = PACK_IDLE;
  seedAdmin();
  reset();
  const m = mount(SkyPackSheet);
  // NO settle: this is the frame between mount and the first
  // `GET /api/survey/pack` answering.
  assert(tid(m.host, "pack-progress") == null,
    "a progress bar rendered before the server had reported any fetch");
  assert(/checking/.test(tid(m.host, "pack-status").textContent),
    "the status line does not say it is still checking");

  await settle();
  assert(tid(m.host, "pack-progress") == null,
    "a progress bar rendered for a status with no fetch in flight");
  unmount(m);
});

await test("a fetch in flight renders the bar at the server's own percentage", async () => {
  PACK = PACK_FETCHING;
  seedAdmin();
  reset();
  const m = mount(SkyPackSheet);
  await settle();
  const bar = tid(m.host, "pack-progress");
  assert(bar != null, "no progress bar while a fetch is in flight");
  eq(bar.getAttribute("aria-valuenow"), "30", "the bar is not at 3 of 10 tiles");
  eq(bar.getAttribute("role"), "progressbar", "the bar does not announce as a progressbar");
  assert(/3\/10/.test(tid(m.host, "pack-status").textContent),
    "the status line does not carry the tile counts the bar draws");
  // A second download must not start while one runs, and it must say why.
  const dl = tid(m.host, "pack-download");
  eq(dl.getAttribute("aria-disabled"), "true", "download is pressable while a fetch runs");
  assert(/already running/.test(String(dl.getAttribute("title"))),
    "the locked download does not name the running fetch as the reason");
  await click(dl);
  eq(count("POST /api/survey/pack/fetch"), 0, "a locked download still started a second fetch");
  unmount(m);
  PACK = PACK_IDLE;
});

await test("download and delete reach their routes, and delete goes through the confirm", async () => {
  PACK = PACK_PRESENT;
  seedAdmin();
  reset();
  const m = mount(SkyPackSheet);
  await settle();
  assert(/Update offline sky pack/i.test(tid(m.host, "pack-download").textContent),
    "a pack already on disk still offers a first download rather than an update");

  await click(tid(m.host, "pack-download"));
  eq(count("POST /api/survey/pack/fetch"), 1, "the download did not post exactly once");

  reset();
  await click(tid(m.host, "pack-delete"));
  eq(count("DELETE /api/survey/pack"), 0, "the pack was deleted before the confirm was answered");
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  eq(count("DELETE /api/survey/pack"), 1, "confirming did not delete the pack exactly once");
  unmount(m);
  PACK = PACK_IDLE;
});

await test("the online-fetch toggle writes the survey config once", async () => {
  PACK = PACK_IDLE;
  seedAdmin();
  reset();
  const m = mount(SkyPackSheet);
  await settle();
  await click(tid(m.host, "pack-online"));
  eq(count("POST /api/config/survey"), 1, "the toggle did not write the survey config exactly once");
  const body = posts.find((p) => p.url.includes("/api/config/survey"))?.body as any;
  eq(body?.online_fetch, true, "the toggle sent the wrong value");
  unmount(m);
});

// ============================================================= 5. TOLERANCES
// Sabotage: send `Number(input.value)` per keystroke instead of `NumberField`'s
// blur commit and the "no write while typing" assertion goes red; drop the
// `binTooNarrow` guard and both the warning and the locked-SAVE assertions go
// red.

await test("a tolerance commits at blur and SAVE writes the whole block once", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  const box = tid(m.host, "cal-tolerance-temp_tol_c");
  assert(box != null, "no temperature-tolerance field");

  await typeInto(box, "3.5");
  eq(count("POST /api/config/calibration"), 0, "typing wrote to the rig before the field was left");

  await blur(box);
  eq(count("POST /api/config/calibration"), 0, "a blur wrote to the rig before SAVE was pressed");
  eq(box.value, "3.5", "the committed number is not the one left in the box");

  await click(tid(m.host, "cal-save"));
  eq(count("POST /api/config/calibration"), 1, "SAVE did not write exactly once");
  const body = posts.find((p) => p.url.includes("/api/config/calibration"))?.body as any;
  eq(body?.temp_tol_c, 3.5, "SAVE sent a different number from the one on screen");
  eq(body?.max_stack_frames, 100, "SAVE dropped a field the editor does not show as changed");
  unmount(m);
});

await test("SAVE is locked with a reason until something changes, and refuses a bin narrower than the tolerance", async () => {
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();

  // Clean: locked, with the reason - not a grey rectangle.
  const save = () => tid(m.host, "cal-save");
  eq(save().getAttribute("aria-disabled"), "true", "SAVE is live with nothing changed");
  eq(save().getAttribute("title"), TOL_CLEAN_REASON, "SAVE does not say why it is inert");
  assert(save().getAttribute("disabled") == null, "SAVE used the native disabled attribute");

  // Make the bin narrower than the tolerance: the warning appears and SAVE
  // states THAT reason instead.
  const tol = tid(m.host, "cal-tolerance-temp_tol_c");
  await typeInto(tol, "9");
  await blur(tol);
  const warn = tid(m.host, "cal-bin-warning");
  assert(warn != null, "a bin narrower than the tolerance raised no warning");
  eq(warn.textContent, binWarning({ ...CALIBRATION, temp_tol_c: 9 } as never),
    "the warning does not state both numbers and the consequence");
  eq(save().getAttribute("aria-disabled"), "true", "SAVE is live with an invalid bin");
  eq(save().getAttribute("title"), TOL_BIN_REASON, "SAVE does not name the bin as the blocker");
  await click(save());
  eq(count("POST /api/config/calibration"), 0, "a locked SAVE still wrote the invalid pair");
  unmount(m);
});

// ============================================================ 6. LOAD ERRORS
// Sabotage: fold the error into the empty state and the "not an empty library"
// assertion goes red - which matters, because "no masters yet" is an
// instruction to spend a night shooting frames you may already have.

await test("a failed masters load renders as an error with a RETRY, never as an empty library", async () => {
  MASTERS_RESPONSE = { status: 500, body: "disk unreadable" };
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  const err = tid(m.host, "cal-load-error");
  assert(err != null, "a failed masters load did not render an error row");
  assert(tid(m.host, "cal-empty") == null, "a load failure rendered as an empty library");
  assert(err.textContent.includes(libraryLoadError("disk unreadable")),
    `the error does not carry the server's reason - got: ${err.textContent}`);

  MASTERS_RESPONSE = { status: 200, body: MASTERS };
  await click(tid(m.host, "cal-retry"));
  eq(tids(m.host, "cal-master-row").length, 2, "RETRY did not refetch the library");
  unmount(m);
});

await test("an empty library says what would fill it, and the retry hint survives a finished fetch", async () => {
  MASTERS_RESPONSE = { status: 200, body: [] };
  seedAdmin();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  assert(tid(m.host, "cal-empty") != null, "an empty library did not render the empty card");
  assert(tid(m.host, "cal-load-error") == null, "an empty library rendered as a load failure");
  unmount(m);
  MASTERS_RESPONSE = { status: 200, body: MASTERS };

  // The pack's equivalent: tiles that failed are still worth saying after the
  // fetch has ended, because "run it again" is the remedy.
  PACK = { ...PACK_IDLE, fetching: { done: 9, total: 10, failed: 1 } };
  seedAdmin();
  const p = mount(SkyPackSheet);
  await settle();
  assert(tid(p.host, "pack-retry-hint") != null, "failed tiles raised no retry hint");
  eq(tid(p.host, "pack-retry-hint").textContent, PACK_RETRY_HINT,
    "the retry hint does not say that running it again resumes");
  unmount(p);
  PACK = PACK_IDLE;
});

// ================================================================= 7. VIEWER
// Sabotage: pass `disabled` instead of `lockedReason`, or hide the delete
// button the way the legacy panel did, and the "every control still on screen,
// locked, with the reason" assertions go red.

await test("a viewer sees the calibration sheet read-only, named by capability, and fires nothing", async () => {
  seedViewer();
  reset();
  const m = mount(CalibrationSheet);
  await settle();
  const before = asked.length;

  const build = tid(m.host, "cal-build");
  eq(build.getAttribute("aria-disabled"), "true", "BUILD is pressable for a viewer");
  assert(build.getAttribute("disabled") == null, "BUILD used the native disabled attribute");
  eq(build.getAttribute("title"), `needs ${accessPhrase("control.capture")}`,
    "BUILD's lock reason does not name control.capture's roles");

  // The row's delete is STILL THERE (the legacy panel hid it), locked.
  const del = tids(m.host, "cal-master-delete");
  eq(del.length, 2, "the per-row delete disappeared for a viewer instead of locking");
  eq(del[0].getAttribute("aria-disabled"), "true", "a viewer can press the row delete");

  const save = tid(m.host, "cal-save");
  eq(save.getAttribute("aria-disabled"), "true", "SAVE is pressable for a viewer");
  eq(save.getAttribute("title"), `needs ${accessPhrase("config.site_optics")}`,
    "SAVE's lock reason does not name config.site_optics' roles");
  const box = tid(m.host, "cal-tolerance-exposure_tol_pct");
  eq(box.getAttribute("aria-disabled"), "true", "a viewer can type into a tolerance");
  eq(box.getAttribute("disabled"), null, "a tolerance used the native disabled attribute");

  // Both read-only sentences are on screen, each naming its own capability.
  const text = m.host.textContent as string;
  assert(text.includes(`building and deleting masters needs ${accessPhrase("control.capture")}`),
    "no read-only sentence for the library");
  assert(text.includes(`changing the matching tolerances needs ${accessPhrase("config.site_optics")}`),
    "no read-only sentence for the tolerances");

  await click(build);
  await click(del[0]);
  await click(save);
  eq(asked.length, before, `a viewer's presses reached the rig: ${asked.slice(before).join(" | ")}`);
  eq((useStore.getState() as any).confirm, null, "a locked delete still raised the confirm");
  unmount(m);
});

await test("a viewer sees the sky pack read-only with config.site_optics, and fires nothing", async () => {
  PACK = PACK_PRESENT;
  seedViewer();
  reset();
  const m = mount(SkyPackSheet);
  await settle();
  const before = asked.length;

  const toggle = tid(m.host, "pack-online");
  const dl = tid(m.host, "pack-download");
  const del = tid(m.host, "pack-delete");
  eq(toggle.getAttribute("aria-disabled"), "true", "a viewer can flip online fetch");
  eq(dl.getAttribute("aria-disabled"), "true", "a viewer can start a 250 MB download");
  assert(del != null, "the delete button disappeared for a viewer instead of locking");
  eq(del.getAttribute("aria-disabled"), "true", "a viewer can delete the pack");
  eq(dl.getAttribute("title"), `needs ${accessPhrase("config.site_optics")}`,
    "the download's lock reason does not name config.site_optics' roles");
  assert((m.host.textContent as string).includes(
    `changing survey settings and the offline pack needs ${accessPhrase("config.site_optics")}`),
  "no read-only sentence for the sky pack");

  await click(toggle);
  await click(dl);
  await click(del);
  eq(asked.length, before, `a viewer's presses reached the rig: ${asked.slice(before).join(" | ")}`);
  eq((useStore.getState() as any).confirm, null, "a locked delete still raised the confirm");
  unmount(m);
  PACK = PACK_IDLE;
});

// =================================================================== summary
const total = passed + failed;
console.log(`calibrationSheetsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
