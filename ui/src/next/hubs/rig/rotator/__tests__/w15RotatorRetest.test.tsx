// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15RotatorRetest.test.tsx - TEST ROTATOR after a FAILED follow test, on the
// ROTATOR sheet, MOUNTED (WP-114; #697, #698, #709).
//
//   Run directly:  npx tsx src/next/hubs/rig/rotator/__tests__/w15RotatorRetest.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// WP-88's button locked itself once the sign was measured AND the follow test
// had an answer, FAILED included, with "measures again after the rig
// reconnects". After the owner re-seated the CAA coupling (#594) the only way
// to try again was a whole-rig reconnect (#697). The server now runs a FAILED
// follow test again when the operator presses the button (the preflight route
// passes `retest_failed`), keeping the sign, and the follow test has an
// observing night (#709), so a verdict from another night reads as null.
//
// WHAT IS WORTH ASSERTING:
//
//   A FAILED TEST LEAVES THE BUTTON LIVE, AND A PRESS POSTS THE PREFLIGHT
//   ROUTE. That is the whole fix: the one state in which a press has
//   something to measure must not be the state in which the button is locked.
//   It holds with the sign unmeasured too (a self-test the engine ran first).
//
//   A PASS STILL LOCKS IT, WITH A REASON THAT SAYS WHEN IT LIFTS: tonight, the
//   next observing night, or a reconnect. Not "after the rig reconnects"
//   alone: that would be a claim about the rig the server no longer keeps.
//
//   THE LOCK LIFTS WHEN THE NIGHT TURNS OVER. The status block publishes null
//   for a verdict from another night, so sign measured and `trusted: null` is
//   a live button, as before.
//
//   THE WORDS CARRY THE NEXT STEP. The failure line names the button and does
//   not say the rig must reconnect; the note beside the button says a failed
//   test is run again on its own, with the sign kept.
//
//   THE SERVER'S CODED REFUSAL REACHES THE OPERATOR (#698): ROTATE TO PA, SYNC
//   TO SKY and TEST ROTATOR answer 409 `sequence_running` while a run is live
//   (the nested `{detail: {detail, code, lane, blocked_by}}` shape), and the
//   panel shows the sentence, not the raw object.
//
// MUTANTS RUN (each from a byte backup of RotatorPanel.tsx, restored
// byte-identically, sha256 compared, the mutant text grepped out):
//
//   W1 "a failed test still locks the button" (`&& rot.trusted === true;`
//   made `&& (rot.trusted === true || rot.trusted === false);`)
//   W2 "the old failure line" (`is off. Press TEST ROTATOR to measure it
//   again` restored to `is off until the rig reconnects`)
//   W3 "the old note" (the retest sentence removed from PREFLIGHT_NOTE)
//   W4 "the old lock reason" (`tonight; it measures again on the next
//   observing night or after the rig reconnects` restored to `; it measures
//   again after the rig reconnects`)
//
// The failing assertion each produced is recorded in the return of WP-114.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
  { url: "http://local/#/rig/devices/rotator", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent",
  "Image", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });

/** The server's `_lane_409` answer while a run is live: the NESTED shape the
 *  panels already parse, with the sentence at `detail.detail`. */
const SEQUENCE_SENTENCE =
  "a sequence is running; rotator preflight refused, because it turns the "
  + "rotator about 22 degrees and takes four plate solves, which would ruin "
  + "the run's frames. Stop the run first";
let runIsLive = false;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: String(url), body });
  if (method === "POST" && runIsLive && /\/api\/rotator\/(rotate-to-pa|sync-to-sky|preflight)$/
    .test(String(url))) {
    return {
      ok: false, status: 409, statusText: "Conflict",
      json: async () => ({
        detail: {
          detail: SEQUENCE_SENTENCE, code: "sequence_running",
          lane: "rotate_to_pa", blocked_by: "sequence",
        },
      }),
    };
  }
  if (method === "POST" && String(url).includes("/api/rotator/preflight")) {
    return ok({ started: "rotate_to_pa" });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { RotatorSheet } = await import("../../sheets/rotator");

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

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: true, reverse: false,
    ...over,
  };
}

