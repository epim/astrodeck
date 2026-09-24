// unreadableFlowCard.test.tsx - the classic library draws a flow this build
// cannot open as a card that SAYS SO, and never opens it (#153; spec
// 2026-09-23 section 3.6).
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
//   2. A PRESS NEVER CALLS `flowsOpen`. Every route answers 404 for these ids,
//      and `flowsOpen` writes that 404 into `libraryError` - so opening one
//      would replace the card that explains itself with "Could not read the
//      flow library", a sentence about the whole library that is false. The
//      press states the reason as a warning toast instead, so it is not silent.
//   3. A READABLE CARD IS UNCHANGED (the controls): it opens on a press and
//      carries no lock.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of FlowLibraryCard.tsx.

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

// --------------------------------------------------------------------- tests
await test("precondition: all three cards render, the unreadable rows included", async () => {
  setUp();
  await mount();
  for (const id of ["good", "future", "broken"]) assert(cardEl(id), `card ${id} never rendered`);
});

// MUTANT "reason not drawn" (FlowLibraryCard's `{unreadable && (` reason line
// made `{false && (`). Observed, 4/5:
//   x an unreadable card shows its reason as text on the card: the reason is not
//     on the card - an operator sees a flow and no word about why it will not open
//
// MUTANT "no lock attribute" (the `aria-disabled={unreadable ? true :
// undefined}` line deleted). Observed, 4/5:
//   x an unreadable card shows its reason as text on the card: the card must say
//     it cannot act (honest-disabled, never `disabled`)
//     expected "true"
//     got      null
//
// MUTANT "status word unchanged" (`const status = cardStatus(card.last_result);`
// for every card). Observed, 4/5:
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
  eq(future.getAttribute("title"), NEWER, "the hover names the same reason");
  assert(!/NEVER RUN/.test(future.textContent ?? ""),
    "the status word says NEVER RUN, which reads as a flow that could be run");
  assert(/CANNOT OPEN/.test(future.textContent ?? ""), "the status word does not say it cannot open");
});

// MUTANT "open every card" (FlowLibraryCard's onClick made
// `() => onOpen(card.id)` whatever the card). Observed, 4/5:
//   x pressing an unreadable card never calls flowsOpen, and states the reason:
//     the press opened a flow every route answers 404 for
//     expected ""
//     got      "future,broken"
//
// MUTANT "silent press" (FlowLibrary stops passing `onExplain={explainCard}`).
// Observed, 4/5:
//   x pressing an unreadable card never calls flowsOpen, and states the reason:
//     the press was silent - toasts: []
await test("pressing an unreadable card never calls flowsOpen, and states the reason", async () => {
  setUp();
  await mount();
  await press(cardEl("future")!);
  await press(cardEl("broken")!);
  eq(opened.join(","), "", "the press opened a flow every route answers 404 for");
  const titles = toastTitles();
  assert(titles.includes(NEWER), `the press was silent - toasts: ${JSON.stringify(titles)}`);
  assert(titles.includes(BROKEN), `the second press was silent - toasts: ${JSON.stringify(titles)}`);
});

// CONTROLS: the readable card opens exactly as before and carries no lock.
await test("control: a readable card opens on a press, once, with its own id, and says nothing", async () => {
  setUp();
  await mount();
  const good = cardEl("good")!;
  eq(good.getAttribute("aria-disabled"), null, "a readable card is locked");
  eq(good.getAttribute("title"), null, "a readable card carries a lock reason");
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
