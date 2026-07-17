// Unit tests for lib/framing.ts — the client mirror of the server mosaic engine
// (design spec §5). Covers the four correctness landmines from the spec:
//   1. M31 (ra 0.71h) 3×1 mosaic — every emitted RA stays in [0,24) (the %24
//      wrap; an unwrapped RA 422s the whole plan — critique C2-#2).
//   2. center-drag ρ→0 — deproject(0,0,...) returns the center, never NaN
//      (the "open on target, hit Send" path — critique C1-A3).
//   3. high-dec (>80°) deproject stays finite.
//   4. fovFromOptics sanity (bin-1 FOV + pixel scale).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as foundation.test.ts /
// eta.test.ts. They compile under `tsc -b` and run directly with a TS-aware
// runner, e.g.  npx tsx src/lib/__tests__/framing.test.ts
// Each `test(...)` maps 1:1 to an `it(...)` if a real runner lands later.

import {
  project,
  deproject,
  mosaicGrid,
  mosaicTotalFov,
  fovFromOptics,
  plausibilityHint,
  wrapRaHours,
  gridHalfHeightPx,
  RHO_EPS,
  type MosaicGridSpec,
} from "../framing";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}
function isFiniteNum(x: number, msg = ""): void {
  if (!Number.isFinite(x)) throw new Error(`${msg} expected finite, got ${x}`);
}

// ----------------------------------------------------------------- fixtures
// M31 — the canonical wrap landmine: RA 0.7123h is close to the 0/24 seam, so a
// 3-wide mosaic deprojects panels that can fall below 0 or above 24 unless wrapped.
const M31_RA = 0.7123;
const M31_DEC = 41.269;

// A representative APS-C-ish rig at 0.5 m focal length.
const OPTICS = {
  focal_length_mm: 500,
  pixel_size_um: 3.76,
  sensor_width_px: 6248,
  sensor_height_px: 4176,
};

// ================================================================ tests
// (1) M31 3×1 mosaic — every panel RA wrapped into [0,24).
test("M31 3x1 mosaic RA stays in [0,24)", () => {
  // Force a wide single-frame FOV so the 3 columns straddle the 0h seam.
  const spec: MosaicGridSpec = {
    ra_hours: M31_RA,
    dec_deg: M31_DEC,
    rows: 1,
    cols: 3,
    overlap: 0.1,
    rotation_deg: 0,
    fov_x_deg: 14, // deliberately huge so a column offset crosses the 0h seam
    fov_y_deg: 9,
  };
  const panels = mosaicGrid(spec);
  assert(panels.length === 3, `expected 3 panels, got ${panels.length}`);
  for (const p of panels) {
    assert(p.ra_hours >= 0 && p.ra_hours < 24, `RA out of range: ${p.ra_hours}`);
    isFiniteNum(p.ra_hours, "panel ra");
    isFiniteNum(p.dec_deg, "panel dec");
  }
  // The leftmost column (c=0) is west of M31 — at dec 41° a 14° frame puts it
  // past 0h, so its RA must have wrapped into the 23.x range, proving the %24
  // path actually fires (not just trivially in-range).
  const left = panels.find((p) => p.col === 0)!;
  assert(left.ra_hours > 20, `expected wrap near 23h, got ${left.ra_hours}`);
});

// (2) center-drag ρ→0 — deproject of the tangent point returns the center.
test("center-drag rho->0 returns center (no NaN)", () => {
  const c = deproject(0, 0, M31_RA, M31_DEC);
  isFiniteNum(c.ra_hours, "ra");
  isFiniteNum(c.dec_deg, "dec");
  near(c.ra_hours, M31_RA, 1e-9, "ra==center");
  near(c.dec_deg, M31_DEC, 1e-9, "dec==center");
  // Sub-epsilon offset still takes the guard (no divide-by-ρ).
  const sub = deproject(RHO_EPS / 10, 0, 12.0, 0);
  isFiniteNum(sub.ra_hours, "sub ra");
  near(sub.ra_hours, 12.0, 1e-9, "sub ra==center");
});

