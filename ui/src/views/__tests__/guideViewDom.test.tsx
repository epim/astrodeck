// guideViewDom.test.tsx — the Guide view's controls, MOUNTED, against a rig that
// is part-way through a start.
//
//   Run directly:  npx tsx src/views/__tests__/guideViewDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS FOR. `POST /api/guide/start` and `POST /api/guide/calibrate` both
// `_spawn` the server's "guide" lane and return `{"started": "guide"}` the
// instant the task is CREATED — while the star search and the 1-3 minute
// calibration walk are still ahead. For all of that time `guider.guiding` is
// FALSE, and every control on this panel was gated on `guiding` alone. So the
// screen said, simultaneously, "Calibrating the guider…" at the top and
// "Guiding isn't running — there is nothing to stop" at the bottom; Start
// offered itself and answered `'guide' is already running`; and Force
// Recalibrate — whose route spawns with replace=True — offered itself and
// CANCELLED the walk in flight to start another one.
//
// None of that is visible in a render of one state. It is a claim about which
// state the controls derive from, so the tests below seed the store the way a
// 2s status frame does (`busy_lanes` + the guider's `phase`), re-render, and
// read the buttons. Two of them press a blocked button and assert the rig was
// NOT called, with a positive control at the end that presses the same button
// when the rig IS idle and asserts it WAS — otherwise "no request was sent"
// would pass just as well against a selector that matched nothing.
//
// THE OTHER DIRECTION MATTERS JUST AS MUCH, and this file was green over its
// absence: a gate that reads the `phase` alone dims those same controls FOREVER
// after an ordinary failed calibration, because the native guider leaks
// `_phase_hint = "calibrating"` on every error path. So the last three cases
// here seed the lane list EMPTY under a stale phase and assert the rig is still
// commandable — and, so the fallback that gate exists for is not quietly
// deleted instead, one case omits `busy_lanes` altogether (a server too old to
// publish it) and asserts the phase is still trusted there.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store or the view are imported: api.ts reads
// window.location at module scope and the store reads localStorage/matchMedia.
// run-tests.mjs gives this file its own process, so planting globals here cannot
// leak into another suite.
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

// ------------------------------------------------------------- the fake rig
type Call = { method: string; url: string; body: any };
const calls: Call[] = [];
type Reply = { status?: number; body?: unknown };
let answer: (c: Call) => Reply = () => ({ body: {} });

const fakeFetch = async (input: any, init: any) => {
  const call: Call = {
    method: init?.method ?? "GET",
    url: String(input),
    body: init?.body ? JSON.parse(init.body) : undefined,
  };
  calls.push(call);
  const r = answer(call);
  const status = r.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "error",
    json: async () => r.body ?? {},
  } as any;
};

/** Requests to `path` seen so far — the unit every assertion below counts in. */
const hits = (method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url.endsWith(path)).length;
const lastCall = (method: string, path: string) =>
  [...calls].reverse().find((c) => c.method === method && c.url.endsWith(path));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
