// settingsPanelsDom.test.tsx — the Settings panels, MOUNTED, and driven over
// time: type something, let a config frame land, and see whether what you typed
// is still there; press a roof control and see whether the switch reports the
// SERVER or the request.
//
//   Run directly:  npx tsx src/components/__tests__/settingsPanelsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// WHY jsdom AND NOT renderToStaticMarkup. Every defect below is a defect in a
// SECOND render: the first paint of all five panels was always correct. A
// static-markup test renders once and would have passed against every one of
// them. What hurts a user here is a config reload arriving while they are half
// way through typing, a 403 landing after an optimistic switch flip, and a POST
// resolving while the mount is still swinging — none of which exist until the
// component has been re-rendered with new external state.
//
// The globals are planted before the store or any component is imported: the
// api client reads window.location at module scope. run-tests.mjs gives this
// file its own process, so that cannot leak into another suite.

/* eslint-disable @typescript-eslint/no-explicit-any */

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

// Repeating timers are RECORDED rather than run: the roof badge polls every 15s
// and a test that waited for that would take 15s. Capturing the callback lets a
// test fire the tick itself, which is the thing under test — that a repeating
// refresh exists and that running it updates the badge.
const intervals: { fn: () => void; ms: number }[] = [];
const handles: number[] = [];
const realSetInterval = win.setInterval.bind(win);
win.setInterval = (fn: () => void, ms: number) => {
  intervals.push({ fn, ms });
  // A real handle so the component's own clearInterval works — parked far
  // enough out that it never fires. They are all cleared at the report, or a
  // forgotten one would hold the event loop open and the run would never end.
  const h = realSetInterval(() => {}, 1e9);
  handles.push(h);
  return h;
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element",
  "Node", "Event", "CustomEvent", "MouseEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch stub
// Routed by "METHOD /path". A test swaps a route in to reach a case the real
// server would have to be broken to produce — a 403 on the safety write, a
// shutter that has moved since the last poll, a sink test that never answers.
type Reply = { status: number; body: unknown };
const routes: Record<string, () => Reply | Promise<Reply>> = {};
const calls: string[] = [];
g.fetch = async (url: string, init?: any) => {
  const method = (init?.method ?? "GET").toUpperCase();
  const key = `${method} ${String(url)}`;
  calls.push(key);
  const make = routes[key] ?? (() => ({ status: 404, body: { detail: "no route" } }));
  // Awaited, so a route may return a PENDING promise — the only way to hold a
  // request open across assertions, which is what "while one sink is testing"
  // means.
  const { status, body } = await make();
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 403 ? "Forbidden" : "OK",
    json: async () => body,
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const SafetyPanel = (await import("../settings/SafetyPanel")).default;
const SafetyLimitsPanel = (await import("../settings/SafetyLimitsPanel")).default;
const EscalationPanel = (await import("../settings/EscalationPanel")).default;
const OpticsPanel = (await import("../settings/OpticsPanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const SAFETY = {
  enabled: true, preset: "backyard", on_unsafe: "pause", unsafe_consecutive: 3,
  resume_when_safe: true, resume_safe_consecutive: 3, max_pause_min: 120,
  min_alt_deg: 10, max_alt_deg: 90, twilight_deg: -12, poll_each_frame: false,
  enforce_pier_limits: true, nogo_box: [], solar_avoidance: true,
  solar_exclusion_deg: 30, close_dome_on_unsafe: false,
  close_dome_when_done: false, reopen_dome_when_safe: false,
};
const COOLING = { warm_ramp: true, warm_rate_c_per_min: 2, ambient_c: 15 };
const ESCALATION = {
  require_cooling: false, cooling_action: "warn", require_guiding: false,
  guiding_action: "warn", af_failure_action: "warn", hfr_reject_action: "warn",
  hfr_retake_limit_per_target: 3, no_progress_watchdog_s: 0,
  reconnect_resume: false, reconnect_retries: 3,
};
const OPTICS = {
  focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248,
  sensor_height_px: 4176, auto_from_camera: true, guide_focal_length_mm: null,
  telescope_name: "Askar FRA400",
};

/** A whole fresh /api/config object, the way loadConfig hands one back — every
 *  nested block a NEW object even when nothing in it changed. That identity
 *  churn is the entire mechanism of the wipe bug, so the fixture must reproduce
 *  it or the test proves nothing. */
const makeConfig = (over: Record<string, unknown> = {}) =>
  JSON.parse(JSON.stringify({
    version: 1, site: {}, safety: SAFETY, cooling: COOLING,
    escalation: ESCALATION, optics: OPTICS, optics_computed: {},
    effective: {}, ...over,
  }));

const ADMIN_CAPS = [
  "view.status", "config.safety", "config.solar_override", "config.alerts",
  "config.site_optics", "control.mount",
];

function seedStore(opts: {
  config?: Record<string, unknown>;
  caps?: string[];
  lanes?: string[];
} = {}): void {
  // Tear the previous panel down FIRST. A store write with a panel from the
  // last test still subscribed re-renders it outside act — harmless to the
  // assertions, but it buries a real failure under React warnings.
  if (root) { act(() => { root.unmount(); }); root = null; }
  // Scope the request log to ONE test. It used to accumulate for the whole file,
  // so `calls.includes("POST /api/dome/close")` could be satisfied by a close a
  // DIFFERENT test sent — which is exactly how the "one tap must not pre-empt a
  // slew" assertion first passed for the wrong reason.
  calls.length = 0;
  useStore.setState({
    config: (opts.config ?? makeConfig()) as never,
    status: { connected: {}, looping: false, mode: "sim",
              busy_lanes: opts.lanes ?? [] } as never,
    principal: { role: "admin", email: null,
                 caps: opts.caps ?? ADMIN_CAPS } as never,
  } as never);
}

const container = win.document.getElementById("root") as any;
let root: any = null;

/** Drain the microtask queue INSIDE act. The stubbed fetch resolves over
 *  several hops (fetch → res.json → the caller's .then), and a state update
 *  landing outside act is both a React warning and a missed assertion. */
async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
}

async function mount(el: unknown): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(el as never); });
  // Let the mount-time GETs (the dome poll) land inside act, or React logs an
  // "update not wrapped in act" for every one of them and the real output is
  // buried under the warning.
  await flush();
}

