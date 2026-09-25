// runArmedRecoveringDom.test.tsx - the #/next Monitor's RUN ARMED card while
// ResumeArm's recovery ladder runs (#246).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/__tests__/runArmedRecoveringDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a restart the ladder blind-solves and re-centres the mount for minutes
// before it starts the armed session, with the engine idle. This card said
// "It starts by itself when its window opens" all that time, over a rig that
// was slewing by itself - and after a night that ended on a safety stop it was
// not on screen at all, because it only rendered from `idle`.
//
// NAMED MUTANTS. Each was run from a byte copy of RecoveryCards.tsx, and the
// file was restored byte-identical afterwards (sha256 checked). The observed
// failure is quoted at the test it turns red.
//   R1 "RunArmedCard ignores recovering"   `recovering` is never computed
//   R2 "keep the idle gate while recovering" the state gate ignores the ladder
//   M3 "drop the live-run guard"           api/sessions.ts resumeRecoveryLine
//   S8 "no stop on the #/next RUN ARMED card" the recovering action list loses STOP
//   S9 "the stop is not locked for a viewer" `lockedFor` always answers null
//   S7 "the stop re-arms"                  stopResumeRecovery.ts sends auto_resume: true

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.fetch = () => Promise.reject(new Error("offline in this test"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { RunArmedCard } = await import("../live/RecoveryCards");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

// ------------------------------------------------------------------ fixtures
const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const LADDER = { step: "limits", session_id: "sess-1", session_name: "M31 LRGB" };
const LINE = "Auto-resume is re-centring the mount for M31 LRGB (limits)";
const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};

function arm(over: Record<string, unknown> = {}): any {
  return { armed: ARMED, hold: null, recovering: false, recovery: null, ...over };
}

function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
set({ sequence: { state: "idle" }, resumeArm: arm(), armedBannerDismissed: null });
act(() => { root.render(createElement(RunArmedCard)); });

const card = () => container.querySelector('[data-testid="run-armed"]') as any;
function row(key: string): string | null {
  const c = card();
  if (!c) return null;
  for (const r of c.querySelectorAll(".nx-incident-row")) {
    if (r.querySelector(".nx-incident-key")?.textContent === key) {
      return r.querySelector(".nx-incident-val")?.textContent ?? "";
    }
  }
  return null;
}
const actions = (): string[] =>
  card() ? [...card().querySelectorAll("button[data-action]")].map((b: any) => b.textContent) : [];

// ==================================================================== tests

test("control: armed and waiting, the card says it starts by itself and can be dismissed", () => {
  assert(card() != null, "precondition: no RUN ARMED card for an armed session - the fixture is wrong");
  eq(row("NEXT"), "It starts by itself when its window opens.", "the waiting card's NEXT changed:");
  assert(actions().includes("DISMISS"), `the waiting card lost DISMISS: ${actions().join(", ")}`);
  assert(!actions().includes("STOP AUTO-RESUME"),
    `a stop for a ladder that is not running: ${actions().join(", ")}`);
});

test("while the ladder runs, NEXT is the server's sentence and not the stale hold", () => {
  // R1 "RunArmedCard ignores recovering", observed (3/7 passed; the next three
  // tests failed with it):
  //   x while the ladder runs, NEXT is the server's sentence and not the stale
  //   hold: NEXT is not the recovery sentence:
  //     expected Auto-resume is re-centring the mount for M31 LRGB (limits)
  //     got      Holding: M31 is below its start floor; not slewing yet. It
  //              starts by itself when that clears.
  set({ resumeArm: arm({ hold: STALE_HOLD, recovering: true, recovery: LADDER }) });
  assert(card() != null, "the card went away while the ladder runs");
  eq(row("NEXT"), LINE, "NEXT is not the recovery sentence:");
  assert(!/since \d/.test(card().textContent), "the card dates itself off the previous attempt's hold");
});

test("while the ladder runs there is no DISMISS, because the card ignores one then", () => {
  // R1, observed:
  //   x while the ladder runs there is no DISMISS, because the card ignores
  //   one then: a DISMISS that changes nothing on screen: OPEN SESSION, DISMISS
  const labels = actions();
  assert(labels.includes("OPEN SESSION"), `OPEN SESSION is gone: ${labels.join(", ")}`);
  assert(!labels.includes("DISMISS"), `a DISMISS that changes nothing on screen: ${labels.join(", ")}`);
});

// ---------------------------------------------------------------- the stop
//
// The card says the mount is moving on its own; with the engine idle no run
// control is on screen. So it carries the session's own disarm, which stops
// the ladder recovering it (H2), for the session the ladder names.

const stopButton = () =>
  card()?.querySelector('button[data-action="stop"]') as any;

test("while the ladder runs the card offers STOP AUTO-RESUME, locked for a viewer", () => {
  // S8 "no stop on the #/next RUN ARMED card", observed:
  //   (9/11 passed; the operator's press failed as "PATCH requests sent
  //   ([]):")
  //   x while the ladder runs the card offers STOP AUTO-RESUME, locked for a
  //   viewer: no STOP AUTO-RESUME while the ladder moves the mount: OPEN
  //   SESSION
  // S9 "the stop is not locked for a viewer", observed:
  //   (9/11 passed)
  //   x while the ladder runs the card offers STOP AUTO-RESUME, locked for a
  //   viewer: a viewer's stop is not locked:
  //   x a viewer's press sends nothing: a viewer's press sent the disarm
  set({ principal: { role: "viewer", email: null, caps: ["view.status"] } });
  const stop = stopButton();
  assert(stop != null, `no STOP AUTO-RESUME while the ladder moves the mount: ${actions().join(", ")}`);
  eq(stop.textContent, "STOP AUTO-RESUME", "the stop's label:");
  eq(stop.getAttribute("data-locked"), "true", "a viewer's stop is not locked:");
  assert(/stopping auto-resume needs .*access/.test(stop.getAttribute("title") ?? ""),
    `the lock does not say what it needs: ${stop.getAttribute("title")}`);
});

await (async () => {
  const asked: { method: string; url: string; body: string | undefined }[] = [];
  const offline = g.fetch;
  g.fetch = async (url: any, init?: { method?: string; body?: string }) => {
    const u = String(url);
    const method = init?.method ?? "GET";
    asked.push({ method, url: u, body: init?.body });
    const data = u.endsWith("/api/sequence/resume-arm")
      ? { armed: null, hold: null, recovering: false, recovery: null }
      : { id: "sess-1", status: "dormant", auto_resume: false, remaining: {} };
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" }, json: async () => data, text: async () => "",
    };
  };
  const settle = async () => {
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  };
  try {
    // A viewer's press explains and sends nothing.
    await act(async () => { stopButton()?.click(); });
    await settle();
    test("a viewer's press sends nothing", () => {
      eq(asked.filter((a) => a.method === "PATCH").length, 0, "a viewer's press sent the disarm");
    });
    set({ principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] } });
    test("an operator's stop is not locked", () => {
      eq(stopButton()?.getAttribute("data-locked") ?? null, null, "an operator's stop is locked:");
    });
    await act(async () => { stopButton()?.click(); });
    await settle();
    test("an operator's STOP AUTO-RESUME disarms the session the ladder names, and the card comes off", () => {
      // S7 "the stop re-arms", observed:
      //   (10/11 passed)
      //   x an operator's STOP AUTO-RESUME disarms the session the ladder names,
      //   and the card comes off: the disarm's body:
      //     expected {"auto_resume":false}
      //     got      {"auto_resume":true}
      const patches = asked.filter((a) => a.method === "PATCH");
      eq(patches.length, 1, `PATCH requests sent (${JSON.stringify(asked)}):`);
      assert(patches[0].url.endsWith("/api/sessions/sess-1"),
        `the disarm went to ${patches[0].url}, not the session the ladder is recovering`);
      eq(patches[0].body, JSON.stringify({ auto_resume: false }), "the disarm's body:");
      eq(card(), null, "the RUN ARMED card outlived the disarm of its session");
    });
  } finally {
    g.fetch = offline;
    set({ principal: null, resumeArm: arm({ hold: STALE_HOLD, recovering: true, recovery: LADDER }) });
  }
})();

