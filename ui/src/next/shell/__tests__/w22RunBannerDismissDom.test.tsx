// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22RunBannerDismissDom.test.tsx - WP-201 (#983, #984): the #/next banner
// strip's SEQUENCE banner, MOUNTED, across more than one run and from a page
// opened part-way through one.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/shell/__tests__/w22RunBannerDismissDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #983. `Banners.tsx` kept its own `dismissed` record, keyed
// `run:<plan name>:<paused|running>` and never cleared, beside the store's
// `runBanner`, which the same click nulls. When the next run raised the store's
// banner again with the SAME plan name the shell's stale key filtered it out:
// on a tab left open across nights, one tap on the x silenced the banner for
// every later run of a plan called "Tonight" until the page was reloaded. The
// store is the banner's only owner now, so the strip keeps no copy of the
// dismissal and a new run is a new banner.
//
// #984. The store raised the banner only for running and holding, so a page
// opened during a pause, or during an abort's wind-down, showed no banner at
// all. The store half is in src/__tests__/w22RunBannerRaise.test.ts; the cases
// here are what is ON SCREEN.
//
// Every case that expects NO banner follows a control that shows the same
// harness DOES draw one, so an absent banner cannot be a blank strip.
//
// NAMED MUTANTS, each applied to a byte copy of the production file and
// restored byte-identical (md5 checked). The observed failures are in the
// WP-201 report.
//   M1 "dismissal kept by plan name"   Banners.tsx: the run: dismissal is
//                                      written to the local record again (the
//                                      unfixed `onDismiss`)
//   M2 "raise running and holding only"  store.ts: paused and aborting refresh
//                                      an existing banner and never raise one

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
let root: ReturnType<typeof createRoot> | null = null;

type State = "idle" | "running" | "paused" | "holding" | "aborting" | "complete" | "aborted";

/** One sequence frame, delivered the way the WebSocket delivers it. */
async function send(state: State, percent: number, plan = "Tonight"): Promise<void> {
  act(() => {
    useStore.getState().handleEvent({
      type: "sequence",
      data: {
        state,
        plan_name: plan,
        progress: { frames_done: percent, frames_total: 100, percent, elapsed_s: percent * 10, rejected: 0 },
      } as unknown as Record<string, unknown>,
      ts: 0,
    });
  });
  await settle();
}

/** A fully-done rig (so the first-time-setup banner never competes for one of
 *  the two slots) with nothing armed and no run. The only thing that can be on
 *  the strip is the run banner. */
function coldStore(): void {
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
    safety: null, weather: null, runBanner: null, armedBannerDismissed: null,
    resumeArm: null, view: "connect", autoMonitor: false,
    lastCaptureAtMs: null, lastFramesDone: null, selectedPreviewId: null,
    sequence: { state: "idle" } as any,
  } as never); });
}

/** A page that has just been opened: a fresh mount of the strip, so the local
 *  state of one case (a dismissal) cannot reach the next. WITHIN a case the
 *  strip stays mounted, which is the tab left open across runs. */
