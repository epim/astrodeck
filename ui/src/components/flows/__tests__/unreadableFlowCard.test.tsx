// unreadableFlowCard.test.tsx - the classic library draws a flow this build
// cannot open as a card that SAYS SO, once, and never opens it (#153; spec
// 2026-09-23 section 3.6; carry-over 6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/unreadableFlowCard.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// `GET /api/flows` lists every file the store could not read as a row carrying
// `unreadable`: "saved by a newer AstroDeck (schema 4); update to open it", or
// "unreadable: <reason>" (server `FlowStore._row`). The old store skipped such
// files, which made a damaged flow look deleted. The rows fix that only if the
// card reads as what it is:
//
//   1. THE REASON IS ON THE CARD as text, not behind a hover or a press. It is
//      the whole answer to "where did my flow go?".
//   2. IT IS ON THE CARD ONCE (carry-over 6). The first cut also carried it as
//      the card's `title` and raised it as a toast on a press: the same
//      sentence three times, two of them repeating a line already on screen.
//      ONCE IS ALSO WHAT A SCREEN READER HEARS (#206). One copy in the DOM was
//      not enough: the card is a <button> with no aria-label, so its
//      accessible name comes from its content, reason line included, and the
//      `aria-describedby` S1-09 pointed at that same line made a screen reader
//      say the sentence in the name and again in the description. The DOM
//      count below graded a proxy; the name-and-description case grades what
//      is heard.
//   3. A PRESS DOES NOTHING. It never calls `flowsOpen`: every route answers
//      404 for these ids, and `flowsOpen` writes that 404 into `libraryError` -
//      so opening one would replace the card that explains itself with "Could
//      not read the flow library", a sentence about the whole library that is
//      false. And it raises no toast (carry-over 6, above).
//   4. THE CARD STAYS REACHABLE: honest-disabled with `aria-disabled`, never
//      the native `disabled`, which would drop it out of the tab order and so
//      out of reach of the one sentence explaining it.
//   5. A READABLE CARD IS UNCHANGED (the controls): it opens on a press, raises
//      no toast, and carries no lock and no description.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of FlowLibraryCard.tsx
// (and, for "restore the toast", of FlowLibrary.tsx too; for the name helper's
// own known positives, of this file).

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const { useStore } = await import("../../../store");
const { api } = await import("../../../api");
const { FlowLibrary } = await import("../FlowLibrary");

// ------------------------------------------------------------------- fixture
const NEWER = "saved by a newer AstroDeck (schema 4); update to open it";
const BROKEN = "unreadable: not valid JSON (line 1, column 2)";

const CARDS = [
  {
    id: "good", name: "M31 LRGB", folder: "My flows",
    tagline: "12 subs each of L, R, G, B", readonly: false,
    stages: 7, wires: 6, last_run: null, last_result: "", updated_ts: 1,
  },
  // Server `FlowStore._row`: the card shape, read-only, no tagline, no run.
  {
    id: "future", name: "Mosaic NGC 7000", folder: "My flows", tagline: "",
    readonly: true, stages: 9, wires: 8, last_run: null, last_result: "",
    updated_ts: 2, unreadable: NEWER,
  },
  {
    id: "broken", name: "broken", folder: "My flows", tagline: "",
    readonly: true, stages: 0, wires: 0, last_run: null, last_result: "",
    updated_ts: 3, unreadable: BROKEN,
  },
];
const FOLDERS = [
  { name: "My flows", count: 3, readonly: false },
  { name: "Examples", count: 0, readonly: true },
];

/** Every `flowsOpen` call the library made, by id. The real action is swapped
 *  out so a press is observed, not performed. */
let opened: string[] = [];

function setUp() {
  unmount();
  opened = [];
  const st = useStore.getState() as any;
  useStore.setState({
    flows: {
      ...st.flows,
      cards: CARDS,
      folders: FOLDERS,
      libraryLoaded: true,
      libraryError: null,
      ui: { ...st.flows.ui, query: "", folderChip: "all", highlightId: null },
    },
    toasts: [],
    authGate: "open",
    principal: { role: "admin", email: "t@t", caps: ["control.capture"] },
    flowsOpen: async (id: string) => { opened.push(id); },
  } as any);
  // The screen fetches on mount; the fixture above is the answer.
  (api as any).get = async (path: string) => (path.includes("folders") ? FOLDERS : CARDS);
}

