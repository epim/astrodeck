// moreSheetsDom.test.tsx - the MORE-group tuning/admin sheets (T-SET-4),
// MOUNTED, and since wave R7 the four SYSTEM sheets end to end.
//
// WHAT THIS FILE IS NOW ABOUT. It began as a wrapper test: every sheet here
// mounted a self-contained legacy panel unchanged, so the only new behaviour
// was the wrapper's (does it mark itself, does it double up a lock note, does
// it remount a panel out from under a half-typed edit). Wave R7 rebuilt the
// panels, so four of these sheets - UPDATE, FACTORY RESET, CREDITS AND
// LICENCES, RESTRICTED ASSETS (T-R7-12) - are graded here in full:
//
//   1. each renders its marker AND its rebuilt editor. A sheet frame with
//      nothing under it would otherwise pass every other assertion.
//   2. CHECK NOW fires exactly ONE request - not one per render, and not a
//      second one on a second press while the first is still in flight. The
//      route polls GitHub and answers slowly.
//   3. a factory reset needs the typed word, then posts exactly once, and only
//      the success path clears this browser's own saved state.
//   4. the DESTRUCTIVE GUARD: a near-miss word ("RESE", "RESETT", "RE SET")
//      leaves the button locked. The server trims and case-folds and would
//      refuse anything else, and a button that lights up on a word the server
//      then rejects is worse than no button.
//   5. a viewer sees all four sheets read-only, each naming its own capability
//      in its own words, and no write reaches the network.
//
// The sheets rebuilt by T-R7-13 (sync / naming / WCS / standards) and T-R7-14
// (calibration / sky pack) keep a marker-and-editor assertion here; their
// content and viewer coverage lives in `filesSheetsDom.test.tsx` and
// `calibrationSheetsDom.test.tsx`, which those tasks own.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/moreSheetsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Convention: jsdom by hand, createRoot + act, native events, settle(), a
// hand-written g.fetch recording into `asked`/`posts`, store seeding via
// useStore.setState, printed tally + the `{ passed, failed, total }` export
// (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Every rebuilt editor mounted below is an AREA ROOT and imports its own
// `<area>.css` - `safety.css` (T-R7-9), `system.css` (T-R7-12), `files.css`,
// `calibration.css` - because wave R7 gives `next.css` to one task and every
// other area its own stylesheet. Node cannot load a stylesheet, so this
// synchronous hook answers with an empty module, exactly as
// `__tests__/shellDom.test.tsx` does for `NextApp`. It must be registered
// before the first `await import` that reaches one.
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  // NOT "performance": this jsdom's Performance delegates to the global one,
  // so copying it onto globalThis makes performance.now() call itself until
  // the stack blows (primitivesDom.test.tsx's note, reproduced here after
  // hitting the same crash).
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// A stub, not a server - every request recorded, answered by path/method.
const asked: string[] = [];
const posts: { url: string; method: string; body: unknown }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

// `let`, not `const`: one test below swaps this for a fixture whose "current"
// night is absent from `nights[]` (the honest-disabled case), then restores
// it. `g.fetch` closes over the BINDING, so the swap is seen immediately.
let NIGHTS = {
  current: "2026-09-10",
  persisted: true,
  nights: [
    { night: "2026-09-10", bytes: 512 },
    { night: "2026-09-09", bytes: 12_400_000 },
  ],
};
// Both remedies, because they are different screens: "fetch" is a source line
// and no control at all, "acknowledge" is the only row with a button and
// therefore the only one that can be locked for a viewer.
const RESTRICTED = {
  assets: [
    {
      id: "dss2", title: "DSS2 imagery", quote: "the licence's own words",
      reading: "AstroDeck's reading of them", remedy: "fetch", source: "CDS/ESA HiPS",
      without: "the offline pack has no imagery", satisfied: true, consent: null,
    },
    {
      id: "astrospheric", title: "Astrospheric forecasts",
      quote: "not for use in a commercial product",
      reading: "we read that as not covering a rig controller you paid for",
      remedy: "acknowledge", source: "astrospheric.com",
      without: "the forecast falls back to Open-Meteo", satisfied: false, consent: null,
    },
  ],
};

// `let`: the update tests swap in an available-release fixture and restore it.
let UPDATE_STATUS: any = {
  current: "0.3.28", latest: "0.3.28", update_available: false, notes_md: "",
  channel: "stable", last_check_ts: 1_757_500_000, phase: "idle", progress: 0,
  error: null, last_result: null,
  supervised: true, can_apply: true, apply_blocked_reason: "",
};

// A two-group credits document, one entry each, with a real licence body in
// the pool and one flagged entry - enough to exercise the search, the count
// line, the group disclosures and the owner-decision group opening itself.
const CREDITS_DOC = {
  generator: "tools/gen_credits.py",
  project: { name: "AstroDeck", version: "0.3.28", spdx: "AGPL-3.0-or-later" },
  scope: "Everything this build ships or fetches at runtime.",
  licenses: { h1: "MIT License\n\nPermission is hereby granted..." },
  groups: [
    {
      id: "python", title: "Python packages", blurb: "Imported by the server at runtime",
      entries: [{
        name: "fastapi", version: "0.141.0", spdx: "MIT", tier: "runtime",
        requires: ["notice"], summaries: ["Keep the copyright notice."],
        texts: [{ title: "MIT License", hash: "h1" }],
        url: "https://fastapi.tiangolo.com",
      }],
    },
    {
      id: "flagged", title: "Needs an owner decision", blurb: "Not settled by this page",
      entries: [{
        name: "playerone-sdk", version: "3.10.1", spdx: "LicenseRef-PlayerOne",
        tier: "vendor", requires: [], summaries: [],
        texts: [], flag: "the vendor has not answered about redistribution",
      }],
    },
  ],
};
const FACTORY_PREVIEW = {
  site_is_default: false, profiles: 2, plans: 3, drivers: 4, alert_sinks: 1,
  users: 2, remote_paired: false, update_credential: false,
  captures: { frames: 12, entries: 3, bytes: 4096 },
  preserved_capture_entries: ["logs"], can_reset: true, blocked_reason: "",
};
const PACK_STATUS = {
  present: false, slug: "", survey: "", order: null, bytes: null,
  tile_count: null, fetched_at: null, fetching: null,
};
const DOME_STATE = { connected: false, shutter: "unknown", requires_park_before_close: false, can_bind: false };
const SYNC_STATUS = {
  enabled: false, kind: "local_dir", path: "", label: "", limit_per_pass: 0,
  configured: false, running: false, debounce_s: 0, sweep_interval_s: 0,
  passes: 0, total_sent: 0, total_bytes: 0, last_attempt_at: null,
  last_ok_at: null, consecutive_failures: 0, alarm: false, last: null,
};

