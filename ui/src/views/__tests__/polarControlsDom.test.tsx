// polarControlsDom.test.tsx — the align screen against a session that lies by omission.
//
//   Run directly:  npx tsx src/views/__tests__/polarControlsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A DOM TEST. The three findings here are all about a WINDOW: the second
// between "started" and the first published frame, the moment a session ends
// without having aligned anything, and the moment one button's request is in
// flight while another must stay reachable. Each needs the view mounted and then
// driven — by replacing the `polar` slice, which is exactly what the driver's
// 2 s publish does.
//
// This is the screen where the user is crouched at the mount with a hex key and
// both hands committed, which is why "the number is stale for a second" and "the
// stop button is grey for a reason that is not about stopping" are not cosmetic.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// `hold` is the point of the Start tests: /api/polar/start returns the instant
// asyncio.create_task hands back a task, so a slow round trip and a fast one both
// leave the session's first publish in the future. `resolveHold` lets a test
// decide when the promise settles, independently of when the driver speaks.
const posts: string[] = [];
let hold: null | (() => void) = null;
// The STOP route's two interesting shapes, for the latch tests at the foot of
// this file: a request that has not answered yet (`stopHold`), and one the
// server refuses (`stopFails` — 409 "polar alignment is not running", or the
// same thing arriving as a dropped relay). Both are states in which the run is
// still live, so both must leave the screen saying so.
let stopHold: null | (() => void) = null;
let stopFails = false;
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  if ((init?.method ?? "GET") === "POST") posts.push(path);
  if (hold && path.includes("/api/polar/pause")) {
    await new Promise<void>((r) => { hold = r; });
  }
  if (path.includes("/api/polar/stop")) {
    if (stopHold) await new Promise<void>((r) => { stopHold = r; });
    if (stopFails) {
      return {
        ok: false, status: 409, statusText: "Conflict",
        json: async () => ({ detail: "polar alignment is not running" }),
      } as any;
    }
  }
  return {
    ok: true, status: 200, statusText: "OK",
    json: async () => ({ started: true, source: "native" }),
  } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const PolarView = (await import("../PolarView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** One `polar` publish, exactly as the driver emits it. */
function polar(state: string, extra: Record<string, unknown> = {}): void {
  useStore.setState({
    polar: {
      state, az_error: 0, alt_error: 0, total_error: 0, progress: 0,
      message: "", source: "native", ...extra,
    },
  } as never);
}

useStore.setState({
  status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
  principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
} as never);
polar("idle");

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => { root.render(createElement(PolarView)); });

async function settle(ms = 0): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
async function publish(...args: Parameters<typeof polar>): Promise<void> {
  await act(async () => { polar(...args); });
}
const buttons = (): any[] => [...container.querySelectorAll("button")];
const byText = (re: RegExp): any =>
  buttons().find((b: any) => re.test((b.textContent || "").trim()));
const text = (): string => container.textContent || "";
const click = async (node: any): Promise<void> => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};
/** A control is out of service either way it is expressed — `disabled` for a
 *  hard lock, `aria-disabled` for the honest kind that still explains itself. */
const inert = (b: any): boolean => !!b && (b.disabled === true || b.getAttribute("aria-disabled") === "true");

// ------------------------------------------------------------- the fixture
test("the align screen mounted, idle, with mount control", () => {
  assert(byText(/Start Alignment/) != null, "no Start button — this is not the align view");
  assert(byText(/^Stop$/) != null, "no Stop button");
  assert(/Not started/.test(text()), "the idle readout is missing — the fixture is wrong");
});

// ----------------------------------------------------- START (audit #60)
test("Stop says why it is refusing, instead of being an unexplained grey box", () => {
  const stop = byText(/^Stop$/);
  assert(inert(stop), "Stop is live with nothing running");
  assert(stop.disabled !== true,
    "Stop uses the native disabled attribute, which takes its reason out of the " +
    "accessibility tree and leaves a rectangle that cannot even be focused to ask why");
});

await click(byText(/Start Alignment/));
await settle();
test("Start commits, and the panel stops claiming the run has not started", () => {
  assert(posts.some((p) => p.includes("/api/polar/start")), "the press never reached the start route");
  assert(!/Not started/.test(text()),
    "the panel still reads 'Not started' after a start that has already committed the mount — " +
    "the POST returns when the driver TASK exists, a beat before its first publish");
  assert(/Starting/.test(text()), "nothing on screen says the alignment is starting");
});

test("…and Start cannot be pressed a second time into a 409", () => {
  const b = byText(/Starting|Start Alignment/);
  assert(b != null && b.disabled === true,
    "Start went live again inside the publish gap, and the second press it invites " +
    "is refused with 'polar alignment is already running'");
});

test("Stop is armed the moment the run is committed, not when the driver speaks", () => {
  const stop = byText(/^Stop$/);
  assert(!inert(stop),
    "Stop is still refusing during the window in which the mount is already moving");
});

await publish("running", { total_error: 25.3, az_error: 20, alt_error: 15 });
test("the driver's first frame takes over from the local latch", () => {
  assert(!/Starting/.test(text()), "the view is still saying 'starting' after the driver reported running");
  assert(byText(/Start Alignment/).disabled === true, "Start went live during a running alignment");
});

