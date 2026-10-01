// confirmHostBody.test.tsx - the classic ConfirmHost renders a confirm's BODY
// validly whatever it holds (#213).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/__tests__/confirmHostBody.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// `ConfirmRequest.body` is typed `ReactNode` and documented as "rendered as-is
// (F-D1)", and callers pass block content: the Flows RUN button's 409
// `unmapped` question (flowRunControls.tsx) passes a <ul> of the settings the
// compile drops. The host used to wrap every body in a <p>, and a <p> may hold
// phrasing content only, so every such confirm was invalid HTML and printed
//
//     Warning: validateDOMNesting(...): <ul> cannot appear as a descendant of <p>.
//
// on every render - the promise "any ReactNode works" held for part of the
// type. The browser still showed the list (React builds the DOM with
// createElement and never re-parses it), so the only witness is that warning,
// and this file watches for it on `console.error`.
//
//   1. A BLOCK BODY (a paragraph and a list) renders inside the dialog, carries
//      the body's classes, and React raises no DOM-nesting warning for it.
//   2. CONTROL: a STRING body is today's paragraph, same text, same classes.
//      Twenty-odd call sites pass a sentence and none of them should change.
//   3. CONTROL: no body draws no body element.
//
// THE SPY HAS A KNOWN POSITIVE, run first. Two ways this file could pass while
// the host is broken: React's production build emits no DOM-nesting warnings
// at all, and React DEDUPES them - one warning per (invalid-parent, child tag,
// ancestor tag) key per process (react-dom.development.js, `didWarn$1[warnKey]`).
// So the positive provokes a DIFFERENT key (<div> in <p>) from the ones the
// host test watches (<p> in <p>, <ul> in <p>); provoking the same key would
// spend the one warning React will ever print for it.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of ConfirmDialog.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div><div id="scratch"></div></body></html>`,
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
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ console spy
// React's dev build calls `console.error("Warning: validateDOMNesting(...): %s
// cannot appear as a descendant of <%s>.", "<ul>", "p", stack)` and reads
// `console.error` at call time, so replacing it here sees every warning. Each
// call is kept FORMATTED (the %s filled in), so a failure message quotes the
// warning a developer would have read. Everything is still forwarded, except
// while the known positive below provokes its warning on purpose.
const realError = console.error.bind(console);
const errors: string[] = [];
let quiet = false;
function format(args: unknown[]): string {
  const [first, ...rest] = args;
  if (typeof first !== "string") return args.map(String).join(" ");
  let i = 0;
  // Arguments past the last %s are dropped. React's component stack is not
  // one of them: its dev `printWarning` appends a %s of its own for the stack
  // (react-dom 18.3.1), so a formatted warning keeps its `at ...` lines. The
  // failures quoted below leave those lines out.
  return first.replace(/%s/g, () => String(rest[i++])).trim();
}
console.error = (...args: unknown[]) => {
  errors.push(format(args));
  if (!quiet) realError(...args);
};
/** React 18 prefixes these "validateDOMNesting(...)"; React 19 dropped that
 *  and says "In HTML, <x> cannot be a descendant of <p>". Both are matched so
 *  an upgrade cannot quietly turn this file blind - and if a third wording
 *  ever appears, the known positive fails first and says so. */
const nesting = (from: number) => errors.slice(from).filter((e) =>
  /validateDOMNesting|cannot (appear as|be) a (descendant|child) of/.test(e));

// ------------------------------------------------------------------- imports
// Dynamic, after the globals: a static import is hoisted above everything in
// this file, and react-dom decides whether it has a DOM when it first loads.
const { createElement, Fragment, act } = await import("react");
const { createRoot } = await import("react-dom/client");

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

const { useStore } = await import("../../store");
const { ConfirmHost, confirmDialog } = await import("../ConfirmDialog");

/** The body's classes, verbatim from the host before #213. A body that lost
 *  them would render in the dialog's default type, not the confirm's. */