// Mirrors whatever the test most recently seeded into the store, so a panel's
// own `loadConfig()` re-GET of `/api/config` (fired after a save) reads back
// the SAME shape it started from rather than wiping it to `{}` - a real
// server behaves this way too (a save is a merge from the caller's own view).
let currentConfig: any = null;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (method !== "GET" && init?.body != null) {
    let body: unknown = init.body;
    try { body = JSON.parse(init.body); } catch { /* leave as text */ }
    posts.push({ url: u, method, body });
  }
  if (u.includes("/api/logs/nights")) return ok(NIGHTS);
  // The acknowledge POST answers with the same envelope the GET does, which is
  // what lets the row re-render from the server's answer instead of guessing.
  if (u.includes("/api/licensing/restricted")) return ok(RESTRICTED);
  if (u.includes("/api/update/")) return ok(UPDATE_STATUS);
  if (u.includes("/api/system/factory-reset")) return ok(FACTORY_PREVIEW);
  if (u.includes("/api/survey/pack")) return ok(PACK_STATUS);
  if (u.includes("/api/calibration/masters")) return ok([]);
  if (u.includes("/api/dome/state")) return ok(DOME_STATE);
  if (u.includes("/api/sync/push")) return ok(SYNC_STATUS);
  if (u.includes("/api/config") && method === "GET") return ok(currentConfig ?? {});
  if (u.includes("/api/config")) return ok(currentConfig ?? {});
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");

const { SafetyTuningSheet } = await import("../sheets/SafetyTuningSheet");
const { StandardsSheet } = await import("../sheets/StandardsSheet");
const { CalibrationSheet } = await import("../sheets/CalibrationSheet");
const { NamingSheet } = await import("../sheets/NamingSheet");
const { WcsSheet } = await import("../sheets/WcsSheet");
const { SyncSheet } = await import("../sheets/SyncSheet");
const { SkyPackSheet } = await import("../sheets/SkyPackSheet");
const { RestrictedSheet } = await import("../sheets/RestrictedSheet");
const { LogExportSheet, exportHref } = await import("../sheets/LogExportSheet");
const { FactoryResetSheet } = await import("../sheets/FactoryResetSheet");
const { UpdateSheet } = await import("../sheets/UpdateSheet");
const { CreditsSheet } = await import("../sheets/CreditsSheet");
// The browser is mounted DIRECTLY, with a fixture, wherever the assertion is
// about its content: the sheet itself reaches the real 550 KB
// `credits.generated.json` through a dynamic `import()`, which is a bundler
// path and not something a jsdom script should be made to resolve.
const { CreditsBrowser, confirmWordOk, clearClientState } =
  await import("../tuning/system");
const { sheets4 } = await import("../sheets/set4");

// ------------------------------------------------------------------ harness
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

/** Mount `Comp` into a fresh container/root, return both so a test can query
 *  and unmount when done - each sheet gets its own root so one sheet's
 *  intervals/effects never leak into the next. */
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

const SAFETY = {
  enabled: true, preset: "backyard", poll_each_frame: false,
  min_alt_deg: 15, max_alt_deg: 85, horizon: null, nogo_box: null,
  enforce_pier_limits: false, twilight_deg: -12,
  solar_avoidance: true, solar_exclusion_deg: 30,
  on_unsafe: "pause", unsafe_consecutive: 3, resume_when_safe: true,
  resume_safe_consecutive: 3, max_pause_min: 120,
  close_dome_on_unsafe: false, close_dome_when_done: false, reopen_dome_when_safe: false,
};
const ESCALATION = {
  require_cooling: false, cooling_action: "warn",
  require_guiding: false, guiding_action: "warn",
  af_failure_action: "warn", hfr_reject_action: "warn",
  hfr_retake_limit_per_target: 3, no_progress_watchdog_s: 0,
  reconnect_resume: false, reconnect_retries: 3,
};

/** Full admin seed: every reused panel's own capability gate opens, and
 *  `config.safety`/`config.escalation` are populated so SafetyLimitsPanel and
 *  EscalationPanel render their real forms rather than "Loading...". */
function seedAdmin(over: Record<string, unknown> = {}): void {
  const config = {
    version: 1, safety: SAFETY, escalation: ESCALATION,
    ...((over.config as object | undefined) ?? {}),
  };
  currentConfig = config;
  useStore.setState({
    principal: ADMIN,
    // The rebuilt editors gate through `next/lib/gate.ts`, whose FIRST clause
    // is "the rig is not reachable" - so a seed that leaves the socket in its
    // initial "connecting" state locks every control for a reason that has
    // nothing to do with the screen under test.
    wsPhase: "up",
    wsConnected: true,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    masters: [],
    config,
    ...over,
  } as never);
}
function seedViewer(over: Record<string, unknown> = {}): void {
  const config = { version: 1, safety: SAFETY, escalation: ESCALATION };
  currentConfig = config;
  useStore.setState({
    principal: VIEWER,
    wsPhase: "up",
    wsConnected: true,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    masters: [],
    config,
    ...over,
  } as never);
}

