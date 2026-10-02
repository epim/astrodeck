// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// tonightTimeline.test.ts — the rules that decide what the Tonight timeline
// draws, and more importantly what it REFUSES to draw.
//   Run:  npx tsx src/components/flows/__tests__/tonightTimeline.test.ts   (from ui/)
//
// Two kinds of assertion, and the second kind is the point.
//
// The first kind pins the placement rule: the axis spans the server's own
// dusk→dawn with a layout gutter, twilight is the two gaps between dusk/dark
// and dark-end/dawn, and an altitude sample lands where its degrees say.
//
// The second kind pins ABSENCE. Every one of these is a case where the server
// legitimately returns null and the prototype drew something anyway — a moon
// bar running to the edge of a night nobody measured, a flats block for a
// window that could not be parsed, an equal share of the sky for a target with
// no coordinates. A timeline that invents those renders beautifully and lies,
// and nothing about it fails. Only a test that asserts the element is MISSING
// can see it.
//
// The last section is a mosaic's band (#189 S3 item 5, task S3-U2), on a
// recorded Tonight answer: drawn between the worst and best panel's peak,
// across the block's window, hatched in the block's own tone, and reaching
// the classic panel although that panel's reader drops the key. Its cases
// name the mutant each kills and quote the failure, run in a private copy of
// ui/ (scratchpad/s3-u2-readouts-m5q8/mut/), never in the shared tree.
/* eslint-disable @typescript-eslint/no-explicit-any */

import { installAutoRaf } from "../../../testing/rafPolyfill";

// ---------------------------------------------------------------- jsdom first
// The module under test reads the Tonight answer out of the store for the
// mosaic band (`withMosaicBands`), and the store reaches lib/base.ts, which
// reads window.location at module scope; the band's own cases also MOUNT the
// classic panel. So a whole jsdom goes in before the dynamic imports, the
// flows test idiom (tonightPanelDom.test.tsx), matchMedia matching because
// Overlay's useMediaQuery asks during render.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
{
  const gl = globalThis as any;
  for (const k of [
    "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
    "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
    "matchMedia", "WebSocket", "location", "history",
  ]) {
    const v = k === "window" ? win : win[k];
    Object.defineProperty(gl, k, { value: v, writable: true, configurable: true });
  }
  installAutoRaf(gl);
  gl.IS_REACT_ACT_ENVIRONMENT = true;
}

const { timelineGeometry, moonPercent, TL_W } = await import("../TonightTimeline");
type TonightTimelineProps = import("../TonightTimeline").TonightTimelineProps;
type TlGeometry = import("../TonightTimeline").TlGeometry;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function atest(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, eps: number, msg: string): void {
  assert(Math.abs(a - b) <= eps, `${msg} (got ${a}, want ~${b})`);
}

// A fixture night: dusk at an arbitrary instant, dawn eight hours later, with
// astronomical dark starting 40 min after dusk and ending 40 min before dawn.
const DUSK = 1_700_000_000;
const H = 3600;
const DAWN = DUSK + 8 * H;
const DARK_START = DUSK + 40 * 60;
const DARK_END = DAWN - 40 * 60;

/** X of an instant under the rule the geometry uses, for expectations. */
function expectX(t: number): number {
  const pad = (DAWN - DUSK) * 0.03;
  return ((t - (DUSK - pad)) / (DAWN - DUSK + 2 * pad)) * TL_W;
}

function props(over: Partial<TonightTimelineProps> = {}): TonightTimelineProps {
  return {
    night: {
      dusk_unix: DUSK, dawn_unix: DAWN,
      dark_start_unix: DARK_START, dark_end_unix: DARK_END,
    },
    flats: null,
    moon: null,
    targets: [],
    ...over,
  };
}

function geo(over: Partial<TonightTimelineProps> = {}): TlGeometry {
  const g = timelineGeometry(props(over));
  if (!g) throw new Error("expected geometry, got null");
  return g;
}

const rectFor = (g: TlGeometry, key: string) => g.rects.find((r) => r.key === key);
const dashFor = (g: TlGeometry, key: string) => g.dashes.find((d) => d.key === key);
const labelText = (g: TlGeometry, key: string) =>
  g.labels.find((l) => l.key === key)?.text;

// ───────────────────────────────────────────────────────────── the axis rule

