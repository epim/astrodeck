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

g.fetch = async (url: string) => {
  const path = String(url);
  const json = path.includes("/api/sessions/sess1") ? SESSION : {};
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

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`sessionReviewDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
