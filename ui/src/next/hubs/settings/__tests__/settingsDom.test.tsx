// settingsDom.test.tsx - the SETTINGS hub root, the GENERAL screen, the
// first-time-setup card and the PHONE preferences (T-SET-1, plan E.3),
// MOUNTED and pressed.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/settingsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).
//
// What each test guards, and what goes RED if the guard is removed:
//
//  1. hub marker + the versions line. If `SettingsHub` stopped rendering
//     `data-testid="hub-settings"` the precondition assert fails before any
//     content assertion runs ("no element found - the fixture is wrong, not the
//     component"). If the honest-absent rule were dropped and the engine
//     version were printed before /healthz answered, the FIRST versions
//     assertion (`app dev` alone, pre-settle) fails.
//  2. the five group headers. Deleting any group (or renaming its testid)
//     fails that group's assert by name - MORE included, which is the group the
//     design's own screenshot does not show and is therefore the easiest to
//     lose.
//  3. the setup ring. With three of five steps done the ring's aria-label is
//     "OF 5 3": if `computeSetup` stopped counting, or the card were wired to
//     `wizard.doneCount` (six steps, not five), the label reads a different
//     number. The card's sub names the FIRST incomplete step; switching it to
//     the last would still print a real step, which is exactly why the
//     assertion names "connect the devices" and not "some step".
//  4. the setup card's dismissal. Seeding `coachSeen` with the legacy
//     `first-run-wizard` key must hide the card; if the card stopped reading
//     that key, a user who finished setup in the classic UI is shown it again
//     and this assertion fails.
//  5. tapping CONNECTION. If the row stopped calling `nav.sheet("connection")`
//     - or opened a modal outside the router, which the browser Back button
//     could not close - the hash assertion fails.
//  6. NIGHT MODE. If the row wrote the key itself instead of calling
//     `store.toggleNight()`, the store assertion fails; if it called the action
//     but the action stopped persisting, the localStorage assertion fails.
//     Both are asserted, because they are different defects.
//  7/8. TOUCH SIZE and AUTO-LOCK write their EXISTING key names
//     (`astrodeck-touch-size` = "on", `astrodeck-autolock` = "180000"). These
//     are migration guards: renaming a key silently resets every phone that
//     has ever been set up, and nothing on screen would say so. AUTO-LOCK is
//     also the row that gives an existing, previously unreachable setting its
//     first UI (plan F.3).
//  9. the viewer. The read-only banner sentence must render, the SITES row must
//     print the derived-privacy line instead of coordinates, and NO
//     `/api/locations` request may be issued - that route needs
//     `config.site_optics`, and a non-holder issuing it would only eat a 403.
//     Removing the cap gate on the effect fails the `asked` assertion.
// 10. the admin-gated MORE row. FACTORY RESET is rendered for a viewer, not
//     hidden, and its sub names what it would need. Hiding it fails the first
//     assert; dropping the reason fails the second.
// 11. the setup SHEET. All five rows, the right-hand word following `done`,
//     the design's footer paragraph, and a step's GO landing on the route the
//     machine names. DONE must mark the LEGACY `first-run-wizard` key in
//     `astrodeck-coach-seen`: inventing a second flag would show the guide
//     again to someone who finished it in the classic UI, and the localStorage
//     assertion is what catches that.
// 12. the quick-defaults sheet, now over the RIG's planning block (D-FU-1).
//     With nothing learned it must SAY so and offer no reset - seeding itself
//     from the wheel and calling the seed "learned from your last session" is
//     the defect the empty-state assertion guards, and the flag it reads is
//     `config.planning.quick.learned` rather than "a browser key exists". With
//     a learned block, the filter rows come from the wheel and a BLACKOUT slot
//     is not among them.
//
//     THE EDIT ASSERTION NAMES THE CONSUMER, NOT A KEY THIS SHEET OWNS. It once
//     asserted that the sheet wrote `astrodeck-next-quick`, which was true and
//     useless: the sheet owned that key, nothing else read it, and the assertion
//     was green for the entire time Settings and the Sky hub were two
//     disconnected halves (review #4, #46). What it asserts now is that
//     `usePlanning().quick` - the same hook, the same module state, that
//     `sky/sheets/quick.tsx` hands to `wheelModel` - sees the edit. Point this
//     sheet at a private key again and it goes red.
//
//     AND IT ASSERTS THE SHAPE ON THE WIRE. A whole-block quick write looks
//     identical on screen and erases every per-filter exposure a narrower
//     client never knew about, so the recorded PUT body must carry exactly the
//     key that changed (`planning.py:122-153`). The viewer case is here for the
//     same reason the other read-only cases are: the same screen, the reason in
//     words, and nothing sent.
// 13. the composed registry. Every name from all four settings tasks must be
//     present, and `sites`/`horizon` must be the SKY hub's own component
//     objects - `hubs/index.ts` throws at load on two DIFFERENT components
//     under one name, so registering a copy would break the whole app rather
//     than just this hub.
// 14. the deleted placeholder. `hubs/settings/sheets.ts` SHADOWED the
//     `sheets/` directory: with both present, `./settings/sheets` resolved to
//     the file and every sheet in the directory was unreachable while the app
//     still compiled and ran. A file assertion is the only thing that catches
//     that, because a module resolution that picks the wrong file looks like a
//     working build.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The Safety tuning sheet mounts `EscalationEditor` (wave R7, T-R7-9), which
// imports its area's `safety.css`, and Node cannot load a stylesheet. The same
// synchronous hook `hubBoundary.test.tsx` installs answers with an empty module.
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
  { url: "http://local/#/settings/general", pretendToBeVisual: true },
);
const win = dom.window as any;