test("no dusk and no dawn is null, not an empty axis", () => {
  assert(timelineGeometry(props({
    night: { dusk_unix: null, dawn_unix: null, dark_start_unix: null, dark_end_unix: null },
  })) === null, "a night with no bounds has no timeline");
  assert(timelineGeometry(props({
    night: { dusk_unix: DAWN, dawn_unix: DUSK, dark_start_unix: null, dark_end_unix: null },
  })) === null, "dawn before dusk is not a span");
});

test("the axis spans dusk to dawn with a gutter at each end", () => {
  const g = geo();
  const pad = (DAWN - DUSK) * 0.03;
  near(g.t0, DUSK - pad, 0.5, "t0 sits one gutter before dusk");
  near(g.t1, DAWN + pad, 0.5, "t1 sits one gutter after dawn");
  // The gutter exists so the DUSK and DAWN labels, centred on their rules, are
  // not half off the edge of the label layer.
  near(dashFor(g, "dusk")!.x, expectX(DUSK), 0.01, "DUSK sits just inside the left edge");
  near(dashFor(g, "dawn")!.x, expectX(DAWN), 0.01, "DAWN sits just inside the right edge");
});

test("hour ticks are whole clock hours, labelled from the operator's clock", () => {
  const g = geo();
  const hours = g.labels.filter((l) => l.key.startsWith("hour-"));
  assert(hours.length === g.hourTicks.length, "one label per hour tick");
  assert(hours.length >= 7 && hours.length <= 9,
    `an eight-hour night carries 8ish whole hours, got ${hours.length}`);
  assert(hours.every((l) => /^\d{2}:00$/.test(l.text)),
    `every hour label is HH:00, got ${hours.map((l) => l.text).join(",")}`);
});

// ─────────────────────────────────────────────────────────────── twilight

test("twilight is the two gaps the server actually reported", () => {
  const g = geo();
  const l = rectFor(g, "tw-l")!;
  const r = rectFor(g, "tw-r")!;
  near(l.x, expectX(DUSK), 0.01, "the left band starts at dusk");
  near(l.x + l.w, expectX(DARK_START), 0.01, "…and ends at astronomical dark");
  // dark_end_unix is returned by the server and the prototype drew nothing with
  // it, starting its right-hand band at dawn instead (contract E.5).
  near(r.x, expectX(DARK_END), 0.01, "the right band starts when dark ends");
  near(r.x + r.w, expectX(DAWN), 0.01, "…and ends at dawn");
});

test("no dark boundaries means no twilight bands invented around them", () => {
  const g = geo({
    night: { dusk_unix: DUSK, dawn_unix: DAWN, dark_start_unix: null, dark_end_unix: null },
  });
  assert(!rectFor(g, "tw-l") && !rectFor(g, "tw-r"), "neither band is drawn");
  assert(!dashFor(g, "dark"), "and there is no DARK rule to put a label on");
});

// ─────────────────────────────────────────────────────────────────── moon

test("a moon with neither crossing known draws no bar at all", () => {
  // The server searches for crossings only inside [dark_start, dark_end], so a
  // moon already up at dark start returns rise AND set null while the story
  // says "up all night" — which is indistinguishable here from a moon that
  // never rose. A full-width bar would assert a moon-up night nobody measured.
  const g = geo({ moon: { illumination: 0.71, rise_unix: null, set_unix: null } });
  assert(!rectFor(g, "moon"), "no moon bar");
  assert(!dashFor(g, "moonrise"), "and no moonrise rule");
});

test("a known moonrise runs to the end of the span and carries the illumination", () => {
  const rise = DUSK + 3 * H;
  const g = geo({ moon: { illumination: 0.71, rise_unix: rise, set_unix: null } });
  const bar = rectFor(g, "moon")!;
  near(bar.x, expectX(rise), 0.01, "the bar starts at moonrise");
  near(bar.x + bar.w, TL_W, 0.01, "and does not claim a set it was not told");
  assert(labelText(g, "moonrise-label") === "☾ 71%",
    `the rule is labelled from moon.illumination, got ${labelText(g, "moonrise-label")}`);
});

test("a known moonset with no rise starts at the span's edge", () => {
  const set = DUSK + 2 * H;
  const g = geo({ moon: { illumination: null, rise_unix: null, set_unix: set } });
  const bar = rectFor(g, "moon")!;
  near(bar.x, 0, 0.01, "the moon was already up when the span opened");
  near(bar.x + bar.w, expectX(set), 0.01, "and the set is the one measured instant");
  assert(!dashFor(g, "moonrise"), "no rise, so no moonrise rule");
});