const text = () => (container.textContent || "").replace(/\s+/g, " ");
const byLabel = (label: string) =>
  container.querySelector(`[aria-label="${label}"]`) as any;
const buttonWith = (re: RegExp) =>
  ([...container.querySelectorAll("button")] as any[])
    .find((b) => re.test(b.textContent || ""));

/** The nearest ancestor (self included) that DEADENS this control — a
 *  `pointer-events-none` wrapper or an `aria-disabled` container.
 *
 *  This exists because jsdom does not implement pointer-events: a dispatched
 *  click reaches a button sitting inside a `pointer-events-none` div exactly as
 *  if the div were not there. A test that only asserted "the POST went out"
 *  would therefore have passed over a button that is dead to every finger on a
 *  tablet — which is precisely how the roof lockout survived a green suite.
 *  Assert on the CHAIN, not on the dispatch. */
function deadenedBy(node: any): any {
  for (let n = node; n; n = n.parentElement) {
    const cls = typeof n.className === "string" ? n.className : "";
    if (/pointer-events-none/.test(cls)) return n;
    if (n.getAttribute?.("aria-disabled") === "true") return n;
  }
  return null;
}

/** Type into a controlled React input. React installs its own value setter on
 *  the node, so assigning `.value` is invisible to it — the native prototype
 *  setter plus an `input` event is what a real keypress looks like. */
async function typeInto(node: any, value: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(
    win.HTMLInputElement.prototype, "value",
  )!.set!;
  await act(async () => {
    setter.call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}

const click = async (node: any) => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};

/** Push a NEW config object into the store, exactly as loadConfig does. */
const pushConfig = async (cfg: Record<string, unknown>) => {
  await act(async () => { useStore.setState({ config: cfg as never } as never); });
};

const setLanes = async (lanes: string[]) => {
  await act(async () => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", busy_lanes: lanes } as never,
    } as never);
  });
};

// =========================================================== drafts survive
// A config reload arrives from EVERYWHERE: this panel's save, another panel's
// save, a profile activating, any `config` frame off the WS. Every panel below
// used to reseed its draft from the OBJECT, which is fresh every time, so a
// reload that changed nothing threw away what was being typed — and took the
// "Unsaved changes" warning with it, so nothing said it had happened.

