// r7Primitives.test.tsx - the three shared primitives wave R7 is built out of.
//
//   Run directly:  npx tsx src/next/ui/__tests__/r7Primitives.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT THIS IS ABOUT. `Disclosure`, `NumberField` and `LockNote` land in ~50
// call sites across nine rebuilt areas, so a broken promise in one of them is a
// broken promise on twenty screens at once. The contracts worth a test:
//
//   1. HONEST-DISABLED (ARCHITECTURE section 6/8). A locked group must not
//      toggle, must not carry the native `disabled` attribute, and must SAY the
//      reason when pressed. The failure guarded against is the silent one: a
//      row that looks live, swallows the tap and explains nothing.
//   2. The RAW-STRING DRAFT (RotatorCard.tsx:36 / SitePanel.tsx:47). A blank
//      numeric field must never be read as a real 0 - `Number("")` is 0, which
//      is finite, plausible and wrong, and it would be written to the rig. A
//      rejected value restores the last committed one; an out-of-range value is
//      clamped and the clamped number is shown, so what the user sees is what
//      was committed.
//   3. `LockNote` renders NOTHING for a null reason. That is what lets 25 call
//      sites pass `lockedReason` straight through with no ternary, and what
//      stops each of 21 panels inventing its own read-only sentence shape.
//   4. The eight `nx-*` classes exist in `next.css`. jsdom computes no layout,
//      so a class emitted by a component with no rule behind it renders as an
//      unstyled row and no DOM test can see it.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act, useState } = await import("react");
const { createRoot } = await import("react-dom/client");
const { Disclosure, LockNote, NumberField } = await import("../index");

const fs = await import("node:fs");
const url = await import("node:url");
const here = url.fileURLToPath(new URL(".", import.meta.url));
const nextCss = fs.readFileSync(`${here}../../next.css`, "utf8");

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
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

const render = (el: any) => { act(() => { root.render(el); }); };
const q = (sel: string): any => container.querySelector(sel);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const keydown = (el: any, key: string) => {
  act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); });
};
/** Type the way a finger does: through the native value setter React's own
 *  tracker watches, then an `input` event. Deliberately no blur and no Enter -
 *  those are the two commit routes, and half of what this file tests is the
 *  state BETWEEN keystrokes. */
