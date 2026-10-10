// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21NoSafetyBannerStanding.test.tsx - WP-198 (#908): the #/next banner strip's
// "auto-resume armed with no safety monitor" banner has no dismiss button.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/shell/__tests__/w21NoSafetyBannerStanding.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `Banners.tsx` said this banner "carries no dismiss" because it is a standing
// condition and not news, but the render loop gave every entry an `onDismiss`,
// so `BannerCard` drew an x on it and one tap hid the only line outside
// Session - Now saying that nothing on the rig is watching for rain while a run
// is armed to start by itself. `now/NowBanners.tsx` draws the same warning with
// no x; the strip now agrees with it.
//
// Every case runs after the page it grades has been found: the controls show
// that the strip renders, that a dismissible banner beside it DOES have its x,
// and that a rig with a safety monitor shows no such banner, so an absent x
// cannot be a blank strip.
//
// NAMED MUTANT, applied to a byte copy of Banners.tsx and restored
// byte-identical (md5 checked). The observed failure is quoted at the case it
// turns red.
//   S1 "every entry dismissible"  the `e.standing ? undefined :` arm removed
//                                 from the `onDismiss` prop of `BannerCard`

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

// `useSetupFacts()` reads `/api/profiles`; everything else is store-seeded.
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
const { useStore } = await import("../../../store");
const { Banners } = await import("../Banners");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;
const q = (sel: string) => document.getElementById("root")!.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);
const DISMISS = 'button[aria-label="Dismiss this notice"]';
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

document.body.innerHTML = `<div id="root"></div>`;
const container = document.getElementById("root")!;
const root = createRoot(container);

const ARMED = {
  id: "s1", name: "NGC 7000 mosaic", owed: 12, accepted: 48, total: 60,
  origin: "flow", origin_id: "f1",
};

// A fully-done rig (so the first-time-setup banner never competes for one of
// the two slots), a run armed to start by itself, and the safety monitor as
// asked for. Nothing else is newsworthy, so the strip holds exactly the armed
// banner and, when the monitor is missing, the warning under test.
function seed(safetyConnected: boolean): void {
  act(() => { useStore.setState({
    wsPhase: "up", telemetryStale: false, equipConnected: true,
    principal: { role: "admin", email: "a@b.c", caps: ["view.status"] } as any,
    authMethods: { methods: [], first_run: false } as any,
    previews: [{ id: "f1" }] as any,
    // A made-up site - not the real one (project rule).
    site: { latitude: 10, longitude: 20, is_default: false, name: "test site" } as any,
    config: {
      version: 1,
      site: { latitude: 10, longitude: 20, is_default: false, name: "test site" },
      optics_computed: { have_optics: true, fov_w_deg: 1.2, fov_h_deg: 0.8 },
      active_profile_id: "p1",
    } as any,
    status: {
      connected: { camera: { connected: true, name: "sim" } },
      camera: { temperature: -10, can_cool: false },
      mode: "sim",
    } as any,
    safety: { connected: safetyConnected } as any,
    weather: null, runBanner: null, armedBannerDismissed: null,
    resumeArm: { armed: ARMED, hold: null, recovering: false, recovery: null },
    sequence: { state: "idle" } as any,
  } as never); });
}

async function render(hub = "sky", sub = ""): Promise<void> {
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE(hub, sub), nowMs: Date.now() }));
  });
  await settle();
}

// ============================================================== the cases

await testAsync("control: with a safety monitor connected the strip shows the armed run and no no-safety warning", async () => {
  seed(true);
  await render();
  const armed = byId("banner-armed");
  assert(armed != null, "no armed banner - the fixture is wrong, not the component");
  assert(byId("banner-no-safety") == null, "a no-safety warning with a safety monitor connected");
});

await testAsync("control: the armed banner beside the warning is dismissible, so a dismiss button is findable", async () => {
  seed(false);
  await render();
  const armed = byId("banner-armed");
  assert(armed != null, "no armed banner - the fixture is wrong, not the component");
  assert(armed.querySelector(DISMISS) != null, "the dismissible armed banner lost its dismiss button");
});

await testAsync("armed with no safety monitor: the warning is on the strip and carries no dismiss button (#908)", async () => {
  // S1 "every entry dismissible", observed (3/5 passed; the case below, which
  // dismisses the banner beside it, fails with it as "grew a dismiss button"):
  //   x armed with no safety monitor: the warning is on the strip and carries
  //   no dismiss button (#908): the no-safety banner carries a dismiss button,
  //   so one tap hides the only warning that nothing is watching for rain
  seed(false);
  await render();
  const warn = byId("banner-no-safety");
  assert(warn != null, "no no-safety banner - the fixture is wrong, not the component");
  assert(/no safety monitor connected/.test(String(warn.textContent)),
    `not the no-safety text: "${warn.textContent}"`);
  assert(warn.querySelector(DISMISS) == null,
    "the no-safety banner carries a dismiss button, so one tap hides the only warning that nothing is watching for rain");
  // Its call to action is still there: it goes to the safety devices.
  const cta = [...warn.querySelectorAll("button")].find((el: any) => /safety/i.test(el.textContent || ""));
  assert(cta != null, "the no-safety banner lost its button to the safety devices");
});

await testAsync("dismissing the banner beside it leaves the warning on the strip (#908)", async () => {
  seed(false);
  await render();
  const armed = byId("banner-armed");
  assert(armed != null && armed.querySelector(DISMISS) != null,
    "precondition: the armed banner and its dismiss button are not on the page");
  await act(async () => { armed.querySelector(DISMISS).click(); });
  await settle();
  assert(byId("banner-armed") == null, "precondition: dismissing the armed banner did not hide it");
  const warn = byId("banner-no-safety");
  assert(warn != null, "the no-safety warning left the strip when the banner beside it was dismissed");
  assert(warn.querySelector(DISMISS) == null, "the no-safety banner grew a dismiss button once it was alone");
});

await testAsync("the warning leaves by itself when a safety monitor connects", async () => {
  seed(false);
  await render();
  assert(byId("banner-no-safety") != null, "precondition: the warning is not on the strip");
  seed(true);
  await render();
  assert(byId("banner-no-safety") == null, "the warning outlived the condition it describes");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(`w21NoSafetyBannerStanding: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
