// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sunWatch894Dom.test.tsx - the #/next root shows the sun watch's state (#894).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/shell/__tests__/sunWatch894Dom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server publishes `status.sun_watch` (blind, position unknown, armed) and,
// until #894, no screen read it: a rig with no alert sink showed a quiet UI
// while the Sun approached a tube nothing was watching. Two places in this root
// now say it:
//
//   * the cross-hub banner strip (`Banners`), on every hub, so it is on the Rig
//     hub too, with a button to the mount sheet where TRUST POSITION lives;
//   * the MONITOR - LIVE health strip, where "Night looks OK" used to print
//     over a blind net (the call site passes `status.sun_watch` to the ladder).
//
// Every case runs after the page it grades has been found (the controls with a
// healthy net find "Night looks OK" and no banner), so an absence cannot pass
// over a blank page.
//
// NAMED MUTANTS, each applied to a byte backup and restored byte-identical; the
// observed failure is quoted at the case it turns red.
//   N1 "no banner"                  Banners.tsx: the sun-watch entry is not pushed
//   N2 "banner on Monitor Live too" Banners.tsx: the monitor/live exemption removed
//   N3 "banner for off"             Banners.tsx: `sun.tier === 2` removed
//   N4 "the call site drops it"     LiveScreen.tsx: `sunWatch: status?.sun_watch,` removed
//   N5 "the call site drops mount"  LiveScreen.tsx: `mountConnected: ...` removed
//   N6 "the button goes nowhere"    Banners.tsx: the position-unknown cta removed
//   N7 "the episode is not in the key"  Banners.tsx: `:${sun.since ?? ""}` removed
//      from the banner's key (a new episode stays hidden behind the last one's
//      dismissal)

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};
win.WebSocket = class { close() {} addEventListener() {} removeEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location",
  "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// `useSetupFacts()` reads `/api/profiles`; the Monitor's cold reads fail, which
// is a supported path for every caller on that screen.
g.fetch = async (url: any) => {
  const u = String(url);
  if (u === "/api/profiles") {
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" },
      json: async () => [{ id: "p1", name: "the rig" }], text: async () => "[]",
    };
  }
  throw new Error("offline in this test");
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { Banners } = await import("../Banners");
const { LiveScreen } = await import("../../hubs/monitor/live/LiveScreen");
const { fmtSinceClock, SUN_WATCH_BLIND_STEP, SUN_WATCH_POSITION_STEP } =
  await import("../../../lib/sunWatch");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;
const byId = (id: string) =>
  document.getElementById("root")!.querySelector(`[data-testid="${id}"]`) as any;
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

document.body.innerHTML = `<div id="root"></div>`;
const container = document.getElementById("root")!;
const root = createRoot(container);

/** Twelve minutes and a few seconds ago, so the age reads "12 min" for as long
 *  as this file takes to run. Epoch seconds, as the server sends them. */
const SINCE = Math.floor(Date.now() / 1000) - 12 * 60 - 5;
const CLOCK = fmtSinceClock(SINCE);

const NET = {
  blind: false, blind_since: null, position_unknown: false,
  position_unknown_since: null, last_position_at: SINCE, armed: true,
};
function status(sunWatch: Record<string, unknown> | undefined, mount = true): any {
  return {
    connected: {
      camera: { name: "sim", kind: "sim", connected: true },
      ...(mount ? { telescope: { name: "mount", kind: "sim", connected: true } } : {}),
    },
    camera: { temperature: -10, can_cool: false },
    mode: "sim",
    ...(sunWatch ? { sun_watch: sunWatch } : {}),
  };
}

// A fully-done rig (so the first-time-setup banner never competes for one of
// the two slots) with nothing else newsworthy.
function seedBase(sunWatch: Record<string, unknown> | undefined, mount = true): void {
  act(() => { useStore.setState({
    wsPhase: "up", wsConnected: true, telemetryStale: false, equipConnected: true,
    principal: { role: "admin", email: "a@b.c", caps: ["view.status", "control.mount"] } as any,
    authMethods: { methods: [], first_run: false } as any,
    previews: [{ id: "f1" }] as any,
    // A made-up site - not the real one (project rule).
    site: { latitude: 10, longitude: 20, is_default: false, name: "test site" } as any,
    config: {
      version: 1,
      site: { latitude: 10, longitude: 20, is_default: false, name: "test site" },
      optics_computed: { have_optics: true, fov_w_deg: 1.2, fov_h_deg: 0.8 },
      active_profile_id: "p1",
      // Weather monitored, so the strip's calm line is "Night looks OK" and not
      // the "but weather monitoring is off" variant.
      weather: { enabled: true },
    } as any,
    status: status(sunWatch, mount),
    safety: null, weather: null, runBanner: null, armedBannerDismissed: null,
    resumeArm: null, sequence: { state: "idle" } as any,
  } as never); });
}

async function renderBanners(hub: string, sub: string): Promise<void> {
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE(hub, sub), nowMs: Date.now() }));
  });
  await settle();
}