Object.defineProperty(g, "fetch", { value: fakeFetch, writable: true, configurable: true });
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const GuideView = (await import("../GuideView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const toasts: { level: string; msg: string }[] = [];

/** A Guiding Assistant report in the server's `report_dict` shape, cut down to
 *  one recommendation — enough for the panel to render its summary card. */
const REPORT = {
  measurements: {
    n: 120, rms_ra_px: 0.4, rms_dec_px: 0.5, rms_total_px: 0.64,
    rms_ra_arcsec: 0.6, rms_dec_arcsec: 0.75, rms_total_arcsec: 0.96,
    drift_per_min_px: 0.8, drift_per_min_arcsec: 1.2,
    pe_amplitude_px: 0.3, pe_period_s: 480, jitter_px: 0.2,
    image_scale_arcsec: 1.5, image_scale_known: true,
    backlash: {
      bl_px: 0.9, bl_ms: 420, sigma_ms: 40, north_rate: 0.002,
      result_code: "VALID", y_rate_source: "calibration", halted: false,
      measured: true,
    },
  },
  recommendations: [{
    key: "ra_min_move", field: "ra_min_move", current: 0.2, recommended: 0.31,
    unit: " px", rationale: "seeing jitter is 0.2 px", confidence: "high",
    advanced: false,
  }],
  current: {
    ra_algorithm: "hysteresis", dec_algorithm: "resist_switch",
    dec_guide_mode: "auto", blc_pulse_ms: 0,
    ra_params: { min_move: 0.2 }, dec_params: { min_move: 0.2 },
  },
  polar: { verdict: "polar alignment looks fine", tone: "good", drift_per_min_arcsec: 1.2 },
  samples: [{ t: 0, ra: 0.1, dec: -0.1 }, { t: 2, ra: -0.2, dec: 0.15 }],
};

/** A status frame. `lanes` is `status.busy_lanes` (hub.busy_lanes — the same set
 *  `busy` collapses to one word) and `phase` is GuideStats.phase, which only the
 *  native guider fills in. Both are real fields off the 2s poll, not pokes.
 *
 *  `lanes: null` omits `busy_lanes` from the frame entirely — the ONE case that
 *  is not "no lane is busy": a server too old to publish the field (pre
 *  2026-08-05), which is the only condition the phase fallback is allowed to
 *  fire on. `[]` is an authoritative "nothing is running". */
function seed({ lanes = [] as string[] | null, phase = "idle", guiding = false } = {}) {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim",
      busy_lanes: lanes === null ? undefined : lanes,
      providers: { guide: { kind: "astrodeck" } },
      guider: {
        name: "native", guiding, phase,
        rms_ra: 0.4, rms_dec: 0.5, rms_total: 0.64, snr: 30,
        recent: [{ t: 0, ra: 0.1, dec: 0.1 }], is_arcsec: true, image_scale: 1.5,
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.guide"] },
    showToast: (level: string, msg: string) => { toasts.push({ level, msg }); },
  } as never);
}

/** Let React run effects AND the promise chains they start (every control here
 *  goes through the async api client). */
async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

function mount() {
  const div = win.document.createElement("div");
  win.document.body.appendChild(div);
  const root = createRoot(div);
  act(() => { root.render(createElement(GuideView)); });
  return { container: div as any, root };
}

const btns = (c: any) => [...c.querySelectorAll("button")] as any[];
const button = (c: any, re: RegExp) =>
  btns(c).find((b) => re.test((b.textContent || "").trim()));
const panel = (c: any, title: string) =>
  [...c.querySelectorAll("section.panel")].find(
    (s: any) => (s.querySelector("h2")?.textContent || "").trim() === title) as any;
const click = async (node: any) => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
  await flush();
};
const blocked = (b: any) => b?.getAttribute("aria-disabled") === "true";

// ======================================================= the control buttons
answer = (c) => {
  if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
    return { body: { report: null } };
  }
  return { body: {} };
};

seed();
const A = mount();
await flush();

await test("the view mounted — the four guide controls are in a real document", async () => {
  // The anti-blank-page guard: every assertion below is about the STATE of one
  // of these buttons, and "no such button" would pass most of them vacuously.
  for (const re of [/^Start Guiding$/, /^Stop$/, /^Force Recalibrate$/, /^Dither$/]) {
    assert(button(A.container, re) != null, `no ${re} button rendered`);
  }
});

await test("idle + connected: Start is live, Stop says there is nothing to stop", async () => {
  await act(async () => { seed(); });
  assert(!blocked(button(A.container, /^Start Guiding$/)),
    "Start is blocked on an idle, connected guider — the fixture is wrong");
  assert(blocked(button(panel(A.container, "Control"), /^Stop$/)),
    "Stop is live with nothing running");
  assert(/there is nothing to stop/.test(A.container.textContent || ""),
    "the idle Stop reason is missing — the later assertion that it is GONE " +
    "during calibration would then prove nothing");
});

await test("mid-calibration: Start is blocked, and pressing it does not reach the rig", async () => {
  await act(async () => { seed({ lanes: ["guide"], phase: "calibrating" }); });
  const start = button(A.container, /^Start Guiding/);
  assert(blocked(start),
    "Start is still live during the calibration walk — pressing it is answered " +
    "`'guide' is already running`");
  const before = hits("POST", "/api/guide/start");
  await click(start);
  assert(hits("POST", "/api/guide/start") === before,
    "the blocked Start still POSTed to the rig");
});

