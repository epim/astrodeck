// monitorPauseDom.test.tsx — the Monitor dashboard across a PAUSE.
//
//   Run directly:  npx tsx src/views/__tests__/monitorPauseDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT (UX-2026-08-05 #5). `POST /api/sequence/pause` sets
// state="paused" the instant it is called, but the engine only reads that flag
// at the top of the frame loop (`_checkpoint`) — so the sub already in flight
// runs to completion. On a 300s sub that is up to five minutes of OPEN SHUTTER
// underneath a badge reading PAUSED, which is the state someone uncaps the
// scope, walks out with a head-torch, or opens the observatory door in.
//
// The dashboard is driven entirely by store slices, so the whole pause can be
// replayed here exactly as the WS delivers it: three frames, in order —
//   1. running, frame 12 of 60, 30s into a 300s sub
//   2. paused, SAME frames_done (this is the pause POST's own snapshot)
//   3. paused, frames_done 13 (the interrupted sub is finally banked)
// Between (2) and (3) the screen must say PAUSING and must keep the sub-frame
// bar; only at (3) may it say PAUSED.

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
// Every network read this view does on mount is a cold-load convenience
// (/api/monitor/snapshot, /api/dome/state). Failing them is a supported path —
// "the WS catches up within ~2s" — and keeps this test about the pause.
win.fetch = () => Promise.reject(new Error("offline in this test"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MonitorView = (await import("../MonitorView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const EXPOSURE_S = 300;
const FRAME_START_MS = 1_700_000_000_000;

/** One WS `sequence` frame, as the engine emits it. `ageS` is how far into the
 *  current sub the server was when it emitted — the pause POST publishes a
 *  fresh snapshot, which is exactly why the client can know this at all. */
function seqFrame(state: "running" | "paused" | "aborting", framesDone: number, ageS: number) {
  return {
    state,
    plan_name: "Tonight",
    target: "NGC 7000",
    progress: {
      frames_done: framesDone,
      frames_total: 60,
      percent: Math.round((framesDone / 60) * 100),
      elapsed_s: 3600,
      rejected: 0,
      eta_s: 7200,
      eta_confident: true,
      current_exposure_s: EXPOSURE_S,
      frame_started_at_ms: FRAME_START_MS,
      server_now_ms: FRAME_START_MS + ageS * 1000,
    },
  };
}

function publish(state: "running" | "paused" | "aborting", framesDone: number, ageS: number): void {
  act(() => {
    useStore.setState({ sequence: seqFrame(state, framesDone, ageS) } as never);
  });
}

// An operator who CAN pause — otherwise the controls row renders locked and the
// pause path under test is unreachable.
act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] },
    wsConnected: true,
    sequence: seqFrame("running", 12, 30),
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(MonitorView)); });

const text = () => (container.textContent || "");
/** The in-flight exposure bar: the one element that says the shutter is open. */
const subFrameBar = () => container.querySelector(".progress-track.\\!h-2");

// --------------------------------------------------------------- the fixture
test("the dashboard mounted mid-run — running, and the sub-frame bar is up", () => {
  // The anti-blank-page guard AND the precondition for everything below: if the
  // bar were never there, "the bar is still there after the pause" would be a
  // vacuous truth, and so would "it is gone once the frame lands".
  assert(/RUNNING/.test(text()), `no RUNNING badge on a running dashboard: ${text().slice(0, 120)}`);
  assert(subFrameBar() != null,
    "no sub-frame bar while running 30s into a 300s sub — the fixture is wrong, " +
    "not the component, and every assertion about that bar below proves nothing");
  assert(/frame /.test(text()), "no 'frame m:ss / 300s' readout beside the bar");
});

// --------------------------------------------------------------- THE HAZARD
test("the pause frame does not claim PAUSED while the sub is still exposing", () => {
  publish("paused", 12, 40);   // the pause POST's own snapshot: same frames_done
  assert(/PAUSING/.test(text()),
    "the badge does not say PAUSING after a pause landed mid-exposure — " +
    `it reads: ${text().slice(0, 160)}`);
  assert(!/PAUSED/.test(text()),
    "the dashboard says PAUSED while the shutter is still open — this is the " +
    "state an operator uncaps the scope in");
});

test("the sub-frame bar stays up through PAUSING — the shutter is still open", () => {
  assert(subFrameBar() != null,
    "the in-flight exposure bar was removed the moment pause was pressed, so the " +
    "one indicator that the shutter is still open disappeared while it still was");
});