// ============================================================== the banners

await testAsync("control: a healthy net on the Rig hub shows no sun-watch banner", async () => {
  seedBase({ ...NET });
  await renderBanners("rig", "devices");
  assert(byId("banners") == null || byId("banner-sunwatch") == null,
    "a sun-watch banner with nothing to say");
});

await testAsync("position unknown: the banner names it, the safe order, and goes to the mount", async () => {
  // N1 "no banner", observed (7/10 passed; the blind banner case and the
  // other-Monitor-screens half of the exemption case fail with it):
  //   x position unknown: the banner names it, the safe order, and goes to the
  //   mount: no banner for a net standing down
  seedBase({ ...NET, position_unknown: true, position_unknown_since: SINCE });
  await renderBanners("rig", "devices");
  const b = byId("banner-sunwatch");
  assert(b != null, "no banner for a net standing down");
  const t = String(b.textContent);
  assert(t.includes(`Sun watch is standing down since ${CLOCK} (12 min)`), `since: ${t}`);
  assert(t.includes("will not park the tube"), `what it will not do: ${t}`);
  assert(t.includes(SUN_WATCH_POSITION_STEP), `the safe order: ${t}`);
  // N6 "the button goes nowhere", observed (9/10 passed):
  //   x position unknown: the banner names it, the safe order, and goes to the
  //   mount: no button to the mount, where TRUST POSITION lives
  const btn = [...b.querySelectorAll("button")].find((el: any) => /mount/i.test(el.textContent || ""));
  assert(btn != null, "no button to the mount, where TRUST POSITION lives");
});

await testAsync("blind: the banner names it, since when and the step, on any hub but Monitor Live", async () => {
  seedBase({ ...NET, blind: true, blind_since: SINCE });
  await renderBanners("sky", "");
  const b = byId("banner-sunwatch");
  assert(b != null, "no banner for a blind net");
  const t = String(b.textContent);
  assert(t.includes(`Sun watch is blind since ${CLOCK} (12 min)`), `since: ${t}`);
  assert(t.includes(SUN_WATCH_BLIND_STEP), `the step: ${t}`);
});

await testAsync("on MONITOR - LIVE the banner stays out of the way (the health strip says it)", async () => {
  // N2 "banner on Monitor Live too", observed (9/10 passed):
  //   x on MONITOR - LIVE the banner stays out of the way (the health strip
  //   says it): the banner repeats the strip
  seedBase({ ...NET, blind: true, blind_since: SINCE });
  await renderBanners("monitor", "live");
  assert(byId("banner-sunwatch") == null, "the banner repeats the strip");
  await renderBanners("monitor", "log");
  assert(byId("banner-sunwatch") != null, "the other Monitor screens have no strip, so they get the banner");
});

await testAsync("'not running' is the health strip's notice alone: no banner on every hub for a choice", async () => {
  // N3 "banner for off", observed (9/10 passed):
  //   x 'not running' is the health strip's notice alone: no banner on every
  //   hub for a choice: a banner for a net that was switched off
  seedBase({ ...NET, armed: false });
  await renderBanners("rig", "devices");
  assert(byId("banner-sunwatch") == null, "a banner for a net that was switched off");
});

await testAsync("a status frame with no sun_watch key says nothing (an older server)", async () => {
  seedBase(undefined);
  await renderBanners("rig", "devices");
  assert(byId("banner-sunwatch") == null, "a banner on the strength of a missing key");
});

