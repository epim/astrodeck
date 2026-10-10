// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RotatorSequenceLock.test.tsx - the ROTATOR sheet locks GO, the nudges,
// ROTATE TO PA, SYNC TO SKY and TEST ROTATOR while a run is live, with the
// rig's own sentence, BEFORE the press (#751; WP-145, wave 16). Mounted.
// REVERSE is locked the same way since #822 (WP-158, wave 18): the rig refuses
// `/api/rotator/reverse` during a run too.
//
//   Run directly:  npx tsx src/next/hubs/rig/rotator/__tests__/w16RotatorSequenceLock.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// WP-114 (#698) made the rotator's move and solving routes answer 409 coded
// `sequence_running` while `engine.running` (`_refuse_while_sequence_runs` in
// `app.py`). The panel showed that sentence AFTER the press (pinned in
// `w15RotatorRetest.test.tsx`) and never disabled the buttons beforehand, as
// `useLock` does for the lane and capability blocks. So during a night the page
// offered five buttons that could only fail.
//
// WHAT IS WORTH ASSERTING:
//
//   EVERY LIVE STATE LOCKS. `running`, `paused`, `holding` (a cloud hold) and
//   `aborting` (the wind-down) are all `engine.running` on the rig, so the lock
//   uses the same predicate every other surface does (`runIsLive`). A lock that
//   read only "running" would arm the buttons through a hold the rig refuses.
//
//   THE SENTENCE IS THE RIG'S OWN, PER ROUTE. Not a paraphrase and not one
//   sentence for five buttons: each route's `refused` and `why` are read from
//   `app.py` here, so a reworded refusal breaks THIS test rather than leaving a
//   panel that says one thing before the press and the rig another after it.
//
//   A PRESS EXPLAINS AND SENDS NOTHING. Locked is the honest-disabled idiom:
//   dim, `aria-disabled`, titled, and a press toasts the reason.
//
//   HALT IS THE WAY OUT AND STAYS LIVE. The rig does not refuse it during a run
//   (`/api/rotator/halt` is deliberately not guarded).
//
//   THE LOCK LIFTS WITH THE RUN. Idle, complete, aborted, error and NINA-driven
//   states leave every button armed, and a run ending re-arms them without a
//   remount.
//
//   THE PERMANENT REASONS STILL COME FIRST. A principal without the capability
//   is told that, not that a run is live: a sign-in cannot lift a run, and the
//   capability sentence is the one that stays true after it ends.
//
// MUTANTS RUN (each from a byte backup of RotatorPanel.tsx, restored
// byte-identically with sha256 compared, the mutant text grepped out). The
// failing assertion each produced, verbatim (the file is 17 cases at the end;
// the first runs of Y1-Y10 were against 16, before the unmeasured-rotator case
// was added, which only Y4 needs):
//
//   Y1 "GO and the nudges are not locked by a run" (`moveReason` without the
//      run), 10/16: "x running: GO, the nudges, ROTATE TO PA, SYNC TO SKY and TEST
//      ROTATOR are locked with the rig's sentence: rotator-move does not carry the
//      rig's own sentence for its route (expected a sequence is running; rotator
//      move refused, because it would turn the camera under the run's frames.
//      Stop the run first, got type a position angle, or drag the dial, and GO
//      will send it.)"
//   Y2 "ROTATE TO PA is not locked by a run", 10/16: "... rotator-solve is ARMED
//      while the run is running: the rig answers it 409 sequence_running"
//   Y3 "SYNC TO SKY is not locked by a run", 11/16: "... rotator-sync is ARMED
//      while the run is running: the rig answers it 409 sequence_running"
//   Y4 "TEST ROTATOR is not locked by a run", 11/16: "... rotator-preflight does
//      not carry the rig's own sentence for its route (expected a sequence is
//      running; rotator preflight refused, because it turns the rotator about 22
//      degrees and takes four plate solves, which would ruin the run's frames.
//      Stop the run first, got ...)"
//   Y5 "HALT is locked by a run", 15/16: "x running: HALT is live and a press
//      posts the halt route: HALT is locked while a run is live (title "a
//      sequence is running; rotator move refused, because it would turn the
//      camera under the run's frames. Stop the run first"): it is the way out,
//      and the rig does not refuse it"
//   Y6 "only `running` counts as a live run", 13/16: "x paused: GO, the nudges,
//      ROTATE TO PA, SYNC TO SKY and TEST ROTATOR are locked with the rig's
//      sentence: rotator-move does not carry the rig's own sentence for its
//      route (expected a sequence is running; rotator move refused, ...)" (and
//      the same for `holding`)
//   Y7 "SYNC's sentence is reworded" (`re-calibrate` made `calibrate`), 11/16:
//      "... rotator-sync does not carry the rig's own sentence for its route
//      (expected a sequence is running; sync to sky refused, because it would
//      re-calibrate the rotator's sky angle under the run. Stop the run first,
//      got a sequ...)"
//   Y8 "a run outranks the capability lock on GO", 15/16: "x a principal without
//      control.capture is told the capability, not the run: rotator-move does
//      not say what the viewer lacks: "a sequence is running; rotator move
//      refused, because it would turn the camera under the run's frames. Stop
//      the run first""
//   Y9 "the lock outlives the run" (the run is always live), 10/16: "x idle:
//      nothing is locked for a run's sake: rotator-move is locked by a run that
//      is idle: "a sequence is running; rotator move refused, because it would
//      turn the camera under the run's frames. Stop the run first""
//   Y10a "the PA field is not locked by a run", 15/16: "x running: the MOVE TO
//      field and the dial are locked with the move sentence: the PA field is not
//      locked by the run (expected a sequence is running; rotator move refused,
//      because it would turn the camera under the run's frames. Stop the run
//      first, got )"
//   Y10b "the dial is not locked by a run", 15/16: "x running: the MOVE TO field
//      and the dial are locked with the move sentence: the MOVE TO dial is armed
//      while the run is live"
//
//   Y11 "the run's note is drawn for a principal who cannot move the rotator"
//      (`{runLive && !motionNote && (` made `{runLive && (`), 16/17: "x a
//      principal without control.capture is told the capability, not the run: a
//      principal who cannot move the rotator is shown the run's note"
//   Y12 (retired by #822, WP-158, wave 18). It was "REVERSE is locked by a run"
//      (`const reverseReason = moveBase ?? inFlight;` made `= moveReason;`): the
//      rig did not refuse `/api/rotator/reverse` then, and this file pinned
//      REVERSE live and failed the day the route took the guard. The route takes
//      it now, so REVERSE is the sixth control in the list.
//   Y13 "REVERSE is not locked by a run" (`const reverseReason = moveBase ?? (runLive
//      ? SEQUENCE_REVERSE : null) ?? inFlight;` made `= moveBase ?? inFlight;`;
//      run from a byte backup of RotatorPanel.tsx, restored byte-identically,
//      md5sum compared), 12/18: "x running: GO, the nudges, ROTATE TO PA, SYNC TO
//      SKY, TEST ROTATOR and REVERSE are locked with the rig's sentence:
//      rotator-reverse is ARMED while the run is running: the rig answers it 409
//      sequence_running" (and paused, holding, aborting), "x running: a press on
//      each locked button states the reason and reaches nothing: rotator-reverse
//      refused in silence: toasts were []" and "x a run ending arms REVERSE, and
//      a press posts the reverse route: premise: REVERSE is locked while the run
//      is live"
//
// Before the change the file read 8/16 (the cases that assert nothing is locked
// without a run, HALT, the capability order and the reader guard passed).

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
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: String(url), body });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const fs = await import("node:fs");
const { fileURLToPath } = await import("node:url");
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