await test("mid-calibration: Force Recalibrate cannot cancel the walk it would restart", async () => {
  // The hazard: /api/guide/calibrate spawns with replace=True, so a second press
  // CANCELS the calibration in flight and begins another two-minute one.
  const cal = button(A.container, /^Force Recalibrate$/);
  assert(blocked(cal), "Force Recalibrate is live during its own calibration walk");
  const before = hits("POST", "/api/guide/calibrate");
  await click(cal);
  assert(hits("POST", "/api/guide/calibrate") === before,
    "pressing Force Recalibrate mid-walk still POSTed — that cancels the walk " +
    "and restarts it");
});

await test("mid-calibration: Stop stops claiming there is nothing to stop", async () => {
  const text = A.container.textContent || "";
  assert(!/there is nothing to stop/.test(text),
    'the panel still reads "there is nothing to stop" while the header reads ' +
    '"Calibrating the guider…"');
  assert(/[Cc]alibrating/.test(text),
    "nothing on screen names the calibration — the fixture never reached the " +
    "state this test is about");
});

await test("guiding: Stop is live again", async () => {
  await act(async () => { seed({ phase: "guiding", guiding: true }); });
  assert(!blocked(button(panel(A.container, "Control"), /^Stop$/)),
    "Stop is dim while guiding is actually running");
});

await test("a run started before this page loaded still shows its bar and Stop", async () => {
  // #8: `running` used to be a local boolean set beside the POST, so a reload
  // mid-run came back to a Run button that 409s. Nothing in THIS component ever
  // started a run; the lane on the status frame is the only evidence, and it is
  // enough.
  await act(async () => { seed({ lanes: ["guide_assistant"] }); });
  const ga = panel(A.container, "Guiding Assistant");
  assert(ga != null, "no Guiding Assistant panel rendered");
  assert(button(ga, /^Run Guiding Assistant$/) == null,
    "the Run button is offered over a run that is already going — pressing it 409s");
  assert(button(ga, /^Stop$/) != null,
    "no Stop for a run in flight: the user cannot call off a 4-minute walk");
});

await test("the loop hunting its lock is stoppable — 'finding' is not always the walk", async () => {
  // `phase: "finding"` means two different things: inside the lane it is the
  // star search the calibration walk begins with (uninterruptible), and after
  // the lane it is the live guide loop waiting for a lock, which Stop ends. Only
  // the first may dim Stop.
  await act(async () => { seed({ lanes: [], phase: "finding" }); });
  assert(!blocked(button(panel(A.container, "Control"), /^Stop$/)),
    "Stop is dim while the guide loop is up and hunting a lock — it would end it");
});

await test("a Stop that never reached the rig is said out loud, not swallowed", async () => {
  await act(async () => { seed({ lanes: ["guide_assistant"] }); });
  // The old handler was `try { post } catch { /* ignore */ }` followed by an
  // unconditional return-to-idle: a 403 or a dropped link left the panel looking
  // finished over a run that was still pulsing the mount.
  const before = toasts.length;
  answer = (c) => {
    if (c.url.endsWith("/api/guide/assistant/stop")) {
      return { status: 403, body: { detail: "control.guide required" } };
    }
    if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
      return { body: { report: null } };
    }
    return { body: {} };
  };
  const ga = panel(A.container, "Guiding Assistant");
  await click(button(ga, /^Stop$/));
  assert(hits("POST", "/api/guide/assistant/stop") === 1, "Stop did not POST");
  assert(toasts.slice(before).some((t) => t.level === "error"),
    "the failed stop raised nothing — the user is told the run is over while it " +
    "is still driving the mount");
  assert(button(panel(A.container, "Guiding Assistant"), /^Stop$/) != null,
    "the panel dropped back to idle after a stop that never landed");
});

