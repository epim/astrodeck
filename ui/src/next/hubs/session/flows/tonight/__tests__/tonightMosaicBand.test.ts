// tonightMosaicBand.test.ts - the #/next Tonight sheet's TIMELINE band for a
// mosaic block (#189 S3 item 5; spec 1.2, 2.3, 6.9).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/tonight/__tests__/tonightMosaicBand.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE BAND IS THE SERVER'S SPREAD. A mosaic's centre has one altitude
//      curve; its panels do not all peak on it. `tonight.py::_band` reduces
//      the stamped panels to the lowest and the highest peak (the
//      `mosaicNightSummary` reduction), and the sheet's reader
//      (`readTonight`, through the one shared `flowsApi.mosaicBand`) must hand
//      exactly those two panels to the timeline, and the card must draw the
//      strip between them across the block's own window.
//   2. SHAPE, NOT HUE (spec 2.3). Under `:root.night` every token collapses
//      toward one red, so the band wears its block's own tone and is told
//      apart by being hatched and outlined against the block's flat fill; its
//      legend swatch is hatched too.
//   3. A SINGLE TARGET AND A POOL ARE UNTOUCHED. Their rows read with the four
//      keys they always had, and a night with no mosaic renders the markup it
//      always did: the band may add marks, never move one.
//
// Every guarded case names the mutant it kills and quotes the failure it
// produced, each run in a private copy of ui/ (scratchpad/s3-u2-readouts-
// m5q8/mut/; the fixture read by scratchpad s4-tonight-mut), never in the
// shared tree.
//
// THE ANSWER IS server/tests/fixtures/tonight_mosaic_2x3.json, READ, NOT
// COPIED (#353 item 7). S3 embedded `flows/tonight.py::resolve_tonight`'s
// answer here as a literal, "recorded, never hand-edited", and nothing
// failed when the server's answer drifted from the recording. Now
// test_flows_tonight_band_fixture.py grades the server against the same
// file, so a change to the answer turns that test red, and a rewrite of the
// file is graded here on the next run. Do not replace the read with a
// literal. The answer is for the graph DUSK -> TARGET M16 (a 3x2, 3 columns
// by 2 rows, at 25%, Rotate to PA 30, 2.0 x 1.33 deg panels, loop wire) ->
// CAPTURE Ha 300 s x 2 -> TARGET M31 (single) -> CAPTURE -> POOL M13, M92 ->
// CAPTURE, resolved at 2026-06-15 20:00 UTC with twilight -12 for a
// SYNTHETIC site (40 N 105 W, the place test_flows_tonight_mosaic.py uses,
// NOT the observatory's), hop_cost_s 160 and a 2x3 progress answer with
// four panels done. Its M16 row's band: worst 2-1 at 35.0, best 1-3 at
// 37.4; the first panel in grid order, 1-1, peaks at 35.8, between them, so
// a band read off one panel cannot pass for the spread. The server test
// holds those premises too.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (h: any) => clearTimeout(h);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { readTonight } = await import("../tonightModel");
const { TonightTimelineCard } = await import("../TonightTimelineCard");
const { mosaicBand } = await import("../../../../../../lib/flowsApi");
const { summarisePanelNight } =
  await import("../../../../../../components/atlas/mosaicNightSummary");
type TonightRead = import("../tonightModel").TonightRead;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
function near(got: number, want: number, eps: number, msg: string): void {
  if (!(Math.abs(got - want) <= eps)) throw new Error(`${msg} (got ${got}, want ~${want})`);
}