await testAsync("dismissed, an episode stays dismissed and a NEW episode is announced again", async () => {
  // This case sits last in the banner section on purpose: `Banners` keeps the
  // dismissals for as long as it stays mounted, and the cases above reuse
  // `SINCE` as their episode, so this one uses its own first episode.
  //
  // N7 "the episode is not in the key", observed (10/11 passed; this case only):
  //   x dismissed, an episode stays dismissed and a NEW episode is announced
  //   again: a NEW episode stayed hidden behind the last one's dismissal
  const FIRST = SINCE - 3600;
  seedBase({ ...NET, blind: true, blind_since: FIRST });
  await renderBanners("sky", "");
  assert(byId("banner-sunwatch") != null, "precondition: the first episode's banner is not on the page");
  const x = byId("banner-sunwatch").querySelector('button[aria-label="Dismiss this notice"]');
  assert(x != null, "the banner has no dismiss button");
  await act(async () => { x.click(); });
  await settle();
  assert(byId("banner-sunwatch") == null, "dismissing did not hide the banner");

  // The next status frame says the same episode: still dismissed.
  seedBase({ ...NET, blind: true, blind_since: FIRST });
  await renderBanners("sky", "");
  assert(byId("banner-sunwatch") == null, "the same episode came back after its dismissal");

  // The net recovered and went blind again: a new episode, new news.
  seedBase({ ...NET, blind: true, blind_since: SINCE });
  await renderBanners("sky", "");
  assert(byId("banner-sunwatch") != null,
    "a NEW episode stayed hidden behind the last one's dismissal");
});

// ====================================================== MONITOR - LIVE strip

async function renderLive(): Promise<void> {
  await act(async () => { root.render(createElement(LiveScreen)); });
  await settle();
  assert(byId("monitor-live") != null, "precondition: MONITOR - LIVE did not render");
}
const strip = () => String(byId("monitor-health")?.textContent ?? "");

await testAsync("control: a healthy net leaves the strip saying the night looks OK", async () => {
  seedBase({ ...NET });
  await renderLive();
  assert(/Night looks OK/.test(strip()),
    `the strip is not on the page, so no later absence means anything: "${strip().slice(0, 200)}"`);
  assert(!/Sun watch/.test(strip()), "the strip names the sun watch with nothing to say");
});

await testAsync("blind: the strip says so, since when and the step; 'Night looks OK' is gone", async () => {
  // N4 "the call site drops it", observed (7/10 passed; the position unknown
  // and off cases fail with it):
  //   x blind: the strip says so, since when and the step; 'Night looks OK' is
  //   gone: the strip does not say the net is blind: "Night looks OK"
  act(() => { useStore.setState({ status: status({ ...NET, blind: true, blind_since: SINCE }) } as never); });
  await settle();
  const t = strip();
  assert(t.includes(`Sun watch is blind since ${CLOCK} (12 min)`),
    `the strip does not say the net is blind: "${t.slice(0, 300)}"`);
  assert(t.includes(SUN_WATCH_BLIND_STEP), "the step is missing");
  assert(!/Night looks OK/.test(t), "'Night looks OK' printed over a blind net");
});

await testAsync("position unknown: the strip says the net is standing down, with the safe order", async () => {
  act(() => { useStore.setState({
    status: status({ ...NET, position_unknown: true, position_unknown_since: SINCE }),
  } as never); });
  await settle();
  const t = strip();
  assert(t.includes(`Sun watch is standing down since ${CLOCK} (12 min)`),
    `the strip does not say the net is standing down: "${t.slice(0, 300)}"`);
  assert(t.includes(SUN_WATCH_POSITION_STEP), "the safe order is missing");
  assert(!/Night looks OK/.test(t), "'Night looks OK' printed over a net that will not park");
});

await testAsync("off with a mount connected: a notice chip; off with no mount: nothing", async () => {
  // N5 "the call site drops mount", observed (9/10 passed):
  //   x off with a mount connected: a notice chip; off with no mount: nothing:
  //   no 'Sun watch is not running' chip
  act(() => { useStore.setState({ status: status({ ...NET, armed: false }) } as never); });
  await settle();
  assert(/Sun watch is not running/.test(strip()), "no 'Sun watch is not running' chip");
  act(() => { useStore.setState({ status: status({ ...NET, armed: false }, false) } as never); });
  await settle();
  assert(!/Sun watch is not running/.test(strip()),
    "a net that is not running was called out with no mount to protect");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(`sunWatch894Dom (#/next): ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