let root: ReturnType<typeof createRoot> | null = null;
function unmount() {
  if (!root) return;
  const r = root;
  root = null;
  act(() => { r.unmount(); });
}
async function mount() {
  const host = document.getElementById("root")!;
  unmount();
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => { root!.render(React.createElement(FlowLibrary)); });
  return host;
}

const cardEl = (id: string) =>
  document.querySelector(`[data-flow-id="${id}"]`) as HTMLElement | null;
async function press(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
const toastTitles = () => ((useStore.getState() as any).toasts as any[]).map((t) => String(t.title));

/** Non-overlapping occurrences of `needle` in `hay`. */
function occurrences(hay: string, needle: string): number {
  let n = 0;
  for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + needle.length)) n++;
  return n;
}

/** Every place the card carries `reason`: its text, plus the value of every
 *  attribute on the card and on everything inside it. A `title`, an
 *  `aria-label`, an `aria-description` or a `data-*` copy all count - each is
 *  a second copy of a sentence already printed on the card. */
function copiesOf(card: HTMLElement, reason: string): { text: number; attrs: string[] } {
  const attrs: string[] = [];
  for (const el of [card, ...Array.from(card.querySelectorAll("*"))]) {
    for (const a of Array.from(el.attributes)) {
      for (let k = occurrences(a.value, reason); k > 0; k--) attrs.push(`${el.tagName.toLowerCase()}[${a.name}]`);
    }
  }
  return { text: occurrences(card.textContent ?? "", reason), attrs };
}

// ------------------------------------------------ accessible name, minimally
// What a screen reader announces for a control is its accessible NAME and its
// DESCRIPTION (W3C accname 1.2). This is a minimal computation of both, enough
// for a <button> built out of spans, and not a general implementation:
//
//   name         aria-labelledby, then aria-label, else the text of the
//                content - where a descendant's own aria-label stands in for
//                that descendant's content (the status LED is a role="img"
//                with a label) and an aria-hidden subtree contributes nothing;
//   description  the text of each element aria-describedby names.
//
// An element named by id counts even when it is itself aria-hidden: that is
// accname's rule, and it is how option (a) of #206 (hide the line from the
// name, keep it in the description) would work, so the helper must not
// mistake that option for a missing reason.
//
// This is a model of the rules, not a screen reader. LISTENING TO THE CARD ON
// NVDA AND ON VOICEOVER IS THE OPERATOR'S STEP (#206: "not yet confirmed on a
// real screen reader"); what this file can do is hold the wiring to the rules.
function contentText(el: Element): string {
  let out = "";
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === 3) { out += n.textContent ?? ""; continue; }
    if (n.nodeType !== 1) continue;
    const child = n as Element;
    if (child.getAttribute("aria-hidden") === "true") continue;
    const label = child.getAttribute("aria-label");
    // Spaces around every element: the card's lines are separate spans, and a
    // name run together ("Mosaic NGC 7000saved by...") would still contain
    // the reason but would not be what is read.
    out += ` ${label && label.trim() ? label : contentText(child)} `;
  }
  return out;
}
const squash = (s: string) => s.replace(/\s+/g, " ").trim();
function textOfIds(ids: string): string {
  return squash(ids.split(/\s+/).filter(Boolean)
    .map((id) => { const t = document.getElementById(id); return t ? contentText(t) : ""; })
    .join(" "));
}
function accName(el: Element): string {
  const by = el.getAttribute("aria-labelledby");
  if (by && by.trim()) return textOfIds(by);
  const label = el.getAttribute("aria-label");
  if (label && label.trim()) return squash(label);
  return squash(contentText(el));
}
function accDescription(el: Element): string {
  const by = el.getAttribute("aria-describedby");
  return by && by.trim() ? textOfIds(by) : "";
}

// --------------------------------------------------------------------- tests

