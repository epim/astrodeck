// rigPolarDom.test.tsx - the POLAR ALIGNMENT sub-sheet, MOUNTED (plan
// hub-rig.md D.2, T-RIG-3 row).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigPolarDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS UNDER TEST, and why each one is worth a file of its own:
//
//   1. The instruments mounted - the reticle, the error number, the action row.
//      A sheet that rendered an empty shell would satisfy every later assertion.
//   2. START posts once and LATCHES. The route returns the instant the task
//      object exists, so for a second the stream still says "idle"; the panel
//      used to read "Not started" over a run that had already committed the
//      mount, and the re-enabled button 409'd on the second tap people gave it.
//   3. A viewer sees the same screen with the reason on it, and presses nothing
//      onto the wire.
//   4. STOP IS LIVE WHILE SOMETHING ELSE IS IN FLIGHT. Routing it through the
//      double-fire guard once made a 40 ms Pause POST the thing that disabled
//      the red button while the mount was still swinging. The user is at the
//      mount with a hex key; this is the assertion that keeps them safe.
//   5. The engine's warnings reach the person holding the bolt, verbatim, and a
//      session abandoned at 25' never reads as a success.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/mount/polar", pretendToBeVisual: true },
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "Image", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
//
// `holdOn` suspends ONE in-flight request so a test can observe the screen while
// a command is on the wire - which is exactly the state that once disabled STOP.
const asked: string[] = [];
let holdOn: string | null = null;
let release: (() => void) | null = null;
const DRIVERS = {
  roles: ["telescope"],
  drivers: [{
    id: "astap-1", type: "astap", label: "ASTAP", host: "", port: 0, enabled: true,
    status: { reachable: true, error: null },
    offers: { devices: [], tasks: ["polar_align", "solve"] },
  }],
};
g.fetch = async (url: string, init?: { method?: string }) => {
  const line = `${init?.method ?? "GET"} ${String(url)}`;
  asked.push(line);
  if (holdOn && String(url).includes(holdOn)) {
    await new Promise<void>((r) => { release = r; });
  }
  if (String(url).includes("/api/drivers")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => DRIVERS };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const {
  PolarSheet, PA_SPREAD_WARNING, SIM_PROVIDER_WARNING, PAUSING_WARNING,
  NO_ALIGNMENT_TO_STOP, staleWarning, newestSolveLine,
} = await import("../sheets/polar");

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

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture",
    "control.mount", "control.guide"],
};
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_derived",
    "control.capture", "control.mount", "control.guide", "control.power",
    "config.backend", "config.safety"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const IDLE_POLAR = {
  state: "idle", az_error: 0, alt_error: 0, total_error: 0,
  progress: 0, message: "", source: null,
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: {
        connected: { telescope: { connected: true, name: "ZWO AM5N" } },
        looping: false,
        busy_lanes: [],
        busy: null,
        backend_links: [{ role: "telescope", connected: true, error: null }],
        mount: {
          ra_hours: 20.5, dec_deg: 60.1, ra_str: "20h 30m", dec_str: "+60° 06'",
          alt: 62, az: 41, tracking: true, parked: false, slewing: false,
          can_find_home: true,
        },
        meridian: {
          status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east",
        },
      },
      equipConnected: true,
      wsPhase: "up",
      principal: OPERATOR,
      polar: IDLE_POLAR,
      logs: [],
      config: { providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" } },
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(PolarSheet as any, { params: {}, depth: 1 })); });
}
function click(node: any): void {
  assert(node != null, "click target missing");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const posted = (needle: string) => asked.some((a) => a === `POST ${needle}`);

// ============================================================ 1. precondition
seed();
mount();

test("the sheet mounted, with the reticle, the number and the actions on it", () => {
  assert(q('[data-testid="rig-polar"]') != null,
    "no sheet marker - the sheet did not render at all");
  assert(q('[data-testid="polar-reticle-card"] svg[role="img"]') != null,
    "the reticle is not on screen - the sheet mounted a shell, not PolarReticle");
  assert(q('[data-testid="polar-total"]') != null, "no total-error readout");
  assert(q('[data-testid="polar-start"]') != null && q('[data-testid="polar-pause"]') != null
    && q('[data-testid="polar-stop"]') != null && q('[data-testid="polar-solve"]') != null,
    "the action row is incomplete");
  assert(q('[data-testid="polar-model"]') != null, "no MOUNT MODEL card");
  assert(q('[data-testid="polar-provider"]') != null, "no PROVIDER row");
  assert(/Not started - press Start Alignment to measure\./.test(text()),
    "an idle aligner does not say it has not started");
  assert(/not solved this session/.test(text()),
    "MOUNT MODEL invents a last solve when nothing solved - the prototype's fixture");
  assert(!/counterweights down/.test(text()),
    "the prototype's fixture line 'counterweights down' is printed as fact (E27)");
});

test("an idle sheet asks the rig for nothing", () => {
  eq(asked.length, 0, `an idle polar sheet put requests on the wire: ${JSON.stringify(asked)}`);
});

// ================================================================= 2. START
await testAsync("START posts once, then shows the starting latch", async () => {
  asked.length = 0;
  click(q('[data-testid="polar-start"]'));
  await settle();
  assert(posted("/api/polar/start"), `START sent nothing - asked: ${JSON.stringify(asked)}`);
  const before = asked.length;
  // The stream still says "idle" - the driver's first publish has not landed.
  eq((useStore.getState().polar as any).state, "idle",
    "precondition: the aligner has not reported a live state yet");
  const btn = q('[data-testid="polar-start"]');
  assert(/STARTING/.test(btn.textContent || ""),
    "START does not latch - the panel reads 'not started' over a committed mount");
  assert(btn.getAttribute("aria-disabled") === "true",
    "a latched START is still pressable - the second tap 409s");
  click(btn);
  await settle();
  eq(asked.length, before, `the second press reached the rig: ${JSON.stringify(asked.slice(before))}`);
});

// ================================================================ 3. viewer
await testAsync("a viewer sees the same screen, dimmed, and it sends nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  const start = q('[data-testid="polar-start"]');
  assert(start != null, "START is HIDDEN from a viewer - nothing may be hidden");
  eq(start.getAttribute("aria-disabled"), "true", "START is not honest-disabled for a viewer");
  assert(/needs operator or admin access/.test(start.getAttribute("title") || ""),
    `START carries no cap sentence for a viewer - title was ${start.getAttribute("title")}`);
  const stop = q('[data-testid="polar-stop"]');
  assert(/Stopping an alignment needs operator or admin access\./
    .test(stop.getAttribute("title") || ""),
    "STOP does not name who may stop an alignment");
  asked.length = 0;
  click(start);
  click(stop);
  click(q('[data-testid="polar-solve"]'));
  await settle();
  eq(asked.length, 0, `a viewer's presses reached the rig: ${JSON.stringify(asked)}`);
});