// -------------------------------------------------- STOP vs BUSY (audit #61)
hold = () => {};   // arm the pause route to hang
{
  const pause = byText(/^Pause$/);
  assert(pause != null, "no Pause button on a running alignment");
  await act(async () => {
    pause.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
test("Pause's in-flight request does not disable the red Stop", () => {
  // PRECONDITION: the pause really is still in flight. Without it this passes
  // on a build where the request finished before the assertion ran.
  assert(typeof hold === "function" && posts.some((p) => p.includes("/api/polar/pause")),
    "the pause request is not actually in flight, so nothing is being proven");
  assert(byText(/^Pause$/) != null && byText(/^Pause$/).disabled === true,
    "the in-flight guard is not engaged at all — this test would prove nothing");
  const stop = byText(/^Stop$/);
  assert(!inert(stop),
    "the emergency stop went out of service because a DIFFERENT button's request is " +
    "in flight, while the mount is mid-slew");
});

{
  const before = posts.length;
  await click(byText(/^Stop$/));
  test("…and a Stop pressed during that window actually reaches the rig", () => {
    assert(posts.slice(before).some((p) => p.includes("/api/polar/stop")),
      "Stop looked pressable and did nothing — removing the grey without removing the " +
      "guard is the worse half of the bug");
  });
}
if (typeof hold === "function") (hold as () => void)();
hold = null;
await settle(10);

// ------------------------------------------------- THE END OF A RUN (audit #10)
await publish("done", { total_error: 25.3, az_error: 20, alt_error: 15 });
test("a session that merely ENDED does not claim the mount is aligned", () => {
  assert(!/aligned to 25.3/.test(text()),
    "the screen prints a green '✓ aligned to 25.3′' — the native engine publishes " +
    "state=done when its 240-update cap expires and NINA publishes it on any clean " +
    "socket close, so 'done' means the session stopped, not that it worked");
  assert(/Keep going/.test(text()),
    "the verdict for a 25.3′ error is missing, so the contradiction under test is absent");
  assert(/Session ended at 25.3/.test(text()),
    "nothing says how the run ended, which leaves the user with a blank where a decision goes");
});

await publish("done", { total_error: 1.4, az_error: 1, alt_error: 1 });
test("…but a genuinely aligned run still gets its tick", () => {
  assert(/✓ aligned to 1.4′/.test(text()),
    "the success line is gone for a run that really did converge — a gate that never " +
    "passes is not a fix");
});

await publish("done", { total_error: 0 });
test("a run that ended before any measurement says exactly that", () => {
  assert(!/aligned to 0/.test(text()),
    "a session with no fitted error printed '✓ aligned to 0.0′', which is the most " +
    "confident possible statement of a number nobody measured");
  assert(/before any error was measured/.test(text()),
    "nothing distinguishes 'no measurement' from a measurement of zero");
});

// ------------------------------- STOP INSIDE THE START LATCH (button-rest #11)
// Press Start, then press Stop in the ~1 s before the driver's first publish.
// Stop's handler used to call setStarting(false) SYNCHRONOUSLY — before
// /api/polar/stop had been sent, let alone answered — so on that single frame
// the screen un-committed itself over a mount that was still moving: Start came
// back to life, the header chip dropped to a grey "idle", the readout reverted
// to "Not started", and the red Stop dimmed to its locked face whose stated
// reason is "No alignment is running — nothing to stop."
await publish("idle");
{
  const before = posts.length;
  await click(byText(/Start Alignment/));
  await settle();
  test("precondition: the start latch is on and the screen reads the run as live", () => {
    assert(posts.slice(before).some((p) => p.includes("/api/polar/start")),
      "the press never reached the start route, so there is no latch to release");
    assert(/Starting/.test(text()), "nothing says the alignment is starting");
    assert(byText(/Starting|Start Alignment/).disabled === true, "Start is not locked out");
    assert(!inert(byText(/^Stop$/)), "Stop is not armed, so pressing it proves nothing");
  });
}

stopHold = () => {};   // the stop request hangs until this test releases it
{
  const before = posts.length;
  await click(byText(/^Stop$/));
  test("Stop does not un-commit the screen before the rig has answered", () => {
    // PRECONDITION: the stop really is still in flight. Without it this passes
    // on a build where the request finished before the assertion ran.
    assert(typeof stopHold === "function" && posts.slice(before).some((p) => p.includes("/api/polar/stop")),
      "the stop request is not actually in flight, so nothing is being proven");
    const start = byText(/Starting|Start Alignment/);
    assert(start != null && start.disabled === true,
      "Start went live again on the PRESS of Stop — before the stop was sent. The press " +
      "it now invites is answered 409 'polar alignment is already running', which is the " +
      "exact failure the starting latch exists to remove");
    assert(!inert(byText(/^Stop$/)),
      "the red Stop dimmed to 'No alignment is running — nothing to stop.' while its own " +
      "request was still in flight and the mount was still moving");
    assert(!/Not started/.test(text()),
      "the readout went back to 'Not started — press Start Alignment to measure.' over an " +
      "alignment the rig has not been told to stop yet");
  });
}
if (typeof stopHold === "function") (stopHold as () => void)();
stopHold = null;
await settle(10);

test("…and the stop that LANDED is what releases the latch", () => {
  const start = byText(/Start Alignment/);
  assert(start != null && start.disabled === false,
    "Start is still locked out after a stop the server accepted — the latch must hand " +
    "over on the resolved stop, not wait out its 6s expiry");
});

// A REFUSED STOP IS NOT A STOP. 409, 403 or a dropped relay all mean the run is
// still going; the escape hatch has to stay armed so the next press re-sends.
stopFails = true;
await click(byText(/Start Alignment/));
await settle();
{
  const armed = /Starting/.test(text());
  await click(byText(/^Stop$/));
  await settle(10);
  const before = posts.length;
  await click(byText(/^Stop$/));
  await settle(10);
  test("a stop the server refuses leaves the alignment on screen — and re-pressable", () => {
    assert(armed, "the start latch was never on, so the refusal below proves nothing");
    const start = byText(/Starting|Start Alignment/);
    assert(start != null && start.disabled === true,
      "a REFUSED stop re-enabled Start over a run that is still going");
    assert(posts.slice(before).some((p) => p.includes("/api/polar/stop")),
      "the second Stop press never reached the rig: the button had already dimmed to its " +
      "locked face, and HonestButton keeps that face pressable, so the retry only toasted " +
      "'No alignment is running — nothing to stop.' at someone whose alignment is running");
  });
}
stopFails = false;

// -------------------------------------------- caveats the panel used to drop
// Both of these were already on the wire and rendered nowhere, which is worse
// than absent: the panel looked confident about a reading it had reason to
// doubt, while someone was crouched at the mount acting on it.

stopFails = false;
await publish("running", {
  phase: "adjusting", total_error: 12.0, az_error: 9, alt_error: 8,
  flags: ["initial_error_large"],
});
test("a large starting error is stated, not silently tolerated", () => {
  const node = container.querySelector("[data-polar-initial-large]");
  assert(node != null,
    "the engine raised initial_error_large and the panel said nothing — the live " +
    "number re-scales approximately here, so the bolts do not move it by what it says");
  assert(/re-run/i.test(node.textContent || ""),
    "the caveat names no way out; 'this is approximate' without 're-run once it is " +
    "smaller' leaves the operator turning bolts against a number they now distrust");
});

await publish("running", {
  phase: "adjusting", total_error: 12.0, az_error: 9, alt_error: 8,
  stale_updates: 4,
});
test("a frozen live number says it is frozen, while it is frozen", () => {
  const node = container.querySelector("[data-polar-stale]");
  assert(node != null,
    "four failed live updates and the panel still presented the old number as current — " +
    "this used to reach only the log, and only when the session ended");
  assert(/4/.test(node.textContent || ""),
    "the warning does not say HOW far behind; 'not updating' and 'not updating for " +
    "30 seconds' are different situations at the mount");
});

await publish("running", {
  phase: "adjusting", total_error: 11.0, az_error: 8, alt_error: 7,
  stale_updates: 0,
});
test("…and it clears when the solves come back", () => {
  assert(container.querySelector("[data-polar-stale]") == null,
    "the staleness warning outlived the staleness — a caveat that never clears is one " +
    "the operator learns to read past");
});

await publish("done", {phase:"adjusting",total_error:0,az_error:0,alt_error:0,reading_ts:Date.now()/1000});
test("a measured zero is a valid result, not an absent measurement",()=>{
  assert(container.textContent.includes("aligned to 0.0"),"measured zero lost its result");
  assert(!container.textContent.includes("nothing was aligned"),"zero described as unmeasured");
});
// ------------------------------------------------------------------- report
const {useExperience}=await import("../../guided/experience");
win.HTMLCanvasElement.prototype.getContext=()=>null;
await act(async()=>{
  useStore.setState({polar:{state:"idle",az_error:0,alt_error:0,total_error:0,reading_ts:0}} as never);
  useExperience.setState({mode:"guided",home:false,wizard:"alignment"});
  root.render(createElement(PolarView,{key:"guided-lesson"}));
});
test("Guided alignment lesson has no idle instrument panel or premature final check",()=>{
  assert(!!container.querySelector("canvas"),"3D example is missing");
  assert(!container.querySelector('[aria-label^="Solve frame settings"]'),"camera settings leaked into lesson");
  assert(!container.textContent.includes("Check the final alignment"),"final check leaked into lesson");
  assert(!container.textContent.includes("Start Alignment"),"live controls leaked into lesson");
});
const openLive=[...container.querySelectorAll("button")].find((b:any)=>b.textContent.includes("Skip lesson")) as HTMLButtonElement;
await act(async()=>openLive.click());
test("the explicit lesson exit opens live alignment controls",()=>{
  assert(!container.querySelector("canvas"),"lesson remains above the controls");
  assert(!!container.querySelector('[aria-label^="Solve frame settings"]'),"live camera dial is missing");
  assert(container.textContent.includes("Start Alignment"),"live start is missing");
});
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`polarControlsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