// The helper's KNOWN POSITIVES, one per rule the heard-once case leans on. If
// the helper read plain textContent it would pass that case for the wrong
// reason; each line below would catch it.
//
// MUTANT "name ignores aria-label" (accName's `if (label && label.trim())
// return squash(label);` line deleted - a mutant of THIS file, run from a
// backup of it). Observed, 8/9:
//   x precondition: the name helper follows the rules it models: an aria-label
//     button's name is not its label
//     expected "Open M31 on the flows canvas"
//     got      "M31 7 stages"
//
// MUTANT "name reads aria-hidden text" (contentText's aria-hidden `continue`
// deleted). Observed, 8/9:
//   x precondition: the name helper follows the rules it models: an aria-hidden
//     glyph reached the name
//     expected "NEW FLOW"
//     got      "+ NEW FLOW"
await test("precondition: the name helper follows the rules it models", async () => {
  const kp = document.createElement("div");
  kp.innerHTML = `
    <button id="kp-label" aria-label="Open M31 on the flows canvas"><span>M31</span><span>7 stages</span></button>
    <button id="kp-content"><span aria-hidden="true">+</span><span>NEW FLOW</span></button>
    <button id="kp-led"><span role="img" aria-label="CANNOT OPEN"></span>x</button>
    <button id="kp-by" aria-labelledby="kp-l" aria-describedby="kp-d">x</button>
    <span id="kp-l">Label from elsewhere</span>
    <span id="kp-d" aria-hidden="true">Why it is locked</span>`;
  document.body.appendChild(kp);
  try {
    const $ = (id: string) => document.getElementById(id)!;
    eq(accName($("kp-label")), "Open M31 on the flows canvas", "an aria-label button's name is not its label");
    eq(accName($("kp-content")), "NEW FLOW", "an aria-hidden glyph reached the name");
    eq(accName($("kp-led")), "CANNOT OPEN x", "a labelled descendant did not stand in for its content");
    eq(accName($("kp-by")), "Label from elsewhere", "aria-labelledby did not win over the content");
    eq(accDescription($("kp-by")), "Why it is locked",
      "a directly referenced, aria-hidden line did not count as the description");
    eq(accDescription($("kp-label")), "", "a button with no aria-describedby has a description");
  } finally {
    kp.remove();
  }
});

await test("precondition: all three cards render, the unreadable rows included", async () => {
  setUp();
  await mount();
  for (const id of ["good", "future", "broken"]) assert(cardEl(id), `card ${id} never rendered`);
});

// MUTANT "reason not drawn" (FlowLibraryCard's `{unreadable && (` reason line
// made `{false && (`). Observed, 6/9 (it also kills the copies case and the
// heard-once case below):
//   x an unreadable card shows its reason as text on the card: the reason is not
//     on the card - an operator sees a flow and no word about why it will not open
//   x the reason occurs exactly once across the card's text and attribute values:
//     future: the reason must be carried exactly once (text 0, attributes []) - a
//     repeat of a line already on the card is slop
//     expected 1
//     got      0
//   x a screen reader meets the reason once: in the card's name or its
//     description, not both: future: a screen reader meets the reason 0 time(s)
//     in the name and 0 in the description
//     name        "Mosaic NGC 7000 9 stages · 8 wires · never run CANNOT OPEN CANNOT OPEN"
//     description ""
//     expected 1
//     got      0
//
// MUTANT "no lock attribute" (the `aria-disabled={unreadable ? true :
// undefined}` line deleted). Observed, 8/9:
//   x an unreadable card shows its reason as text on the card: the card must say
//     it cannot act (honest-disabled, never `disabled`)
//     expected "true"
//     got      null
//
// MUTANT "status word unchanged" (`const status = cardStatus(card.last_result);`
// for every card). Observed, 8/9:
//   x an unreadable card shows its reason as text on the card: the status word
//     says NEVER RUN, which reads as a flow that could be run
await test("an unreadable card shows its reason as text on the card", async () => {
  setUp();
  await mount();
  const future = cardEl("future")!;
  assert((future.textContent ?? "").includes(NEWER),
    "the reason is not on the card - an operator sees a flow and no word about why it will not open");
  assert((cardEl("broken")!.textContent ?? "").includes(BROKEN),
    "the second unreadable card does not carry its own reason");
  eq(future.getAttribute("aria-disabled"), "true",
    "the card must say it cannot act (honest-disabled, never `disabled`)");
  assert(!/NEVER RUN/.test(future.textContent ?? ""),
    "the status word says NEVER RUN, which reads as a flow that could be run");
  assert(/CANNOT OPEN/.test(future.textContent ?? ""), "the status word does not say it cannot open");
});

