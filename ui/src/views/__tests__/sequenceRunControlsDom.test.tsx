// sequenceRunControlsDom.test.tsx — the run panel while the rig disagrees with it.
//
//   Run directly:  npx tsx src/views/__tests__/sequenceRunControlsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A DOM TEST. Every claim here is about STATE OVER TIME — a badge that must
// say one thing while a shutter is open and another once it shuts, a button that
// must stay out of service across a POST that never resolves, a lock that must
// explain itself only for the duration of a run. A single rendered tree cannot
// express any of that, so the view is mounted into a real document and driven
// the way the engine drives it: by replacing the `sequence` slice, which is
// exactly what the 2 s status frame does.
//
// WHAT IT CANNOT DO: jsdom lays nothing out, so nothing here is evidence about
// widths or overlap. It answers "what did the panel say, and could you press
// it", which is what these findings were about.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store, the api client or the view are imported: the api
// client reads window.location at module scope and the tree reads matchMedia.
// run-tests.mjs gives this file its own process.
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
win.Element.prototype.scrollIntoView = function () {};

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
// `abortReply` is the knob the abort tests turn: the real route awaits the whole
// teardown, so the honest simulation of it is a promise that does not resolve
// inside api.ts's budget — and then a rejection shaped exactly like the one
// api.ts throws on its own timeout.
const NOW_S = Math.floor(Date.now() / 1000);
const NIGHT = {
  date: "2026-08-05", transit_unix: NOW_S + 3600, transit_alt: 70,
  transit_in_daylight: false,
  dark_start_unix: NOW_S, dark_end_unix: NOW_S + 7200,
  darkness_kind: "astronomical",
  samples: [
    { unix: NOW_S, alt: 40, az: 90, moon_sep: 90 },
    { unix: NOW_S + 3600, alt: 70, az: 150, moon_sep: 90 },
    { unix: NOW_S + 7200, alt: 45, az: 220, moon_sep: 90 },
  ],
  moon: { illumination: 0.2, alt: -20, az: 10, separation_deg: 90 },
  best_window: { start_unix: NOW_S, end_unix: NOW_S + 7200, mean_alt: 55 },
  alt_limit_deg: 30, never_rises_above_limit: false,
};

const posts: string[] = [];
let abortReply: "ok" | "hang" = "ok";
// One catalog hit, so the add-a-target path can actually be DRIVEN (type into
// the shared search, pick the row) rather than asserted about from the outside.
const CATALOG_HIT = {
  id: "M31", name: "Andromeda Galaxy", type: "galaxy",
  ra_hours: 0.712, dec_deg: 41.27, mag: 3.4, size_arcmin: 190, alt: 55, az: 60,
};
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  if ((init?.method ?? "GET") === "POST") posts.push(path);
  if (path.includes("/api/sequence/abort") && abortReply === "hang") {
    // What api.ts's own AbortSignal timeout throws — and it tests it with
    // `e instanceof DOMException`, so this must be the SAME constructor api.ts
    // sees (the global one), not jsdom's separate copy.
    throw new DOMException("Timeout", "TimeoutError");
  }
  const json =
    path.includes("/api/sequence/recoverable") ? { recoverable: false }
      : path.includes("/api/catalog") ? { results: [CATALOG_HIT], notes: [] }
        : path.includes("/api/calibration/masters") ? []
          : path.includes("/api/plans") ? []
            : path.includes("/api/visibility") ? NIGHT
              : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

