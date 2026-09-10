// shellFixes.test.tsx - the four dead-or-missing shell controls the whole-branch
// review found, each mounted for real.
//
//   Run directly:  npx tsx src/next/__tests__/shellFixes.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// EVERY ONE OF THESE IS THE SAME DEFECT CLASS: a control that renders, accepts
// the press, and changes nothing - or a signal the store keeps that no surface
// reads. A unit test of the piece cannot see any of them, because each piece
// was individually correct.
//
//   #6  the sequence-fatal toast's VIEW LOG button called `openLog()`, and the
//       only component that ever rendered off `logOpen` is not mounted under
//       this root. Sticky toast (`ttl: 0`), dead button, after the worst thing
//       that can happen to a run.
//   #12 `store.unseenError` is bumped for every error line logged while the log
//       is closed, and NOTHING in `ui/src/next` read it. Once the toast was
//       dismissed and the line had scrolled past the 200-line ring, the error
//       was announced nowhere.
//   #14 night mode's only control was SETTINGS - GENERAL - scroll to PHONE:
//       four navigations to go red at the eyepiece.
//   #15 nothing prompted first-run setup outside `#/settings/general`, so a rig
//       out of the box opened on Sky at (0, 0) with no sign setup existed.
//
// The components are mounted individually rather than through `NextApp`: each
// assertion is then about the piece that was broken, and a failure names it.
//
// SABOTAGE CHECKS (each names the assertion that goes red):
//   * drop the `nav.go` from `Toasts.tsx`'s openLog branch -> "VIEW LOG lands
//     on the log screen".
//   * drop `openLog()` from it -> "and clears the unseen-error badge".
//   * revert the LOG chip to `{ id: "log", label: "LOG" }` -> "the LOG chip
//     carries the unseen-error count".
//   * remove the monitor badge from TabBar -> "the MONITOR tab wears the count".
//   * remove the night button from the header -> "the header carries a night
//     toggle" and "pressing it flips store.night".
//   * give the night toggle a text child again (`DAY` / `NIGHT`) -> "the night
//     toggle is icon-only".
//   * spell the role out as `VIEW ONLY` / `OPERATOR` again -> "the role chip is
//     four characters and says the rest in its name".
//   * put the mount's state word back in the chip's own text -> "the mount
//     chip's state word is separable".
//   * gate the setup banner on `complete` the wrong way, or drop it -> the
//     three banner assertions.

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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} removeEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// `GET /api/profiles` is the ONE first-run signal that is not in the store, so
// the setup banner's count depends on it. Everything else answers 404 quietly.
let profiles: unknown[] = [];
g.fetch = async (url: any) => {
  const u = String(url);
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (u === "/api/profiles") return ok(profiles);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}), text: async () => "",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { Toasts } = await import("../shell/Toasts");
const { Header } = await import("../shell/Header");
const { TabBar } = await import("../shell/TabBar");
const { Banners } = await import("../shell/Banners");
const { HUB_META } = await import("../hubs");
type SubContext = import("../hubs").SubContext;

// ------------------------------------------------------------------ harness
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
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const root = createRoot(container);
const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;

// ================================================================== #6 toast

await testAsync("VIEW LOG lands on the log screen, and clears the unseen-error badge", async () => {
  win.location.hash = "#/sky";
  act(() => { useStore.setState({ toasts: [], logOpen: false, unseenError: 4 } as never); });
  await act(async () => { root.render(createElement(Toasts as any, { bp: "phone" })); });

  act(() => {
    useStore.getState().enqueueToast({
      level: "error",
      title: "Sequence failed",
      ttl: 0,                                    // sticky, as store.ts:2029 raises it
      action: { label: "View log", kind: "openLog" },
    });
  });

  const btn = q('[data-testid="toasts"] .nx-toast-action button');
  assert(btn != null, "the fatal toast rendered no action button");
  eq(String(btn.textContent).trim(), "View log");

  click(btn);

  eq(win.location.hash, "#/monitor/log",
    "VIEW LOG must NAVIGATE - setting store.logOpen renders nothing under this root:");
  eq(useStore.getState().unseenError, 0,
    "and it must still call openLog(), which is what clears the error badge:");
});

