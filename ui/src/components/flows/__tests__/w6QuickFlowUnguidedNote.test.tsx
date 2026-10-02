// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w6QuickFlowUnguidedNote.test.tsx -- the classic quick flow shows the
// unguided sub-limit note beside its Guide toggle, sharing its constant with
// #/next (#588, backlog WP-48a fix (b)).
//
//   Run directly:  node --import tsx src/components/flows/__tests__/w6QuickFlowUnguidedNote.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY. H4 built the UNGUIDED_SUB_LINE_S mirror and its note on the #/next
// quick sheet only (next/hubs/session/flows/create/quick.tsx,
// quickUnguidedLimit.test.tsx). The classic sheet, which the root UI shows,
// kept a bare ON/OFF Guide toggle: Guide OFF on the sheet's own 180 s
// narrowband default is refused only by a 422 the operator meets after
// pressing SAVE, on numbers the sheet filled in itself.
//
// NOT ONE SHARED MODULE. The issue's suggested shape moves both constants
// into a module both sheets import. That module would be a new, non-test
// file outside the three this WP owns (FlowNode.tsx, QuickFlow.tsx,
// quick.tsx) -- stopped here and reported in the work package's return. What
// IS built: QuickFlow.tsx carries its own copy, word for word the #/next
// sheet's, and this file holds the two copies equal to each other AND both
// to doctor.py's source, the same two-check shape quickUnguidedLimit.test.tsx
// already runs for the #/next copy alone -- the established pattern this very
// file (QuickFlow.tsx) already uses for its other copied constants
// (DRIFT_TESTS, r7Parity.test.ts: "pins create/quickPayload.ts's six copied
// helpers against components/flows/QuickFlow").
//
// WHAT IS GUARDED
//
//   1. Guide off: the note beside the toggle is QuickFlow's own UNGUIDED_NOTE,
//      naming the limit in seconds.
//   2. Guide on (the control): the limit is not shown.
//   3. THE TWO SHEETS AGREE: QuickFlow.tsx's UNGUIDED_SUB_LINE_S and
//      UNGUIDED_NOTE equal quick.tsx's, word for word -- a WP that edited one
//      and not the other goes red here.
//   4. THE MIRROR IS THE SERVER'S NUMBER: UNGUIDED_SUB_LINE_S equals
//      doctor.py's, read out of that file's source.
//
// Convention: shell-and-tests.md section 4 (jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// quick.tsx (imported below only for its two constants, never rendered) owns
// one CSS import site (`./create.css`); Node has no idea what a `.css` file
// is without this hook.
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

// ------------------------------------------------------------------ imports
const { readFileSync } = await import("node:fs");
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const QuickFlow = (await import("../QuickFlow")).default;
const { UNGUIDED_NOTE: CLASSIC_NOTE, UNGUIDED_SUB_LINE_S: CLASSIC_LINE } =
  await import("../QuickFlow");
const { UNGUIDED_NOTE: NEXT_NOTE, UNGUIDED_SUB_LINE_S: NEXT_LINE } =
  await import("../../../next/hubs/session/flows/create/quick");

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

const RIG_WHEEL = { names: ["L", "R", "G", "B", "S", "Ha", "Oiii", "DARK"],
  opaque: [false, false, false, false, false, false, false, true] };

let root: ReturnType<typeof createRoot> | null = null;
function unmount(): void {
  if (!root) return;
  const r = root; root = null;
  act(() => { r.unmount(); });
}
function setUp(): void {
  unmount();
  useStore.setState({
    flows: { ...(useStore.getState() as any).flows, ui: { ...(useStore.getState() as any).flows.ui, quickOpen: true, highlightId: null } },
    principal: { role: "admin", email: "t@t", caps: ["control.capture", "control.mount"] },
    status: { filterwheel: RIG_WHEEL },
    pushConfirm: async () => true,
    flowsLoadLibrary: async () => {},
  } as any);
}
async function mount(): Promise<HTMLElement> {
  const host = win.document.getElementById("root") as HTMLElement;
  unmount();
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => { root!.render(createElement(QuickFlow)); });
  return host;
}
const buttons = (): HTMLElement[] => Array.from(win.document.querySelectorAll("button"));
const byText = (re: RegExp): HTMLElement | null =>
  buttons().find((b) => re.test((b.textContent || "").trim())) ?? null;
/** The note beside the Guide toggle, or "" when the sheet draws none. */
function guideNote(): string {
  return (win.document.querySelector("[data-quick-unguided-note]")?.textContent ?? "") as string;
}

setUp();
await mount();

// ==================================================== the classic sheet's note

