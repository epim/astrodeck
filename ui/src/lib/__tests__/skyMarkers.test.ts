// skyMarkers.test.ts — the Atlas actually draws the sky, and a tap finds it.
//
//   Run directly:  npx tsx src/lib/__tests__/skyMarkers.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THIS FILE EXISTS AT ALL. Task #111 ("add named stars, planets, Moon, Sun
// to the Atlas") was closed on work that rendered nothing: the objects reached
// the search dropdown and never reached the canvas. The suite did not notice
// because no test asserted anything about the picture — views/__tests__/
// atlasHonesty.test.tsx says so in its own header ("Nothing here asserts
// anything about the picture — only about the controls around it").
//
// So every assertion below is about a PLACE. The expected screen coordinates
// are derived here from the gnomonic identities, in this file, NOT by calling
// the projection the code under test calls — a test that computes its
// expectation with the function it is testing agrees with any bug in it.
//
//   On the tangent point's own meridian (dRA = 0), a point d degrees away in
//   declination projects to eta = tan(d), xi = 0. The Atlas maps that to
//   y = view/2 - tan(d)*pxPerDeg, x = view/2  (North-up, East-LEFT).
//
// Two cases get their own tests because they are where a projection fails
// QUIETLY rather than loudly: the 0h/24h RA wrap (an object 0.3° away read as
// 23.98 hours away) and the celestial pole (where "12 hours of RA apart" and
// "1 degree apart" are the same thing).

import {
  hitTest, labelBudget, markerRadius, placeSky, glyphFor, MIN_R,
  type SkyRow,
} from "../skyMarkers";
import type { AtlasViewGeom } from "../atlasFov";

const RAD = 180 / Math.PI;
const VIEW = 1000;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, tol: number, what: string): void {
  assert(Math.abs(a - b) <= tol, `${what}: got ${a}, expected ${b} (±${tol})`);
}

/** A deterministic stand-in for the browser's text measurement. Real width
 *  comes from canvas measureText in SkyCanvas; the placement logic only needs
 *  A width, and a fixed one makes every collision assertion reproducible. */
const measure = (t: string) => t.length * 8 + 8;

function row(over: Partial<SkyRow> & { id: string }): SkyRow {
  return {
    label: over.id,
    kind: "dso",
    type: "Galaxy",
    ra_hours: 0,
    dec_deg: 0,
    mag: 9,
    size_arcmin: 0,
    constellation: "Andromeda",
    describe: "galaxy in Andromeda · mag 9.0",
    alias: null,
    ...over,
  } as SkyRow;
}

function geomAt(ra: number, dec: number, fovDeg: number): AtlasViewGeom {
  return {
    centerRaHours: ra,
    centerDecDeg: dec,
    pxPerDeg: VIEW / fovDeg,
    view: VIEW,
  };
}

const BOX = 400; // CSS px canvas edge

// ============================================ a marker lands where it belongs

test("an object at the view centre draws a marker at the centre of the canvas", () => {
  const geom = geomAt(0.712, 41.269, 2);
  const { markers } = placeSky([row({ id: "M31", ra_hours: 0.712, dec_deg: 41.269 })],
                               geom, { boxPx: BOX, measure });
  assert(markers.length === 1,
    `expected exactly one marker, got ${markers.length} — nothing is drawn, ` +
    "which is precisely the state #111 shipped in");
  near(markers[0].x, VIEW / 2, 1e-6, "marker x");
  near(markers[0].y, VIEW / 2, 1e-6, "marker y");
});

test("an object half a degree north lands half a degree up, by the gnomonic identity", () => {
  const fov = 2;
  const geom = geomAt(5.0, 10.0, fov);
  const dDeg = 0.5;
  const { markers } = placeSky(
    [row({ id: "X", ra_hours: 5.0, dec_deg: 10.0 + dDeg })], geom,
    { boxPx: BOX, measure },
  );
  // eta = tan(d) on the tangent point's own meridian, and the Atlas is N-up.
  const expectedY = VIEW / 2 - Math.tan(dDeg / RAD) * RAD * (VIEW / fov);
  assert(markers.length === 1, "the object was not placed at all");
  near(markers[0].x, VIEW / 2, 1e-6, "x should not move on the same meridian");
  near(markers[0].y, expectedY, 1e-6, "marker y");
  assert(expectedY < VIEW / 2, "sanity: north must be UP on this canvas");
});