Object.defineProperty(win, "isSecureContext", { value: true, configurable: true });
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
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// Every request is recorded so a test can assert what was, and was NOT, asked
// for - the point of the viewer test below.
const asked: string[] = [];
const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});

// ------------------------------------------------------- the planning block
// The quick-session defaults are the RIG's now (D-FU-1), so this file stubs
// `GET/PUT /api/planning` and MERGES the way the server does: `quick` nested
// partially (`planning.py:122-153`), `pool` replaced whole. A stub that
// answered with the block it was sent would be green whether the sheet sent one
// changed key or all seven, which is the one thing block 8 has to grade.
interface WireQuick {
  hours: number; dawn: boolean;
  on: Record<string, boolean>; exp: Record<string, number>;
  extras: Record<string, boolean>; dither_n: number; learned: boolean;
}
const NOTHING_LEARNED: WireQuick = {
  hours: 2, dawn: false, on: {}, exp: {}, extras: {}, dither_n: 3, learned: false,
};
let planningBlock: { quick: WireQuick; pool: string[] } =
  { quick: { ...NOTHING_LEARNED }, pool: [] };
const planningPuts: { quick?: Record<string, unknown>; pool?: string[] }[] = [];

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/planning")) {
    if ((init?.method ?? "GET") === "PUT") {
      const body = JSON.parse(init?.body ?? "{}");
      planningPuts.push(body);
      if (body.quick) planningBlock.quick = { ...planningBlock.quick, ...body.quick };
      if (body.pool) planningBlock.pool = body.pool;
    }
    return ok({ quick: { ...planningBlock.quick }, pool: [...planningBlock.pool] });
  }
  if (u.includes("/healthz")) return ok({ ok: true, version: "1.9.2" });
  if (u.includes("/api/remote/status")) {
    return ok({
      enabled: false, home_id: null, relay_host: null, connected: false,
      last_error: null, since_unix: null, gen: null, via: "direct",
    });
  }
  if (u.includes("/api/profiles")) return ok([]);
  if (u.includes("/api/locations")) return ok([]);
  if (u.includes("/api/sessions")) return ok({ sessions: [] });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { nav } = await import("../../../router");

// THE ROUTE IS SET THROUGH `nav`, NOT BY ASSIGNING `location.hash` (review #59).
// The old form wrote the hash by hand and then called the router's
// `resetRouterCacheForTests` hatch to drop its cached parse - a step no browser
// and no production caller ever performs, so every assertion below ran over a
// route that had been arrived at differently from the way the app arrives at
// one. `nav.go` is the path the screens themselves take: it canonicalises,
// pushes, and emits synchronously, so the test needs no hatch and grades the
// real navigation.
const { SettingsHub } = await import("../SettingsHub");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const host = win.document.getElementById("root") as any;
const q = (sel: string): any => host.querySelector(sel);
const text = (sel: string): string => (q(sel)?.textContent ?? "");
const click = (el: any) => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};

