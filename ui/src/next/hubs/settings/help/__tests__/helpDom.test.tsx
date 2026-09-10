// helpDom.test.tsx - the rebuilt Help area, MOUNTED inside its sheet
// (wave R7, T-R7-16).
//
//   Run directly:  npx tsx src/next/hubs/settings/help/__tests__/helpDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).
//
// WHAT EACH TEST GUARDS, and what goes RED when the guarded behaviour is
// removed (every one of these was actually sabotaged and restored - the failing
// text is quoted in the task report):
//
//   1. markers          - the vacuity guard. Every later assertion counts or
//      reads elements; if the sheet rendered nothing at all, a count of zero
//      would otherwise have to be caught by a `=== 10` that reads as a content
//      bug rather than a blank page.
//   2. PARITY, problems - the rendered entry count equals `TROUBLESHOOTING.length`
//      AND every symptom string is on screen. Count alone would pass a list
//      that rendered one entry ten times; the symptom sweep would pass a
//      list that rendered ten entries plus an eleventh invented one. Both.
//   3. PARITY, glossary - the rendered row count equals `Object.keys(HELP).length`
//      and every term label is on screen, for the same pair of reasons.
//   4. copy rules       - no em-dash, en-dash or typographic quote survives to
//      the DOM. `TROUBLESHOOTING` and `HELP` carry 30-odd of them at source and
//      are shared with `#/classic`, so the only place this can be enforced is
//      the render boundary (`hyphenate`).
//   5. the deep link    - seeding `?topic=` opens and highlights THAT entry and
//      only that one. "Only that one" is half the assertion: a list that opened
//      everything would satisfy "the arriving topic is open".
//   6. the one-shot     - the highlight is armed for exactly 2500 ms and, when
//      it fires, `helpTopic` is spent and the highlight goes - while the entry
//      STAYS OPEN. Closing the section the reader is two seconds into would be
//      a fix for a problem nobody has.
//   7. the setup guide  - the one button calls `openWizard` exactly ONCE and
//      leaves `wizardStepId` null, which is the whole resume-not-restart
//      promise the card's copy makes.
//   8. no network       - Help is content, not a rig surface. A rebuilt panel
//      that started polling would be invisible until a probe caught it.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `HelpArea` imports `help.css` - it is the single import site for the area's
// sheet, by contract, so that the cascade order cannot depend on module
// resolution order. Node has no idea what a `.css` file is, so a synchronous
// load hook answers with an empty module. This is the ONLY way to keep both
// facts true at once: the area has one style entry point, and that entry point
// is still mountable in a test. It must run BEFORE the dynamic imports below,
// which is why every import in this file is dynamic (a static `import` would
// hoist above the hook and resolve `help.css` for real).
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

// Everything answers false, so the phone layout renders. The area is the same
// at every breakpoint (the glossary's second column is a CSS decision, not a
// React one), so this is the narrower of the two and therefore the honest one.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// jsdom implements neither; the deep-link effect calls the first one.
win.HTMLElement.prototype.scrollIntoView = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// Help fetches nothing. Recording every call is how test 8 can say so.
const asked: string[] = [];
g.fetch = async (url: any, init?: { method?: string }) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { TROUBLESHOOTING } = await import("../../../../../lib/troubleshoot");
const { HELP } = await import("../../../../../help");
const { HIGHLIGHT_MS, hyphenate, termLabel } = await import("../helpModel");
const { HelpSheet } = await import("../../sheets/HelpSheet");

// ------------------------------------------------------------- timer capture
// The one-shot `clearHelpTopic()` is armed with `window.setTimeout`, so the
// window's clock is the one to take over: it makes the 2500 ms a value this
// test can ASSERT rather than a delay it has to sit through, and it keeps
// React's own scheduling (bare `setTimeout`, i.e. Node's) untouched. Installed
// after every import, so nothing armed at module scope lands in here.
interface Armed { id: number; fn: () => void; ms: number }
const armed: Armed[] = [];
let nextTimerId = 1;
win.setTimeout = ((fn: () => void, ms?: number): number => {
  const id = nextTimerId++;
  armed.push({ id, fn, ms: ms ?? 0 });
  return id;
}) as never;
win.clearTimeout = ((id?: number): void => {
  const i = armed.findIndex((a) => a.id === id);
  if (i >= 0) armed.splice(i, 1);
}) as never;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const host = win.document.getElementById("root") as any;
const q = (sel: string): any => host.querySelector(sel);
const all = (sel: string): any[] => Array.from(host.querySelectorAll(sel));
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
};

const HELP_KEY_LIST = Object.keys(HELP);