// ------------------------------------------- the rig's sentences, from app.py
// A UI test cannot import Python, so the four refusals are READ as text
// (`weatherHoldClaim.test.ts` does the same for its clauses). The helper's
// template and each route's two arguments are taken from the source: a route
// that stops calling the helper, or rewords what it says, fails here.
const APP_PY = fs.readFileSync(
  fileURLToPath(new URL("../../../../../../../server/astrodeck/api/app.py", import.meta.url)), "utf8");

/** The Python string literals of one call's arguments, in order: adjacent
 *  literals concatenate (`"a " "b"` is one argument), a comma ends an argument,
 *  and the keyword arguments (`lane=`) end the list. */
function callArgs(source: string, routeFn: string): string[] {
  const at = source.indexOf(`async def ${routeFn}(`);
  if (at < 0) throw new Error(`app.py no longer defines ${routeFn}`);
  const call = source.indexOf("_refuse_while_sequence_runs(", at);
  const nextDef = source.indexOf("async def ", at + 10);
  if (call < 0 || (nextDef > 0 && call > nextDef)) {
    throw new Error(`${routeFn} no longer calls _refuse_while_sequence_runs`);
  }
  let i = call + "_refuse_while_sequence_runs(".length;
  const args: string[] = [];
  let cur: string | null = null;
  while (i < source.length) {
    const c = source[i];
    if (c === '"') {
      let j = i + 1;
      let lit = "";
      while (source[j] !== '"') { lit += source[j]; j++; }
      cur = (cur ?? "") + lit;
      i = j + 1;
      continue;
    }
    if (c === ",") { if (cur !== null) args.push(cur); cur = null; }
    if (/[A-Za-z_]/.test(c)) break;              // `lane=`: the keyword arguments
    if (c === ")") break;
    i++;
  }
  if (cur !== null) args.push(cur);
  return args;
}