// ============================================================ #12 the badge

test("the LOG chip carries the unseen-error count, and no count at zero", () => {
  const ctx: SubContext = {
    flowCount: null, incidentTone: null, incidentCount: 0, alertsUndelivered: null,
    rigDeviceCount: null, rigLinkTone: null, weatherDot: null, unseenError: 3,
  };
  const log = HUB_META.monitor.subs(ctx).find((i) => i.id === "log");
  assert(log != null, "the monitor hub has no LOG chip");
  eq(log!.count, 3, "LOG chip count:");

  const quiet = HUB_META.monitor.subs({ ...ctx, unseenError: 0 }).find((i) => i.id === "log");
  eq(quiet!.count, undefined, "zero errors must render NO count, not a `0`:");
});

await testAsync("the MONITOR tab wears the count, and says so in its accessible name", async () => {
  act(() => { useStore.setState({ unseenError: 3 } as never); });
  await act(async () => {
    root.render(createElement(TabBar as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });

  const badge = byId("tab-monitor-errors");
  assert(badge != null, "no unseen-error badge on the MONITOR tab");
  eq(String(badge.textContent), "3");

  const tab = byId("tab-monitor");
  const label = String(tab.getAttribute("aria-label"));
  assert(/MONITOR/.test(label), `the hub name must lead the accessible name, got "${label}"`);
  assert(/3 unseen errors/.test(label), `the count must be in the name, got "${label}"`);

  // And it clears. `LogScreen` calls `openLog()` on mount, which is what zeroes
  // the counter - so the badge going away is the whole loop, not a repaint.
  act(() => { useStore.getState().openLog(); });
  eq(byId("tab-monitor-errors"), null, "the badge survived the log being opened");
});

// ============================================================= #14 the night

await testAsync("the header carries a night toggle, and pressing it flips store.night", async () => {
  act(() => {
    useStore.setState({
      night: false, wsPhase: "up", telemetryStale: false,
      status: {
        connected: { camera: { connected: true, name: "sim" } },
        camera: { temperature: -10 }, mode: "sim",
      } as any,
    } as never);
  });
  await act(async () => { root.render(createElement(Header as any, null)); });
  await settle();

  const btn = byId("header-night");
  assert(btn != null, "no night control in the header - it is four navigations away again");
  eq(btn.tagName, "BUTTON", "a night TOGGLE has to be pressable:");
  eq(String(btn.textContent), "",
    "the toggle is icon-only: the word was the first thing the 390 px row " +
      "truncated, and a chip reading `D` is not a state -");
  assert(/Switch to red night-vision mode/.test(String(btn.getAttribute("aria-label"))),
    `the accessible name must say what the press does, got "${btn.getAttribute("aria-label")}"`);

  click(btn);
  eq(useStore.getState().night, true, "pressing NIGHT did not change the mode:");
  assert(/Switch to day mode/.test(String(byId("header-night").getAttribute("aria-label"))),
    "the state must be in the accessible name, and it must follow the toggle");

  click(byId("header-night"));
  eq(useStore.getState().night, false, "the toggle only works one way:");
});

test("the CAM chip carries its unit, and nothing but its unit", () => {
  const cam = byId("header-cam");
  assert(cam != null, "no camera chip");
  assert(/-10°/.test(String(cam.textContent)),
    `a temperature without a unit reads as one more state word, got "${cam.textContent}"`);
  // ...and the tenth of a degree is gone with the unit WORD (measured
  // 2026-09-10): `CAM -10.0°` is 12 px wider than `CAM -10°` on a 390 px row
  // that had 0 px to spare, and the digit it spends them on changes on every
  // poll and is not a number anyone acts on. A set point is whole degrees.
  assert(!/-10\.0/.test(String(cam.textContent)),
    `the header prints whole degrees, got "${cam.textContent}"`);
  assert(!/\bC\b|degC|deg /i.test(String(cam.textContent).replace("CAM", "")),
    `the degree sign is the whole unit, got "${cam.textContent}"`);
});

// ============================================== the header's width, at 390 px
//
// MEASURED (probe, 390x844, `#/session/now`): every chip in this row ellipsised
// at once - `CAM ...`, `MOUNT...`, `S...`, `D` as an admin, `C...`, `MO...`,
// `S.`, `V...` as a viewer. jsdom computes no widths, so the width policy
// itself is guarded by `shellCss.test.ts` reading the stylesheet. What THIS
// file can hold is the DOM shape the policy needs: a night toggle with no text
// to truncate, a role chip short enough to survive, and a mount state word in
// its own element so CSS can drop it without dropping the chip.

test("the night toggle is icon-only, and says what it is in its name", () => {
  const btn = byId("header-night");
  assert(btn != null, "no night control in the header");
  eq(String(btn.textContent), "", "the night toggle must render no text:");
  const glyph = btn.querySelector(".nx-pill-glyph svg");
  assert(glyph != null, "an icon-only control with no icon is a blank button");
  // The two halves a screen reader would otherwise have to guess: what the
  // screen is in now, and what pressing it does. Both, in both places.
  const label = String(btn.getAttribute("aria-label"));
  eq(btn.getAttribute("title"), label, "aria-label and title must say the same thing:");
  assert(/Day mode is on/.test(label), `the state must be in the name, got "${label}"`);
  assert(/Switch to/.test(label), `what the press does must be in the name, got "${label}"`);
});

/** A connected sim rig at -10 with a tracking mount, signed in as `role`. The
 *  width tests need a FULL row: five chips is what a viewer sees, and five
 *  chips is what would not fit. */
async function seedRow(role: "admin" | "viewer" | "operator"): Promise<void> {
  act(() => {
    useStore.setState({
      night: false, wsPhase: "up", telemetryStale: false,
      principal: { role, email: "a@b.c", caps: ["view.status"] } as any,
      status: {
        connected: {
          camera: { connected: true, name: "sim" },
          telescope: { connected: true, name: "sim" },
        },
        camera: { temperature: -10 },
        mount: { tracking: true, parked: false, slewing: false },
        mode: "sim",
      } as any,
    } as never);
  });
  await act(async () => { root.render(createElement(Header as any, null)); });
  await settle();
}

await testAsync("the mount chip's state word is separable from the chip", async () => {
  await seedRow("admin");
  const mount = byId("header-mount");
  assert(mount != null, "no mount chip");
  // The word is still in the DOM at every width - `shell.css` hides it at
  // `data-bp="phone"` only, so tablet and desktop still read `MOUNT TRACK`.
  assert(/TRACK/.test(String(mount.textContent)),
    `a tracking mount must say so, got "${mount.textContent}"`);
  const state = mount.querySelector(".nx-mount-state");
  assert(state != null,
    "the state word must live in `.nx-mount-state`, or the phone rule can only " +
      "drop the whole chip - `MOUNT TRACK` is 96 px in a row that has 271");
  eq(String(state.textContent).trim(), "TRACK", "the separable part is the state word alone:");
  eq(String(mount.textContent).replace(String(state.textContent), ""), "MOUNT",
    "what is left when the state word goes must be the label alone:");
});

await testAsync("an admin's row carries no role chip and is not dense", async () => {
  await seedRow("admin");
  eq(byId("header-role"), null, "admin is the default posture and states nothing:");
  const row = q(".nx-header-chips");
  assert(row != null, "no chips row");
  eq(row.getAttribute("data-dense"), null,
    "an admin row has one chip fewer, so it keeps the flows count:");
});

await testAsync("a viewer's role chip is four characters, with the rest in its name", async () => {
  await seedRow("viewer");
  const chip = byId("header-role");
  assert(chip != null, "a viewer whose controls are all locked must be told so");
  const text = String(chip.textContent);
  eq(text, "VIEW", "the visible role is the short form:");
  assert(text.length <= 4,
    `a role chip longer than four characters does not fit a 390 px row, got "${text}"`);
  // The sentence the short form stands in for. `role="img"` is what makes the
  // label the chip's accessible NAME - a bare <span> has none of its own, so
  // aria-label on one is not read.
  eq(chip.getAttribute("role"), "img", "the chip needs a role to carry a name:");
  const full = String(chip.getAttribute("aria-label"));
  eq(chip.getAttribute("title"), full, "aria-label and title must say the same thing:");
  assert(/viewer/i.test(full), `the full role must be in the name, got "${full}"`);
  assert(/no rig control|view only/i.test(full),
    `the name must say what the role cannot do, got "${full}"`);
  // ...and the row says it is carrying the extra chip, which is the input the
  // stylesheet drops the flows count on. No media query can see this: it
  // varies per USER, not per width.
  eq(q(".nx-header-chips").getAttribute("data-dense"), "true",
    "the row must declare itself dense when it carries a role chip:");
});

await testAsync("an operator's role chip is shorter still", async () => {
  await seedRow("operator");
  const chip = byId("header-role");
  assert(chip != null, "no role chip for an operator");
  eq(String(chip.textContent), "OP", "the operator's short form:");
  assert(/operator/i.test(String(chip.getAttribute("title"))),
    `the full role must be in the tooltip, got "${chip.getAttribute("title")}"`);
});

// ============================================================ #15 the banner

/** Three of the five setup steps done: the phone is paired (link up, identity
 *  resolved), the site is real, the optics are known. No devices connected and
 *  no frame taken. */
function seedSetup(done: 3 | 5): void {
  profiles = done === 5 ? [{ id: "p1", name: "the rig" }] : [];
  useStore.setState({
    wsPhase: "up",
    principal: { role: "admin", email: "a@b.c", caps: ["view.status"] } as any,
    authMethods: { methods: [], first_run: false } as any,
    telemetryStale: false,
    equipConnected: done === 5,
    previews: done === 5 ? ([{ id: "f1" }] as any) : ([] as any),
    site: { latitude: 47.6, longitude: -122.3, is_default: false, name: "home" } as any,
    config: {
      version: 1,
      site: { latitude: 47.6, longitude: -122.3, is_default: false, name: "home" },
      optics_computed: { have_optics: true, fov_w_deg: 1.2, fov_h_deg: 0.8 },
      active_profile_id: done === 5 ? "p1" : null,
    } as any,
    status: {
      connected: done === 5 ? { camera: { connected: true, name: "sim" } } : {},
      camera: { temperature: -10, can_cool: false },
      mode: "sim",
    } as any,
    resumeArm: null, runBanner: null, weather: null, safety: null,
    sequence: { state: "idle" } as any,
  } as never);
}

await testAsync("an unfinished rig is told setup exists, on a hub that is not Settings", async () => {
  act(() => { seedSetup(3); });
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();

  const banner = byId("banner-setup");
  assert(banner != null,
    "a rig out of the box opens on Sky with nothing saying setup exists - review #15");
  const text = String(banner.textContent);
  assert(/First-time setup: 3 of 5 done/.test(text),
    `the banner must carry the count, got "${text}"`);
  assert(/settings/.test(text), `and a way there, got "${text}"`);
});

await testAsync("it is silent on the Settings hub, where the card says the same thing", async () => {
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE("settings", "general"), nowMs: Date.now() }));
  });
  await settle();
  eq(byId("banner-setup"), null,
    "the banner and SetupCard would be two announcements of one fact on one screen:");
});

await testAsync("it goes away at 5 of 5", async () => {
  act(() => { seedSetup(5); });
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();
  eq(byId("banner-setup"), null, "a finished rig is still being nudged to set itself up:");
});

await testAsync("and it can be dismissed for the session", async () => {
  act(() => { seedSetup(3); });
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();
  assert(byId("banner-setup") != null, "precondition: the banner is up at 3 of 5");

  const x = byId("banner-setup").querySelector(".nx-banner-x");
  assert(x != null, "the setup banner has no dismiss");
  click(x);
  eq(byId("banner-setup"), null, "the banner survived its own dismiss button:");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`shellFixes.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
export { passed, failed, total };
