// safetyTuningDom.test.tsx - Settings > MORE > SAFETY after the reduction
// (wave R7, T-R7-9; plan sections 3.F1, 3.F2 and 6.1 defects 5 and 6).
//
//   Run directly:  npx tsx src/next/hubs/settings/tuning/safety/__tests__/safetyTuningDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// FIVE DECISIONS THIS SHEET MAKES.
//
//   1. It renders, and it renders three DIFFERENT things - the preset picker,
//      the rebuilt escalation editor and the links out. A marker test that
//      names one control passes on a page that drew only a header.
//   2. THE OVERLAP IS GONE. This is the sabotage target for the whole ruling:
//      the sheet must render NONE of the controls `hubs/rig/sheets/safety.tsx`
//      owns, and none of the legacy panels' own markers. Re-mount either panel
//      and this goes red - which is the point, because two editors for one
//      config block is a screen that can lie about what the rig will do when
//      it rains.
//   3. Picking a preset writes its NUMBERS, once. The server derives the
//      `preset` label from the five fields on every read, so a body carrying
//      only the label is a control that does nothing and says it did.
//   4. A number commits at BLUR, not per keystroke, and it commits the value on
//      the screen.
//   5. A viewer sees exactly ONE read-only sentence for the whole sheet (two
//      capabilities, one note - the primitive is singular on purpose), every
//      control still on screen, and nothing is sent.
//
// Convention: jsdom by hand, createRoot + act, native events, a recording fetch
// mock, printed tally + the counts export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `EscalationEditor` imports `safety.css`, which Node cannot load. The same
// synchronous hook `hubBoundary.test.tsx` installs answers with an empty
// module.
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
  { url: "http://local/#/settings/general/safetyTuning", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (q: string) => ({
  matches: false, media: q,
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
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
// It REMEMBERS what was posted, so the store's loadConfig() after a write
// re-seeds the sheet from what was actually stored rather than from the
// fixture. A mock that always echoed the seed would let a write that dropped a
// field still look green.
const asked: string[] = [];
const bodies: Record<string, unknown>[] = [];

const SEED_SAFETY: Record<string, unknown> = {
  enabled: true,
  preset: "backyard",
  poll_each_frame: true,
  sky_fallback_hold: true,
  meridian_flip_warn_min: 15,
  min_alt_deg: 10,
  max_alt_deg: 90,
  horizon: null,
  nogo_box: null,
  enforce_pier_limits: false,
  twilight_deg: -12,
  solar_avoidance: true,
  solar_exclusion_deg: 30,
  on_unsafe: "pause",
  unsafe_consecutive: 3,
  resume_when_safe: true,
  resume_safe_consecutive: 3,
  max_pause_min: 120,
  close_dome_on_unsafe: false,
  close_dome_when_done: false,
  reopen_dome_when_safe: false,
};

const SEED_ESCALATION: Record<string, unknown> = {
  require_cooling: true,
  cooling_action: "abort",
  require_guiding: false,
  guiding_action: "warn",
  af_failure_action: "skip",
  hfr_reject_action: "retake",
  hfr_retake_limit_per_target: 4,
  // Owned by another screen. It has no control here, so it is the field that
  // proves the write echoes the WHOLE block instead of a patch.
  hfr_reject_factor: 1.5,
  require_safety_monitor: false,
  no_progress_watchdog_s: 900,
  reconnect_resume: true,
  reconnect_retries: 3,
};

let serverConfig: Record<string, unknown> = {};

function freshConfig(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 7,
    site: { name: "home", latitude: 37, longitude: -122, elevation_m: 20 },
    optics: { focal_length_mm: 500, aperture_mm: 100 },
    active_profile_id: null,
    safety: JSON.parse(JSON.stringify(SEED_SAFETY)),
    cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null },
    escalation: JSON.parse(JSON.stringify(SEED_ESCALATION)),
    alerts: [],
    deadman_url: "",
    ...over,
  };
}

let refuse = false;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  asked.push(`${method} ${u}`);
  const body = init?.body ? (JSON.parse(init.body) as Record<string, unknown>) : {};
  if (method !== "GET") bodies.push({ url: u, ...body });
  const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/config")) {
    if (method === "POST") {
      if (refuse) {
        return {
          ok: false, status: 403, statusText: "Forbidden",
          json: async () => ({ detail: "config.alerts required" }),
        };
      }
      serverConfig = { ...serverConfig, ...body };
    }
    return ok(serverConfig);
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { SafetyTuningSheet } = await import("../../../sheets/SafetyTuningSheet");
const {
  CUSTOM_NOT_PICKABLE, PRESET_BLURB, PRESET_VALUES, RETAKE_OFF_REASON, presetLine, watchdogLine,
  tuningLockSentence,
} = await import("../index");

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
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise", "view.site_derived",
    "view.weather", "control.capture", "control.mount", "control.guide", "control.power",
    "config.safety", "config.solar_override", "config.backend", "config.site_optics",
    "config.alerts", "admin.users", "system.update"],
};

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