test("a tap at the marker's own coordinate resolves to that object", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const { markers } = placeSky(
    [row({ id: "M13", ra_hours: 5.0, dec_deg: 10.4 }),
     row({ id: "elsewhere", ra_hours: 5.0, dec_deg: 9.4 })],
    geom, { boxPx: BOX, measure },
  );
  const m = markers.find((k) => k.row.id === "M13")!;
  const hit = hitTest(markers, m.x, m.y, 22 * (VIEW / BOX));
  assert(hit?.row.id === "M13",
    `a tap on M13's own pixel resolved to ${hit?.row.id ?? "nothing"}`);
});

test("a tap on empty sky resolves to nothing, not to the nearest thing anywhere", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const { markers } = placeSky([row({ id: "M13", ra_hours: 5.0, dec_deg: 10.4 })],
                               geom, { boxPx: BOX, measure });
  const m = markers[0];
  const reach = 22 * (VIEW / BOX);
  const hit = hitTest(markers, m.x + reach * 4, m.y + reach * 4, reach);
  assert(hit === null,
    `a tap 4 tap-radii away claimed ${hit && (hit as { row: SkyRow }).row.id} — ` +
    "an info card for something the user did not touch is worse than none");
});

test("the tap target is thumb-sized, not marker-sized", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const { markers } = placeSky([row({ id: "tiny", ra_hours: 5.0, dec_deg: 10.0 })],
                               geom, { boxPx: BOX, measure });
  const m = markers[0];
  assert(m.r <= MIN_R + 1e-9, "fixture: this marker should be the floor glyph");
  const reach = 22 * (VIEW / BOX);       // 22 CSS px -> viewBox units
  // 20 CSS px away: outside the 5-unit glyph, inside the finger.
  const off = 20 * (VIEW / BOX);
  assert(hitTest(markers, m.x + off, m.y, reach)?.row.id === "tiny",
    "a tap 20px from a marker missed it — that is a 10px target in the dark");
});

test("the whole face of a drawn galaxy is tappable, but a small marker inside it still wins", () => {
  // M31 is 190' across (3.17 deg); at a 6 deg zoom its outline is a big drawn
  // ellipse that still fits comfortably inside the canvas.
  const geom = geomAt(0.712, 41.269, 6);
  const { markers } = placeSky(
    [row({ id: "M31", ra_hours: 0.712, dec_deg: 41.269, size_arcmin: 190 }),
     row({ id: "M32", ra_hours: 0.712, dec_deg: 41.269 + 1.0 })],
    geom, { boxPx: BOX, measure },
  );
  const m31 = markers.find((k) => k.row.id === "M31")!;
  const m32 = markers.find((k) => k.row.id === "M32")!;
  assert(m31.extended, "fixture: M31 should be drawn at its real size here");
  const reach = 22 * (VIEW / BOX);
  // Well inside M31's face, far from any centre-glyph.
  assert(hitTest(markers, m31.x + m31.r * 0.7, m31.y, reach)?.row.id === "M31",
    "a tap inside the drawn galaxy did not select it");
  // On M32, which sits inside M31's outline: nearest centre wins.
  assert(hitTest(markers, m32.x, m32.y, reach)?.row.id === "M32",
    "a small object inside a big one's outline was unselectable");
});

// ================================================= where projections fail quietly

test("across the 0h/24h wrap an object 0.3 deg away is drawn 0.3 deg away", () => {
  const fov = 2;
  const geom = geomAt(0.01, 0.0, fov);
  const { markers } = placeSky([row({ id: "wrapped", ra_hours: 23.99, dec_deg: 0 })],
                               geom, { boxPx: BOX, measure });
  assert(markers.length === 1,
    "an object 0.3 deg away across the RA wrap was not drawn at all — a naive " +
    "RA difference makes it 23.98 HOURS away and it falls off the canvas");
  // dRA = -0.3 deg on the equator: xi = tan(-0.3), eta = 0, and x = half - xi*ppd.
  const expectedX = VIEW / 2 + Math.tan(0.3 / RAD) * RAD * (VIEW / fov);
  near(markers[0].x, expectedX, 1e-6, "wrapped marker x");
  near(markers[0].y, VIEW / 2, 1e-6, "wrapped marker y");
});