// ------------------------------------------------------- the 500 ms tick meter
// `useShutterRemainingS` owns the only 500 ms interval in this tree (Toasts has
// one, but it is not mounted here and goes through window.setInterval, not the
// module-scope global this wrapper replaces). Counting the LIVE handles — added
// on create, removed on clear — is how the "does it tick for the whole run?"
// question gets an answer from outside the component.
const live500 = new Set<any>();
const realSetInterval = g.setInterval;
const realClearInterval = g.clearInterval;
g.setInterval = (fn: any, ms?: number, ...rest: any[]) => {
  const h = realSetInterval(fn, ms, ...rest);
  if (ms === 500) live500.add(h);
  return h;
};
g.clearInterval = (h: any) => { live500.delete(h); return realClearInterval(h); };

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const SequenceView = (await import("../SequenceView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** One `sequence` frame.
 *
 *  `frameStartedAtMs` is the engine's own in-flight marker (`_begin_frame` sets
 *  it immediately before `hub.capture`; `_record_frame` zeroes it, and it
 *  serializes as null when zero). Passing null is the engine saying the shutter
 *  is shut — which is the entire difference between the two pause phases. */
function seq(
  state: string,
  { frameStartedAtMs = null as number | null, exposureS = 300, framesDone = 3 } = {},
): void {
  const now = Date.now();
  useStore.setState({
    sequence: {
      state,
      plan_name: "NGC7000 SHO",
      detail: "NGC7000: Ha 300s  [4/20]",
      progress: {
        frames_done: framesDone, frames_total: 20, percent: 15, elapsed_s: 900,
        rejected: 0,
        server_now_ms: now,
        current_exposure_s: exposureS,
        frame_started_at_ms: frameStartedAtMs,
      },
    },
  } as never);
}

function seedRig(): void {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim", busy_lanes: [],
      filterwheel: { names: ["L", "Ha"], opaque: [false, false], position: 0 },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
    plan: {
      ...(useStore.getState() as any).plan,
      name: "NGC7000 SHO",
      targets: [{
        id: "t1", name: "NGC7000", ra_hours: 20.98, dec_deg: 44.5,
        center: true, autofocus_first: false, calibration: false,
        steps: [{ id: "s1", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 20 }],
      }],
    },
  } as never);
}

seedRig();
seq("running", { frameStartedAtMs: Date.now() - 20_000 });
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => { root.render(createElement(SequenceView)); });

async function settle(ms = 0): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
/** The status frame — the only thing that ever tells this view the truth. */
async function frame(...args: Parameters<typeof seq>): Promise<void> {
  await act(async () => { seq(...args); });
}
const buttons = (): any[] => [...container.querySelectorAll("button")];
const byText = (re: RegExp): any =>
  buttons().find((b: any) => re.test((b.textContent || "").trim()));
const text = (): string => container.textContent || "";
/** The state badge's WORD. Read from the badge itself rather than by scanning
 *  the page: the pausing note deliberately contains the word PAUSED ("until
 *  this reads PAUSED"), so a page-wide regex would find the warning it is
 *  supposed to be replacing. */
const badge = (): string => {
  const el = [...container.querySelectorAll("span")].find((s: any) =>
    /^(RUNNING|PAUSING|PAUSED|ABORTING|COMPLETE|ERROR|ABORTED)$/.test((s.textContent || "").trim()));
  return el ? (el.textContent || "").trim() : "";
};

await settle(50);

// ------------------------------------------------------------- the fixture
test("the run panel mounted, with a live run and a plan to lock", () => {
  // Anti-blank-page guard: most assertions below are "this word is NOT on the
  // screen", which an empty document satisfies for free.
  assert(badge() === "RUNNING", `no RUNNING badge — this is not the live run panel (badge: "${badge()}")`);
  assert(byText(/^Pause$/) != null, "no Pause button");
  assert(byText(/Abort/) != null, "no Abort button");
  assert(/NGC7000/.test(text()), "the plan never rendered, so the lock note has nothing to lock");
});

// ------------------------------------------------ PAUSE, PHASE ONE (audit #4)
await frame("paused", { frameStartedAtMs: Date.now() - 20_000, exposureS: 300 });
test("a pause over an open shutter says PAUSING, not PAUSED", () => {
  assert(badge() === "PAUSING",
    `the badge reads "${badge()}" while the engine's own in-flight marker says a 300s ` +
    "exposure is still running — this is the screen an operator reads before " +
    "uncapping the scope");
});

test("…and says how long the shutter has left", () => {
  // The engine reported a 300s frame 20s in, so ~4:40 remain. Assert the shape,
  // not the second, so a slow test box cannot make this flap.
  assert(/\d?\d:\d\d of shutter left/.test(text()),
    `no countdown for the frame in flight — the note read: ${text().slice(0, 400)}`);
  assert(/hands off the scope/i.test(text()),
    "the pausing note never says what not to do, which is the only reason it exists");
});