async function render(): Promise<void> {
  if (root) { const r = root; act(() => { r.unmount(); }); }
  root = createRoot(container);
  const mounted = root;
  await act(async () => {
    mounted.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();
}

const banner = (): any => byId("banner-run");
const text = (): string => String(banner()?.textContent ?? "");
async function dismiss(): Promise<void> {
  const x = banner()?.querySelector(DISMISS);
  assert(x != null, "PRECONDITION: the run banner has no dismiss button to press");
  act(() => { x.click(); });
  await settle();
}

// ====================================== #983: a dismissal belongs to one run

await testAsync("control: an idle rig has no run banner; a running one has it, with its dismiss button", async () => {
  coldStore();
  await render();
  assert(banner() == null, `an idle rig shows a run banner: "${text()}"`);
  await send("running", 10);
  assert(banner() != null, "NO run banner during a run: the harness cannot show one");
  assert(text().includes("SEQUENCE RUNNING") && text().includes("Tonight"), `the banner reads "${text()}"`);
  assert(banner().querySelector(DISMISS) != null, "the run banner has no dismiss button");
});

await testAsync("dismissed, the banner is gone for the rest of THAT run: a pause, a hold, a resume do not bring it back", async () => {
  coldStore();
  await render();
  await send("running", 20);
  await dismiss();
  assert(banner() == null, "PRECONDITION: pressing the x did not hide the banner");
  for (const [state, pct] of [["running", 21], ["paused", 22], ["running", 23], ["holding", 24], ["running", 25]] as const) {
    await send(state, pct);
    assert(banner() == null, `"${state}" brought back a banner the operator dismissed: "${text()}"`);
  }
});

await testAsync("a dismissed run banner comes back for the NEXT run of a plan with the same name", async () => {
  // The unfixed strip: running (banner), x, complete, running with the same
  // plan name -> store runBanner {"active":true,"plan_name":"Tonight"} but no
  // [data-testid="banner-run"] on the page (issue #983's own probe).
  coldStore();
  await render();
  await send("running", 30);
  await dismiss();
  assert(banner() == null, "PRECONDITION: pressing the x did not hide the banner");
  await send("complete", 100);
  assert(banner() == null, `the banner outlived the run it described: "${text()}"`);
  await send("running", 3);
  assert(useStore.getState().runBanner?.active === true,
    "PRECONDITION: the store did not raise the next run's banner");
  assert(banner() != null,
    "the store raised the next run's banner and the strip does not show it: the old run's dismissal is still held by the plan name");
  assert(text().includes("SEQUENCE RUNNING") && text().includes("3%"), `the banner reads "${text()}"`);
});

await testAsync("and it can be dismissed again, and the run after that shows it once more", async () => {
  coldStore();
  await render();
  await send("running", 30);
  await dismiss();
  await send("aborting", 30);
  await send("aborted", 30);
  await send("running", 1);
  assert(banner() != null, "the second run has no banner after an abort ended the first");
  await dismiss();
  assert(banner() == null, "PRECONDITION: the second run's x did not hide its banner");
  await send("running", 2);
  assert(banner() == null, "the second run's banner came back inside the same run");
  await send("complete", 100);
  await send("running", 1);
  assert(banner() != null, "the third run has no banner: two dismissals silenced the plan name");
});

await testAsync("a run dismissed during a pause does not silence the next run, which starts running", async () => {
  // A control, not a regression test: the old key carried the state
  // (`:paused`, `:running`), so a dismissal made during a pause happened not to
  // collide with the next run's `:running`. It passes on the unfixed strip and
  // must keep passing, so a fix cannot trade one dismissal case for another.
  coldStore();
  await render();
  await send("running", 40);
  await send("paused", 41);
  await dismiss();
  await send("running", 42);
  assert(banner() == null, "the resume brought back a banner dismissed during the pause");
  await send("complete", 100);
  await send("running", 1);
  assert(banner() != null, "the next run has no banner after a dismissal made during a pause");
});

await testAsync("control: a run with a different plan name shows its banner after a dismissal", async () => {
  coldStore();
  await render();
  await send("running", 50, "NGC 7000");
  await dismiss();
  await send("complete", 100, "NGC 7000");
  await send("running", 1, "M 31");
  assert(banner() != null && text().includes("M 31"), `the next plan's banner is not up: "${text()}"`);
});

// ============ #984: a page opened part-way through a run shows the banner

await testAsync("a page opened during a pause shows SEQUENCE PAUSED, and keeps the banner through the resume", async () => {
  coldStore();
  await render();
  await send("paused", 60);
  assert(banner() != null, "a page opened during a pause has no banner: its first frame is \"paused\"");
  assert(text().includes("SEQUENCE PAUSED") && text().includes("Tonight") && text().includes("60%"),
    `the banner reads "${text()}", expected "SEQUENCE PAUSED - Tonight - 60%"`);
  assert(/open live/i.test(text()), `the banner lost its open-live action: "${text()}"`);
  await send("running", 61);
  assert(banner() != null, "the resume took the banner away, or never raised it");
  assert(text().includes("SEQUENCE RUNNING") && text().includes("61%"), `the banner reads "${text()}" after the resume`);
});

await testAsync("a page opened during an abort's wind-down shows SEQUENCE ABORTING, and loses it when \"aborted\" lands", async () => {
  coldStore();
  await render();
  await send("aborting", 70);
  assert(banner() != null, "a page opened during the wind-down has no banner: its first frame is \"aborting\"");
  assert(text().includes("SEQUENCE ABORTING") && text().includes("70%"), `the banner reads "${text()}"`);
  await send("aborted", 70);
  assert(banner() == null, `the banner outlived the run: "${text()}"`);
});

await testAsync("a banner dismissed on a page opened during a pause stays dismissed through the resume", async () => {
  coldStore();
  await render();
  await send("paused", 80);
  await dismiss();
  assert(banner() == null, "PRECONDITION: pressing the x did not hide the banner");
  await send("running", 81);
  assert(banner() == null, "the resume re-raised a banner the operator dismissed");
});

act(() => { root?.unmount(); });

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(`w22RunBannerDismissDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
