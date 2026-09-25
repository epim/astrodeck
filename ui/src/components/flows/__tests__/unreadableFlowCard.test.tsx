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
//   6. THE STATUS WORD IS HEARD ONCE AND STILL PRINTED (#217), on every card,
//      readable or not. The LED beside it used to carry the same word as its
//      label, so the name said it twice. The names the #206 records below
//      quote end in the word twice for that reason; they were observed before
//      #217 and are left verbatim.
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
const { FlowLibraryCard } = await import("../FlowLibraryCard");

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
/** One card on its own, for the status words the library fixture does not
 *  carry: every card in it has never run, and `cardStatus` has three more
 *  readable words than NEVER RUN. */
async function mountCard(card: any): Promise<HTMLElement> {
  const host = document.getElementById("root")!;
  unmount();
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => {
    root!.render(React.createElement(FlowLibraryCard, { card, onOpen: () => {} }));
  });
  return host.querySelector(`[data-flow-id="${card.id}"]`) as HTMLElement;
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

/** The text a sighted reader can see in `el`: its text, less every subtree the
 *  page hides from the eye. `textContent` is not that - a word in an `sr-only`
 *  span is in `textContent` and in the accessible name, and on nobody's
 *  screen. jsdom has no stylesheet here (the CSS is stubbed), so this reads the
 *  markup this codebase hides text with: the `hidden` attribute and the
 *  `sr-only`, `hidden` and `invisible` utilities, at their unprefixed (phone)
 *  width. `aria-hidden` is NOT one of them: it hides from the ear, not the eye. */
function seenText(el: Element): string {
  let out = "";
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === 3) { out += n.textContent ?? ""; continue; }
    if (n.nodeType !== 1) continue;
    const child = n as Element;
    if (child.hasAttribute("hidden")) continue;
    if (["sr-only", "hidden", "invisible"].some((c) => child.classList.contains(c))) continue;
    out += seenText(child);
  }
  return out;
}

// ------------------------------------------------ accessible name, minimally
// What a screen reader announces for a control is its accessible NAME and its
// DESCRIPTION (W3C accname 1.2). This is a minimal computation of both, enough
// for a <button> built out of spans, and not a general implementation:
//
//   name         aria-labelledby, then aria-label, else the text of the
//                content - where a descendant's own aria-label stands in for
//                that descendant's content (a labelled LED is a role="img"
//                with an aria-label, which is how the status word reached the
//                name twice, #217) and an aria-hidden subtree contributes
//                nothing;
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
//
// MUTANT "descendant label ignored" (contentText's
// `${label && label.trim() ? label : contentText(child)}` made
// `${contentText(child)}`). The #217 status-word case leans on this rule: a
// helper that read a labelled LED as empty could never hear its label, so it
// could never see the word twice. Observed, 10/11, and the SAME 10/11 when it
// was run together with the card mutant "LED keeps its label" - the status
// case stayed green, blind, and only this line caught it:
//   x precondition: the name helper follows the rules it models: a labelled
//     descendant did not stand in for its content
//     expected "CANNOT OPEN x"
//     got      "x"
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
// So a ruling for (a) or (b) changes FlowLibraryCard.tsx, not this case. It
// does change the #217 control further down, which holds the rest of each
// card's name and so pins (c); that control's comment quotes both failures.
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

