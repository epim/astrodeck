// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14GuiderSettleNative.test.tsx - the GUIDER sheet's SETTLE boxes on the native
// guider (#679, backlog WP-94).
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w14GuiderSettleNative.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. SETTLE PX and SETTLE S (and a note saying DITHER NOW and every
// run use them) were shown on every guider. The native engine ignores both:
// `guide/native.py::dither` reads only `settle["timeout"]`, the Rust engine
// settles on its own 1.5 px / 10 s rule. So on the AstroDeck native guider (and
// the simulator that runs it) an operator could type a settle distance, see it
// save and come back, and change nothing. The fix is UI-only (the plan's option
// 1, the orchestrator's ruling): lock the two boxes with the reason when the
// provider is KNOWN native, show the active provider's real default as the
// placeholder, say which fields apply in the footer, and stop sending the two
// ignored fields from DITHER NOW. A SAVED value is never cleared: the rig can
// switch to PHD2 by profile, and PHD2 reads it.
//
// WHAT EACH CASE GUARDS.
//   (a) native: the two boxes are readOnly + aria-disabled with the reason as
//       their title, typing and blurring sends no PUT, TIMEOUT S still commits,
//       the placeholders read 1.5 / 10 / 60.
//   (b) PHD2: nothing is locked, placeholders 1.5 / 8 / 60, a blur PUTs.
//   (c) provider unknown (no `providers` yet, no `guide` row, "unavailable",
//       NINA): the boxes stay editable. NEVER lock on unknown.
//   (d) DITHER NOW on native omits settle_pixels / settle_time_s and keeps the
//       timeout; on PHD2 it sends all three (the positive control that the
//       press reaches the POST at all).
//   (e) a saved value survives the lock, shown and not cleared by a timeout
//       commit's whole-block PUT.
//   (f) the three constants cannot drift from the sources they copy.
//
// MUTANTS (each run from a byte backup of the source inside this worktree,
// the failure quoted from the run, the source restored byte-identically):
//   M1  guider.tsx `const nativeSettle = settleIgnoredByGuider(providers?.guide?.kind);`
//       -> `const nativeSettle = false;`:
//         RED  (a) native: SETTLE PX and SETTLE S are locked with the reason:
//         the native guider's [data-testid="guider-settle-px"] box is not
//         locked (expected "true", got null)
//   M2  guideSettle.ts `settleIgnoredByGuider` returns
//       `kind === undefined || kind === "astrodeck" || kind === "sim"`:
//         RED  (c) no providers on the status frame yet: a
//         [data-testid="guider-settle-px"] box is locked while the provider is
//         "no providers on the status frame yet" (expected false, got true)
//   M3  guider.tsx `...ditherSettleBody(nativeSettle,` -> `...ditherSettleBody(false,`:
//         RED  (d) native: DITHER NOW on native still sent settle_pixels
//         (expected undefined, got 2.5)
//   M4  guideSettle.ts SETTLE_DEFAULTS `native: { px: 1.5, s: 10, ...` -> `s: 8`:
//         RED  (f) native defaults are app.py's NATIVE_GUIDE_SETTLE:
//         SETTLE_DEFAULTS.native.s drifted from app.py's NATIVE_GUIDE_SETTLE
//         (expected 10, got 8)
//   M5  guider.tsx footer `? NATIVE_SETTLE_NOTE` -> the PHD2 sentence ("Saved
//       for every run - DITHER NOW uses these three too ..."):
//         RED  (a) native: the footer says only TIMEOUT applies: the native
//         footer note is not the native wording (expected "Only TIMEOUT S
//         applies on the native guider, ...", got "Saved for every run -
//         DITHER NOW uses these three too. ...")
//   M11 guider.tsx the seeding effect blanks the settle-pixels box when the
//       guider is native (a lock that CLEARS the saved value):
//         RED  (e) native: a saved settle value is shown, dimmed: the saved
//         settle pixels were cleared from the locked box (expected "2.5", got "")
//   M12 guider.tsx SETTLE S placeholder `String(settleDefaults.s)` -> "8":
//         RED  (a) native: the placeholders show the native defaults: native
//         SETTLE S placeholder ... (expected "10", got "8")
//   M13 guider.tsx TIMEOUT S given the provider lock (`settleBoxLock`):
//         RED  (a) native: TIMEOUT S is NOT locked and still commits: TIMEOUT S
//         is locked on the native guider, but it is the one box the native
//         dither honours
// The same text is in the work package's return, the durable record.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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
function resetStored(over: Record<string, unknown> = {}): void {
  Object.assign(storedGuide, {
    dither_settle_pixels: null, dither_settle_time_s: null,
    dither_settle_timeout_s: null,
  }, over);
}

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
const settleLib = await import("../../lib/guideSettle");
const {
  settleHonoured, settleIgnoredByGuider, SETTLE_DEFAULTS, NATIVE_SETTLE_REASON,
  NATIVE_SETTLE_NOTE, ditherSettleBody,
} = settleLib;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
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

function guideStats(guiding: boolean): Record<string, unknown> {
  return {
    guiding, rms_ra: 0.4, rms_dec: 0.4, rms_total: 0.6, snr: 40,
    recent: [], is_arcsec: true, image_scale: 1.6, phase: guiding ? "guiding" : "idle",
  };
}

type Provider = { kind: string; label: string } | null;
const NATIVE: Provider = { kind: "astrodeck", label: "AstroDeck native" };
const SIM: Provider = { kind: "sim", label: "Simulated guider" };
const PHD2: Provider = { kind: "backend", label: "PHD2" };
const NINA: Provider = { kind: "backend", label: "NINA" };
const UNAVAILABLE: Provider = { kind: "unavailable", label: "Unavailable" };

/** `provider === null` is the status frame that carries no `providers` at all
 *  (the boot window); `"no-guide-row"` is a `providers` object with no `guide`. */
function seed(provider: Provider | "no-guide-row", guiding = false): void {
  const base = useStore.getState();
  const providers = provider === null
    ? undefined
    : provider === "no-guide-row"
      ? {}
      : { guide: { ...provider, reason: "fixture", options: [], eligible: [] } };
  act(() => {
    useStore.setState({
      principal: OPERATOR, wsPhase: "up", equipConnected: true,
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
        guider: { ...guideStats(guiding), name: "guider" },
        guide_camera: { name: "ZWO ASI120MM", connected: true, preview_ok: true, preview_source: "ZWO ASI120MM" },
        ...(providers === undefined ? {} : { providers }),
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
async function open(provider: Provider | "no-guide-row", guiding = false): Promise<void> {
  seed(provider, guiding);
  mount();
  await settle();
  // The anti-blank-page guard: every assertion below is about one box, and
  // "no such box" would pass "was not locked" vacuously.
  assert(q('[data-testid="rig-guider"]') != null && q('[data-testid="guider-settle-px"]') != null,
    "the guider sheet did not render its settle boxes at all");
  asked.length = 0;
}
const q = (sel: string) => container.querySelector(sel) as any;
const puts = () => asked.filter((a) => a.method === "PUT" && a.url.includes("/api/guide/settings"));
const dithers = () => asked.filter((a) => a.method === "POST" && a.url.includes("/api/guide/dither"));
const PX = '[data-testid="guider-settle-px"]';
const S = '[data-testid="guider-settle-s"]';
const TO = '[data-testid="guider-settle-timeout"]';
const NOTE = '[data-testid="guider-settle-note"]';

const setValue = (input: any, v: string) => {
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(input, v);
  act(() => { input.dispatchEvent(new win.Event("input", { bubbles: true })); });
};
/** A real focus()/blur() pair so React's `onBlur` (delegated from `focusout`)
 *  fires; see w12GuiderSettlePersist.test.tsx. */
const blur = (input: any) => act(() => { input.focus(); input.blur(); });
const pressEnter = (input: any) => act(() => {
  input.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
});
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const locked = (input: any) => input.getAttribute("aria-disabled") === "true" && input.readOnly === true;

// ======================================================= (a) the native guider
for (const [name, provider] of [["native", NATIVE], ["simulator", SIM]] as const) {
  await testAsync(`(a) ${name}: SETTLE PX and SETTLE S are locked with the reason, and nothing typed reaches the rig`, async () => {
    // Mutant M1 (`const nativeSettle = false` in guider.tsx): RED - the native
    // guider's [data-testid="guider-settle-px"] box is not locked (expected
    // "true", got null)
    await open(provider);
    for (const sel of [PX, S]) {
      const box = q(sel);
      eq(box.getAttribute("aria-disabled"), "true",
        `the ${name} guider's ${sel} box is not locked`);
      eq(box.readOnly, true, `the ${name} guider's ${sel} box accepts typing`);
      eq(box.title, NATIVE_SETTLE_REASON,
        `the ${name} guider's ${sel} box does not carry the reason as its title`);
      setValue(box, "9");
      blur(box);
      pressEnter(box);
      await settle();
      eq(box.value, "", `typing into the locked ${sel} box changed what it shows`);
    }
    eq(puts().length, 0, "a locked settle box reached the rig with a PUT");
  });
}

await testAsync("(a) native: TIMEOUT S is NOT locked and still commits", async () => {
  await open(NATIVE);
  const to = q(TO);
  assert(!locked(to), "TIMEOUT S is locked on the native guider, but it is the one box the native dither honours");
  eq(to.title, "", "TIMEOUT S carries a lock reason it should not have");
  setValue(to, "30");
  blur(to);
  await settle();
  eq(puts().length, 1, "committing TIMEOUT S on the native guider did not PUT");
  eq(puts()[0].body.dither_settle_timeout_s, 30, "the typed timeout was not in the PUT body");
});

await testAsync("(a) native: the placeholders show the native defaults, 1.5 / 10 / 60", async () => {
  await open(NATIVE);
  eq(q(PX).placeholder, "1.5", "native SETTLE PX placeholder");
  eq(q(S).placeholder, "10", "native SETTLE S placeholder - the native rule holds 1.5 px for 10 s, not PHD2's 8");
  eq(q(TO).placeholder, "60", "native TIMEOUT S placeholder");
});

await testAsync("(a) native: the footer says only TIMEOUT applies, not that DITHER NOW uses all three", async () => {
  // Mutant M5 (the PHD2 sentence in place of NATIVE_SETTLE_NOTE in guider.tsx):
  // RED - the native footer note is not the native wording (expected "Only
  // TIMEOUT S applies on the native guider, ...", got "Saved for every run -
  // DITHER NOW uses these three too. ...")
  await open(NATIVE);
  const note = q(NOTE);
  assert(note != null, "the settle footer note is missing");
  const t = String(note.textContent).replace(/\s+/g, " ").trim();
  eq(t, NATIVE_SETTLE_NOTE, "the native footer note is not the native wording");
  assert(!/DITHER NOW uses these three/i.test(t),
    "the footer still tells a native operator that DITHER NOW uses all three boxes");
  assert(/only TIMEOUT S applies/i.test(t), "the footer does not say which box applies");
});

// ============================================================== (b) PHD2
await testAsync("(b) PHD2: nothing is locked, the placeholders read 1.5 / 8 / 60, a blur PUTs", async () => {
  resetStored();
  await open(PHD2);
  for (const sel of [PX, S, TO]) {
    assert(!locked(q(sel)), `PHD2's ${sel} box is locked: PHD2 applies all three`);
    eq(q(sel).title, "", `PHD2's ${sel} box carries a lock reason`);
  }
  eq(q(PX).placeholder, "1.5", "PHD2 SETTLE PX placeholder");
  eq(q(S).placeholder, "8", "PHD2 SETTLE S placeholder");
  eq(q(TO).placeholder, "60", "PHD2 TIMEOUT S placeholder");
  const note = String(q(NOTE).textContent).replace(/\s+/g, " ");
  assert(/DITHER NOW uses these three too/.test(note),
    `PHD2's footer lost the sentence that is true there: ${JSON.stringify(note)}`);
  const px = q(PX);
  setValue(px, "2");
  blur(px);
  await settle();
  eq(puts().length, 1, "blurring PHD2's settle-pixels box did not PUT");
  eq(puts()[0].body.dither_settle_pixels, 2, "the typed settle pixels were not in the PUT body");
});

// ================================================== (c) the provider is unknown
for (const [name, provider] of [
  ["no providers on the status frame yet", null],
  ["providers without a guide row", "no-guide-row"],
  ["an unavailable resolver", UNAVAILABLE],
  ["NINA, which publishes no settle rule", NINA],
] as const) {
  await testAsync(`(c) ${name}: the settle boxes stay editable`, async () => {
    // Mutant M2 (`settleIgnoredByGuider` also true for an undefined kind): RED
    // - a [data-testid="guider-settle-px"] box is locked while the provider is
    // "no providers on the status frame yet" (expected false, got true)
    resetStored();
    await open(provider);
    for (const sel of [PX, S, TO]) {
      eq(locked(q(sel)), false, `a ${sel} box is locked while the provider is "${name}"`);
    }
    const px = q(PX);
    setValue(px, "2.5");
    eq(px.value, "2.5", "an editable box refused typing");
    blur(px);
    await settle();
    eq(puts().length, 1, `a blur under "${name}" did not PUT`);
  });
}

// ===================================================== (d) DITHER NOW's body
await testAsync("(d) native: DITHER NOW omits settle_pixels and settle_time_s, keeps the timeout", async () => {
  // The saved values are on the rig (an earlier PHD2 night set them) and must
  // not ride a native dither: nothing there reads them.
  //
  // Mutant M3 (`ditherSettleBody(false, ...)` in guider.tsx): RED - DITHER NOW
  // on native still sent settle_pixels (expected undefined, got 2.5)
  resetStored({ dither_settle_pixels: 2.5, dither_settle_time_s: 7, dither_settle_timeout_s: 40 });
  await open(NATIVE, true);
  click(q('[data-testid="guider-dither-now"]'));
  await settle();
  eq(dithers().length, 1, "DITHER NOW did not POST /api/guide/dither");
  const body = dithers()[0].body;
  eq(body.settle_pixels, undefined, "DITHER NOW on native still sent settle_pixels");
  eq(body.settle_time_s, undefined, "DITHER NOW on native still sent settle_time_s");
  eq(body.settle_timeout_s, 40, "DITHER NOW on native dropped the timeout, the one field it honours");
  eq(body.pixels, 3, "the dither distance did not ride along");
});

await testAsync("(d) PHD2: DITHER NOW still sends all three (the positive control)", async () => {
  resetStored({ dither_settle_pixels: 2.5, dither_settle_time_s: 7, dither_settle_timeout_s: 40 });
  await open(PHD2, true);
  click(q('[data-testid="guider-dither-now"]'));
  await settle();
  eq(dithers().length, 1, "DITHER NOW did not POST /api/guide/dither under PHD2");
  const body = dithers()[0].body;
  eq(body.settle_pixels, 2.5, "PHD2's DITHER NOW lost settle_pixels");
  eq(body.settle_time_s, 7, "PHD2's DITHER NOW lost settle_time_s");
  eq(body.settle_timeout_s, 40, "PHD2's DITHER NOW lost settle_timeout_s");
});

// ===================================== (e) a saved value survives the lock
await testAsync("(e) native: a saved settle value is shown, dimmed, and survives a timeout commit", async () => {
  resetStored({ dither_settle_pixels: 2.5, dither_settle_time_s: 7 });
  await open(NATIVE);
  const px = q(PX);
  const s = q(S);
  eq(px.value, "2.5", "the saved settle pixels were cleared from the locked box");
  eq(s.value, "7", "the saved settle time was cleared from the locked box");
  assert(/nx-locked/.test(px.className), "the locked box is not dimmed");
  // A commit of the one editable box is a whole-block PUT: the stored pair must
  // ride it untouched, or switching to PHD2 later finds them gone.
  const to = q(TO);
  setValue(to, "20");
  blur(to);
  await settle();
  eq(puts().length, 1, "the timeout commit did not PUT");
  const body = puts()[0].body;
  eq(body.dither_settle_timeout_s, 20, "the timeout was not committed");
  eq(body.dither_settle_pixels, 2.5, "a timeout commit cleared the saved settle pixels");
  eq(body.dither_settle_time_s, 7, "a timeout commit cleared the saved settle time");
  eq(storedGuide.dither_settle_pixels, 2.5, "the rig no longer holds the saved settle pixels");
});

// ===================================================== the module's own rules
test("settleIgnoredByGuider is true for the native kinds and false for everything else, undefined included", () => {
  eq(settleIgnoredByGuider("astrodeck"), true, "astrodeck");
  eq(settleIgnoredByGuider("sim"), true, "sim");
  eq(settleIgnoredByGuider("backend"), false, "backend");
  eq(settleIgnoredByGuider("astap"), false, "astap");
  eq(settleIgnoredByGuider("unavailable"), false, "unavailable");
  eq(settleIgnoredByGuider(undefined), false, "undefined (status still loading)");
});

test("settleHonoured is exactly the PHD2 bridge, as _guider_and_settle tests it", () => {
  eq(settleHonoured("backend", "PHD2"), true, "backend PHD2");
  eq(settleHonoured("backend", "NINA"), false, "backend NINA publishes no settle rule");
  eq(settleHonoured("astrodeck", "PHD2"), false, "a native kind with a PHD2 label");
  eq(settleHonoured(undefined, undefined), false, "unknown");
});

test("ditherSettleBody leaves a blank box out and never turns it into 0", () => {
  const t = { px: "", s: "  ", timeoutS: "abc" };
  const body = ditherSettleBody(false, t);
  eq(body.settle_pixels, undefined, "a blank box became a value");
  eq(body.settle_time_s, undefined, "a whitespace box became a value");
  eq(body.settle_timeout_s, undefined, "a non-numeric box became a value");
  eq(ditherSettleBody(false, { px: "0", s: "0", timeoutS: "0" }).settle_pixels, 0,
    "a typed 0 was dropped: 0 is a value the operator chose");
});

// ============================================== (f) the constants cannot drift
const REPO = "../../../../../../../";
function repoText(rel: string): string {
  try {
    return readFileSync(new URL(REPO + rel, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${rel}, the source SETTLE_DEFAULTS is graded against: ${(e as Error).message}`);
  }
}

test("(f) native defaults are app.py's NATIVE_GUIDE_SETTLE", () => {
  // Mutant M4 (SETTLE_DEFAULTS.native.s 10 -> 8): RED - SETTLE_DEFAULTS.native.s
  // drifted from app.py's NATIVE_GUIDE_SETTLE (expected 10, got 8)
  const m = /^NATIVE_GUIDE_SETTLE\s*=\s*\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)/m
    .exec(repoText("server/astrodeck/api/app.py"));
  assert(m != null, "app.py no longer defines NATIVE_GUIDE_SETTLE as a (pixels, seconds) literal");
  eq(SETTLE_DEFAULTS.native.px, Number(m![1]), "SETTLE_DEFAULTS.native.px drifted from app.py's NATIVE_GUIDE_SETTLE");
  eq(SETTLE_DEFAULTS.native.s, Number(m![2]), "SETTLE_DEFAULTS.native.s drifted from app.py's NATIVE_GUIDE_SETTLE");
});

test("(f) native TIMEOUT is the Rust engine's DEFAULT_SETTLE_TIMEOUT_S", () => {
  const rs = repoText("native/crates/astro-guide/src/engine.rs");
  const tol = /const DEFAULT_SETTLE_TOL_PX:\s*f64\s*=\s*([0-9.]+);/.exec(rs);
  const time = /const DEFAULT_SETTLE_TIME_S:\s*f64\s*=\s*([0-9.]+);/.exec(rs);
  const timeout = /const DEFAULT_SETTLE_TIMEOUT_S:\s*f64\s*=\s*([0-9.]+);/.exec(rs);
  assert(tol != null && time != null && timeout != null,
    "engine.rs no longer defines the three DEFAULT_SETTLE_* constants as f64 literals");
  eq(SETTLE_DEFAULTS.native.px, Number(tol![1]), "native px drifted from DEFAULT_SETTLE_TOL_PX");
  eq(SETTLE_DEFAULTS.native.s, Number(time![1]), "native s drifted from DEFAULT_SETTLE_TIME_S");
  eq(SETTLE_DEFAULTS.native.timeoutS, Number(timeout![1]), "native timeout drifted from DEFAULT_SETTLE_TIMEOUT_S");
});

test("(f) PHD2 defaults are guide.phd2.SETTLE", () => {
  const m = /^SETTLE\s*=\s*\{\s*"pixels":\s*([0-9.]+),\s*"time":\s*([0-9.]+),\s*"timeout":\s*([0-9.]+)\s*\}/m
    .exec(repoText("server/astrodeck/guide/phd2.py"));
  assert(m != null, "phd2.py no longer defines SETTLE as a pixels/time/timeout literal");
  eq(SETTLE_DEFAULTS.phd2.px, Number(m![1]), "SETTLE_DEFAULTS.phd2.px drifted from phd2.SETTLE");
  eq(SETTLE_DEFAULTS.phd2.s, Number(m![2]), "SETTLE_DEFAULTS.phd2.s drifted from phd2.SETTLE");
  eq(SETTLE_DEFAULTS.phd2.timeoutS, Number(m![3]), "SETTLE_DEFAULTS.phd2.timeoutS drifted from phd2.SETTLE");
});

test("(f) the honoured / ignored tests are the ones _guider_and_settle uses", () => {
  const app = repoText("server/astrodeck/api/app.py");
  assert(/if choice\.kind in \("astrodeck", "sim"\):\s*\n\s*return choice\.label or None, NATIVE_GUIDE_SETTLE/.test(app),
    "_guider_and_settle no longer treats astrodeck/sim as the native-settle kinds: settleIgnoredByGuider has drifted from it");
  assert(/if choice\.kind == "backend" and choice\.label == "PHD2":\s*\n\s*from \.\.guide\.phd2 import SETTLE as phd2_settle/.test(app),
    "_guider_and_settle no longer applies the saved override for PHD2 only: settleHonoured has drifted from it");
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w14GuiderSettleNative.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