test("a dismissed armed card comes back while the ladder moves the rig", () => {
  // R1, observed:
  //   x a dismissed armed card comes back while the ladder moves the rig: the
  //   dismissal hid the recovery
  //     expected Auto-resume is re-centring the mount for M31 LRGB (limits)
  //     got      null
  set({ armedBannerDismissed: "sess-1" });
  eq(row("NEXT"), LINE, "the dismissal hid the recovery");
});

test("after a night that ended on a safety stop, the card is up while the ladder runs", () => {
  // R2 "keep the idle gate while recovering", observed (6/7 passed; R1 fails
  // here too, with the same line):
  //   x after a night that ended on a safety stop, the card is up while the
  //   ladder runs: the card is gated on idle and a terminal state hid it
  set({ armedBannerDismissed: null, sequence: { state: "aborted", end_reason: "unsafe" } });
  assert(card() != null, "the card is gated on idle and a terminal state hid it");
  eq(row("NEXT"), LINE, "the terminal-state card is not the recovery sentence:");
});

test("control: the same terminal state with no ladder shows no RUN ARMED card, as before", () => {
  set({ resumeArm: arm() });
  eq(card(), null, "a RUN ARMED card over a finished run with nothing recovering");
});

test("control: a live run beside a stale recovering poll shows no RUN ARMED card", () => {
  // M3 "drop the live-run guard", observed (6/7 passed):
  //   x control: a live run beside a stale recovering poll shows no RUN ARMED
  //   card: a re-centring card over a run that is going
  set({ sequence: { state: "running" }, resumeArm: arm({ recovering: true, recovery: LADDER }) });
  eq(card(), null, "a re-centring card over a run that is going");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`runArmedRecoveringDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