test("Resume is not offered for a pause that has not happened", () => {
  assert(byText(/^Resume$/) == null,
    "a bare 'Resume' during PAUSING claims a transition the engine has not made");
  assert(byText(/Cancel pause/) != null,
    "nothing offers the way back out of a pause that is still pending");
});

// ------------------------------------------------ PAUSE, PHASE TWO (audit #4)
await frame("paused", { frameStartedAtMs: null, framesDone: 4 });
test("PAUSED lands the moment the engine clears its in-flight marker", () => {
  assert(badge() === "PAUSED",
    `the badge reads "${badge()}" after the frame landed and the engine dropped its marker`);
  assert(!/of shutter left/.test(text()),
    "the pausing note outlived the exposure it was counting down");
  assert(byText(/^Resume$/) != null, "Resume never came back on a genuinely paused run");
});

// ------------------------------------------------------------ ABORT (audit #9)
await frame("running", { frameStartedAtMs: Date.now() - 5_000 });
test("Abort is live and asks for a hold before it is pressed", () => {
  const b = byText(/Abort/);
  assert(b != null && b.disabled === false, "Abort starts out unavailable — the fixture is wrong");
  assert(/hold to confirm/.test(b.textContent || ""), "the hold affordance is missing");
});

abortReply = "hang";
{
  // Hold it the way HoldButton requires: press, wait past holdMs, release.
  const b = byText(/Abort/);
  const ev = (type: string) => {
    const e = new win.Event(type, { bubbles: true, cancelable: true });
    (e as any).pointerId = 1; (e as any).pointerType = "touch";
    (e as any).button = 0; (e as any).isPrimary = true;
    return e;
  };
  await act(async () => { b.dispatchEvent(ev("pointerdown")); });
  await settle(900);
  await act(async () => { b.dispatchEvent(ev("pointerup")); });
  await settle(30);
}
test("the hold actually fired — otherwise everything below is vacuous", () => {
  assert(posts.some((p) => p.includes("/api/sequence/abort")),
    "the hold never reached the abort route, so no in-flight state is under test");
});

test("an abort in flight takes itself and Pause out of service", () => {
  const b = byText(/Aborting/);
  assert(b != null,
    "Abort still reads 'Abort' while the teardown runs — the route awaits the whole " +
    "shutdown, so nothing about it is instant");
  assert(b.disabled === true, "the aborting button is still pressable");
  assert(b.getAttribute("aria-busy") === "true", "no aria-busy for a screen reader");
  const pause = byText(/^Pause$/);
  assert(pause != null && pause.disabled === true,
    "Pause is still live on a run that is being torn down — it would post a pause " +
    "against an engine whose task is already cancelled");
});

test("a timeout on the abort POST is not reported as a dead server", () => {
  // THE FINDING. api.ts caps at 15s; the teardown routinely outlives it, so the
  // abort that WORKED came back as "request timed out — server not responding"
  // in a red toast.
  const toasts = (useStore.getState() as any).toasts ?? [];
  const words = toasts.map((t: any) => `${t.title ?? ""} ${t.detail ?? ""}`).join(" | ");
  assert(!/not responding|timed out|unreachable/i.test(words),
    `the view toasted over an abort that is running exactly as asked: ${words}`);
});

await frame("aborted", { frameStartedAtMs: null });
test("the engine's own terminal state ends the aborting state", () => {
  assert(byText(/Aborting/) == null,
    "the panel is still aborting after the engine reported ABORTED — the local latch " +
    "never handed over to server truth");
  assert(badge() === "ABORTED", `the terminal state never rendered (badge: "${badge()}")`);
});
abortReply = "ok";