// ------------------------------------------------------------------ fixture
// MUTANT "fixture unreadable" (FIXTURE_REL pointed at a file that is not
// there), run in scratchpad s4-tonight-mut. Observed: the file stops before
// its first case with exit code 1, which run-tests.mjs scores as a failure:
//   Error: cannot read ../../../../../../../../server/tests/fixtures/
//     tonight_mosaic_2x3.missing.json, the Tonight answer this sheet is
//     graded against: ENOENT: no such file or directory, open '...'
// MUTANT "band from the first panel only" (below), re-run against the file
// in the same copy: 2/7, as recorded at the first case.
// MUTANT "fixture band edited" (the file's M16 band worst transit_alt 35.0
// made 35.1, nothing in this file touched), run in scratchpad
// s4-tonight-resume-mut: this file follows the server's file, where the
// literal it replaced could not. Observed, 4/7 (test_flows_tonight_band_
// fixture.py goes red on the same edit, so the file cannot drift unseen):
//   x the sheet's reader hands the timeline the server's worst and best
//     panel: ... got "{\"worst\":{...\"transit_alt\":35.1}, ...}"
//   x the band is the mosaicNightSummary spread of the same panels: the
//     band's foot is the lowest peak  expected 35  got 35.1
//   x the card draws the band between the worst and best peak, across the
//     block's window: its foot is the worst panel's peak, 2-1 at 35.0 (got
//     78.69999999999999, want ~78.77777777777777)
const FIXTURE_REL = "../../../../../../../../server/tests/fixtures/tonight_mosaic_2x3.json";

/** The server's answer. A missing or unreadable file must FAIL the whole
 *  file, never skip it: a skipped fixture reads as a green band. */
function readAnswer(): Record<string, unknown> {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the Tonight answer this sheet is graded `
      + `against: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text) as { response?: Record<string, unknown> };
  const targets = fx.response?.targets;
  if (!Array.isArray(targets) || !(targets[0] as any)?.mosaic) {
    throw new Error(`${FIXTURE_REL} does not hold a Tonight answer whose first row is a mosaic`);
  }
  return fx.response!;
}
const TONIGHT_2X3: Record<string, unknown> = readAnswer();

/** The recorded answer with every row's `mosaic` key taken off: a night with
 *  no mosaic in it, otherwise the same bytes. */
function withoutMosaic(payload: Record<string, unknown>): Record<string, unknown> {
  return {
    ...payload,
    targets: (payload.targets as any[]).map(({ mosaic: _m, ...rest }) => rest),
  };
}

/** y of an altitude inside the band, the rule the arc uses: 0 deg at 106, 90 at 36. */
const altY = (alt: number) => 36 + 70 * (1 - alt / 90);

function read(payload: Record<string, unknown>): TonightRead {
  const r = readTonight(payload);
  if (!r) throw new Error("precondition: the recorded answer reads");
  return r;
}

const root = createRoot(win.document.getElementById("root"));
const doc = win.document as Document;

function mountCard(r: TonightRead): HTMLElement {
  act(() => root.render(null));
  act(() => root.render(createElement(TonightTimelineCard, {
    night: r.night, flats: r.flats, moon: r.moon, targets: r.targets,
  })));
  const card = doc.querySelector<HTMLElement>("[data-testid='tonight-timeline']");
  if (!card) throw new Error("precondition: the timeline card rendered");
  return card;
}

const labelIn = (card: HTMLElement, text: string): HTMLElement | undefined =>
  [...card.querySelectorAll<HTMLElement>(".nx-tn-tl-label")].find((d) => d.textContent === text);

// ================================================ 1. THE READER HANDS IT ON