test("near the pole, 12 hours of RA away is 1 degree away and is drawn there", () => {
  const fov = 4;
  const geom = geomAt(0.0, 89.5, fov);
  const { markers } = placeSky(
    [row({ id: "over-the-pole", ra_hours: 12.0, dec_deg: 89.5 })], geom,
    { boxPx: BOX, measure },
  );
  assert(markers.length === 1,
    "an object 1 deg away over the pole was not drawn — at dec 89.5 every " +
    "right ascension is 'close', and an RA-difference test cannot know that");
  // Both points are 0.5 deg from the pole on opposite meridians: 1 deg apart,
  // straight 'north' across the pole on this tangent plane.
  const expectedY = VIEW / 2 - Math.tan(1.0 / RAD) * RAD * (VIEW / fov);
  near(markers[0].x, VIEW / 2, 1e-6, "pole-crossing marker x");
  near(markers[0].y, expectedY, 1e-6, "pole-crossing marker y");
});

test("an object on the far hemisphere is refused, not mirrored onto this one", () => {
  const geom = geomAt(0.0, 0.0, 60);
  const { markers } = placeSky([row({ id: "antipode", ra_hours: 12.0, dec_deg: 0 })],
                               geom, { boxPx: BOX, measure });
  assert(markers.length === 0,
    "a point 180 deg away was placed on this map. The gnomonic divides by " +
    "cos(separation), which goes negative past 90 deg, so it comes back " +
    "MIRRORED onto the near side — at the antipode, dead centre.");
});

test("an object scrolled off the edge is dropped, and one on the edge is kept", () => {
  const fov = 2;                       // canvas spans 2 deg, so 1 deg = the edge
  const geom = geomAt(5.0, 0.0, fov);
  const off = placeSky([row({ id: "gone", ra_hours: 5.0, dec_deg: 5.0 })], geom,
                       { boxPx: BOX, measure });
  assert(off.markers.length === 0, "an object 5 deg outside a 2 deg view was drawn");
  const on = placeSky([row({ id: "rim", ra_hours: 5.0, dec_deg: 0.98 })], geom,
                      { boxPx: BOX, measure });
  assert(on.markers.length === 1, "an object just inside the top edge was dropped");
});

// ====================================================== labels: budget and collisions

test("two objects at the same point get two markers and one label", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const { markers, labels } = placeSky(
    [row({ id: "First", ra_hours: 5.0, dec_deg: 10.0 }),
     row({ id: "Second", ra_hours: 5.0, dec_deg: 10.0 })],
    geom, { boxPx: BOX, measure },
  );
  assert(markers.length === 2, "an overlapping object was silently dropped");
  const ids = labels.map((l) => l.id);
  assert(labels.length === 2 && ids[0] === "First",
    `expected both to be labelled on opposite sides, got ${JSON.stringify(ids)}`);
  assert(labels[0].anchor !== labels[1].anchor,
    "two labels for objects at the SAME pixel took the same anchor — they are " +
    "printed on top of each other");
});

test("a label that cannot fit anywhere is dropped; its marker and its tap target are not", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const rows = [
    row({ id: "AAAA", ra_hours: 5.0, dec_deg: 10.0 }),
    row({ id: "BBBB", ra_hours: 5.0, dec_deg: 10.0 }),
    row({ id: "CCCC", ra_hours: 5.0, dec_deg: 10.0 }),
    row({ id: "DDDD", ra_hours: 5.0, dec_deg: 10.0 }),
    row({ id: "EEEE", ra_hours: 5.0, dec_deg: 10.0 }),
  ];
  const { markers, labels } = placeSky(rows, geom, { boxPx: BOX, measure });
  assert(markers.length === 5, "markers were lost with the labels");
  assert(labels.length <= 4,
    `five objects stacked on one pixel produced ${labels.length} labels — ` +
    "there are only four anchors, so at least one must go unlabelled");
  const reach = 22 * (VIEW / BOX);
  assert(hitTest(markers, markers[4].x, markers[4].y, reach) !== null,
    "the unlabelled object became untappable — losing a label must not lose " +
    "the object");
});

