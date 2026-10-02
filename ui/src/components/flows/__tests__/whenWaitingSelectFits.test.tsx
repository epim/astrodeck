// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// whenWaitingSelectFits.test.tsx - the flow's "While a mosaic waits" setting
// shows its whole value in all three places it is edited (#469; mosaic slice
// S7, spec 1.6 and 2.4 RUN).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx
//                  src/components/flows/__tests__/whenWaitingSelectFits.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The S5 real-page probe's screenshots showed the value cut at 1440 x 900: in
// the Target modal's RUN section, beside its label in the 360 px column,
// "Shoot later targets, then c"; in the classic inspector's flow overview, at
// the edge of the 284 px column, "Shoot later targets, then come ba". A native
// select paints its value outside the DOM's text layout, so the probe's
// text-clip check had nothing to measure, and it cannot wrap: the value fits
// in the control's inner width or it is cut. The #/next overview is a
// segmented control in a horizontal scroller, where the two options side by
// side are wider than the column and the second, when chosen, sat out of view.
//
// jsdom lays nothing out, so each case holds the arrangement in the rendered
// DOM and then the width arithmetic, from the stylesheets and class names the
// control really gets: IBM Plex Mono advances every glyph 0.6 em (measured on
// the real page, 35 characters at 12px are 252 px), so the longest option's
// width is its length times 0.6 times the font size, and it must not exceed
// the select's width less its border and padding. The real page at 1440 x 900
// agreed with every number here (the task report has the measure).
//
// A DESKTOP COLUMN THAT SCROLLS LOSES ITS SCROLLBAR'S WIDTH (the S7
// integration). All three columns are `overflow-y: auto`, and index.css makes
// every scrollbar thin. The S7-ULAYOUT verifier measured the classic
// inspector in Chromium on Windows with real scrollbars and found the thin
// one takes 10 px, so the 11px select that fitted the resting column (231 px
// in 235) was cut whenever the overview's lists made it scroll (231 in 225).
// The real-page probe could not see it: Playwright launches Chromium with
// --hide-scrollbars by default. So every desktop column below is measured
// less THIN_SCROLLBAR_PX when its rule scrolls. The phone landscape width is
// not: a phone's scrollbar overlays the content and takes none.
//
// Every case was run RED under a named mutation in a private copy of ui/, and
// each "Observed" quote is the run's failure line verbatim, wrapped, less the
// harness's leading marker. Each mutant turned its own case red and left the
// other two green (every run passed 2/3), which is the control: no case here
// grades another place's control.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {} });
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "HTMLSelectElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "getComputedStyle", "matchMedia", "fetch", "WebSocket", "location", "history"]) {
  Object.defineProperty(g, k, { value: k === "window" ? win : win[k], writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;

const { readFileSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");
const src = (rel: string): string => readFileSync(fileURLToPath(new URL(`../../../${rel}`, import.meta.url)), "utf8");
const INDEX_CSS = src("index.css");
const FRAMING_CSS = src("components/flows/framing/framing.css");
const NEXT_CSS = src("next/next.css");
const INSPECTOR_CSS = src("next/hubs/session/flows/inspector/inspector.css");

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { createParams } = await import("../nodeDefs");
const { FLOW_SETTINGS } = await import("../flowsTypes");
const { RunSection, WHEN_WAITING_LABEL } = await import("../framing/sections/RunSection");
const FlowInspector = (await import("../FlowInspector")).default;
const { FlowInspectorColumn } = await import("../../../next/hubs/session/flows/inspector/FlowInspectorColumn");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { act(() => { root.render(null); }); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root");
const root = createRoot(container);
function render(el: any): void { act(() => { root.render(el); }); }

// ------------------------------------------------------------- the arithmetic
/** IBM Plex Mono: every glyph is 600 units of a 1000-unit em. */
const MONO_EM = 0.6;
/** A desktop browser's thin scrollbar (`scrollbar-width: thin`, index.css),
 *  measured at 10 px in Chromium on Windows by the S7-ULAYOUT verifier: what
 *  a scrolling column's content box loses (see the header). */
const THIN_SCROLLBAR_PX = 10;
const scrolls = (overflowY: string | null): boolean => !!overflowY && /\b(?:auto|scroll)\b/.test(overflowY);
const OPTIONS: readonly string[] = FLOW_SETTINGS.whenWaiting.options;
const LONGEST = OPTIONS.reduce((a, b) => (b.length > a.length ? b : a), "");

/** Every block in a stylesheet whose selector list holds `selector` exactly,
 *  at any nesting (a rule inside @container or @media counts), comments
 *  stripped. */
function blocks(css: string, selector: string): string[] {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  for (const m of bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1].split(",").map((s) => s.trim().replace(/\s+/g, " ")).includes(selector)) out.push(m[2]);
  }
  return out;
}
function declared(block: string, prop: string): string | null {
  const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(block);
  return m ? m[1].trim() : null;
}
/** One px length out of a declaration, or its first term for a shorthand's
 *  horizontal side: `padding: 0 12px` -> 12, `padding: 6px 9px` -> 9. */
function px(v: string | null, what: string): number {
  assert(v !== null, `${what} is not declared`);
  const terms = v!.split(/\s+/);
  const t = terms.length >= 2 ? terms[1] : terms[0];
  const m = /^(-?\d+(?:\.\d+)?)(px)?$/.exec(t === "0" ? "0px" : t);
  assert(!!m, `${what} is not a px length: ${v}`);
  return Number(m![1]);
}
function onlyOne(css: string, selector: string, prop: string): string {
  const got = [...new Set(blocks(css, selector).map((b) => declared(b, prop)).filter((v): v is string => v !== null))];
  assert(got.length === 1, `${selector} declares ${prop} as ${JSON.stringify(got)}, expected one value`);
  return got[0];
}

/** `.field` in index.css: what every select here starts from. */
const FIELD = {
  font: px(onlyOne(INDEX_CSS, ".field", "font-size"), ".field font-size"),
  pad: px(onlyOne(INDEX_CSS, ".field", "padding"), ".field padding"),
  border: px(onlyOne(INDEX_CSS, ".field", "border").split(/\s+/)[0], ".field border"),
};
const textPx = (s: string, font: number): number => s.length * MONO_EM * font;
const f1 = (n: number): string => `${+n.toFixed(2)}`;

// ---------------------------------------------------------------- fixtures
function runProps(over: Record<string, unknown> = {}): any {
  return {
    onePanel: false, noMosaic: false, loop: true, loopLock: null, passes: 1, minVisit: 0,
    whenWaiting: FLOW_SETTINGS.whenWaiting.default, lines: null, linesNote: null,
    countsNotice: null, campaign: null,
    onLoop() {}, onPasses() {}, onMinVisit() {}, onWhenWaiting() {}, explain() {},
    ...over,
  };
}
function seed(settings: Record<string, string> = {}): void {
  const target = { id: "ta", type: "target", x: 0, y: 0, params: { ...createParams("target"), name: "M31", rows: 2, cols: 2 } };
  const graph = { nodes: [target], edges: [], settings };
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: { id: "f1", name: "M31", folder: "", tagline: "", graph, created_ts: 0, updated_ts: 0,
          last_run: null, last_result: "", readonly: false },
        graph,
        sel: null,
      },
    } as any);
  });
}