// MUTANT "band from the first panel only" (flowsApi.mosaicBand reads both
// edges off the first answered panel, `mosaic.panels[0]`, instead of the
// server's worst and best). Observed, 2/7:
//   x the sheet's reader hands the timeline the server's worst and best
//     panel: the band is tonight.py's worst and best panel, as it sent them
//     expected "{\"worst\":{\"panel\":\"2-1\",\"row\":1,\"col\":0,\"transit_
//       alt\":35},\"best\":{\"panel\":\"1-3\",\"row\":0,\"col\":2,\"transit_al
//       t\":37.4}}"
//     got      "{\"worst\":{\"panel\":\"1-1\",\"row\":0,\"col\":0,\"transit_
//       alt\":35.8},\"best\":{\"panel\":\"1-1\",\"row\":0,\"col\":0,\"transit_
//       alt\":35.8}}"
// MUTANT "next reader drops the band" (readTonight builds its targets
// without `band`, as it did before S3). Observed, 3/7:
//   x the sheet's reader hands the timeline the server's worst and best
//     panel: the M16 row reached the timeline with no band
await test("the sheet's reader hands the timeline the server's worst and best panel", () => {
  const r = read(TONIGHT_2X3);
  const band = r.targets[0]?.band;
  assert(band, "the M16 row reached the timeline with no band");
  eq(JSON.stringify(band), JSON.stringify({
    worst: { panel: "2-1", row: 1, col: 0, transit_alt: 35 },
    best: { panel: "1-3", row: 0, col: 2, transit_alt: 37.4 },
  }), "the band is tonight.py's worst and best panel, as it sent them");
});

await test("the band is the mosaicNightSummary spread of the same panels", () => {
  // Spec S3 item 5 names the reduction: the band's edges are the peak spread
  // the Atlas's own summary computes over the panels the server stamped.
  const rows = TONIGHT_2X3.targets as any[];
  const spread = summarisePanelNight(rows[0].mosaic.panels, 30).peak;
  assert(spread, "premise: the recorded panels carry peak altitudes");
  const band = read(TONIGHT_2X3).targets[0]?.band;
  eq(band?.worst.transit_alt, spread!.min, "the band's foot is the lowest peak");
  eq(band?.best.transit_alt, spread!.max, "the band's top is the highest peak");
});

// MUTANT "no order check" (mosaicBand's `worst.transit_alt >
// best.transit_alt` refusal deleted). Observed, 6/7:
//   x a band that is not the contract's is no band, never a strip at 0
//     degrees: a worst edge above the best cannot be drawn truthfully
//     expected null
//     got      {"worst":{"panel":"1-3","row":0,"col":2,"transit_alt":37.4},"
//       best":{"panel":"2-1","row":1,"col":0,"transit_alt":35}}
// MUTANT "no field guard" (bandPanel's finite-number check on transit_alt
// deleted). Observed, 6/7:
//   x a band that is not the contract's is no band, never a strip at 0
//     degrees: an edge with no peak altitude
//     expected null
//     got      {"worst":{"panel":"2-1","row":1,"col":0},"best":{"panel":"1-3
//       ","row":0,"col":2,"transit_alt":37.4}}
await test("a band that is not the contract's is no band, never a strip at 0 degrees", () => {
  const m = (TONIGHT_2X3.targets as any[])[0].mosaic;
  eq(mosaicBand(undefined), null, "a row with no mosaic key (a single target, a pool member)");
  eq(mosaicBand({ ...m, band: null }), null, "no panel answered");
  eq(mosaicBand({ ...m, band: { worst: m.band.best, best: m.band.worst } }), null,
    "a worst edge above the best cannot be drawn truthfully");
  const { transit_alt: _t, ...noAlt } = m.band.worst;
  eq(mosaicBand({ ...m, band: { ...m.band, worst: noAlt } }), null,
    "an edge with no peak altitude");
  eq(mosaicBand({ ...m, band: { ...m.band, best: { ...m.band.best, panel: "" } } }), null,
    "an edge with no panel name");
});