test("the label budget is honoured and scales with the canvas", () => {
  const geom = geomAt(5.0, 10.0, 4);
  // A grid of well-separated objects: nothing collides, so only the budget can
  // limit the labels.
  const rows: SkyRow[] = [];
  for (let i = 0; i < 40; i++) {
    rows.push(row({
      id: `obj${i}`,
      ra_hours: 5.0 + ((i % 8) - 4) * 0.03,
      dec_deg: 10.0 + (Math.floor(i / 8) - 2) * 0.35,
    }));
  }
  const phone = placeSky(rows, geom, { boxPx: 390, measure });
  const desk = placeSky(rows, geom, { boxPx: 720, measure });
  assert(phone.markers.length === 40, "markers are budgeted — they must not be");
  assert(phone.labels.length <= labelBudget(390),
    `${phone.labels.length} labels on a 390px phone, budget ${labelBudget(390)}`);
  assert(desk.labels.length > phone.labels.length,
    "a canvas nearly twice as wide showed no more names than a phone");
});

test("solar-system bodies are labelled even when they rank below the budget", () => {
  const geom = geomAt(5.0, 10.0, 4);
  const rows: SkyRow[] = [];
  for (let i = 0; i < 30; i++) {
    rows.push(row({
      id: `bright${i}`,
      ra_hours: 5.0 + ((i % 6) - 3) * 0.04,
      dec_deg: 10.0 + (Math.floor(i / 6) - 2) * 0.35,
    }));
  }
  // Last in score order — the position a budget would cut first.
  rows.push(row({
    id: "Uranus", kind: "solar_system", type: "Planet", mag: 5.8,
    ra_hours: 5.0, dec_deg: 10.9, describe: "in Taurus · 3.5\" disc",
  }));
  const { labels } = placeSky(rows, geom, { boxPx: 390, measure });
  assert(labels.some((l) => l.id === "Uranus"),
    "a planet went unnamed on a map full of anonymous galaxies — the planets " +
    "are the only things on this map that move, and the first thing anyone " +
    "with a new telescope asks about");
});

test("a label keeps the side it was on last frame while the sky moves under it", () => {
  const first = placeSky(
    [row({ id: "Steady", ra_hours: 5.0, dec_deg: 10.0 })],
    geomAt(5.0, 10.0, 2), { boxPx: BOX, measure },
  );
  const anchor = first.labels[0].anchor;
  // Force the default order to prefer a different anchor by blocking "e" with a
  // reserved box, then confirm the sticky anchor still wins when it fits.
  const moved = placeSky(
    [row({ id: "Steady", ra_hours: 5.0, dec_deg: 10.02 })],
    geomAt(5.0, 10.0, 2),
    { boxPx: BOX, measure, sticky: new Map([["Steady", "s" as const]]) },
  );
  assert(anchor === "e", `fixture: the default anchor should be "e", got ${anchor}`);
  assert(moved.labels[0].anchor === "s",
    `the label ignored the side it was on last frame (${moved.labels[0].anchor}) ` +
    "— re-running greedy placement every frame makes labels flicker as the " +
    "view moves a pixel");
});

test("a label never lands on the canvas's own furniture", () => {
  const geom = geomAt(5.0, 10.0, 2);
  const bottomLeft = { x: 0, y: BOX - 22, w: 130, h: 22 };
  // An object near the bottom-left corner, where the pixel-scale readout lives.
  const { labels } = placeSky(
    [row({ id: "Corner", ra_hours: 5.0, dec_deg: 10.0 - 0.95 })],
    geom, { boxPx: BOX, measure, reserved: [bottomLeft] },
  );
  for (const l of labels) {
    const overlaps =
      l.left < bottomLeft.x + bottomLeft.w && bottomLeft.x < l.left + measure(l.text) &&
      l.top < bottomLeft.y + bottomLeft.h && bottomLeft.y < l.top + 16;
    assert(!overlaps,
      "an object label was printed over the pixel-scale readout — two texts in " +
      "one place is neither");
  }
});