await test("SafetyLimits: GUARD — the panel mounted with an editable altitude floor", async () => {
  seedStore();
  await mount(createElement(SafetyLimitsPanel as never));
  const floor = byLabel("Obstruction 1 azimuth from") ?? null;
  assert(floor === null, "fixture seeds no obstructions, so none should render");
  const inputs = [...container.querySelectorAll("input[type=number]")] as any[];
  assert(inputs.length > 0, `no number inputs rendered — the panel did not mount: ${text().slice(0, 160)}`);
  assert(inputs.some((i) => i.value === "10"),
    `the seeded 10° floor is not on screen, so nothing below is editing the real control: ${inputs.map((i) => i.value)}`);
});

await test("SafetyLimits: a typed floor survives a config reload that changed nothing here", async () => {
  seedStore();
  await mount(createElement(SafetyLimitsPanel as never));
  const floor = ([...container.querySelectorAll("input[type=number]")] as any[])
    .find((i) => i.value === "10");
  await typeInto(floor, "25");

  // PRECONDITION. Without it "still 25" could pass because it was never 25.
  assert(floor.value === "25", `the floor field did not take the edit (got ${floor.value})`);
  assert(/Unsaved changes/.test(text()), "the panel does not consider itself dirty, so there is nothing to lose");

  await pushConfig(makeConfig()); // identical values, all-new objects

  const after = ([...container.querySelectorAll("input[type=number]")] as any[])[0];
  assert(after.value === "25",
    `the altitude floor reverted to ${after.value} on a config reload that changed nothing — ` +
    "everything typed since the page loaded is gone, silently");
  assert(/Unsaved changes/.test(text()),
    "the unsaved-changes warning vanished with the edit, so the revert is invisible");
});

await test("SafetyLimits: a change made elsewhere in the SAME block lands without eating the edit", async () => {
  // SafetyPanel (sun avoidance, the roof flags) writes the same SafetyConfig
  // from the same tab, so its saves arrive here as a genuinely changed block.
  // Both halves matter: the user's floor must survive AND the other panel's
  // change must be adopted, or our next full-block echo would revert it.
  seedStore();
  await mount(createElement(SafetyLimitsPanel as never));
  const floor = ([...container.querySelectorAll("input[type=number]")] as any[])
    .find((i) => i.value === "10");
  await typeInto(floor, "25");
  assert(floor.value === "25", "precondition: the floor field took the edit");
  assert(!/Safety off/.test(text()), "precondition: monitoring starts ON");

  await pushConfig(makeConfig({ safety: { ...SAFETY, enabled: false } }));

  const after = ([...container.querySelectorAll("input[type=number]")] as any[])[0];
  assert(after.value === "25",
    `the floor reverted to ${after.value} because another panel touched an unrelated field of the same block`);
  assert(/Safety off/.test(text()),
    "the other panel's change was NOT adopted — this draft would revert it on the next save");
});

await test("Optics: a typed focal length survives a config reload (adding a driver reloads config)", async () => {
  seedStore();
  await mount(createElement(OpticsPanel as never));
  const focal = ([...container.querySelectorAll("input[type=number]")] as any[])
    .find((i) => i.value === "530");
  assert(focal != null, `the 530mm focal-length box is not on screen: ${text().slice(0, 160)}`);
  await typeInto(focal, "424");
  assert(focal.value === "424", "precondition: the focal-length field took the edit");
  assert(/Unsaved changes/.test(text()), "precondition: the panel is dirty");

  await pushConfig(makeConfig());

  const after = ([...container.querySelectorAll("input[type=number]")] as any[])[0];
  assert(after.value === "424",
    `focal length reverted to ${after.value} on an unrelated config reload — the reducer the user just ` +
    "typed is gone and the plate solver keeps the old scale");
  assert(/Unsaved changes/.test(text()), "the unsaved warning went with it, so the revert is silent");
});

await test("Escalation: a typed watchdog survives a config reload", async () => {
  seedStore();
  await mount(createElement(EscalationPanel as never));
  const watchdog = ([...container.querySelectorAll("input[type=number]")] as any[])[0];
  assert(watchdog != null, `no watchdog field on screen: ${text().slice(0, 160)}`);
  await typeInto(watchdog, "45");
  assert(watchdog.value === "45", "precondition: the watchdog field took the edit");
  assert(/Unsaved changes/.test(text()), "precondition: the panel is dirty");

  await pushConfig(makeConfig());

  const after = ([...container.querySelectorAll("input[type=number]")] as any[])[0];
  assert(after.value === "45",
    `the watchdog reverted to ${after.value} when a neighbouring panel saved`);
});