const q = (host: any, sel: string): any => host.querySelector(sel);
const qa = (host: any, sel: string): any[] => Array.from(host.querySelectorAll(sel));
const panelTitles = (host: any): string[] => qa(host, ".panel-title").map((e: any) => e.textContent);

// ============================================================ 1. PRECONDITION
// Every sheet renders its own `settings-<name>` marker AND the mounted
// panel's own heading text. Sabotage: drop the `data-testid` off `Sheet` (or
// the icon/title/sub props) and this assertion goes red on the FIRST half;
// drop or misname the imported panel and it goes red on the SECOND half.

// REDUCED, wave R7 T-R7-9 (ruling 3). This sheet used to mount `SafetyPanel`,
// `SafetyLimitsPanel` and `EscalationPanel`, and this test named all three
// legacy `.panel-title` headings. Two of those panels were a SECOND editor for
// the `config.safety` block that `hubs/rig/sheets/safety.tsx` already owns, so
// they are unmounted; what is left is the preset, the rebuilt escalation editor
// and links to the screens that own the rest. The assertion tracks that: three
// DIFFERENT surfaces plus the marker, so a sheet that rendered only its header
// cannot pass. The duplication itself is graded in
// `settings/tuning/safety/__tests__/safetyTuningDom.test.tsx`.
await test("SafetyTuningSheet: marker + the preset, the editor and the links out", async () => {
  seedAdmin();
  const m = mount(SafetyTuningSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-safetyTuning"]') != null, "no settings-safetyTuning marker");
  assert(q(m.host, '[data-testid="safety-preset-seg"]') != null, "no safety preset picker");
  assert(q(m.host, '[data-testid="escalation-editor"]') != null, "no escalation editor");
  assert(q(m.host, '[data-testid="safety-tuning-warm-link"]') != null,
    "no link to the warm ramp, which lives on the camera sheet");
  assert(/LIVE SAFETY STATUS/.test(m.host.textContent), "no link to the rig hub's live safety sheet");
  // The two legacy panels must not come back: two editors for one config block
  // is a screen that can disagree with the rig about what happens when it rains.
  const titles = panelTitles(m.host);
  for (const t of ["Sun avoidance", "Safety limits"]) {
    assert(!titles.includes(t), `the legacy "${t}" panel is mounted again`);
  }
  unmount(m);
});

await test("StandardsSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(StandardsSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-standards"]') != null, "no settings-standards marker");
  // Rebuilt by T-R7-13; its content lives in `filesSheetsDom.test.tsx`. What
  // this file still grades is that the sheet mounts SOMETHING, so a header
  // over an empty body cannot pass.
  assert(q(m.host, '[data-testid="standards-editor"]') != null, "no rebuilt standards editor");
  unmount(m);
});

await test("CalibrationSheet: marker + both panel headings", async () => {
  seedAdmin();
  const m = mount(CalibrationSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-calibration"]') != null, "no settings-calibration marker");
  // Rebuilt by T-R7-14; graded in `calibrationSheetsDom.test.tsx`.
  assert(q(m.host, '[data-testid="cal-library"]') != null, "no rebuilt calibration library");
  assert(q(m.host, '[data-testid="cal-tolerances"]') != null, "no rebuilt tolerances editor");
  unmount(m);
});

await test("NamingSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(NamingSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-naming"]') != null, "no settings-naming marker");
  assert(q(m.host, '[data-testid="naming-editor"]') != null, "no rebuilt naming editor");
  unmount(m);
});

await test("WcsSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(WcsSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-wcs"]') != null, "no settings-wcs marker");
  assert(q(m.host, '[data-testid="wcs-editor"]') != null, "no rebuilt WCS stamp editor");
  unmount(m);
});

await test("SyncSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(SyncSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-sync"]') != null, "no settings-sync marker");
  assert(q(m.host, '[data-testid="sync-editor"]') != null, "no rebuilt sync editor");
  unmount(m);
});

await test("SkyPackSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(SkyPackSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-skyPack"]') != null, "no settings-skyPack marker");
  assert(q(m.host, '[data-testid="pack-card"]') != null, "no rebuilt sky pack editor");
  unmount(m);
});

