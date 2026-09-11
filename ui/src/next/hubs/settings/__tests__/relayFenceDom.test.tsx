// relayFenceDom.test.tsx - every SETTINGS write the rig fences to the LAN,
// mounted OVER THE RELAY and pressed (wave-2 review R8's FIX-U-settings P1).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/relayFenceDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// THE DEFECT THIS FILE EXISTS FOR. `app.py` refuses a dozen route families to
// any session that arrived through the relay - `_REMOTE_LOCAL_ONLY_EXACT`,
// `_REMOTE_LOCAL_ONLY_PREFIXES` and `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES` -
// answering 403 `code: "local_only"` FOR EVERY ROLE, an admin included. Nothing
// in Settings knew that: UPGRADE, CHECK NOW, SAVE SETTINGS, FACTORY RESET, SAVE
// METHODS, the PEOPLE list, the sky-pack DOWNLOAD and DELETE, PUSH NOW, the
// solver pick and REFRESH ELEMENTS all rendered armed from the sofa and the user
// learned the refusal by pressing them. `gate.ts`'s `needsLan` is the fix, and
// this file is what keeps it wired.
//
// WHY THE PRINCIPAL IS AN ADMIN WITH EVERY CAPABILITY. `lockReason` ranks the
// LAN rule ABOVE the capability rule, so a viewer would be locked either way and
// every assertion below would pass over a component that had never heard of the
// fence. An admin has exactly one possible blocker here, which is what makes
// LOCAL_ONLY_REASON an assertion about `needsLan` and nothing else.
//
// NAMED SABOTAGES, each of which turns a named test RED:
//   * drop `needsLan: true` from any one editor's `useLock` -> that editor's
//     block fails on `aria-disabled` (null, not "true") and on its toast.
//   * move the `needsLan` rule below the cap rule in `gate.ts` -> nothing here
//     moves (the admin holds every cap), but `lib/__tests__/gate.test.ts` goes
//     red; that split is deliberate, this file grades the WIRING.
//   * drop the `|| onRelay` guard from `UsersEditor.refresh` /
//     `AuthMethodsEditor.refreshSetupUsers` / `FactoryResetEditor.refresh` ->
//     "asks the rig for nothing it will refuse" fails on the recorded GET.
//   * lock everything unconditionally instead -> the VACUITY GUARD at the
//     bottom fails: `cal-build` (POST /api/calibration/build, deliberately NOT
//     on the fence) must stay pressable on the same mount, on the same origin.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Four of these areas ship their own stylesheet and import it from the module
// this file reaches (`tuning/<area>/index.ts`, the area root). Node cannot load
// a `.css` file, so a load hook answers with an empty module - registered
// BEFORE the first `await import` that reaches one.
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
// The URL is the LAN one on purpose. `relay.ts` falls back to the pathname when
// nothing has told it the rig's own answer, so a `/h/<home>/` URL would make
// every block below pass on the fallback alone - and the fallback is not the
// thing under test. `noteRemoteStatus` supplies the rig's `via` instead, which
// is the path a real session takes (the Connection sheet reads
// `GET /api/remote/status` and hands the answer over).
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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string }
const asks: Ask[] = [];

const CONFIG: any = {
  version: 11,
  site: { is_default: false },
  optics: {
    focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248,
    sensor_height_px: 4176, auto_from_camera: false, guide_focal_length_mm: 200,
    telescope_name: "Askar FRA400", aperture_mm: 0, reducer: 1,
  },
  survey: { online_fetch: false },
  calibration: {
    dark_temp_tolerance_c: 1, dark_exposure_tolerance_pct: 10,
    dark_temp_bin_c: 2, flat_max_age_days: 30, min_frames: 5,
  },
  sync_push: { enabled: false, kind: "local_dir", path: "", label: "", limit_per_pass: 0 },
  naming: { template: "{target}/{filter}/{ts}" },
  standards: {},
  wcs_stamp: { solver: "auto", downsample: 2, min_stars: 10 },
  solve_saved_lights: false,
  safety: { preset: "balanced" },
  escalation: { cooling: "pause", guiding: "pause" },
  alerts: [],
  deadman_configured: false,
  update: {
    enabled: true, auto_check: false, check_interval_hours: 24, channel: "stable",
    repo: "epim/astrodeck", signing_pubkey: "key", health_timeout_s: 60, last_check_ts: null,
  },
  auth: {
    methods: ["local"], provider: "local", google_configured: false,
    admin_token_configured: false, session_signing_configured: true,
    role_allowlist: {}, default_role: "viewer", trust_loopback: true,
    session_ttl_s: 28800, local_enabled_first_run: true,
  },
  providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
};