function seed(over: Record<string, unknown> = {}): void {
  serverConfig = freshConfig((over.config as Record<string, unknown>) ?? {});
  act(() => {
    useStore.setState({
      wsPhase: "up",
      equipConnected: true,
      principal: ADMIN,
      authGate: "open",
      toasts: [],
      confirm: null,
      config: JSON.parse(JSON.stringify(serverConfig)),
      status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
      ...over,
    } as never);
  });
}

async function mount(): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(SafetyTuningSheet as any, { params: {}, depth: 0 })); });
  await settle();
}

function click(node: any): void {
  assert(node != null, "cannot press a control that is not on screen");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
/** Type into a React-controlled input: the native value setter, then the
 *  `input` event React delegates `onChange` from. */
function type(node: any, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!
      .call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
/** Blur it for real: React 18 delegates `onBlur` from `focusout`, which jsdom
 *  raises only for an element that was actually focused. */
function blur(node: any): void {
  act(() => { node.focus(); node.blur(); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
const mark = () => asked.length;
const writesSince = (m: number) => asked.slice(m).filter((a) => !a.startsWith("GET "));
const lastPost = () => bodies[bodies.length - 1] as any;

// ================================================== 1. it actually rendered

seed();
await mount();

test("the sheet mounted, with the preset, the editor and the links out", () => {
  // Vacuity guard: four DIFFERENT surfaces, so a shell that rendered only the
  // sheet header cannot satisfy the assertions below by accident.
  assert(q('[data-testid="settings-safetyTuning"]') != null,
    "no sheet marker - the sheet did not render at all");
  assert(q('[data-testid="safety-preset-seg"]') != null, "no preset picker");
  assert(q('[data-testid="escalation-editor"]') != null, "no escalation editor");
  assert(q('[data-testid="safety-tuning-live-link"]') != null, "no link to the live safety sheet");
  assert(q('[data-testid="safety-tuning-warm-link"]') != null, "no link to the warm ramp");
});

test("every escalation control in the parity table is on screen", () => {
  // Section 3.F2, row by row. A rebuild that dropped one of these would still
  // render, still type-check, and quietly stop being able to set it.
  for (const key of [
    "cooling", "cooling-action", "guiding", "guiding-action", "af", "hfr",
    "retake-limit", "watchdog", "reconnect", "reconnect-retries",
  ]) {
    assert(q(`[data-testid="escalation-row-${key}"]`) != null,
      `the escalation editor has no control for ${key}`);
  }
});

test("the preset says what it MEANS, not just what it is called", () => {
  const blurb = PRESET_BLURB.backyard;
  assert(text().includes(blurb), "the preset blurb is missing, so the picker is three bare words");
  const line = q('[data-testid="safety-preset-line"]');
  assert(line != null, "the preset shows no numbers");
  eq((line.textContent || "").trim(), presetLine(SEED_SAFETY as never),
    "the preset line does not carry the five values the label stands for");
});

test("the watchdog says what its number does to the run", () => {
  assert(text().includes(watchdogLine(900)),
    "the no-progress watchdog prints a number with no consequence attached");
});

test("no native disabled attribute anywhere on the sheet", () => {
  eq(qa("[disabled]").length, 0,
    "a control is natively disabled, so pressing it says nothing about why");
});

// =========================================== 2. THE OVERLAP IS GONE (sabotage)

test("this sheet renders NONE of the controls the rig safety sheet owns", () => {
  // The whole reduction in one assertion. Re-mount SafetyPanel or
  // SafetyLimitsPanel here and both halves of this go red.
  for (const owned of [
    "safety-sky-hold", "safety-on-unsafe", "safety-floor-switch", "safety-sun-switch",
  ]) {
    assert(q(`[data-testid="${owned}"]`) == null,
      `${owned} is rendered here as well as on the rig safety sheet - two editors, one config block`);
  }
  // ... and none of the legacy panels' own markers, which is what a re-mount
  // would actually put on the page (the testids above belong to the native rig
  // sheet, so they alone would not catch it).
  for (const legacy of [
    "Twilight threshold", "Action when unsafe", "Warm ramp rate in degrees C per minute",
  ]) {
    assert(q(`[aria-label="${legacy}"]`) == null,
      `"${legacy}" is a SafetyLimitsPanel control - the legacy panel is mounted again`);
  }
  assert(!/Sun avoidance/.test(text()), "SafetyPanel is mounted again (its Sun avoidance card)");
  assert(!/Safety limits/.test(text()), "SafetyLimitsPanel is mounted again (its Safety limits card)");
});

test("the warm ramp is ROUTED, not dropped", () => {
  const row = q('[data-testid="safety-tuning-warm-link"]');
  assert(row != null, "the warm ramp has no home from this sheet at all");
  assert(/camera sheet/.test(row.textContent || ""),
    `the warm-ramp row does not say where it went: "${row.textContent}"`);
});

// ==================================================== 3. picking a preset

await testAsync("picking REMOTE posts the preset's NUMBERS, once", async () => {
  seed();
  await mount();
  const m = mark();
  click(q('[data-testid="safety-preset-seg"] [data-value="remote"]'));
  await settle();

  const writes = writesSince(m);
  eq(writes.length, 1, `picking a preset made ${writes.length} writes, not one`);
  eq(writes[0], "POST /api/config", "the preset did not go through POST /api/config");

  const body = lastPost();
  const sent = body.safety as Record<string, unknown>;
  assert(sent != null, "the body carries no safety block");
  eq(sent.preset, "remote", "the label was not sent");
  // THE ASSERTION THAT MATTERS. The server re-derives `preset` from these
  // fields on every read (config.py _derive_preset_label), so a body with only
  // the label is a control that reports success and changes nothing.
  for (const [k, v] of Object.entries(PRESET_VALUES.remote)) {
    eq(JSON.stringify(sent[k]), JSON.stringify(v),
      `the preset did not send ${k}, so the rig would derive the label straight back`);
  }
  // Wholesale replace: untouched fields ride along rather than being dropped.
  eq(sent.solar_exclusion_deg, 30, "an untouched safety field was dropped from the echo");
});

await testAsync("CUSTOM is shown as the current state but cannot be picked", async () => {
  seed();
  await mount();
  const custom = q('[data-testid="safety-preset-seg"] [data-value="custom"]');
  assert(custom != null, "CUSTOM is hidden, so a custom rig cannot see what it is on");
  eq(custom.getAttribute("aria-disabled"), "true", "CUSTOM is pressable, and pressing it does nothing");
  const m = mark();
  click(custom);
  await settle();
  eq(writesSince(m).length, 0, "pressing CUSTOM wrote to the rig");
  const toast = useStore.getState().toasts.at(-1) as any;
  eq(toast?.title, CUSTOM_NOT_PICKABLE, "pressing CUSTOM said nothing about why it did nothing");
});

// ============================================= 4. the number commits at blur

await testAsync("the retake limit commits on blur, and only on blur", async () => {
  seed();
  await mount();
  const field = q('[data-testid="escalation-row-retake-limit"]');
  assert(field != null, "no retake-limit field");
  eq(field.value, "4", "precondition: the field did not seed from the config");

  const m = mark();
  type(field, "1");
  type(field, "12");
  eq(writesSince(m).length, 0,
    "a keystroke wrote to the rig - every digit on the way to 12 would be its own policy");

  blur(field);
  await settle();
  const writes = writesSince(m);
  eq(writes.length, 1, `blurring made ${writes.length} writes, not one`);
  const body = lastPost();
  const sent = body.escalation as Record<string, unknown>;
  eq(sent.hfr_retake_limit_per_target, 12, "the committed value is not the one on the screen");
  // Wholesale replace: the field no control here owns must survive the write.
  eq(sent.hfr_reject_factor, 1.5,
    "the quality-gate factor was dropped - the block is replaced, not patched");
  eq(sent.require_safety_monitor, false,
    "the absent-monitor policy was dropped - the block is replaced, not patched");
});

await testAsync("a blank retake limit is refused, not read as zero", async () => {
  seed();
  await mount();
  const field = q('[data-testid="escalation-row-retake-limit"]');
  const m = mark();
  type(field, "");
  blur(field);
  await settle();
  eq(writesSince(m).length, 0, "an empty box was committed - Number(\"\") is 0 and 0 retakes is a policy");
  eq(field.value, "4", "the emptied field did not go back to the value the rig has");
});

await testAsync("a sub-control whose parent switch is off is locked, not hidden", async () => {
  seed({ config: { escalation: { ...SEED_ESCALATION, hfr_reject_action: "warn" } } });
  await mount();
  const field = q('[data-testid="escalation-row-retake-limit"]');
  assert(field != null,
    "the retake limit VANISHED with its parent - a number somebody set is now invisible, not inapplicable");
  eq(field.getAttribute("aria-disabled"), "true", "the inapplicable field is still live");
  const m = mark();
  click(field);
  await settle();
  eq(writesSince(m).length, 0, "pressing the locked field wrote to the rig");
  const toast = useStore.getState().toasts.at(-1) as any;
  eq(toast?.title, RETAKE_OFF_REASON, "the locked field did not say which switch turns it back on");
});

// ================================================== 5. the viewer rendering

await testAsync("a viewer sees ONE read-only sentence, every control, and sends nothing", async () => {
  seed({ principal: VIEWER });
  await mount();

  const notes = qa(".nx-locknote");
  eq(notes.length, 1,
    `a viewer sees ${notes.length} read-only sentences; two capabilities share one note here`);
  const expected = `Read-only - ${tuningLockSentence("needs admin access", "needs admin access")}`;
  eq((notes[0].textContent || "").trim(), expected,
    "the read-only sentence does not name both capabilities and the roles that hold them");

  // Nothing is hidden: a viewer sees the same screen an admin does.
  assert(q('[data-testid="safety-preset-seg"]') != null, "the preset picker is hidden from a viewer");
  assert(q('[data-testid="escalation-row-cooling"]') != null, "the escalation controls are hidden from a viewer");
  eq(qa("[disabled]").length, 0, "a viewer's controls are natively disabled instead of honest-locked");

  const m = mark();
  click(q('[data-testid="safety-preset-seg"] [data-value="remote"]'));
  click(q('[data-testid="escalation-row-cooling"]'));
  click(q('[data-testid="escalation-row-af"] [data-value="abort"]'));
  const field = q('[data-testid="escalation-row-retake-limit"]');
  type(field, "9");
  blur(field);
  await settle();
  eq(writesSince(m).length, 0, "a viewer changed the rig's safety policy");
});

await testAsync("a refused write says so and puts the number back", async () => {
  seed();
  await mount();
  const field = q('[data-testid="escalation-row-retake-limit"]');
  refuse = true;
  type(field, "7");
  blur(field);
  await settle();
  refuse = false;
  const status = q('[data-testid="escalation-status"]');
  assert(/admin access/.test(status.textContent || ""),
    `a refused write did not name what it needs: "${status.textContent}"`);
  eq(q('[data-testid="escalation-row-retake-limit"]').value, "4",
    "the box still shows a number the rig refused, so the screen and the rig disagree");
});

// ------------------------------------------------------------------- tally
act(() => { rootRef?.unmount(); });
const total = passed + failed;
for (const f of failures) console.error(f);
console.log(`safetyTuningDom: ${passed}/${total} passed`);
export { passed, failed, total };