// ====================================================== 4. STOP is never dead
await testAsync("STOP stays live while another command is in flight", async () => {
  seed({
    polar: { ...IDLE_POLAR, state: "pausing", total_error: 4.2, az_error: -2, alt_error: 3.6 },
  });
  mount();
  await settle();
  assert(q('[data-testid="polar-pausing"]') != null,
    "precondition: a pausing alignment does not warn that the mount may still be moving");
  assert(text().includes(PAUSING_WARNING),
    "the pausing warning is paraphrased rather than said");

  // Put a command on the wire and LEAVE it there.
  holdOn = "/api/mount/solve_sync";
  asked.length = 0;
  click(q('[data-testid="polar-solve"]'));
  await settle();
  assert(posted("/api/mount/solve_sync"), "the held command never went out");
  const stop = q('[data-testid="polar-stop"]');
  assert(stop.getAttribute("aria-disabled") == null,
    "an in-flight command disabled STOP - the 40 ms Pause POST regression, over a moving mount");
  click(stop);
  await settle();
  assert(posted("/api/polar/stop"),
    `STOP did not reach the rig while another command was in flight: ${JSON.stringify(asked)}`);
  if (release) { release(); release = null; }
  holdOn = null;
  await settle();
});

test("STOP with nothing running explains instead of firing", () => {
  seed();
  mount();
  const stop = q('[data-testid="polar-stop"]');
  eq(stop.getAttribute("title"), NO_ALIGNMENT_TO_STOP,
    "an idle STOP does not say there is nothing to stop");
});