// MUTANT "no limit beside the classic toggle" (QuickFlow.tsx: the note's
// `{!guided && (` made `{false && !guided && (`, so Guide OFF draws nothing
// new). Run from a byte backup in this worktree, restored byte-identical
// after (sha256 matched, the mutant text grepped gone). Observed,
// w6QuickFlowUnguidedNote.test 6/7:
//   x with Guide off, the classic sheet names the unguided limit: Guide is
//     off and no note names the limit
//     expected no GUIDE stage: every sub must be under 120 s, where unguided
//       stars start to trail. A longer one is refused.
//     got      (empty)
test("precondition: the classic sheet rendered with Guide on and no note", () => {
  const guide = byText(/^Guide/);
  assert(guide != null, "no Guide toggle on the classic quick sheet");
  eq(guide!.getAttribute("aria-pressed"), "true", "precondition: Guide starts on");
  eq(guideNote(), "", "a note shows while Guide is on");
});

test("CONTROL: with Guide on, the limit is not shown", () => {
  const note = guideNote();
  assert(!note.includes(`${CLASSIC_LINE} s`) && note !== CLASSIC_NOTE,
    `Guide is on, and the classic sheet shows the unguided limit: "${note}"`);
});

await (async () => {
  act(() => { byText(/^Guide/)!.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
})();

test("with Guide off, the classic sheet names the unguided limit", () => {
  eq(byText(/^Guide/)!.getAttribute("aria-pressed"), "false", "precondition: the press turned Guide off");
  eq(guideNote(), CLASSIC_NOTE, "Guide is off and no note names the limit");
  assert(CLASSIC_NOTE.includes(`${CLASSIC_LINE} s`),
    `the note must say the limit in seconds (${CLASSIC_LINE} s): "${CLASSIC_NOTE}"`);
  assert(!/—/.test(CLASSIC_NOTE), "no em-dash may reach the screen");
});

await (async () => {
  act(() => { byText(/^Guide/)!.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
})();

test("CONTROL: turned back on, the limit goes again", () => {
  eq(byText(/^Guide/)!.getAttribute("aria-pressed"), "true", "precondition: Guide is on again");
  eq(guideNote(), "", `the limit outlived the switch: "${guideNote()}"`);
});

// ============================================ the two sheets cannot drift

// MUTANT "classic copy drifts from #/next's" (QuickFlow.tsx's
// UNGUIDED_SUB_LINE_S = 120 made 119, leaving quick.tsx's at 120). Run from a
// byte backup in this worktree, restored byte-identical after (sha256
// matched, the mutant text grepped gone). Observed, w6QuickFlowUnguidedNote.
// test 4/7 (this case, the note-text case and the doctor.py mirror case):
//   x the classic and #/next copies of UNGUIDED_SUB_LINE_S agree
//     expected 120
//     got      119
//   x the classic and #/next copies of UNGUIDED_NOTE agree, word for word
//     expected no GUIDE stage: every sub must be under 120 s, where unguided
//       stars start to trail. A longer one is refused.
//     got      no GUIDE stage: every sub must be under 119 s, where unguided
//       stars start to trail. A longer one is refused.
//   x UNGUIDED_SUB_LINE_S mirrors doctor.py's: the classic sheet's unguided
//     limit is not the doctor's line
//     expected 120
//     got      119
test("the classic and #/next copies of UNGUIDED_SUB_LINE_S agree", () => {
  eq(CLASSIC_LINE, NEXT_LINE, "the classic and #/next copies of UNGUIDED_SUB_LINE_S agree");
});

test("the classic and #/next copies of UNGUIDED_NOTE agree, word for word", () => {
  eq(CLASSIC_NOTE, NEXT_NOTE, "the classic and #/next copies of UNGUIDED_NOTE agree");
});

// --------------------------------------------------------- the server's number
const DOCTOR_REL = "../../../../../server/astrodeck/flows/doctor.py";

test("UNGUIDED_SUB_LINE_S mirrors doctor.py's", () => {
  let src: string;
  try {
    src = readFileSync(new URL(DOCTOR_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${DOCTOR_REL}: ${(e as Error).message}`);
  }
  const all = Array.from(src.matchAll(/^UNGUIDED_SUB_LINE_S = (\d+)\s*$/gm));
  eq(all.length, 1, "doctor.py must bind UNGUIDED_SUB_LINE_S exactly once, as a whole number");
  eq(CLASSIC_LINE, Number(all[0][1]), "the classic sheet's unguided limit is not the doctor's line");
});

// ------------------------------------------------------------------- tally
unmount();
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw6QuickFlowUnguidedNote.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
