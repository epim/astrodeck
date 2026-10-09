// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6DevicesRetryATimeout.test.tsx -- RIG · DEVICES asks a timed-out drivers
// read again by itself instead of replacing the screen with "Couldn't load
// drivers" (#859).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/p6DevicesRetryATimeout.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Harness cut down from rigDevicesDom.test.tsx. Each `/api/drivers` and
// `/api/profiles` read is scripted per call: it answers, or times out the way
// a browser's AbortSignal does (api.ts turns that into "request timed out").
// The retry wait runs at once, or is HELD on a promise the case releases.
//
// Named mutants (each turns the case beside it red):
//   DV    reloadDrivers calls listDrivers directly (no retryTransient) -> the
//         first-load EmptyCard `devices-load-error` replaces the screen.
//   UX12  the `devices-retrying` banner is not rendered -> no banner while the
//         wait is held.
//   UX4   delete DevicesScreen's useRetryOnReturn -> a drivers read that
//         settled as failed is not asked again when the tab comes back.
//   UX10  reloadProfiles calls listProfiles directly (no retryTransient) ->
//         one timed-out profiles read leaves the PROFILE row on "no profile".
//   PG    reloadProfiles' `stop` and setters check only `alive`, not the
//         generation -> the mount's read, still waiting to ask again, asks
//         after a save's reload already landed and overwrites its rows.

/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const ROLES = ["camera", "telescope"];
const DRIVERS = {
  roles: ROLES,
  drivers: [{
    id: "sim", type: "sim", label: "Simulator", enabled: true, implicit: true,
    status: { reachable: true, error: null, detail: null, probed_at: 0 },
    offers: { devices: ROLES.map((r) => ({ role: r, name: `Simulated ${r}` })), tasks: [] },
  }],
};
const profile = (name: string) => ({
  id: "p1", name, mode: "alpaca", devices_count: 2, updated_ts: 1,
});
const ok = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => body });