// Carry-over 6, and the reason the assertion that used to sit here
// (`title` equal to the reason: "the hover names the same reason") is gone on
// purpose: the hover was a second copy of the printed line, and a copy of what
// is already on screen tells the operator nothing new.
//
// MUTANT "restore title" (`title={unreadable ?? undefined}` put back on the
// card's <button>). Observed, 8/9:
//   x the reason occurs exactly once across the card's text and attribute values:
//     future: the reason must be carried exactly once (text 1, attributes
//     ["button[title]"]) - a repeat of a line already on the card is slop
//     expected 1
//     got      2
await test("the reason occurs exactly once across the card's text and attribute values", async () => {
  setUp();
  await mount();
  for (const [id, reason] of [["future", NEWER], ["broken", BROKEN]] as const) {
    const c = copiesOf(cardEl(id)!, reason);
    eq(c.text + c.attrs.length, 1,
      `${id}: the reason must be carried exactly once (text ${c.text}, attributes ${JSON.stringify(c.attrs)})`
      + " - a repeat of a line already on the card is slop");
    eq(c.text, 1, `${id}: the one copy must be the printed line, not an attribute`);
  }
});

// MUTANT "restore the toast" (FlowLibraryCard regains its `onExplain` prop and
// calls `onExplain?.(unreadable)` on an unreadable press; FlowLibrary regains
// `explainCard` = `enqueueToast({ level: "warning", title: reason })` and passes
// it as `onExplain` - the first cut's toast path, and nothing else of it).
// Observed, 7/8, on the eight-case file before #206. That mutant was NOT re-run
// with #206, because it edits FlowLibrary.tsx, which is outside that task's
// files.
//   x pressing an unreadable card never calls flowsOpen and raises no toast: the
//     press repeated the card's own printed reason as a toast
//     expected "[]"
//     got      "[\"saved by a newer AstroDeck (schema 4); update to open
//     it\",\"unreadable: not valid JSON (line 1, column 2)\"]"
//
// MUTANT "card toasts its reason" (the same toast, confined to
// FlowLibraryCard.tsx: the unreadable branch of its onClick calls
// `useStore.getState().enqueueToast({ level: "warning", title: unreadable })`).
// This is the nine-case file after #206. Observed, 8/9, with the same failure
// as quoted above.
//
// MUTANT "open every card" (FlowLibraryCard's onClick made
// `() => onOpen(card.id)` whatever the card). Observed, 8/9:
//   x pressing an unreadable card never calls flowsOpen and raises no toast: the
//     press opened a flow every route answers 404 for
//     expected ""
//     got      "future,broken"
await test("pressing an unreadable card never calls flowsOpen and raises no toast", async () => {
  setUp();
  await mount();
  await press(cardEl("future")!);
  await press(cardEl("broken")!);
  eq(opened.join(","), "", "the press opened a flow every route answers 404 for");
  // REWRITTEN DELIBERATELY (carry-over 6). This used to require the press to
  // toast each reason ("the press was silent"). The reason is printed on the
  // card the operator just pressed, so the toast said nothing they could not
  // already read; the press is now inert, and a toast is the regression.
  eq(JSON.stringify(toastTitles()), "[]", "the press repeated the card's own printed reason as a toast");
});

// HEARD ONCE (#206). The reason must occur in EXACTLY ONE of what a screen
// reader announces for the card - its name or its description - and once
// there. This deliberately does not say WHICH: #206 lists three shapes that
// each pass it ((a) aria-hidden on the line plus aria-describedby, (b) an
// explicit aria-label plus aria-describedby, (c) no description, the reason
// heard in the name from content), and the ruling between them is not this
// test's to make. The card's own name must still be in the name, so no shape
// can pass by hiding the whole card.
//
// MUTANT "keep aria-describedby" (S1-09's wiring put back: `const reasonId =
// useId();`, `aria-describedby={unreadable ? reasonId : undefined}` on the
// <button> and `id={reasonId}` on the reason line - the code before #206).
// Observed, 8/9:
//   x a screen reader meets the reason once: in the card's name or its
//     description, not both: future: a screen reader meets the reason 1 time(s)
//     in the name and 1 in the description
//     name        "Mosaic NGC 7000 saved by a newer AstroDeck (schema 4); update to open it 9 stages · 8 wires · never run CANNOT OPEN CANNOT OPEN"
//     description "saved by a newer AstroDeck (schema 4); update to open it"
//     expected 1
//     got      2
//
// NOT LOCKED TO AN OPTION, shown by running the other two shapes of #206 from
// the same backup; both left all 9/9 cases green:
//   (a) the "keep aria-describedby" wiring plus `aria-hidden="true"` on the
//       reason line (the name loses it, the description keeps it);
//   (b) the same wiring plus `aria-label={`${card.name}, ${status.text}`}` on
//       an unreadable card (an explicit name that leaves the reason out).
// So a ruling for (a) or (b) changes FlowLibraryCard.tsx, not this case.
await test("a screen reader meets the reason once: in the card's name or its description, not both", async () => {
  setUp();
  await mount();
  for (const [id, reason, cardName] of [
    ["future", NEWER, "Mosaic NGC 7000"], ["broken", BROKEN, "broken"],
  ] as const) {
    const card = cardEl(id)!;
    const name = accName(card);
    const desc = accDescription(card);
    assert(occurrences(name, cardName) >= 1,
      `${id}: the card's name no longer says which flow it is: ${JSON.stringify(name)}`);
    const inName = occurrences(name, reason);
    const inDesc = occurrences(desc, reason);
    eq(inName + inDesc, 1,
      `${id}: a screen reader meets the reason ${inName} time(s) in the name and ${inDesc} in the`
      + ` description\n  name        ${JSON.stringify(name)}\n  description ${JSON.stringify(desc)}`);
  }
});