// ================================================= the sheet with no deep link
// The press counter is installed BEFORE the first render, not swapped in
// mid-test: replacing a store action while the tree is mounted is itself a
// state change, and a component that re-rendered on it would be pressing a
// different function from the one it was holding when the click was queued.
let wizardCalls = 0;
const realOpenWizard = useStore.getState().openWizard;

const root = createRoot(host);
useStore.setState({
  helpTopic: null, wizardOpen: false, wizardStepId: null,
  openWizard: () => { wizardCalls++; realOpenWizard(); },
} as never);
act(() => { root.render(createElement(HelpSheet, { params: {}, depth: 0 })); });
await settle();

test("precondition: the sheet and all three blocks rendered their markers", () => {
  assert(q('[data-testid="settings-help"]') != null,
    "no [data-testid=settings-help] - the fixture is wrong, not the component");
  assert(q('[data-testid="help-setup-guide"]') != null, "no SETUP GUIDE button");
  assert(all('[data-testid="help-problem"]').length > 0, "no COMMON PROBLEMS entries");
  assert(all('[data-testid="help-glossary"]').length > 0, "no GLOSSARY rows");
});

test("PARITY: one entry per TROUBLESHOOTING row, and every symptom is on screen", () => {
  eq(all('[data-testid="help-problem"]').length, TROUBLESHOOTING.length,
    "rendered problem entries vs TROUBLESHOOTING.length:");
  const text = String(host.textContent);
  for (const e of TROUBLESHOOTING) {
    assert(text.includes(hyphenate(e.symptom)),
      `the symptom "${e.symptom}" never reached the DOM`);
  }
});

test("PARITY: one row per HELP key, and every term is on screen", () => {
  eq(all('[data-testid="help-glossary"]').length, HELP_KEY_LIST.length,
    "rendered glossary rows vs Object.keys(HELP).length:");
  const text = String(host.textContent);
  for (const k of HELP_KEY_LIST) {
    assert(text.includes(termLabel(k)), `the glossary term "${termLabel(k)}" is missing`);
  }
});

test("the collapsed entries say how long each fix is", () => {
  const heads = all('[data-testid="help-problem"] button[aria-expanded]');
  eq(heads.length, TROUBLESHOOTING.length, "one head per entry:");
  const first = String(heads[0].textContent);
  assert(/\d+ steps?/.test(first),
    `a closed head must state its step count, got "${first}"`);
});

// Opened by HAND, before the copy sweep below, and that ordering is the point:
// a body's cause, steps and cross-links are only in the DOM while the entry is
// open, so a sweep over the all-closed screen would grade the symptom lines and
// call it a pass. `cooler-stuck` is the entry chosen because its steps carry
// both an en-dash and an em-dash at source AND it declares `seeAlso`.
const OPENED = TROUBLESHOOTING.find((e) => e.topic === "cooler-stuck")!;
test("tapping a head opens that entry, and the body carries the cross-links", () => {
  const row = q('[data-testid="help-problem"][data-topic="cooler-stuck"]');
  assert(row != null, "no cooler-stuck entry - the fixture is wrong, not the component");
  const head = row.querySelector("button[aria-expanded]");
  eq(head.getAttribute("aria-expanded"), "false", "the entry before the tap:");
  click(head);
  eq(head.getAttribute("aria-expanded"), "true", "the entry after the tap:");
  eq(all('[data-testid="help-problem"] button[aria-expanded="true"]').length, 1,
    "expanded entries after one tap:");

  const body = String(row.textContent);
  assert(body.includes(hyphenate(OPENED.cause)), "the opened entry's cause is not on screen");
  for (const s of OPENED.steps) {
    assert(body.includes(hyphenate(s)), `the step "${s}" is not on screen`);
  }
  // `TroubleshootEntry.seeAlso` is declared, populated for seven entries,
  // guarded by `lib/__tests__/troubleshoot.test.ts` - and rendered by NOTHING
  // in `views/HelpView.tsx`. The rebuild honours it; this is what says so.
  assert(/See also in the glossary: COOL TO/.test(body),
    `the entry's HELP cross-links are not rendered, got "${body}"`);
});

test("no em-dash, en-dash or smart quote survives to the DOM", () => {
  const text = String(host.textContent);
  const bad = [...new Set(text.match(/[—–‘’“”]/g) ?? [])];
  eq(bad.length, 0,
    `shared copy reached the screen un-hyphenated (${bad.map((c) => "U+" + c.codePointAt(0)!.toString(16).toUpperCase()).join(",")}); count:`);
});

test("Open the setup guide calls openWizard exactly once, and resumes", () => {
  const btn = q('[data-testid="help-setup-guide"]');
  assert(btn != null, "no SETUP GUIDE button - the fixture is wrong");
  assert(btn.disabled !== true, "the setup guide button uses the native disabled attribute");
  click(btn);
  eq(wizardCalls, 1, "openWizard calls for one press:");
  eq(useStore.getState().wizardOpen, true, "wizardOpen after the press:");
  // A null step override is what `computeWizard` resolves to the FIRST
  // INCOMPLETE step: this is the resume-not-restart promise, in one field.
  eq(useStore.getState().wizardStepId, null, "wizardStepId after the press:");
});

