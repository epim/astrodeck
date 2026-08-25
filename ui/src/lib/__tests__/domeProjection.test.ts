// domeProjection.test.ts — the geometry, without a browser.
//
// Every bug this catches is one that would look plausible on screen: a dome
// drawn upside down, azimuth mirrored so a cloud in the east is painted west,
// or cells offset half a step so a bank sits a few degrees from where it is.
// Those are exactly the failures a screenshot does not reveal.
import {
  DOME_TILT_DEG,
  domeCells,
  domeExtent,
  domePeak,
  occlusionFill,
  occlusionWord,
  projectAltAz,
  skyVector,
} from "../domeProjection";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg: string): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function near(a: number, b: number, tol: number, msg: string): void {
  if (!(Math.abs(a - b) <= tol)) {
    throw new Error(`${msg} expected ~${b} (+/-${tol}), got ${a}`);
  }
}

// ------------------------------------------------------------- sky vectors
test("skyVector: the zenith is straight up and nowhere else", () => {
  const v = skyVector(90, 0);
  near(v.z, 1, 1e-9, "zenith z");
  near(Math.hypot(v.x, v.y), 0, 1e-9, "zenith has no horizontal component");
});

test("skyVector: the cardinal points sit on the right axes", () => {
  const n = skyVector(0, 0), e = skyVector(0, 90), s = skyVector(0, 180), w = skyVector(0, 270);
  near(n.y, 1, 1e-9, "North is +y");
  near(e.x, 1, 1e-9, "East is +x");
  near(s.y, -1, 1e-9, "South is -y");
  near(w.x, -1, 1e-9, "West is -x");
  for (const [name, v] of [["N", n], ["E", e], ["S", s], ["W", w]] as const) {
    near(v.z, 0, 1e-9, `${name} is on the horizon`);
  }
});

// --------------------------------------------------------------- projection
test("projectDome: the zenith is above the horizon centre on screen", () => {
  const z = projectAltAz(90, 0, 100, 100, 50);
  near(z.x, 100, 1e-9, "zenith is centred horizontally");
  assert(z.y < 100, `zenith must draw ABOVE centre, got y=${z.y} against cy=100`);
  assert(z.facing, "the zenith is never on the far side");
});

test("projectDome: altitude climbs UP the screen", () => {
  const low = projectAltAz(10, 90, 100, 100, 50);
  const high = projectAltAz(70, 90, 100, 100, 50);
  assert(high.y < low.y, (
    `70 deg altitude must draw above 10 deg; got ${high.y} vs ${low.y}. If this `
    + `flips, the whole dome is upside down and low cloud reads as overhead.`));
});

test("projectDome: east is right, west is left -- not mirrored", () => {
  const e = projectAltAz(0, 90, 100, 100, 50);
  const w = projectAltAz(0, 270, 100, 100, 50);
  assert(e.x > 100, `east must be right of centre, got ${e.x}`);
  assert(w.x < 100, `west must be left of centre, got ${w.x}`);
  near(e.x - 100, 100 - w.x, 1e-9, "east and west are symmetric about centre");
});

test("projectDome: north is at the back, south at the front", () => {
  const n = projectAltAz(0, 0, 100, 100, 50);
  const s = projectAltAz(0, 180, 100, 100, 50);
  assert(n.y < s.y, (
    `looking from the south, North must sit further UP the canvas than South; `
    + `got N y=${n.y}, S y=${s.y}`));
  assert(n.depth < 0, "north is the FAR half (normal points away)");
  assert(s.depth > 0, "south is the near half");
  assert(!n.facing, "the northern horizon is hidden behind the dome");
  assert(s.facing, "the southern horizon faces us");
});

test("projectDome: nothing escapes the horizon circle", () => {
  for (let alt = 0; alt <= 90; alt += 5) {
    for (let az = 0; az < 360; az += 5) {
      const p = projectAltAz(alt, az, 0, 0, 50);
      assert(Math.hypot(p.x, p.y) <= 50 + 1e-9, (
        `alt ${alt} az ${az} projected outside the dome at r=${Math.hypot(p.x, p.y)}`));
    }
  }
});

test("projectDome: a flat tilt collapses the horizon, which is why the default is not 0", () => {
  const n = projectAltAz(0, 0, 0, 0, 50, 0);
  const s = projectAltAz(0, 180, 0, 0, 50, 0);
  near(n.y, s.y, 1e-9, "at tilt 0 the horizon has no depth -- N and S coincide");
  const nTilted = projectAltAz(0, 0, 0, 0, 50, DOME_TILT_DEG);
  const sTilted = projectAltAz(0, 180, 0, 0, 50, DOME_TILT_DEG);
  assert(Math.abs(nTilted.y - sTilted.y) > 10, (
    "at the default tilt the horizon must be a readable ellipse"));
});