// THE STATUS WORD IS HEARD ONCE TOO (#217). The status row prints its word
// beside an LED, and the LED used to carry the same word as its label. `Led`
// draws a labelled LED as role="img" with that aria-label, and in a button
// named from its content a descendant's label stands in for the descendant, so
// every card said its status twice - the names quoted for #206 above end
// "never run CANNOT OPEN CANNOT OPEN". Same class as #206: the DOM holds the
// word once and the ear gets it twice.
//
// Two halves, because either alone passes a wrong fix:
//   - the word is in the card's name ONCE: not twice, and not zero, which is
//     what hiding the whole status row from the ear would give;
//   - the word is still PRINTED on the card. Deleting the text and keeping the
//     LED's label would pass the first half and leave a sighted reader with an
//     LED's colour and shape and no word. "Printed" is what `seenText` reads,
//     not `textContent`: moving the word into an `sr-only` span keeps it in
//     `textContent` and in the name, and takes it off the screen.
//
// Every status word the card can print, readable and unreadable: CANNOT OPEN
// and NEVER RUN from the library, the other three from `mountCard`.
//
// Counted case-sensitively, which matters for one card: a card that has never
// run also says "never run" (lowercase) in its meta line's last-run slot, so it
// is heard as "never run NEVER RUN" whether or not the LED is labelled. Those
// are two designed strings for two fields (last_run and last_result); this case
// grades the LED's copy of the status word, not that design.
//
// NOT HEARD ON A SCREEN READER. Like the #206 case, this holds the wiring to
// the accname rules the helper models; nobody has listened to a card on NVDA
// or on VoiceOver.
//
// It can see a labelled LED only because the helper lets a descendant's
// aria-label stand in for it; the precondition case pins that rule, and the
// "descendant label ignored" record there shows this case going blind without
// it.
//
// MUTANT "LED keeps its label" (the status row's `<Led state={status.led} />`
// given back `label={status.text}` - the code before #217). Observed, 10/11:
//   x a screen reader hears each card's status word once, and the word is still
//     printed: future (unreadable): "CANNOT OPEN" heard 2 time(s), name "Mosaic NGC 7000 saved by a newer AstroDeck (schema 4); update to open it 9 stages · 8 wires · never run CANNOT OPEN CANNOT OPEN"
//     broken (unreadable): "CANNOT OPEN" heard 2 time(s), name "broken unreadable: not valid JSON (line 1, column 2) 0 stages · 0 wires · never run CANNOT OPEN CANNOT OPEN"
//     good (readable): "NEVER RUN" heard 2 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run NEVER RUN NEVER RUN"
//     last_result "ok" (readable): "COMPLETED CLEAN" heard 2 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run COMPLETED CLEAN COMPLETED CLEAN"
//     last_result "warn" (readable): "WARN" heard 2 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run WARN WARN"
//     last_result "bad" (readable): "BAD" heard 2 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run BAD BAD"
//
// MUTANT "word only in the LED label" (the LED labelled again and the
// `{status.text}` line after it deleted). Observed, 8/11:
//   x an unreadable card shows its reason as text on the card: the status word
//     does not say it cannot open
//   x a screen reader hears each card's status word once, and the word is still
//     printed: future (unreadable): "CANNOT OPEN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     broken (unreadable): "CANNOT OPEN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     good (readable): "NEVER RUN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "ok" (readable): "COMPLETED CLEAN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "warn" (readable): "WARN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "bad" (readable): "BAD" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//   x control: a readable card opens on a press, once, with its own id, and
//     says nothing: the readable card's status word changed
//
// MUTANT "status row hidden" (`aria-hidden="true"` on the status row's <span>,
// the LED left unlabelled). Observed, 10/11:
//   x a screen reader hears each card's status word once, and the word is still
//     printed: future (unreadable): "CANNOT OPEN" heard 0 time(s), name "Mosaic NGC 7000 saved by a newer AstroDeck (schema 4); update to open it 9 stages · 8 wires · never run"
//     broken (unreadable): "CANNOT OPEN" heard 0 time(s), name "broken unreadable: not valid JSON (line 1, column 2) 0 stages · 0 wires · never run"
//     good (readable): "NEVER RUN" heard 0 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run"
//     last_result "ok" (readable): "COMPLETED CLEAN" heard 0 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run"
//     last_result "warn" (readable): "WARN" heard 0 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run"
//     last_result "bad" (readable): "BAD" heard 0 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run"
//
// MUTANT "LED labelled when on" (`label={status.led === "on" ? status.text :
// undefined}`: only COMPLETED CLEAN's LED is labelled). This is why the case
// mounts the three readable words the library fixture lacks. Observed, 10/11:
//   x a screen reader hears each card's status word once, and the word is still
//     printed: last_result "ok" (readable): "COMPLETED CLEAN" heard 2 time(s), name "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run COMPLETED CLEAN COMPLETED CLEAN"
//
// MUTANT "word visually hidden" (the LED left unlabelled and the printed
// `{status.text}` wrapped in `<span className="sr-only">`: heard once, seen
// nowhere). Found by the #217 verifier, when this case still counted
// `textContent` and left all 11/11 green. Observed with `seenText`, 10/11:
//   x a screen reader hears each card's status word once, and the word is still
//     printed: future (unreadable): "CANNOT OPEN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     broken (unreadable): "CANNOT OPEN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     good (readable): "NEVER RUN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "ok" (readable): "COMPLETED CLEAN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "warn" (readable): "WARN" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//     last_result "bad" (readable): "BAD" printed 0 time(s) - without the word the status is an LED's colour and shape alone
//
// MUTANT "seenText reads textContent" (the helper's body made `return
// el.textContent ?? "";` - a mutant of THIS file). The known positive at the
// top of the case catches it; run together with "word visually hidden", the
// same single line is the only failure, so the positive is what keeps the
// printed half from going blind. Observed, 10/11, alone and together:
//   x a screen reader hears each card's status word once, and the word is still
//     printed: precondition: seenText read text the eye cannot see
//     expected "E F"
//     got      "A B C D E F"
//
// NOT LOCKED TO ONE SHAPE. The other way to say the word once - the LED keeps
// its label and the printed word is wrapped in `<span aria-hidden="true">` -
// was run from the same backup and left all 11/11 cases green. The shipped
// shape unlabels the LED because #217 asked for it, not because this case
// requires it. It stays green with `seenText`, which is why the helper must
// not treat `aria-hidden` as unseen.
await test("a screen reader hears each card's status word once, and the word is still printed", async () => {
  // The seen-text helper's known positive: every way this codebase hides text
  // from the eye is dropped, and an aria-hidden span, which only the ear loses,
  // is kept.
  const kp = document.createElement("div");
  kp.innerHTML = `<span class="sr-only">A</span> <span hidden>B</span> `
    + `<span class="hidden lg:inline">C</span> <span class="invisible">D</span> `
    + `<span aria-hidden="true">E</span> F`;
  eq(squash(seenText(kp)), "E F", "precondition: seenText read text the eye cannot see");

  const wrong: string[] = [];
  const check = (who: string, card: HTMLElement, word: string) => {
    const name = accName(card);
    const heard = occurrences(name, word);
    if (heard !== 1) {
      wrong.push(`${who}: ${JSON.stringify(word)} heard ${heard} time(s), name ${JSON.stringify(name)}`);
    }
    const printed = occurrences(seenText(card), word);
    if (printed !== 1) {
      wrong.push(`${who}: ${JSON.stringify(word)} printed ${printed} time(s) - without the word`
        + " the status is an LED's colour and shape alone");
    }
  };
  setUp();
  await mount();
  check("future (unreadable)", cardEl("future")!, "CANNOT OPEN");
  check("broken (unreadable)", cardEl("broken")!, "CANNOT OPEN");
  check("good (readable)", cardEl("good")!, "NEVER RUN");
  // `last_run` stays null on these three, so their meta line says "never run"
  // beside a result. That is not a state the server sends, and it does not
  // matter here: the meta line is not graded. A real time would be formatted
  // in LOCAL time, which would make the quoted names below depend on the
  // machine's time zone.
  for (const [lastResult, word] of [["ok", "COMPLETED CLEAN"], ["warn", "WARN"], ["bad", "BAD"]] as const) {
    const card = await mountCard({ ...CARDS[0], id: `ran-${lastResult}`, last_result: lastResult });
    check(`last_result ${JSON.stringify(lastResult)} (readable)`, card, word);
  }
  assert(wrong.length === 0, wrong.join("\n  "));
});