const ADMIN = {
  role: "admin", email: "night-owner@example.test",
  caps: [
    "view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts", "admin.users",
    "system.update",
  ],
};
const VIEWER = { role: "viewer", email: "guest@example.test", caps: ["view.status", "view.preview"] };

/** Three of five done: PAIR (link up + identity), OPTICS (the server computed a
 *  field) and SITE + HORIZON (a real site with a drawn line). CONNECT THE
 *  DEVICES and FIRST LIGHT are not - and they are not adjacent, so "the first
 *  incomplete" and "the last incomplete" name different steps. */
const seed = (principal: unknown, over: Record<string, unknown> = {}) => {
  useStore.setState({
    principal,
    wsPhase: "up",
    equipConnected: false,
    coachSeen: {},
    previews: [],
    plan: { targets: [], name: "", id: null },
    flows: { libraryLoaded: true, cards: [], folders: [], libraryError: null },
    safety: { connected: false, reading: null, streak: 0 },
    status: {
      connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [],
      disk: { free_gb: 412, low: false, critical: false },
    },
    site: {
      name: "Back lawn", latitude: 47.6104, longitude: -122.3312,
      elevation_m: 40, is_default: false, horizon_min_deg: 20,
    },
    config: {
      version: 1,
      active_profile_id: null,
      site: {
        name: "Back lawn", latitude: 47.6104, longitude: -122.3312,
        elevation_m: 40, is_default: false, horizon_min_deg: 20,
      },
      optics: {
        focal_length_mm: 530, pixel_size_um: 3.76,
        sensor_width_px: 6248, sensor_height_px: 4176,
        auto_from_camera: false, telescope_name: "",
      },
      optics_computed: {
        have_optics: true, source: "config",
        focal_length_mm: 530, pixel_size_um: 3.76,
        sensor_width_px: 6248, sensor_height_px: 4176,
        image_scale_arcsec_px: 1.46, fov_w_deg: 2.54, fov_h_deg: 1.7,
        fov_diag_deg: 3.06,
      },
      safety: { horizon: [[0, 20], [90, 38], [180, 25], [270, 30]] },
      escalation: {}, alerts: [], deadman_url: "",
    },
    ...over,
  } as never);
};

// ======================================================== 1. GENERAL, as admin
nav.go("/settings/general");
seed(ADMIN);

const root = createRoot(host);
act(() => { root.render(createElement(SettingsHub)); });

test("precondition: the hub rendered its marker", () => {
  assert(q('[data-testid="hub-settings"]') != null,
    "no [data-testid=hub-settings] - the fixture is wrong, not the component");
  assert(q('[data-testid="screen-settings-general"]') != null,
    "the GENERAL sub-nav default did not render its screen");
});

test("the versions line prints the app alone until /healthz answers", () => {
  eq(text('[data-testid="settings-versions"]'), "app dev",
    "a placeholder engine version was printed before /healthz came back:");
});

await settle();

test("the versions line names the engine once /healthz answers", () => {
  eq(text('[data-testid="settings-versions"]'), "engine 1.9.2 · app dev", "versions:");
});

test("the five group headers are all on screen", () => {
  for (const id of ["rig", "sky", "phone", "library", "more"]) {
    assert(q(`[data-testid="group-${id}"]`) != null, `the ${id.toUpperCase()} group is missing`);
  }
  assert(/DANGER ZONE/.test(host.textContent), "the FACTORY RESET danger zone label is missing");
});

// ------------------------------------------------------------ 2. the setup card
test("the setup card shows 3 of 5 and names the FIRST incomplete step", () => {
  const ring = q('[data-testid="setup-ring"]');
  assert(ring != null, "no setup ring - the card did not render for an incomplete rig");
  eq(ring.getAttribute("aria-label"), "OF 5 3", "the ring's count:");
  const sub = text('[data-testid="setup-next"]');
  assert(sub.startsWith("next: connect the devices"),
    `the card named the wrong step (the first incomplete one is step 2): ${sub}`);
});

// ------------------------------------------------------- 3. tapping CONNECTION
test("tapping CONNECTION opens the connection sheet as route state", () => {
  const row = q('[data-testid="row-connection"]');
  assert(row != null, "no CONNECTION row - the fixture is wrong, not the component");
  click(row);
  eq(win.location.hash, "#/settings/general/connection",
    "the row did not push the sheet onto the route (the Back button must close it):");
});

act(() => { nav.go("/settings/general"); });
await settle();

