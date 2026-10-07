// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15RotatorCardRetest.test.tsx - Test rotator after a FAILED follow test, on
// the legacy equipment card (`components/equipment/RotatorCard.tsx`, which
// still serves `#/classic`), MOUNTED (WP-114; #697, #698, #709).
//
//   Run directly:  npx tsx src/components/__tests__/w15RotatorCardRetest.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The next-UI panel has its own test (`next/hubs/rig/rotator/__tests__/
// w15RotatorRetest.test.tsx`); this card carries its own copy of the same
// copy, so each side pins its own sentences and a change to one that is not
// made to the other fails here.
//
//   A FAILED TEST LEAVES THE BUTTON LIVE, AND A PRESS POSTS THE PREFLIGHT
//   ROUTE (#697): the server runs a failed follow test again, keeping the
//   sign, and before this the card locked itself with "measures again after
//   the rig reconnects".
//   A PASS STILL DISABLES IT, WITH A REASON THAT SAYS WHEN IT LIFTS (#709: the
//   follow test has an observing night; the status block publishes null for a
//   verdict from another night, which is a live button).
//   THE WORDS CARRY THE NEXT STEP: the failure line names the button and does
//   not say the rig must reconnect; the note says a failed test is run again.
//   THE SERVER'S CODED REFUSAL REACHES THE OPERATOR (#698): a 409
//   `sequence_running` from ROTATE TO PA / SYNC TO SKY / TEST ROTATOR shows its
//   sentence on the card.
//
// MUTANTS RUN (each from a byte backup of RotatorCard.tsx, restored
// byte-identically, sha256 compared, the mutant text grepped out):
//
//   C1 "a failed test still disables the button" (`&& rot.trusted === true;`
//   made `&& (rot.trusted === true || rot.trusted === false);`)
//   C2 "the old failure line" (`is off. Press Test rotator to measure it
//   again` restored to `is off until the rig reconnects`)
//   C3 "the old note" (the retest sentence removed from PREFLIGHT_NOTE)
//   C4 "the old lock reason" (the tonight / next observing night wording
//   restored to `; it measures again after the rig reconnects`)
//
// The failing assertion each produced is recorded in the return of WP-114.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/classic/equipment", pretendToBeVisual: true },
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

interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });

/** The server's `_lane_409` answer while a run is live: the NESTED shape. */
const SEQUENCE_SENTENCE =
  "a sequence is running; rotate to PA refused, because it would turn the "
  + "camera under the run's frames. Stop the run first";
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
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const RotatorCard = (await import("../equipment/RotatorCard")).default;

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
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: false, reverse: false,
    ...over,
  };
}

function seed(rot: Record<string, unknown> = rotator()): void {
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
      status: { connected: {}, backend_links: [], busy_lanes: [], rotator: rot },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorCard as any)); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const button = () => q("[data-rotator-preflight]");
const line = (): string => (q("[data-rotator-preflight-line]")?.textContent ?? "") as string;
const commands = () => asked.filter((a) => a.method !== "GET");
const buttonNamed = (name: string) => (Array.from(container.querySelectorAll("button")) as any[])
  .find((b) => (b.textContent || "").trim() === name);

// ======================================== a FAILED follow test is retestable
await testAsync("a failed follow test leaves Test rotator live, and a press posts the preflight route", async () => {
  seed(rotator({ sky_sign: -1, trusted: false }));
  mount();
  assert(button().disabled === false,
    `Test rotator is disabled after a failed follow test (title ${JSON.stringify(button().getAttribute("title"))}): `
    + "the only way back is then a whole-rig reconnect");
  asked.length = 0;
  click(button());
  await settle();
  const posts = commands();
  eq(posts.length, 1, "the press sent more or fewer than one command");
  assert(posts[0].method === "POST" && posts[0].url.endsWith("/api/rotator/preflight"),
    `posted ${posts[0].method} ${posts[0].url} instead of POST /api/rotator/preflight`);
});

test("a failed follow test with the sign never measured is live too", () => {
  seed(rotator({ sky_sign: null, trusted: false }));
  mount();
  assert(button().disabled === false, "Test rotator is disabled for an untrusted rotator");
});

test("the failure line names the button and does not say the rig must reconnect", () => {
  seed(rotator({ sky_sign: -1, trusted: false }));
  mount();
  assert(/^FAILED:/.test(line()), `the line does not lead with FAILED (got "${line()}")`);
  assert(/rotation is off/.test(line()), `the line does not say what the failure costs (got "${line()}")`);
  assert(/Press Test rotator to measure it again/.test(line()),
    `the line does not name the button that clears it (got "${line()}")`);
  assert(!/reconnect/i.test(line()),
    `the line still sends the operator to reconnect the rig (got "${line()}")`);
});

test("the note says a failed test is run again on its own, with the sign kept", () => {
  seed(rotator({ sky_sign: -1, trusted: false }));
  mount();
  const text = (container.textContent || "") as string;
  assert(/FAILED follow test/.test(text), "the card does not mention a failed test");
  assert(/again on its own/.test(text), "the card does not say it is run again");
  assert(/sign kept/.test(text), "the card does not say the sign is kept");
  assert(/about 22 degrees/.test(text) && /four plate solves/.test(text),
    "the first press's cost is gone from the card");
});

// ============================================== a PASS locks, a new night lifts
test("a passed test still disables the button, and the reason says when it lifts", () => {
  seed(rotator({ sky_sign: 1, trusted: true }));
  mount();
  assert(button().disabled === true, "Test rotator is live when there is nothing left to measure");
  const title = button().getAttribute("title") || "";
  assert(/already measured the rotator tonight/.test(title),
    `the lock does not say it is tonight's: ${JSON.stringify(title)}`);
  assert(/next observing night/.test(title) && /reconnects/.test(title),
    `the lock does not say what lifts it: ${JSON.stringify(title)}`);
});

test("the lock lifts when the night turns over: sign measured, verdict null", () => {
  seed(rotator({ sky_sign: 1, trusted: null }));
  mount();
  assert(button().disabled === false, "the button is still disabled on a night that has no verdict");
  eq(line(), "sign +1 measured, follow test not run", "the line does not say the test is owed");
});

// ============================================== the coded refusal is shown
for (const [name, route] of [
  ["Rotate to PA (plate solve)", "/api/rotator/rotate-to-pa"],
  ["Sync to sky (no movement)", "/api/rotator/sync-to-sky"],
  ["Test rotator", "/api/rotator/preflight"],
] as const) {
  await testAsync(`${route} refused with sequence_running shows the sentence`, async () => {
    runIsLive = true;
    try {
      seed();
      mount();
      asked.length = 0;
      const b = buttonNamed(name);
      assert(b != null, `no "${name}" button on the card`);
      click(b);
      await settle();
      eq(commands().length, 1, "the press did not reach the route");
      assert(commands()[0].url.endsWith(route), `posted ${commands()[0].url}`);
      const text = (container.textContent || "") as string;
      assert(/a sequence is running/.test(text) && /Stop the run first/.test(text),
        "the refusal's sentence is not shown on the card");
      assert(!/blocked_by|sequence_running/.test(text), "the raw object is shown");
    } finally {
      runIsLive = false;
    }
  });
}

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w15RotatorCardRetest.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