// ============================================== the roof: capability + truth

const domeOpen = () => ({
  status: 200,
  body: { connected: true, shutter: "open", requires_park_before_close: true, can_slave: false },
});

await test("Roof: a caller without config.safety cannot flip the interlocks at all", async () => {
  // Neither operator nor viewer holds ANY config.* cap, so every tap here was a
  // 403 — and the switch flipped ON regardless, telling someone a rain trip
  // would close the roof over their gear when the server had never agreed.
  routes["GET /api/dome/state"] = domeOpen;
  seedStore({ caps: ["view.status", "control.mount"] });
  await mount(createElement(SafetyPanel as never));

  const sw = byLabel("Close roof on unsafe");
  assert(sw != null, `the roof switch is not on screen: ${text().slice(0, 200)}`);
  assert(sw.disabled === true,
    "the roof interlock is still pressable without config.safety — a keyboard user reaches a 403");
  assert(byLabel("Close roof at end-of-night").disabled === true,
    "the end-of-night switch is still pressable without config.safety");
  assert(/needs admin access \(config\.safety\)/.test(text()),
    `the panel never says WHY the block is inert: ${text().slice(-400)}`);
});

// The CONFIG lock and the ACTION are two different permissions, and folding them
// together cost the operator the only manual roof close in the whole app.
// POST /api/dome/close is gated on control.mount (app.py:3748) BECAUSE IT MOVES
// THE MOUNT, and ROLES_CAP["operator"] holds control.mount while holding no
// config.* cap at all. This is the persona the rental model exists for and the
// one standing under the sky when it starts raining.
const OPERATOR_CAPS = [
  "view.status", "view.preview", "view.weather",
  "control.capture", "control.guide", "control.mount",
];

await test("Roof: an operator keeps 'Close roof now' that the config lock took away", async () => {
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/dome/close"] = () => ({ status: 200, body: { started: "goto" } });
  seedStore({ caps: OPERATOR_CAPS, lanes: [] });
  await mount(createElement(SafetyPanel as never));

  // PRECONDITION — the config lock really IS engaged for this caller. Without
  // this the test could pass on a build where nothing is locked at all, and it
  // would prove nothing about the separation.
  assert(byLabel("Close roof on unsafe").disabled === true,
    "precondition: an operator cannot flip the auto-close interlock (config.safety)");
  assert(deadenedBy(byLabel("Close roof on unsafe")) != null,
    "precondition: the interlock block is dimmed/inert for this caller");

  const btn = buttonWith(/Close roof now/);
  assert(btn != null, `no close-roof button on screen: ${text().slice(0, 200)}`);
  assert(btn.disabled === false,
    "an operator's 'Close roof now' is natively disabled — control.mount is exactly the cap this route needs");
  assert(btn.getAttribute("aria-disabled") !== "true",
    "'Close roof now' is announced as disabled to an operator who can perform it");

  // The one that catches the real defect: the button was LIVE to a dispatched
  // click and to a keyboard, but sat inside the config lock's
  // `opacity-50 pointer-events-none` wrapper, so no finger could ever reach it.
  const dead = deadenedBy(btn);
  assert(dead === null,
    "'Close roof now' sits inside a deadened container (" +
    `class="${dead?.className}" aria-disabled="${dead?.getAttribute?.("aria-disabled")}"), ` +
    "so it is unreachable by touch — the app's only manual roof close, gone for the operator");

  await click(btn);
  await flush();
  assert(calls.includes("POST /api/dome/close"),
    "the operator's close never reached the server");

  // ...and the lock note must not claim otherwise.
  assert(/separate permission/.test(text()),
    `the config lock note still speaks for the manual close: ${text().slice(-500)}`);
  delete routes["POST /api/dome/close"];
});