await test('"Stopping…" stays up until the RIG stops, not until the POST returns', async () => {
  // POST /api/guide/assistant/stop only sets a cancel flag the run polls
  // between pulses; it resolves in ~40ms while the mount is still being pulsed.
  // Clearing `stopping` in a `finally` therefore made the label and its
  // "checks for this between pulses" note describe the REQUEST: they blinked
  // for one frame and the button went back to reading "Stop" over a live run.
  answer = (c) => {
    if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
      return { body: { report: null } };
    }
    return { body: {} };
  };
  await act(async () => { seed({ lanes: ["guide_assistant"] }); });
  const stop0 = button(panel(A.container, "Guiding Assistant"), /^Stop/);
  assert(stop0 != null && (stop0.textContent || "").trim() === "Stop",
    "the run's Stop button is not in its resting state — nothing below is a change");
  const before = hits("POST", "/api/guide/assistant/stop");
  await click(stop0);
  assert(hits("POST", "/api/guide/assistant/stop") === before + 1, "Stop did not POST");
  // The POST has resolved (click() flushes the promise chain) and the lane is
  // still on the status frame: the run is still going.
  const ga = panel(A.container, "Guiding Assistant");
  assert(/Stopping…/.test(ga?.textContent || ""),
    'the button went back to "Stop" as soon as the request returned — it was ' +
    "reporting the round trip, not the run");
  assert(/between pulses/.test(ga?.textContent || ""),
    "the note explaining the delay went with it");
  // And it is the LANE that releases it: the run ends, a later run gets a
  // usable Stop back rather than a latched "Stopping…".
  await act(async () => { seed({ lanes: [] }); });
  await act(async () => { seed({ lanes: ["guide_assistant"] }); });
  const stop1 = button(panel(A.container, "Guiding Assistant"), /^Stop/);
  assert(stop1 != null && (stop1.textContent || "").trim() === "Stop",
    'the next run opened with a latched "Stopping…" — the latch never cleared');
});

await test("a run refused on the spot shows its failure card, not a full progress bar", async () => {
  // Every failure path publishes a TERMINAL {phase:"error", message} tick
  // (native.py run_guiding_assistant). A refusal that lands inside the first
  // two seconds — parked mount, no star — arrives while `running` is still the
  // local arm() latch, so a bar gated on `running && !report` sat at 100%
  // captioned with the error text, over a live Stop, for the whole 6s grace.
  await act(async () => { seed({ lanes: ["guide_assistant"] }); });
  assert(button(panel(A.container, "Guiding Assistant"), /^Stop/) != null,
    "the run bar is not up — the switch to the failure card below proves nothing");
  await act(async () => {
    useStore.setState({
      guideAssistant: {
        phase: "error", pct: 100,
        message: "native guider: no guide star found",
      },
    } as never);
  });
  await flush();
  const ga = panel(A.container, "Guiding Assistant");
  assert(/The Guiding Assistant stopped/.test(ga?.textContent || ""),
    "the terminal error tick did not raise the failure card");
  assert(/no guide star found/.test(ga?.textContent || ""),
    "the failure card does not say what went wrong");
  assert(button(ga, /^Stop$/) == null,
    "a Stop is still offered for a run that already failed");
  assert(button(ga, /^Try again$/) != null,
    "the failure card offers no way out");
  await act(async () => { useStore.setState({ guideAssistant: null } as never); });
});

await test("a terminal tick fetches the report even when this panel never started the run", async () => {
  // #6/#8 together: the report belongs to the RUN, not to whoever is watching.
  // Here the lane has gone quiet and nothing local was ever set — only the
  // "done" tick says a report exists.
  answer = (c) => {
    if (c.url.endsWith("/api/guide/assistant/report")) return { body: { report: REPORT } };
    if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
      return { body: { report: null } };
    }
    return { body: {} };
  };
  await act(async () => { seed(); });
  await act(async () => {
    useStore.setState({
      guideAssistant: { phase: "done", pct: 100, message: "Recommended settings are ready." },
    } as never);
  });
  await flush();
  assert(hits("GET", "/api/guide/assistant/report") === 1,
    "no report was fetched on the terminal tick — a run the user reloaded through, " +
    "or stopped, ends with its measurements unreachable");
  const ga = panel(A.container, "Guiding Assistant");
  assert(/Apply recommended settings/.test(ga?.textContent || ""),
    "the report was fetched but never rendered");
  await act(async () => { useStore.setState({ guideAssistant: null } as never); });
});

