// flowLibraryDom.test.tsx -- the library screen as a phone renders it.
//
//   Run directly:  npx tsx src/components/flows/__tests__/flowLibraryDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Three defects from the 2026-09-07 phone review, and all three are the kind
// that a screenshot finds and a unit test never does -- so what is pinned here is
// the MEASURABLE half of each, the part that would silently come back.
//
// 1. THE SEARCH GLYPH SAT ON THE PLACEHOLDER. The gutter was `pl-[30px]`, a
//    Tailwind utility, against `.field { padding: 6px 9px }` in unlayered CSS,
//    which wins -- so the text started at 9px under a glyph running 10..23px.
//    The assertion below reads the input's OWN paddingLeft and requires it to
//    clear the glyph's right edge. An assertion about a CLASS NAME could not
//    have caught the original bug: the class was there and did nothing.
//
// 2. THE NEW-FLOW CELL LED THE GRID. Under a heading reading "MY FLOWS 7" the
//    one thing a phone could see was a control for making an eighth. The
//    assertion is on DOM ORDER inside the grid, which is what decides what is
//    above the fold -- not on a class, and not on a screenshot nobody re-takes.
//
// 3. THERE WAS NO WAY TO MAKE A FLOW FROM THE TOOLBAR, so the only creation
//    control was the cell that had to be scrolled to.

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
function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; })
    .catch((e) => { failed++; failures.push(`x ${name}: ${(e as Error).message}`); });
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { useStore } = await import("../../../store");
const { api } = await import("../../../api");
const {
  FlowLibrary, SEARCH_GLYPH_LEFT_PX, SEARCH_GLYPH_SIZE_PX,
} = await import("../FlowLibrary");

// ------------------------------------------------------------------- harness
const CARDS = Array.from({ length: 7 }, (_, i) => ({
  id: `f${i}`,
  name: `FLOW ${i}`,
  folder: "My flows",
  // Long on purpose: a generated tagline runs to four lines on a 372px phone,
  // which is what made a 150px card into a 200px one.
  tagline: "12 subs each of L, R, G, B, Ha on NGC 6946: 60 frames, guided",
  readonly: false,
  stages: 11,
  wires: 12,
  last_run: null,
  last_result: "",
  updated_ts: 0,
}));
const FOLDERS = [
  { name: "My flows", count: 7, readonly: false },
  { name: "Examples", count: 5, readonly: true },
];

function setUp() {
  // Unmount BEFORE writing the fixture. Setting store state while the previous
  // test's tree is still mounted re-renders it outside act(), which React
  // reports as a warning and which makes one test's fixture briefly the
  // previous test's subject.
  unmount();
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
    principal: { role: "admin", email: "t@t", caps: ["control.capture"] },
  } as any);
  // The screen fetches on mount; the fixture above is the answer.
  (api as any).get = async (path: string) =>
    (path.includes("folders") ? FOLDERS : CARDS);
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
  // Awaited: the screen fetches its library on mount, and asserting on a tree
  // that has not yet taken the answer is how a test grades a loading spinner.
  await act(async () => { root!.render(React.createElement(FlowLibrary)); });
  return host;
}

const buttons = () =>
  Array.from(document.querySelectorAll("button")) as HTMLElement[];

// --------------------------------------------------------------------- tests
await test("the filter field's text starts clear of the search glyph", async () => {
  setUp();
  await mount();
  const input = document.querySelector(
    'input[aria-label="Filter flows"]') as HTMLInputElement;
  assert.ok(input, "no filter field");
  const pad = parseFloat(input.style.paddingLeft || "0");
  const glyphRight = SEARCH_GLYPH_LEFT_PX + SEARCH_GLYPH_SIZE_PX;
  assert.ok(
    pad >= glyphRight,
    `the placeholder starts at ${pad}px and the glyph ends at ${glyphRight}px `
    + ` -- they overlap. The gutter must be set where an unlayered .field rule `
    + `cannot beat it (inline), not as a plain Tailwind utility.`,
  );
});

await test("the flow cards come BEFORE the creation controls", async () => {
  setUp();
  await mount();
  const grid = (document.querySelector("[data-flow-id]") as HTMLElement)
    ?.parentElement;
  assert.ok(grid, "no card grid");
  const cells = Array.from(grid!.children) as HTMLElement[];
  const firstCard = cells.findIndex((c) => c.hasAttribute("data-flow-id"));
  const newFlow = cells.findIndex((c) => c.hasAttribute("data-flow-new"));
  const quick = cells.findIndex((c) => c.hasAttribute("data-flow-quick-card"));
  assert.ok(firstCard === 0,
    `the first cell of MY FLOWS is not a flow (index ${firstCard}) -- a phone `
    + `sees a control for making an eighth flow and none of the seven`);
  assert.ok(newFlow > firstCard && quick > firstCard,
    `a creation control (new=${newFlow}, quick=${quick}) sits above the flows`);
});

