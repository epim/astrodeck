// moreSheetsDom.test.tsx - the ten MORE-group tuning/admin sheets (T-SET-4),
// MOUNTED. Every one of these wraps an EXISTING, self-contained legacy panel
// unchanged (`ARCHITECTURE.md` section 11 / plan section C.6); the only new
// behaviour worth a test is the WRAPPER'S: does it mark itself, does it add
// no lock note on top of the panel's own, and does it avoid remounting a
// panel out from under a half-typed edit.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/moreSheetsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Convention: jsdom by hand, createRoot + act, native events, settle(), a
// hand-written g.fetch recording into `asked`/`posts`, store seeding via
// useStore.setState, printed tally + the `{ passed, failed, total }` export
// (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

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
const RESTRICTED = {
  assets: [{
    id: "dss2", title: "DSS2 imagery", quote: "the licence's own words",
    reading: "AstroDeck's reading of them", remedy: "fetch", source: "CDS/ESA HiPS",
    without: "the offline pack has no imagery", satisfied: true, consent: null,
  }],
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
  if (u.includes("/api/licensing/restricted")) return ok(RESTRICTED);
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

await test("SafetyTuningSheet: marker + all three panel headings + the live-status link", async () => {
  seedAdmin();
  const m = mount(SafetyTuningSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-safetyTuning"]') != null, "no settings-safetyTuning marker");
  const titles = panelTitles(m.host);
  for (const t of ["Sun avoidance", "Safety limits", "When something fails"]) {
    assert(titles.includes(t), `missing panel heading "${t}" - titles were: ${titles.join(", ")}`);
  }
  assert(/LIVE SAFETY STATUS/.test(m.host.textContent), "no link to the rig hub's live safety sheet");
  unmount(m);
});

await test("StandardsSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(StandardsSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-standards"]') != null, "no settings-standards marker");
  assert(panelTitles(m.host).includes("Imaging standards"), "StandardsPanel heading missing");
  unmount(m);
});

await test("CalibrationSheet: marker + both panel headings", async () => {
  seedAdmin();
  const m = mount(CalibrationSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-calibration"]') != null, "no settings-calibration marker");
  const titles = panelTitles(m.host);
  assert(titles.includes("Calibration Library"), "CalibrationLibraryPanel heading missing");
  assert(titles.includes("Matching and stacking"), "CalibrationTolerancesPanel heading missing");
  unmount(m);
});

await test("NamingSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(NamingSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-naming"]') != null, "no settings-naming marker");
  assert(panelTitles(m.host).includes("File Naming"), "NamingPanel heading missing");
  unmount(m);
});

await test("WcsSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(WcsSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-wcs"]') != null, "no settings-wcs marker");
  assert(panelTitles(m.host).includes("Record where each photo points"), "WcsStampPanel heading missing");
  unmount(m);
});

await test("SyncSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(SyncSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-sync"]') != null, "no settings-sync marker");
  assert(panelTitles(m.host).includes("File sync"), "SyncPanel heading missing");
  unmount(m);
});

await test("SkyPackSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(SkyPackSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-skyPack"]') != null, "no settings-skyPack marker");
  assert(panelTitles(m.host).includes("Sky Atlas"), "SkyAtlasPanel heading missing");
  unmount(m);
});

await test("RestrictedSheet: marker + panel heading (with at least one asset)", async () => {
  seedAdmin();
  const m = mount(RestrictedSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-restricted"]') != null, "no settings-restricted marker");
  assert(panelTitles(m.host).includes("Not ours to give you"), "RestrictedAssetsPanel heading missing");
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

await test("FactoryResetSheet: marker + panel heading", async () => {
  seedAdmin();
  const m = mount(FactoryResetSheet);
  await settle();
  assert(q(m.host, '[data-testid="settings-factoryReset"]') != null, "no settings-factoryReset marker");
  assert(panelTitles(m.host).includes("Factory reset"), "FactoryResetPanel heading missing");
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
  const toggle = qa(m.host, 'button[role="switch"]').find((b: any) =>
    b.getAttribute("aria-label") === "Apply per-filter focus offsets");
  assert(toggle != null, "no 'Apply per-filter focus offsets' switch rendered");
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
// SafetyTuningSheet as a viewer: SafetyLimitsPanel's own read-only sentence
// renders verbatim, and no WRITE reaches the network (SafetyPanel's dome-
// status GET is an ungated telemetry poll every role gets, per
// `SafetyPanel.tsx`, so it is not what this assertion is about - the
// assertion is that a viewer's mount can never WRITE a safety config).
// Sabotage: delete the `!canEdit` branch inside SafetyLimitsPanel (or have
// this sheet fork/re-skin the panel instead of mounting it) and this goes
// red on the missing-text half; have this sheet add its own Save button on
// top of the panel and it goes red on the "fires nothing" half.

await test("SafetyTuningSheet as a viewer: the panel's own lock note renders, and no write fires", async () => {
  seedViewer();
  const before = posts.length;
  const m = mount(SafetyTuningSheet);
  await settle();
  assert(
    /Changing safety limits needs.*\(config\.safety\).*an admin\. The current settings are shown for\s*reference\./s
      .test(m.host.textContent.replace(/\s+/g, " ")),
    "the SafetyLimitsPanel read-only sentence is not on screen for a viewer: " + m.host.textContent.slice(0, 400),
  );
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
  const findBtn = () => qa(m.host, "button").find((b: any) => /^Factory reset$/.test((b.textContent || "").trim()));
  const btn = findBtn();
  assert(btn != null, "no Factory reset button rendered");
  eq(btn.getAttribute("aria-disabled"), "true", "the button is live before RESET is typed");
  assert(/Type RESET in the box above to arm this\./.test(m.host.textContent),
    "the typed-word interlock reason is not on screen");

  const input = q(m.host, "#factory-reset-confirm");
  assert(input != null, "no factory-reset-confirm input rendered");
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

// =================================================================== summary
const total = passed + failed;
console.log(`moreSheetsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