// CONTROL. MUTANT "a band key on every row" (readTonight writes `band` on
// every target, null where there is none). Observed, 6/7:
//   x CONTROL: the single target and the pool members read byte for byte as
//     before: M31's row must read exactly as it did before mosaics
//     expected "{\"label\":\"M31\",\"window\":{\"start_unix\":1781599500,\"e
//       nd_unix\":1781601900},\"curve\":[[1781582100,-4.94],[1781582700,-4.25]
//       ,[1781583300,-3.51],[1781583900,-2.71],[1781584500,-1.87],[1781585100,
//       -0.97],[1781585700,-0.03],[178 ... (elided)
//     got      "{\"band\":null,\"label\":\"M31\",\"window\":{\"start_unix\":
//       1781599500,\"end_unix\":1781601900},\"curve\":[[1781582100,-4.94],[178
//       1582700,-4.25],[1781583300,-3.51],[1781583900,-2.71],[1781584500,-1.87
//       ],[1781585100,-0.97],[17815857 ... (elided)
await test("CONTROL: the single target and the pool members read byte for byte as before", () => {
  const rows = TONIGHT_2X3.targets as any[];
  const r = read(TONIGHT_2X3);
  for (const i of [1, 2, 3]) {
    const t = rows[i];
    // Exactly the object the reader built before S3: four keys, in order.
    const before = {
      label: t.label || t.name,
      window: { start_unix: t.window.start_unix, end_unix: t.window.end_unix },
      curve: t.curve,
      meridian_flip_unix: t.meridian_flip_unix,
    };
    eq(JSON.stringify(r.targets[i]), JSON.stringify(before),
      `${before.label}'s row must read exactly as it did before mosaics`);
  }
  const plain = read(withoutMosaic(TONIGHT_2X3));
  assert(plain.targets.every((t) => !("band" in t)),
    "a night with no mosaic has no band key on any row");
});

// ================================================ 2. THE CARD DRAWS IT

// MUTANT "band from the first panel only", on the card. Observed:
//   x the card draws the band between the worst and best peak, across the
//     block's window: its top edge is the best panel's peak, 1-3 at 37.4 (got
//     78.15555555555555, want ~76.91111111111111)
// MUTANT "next label centred" (the card drops the band label's end anchor,
// leaving the stylesheet's centring). Observed, 6/7:
//   x the card draws the band between the worst and best peak, across the
//     block's window: the band's label ends on the band's right end, inside
//     the block it names
//     expected "translate(-100%, -50%)"
//     got      ""
await test("the card draws the band between the worst and best peak, across the block's window", () => {
  const card = mountCard(read(TONIGHT_2X3));
  const svg = card.querySelector("svg")!;
  const rects = [...svg.querySelectorAll("rect")];
  const strip = svg.querySelector("rect[data-shape='hatch']");
  assert(strip, "the card drew no band for the recorded 2x3");
  // The block is the flat rect that starts where the band does and spans the
  // full band height (36 to 106): the M16 block, drawn first of the targets.
  const block = rects.find((e) => e !== strip && e.getAttribute("x") === strip!.getAttribute("x")
    && e.getAttribute("height") === "70");
  assert(block, "no block starts where the band does: the band is not laid across its "
    + "block's window");
  eq(strip!.getAttribute("width"), block!.getAttribute("width"),
    "the band is exactly as wide as its block's window");
  near(Number(strip!.getAttribute("y")), altY(37.4), 1e-6,
    "its top edge is the best panel's peak, 1-3 at 37.4");
  near(Number(strip!.getAttribute("y")) + Number(strip!.getAttribute("height")),
    altY(35.0), 1e-6, "its foot is the worst panel's peak, 2-1 at 35.0");
  const label = labelIn(card, "2-1 low, 1-3 high");
  assert(label, "the band names its worst and best panel in the HTML layer");
  eq(label!.style.transform, "translate(-100%, -50%)",
    "the band's label ends on the band's right end, inside the block it names");
});