const helperAt = APP_PY.indexOf("def _refuse_while_sequence_runs(");
const helperEnd = APP_PY.indexOf("@app.post(", helperAt);
const HELPER = APP_PY.slice(helperAt, helperEnd);
const TEMPLATE_OK = HELPER.includes('f"a sequence is running; {refused} refused, because {why}. "')
  && HELPER.includes('f"Stop the run first"');
const serverSentence = (routeFn: string): string => {
  const [refused, why] = callArgs(APP_PY, routeFn);
  return `a sequence is running; ${refused} refused, because ${why}. Stop the run first`;
};
const SENTENCE = {
  move: () => serverSentence("rotator_move"),
  rotate: () => serverSentence("rotator_rotate_to_pa"),
  sync: () => serverSentence("rotator_sync_to_sky"),
  preflight: () => serverSentence("rotator_preflight"),
  reverse: () => serverSentence("rotator_reverse"),
};

test("the parser reads the rig's template and its four sentences (a vacuity guard)", () => {
  assert(TEMPLATE_OK, "app.py's _refuse_while_sequence_runs no longer builds its sentence as this test expects");
  eq(SENTENCE.move(),
    "a sequence is running; rotator move refused, because it would turn the camera under the run's frames. Stop the run first",
    "the move sentence was not read from app.py as written");
  eq(SENTENCE.preflight(),
    "a sequence is running; rotator preflight refused, because it turns the rotator about 22 degrees and takes four plate solves, which would ruin the run's frames. Stop the run first",
    "the preflight sentence (two adjacent literals) was not read as one");
  eq(SENTENCE.reverse(),
    "a sequence is running; rotator reverse refused, because it would flip the rotator's direction convention under the run, changing what every later rotation means. Stop the run first",
    "the reverse sentence (two adjacent literals) was not read from app.py as written");
});

// -------------------------------------------------------------------- seeding
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = {
  role: "viewer", email: "viewer@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived"],
};

