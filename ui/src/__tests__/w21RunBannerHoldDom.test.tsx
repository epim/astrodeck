// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21RunBannerHoldDom.test.tsx - the persistent run banner, MOUNTED, through a
// cloud hold (#959, WP-186, wave 21). The store half is
// w21RunBannerHold.test.ts.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/w21RunBannerHoldDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Two screens print the banner: the classic App strip (above every classic view
// but the Monitor) and the #/next shell's Banners (above every hub but
// Session). Each is mounted on the live store and fed the frames the engine
// publishes, in order: running, holding, running, paused, running, aborting,
// aborted. What is asserted is what is ON SCREEN after each frame:
//   - the banner is up at every live step, with its OPEN LIVE action, and is
//     gone after "aborted";
//   - it NAMES the state. The strip used to print only RUNNING or PAUSED, so
//     once the banner stays up through a hold or an abort's wind-down it said
//     SEQUENCE RUNNING over a Monitor whose badge says HOLDING / ABORTING for
//     the same rig;
//   - the classic LED is amber for every state but running.
//
// MUTANTS, each from a byte backup and restored byte-identical (md5sum
// compared); the failing lines are in the WP-186 report.
//   M1 "clear by omission"  store.ts: the unfixed branch (holding clears)
//   M3 "running or paused"  lib/stateMeta.ts runBannerLabel made
//                           `state === "paused" ? "SEQUENCE PAUSED" : "SEQUENCE RUNNING"`

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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// `useSetupFacts()` (the #/next shell) reads `/api/profiles`; nothing else is
// fetched from a store-seeded page.
g.fetch = async (url: any) => {
  const u = String(url);
  if (u === "/api/profiles") {
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" },
      json: async () => [{ id: "p1", name: "the rig" }], text: async () => "[]",
    };
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}), text: async () => "",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const App = (await import("../App")).default;
const { Banners } = await import("../next/shell/Banners");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  for (let i = 0; i < 3; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;

type State = "idle" | "running" | "paused" | "holding" | "aborting" | "complete" | "aborted";

/** One sequence frame, delivered the way the WebSocket delivers it. */
async function send(state: State, percent: number): Promise<void> {
  act(() => {
    useStore.getState().handleEvent({
      type: "sequence",
      data: {
        state,
        plan_name: "Tonight",
        progress: { frames_done: percent, frames_total: 100, percent, elapsed_s: percent * 10, rejected: 0 },
      } as unknown as Record<string, unknown>,
      ts: 0,
    });
  });
  await settle();
}

function coldStore(): void {
  act(() => {
    useStore.setState({
      authMethods: { methods: [], first_run: false } as any,
      principal: { role: "admin", email: null, caps: ["view.status", "view.preview"] } as any,
      wsPhase: "up",
      telemetryStale: false,
      equipConnected: true,
      // Not the Monitor (which never shows the banner) and no auto-select.
      view: "connect",
      autoMonitor: false,
      runBanner: null,
      resumeArm: null,
      armedBannerDismissed: null,
      weather: null,
      safety: null,
      sequence: { state: "idle" } as any,
      lastCaptureAtMs: null,
      lastFramesDone: null,
      status: {
        connected: { camera: { connected: true, name: "sim" } },
        camera: { temperature: -10, can_cool: false },
        mode: "sim",
      } as any,
      // A made-up site - not the real one (project rule).
      site: { latitude: 10, longitude: 20, is_default: false, name: "test site" } as any,
      config: {
        version: 1,
        site: { latitude: 10, longitude: 20, is_default: false, name: "test site" },
        optics_computed: { have_optics: true, fov_w_deg: 1.2, fov_h_deg: 0.8 },
        active_profile_id: "p1",
      } as any,
      previews: [{ id: "f1" }] as any,
    } as never);
  });
}

/** The banner's expected face for each state of the run. */
const FACE: Array<[State, number, string | null, string]> = [
  // state, percent, banner text (null = no banner), classic LED class
  ["idle", 0, null, ""],
  ["running", 10, "SEQUENCE RUNNING", "led-busy"],
  ["holding", 11, "SEQUENCE HOLDING", "led-warn"],
  ["holding", 12, "SEQUENCE HOLDING", "led-warn"],
  ["running", 13, "SEQUENCE RUNNING", "led-busy"],
  ["paused", 14, "SEQUENCE PAUSED", "led-warn"],
  ["running", 15, "SEQUENCE RUNNING", "led-busy"],
  ["aborting", 16, "SEQUENCE ABORTING", "led-warn"],
  ["aborted", 16, null, ""],
];

// ======================================================= the classic App strip
const DISMISS = 'button[aria-label="Dismiss sequence-running banner"]';
const strip = (): any => container.querySelector(DISMISS)?.parentElement ?? null;

win.location.hash = "#/classic";
coldStore();
const appRoot = createRoot(container);
act(() => { appRoot.render(createElement(App)); });
await settle();

test("control: the classic App rendered, and an idle rig has no run banner", () => {
  assert(container.querySelector(".app-header") != null, "no .app-header - the render never reached the App");
  assert(strip() == null, `an idle rig shows a run banner: "${strip()?.textContent}"`);
});

{
  let step = 0;
  for (const [state, pct, text, led] of FACE) {
    await send(state, pct);
    const seen = String(strip()?.textContent ?? "");
    const ledClass = String(strip()?.querySelector(".led")?.className ?? "");
    const n = ++step;
    test(`classic strip, step ${n} (${state} ${pct}%): ${text ?? "no banner"}`, () => {
      if (text === null) {
        assert(strip() == null, `the banner is still up after "${state}": "${seen}"`);
        return;
      }
      assert(strip() != null, `NO run banner after "${state}", and the run is live`);
      assert(seen.includes(text) && seen.includes("Tonight") && seen.includes(`${pct}%`),
        `after "${state}" the strip reads "${seen}", expected "${text} · Tonight · ${pct}%"`);
      assert(/OPEN LIVE/.test(seen), `the strip lost its OPEN LIVE action after "${state}": "${seen}"`);
      assert(ledClass.split(" ").includes(led), `the LED is "${ledClass}" in "${state}", expected ${led}`);
    });
  }
}

await send("running", 20);
await send("holding", 21);
test("control: the strip is up in a hold before it is dismissed", () => {
  assert(strip() != null, "no strip during the hold");
});
act(() => { (container.querySelector(DISMISS) as any)?.click(); });
await settle();
await send("running", 22);
test("a strip dismissed during a hold is not re-raised when the hold lifts", () => {
  assert(strip() == null, `the dismissed banner came back: "${strip()?.textContent}"`);
});
await send("complete", 100);
await send("running", 3);
test("...and the next run raises it again", () => {
  assert(strip() != null, "the next run has no banner");
});

act(() => { appRoot.unmount(); });

// ================================================== the #/next shell's Banners
const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;
const nextBanner = (): any => container.querySelector('[data-testid="banner-run"]');

coldStore();
const nextRoot = createRoot(container);
async function renderBanners(): Promise<void> {
  await act(async () => {
    nextRoot.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();
}
await renderBanners();

test("control: the #/next strip rendered, and an idle rig has no run banner", () => {
  assert(nextBanner() == null, `an idle rig shows a run banner: "${nextBanner()?.textContent}"`);
});

{
  let step = 0;
  for (const [state, pct, text] of FACE) {
    await send(state, pct);
    const seen = String(nextBanner()?.textContent ?? "");
    const n = ++step;
    test(`#/next strip, step ${n} (${state} ${pct}%): ${text ?? "no banner"}`, () => {
      if (text === null) {
        assert(nextBanner() == null, `the banner is still up after "${state}": "${seen}"`);
        return;
      }
      assert(nextBanner() != null, `NO run banner after "${state}", and the run is live`);
      assert(seen.includes(text) && seen.includes("Tonight") && seen.includes(`${pct}%`),
        `after "${state}" the banner reads "${seen}", expected "${text} - Tonight - ${pct}%"`);
      assert(/open live/i.test(seen), `the banner lost its open-live action after "${state}": "${seen}"`);
    });
  }
}

act(() => { nextRoot.unmount(); });

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(`w21RunBannerHoldDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