// ---------------------------------------------------------------- the modal

// MUTANT "select beside its label" (RunSection.tsx: the label and the select
// back in one `.tfs-row`, the markup before #469). Observed:
//   x the modal's RUN section puts the select on its own row, wide enough
//     for every option: the select shares its row with its label, so it has
//     at most 176.25 px of the 360 px column's 335 px and 156.25 px inside
//     its padding, where "Shoot later targets, then come back" needs 252 px
// MUTANT "the modal select at 13px" (RunSection.tsx: WHEN_WAITING_SELECT's
// fontSize 13, `.field`'s own). Observed:
//   x the modal's RUN section puts the select on its own row, wide enough
//     for every option: in the narrowest phone landscape (667 px, the
//     controls 45% of it) the select is 256.15 px inside its border and
//     padding, and "Shoot later targets, then come back" is 273 px at 13px
await test("the modal's RUN section puts the select on its own row, wide enough for every option", () => {
  render(h(RunSection, runProps()));
  const sel = container.querySelector("#tfs-when-waiting");
  const label = container.querySelector('label[for="tfs-when-waiting"]');
  assert(sel && label, "no whenWaiting select and label in the RUN section");
  assert(label.textContent === WHEN_WAITING_LABEL, `the label reads "${label.textContent}"`);
  const selRow = sel.closest(".tfs-row");
  const labelRow = label.closest(".tfs-row");
  assert(selRow && labelRow, "the select or its label is in no .tfs-row");

  // The arithmetic's inputs, from framing.css. The desktop column is the
  // one rule that gives `.tfs-controls` a px width; the phone landscape rule
  // gives it a flex basis of 45%, and the spec's narrowest landscape phone is
  // 667 px wide (2.2).
  const controls = ".tfs .tfs-body > .tfs-controls";
  const columnW = px(onlyOne(FRAMING_CSS, controls, "width"), `${controls} width`);
  const columnBorder = px(onlyOne(FRAMING_CSS, controls, "border-left").split(/\s+/)[0], `${controls} border-left`);
  const basis = blocks(FRAMING_CSS, controls).map((b) => declared(b, "flex")).find((v) => v && /%$/.test(v));
  assert(basis, `${controls} has no percentage flex basis for phone landscape`);
  const landscapeW = 667 * Number(/(\d+(?:\.\d+)?)%$/.exec(basis!)![1]) / 100;
  const rowPad = px(onlyOne(FRAMING_CSS, ".tfs-row", "padding"), ".tfs-row padding");
  const rowGap = px(onlyOne(FRAMING_CSS, ".tfs-row", "gap"), ".tfs-row gap");
  const labelBasis = Number(/(\d+(?:\.\d+)?)%/.exec(onlyOne(FRAMING_CSS, ".tfs-label", "flex"))![1]) / 100;
  const font = sel.style.fontSize ? parseFloat(sel.style.fontSize) : FIELD.font;
  const need = textPx(LONGEST, font);

  // The sheet scrolls in `.tfs-scroller`, which holds every section: on a
  // desktop its thin scrollbar comes off the column once it scrolls.
  const scroller = onlyOne(FRAMING_CSS, ".tfs-scroller", "overflow-y");
  const bar = scrolls(scroller) ? THIN_SCROLLBAR_PX : 0;
  const desktopRow = columnW - columnBorder - bar - 2 * rowPad;
  if (selRow === labelRow) {
    // Beside its label the select has what the label's basis and the gap
    // leave, at most; the flexbox can only give it less once the label has
    // its minimum.
    const most = desktopRow - labelBasis * desktopRow - rowGap;
    throw new Error(`the select shares its row with its label, so it has at most ${f1(most)} px of the ` +
      `${columnW} px column's ${f1(desktopRow)} px and ${f1(most - 2 * FIELD.border - 2 * FIELD.pad)} px inside ` +
      `its padding, where "${LONGEST}" needs ${f1(need)} px`);
  }
  assert(selRow.previousElementSibling === labelRow, "the select's row is not the one right under its label's");
  assert(selRow.querySelectorAll("select, input, button, label").length === 1,
    "the select's row holds another control or label beside it");
  for (const [where, rowW] of [
    [`the ${columnW} px column${bar ? ", scrolling" : ""}`, desktopRow],
    ["the narrowest phone landscape (667 px, the controls 45% of it)", landscapeW - 2 * rowPad],
  ] as [string, number][]) {
    const inner = rowW - 2 * FIELD.border - 2 * FIELD.pad;
    assert(need <= inner, `in ${where} the select is ${f1(inner)} px inside its border and padding, and ` +
      `"${LONGEST}" is ${f1(need)} px at ${font}px`);
  }
});