const BODY_CLASSES = ["text-sm", "text-ink", "leading-relaxed", "break-words"];

// ------------------------------------------------------------------- harness
let root: ReturnType<typeof createRoot> | null = null;
async function unmountHost(): Promise<void> {
  if (!root) return;
  const r = root;
  root = null;
  await act(async () => { r.unmount(); });
}
async function mountHost(): Promise<void> {
  await unmountHost();
  useStore.setState({ confirm: null, authGate: "open" } as any);
  const host = document.getElementById("root")!;
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => { root!.render(createElement(ConfirmHost)); });
}
/** Ask a question through the real imperative entry point, the one every call
 *  site uses, and let React commit it. The promise is left pending on purpose;
 *  the next `mountHost` clears the slice. */
async function ask(opts: Parameters<typeof confirmDialog>[0]): Promise<void> {
  await act(async () => { void confirmDialog(opts); });
}
// ConfirmHost renders through Overlay, which portals to <body>.
const dialog = () => document.body.querySelector('[role="alertdialog"]') as HTMLElement | null;

// --------------------------------------------------------------------- tests

// The spy's known positive. If this fails, every "no warning" below is
// worthless: either the build is not React's development build, or the spy
// is not where React writes.
//
// MUTANT "spy records nothing" (the `errors.push(format(args))` line above
// deleted - a mutant of THIS file, run from a backup of it). Observed, 3/4;
// the block-body case below stayed GREEN, which is the vacuous pass this
// precondition exists to expose:
//   x precondition: the spy catches a DOM-nesting warning React raises: the spy
//     saw [] for a <div> inside a <p>
//     expected 1
//     got      0
await test("precondition: the spy catches a DOM-nesting warning React raises", async () => {
  const from = errors.length;
  const scratch = createRoot(document.getElementById("scratch")!);
  quiet = true;
  try {
    await act(async () => {
      scratch.render(createElement("p", null, createElement("div", null, "block in a paragraph")));
    });
  } finally {
    quiet = false;
    await act(async () => { scratch.unmount(); });
  }
  const seen = nesting(from);
  eq(seen.length, 1, `the spy saw ${JSON.stringify(seen)} for a <div> inside a <p>`);
  assert(/<div>/.test(seen[0]) && /<p>/.test(seen[0]),
    `the warning the spy caught is not about <div> in <p>: ${JSON.stringify(seen[0])}`);
});

// MUTANT "restore the <p> wrapper" (the non-string branch of ConfirmHost's
// body renders `<p className=...>` again, as before #213). Observed, 3/4
// (and the two warnings on stderr, forwarded by the spy):
//   x a block body renders in the dialog, with the body's classes and no
//     DOM-nesting warning: React warned about the body's nesting:
//     Warning: validateDOMNesting(...): <p> cannot appear as a descendant of <p>.
//     Warning: validateDOMNesting(...): <ul> cannot appear as a descendant of <p>.
//     expected 0
//     got      2
//
// MUTANT "div without the classes" (the non-string branch renders a bare
// `<div>`). Observed, 3/4:
//   x a block body renders in the dialog, with the body's classes and no
//     DOM-nesting warning: the block body lost the confirm body's classes: ""
//
// MUTANT "span wrapper" (the non-string branch renders `<span className=...>`).
// It SURVIVED 4/4 before the tagName check existed: no <p> above the list, the
// classes kept, and React warns about nothing. Observed after it, 3/4:
//   x a block body renders in the dialog, with the body's classes and no
//     DOM-nesting warning: the block body's wrapper is not a <div>
//     expected "DIV"
//     got      "SPAN"
await test("a block body renders in the dialog, with the body's classes and no DOM-nesting warning", async () => {
  await mountHost();
  const from = errors.length;
  await ask({
    title: "Run anyway?",
    // The shape of the Flows RUN question: a sentence, then a list. A <p> of
    // the body's own is included because a <p> in a <p> is the other half of
    // the same defect (#213: "A body holding a <p> of its own nests
    // paragraphs").
    body: createElement(Fragment, null,
      createElement("p", null, "This flow has settings the run cannot use:"),
      createElement("ul", null,
        createElement("li", null, "Dither every 3 frames"),
        createElement("li", null, "Rotator to 90 deg"))),
  });
  const d = dialog();
  assert(d, "the confirm never opened - nothing below would be measuring the host");
  const ul = d!.querySelector("ul");
  assert(ul && ul.querySelectorAll("li").length === 2, "the list body never reached the dialog");
  const seen = nesting(from);
  eq(seen.length, 0, `React warned about the body's nesting:\n    ${seen.join("\n    ")}`);
  eq(ul!.closest("p"), null, "the list sits inside a <p>, which may hold phrasing content only");
  const wrap = ul!.parentElement!;
  // A <div>, not merely "not a <p>". A <span> may hold phrasing content only
  // as well, and React raises no DOM-nesting warning for a <ul> inside one,
  // so neither check above would see that wrapper.
  eq(wrap.tagName, "DIV", "the block body's wrapper is not a <div>");
  const cls = wrap.getAttribute("class") ?? "";
  assert(BODY_CLASSES.every((c) => cls.split(/\s+/).includes(c)),
    `the block body lost the confirm body's classes: ${JSON.stringify(cls)}`);
  assert(wrap.textContent!.startsWith("This flow has settings"),
    "the body's sentence is not in the same wrapper as its list");
});

