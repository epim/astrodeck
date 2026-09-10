// planAutomationDom.test.tsx - the plan editor's automation, instructions and
// sessions sections, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/plan/automation/__tests__/planAutomationDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `npx tsc --noEmit`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE PARITY TEST IS THE POINT. Twenty settings decide what an unattended
//      rig does at 3am. A rebuild that drops one produces a screen that looks
//      finished and a night that behaves differently, and no amount of "it
//      renders" catches that. So the set of accessible names the section really
//      renders is compared against `AUTOMATION_LABELS`, which was lifted
//      verbatim from `views/SequenceView.tsx`. A dropped control is a red suite.
//   2. ZERO IS A SETTING, NOT AN ABSENCE. `Number("") === 0` is the defect the
//      `NumberField` primitive exists to close, and the fields here are where it
//      bites: a blank "refocus every N frames" read as 0 would silently disarm
//      the refocus. The test proves a real 0 commits AND that its "0 = ..." line
//      says what the 0 means.
//   3. ONE LOCK NOTE, NOT TWENTY. A viewer must see the same screen an operator
//      does, with one sentence saying why nothing writes - not twenty repeats of
//      it, and not a hidden panel.
//   4. THE FROZEN-COPY NOTE. While a sequence runs, every control here still
//      moves and NONE of it reaches that run: the engine executes the copy of
//      the plan it took at Run. That note is the whole honesty of the panel.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The three area roots import their own `<area>.css` (wave R7 section 1). Node
// has no idea what a `.css` file is, so a load hook answers with an empty
// module - the same hook `shellDom.test.tsx` installs for `next.css`.
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "FocusEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// -------------------------------------------------------------- the fake rig
const SESSION_ROW = {
  id: "s1", name: "NGC 7331 Ha", status: "dormant",
  created_ts: 1_757_000_000, updated_ts: 1_757_090_000,
  nights: 2, accepted: 31, total: 60, auto_resume: false,
};

const SESSION = {
  id: "s1", schema_version: 1, name: "NGC 7331 Ha",
  created_ts: SESSION_ROW.created_ts, updated_ts: SESSION_ROW.updated_ts,
  status: "dormant", nights: ["2026-09-08", "2026-09-09"], auto_resume: false,
  plan: {
    name: "NGC 7331 Ha",
    targets: [{
      id: "t1", name: "NGC 7331", ra_hours: 22.6, dec_deg: 34.4,
      center: true, autofocus_first: true, calibration: false,
      steps: [{ id: "st1", filter: "Ha", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 60, frame_type: "Light" }],
    }],
  },
  frames: [
    { id: "f1", ts: 5, night: "2026-09-09", target_id: "t1", step_id: "st1", thumb: null, metrics: {}, auto_accepted: true, override: null },
  ],
};

const asked: { url: string; method: string }[] = [];

g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const json = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK", json: async () => data,
  });
  if (url === "/api/sessions") return json({ sessions: [SESSION_ROW] });
  if (/^\/api\/sessions\/[^/]+$/.test(url)) return json(SESSION);
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }) };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { AUTOMATION_LABELS, AUTOMATION_GROUPS, groupSummary, runCopyNote } =
  await import("../automationModel");
const { PlanAutomationSection } = await import("../index");
const { PlanInstructionsSection } = await import("../../instructions/index");
// Imported through the area `index.ts` on purpose: that file is the contract
// T-R7-5 composes against, so a rename that breaks composition breaks here too.
const { PlanSessionsSection } = await import("../../sessions/index");
const { NO_SESSIONS } = await import("../../sessions/PlanSessionsSection");
const { instructionsSummary, NO_INSTRUCTIONS_SUMMARY, hyphenate } =
  await import("../../instructions/instructionsModel");

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
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

/** `plan.instructions` is optional on `SequencePlan` (absent === no rules), so
 *  the assertions below count through this rather than through a non-null
 *  assertion that would hide an accidental undefined. */
const ruleCount = (): number => useStore.getState().plan.instructions?.length ?? -1;

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const all = (sel: string): any[] => Array.from(container.querySelectorAll(sel));
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** Type into an input and blur it - `NumberField` commits at blur, never on
 *  keystroke, which is the whole point of its raw-string draft. */