// ============================================================== 5. the truth
test("every engine warning reaches the person holding the bolt", () => {
  seed({
    polar: {
      ...IDLE_POLAR, state: "running", phase: "adjusting", total_error: 6.4,
      az_error: -2.1, alt_error: 6.0,
      flags: ["position_angle_spread_large", "initial_error_large"],
      stale_updates: 2,
    },
    status: {
      ...(useStore.getState().status as any),
      providers: { polar_align: { kind: "sim", label: "Simulator", reason: "no solver installed" } },
    },
  });
  mount();
  assert(text().includes(PA_SPREAD_WARNING("well past 5°").slice(0, 60)),
    "a fit measured through a camera-angle change is not called untrustworthy");
  assert(q('[data-testid="polar-initial-large"]') != null,
    "the initial-error-large warning has no home - it arrives on the wire and goes nowhere");
  assert(text().includes(staleWarning(2)),
    "a frozen adjust phase does not say the number is older than the last adjustment");
  assert(text().includes(SIM_PROVIDER_WARNING),
    "a simulator provider does not say the numbers are fabricated");
});

test("a session abandoned at 25 arcmin never reads as a success", () => {
  seed({ polar: { ...IDLE_POLAR, state: "done", total_error: 25.3, az_error: 20, alt_error: 15 } });
  mount();
  const summary = q('[data-testid="polar-summary"]');
  assert(summary != null, "a finished session says nothing about how it ended");
  assert(/Session ended at 25\.3′/.test(summary.textContent || ""),
    "a run abandoned at 25' is reported as an alignment");
  assert(!/aligned to/.test(summary.textContent || ""),
    "the green 'aligned to' line is printed under a 'Keep going' verdict");
  // The INSTRUCTION's bands are its own (30'/10'/1'), deliberately not the
  // verdict's: at 25' the bolts still have the travel, so it must not send the
  // user to the tripod.
  assert(/Use the azimuth and altitude bolts\./.test(summary.textContent || ""),
    "the summary does not say what to physically do next");
});

test("a good session says so, without a glyph (E18)", () => {
  seed({ polar: { ...IDLE_POLAR, state: "done", total_error: 1.2, az_error: -0.9, alt_error: 0.8 } });
  mount();
  const summary = q('[data-testid="polar-summary"]');
  assert(/aligned to 1\.2′ total error/.test(summary.textContent || ""),
    "an excellent session does not report its error");
  assert(!/[✓✔]/.test(summary.textContent || ""),
    "the design's check glyph survived - house rule: no glyphs");
});

// ============================================================ 6. the provider
await testAsync("the provider row offers what actually runs, and says where a save lands", async () => {
  seed({
    principal: ADMIN,
    config: {
      providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
      effective: {
        "providers.polar_align": {
          value: "astap-1", layer: "profile", profile_id: "p1", profile_name: "Backyard",
        },
      },
    },
  });
  mount();
  await settle();
  const pick = q('[data-testid="polar-provider-pick"]');
  assert(pick != null, "no provider picker for an admin");
  const values = [...pick.querySelectorAll("option")].map((o: any) => o.value);
  assert(values.includes("auto"), "the provider picker does not offer Auto");
  assert(values.includes("astap-1"),
    `a reachable driver that offers polar_align is not listed - got ${JSON.stringify(values)}`);
  const note = q('[data-testid="polar-provider-note"]');
  assert(note != null && /Backyard/.test(note.textContent || ""),
    "a profile-pinned row does not say the save rewrites the profile, not the global setting");
  assert(q('[data-testid="polar-provider-unpin"]') != null,
    "a pinned row offers no way back to the global setting");
});

