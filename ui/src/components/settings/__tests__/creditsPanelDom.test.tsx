// creditsPanelDom.test.tsx — the credits screen, MOUNTED, with the REAL data.
//
//   Run directly:  npx tsx src/components/settings/__tests__/creditsPanelDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// What needs a DOM rather than a unit test: whether the page actually PUTS every
// group and every entry on screen. A credits page that renders 40 of 123
// entries is worse than no page — it looks complete and is a licence breach.
// So this mounts the panel with the real generated document and counts what
// came out against what went in, group by group.
//
// It also checks the two behaviours that could hide an entry from a reader who
// is looking for it: a collapsed group must open, and a search must not leave
// its own matches behind a closed disclosure.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const CreditsPanel = (await import("../CreditsPanel")).default;
const rawDoc = (await import("../../../credits.generated.json")).default;
const { countEntries } = await import("../../../lib/credits");

const doc = rawDoc as any;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  // The `data` prop bypasses the dynamic import(): this test is about what the
  // panel DRAWS, not about the bundler's chunk splitting.
  act(() => { rootRef!.render(createElement(CreditsPanel, { data: doc })); });
}
function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
function type(value: string): void {
  const input = container.querySelector('input[type="search"]') as any;
  act(() => {
    // React 18 tracks the previous value on the node; bypass the setter so the
    // synthetic onChange actually fires.
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, value);
    input.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
const groupNodes = (): any[] => [...container.querySelectorAll("[data-credit-group]")];
const entryNodes = (): any[] => [...container.querySelectorAll("[data-credit-entry]")];
const groupToggle = (id: string): any =>
  container.querySelector(`[data-credit-group="${id}"] button`);

// --------------------------------------------------------------------- tests
mount();

test("every group in the document reaches the screen", () => {
  const rendered = groupNodes().map((n) => n.getAttribute("data-credit-group"));
  const expected = doc.groups.map((x: any) => x.id);
  assert(rendered.length === expected.length,
    `rendered ${rendered.length} groups, document has ${expected.length}`);
  for (const id of expected) {
    assert(rendered.includes(id), `group ${id} is missing from the screen`);
  }
});

test("group order is preserved, so owner decisions stay at the top", () => {
  const rendered = groupNodes().map((n) => n.getAttribute("data-credit-group"));
  assert(rendered.join(",") === doc.groups.map((x: any) => x.id).join(","),
    `order changed: ${rendered.join(",")}`);
});

test("the header states the true total, not the number currently expanded", () => {
  const total = countEntries(doc as any);
  const text = container.textContent || "";
  assert(text.includes(String(total)), `total ${total} is not stated on the page`);
  const count = container.querySelector("[data-credit-count]");
  assert(!!count && /Showing all/.test(count.textContent || ""),
    `count line reads "${count?.textContent}"`);
});

test("expanding every group reveals every entry — nothing is silently truncated", () => {
  for (const gr of doc.groups) {
    const toggle = groupToggle(gr.id);
    assert(!!toggle, `no toggle for group ${gr.id}`);
    if (toggle.getAttribute("aria-expanded") !== "true") click(toggle);
  }
  const rendered = entryNodes().length;
  const expected = countEntries(doc as any);
  assert(rendered === expected,
    `screen shows ${rendered} entries, document has ${expected}`);
});

test("every entry that requires reproduced text offers it on screen", () => {
  // The disclosure button is the proof the text is present at all; a licence
  // with nothing behind it renders the red "missing from this build" line.
  const missing = (container.textContent || "").includes("missing from this build");
  assert(!missing, "at least one licence body did not resolve");
  const readable = [...container.querySelectorAll("button")]
    .filter((b: any) => /^Read /.test((b.textContent || "").trim()));
  assert(readable.length > 50,
    `only ${readable.length} licence texts are offered — expected one per entry that needs one`);
});

test("a licence body can actually be opened and contains the licence", () => {
  const button = [...container.querySelectorAll("button")]
    .find((b: any) => /^Read /.test((b.textContent || "").trim()));
  click(button);
  const pre = container.querySelector("pre");
  assert(!!pre, "opening a licence rendered no text block");
  assert((pre.textContent || "").length > 200,
    `licence body is only ${(pre.textContent || "").length} characters`);
});

test("the flagged group is expanded on arrival — a decision below the fold is not made", () => {
  mount();
  const flagged = doc.groups.find((x: any) => x.id === "flagged");
  if (!flagged) return;
  const toggle = groupToggle("flagged");
  assert(toggle.getAttribute("aria-expanded") === "true",
    "the owner-decision group starts collapsed");
  const shown = container.querySelectorAll(
    '[data-credit-group="flagged"] [data-credit-entry]').length;
  assert(shown === flagged.entries.length,
    `flagged group shows ${shown} of ${flagged.entries.length}`);
});

test("the other groups start collapsed, so the page is navigable", () => {
  mount();
  const collapsed = doc.groups.filter((x: any) => x.id !== "flagged")
    .filter((x: any) => groupToggle(x.id).getAttribute("aria-expanded") !== "true");
  assert(collapsed.length === doc.groups.length - (
    doc.groups.some((x: any) => x.id === "flagged") ? 1 : 0),
    "some non-flagged group starts expanded");
});

test("searching narrows the page and shows the matches without another click", () => {
  mount();
  type("react");
  const names = entryNodes().map((n) => n.getAttribute("data-credit-entry"));
  assert(names.includes("react"), `search for "react" did not surface react: ${names}`);
  assert(names.length < countEntries(doc as any), "search did not narrow anything");
  const count = container.querySelector("[data-credit-count]");
  assert(/Showing \d+ of \d+/.test(count.textContent || ""),
    `count line did not switch to a subset: "${count?.textContent}"`);
});

test("a search that matches nothing says so instead of showing a blank page", () => {
  mount();
  type("zzzznotapackage");
  assert(entryNodes().length === 0, "entries survived an impossible search");
  assert(/Nothing matches/.test(container.textContent || ""),
    "no empty-state message");
});

test("a vendored binary is findable by the vendor's name", () => {
  mount();
  type("zwo");
  const names = entryNodes().map((n) => n.getAttribute("data-credit-entry"));
  assert(names.some((n) => /ZWO/i.test(n || "")),
    `searching "zwo" found ${names.length} entries, none of them the SDK`);
});

act(() => { rootRef?.unmount(); });

console.log(`creditsPanelDom.test: ${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