// CONTROL (#217): the fix takes a copy of the status word out of the name and
// nothing else. With EVERY copy of the word taken out, what is left of each
// card's name must be what the card said before the fix, so this case is blind
// to the count the case above grades and sees only the rest of the name - the
// title, the tagline, the reason and the meta line. Run against the card
// before #217 it passed, which is what makes it a control and not a second
// copy of the count.
//
// IT PINS #206's SHAPE (c) FOR THE UNREADABLE CARDS. The #206 case above is
// deliberately open between that issue's three shapes; this control is not,
// because the reason is part of the rest of the name it holds. Found by the
// #217 verifier, running the two other shapes exactly as the #206 record
// describes them. Each leaves every other case green and fails only here,
// 10/11. Shape (a), the reason aria-hidden and referenced by
// aria-describedby:
//   x control: apart from its status word, each card's name is what it was:
//     future: the unreadable card's name lost or gained something besides its
//     status word
//     expected "Mosaic NGC 7000 saved by a newer AstroDeck (schema 4); update to open it 9 stages · 8 wires · never run"
//     got      "Mosaic NGC 7000 9 stages · 8 wires · never run"
// Shape (b), an explicit aria-label on an unreadable card: the same line,
// with got "Mosaic NGC 7000,". So a ruling for (a) or (b) on #206 also
// rewrites the two unreadable expectations below; the readable one stands.
//
// MUTANT "name from an aria-label" (`aria-label={`${card.name},
// ${status.text}`}` on every card's <button>, the LED left unlabelled: the
// status word is heard once, so the case above stays green). Observed, 9/11
// (the #206 heard-once case also fails, with name "Mosaic NGC 7000, CANNOT
// OPEN" and the reason heard 0 times):
//   x control: apart from its status word, each card's name is what it was:
//     good: the readable card's name lost or gained something besides its
//     status word
//     expected "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run"
//     got      "M31 LRGB,"
await test("control: apart from its status word, each card's name is what it was", async () => {
  setUp();
  await mount();
  const rest = (id: string, word: string) => squash(accName(cardEl(id)!).split(word).join(" "));
  eq(rest("good", "NEVER RUN"), "M31 LRGB 12 subs each of L, R, G, B 7 stages · 6 wires · never run",
    "good: the readable card's name lost or gained something besides its status word");
  eq(rest("future", "CANNOT OPEN"), `Mosaic NGC 7000 ${NEWER} 9 stages · 8 wires · never run`,
    "future: the unreadable card's name lost or gained something besides its status word");
  eq(rest("broken", "CANNOT OPEN"), `broken ${BROKEN} 0 stages · 0 wires · never run`,
    "broken: the unreadable card's name lost or gained something besides its status word");
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