// ------------------------------------------------------------ 4. PHONE prefs
test("the NIGHT MODE switch calls toggleNight and the store persists the key", () => {
  const before = useStore.getState().night;
  const sw = q('[data-testid="switch-night"]');
  assert(sw != null, "no NIGHT MODE switch - the fixture is wrong, not the component");
  click(sw);
  eq(useStore.getState().night, !before, "store.night after the tap:");
  eq(win.localStorage.getItem("astrodeck-night"), !before ? "1" : "0",
    "astrodeck-night after the tap:");
  click(sw);   // leave the app the way we found it
});

test("TOUCH SIZE writes the EXISTING astrodeck-touch-size key", () => {
  const large = q('[data-testid="seg-touch-size"] [data-value="on"]');
  assert(large != null, "no Large option in the touch-size radiogroup");
  click(large);
  eq(win.localStorage.getItem("astrodeck-touch-size"), "on", "astrodeck-touch-size:");
  eq(useStore.getState().touch.touchSizing, "on", "store.touch.touchSizing:");
});

test("AUTO-LOCK writes the EXISTING astrodeck-autolock key in milliseconds", () => {
  const three = q('[data-testid="seg-autolock"] [data-value="180000"]');
  assert(three != null, "no 3 min option in the auto-lock radiogroup");
  click(three);
  eq(win.localStorage.getItem("astrodeck-autolock"), "180000", "astrodeck-autolock:");
  eq(useStore.getState().touch.autoLockMs, 180000, "store.touch.autoLockMs:");
});

// DOWNLOADS is asserted at the CONSUMER end, deliberately (review #5/#46).
//
// The test this replaces read the key the sheet itself owns
// (`astrodeck-next-dl-pref` = "jpeg"), which was true and told nobody that
// NOTHING ELSE READ IT: the Files sheet's DOWNLOAD button decides the format
// from `filesData.readDlPref`, a different key under a different value
// spelling, so tapping JPEG here changed nothing and the Files sheet went on
// offering FITS. A guard that asserts a sheet against its own key can never see
// that. This one goes round the loop: tap here, read back through the module
// the consumer imports.
const { readDlPref, DLPREF_KEY } = await import("../../session/sheets/filesData");

test("DOWNLOADS is read back by the Files sheet's own reader, not just written", () => {
  const jpeg = q('[data-testid="seg-downloads"] [data-value="jpg"]');
  assert(jpeg != null,
    "no JPEG option in the downloads radiogroup, or its stored id is not the one "
    + "`filesData` reads (\"jpg\")");
  click(jpeg);
  eq(readDlPref(), "jpg",
    "the Files sheet's reader does not see this row's pick - two keys again");
  eq(win.localStorage.getItem(DLPREF_KEY), "jpg",
    "and it is not under the key that module owns");

  const fits = q('[data-testid="seg-downloads"] [data-value="fits"]');
  assert(fits != null, "no FITS option");
  click(fits);
  eq(readDlPref(), "fits", "the round trip only works in one direction");
});

// -------------------------------------------- 5. a dismissed guide hides the card
act(() => { useStore.setState({ coachSeen: { "first-run-wizard": true } } as never); });

test("a coach map carrying first-run-wizard hides the setup card", () => {
  assert(q('[data-testid="setup-card"]') == null,
    "the setup card is still shown to someone who already finished the guide");
});

act(() => { useStore.setState({ coachSeen: {} } as never); });

// ============================================================== 6. the viewer
act(() => { root.unmount(); });

const askedBefore = asked.length;
const root2 = createRoot(host);
seed(VIEWER);
act(() => { root2.render(createElement(SettingsHub)); });
await settle();

test("a viewer gets the read-only sentence, naming what it would need", () => {
  const banner = q('[data-testid="banner-readonly"]');
  assert(banner != null, "no read-only banner for a viewer");
  assert(/Read-only/.test(banner.textContent),
    `the viewer sentence is missing: ${banner.textContent}`);
  assert(/connecting rigs and editing profiles needs/.test(banner.textContent),
    `the banner never names the blocker: ${banner.textContent}`);
});

test("a viewer sees the site row's derived line, never the coordinates", () => {
  const sub = text('[data-testid="row-sites"]');
  assert(/precise location hidden for this role/.test(sub),
    `the derived-privacy line is missing: ${sub}`);
  assert(!/47\.61/.test(sub), `a viewer was shown the latitude: ${sub}`);
});