await test("Roof: a VIEWER — who holds no control.mount — cannot close, and is told why", async () => {
  // The converse of the test above: the split must not become "always enabled".
  routes["GET /api/dome/state"] = domeOpen;
  seedStore({ caps: ["view.status", "view.preview"], lanes: [] });
  await mount(createElement(SafetyPanel as never));

  const btn = buttonWith(/Close roof now/);
  assert(btn.disabled === true,
    "a viewer can press 'Close roof now' — the tap is a 403 the button never admits to");
  assert(/control\.mount/.test(text()),
    `nothing on screen names the missing capability: ${text().slice(-400)}`);
});

await test("Roof: a refused write puts the switch back where the SERVER is", async () => {
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/config"] = () => ({ status: 403, body: { detail: "forbidden" } });
  seedStore();
  await mount(createElement(SafetyPanel as never));

  const sw = byLabel("Close roof on unsafe");
  assert(sw.getAttribute("aria-checked") === "false", "precondition: the flag starts OFF");
  assert(sw.disabled === false, "precondition: an admin can press it");

  await click(sw);
  await flush();

  const again = byLabel("Close roof on unsafe");
  assert(again.getAttribute("aria-checked") === "false",
    "the switch reads ON after the server refused the write — the rig is left believing a rain trip closes the roof");
  assert(/config\.safety/.test(text()),
    `the refusal is not explained anywhere: ${text().slice(-400)}`);
  delete routes["POST /api/config"];
});

await test("Roof: 'Close roof now' reports the LANE, not the 40ms POST", async () => {
  // /api/dome/close _spawns the "goto" lane with replace=True: the park and the
  // shutter travel both happen inside it, and a second tap CANCELS the park and
  // starts over. The old button derived its state from the POST promise, so it
  // sat there looking ready and re-pressable for the whole close.
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/dome/close"] = () => ({ status: 200, body: { started: "goto" } });
  seedStore({ lanes: [] });
  await mount(createElement(SafetyPanel as never));

  const btn = buttonWith(/Close roof now/);
  assert(btn != null, `no close-roof button on screen: ${text().slice(0, 200)}`);
  assert(btn.disabled === false, "precondition: the button starts live with a dome connected");

  await click(btn);
  // The rig has NOT answered yet — busy_lanes is still empty. This is the exact
  // window in which the old button was re-pressable.
  const during = buttonWith(/Closing|Close roof now/);
  assert(during.disabled === true,
    "the close button is pressable again immediately after the tap — a second press cancels the park in flight");
  assert(/Closing…/.test(during.textContent || ""),
    `the button does not say it is working: "${during.textContent}"`);

  // Now the rig picks the lane up, and later drops it.
  await setLanes(["goto"]);
  assert(buttonWith(/Closing|Close roof now/).disabled === true,
    "the button re-enabled while the server still reports the goto lane in flight");

  routes["GET /api/dome/state"] = () => ({
    status: 200,
    body: { connected: true, shutter: "closed", requires_park_before_close: true, can_slave: false },
  });
  await setLanes([]);
  await flush();

  assert(/Roof closed/.test(text()),
    `the badge still does not know the roof shut: ${text().slice(0, 300)}`);
  assert(buttonWith(/Close roof now/) != null,
    "the button never came back after the lane finished — the control is dead");
  delete routes["POST /api/dome/close"];
});

await test("Roof: somebody else's slew does not block the close — it asks first", async () => {
  // The goto lane is shared. A second tap on OUR OWN close cancels the park we
  // already started (replace=True), which is pure loss — that stays disabled
  // (the test above). A slew somebody ELSE started is the opposite case: the
  // server calls hub.bump_motion_epoch() and respawns with replace=True
  // SPECIFICALLY so the close can pre-empt one. Blocking it for the 30-90s of a
  // goto_and_center, and saying "wait for it to finish", made the protective
  // action queue behind the hazard. Rain does not wait for a slew.
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/dome/close"] = () => ({ status: 200, body: { started: "goto" } });
  seedStore({ lanes: ["goto"] });
  await mount(createElement(SafetyPanel as never));

  const btn = buttonWith(/Close roof now/);
  assert(btn != null, `no close-roof button on screen: ${text().slice(0, 200)}`);
  assert(btn.disabled === false,
    "the roof close is dead for the whole of somebody else's slew — 30-90s during which nothing can shut the roof");
  assert(/mount is moving/.test(text()),
    `the panel does not say the mount is moving: ${text().slice(0, 400)}`);
  assert(/Hold the button|stop the mount yourself/.test(text()),
    `the copy does not name a way through, only a wait: ${text().slice(0, 400)}`);

  // It must not fire on a single tap: this cancels a move in flight.
  await click(btn);
  assert(!calls.includes("POST /api/dome/close"),
    "one tap cancelled a slew in flight with no confirmation at all");
  const pending = (useStore.getState() as any).confirm;
  assert(pending != null,
    "the tap did nothing and offered nothing — the close is simply swallowed during a slew");
  assert(pending.mode === "hold",
    `pre-empting a moving mount should take a deliberate hold, got mode="${pending.mode}"`);

  // Backing out leaves the mount alone.
  await act(async () => { (useStore.getState() as any).resolveConfirm(false); });
  await flush();
  assert(!calls.includes("POST /api/dome/close"),
    "cancelling the confirm still sent the close");

  // Confirming gets the roof shut.
  await click(buttonWith(/Close roof now/));
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await flush();
  assert(calls.includes("POST /api/dome/close"),
    "the confirmed close never reached the server — the roof stays open through the weather");
  delete routes["POST /api/dome/close"];
});