// --------------------------------------------------------- RESUME (audit #52)
// A recoverable run: the failed-actions row offers Resume-from-frame-N.
await act(async () => {
  useStore.setState({ sequence: { ...(useStore.getState() as any).sequence, detail: "camera error" } } as never);
});
{
  const before = posts.length;
  // The banner/row Resume needs `recoverable` from /api/sequence/recoverable,
  // which answered false above; drive the always-present paused Resume instead —
  // same guard, same label, and it is reachable without re-mocking the fetch.
  await frame("paused", { frameStartedAtMs: null });
  const b = byText(/^Resume$/);
  assert(b != null, "no Resume button to double-tap");
  // Two taps in the same turn, which is what a cold thumb on a phone does.
  await act(async () => {
    b.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    b.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  const fired = posts.slice(before).filter((p) => p.includes("/api/sequence/resume"));
  test("a double-tapped Resume posts once", () => {
    assert(fired.length === 1,
      `Resume fired ${fired.length} times — the second lands on a route whose session ` +
      "is already claimed and toasts an error over a resume that started fine");
  });
}
await settle(20);

// ------------------------------------------------------- LOCKED PLAN (audit #50)
await frame("running", { frameStartedAtMs: Date.now() - 1_000 });
test("the run says why half of every target card went inert", () => {
  assert(/Plan structure is locked/.test(text()),
    "the + step / delete / template buttons grey out during a run with nothing on " +
    "screen tying the lock to the run");
  assert(/apply to your next run/.test(text()),
    "the note locks the structure without saying what the still-editable fields now affect, " +
    "which is the half that teaches the wrong rule");
});

test("the lock note names the run doing the locking, not just 'a run'", () => {
  assert(/Plan structure is locked while “NGC7000 SHO” runs/.test(text()),
    "the note does not name the plan holding the lock");
});

// Every clause of that note is a claim about a control, so read the controls.
// The note is what the user believes INSTEAD of trying it, which is why an
// over-broad clause ("the fields below stay editable", said while the schedule
// editor was disabled) costs more than saying nothing.
{
  const disclosure: any = container.querySelector('button[aria-label^="Edit schedule"]');
  if (disclosure) {
    await act(async () => {
      disclosure.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
  }
  test("every lock the note claims is a lock the DOM actually has", () => {
    const addStep = buttons().find((b: any) => /\+ step/.test(b.textContent || ""));
    assert(addStep != null && addStep.disabled === true,
      "'+ step' is live while the note says steps can't be added");
    const del: any = container.querySelector('button[aria-label^="Delete target"]');
    assert(del != null && del.disabled === true,
      "the target delete is live while the note says targets can't be removed");
    const tpl: any = container.querySelector('button[aria-label^="Apply starter template"]');
    assert(tpl != null && tpl.disabled === true,
      "a starter template is live while the note says templates can't be applied");
    // "per-target scheduling is frozen" — the disclosure row itself stays
    // pressable (reading the schedule is not a write), so the claim is about
    // everything inside it.
    assert(disclosure != null, "no schedule editor on the card — the clause has no subject");
    const inside = [...disclosure.parentElement.querySelectorAll("button, input, select")]
      .filter((c: any) => c !== disclosure);
    assert(inside.length > 0,
      "the schedule editor never opened, so 'scheduling is frozen' is untested");
    const live = inside.filter((c: any) => c.disabled !== true);
    assert(live.length === 0,
      `${live.length} of ${inside.length} schedule control(s) still take input during a run`);
  });
}

// ------------------------------------------- THE LOCK IS KEPT, NOT JUST CLAIMED
// The note says targets can't be added. The control that adds them is the shared
// Atlas search in this panel's own header, and it had no lock of any kind: a
// search-pick appended a card that `+ step` (disabled={running}) could not fill
// and delete (disabled={running}) could not remove — an un-editable,
// un-deletable target directly under the sentence promising it could not exist.
const searchInput = (): any =>
  container.querySelector('input[aria-label="Search the target catalog"]');
const targetCount = (): number =>
  ((useStore.getState() as any).plan.targets as any[]).length;

/** Drive the search the way a thumb does: type (React tracks the input's value,
 *  so the NATIVE setter is what makes onChange fire at all), wait past the
 *  250 ms debounce, click the suggestion. Returns false if there was nothing to
 *  click — which the callers assert on, so no claim below can be vacuous. */
async function pickFromSearch(q: string): Promise<boolean> {
  const input = searchInput();
  if (!input) return false;
  const setValue = Object.getOwnPropertyDescriptor(
    win.HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setValue.call(input, q);
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle(320);
  const row = buttons().find((b: any) => /Andromeda Galaxy/.test(b.textContent || ""));
  if (!row) return false;
  await act(async () => {
    row.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle(30);
  return true;
}

test("the search that adds targets is out of service while the run holds the plan", () => {
  const input = searchInput();
  assert(input != null, "the catalog search is not on screen at all — nothing to lock");
  assert(input.closest("fieldset[disabled]") != null,
    "the control that adds targets is fully live under a note saying targets can't be " +
    "added — and a browser will let a keyboard user Tab into it, which is the hole " +
    "`pointer-events-none` leaves open");
});

{
  const before = targetCount();
  const drove = await pickFromSearch("M31");
  test("…and a pick that reaches the handler anyway appends nothing", () => {
    // jsdom does not implement fieldset-disabled propagation, so this drives the
    // pick a real browser refuses — which is precisely the second half of the
    // lock (addTarget's own `running` guard) under test.
    assert(drove, "the suggestion row never rendered, so no pick happened (vacuous)");
    assert(targetCount() === before,
      `the plan grew from ${before} to ${targetCount()} targets during a run, and the new ` +
      "card cannot be given steps or deleted until the run ends");
  });
}

await frame("complete", { frameStartedAtMs: null });
test("…and the note goes away when the run does", () => {
  assert(!/Plan structure is locked/.test(text()),
    "the lock note outlived the run that justified it");
});

{
  // POSITIVE CONTROL. Without this the lock test above would pass just as well
  // against a search that is broken for everyone, or a fixture that never
  // reaches the catalog at all.
  const before = targetCount();
  const input = searchInput();
  test("the search is live again the moment the run is over", () => {
    assert(input != null && input.closest("fieldset[disabled]") == null,
      "the lock outlived the run — targets can no longer be added to the next plan");
  });
  const drove = await pickFromSearch("M31");
  test("…and picking a result really does add the target", () => {
    assert(drove, "the suggestion row never rendered, so the drive proves nothing");
    assert(targetCount() === before + 1,
      `picking a catalog result added ${targetCount() - before} targets — the drive used by ` +
      "the lock test does not actually add, so that test was vacuous");
  });
}

// ------------------------------------------------- THE 500 ms TICK (audit #4b)
// The pause countdown is the only thing that needs a twice-a-second re-render,
// and it renders only while paused. Ticking on "a frame is in flight" re-rendered
// every target card, step grid, TargetSpark and SchedulePanel for the whole run.
await frame("running", { frameStartedAtMs: Date.now() - 20_000, exposureS: 300 });
await settle(20);
test("a running plan does not re-render the whole view twice a second", () => {
  assert(badge() === "RUNNING", `not a live run (badge: "${badge()}") — nothing to measure`);
  assert(live500.size === 0,
    `${live500.size} 500 ms timer(s) running over a plan editor whose fields the user can ` +
    "still type in, to move a countdown that is not on screen");
});

await frame("paused", { frameStartedAtMs: Date.now() - 20_000, exposureS: 300 });
test("…and the pause countdown does tick, or it would sit at one number", () => {
  assert(badge() === "PAUSING", `no PAUSING phase (badge: "${badge()}") — nothing to count down`);
  assert(live500.size === 1,
    `${live500.size} 500 ms timer(s) while a countdown is on screen — it would freeze at the ` +
    "number the pause frame happened to arrive with");
});

await frame("running", { frameStartedAtMs: Date.now() - 20_000, exposureS: 300 });
test("…and the timer stops again when the pause is cancelled", () => {
  assert(live500.size === 0, `${live500.size} 500 ms timer(s) survived the resume`);
});

// ------------------------------------------- THE TEARDOWN IS A LIVE RUN (#9b)
// The engine publishes state="aborting" for the WHOLE wind-down — abort the
// exposure, stop the guider, panel/cover off, finalize the report, drain the
// thumbnails — and only says "aborted" once the rig has actually stopped. The
// client's is-live predicates were `running || paused`, so that frame read as
// "not a run": the whole panel (progress, badge, and the Abort control itself)
// unmounted for the ~210 s teardown, and the local latch's `if (!running)`
// deleted the "Aborting…" label the instant the server started agreeing with
// it. Nothing on screen, over a rig that is still moving — strictly worse than
// the stale RUNNING panel it replaced.
await frame("running", { frameStartedAtMs: Date.now() - 5_000 });
abortReply = "hang";
{
  const b = byText(/Abort/);
  const ev = (type: string) => {
    const e = new win.Event(type, { bubbles: true, cancelable: true });
    (e as any).pointerId = 1; (e as any).pointerType = "touch";
    (e as any).button = 0; (e as any).isPrimary = true;
    return e;
  };
  const before = posts.length;
  await act(async () => { b.dispatchEvent(ev("pointerdown")); });
  await settle(900);
  await act(async () => { b.dispatchEvent(ev("pointerup")); });
  await settle(30);
  test("precondition: the hold fired and the row latched before the frame lands", () => {
    assert(posts.slice(before).some((p) => p.includes("/api/sequence/abort")),
      "the hold never reached the abort route, so nothing below is under test");
    assert(byText(/Aborting/) != null,
      "the row never entered its own aborting state, so the frame below has " +
      "nothing to preserve");
  });
}

// The engine's first teardown frame.
await frame("aborting", { frameStartedAtMs: null });

test("the panel stays on screen for the whole teardown", () => {
  assert(badge() === "ABORTING",
    `the badge reads "${badge()}" on the engine's own aborting frame — the run ` +
    "panel unmounts and the operator has nothing on screen while the rig stops");
  assert(/Sequence · NGC7000 SHO/.test(text()),
    "the run panel itself is gone during the teardown");
  // The badge's WORD survives the unknown-state fallback (it upper-cases
  // whatever it was handed), so read the tone too: the fallback paints an
  // accent/info chip, which is the same chip a NINA handover gets.
  const chip = [...container.querySelectorAll("span")].find(
    (s: any) => (s.textContent || "").trim() === "ABORTING");
  const cls = chip ? chip.className : "";
  assert(/text-warn/.test(cls),
    `the ABORTING badge is toned "${cls}" — a rig that has not stopped yet reads ` +
    "the same as one that is idling or handed over");
  assert(/blink/.test(cls),
    "the badge is static while a teardown is running — the blink is the channel " +
    "that survives a red-light screen where hue barely reads");
});

test("…and the Abort control stays with it, reporting instead of inviting", () => {
  const b = byText(/Aborting/);
  assert(b != null,
    "the 'Aborting…' label was deleted by the very frame that confirms it — the " +
    "abort now looks like it never happened, on the control an operator reaches " +
    "for when something is already wrong");
  assert(b.disabled === true,
    "the Abort is pressable during its own teardown: a second one used to cancel " +
    "a task already inside its cancellation handler and sever the wind-down");
  assert(b.getAttribute("aria-busy") === "true", "no aria-busy for a screen reader");
  assert(!/parking/i.test(b.textContent || ""),
    "the button claims a park; the user-abort path ends the exposure and the " +
    "guider and leaves the mount tracking (engine._safe_stop)");
});

test("…and neither Pause nor Resume is offered on a run being torn down", () => {
  assert(byText(/^Pause$/) == null,
    "Pause is on screen during a teardown — the engine refuses it, so it is a " +
    "button that cannot do what it says");
  assert(byText(/^Resume$/) == null && byText(/Cancel pause/) == null,
    "a resume control is offered for a run whose task has already been cancelled");
});

// A CLIENT THAT NEVER SAW THE PRESS. The local latch is what carried the state
// above; a phone that unlocks mid-teardown, or a second browser, has none. The
// server frame alone has to be enough.
{
  const fresh = win.document.createElement("div");
  win.document.body.appendChild(fresh);
  const freshRoot = createRoot(fresh);
  await act(async () => { freshRoot.render(createElement(SequenceView)); });
  await settle(20);
  const freshText = (): string => fresh.textContent || "";
  const freshBtn = (re: RegExp): any =>
    [...fresh.querySelectorAll("button")].find((b: any) => re.test((b.textContent || "").trim()));
  test("a client that arrived mid-teardown sees the live panel too", () => {
    assert(/Sequence · NGC7000 SHO/.test(freshText()),
      "a client with no local abort latch sees no run panel at all while the rig " +
      "is tearing down — the state on the wire is the only thing it has");
    assert(/ABORTING/.test(freshText()),
      `no ABORTING badge on a fresh mount: ${freshText().slice(0, 200)}`);
    const b = freshBtn(/Aborting/);
    assert(b != null && b.disabled === true,
      "the Abort control is missing (or live) for a client that did not witness " +
      "the press");
  });
  await act(async () => { freshRoot.unmount(); });
  fresh.remove();
}

await frame("aborted", { frameStartedAtMs: null });
test("…and the terminal state still ends it", () => {
  assert(badge() === "ABORTED", `the teardown never terminated (badge: "${badge()}")`);
  assert(byText(/Aborting/) == null, "the aborting row outlived the teardown");
});
abortReply = "ok";

// ------------------------- THE AUTOMATION COLUMN'S CLAIM (button-rest #12)
// The Automation panel stays fully live during a run and NONE of it reaches
// that run: the engine takes a copy of the plan at Run (engine.py
// `self.plan = plan`) and reads dither, refocus, the flip, the quality gates,
// cool-to, the safety gate and the end-of-run park/warm out of that copy for
// the rest of the night. So the operator who sees cloud coming at 2am, slides
// "safety monitor gate" ON and watches it STAY on — aria-checked and all —
// walks away believing an unattended run will now park on an unsafe verdict.
// Targets says this (its own LockedNote) and SchedulePanel is disabled while
// running; this column had neither, and it is the one holding the safety gate.
await frame("running", { frameStartedAtMs: Date.now() - 1_000 });
const switches = (): any[] => [...container.querySelectorAll('[role="switch"]')];
const safetyGate = (): any =>
  switches().find((s: any) => /safety monitor gate/i.test(s.getAttribute("aria-label") || ""));

test("the Automation panel says its settings belong to the NEXT run", () => {
  // PRECONDITION: the panel is actually on screen, or "the note is missing"
  // and "the panel is missing" would be the same result.
  assert(safetyGate() != null,
    "no safety-monitor-gate switch on screen — the panel this test is about never rendered");
  assert(/These settings apply to your NEXT run/.test(text()),
    "the whole Automation column — the safety gate included — is live and ignored during a " +
    "run, with nothing on screen saying so: the switch slides, stays engaged, and the " +
    "engine never sees it");
  assert(/does not reach the run in progress/.test(text()),
    "the note stops short of saying what the switch the user just moved will NOT do");
  assert(/“NGC7000 SHO”/.test(text()),
    "the note does not name the run holding the plan copy");
});

test("…and the switch is still operable, because it is the NEXT run's setting", () => {
  // The fix must be a note, not a lock: preparing tomorrow night's automation
  // while tonight runs is the whole reason Targets chose a note too. A test
  // that only asserted the note would pass just as well over a dead panel.
  const gate = safetyGate();
  assert(gate.getAttribute("aria-disabled") !== "true" && gate.disabled !== true,
    "the safety gate went out of service — the note explains a lock nobody asked for");
});

test("Instructions carry the same warning, since the rules are in that copy too", () => {
  assert(/Rules apply to your next run/.test(text()),
    "a conditional rule added mid-run never fires tonight (engine reads " +
    "self.plan.instructions off the same snapshot) and the editor says nothing");
});

await frame("complete", { frameStartedAtMs: null });
test("…and both notes go away when the run does", () => {
  assert(!/These settings apply to your NEXT run/.test(text()),
    "the automation note outlived the run that justified it, so it now describes a lie " +
    "in the other direction");
  assert(!/Rules apply to your next run/.test(text()),
    "the instructions note outlived the run");
  assert(safetyGate() != null, "the panel vanished with the run — that is not the fix either");
});

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`sequenceRunControlsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
