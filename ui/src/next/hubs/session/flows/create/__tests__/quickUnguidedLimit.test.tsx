// quickUnguidedLimit.test.tsx - the quick sheet shows the unguided limit
// beside the Guide switch whenever Guide is off (#518, H4 orchestrator
// ruling 5).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/create/__tests__/quickUnguidedLimit.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY. `wizard.quick` now refuses an unguided sub at or past the doctor's
// line (`doctor.UNGUIDED_SUB_LINE_S`, 120 s), 422 `invalid_quick_flow`
// naming the filter. The sheet pre-fills narrowband at 180 s, so with Guide
// off the operator's first SAVE would be that refusal, and the switch's note
// said "the subs are limited by the mount" while nothing limited them. Now
// the note says where the line is, before SAVE.
//
// WHAT IS GUARDED
//
//   1. Guide off: the note beside the switch is `UNGUIDED_NOTE`, naming the
//      limit in seconds.
//   2. Guide on (the control): the limit is not shown.
//   3. THE MIRROR IS THE SERVER'S NUMBER: `UNGUIDED_SUB_LINE_S` here equals
//      `UNGUIDED_SUB_LINE_S = <n>` in server/astrodeck/flows/doctor.py, read
//      out of that file's source, so a line moved on the server and not here
//      (or here and not there) is red.
//
// Each mutant ran in a private copy of `ui/` (scratchpad `H4-FLOWS-mut`,
// with doctor.py copied beside it at the same relative path), never in the
// shared tree. Convention: shell-and-tests.md section 4 (jsdom by hand,
// createRoot + act, native events, printed tally plus the
// `{ passed, failed, total }` export).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  // Tablet: the flows library's creation door is 768 px and up.
  return {
    matches: m ? 1024 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Nothing on this sheet needs the network to draw the switch; anything asked
// is answered empty, so a stray request cannot throw into React.
g.fetch = async () => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => ({ results: [], notes: [] }),
});

// ------------------------------------------------------------------ imports
const { readFileSync } = await import("node:fs");
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FlowQuickSheet, UNGUIDED_NOTE, UNGUIDED_SUB_LINE_S } = await import("../quick");

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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

/** The note drawn beside the Guide switch, from the switch's own row. */
function guideNote(): string {
  const sw = tid("quick-guide");
  assert(sw != null, "no Guide switch on the quick sheet");
  const row = sw.closest(".nx-switch-row");
  assert(row != null, "the Guide switch is drawn with no row to carry its note");
  return (row.querySelector(".nx-switch-note")?.textContent ?? "") as string;
}

// A wheel with a narrowband slot: the case the refusal is about, Ha
// pre-filled at 180 s, past the line.
win.history.replaceState(null, "", "http://local/#/session/flows/flowQuick");
resetRouterCacheForTests();
act(() => {
  useStore.setState({
    principal: {
      role: "admin", email: null,
      caps: ["view.status", "view.preview", "control.mount", "control.capture"],
    } as never,
    authGate: "open",
    sequence: { state: "idle" } as never,
    status: {
      connected: { camera: { connected: true } },
      filterwheel: { names: ["L", "R", "Ha"], narrowband: [false, false, true] },
    } as never,
    equipConnected: true,
    wsPhase: "up",
    confirm: null as never,
    toasts: [] as never,
  } as never);
});
await act(async () => { root.render(createElement(FlowQuickSheet)); });
await settle();

const LIMIT = `${UNGUIDED_SUB_LINE_S} s`;

test("precondition: the quick sheet rendered with Guide on and the wheel's rows", () => {
  assert(tid("session-flow-quick") != null,
    "no session-flow-quick marker - the fixture is wrong, not the sheet");
  eq(tid("quick-guide")?.getAttribute("aria-checked"), "true", "Guide starts on");
  const rows = Array.from(container.querySelectorAll('[data-testid="quick-channel"]'))
    .map((r: any) => r.getAttribute("data-channel")).join("|");
  eq(rows, "L|R|Ha", "the rows are the connected wheel's");
});