// ---------------------------------------------------------- classic overview

// MUTANT "the classic select back at 12px" (FlowInspector.tsx: the select's
// `!text-[11px]` made `!text-[12px]`, the class before #469). Observed:
//   x the classic flow overview's select fits the 284 px column: the select
//     is 235 px inside its border and padding in the 284 px column, and
//     "Shoot later targets, then come back" is 252 px at 12px
// (S7-ULAYOUT's record, from before the scrollbar was counted.) Since the S7
// integration the column is measured scrolling, and the select is 10.5px.
// MUTANT "the classic select at 11px" (`!text-[10.5px]` made `!text-[11px]`,
// S7-ULAYOUT's size), in the integration's private copy (the session
// scratchpad's S7-INTEG-r2-mut, from a byte backup, sha256 checked after):
//   x the classic flow overview's select fits the 284 px column: the select
//     is 225 px inside its border and padding in the 284 px column,
//     scrolling (its 10 px scrollbar off), and "Shoot later targets, then
//     come back" is 231 px at 11px
await test("the classic flow overview's select fits the 284 px column", () => {
  seed();
  render(h(FlowInspector, { variant: "column" }));
  const column = container.querySelector("[data-flows-inspector]");
  const sel = container.querySelector('select[data-flows-setting="whenWaiting"]');
  assert(column && sel, "no inspector column or whenWaiting select: the overview did not render");
  // Under its label already: `Field` is a flex column, the label's text above
  // and the select alone below it, so the select is the column's full width.
  const field = sel.parentElement;
  assert(field.tagName === "LABEL" && /\bflex-col\b/.test(field.className),
    `the select is not alone under its label in a Field: its parent is <${field.tagName.toLowerCase()} ` +
    `class="${field.className}">`);
  assert(field.querySelectorAll("select, input, button").length === 1, "the select's Field holds another control");

  const cls = String(column.className);
  const w = /(?:^|\s)w-\[(\d+)px\]/.exec(cls);
  const padX = /(?:^|\s)px-(\d+(?:\.\d+)?)(?:\s|$)/.exec(cls);
  assert(w && padX && /(?:^|\s)border-l(?:\s|$)/.test(cls), `the column's width, padding or border moved: "${cls}"`);
  const columnW = Number(w![1]);
  // The column scrolls (`overflow-y-auto`) once the overview's lists pass its
  // height, and its thin scrollbar then comes off the content box.
  const bar = /(?:^|\s)overflow-y-(?:auto|scroll)(?:\s|$)/.test(cls) ? THIN_SCROLLBAR_PX : 0;
  const content = columnW - 1 - bar - 2 * Number(padX![1]) * 4;   // Tailwind spacing: 0.25rem = 4px
  // The select's own `!` utilities beat `.field`, which is unlayered.
  const selCls = String(sel.className);
  const font = Number(/!text-\[(\d+(?:\.\d+)?)px\]/.exec(selCls)?.[1] ?? FIELD.font);
  const pad = Number(/!px-\[(\d+(?:\.\d+)?)px\]/.exec(selCls)?.[1] ?? FIELD.pad);
  const inner = content - 2 * FIELD.border - 2 * pad;
  const need = textPx(LONGEST, font);
  assert(need <= inner, `the select is ${f1(inner)} px inside its border and padding in the ${columnW} px ` +
    `column${bar ? `, scrolling (its ${bar} px scrollbar off)` : ""}, and "${LONGEST}" is ${f1(need)} px at ${font}px`);
});