await test("positive control: Force Recalibrate DOES post when the rig is idle", async () => {
  // Without this, the two "nothing was POSTed" assertions above would hold just
  // as well against a selector that matched the wrong node or a click that never
  // reached React.
  await act(async () => { seed(); });
  const cal = button(A.container, /^Force Recalibrate$/);
  assert(!blocked(cal), "Force Recalibrate is blocked on an idle rig");
  const before = hits("POST", "/api/guide/calibrate");
  await click(cal);
  assert(hits("POST", "/api/guide/calibrate") === before + 1,
    "an unblocked Force Recalibrate did not reach the rig — the two blocked-press " +
    "assertions above prove nothing");
});

// ===================================== a stale `phase` must not lock the rig out
// The native guider sets `_phase_hint = "calibrating"` before the walk and
// clears it ONLY on the success path (native.py:454) or in `stop_guiding`. A
// walk that times out or loses its star raises through `_spawn`'s wrapper,
// which just logs — so the hint survives, and every 2s status frame republishes
// it (hub.py → guider.stats() → _current_phase). That is the ORDINARY failure:
// a cloud rolls in during calibration. The lane is long gone; the phase is a
// ghost. Gating the controls on that ghost dimmed Start, Force Recalibrate AND
// Stop for the rest of the session, with no way out from this page — and the
// two it dimmed are the two that recover the rig.
//
// The press above armed `useBusyOrPending`'s local latch; hand it over to the
// server's answer the way a real status frame does, so the assertions below are
// about `phase` and not about a 6s latch.
await act(async () => { seed({ lanes: ["guide"] }); });
await act(async () => { seed({ lanes: [] }); });

await test("precondition: the arm latch has been handed over — Start is live when idle", async () => {
  await act(async () => { seed(); });
  assert(!blocked(button(A.container, /^Start Guiding$/)),
    "Start is still blocked on an idle rig — the phase assertions below would " +
    "hold for the wrong reason");
});

await test("a server too old to publish busy_lanes still trusts phase (the fallback is intact)", async () => {
  // `busy_lanes` ABSENT is the one state that is not an answer. There the
  // phase is all there is, and "calibrating" is worth trusting: the walk only
  // ever runs inside the lane.
  await act(async () => { seed({ lanes: null, phase: "calibrating" }); });
  assert(blocked(button(A.container, /^Start Guiding$/)),
    "Start is live mid-calibration on a server that publishes no lanes — the " +
    "fallback the comment claims was dropped, not narrowed");
  assert(/Guiding begins on its own when it finishes/.test(A.container.textContent || ""),
    "the blocked Start states no reason on the fallback path");
});

await test("a stale 'calibrating' phase with the lane idle leaves the rig recoverable", async () => {
  await act(async () => { seed({ lanes: [], phase: "calibrating" }); });
  // Precondition: the ghost really is on the wire. (The narration line reads
  // from the same `phase` and is still wrong here — the server-side hint leak
  // is the root cause and is not fixed in this file.)
  assert(/Calibrating/.test(A.container.textContent || ""),
    "the fixture never reached the stale-phase state this test is about");
  const start = button(A.container, /^Start Guiding$/);
  const cal = button(A.container, /^Force Recalibrate$/);
  assert(!blocked(start),
    "Start is dim over an authoritative empty lane list — a calibration that " +
    "died minutes ago has locked the user out of restarting guiding");
  assert(!blocked(cal),
    "Force Recalibrate is dim over an authoritative empty lane list — the one " +
    "control that recovers from a failed calibration is unreachable");
  assert(!/Guiding begins on its own when it finishes/.test(A.container.textContent || ""),
    "the panel still promises a walk that is finishing on its own; nothing is " +
    "running");
  // And it is pressable, not merely un-dimmed.
  const before = hits("POST", "/api/guide/calibrate");
  await click(cal);
  assert(hits("POST", "/api/guide/calibrate") === before + 1,
    "Force Recalibrate looks live but never reached the rig");
});

