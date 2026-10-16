// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w23BannerConnEpisode.test.tsx - the #/next shell's connection strips are
// dismissed per OUTAGE, not per kind (#1012, WP-218, wave 23).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/__tests__/w23BannerConnEpisode.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `Banners` keyed its connection entries by KIND (`conn:down`,
// `conn:stale`, `conn:signin`, `conn:norig`) and kept the dismissal in a local
// record nothing ever cleared. A phone left open across a night dismissed "No
// link to the rig" once, and every later outage was silenced until a reload:
// the one strip that says the view is the last state received, gone, over a
// frozen run that then reads as live. The same class as #983 (a dismissal keyed
// by something the next episode shares).
//
// WHAT IS ASSERTED, MOUNTED on the live store and driven the way the socket
// drives it (wsPhase, telemetryStale, equipConnected), for each of the four:
//   - the strip is up while its condition holds, and a dismissal hides it;
//   - it STAYS hidden for the rest of that outage, through the phases a
//     reconnect passes through (down, reconnecting, down), so the fix cannot be
//     "forget the dismissal on any change" and turn a dismissal into a nag;
//   - once the condition has cleared, the NEXT outage raises it again;
//   - the no-rig note is not re-raised by leaving the Rig hub, where it is
//     hidden by design: a page change does not end that condition.
//
// MUTANTS, each from a byte backup of ui/src/next/shell/Banners.tsx and
// restored byte-identical (md5sum compared); the failing lines are in the
// WP-218 report.
//   M1 "key by kind"      `useConnEpisode` returns 0 for every episode, so the
//                         keys are `conn:down:0` and the like: the unfixed
//                         behaviour
//   M2 "episode off the page"  `useConnEpisode(conn)` in place of the
//                         page-independent read: leaving the Rig hub, where the
//                         no-rig note is hidden, then counts as a new episode

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

// ====================================================== the link goes down
coldStore();
await show();

test("control: a healthy link and a connected rig show no connection strip", () => {
  assert(conn() == null, `a healthy rig shows a connection strip: "${connText()}"`);
});

await link({ wsPhase: "down" });
test("control: a dropped link raises the strip", () => {
  assert(conn() != null, "no connection strip with the link down");
  assert(/No link to the rig/.test(connText()), `wrong strip for a dropped link: "${connText()}"`);
});

await dismissConn();
test("a dismissed link strip is gone", () => {
  assert(conn() == null, `the strip survived its own dismiss button: "${connText()}"`);
});

await link({ wsPhase: "reconnecting" });
await link({ wsPhase: "down" });
test("...and stays gone through the reconnect attempts of the SAME outage", () => {
  assert(conn() == null, `the dismissed strip came back mid-outage: "${connText()}"`);
});

await link({ wsPhase: "up" });
test("control: the link coming back leaves no strip", () => {
  assert(conn() == null, `a restored link still shows a strip: "${connText()}"`);
});

await link({ wsPhase: "down" });
test("the NEXT outage raises the link strip again", () => {
  assert(conn() != null,
    "the second outage is silent: a strip dismissed once hid every later outage until a reload");
  assert(/No link to the rig/.test(connText()), `wrong strip for the second outage: "${connText()}"`);
});

await dismissConn();
await link({ wsPhase: "up" });
await link({ wsPhase: "down" });
test("...and the one after that, each outage on its own", () => {
  assert(conn() != null, "the third outage is silent");
});

// ============================================== the telemetry goes quiet
coldStore();
await show();
await link({ telemetryStale: true });
test("control: a quiet socket raises the stale strip", () => {
  assert(conn() != null && /Telemetry has gone quiet/.test(connText()),
    `no stale strip: "${connText()}"`);
});
await dismissConn();
test("a dismissed stale strip is gone, and stays gone while the same silence lasts", () => {
  assert(conn() == null, `the stale strip survived its dismiss: "${connText()}"`);
});
await link({ telemetryStale: false });
await link({ telemetryStale: true });
test("the NEXT silence raises the stale strip again", () => {
  assert(conn() != null && /Telemetry has gone quiet/.test(connText()),
    "the second silence is silent: the stale dismissal outlived its episode");
});

// ================================================== sign-in is required
coldStore({
  authMethods: { methods: ["local"], first_run: false } as any,
  principal: null,
});
await show();
await link({ wsPhase: "down" });
test("control: a gated server with no session raises the sign-in strip", () => {
  assert(conn() != null && /Sign-in required/.test(connText()), `no sign-in strip: "${connText()}"`);
});
await dismissConn();
await link({ wsPhase: "up" });
await link({ wsPhase: "down" });
test("the next time the session is missing raises the sign-in strip again", () => {
  assert(conn() != null && /Sign-in required/.test(connText()),
    "the second sign-in prompt is silent");
});

// ===================================================== no rig connected
coldStore({ equipConnected: false });
await show();
test("control: a browser with no rig connected raises the quiet note", () => {
  assert(conn() != null && /No rig connected/.test(connText()), `no note: "${connText()}"`);
});
await dismissConn();
test("a dismissed no-rig note is gone", () => {
  assert(conn() == null, `the note survived its dismiss: "${connText()}"`);
});
await show("rig");
await show("sky");
test("...and leaving and returning to the Rig hub does not raise it again", () => {
  assert(conn() == null,
    `the note came back after a page change; the condition never ended: "${connText()}"`);
});
await link({ equipConnected: true });
await link({ equipConnected: false });
test("the next time there is no rig, the note is raised again", () => {
  assert(conn() != null && /No rig connected/.test(connText()),
    "a rig that was connected and dropped again is silent");
});

// =============================================== the same, under StrictMode
strict = true;
coldStore();
await show();
await link({ wsPhase: "down" });
await dismissConn();
await link({ wsPhase: "reconnecting" });
test("StrictMode: a dismissed strip stays gone through the same outage", () => {
  assert(conn() == null, `the dismissed strip came back under StrictMode: "${connText()}"`);
});
await link({ wsPhase: "up" });
await link({ wsPhase: "down" });
test("StrictMode: the next outage raises the strip again", () => {
  assert(conn() != null && /No link to the rig/.test(connText()),
    "the second outage is silent under StrictMode's doubled render");
});

// ------------------------------------------------------------------- summary
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w23BannerConnEpisode: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