test("a viewer never ISSUES the config.site_optics request", () => {
  const since = asked.slice(askedBefore);
  assert(!since.some((r) => r.includes("/api/locations")),
    `a viewer fired a request it could only eat a 403 for: ${JSON.stringify(since)}`);
  assert(since.some((r) => r.includes("/api/profiles")),
    "the view.status reads a viewer MAY make were suppressed too - that is over-gating");
});

test("the admin-only MORE rows render read-only for a viewer, never hidden", () => {
  const reset = q('[data-testid="row-more-factoryReset"]');
  assert(reset != null, "FACTORY RESET was hidden from a viewer instead of rendered read-only");
  assert(/needs .*access/.test(reset.textContent),
    `the row never names what it would need: ${reset.textContent}`);
  assert(q('[data-testid="row-more-safetyTuning"]') != null, "the SAFETY row was hidden");
});

act(() => { root2.unmount(); });

// ========================================================== 7. the setup sheet
{
  const { SetupSheet } = await import("../sheets/SetupSheet");
  const root3 = createRoot(host);
  seed(ADMIN);
  nav.go("/settings/general/setup");
  act(() => { root3.render(createElement(SetupSheet, { params: {}, depth: 0 })); });
  await settle();

  test("the setup sheet lists all five steps with their GO / REVIEW words", () => {
    for (const id of ["pair", "devices", "optics", "site", "light"]) {
      assert(q(`[data-testid="setup-step-${id}"]`) != null, `step ${id} is missing`);
    }
    assert(/REVIEW/.test(text('[data-testid="setup-step-pair"]')),
      "a finished step does not say REVIEW");
    assert(/GO/.test(text('[data-testid="setup-step-devices"]')),
      "an unfinished step does not say GO");
    assert(/3 of 5 done/.test(host.textContent), "the sheet's sub-line does not carry the count");
  });

  test("the setup sheet keeps the design's footer paragraph", () => {
    assert(/Five things, once\./.test(host.textContent), "the footer paragraph is missing");
    assert(/Steps re-open if a device disappears/.test(host.textContent),
      "the sentence that the live derivation makes true is missing");
  });

  test("a step's GO navigates where the machine says", () => {
    click(q('[data-testid="setup-step-devices"]'));
    eq(win.location.hash, "#/rig/devices", "step 2's GO:");
  });

  act(() => { nav.go("/settings/general/setup"); });
  await settle();

  test("DONE marks the legacy first-run-wizard key rather than a second flag", () => {
    const btn = q('[data-testid="setup-dismiss"]');
    assert(btn != null, "no dismiss button on an undismissed guide");
    click(btn);
    eq(useStore.getState().coachSeen["first-run-wizard"], true, "coachSeen:");
    const raw = win.localStorage.getItem("astrodeck-coach-seen") ?? "";
    assert(raw.includes("first-run-wizard"),
      `the seen map was not persisted under its existing key: ${raw}`);
  });

  act(() => { root3.unmount(); });
  act(() => { useStore.setState({ coachSeen: {} } as never); });
  win.localStorage.removeItem("astrodeck-coach-seen");
}

