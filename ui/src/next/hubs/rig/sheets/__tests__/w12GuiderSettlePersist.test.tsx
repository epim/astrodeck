// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w12GuiderSettlePersist.test.tsx - the GUIDER sheet's three SETTLE boxes
// persist to the rig's config (#560, backlog WP-58, plan ruling owner-approved
// 2026-09-30).
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w12GuiderSettlePersist.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// SETTLE PX / SETTLE S / TIMEOUT S used to be local UI state: typed into, read
// only by a DITHER NOW press, and never saved - so a reload lost them and a
// scheduled run's own dithers never saw them (server/astrodeck/sequence/
// engine.py's two dither call sites passed no settle at all before this WP).
// This file is the UI half of that fix: the three boxes now round-trip
// through `GET`/`PUT /api/guide/settings` exactly like `dither_pixels`
// already did, committing on blur/Enter (never on every keystroke, so a
// half-typed digit cannot reach the rig).
//
// Reuses rigGuiderDom.test.tsx's jsdom + fetch-recorder shape rather than
// importing it (that file is not this WP's to edit, and a shared harness
// module would be a third file this WP would need to touch).
//
// THE GUIDER IS PHD2 HERE. This file seeded the native guider until #679
// (WP-94): SETTLE PX and SETTLE S are locked on the native guider, which
// ignores both (guide/native.py::dither reads only the timeout), so the
// commit-on-blur cases below could not type into them. They run against PHD2,
// the guider that applies all three; the native lock has its own file,
// w14GuiderSettleNative.test.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/guider", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (_q: string) => ({
  matches: false,
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "Image", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
/** The rig's stored guide block, carrying the three new fields unset - the
 *  state every rig this WP ships to actually has until an operator sets one. */
const storedGuide: Record<string, unknown> = {
  ra_algorithm: "hysteresis",
  dec_algorithm: "resist_switch",
  dec_guide_mode: "auto",
  blc_pulse_ms: 0,
  ra_params: { min_move: 0.2, hysteresis: 0.1, aggression: 0.7 },
  dec_params: { min_move: 0.2, aggression: 1.0 },
  dither_pixels: 3.0,
  recover_guiding: true,
  exposure_s: 2.0,
  gain: 100,
  binning: 1,
  offset: 30,
  recalibrate_after_pier_change: true,
  dither_settle_pixels: null,
  dither_settle_time_s: null,
  dither_settle_timeout_s: null,
};

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: u, body });
  const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/guide/settings")) {
    if (method === "PUT") { Object.assign(storedGuide, body); return ok(storedGuide); }
    return ok(storedGuide);
  }
  if (u.includes("/api/guide/calibration")) return ok({ report: null });
  if (u.includes("/api/guide/assistant/report")) return ok({ report: null });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { GuiderSheet } = await import("../guider");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount", "control.power"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function guideStats(): Record<string, unknown> {
  return {
    guiding: false, rms_ra: 0.4, rms_dec: 0.4, rms_total: 0.6, snr: 40,
    recent: [], is_arcsec: true, image_scale: 1.6, phase: "idle",
  };
}