type Step = "timeout" | "answer";
/** How the n-th read (0-based) of each route answers; set per case. */
let driversStep: (n: number) => Step = () => "answer";
let profilesStep: (n: number) => Step = () => "answer";
/** What the n-th profiles read answers with. */
let profilesRows: (n: number) => unknown[] = () => [];
let driverReads = 0;
let profileReads = 0;
let captures = 0;
const timedOut = (): never => { throw new DOMException("The operation timed out.", "TimeoutError"); };
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  if (u.includes("/api/drivers")) {
    const n = driverReads++;
    if (driversStep(n) === "timeout") timedOut();
    return ok({ ...DRIVERS, drivers: [...DRIVERS.drivers] });
  }
  if (u.includes("/api/profiles/capture") && method === "POST") {
    captures++;
    return ok(profile("Backyard"));
  }
  if (u.includes("/api/profiles") && method === "GET") {
    const n = profileReads++;
    if (profilesStep(n) === "timeout") timedOut();
    return ok(profilesRows(n));
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { setRetrySleepForTests } = await import("../../../../lib/retryLoad");
const { DevicesScreen } = await import("../devices/DevicesScreen");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

function seed(activeProfileId: string | null = null): void {
  act(() => {
    useStore.setState({
      status: {
        connected: {
          camera: { name: "ASI2600MM Pro", kind: "camera", connected: true },
          telescope: { name: "AM5N", kind: "telescope", connected: true },
        },
        backend_links: ROLES.map((role) => ({ role, ok: true, error: null, attempted: true, connected: true })),
      },
      equipConnected: true,
      wsPhase: "up",
      principal: { role: "admin", email: "a@rig", caps: ["view.status", "config.backend"] },
      config: { active_profile_id: activeProfileId },
      sequence: { state: "idle" },
      toasts: [],
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
/** The popover portals to document.body, so it is NOT under `container`. */
const qd = (sel: string) => win.document.querySelector(sel) as any;
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

async function mount(): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  driverReads = 0;
  profileReads = 0;
  captures = 0;
  await act(async () => { root.render(createElement(DevicesScreen as any)); });
  await settle();
}
function reset(): void {
  driversStep = () => "answer";
  profilesStep = () => "answer";
  profilesRows = () => [];
  setRetrySleepForTests(async () => {});
}
const held = () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  setRetrySleepForTests(() => gate);
  return () => release();
};

seed();

await test("DV a drivers read that times out once and then answers shows the screen, not the error", async () => {
  reset();
  driversStep = (n) => (n === 0 ? "timeout" : "answer");
  await mount();
  assert(driverReads === 2, `precondition: the drivers read was asked twice (asked ${driverReads})`);
  assert(q('[data-testid="devices-load-error"]') == null,
    "one timed-out read replaced the screen with the first-load error");
  assert(q('[data-testid="devices-retrying"]') == null,
    "the retrying banner outlived the answer");
  assert(q('[data-testid^="device-row-"]') != null,
    "no device rows after the retry answered");
});

await test("UX12 while the drivers read waits to ask again the screen says so, with no error", async () => {
  reset();
  driversStep = (n) => (n === 0 ? "timeout" : "answer");
  const release = held();
  try {
    await mount();
    const banner = q('[data-testid="devices-retrying"]');
    assert(banner != null, "no retrying banner while the drivers read waits to ask again");
    assert(/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(banner.textContent),
      `the banner reads "${banner.textContent}"`);
    assert(q('[data-testid="devices-load-error"]') == null, "the error shows while the app is still asking");
    release();
    await settle();
    assert(q('[data-testid="devices-retrying"]') == null, "the banner outlived the answer");
  } finally {
    setRetrySleepForTests(async () => {});
  }
});

await test("UX4 a drivers read that settled as failed is asked once more when the tab comes back", async () => {
  reset();
  driversStep = () => "timeout";
  await mount();
  assert(driverReads === 4, `precondition: four tries (asked ${driverReads})`);
  assert(q('[data-testid="devices-load-error"]') != null, "precondition: the first-load error shows");
  driversStep = () => "answer";
  driverReads = 0;
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await settle();
  assert(driverReads === 1, `the tab coming back asked the drivers ${driverReads} times, not once`);
  assert(q('[data-testid^="device-row-"]') != null, "no device rows after the re-ask answered");
});

await test("UX10 a profiles read that times out once and then answers names the active profile", async () => {
  reset();
  seed("p1");
  profilesStep = (n) => (n === 0 ? "timeout" : "answer");
  profilesRows = () => [profile("Backyard")];
  await mount();
  assert(profileReads === 2, `precondition: the profiles read was asked twice (asked ${profileReads})`);
  const value = q('[data-testid="profile-value"]');
  assert(/^Backyard/.test(value.textContent), `the PROFILE row reads "${value.textContent}"`);
});

await test("PG a mount read still waiting to ask again never overwrites a newer reload's rows", async () => {
  reset();
  seed("p1");
  // Read 0 (the mount's) times out and waits; read 1 (the save's reload)
  // answers the fresh rows; a read 2 would be the stale mount read asking again.
  profilesStep = (n) => (n === 0 ? "timeout" : "answer");
  profilesRows = (n) => (n === 1 ? [profile("Backyard")] : [profile("Stale")]);
  const release = held();
  try {
    await mount();
    assert(profileReads === 1, `precondition: the mount's read is waiting (asked ${profileReads})`);
    click(q('[data-testid="profile-row"]'));
    const input = qd('[data-testid="profile-save-name"]');
    assert(input != null, "precondition: the popover's save field is on the page");
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    act(() => {
      setter.call(input, "Backyard");
      input.dispatchEvent(new win.Event("input", { bubbles: true }));
    });
    click(qd('[data-testid="profile-save"]'));
    await settle();
    assert(captures === 1, `precondition: the save posted once (posted ${captures})`);
    assert(profileReads === 2, `precondition: the save's reload asked once (asked ${profileReads})`);
    release();
    await settle();
    assert(profileReads === 2,
      `the superseded mount read asked again after the reload landed (asked ${profileReads})`);
    const value = q('[data-testid="profile-value"]');
    assert(/^Backyard/.test(value.textContent), `the PROFILE row reads "${value.textContent}"`);
  } finally {
    setRetrySleepForTests(async () => {});
  }
});

await act(async () => { root.unmount(); });
setRetrySleepForTests(null);

console.log(`p6DevicesRetryATimeout.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