// ================================================= 8. the quick-defaults sheet
{
  const { QuickDefaultsSheet } = await import("../sheets/QuickDefaultsSheet");
  // The store BOTH sheets read, imported here on purpose: this block grades the
  // settings sheet against the module that consumes what it writes, which is
  // the whole reason it exists (review #4).
  const { usePlanning, resetPlanningForTests } = await import("../../../lib/planning");

  // A probe rendered beside the sheet, so an assertion can read exactly what
  // `sky/sheets/quick.tsx` would read on its next open - the same hook, the
  // same module state, no key in the middle.
  let seen: ReturnType<typeof usePlanning> | null = null;
  const Probe = (): null => { seen = usePlanning(); return null; };

  const mountSheet = async (root: ReturnType<typeof createRoot>) => {
    act(() => {
      root.render(createElement(
        "div", null,
        createElement(QuickDefaultsSheet, { params: {}, depth: 0 }),
        createElement(Probe),
      ));
    });
    await settle();
  };

  // ---- nothing learned: the rig says so, and the sheet says so -------------
  planningBlock = { quick: { ...NOTHING_LEARNED }, pool: [] };
  resetPlanningForTests();
  const empty = createRoot(host);
  seed(ADMIN);
  await mountSheet(empty);

  test("with nothing learned the quick-defaults sheet says so and offers no reset", () => {
    assert(q('[data-testid="quick-empty"]') != null,
      "the sheet invented a default set instead of saying nothing has been learned");
    assert(q('[data-testid="quick-reset"]') == null,
      "a RESET button was offered for defaults that do not exist");
  });

  test("the empty face is the RIG's learned flag, not an absent browser key", () => {
    assert(asked.includes("GET /api/planning"),
      "the sheet never asked the rig for the block: " + asked.join(", "));
    eq(seen?.mode, "rig", "the mode the store settled on after the rig answered:");
    eq(seen?.learned, false, "config.planning.quick.learned, as the sheet reads it:");
  });
  act(() => { empty.unmount(); });

  // ---- a rig that HAS learned ---------------------------------------------
  planningBlock = {
    quick: {
      hours: 2, dawn: false, on: { L: true, Ha: false }, exp: { L: 60, Ha: 180 },
      extras: {}, dither_n: 3, learned: true,
    },
    pool: [],
  };
  resetPlanningForTests();
  planningPuts.length = 0;
  const loaded = createRoot(host);
  seed(ADMIN, {
    status: {
      connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [],
      filterwheel: { position: 0, names: ["L", "Ha", "DARK"], opaque: [false, false, true] },
    },
  });
  await mountSheet(loaded);

  test("the filter rows come from the WHEEL, and a blackout slot is not one", () => {
    assert(q('[data-testid="quick-filter-L"]') != null, "no row for the wheel's L slot");
    assert(q('[data-testid="quick-filter-Ha"]') != null, "no row for the wheel's Ha slot");
    assert(q('[data-testid="quick-filter-DARK"]') == null,
      "a blackout slot was offered as a filter to image with");
  });

  test("the sheet reads what the RIG holds, including a filter left OFF", () => {
    const ha = q('[data-testid="quick-filter-Ha"]');
    assert(ha != null, "no Ha row to read - the fixture is wrong, not the component");
    eq(ha.getAttribute("aria-checked"), "false",
      "Ha is stored OFF on the rig and the settings sheet shows:");
    const l = q('[data-testid="quick-filter-L"]');
    eq(l?.getAttribute("aria-checked"), "true", "L is stored ON and shows:");
  });

  click(q('[data-testid="quick-filter-Ha"]'));
  await settle();

  // The CONSUMER, not the key: `usePlanning().quick` is what `sheets/quick.tsx`
  // hands to `wheelModel`. Asserting a raw localStorage key would be green even
  // if nothing else in the app could read the shape written under it, which is
  // exactly how the split shipped.
  test("an edit here is what the SKY hub's own store reads back", () => {
    eq(seen?.quick.on.Ha, true, "Ha after ticking it, as the Sky quick sheet reads it:");
    eq(seen?.quick.on.L, true, "L is untouched by the edit:");
  });

  test("the edit travels as the CHANGED KEY ONLY, so learned exposures survive", () => {
    const last = planningPuts[planningPuts.length - 1];
    assert(last?.quick != null,
      "no quick write reached the rig: " + JSON.stringify(planningPuts));
    eq(Object.keys(last.quick ?? {}).join(","), "on",
      "the keys PUT /api/planning was sent under quick:");
    // A whole-block send would look identical on screen and erase every
    // per-filter exposure a client with a narrower type never knew about -
    // `planning.py:122-153` is written against exactly that.
    eq(seen?.quick.exp.Ha, 180, "the exposure this sheet never touched:");
  });

  click(q('[data-testid="quick-hours"] [data-value="dawn"]'));
  await settle();
  const dawnPut = planningPuts[planningPuts.length - 1];
  click(q('[data-testid="quick-hours"] [data-value="3"]'));
  await settle();
  const hoursPut = planningPuts[planningPuts.length - 1];

  test("TO DAWN survives as a CHOICE, not as tonight's number of hours", () => {
    eq(JSON.stringify(dawnPut?.quick), JSON.stringify({ dawn: true }),
      "the dawn choice, as it goes on the wire:");
    eq(JSON.stringify(hoursPut?.quick), JSON.stringify({ hours: 3, dawn: false }),
      "picking a fixed length clears the dawn choice:");
    eq(seen?.quick.dawn, false, "and the store the Sky sheet reads agrees:");
    eq(seen?.quick.hours, 3, "with the hours:");
  });

  // `liveStack` vs `stack` was one label over two settings: the row here and
  // the chip on the quick sheet wrote different keys, so turning live stacking
  // off in Settings left it on in the night that ran.
  click(q('[data-testid="quick-extra-stack"]'));
  await settle();
  test("the automation rows key on the names the Sky hub's chips write", () => {
    eq(seen?.quick.extras.stack, false,
      "live stacking after switching it off here, as the Sky quick sheet reads it:");
  });

  test("nothing about the defaults was written to this phone", () => {
    eq(win.localStorage.getItem("astrodeck-next-sky-quick"), null,
      "the settings sheet wrote the browser key the rig now owns:");
  });

  act(() => { loaded.unmount(); });

  // ---- a viewer: the whole screen, read-only, and no write -----------------
  resetPlanningForTests();
  planningPuts.length = 0;
  const ro = createRoot(host);
  seed(VIEWER, {
    status: {
      connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [],
      filterwheel: { position: 0, names: ["L", "Ha"], opaque: [false, false] },
    },
  });
  await mountSheet(ro);
  click(q('[data-testid="quick-filter-L"]'));
  await settle();

  test("a viewer sees the rig's plan, is told why it is read-only, and writes nothing", () => {
    assert(q('[data-testid="quick-filter-L"]') != null,
      "a viewer must see the same rows - this is what the rig shoots tonight");
    const note = q(".nx-locknote");
    assert(note != null, "no read-only sentence for a role that cannot write the block");
    assert(String(note.textContent).includes("needs operator or admin access"),
      "the reason must name the access level, got: " + String(note?.textContent));
    eq(planningPuts.length, 0, "a viewer's tap reached the rig; writes sent:");
  });

  // EVERY control, not just the footer's RESET. `save()` refused the write and
  // toasted the reason, so "writes nothing" above was already green - while the
  // row moved under the finger and eleven controls looked live to somebody who
  // could not change one of them. ARCHITECTURE section 8 asks for the opposite:
  // dim it, carry the reason, fire nothing. Sabotage: drop `lockedReason` from
  // any one of these and its id is named in the failure.
  test("a viewer's eleven quick-defaults controls are all honest-disabled, with the reason", () => {
    const reason = "needs operator or admin access";
    // `Stepper2` marks its two BUTTONS rather than the group it labels, so the
    // steppers are asked for their buttons. Everything else marks itself.
    const ids = [
      "quick-hours",
      "quick-filter-L", "quick-filter-Ha",
      "quick-extra-af", "quick-extra-guide", "quick-extra-dither",
      "quick-extra-cloud", "quick-extra-hfr", "quick-extra-stack",
      "quick-reset",
    ];
    const steppers = ["quick-exp-L", "quick-exp-Ha", "quick-dither-n"];
    const check = (el: any, what: string): void => {
      assert(el != null, `${what} was hidden from a viewer instead of rendered read-only`);
      eq(el.hasAttribute("disabled"), false, `${what} uses the native disabled attribute`);
      eq(el.getAttribute("aria-disabled"), "true", `${what} looks live to a viewer`);
      eq(el.getAttribute("title"), reason, `${what} does not carry the reason`);
    };
    for (const id of ids) check(q(`[data-testid="${id}"]`), id);
    for (const id of steppers) {
      const group = q(`[data-testid="${id}"]`);
      assert(group != null, `${id} was hidden from a viewer`);
      const btns = Array.from(group.querySelectorAll("button")) as any[];
      eq(btns.length, 2, `${id} does not have its two step buttons`);
      check(btns[0], `${id} (down)`);
      check(btns[1], `${id} (up)`);
    }
  });
  act(() => { ro.unmount(); });
  resetPlanningForTests();
}