function seed(sequence: Record<string, unknown>, principal: any = OPERATOR,
              measured: Record<string, unknown> = { sky_sign: 1, trusted: true }): void {
  act(() => {
    useStore.setState({
      principal,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      sequence,
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
        rotator: {
          name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
          synced: true, can_reverse: true, reverse: false,
          // by default a measured, PASSED rotator: TEST ROTATOR's own "nothing to
          // measure" lock is on, so the run's sentence must outrank it to be seen.
          // A case below seeds an UNMEASURED one, where the run is alone.
          ...measured,
        },
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
const titleOf = (node: any): string => (node?.getAttribute("title") ?? "") as string;
const commands = () => asked.filter((a) => a.method !== "GET");
const toastTitles = (): string[] =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => String(t.title));

/** [test id, the sentence that must be on it]. TEST ROTATOR is on a PASSED
 *  rotator in `seed`, so its own "already measured" lock is also set: the run's
 *  sentence has to win over it. */
const LOCKED_BY_A_RUN: Array<[string, () => string]> = [
  ["rotator-move", SENTENCE.move],
  ["rotator-nudge-minus", SENTENCE.move],
  ["rotator-nudge-plus", SENTENCE.move],
  ["rotator-solve", SENTENCE.rotate],
  ["rotator-sync", SENTENCE.sync],
  ["rotator-preflight", SENTENCE.preflight],
  // Locked by #822: the rig refuses the reverse route during a run too.
  ["rotator-reverse", SENTENCE.reverse],
];

// =========================================== every live state locks, per route
for (const state of ["running", "paused", "holding", "aborting"]) {
  test(`${state}: GO, the nudges, ROTATE TO PA, SYNC TO SKY, TEST ROTATOR and REVERSE are locked with the rig's sentence`, () => {
    seed({ state });
    mount();
    for (const [testid, sentence] of LOCKED_BY_A_RUN) {
      const b = id(testid);
      assert(b != null, `${testid} is not on the sheet - nothing may be hidden`);
      assert(locked(b),
        `${testid} is ARMED while the run is ${state}: the rig answers it 409 sequence_running`);
      eq(titleOf(b), sentence(), `${testid} does not carry the rig's own sentence for its route`);
    }
  });
}

test("running, rotator not yet measured: TEST ROTATOR is locked by the run alone", () => {
  seed({ state: "running" }, OPERATOR, { sky_sign: null, trusted: null });
  mount();
  const b = id("rotator-preflight");
  assert(locked(b), "TEST ROTATOR is ARMED while the run is live: the rig answers it 409");
  eq(titleOf(b), SENTENCE.preflight(), "TEST ROTATOR does not carry the rig's sentence");
  act(() => { useStore.setState({ sequence: { state: "idle" } } as never); });
  assert(!locked(id("rotator-preflight")),
    "TEST ROTATOR stays locked on an unmeasured rotator with no run");
});

test("running: the MOVE TO field and the dial are locked with the move sentence", () => {
  seed({ state: "running" });
  mount();
  eq(titleOf(id("rotator-pa")), SENTENCE.move(), "the PA field is not locked by the run");
  assert(locked(id("rotator-pa")), "the PA field is armed while the run is live");
  // The Dial primitive puts the lock on its slider, not on its wrapper.
  const slider = id("rotator-dial")?.querySelector('[role="slider"]');
  assert(slider != null, "the MOVE TO dial has no slider");
  assert(locked(slider), "the MOVE TO dial is armed while the run is live");
  eq(titleOf(slider), SENTENCE.move(), "the dial does not carry the move sentence");
});

test("running: the sheet says why, once, where the controls are", () => {
  seed({ state: "running" });
  mount();
  const note = id("rotator-lock-sequence");
  assert(note != null, "no note says why five buttons are dim");
  const text = String(note.textContent);
  assert(/sequence is running/i.test(text), `the note does not say a run is why: "${text}"`);
  assert(/HALT stays live/.test(text), `the note does not say what is still pressable: "${text}"`);
});

// ========================================== a press explains and sends nothing
await testAsync("running: a press on each locked button states the reason and reaches nothing", async () => {
  seed({ state: "running" });
  mount();
  for (const [testid, sentence] of LOCKED_BY_A_RUN) {
    act(() => { useStore.setState({ toasts: [] } as never); });
    asked.length = 0;
    click(id(testid));
    await settle();
    assert(toastTitles().includes(sentence()),
      `${testid} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
    eq(commands().length, 0,
      `${testid} reached the rig anyway: ${JSON.stringify(commands())}`);
  }
});

// ======================================================== HALT is the way out
await testAsync("running: HALT is live and a press posts the halt route", async () => {
  seed({ state: "running" });
  mount();
  const halt = id("rotator-halt");
  assert(!locked(halt), `HALT is locked while a run is live (title ${JSON.stringify(titleOf(halt))}): `
    + "it is the way out, and the rig does not refuse it");
  asked.length = 0;
  click(halt);
  await settle();
  eq(commands().length, 1, "HALT sent more or fewer than one command");
  assert(commands()[0].url.endsWith("/api/rotator/halt"), `HALT posted ${commands()[0].url}`);
});

// ========================================================= REVERSE is one of the six
// `/api/rotator/reverse` calls `_refuse_while_sequence_runs` since #822 (the sheet
// left REVERSE live before that only because the rig answered it). Its sentence
// is read from `app.py` like the other five (`SENTENCE.reverse` throws if the
// route stops calling the guard), and REVERSE is in `LOCKED_BY_A_RUN`, so every
// live state, the press-explains-and-sends-nothing case and the capability-first
// case grade it with the rest. This one is the control: once the run ends the
// switch is armed and a press posts the reverse route.
await testAsync("a run ending arms REVERSE, and a press posts the reverse route", async () => {
  seed({ state: "running" });
  mount();
  assert(locked(id("rotator-reverse")), "premise: REVERSE is locked while the run is live");
  act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
  await settle();
  const rev = id("rotator-reverse");
  assert(rev != null, "REVERSE is not on the sheet for a rotator that can reverse");
  assert(!locked(rev),
    `REVERSE is still locked after the run ended (title ${JSON.stringify(titleOf(rev))})`);
  asked.length = 0;
  click(rev);
  await settle();
  eq(commands().length, 1, "REVERSE sent more or fewer than one command");
  assert(commands()[0].url.endsWith("/api/rotator/reverse"), `REVERSE posted ${commands()[0].url}`);
});

// ===================================================== the lock lifts with a run
for (const state of ["idle", "complete", "aborted", "error", "nina_native"]) {
  test(`${state}: nothing is locked for a run's sake`, () => {
    seed({ state });
    mount();
    for (const [testid] of LOCKED_BY_A_RUN) {
      if (testid === "rotator-preflight") continue;   // the PASSED rotator's own lock, not a run's
      const b = id(testid);
      assert(b != null, `${testid} is not on the sheet`);
      assert(!/sequence is running/.test(titleOf(b)),
        `${testid} is locked by a run that is ${state}: ${JSON.stringify(titleOf(b))}`);
    }
    assert(!locked(id("rotator-solve")) && !locked(id("rotator-sync")),
      `ROTATE TO PA or SYNC TO SKY is locked with the run ${state}`);
    assert(id("rotator-lock-sequence") == null, `the run note is on the sheet with the run ${state}`);
    assert(!/sequence is running/.test(titleOf(id("rotator-preflight"))),
      "TEST ROTATOR carries a run's sentence with no run");
  });
}

await testAsync("a run ending re-arms the buttons without a remount", async () => {
  seed({ state: "running" });
  mount();
  assert(locked(id("rotator-solve")), "premise: ROTATE TO PA is locked while the run is live");
  act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
  await settle();
  assert(!locked(id("rotator-solve")), "ROTATE TO PA is still locked after the run ended");
  // GO may still be locked for want of a target; it must not be for the run.
  assert(!/sequence is running/.test(titleOf(id("rotator-move"))),
    "GO is still locked by the run after it ended");
  assert(id("rotator-lock-sequence") == null, "the run note outlived the run");
});

// ======================================== the permanent reasons still come first
test("a principal without control.capture is told the capability, not the run", () => {
  seed({ state: "running" }, VIEWER);
  mount();
  for (const [testid] of LOCKED_BY_A_RUN) {
    const b = id(testid);
    assert(locked(b), `${testid} is armed for a viewer`);
    assert(/needs operator or admin access/.test(titleOf(b)),
      `${testid} does not say what the viewer lacks: ${JSON.stringify(titleOf(b))}`);
    assert(!/sequence is running/.test(titleOf(b)),
      `${testid} tells a viewer about the run, which a sign-in cannot lift: ${JSON.stringify(titleOf(b))}`);
  }
  // The sheet's own read-only line already says what they lack; the run's note
  // is for someone who could have pressed the buttons.
  assert(id("rotator-lock-sequence") == null, "a principal who cannot move the rotator is shown the run's note");
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w16RotatorSequenceLock.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