test("PAUSING states how long until the run has actually stopped", () => {
  // 40s into a 300s sub: about four and a half minutes left. The exact digits
  // depend on the coarse tick; the point is that a time is offered at all and
  // that it is the frame's remaining time, not the run's ETA.
  assert(/shutter open/i.test(text()),
    "PAUSING says nothing about why the run has not stopped yet");
  assert(/0?4:[0-9]{2}/.test(text()),
    `no ~4:xx remaining-on-this-frame clock in the header: ${text().slice(0, 200)}`);
});

// ------------------------------------------------------------- the handover
test("once the interrupted frame is banked, it is PAUSED for real", () => {
  publish("paused", 13, 0);    // the frame completed; frames_done advanced
  assert(/PAUSED/.test(text()),
    "the run never reaches PAUSED after the in-flight frame completed — a phase " +
    "that cannot end is worse than the wrong word");
  assert(!/PAUSING/.test(text()), "still PAUSING after the frame was banked");
  assert(subFrameBar() == null,
    "the sub-frame bar is still up with no exposure running — it now says the " +
    "shutter is open when it is closed, which is the same lie in reverse");
});

test("a pause between frames is PAUSED immediately — no invented PAUSING", () => {
  // The other real case: the operator hits pause while the rig is dithering or
  // slewing, so nothing is exposing and the run stops at once. Claiming
  // "finishing this frame" there would be its own fabrication.
  publish("running", 20, 30);
  assert(/RUNNING/.test(text()), "precondition: the run did not go back to running");
  publish("paused", 20, EXPOSURE_S + 60);  // last frame ended a minute ago
  assert(/PAUSED/.test(text()),
    "a pause with no exposure in flight still reported PAUSING — the phase must " +
    "come from the frame, not from the word 'paused' arriving");
  assert(!/PAUSING/.test(text()), "invented a PAUSING phase with nothing exposing");
});

// ------------------------------------------------- THE TEARDOWN (review #9b)
// `POST /api/sequence/abort` awaits the whole wind-down — abort the exposure,
// stop the guider, panel/cover off, finalize, drain the thumbnails — so the
// engine publishes state="aborting" the moment it starts and "aborted" only
// once the rig has stopped. The Monitor derived "a run is live" from
// running||paused, so that frame unmounted the controls row — INCLUDING the
// Abort button — for the entire ~210 s teardown. The operator holds Abort,
// everything vanishes, and the mount is still slewing.
const buttonNamed = (re: RegExp): any =>
  [...container.querySelectorAll("button")].find((b: any) => re.test(b.textContent || ""));

test("the run controls stay on the dashboard for the whole teardown", () => {
  // The engine drops eta_s on this frame (there is no finish to predict), which
  // is exactly the frame the header has to cope with.
  act(() => {
    const f: any = seqFrame("aborting", 13, 40);
    delete f.progress.eta_s;
    delete f.progress.eta_confident;
    f.detail = "stopping the run — ending the exposure and the guider.";
    useStore.setState({ sequence: f } as never);
  });
  assert(/ABORTING/.test(text()),
    `the badge does not say ABORTING while the engine tears the run down: ${text().slice(0, 200)}`);
  const abort = buttonNamed(/Abort/i);
  assert(abort != null,
    "the Abort control unmounted the moment the abort landed — the operator is " +
    "left with no evidence the press did anything, over a rig that is still moving");
  assert(abort.disabled === true,
    "Abort is still pressable during its own teardown; a second one re-cancels a " +
    "task already inside its cancellation handler and severs the wind-down");
  assert(/Aborting/.test(abort.textContent || ""),
    `the button still reads as an offer, not a report: "${abort.textContent}"`);
  const pause = buttonNamed(/^\s*Pause\s*$/);
  assert(pause == null || pause.disabled === true,
    "Pause is live on a run being torn down — the engine refuses it, so the " +
    "button cannot do what it says");
});

test("…and the finish clock says what is happening instead of counting down", () => {
  assert(/no finish time/.test(text()),
    "the header slot went blank (which reads as 'over') or kept counting down to " +
    `a completion that was cancelled: ${text().slice(0, 300)}`);
});

test("…and the terminal state still ends it", () => {
  act(() => {
    useStore.setState({ sequence: { state: "aborted", plan_name: "Tonight" } } as never);
  });
  assert(/ABORTED/.test(text()), `the teardown never terminated: ${text().slice(0, 160)}`);
  assert(buttonNamed(/Abort/i) == null,
    "the run controls outlived the run — Abort is offered over a stopped rig");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`monitorPauseDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