// Two ways to lose the card, two assertions. `focus()` catches the native
// `disabled` (jsdom, like a browser, will not focus a disabled control); it
// does NOT catch `tabIndex={-1}`, which still takes programmatic focus but is
// skipped by Tab - hence the separate tabIndex check.
//
// MUTANT "native disabled" (`aria-disabled={unreadable ? true : undefined}`
// made `disabled={unreadable ? true : undefined}`). Observed, 7/9:
//   x an unreadable card shows its reason as text on the card: the card must say
//     it cannot act (honest-disabled, never `disabled`)
//     expected "true"
//     got      null
//   x an unreadable card is still focusable and in the tab order: the card cannot
//     take focus - a keyboard or screen-reader user can never reach the line
//     explaining it
//
// MUTANT "out of tab order" (`tabIndex={unreadable ? -1 : undefined}` added to
// the card's <button>). Observed, 8/9:
//   x an unreadable card is still focusable and in the tab order: the card is
//     skipped by Tab (tabIndex -1) - a keyboard user never reaches the line
//     explaining it
await test("an unreadable card is still focusable and in the tab order", async () => {
  setUp();
  await mount();
  const future = cardEl("future")!;
  act(() => { future.focus(); });
  assert(document.activeElement === future,
    "the card cannot take focus - a keyboard or screen-reader user can never reach the line explaining it");
  assert(future.tabIndex >= 0,
    `the card is skipped by Tab (tabIndex ${future.tabIndex}) - a keyboard user never reaches the line explaining it`);
});

// CONTROLS: the readable card opens exactly as before and carries no lock.
//
// MUTANT "describedby on every card" (the "keep aria-describedby" wiring of
// the heard-once case, with `aria-describedby={reasonId}` unconditional).
// Observed, 7/9 (the heard-once case failed as quoted there, and):
//   x control: a readable card opens on a press, once, with its own id, and says
//     nothing: a readable card names a description it does not have
//     expected null
//     got      ":ri:"
//
// MUTANT "no card opens" (onClick made `() => {}`). Observed, 8/9:
//   x control: a readable card opens on a press, once, with its own id, and says
//     nothing: a readable card no longer opens
//     expected "good"
//     got      ""
await test("control: a readable card opens on a press, once, with its own id, and says nothing", async () => {
  setUp();
  await mount();
  const good = cardEl("good")!;
  eq(good.getAttribute("aria-disabled"), null, "a readable card is locked");
  eq(good.getAttribute("title"), null, "a readable card carries a lock reason");
  eq(good.getAttribute("aria-describedby"), null, "a readable card names a description it does not have");
  eq(good.querySelector("[data-flow-unreadable]"), null, "a readable card prints a reason line");
  assert((good.textContent ?? "").includes("12 subs each of L, R, G, B"), "the tagline is gone");
  assert(/NEVER RUN/.test(good.textContent ?? ""), "the readable card's status word changed");
  await press(good);
  eq(opened.join(","), "good", "a readable card no longer opens");
  eq(toastTitles().length, 0, "a readable card's press raised a toast");
});

await test("control: the folder heading counts every card under it, rows included", async () => {
  setUp();
  await mount();
  // The count is of what is drawn under the heading; the rows are drawn, so a
  // count that skipped them would disagree with the cards the operator sees.
  const heading = Array.from(document.querySelectorAll("h2"))
    .find((h) => h.textContent === "My flows")!;
  eq(heading.nextElementSibling?.textContent, "3", "the heading's count left out the rows it draws");
});

// -------------------------------------------------------------------- report
unmount();
const total = passed + failed;
console.log(`unreadableFlowCard.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
