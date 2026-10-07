// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14AutoResumeHelp.test.tsx - DUSK WINDOW's Automatic resume row, MOUNTED in
// both inspectors: the label, the On/Off control, and the INFO ICON that
// carries the field's `help` (#195, owner ruling 7 on #189). The classic row's
// icon is the legacy `InfoDot`; the #/next row's is its own `InfoButton` on the
// new UI's `Popover` (r7Parity.test.ts keeps components/ out of next/), so a
// "press" is a pointer tap on the first and a click on the second.
//
//   Run directly:  npx tsx src/components/flows/__tests__/w14AutoResumeHelp.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `FieldDef.help` was data only (wave 4): nothing drew it, so the text the
// owner asked for was in the source and on no screen. Both `FlowFieldRow`s now
// draw it as an `InfoDot` beside the label, and these cases mount each row for
// the DUSK `autoResume` field and look for what a person and a screen reader
// get:
//
//   1. the exact label, the On/Off control and the stored value reading On;
//   2. an info button on the row, named for the field, that OPENS on a tap
//      (the coarse-pointer path; the classic hover path is `Tooltip`'s own,
//      held in its own tests) and shows the help text in a `role="tooltip"`
//      bubble; a second tap closes the #/next one;
//   3. the control DESCRIBED by the help (`aria-describedby` to an element that
//      is in the document and holds the text), because the bubble is mounted
//      only while it is open;
//   4. a press on the icon never reaches the control (the select does not
//      open, nothing is written to the store);
//   5. a field with no help renders exactly as before: no icon, no description.
//
// NAMED MUTANTS (each run from a byte backup inside the worktree, restored and
// sha256-compared, the mutant text grepped out afterwards; the first failure
// is quoted):
//
//   "help not rendered, classic": the `InfoDot` removed from
//     components/flows/FlowFieldRow.tsx. RED, observed:
//       w14AutoResumeHelp.test: 11/15 passed
//       x classic: the row carries an info icon, named for the field: no info
//         button on the classic row for the DUSK autoResume field: the help
//         text exists in nodeDefs.ts and is drawn nowhere
//   "help not rendered, #/next": the `InfoDot` removed from
//     next/hubs/session/flows/inspector/FlowFieldRow.tsx. RED, observed:
//       w14AutoResumeHelp.test: 12/15 passed
//       x next: the row carries an info icon, named for the field: no info
//         button on the next row for the DUSK autoResume field: the help text
//         exists in nodeDefs.ts and is drawn nowhere

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import { installAutoRaf } from "../../../testing/rafPolyfill";
import { createElement, act } from "react";
import { createRoot } from "react-dom/client";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const { useStore } = await import("../../../store");
const { NODE_DEFS, AUTO_RESUME_HELP, AUTO_RESUME_LABEL, fieldValue } = await import("../nodeDefs");
const Classic = (await import("../FlowFieldRow")).default;
const { FlowFieldRow: Next } = await import(
  "../../../next/hubs/session/flows/inspector/FlowFieldRow");

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

const FIELD = NODE_DEFS.dusk.fields.find((f) => f.key === "autoResume")!;
const START = NODE_DEFS.dusk.fields.find((f) => f.key === "start")!;

function seed(params: Record<string, string | number>): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      flows: {
        ...s.flows,
        graph: { nodes: [{ id: "d", type: "dusk", x: 0, y: 0, params }], edges: [] },
        sel: { kind: "node", id: "d" },
        compiled: null,
      },
    } as any);
  });
}
const storedParams = (): Record<string, string | number> =>
  useStore.getState().flows.graph.nodes.find((n) => n.id === "d")!.params;

function mount(which: "classic" | "next", field = FIELD, params: Record<string, string | number> = { ...NODE_DEFS.dusk.params }): void {
  seed(params);
  // `fieldValue`, as FlowInspector and FlowNodeEditor pass it: a row is never
  // handed the raw param, so a stored flow with no key reads its derived value.
  const value = fieldValue(field, params);
  act(() => { root.render(createElement("div")); });
  act(() => {
    root.render(which === "classic"
      ? createElement(Classic, { nodeId: "d", field, value })
      : createElement(Next, { nodeId: "d", nodeType: "dusk", field, value }));
  });
}

const text = (el: any): string => String(el?.textContent ?? "").replace(/\s+/g, " ").trim();
/** The info button of the row: the focusable `role="button"` the icon is. */
const infoButton = (): any =>
  container.querySelector('[role="button"][aria-label^="Explain:"], button[aria-label^="Explain:"]');
const bubble = (): any => win.document.querySelector('[role="tooltip"]');
/** A tap on the classic icon: pointerdown then pointerup of a touch, the
 *  legacy tooltip's coarse-pointer path. */
function tapClassic(el: any): void {
  act(() => {
    el.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new win.MouseEvent("pointerup", { bubbles: true, cancelable: true }));
  });
}
/** A tap on the #/next icon: a click, which is what a touch tap, a mouse click
 *  and a keyboard's Enter and Space all make of a <button>. */
