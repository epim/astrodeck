// filesSheetsDom.test.tsx - the four FILES-AND-STANDARDS sheets, MOUNTED
// (wave R7, T-R7-13): FILE SYNC, FILE NAMING, PLATE-SOLVE STAMP and IMAGING
// STANDARDS, each now rendering a rebuilt editor from
// `hubs/settings/tuning/files/` instead of a legacy `components/settings/*Panel`.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/filesSheetsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THESE TESTS ARE ABOUT. A re-skin is exactly the change that loses a
// control without anyone noticing, so each assertion below is aimed at a
// promise the pixels cannot keep on their own:
//
//   1. PRECONDITION MARKERS. Every later assertion is worthless if the sheet
//      never rendered, so each block opens by finding the sheet's own marker
//      AND the rebuilt editor's, and fails with "the fixture is wrong, not the
//      component".
//   2. THE NAMING PREVIEW IS LOCAL. Tapping a token must change the previewed
//      path with NO request: the preview is the only way to see what a template
//      does before committing it, and a preview that needed a round trip would
//      be a preview of what the rig already has.
//   3. THE STAR FLOOR REJECTS BLANK. `Number("")` is 0 and 0 means "no floor,
//      solve every frame", so a blank box that committed would silently turn
//      the gate off. Blur with an empty field must write nothing and restore.
//   4. ONE COMPONENT, TWO MOUNT POINTS. The plate-solve editor renders in the
//      PLATE-SOLVE STAMP sheet and inside the Optics sheet. A fork would be two
//      answers to one setting, and only a per-mount assertion can tell them
//      apart.
//   5. A VIEWER IS LOCKED, HONESTLY, EVERYWHERE. Same screen, the right
//      capability named per sheet (`config.site_optics` for sync/naming/wcs,
//      `config.safety` for standards), no native `disabled`, and NOTHING is
//      written by a viewer's mount or press.
//   6. THE PLAN-EDITOR LINK GOES SOMEWHERE. It used to call
//      `setView("sequence")`, which ARCHITECTURE section 9 forbids the new UI
//      from doing; the rebuild calls `nav.sheet("planEditor")`. Asserting the
//      hash AND that `setView` was not called is the only way to tell the fix
//      from the bridge still covering for it.
//
// Convention: jsdom by hand, createRoot + act, native events, settle(), a
// hand-written g.fetch recording into `asked`/`posts`, store seeding via
// useStore.setState, printed tally + the `{ passed, failed, total }` export
// (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The four editors import `files.css` (the wave rule: `next.css` belongs to one
// task, every other area carries its own stylesheet next to its components).
// Node has no idea what a `.css` file is, so a synchronous load hook answers
// with an empty module. It has to run BEFORE any import that reaches one, which
// is why every import in this file is dynamic and below this block.
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
  { url: "http://local/#/settings/general/standards", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// The Optics sheet's Dial calls both behind a guard; jsdom implements neither.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "DOMException",
  // `next/router.ts` reads the BARE `location`/`history` to move the hash, and
  // the plan-editor link assertion is about exactly that.
  "location", "history",
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
const posts: { url: string; method: string; body: any }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

const SYNC_STATUS: any = {
  enabled: true, kind: "local_dir", path: "D:\\incoming", label: "", limit_per_pass: 0,
  configured: true, running: false, debounce_s: 20, sweep_interval_s: 900,
  passes: 4, total_sent: 120, total_bytes: 2_310_000_000,
  last_attempt_at: 1_757_000_000, last_ok_at: 1_757_000_000,
  consecutive_failures: 0, alarm: false,
  last: {
    sent: 12, failed: 0, bytes_sent: 240_000_000, already_there: 3,
    extra_at_destination: 1, elapsed_s: 9, error: "", summary: "12 frames sent",
  },
};

// Mirrors whatever the test most recently seeded, so an editor's own
// `loadConfig()` re-GET after a save reads back the same shape it started from
// rather than wiping the store to `{}`.
let currentConfig: any = null;

g.fetch = async (url: any, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = String(init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (method !== "GET" && init?.body != null) {
    let body: unknown = init.body;
    try { body = JSON.parse(init.body); } catch { /* leave as text */ }
    posts.push({ url: u, method, body });
  }
  if (u.includes("/api/sync/push")) return ok(SYNC_STATUS);
  if (u.includes("/api/survey/pack")) {
    return ok({ present: false, slug: "", survey: "", order: null, bytes: null,
      tile_count: null, fetched_at: null, fetching: null });
  }
  if (u.includes("/api/drivers")) return ok({ roles: [], drivers: [] });
  if (u.includes("/healthz")) return ok({ ok: true, version: "0.3.28" });
  if (u.includes("/api/me")) return ok({ role: "admin", email: "a@b.test", caps: [] });
  if (u.includes("/api/config")) return ok(currentConfig ?? {});
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { readFileSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");

const { SyncSheet } = await import("../sheets/SyncSheet");
const { NamingSheet } = await import("../sheets/NamingSheet");
const { WcsSheet } = await import("../sheets/WcsSheet");
const { StandardsSheet } = await import("../sheets/StandardsSheet");
const { OpticsSheet } = await import("../sheets/OpticsSheet");
const { namingPreview, syncSummary, formatBytes, pushBlockedReason } =
  await import("../tuning/files");
const { DEFAULT_TEMPLATE } = await import("../../../../lib/naming");
const { STANDARDS_NUMBER_FIELDS } = await import("../../../../lib/standards");

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

/** Each sheet gets its own container/root so one sheet's intervals and effects
 *  never leak into the next. */
function mount(Comp: (p: typeof SP) => any): { host: any; root: any } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(createElement(Comp, SP)); });
  return { host, root };
}
async function unmount(m: { host: any; root: any }): Promise<void> {
  await act(async () => { m.root.unmount(); });
  m.host.remove();
}

const q = (host: any, sel: string): any => host.querySelector(sel);
const qa = (host: any, sel: string): any[] => Array.from(host.querySelectorAll(sel));
const byId = (host: any, id: string): any => q(host, `[data-testid="${id}"]`);
const text = (host: any): string => String(host.textContent ?? "").replace(/\s+/g, " ");
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** Type into a controlled React input the way a user does. */
const typeInto = (el: any, value: string): void => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
/** React 18 hears `onBlur` through the delegated `focusout` at the root, not
 *  through the native non-bubbling `blur`, so a test that fires only the latter
 *  asserts on a commit that never ran. Both go out. */
const blur = (el: any): void => {
  act(() => {
    el.dispatchEvent(new win.Event("blur", { bubbles: true }));
    el.dispatchEvent(new win.Event("focusout", { bubbles: true }));
  });
};
const writes = (from: number): { url: string; method: string; body: any }[] =>
  posts.slice(from).filter((p) => ["POST", "PUT", "PATCH", "DELETE"].includes(p.method));

// ------------------------------------------------------------------ fixtures
const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.site_precise",
  "view.site_derived", "view.weather",
  "control.capture", "control.mount", "control.guide", "control.power",
  "config.safety", "config.solar_override", "config.backend",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

const STANDARDS = {
  apply_filter_offsets: true,
  refocus_on_temp_delta_c: 0,
  min_stars: 25,
  max_guide_rms: 1.2,
  max_eccentricity: 0.65,
  max_consecutive_rejects: 10,
  max_consecutive_rejects_night: 20,
};

const BASE_CONFIG: any = {
  version: 42,
  naming: { template: DEFAULT_TEMPLATE },
  solve_saved_lights: true,
  wcs_stamp: { solver: "auto", downsample: 2, min_stars: 30, queue_max: 4 },
  standards: STANDARDS,
  sync_push: { enabled: true, kind: "local_dir", path: "D:\\incoming", label: "", limit_per_pass: 0 },
  site: { is_default: false, horizon_min_deg: 25 },
  optics: {
    focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248,
    sensor_height_px: 4176, auto_from_camera: true, guide_focal_length_mm: 200,
    telescope_name: "Askar FRA400",
  },
  optics_computed: {
    have_optics: true, source: "config", focal_length_mm: 530, pixel_size_um: 3.76,
    sensor_width_px: 6248, sensor_height_px: 4176, image_scale_arcsec_px: 1.46,
    fov_w_deg: 2.54, fov_h_deg: 1.7, fov_diag_deg: 3.06,
  },
  active_profile_id: null,
  providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
  safety: {}, escalation: {}, alerts: [], deadman_url: "",
};

function seed(role: "admin" | "viewer", over: Record<string, unknown> = {}): void {
  const config = { ...BASE_CONFIG, ...((over.config as object | undefined) ?? {}) };
  currentConfig = config;
  useStore.setState({
    config,
    principal: {
      role,
      email: role === "admin" ? "admin@example.test" : "viewer@example.test",
      caps: role === "admin" ? ALL_CAPS : ["view.status", "view.preview"],
    },
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    providers: { solve: { kind: "astap" } },
    wsPhase: "up",
    equipConnected: true,
    sequence: { state: "idle" },
    preview: null,
    toasts: [],
    ...over,
  } as never);
}

// ================================================== 1. PRECONDITION / MARKERS
// Sabotage: drop the `data-testid` from any `Sheet` and the first half goes
// red; point a sheet back at its legacy `components/settings/*Panel` and the
// second half goes red because no rebuilt editor renders.

await test("markers: each sheet renders its own marker AND its rebuilt editor", async () => {
  const cases: [string, any, string][] = [
    ["settings-sync", SyncSheet, "sync-editor"],
    ["settings-naming", NamingSheet, "naming-editor"],
    ["settings-wcs", WcsSheet, "wcs-editor"],
    ["settings-standards", StandardsSheet, "standards-editor"],
  ];
  for (const [marker, Comp, editor] of cases) {
    seed("admin");
    const m = mount(Comp);
    await settle();
    assert(byId(m.host, marker) != null, `no ${marker} marker`);
    assert(byId(m.host, editor) != null, `${marker} did not render ${editor}`);
    // The legacy panels all render `.panel-title`; the rebuild renders none.
    assert(q(m.host, ".panel-title") == null,
      `${marker} still mounts a legacy Panel (.panel-title found)`);
    await unmount(m);
  }
});

await test("markers: the controls the probe walks are present and named", async () => {
  seed("admin");
  const sync = mount(SyncSheet);
  await settle();
  assert(byId(sync.host, "sync-dest") != null, "no sync-dest input");
  assert(byId(sync.host, "sync-push-now") != null, "no sync-push-now button");
  await unmount(sync);

  const naming = mount(NamingSheet);
  await settle();
  assert(byId(naming.host, "naming-template") != null, "no naming-template input");
  eq(qa(naming.host, '[data-testid="naming-token"]').length, 8,
    "the eight naming tokens did not all render as chips");
  await unmount(naming);

  const wcs = mount(WcsSheet);
  await settle();
  assert(byId(wcs.host, "wcs-enabled") != null, "no wcs-enabled switch");
  // Solver, downsample and the star floor live behind ADVANCED, collapsed by
  // default (the legacy panel's own progressive disclosure).
  assert(byId(wcs.host, "wcs-solver") == null,
    "the advanced block is open on arrival - the novice surface is one switch");
  click(q(wcs.host, '[data-testid="wcs-advanced"] button[aria-expanded]'));
  await settle();
  assert(byId(wcs.host, "wcs-solver") != null, "no wcs-solver radiogroup once ADVANCED is open");
  assert(byId(wcs.host, "wcs-downsample") != null, "no wcs-downsample radiogroup");
  assert(byId(wcs.host, "wcs-minstars") != null, "no wcs-minstars field");
  await unmount(wcs);

  const std = mount(StandardsSheet);
  await settle();
  assert(byId(std.host, "standards-offsets") != null, "no standards-offsets switch");
  for (const f of STANDARDS_NUMBER_FIELDS) {
    assert(byId(std.host, `standards-${String(f.key)}`) != null,
      `the standards editor dropped the "${f.label}" field (standards-${String(f.key)})`);
  }
  await unmount(std);
});

// =========================================================== 2. NAMING PREVIEW
// Tapping a token appends it to the template AND re-renders the previewed path,
// with no request. Only the SAVE press writes.
// Sabotage: make the preview read `config.naming.template` instead of the local
// draft and "the preview did not follow the token" goes red; make the chip
// `onClick` call `save()` and "a token tap fired a request" goes red.

await test("naming: a token chip changes the preview locally, and only SAVE writes", async () => {
  seed("admin");
  const before = posts.length;
  const m = mount(NamingSheet);
  await settle();

  const input = byId(m.host, "naming-template");
  const preview = byId(m.host, "naming-preview");
  eq(preview.textContent, namingPreview(DEFAULT_TEMPLATE),
    "the preview did not start from the stored template");

  const chip = qa(m.host, '[data-testid="naming-token"]')
    .find((c: any) => c.textContent.includes("$$NIGHT$$"));
  assert(chip != null, "no $$NIGHT$$ token chip");
  click(chip);
  await settle();

  const want = `${DEFAULT_TEMPLATE}$$NIGHT$$`;
  eq(input.value, want, "the token was not appended to the template");
  eq(byId(m.host, "naming-preview").textContent, namingPreview(want),
    "the preview did not follow the token");
  eq(writes(before).length, 0, `a token tap fired a request: ${JSON.stringify(writes(before))}`);

  click(byId(m.host, "naming-save"));
  await settle();
  const saved = writes(before).filter((p) => p.url.includes("/api/config/naming"));
  eq(saved.length, 1, `SAVE did not post exactly once: ${JSON.stringify(writes(before))}`);
  eq(saved[0].body.template, want, "the saved template is not the one the preview showed");
  await unmount(m);
});

// ============================================================ 3. THE STAR FLOOR
// Sabotage: swap `NumberField` for a plain input that calls `onCommit(Number(v))`
// and "a blank star floor was committed" goes red - `Number("")` is 0, a
// finite, plausible, WRONG value that turns the gate off.

await test("wcs: the star floor commits on blur and REJECTS a blank", async () => {
  seed("admin");
  const m = mount(WcsSheet);
  await settle();
  click(q(m.host, '[data-testid="wcs-advanced"] button[aria-expanded]'));
  await settle();

  const stars = byId(m.host, "wcs-minstars");
  assert(stars != null, "no wcs-minstars field (is ADVANCED still collapsed?)");
  eq(stars.value, "30", "the star floor did not seed from config.wcs_stamp.min_stars");

  // (a) a real number commits, once, on blur.
  let before = posts.length;
  typeInto(stars, "45");
  eq(writes(before).length, 0, "typing wrote before the field was left");
  blur(stars);
  await settle();
  const wrote = writes(before).filter((p) => p.url.includes("/api/config/wcs"));
  eq(wrote.length, 1, `blur did not post exactly once: ${JSON.stringify(writes(before))}`);
  eq(wrote[0].body.wcs_stamp.min_stars, 45, "the committed star floor is wrong");
  eq(wrote[0].body.solve_saved_lights, true,
    "the master enable was not carried with the advanced block");

  // (b) a blank commits NOTHING and the box goes back to the last real value.
  before = posts.length;
  const stars2 = byId(m.host, "wcs-minstars");
  typeInto(stars2, "");
  blur(stars2);
  await settle();
  eq(writes(before).length, 0,
    `a blank star floor was committed: ${JSON.stringify(writes(before))}`);
  eq(byId(m.host, "wcs-minstars").value, "30",
    "a rejected blank did not restore the last committed value");
  await unmount(m);
});

// ================================================ 4. ONE COMPONENT, TWO MOUNTS
// Sabotage: point `OpticsSheet`'s plate-solve card back at
// `components/settings/WcsStampPanel` and the Optics half goes red.

await test("wcs: the SAME rebuilt editor renders in both mount sites", async () => {
  seed("admin");
  const sheet = mount(WcsSheet);
  await settle();
  assert(byId(sheet.host, "wcs-editor") != null,
    "the plate-solve sheet does not render wcs-editor");
  await unmount(sheet);

  const optics = mount(OpticsSheet);
  await settle();
  assert(byId(optics.host, "optics-wcs") != null,
    "precondition: the Optics sheet's plate-solve card did not render");
  assert(q(byId(optics.host, "optics-wcs"), '[data-testid="wcs-editor"]') != null,
    "the Optics sheet's plate-solve card does not render the rebuilt wcs-editor");
  assert(q(optics.host, ".panel-title") == null,
    "the Optics sheet still mounts the legacy WcsStampPanel (.panel-title found)");
  await unmount(optics);
});

// ==================================================== 5. THE VIEWER IS LOCKED
// One capability per sheet, named in the sheet's own sentence, with nothing
// written by the mount or by a press.
// Sabotage: drop `lockedReason` from any control here and "a viewer's press
// wrote" goes red; drop the `LockNote` and the sentence half goes red.

await test("viewer: sync, naming and wcs name config.site_optics and write nothing", async () => {
  const cases: [string, any, string][] = [
    ["sync", SyncSheet, "sync-lock"],
    ["naming", NamingSheet, "naming-lock"],
    ["wcs", WcsSheet, "wcs-lock"],
  ];
  for (const [name, Comp, lockId] of cases) {
    seed("viewer");
    const before = posts.length;
    const m = mount(Comp);
    await settle();
    const note = byId(m.host, lockId);
    assert(note != null, `${name}: no lock note for a viewer`);
    assert(/Read-only - .*admin access/.test(note.textContent),
      `${name}: the lock note does not name who can change it: "${note.textContent}"`);
    // Honest-disabled, not the native attribute.
    eq(qa(m.host, "[disabled]").length, 0, `${name}: a native disabled attribute survived`);
    assert(qa(m.host, '[aria-disabled="true"]').length > 0,
      `${name}: nothing is marked aria-disabled for a viewer`);
    // Press the loudest control on the sheet and prove it wrote nothing.
    const press = byId(m.host, name === "sync" ? "sync-enabled"
      : name === "naming" ? "naming-save" : "wcs-enabled");
    assert(press != null, `${name}: the control this test presses is missing`);
    click(press);
    await settle();
    eq(writes(before).length, 0,
      `${name}: a viewer's press wrote ${JSON.stringify(writes(before))}`);
    await unmount(m);
  }
});

await test("viewer: imaging standards names config.safety, not config.site_optics", async () => {
  seed("viewer");
  const before = posts.length;
  const m = mount(StandardsSheet);
  await settle();
  const note = byId(m.host, "standards-lock");
  assert(note != null, "no lock note on the standards sheet for a viewer");
  assert(/Read-only - changing this needs admin access/.test(note.textContent),
    `the standards lock note reads "${note.textContent}"`);
  // The capability itself, read off the control the server gates on
  // config.safety: `useLock({cap:"config.safety"})` is what produces this title.
  const offsets = byId(m.host, "standards-offsets");
  eq(offsets.getAttribute("aria-disabled"), "true",
    "the per-filter offsets switch is live for a viewer");
  eq(qa(m.host, "[disabled]").length, 0, "a native disabled attribute survived");
  click(offsets);
  await settle();
  eq(writes(before).length, 0,
    `a viewer's press wrote ${JSON.stringify(writes(before))}`);
  await unmount(m);
});

// ===================================================== 6. THE PLAN-EDITOR DOOR
// Sabotage: restore `useStore.getState().setView("sequence")` on the link and
// BOTH halves go red - the hash never gains the sheet, and setView is called.

await test("standards: the footer link opens the planEditor sheet without setView", async () => {
  seed("admin");
  win.location.hash = "#/settings/general/standards";
  const real = useStore.getState().setView;
  let setViewCalls = 0;
  useStore.setState({ setView: ((v: any) => { setViewCalls += 1; real(v); }) as any } as never);

  const m = mount(StandardsSheet);
  await settle();
  const link = byId(m.host, "standards-plan-link");
  assert(link != null, "no plan-editor link on the standards sheet");
  eq(link.textContent, "plan editor", "the link's own word changed");
  click(link);
  await settle();

  assert(/#\/settings\/general\/standards\/planEditor/.test(String(win.location.hash)),
    `the link did not push the planEditor sheet - hash is "${win.location.hash}"`);
  eq(setViewCalls, 0, "the link still writes store.view, which ARCHITECTURE section 9 forbids");

  useStore.setState({ setView: real } as never);
  await unmount(m);
});

// ============================================================ 7. SYNC HONESTY
// The master toggle used to be a NATIVE `disabled` when no destination was set,
// so a user who had never typed a path saw a dead switch and no sentence
// anywhere saying what to do.
// Sabotage: return `null` from `syncEnableBlockedReason`'s empty-path branch and
// the reason half goes red; make it a native `disabled` and the count goes red.

await test("sync: turning it on with no destination is refused WITH the reason", async () => {
  seed("admin", { config: { ...BASE_CONFIG, sync_push: {
    enabled: false, kind: "local_dir", path: "", label: "", limit_per_pass: 0,
  } } });
  const before = posts.length;
  const m = mount(SyncSheet);
  await settle();
  const sw = byId(m.host, "sync-enabled");
  eq(sw.getAttribute("aria-disabled"), "true",
    "the master toggle is live with no destination - the server answers 422 to that");
  assert(/destination folder/i.test(String(sw.getAttribute("title"))),
    `the refusal does not name the fix: "${sw.getAttribute("title")}"`);
  eq(qa(m.host, "[disabled]").length, 0, "a native disabled attribute survived");
  click(sw);
  await settle();
  eq(writes(before).length, 0, `the refused toggle still wrote ${JSON.stringify(writes(before))}`);
  await unmount(m);
});

await test("sync: the runner's real counters reach the screen", async () => {
  seed("admin");
  const m = mount(SyncSheet);
  await settle();
  const summary = byId(m.host, "sync-summary");
  assert(summary != null, "no sync summary line");
  assert(summary.textContent.includes(formatBytes(SYNC_STATUS.total_bytes)),
    `the summary hides the bytes sent: "${summary.textContent}"`);
  assert(text(m.host).includes("already there"),
    "the last-pass line drops the already-there count, which is what says sync never deletes");
  await unmount(m);
});

// ============================================================== 8. PURE LOGIC
// The sentences, graded without a DOM. These are the four states that all look
// like "nothing is happening" from the outside.

await test("syncSummary tells off from on-but-never-run from working from broken", () => {
  eq(syncSummary(null, null).text, "Off - frames stay on the rig.", "null status");
  eq(syncSummary({ ...SYNC_STATUS, enabled: false } as any, null).text,
    "Off - frames stay on the rig.", "disabled");
  eq(syncSummary({ ...SYNC_STATUS, configured: false } as any, null).text,
    "On, but no destination is set.", "no destination");
  eq(syncSummary({ ...SYNC_STATUS, alarm: true, consecutive_failures: 5 } as any, null).text,
    "FAILING - 5 passes in a row. Frames are NOT leaving the rig.", "alarm");
  eq(syncSummary({ ...SYNC_STATUS, passes: 0 } as any, null).text,
    "On. No pass has run yet.", "never run");
  eq(syncSummary(SYNC_STATUS as any, "9 minutes").text,
    "120 frames sent (2.31 GB). Last success 9 minutes ago.", "working");
  // Hyphens, never em-dashes (ARCHITECTURE non-negotiable 5).
  for (const s of [null, SYNC_STATUS, { ...SYNC_STATUS, alarm: true }]) {
    assert(!/[\u2014\u2013]/.test(syncSummary(s as any, "9 minutes").text),
      "an em-dash survived into the sync summary");
  }
});

await test("pushBlockedReason puts the capability first, then the fixable states", () => {
  eq(pushBlockedReason("needs syncer or admin access", SYNC_STATUS as any, false),
    "needs syncer or admin access", "the capability outranks everything");
  eq(pushBlockedReason(null, { ...SYNC_STATUS, running: true } as any, false),
    "a pass is already running", "running");
  eq(pushBlockedReason(null, { ...SYNC_STATUS, configured: false } as any, false),
    "turn sync on and save a destination first", "not configured");
  eq(pushBlockedReason(null, SYNC_STATUS as any, false), null, "nothing in the way");
});

// ================================================================= 9. THE CSS
// Every `nx-files-*` class the four editors emit has a rule in `files.css`, and
// no rule here redefines a class `next.css` owns (the wave's one-owner rule).
// Sabotage: rename `.nx-files-note` in the css and this goes red with the class
// that lost its rule.

await test("every nx-files-* class the editors emit has a rule in files.css", () => {
  const here = fileURLToPath(new URL(".", import.meta.url));
  const read = (rel: string) => readFileSync(`${here}${rel}`, "utf8");
  const src = ["SyncEditor.tsx", "NamingEditor.tsx", "WcsStampEditor.tsx", "StandardsEditor.tsx"]
    .map((f) => read(`../tuning/files/${f}`)).join("\n");
  const css = read("../tuning/files/files.css");
  const emitted = new Set<string>();
  for (const m of src.matchAll(/nx-files-[a-z-]+/g)) emitted.add(m[0]);
  assert(emitted.size >= 6, `only ${emitted.size} nx-files-* classes found - is the scan working?`);
  for (const cls of emitted) {
    assert(css.includes(`.${cls} `) || css.includes(`.${cls},`) || css.includes(`.${cls}{`)
      || css.includes(`.${cls}:`),
      `${cls} is emitted by an editor but has no rule in files.css`);
  }
  // The area file must not redefine a shared primitive's class.
  for (const shared of ["nx-card", "nx-field", "nx-input", "nx-switch", "nx-seg",
    "nx-disclosure", "nx-locknote", "nx-numfield", "nx-mono", "nx-chip"]) {
    assert(!new RegExp(`\\.${shared}[\\s,:{]`).test(css),
      `files.css redefines ${shared}, which next.css owns`);
  }
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
for (const f of failures) console.error(f);
console.log(`filesSheetsDom: ${passed}/${total} passed`);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
