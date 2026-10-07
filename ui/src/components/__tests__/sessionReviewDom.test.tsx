// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sessionReviewDom.test.tsx — frame selection in Session Review, seen the way
// the operator sees it at 2am.
//
//   Run directly:  npx tsx src/components/__tests__/sessionReviewDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FINDING. Tapping a frame card selects it for a bulk accept/reject, and the
// only visual difference used to be `border-accent` vs `border-line`: one 1px
// hairline, same width, no fill, no glyph. Under :root.night those two tokens
// are the SAME red (--accent #ff3a3a vs --line rgba(255,50,50,0.25), index.css
// 109/124) — one hue at two brightnesses on a hairline — so the operator picking
// keepers out of a three-night SHO grid before pressing "mark rejected" was
// reading a tint. index.css says the rule out loud (§633: "Colour cannot carry
// it … so the channel is SHAPE"), and the gallery's own multi-select grid
// (gallery/FrameTile.tsx) already obeys it with a real tick box.
//
// WHAT THIS CAN AND CANNOT PROVE. jsdom applies no stylesheet, so nothing here
// measures contrast. What it CAN do is prove there is a channel that is not a
// colour at all: a box that is EMPTY when the card is unselected and FILLED with
// a tick when it is selected, and that it tracks the same state aria-pressed
// does. A pure-colour regression (dropping the tick and going back to swapping
// two border tokens) fails these assertions; a re-styling that keeps the tick
// does not, which is the right sensitivity.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.scrollIntoView = function () {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// Three frames on one night, one step, so the filters are irrelevant and the
// only thing under test is what a tap does to a card.
const FRAME = (id: string, hfr: number) => ({
  id, ts: 1_770_000_000, night: "2026-08-04", target_id: "t1", step_id: "s1",
  thumb: null, metrics: { hfr, stars: 220, guide_rms: 0.6 },
  auto_accepted: true, override: null,
});
const SESSION = {
  id: "sess1", schema_version: 1, name: "NGC7000 SHO",
  created_ts: 1_770_000_000, updated_ts: 1_770_000_100,
  status: "dormant", nights: ["2026-08-04"], auto_resume: true,
  plan: {
    name: "NGC7000 SHO",
    targets: [{
      id: "t1", name: "NGC7000", ra_hours: 20.98, dec_deg: 44.5,
      steps: [{ id: "s1", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 20 }],
    }],
  },
  frames: [FRAME("f1", 2.4), FRAME("f2", 2.6), FRAME("f3", 3.9)],
};

// A second session, for the mosaic grouping (#188, WP-1): a 2x2 mosaic "Veil"
// whose four targets are listed out of grid order, plus one single target. The
// frames are chosen so each roll-up count is distinguishable: 1-1 holds one
// accepted and one rejected frame, 1-2 one accepted, 2-1 one frame the camera
// accepted that the operator regraded OUT (so only the EFFECTIVE verdict reads
// it as rejected), and 2-2 none at all (a panel set aside still has a row).
const MOSAIC_TARGET = (id: string, row: number, col: number) => ({
  id, name: `Veil ${row + 1}-${col + 1}`, ra_hours: 20.7, dec_deg: 31.2,
  mosaic_group: "g1", panel_row: row, panel_col: col,
  steps: [{ id: `st-${id}`, filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 20 }],
});
const MFRAME = (id: string, target_id: string, over: Record<string, unknown> = {}) => ({
  id, ts: 1_770_000_000, night: "2026-08-04", target_id, step_id: `st-${target_id}`,
  thumb: null, metrics: { hfr: 2.5, stars: 200, guide_rms: 0.6 },
  auto_accepted: true, override: null, ...over,
});
const MOSAIC_SESSION = {
  id: "sess2", schema_version: 1, name: "Veil mosaic",
  created_ts: 1_770_000_000, updated_ts: 1_770_000_100,
  status: "dormant", nights: ["2026-08-04"], auto_resume: true,
  plan: {
    name: "Veil mosaic",
    targets: [
      MOSAIC_TARGET("p22", 1, 1), MOSAIC_TARGET("p11", 0, 0),
      MOSAIC_TARGET("p21", 1, 0), MOSAIC_TARGET("p12", 0, 1),
      { id: "solo", name: "M31", ra_hours: 0.7, dec_deg: 41.3,
        steps: [{ id: "st-solo", filter: "Ha", exposure_s: 300, gain: 100, offset: 10, binning: 1, count: 20 }] },
    ],
    groups: [{
      id: "g1", name: "Veil", kind: "mosaic", mode: "rotate", visit_passes: 1,
      visit_min_s: 0, order: "grid", require_centred: true, max_failed_visits: 3,
      pa_deg: null, rotate: false, angle_tolerance_deg: null, skipped_ids: [], geometry: {},
    }],
  },
  frames: [
    MFRAME("a1", "p11"), MFRAME("a2", "p11", { auto_accepted: false }),
    MFRAME("b1", "p12"), MFRAME("c1", "p21", { override: "reject" }),
    MFRAME("s1", "solo"),
  ],
};