function seed(statusOver: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      config: {
        active_profile_id: null,
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      status: {
        connected: {
          rotator: { name: "ZWO CAA", connected: true },
          camera: { name: "ZWO ASI", connected: true },
        },
        backend_links: [
          { role: "rotator", connected: true, error: null },
          { role: "camera", connected: true, error: null },
        ],
        busy_lanes: [],
        rotator: rotator(),
        ...statusOver,
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const id = (marker: string) => q(`[data-testid="${marker}"]`);
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const commands = () => asked.filter((a) => a.method !== "GET");
const line = (): string => (id("rotator-preflight-line")?.textContent ?? "") as string;

// ======================================== a FAILED follow test is retestable
await testAsync("a failed follow test leaves TEST ROTATOR live, and a press posts the preflight route", async () => {
  seed({ rotator: rotator({ sky_sign: -1, trusted: false }) });
  mount();
  const b = id("rotator-preflight");
  assert(!locked(b),
    `TEST ROTATOR is locked after a failed follow test (title ${JSON.stringify(b.getAttribute("title"))}): `
    + "the only way back is then a whole-rig reconnect");
  asked.length = 0;
  click(b);
  await settle();
  const posts = commands();
  eq(posts.length, 1, "the press sent more or fewer than one command");
  eq(posts[0].method, "POST", "wrong method");
  assert(posts[0].url.endsWith("/api/rotator/preflight"),
    `posted ${posts[0].url} instead of /api/rotator/preflight`);
});

test("a failed follow test with the sign never measured is live too", () => {
  // The engine's nightly self-test can run before anything learned the sign.
  seed({ rotator: rotator({ sky_sign: null, trusted: false }) });
  mount();
  assert(!locked(id("rotator-preflight")), "TEST ROTATOR is locked for an untrusted rotator");
});

test("the failure line names the button and does not say the rig must reconnect", () => {
  seed({ rotator: rotator({ sky_sign: -1, trusted: false }) });
  mount();
  assert(/^FAILED:/.test(line()), `the line does not lead with FAILED (got "${line()}")`);
  assert(/rotation is off/.test(line()), `the line does not say what the failure costs (got "${line()}")`);
  assert(/Press TEST ROTATOR to measure it again/.test(line()),
    `the line does not name the button that clears it (got "${line()}")`);
  assert(!/reconnect/i.test(line()),
    `the line still sends the operator to reconnect the rig (got "${line()}")`);
  assert(id("rotator-preflight-line").className.includes("nx-rot-err"),
    "a failed test is not drawn as an error");
});

test("the note says a failed test is run again on its own, with the sign kept", () => {
  seed({ rotator: rotator({ sky_sign: -1, trusted: false }) });
  mount();
  const note = (id("rotator-preflight-note")?.textContent ?? "") as string;
  assert(/FAILED follow test/.test(note), `the note does not mention a failed test: ${note}`);
  assert(/again on its own/.test(note), `the note does not say it is run again: ${note}`);
  assert(/sign kept/.test(note), `the note does not say the sign is kept: ${note}`);
  assert(/about 22 degrees/.test(note) && /four plate solves/.test(note),
    `the first press's cost is gone from the note: ${note}`);
});

// ============================================== a PASS locks, a new night lifts
test("a passed test still locks the button, and the reason says when it lifts", () => {
  seed({ rotator: rotator({ sky_sign: 1, trusted: true }) });
  mount();
  const b = id("rotator-preflight");
  assert(locked(b), "TEST ROTATOR is live when there is nothing left to measure");
  const title = b.getAttribute("title") || "";
  assert(/already measured the rotator tonight/.test(title),
    `the lock does not say it is tonight's: ${JSON.stringify(title)}`);
  assert(/next observing night/.test(title) && /reconnects/.test(title),
    `the lock does not say what lifts it: ${JSON.stringify(title)}`);
});

test("the lock lifts when the night turns over: sign measured, verdict null", () => {
  // The server reads a verdict from another night as None, so the status block
  // publishes `trusted: null` again and the first press of the night is live.
  seed({ rotator: rotator({ sky_sign: 1, trusted: null }) });
  mount();
  assert(!locked(id("rotator-preflight")),
    "the button is still locked on a night that has no verdict");
  eq(line(), "sign +1 measured, follow test not run", "the line does not say the test is owed");
});

// ============================================== the coded refusal is shown
for (const [testid, route] of [
  ["rotator-solve", "/api/rotator/rotate-to-pa"],
  ["rotator-sync", "/api/rotator/sync-to-sky"],
  ["rotator-preflight", "/api/rotator/preflight"],
] as const) {
  await testAsync(`${route} refused with sequence_running shows the sentence`, async () => {
    runIsLive = true;
    try {
      seed();
      mount();
      asked.length = 0;
      click(id(testid));
      await settle();
      eq(commands().length, 1, "the press did not reach the route");
      assert(commands()[0].url.endsWith(route), `posted ${commands()[0].url}`);
      const shown = (id("rotator-error")?.textContent ?? "") as string;
      assert(/a sequence is running/.test(shown) && /Stop the run first/.test(shown),
        `the refusal's sentence is not shown (got ${JSON.stringify(shown)})`);
      assert(!/\{|code|blocked_by/.test(shown), `the raw object is shown: ${shown}`);
    } finally {
      runIsLive = false;
    }
  });
}

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w15RotatorRetest.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