// --------------------------------------------------------------- grid cells
test("domeCells: samples are CENTRES, so cells straddle them", () => {
  const cells = domeCells({
    rows: [[0.1, 0.2]], alt_start: 5, alt_step: 10, az_step: 180,
  });
  eq(cells.length, 2, "two azimuth samples");
  eq(cells[0].altLo, 0, "the 5 deg row reaches down to the horizon, not to 5");
  eq(cells[0].altHi, 10, "and up to 10");
  eq(cells[0].azLo, -90, "the az=0 sample spans half a step either side");
  eq(cells[0].azHi, 90, "");
  eq(cells[1].azLo, 90, "the second sample starts where the first ends");
});

test("domeCells: never claims sky below the horizon or above the zenith", () => {
  const cells = domeCells({
    rows: [[0], [0], [0]], alt_start: 5, alt_step: 40, az_step: 360,
  });
  for (const c of cells) {
    assert(c.altLo >= 0, `cell dips below the horizon: ${c.altLo}`);
    assert(c.altHi <= 90, `cell rises past the zenith: ${c.altHi}`);
  }
});

test("domeCells: a null sample stays null rather than becoming clear", () => {
  const cells = domeCells({
    rows: [[null as unknown as number, 0.5]], alt_start: 45, alt_step: 10, az_step: 180,
  });
  eq(cells[0].p, null, "no data must not be rendered as zero occlusion");
  eq(cells[1].p, 0.5, "");
});

// ------------------------------------------------------------------ summary
test("domePeak: the worst cell anywhere, ignoring gaps", () => {
  eq(domePeak({ rows: [[0.01, 0.2], [null as unknown as number, 0.34]],
                alt_start: 5, alt_step: 10, az_step: 180 }), 0.34, "peak");
  eq(domePeak({ rows: [[null as unknown as number]],
                alt_start: 5, alt_step: 10, az_step: 360 }), null, "all gaps");
});

test("occlusionWord and occlusionFill agree about where the boundaries are", () => {
  eq(occlusionWord(0.005), "clear", "");
  eq(occlusionWord(0.05), "thin", "");
  eq(occlusionWord(0.2), "patchy", "");
  eq(occlusionWord(0.5), "cloudy", "");
  eq(occlusionWord(0.9), "socked in", "");
  eq(occlusionWord(null), "no data", "");
  // The picture and the legend must break at the same values, or the dome
  // shows one thing and the words say another.
  assert(occlusionFill(0.019) !== occlusionFill(0.021), "boundary at 0.02");
  assert(occlusionFill(0.09) !== occlusionFill(0.11), "boundary at 0.10");
  assert(occlusionFill(0.34) !== occlusionFill(0.36), "boundary at 0.35");
});

test("occlusionFill: missing data is NOT drawn as clear sky", () => {
  const gap = occlusionFill(null);
  const clear = occlusionFill(0);
  assert(gap !== clear, (
    "a cell we have no reading for must look different from one we know is "
    + "clear -- otherwise a dead feed reads as perfect conditions"));
});

test("domeExtent: the top of the dome is NOT the zenith", () => {
  const ext = domeExtent();
  const zenithUp = Math.cos((DOME_TILT_DEG * Math.PI) / 180);
  assert(ext.top > zenithUp, (
    `the dome reaches ${ext.top} above centre but the zenith only ${zenithUp}; `
    + `sizing off the zenith clips northern sky`));
  near(ext.top, 1, 1e-9, "the maximum is exactly 1, at alt (90-tilt) due north");
});

test("nothing is drawn outside the canvas that extent sizes", () => {
  // Reproduce the component's sizing and sweep the whole hemisphere. The
  // telescope marker, parked at alt 37 due north, projected to y = -6 and was
  // drawn off the top edge until domeExtent replaced a cos(tilt) guess.
  const cssW = 400, cssH = 300;
  const ext = domeExtent();
  const r = Math.min((cssW - 24) / 2, (cssH - 28) / (ext.top + ext.bottom));
  const cx = cssW / 2, cy = 14 + r * ext.top;
  for (let alt = 0; alt <= 90; alt += 2) {
    for (let az = 0; az < 360; az += 5) {
      const p = projectAltAz(alt, az, cx, cy, r);
      assert(p.y >= 0 && p.y <= cssH,
        `alt ${alt} az ${az} drew at y=${p.y.toFixed(1)}, outside 0..${cssH}`);
      assert(p.x >= 0 && p.x <= cssW, `alt ${alt} az ${az} drew at x=${p.x}`);
    }
  }
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ndomeProjection.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