act(() => { A.root.unmount(); });

// ================================================== the Guide Tuning drawer
// The persisted per-axis params were never read, and `chooseRa`/`chooseDec` reset
// the editors to the dossier §15 factory defaults — so the drawer opened on
// defaults whatever the rig held, and Save PUT those defaults back over a
// hand-tuned axis.
const SAVED_SETTINGS = {
  ra_algorithm: "hysteresis",
  dec_algorithm: "resist_switch",
  dec_guide_mode: "auto",
  blc_pulse_ms: 120,
  // min_move/aggression are pinned; hysteresis is null = "not pinned", which
  // must leave the engine default (0.1) showing rather than blanking the field.
  ra_params: { min_move: 0.35, aggression: 0.42, hysteresis: null },
  dec_params: { aggression: 1.4 },
};

/** What `GET /api/guide/calibration` actually answers with: the ENGINE's
 *  in-memory calibration (native.py calibration_report → dump_calibration).
 *  Deliberately a GOOD one — the green LED is the thing under test. */
const CAL = {
  is_valid: true, ortho_error_deg: 1.2, declination_deg: 41, pier_side: "east",
  binning: 1, advisories: [], source: "native",
};

answer = (c) => {
  if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
    // The DELETE below unlinks the PERSISTED file only, and this GET does not
    // read that file — so the report is byte-identical before and after, on
    // purpose. That is the whole point of the test.
    return { body: { report: CAL } };
  }
  if (c.url.endsWith("/api/guide/settings") && c.method === "GET") {
    return { body: SAVED_SETTINGS };
  }
  if (c.url.endsWith("/api/guide/calibration") && c.method === "DELETE") {
    return { body: { cleared: true } };
  }
  return { body: {} };
};

seed();
const B = mount();
await flush();

const axisBlock = (c: any, name: string) =>
  ([...c.querySelectorAll("label")] as any[]).find(
    (l) => (l.querySelector("span")?.textContent || "").trim() === name);
const paramInput = (c: any, axis: string, key: string) => {
  const block = axisBlock(c, axis);
  if (!block) return null;
  const lbl = ([...block.querySelectorAll("label")] as any[]).find(
    (l) => (l.querySelector("span")?.textContent || "").trim() === key);
  return lbl?.querySelector("input") ?? null;
};

await test("opening Guide Tuning reads the rig's saved per-axis params", async () => {
  await click(button(B.container, /^Edit$/));
  assert(axisBlock(B.container, "RA algorithm") != null,
    "the tuning editor did not open — nothing below is being read");
  const minMove = paramInput(B.container, "RA algorithm", "minMove");
  const aggression = paramInput(B.container, "RA algorithm", "aggression");
  assert(minMove != null && aggression != null, "the RA param editors are missing");
  assert(minMove.value === "0.35",
    `RA minMove shows ${minMove.value}, not the rig's 0.35 (the §15 default is 0.2)`);
  assert(aggression.value === "0.42",
    `RA aggression shows ${aggression.value}, not the rig's 0.42 (the §15 default is 0.7)`);
  const hyst = paramInput(B.container, "RA algorithm", "hysteresis");
  assert(hyst != null && hyst.value === "0.1",
    `a null (unpinned) param must keep the engine default 0.1, got ${hyst?.value}`);
});

await test("Save sends back what the rig had, not the factory defaults", async () => {
  const before = hits("PUT", "/api/guide/settings");
  await click(button(B.container, /^Save$/));
  assert(hits("PUT", "/api/guide/settings") === before + 1, "Save did not PUT");
  const put = lastCall("PUT", "/api/guide/settings");
  assert(put?.body?.ra_params?.aggression === 0.42,
    `Save PUT ra aggression ${put?.body?.ra_params?.aggression} — it overwrote the ` +
    "rig's 0.42 with a factory default");
  assert(put?.body?.ra_params?.min_move === 0.35,
    `Save PUT ra min_move ${put?.body?.ra_params?.min_move}, not the rig's 0.35`);
  assert(put?.body?.dec_params?.aggression === 1.4,
    `Save PUT dec aggression ${put?.body?.dec_params?.aggression}, not the rig's 1.4`);
});