// CONTROL. A sentence is a paragraph, and the host keeps drawing it as one:
// the same element, text and classes as before #213, so none of the call
// sites that pass a string sees a change.
//
// MUTANT "string body in a div" (the string branch renders `<div>`). Observed, 3/4:
//   x control: a string body is the same paragraph as before, same text, same
//     classes: a string body is no longer a paragraph
//     expected "P"
//     got      "DIV"
//
// MUTANT "string body without the classes" (the string branch renders a bare
// `<p>`). Observed, 3/4:
//   x control: a string body is the same paragraph as before, same text, same
//     classes: the string body's classes changed
//     expected "text-sm text-ink leading-relaxed break-words"
//     got      null
await test("control: a string body is the same paragraph as before, same text, same classes", async () => {
  await mountHost();
  const from = errors.length;
  const SENTENCE = "Clear the horizon at Back Lawn? This cannot be undone.";
  await ask({ title: "Clear horizon", body: SENTENCE });
  const d = dialog();
  assert(d, "the confirm never opened");
  const holder = Array.from(d!.querySelectorAll("p, div"))
    .find((el) => el.textContent === SENTENCE) as HTMLElement | undefined;
  assert(holder, "the sentence is not in the dialog as one element's text");
  eq(holder!.tagName, "P", "a string body is no longer a paragraph");
  eq(holder!.getAttribute("class"), BODY_CLASSES.join(" "), "the string body's classes changed");
  eq(nesting(from).length, 0, "a string body raised a DOM-nesting warning");
});

// CONTROL. No body, no body element: the title stands alone, as before.
//
// MUTANT "body element always drawn" (the `req.body &&` guard removed, so an
// absent body falls to the non-string branch). Observed, 3/4:
//   x control: a confirm with no body draws no body element: an absent body
//     drew an empty body element
//     expected 0
//     got      1
await test("control: a confirm with no body draws no body element", async () => {
  await mountHost();
  await ask({ title: "Disconnect all?" });
  const d = dialog();
  assert(d, "the confirm never opened");
  assert(/Disconnect all\?/.test(d!.querySelector("h2")?.textContent ?? ""), "the title is missing");
  const bodies = Array.from(d!.querySelectorAll("p, div"))
    .filter((el) => BODY_CLASSES.every((c) => el.classList.contains(c)));
  eq(bodies.length, 0, "an absent body drew an empty body element");
});

// -------------------------------------------------------------------- report
await unmountHost();
console.error = realError;
const total = passed + failed;
console.log(`confirmHostBody.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