const typeAndBlur = (el: any, text: string) => {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, text);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  // React 18 delegates `onBlur` from the bubbling `focusout`, not from the
  // non-bubbling `blur`, so a plain blur event never reaches the component.
  act(() => { el.dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
};

const PLAN = {
  name: "NGC 7331 Ha",
  targets: [{
    id: "t1", name: "NGC 7331", ra_hours: 22.6, dec_deg: 34.4,
    center: true, autofocus_first: true, calibration: false,
    steps: [{ id: "st1", filter: "Ha", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 60, frame_type: "Light" }],
  }, {
    id: "t2", name: "NGC 6946", ra_hours: 20.6, dec_deg: 60.1,
    center: true, autofocus_first: false, calibration: false,
    steps: [{ id: "st2", filter: "L", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 40, frame_type: "Light" }],
  }],
  guide: true,
  dither_every: 5,
  dither_pixels: 3,
  autofocus_every: 20,
  cool_to: -10,
  cool_timeout_s: null,
  apply_filter_offsets: true,
  refocus_on_temp_delta_c: 1.5,
  meridian_flip: true,
  recover_guiding: true,
  hfr_reject_factor: 1.6,
  park_when_done: true,
  warm_cooler_when_done: true,
  safety_check: true,
  meridian_flip_warn_min: 15,
  count_mode: "attempts",
  min_stars: 25,
  max_guide_rms: 1.2,
  max_consecutive_rejects: 3,
  max_consecutive_rejects_night: 8,
  max_eccentricity: 0.65,
  instructions: [],
};

function seed(role: string, caps: string[], over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      plan: JSON.parse(JSON.stringify(PLAN)),
      sequence: { state: "idle" } as never,
      safety: { connected: true, reading: null, streak: 0 } as never,
      weather: null,
      config: null,
      status: null, equipConnected: true, wsPhase: "up",
      ...over,
    } as never);
  });
}

async function mount(node: any, props: any = {}): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(node, { lockedReason: null, onExplain: () => {}, ...props })); });
  await settle();
}

/** Every group is a controlled `Disclosure` and only one is open at a time, so
 *  the labels are collected group by group. */
function collectAutomationLabels(): string[] {
  const out: string[] = [];
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  for (const head of heads) {
    click(head);
    for (const el of all('[data-testid^="plan-guard-"]')) {
      out.push(el.getAttribute("aria-label") ?? "");
    }
  }
  return out;
}

// ================================================ 1. it rendered, with groups

seed("admin", ["view.status", "control.mount", "control.capture", "config.backend"]);
await mount(PlanAutomationSection);

test("precondition: the automation card and all six groups rendered", () => {
  assert(tid("plan-automation") != null,
    "no plan-automation marker - the fixture is wrong, not the screen");
  const groups = all('[data-testid="plan-automation-group"]');
  eq(groups.length, AUTOMATION_GROUPS.length,
    "every group in the model must reach the DOM");
  assert(/GUIDING AND DITHER/.test(tid("plan-automation").textContent),
    "the first group's eyebrow is missing");
});

test("a closed group states its current effect, not its name alone", () => {
  const txt = tid("plan-automation").textContent as string;
  assert(/guiding, dither every 5 frames at 3 px/.test(txt),
    `the guiding summary must state the setting, got: ${txt.slice(0, 240)}`);
  assert(/cools to -10/.test(txt), "the endings summary must state the setpoint");
  assert(/counting attempts/.test(txt), "the quality summary must state the count mode");
});

// ================================================== 2. THE PARITY TEST

test("PARITY: the rendered controls are exactly SequenceView's twenty", () => {
  const rendered = collectAutomationLabels();
  eq(rendered.length, AUTOMATION_LABELS.length,
    `twenty controls must render, one per legacy setting.\n  rendered: ${rendered.join(" | ")}`);
  const want = [...AUTOMATION_LABELS].sort();
  const got = [...rendered].sort();
  for (let i = 0; i < want.length; i++) {
    eq(got[i], want[i], "a legacy automation setting is missing or renamed");
  }
});

test("every control carries a plan-guard marker naming the field it writes", () => {
  const ids = new Set<string>();
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  for (const head of heads) {
    click(head);
    for (const el of all('[data-testid^="plan-guard-"]')) {
      ids.add((el.getAttribute("data-testid") as string).replace("plan-guard-", ""));
    }
  }
  for (const key of ["guide", "safety_check", "cool_to", "count_mode", "min_stars",
    "max_consecutive_rejects_night"]) {
    assert(ids.has(key), `plan-guard-${key} never rendered`);
  }
});

// ============================================= 3. a toggle writes the draft

test("toggling Guide during sequence writes the plan draft", () => {
  click(all('[data-testid="plan-automation-group"] button[aria-expanded]')[0]);
  const sw = tid("plan-guard-guide");
  assert(sw != null, "the guide switch never rendered");
  eq(sw.getAttribute("aria-checked"), "true", "the fixture guides");
  click(sw);
  eq(useStore.getState().plan.guide, false, "the switch must write plan.guide");
  click(tid("plan-guard-guide"));
  eq(useStore.getState().plan.guide, true, "and write it back");
});

