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
// m5q8/mut/), never in the shared tree.
//
// RECORDED, NOT HAND-WRITTEN. TONIGHT_2X3 is `flows/tonight.py::
// resolve_tonight`'s own answer, as S3-T left it, recorded by
// scratchpad/s3-u2-readouts-m5q8/record.py: the graph DUSK -> TARGET M16 (a
// 2x3 at 25%, Rotate to PA 30, 2.0 x 1.33 deg panels, loop wire) -> CAPTURE
// Ha 300 s x 2 -> TARGET M31 (single) -> CAPTURE -> POOL M13, M92 -> CAPTURE,
// resolved at 2026-06-15 20:00 UTC with twilight -12 for a SYNTHETIC site
// (40 N 105 W, the place test_flows_tonight_mosaic.py uses, NOT the
// observatory's), Hub.site patched to the same place because the panel stamp
// reads the hub's site (#336), hop_cost_s 160 and the recorded 2x3 progress
// answer. Its M16 row's band: worst 2-1 at 35.0, best 1-3 at 37.4; the first
// panel in grid order, 1-1, peaks at 35.8, between them, so a band read off
// one panel cannot pass for the spread. Re-record, never hand-edit.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// printed tally plus the `{ passed, failed, total }` export.

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
const TONIGHT_2X3: Record<string, unknown> = {
  "ok": true,
  "reason": "",
  "now_unix": 1781553600.0,
  "twilight_deg": -12.0,
  "night": {
    "dusk_unix": 1781581509.7503662,
    "dawn_unix": 1781604969.4198608,
    "window_start_unix": 1781579709.7503662,
    "window_stop_unix": 1781604969.4198608,
    "dark_start_unix": 1781584500.0,
    "dark_end_unix": 1781601900.0,
    "darkness_kind": "astronomical"
  },
  "flats": null,
  "moon": {
    "illumination": 0.023,
    "phase_name": "Waxing Crescent",
    "alt": -23.8,
    "az": 352.3,
    "rise_unix": null,
    "set_unix": null
  },
  "targets": [
    {
      "name": "M16",
      "label": "M16",
      "coords_from": "node",
      "ra_hours": 18.313333333333333,
      "dec_deg": -13.816666666666666,
      "resolved": true,
      "min_altitude_deg": 30.0,
      "pool_rank": null,
      "window": {"start_unix": 1781589300.0, "end_unix": 1781601900.0, "mean_alt": 34.2},
      "curve": [
        [1781582100.0, 14.65], [1781582700.0, 16.25], [1781583300.0, 17.81],
        [1781583900.0, 19.33], [1781584500.0, 20.81], [1781585100.0, 22.24],
        [1781585700.0, 23.62], [1781586300.0, 24.96], [1781586900.0, 26.23],
        [1781587500.0, 27.45], [1781588100.0, 28.6], [1781588700.0, 29.69],
        [1781589300.0, 30.7], [1781589900.0, 31.64], [1781590500.0, 32.5],
        [1781591100.0, 33.28], [1781591700.0, 33.98], [1781592300.0, 34.58],
        [1781592900.0, 35.09], [1781593500.0, 35.51], [1781594100.0, 35.83],
        [1781594700.0, 36.05], [1781595300.0, 36.17], [1781595900.0, 36.19],
        [1781596500.0, 36.11], [1781597100.0, 35.93], [1781597700.0, 35.64],
        [1781598300.0, 35.27], [1781598900.0, 34.79], [1781599500.0, 34.22],
        [1781600100.0, 33.56], [1781600700.0, 32.82], [1781601300.0, 31.99],
        [1781601900.0, 31.08], [1781602500.0, 30.09], [1781603100.0, 29.03],
        [1781603700.0, 27.9], [1781604300.0, 26.71], [1781604900.0, 25.46]
      ],
      "transit_unix": 1781595900.0,
      "transit_alt": 36.2,
      "transit_in_daylight": false,
      "meridian_flip_unix": 1781595740.6350548,
      "moon_sep_deg": 13.7,
      "never_rises": false,
      "mosaic": {
        "rows": 2,
        "cols": 3,
        "live": 6,
        "skipped": [],
        "panels": [
          {"panel": "1-1", "row": 0, "col": 0, "transit_alt": 35.8},
          {"panel": "1-2", "row": 0, "col": 1, "transit_alt": 36.6},
          {"panel": "1-3", "row": 0, "col": 2, "transit_alt": 37.4},
          {"panel": "2-1", "row": 1, "col": 0, "transit_alt": 35.0},
          {"panel": "2-2", "row": 1, "col": 1, "transit_alt": 35.8},
          {"panel": "2-3", "row": 1, "col": 2, "transit_alt": 36.5}
        ],
        "band": {
          "worst": {"panel": "2-1", "row": 1, "col": 0, "transit_alt": 35.0},
          "best": {"panel": "1-3", "row": 0, "col": 2, "transit_alt": 37.4}
        }
      }
    },
    {
      "name": "M31",
      "label": "M31",
      "coords_from": "node",
      "ra_hours": 0.7122222222222222,
      "dec_deg": 41.26916666666666,
      "resolved": true,
      "min_altitude_deg": 30.0,
      "pool_rank": null,
      "window": {"start_unix": 1781599500.0, "end_unix": 1781601900.0, "mean_alt": 34.9},
      "curve": [
        [1781582100.0, -4.94], [1781582700.0, -4.25], [1781583300.0, -3.51],
        [1781583900.0, -2.71], [1781584500.0, -1.87], [1781585100.0, -0.97],
        [1781585700.0, -0.03], [1781586300.0, 0.96], [1781586900.0, 2.0], [1781587500.0, 3.08],
        [1781588100.0, 4.2], [1781588700.0, 5.36], [1781589300.0, 6.56], [1781589900.0, 7.79],
        [1781590500.0, 9.07], [1781591100.0, 10.38], [1781591700.0, 11.72],
        [1781592300.0, 13.09], [1781592900.0, 14.49], [1781593500.0, 15.92],
        [1781594100.0, 17.38], [1781594700.0, 18.87], [1781595300.0, 20.38],
        [1781595900.0, 21.91], [1781596500.0, 23.47], [1781597100.0, 25.05],
        [1781597700.0, 26.65], [1781598300.0, 28.27], [1781598900.0, 29.9],
        [1781599500.0, 31.56], [1781600100.0, 33.23], [1781600700.0, 34.92],
        [1781601300.0, 36.63], [1781601900.0, 38.35], [1781602500.0, 40.08],
        [1781603100.0, 41.83], [1781603700.0, 43.59], [1781604300.0, 45.36],
        [1781604900.0, 47.14]
      ],
      "transit_unix": 1781601900.0,
      "transit_alt": 38.4,
      "transit_in_daylight": true,
      "meridian_flip_unix": null,
      "moon_sep_deg": 117.1,
      "never_rises": false
    },
    {
      "name": "M13",
      "label": "M13",
      "coords_from": "catalog",
      "ra_hours": 16.6949,
      "dec_deg": 36.4613,
      "resolved": true,
      "min_altitude_deg": 30.0,
      "pool_rank": 1,
      "window": {"start_unix": 1781584500.0, "end_unix": 1781601900.0, "mean_alt": 72.6},
      "curve": [
        [1781582100.0, 64.37], [1781582700.0, 66.29], [1781583300.0, 68.21],
        [1781583900.0, 70.13], [1781584500.0, 72.05], [1781585100.0, 73.96],
        [1781585700.0, 75.86], [1781586300.0, 77.75], [1781586900.0, 79.61],
        [1781587500.0, 81.43], [1781588100.0, 83.16], [1781588700.0, 84.74],
        [1781589300.0, 85.95], [1781589900.0, 86.41], [1781590500.0, 85.86],
        [1781591100.0, 84.61], [1781591700.0, 83.01], [1781592300.0, 81.27],
        [1781592900.0, 79.45], [1781593500.0, 77.58], [1781594100.0, 75.69],
        [1781594700.0, 73.79], [1781595300.0, 71.88], [1781595900.0, 69.96],
        [1781596500.0, 68.04], [1781597100.0, 66.12], [1781597700.0, 64.2],
        [1781598300.0, 62.28], [1781598900.0, 60.37], [1781599500.0, 58.46],
        [1781600100.0, 56.55], [1781600700.0, 54.65], [1781601300.0, 52.76],
        [1781601900.0, 50.87], [1781602500.0, 49.0], [1781603100.0, 47.12],
        [1781603700.0, 45.26], [1781604300.0, 43.41], [1781604900.0, 41.57]
      ],
      "transit_unix": 1781589900.0,
      "transit_alt": 86.4,
      "transit_in_daylight": false,
      "meridian_flip_unix": 1781589914.2750547,
      "moon_sep_deg": 61.2,
      "never_rises": false
    },
    {
      "name": "M92",
      "label": "M92",
      "coords_from": "catalog",
      "ra_hours": 17.2854,
      "dec_deg": 43.1359,
      "resolved": true,
      "min_altitude_deg": 30.0,
      "pool_rank": 2,
      "window": {"start_unix": 1781584500.0, "end_unix": 1781601900.0, "mean_alt": 75.2},
      "curve": [
        [1781582100.0, 59.25], [1781582700.0, 61.06], [1781583300.0, 62.88],
        [1781583900.0, 64.7], [1781584500.0, 66.53], [1781585100.0, 68.36],
        [1781585700.0, 70.19], [1781586300.0, 72.02], [1781586900.0, 73.84],
        [1781587500.0, 75.67], [1781588100.0, 77.48], [1781588700.0, 79.28],
        [1781589300.0, 81.06], [1781589900.0, 82.78], [1781590500.0, 84.41],
        [1781591100.0, 85.84], [1781591700.0, 86.77], [1781592300.0, 86.74],
        [1781592900.0, 85.78], [1781593500.0, 84.34], [1781594100.0, 82.7],
        [1781594700.0, 80.97], [1781595300.0, 79.2], [1781595900.0, 77.4],
        [1781596500.0, 75.58], [1781597100.0, 73.76], [1781597700.0, 71.93],
        [1781598300.0, 70.1], [1781598900.0, 68.27], [1781599500.0, 66.44],
        [1781600100.0, 64.61], [1781600700.0, 62.79], [1781601300.0, 60.97],
        [1781601900.0, 59.16], [1781602500.0, 57.36], [1781603100.0, 55.56],
        [1781603700.0, 53.76], [1781604300.0, 51.98], [1781604900.0, 50.2]
      ],
      "transit_unix": 1781591700.0,
      "transit_alt": 86.8,
      "transit_in_daylight": false,
      "meridian_flip_unix": 1781592040.0750546,
      "moon_sep_deg": 66.6,
      "never_rises": false
    }
  ],
  "budget": [],
  "campaign": {
    "is_campaign": false,
    "has_pool": true,
    "has_ledger": false,
    "quota": 45,
    "members": [
      {"name": "M13", "banked": null, "quota": 45, "done": false, "pct": null},
      {"name": "M92", "banked": null, "quota": 45, "done": false, "pct": null}
    ],
    "note": "Single-night flow - set DUSK WINDOW \u2192 Repeat to make this a campaign. 4 of 6 " +
      "mosaic panels done, 9 of 12 subs banked.",
    "panels": [
      {
        "block": "t",
        "name": "M16 1-1",
        "row": 0,
        "col": 0,
        "banked": 2,
        "owed": 0,
        "total": 2,
        "done": true,
        "pct": 100,
        "skipped": false
      },
      {
        "block": "t",
        "name": "M16 1-2",
        "row": 0,
        "col": 1,
        "banked": 1,
        "owed": 1,
        "total": 2,
        "done": false,
        "pct": 50,
        "skipped": false
      },
      {
        "block": "t",
        "name": "M16 1-3",
        "row": 0,
        "col": 2,
        "banked": 2,
        "owed": 0,
        "total": 2,
        "done": true,
        "pct": 100,
        "skipped": false
      },
      {
        "block": "t",
        "name": "M16 2-3",
        "row": 1,
        "col": 2,
        "banked": 0,
        "owed": 2,
        "total": 2,
        "done": false,
        "pct": 0,
        "skipped": false
      },
      {
        "block": "t",
        "name": "M16 2-2",
        "row": 1,
        "col": 1,
        "banked": 2,
        "owed": 0,
        "total": 2,
        "done": true,
        "pct": 100,
        "skipped": false
      },
      {
        "block": "t",
        "name": "M16 2-1",
        "row": 1,
        "col": 0,
        "banked": 2,
        "owed": 0,
        "total": 2,
        "done": true,
        "pct": 100,
        "skipped": false
      }
    ],
    "has_progress": true
  },
  "brief": "This flow arms at astronomical dusk (\u221230 min). It then selects the best of M13, " +
    "M92 - above 30\u00b0, at least 40\u00b0 from the moon (if up), within 4 h of the " +
    "meridian. M16 is a 2x3 mosaic of 6 panels at 25% overlap, laid out at PA 30\u00b0 with " +
    "the rotator turned to it at every panel. After 1 pass of its filters on a panel it " +
    "moves on to the next (least complete first), and comes back until every panel has its " +
    "subs. A hop between panels takes about 2 m 40 s, as measured on this rig. It captures " +
    "Ha 300 s \u00d7 2 (gain 100, bin 1). If the active target sinks to the 30\u00b0 floor, " +
    "it is set aside for tonight - a restart tonight does not retry it, the next night does " +
    "- and the next best takes over.",
  "story": [
    {
      "t_unix": 1781579709.7503662,
      "label": "",
      "msg": "Autorun window opens (sun \u221212\u00b0, \u221230 min offset applied)",
      "tone": "text"
    },
    {"t_unix": 1781584500.0, "label": "", "msg": "Astronomical darkness", "tone": "faint"},
    {
      "t_unix": 1781584500.0,
      "label": "",
      "msg": "Pool re-scores 4 candidates each cycle (altitude \u00d7 moon separation \u00d7 hour " +
        "angle) \u2014 best available wins; re-evaluates on completion or an altitude floor",
      "tone": "text"
    },
    {
      "t_unix": 1781584500.0,
      "label": "",
      "msg": "Moon stays down all night (2% illuminated) \u2014 no moonglow in any of it",
      "tone": "faint"
    },
    {
      "t_unix": 1781589914.2750547,
      "label": "",
      "msg": "M13 crosses the meridian \u2014 engine flips, re-centers via plate solve, restarts " +
        "guiding; worst case one frame lost",
      "tone": "warn"
    },
    {
      "t_unix": 1781592040.0750546,
      "label": "",
      "msg": "M92 crosses the meridian \u2014 engine flips, re-centers via plate solve, restarts " +
        "guiding; worst case one frame lost",
      "tone": "warn"
    },
    {
      "t_unix": 1781595740.6350548,
      "label": "",
      "msg": "M16 crosses the meridian \u2014 engine flips, re-centers via plate solve, restarts " +
        "guiding; worst case one frame lost",
      "tone": "warn"
    },
    {
      "t_unix": 1781604969.4198608,
      "label": "",
      "msg": "Dawn: loop ends, mount parks, camera warms",
      "tone": "text"
    }
  ]
};

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