await testAsync("CLEAR THE PROFILE PIN is honest-disabled for a non-holder, not hidden (review #77)", async () => {
  seed({
    principal: OPERATOR,                       // no config.backend
    config: {
      providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
      effective: {
        "providers.polar_align": {
          value: "astap-1", layer: "profile", profile_id: "p1", profile_name: "Backyard",
        },
      },
    },
  });
  mount();
  await settle();
  const unpin = q('[data-testid="polar-provider-unpin"]');
  assert(unpin != null,
    "the pin control is HIDDEN from a non-holder, so the row reads as unremovable "
    + "with nothing on screen saying who can remove it (ARCHITECTURE section 8)");
  eq(unpin.getAttribute("aria-disabled"), "true", "it looks live to an operator");
  assert((unpin.getAttribute("title") ?? "").length > 0,
    "it is dimmed with no reason attached");
  const before = asked.length;
  click(unpin);
  await settle();
  eq(asked.length, before, "a locked press reached the server");
});

// ================================ 7. the engine's running message (review #10)
//
// Both readers of `polar.message` sat behind `state === "error"`, while the
// server publishes load-bearing sentences there WHILE IT RUNS: "plate solve
// failed 3x - still retrying", "measured point 2/3", "adjust the mount", and
// the two stale-update lines. Without them a solver on its third retry looks
// exactly like a slow exposure, at the mount, in the dark.
const RUNNING_MESSAGES = [
  "native TPPA: measured point 2/3",
  "native TPPA: plate solve failed 3x - still retrying",
  "adjust the mount",
];
for (const msg of RUNNING_MESSAGES) {
  await testAsync(`a running alignment renders the engine's own line: "${msg}"`, async () => {
    seed({
      polar: {
        ...IDLE_POLAR, state: "running", progress: 0.4, activity: "solving",
        message: msg,
      },
    });
    mount();
    await settle();
    const card = q('[data-testid="polar-reticle-card"]');
    assert(card != null, "the reticle card is not on the page - this assertion would be vacuous");
    const line = q('[data-testid="polar-message"]');
    assert(line != null,
      `the engine's running message has no home outside the error state: "${msg}"`);
    eq((line.textContent as string).trim(), msg,
      "the message was paraphrased instead of shown");
    assert(card.contains(line),
      "the message renders somewhere other than under the reticle, where the eye is");
  });
}

await testAsync("an EMPTY message prints nothing, and the error state still owns its own card", async () => {
  seed({ polar: { ...IDLE_POLAR, state: "running", message: "" } });
  mount();
  await settle();
  assert(q('[data-testid="polar-reticle-card"]') != null, "precondition: no reticle card");
  assert(q('[data-testid="polar-message"]') == null,
    "an empty message rendered an empty line");

  seed({ polar: { ...IDLE_POLAR, state: "error", message: "solver never converged" } });
  mount();
  await settle();
  const err = q('[data-testid="polar-error"]');
  assert(err != null && /solver never converged/.test(err.textContent || ""),
    "the error card lost the message");
  assert(q('[data-testid="polar-message"]') == null,
    "the same sentence is now printed twice - once in the error card and once under it");
});

test("the last-solve row reads the newest solver line, by identity", () => {
  const a = { type: "log", ts: 100, data: { level: "info", message: "solve failed", source: "solve" } };
  const b = { type: "log", ts: 200, data: { level: "info", message: "solved & synced", source: "solve" } };
  const other = { type: "log", ts: 300, data: { level: "info", message: "parked", source: "mount" } };
  eq(newestSolveLine([]), null, "an empty log invents a solve");
  eq(newestSolveLine([a, b, other]), b, "the newest SOLVER line is not the one picked");
  eq(newestSolveLine([other]), null, "a non-solver line is read as a solve");
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigPolar.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