await test("Roof: a REFUSED close does not leave the panel narrating a move that is not happening", async () => {
  // The pending latch used to be armed BEFORE the POST. A refusal cleared the
  // local flag but not the 6s latch, so for six seconds the button was dead and
  // the line beside it read "The mount is moving" while the error toast said the
  // close had failed. Arm on ACCEPTANCE only.
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/dome/close"] = () => ({ status: 500, body: { detail: "dome jammed" } });
  seedStore({ lanes: [] });
  await mount(createElement(SafetyPanel as never));

  const btn = buttonWith(/Close roof now/);
  assert(btn.disabled === false, "precondition: the close starts live");
  assert(!/mount is moving/.test(text()), "precondition: nothing is moving");

  await click(btn);
  await flush();
  assert(calls.includes("POST /api/dome/close"), "precondition: the close was actually attempted");

  const after = buttonWith(/Close roof now|Closing/);
  assert(after.disabled === false,
    "the close is still dead after the server refused it — the operator cannot retry for six seconds");
  assert(!/mount is moving/.test(text()),
    `the panel claims the mount is moving after a refused close: ${text().slice(-400)}`);
  assert(/Park the mount, then close the roof now/.test(text()),
    `the control does not read as ready again: ${text().slice(-400)}`);
  delete routes["POST /api/dome/close"];
});

await test("Roof: a refused SETTING reports itself at the switch, above the manual close", async () => {
  // #3's residual: `err` rendered only at the foot of the panel. The roof block
  // then grew and pushed the manual close between the switch and its own error,
  // so a refusal reported itself further from the control than before.
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/config"] = () => ({ status: 403, body: { detail: "forbidden" } });
  seedStore();
  await mount(createElement(SafetyPanel as never));

  await click(byLabel("Close roof on unsafe"));
  await flush();

  const t = text();
  const errAt = t.indexOf("Changing the roof settings needs");
  const btnAt = t.indexOf("Close roof now");
  assert(errAt >= 0, `the refusal is not reported at all: ${t.slice(-400)}`);
  assert(btnAt >= 0, "precondition: the manual close is on screen");
  assert(errAt < btnAt,
    "the refusal is printed below the manual close button — a screenful away from the switch that snapped back");
  delete routes["POST /api/config"];
});

await test("Sun avoidance: a refused re-arm reports itself before the roof section", async () => {
  routes["GET /api/dome/state"] = domeOpen;
  routes["POST /api/config"] = () => ({ status: 403, body: { detail: "forbidden" } });
  seedStore({ config: makeConfig({ safety: { ...SAFETY, solar_avoidance: false } }) });
  await mount(createElement(SafetyPanel as never));

  const sw = byLabel("Sun avoidance");
  assert(sw.getAttribute("aria-checked") === "false", "precondition: avoidance starts OFF");
  assert(sw.disabled === false, "precondition: an admin can re-arm it");

  await click(sw); // re-arming is the no-confirm direction
  await flush();

  const t = text();
  const errAt = t.indexOf("Sun avoidance can only be changed by an admin");
  const roofAt = t.indexOf("Observatory roof");
  assert(errAt >= 0, `the refused re-arm is not reported anywhere: ${t.slice(-400)}`);
  assert(roofAt >= 0, "precondition: the roof section is on screen");
  assert(errAt < roofAt,
    "the refused re-arm is reported below the whole roof section, not beside the switch that snapped back");
  delete routes["POST /api/config"];
});