// ================================ 8b. the QUICK SESSION DEFAULTS row's chip
// The row printed the literal string "from your last session" whether or not a
// session had ever been generated - and the sheet it opens says "NOTHING
// LEARNED YET" on exactly that state, so on a fresh install the row and the
// screen behind it contradicted each other. The chip is a claim about the rig,
// so it reads the rig's own `quick.learned`.
{
  const { GeneralScreen } = await import("../general/GeneralScreen");
  const { resetPlanningForTests } = await import("../../../lib/planning");

  planningBlock = { quick: { ...NOTHING_LEARNED }, pool: [] };
  resetPlanningForTests();
  const fresh = createRoot(host);
  seed(ADMIN);
  act(() => { fresh.render(createElement(GeneralScreen)); });
  await settle();

  test("with nothing learned the row says so, instead of naming a session that never ran", () => {
    const row = q('[data-testid="row-quick-defaults"]');
    assert(row != null, "the QUICK SESSION DEFAULTS row is not on the page");
    const t = String(row.textContent);
    assert(/not learned yet/.test(t), `the chip claims a history the rig has not got: "${t}"`);
    assert(!/from your last session/.test(t),
      `the row contradicts the sheet it opens, which says NOTHING LEARNED YET: "${t}"`);
  });
  act(() => { fresh.unmount(); });

  planningBlock = {
    quick: {
      hours: 3, dawn: false, on: { L: true }, exp: { L: 120 },
      extras: {}, dither_n: 3, learned: true,
    },
    pool: [],
  };
  resetPlanningForTests();
  const learned = createRoot(host);
  seed(ADMIN);
  act(() => { learned.render(createElement(GeneralScreen)); });
  await settle();

  test("once the rig HAS learned, the row says where the numbers came from", () => {
    const row = q('[data-testid="row-quick-defaults"]');
    assert(row != null, "the QUICK SESSION DEFAULTS row is not on the page");
    assert(/from your last session/.test(String(row.textContent)),
      `the positive control failed, so the test above proves nothing: "${row.textContent}"`);
  });
  act(() => { learned.unmount(); });
  resetPlanningForTests();
}