g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  // A regrade is a PATCH; the drawer reads `remaining` off its answer.
  const json: any = (init && init.method === "PATCH") ? { remaining: {} }
    : path.includes("/api/sessions/sess1") ? SESSION
    : path.includes("/api/sessions/sess2") ? MOSAIC_SESSION : {};
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const SessionReviewDrawer = (await import("../sequence/SessionReviewDrawer")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

useStore.setState({
  status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
  principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => {
  root.render(createElement(SessionReviewDrawer, { id: "sess1", onClose: () => {} }));
});
await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

// The drawer renders through <Overlay/>, which portals into a body-level host —
// so everything below reads the DOCUMENT, not the mount container.
const doc = (): any => win.document.body;
const cards = (): any[] =>
  [...doc().querySelectorAll('button[aria-label^="Select frame"]')];
const card = (id: string): any =>
  cards().find((b: any) => b.getAttribute("aria-label") === `Select frame ${id}`);
const tick = (b: any): string | null => {
  const el = b.querySelector("[data-tick]");
  return el ? el.getAttribute("data-tick") : null;
};
const text = (): string => doc().textContent || "";
const click = async (node: any): Promise<void> => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};

// --------------------------------------------------------------- the fixture
test("the review grid mounted with three frames and nothing selected", () => {
  assert(cards().length === 3,
    `${cards().length} frame cards in the drawer, expected 3 — the fixture never loaded, ` +
    "and every assertion below would be about an empty document");
  assert(cards().every((b: any) => b.getAttribute("aria-pressed") === "false"),
    "a card is already selected before anything was tapped");
});

// ------------------------------------------- SELECTION NEEDS A SHAPE (class F)
test("every card carries a tick box, empty, before anything is picked", () => {
  assert(cards().every((b: any) => tick(b) === "off"),
    "there is no tick box on the cards at all — selection is back to being a border " +
    "colour, which under the night palette is one red at two brightnesses on a 1px " +
    "hairline, on the grid feeding a bulk REJECT");
});

await click(card("f2"));

test("tapping a card fills its tick box — a channel that is not a colour", () => {
  const b = card("f2");
  assert(b.getAttribute("aria-pressed") === "true",
    "the tap did not select the card, so the visual assertions below are vacuous");
  assert(tick(b) === "on",
    "the selected card's tick box did not fill: nothing about this card differs from its " +
    "neighbours except hue, which is exactly what a red-filtered tablet cannot show");
  assert(b.getAttribute("data-selected") === "true", "the card does not declare its selection");
});

test("…and its neighbours stay empty, so the difference is legible frame to frame", () => {
  assert(tick(card("f1")) === "off" && tick(card("f3")) === "off",
    "the unselected cards also read as ticked — a selection that marks everything marks " +
    "nothing");
  assert(card("f1").getAttribute("aria-pressed") === "false",
    "a neighbour became selected too");
});

test("the tick box is decorative, not a second control inside the card button", () => {
  // A real <input type=checkbox> is what the gallery grid uses, but the gallery
  // tile is a DIV. This card is itself a <button>, so an interactive child would
  // be invalid HTML and a duplicate tab stop; the glyph must therefore be
  // aria-hidden and the card's own aria-pressed must carry the state.
  const el = card("f2").querySelector("[data-tick]");
  assert(el.getAttribute("aria-hidden") != null,
    "the tick box is exposed to assistive tech as well as aria-pressed — the state would " +
    "be announced twice");
  assert(card("f2").querySelector("input, button") == null,
    "there is an interactive element nested inside the card button");
});

// ------------------------------------- THE DESTRUCTIVE HALF SAYS HOW MANY (#13)
test("both bulk buttons say how many frames they are about to regrade", () => {
  assert(/mark accepted \(1\)/.test(text()),
    "the accepting button lost its count");
  assert(/mark rejected \(1\)/.test(text()),
    "'mark rejected' still has no count: on the destructive half of the pair, with the " +
    "selection itself carried by a hairline, NOTHING on screen stated the size of what " +
    "was about to be rejected");
});

await click(card("f3"));
test("…and the count follows the selection", () => {
  assert(/mark rejected \(2\)/.test(text()),
    `the count did not follow a second pick: ${text().slice(0, 200)}`);
});

await click(card("f3"));
test("tapping a selected card clears it, box and all", () => {
  const b = card("f3");
  assert(b.getAttribute("aria-pressed") === "false", "the second tap did not deselect");
  assert(tick(b) === "off",
    "the tick box stayed filled on a card that is no longer selected — the shape channel " +
    "now contradicts aria-pressed, which is worse than not having one");
  assert(/mark rejected \(1\)/.test(text()), "the count did not follow the deselect");
});

// ------------------------------------------------ a session with no mosaic
test("a single-target session has no roll-up and no optgroup (nothing changed for it)", () => {
  assert(doc().querySelectorAll("[data-rollup-group]").length === 0,
    "a session with no mosaic grew a roll-up header");
  assert(doc().querySelectorAll("optgroup").length === 0,
    "a session with no mosaic grew an optgroup in the target select");
});

// -------------------------------------- MOSAIC GROUPING (#188, WP-1)
// THE FINDING. A 3x3 mosaic's frames were scattered across nine targets named
// "<name> r-c", with no group header, no per-panel roll-up and no panel filter,
// in the one drawer both UIs open. A session whose plan carries a 2x2 group
// must read as ONE mosaic with four panel rows, and a panel must be one tap
// from its own frames.
//
// NAMED MUTANTS (WP-108), each a one-line source change applied from a byte
// backup of lib/sessionReview.ts or SessionReviewDrawer.tsx and restored
// byte-identical. The first failing assertion of each in THIS file, verbatim:
//   "group by target name" (`const key = t.mosaic_group;` -> `t.name`)
//     x a 2x2 mosaic renders ONE header and FOUR panel rows, in grid order:
//       0 roll-up groups, expected one
//   "count rejected as accepted" (`effectiveAccepted(f)` -> `f.auto_accepted`)
//     x each row counts accepted and rejected from the EFFECTIVE verdict:
//       2-1 reads "2-1 1 accepted · 0 rejected": its frame was accepted by the
//       camera and regraded out, so only the effective verdict counts it as
//       rejected
//   "group filter inert" (filterFrames ignores group_id)
//     x choosing 'all panels' shows every panel's frames and not the single
//       target's: grid shows a1,a2,b1,c1,s1, expected a1,a2,b1,c1
//   "panels in plan order" (drop `.sort(byGridPlace)`)
//     x a 2x2 mosaic renders ONE header and FOUR panel rows, in grid order:
//       panel rows 2-2,1-1,2-1,1-2, expected 1-1,1-2,2-1,2-2 even though the
//       plan lists them out of order
//   "row tap does nothing" (the row's onClick becomes `() => undefined`)
//     x tapping the 1-2 row filters the grid to that panel's frames:
//       grid shows a1,a2,b1,c1,s1 after tapping 1-2, expected only b1
//   "row never un-toggles" (a pressed row sets itself again instead of clearing)
//     x tapping the pressed row again clears the filter:
//       1 frames after un-pressing, expected 5
//   "roll-up counts the filtered grid" (`mosaicRollup(mosaics, frames)`)
//     x the roll-up still shows every panel, at its session counts, while one
//       is filtered: 1-1 reads "1-1 0 accepted · 0 rejected" with the 1-2
//       filter on: the roll-up is counting the filtered grid, so the panels
//       the operator is not looking at read as empty
//   "an optgroup per panel" (the `emitted` guard in the select is dropped)
//     x the target select offers the mosaic as an optgroup, singles plain:
//       4 optgroups, expected one labelled Veil
//   "filter change keeps the selection" (applyFlt's prune becomes `s => s`)
//     x a panel tap prunes a selection the new filter hides: a frame selected
//       under the old filter is still selected, hidden, behind a panel tap: a
//       later bulk regrade would act on it unseen
//   "pressed tick never set" (`data-rollup-tick={on ? "on" : "off"}` -> "off")
//     x the pressed row carries a tick, not only the accent (a shape, not a hue):
//       the pressed 1-2 row declares no tick: a pressed panel is back to being
//       a border colour
//   "pressed tick never drawn" (the glyph's class stays text-transparent)
//     x the pressed row carries a tick, not only the accent (a shape, not a hue):
//       the pressed row's tick is not visible (classes: shrink-0
//       text-transparent): the marker is there but nothing is drawn
//   "select ignores the group choice" (its value reads only flt.target_id)
//     x choosing 'all panels' shows every panel's frames and not the single
//       target's: the select lost its group choice
const rollGroups = (): any[] => [...doc().querySelectorAll("[data-rollup-group]")];
const rollRows = (): any[] => [...doc().querySelectorAll("button[data-rollup-row]")];
const rollRow = (label: string): any =>
  rollRows().find((b: any) => b.querySelector("[data-panel-label]")?.textContent === label);
const targetSelect = (): any => doc().querySelector('select[aria-label="Filter by target"]');
// A missing control is a FAILED test, not a crash that takes every result before
// it down with the file: the top-level awaits below would otherwise throw out of
// the module and print nothing.
const tap = async (node: any, what: string): Promise<void> => {
  if (!node) { failed++; failures.push(`x could not find ${what} to tap`); return; }
  await click(node);
};
const choose = async (value: string): Promise<void> => {
  await act(async () => {
    const s = targetSelect();
    s.value = value;
    s.dispatchEvent(new win.Event("change", { bubbles: true }));
  });
};
const cardIds = (): string[] =>
  cards().map((b: any) => b.getAttribute("aria-label").replace("Select frame ", ""));
const norm = (s: string | null | undefined): string => (s || "").replace(/\s+/g, " ").trim();

await act(async () => {
  root.render(createElement(SessionReviewDrawer, { id: null, onClose: () => {} }));
});
await act(async () => {
  root.render(createElement(SessionReviewDrawer, { id: "sess2", onClose: () => {} }));
});
await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

test("the mosaic session loaded: five frames, nothing filtered yet", () => {
  assert(cards().length === 5,
    `${cards().length} frame cards, expected 5 — the mosaic fixture never loaded`);
});

test("a 2x2 mosaic renders ONE header and FOUR panel rows, in grid order", () => {
  assert(rollGroups().length === 1, `${rollGroups().length} roll-up groups, expected one`);
  const header = doc().querySelectorAll("[data-rollup-header]");
  assert(header.length === 1, `${header.length} headers, expected one`);
  assert(norm(header[0].textContent).startsWith("Veil"),
    `the header does not name the mosaic: "${norm(header[0].textContent)}"`);
  const labels = rollRows().map((b: any) => b.querySelector("[data-panel-label]")?.textContent);
  assert(labels.join(",") === "1-1,1-2,2-1,2-2",
    `panel rows ${labels.join(",")}, expected 1-1,1-2,2-1,2-2 even though the plan lists them out of order`);
});

test("each row counts accepted and rejected from the EFFECTIVE verdict", () => {
  const row = (l: string) => norm(rollRow(l)?.textContent);
  assert(/^1-1 1 accepted · 1 rejected$/.test(row("1-1")), `1-1 reads "${row("1-1")}"`);
  assert(/^1-2 1 accepted · 0 rejected$/.test(row("1-2")), `1-2 reads "${row("1-2")}"`);
  assert(/^2-1 0 accepted · 1 rejected$/.test(row("2-1")),
    `2-1 reads "${row("2-1")}": its frame was accepted by the camera and regraded out, so only ` +
    "the effective verdict counts it as rejected");
  assert(/^2-2 0 accepted · 0 rejected$/.test(row("2-2")),
    `2-2 reads "${row("2-2")}": a panel with no frames must still have its row`);
  const header = norm(doc().querySelector("[data-rollup-header]").textContent);
  assert(/^Veil 2 accepted · 2 rejected$/.test(header), `the header sum reads "${header}"`);
});

test("the target select offers the mosaic as an optgroup, singles plain", () => {
  const og = doc().querySelectorAll("optgroup");
  assert(og.length === 1 && og[0].getAttribute("label") === "Veil",
    `${og.length} optgroups, expected one labelled Veil`);
  const opts = [...og[0].querySelectorAll("option")].map((o: any) => norm(o.textContent));
  assert(opts.join(",") === "all panels,1-1,1-2,2-1,2-2",
    `optgroup options ${opts.join(",")}, expected all panels,1-1,1-2,2-1,2-2`);
  const solo = [...targetSelect().querySelectorAll("option")]
    .find((o: any) => norm(o.textContent) === "M31");
  assert(solo != null && solo.parentElement === targetSelect(),
    "the single target M31 is missing from the select, or sits inside the mosaic's optgroup");
});

test("no panel is pressed before anything is chosen", () => {
  assert(rollRows().every((b: any) => b.getAttribute("aria-pressed") === "false"),
    "a roll-up row starts pressed");
});

await tap(rollRow("1-2"), "the 1-2 roll-up row");
test("tapping the 1-2 row filters the grid to that panel's frames", () => {
  assert(cardIds().join(",") === "b1",
    `grid shows ${cardIds().join(",")} after tapping 1-2, expected only b1`);
  assert(rollRow("1-2").getAttribute("aria-pressed") === "true", "the tapped row is not pressed");
  assert(rollRow("1-1").getAttribute("aria-pressed") === "false", "an untapped row is pressed");
  assert(targetSelect().value === "p12",
    `the target select reads "${targetSelect().value}", expected p12, so the two controls disagree`);
});

test("the pressed row carries a tick, not only the accent (a shape, not a hue)", () => {
  // The drawer says a pressed row wears a tick "for the same reason the frame
  // cards do": under :root.night the accent and the line are one red, so a hue
  // alone is not a channel. jsdom applies no stylesheet, so the proof is that
  // the pressed row's glyph is the VISIBLE one (text-accent, not
  // text-transparent) and every other row's is hidden, tracking aria-pressed.
  const glyph = (l: string): any => rollRow(l)?.querySelector("[data-rollup-tick]");
  const cls = (el: any): string[] => (el?.getAttribute("class") || "").split(" ");
  const on = glyph("1-2");
  assert(on != null && on.getAttribute("data-rollup-tick") === "on",
    "the pressed 1-2 row declares no tick: a pressed panel is back to being a border colour");
  assert(cls(on).includes("text-accent") && !cls(on).includes("text-transparent"),
    `the pressed row's tick is not visible (classes: ${cls(on).join(" ")}): the marker is there ` +
    "but nothing is drawn, so under the night palette the row reads as every other row");
  assert(on.getAttribute("aria-hidden") != null,
    "the tick is exposed to assistive tech as well as aria-pressed: the state would be announced twice");
  for (const l of ["1-1", "2-1", "2-2"]) {
    const off = glyph(l);
    assert(off != null && off.getAttribute("data-rollup-tick") === "off" && cls(off).includes("text-transparent"),
      `the unpressed ${l} row shows a tick: a tick on every row marks nothing`);
  }
});

test("the roll-up still shows every panel, at its session counts, while one is filtered", () => {
  assert(rollRows().length === 4, `${rollRows().length} rows after filtering, expected 4`);
  const r11 = norm(rollRow("1-1")?.textContent);
  assert(/^1-1 1 accepted · 1 rejected$/.test(r11),
    `1-1 reads "${r11}" with the 1-2 filter on: the roll-up is counting the filtered grid, so ` +
    "the panels the operator is not looking at read as empty");
});

await tap(rollRow("1-2"), "the 1-2 roll-up row");
test("tapping the pressed row again clears the filter", () => {
  assert(cardIds().length === 5, `${cardIds().length} frames after un-pressing, expected 5`);
  assert(rollRow("1-2").getAttribute("aria-pressed") === "false", "the row stayed pressed");
  assert(rollRows().every((b: any) => b.querySelector("[data-rollup-tick]")?.getAttribute("data-rollup-tick") === "off"),
    "a tick stayed filled on a row that is no longer pressed: the shape channel now contradicts aria-pressed");
});

await choose("group:g1");
test("choosing 'all panels' shows every panel's frames and not the single target's", () => {
  assert(cardIds().sort().join(",") === "a1,a2,b1,c1",
    `grid shows ${cardIds().sort().join(",")}, expected a1,a2,b1,c1`);
  assert(targetSelect().value === "group:g1", "the select lost its group choice");
});

await choose("");
await tap(card("a1"), "the a1 frame card");
test("a frame is selected before the filter changes", () => {
  assert(card("a1").getAttribute("aria-pressed") === "true", "the tap did not select a1");
  assert(/mark rejected \(1\)/.test(text()), "the count did not follow the pick");
});

await tap(rollRow("1-2"), "the 1-2 roll-up row");
test("a panel tap prunes a selection the new filter hides", () => {
  assert(/mark rejected \(0\)/.test(text()),
    "a frame selected under the old filter is still selected, hidden, behind a panel tap: " +
    "a later bulk regrade would act on it unseen");
});

await tap(rollRow("1-2"), "the 1-2 roll-up row");
await tap(card("a1"), "the a1 frame card");
await tap([...doc().querySelectorAll("button")].find((b: any) => /mark rejected/.test(b.textContent)),
  "the mark rejected button");
await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
test("a regrade moves the roll-up counts", () => {
  const row = norm(rollRow("1-1")?.textContent);
  assert(/^1-1 0 accepted · 2 rejected$/.test(row),
    `1-1 reads "${row}" after its accepted frame was regraded out; the roll-up is counting ` +
    "the camera's verdict, not the operator's");
  const header = norm(doc().querySelector("[data-rollup-header]").textContent);
  assert(/^Veil 1 accepted · 3 rejected$/.test(header), `the header sum reads "${header}"`);
});

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`sessionReviewDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