await test("Roof: the shutter badge follows a poll, not the page-load snapshot", async () => {
  // It was fetched ONCE on mount. A rain trip five minutes later closed the roof
  // and this badge — the only roof readout in Settings — went on saying "Roof
  // open" for the rest of the visit.
  intervals.length = 0;
  routes["GET /api/dome/state"] = domeOpen;
  seedStore();
  await mount(createElement(SafetyPanel as never));
  assert(/Roof open/.test(text()), "precondition: the badge shows the mount-time state");
  assert(intervals.length > 0, "the panel registered no repeating refresh at all");

  routes["GET /api/dome/state"] = () => ({
    status: 200,
    body: { connected: true, shutter: "closed", requires_park_before_close: true, can_slave: false },
  });
  await act(async () => {
    for (const i of intervals) i.fn();
    await Promise.resolve(); await Promise.resolve();
  });
  assert(/Roof closed/.test(text()),
    `the badge is still a page-load snapshot: ${text().slice(0, 300)}`);
});

// ================================================ alert sinks: a busy row is
// one row

await test("Alerts: testing one channel does not deaden the other rows' Test buttons", async () => {
  // The guard was per PANEL: `if (busyId) return`. While an ntfy test waited on
  // a slow network the Discord row's Test button still looked live, pressed
  // down, and did nothing — no dim, no aria-disabled, no toast. Nothing on
  // screen distinguished that from a broken button.
  routes["GET /api/alerts/health"] = () => ({
    status: 200,
    body: {
      undelivered: 0, undelivered_by_sink: {},
      deadman: { configured: false, healthy: false, last_ping_age_s: null },
    },
  });
  // Sink "a" is held open — a slow SMTP handshake, a webhook that is not
  // answering. That is the whole window in which the panel used to go dead.
  let release: () => void = () => {};
  const hang = new Promise<Reply>((res) => {
    release = () => res({ status: 200, body: { ok: true, verified: true } });
  });
  routes["POST /api/alerts/a/test"] = () => hang;
  routes["POST /api/alerts/b/test"] = () => ({
    status: 200, body: { ok: true, verified: true },
  });

  seedStore({
    config: makeConfig({
      alerts: [
        { id: "a", kind: "ntfy", url: "https://ntfy.sh/x", enabled: true, events: [], token_configured: false },
        { id: "b", kind: "webhook", url: "https://example/hook", enabled: true, events: [], token_configured: false },
      ],
    }),
  });
  const AlertsPanel = (await import("../settings/AlertsPanel")).default;
  await mount(createElement(AlertsPanel as never));

  const tests = ([...container.querySelectorAll("button")] as any[])
    .filter((b) => (b.textContent || "").trim() === "Test");
  assert(tests.length === 2, `expected a Test button per sink, found ${tests.length}`);
  assert(tests.every((b) => b.getAttribute("aria-disabled") == null),
    "precondition: both Test buttons start live");

  await click(tests[0]);
  const after = ([...container.querySelectorAll("button")] as any[])
    .filter((b) => (b.textContent || "").trim() === "Test");
  // PRECONDITION. If row "a" were not actually in flight, row "b" would work for
  // the trivial reason and this test would pass against the broken guard.
  assert(after[0].getAttribute("aria-disabled") === "true",
    "the first row never went busy, so there is no in-flight state to be blocked by");
  assert(!calls.includes("POST /api/alerts/b/test"), "precondition: b has not been tested yet");

  await click(after[1]);
  assert(calls.includes("POST /api/alerts/b/test"),
    "pressing the second channel's Test button sent nothing — the press was swallowed " +
    "because a DIFFERENT row was busy, and the button gave no sign of it");

  release();
  await flush();
});

// ------------------------------------------------------------------- report
if (root) await act(async () => { root.unmount(); });
for (const h of handles) win.clearInterval(h);
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`settingsPanelsDom.test: ${passed}/${total} passed`);
for (const f of failures) {
  // eslint-disable-next-line no-console
  console.log("  " + f);
}

export const result = { passed, failed, total };
export default result;
export { passed, failed, total };