// A 1×1 mosaic on-target must also return exactly the center (the Send path).
test("1x1 mosaic on target returns center coords", () => {
  const spec: MosaicGridSpec = {
    ra_hours: M31_RA,
    dec_deg: M31_DEC,
    rows: 1,
    cols: 1,
    overlap: 0.25,
    rotation_deg: 0,
    fov_x_deg: 1.2,
    fov_y_deg: 0.8,
  };
  const panels = mosaicGrid(spec);
  assert(panels.length === 1, "1x1 -> one panel");
  near(panels[0].ra_hours, M31_RA, 1e-9, "1x1 ra");
  near(panels[0].dec_deg, M31_DEC, 1e-9, "1x1 dec");
});

// (3) high-dec (>80°) deproject stays finite (polar mosaics).
test("dec>80 deproject is finite", () => {
  const ra0 = 2.53; // near Polaris
  const dec0 = 85.0;
  for (const xi of [-2, -1, 0, 1, 2]) {
    for (const eta of [-2, -1, 0, 1, 2]) {
      const s = deproject(xi, eta, ra0, dec0);
      isFiniteNum(s.ra_hours, `ra at (${xi},${eta})`);
      isFiniteNum(s.dec_deg, `dec at (${xi},${eta})`);
      assert(s.ra_hours >= 0 && s.ra_hours < 24, `ra range at (${xi},${eta}): ${s.ra_hours}`);
      assert(s.dec_deg <= 90 && s.dec_deg >= -90, `dec range at (${xi},${eta}): ${s.dec_deg}`);
    }
  }
  // A genuine high-dec 2×2 grid produces 4 finite, in-range panels.
  const spec: MosaicGridSpec = {
    ra_hours: ra0,
    dec_deg: dec0,
    rows: 2,
    cols: 2,
    overlap: 0.2,
    rotation_deg: 17,
    fov_x_deg: 1.5,
    fov_y_deg: 1.0,
  };
  const panels = mosaicGrid(spec);
  assert(panels.length === 4, "2x2 -> 4 panels");
  for (const p of panels) {
    isFiniteNum(p.ra_hours, "hi-dec panel ra");
    isFiniteNum(p.dec_deg, "hi-dec panel dec");
    assert(p.ra_hours >= 0 && p.ra_hours < 24, `hi-dec ra range: ${p.ra_hours}`);
  }
});

// project/deproject round-trip at a benign location is identity.
test("project/deproject round-trips", () => {
  const ra0 = 12.0;
  const dec0 = 20.0;
  const ra = 12.05;
  const dec = 20.3;
  const p = project(ra, dec, ra0, dec0);
  isFiniteNum(p.xi, "xi");
  isFiniteNum(p.eta, "eta");
  const back = deproject(p.xi, p.eta, ra0, dec0);
  near(back.ra_hours, ra, 1e-7, "round-trip ra");
  near(back.dec_deg, dec, 1e-7, "round-trip dec");
});

// (4) fovFromOptics sanity — bin-1 FOV + pixel scale for the fixture rig.
test("fovFromOptics sanity (500mm, 3.76um, APS-C)", () => {
  const f = fovFromOptics(OPTICS);
  // pixel scale = 206.265 * 3.76 / 500 ≈ 1.551 "/px
  near(f.pixel_scale_arcsec, 1.551, 0.01, "pixel scale");
  // FOV_x ≈ 6248 * 1.551 / 3600 ≈ 2.69°, FOV_y ≈ 4176 * 1.551 / 3600 ≈ 1.80°
  near(f.fov_x_deg, 2.69, 0.05, "fov_x");
  near(f.fov_y_deg, 1.80, 0.05, "fov_y");
  assert(f.fov_x_deg > f.fov_y_deg, "landscape: fov_x > fov_y");
});

// fovFromOptics degrades to zeros (not NaN) on bad/missing optics.
test("fovFromOptics returns zeros on bad optics", () => {
  const z = fovFromOptics(null);
  assert(z.fov_x_deg === 0 && z.pixel_scale_arcsec === 0, "null optics -> zeros");
  const bad = fovFromOptics({ ...OPTICS, focal_length_mm: 0 });
  assert(bad.fov_x_deg === 0, "zero focal -> zero fov");
  // focalMm override wins over the optics value.
  const over = fovFromOptics({ ...OPTICS, focal_length_mm: 0 }, 500);
  near(over.pixel_scale_arcsec, 1.551, 0.01, "override focal applies");
});