test("moonPercent reports nothing rather than a stand-in", () => {
  assert(moonPercent(null) === null, "no moon block, no percentage");
  assert(moonPercent({ illumination: null, rise_unix: null, set_unix: null }) === null,
    "no illumination, no percentage");
  assert(moonPercent({ illumination: 0.706, rise_unix: null, set_unix: null }) === 71,
    "a real fraction rounds to whole percent");
});

// ────────────────────────────────────────────────────────────────── flats

test("a flats window with either bound missing draws nothing", () => {
  const half = geo({ flats: { start_unix: DUSK + 600, end_unix: null } });
  assert(!rectFor(half, "flats"), "an unparseable window has no clock time to draw");
  const both = geo({
    flats: { start_unix: DUSK + 600, end_unix: DUSK + 1500 },
  });
  const block = rectFor(both, "flats")!;
  near(block.x, expectX(DUSK + 600), 0.01, "…and a parsed one is drawn at its own times");
  assert(labelText(both, "flats-label") === "FLATS", "labelled FLATS in body ink");
});

// ──────────────────────────────────────────────────────────────── targets

test("an unplaceable target gets no block, no arc and no flip rule", () => {
  // `resolved: false` comes back with window null and an empty curve, and the
  // prototype would still have given it an equal share of the imaging band.
  const g = geo({
    targets: [{
      label: "NGC 6946", window: null, curve: [],
      meridian_flip_unix: DUSK + 4 * H,
    }],
  });
  assert(g.rects.every((r) => !r.key.startsWith("tgt-")), "no imaging block");
  assert(g.paths.length === 0, "no altitude arc");
  assert(!dashFor(g, "flip-0"), "and no meridian flip in a night it never enters");
});

test("a placed target is drawn at its own window, labelled, with its own curve", () => {
  const s = DUSK + 1 * H;
  const e = DUSK + 6 * H;
  const g = geo({
    targets: [{
      label: "M16",
      window: { start_unix: s, end_unix: e },
      // Includes samples outside the window: the arc is clipped to the block the
      // legend ties it to.
      curve: [[DUSK, 5], [s, 30], [s + 2 * H, 90], [e, 30], [DAWN, 5]],
      meridian_flip_unix: s + 2 * H,
    }],
  });
  const block = rectFor(g, "tgt-0")!;
  near(block.x, expectX(s), 0.01, "the block starts when the window opens");
  near(block.x + block.w, expectX(e), 0.01, "and ends when it closes");
  assert(labelText(g, "tgt-0-label") === "M16", "labelled with the server's short name");
  near(dashFor(g, "flip-0")!.x, expectX(s + 2 * H), 0.01, "the flip rule is placed");

  const pts = g.paths[0].d.replace(/^M/, "").split("L").map((p) => p.split(" ").map(Number));
  assert(pts.length === 3, `only the three in-window samples are drawn, got ${pts.length}`);
  // 0° at the band's foot (y=106), 90° at its head (y=36).
  near(pts[1][1], 36, 0.05, "the 90° sample sits at the top of the band");
  near(pts[0][1], 36 + 70 * (1 - 30 / 90), 0.05, "and 30° two-thirds of the way down");
});

test("a single-sample curve draws no arc", () => {
  const s = DUSK + 1 * H;
  const g = geo({
    targets: [{
      label: "M16", window: { start_unix: s, end_unix: s + H },
      curve: [[s, 40]], meridian_flip_unix: null,
    }],
  });
  assert(g.paths.length === 0, "one point is not a line");
});

// ──────────────────────────────────────────────────────────────── labels

test("every rule that is drawn is labelled, and none that is not", () => {
  const g = geo();
  assert(labelText(g, "dusk-label") === "DUSK", "DUSK");
  assert(labelText(g, "dark-label") === "DARK", "DARK");
  assert(labelText(g, "dawn-label") === "DAWN", "DAWN");
  assert(g.dashes.every((d) => g.labels.some((l) => l.key === `${d.key}-label`)),
    "no unlabelled dashed rule");
});

