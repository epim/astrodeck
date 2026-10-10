// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RotatorCardSequenceLock.test.tsx - the CLASSIC rotator card disables Go,
// the nudges, Rotate to PA, Sync to sky and Test rotator while a run is live,
// with the rig's own sentence as their title (#751; WP-145, wave 16). Mounted.
// The reverse box is locked the same way since #822 (WP-158, wave 18): the rig
// refuses `/api/rotator/reverse` during a run too.
//
//   Run directly:  npx tsx src/components/__tests__/w16RotatorCardSequenceLock.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The same defect as the #/next panel's (`w16RotatorSequenceLock.test.tsx` has
// the story): WP-114 (#698) made the rig refuse these five with 409
// `sequence_running` while a run is going, and the card offered them armed and
// let the operator find out after the press. The classic root is the production
// root today.
//
// WHAT IS WORTH ASSERTING (the same list, in the card's own idiom):
//
//   EVERY LIVE STATE DISABLES, per route, with the rig's sentence read out of
//   `app.py` so a reworded refusal breaks this file.
//   HALT STAYS LIVE and a press posts the halt route.
//   THE LOCK LIFTS WITH THE RUN, in every state that is not live, and on the
//   run ending without a remount.
//   A VIEWER IS TOLD THE CAPABILITY, not the run: the card's own read-only line
//   already says it, and the run's note is not drawn for someone who could not
//   have pressed the buttons anyway.
//   A DISABLED BUTTON'S TITLE IS NOT SHOWN ON TOUCH, so the lock is also said once
//   on the card (`data-rotator-sequence-lock`).
//
// MUTANTS RUN (each from a byte backup of RotatorCard.tsx, restored
// byte-identically with sha256 compared, the mutant text grepped out). The
// failing assertion each produced, verbatim (14 cases when Z1-Z9 first ran; the
// unmeasured-rotator case, which only Z4 needs, made it 15):
//
//   Z1 "a run never disables anything" (`runLive` made `false`), 8/14: "x
//      running: Go, the nudges, Rotate to PA, Sync to sky and Test rotator are
//      disabled with the rig's sentence: Go does not carry the rig's own sentence
//      for its route (expected a sequence is running; rotator move refused,
//      because it would turn the camera under the run's frames. Stop the run
//      first, got )"
//   Z2 "Rotate to PA is not disabled by a run", 9/14: "... Rotate to PA is
//      ENABLED while the run is running: the rig answers it 409 sequence_running"
//   Z3 "Sync to sky is not disabled by a run", 10/14: "... Sync to sky is
//      ENABLED while the run is running: the rig answers it 409 sequence_running"
//   Z4 "Test rotator is not disabled by a run", 14/14 on the first seed (SURVIVED:
//      a PASSED rotator disables the button anyway, so `runLive` in `disabled`
//      is unobservable), then 14/15 with the unmeasured-rotator case: "x running,
//      rotator not yet measured: Test rotator is disabled by the run alone: Test
//      rotator is ENABLED while the run is live: the rig answers it 409"
//   Z5 "Halt is disabled by a run", 13/14: "x running: Halt is live and a press
//      posts the halt route: Halt is disabled while a run is live: it is the way
//      out, and the rig does not refuse it"
//   Z6 "only `running` counts as a live run", 11/14: "x paused: Go, the nudges,
//      ... are disabled with the rig's sentence: Go does not carry the rig's own
//      sentence for its route (expected a sequence is running; rotator move
//      refused, ...)" (and holding, aborting)
//   Z7 "Sync's sentence is reworded", 10/14: "... Sync to sky does not carry the
//      rig's own sentence for its route (expected a sequence is running; sync to
//      sky refused, because it would re-calibrate the rotator's sky angle under
//      the run. Stop the run first, got a sequ...)"
//   Z8 "a viewer is told about the run", 13/14: "x a viewer is told the
//      capability, not the run: Go tells a viewer about the run, which a sign-in
//      cannot lift: "a sequence is running; rotator move refused, because it
//      would turn the camera under the run's frames. Stop the run first""
//   Z9 "the lock outlives the run" (the run is always live), 8/14: "x idle:
//      nothing is disabled for a run's sake: Go carries a run's sentence with the
//      run idle: "a sequence is running; rotator move refused, because it would
//      turn the camera under the run's frames. Stop the run first""
//   Z10 "Go is not disabled by a run" (`|| runLive` removed from Go's `disabled`;
//      found by the independent verifier), SURVIVED at 15/15 because Go is also
//      disabled by the empty target field on a fresh card; then, with a valid PA
//      typed in first, 16/17: "x running, a position angle typed in: Go is
//      disabled by the run alone: Go is ENABLED while the run is live and a PA is
//      typed: the rig answers it 409 sequence_running"
//   Z11 (retired by #822, WP-158, wave 18). It was "the reverse box is disabled
//      by a run": the rig did not refuse `/api/rotator/reverse` then, and this
//      file pinned the box live and failed the day the route took the guard.
//      The route takes it now, so the box is the sixth control in the list.
//   Z12 "the reverse box is not disabled by a run" (`|| runLive` removed from
//      the checkbox's `disabled`; run from a byte backup of RotatorCard.tsx,
//      restored byte-identically, md5sum compared), 12/17: "x running: Go, the
//      nudges, Rotate to PA, Sync to sky, Test rotator and reverse are disabled
//      with the rig's sentence: reverse is ENABLED while the run is running: the
//      rig answers it 409 sequence_running" (and paused, holding, aborting), and
//      "x a run ending re-enables the reverse box, and a press posts the reverse
//      route: premise: the reverse box is disabled while the run is live"
//
// The card as it stood before this change (`git show HEAD:` of the file, put in
// place from a byte backup and restored) read 8/14: the same cases as Z1.

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
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: String(url), body });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const fs = await import("node:fs");
const { fileURLToPath } = await import("node:url");
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