// RED under the quick.tsx mutant "the limit whether or not Guide is on" (the
// Switch's `note` made `UNGUIDED_NOTE` on both arms), observed (3/5 passed):
//
//   x CONTROL: with Guide on, the limit is not shown: Guide is on, and the
//     note shows the unguided limit: "no GUIDE stage: every sub must be under
//     120 s, where unguided stars start to trail. A longer one is refused."
//   x CONTROL: turned back on, the limit goes again: the limit outlived the
//     switch: "no GUIDE stage: every sub must be under 120 s, where unguided
//     stars start to trail. A longer one is refused."
//
// A comment added to quick.tsx left all five green (the control on the
// harness).
test("CONTROL: with Guide on, the limit is not shown", () => {
  const note = guideNote();
  assert(note.length > 0, "precondition: the switch carries a note while on");
  assert(!note.includes(LIMIT) && note !== UNGUIDED_NOTE,
    `Guide is on, and the note shows the unguided limit: "${note}"`);
});

// RED under the quick.tsx mutant "no limit beside the switch" (the off arm
// given back "no GUIDE stage: the run is unguided and the subs are limited by
// the mount"), observed (4/5 passed):
//
//   x with Guide off, the note beside the switch names the limit: Guide is
//     off and the note does not name the unguided limit
//   expected no GUIDE stage: every sub must be under 120 s, where unguided
//     stars start to trail. A longer one is refused.
//   got      no GUIDE stage: the run is unguided and the subs are limited by
//     the mount
await (async () => {
  click(tid("quick-guide"));
  await settle();
})();

test("with Guide off, the note beside the switch names the limit", () => {
  eq(tid("quick-guide")?.getAttribute("aria-checked"), "false",
    "precondition: the press turned Guide off");
  eq(guideNote(), UNGUIDED_NOTE, "Guide is off and the note does not name the unguided limit");
  assert(UNGUIDED_NOTE.includes(LIMIT),
    `the note must say the limit in seconds (${LIMIT}): "${UNGUIDED_NOTE}"`);
  assert(!/—/.test(UNGUIDED_NOTE), "no em-dash may reach the screen");
});

// Back on: the limit goes with it, so the note follows the switch both ways.
await (async () => {
  click(tid("quick-guide"));
  await settle();
})();

test("CONTROL: turned back on, the limit goes again", () => {
  eq(tid("quick-guide")?.getAttribute("aria-checked"), "true", "precondition: Guide is on again");
  assert(!guideNote().includes(LIMIT), `the limit outlived the switch: "${guideNote()}"`);
});

// ----------------------------------------------------- the server's number
//
// RED under the quick.tsx mutant "a changed mirror" (`UNGUIDED_SUB_LINE_S =
// 120` made 119), observed:
//
//   x UNGUIDED_SUB_LINE_S mirrors doctor.py's: the quick sheet's unguided
//     limit is not the doctor's line
//     expected 120
//     got      119
//
// and red the same way with the mirror left at 120 and doctor.py's constant
// moved to 100 in the scratch copy ("expected 100 / got 120").
const DOCTOR_REL = "../../../../../../../../server/astrodeck/flows/doctor.py";

test("UNGUIDED_SUB_LINE_S mirrors doctor.py's", () => {
  let src: string;
  try {
    src = readFileSync(new URL(DOCTOR_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${DOCTOR_REL}: ${(e as Error).message}`);
  }
  const all = Array.from(src.matchAll(/^UNGUIDED_SUB_LINE_S = (\d+)\s*$/gm));
  eq(all.length, 1, "doctor.py must bind UNGUIDED_SUB_LINE_S exactly once, as a whole number");
  eq(UNGUIDED_SUB_LINE_S, Number(all[0][1]), "the quick sheet's unguided limit is not the doctor's line");
});

// ------------------------------------------------------------------- tally
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`quickUnguidedLimit.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