// ──────────────────────────────────────────────────────── the mosaic band
//
// #189 S3 item 5: a mosaic block's TIMELINE band, drawn between its worst and
// best panel (the peak-altitude spread `tonight.py::_band` reduces the panels
// to), across the block's own window, and told apart from the block by SHAPE,
// not hue (spec 2.3: under the night palette every token collapses to red).
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

const { mosaicBand } = await import("../../../lib/flowsApi");
const { withMosaicBands, bandText } = await import("../TonightTimeline");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const TonightPanel = (await import("../TonightPanel")).default;
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
type TonightTarget = import("../TonightTimeline").TonightTarget;

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

const ROWS = TONIGHT_2X3.targets as any[];
const REC_NIGHT = TONIGHT_2X3.night as any;
const REC_MOON = TONIGHT_2X3.moon as any;

/** The targets exactly as TonightPanel's module-private reader builds them:
 *  four fields, and no `mosaic` - which is why the component reads the band
 *  off the store (`withMosaicBands`). */
function panelReads(rows: any[]): TonightTarget[] {
  return rows.map((t) => ({
    label: t.label || t.name,
    window: t.window ? { start_unix: t.window.start_unix, end_unix: t.window.end_unix } : null,
    curve: t.curve,
    meridian_flip_unix: t.meridian_flip_unix ?? null,
  }));
}

function recordedProps(targets: TonightTarget[]): TonightTimelineProps {
  return {
    night: {
      dusk_unix: REC_NIGHT.dusk_unix, dawn_unix: REC_NIGHT.dawn_unix,
      dark_start_unix: REC_NIGHT.dark_start_unix, dark_end_unix: REC_NIGHT.dark_end_unix,
    },
    flats: null,
    moon: { illumination: REC_MOON.illumination, rise_unix: REC_MOON.rise_unix,
            set_unix: REC_MOON.set_unix },
    targets,
  };
}

function recordedGeo(targets: TonightTarget[]): TlGeometry {
  const g = timelineGeometry(recordedProps(targets));
  if (!g) throw new Error("expected geometry for the recorded night, got null");
  return g;
}

/** y of an altitude inside the band, the rule the arc uses: 0 deg at 106, 90 at 36. */
const altY = (alt: number) => 36 + 70 * (1 - alt / 90);

/** The recorded answer with every row's `mosaic` key taken off: a night with
 *  no mosaic in it, otherwise the same bytes. */
function withoutMosaic(payload: Record<string, unknown>): Record<string, unknown> {
  return {
    ...payload,
    targets: (payload.targets as any[]).map(({ mosaic: _m, ...rest }) => rest),
  };
}

// MUTANT "band from the first panel only" (flowsApi.mosaicBand reads both
// edges off the first answered panel, `mosaic.panels[0]`, instead of the
// server's worst and best). Observed, 18/21:
//   x a mosaic's band runs from its best panel's peak down to its worst's,
//     across its window: its top edge is the best panel's peak, 1-3 at 37.4
//     (got 78.15555555555555, want ~76.91111111111111)
// MUTANT "band across the whole night" (the band's rect laid from the axis'
// left edge to its right, not across the block's window). Observed,
// 20/21:
//   x a mosaic's band runs from its best panel's peak down to its worst's,
//     across its window: the band starts where the block's window opens (got
//     0, want ~341.5753584852571)
// MUTANT "band label centred" (the band label's `anchor: "end"` dropped, so
// it straddles the window's end). Observed, 19/21:
//   x a mosaic's band runs from its best panel's peak down to its worst's,
//     across its window: the band's label ends on the band's right end, inside
//     the block it names
test("a mosaic's band runs from its best panel's peak down to its worst's, across its window", () => {
  const band = mosaicBand(ROWS[0].mosaic);
  assert(band !== null, "premise: the recorded M16 row carries a band");
  const g = recordedGeo(withMosaicBands(panelReads(ROWS), ROWS));
  const block = rectFor(g, "tgt-0")!;
  const strip = rectFor(g, "band-0");
  assert(block && strip !== undefined, "the mosaic block drew no band");
  near(strip!.x, block.x, 1e-9, "the band starts where the block's window opens");
  near(strip!.w, block.w, 1e-9, "and is exactly as wide as the block");
  near(strip!.y, altY(37.4), 1e-9, "its top edge is the best panel's peak, 1-3 at 37.4");
  near(strip!.y + strip!.h, altY(35.0), 1e-9, "its foot is the worst panel's peak, 2-1 at 35.0");
  const label = g.labels.find((l) => l.key === "band-0-label");
  assert(label?.text === "2-1 low, 1-3 high",
    `the band names its worst and best panel, got ${JSON.stringify(label?.text)}`);
  assert(label?.anchor === "end",
    "the band's label ends on the band's right end, inside the block it names");
  near(label!.leftPct, ((strip!.x + strip!.w) / TL_W) * 100, 1e-9,
    "and is placed on that right end");
});

