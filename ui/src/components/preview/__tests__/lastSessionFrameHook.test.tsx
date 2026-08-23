// lastSessionFrameHook.test.tsx — the ORDERING contract of useLastSessionFrame,
// probed directly.
//
//   Run directly:  npx tsx src/components/preview/__tests__/lastSessionFrameHook.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A SEPARATE FILE FROM captureLastFrameDom. That file mounts the real
// Capture screen and asserts what a user sees, which is the right test for the
// feature — but it CANNOT see the hook's ordering guards at all, because
// PreviewStage paints a stand-in ONLY when it has no frame of its own. Strip
// every guard out of the hook and each assertion over there still passes: the
// stage covers for it. Measured, not assumed — the guards were deleted one at a
// time and that file stayed green. A guard nothing can fail is not a guard.
//
// So this file probes the hook's RETURN VALUE, where the two orderings that
// have no other witness are visible:
//
//   * a reply that lands after the caller stopped needing one must be dropped
//     — not parked in state, where the next time the caller needs one it would
//     surface a picture minutes stale, over a run that has been delivering
//     frames all night;
//   * one fetch, ever — the status frame arrives every 2 s and the listing is a
//     library walk that reads every FITS header on a cold cache.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the api client: BASE reads window.location at module scope.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

const STAGE_CSS_W = 512;
win.devicePixelRatio = 3;
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  configurable: true, get() { return STAGE_CSS_W; },
});

let calls = 0;
let gate: Promise<void> | null = null;
let openGate: (() => void) | null = null;
let payload: any = null;

win.fetch = async (input: any) => {
  const path = String(input);
  if (path.startsWith("/api/gallery/frames")) {
    calls++;
    if (gate) await gate;
  }
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: async () => payload ?? {},
    text: async () => JSON.stringify(payload ?? {}),
  };
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "fetch", "devicePixelRatio",
]) {
  Object.defineProperty(g, k, {
    value: k === "window" ? win : win[k], writable: true, configurable: true,
  });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, useRef, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useLastSessionFrame } = await import("../useLastSessionFrame");
const { VIEW_WIDTH_STEPS } = await import("../../../lib/frameView");
type SequenceState = import("../../../types").SequenceState;

// ------------------------------------------------------------------ harness
let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const NOW_MS = Date.now();
const NOW_S = Math.floor(NOW_MS / 1000);
const PATH = "NGC 6946/Light_NGC6946_L_180s_0042.fits";

const running: SequenceState = {
  state: "running",
  target: "NGC 6946",
  progress: {
    frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800,
    rejected: 0, server_now_ms: NOW_MS,
  },
};

function listing(): any {
  return {
    frames: [{
      path: PATH, name: "f.fits", folder: "NGC 6946", night: "2026-08-16",
      ts: NOW_S - 40, local_date: "2026-08-17", local_clock: "23:14",
      target: "NGC 6946", filter: "L", frame_type: "Light", exposure_s: 180,
      bytes: 1, mtime: NOW_S - 35,
    }],
    total: 1, bytes: 1, offset: 0, limit: 12, truncated: false, scan_ms: 1,
  };
}

/** The probe. Publishes whatever the hook returned into `seen` so the test can
 *  assert on the hook's own answer rather than on some component's rendering of
 *  it — the point of this file. */