test("the count-mode switch writes the string the engine reads, not a boolean", () => {
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  click(heads[4]);                       // FRAME QUALITY GATES
  const sw = tid("plan-guard-count_mode");
  assert(sw != null, "the count-mode switch never rendered");
  click(sw);
  eq(useStore.getState().plan.count_mode, "accepted",
    "count_mode is a string enum; a true/false here would be silently ignored");
});

// ================================== 4. zero is a setting, blank is rejected

test("a 0 in Refocus every N frames commits 0 and says what the 0 means", () => {
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  click(heads[1]);                       // FOCUS
  const input = tid("plan-guard-autofocus_every");
  assert(input != null, "the refocus field never rendered");
  typeAndBlur(input, "0");
  eq(useStore.getState().plan.autofocus_every, 0,
    "0 is a real setting (no refocus by frame count) and must commit");
  const row = input.closest(".nx-field");
  assert(/0 = no frame-count refocus/.test(row.textContent),
    `the zero line must say what off means, got: ${row.textContent}`);
});

test("blanking a numeric commits nothing and restores the last value", () => {
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  click(heads[4]);
  const input = tid("plan-guard-min_stars");
  eq(useStore.getState().plan.min_stars, 25, "the fixture floor");
  typeAndBlur(input, "");
  eq(useStore.getState().plan.min_stars, 25,
    'Number("") is 0 - a blank field must never be read as "no star floor"');
  eq(input.value, "25", "and the field restores what it had");
});

// ================================= 5. cooling: blank is off, 0 is a setpoint

test("cooling can be turned off and armed again without 0 meaning off", () => {
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  click(heads[3]);                       // COOLING AND END OF NIGHT
  assert(tid("plan-cool-off") != null, "an armed cooler needs a way to turn it off");
  click(tid("plan-cool-off"));
  eq(useStore.getState().plan.cool_to, null, "off is null, never 0");
  assert(tid("plan-cool-arm") != null, "and a way back");
  click(tid("plan-cool-arm"));
  eq(useStore.getState().plan.cool_to, 0, "0 degrees is a real setpoint");
});

// ========================================== 6. the frozen-copy note, running

test("a running sequence says these settings reach the NEXT run", () => {
  act(() => {
    useStore.setState({
      sequence: { state: "running", plan_name: "NGC 7331 Ha" } as never,
    } as never);
  });
  const note = tid("plan-automation-run-note");
  assert(note != null, "the frozen-copy note never rendered while running");
  eq(note.textContent, runCopyNote("NGC 7331 Ha"),
    "the note must name the running plan and say the run cannot see these edits");
  assert(!/—/.test(note.textContent), "no em-dashes in UI copy");
  act(() => { useStore.setState({ sequence: { state: "idle" } as never } as never); });
});

// ============================================ 7. a viewer: locked, once

await testAsync("a viewer sees every control locked and ONE lock note", async () => {
  const before = asked.length;
  seed("viewer", ["view.status"]);
  await mount(PlanAutomationSection, { lockedReason: "needs operator or admin access" });

  // Counted with EVERY group open: a collapsed `Disclosure` does not mount its
  // children, so counting the notes on a closed column would pass over a
  // section that repeats the sentence on all twenty rows.
  const heads = all('[data-testid="plan-automation-group"] button[aria-expanded]');
  let mostNotes = 0;
  for (const head of heads) {
    click(head);
    mostNotes = Math.max(mostNotes, all(".nx-locknote").length);
  }
  eq(mostNotes, 1, "one sentence for the whole column, not one per control");
  const note = all(".nx-locknote")[0];
  assert(/Read-only - needs operator or admin access/.test(note.textContent),
    `the note must carry the reason, got "${note.textContent}"`);

  click(heads[0]);
  const sw = tid("plan-guard-guide");
  eq(sw.getAttribute("aria-disabled"), "true", "a locked switch is aria-disabled");
  assert(sw.getAttribute("disabled") == null,
    "and never the native disabled attribute - the press has to be able to explain itself");
  const wasGuide = useStore.getState().plan.guide;
  click(sw);
  eq(useStore.getState().plan.guide, wasGuide, "a locked press must not write the draft");
  eq(asked.length, before, "and must not reach the network");
});

// ================================================ 8. instructions section