// ======================================================= shapes and sizes

test("each kind of thing gets its own shape, so colour is never the only signal", () => {
  assert(glyphFor(row({ id: "a", type: "Galaxy" })) === "galaxy", "galaxy");
  assert(glyphFor(row({ id: "b", type: "Emission Nebula" })) === "nebula", "nebula");
  assert(glyphFor(row({ id: "c", type: "Globular Cluster" })) === "cluster", "cluster");
  assert(glyphFor(row({ id: "d", kind: "star", type: "Star" })) === "star", "star");
  assert(glyphFor(row({ id: "e", kind: "solar_system", type: "Planet" })) === "body",
    "solar-system body");
  const shapes = new Set(["galaxy", "nebula", "cluster", "star", "body"]);
  assert(shapes.size === 5, "two kinds share a shape");
});

test("a big object is drawn at its real size; a tiny one is drawn at the floor", () => {
  const ppd = VIEW / 6;                                  // 6 deg across the canvas
  const big = markerRadius(row({ id: "M31", size_arcmin: 190 }), ppd, VIEW);
  near(big.r, (190 / 60 / 2) * ppd, 1e-9, "M31 semi-axis");
  assert(big.extended, "M31 should be drawn at its true extent at this zoom");

  const tiny = markerRadius(row({ id: "M57", size_arcmin: 1.4 }), ppd, VIEW);
  assert(!tiny.extended && tiny.r === MIN_R,
    "a 1.4' planetary at a 6 deg zoom is a tenth of a unit across — drawn true " +
    "to size it is invisible and untappable");

  // Zoomed way out, M31's outline would be a smudge; zoomed way in it would
  // wash the whole canvas. Both fall back to the floor glyph.
  const washed = markerRadius(row({ id: "M31", size_arcmin: 190 }), VIEW / 2, VIEW);
  assert(!washed.extended,
    "an outline larger than the canvas is not an outline, it is a wash");
});

test("a star's size comes from its magnitude, and an unmeasured one is not drawn as bright", () => {
  const ppd = VIEW / 2;
  const vega = markerRadius(row({ id: "Vega", kind: "star", mag: 0.03, size_arcmin: 0 }), ppd, VIEW);
  const faint = markerRadius(row({ id: "dim", kind: "star", mag: 4.5, size_arcmin: 0 }), ppd, VIEW);
  const unknown = markerRadius(row({ id: "?", kind: "star", mag: null, size_arcmin: 0 }), ppd, VIEW);
  assert(vega.r > faint.r, "a mag-0 star is not drawn bigger than a mag-4.5 one");
  assert(unknown.r <= faint.r + 1e-9,
    `an unmeasured star was drawn at r=${unknown.r}, a measured mag-4.5 one at ` +
    `r=${faint.r} — "nobody published a brightness" must never render as ` +
    `"it is bright"`);
});

test("the framed object gets a marker but no second size claim", () => {
  const geom = geomAt(0.712, 41.269, 6);
  const rows = [row({ id: "M31", ra_hours: 0.712, dec_deg: 41.269, size_arcmin: 190 })];
  const plain = placeSky(rows, geom, { boxPx: BOX, measure });
  const framed = placeSky(rows, geom, { boxPx: BOX, measure, framedId: "M31" });
  assert(plain.markers[0].extended, "fixture: M31 is normally drawn at its extent");
  assert(!framed.markers[0].extended,
    "the FRAMED object was given a second extent ellipse. FovOverlay already " +
    "draws that one object's angular size at the view centre, so this puts two " +
    "different claims about one object's size on one canvas");
  assert(framed.markers.length === 1,
    "the framed object lost its marker entirely — it must stay tappable");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`skyMarkers.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