await test("RestrictedSheet: marker + the disclosure heading + a row per asset", async () => {
  seedAdmin();
  const m = mount(RestrictedSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-restricted"]') != null, "no settings-restricted marker");
  // The heading is a parity string from the wave plan's 3.F10 row: the design
  // uppercases it in CSS, so the DOM text is still the sentence.
  assert(/Not ours to give you/.test(m.host.textContent),
    "the 'Not ours to give you' heading is gone from the rebuilt list");
  eq(qa(m.host, '[data-testid="restricted-row"]').length, RESTRICTED.assets.length,
    "the rebuilt list dropped an asset the server disclosed");
  // Both halves of the disclosure: the licensor's words and our reading, never
  // merged into one sentence.
  assert(/not for use in a commercial product/.test(m.host.textContent),
    "the licensor's own quote is not on screen");
  assert(/we read that as not covering a rig controller/.test(m.host.textContent),
    "AstroDeck's reading of the quote is not on screen");
  assert(/Until then, the forecast falls back to Open-Meteo\./.test(m.host.textContent),
    "an unsatisfied asset does not say what the rig does without it");
  unmount(m);
});

await test("LogExportSheet: marker + EXPORT buttons render", async () => {
  seedAdmin();
  const m = mount(LogExportSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-logExport"]') != null, "no settings-logExport marker");
  assert(q(m.host, '[data-testid="log-export-txt"]') != null, "no EXPORT .TXT button");
  assert(q(m.host, '[data-testid="log-export-jsonl"]') != null, "no EXPORT .JSONL button");
  unmount(m);
});

await test("FactoryResetSheet: marker + the measured scope, both halves", async () => {
  seedAdmin();
  const m = mount(FactoryResetSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-factoryReset"]') != null, "no settings-factoryReset marker");
  // The heading the legacy panel repeated ("Factory reset", under a sheet
  // titled FACTORY RESET) is gone on purpose. What has to be here is the
  // MEASURED scope, which is the thing a tester reads before committing.
  assert(q(m.host, '[data-testid="reset-cleared"]') != null, "no CLEARED list");
  assert(q(m.host, '[data-testid="reset-kept"]') != null, "no KEPT list");
  assert(q(m.host, '[data-testid="reset-run"]') != null, "no FACTORY RESET button");
  const text = m.host.textContent as string;
  assert(/12 frames in 3 folders \(4\.0 KB\)/.test(text),
    "the frame count on screen is not the one the preview route measured: " + text.slice(0, 400));
  assert(/2 saved profiles, 3 saved plans, 4 configured drivers/.test(text),
    "the cleared list is not counting the real profiles/plans/drivers");
  unmount(m);
});

await test("set4 registers exactly the ten sheet names, matching the plan's table", () => {
  const names = Object.keys(sheets4).sort();
  const want = [
    "calibration", "factoryReset", "logExport", "naming", "restricted",
    "safetyTuning", "skyPack", "standards", "sync", "wcs",
  ];
  eq(JSON.stringify(names), JSON.stringify(want), "sheets4's keys drifted from the plan's C.6 table");
});

// ============================================================ 2. INTERACTION
// StandardsSheet: flipping "Apply per-filter focus offsets" fires
// POST /api/config with a {standards} body. Sabotage: this goes red if the
// sheet ever stopped mounting the REAL StandardsPanel (e.g. a typo'd import,
// or a locally-forked copy that doesn't call setStandardsConfig).

await test("StandardsSheet interaction: the toggle saves via POST /api/config {standards}", async () => {
  seedAdmin();
  const before = posts.length;
  const m = mount(StandardsSheet);
  await settle();
  const toggle = q(m.host, '[data-testid="standards-offsets"]');
  assert(toggle != null, "no per-filter focus offsets switch rendered");
  act(() => { toggle.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
  const saved = posts.slice(before).find((p) => p.url.includes("/api/config") && p.method === "POST");
  assert(saved != null, "no POST /api/config was made when the toggle was pressed");
  const body = saved!.body as { standards?: Record<string, unknown> };
  assert(body.standards != null, "the POST body carried no `standards` key");
  eq(body.standards!.apply_filter_offsets, false, "the toggle did not flip apply_filter_offsets in the saved body");
  unmount(m);
});

// ============================================================ 3. VIEWER
// SafetyTuningSheet as a viewer: ONE read-only sentence naming both
// capabilities this sheet edits, and no WRITE reaches the network. The sentence
// used to come from `SafetyLimitsPanel`; since wave R7 the sheet owns it, and
// it is one note rather than two because a `LockNote` per capability is how a
// screen ends up saying the same thing twice in two different shapes.
// Sabotage: drop the `LockNote` from `SafetyTuningPanel` and this goes red on
// the missing-text half; give any control here a live write path for a viewer
// and it goes red on the "fires nothing" half.

await test("SafetyTuningSheet as a viewer: one read-only sentence renders, and no write fires", async () => {
  // `wsPhase: "up"` on purpose. This sheet locks through `next/lib/gate.ts`,
  // whose priority is link-down BEFORE capability, so an unset ws phase would
  // give an ADMIN the same sentence and the test would stop being about the
  // role at all.
  seedViewer({ wsPhase: "up" });
  const before = posts.length;
  const m = mount(SafetyTuningSheet);
  await settle();
  const notes = qa(m.host, ".nx-locknote");
  eq(notes.length, 1, "a viewer does not see exactly one read-only sentence on this sheet");
  const said = (notes[0].textContent || "").replace(/\s+/g, " ");
  assert(/config\.safety/.test(said) && /config\.alerts/.test(said) && /admin access/.test(said),
    "the read-only sentence does not name both capabilities and the roles that hold them: " + said);
  const wrote = posts.slice(before).some((p) => ["POST", "PUT", "PATCH", "DELETE"].includes(p.method));
  assert(!wrote, "a viewer's mount alone caused a write request - " + JSON.stringify(posts.slice(before)));
  unmount(m);
});

// ============================================================= 4. NO REMOUNT
// Typing into NamingPanel's template field, then a config.version bump (the
// D.4 hazard: ANY unrelated config write bumps version), must not lose the
// draft. Sabotage: put `key={config.version}` (or any config-keyed key) on
// the Sheet/NamingPanel and this goes red - React remounts NamingPanel, its
// `useState(stored)` re-initialises, and the typed text is gone.

await test("NamingSheet: a config.version bump while typing does not discard the draft", async () => {
  seedAdmin();
  const m = mount(NamingSheet);
  await settle();
  const input = q(m.host, '[aria-label="File-naming template"]');
  assert(input != null, "no File-naming template input rendered");

  const TYPED = "$$TARGET$$/CUSTOM_$$FRAMENR$$";
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, TYPED);
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  eq(input.value, TYPED, "precondition: the typed text never reached the input");

  const live = useStore.getState().config as any;
  await act(async () => {
    useStore.setState({ config: { ...live, version: (live.version ?? 1) + 1 } } as never);
  });
  await settle();

  eq(q(m.host, '[aria-label="File-naming template"]').value, TYPED,
    "a config.version bump discarded the typed naming template - the sheet remounted the panel");
  unmount(m);
});

// ============================================================= 5. FACTORY RESET
// (a) non-admin: the scope preview is ABSENT ("hidden (needs admin access)"),
//     never a fabricated zero, and no preview GET is fired at all.
// (b) admin: the button stays blocked until RESET is typed, then unblocks.
// Sabotage for (a): render `snap?.profiles ?? 0` instead of the panel's own
// noNumbers fallback - this goes red on the "no fabricated zero" half, or on
// the "no GET fired" half if the sheet added its own eager preview fetch.
// Sabotage for (b): drop the typed-word interlock - this goes red on
// "stays blocked" before typing RESET.

await test("FactoryResetSheet as a non-admin: no fabricated preview, and no preview GET fired", async () => {
  seedAdmin({ principal: VIEWER }); // safety/escalation stay seeded; only the role changes
  const before = asked.length;
  const m = mount(FactoryResetSheet);
  await settle();
  const text = m.host.textContent as string;
  assert(/hidden \(needs admin access\)/.test(text),
    "the non-admin scope preview did not fall back to the honest 'hidden' text: " + text.slice(0, 300));
  assert(!/\b\d+\s*frame/i.test(text),
    "a frame COUNT is on screen for a non-admin - that's a fabricated number, not the real one");
  const previewFired = asked.slice(before).some((a) => a.includes("/api/system/factory-reset"));
  assert(!previewFired, "the reset-scope preview GET fired for a non-admin, who cannot read it");
  unmount(m);
});

await test("FactoryResetSheet as admin: blocked until RESET is typed, then live", async () => {
  seedAdmin();
  const m = mount(FactoryResetSheet);
  await settle();
  // The button carries the design's caps label now, so it is found by its
  // marker rather than by its text; the two assertions below are unchanged.
  const findBtn = () => q(m.host, '[data-testid="reset-run"]');
  const btn = findBtn();
  assert(btn != null, "no Factory reset button rendered");
  eq(btn.getAttribute("aria-disabled"), "true", "the button is live before RESET is typed");
  assert(/Type RESET in the box above to arm this\./.test(m.host.textContent),
    "the typed-word interlock reason is not on screen");

  const input = q(m.host, "#factory-reset-confirm");
  assert(input != null, "no factory-reset-confirm input rendered");
  eq(input.getAttribute("data-testid"), "reset-confirm", "the confirm box lost its probe marker");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, "RESET");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
    input.dispatchEvent(new win.Event("change", { bubbles: true }));
  });
  await settle();

  eq(findBtn().getAttribute("aria-disabled"), null, "the button stayed blocked after RESET was typed");
  unmount(m);
});

// ========================================================= 6. LOG EXPORT HREF
// One real, named sabotage lives beside this test (see the report): swapping
// `format` for a hardcoded "txt" made BOTH buttons carry the same href
// regardless of which one was pressed - this assertion pair is what turned
// red, and only the JSONL half.

await test("LogExportSheet: the export href names the chosen night and format", async () => {
  seedAdmin();
  const m = mount(LogExportSheet);
  await settle();

  eq(exportHref("2026-09-10", "txt"), "/api/logs/export?night=2026-09-10&format=txt",
    "exportHref built the wrong URL for a plain night/format pair");

  const txt = () => q(m.host, '[data-testid="log-export-txt"]');
  const jsonl = () => q(m.host, '[data-testid="log-export-jsonl"]');

  eq(txt().getAttribute("href"), "/api/logs/export?night=2026-09-10&format=txt",
    "the default night (tonight) did not produce the right TXT href");
  eq(jsonl().getAttribute("href"), "/api/logs/export?night=2026-09-10&format=jsonl",
    "the default night (tonight) did not produce the right JSONL href");
  assert(txt().getAttribute("aria-disabled") == null, "tonight is on disk in the fixture - TXT should not be locked");

  const pastChip = q(m.host, '[data-testid="night-2026-09-09"]');
  assert(pastChip != null, "no chip rendered for the past night in the fixture");
  act(() => { pastChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();

  eq(txt().getAttribute("href"), "/api/logs/export?night=2026-09-09&format=txt",
    "picking a different night did not update the TXT href");
  eq(jsonl().getAttribute("href"), "/api/logs/export?night=2026-09-09&format=jsonl",
    "picking a different night did not update the JSONL href");

  unmount(m);
});

await test("LogExportSheet: a night on disk carries no lock note (the complement of the next test)", async () => {
  seedAdmin();
  const m = mount(LogExportSheet);
  await settle();
  assert(!/this night was never written to disk/.test(m.host.textContent),
    "a night that IS on disk is being reported as never written");
  eq(q(m.host, '[data-testid="log-export-txt"]').getAttribute("aria-disabled"), null,
    "a night on disk should not lock the export button");
  unmount(m);
});

await test("LogExportSheet: a night never written to disk locks both buttons, honestly", async () => {
  const saved = NIGHTS;
  // Tonight has not flushed to the persisted store yet - it is "current" but
  // absent from `nights[]`, which is exactly the case export must refuse
  // rather than open a tab onto a 404.
  NIGHTS = { current: "2026-09-11", persisted: true, nights: saved.nights };
  seedAdmin();
  const m = mount(LogExportSheet);
  await settle();
  const txt = () => q(m.host, '[data-testid="log-export-txt"]');
  const jsonl = () => q(m.host, '[data-testid="log-export-jsonl"]');
  eq(txt().getAttribute("aria-disabled"), "true", "an unpersisted night did not lock EXPORT .TXT");
  eq(jsonl().getAttribute("aria-disabled"), "true", "an unpersisted night did not lock EXPORT .JSONL");
  assert(txt().getAttribute("href") == null, "a locked export button must not carry a live href");
  assert(/this night was never written to disk/.test(m.host.textContent),
    "the honest lock reason is not on screen for a night absent from nights[]");
  unmount(m);
  NIGHTS = saved;
});

// ========================================================== 7. SYSTEM SHEETS
// The four T-R7-12 sheets, rebuilt. Everything below this line is about the
// rebuilt editors rather than about a wrapper.

await test("UpdateSheet: marker, both cards, and the controls the probe walks", async () => {
  seedAdmin();
  const m = mount(UpdateSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-update"]') != null, "no settings-update marker");
  assert(q(m.host, '[data-testid="update-status"]') != null, "no software-updates card");
  assert(q(m.host, '[data-testid="update-settings"]') != null, "no update-settings card");
  const text = m.host.textContent as string;
  // Both parity headings from the wave plan's 3.F7 row. The design uppercases
  // a Label in CSS, so the DOM text is still the sentence.
  assert(/Software updates/.test(text), "the 'Software updates' heading is gone");
  assert(/Update settings/.test(text), "the 'Update settings' heading is gone");
  for (const id of ["update-check", "update-channel", "update-auto", "update-interval",
    "update-pubkey", "update-save"]) {
    assert(q(m.host, `[data-testid="${id}"]`) != null, `no ${id} control rendered`);
  }
  // The running version reached the screen from the route, not from a default.
  assert(/v0\.3\.28/.test(text), "the running version is not on screen: " + text.slice(0, 200));
  // No native `disabled` anywhere on this sheet, ever (ARCHITECTURE #6).
  eq(qa(m.host, "[disabled]").length, 0, "a native disabled attribute is on the update sheet");
  unmount(m);
});

// CHECK NOW fires exactly ONE request. Two presses inside one React batch share
// a render, so both handlers read the same `checking === false` - which is why
// the guard is a ref, not the state. Sabotage: delete `checkingRef` and the
// count assertion below reads 2.
await test("UpdateSheet: CHECK NOW fires exactly one request, even pressed twice", async () => {
  seedAdmin();
  const m = mount(UpdateSheet);
  await settle();
  const before = asked.filter((a) => a.startsWith("POST") && a.includes("/api/update/check")).length;
  eq(before, 0, "a check was fired by MOUNTING the sheet, before anyone pressed anything");

  const btn = q(m.host, '[data-testid="update-check"]');
  act(() => {
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();

  const checks = asked.filter((a) => a.startsWith("POST") && a.includes("/api/update/check")).length;
  eq(checks, 1, "CHECK NOW did not fire exactly one POST /api/update/check");
  // And the answer is re-read, or the panel would show yesterday's verdict
  // beside today's check.
  assert(asked.some((a) => a.startsWith("GET") && a.includes("/api/update/status")),
    "the status was never re-read after the check");
  unmount(m);
});

await test("UpdateSheet: SAVE is locked until something changes, then writes the whole block once", async () => {
  const UC = {
    enabled: true, auto_check: false, check_interval_hours: 24, channel: "stable",
    repo: "epim/astrodeck", signing_pubkey: "AAAA", health_timeout_s: 60, last_check_ts: null,
  };
  seedAdmin({ config: { version: 1, safety: SAFETY, escalation: ESCALATION, update: UC } });
  const m = mount(UpdateSheet);
  await settle();

  const save = () => q(m.host, '[data-testid="update-save"]');
  eq(save().getAttribute("aria-disabled"), "true",
    "SAVE is live with nothing edited - it would re-write the block it just read");

  const pre = q(m.host, '[data-testid="update-channel"]').querySelector('[data-value="prerelease"]');
  assert(pre != null, "no PRE-RELEASE option in the channel picker");
  act(() => { pre.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
  eq(save().getAttribute("aria-disabled"), null, "SAVE stayed locked after the channel changed");

  const before = posts.length;
  act(() => { save().dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
  const wrote = posts.slice(before).filter((p) => p.url.includes("/api/update/config"));
  eq(wrote.length, 1, "SAVE did not write exactly one POST /api/update/config");
  const body = wrote[0]!.body as Record<string, unknown>;
  eq(body.channel, "prerelease", "the saved body did not carry the edited channel");
  // The whole block goes back, not a patch: the route replaces `config.update`.
  eq(body.signing_pubkey, "AAAA", "the saved body dropped the signing key it never edited");
  eq(body.repo, "epim/astrodeck", "the saved body dropped the repo it never edited");
  unmount(m);
});

await test("CreditsSheet: marker, and a body under it either way", async () => {
  seedAdmin();
  const m = mount(CreditsSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-credits"]') != null, "no settings-credits marker");
  // The sheet reaches the generated document through a dynamic import, which
  // this runner cannot resolve - so what is asserted is that BOTH outcomes are
  // a real screen: the browser, or the honest "did not load" card. A blank
  // sheet is the one thing that must not happen.
  const has = (id: string) => q(m.host, `[data-testid="${id}"]`) != null;
  assert(has("credits-summary") || has("credits-loading") || has("credits-error"),
    "the credits sheet rendered a header over nothing at all");
  unmount(m);
});

await test("CreditsBrowser: the search narrows the grouped list and the count says so", async () => {
  seedAdmin();
  const m = mount(() => createElement(CreditsBrowser, { data: CREDITS_DOC as any }));
  await settle();

  assert(q(m.host, '[data-testid="credits-search"]') != null, "no credits search box");
  eq(q(m.host, '[data-testid="credits-search"]').getAttribute("placeholder"),
    "package, licence, or what it requires",
    "the search placeholder no longer says what can be searched");
  eq(q(m.host, '[data-testid="credits-count"]').textContent, "Showing all 2",
    "the count line does not match the fixture");
  eq(qa(m.host, '[data-testid="credits-group"]').length, 2, "not one card per group");

  // The owner-decision group opens itself; the ordinary one does not, because
  // 123 entries expanded is not a page anybody reads.
  const grp = (id: string) => q(m.host, `[data-testid="credits-group-${id}"] button[aria-expanded]`);
  eq(grp("flagged").getAttribute("aria-expanded"), "true",
    "the group holding owner decisions did not open itself");
  eq(grp("python").getAttribute("aria-expanded"), "false",
    "an ordinary group opened itself");
  assert(/the vendor has not answered about redistribution/.test(m.host.textContent),
    "the flagged entry's decision text is not on screen");

  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(q(m.host, '[data-testid="credits-search"]'), "fastapi");
    q(m.host, '[data-testid="credits-search"]').dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();

  eq(q(m.host, '[data-testid="credits-count"]').textContent, "Showing 1 of 2",
    "the count line did not follow the search");
  eq(qa(m.host, '[data-testid="credits-group"]').length, 1, "a group with no matches is still on screen");
  // A search that narrows to one result must not leave it behind a closed
  // disclosure - that is just a second click.
  eq(grp("python").getAttribute("aria-expanded"), "true",
    "the matching group stayed collapsed after a search narrowed to it");
  assert(/MIT License/.test(m.host.textContent),
    "the matched entry's own licence row is not rendered");
  unmount(m);
});

await test("RestrictedSheet: acknowledging posts once and re-renders from the server's answer", async () => {
  seedAdmin();
  const m = mount(RestrictedSheet);
  await settle();
  const before = posts.length;
  const ack = q(m.host, '[data-testid="restricted-ack"]');
  assert(ack != null, "no acknowledge button on the acknowledge-remedy row");
  eq(ack.getAttribute("aria-disabled"), null, "an admin's acknowledge button is locked");
  act(() => { ack.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
  const acks = posts.slice(before).filter((p) =>
    p.url.includes("/api/licensing/restricted/astrospheric/acknowledge"));
  eq(acks.length, 1, "acknowledging did not post exactly once to the asset's own route");
  eq((acks[0]!.body as { withdraw?: boolean }).withdraw, false,
    "the first acknowledgment was sent as a withdrawal");
  // A "fetch" asset has no button at all: there is nothing to consent to, only
  // somewhere to get it from.
  eq(qa(m.host, '[data-testid="restricted-ack"]').length, 1,
    "a fetch-remedy asset grew an acknowledge button");
  assert(/CDS\/ESA HiPS/.test(m.host.textContent),
    "the fetch-remedy asset does not say where this machine gets it");
  unmount(m);
});

// ===================================================== 8. THE FACTORY RESET
// The whole point of this screen is that it is hard to do by accident and
// impossible to do by surprise.

await test("FactoryResetSheet: the typed word arms it, and the reset posts exactly once", async () => {
  seedAdmin();
  // Something of this browser's own to lose, so the clear is observable.
  win.localStorage.setItem("astrodeck-next-sky-lens", "wide");
  win.localStorage.setItem("astrodeck.equipment.assignments.v1", "{}");
  const m = mount(FactoryResetSheet);
  await settle();

  const btn = () => q(m.host, '[data-testid="reset-run"]');
  eq(btn().getAttribute("aria-disabled"), "true", "the button is live before RESET is typed");

  const input = q(m.host, '[data-testid="reset-confirm"]');
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, "RESET");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
  eq(btn().getAttribute("aria-disabled"), null, "the button stayed locked after RESET was typed");

  const before = posts.length;
  act(() => { btn().dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
  // The third interlock: nothing has been sent yet, only asked.
  eq(posts.slice(before).filter((p) => p.url.includes("/api/system/factory-reset")).length, 0,
    "the reset was sent before the hold-to-confirm dialog was answered");
  assert(useStore.getState().confirm != null, "no confirm dialog was raised at all");

  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();

  const wrote = posts.slice(before).filter((p) => p.url.includes("/api/system/factory-reset"));
  eq(wrote.length, 1, "the reset did not post exactly once");
  const body = wrote[0]!.body as Record<string, unknown>;
  eq(body.confirm, "RESET", "the typed word did not reach the server, which checks it too");
  eq(body.delete_captures, false, "captures were opted into a reset nobody asked for");
  eq(body.reset_auth, false, "accounts were opted into a reset nobody asked for");
  eq(body.reset_remote, false, "relay pairing was opted into a reset nobody asked for");
  // The browser half. A fresh server behind a stale client is the exact
  // confusion this feature exists to prevent.
  eq(win.localStorage.getItem("astrodeck-next-sky-lens"), null,
    "this browser's own saved state survived the reset");
  eq(win.localStorage.getItem("astrodeck.equipment.assignments.v1"), null,
    "the device assignments survived the reset");
  unmount(m);
});

await test("FactoryResetSheet: dismissing the confirm sends nothing and clears nothing", async () => {
  seedAdmin();
  win.localStorage.setItem("astrodeck-night", "1");
  const m = mount(FactoryResetSheet);
  await settle();
  const input = q(m.host, '[data-testid="reset-confirm"]');
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, "RESET");
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
  const before = posts.length;
  act(() => {
    q(m.host, '[data-testid="reset-run"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  await act(async () => { (useStore.getState() as any).resolveConfirm(false); });
  await settle();
  eq(posts.slice(before).filter((p) => p.url.includes("/api/system/factory-reset")).length, 0,
    "KEEP EVERYTHING reset the controller anyway");
  eq(win.localStorage.getItem("astrodeck-night"), "1",
    "KEEP EVERYTHING wiped this browser's state anyway");
  win.localStorage.removeItem("astrodeck-night");
  unmount(m);
});

// DESTRUCTIVE GUARD. The server trims and case-folds the word and refuses
// anything else, so a button that lights up on a near miss is a button that
// invites a 4xx on the one action with no undo. Sabotage: relax `confirmWordOk`
// to `startsWith`/`includes` and the "RESETT" case goes red in both halves.
await test("FactoryResetSheet: a near-miss confirm word leaves the button locked", async () => {
  seedAdmin();
  const m = mount(FactoryResetSheet);
  await settle();
  const input = q(m.host, '[data-testid="reset-confirm"]');
  const btn = () => q(m.host, '[data-testid="reset-run"]');
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  const before = posts.length;

  for (const near of ["RESE", "RESETT", "RE SET", "RESETS", "RESET!", "R E S E T"]) {
    act(() => {
      setter.call(input, near);
      input.dispatchEvent(new win.Event("input", { bubbles: true }));
    });
    eq(confirmWordOk(near), false, `confirmWordOk accepted the near miss "${near}"`);
    eq(btn().getAttribute("aria-disabled"), "true",
      `the reset button armed on the near miss "${near}"`);
    // Pressing it anyway states the reason and sends nothing.
    act(() => { btn().dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  }
  await settle();
  eq(posts.slice(before).filter((p) => p.url.includes("/api/system/factory-reset")).length, 0,
    "a near-miss word still managed to send a reset");
  assert(/Type RESET in the box above to arm this\./.test(m.host.textContent),
    "the typed-word interlock reason is not on screen");

  // The complement, so the guard is not vacuously strict: the two shapes a
  // phone keyboard actually produces DO arm it.
  for (const okWord of ["RESET", "reset", " Reset "]) {
    eq(confirmWordOk(okWord), true, `confirmWordOk refused the real word as "${okWord}"`);
  }
  unmount(m);
});

// ============================================== 9. VIEWER: all four, read-only
// Each sheet names its OWN capability in its own words - four different
// sentences, because "you need admin" on four screens tells a viewer nothing
// about which four things they cannot do. Sabotage: drop any one editor's
// `lockedReason` and both halves of its row go red (the sentence and the
// aria-disabled), and dropping the `if (!canAdmin) return` in the reset
// editor's `refresh` goes red on the "fires nothing" assertion.
await test("viewer: the four system sheets are read-only, each in its own words, and write nothing", async () => {
  seedViewer();
  const before = posts.length;
  const askedBefore = asked.length;

  const up = mount(UpdateSheet);
  await settle();
  const upText = up.host.textContent as string;
  assert(/checking for and installing updates needs admin access/.test(upText),
    "the update sheet does not say what a viewer cannot do: " + upText.slice(0, 300));
  eq(q(up.host, '[data-testid="update-check"]').getAttribute("aria-disabled"), "true",
    "CHECK NOW is live for a viewer");
  eq(q(up.host, '[data-testid="update-save"]').getAttribute("aria-disabled"), "true",
    "SAVE SETTINGS is live for a viewer");
  eq(qa(up.host, "[disabled]").length, 0, "the update sheet used a native disabled attribute");
  unmount(up);

  const fr = mount(FactoryResetSheet);
  await settle();
  const frText = fr.host.textContent as string;
  assert(/A factory reset needs admin access\./.test(frText),
    "the factory reset sheet does not name its capability: " + frText.slice(0, 300));
  assert(/hidden \(needs admin access\)/.test(frText),
    "the scope preview did not fall back to the honest 'hidden' text");
  assert(!/\b\d+\s*frame/i.test(frText),
    "a frame COUNT is on screen for a non-admin - that is a fabricated number");
  eq(q(fr.host, '[data-testid="reset-run"]').getAttribute("aria-disabled"), "true",
    "the reset button is live for a viewer");
  eq(qa(fr.host, "[disabled]").length, 0, "the reset sheet used a native disabled attribute");
  unmount(fr);

  const rs = mount(RestrictedSheet);
  await settle();
  const rsText = rs.host.textContent as string;
  assert(/acknowledging a licence needs admin access/.test(rsText),
    "the restricted sheet does not say why a viewer cannot answer: " + rsText.slice(0, 300));
  assert(/statement about this whole rig, not about you/.test(rsText),
    "the instance-wide reason for the gate is gone");
  eq(q(rs.host, '[data-testid="restricted-ack"]').getAttribute("aria-disabled"), "true",
    "a viewer can acknowledge a licence for the whole deployment");
  // But the disclosure itself is NOT hidden: it is the licensor's words.
  assert(/Not ours to give you/.test(rsText), "the disclosure is hidden from a viewer");
  unmount(rs);

  const cr = mount(() => createElement(CreditsBrowser, { data: CREDITS_DOC as any }));
  await settle();
  const crText = cr.host.textContent as string;
  // The one screen with no gate at all, and it says so rather than leaving a
  // reader to wonder what is being withheld.
  assert(/Every role can read this page/.test(crText),
    "the credits screen does not say it is readable by every role: " + crText.slice(0, 300));
  assert(!/needs admin access/.test(crText),
    "the credits screen grew a capability gate - a licence notice only an admin can read is not published");
  eq(q(cr.host, '[data-testid="credits-search"]').getAttribute("aria-disabled"), null,
    "the credits search is locked for a viewer");
  unmount(cr);

  const wrote = posts.slice(before).filter((p) => ["POST", "PUT", "PATCH", "DELETE"].includes(p.method));
  eq(wrote.length, 0, "a viewer's mount alone caused a write: " + JSON.stringify(wrote));
  // And the admin-only preview GET is never even attempted for a viewer.
  assert(!asked.slice(askedBefore).some((a) => a.startsWith("GET") && a.includes("/api/system/factory-reset")),
    "the reset-scope preview GET fired for a viewer, who cannot read it");
});

// `clearClientState` is what the success path calls, and its blast radius is
// the whole origin ON PURPOSE (wave plan section 0, ruling 8: state the key
// list, do not change the behaviour). The keys live in a dozen modules and an
// enumerated list is a list somebody forgets to extend - which is how a stale
// client outlives a reset server.
await test("clearClientState clears the whole origin, astrodeck-next- keys included", () => {
  win.localStorage.setItem("astrodeck-next-sky-mode", "frame");
  win.localStorage.setItem("astrodeck-coach-seen", "{}");
  win.localStorage.setItem("something-else-entirely", "x");
  win.sessionStorage.setItem("astrodeck-next-dl", "1");
  clearClientState();
  eq(win.localStorage.length, 0, "localStorage was not cleared");
  eq(win.sessionStorage.length, 0, "sessionStorage was not cleared");
});

// =================================================================== summary
const total = passed + failed;
console.log(`moreSheetsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