function seed(principal: Record<string, unknown> = OPERATOR): void {
  const base = useStore.getState();
  act(() => {
    useStore.setState({
      principal, wsPhase: "up", equipConnected: true,
      guide: null, guideAssistant: null, guideRmsByKind: {}, toasts: [],
      plan: { name: "tonight", targets: [], guide: true, dither_every: 3 },
      frameSettings: {
        ...base.frameSettings,
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
      config: { active_profile_id: null, providers: { guide: "auto" } },
      status: {
        connected: { guider: { name: "ZWO ASI120MM", connected: true } },
        backend_links: [{ role: "guider", connected: true, error: null }],
        busy_lanes: [],
        guider: { ...guideStats(), name: "PHD2" },
        guide_camera: { name: "ZWO ASI120MM", connected: true, preview_ok: true, preview_source: "ZWO ASI120MM" },
        // PHD2, not the native guider: SETTLE PX and SETTLE S are only
        // editable where the guider applies them (#679, WP-94). The native
        // guider locks both, and w14GuiderSettleNative.test.tsx owns that case.
        providers: { guide: { kind: "backend", label: "PHD2", reason: "PHD2 bridge", options: [], eligible: [] } },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(GuiderSheet as any, { params: {}, depth: 0 })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const puts = () => asked.filter((a) => a.method === "PUT" && a.url.includes("/api/guide/settings"));

const setValue = (input: any, v: string) => {
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(input, v);
  act(() => { input.dispatchEvent(new win.Event("input", { bubbles: true })); });
};
/** A REAL focus()/blur() pair, not a synthetic "blur" dispatch: React 18
 *  delegates `onBlur` from the native `focusout`, which bubbles only when the
 *  element actually held focus first (rigCameraDom.test.tsx's `enter` helper
 *  hits the same jsdom limit and sidesteps it; this one gives the element a
 *  real focus lifecycle instead, so the component's own `onBlur` wiring - not
 *  only `onKeyDown` - gets exercised). */
const blur = (input: any) => act(() => {
  input.focus();
  input.blur();
});
const pressEnter = (input: any) => act(() => {
  input.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
});

// ============================================================ seeding

await testAsync("a previously saved settle value seeds the box on load", async () => {
  storedGuide.dither_settle_pixels = 1.5;
  storedGuide.dither_settle_time_s = null;
  storedGuide.dither_settle_timeout_s = null;
  seed();
  mount();
  await settle();
  const px = q('[data-testid="guider-settle-px"]');
  eq(px.value, "1.5", "the saved settle-pixels override did not seed the box");
  const s = q('[data-testid="guider-settle-s"]');
  eq(s.value, "", "a null settle field rendered as something other than blank");
});

// ============================================================ commit on blur

await testAsync("blurring a settle box PUTs it, merged onto the whole block", async () => {
  // Mutant (drop `SettleField`'s `onBlur` handler, leaving only `onKeyDown`):
  // RED - blurring the settle-pixels box did not PUT /api/guide/settings
  // (expected 1, got 0)
  asked.length = 0;
  const px = q('[data-testid="guider-settle-px"]');
  setValue(px, "2.5");
  eq(puts().length, 0, "typing alone (no blur) must not write - every keystroke would be a save");
  blur(px);
  await settle();
  eq(puts().length, 1, "blurring the settle-pixels box did not PUT /api/guide/settings");
  const body = puts()[0].body;
  eq(body.dither_settle_pixels, 2.5, "the typed settle-pixels value was not in the PUT body");
  eq(body.dither_pixels, 3.0,
    "the settle commit dropped the dither distance - the PUT replaces the whole block");
  eq(body.ra_algorithm, "hysteresis",
    "the settle commit dropped the tuning fields - the PUT replaces the whole block");
});

await testAsync("Enter commits a settle box exactly like blur", async () => {
  asked.length = 0;
  const s = q('[data-testid="guider-settle-s"]');
  setValue(s, "8");
  pressEnter(s);
  await settle();
  eq(puts().length, 1, "Enter did not PUT /api/guide/settings");
  eq(puts()[0].body.dither_settle_time_s, 8, "the typed settle-time value was not in the PUT body");
});

await testAsync("blanking a saved settle box commits null, not an omitted field", async () => {
  // storedGuide.dither_settle_pixels is 2.5 from the earlier commit test (the
  // fetch mock persists it, like the real route). Blanking must send an
  // EXPLICIT null: `cfg.put` merges onto the last saved block, so leaving the
  // key out of the patch could never clear a previously saved override.
  //
  // Mutant (`commitSettle` sends `{ [key]: n }` with `n` left `undefined` on a
  // blank box, instead of coercing to `null`): RED - blanking sent undefined,
  // not null (an `undefined`-valued key never reaches the wire at all -
  // `JSON.stringify` drops it - so the PUT silently keeps the OLD value
  // instead of clearing it).
  asked.length = 0;
  const px = q('[data-testid="guider-settle-px"]');
  eq(px.value, "2.5", "premise: the box shows the previously committed value");
  setValue(px, "");
  blur(px);
  await settle();
  eq(puts().length, 1, "blanking the settle-pixels box did not PUT");
  assert(puts()[0].body.dither_settle_pixels === null,
    `blanking sent ${JSON.stringify(puts()[0].body.dither_settle_pixels)}, not null`);
});

// ============================================================ viewer

await testAsync("a viewer's settle boxes are locked, and nothing they type reaches the rig", async () => {
  asked.length = 0;
  seed(VIEWER);
  mount();
  await settle();
  const px = q('[data-testid="guider-settle-px"]');
  eq(px.getAttribute("aria-disabled"), "true", "a viewer's settle-pixels box is not locked");
  eq(px.readOnly, true, "a viewer's settle-pixels box accepts typing");
  asked.length = 0;
  blur(px);
  await settle();
  eq(puts().length, 0, "a viewer's blur on a locked settle box reached the rig");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w12GuiderSettlePersist.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