await test("every flow in the folder is rendered, not just the first screenful", async () => {
  setUp();
  await mount();
  assert.equal(document.querySelectorAll("[data-flow-id]").length, CARDS.length);
});

await test("the toolbar carries NEW FLOW and QUICK FLOW", async () => {
  setUp();
  await mount();
  // The toolbar buttons are the ones OUTSIDE the card grid.
  const grid = (document.querySelector("[data-flow-id]") as HTMLElement)
    .parentElement!;
  const toolbar = buttons().filter((b) => b.parentElement !== grid);
  assert.ok(toolbar.some((b) => /NEW FLOW/.test(b.textContent || "")),
    "no NEW FLOW control in the toolbar -- the only one is below seven cards");
  assert.ok(toolbar.some((b) => /QUICK FLOW/.test(b.textContent || "")),
    "no QUICK FLOW control in the toolbar");
});

await test("nothing on the library invites a file to be dropped on it", async () => {
  setUp();
  const host = await mount();
  // There is no onDrop, no dragover and no file input anywhere in this surface,
  // so a dashed rectangle -- the web's drop-zone idiom -- promised a gesture that
  // does nothing. Checked on the CLASS here because "dashed" is exactly the
  // thing being removed and there is no computed style in jsdom to read.
  assert.ok(!/border-dashed/.test(host.innerHTML),
    "a dashed drop-zone border is back on a screen with no drag-and-drop");
  assert.ok(!/\bdrop\b|drag/i.test(host.textContent || ""),
    "the library offers copy about dropping or dragging something");
});

await test("the two creation controls open their own sheets", async () => {
  setUp();
  await mount();
  const grid = (document.querySelector("[data-flow-id]") as HTMLElement)
    .parentElement!;
  const toolbar = buttons().filter((b) => b.parentElement !== grid);
  const newBtn = toolbar.find((b) => /NEW FLOW/.test(b.textContent || ""))!;
  await act(async () => { newBtn.click(); });
  assert.equal((useStore.getState() as any).flows.ui.wizardOpen, true,
    "NEW FLOW did not arm the guided wizard");
  assert.equal((useStore.getState() as any).flows.ui.quickOpen, false,
    "NEW FLOW armed the quick sheet as well");

  const quickBtn = toolbar.find((b) => /QUICK FLOW/.test(b.textContent || ""))!;
  await act(async () => { quickBtn.click(); });
  assert.equal((useStore.getState() as any).flows.ui.quickOpen, true,
    "QUICK FLOW did not arm the quick sheet");
});

await test("a just-saved flow is named on the card, not only ringed", async () => {
  setUp();
  const st = useStore.getState() as any;
  useStore.setState({
    flows: { ...st.flows, ui: { ...st.flows.ui, highlightId: "f3" } },
  } as any);
  await mount();
  const card = document.querySelector('[data-flow-id="f3"]') as HTMLElement;
  assert.ok(card?.getAttribute("data-flow-highlight") === "true",
    "the saved flow is not marked");
  assert.ok(/JUST SAVED/.test(card.textContent || ""),
    "the highlight is colour alone -- a card picked out by border colour is a "
    + "card nobody colour-blind can find");
  const others = Array.from(document.querySelectorAll("[data-flow-id]"))
    .filter((c) => c.getAttribute("data-flow-id") !== "f3");
  assert.ok(others.every((c) => !c.hasAttribute("data-flow-highlight")),
    "more than one card claims to be the one just saved");
});

await test("filtering hides the creation cells with the flows they are not", async () => {
  setUp();
  const st = useStore.getState() as any;
  useStore.setState({
    flows: { ...st.flows, ui: { ...st.flows.ui, query: "FLOW 2" } },
  } as any);
  await mount();
  assert.ok(!document.querySelector("[data-flow-new]"),
    "a cell that is not a search result is sitting inside a list of results");
  assert.ok(!document.querySelector("[data-flow-quick-card]"),
    "a cell that is not a search result is sitting inside a list of results");
});

// -------------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nflowLibraryDom.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