await test("clearing the calibration changes what the Calibration panel SAYS", async () => {
  // The earlier version of this test asserted that a GET went out. It did —
  // and it changed nothing, because DELETE removes the persisted
  // `<profile>.json` while GET reports `self._engine.dump_calibration()`. Two
  // different objects: the re-read comes back identical and the green LED
  // stays. So the panel has to state which copy went, and that is what is
  // asserted here.
  const cal = () => panel(B.container, "Calibration");
  assert(cal() != null, "no Calibration panel rendered — nothing below is being read");
  assert(/Good calibration/.test(cal()?.textContent || ""),
    "the panel is not showing a good calibration — the state this is about is " +
    "the GREEN one surviving a Clear");
  assert(!/saved copy/.test(cal()?.textContent || ""),
    "the panel already talks about the saved copy before anything was cleared");

  // The converse first: the route answers `{cleared:false}` when this profile
  // had nothing saved. Nothing happened, so the panel must say nothing — a note
  // that appears on any press would be describing the press, not the rig.
  const deletes0 = hits("DELETE", "/api/guide/calibration");
  answer = (c) => {
    if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
      return { body: { report: CAL } };
    }
    if (c.url.endsWith("/api/guide/calibration") && c.method === "DELETE") {
      return { body: { cleared: false } };
    }
    return { body: {} };
  };
  await click(button(B.container, /^Clear Calibration$/));
  assert(hits("DELETE", "/api/guide/calibration") === deletes0 + 1,
    "Clear Calibration did not DELETE");
  assert(!/saved copy/.test(cal()?.textContent || ""),
    "the panel reports a cleared saved copy over a server that just said there " +
    "was none to clear");

  answer = (c) => {
    if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
      return { body: { report: CAL } };
    }
    if (c.url.endsWith("/api/guide/calibration") && c.method === "DELETE") {
      return { body: { cleared: true } };
    }
    return { body: {} };
  };
  await click(button(B.container, /^Clear Calibration$/));
  assert(hits("DELETE", "/api/guide/calibration") === deletes0 + 2,
    "the second Clear Calibration did not DELETE");

  const after = cal()?.textContent || "";
  assert(/saved copy was cleared/.test(after),
    "the panel says nothing after a Clear — the user pressed a destructive " +
    "button and the green 'calibrated · Good calibration' LED sat there " +
    "unchanged, which reads as 'nothing happened'");
  assert(/next start calibrates from scratch/.test(after),
    "the panel does not say what the clear will actually cost");
  // The other half of honest: the LED did NOT go red, because the guider does
  // still hold this calibration. A test that demanded the LED change would be
  // demanding a lie.
  assert(/Good calibration/.test(after),
    "the panel now claims the calibration is gone — the engine still has it");
});

act(() => { B.root.unmount(); });

// ============================================ the drawer when the GET fails
answer = (c) => {
  if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
    return { body: { report: null } };
  }
  if (c.url.endsWith("/api/guide/settings") && c.method === "GET") {
    return { status: 503, body: { detail: "guider is not responding" } };
  }
  return { body: {} };
};

seed();
const C = mount();
await flush();

await test("a failed load holds Save back instead of letting it write defaults", async () => {
  await click(button(C.container, /^Edit$/));
  assert(axisBlock(C.container, "RA algorithm") != null, "the tuning editor did not open");
  const save = button(C.container, /^Save$/);
  assert(blocked(save),
    "Save is live over fields that are factory defaults, not the rig's — pressing " +
    "it replaces the rig's tuning with them");
  const before = hits("PUT", "/api/guide/settings");
  await click(save);
  assert(hits("PUT", "/api/guide/settings") === before,
    "the blocked Save still PUT the defaults");
  assert(/factory defaults/.test(C.container.textContent || ""),
    "nothing on screen tells the user the values they are looking at are not the rig's");
});

act(() => { C.root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`guideViewDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