// plausibilityHint flags a focal-length typo (800 vs 80).
test("plausibilityHint flags wide/narrow scales", () => {
  // 80mm by mistake -> ~9.7"/px -> very wide field
  const wide = fovFromOptics({ ...OPTICS, focal_length_mm: 80 });
  assert(plausibilityHint(wide.pixel_scale_arcsec) === "very wide field", "wide hint");
  // 2000mm -> ~0.39"/px -> very high resolution
  const narrow = fovFromOptics({ ...OPTICS, focal_length_mm: 2000 });
  assert(plausibilityHint(narrow.pixel_scale_arcsec) === "very high resolution", "narrow hint");
  // typical -> no note
  const ok = fovFromOptics(OPTICS);
  assert(plausibilityHint(ok.pixel_scale_arcsec) === null, "typical -> no hint");
});

// mosaicTotalFov is the tangent-plane extent (spec §5), not raw RA degrees.
test("mosaicTotalFov tangent-plane extent", () => {
  // 3 cols, 25% overlap, 1° frames -> (3 - 2*0.25)*1 = 2.5°
  const t = mosaicTotalFov(3, 2, 0.25, 1.0, 0.8);
  near(t.total_fov_x_deg, 2.5, 1e-9, "total_x");
  near(t.total_fov_y_deg, (2 - 1 * 0.25) * 0.8, 1e-9, "total_y");
});

// wrapRaHours folds negatives and a rounding-to-24 case into [0,24).
test("wrapRaHours normalizes into [0,24)", () => {
  near(wrapRaHours(-0.5), 23.5, 1e-12, "negative");
  near(wrapRaHours(24.5), 0.5, 1e-12, "over 24");
  assert(wrapRaHours(24) === 0, "exactly 24 -> 0");
  assert(wrapRaHours(23.9999) < 24, "just under 24 stays < 24");
});

// snake (boustrophedon) ordering — odd rows reverse.
test("mosaicGrid emits boustrophedon order", () => {
  const spec: MosaicGridSpec = {
    ra_hours: 12,
    dec_deg: 20,
    rows: 2,
    cols: 3,
    overlap: 0.25,
    rotation_deg: 0,
    fov_x_deg: 1,
    fov_y_deg: 1,
  };
  const panels = mosaicGrid(spec);
  assert(panels.length === 6, "2x3 -> 6 panels");
  // row 0: cols 0,1,2 ; row 1: cols 2,1,0
  const cols = panels.map((p) => p.col);
  assert(JSON.stringify(cols) === JSON.stringify([0, 1, 2, 2, 1, 0]), `snake order: ${cols}`);
});

// gridHalfHeightPx — a single 1x1 panel collapses to just its own half-height
// (rows=1 drops the row-span term entirely, matching a plain rectangle).
test("gridHalfHeightPx: single panel is just half its own height", () => {
  // fovYDeg=2, pxPerDeg=100 -> frameH=200, halfH=100.
  near(gridHalfHeightPx(2, 100, 1, 0.2), 100, 1e-9, "1x1 collapses to halfH");
});

// A multi-row mosaic's half-height grows with the row span (wave-2 G3: the
// rotate handle must clear the WHOLE grid, not just one panel, or a tall
// mosaic would draw its stalk through the upper panels).
test("gridHalfHeightPx: multi-row grid extends past one panel's half-height", () => {
  const oneRow = gridHalfHeightPx(2, 100, 1, 0.2);
  const threeRows = gridHalfHeightPx(2, 100, 3, 0.2);
  assert(threeRows > oneRow, `3-row grid (${threeRows}) must exceed 1-row (${oneRow})`);
  // halfH=100, stepY=200*0.8=160, +((3-1)*160)/2 = +160 -> 260 total.
  near(threeRows, 260, 1e-9, "3-row half-height");
});

// Full overlap (1.0, clamped upstream by callers to <=0.5 in practice, but the
// function itself is a pure arithmetic helper) collapses stepY to 0 — every
// row stacks on the first, so half-height stays at a single panel's.
test("gridHalfHeightPx: zero step (overlap=1) ignores row count", () => {
  near(gridHalfHeightPx(2, 100, 5, 1), 100, 1e-9, "stepY=0 -> halfH only");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nframing.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