// ------------------------------------------- the rig's sentences, from app.py
// Read as text (a UI test cannot import Python); the same reader as
// `next/hubs/rig/rotator/__tests__/w16RotatorSequenceLock.test.tsx`.
const APP_PY = fs.readFileSync(
  fileURLToPath(new URL("../../../../server/astrodeck/api/app.py", import.meta.url)), "utf8");

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
const HELPER = APP_PY.slice(helperAt, APP_PY.indexOf("@app.post(", helperAt));
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

test("the reader finds the rig's template and its sentences (a vacuity guard)", () => {
  assert(HELPER.includes('f"a sequence is running; {refused} refused, because {why}. "')
    && HELPER.includes('f"Stop the run first"'),
    "app.py's _refuse_while_sequence_runs no longer builds its sentence as this test expects");
  eq(SENTENCE.sync(),
    "a sequence is running; sync to sky refused, because it would re-calibrate the rotator's sky angle under the run. Stop the run first",
    "the sync sentence was not read from app.py as written");
  eq(SENTENCE.reverse(),
    "a sequence is running; rotator reverse refused, because it would flip the rotator's direction convention under the run, changing what every later rotation means. Stop the run first",
    "the reverse sentence (two adjacent literals) was not read from app.py as written");
});

// -------------------------------------------------------------------- seeding
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: "viewer@rig", caps: ["view.status", "view.preview"] };

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
        connected: {}, backend_links: [], busy_lanes: [],
        rotator: {
          name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
          synced: true, can_reverse: true, reverse: false,
          // by default a measured, PASSED rotator: Test rotator's own "already
          // measured" lock is on, so the run's sentence has to win over it to be
          // seen. A case below seeds an UNMEASURED one, where the run is the
          // only thing that disables the button.
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
  act(() => { rootRef!.render(createElement(RotatorCard as any)); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const buttonNamed = (name: string) => (Array.from(container.querySelectorAll("button")) as any[])
  .find((b) => (b.textContent || "").trim() === name);
const commands = () => asked.filter((a) => a.method !== "GET");
// A button carries its own title; the reverse checkbox is titled by the label
// that wraps it (the whole "reverse" control is the hover target).
const titleOf = (node: any): string => (node?.getAttribute("title")
  ?? node?.closest?.("label")?.getAttribute("title") ?? "") as string;
/** Type into the card's target-PA field the way a person does: the native value
 *  setter, then an `input` event, which is what React's onChange listens for. */
function typeAngle(value: string): void {
  const input = q('input[aria-label="Target position angle, degrees"]');
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}

const MINUS = "\u2212";
/** [what, how to find it, the rig's sentence for its route]. */
const LOCKED_BY_A_RUN: Array<[string, () => any, () => string]> = [
  ["Go", () => buttonNamed("Go"), SENTENCE.move],
  [`${MINUS}1\u00b0`, () => buttonNamed(`${MINUS}1\u00b0`), SENTENCE.move],
  ["+1\u00b0", () => buttonNamed("+1\u00b0"), SENTENCE.move],
  ["Rotate to PA", () => buttonNamed("Rotate to PA (plate solve)"), SENTENCE.rotate],
  ["Sync to sky", () => q("[data-rotator-sync]"), SENTENCE.sync],
  ["Test rotator", () => q("[data-rotator-preflight]"), SENTENCE.preflight],
  // Locked by #822: the rig refuses the reverse route during a run too.
  ["reverse", () => q('input[type="checkbox"]'), SENTENCE.reverse],
];

// =========================================== every live state locks, per route
for (const state of ["running", "paused", "holding", "aborting"]) {
  test(`${state}: Go, the nudges, Rotate to PA, Sync to sky, Test rotator and reverse are disabled with the rig's sentence`, () => {
    seed({ state });
    mount();
    for (const [what, find, sentence] of LOCKED_BY_A_RUN) {
      const b = find();
      assert(b != null, `${what} is not on the card`);
      assert(b.disabled === true,
        `${what} is ENABLED while the run is ${state}: the rig answers it 409 sequence_running`);
      eq(titleOf(b), sentence(), `${what} does not carry the rig's own sentence for its route`);
    }
  });
}

test("running, rotator not yet measured: Test rotator is disabled by the run alone", () => {
  // With the sign unmeasured nothing else disables it, so this is the case in
  // which `runLive` in the button's `disabled` is observable at all.
  seed({ state: "running" }, OPERATOR, { sky_sign: null, trusted: null });
  mount();
  const b = q("[data-rotator-preflight]");
  assert(b.disabled === true, "Test rotator is ENABLED while the run is live: the rig answers it 409");
  eq(titleOf(b), SENTENCE.preflight(), "Test rotator does not carry the rig's sentence");
  act(() => { useStore.setState({ sequence: { state: "idle" } } as never); });
  assert(q("[data-rotator-preflight]").disabled === false,
    "Test rotator stays disabled on an unmeasured rotator with no run");
});

// GO'S `disabled` ALSO READS THE TARGET FIELD, and a fresh card's field is empty,
// so every case above sees Go disabled whether or not the run is in the
// expression: the verifier's mutant C-go-unlocked (`|| runLive` removed from Go's
// `disabled`) survived 15/15. With a valid PA typed in, the run is the only thing
// that can disable it.
test("running, a position angle typed in: Go is disabled by the run alone", () => {
  seed({ state: "running" });
  mount();
  typeAngle("10");
  const go = buttonNamed("Go");
  assert(go != null, "Go is not on the card");
  assert(go.disabled === true,
    "Go is ENABLED while the run is live and a PA is typed: the rig answers it 409 sequence_running");
  eq(titleOf(go), SENTENCE.move(), "Go does not carry the rig's own sentence for its route");
  act(() => { useStore.setState({ sequence: { state: "idle" } } as never); });
  assert(buttonNamed("Go").disabled === false,
    "premise: with a PA typed and no run, Go is armed (so the run was what disabled it)");
});

test("running: the card says why, once, and names what stays live", () => {
  seed({ state: "running" });
  mount();
  const note = q("[data-rotator-sequence-lock]");
  assert(note != null, "no note says why five buttons are disabled (a title is not shown on touch)");
  const text = String(note.textContent);
  assert(/sequence is running/i.test(text), `the note does not say a run is why: "${text}"`);
  assert(/Halt stays live/.test(text), `the note does not say what is still pressable: "${text}"`);
});

// ======================================================== Halt is the way out
await testAsync("running: Halt is live and a press posts the halt route", async () => {
  seed({ state: "running" });
  mount();
  const halt = buttonNamed("Halt");
  assert(halt != null, "no Halt button");
  assert(halt.disabled === false,
    "Halt is disabled while a run is live: it is the way out, and the rig does not refuse it");
  asked.length = 0;
  click(halt);
  await settle();
  eq(commands().length, 1, "Halt sent more or fewer than one command");
  assert(commands()[0].url.endsWith("/api/rotator/halt"), `Halt posted ${commands()[0].url}`);
});

// ================================================ the reverse box is one of the six
// `/api/rotator/reverse` calls `_refuse_while_sequence_runs` since #822 (the box
// was left live before that only because the rig answered it). The sentence is
// read from `app.py` like the other five (`SENTENCE.reverse` throws if the route
// stops calling the guard), so the box and the rig cannot drift apart. The "every
// live state" loop above grades the disabled state and the title in each live
// state; a synthetic click is NOT graded on the disabled box, because jsdom
// toggles a disabled checkbox on `dispatchEvent` and React then fires onChange,
// which a browser never does for a person. What is graded below is the other
// half: the box works again once the run ends.
await testAsync("a run ending re-enables the reverse box, and a press posts the reverse route", async () => {
  seed({ state: "running" });
  mount();
  assert(q('input[type="checkbox"]').disabled === true,
    "premise: the reverse box is disabled while the run is live");
  act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
  await settle();
  const box = q('input[type="checkbox"]');
  assert(box.disabled === false, "the reverse box is still disabled after the run ended");
  eq(titleOf(box), "", "the reverse box still carries a run's sentence after the run ended");
  asked.length = 0;
  click(box);
  await settle();
  eq(commands().length, 1, "the reverse box sent more or fewer than one command");
  assert(commands()[0].url.endsWith("/api/rotator/reverse"), `the reverse box posted ${commands()[0].url}`);
});

// ===================================================== the lock lifts with a run
for (const state of ["idle", "complete", "aborted", "error", "nina_native"]) {
  test(`${state}: nothing is disabled for a run's sake`, () => {
    seed({ state });
    mount();
    for (const [what, find] of LOCKED_BY_A_RUN) {
      const b = find();
      assert(b != null, `${what} is not on the card`);
      assert(!/sequence is running/.test(titleOf(b)),
        `${what} carries a run's sentence with the run ${state}: ${JSON.stringify(titleOf(b))}`);
    }
    assert(buttonNamed("Rotate to PA (plate solve)").disabled === false,
      `Rotate to PA is disabled with the run ${state}`);
    assert(q("[data-rotator-sync]").disabled === false, `Sync to sky is disabled with the run ${state}`);
    assert(q("[data-rotator-sequence-lock]") == null, `the run note is drawn with the run ${state}`);
  });
}

await testAsync("a run ending re-enables the buttons without a remount", async () => {
  seed({ state: "running" });
  mount();
  assert(buttonNamed("Rotate to PA (plate solve)").disabled === true,
    "premise: Rotate to PA is disabled while the run is live");
  act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
  await settle();
  assert(buttonNamed("Rotate to PA (plate solve)").disabled === false,
    "Rotate to PA is still disabled after the run ended");
  assert(q("[data-rotator-sequence-lock]") == null, "the run note outlived the run");
});

// ======================================== the permanent reasons still come first
test("a viewer is told the capability, not the run", () => {
  seed({ state: "running" }, VIEWER);
  mount();
  for (const [what, find] of LOCKED_BY_A_RUN) {
    const b = find();
    assert(b.disabled === true, `${what} is enabled for a viewer`);
    assert(!/sequence is running/.test(titleOf(b)),
      `${what} tells a viewer about the run, which a sign-in cannot lift: ${JSON.stringify(titleOf(b))}`);
  }
  assert(q("[data-rotator-sequence-lock]") == null, "a viewer is shown the run's note");
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w16RotatorCardSequenceLock.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