// ------------------------------------------------------------ #/next overview

// MUTANT "the #/next choice back in its scroller" (FlowInspectorColumn.tsx:
// the Segmented wrapped in <div className="nx-flowfield-scroll"> again, the
// markup before #469). Observed:
//   x the #/next flow overview keeps both choices in the column, wrapping
//     rather than scrolling: the choice sits in .nx-flowfield-scroll, which
//     scrolls sideways (overflow-x: auto), so an option wider than the
//     column's share is out of view rather than wrapped
await test("the #/next flow overview keeps both choices in the column, wrapping rather than scrolling", () => {
  seed({ whenWaiting: "Wait for the mosaic" });
  render(h(FlowInspectorColumn, { variant: "column" }));
  const group = container.querySelector('[data-testid="flow-when-waiting-choice"]');
  const column = container.querySelector(".nx-flowins");
  assert(group && column, "no whenWaiting choice or inspector column: the overview did not render");
  const on = group.querySelector('[aria-checked="true"]');
  assert(on && on.getAttribute("data-value") === "Wait for the mosaic",
    "the choice does not show the flow's value as the one on");

  const rulesFor = (cls: string): string[] =>
    [...blocks(NEXT_CSS, `.${cls}`), ...blocks(INSPECTOR_CSS, `.${cls}`)];
  // Nothing between the group and the column scrolls sideways.
  for (let a = group.parentElement; a && a !== column.parentElement; a = a.parentElement) {
    for (const cls of String(a.className).split(/\s+/).filter(Boolean)) {
      for (const b of rulesFor(cls)) {
        const ox = declared(b, "overflow-x") ?? declared(b, "overflow");
        assert(!ox || !/\b(?:auto|scroll)\b/.test(ox),
          `the choice sits in .${cls}, which scrolls sideways (overflow-x: ${ox}), so an option wider than ` +
          "the column's share is out of view rather than wrapped");
      }
    }
  }
  // The group's parent is a flex column, which stretches the group to the
  // column's width, and the options may wrap their words.
  const parentRules = String(group.parentElement.className).split(/\s+/).filter(Boolean).flatMap(rulesFor);
  assert(parentRules.some((b) => declared(b, "display") === "flex" && declared(b, "flex-direction") === "column"),
    `the choice's parent (class "${group.parentElement.className}") is not a flex column, so the group is ` +
    "not held to the column's width");
  for (const cls of ["nx-seg", "nx-seg-opt", "nx-seg-label"]) {
    for (const b of rulesFor(cls)) {
      assert(declared(b, "white-space") !== "nowrap", `.${cls} forbids wrapping (white-space: nowrap)`);
    }
  }
  // Wrapped, each option is as narrow as its longest word plus its padding;
  // side by side they must fit the column's content box inside the group's
  // border.
  const colRule = blocks(INSPECTOR_CSS, '.nx-flowins[data-variant="column"]')[0];
  assert(colRule, 'no .nx-flowins[data-variant="column"] rule in inspector.css');
  const colW = px(declared(colRule, "width"), "the column's width");
  const colPad = px(declared(colRule, "padding"), "the column's padding");
  const colBorder = px((declared(colRule, "border-left") ?? "0").split(/\s+/)[0], "the column's border-left");
  const optRule = blocks(NEXT_CSS, ".nx-seg-opt")[0];
  const optFont = px(declared(optRule, "font-size"), ".nx-seg-opt font-size");
  const optPad = px(declared(optRule, "padding"), ".nx-seg-opt padding");
  const groupBorder = px((declared(blocks(NEXT_CSS, ".nx-seg")[0], "border") ?? "0").split(/\s+/)[0], ".nx-seg border");
  const bar = scrolls(declared(colRule, "overflow-y")) ? THIN_SCROLLBAR_PX : 0;
  const content = colW - colBorder - bar - 2 * colPad;
  const narrowest = OPTIONS.reduce((sum, o) =>
    sum + textPx(o.split(/\s+/).reduce((a, b) => (b.length > a.length ? b : a), ""), optFont) + 2 * optPad, 0)
    + 2 * groupBorder;
  assert(narrowest <= content, `wrapped to their longest words the options need ${f1(narrowest)} px, and the ` +
    `column holds ${f1(content)} px`);
  assert(group.querySelectorAll('[role="radio"]').length === OPTIONS.length, "an option is missing from the choice");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nwhenWaitingSelectFits.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