test("bandText names one panel when the worst and the best are the same", () => {
  const one = { panel: "1-1", row: 0, col: 0, transit_alt: 40 };
  assert(bandText({ worst: one, best: one }) === "1-1", "a single panel is named once");
  assert(bandText({ worst: one, best: { ...one, panel: "2-2", transit_alt: 41 } })
    === "1-1 low, 2-2 high", "two panels: which peaks lowest, which highest");
});

// MUTANT "band drawn as a flat block" (the band's rect built without
// `shape: "hatch"`, so it is a flat fill like its block). Observed,
// 18/21:
//   x the band is told apart from its block by shape, not by hue: the band
//     is hatched: its shape is what tells it apart
test("the band is told apart from its block by shape, not by hue", () => {
  const g = recordedGeo(withMosaicBands(panelReads(ROWS), ROWS));
  const block = rectFor(g, "tgt-0")!;
  const strip = rectFor(g, "band-0")!;
  assert(strip !== undefined, "the mosaic block drew no band");
  assert(strip.tone === block.tone,
    `the band wears its block's own tone (${block.tone}); a second hue would vanish at night`);
  assert(strip.shape === "hatch", "the band is hatched: its shape is what tells it apart");
  assert(block.shape === undefined, "the block stays a flat fill");
});

// CONTROL: the single target and the pool members draw byte for byte what
// they drew before. MUTANT "withMosaicBands rebuilds every row" (a row with
// no band handed back as a fresh copy, `{ ...t, band }` with band null).
// Observed, 19/21:
//   x CONTROL: the single target and the pool draw byte for byte what they
//     drew before: M31 was handed on as a new object; its row must be the one
//     it was
test("CONTROL: the single target and the pool draw byte for byte what they drew before", () => {
  const plain = panelReads(ROWS);
  const filled = withMosaicBands(plain, ROWS);
  for (const i of [1, 2, 3]) {
    assert(mosaicBand(ROWS[i].mosaic) === null, `premise: row ${i} is not a mosaic`);
    assert(filled[i] === plain[i],
      `${plain[i].label} was handed on as a new object; its row must be the one it was`);
  }
  const before = recordedGeo(plain);
  const after = recordedGeo(filled);
  const noBand = (g: TlGeometry) => ({
    ...g,
    rects: g.rects.filter((r) => !r.key.startsWith("band-")),
    labels: g.labels.filter((l) => !l.key.startsWith("band-")),
  });
  assert(JSON.stringify(noBand(after)) === JSON.stringify(before),
    "adding the band moved something else on the night");
  assert(before.rects.every((r) => r.shape === undefined)
    && before.labels.every((l) => l.anchor === undefined),
    "a night drawn with no band has no hatched rect and no end-anchored label");
  const none = withoutMosaic(TONIGHT_2X3).targets as any[];
  assert(withMosaicBands(plain, none) === plain,
    "a night with no mosaic must get the very array it handed in");
});

// MUTANT "match by place only" (withMosaicBands' label check deleted, so any
// list of the right length fills by index). Observed, 20/21:
//   x the band fills only from the answer these targets came from: a row
//     whose label is not the target's is not that target's row
// MUTANT "no length check" (withMosaicBands' `rawTargets.length !==
// targets.length` refusal deleted). Found SURVIVING by the S3-U2 verifier
// while this case read `ROWS.slice(1)`: that list is shifted, so the label
// check refused it too and the length rule was never the one graded. The
// list below keeps every one of the four rows at its place, labels and all,
// and adds a fifth, so only the length can refuse it. Run in the verifier's
// private copy (scratchpad/s3-u2-verify-k7r2/). Observed, 20/21:
//   x the band fills only from the answer these targets came from: a list of
//     another length is not the answer the panel read, even with every row it
//     shares in its place
test("the band fills only from the answer these targets came from", () => {
  const plain = panelReads(ROWS);
  assert(withMosaicBands(plain, ROWS.slice(1)) === plain,
    "a shifted list is not the answer the panel read");
  assert(withMosaicBands(plain, [...ROWS, ROWS[3]]) === plain,
    "a list of another length is not the answer the panel read, even with every "
    + "row it shares in its place");
  const renamed = ROWS.map((r, i) => (i === 0 ? { ...r, label: "M17", name: "M17" } : r));
  assert(withMosaicBands(plain, renamed) === plain,
    "a row whose label is not the target's is not that target's row");
  const already = plain.map((t, i) => (i === 0 ? { ...t, band: null } : t));
  assert(withMosaicBands(already, ROWS)[0].band === null,
    "a target that already carries a band (or null) is left as it is");
});