let seen: { src: string; label: string; alt: string } | null = null;
let renders = 0;
function Probe(props: { enabled: boolean; sequence: SequenceState; linkDown?: boolean }): any {
  const stageRef = useRef<HTMLDivElement>(null);
  seen = useLastSessionFrame({
    enabled: props.enabled, sequence: props.sequence,
    linkDown: props.linkDown ?? false, stageRef,
  });
  renders++;
  return createElement("div", { ref: stageRef });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
/** A render of the caller. `sequence` is rebuilt each time BY DEFAULT because
 *  that is what really happens: the 2 s status frame replaces the object, ~1800
 *  times a night, and an effect that depended on its identity would turn this
 *  one-shot fetch into a library walk every two seconds. */
function render(enabled: boolean, sequence: SequenceState = { ...running }): void {
  act(() => { root.render(createElement(Probe, { enabled, sequence })); });
}
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

// ---------------------------------------------------------------------- tests
await test("an enabled caller under a live run gets the saved frame", async () => {
  payload = listing();
  render(true);
  assert(seen === null, "the hook answered before the fetch could have resolved");
  await settle();
  assert(seen != null, `the hook never produced a stand-in (${calls} gallery calls)`);
  assert(seen!.src.includes(encodeURIComponent(PATH)), `wrong frame: ${seen!.src}`);
});

await test("the render width comes from the stage, in device pixels", () => {
  // PRECONDITION: there is a URL to read.
  assert(seen != null, "no stand-in (precondition)");
  const w = Number(/[?&]w=(\d+)/.exec(seen!.src)?.[1] ?? 0);
  assert((VIEW_WIDTH_STEPS as readonly number[]).includes(w), `w=${w} is not a rung`);
  assert(w >= STAGE_CSS_W * 3,
    `asked for ${w} px to fill ${STAGE_CSS_W * 3} device px — the stand-in is upscaled`);
});

await test("the 2 s status frame does not walk the library again", async () => {
  // PRECONDITION: it walked it once. Each extra call is a full header scan of
  // the library on a cold cache.
  assert(calls === 1, `expected 1 gallery call so far, saw ${calls}`);
  const before = renders;
  for (let i = 0; i < 6; i++) render(true);   // six fresh sequence objects
  await settle();
  assert(renders > before, "the probe did not actually re-render (precondition)");
  assert(calls === 1,
    `six status frames produced ${calls} gallery calls — that is a poll. The ` +
    "sequence OBJECT is replaced on every one of them, so an effect keyed on " +
    "its identity walks the library all night");
});

await test("a caller that no longer needs one is handed nothing", async () => {
  // PRECONDITION: there IS a stand-in to withhold.
  assert(seen != null, "nothing was being offered before the live frame (precondition)");
  render(false);                                    // the live frame arrived
  await settle();
  assert(seen === null,
    "the hook kept offering a saved frame to a caller that has a live one — " +
    "the only thing standing between that and the screen would be the stage");
});

await test("and it stays nothing when the caller needs one again", async () => {
  // The store's auth gate clears `previews` back to [] (clearedRigState), so a
  // re-authenticating browser really can go from "has a live frame" back to
  // "has nothing" with the same run still going. What must NOT happen then is
  // the minutes-old picture from before popping back over an active run.
  // PRECONDITION: we are in the post-live state.
  assert(seen === null, "the hook is still holding a stand-in (precondition)");
  render(true);
  await settle();
  assert(seen === null,
    "a stand-in fetched before the first live frame came back afterwards — by " +
    "then the rig has been delivering frames for however long, and this " +
    "picture is the oldest thing on the screen");
  assert(calls === 1,
    `needing one again walked the library a second time (${calls} calls). One ` +
    "fetch means one per mount: by now the ring is the honest source and a " +
    "second listing would only race it");
});

// ---------------------------------------------------- the race, at the source
await test("a reply that lands after the caller gave up is dropped, not parked", async () => {
  // Fresh mount: a new browser session, a slow library walk.
  act(() => { root.unmount(); });
  const c2 = win.document.createElement("div");
  win.document.body.appendChild(c2);
  const r2 = createRoot(c2);
  const render2 = (enabled: boolean) => {
    act(() => { r2.render(createElement(Probe, { enabled, sequence: running })); });
  };

  calls = 0;
  seen = null;
  payload = listing();
  gate = new Promise<void>((resolve) => { openGate = resolve; });

  render2(true);
  await settle();
  // PRECONDITION: the walk is genuinely in flight and unanswered.
  assert(calls === 1, `expected the walk to have started, saw ${calls} calls`);
  assert(seen === null, "the gate did not hold the reply");

  // The first live preview lands while the walk is still running.
  render2(false);
  await settle();
  assert(seen === null, "the hook is offering a stand-in to a caller with a live frame");

  openGate!();
  gate = null;
  await settle();
  assert(seen === null, "the late reply was handed over anyway");

  // …and the caller losing its live frame must not surface the late reply.
  render2(true);
  await settle();
  assert(seen === null,
    "the reply that landed after the live frame was parked in state and " +
    "surfaced the moment the caller asked again — the stand-in has to be " +
    "retired at the point the race is lost, not merely hidden");
  act(() => { r2.unmount(); });
});

const total = passed + failed;
console.log(`lastSessionFrameHook: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