await testAsync("the instructions section renders, adds a rule and locks honestly", async () => {
  seed("admin", ["view.status", "control.mount"]);
  await mount(PlanInstructionsSection);
  assert(tid("plan-instructions") != null, "no plan-instructions marker");
  assert((tid("plan-instructions").textContent as string).includes(NO_INSTRUCTIONS_SUMMARY),
    "an empty rule list says so on the closed row");

  click(tid("plan-instructions-rules").querySelector("button[aria-expanded]"));
  await settle();
  click(tid("plan-instructions-add"));
  await settle();
  eq(ruleCount(), 1, "add rule must write the draft");
  assert(tid("plan-rule-0") != null, "and the rule row must render");
  assert(tid("plan-rule-trigger-0") != null, "with a trigger picker");
  assert(tid("plan-rule-action-0") != null, "and an action picker");

  // A viewer sees the same rule, locked, and the picker refuses the change.
  await mount(PlanInstructionsSection, { lockedReason: "needs operator or admin access" });
  click(tid("plan-instructions-rules").querySelector("button[aria-expanded]"));
  await settle();
  const sel = tid("plan-rule-trigger-0");
  eq(sel.getAttribute("aria-disabled"), "true", "a locked picker is aria-disabled");
  assert(sel.getAttribute("disabled") == null, "never the native disabled attribute");
  const add = tid("plan-instructions-add");
  eq(add.getAttribute("aria-disabled"), "true", "and add rule is locked too");
  const count = ruleCount();
  click(add);
  eq(ruleCount(), count,
    "a locked add must not write the draft");
});

// =================================================== 9. sessions section

await testAsync("the sessions ledger lists the rig's sessions and locks its verbs", async () => {
  seed("admin", ["view.status", "control.mount"]);
  await mount(PlanSessionsSection);
  assert(tid("plan-sessions") != null, "no plan-sessions marker");
  assert(tid("plan-session-s1") != null, "the session row never rendered");
  const row = tid("plan-session-s1").textContent as string;
  assert(/NGC 7331 Ha/.test(row), "the row carries the session name");
  assert(/31\/60 accepted over 2 nights/.test(row), `and its tally, got: ${row.slice(0, 200)}`);
  assert(tid("plan-session-delete-s1") != null, "DELETE must be present");

  const before = asked.length;
  await mount(PlanSessionsSection, { lockedReason: "needs operator or admin access" });
  await settle();
  for (const verb of ["resume", "update", "abandon", "delete"]) {
    const el = tid(`plan-session-${verb}-s1`);
    assert(el != null, `the ${verb} verb must still be rendered for a viewer`);
    eq(el.getAttribute("aria-disabled"), "true", `${verb} must be honest-disabled`);
  }
  // REVIEW is a read and stays live, which is correct.
  assert(tid("plan-session-review-s1").getAttribute("aria-disabled") == null,
    "reading a session's frames needs no capability");
  click(tid("plan-session-delete-s1"));
  await settle();
  eq(useStore.getState().confirm, null, "a locked verb must not even raise a confirm");
  eq(asked.filter((a) => a.method === "DELETE").length, 0, "and must not reach the network");
  assert(asked.length >= before, "the ledger still refreshed");
});

// ============================================= 10. the pure model, directly

test("groupSummary states the OFF states in words, not zeros", () => {
  const off = {
    ...PLAN, guide: false, dither_every: 0, autofocus_every: 0, cool_to: null,
    park_when_done: false, warm_cooler_when_done: false,
  } as any;
  const eff = {
    dither_pixels: 3, recover_guiding: false, apply_filter_offsets: false,
    refocus_on_temp_delta_c: 0, meridian_flip_warn_min: 15, hfr_reject_factor: 0,
    min_stars: 0, max_guide_rms: 0, max_eccentricity: 0,
    max_consecutive_rejects: 0, max_consecutive_rejects_night: 0, cool_timeout_s: 600,
  };
  eq(groupSummary("guiding", off, eff), "unguided, no dither", "guiding, all off");
  assert(/no cooling/.test(groupSummary("endings", off, eff)), "cooling off reads as off");
  assert(/no quality gate armed/.test(groupSummary("quality", off, eff)),
    "no gate armed has to say so - an empty gate list is not the same as a strict one");
  eq(groupSummary("guards", off, eff),
    "never moves on by itself, never ends the night by itself",
    "the reject guards borrow QuotaRows' own vocabulary");
});

test("instructionsSummary keeps one whole rule and counts the rest", () => {
  eq(instructionsSummary([]), NO_INSTRUCTIONS_SUMMARY, "empty");
  eq(instructionsSummary(["a"]), "a", "one rule is shown in full");
  eq(instructionsSummary(["a", "b", "c"]), "a  ·  +2 more",
    "never a truncated join - the count has to be visible");
});

test("hyphenate takes em-dashes and smart quotes out of shared copy", () => {
  eq(hyphenate("ready to fire — a real run"), "ready to fire - a real run", "em dash");
  eq(hyphenate("“Once” isn’t looping"), '"Once" isn\'t looping', "quotes");
});

test("NO_SESSIONS says what would create one", () => {
  assert(/ACCEPTED frames/.test(NO_SESSIONS),
    "the empty state has to name the setting that opens a session");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`planAutomationDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