const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method });
  if (u.includes("/api/survey/pack")) {
    return ok({ present: true, bytes: 262_144_000, order: 4, fetched_at: 1_756_000_000, fetching: null });
  }
  if (u.includes("/api/sync/push")) {
    return ok({ configured: true, running: false, last: null, last_ok_at: null });
  }
  if (u.includes("/api/calibration/masters")) return ok([]);
  // THESE TWO ANSWER PROPERLY ON PURPOSE. They are the routes the relay guards
  // stop this UI from ever asking for, and a fixture that answered `{ok:true}`
  // would make the sabotage (deleting a guard) CRASH on an undefined field
  // instead of failing the assertion that names the defect. A real shape means
  // the removed guard reads as "a request went out", which is the finding.
  if (u.includes("/api/system/factory-reset")) {
    return ok({
      profiles: 3, plans: 2, drivers: 4, alert_sinks: 1, users: 2,
      captures: { frames: 170, entries: 12, bytes: 42_000_000_000 },
      preserved_capture_entries: ["logs", "sky packs"],
      can_reset: true, blocked_reason: null, remote_paired: true, update_credential: false,
    });
  }
  if (u.includes("/api/users")) {
    return ok({ users: [{ id: "u1", username: "bear", email: "bear@rig", role: "admin", enabled: true }] });
  }
  if (u.includes("/api/ephemeris/status")) {
    return ok({
      satellites: { present: true, count: 200, source: "celestrak", fetched_unix: 1_756_000_000, age_days: 2 },
      comets: { present: false, count: 0, source: null, fetched_unix: null, age_days: null },
      fetching: [],
    });
  }
  if (u.includes("/api/update/status")) {
    return ok({
      current: "0.3.28", latest: "0.3.29", update_available: true, channel: "stable",
      phase: "idle", progress: 0, supervised: true, can_apply: true,
      apply_blocked_reason: null, last_check_ts: 1_756_000_000, notes_md: "", last_result: null,
    });
  }
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({ ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { LOCAL_ONLY_REASON } = await import("../../../lib/gate");
const { noteRemoteStatus, resetRelayForTests, onRelay } = await import("../../../lib/relay");

const { UpdateEditor, FactoryResetEditor } = await import("../tuning/system");
const { UsersEditor, AuthMethodsEditor } = await import("../tuning/people");
const {
  CalibrationLibraryEditor, CalibrationTolerancesEditor, SkyPackEditor,
} = await import("../tuning/calibration");
const { SyncEditor, NamingEditor, StandardsEditor, WcsStampEditor } = await import("../tuning/files");
const { SafetyTuningPanel } = await import("../tuning/safety");
const { EphemerisCard } = await import("../sheets/EphemerisCard");

// -------------------------------------------------------------------- harness
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
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.weather", "view.site_precise",
  "view.site_derived", "control.capture", "control.guide", "control.mount",
  "control.power", "config.backend", "config.safety", "config.solar_override",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

function seed(cfg: any = CONFIG): void {
  useStore.setState({
    config: cfg,
    principal: { role: "admin", email: "admin@rig", caps: ALL_CAPS },
    authGate: "open",
    wsPhase: "up",
    wsConnected: true,
    equipConnected: true,
    status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    update: {
      current: "0.3.28", latest: "0.3.29", update_available: true, channel: "stable",
      phase: "idle", progress: 0, supervised: true, can_apply: true,
      apply_blocked_reason: null, last_check_ts: 1_756_000_000, notes_md: "", last_result: null,
    },
    toasts: [],
  } as never);
}

/** Mount one editor on the relay origin. The log is cleared BEFORE the render,
 *  not after: three of these editors must ask the rig for NOTHING on a tunnelled
 *  origin (the people list, the guided card's copy of it, the factory-reset
 *  scope), and a log cleared after mount would have thrown away the very request
 *  those assertions are looking for - the guard could be deleted and the test
 *  would stay green. Mounting fires no writes, so `writes()` can safely read the
 *  whole log. */
const mount = async (el: any, cfg: any = CONFIG): Promise<void> => {
  await act(async () => { root.render(null); });
  await settle();
  seed(cfg);
  asks.length = 0;
  await act(async () => { root.render(el); });
  await settle();
  await act(async () => { useStore.setState({ toasts: [] } as never); });
};

/** Every non-GET request this editor has made. The fenced routes answer 403 in
 *  production, so "the control fired nothing" is the assertion that separates an
 *  honest lock from a button that presses and then apologises. */
const writes = () => asks.filter((a) => a.method !== "GET");
const toastTitles = () =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => t.title);

/** The whole assertion for one fenced control, in the words a failure needs:
 *  dimmed, aria-disabled, carrying THIS sentence, and firing nothing. */
async function refuses(id: string, what: string, pressSel?: string): Promise<void> {
  const el = q(id);
  assert(el != null, `${id} (${what}) is not on the page - the fixture is wrong, not the component`);
  eq(el.hasAttribute("disabled"), false, `${id} uses the native disabled attribute`);
  eq(el.getAttribute("aria-disabled"), "true",
    `${id} (${what}) renders ARMED over the relay - the rig would answer 403 local_only`);
  eq(el.getAttribute("title"), LOCAL_ONLY_REASON,
    `${id} does not name the relay as the blocker`);
  // `Segmented` marks the GROUP and handles the press on each option, so a
  // radiogroup needs the option named rather than its container.
  const target = pressSel ? el.querySelector(pressSel) : el;
  assert(target != null, `${id} has no press target matching ${pressSel}`);
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(target);
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `${id} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
  eq(writes().length, 0,
    `${id} reached the rig anyway: ${JSON.stringify(writes())}`);
}

// ====================================================== the origin under test
// The rig's own `via`, exactly as the Connection sheet hands it over.
noteRemoteStatus({ via: "relay" });

test("precondition: this tab counts as tunnelled, from the rig's own answer", () => {
  eq(onRelay(), true,
    "the relay derivation says LAN - every assertion in this file would be vacuous");
});

// ============================================================ SYSTEM > UPDATE
// /api/update/check, /api/update/apply and /api/update/config are EXACT entries
// on `_REMOTE_LOCAL_ONLY_EXACT`, which catches every method.
await mount(createElement(UpdateEditor));

await testAsync("UPDATE: CHECK NOW, UPGRADE and SAVE SETTINGS all refuse over the relay", async () => {
  assert(q("update-status") != null, "the update editor never rendered");
  await refuses("update-check", "POST /api/update/check");
  await refuses("update-apply", "POST /api/update/apply");
  await refuses("update-save", "POST /api/update/config");
});

test("UPDATE: the read-only note names the relay, not the capability an admin holds", () => {
  const note = q("update-lock-note");
  assert(note != null, "no read-only note on a panel where nothing can be saved");
  assert(String(note.textContent).includes(LOCAL_ONLY_REASON),
    `the note names the wrong blocker: "${note.textContent}"`);
});

// ===================================================== SYSTEM > FACTORY RESET
// `/api/system/factory-reset` is EXACT too, so the scope-preview GET is fenced
// exactly like the POST that performs the reset.
await mount(createElement(FactoryResetEditor));

await testAsync("FACTORY RESET: the button refuses, and the preview GET is never fired", async () => {
  assert(q("reset-scope") != null, "the factory-reset editor never rendered");
  eq(asks.filter((a) => a.url.includes("/api/system/factory-reset")).length, 0,
    "the scope preview was requested over the relay - a 403 and a red box for a "
    + "number this origin is not allowed to have");
  await refuses("reset-run", "POST /api/system/factory-reset");
  await refuses("reset-also-captures", "the delete-frames opt-in");
});

test("FACTORY RESET: the counts say they were not read, never \"counting...\" forever", () => {
  const kept = q("reset-kept");
  assert(kept != null, "the KEPT column is missing - the assertion below would be vacuous");
  assert(!/counting\.\.\./.test(String(kept.textContent)),
    `a count that will never arrive is still spinning: "${kept.textContent}"`);
  assert(/LAN-only/.test(String(kept.textContent)),
    `the scope does not say why it has no numbers: "${kept.textContent}"`);
});

// ============================================================ PEOPLE > PEOPLE
// `/api/users` is fenced by PREFIX and for every method, so the LIST READ is
// refused as well as the four mutations under it.
await mount(createElement(UsersEditor));

await testAsync("PEOPLE: ADD USER refuses, and the list is never requested", async () => {
  assert(q("users-editor") != null, "the people editor never rendered");
  eq(asks.filter((a) => a.url.includes("/api/users")).length, 0,
    "GET /api/users was fired over the relay, where the rig refuses it for every role");
  await refuses("users-add", "POST /api/users");
});

test("PEOPLE: the list region says the LAN is the blocker, not a missing capability", () => {
  const card = q("users-lan-only");
  assert(card != null,
    "the list is still 'reading the account list from the rig' - a spinner over a "
    + "request this screen deliberately never makes");
  assert(/LAN/.test(String(card.textContent)),
    `the region does not name the relay: "${card.textContent}"`);
  assert(!/admin access/.test(String(card.textContent)),
    "an admin was told they need admin access - the wrong-blocker defect");
});

// ================================================== PEOPLE > SIGN-IN METHODS
// WITH NO METHOD ENABLED, which is the one state the guided setup card appears
// in - and the card is what reads `GET /api/users`. Mounted on the shipped
// fixture the read never runs at all, so the "asks for no users" assertion would
// be true of a component that had never heard of the fence.
await mount(createElement(AuthMethodsEditor), { ...CONFIG, auth: { ...CONFIG.auth, methods: [] } });

await testAsync("SIGN-IN METHODS: SAVE METHODS refuses, and the guided card asks for no users", async () => {
  assert(q("auth-setup-card") != null,
    "the guided setup card is not on the page, so the user-list read under test "
    + "never had a chance to fire - this assertion would be vacuous");
  assert(q("auth-editor") != null, "the sign-in methods editor never rendered");
  eq(asks.filter((a) => a.url.includes("/api/users")).length, 0,
    "the guided card read the user list over the relay");
  await refuses("auth-save", "POST /api/auth/config");
});

// ======================================================= MORE > CALIBRATION
await mount(createElement(CalibrationTolerancesEditor));

await testAsync("CALIBRATION TOLERANCES: SAVE and RESET refuse (POST /api/config/calibration)", async () => {
  assert(q("cal-tolerances") != null, "the tolerances editor never rendered");
  await refuses("cal-save", "POST /api/config/calibration");
  await refuses("cal-reset", "POST /api/config/calibration");
});

// ================================================== MORE > SKY ATLAS PACK
await mount(createElement(SkyPackEditor));

await testAsync("SKY PACK: the online switch, DOWNLOAD and DELETE all refuse", async () => {
  assert(q("pack-card") != null, "the sky-pack editor never rendered");
  await refuses("pack-online", "POST /api/config/survey");
  await refuses("pack-download", "POST /api/survey/pack/fetch");
  await refuses("pack-delete", "DELETE /api/survey/pack");
});

// ========================================================== MORE > FILE SYNC
await mount(createElement(SyncEditor));

await testAsync("FILE SYNC: the enable switch and PUSH NOW refuse", async () => {
  assert(q("sync-editor") != null, "the sync editor never rendered");
  await refuses("sync-enabled", "POST /api/config/sync");
  await refuses("sync-push-now", "POST /api/sync/push/now");
});

// ======================================================== MORE > FILE NAMING
await mount(createElement(NamingEditor));

await testAsync("FILE NAMING: SAVE refuses (POST /api/config/naming)", async () => {
  assert(q("naming-editor") != null, "the naming editor never rendered");
  await refuses("naming-save", "POST /api/config/naming");
});

// ================================================= MORE > IMAGING STANDARDS
await mount(createElement(StandardsEditor));

await testAsync("IMAGING STANDARDS: the filter-offsets switch refuses (POST /api/config)", async () => {
  assert(q("standards-editor") != null, "the standards editor never rendered");
  await refuses("standards-offsets", "POST /api/config {standards}");
});

// ================================================ MORE > PLATE-SOLVE STAMP
await mount(createElement(WcsStampEditor));

await testAsync("PLATE-SOLVE STAMP: the master switch refuses (POST /api/config/wcs)", async () => {
  assert(q("wcs-editor") != null, "the plate-solve editor never rendered");
  await refuses("wcs-enabled", "POST /api/config/wcs");
});

// ============================================================= MORE > SAFETY
await mount(createElement(SafetyTuningPanel));

await testAsync("SAFETY: the preset picker refuses (POST /api/config {safety})", async () => {
  assert(q("safety-preset-card") != null, "the safety tuning panel never rendered");
  await refuses("safety-preset-seg", "POST /api/config {safety}", '[data-value="remote"]');
});

test("SAFETY: the folded two-capability note prints the relay sentence whole", () => {
  const note = q("safety-tuning-lock");
  assert(note != null, "no read-only note on a panel where neither block can be written");
  assert(String(note.textContent).includes(LOCAL_ONLY_REASON),
    `the folded sentence mangled the relay reason: "${note.textContent}"`);
  assert(!/changing the safety preset \(config\.safety\) This changes/.test(String(note.textContent)),
    "a whole sentence was glued behind a clause - the line does not parse");
});

await testAsync("SAFETY: the escalation rows below refuse too (POST /api/config {escalation})", async () => {
  assert(q("escalation-editor") != null, "the escalation editor never rendered");
  await refuses("escalation-row-cooling", "POST /api/config {escalation}");
});

// ========================================================= SKY > EPHEMERIS
// `/api/ephemeris` is on the mutation fence because the refresh makes the RIG
// dial out to CelesTrak and the MPC. The status GET stays open, so the card
// still reads - and that is asserted, because a card that refused to read would
// pass the lock assertion for the wrong reason.
await mount(createElement(EphemerisCard));

await testAsync("EPHEMERIS: REFRESH ELEMENTS refuses, and the status GET still happened", async () => {
  assert(q("settings-ephemeris") != null, "the ephemeris card never rendered");
  assert(q("ephemeris-row-satellites") != null,
    "the card never read the status - GET /api/ephemeris/status is NOT fenced and "
    + "over-gating it would hide how old the elements are");
  await refuses("ephemeris-refresh", "POST /api/ephemeris/refresh");
});

// ===================================================== the vacuity guard
// One editor on the SAME origin, in the SAME mount, whose route is deliberately
// NOT on the fence: `POST /api/calibration/build` builds masters from frames
// already on this box's disk, which is the science rather than the policy the
// fence protects. If REBUILD locked here, every assertion above would be
// "the relay flag locks everything" rather than "it locks what the rig refuses".
await mount(createElement(CalibrationLibraryEditor));

test("VACUITY GUARD: a route that is NOT fenced stays pressable on the same relay origin", () => {
  const build = q("cal-build");
  assert(build != null, "the master-library editor never rendered");
  eq(build.getAttribute("aria-disabled"), null,
    "REBUILD LIBRARY is locked over the relay, but POST /api/calibration/build is not "
    + "on the rig's fence - this would make every lock assertion in this file vacuous");
});

await act(async () => { root.render(null); });
resetRelayForTests();

// =================================================================== summary
const total = passed + failed;
console.log(`relayFenceDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
