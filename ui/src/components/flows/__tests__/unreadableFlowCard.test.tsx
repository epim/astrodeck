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
//      Assistive tech reaches the printed line through `aria-describedby`, an
//      id reference, so there is still one copy of the words.
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
// (and, for "restore the toast", of FlowLibrary.tsx too).

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

// --------------------------------------------------------------------- tests
await test("precondition: all three cards render, the unreadable rows included", async () => {
  setUp();
  await mount();
  for (const id of ["good", "future", "broken"]) assert(cardEl(id), `card ${id} never rendered`);
});

// MUTANT "reason not drawn" (FlowLibraryCard's `{unreadable && (` reason line
// made `{false && (`). Observed, 5/8 (it also kills the next two cases):
//   x an unreadable card shows its reason as text on the card: the reason is not
//     on the card - an operator sees a flow and no word about why it will not open
//   x the reason occurs exactly once across the card's text and attribute values:
//     future: the reason must be carried exactly once (text 0, attributes []) - a
//     repeat of a line already on the card is slop
//     expected 1
//     got      0
//   x aria-describedby resolves to the element holding the reason: future:
//     aria-describedby=":rd:" points at nothing
//
// MUTANT "no lock attribute" (the `aria-disabled={unreadable ? true :
// undefined}` line deleted). Observed, 7/8:
//   x an unreadable card shows its reason as text on the card: the card must say
//     it cannot act (honest-disabled, never `disabled`)
//     expected "true"
//     got      null
//
// MUTANT "status word unchanged" (`const status = cardStatus(card.last_result);`
// for every card). Observed, 7/8:
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
// card's <button>). Observed, 7/8:
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
// Observed, 7/8:
//   x pressing an unreadable card never calls flowsOpen and raises no toast: the
//     press repeated the card's own printed reason as a toast
//     expected "[]"
//     got      "[\"saved by a newer AstroDeck (schema 4); update to open
//     it\",\"unreadable: not valid JSON (line 1, column 2)\"]"
//
// MUTANT "open every card" (FlowLibraryCard's onClick made
// `() => onOpen(card.id)` whatever the card). Observed, 7/8:
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

// MUTANT "drop aria-describedby" (the card's `aria-describedby={unreadable ?
// reasonId : undefined}` line deleted). Observed, 7/8:
//   x aria-describedby resolves to the element holding the reason: future: the
//     card names no description, so a screen reader meets a disabled card with
//     no reason attached
//
// MUTANT "shared id" (`const reasonId = useId();` made the constant
// `"flow-unreadable-reason"`, so every unreadable card carries the same id and
// getElementById answers the first). Observed, 7/8:
//   x aria-describedby resolves to the element holding the reason: broken: the
//     description is not the card's own printed line
//
// MUTANT "id on the wrong line" (`id={reasonId}` moved from the reason span to
// the card's name span, so the reference resolves inside the right card to the
// wrong words). Observed, 7/8:
//   x aria-describedby resolves to the element holding the reason: future: the
//     description resolves to a line of the card that is not its reason
//     expected "saved by a newer AstroDeck (schema 4); update to open it"
//     got      "Mosaic NGC 7000"
await test("aria-describedby resolves to the element holding the reason", async () => {
  setUp();
  await mount();
  for (const [id, reason] of [["future", NEWER], ["broken", BROKEN]] as const) {
    const card = cardEl(id)!;
    const ref = card.getAttribute("aria-describedby");
    assert(ref, `${id}: the card names no description, so a screen reader meets a disabled card with no reason attached`);
    // ONE id, not a list and not the sentence itself: the attribute is a
    // pointer at the printed line, so the words exist once.
    assert(!/\s/.test(ref!), `${id}: aria-describedby is not a single id reference: ${JSON.stringify(ref)}`);
    const target = document.getElementById(ref!);
    assert(target, `${id}: aria-describedby=${JSON.stringify(ref)} points at nothing`);
    assert(card.contains(target), `${id}: the description is not the card's own printed line`);
    eq(target!.textContent, reason, `${id}: the description resolves to a line of the card that is not its reason`);
  }
});

// Two ways to lose the card, two assertions. `focus()` catches the native
// `disabled` (jsdom, like a browser, will not focus a disabled control); it
// does NOT catch `tabIndex={-1}`, which still takes programmatic focus but is
// skipped by Tab - hence the separate tabIndex check.
//
// MUTANT "native disabled" (`aria-disabled={unreadable ? true : undefined}`
// made `disabled={unreadable ? true : undefined}`). Observed, 6/8:
//   x an unreadable card shows its reason as text on the card: the card must say
//     it cannot act (honest-disabled, never `disabled`)
//     expected "true"
//     got      null
//   x an unreadable card is still focusable and in the tab order: the card cannot
//     take focus - a keyboard or screen-reader user can never reach the line
//     explaining it
//
// MUTANT "out of tab order" (`tabIndex={unreadable ? -1 : undefined}` added to
// the card's <button>). Observed, 7/8:
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
// MUTANT "describedby on every card" (`aria-describedby={reasonId}` on every
// card). Observed, 7/8:
//   x control: a readable card opens on a press, once, with its own id, and says
//     nothing: a readable card names a description it does not have
//     expected null
//     got      ":ri:"
//
// MUTANT "no card opens" (onClick made `() => {}`). Observed, 7/8:
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