const typeInto = (el: any, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(
    win.HTMLInputElement.prototype, "value",
  )!.set as (v: string) => void;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
const blur = (el: any) => { act(() => { el.focus(); el.blur(); }); };

const REASON = "needs operator or admin access";

// ============================================================ 1. the marker
//
// Vacuity guard. Every assertion below queries by `data-testid` or by class; if
// the forwarding is dropped, those queries return null and the tests that
// assert "not present" would pass for the wrong reason.

{
  render(createElement("div", null,
    createElement(Disclosure, {
      key: "d", summary: "ADVANCED", sub: "3 settings", "data-testid": "disc",
    } as any, createElement("span", { "data-testid": "disc-child" }, "body")),
    createElement(NumberField, {
      key: "n", label: "DITHER SIZE", value: 12, onCommit: () => {},
      unit: "px", ariaLabel: "Dither size, pixels", "data-testid": "num",
    } as any),
    createElement(LockNote, { key: "l", reason: REASON, "data-testid": "note" } as any),
  ));

  test("marker: all three primitives render, forward data-testid and carry their text", () => {
    assert(q('[data-testid="disc"]') != null, "Disclosure did not render or swallowed data-testid");
    assert(q('[data-testid="num"]') != null, "NumberField did not render or swallowed data-testid");
    assert(q('[data-testid="note"]') != null, "LockNote did not render or swallowed data-testid");
    assert(q(".nx-disclosure-head") != null, "no .nx-disclosure-head row");
    assert(q(".nx-numfield") != null, "no .nx-numfield wrapper");
    assert(q(".nx-locknote-glyph") != null, "no lock glyph on the read-only note");
    eq(q('[data-testid="num"]').value, "12", "the number never reached the field");
    eq(q('[data-testid="num"]').getAttribute("aria-label"), "Dither size, pixels",
      "the field has no accessible name: the visible label is an eyebrow word and the " +
      "unit is aria-hidden, so a reader would announce a bare number");
    eq(q(".nx-numfield-unit").textContent, "px", "the unit suffix is missing from the field");
    eq(q('[data-testid="note"]').textContent, `Read-only - ${REASON}`,
      "the read-only note does not carry the reason verbatim: the sentence a panel " +
      "prints while idle must be word-for-word the one a locked press states");
  });
}

// ======================================================== 2. Disclosure toggle

{
  const explains: string[] = [];
  const head = () => q('[data-testid="disc"] .nx-disclosure-head');
  const body = () => q('[data-testid="disc"] .nx-disclosure-body');

  test("Disclosure: the press drives aria-expanded and the body, and a LOCKED one refuses and explains", () => {
    render(createElement(Disclosure, {
      summary: "ADVANCED", sub: "B / M / W handles", "data-testid": "disc",
    } as any, createElement("span", { "data-testid": "disc-child" }, "black point")));

    eq(head().getAttribute("aria-expanded"), "false", "a fresh group is not collapsed");
    eq(body().hidden, true, "the body is visible while the group is collapsed");
    assert(q('[data-testid="disc-child"]') == null,
      "a collapsed group still mounts its children: eight call sites hide fetch-backed " +
      "rows behind it and a closed group must cost nothing");
    assert(!!body().id && head().getAttribute("aria-controls") === body().id,
      "aria-controls does not name the body region, so a reader is told the row " +
      "expands something it cannot find");

    click(head());
    eq(head().getAttribute("aria-expanded"), "true", "pressing the row did not expand it");
    eq(body().hidden, false, "the body stayed hidden after expanding");
    assert(q('[data-testid="disc-child"]') != null, "the children never mounted");

    click(head());
    eq(head().getAttribute("aria-expanded"), "false", "pressing the row again did not collapse it");
    eq(body().hidden, true, "the body stayed visible after collapsing");

    // ---- locked
    render(createElement(Disclosure, {
      summary: "ADVANCED", lockedReason: REASON, "data-testid": "disc",
      onExplain: (r: string) => { explains.push(r); },
    } as any, createElement("span", { "data-testid": "disc-child" }, "black point")));

    eq(head().getAttribute("aria-disabled"), "true", "a locked group is not marked aria-disabled");
    assert(!head().hasAttribute("disabled"),
      "the native disabled attribute is on the row: it leaves the tab order and takes " +
      "the reason out of the accessibility tree with it");
    eq(head().getAttribute("title"), REASON, "the reason is not readable without pressing");

    click(head());
    eq(head().getAttribute("aria-expanded"), "false",
      "a locked group toggled anyway: the lock is decoration");
    eq(body().hidden, true, "a locked group revealed its body");
    eq(explains.length, 1, "a locked press said nothing - the silent-swallow defect");
    eq(explains[0], REASON, "the explanation carried the wrong reason");
  });
}

// ========================================================= 3. NumberField draft

{
  const commits: number[] = [];
  const explains: string[] = [];
  function NumHarness({ locked }: { locked: string | null }) {
    const [v, setV] = useState(50);
    return createElement(NumberField, {
      label: "REFOCUS EVERY", value: v, min: 1, max: 100,
      onCommit: (n: number) => { commits.push(n); setV(n); },
      ariaLabel: "Refocus every N frames", lockedReason: locked,
      onExplain: (r: string) => { explains.push(r); },
      "data-testid": "num",
    } as any);
  }
  const inp = () => q('[data-testid="num"]');

  test("NumberField: the field's own text is the draft - blank is rejected, out of range is clamped", () => {
    render(createElement(NumHarness, { locked: null } as any));

    // (a) a trailing "." survives. A field driven from `String(Number(text))`
    //     wipes it on every keystroke, so "1.5" could never be typed.
    typeInto(inp(), "1.");
    eq(inp().value, "1.", "the field re-parsed mid-typing and wiped the trailing dot");
    eq(commits.length, 0, "typing committed - every keystroke would write to the rig");

    // (b) blank is REJECTED, never read as 0 (`Number("") === 0`).
    typeInto(inp(), "");
    blur(inp());
    eq(commits.length, 0,
      "a blank field committed: Number(\"\") is 0, which is finite, plausible and wrong");
    eq(inp().value, "50", "a rejected value did not restore the last committed number");

    // (c) out of range is clamped, and the clamped number is what the field
    //     shows - what the user sees is what was written.
    typeInto(inp(), "999");
    blur(inp());
    eq(commits.length, 1, "a clampable value never committed");
    eq(commits[0], 100, "the value was not clamped to max on commit");
    eq(inp().value, "100", "the field kept showing 999 after committing 100");

    // (d) locked: read-only, not `disabled`, and a tap says why.
    render(createElement(NumHarness, { locked: REASON } as any));
    eq(inp().readOnly, true, "a locked numeric field is not readOnly");
    assert(!inp().hasAttribute("disabled"),
      "the native disabled attribute is on it: the number can no longer be selected and copied");
    typeInto(inp(), "7");
    eq(inp().value, "100", "a locked field accepted a keystroke");
    keydown(inp(), "Enter");
    blur(inp());
    eq(commits.length, 1, "a locked field committed on Enter or on blur");
    click(inp());
    eq(explains.length, 1, "tapping a locked field explained nothing");
    eq(explains[0], REASON, "the locked field explained the wrong thing");
  });
}

// ============================ 3b. NumberField resetKey: the refused write
//
// The draft re-syncs from `value`, so a write the rig REFUSED - where `value`
// never moves at all - left the typed number in the box. Nothing on the screen
// then said the heater was still on the old setting: 45 in a box next to a rig
// running 30, which is the shape of every "I set that hours ago" bug report.
// `resetKey` is how the caller says "that write did not land"; `writeDew` and
// `writeTempComp` bump it in their refusal branches.

{
  const attempts: number[] = [];
  /** A field whose owner REFUSES every commit: `value` is a constant, exactly
   *  as a config field is when the POST came back 4xx. */
  function RefusingHarness() {
    const [key, setKey] = useState(0);
    return createElement(NumberField, {
      label: "MAX POWER", value: 30, min: 0, max: 100, integer: true,
      onCommit: (n: number) => { attempts.push(n); setKey((k) => k + 1); },
      ariaLabel: "Maximum dew heater power, percent",
      resetKey: key,
      "data-testid": "refnum",
    } as any);
  }
  const rinp = () => q('[data-testid="refnum"]');

  test("NumberField: a refused write puts the rig's own number back in the box", () => {
    render(createElement(RefusingHarness, null));
    eq(rinp().value, "30", "the harness did not start at the rig's number");

    typeInto(rinp(), "45");
    blur(rinp());
    eq(attempts.length, 1, "the commit never fired, so nothing was refused and this is vacuous");
    eq(attempts[0], 45, "the wrong number was sent");
    eq(rinp().value, "30",
      "the refused number is still in the box: the screen claims 45 and the rig is on 30, "
      + "and nothing on the screen says which one is true");
  });

  test("NumberField: resetKey does not fight the user between keystrokes", () => {
    // The reset is a one-shot on the BUMP, not a per-render clamp: a field that
    // restored `value` on every render could never be typed into at all.
    typeInto(rinp(), "7");
    eq(rinp().value, "7", "the draft was wiped mid-typing - the field cannot be used");
    eq(attempts.length, 1, "typing committed");
  });
}

// ============================================================== 4. LockNote

{
  test("LockNote: a null reason renders NOTHING, so lockedReason passes straight through", () => {
    // The component's own return value, not only the DOM: the contract 25 call
    // sites rely on is `null` - `<LockNote reason={lockedReason} />` with no
    // ternary and no `&&` leaving a stray value in the tree.
    eq(LockNote({ reason: null }), null,
      "LockNote returned a node for a null reason: every caller now needs its own " +
      "`reason && <LockNote/>` ternary, which is the per-panel shape this component removes");
    eq(LockNote({ reason: "" }), null, "an empty reason produced a note with no reason in it");

    render(createElement("div", { "data-testid": "wrap" },
      createElement(LockNote, { reason: null, "data-testid": "note" } as any)));
    assert(q('[data-testid="note"]') == null, "a null reason still rendered a note element");
    assert(q(".nx-locknote") == null, "a null reason still rendered the note's box");
    eq(q('[data-testid="wrap"]').textContent, "",
      "a null reason printed a bare \"Read-only\" with nothing after it");
  });
}

// ====================================================== 5. the CSS behind them

{
  /** The declaration block of the first `.cls { ... }` rule whose selector list
   *  contains `.cls` as a standalone selector (not `.other .cls`, not
   *  `.cls-suffix`). Comments are stripped first so a note mentioning the class
   *  cannot be mistaken for a rule. `shellCss.test.ts`'s idiom. */
  function ruleBodyFor(cls: string, source: string): string | null {
    const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "");
    const re = /([^{}]+)\{([^}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(stripped)) !== null) {
      const selectors = m[1].split(",").map((s) => s.trim());
      if (selectors.includes(`.${cls}`)) return m[2];
    }
    return null;
  }

  const CLASSES = [
    "nx-disclosure", "nx-disclosure-head", "nx-disclosure-body", "nx-disclosure-chev",
    "nx-numfield", "nx-numfield-unit", "nx-locknote", "nx-locknote-glyph",
  ];

  const SOURCES = ["Disclosure.tsx", "NumberField.tsx", "LockNote.tsx"].map((f) => ({
    file: f,
    // Comments stripped: "honest-disabled" and "not `disabled`" are what these
    // files are ABOUT, and every occurrence of the word is in prose.
    text: fs.readFileSync(`${here}../${f}`, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, ""),
  }));

  test("next.css: every class the three primitives emit has a rule, and no `disabled` anywhere", () => {
    for (const cls of CLASSES) {
      assert(ruleBodyFor(cls, nextCss) != null,
        `.${cls} is emitted by a component but has no rule in next.css: jsdom computes ` +
        "no layout, so an unstyled row renders and no DOM test can see it");
    }
    // The two behaviours that live only in CSS. `display: flex` on the body
    // outranks the UA sheet's `[hidden] { display: none }`, so without this the
    // closed group keeps its box and its gaps; and the chevron's rotation has
    // to stop under reduced motion like every other transition in the file.
    assert(/\.nx-disclosure-body\[hidden\][^{]*\{[^}]*display\s*:\s*none/.test(nextCss),
      ".nx-disclosure-body[hidden] must declare `display: none`, or the closed group's " +
      "flex box outranks the UA stylesheet and the group never really closes");
    const reduced = /@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*)\}/.exec(nextCss);
    assert(reduced != null && /\.nx-disclosure-chev/.test(reduced[1]),
      "the chevron's rotate transition is not cancelled under prefers-reduced-motion");

    // No native `disabled`, in the source and as the DOM sees it.
    for (const s of SOURCES) {
      assert(!/[^-\w]disabled[^-\w]/.test(s.text),
        `${s.file} uses the native disabled attribute: it strips the control from the ` +
        "accessibility tree and takes the reason with it (use lockedReason + onExplain)");
    }
    render(createElement("div", null,
      createElement(Disclosure, {
        key: "d", summary: "ADVANCED", lockedReason: REASON, "data-testid": "d2",
      } as any, createElement("span", null, "body")),
      createElement(NumberField, {
        key: "n", label: "DITHER", value: 3, onCommit: () => {}, lockedReason: REASON,
        ariaLabel: "Dither every N frames", "data-testid": "n2",
      } as any),
      createElement(LockNote, { key: "l", reason: REASON } as any),
    ));
    assert(container.querySelector("[disabled]") == null,
      "a locked primitive rendered a natively disabled element");
    assert(container.querySelectorAll('[aria-disabled="true"]').length === 2,
      "both locked controls must be marked aria-disabled: the lock reached neither the " +
      "DOM nor a screen reader");
  });
}

act(() => { root.unmount(); });

// --------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`r7Primitives.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default { passed, failed, total };
export { passed, failed, total };