test("Help fetches nothing", () => {
  eq(asked.length, 0, `Help fired requests (${JSON.stringify(asked)}); count:`);
});

act(() => { root.unmount(); });

// ===================================================== the sheet with a topic
// `camera-offline` because both its cause and one of its steps carry an em-dash
// at source, so the second copy sweep below grades the seam on an OPEN body.
const TOPIC = "camera-offline";
const ENTRY = TROUBLESHOOTING.find((e) => e.topic === TOPIC)!;

const root2 = createRoot(host);
useStore.setState({
  helpTopic: null, wizardOpen: false, wizardStepId: null, openWizard: realOpenWizard,
} as never);
armed.length = 0;
act(() => { root2.render(createElement(HelpSheet, { params: { topic: TOPIC }, depth: 0 })); });
await settle();

test("the route param reaches the store through openHelp", () => {
  eq(useStore.getState().helpTopic, TOPIC, "store.helpTopic from ?topic=:");
});

test("the deep link opens and highlights that entry, and ONLY that one", () => {
  const active = all('[data-testid="help-problem"][data-active="true"]');
  eq(active.length, 1, "highlighted entries:");
  eq(active[0].getAttribute("data-topic"), TOPIC, "the highlighted entry:");

  const open = all('[data-testid="help-problem"] button[aria-expanded="true"]');
  eq(open.length, 1, "expanded entries:");
  const openTopic = open[0].closest('[data-testid="help-problem"]').getAttribute("data-topic");
  eq(openTopic, TOPIC, "the expanded entry:");

  // The body is genuinely mounted, not just marked open.
  assert(String(active[0].textContent).includes(hyphenate(ENTRY.steps[0])),
    "the opened entry's first step is not on screen");
});

test("the deep-linked body is hyphenated too", () => {
  const row = q('[data-testid="help-problem"][data-active="true"]');
  assert(row != null, "no highlighted entry to read - see the deep-link assertion above");
  const body = String(row.textContent);
  assert(body.includes(hyphenate(ENTRY.cause)), "the opened entry's cause is not on screen");
  const bad = [...new Set(body.match(/[—–‘’“”]/g) ?? [])];
  eq(bad.length, 0,
    `the deep-linked body reached the screen un-hyphenated (${bad.map((c) => "U+" + c.codePointAt(0)!.toString(16).toUpperCase()).join(",")}); count:`);
});

test("the highlight says in words why it is highlighted", () => {
  const head = q('[data-testid="help-problem"][data-active="true"] button[aria-expanded]');
  assert(head != null, "no highlighted entry to read - see the deep-link assertion above");
  assert(/opened by the error you tapped/.test(String(head.textContent)),
    `the accent tint is carrying the deep-link state alone, got "${String(head.textContent)}"`);
});

test("the one-shot clear is armed for exactly 2500 ms", () => {
  eq(armed.length, 1, `timers armed on the window clock (${JSON.stringify(armed.map((a) => a.ms))}); count:`);
  eq(armed[0].ms, HIGHLIGHT_MS, "the highlight delay:");
  eq(HIGHLIGHT_MS, 2500, "HIGHLIGHT_MS, verbatim from views/HelpView.tsx:60:");
});

// Fire it, exactly as the browser would. Guarded, because a rebuild that armed
// NO timer would otherwise throw here, outside any `test()`, and the file would
// print no tally at all - which the runner scores as "cannot be scored" rather
// than as the wrong answer it is. A failure has to explain itself.
const fire = armed[0] ?? null;
if (fire) {
  act(() => { fire.fn(); });
  await settle();
}

test("when it fires the topic is spent and the highlight goes", () => {
  assert(fire != null, "no one-shot was armed, so nothing could fire it");
  eq(useStore.getState().helpTopic, null, "store.helpTopic after the one-shot:");
  eq(all('[data-testid="help-problem"][data-active="true"]').length, 0,
    "still-highlighted entries after the one-shot:");
});

test("but the entry the reader is in stays open", () => {
  const open = all('[data-testid="help-problem"] button[aria-expanded="true"]');
  eq(open.length, 1, "expanded entries after the one-shot:");
  eq(open[0].closest('[data-testid="help-problem"]').getAttribute("data-topic"), TOPIC,
    "the entry that is still open:");
});

test("and it does not re-arm itself", () => {
  eq(armed.filter((a) => a.id !== fire.id).length, 0,
    "extra timers armed after the one-shot fired; count:");
});

act(() => { root2.unmount(); });

// --------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`helpDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
