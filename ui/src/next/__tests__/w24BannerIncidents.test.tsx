// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w24BannerIncidents.test.tsx - the #/next shell's incident strip: one strip
// per fact for a link outage (#1041), and a dismissal that belongs to ONE
// episode of an undated incident (#1042). WP-228, wave 24.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/__tests__/w24BannerIncidents.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #1041. `Banners` suppressed the incident strip for a link outage by comparing
// `incident.id === "inc-link"`, but #268 made the id `inc-link-<pill>-<since>`,
// so the guard never fired and one fact took both slots: "No link to the rig"
// from the connection strip and "LINK LOST" from the incident fold.
// #1042. An incident the rig has not dated (an open cloud hold with no log
// line) had one id for every episode, and the dismissal outlived the hold.
//
// MOUNTED on the live store, driven as the socket drives it.
//
// MUTANTS, each from a byte backup of the production file, restored by cp:
//   M1 crossHub.ts `incidentKind: top.kind` removed: the link guard cannot
//      match (the unfixed behaviour of #1041).
//   M2 crossHub.ts `useIncidentEpisode` returns 0: undated episodes share an
//      id again (the unfixed behaviour of #1042).

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

// `useSetupFacts()` reads `/api/profiles`; one profile keeps the setup banner
// out of the way, so the only strips on screen are the connection ones.
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

const { createElement, act, StrictMode } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { Banners } = await import("../shell/Banners");

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
const root = createRoot(container);
const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;
const inc = (): any => container.querySelector('[data-testid="banner-inc"]');
const conn = (): any => container.querySelector('[data-testid="banner-conn"]');
const connText = (): string => String(conn()?.textContent ?? "");

/** The dev build renders under StrictMode, which runs every component body and
 *  state updater twice. The last section turns it on. */
let strict = false;

/** Mount (or re-render) the shell strip on a hub. A fresh `nowMs` each time. */
async function show(hub = "sky"): Promise<void> {
  const strip = createElement(Banners as any, { route: ROUTE(hub, ""), nowMs: Date.now() });
  await act(async () => {
    root.render(strict ? createElement(StrictMode, null, strip) : strip);
  });
  await settle();
}

/** One store change the way the socket makes it, then a settle. */
async function link(patch: Record<string, unknown>, hub = "sky"): Promise<void> {
  act(() => { useStore.setState(patch as never); });
  await settle();
  // The strip is a function of the store plus the route; a fresh render with the
  // same route is what the shell does on every store change anyway.
  await show(hub);
}

/** Press the strip's x. A strip that is not there is left alone, so a missing
 *  strip is reported by the assertion that expected it and not thrown here. */
async function dismissConn(): Promise<void> {
  const x = conn()?.querySelector(".nx-banner-x");
  if (x == null) return;
  await act(async () => {
    x.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}

function coldStore(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      authMethods: { methods: [], first_run: false } as any,
      principal: { role: "admin", email: null, caps: ["view.status", "view.preview"] } as any,
      wsPhase: "up",
      wsLastEvent: Date.now(),
      telemetryStale: false,
      equipConnected: true,
      runBanner: null,
      resumeArm: null,
      armedBannerDismissed: null,
      weather: null,
      safety: null,
      sequence: { state: "idle" } as any,
      logs: [],
      mountOp: null, focus: null, lastAutofocusResult: null, guide: null,
      lastCaptureAtMs: null,
      lastReportId: null,
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
      ...over,
    } as never);
  });
}

/** Connection plus incident strips: the two that can announce one outage. The
 *  first-time-setup note is unrelated news and is not counted. */
const strips = (): number =>
  container.querySelectorAll('[data-testid="banner-conn"], [data-testid="banner-inc"]').length;
async function dismissInc(): Promise<void> {
  const x = inc()?.querySelector(".nx-banner-x");
  if (x == null) return;
  await act(async () => {
    x.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}

// ================================================ #1041 one strip per fact
coldStore();
await show();
await link({ wsPhase: "down", wsLastEvent: Date.now() - 60_000 });
test("control: the connection strip says the link is down", () => {
  assert(conn() != null && /No link to the rig/.test(connText()), `no conn strip: "${connText()}"`);
});
test("a link outage is ONE strip, not the connection strip plus LINK LOST", () => {
  assert(inc() == null,
    `the incident strip repeats the outage beside the connection strip: "${inc()?.textContent}"`);
  assert(strips() === 1, `expected exactly one strip, found ${strips()}`);
});
await dismissConn();
test("dismissing the connection strip quiets the whole outage", () => {
  assert(strips() === 0, `the outage still shows ${strips()} strip(s) after its dismissal`);
});

coldStore();
await show();
await link({ telemetryStale: true });
test("a silent socket is also ONE strip", () => {
  assert(conn() != null && inc() == null && strips() === 1,
    `stale: ${strips()} strips, incident=${inc()?.textContent ?? "none"}`);
});

// ===================== #1042 an undated incident, dismissed, then again
const HOLD = { state: "holding", hold: "clouds" } as any;
coldStore();
await show();
await link({ sequence: HOLD });
test("control: an undated cloud hold raises the incident strip", () => {
  assert(inc() != null && /CLOUD HOLD/.test(String(inc()?.textContent)),
    `no cloud strip: "${inc()?.textContent}"`);
});
await dismissInc();
test("a dismissed cloud strip is gone and stays gone through the same hold", () => {
  assert(inc() == null, "the strip survived its dismiss");
});
await show("weather");
test("...also after a re-render on another hub", () => {
  assert(inc() == null, "the dismissed strip came back mid-episode");
});
await link({ sequence: { state: "running" } as any });
test("control: the hold clearing leaves no strip", () => {
  assert(inc() == null, "a cleared hold still has a strip");
});
await link({ sequence: HOLD });
test("the NEXT undated hold raises the strip again", () => {
  assert(inc() != null && /CLOUD HOLD/.test(String(inc()?.textContent)),
    "the second undated hold is silent: its dismissal key collided with the first episode's");
});
await dismissInc();
await link({ sequence: { state: "running" } as any });
await link({ sequence: HOLD });
test("...and the one after that", () => {
  assert(inc() != null, "the third undated hold is silent");
});

strict = true;
coldStore();
await show();
await link({ sequence: HOLD });
await dismissInc();
await link({ sequence: { state: "running" } as any });
await link({ sequence: HOLD });
test("StrictMode: the next undated hold raises the strip again", () => {
  assert(inc() != null, "silent under StrictMode's doubled render");
});

act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w24BannerIncidents: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