// ============================================== 9. the composed sheet registry
{
  const { sheets } = await import("../sheets");
  const sky = await import("../../sky/sheets/sites");
  const hz = await import("../../sky/sheets/horizon");

  test("the settings registry carries this task's two sheets and the other three blocks", () => {
    for (const name of [
      "setup", "quickDefaults",                     // T-SET-1
      "connection", "optics",                       // T-SET-2
      "account", "authMethods", "users", "update", "credits", "help",   // T-SET-3
      "safetyTuning", "standards", "calibration", "naming", "wcs", "sync",
      "skyPack", "restricted", "logExport", "factoryReset",             // T-SET-4
    ]) {
      // An ENTRY, not a component: sheets are code-split (D-FU-2), so a
      // registry holds `{ id, load }` and `hubs/index.ts` builds the lazy
      // components from it.
      assert(sheets[name] != null && typeof sheets[name].load === "function",
        `the registry has no "${name}" sheet`);
    }
  });

  test("sites and horizon are the SKY hub's own modules, not a second copy", () => {
    // `hubs/index.ts` throws at module load when one name maps to two different
    // MODULE IDS. Since the sheets became dynamic imports, each hub writes its
    // own entry object, so identity is no longer the thing to compare - the id
    // is, and it is what keeps `nav.sheet("sites")` from Settings and from Sky
    // one screen. The loaders are then checked to land on the same component.
    eq(sheets.sites.id, "sky/sheets/sites", "settings registered a DIFFERENT sites sheet:");
    eq(sheets.horizon.id, "sky/sheets/horizon", "settings registered a DIFFERENT horizon sheet:");
  });

  const loadedSites = (await sheets.sites.load()).default;
  const loadedHorizon = (await sheets.horizon.load()).default;
  test("and their loaders really resolve to the SKY hub's components", () => {
    eq(loadedSites, sky.SitesSheet,
      "the settings `sites` entry loads something other than the Sky sheet:");
    eq(loadedHorizon, hz.HorizonSheet,
      "the settings `horizon` entry loads something other than the Sky sheet:");
  });
}

// ================================= 10. the placeholder registry is really gone
{
  const fs = await import("node:fs");
  const url = await import("node:url");
  const here = url.fileURLToPath(new URL(".", import.meta.url));
  const shadow = `${here}../sheets.ts`;
  test("the placeholder hubs/settings/sheets.ts no longer shadows the sheets/ directory", () => {
    assert(!fs.existsSync(shadow),
      "hubs/settings/sheets.ts still exists: `./settings/sheets` resolves to IT, " +
      "so every sheet in sheets/index.ts is unreachable while the app still builds");
    assert(fs.existsSync(`${here}../sheets/index.ts`),
      "sheets/index.ts is missing - the hub registers no sheets at all");
  });
}

// --------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`settingsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