function clickEl(el: any): boolean {
  let prevented = false;
  act(() => {
    const ev = new win.MouseEvent("click", { bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    prevented = ev.defaultPrevented;
  });
  return prevented;
}
const press = (which: "classic" | "next", el: any): void =>
  which === "classic" ? tapClassic(el) : void clickEl(el);
function closeBubble(): void {
  act(() => { win.document.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true })); });
}

// --------------------------------------------------------------------- tests

for (const which of ["classic", "next"] as const) {
  test(`${which}: the row says the owner's label, offers On and Off, and reads On`, () => {
    mount(which);
    const row = container.firstElementChild;
    assert(text(row).includes(AUTO_RESUME_LABEL),
      `the row does not carry the label: ${text(row)}`);
    eq(AUTO_RESUME_LABEL, "Automatic resume on subsequent nights until capture quota is fulfilled",
      "the exported label is the owner's, exactly");
    if (which === "classic") {
      const sel = container.querySelector("select");
      assert(sel, "the classic row has no select");
      eq([...sel.options].map((o: any) => o.value).join("|"), "On|Off", "the options");
      eq(sel.value, "On", "a DUSK WINDOW that says nothing reads On");
    } else {
      const radios = [...container.querySelectorAll('[role="radio"]')];
      eq(radios.map((r: any) => text(r)).join("|"), "On|Off", "the segments");
      eq(radios.find((r: any) => r.getAttribute("aria-checked") === "true") && text(
        radios.find((r: any) => r.getAttribute("aria-checked") === "true")), "On",
        "a DUSK WINDOW that says nothing reads On");
    }
  });

  // RED under mutant "help not rendered, <which>" (the InfoDot removed from the
  // row): there is no info button, and nothing describes the control.
  test(`${which}: the row carries an info icon, named for the field`, () => {
    mount(which);
    const btn = infoButton();
    assert(btn, `no info button on the ${which} row for the DUSK autoResume field: the help text `
      + `exists in nodeDefs.ts and is drawn nowhere`);
    assert(String(btn.getAttribute("aria-label")).includes(AUTO_RESUME_LABEL),
      `the icon's accessible name does not name the field: ${btn.getAttribute("aria-label")}`);
    // reachable by keyboard: the classic icon is a role=button span with
    // tabindex 0, the #/next one a native <button>
    assert(btn.getAttribute("tabindex") === "0" || btn.tagName === "BUTTON",
      "the icon cannot be reached by keyboard");
  });

  test(`${which}: a tap on the icon opens the help text in a tooltip`, () => {
    mount(which);
    eq(bubble(), null, "premise: the bubble starts closed");
    press(which, infoButton());
    const b = bubble();
    assert(b, "a tap on the info icon opened nothing");
    eq(text(b), AUTO_RESUME_HELP, "the bubble holds the field's help, verbatim");
    eq(infoButton().getAttribute("aria-describedby"), b.getAttribute("id"),
      "while open, the icon is described by the bubble");
    if (which === "next") {
      press(which, infoButton());
      eq(bubble(), null, "a second tap on the #/next icon did not close it");
      eq(infoButton().getAttribute("aria-expanded"), "false", "the icon still says it is expanded");
      press(which, infoButton());
      assert(bubble(), "a third tap did not open it again");
      closeBubble();
      eq(bubble(), null, "a press outside did not close the #/next popover");
    }
    closeBubble();
  });

  test(`${which}: the control is DESCRIBED by the help, which is in the document while the bubble is closed`, () => {
    mount(which);
    eq(bubble(), null, "premise: the bubble is closed");
    // `aria-describedby` rides on the select (classic) and on the group that
    // wraps the radiogroup (next: `Segmented` takes no such prop).
    const carrier = which === "classic"
      ? container.querySelector("select")
      : container.querySelector('[role="group"][aria-describedby]');
    assert(carrier, "nothing on the row is described by the help");
    const ref = carrier.getAttribute("aria-describedby");
    assert(ref, "aria-describedby is empty");
    const target = win.document.getElementById(ref);
    assert(target, `aria-describedby points at #${ref}, which is not in the document`);
    eq(text(target), AUTO_RESUME_HELP, "the described-by element holds the help text");
    assert(String(target.className).includes("sr-only"),
      "the copy of the help is for a screen reader, not a second visible paragraph");
  });

  test(`${which}: a press on the icon does not reach the control`, () => {
    mount(which);
    const before = JSON.stringify(storedParams());
    const btn = infoButton();
    const prevented = clickEl(btn);
    if (which === "classic") {
      assert(prevented, "the click was not cancelled: inside a <label> a browser would forward it to the control");
    }
    eq(JSON.stringify(storedParams()), before, "a press on the icon wrote a param");
    closeBubble();
  });

  test(`${which}: choosing Off writes the string Off, and a stored Single night is not it`, () => {
    mount(which, FIELD, { ...NODE_DEFS.dusk.params, repeat: "Single night" });
    if (which === "classic") {
      const sel = container.querySelector("select");
      eq(sel.value, "On", "a stored repeat of Single night must NOT read as Off");
      act(() => {
        Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value")!.set!.call(sel, "Off");
        sel.dispatchEvent(new win.Event("change", { bubbles: true }));
      });
    } else {
      const off = [...container.querySelectorAll('[role="radio"]')].find((r: any) => text(r) === "Off");
      assert(off, "no Off segment");
      act(() => { off.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    }
    eq(storedParams().autoResume, "Off", "the edit did not reach the store as the string Off");
    eq(storedParams().repeat, "Single night", "the edit touched repeat");
  });

  // A SAVED FLOW HAS NO `autoResume` KEY: every file 0.3.40 or earlier wrote
  // carries a DUSK with a `repeat` and nothing else, and the server reads that
  // as On (nodes.py `dusk_auto_resume`). The editor must SHOW On, not a blank
  // select, which is what `selectOptions` makes of an empty stored value (an
  // extra "" option, selected): the migration note says "this flow now shows
  // that as ON", and a flow that resumes must not read as undecided. Display
  // only: nothing is written until the operator picks.
  //
  // RED under the nodeDefs.ts mutant "no derived On" (the `derive` removed from
  // the autoResume field), observed:
  //   x classic: a stored DUSK with no autoResume key shows On, not a blank:
  //     the select shows "" for a flow the server resumes
  test(`${which}: a stored DUSK with no autoResume key shows On, not a blank`, () => {
    const stored: Record<string, string | number> = { ...NODE_DEFS.dusk.params, repeat: "Single night" };
    delete stored.autoResume;
    mount(which, FIELD, stored);
    if (which === "classic") {
      const sel = container.querySelector("select");
      eq([...sel.options].map((o: any) => o.value).join("|"), "On|Off",
        "the select grew a blank option for a flow that says nothing");
      eq(sel.value, "On", `the select shows "${sel.value}" for a flow the server resumes`);
    } else {
      const radios = [...container.querySelectorAll('[role="radio"]')];
      eq(radios.map((r: any) => text(r)).join("|"), "On|Off", "the segments");
      const on = radios.find((r: any) => r.getAttribute("aria-checked") === "true");
      assert(on && text(on) === "On",
        "no segment is checked for a flow the server resumes: it reads as undecided");
    }
    eq(storedParams().autoResume, undefined, "showing On wrote a key the operator never chose");
  });

  test(`${which}: a field with no help has no icon and no description`, () => {
    mount(which, START);
    eq(START.help, undefined, "premise: START carries no help");
    eq(infoButton(), null, "an info icon on a field with no help");
    eq(container.querySelector("[aria-describedby]"), null, "a description on a field with no help");
    assert(text(container).includes("Start"), "premise: the Start row rendered");
  });
}

// THE #/next ICON'S 44 PX TARGET (#195: "a 44 px target"). jsdom has no layout, so
// this holds the style that makes the number true. The app's CSS is Tailwind's
// preflight, `box-sizing: border-box` on everything, under which a 14 px box
// with 15 px of padding is a 30 px box (a border box cannot be narrower than its
// padding) and its -15 px margins leave it a footprint of 0: measured in
// Chromium, 30 x 30 with no footprint, so a finger gets a target of 30 px and the
// glyph sits on the label. A 44 x 44 box with no padding is 44 under either box
// model, and a margin of -15 gives the 14 px footprint a 14 px glyph needs.
//
// RED under the mutant "padding instead of size" (INFO_BUTTON put back to
// `width: 14, height: 14, padding: 15`), observed:
//   x next: the info button is a 44 px box of its own, not 14 px plus padding:
//     width 14px: under border-box the 15 px padding makes this a 30 px box
test("next: the info button is a 44 px box of its own, not 14 px plus padding", () => {
  mount("next");
  const btn = infoButton();
  assert(btn, "no info button on the next row");
  eq(btn.style.width, "44px", `width ${btn.style.width}: under border-box the padding makes this a 30 px box`);
  eq(btn.style.height, "44px", `height ${btn.style.height}: under border-box the padding makes this a 30 px box`);
  eq(parseFloat(btn.style.padding || "0"), 0, "padding on a sized box: a border box cannot be narrower than its padding");
  eq(btn.style.margin, "-15px", "the negative margin that keeps the footprint at the glyph's 14 px: (44 - 14) / 2");
  closeBubble();
});

test("classic: a field with help is not one <label> around the icon", () => {
  // A <label> wrapping the icon would put "Explain: ..." into the select's own
  // accessible name and forward a press on the icon to the select.
  mount("classic");
  const btn = infoButton();
  eq(btn.closest("label"), null, "the info icon sits inside a <label>");
  const sel = container.querySelector("select");
  const lab = container.querySelector(`label[for="${sel.id}"]`);
  assert(lab, "the select has no <label for> naming it");
  eq(text(lab), AUTO_RESUME_LABEL, "the select is named by the owner's label, and only by it");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w14AutoResumeHelp.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