// MUTANT "next card draws the band flat" (TonightTimelineCard ignores
// `shape` and draws every rect as a flat fill). Observed, 4/7:
//   x the band is told apart by shape, not hue, and the legend says so: the
//     card drew no hatched band
await test("the band is told apart by shape, not hue, and the legend says so", () => {
  const card = mountCard(read(TONIGHT_2X3));
  const svg = card.querySelector("svg")!;
  const strip = svg.querySelector("rect[data-shape='hatch']");
  assert(strip, "the card drew no hatched band");
  const m = /^url\(#(.+)\)$/.exec(strip!.getAttribute("fill") ?? "");
  assert(m, `the band is filled with a pattern, got ${strip!.getAttribute("fill")}`);
  const pattern = doc.getElementById(m![1]);
  eq(pattern?.tagName.toLowerCase(), "pattern", "the band's fill names a pattern the svg defines");
  const block = [...svg.querySelectorAll("rect")].find((e) => e.getAttribute("height") === "70"
    && e.getAttribute("x") === strip!.getAttribute("x"));
  eq(strip!.getAttribute("stroke"), block?.getAttribute("fill"),
    "the band's outline is its block's own tone: a second hue would vanish at night");
  eq(pattern!.querySelector("line")?.getAttribute("stroke"), block?.getAttribute("fill"),
    "and so are its hatch lines");
  const legend = card.querySelector("[data-legend='mosaic-band']");
  assert(legend, "the legend says what the hatched band is");
  eq(legend!.querySelector("[data-shape]")?.getAttribute("data-shape"), "hatch",
    "the legend's swatch is hatched, the band's own shape");
});

// CONTROL. MUTANT "next legend always shows the band" (the card's legend
// entry drawn whether or not a band is). Observed, 6/7:
//   x CONTROL: a night with no mosaic renders the markup it always did: no
//     legend entry for a mark the picture does not have
await test("CONTROL: a night with no mosaic renders the markup it always did", () => {
  const plainCard = mountCard(read(withoutMosaic(TONIGHT_2X3)));
  assert(!plainCard.querySelector("defs") && !plainCard.querySelector("rect[data-shape]"),
    "no pattern and no hatched rect on a night with no band");
  assert(!plainCard.querySelector("[data-legend]"),
    "no legend entry for a mark the picture does not have");
  const plain = plainCard.outerHTML;

  // THE PLAIN NIGHT IS WHAT IT WAS BEFORE MOSAICS, not only the mosaic night
  // less its band. The comparison below is relative, so a change that moves
  // every night's marks alike passes it. S3-U2 rewrote two things every night
  // draws through: the flat rect, now the other arm of the hatch branch, and
  // the label's style, which now spreads a transform in for the band's label
  // alone. Both are pinned here to what they drew before S3: no inline
  // transform (the stylesheet centres a label) and the 3-unit corner. MUTANTS
  // "next card anchors every label at its end" (the spread's condition read
  // `true`) and "next plain rect loses its corner radius" (rx dropped from
  // the flat arm), both found SURVIVING by the S3-U2 verifier and run in its
  // private copy (scratchpad/s3-u2-verify-k7r2/). Observed, 6/7 each:
  //   x CONTROL: a night with no mosaic renders the markup it always did:
  //     every label on a night with no band keeps the stylesheet's centring,
  //     as before mosaics; 21:00 carries translate(-100%, -50%)
  //   x CONTROL: a night with no mosaic renders the markup it always did:
  //     every block on a night with no band keeps its corner radius, as
  //     before mosaics; got rx=null
  const plainLabels = [...plainCard.querySelectorAll<HTMLElement>(".nx-tn-tl-label")];
  assert(plainLabels.length > 0, "precondition: the plain night drew its labels");
  const moved = plainLabels.find((d) => d.style.transform !== "");
  assert(!moved, "every label on a night with no band keeps the stylesheet's centring, as "
    + `before mosaics; ${moved?.textContent} carries ${moved?.style.transform}`);
  const square = [...plainCard.querySelectorAll("rect")].find((e) => e.getAttribute("rx") !== "3");
  assert(!square, "every block on a night with no band keeps its corner radius, as before "
    + `mosaics; got rx=${square?.getAttribute("rx")}`);

  const card = mountCard(read(TONIGHT_2X3));
  card.querySelector("defs")?.remove();
  card.querySelectorAll("rect[data-shape='hatch']").forEach((e) => e.remove());
  labelIn(card, "2-1 low, 1-3 high")?.remove();
  card.querySelector("[data-legend='mosaic-band']")?.remove();
  eq(card.outerHTML, plain,
    "with the band's own marks taken out, the mosaic night's markup must be the plain "
    + "night's: the band may add marks, never move one");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`tonightMosaicBand.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