// ------------------------------------------------ the classic panel, mounted
const tlRoot = createRoot(win.document.getElementById("root"));

/** Mount the REAL TonightPanel on the TIMELINE tab with `payload` in the
 *  store. No record is open, so it does not fetch; `flowsFetchTonight` is a
 *  no-op anyway, since the real one reaches the network. */
function mountPanel(payload: Record<string, unknown>): void {
  act(() => tlRoot.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT, record: null, tonight: payload, tonightLoading: false,
        tonightError: null,
        ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "timeline" },
      },
      flowsFetchTonight: async () => {},
    } as any);
  });
  act(() => tlRoot.render(createElement(TonightPanel)));
}

const doc = win.document as Document;
const svgOf = (): SVGSVGElement | null => doc.querySelector("svg[viewBox='0 0 1000 150']");
const labelEl = (text: string): HTMLElement | undefined =>
  [...doc.querySelectorAll<HTMLElement>("div")].find((d) => d.textContent === text
    && d.style.left !== "");

// MUTANT "classic panel never reads the band" (withMosaicBands hands the
// targets back untouched, which is what the panel's own reader gives it).
// Observed, 18/21:
//   x the classic panel draws the band although its own reader drops the
//     key: the classic timeline drew no band for the recorded 2x3
await atest("the classic panel draws the band although its own reader drops the key", () => {
  mountPanel(TONIGHT_2X3);
  const svg = svgOf();
  assert(!!svg, "precondition: the classic TIMELINE drew its svg");
  const strip = svg!.querySelector("rect[data-shape='hatch']");
  assert(!!strip, "the classic timeline drew no band for the recorded 2x3");
  const m = /^url\(#(.+)\)$/.exec(strip!.getAttribute("fill") ?? "");
  assert(!!m, `the band is filled with a pattern, got ${strip!.getAttribute("fill")}`);
  assert(doc.getElementById(m![1])?.tagName.toLowerCase() === "pattern",
    "the band's fill names a pattern the svg defines");
  // The band's hatch lines and its outline wear its block's own tone (spec
  // 2.3), and the outline is what keeps a band two degrees deep visible at
  // all. MUTANTS "classic hatch lines in another tone" (the pattern's line
  // stroked TONE_VAR.ink) and "classic band outline dropped" (the hatched
  // rect's stroke deleted), both found SURVIVING by the S3-U2 verifier and
  // run in its private copy (scratchpad/s3-u2-verify-k7r2/). Observed, 20/21
  // each:
  //   x the classic panel draws the band although its own reader drops the
  //     key: the band's hatch lines are its block's own tone, got var(--text)
  //     for var(--accent)
  //   x the classic panel draws the band although its own reader drops the
  //     key: the band's outline is its block's own tone, got null for
  //     var(--accent)
  const block = [...svg!.querySelectorAll("rect")].find((e) => e !== strip
    && e.getAttribute("x") === strip!.getAttribute("x") && e.getAttribute("height") === "70");
  assert(!!block, "precondition: the M16 block starts where its band does");
  assert(strip!.getAttribute("stroke") === block!.getAttribute("fill"),
    `the band's outline is its block's own tone, got ${strip!.getAttribute("stroke")} `
    + `for ${block!.getAttribute("fill")}`);
  const hatchLine = doc.getElementById(m![1])?.querySelector("line");
  assert(hatchLine?.getAttribute("stroke") === block!.getAttribute("fill"),
    `the band's hatch lines are its block's own tone, got ${hatchLine?.getAttribute("stroke")} `
    + `for ${block!.getAttribute("fill")}`);
  near(Number(strip!.getAttribute("y")), altY(37.4), 1e-6, "top edge at the best panel's peak");
  near(Number(strip!.getAttribute("y")) + Number(strip!.getAttribute("height")), altY(35.0),
    1e-6, "foot at the worst panel's peak");
  const label = labelEl("2-1 low, 1-3 high");
  assert(!!label, "the band's label is in the HTML layer");
  assert(label!.style.transform === "translate(-100%,-50%)",
    `the label ends on the band's right end, got ${label!.style.transform}`);
  assert(!!doc.querySelector("[data-legend='mosaic-band']"),
    "the legend says what the hatched band is");
});

// CONTROL. MUTANT "legend always shows the band" (the legend entry drawn
// whether or not a band is). Observed, 20/21:
//   x CONTROL: a night with no mosaic renders the markup it always did: no
//     legend entry for a mark the picture does not have
await atest("CONTROL: a night with no mosaic renders the markup it always did", () => {
  mountPanel(withoutMosaic(TONIGHT_2X3));
  const svg = svgOf();
  assert(!!svg, "precondition: the classic TIMELINE drew its svg");
  assert(!svg!.querySelector("defs") && !svg!.querySelector("rect[data-shape]"),
    "no pattern and no hatched rect on a night with no band");
  assert(!doc.querySelector("[data-legend='mosaic-band']"),
    "no legend entry for a mark the picture does not have");
  const layer = svg!.parentElement!;
  const legend = layer.nextElementSibling!;
  const plain = layer.outerHTML + legend.outerHTML;

  // THE PLAIN NIGHT IS WHAT IT WAS BEFORE MOSAICS, not only the mosaic night
  // less its band. The comparison below is relative, so a change that moves
  // every night's marks alike passes it. S3-U2 rewrote two lines every night
  // draws through: the flat rect, now the other arm of the hatch branch, and
  // the label's transform, now the other arm of the anchor branch. Both are
  // pinned here to what they drew before S3. MUTANTS "classic plain label
  // transform moved" (the centred arm read "translate(-50%,-60%)") and
  // "classic plain rect loses its corner radius" (rx dropped from the flat
  // arm), both found SURVIVING by the S3-U2 verifier and run in its private
  // copy (scratchpad/s3-u2-verify-k7r2/). Observed, 20/21 each:
  //   x CONTROL: a night with no mosaic renders the markup it always did:
  //     every label on a night with no band is centred on its point, as
  //     before mosaics; 21:00 reads translate(-50%,-60%)
  //   x CONTROL: a night with no mosaic renders the markup it always did:
  //     every block on a night with no band keeps its corner radius, as
  //     before mosaics; got rx=null
  const plainLabels = [...layer.querySelectorAll<HTMLElement>("div")]
    .filter((d) => d.style.left !== "");
  assert(plainLabels.length > 0, "precondition: the plain night drew its labels");
  const moved = plainLabels.find((d) => d.style.transform !== "translate(-50%,-50%)");
  assert(!moved, "every label on a night with no band is centred on its point, as before "
    + `mosaics; ${moved?.textContent} reads ${moved?.style.transform}`);
  const square = [...svg!.querySelectorAll("rect")].find((e) => e.getAttribute("rx") !== "3");
  assert(!square, "every block on a night with no band keeps its corner radius, as before "
    + `mosaics; got rx=${square?.getAttribute("rx")}`);

  mountPanel(TONIGHT_2X3);
  const svg2 = svgOf()!;
  const layer2 = svg2.parentElement!;
  const legend2 = layer2.nextElementSibling!;
  svg2.querySelector("defs")?.remove();
  svg2.querySelectorAll("rect[data-shape='hatch']").forEach((e) => e.remove());
  labelEl("2-1 low, 1-3 high")?.remove();
  legend2.querySelector("[data-legend='mosaic-band']")?.remove();
  assert(layer2.outerHTML + legend2.outerHTML === plain,
    "with the band's own marks taken out, the mosaic night's markup must be the plain "
    + "night's: the band may add marks, never move one");
});

act(() => tlRoot.unmount());

// eslint-disable-next-line no-console
console.log(`\ntonightTimeline: ${passed} passed, ${failed} failed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

const total = passed + failed;
export const result = { passed, failed, total };
