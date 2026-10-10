// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// T15: the live-frame mesh of the panorama scanner (SPEC-v2 4.11, D19).
//
// Mutant this file must catch (SPEC-v2 7.2): a single row of cells, `rows` defaulting to 1 instead of 8. The first
// test below measures the mesh against the exact projection at 36 lattice points per triangle, the triangle edges
// included (barycentric step 1/7, where the drawn error peaks: 0.2088 at pitch 23), and fails on the 0.21
// ribbon-degree bound (1.71 degrees with one row). It runs first, and on the default options, so the mutant is
// reported on that line and not on a count.
//
// The altitude cap (ruling S23) is graded by sweeping it: the cap test puts the cap between every two consecutive
// vertex altitudes, with the camera rolled by +-20 degrees so that no two vertices share an altitude, and compares
// the mesh with the triangles whose three vertices are all under the cap. Its tally shows that each of the three
// vertex positions of a triangle is, in some swept case, the only vertex over the cap. Mutants it was checked
// against: the cap test missing the first vertex of a triangle (the one the earlier test, with three fixed caps and
// no roll, let live), the second, and the third.
//
// Mutants the other tests were checked against: the altitude cap ignored, the azimuth not wrapped in `ribbonPoint`,
// the over-the-pole guard removed, `b` and `c` swapped in `affineFromTriangle`, `srcW` and `srcH` ignored, and the
// vertical limit 1.0 instead of 0.9.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { lookBasis, rotateBasis, skyAngles, type CameraBasis, type V3 } from '../../photosphereGeometry';
import { affineFromTriangle, liveFrameMesh, ribbonPoint } from '../liveFrame';
import { intrinsicsAt, quatFromBasis } from '../rotation';
import type { Intrinsics, LiveTriangle, Quat, RibbonView } from '../types';

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

const near = (a: number, b: number, tol: number, what = '') => assert.ok(Math.abs(a - b) < tol, `${what} ${a} != ${b} (tol ${tol})`);

/** mulberry32: a seeded generator, so every random case below is the same case every run. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The S25-like lens of the default scan: 180 x 320 analysis frame, 41.14 degree short axis, so the 90 x 160 live
// source is level 1 at a short-axis focal of 0.5 / tan(20.57 degrees) = 1.3326 widths.
const FNORM = 0.5 / Math.tan(41.14 * Math.PI / 360);
const K1: Intrinsics = intrinsicsAt(1, 180, 320, FNORM);
const RIBBON_PX = 390;   // 150 degrees over 390 CSS px is 2.6 px/deg; altitude 60 down to -10 is 182 px
const viewFor = (centreAz: number): RibbonView => ({ centreAz, altTop: 60, altBottom: -10, pxPerDeg: RIBBON_PX / 150, widthPx: RIBBON_PX });
const altOfY = (view: RibbonView, y: number) => view.altTop - y / view.pxPerDeg;

/** A camera looking at azimuth `az` and altitude `pitch`, rolled `roll` degrees about its axis: the basis, and the
 *  pose that `quatFromBasis` makes of it. The tests below project through the basis with plain vector algebra, so
 *  they do not share a code path with `unprojectPixel` and `qrotate`. */
function cameraAt(az: number, pitch: number, roll = 0): { basis: CameraBasis; pose: Quat } {
  const basis = roll === 0 ? lookBasis(az, pitch) : rotateBasis(lookBasis(az, pitch), 0, 0, roll);
  return { basis, pose: quatFromBasis(basis) };
}

/** Difference of two azimuths taken the short way round, in [-180, 180). */
const shortWay = (deg: number) => (((deg % 360) + 540) % 360) - 180;

/** The exact ribbon pixel of a source pixel of `k`: the ray is forward + tx right + ty up (a tangent plane at unit
 *  distance), then azimuth and altitude, then the ribbon. */
function exactRibbon(basis: CameraBasis, k: Intrinsics, view: RibbonView, x: number, y: number): [number, number] {
  const tx = (x - k.cx) / k.f, ty = -(y - k.cy) / k.f;
  const r = [0, 1, 2].map(n => basis.forward[n] + tx * basis.right[n] + ty * basis.up[n]);
  const norm = Math.hypot(r[0], r[1], r[2]);
  const { az, alt } = skyAngles([r[0] / norm, r[1] / norm, r[2] / norm] as V3);
  return [view.widthPx / 2 + shortWay(az - view.centreAz) * view.pxPerDeg, (view.altTop - alt) * view.pxPerDeg];
}

/** The 36 lattice points of a triangle cut into seven, its edges and vertices included: barycentric
 *  (i, j, 7 - i - j) / 7 with i, j >= 0. The drawn error peaks on the edges, which a strictly interior lattice (the 36
 *  points of a triangle cut into ten, all three weights >= 1 / 10) never reaches: it reads 0.1926 where this reads 0.2088. */
const LATTICE: [number, number, number][] = [];
for (let i = 0; i <= 7; i++) for (let j = 0; i + j <= 7; j++) LATTICE.push([i / 7, j / 7, (7 - i - j) / 7]);

const apply = (m: readonly number[], x: number, y: number): [number, number] => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];

/** Worst departure, in ribbon degrees, of the drawn mesh from the exact projection over the lattice points of every
 *  triangle. "Drawn" is the affine transform the canvas would be given, applied to the point's source pixel. */
function meshError(tris: LiveTriangle[], cam: { basis: CameraBasis }, k: Intrinsics, view: RibbonView): number {
  let worst = 0;
  for (const t of tris) {
    const m = affineFromTriangle(t);
    for (const [wa, wb, wc] of LATTICE) {
      const x = wa * t.src[0] + wb * t.src[2] + wc * t.src[4], y = wa * t.src[1] + wb * t.src[3] + wc * t.src[5];
      const [dx, dy] = apply(m, x, y), [ex, ey] = exactRibbon(cam.basis, k, view, x, y);
      worst = Math.max(worst, Math.hypot(dx - ex, dy - ey) / view.pxPerDeg);
    }
  }
  return worst;
}

// ---- The proof row ---------------------------------------------------------

test('the 4 x 8 mesh matches the exact projection within 0.21 ribbon degrees at 36 lattice points per triangle, edges included (pitch 23)', () => {
  assert.equal(LATTICE.length, 36);
  let worst = 0, checked = 0;
  // The mesh does not depend on the azimuth; four headings, one of them across the 359/0 seam.
  for (const az of [0, 90.5, 200, 355]) {
    const cam = cameraAt(az, 23), view = viewFor(az);
    const tris = liveFrameMesh(cam.pose, K1, 90, 160, view);
    assert.ok(tris.length > 0, `no triangles at azimuth ${az}`);
    worst = Math.max(worst, meshError(tris, cam, K1, view));
    checked += tris.length * LATTICE.length;
  }
  assert.ok(worst < 0.21, `worst departure ${worst.toFixed(4)} ribbon degrees, not under 0.21`);
  // The lattice must reach the edges, where the peak is: a lattice that misses them reads 0.1926 and would pass the bound too easily.
  assert.ok(worst > 0.2, `worst departure ${worst.toFixed(4)} ribbon degrees: the lattice does not reach the peak of 0.2088`);
  assert.equal(checked, 4 * 64 * 36);
});

test('the bound can fail: one row of cells departs by more than 1 degree, and one column by more than 0.21', () => {
  const cam = cameraAt(0, 23), view = viewFor(0);
  const oneRow = meshError(liveFrameMesh(cam.pose, K1, 90, 160, view, { rows: 1 }), cam, K1, view);
  assert.ok(oneRow > 1, `one row departs by ${oneRow.toFixed(4)}`);
  const oneCol = meshError(liveFrameMesh(cam.pose, K1, 90, 160, view, { cols: 1 }), cam, K1, view);
  assert.ok(oneCol > 0.21, `one column departs by ${oneCol.toFixed(4)}`);
});

// ---- The grid --------------------------------------------------------------

test('the default grid is 4 x 8 cells of two triangles, covering +-10 degrees by 0.9 of the half-height of the source', () => {
  const tris = liveFrameMesh(cameraAt(0, 23).pose, K1, 90, 160, viewFor(0));
  assert.equal(tris.length, 64);
  const xs = tris.flatMap(t => [t.src[0], t.src[2], t.src[4]]), ys = tris.flatMap(t => [t.src[1], t.src[3], t.src[5]]);
  const halfX = K1.f * Math.tan(10 * Math.PI / 180);
  near(Math.min(...xs), 45 - halfX, 1e-9, 'left');
  near(Math.max(...xs), 45 + halfX, 1e-9, 'right');
  near(Math.min(...ys), 80 - 0.9 * 80, 1e-9, 'top');
  near(Math.max(...ys), 80 + 0.9 * 80, 1e-9, 'bottom');
  // 5 x 9 distinct vertices, on a regular lattice in the source.
  const distinct = (v: number[]) => [...new Set(v.map(n => n.toFixed(9)))];
  assert.equal(distinct(xs).length, 5);
  assert.equal(distinct(ys).length, 9);
  // Every cell is cut along the same diagonal into two triangles that tile it: summed source area is the rectangle.
  const area = tris.reduce((s, t) => s + Math.abs((t.src[2] - t.src[0]) * (t.src[5] - t.src[1]) - (t.src[4] - t.src[0]) * (t.src[3] - t.src[1])) / 2, 0);
  near(area, 2 * halfX * 2 * 72, 1e-6, 'area');
});

test('cols, rows and halfWidthDeg size the grid; a grid that is not a positive integer count is refused', () => {
  const pose = cameraAt(0, 23).pose, view = viewFor(0);
  assert.equal(liveFrameMesh(pose, K1, 90, 160, view, { cols: 2, rows: 3 }).length, 12);
  assert.equal(liveFrameMesh(pose, K1, 90, 160, view, { cols: 6, rows: 16 }).length, 192);
  const narrow = liveFrameMesh(pose, K1, 90, 160, view, { halfWidthDeg: 5 });
  const xs = narrow.flatMap(t => [t.src[0], t.src[2], t.src[4]]);
  near(Math.max(...xs) - Math.min(...xs), 2 * K1.f * Math.tan(5 * Math.PI / 180), 1e-9, 'width of a 5 degree half-width');
  for (const bad of [{ cols: 0 }, { rows: 0 }, { cols: 2.5 }, { rows: -1 }, { rows: NaN }]) {
    assert.throws(() => liveFrameMesh(pose, K1, 90, 160, view, bad), RangeError, JSON.stringify(bad));
  }
});

test('source vertices are in canvas pixels: srcW and srcH scale the intrinsics pixels and leave the ribbon alone', () => {
  const cam = cameraAt(30, 23), view = viewFor(30);
  const a = liveFrameMesh(cam.pose, K1, 90, 160, view), b = liveFrameMesh(cam.pose, K1, 180, 320, view);
  const c = liveFrameMesh(cam.pose, K1, 45, 160, view);
  assert.equal(a.length, b.length);
  a.forEach((t, i) => {
    t.src.forEach((v, n) => near(b[i].src[n], 2 * v, 1e-9, `2x src ${i}.${n}`));
    t.dst.forEach((v, n) => near(b[i].dst[n], v, 1e-12, `dst ${i}.${n}`));
    near(c[i].src[0], t.src[0] / 2, 1e-9, 'x halved');
    near(c[i].src[1], t.src[1], 1e-9, 'y unchanged');
  });
});

// ---- Vertices and the altitude cap -----------------------------------------

test('every vertex is projected exactly through the live pose, with roll and at any azimuth', () => {
  const random = rng(15);
  for (let n = 0; n < 12; n++) {
    const az = random() * 360, pitch = random() * 80 - 10, roll = (random() - 0.5) * 60;
    const cam = cameraAt(az, pitch, roll), view = viewFor(random() < 0.5 ? az : random() * 360);
    const tris = liveFrameMesh(cam.pose, K1, 90, 160, view, { altCapDeg: 80 });
    for (const t of tris) for (let v = 0; v < 3; v++) {
      const [ex, ey] = exactRibbon(cam.basis, K1, view, t.src[2 * v], t.src[2 * v + 1]);
      // A triangle at the far side of the ribbon seam is moved by 360 degrees; compare the short way round.
      near(shortWay((t.dst[2 * v] - ex) / view.pxPerDeg), 0, 1e-9, `x at az ${az.toFixed(1)} pitch ${pitch.toFixed(1)} roll ${roll.toFixed(1)}`);
      near(t.dst[2 * v + 1], ey, 1e-9, 'y');
    }
  }
});

test('no vertex is above altCapDeg: a triangle with any vertex over the cap is dropped, and only such a triangle', () => {
  const cam = cameraAt(0, 23), view = viewFor(0);
  const all = liveFrameMesh(cam.pose, K1, 90, 160, view);
  const maxAlt = (t: LiveTriangle) => Math.max(altOfY(view, t.dst[1]), altOfY(view, t.dst[3]), altOfY(view, t.dst[5]));
  assert.ok(all.every(t => maxAlt(t) < 90), 'the default cap of 90 drops nothing at pitch 23');
  for (const cap of [30, 40, 50]) {
    const capped = liveFrameMesh(cam.pose, K1, 90, 160, view, { altCapDeg: cap });
    const expected = all.filter(t => maxAlt(t) <= cap);
    assert.ok(expected.length > 0 && expected.length < all.length, `cap ${cap} keeps ${expected.length} of ${all.length}`);
    assert.deepEqual(capped, expected, `cap ${cap}`);
    for (const t of capped) assert.ok(maxAlt(t) <= cap + 1e-9, `a vertex of a kept triangle is at ${maxAlt(t)} over the cap ${cap}`);
  }
  assert.deepEqual(liveFrameMesh(cam.pose, K1, 90, 160, view, { altCapDeg: -20 }), [], 'a cap under the whole frame leaves nothing');
});

/** The altitude (degrees) of the three vertices of a triangle, from the exact projection of each source pixel and not
 *  from the ribbon pixels the mesh computed. */
const vertexAltitudes = (t: LiveTriangle, cam: { basis: CameraBasis }, view: RibbonView): number[] =>
  [0, 1, 2].map(v => altOfY(view, exactRibbon(cam.basis, K1, view, t.src[2 * v], t.src[2 * v + 1])[1]));

test('a cap between any two consecutive vertex altitudes keeps exactly the triangles whose three vertices are under it (pitch 23, roll 0 and +-20)', () => {
  // How many swept caps left vertex 0, 1 or 2 of some triangle as the only one over it, per vertex position.
  const sole = [0, 0, 0];
  for (const roll of [0, 20, -20]) {
    const cam = cameraAt(40, 23, roll), view = viewFor(40);
    const all = liveFrameMesh(cam.pose, K1, 90, 160, view);
    assert.equal(all.length, 64, `roll ${roll}: the default cap of 90 drops nothing`);
    const alts = all.map(t => vertexAltitudes(t, cam, view));
    const levels = [...new Set(alts.flat().map(a => a.toFixed(9)))].map(Number).sort((p, q) => p - q);
    // At roll 0 the frame mirrors about its vertical axis and its vertices pair up; a roll gives all 45 their own altitude.
    if (roll === 0) assert.ok(levels.length < 45, `roll 0: ${levels.length} distinct altitudes`);
    else assert.equal(levels.length, 45, `roll ${roll}: ${levels.length} distinct altitudes`);
    for (let n = 0; n + 1 < levels.length; n++) {
      const cap = (levels[n] + levels[n + 1]) / 2;
      const expected = all.filter((_, i) => Math.max(...alts[i]) <= cap);
      const capped = liveFrameMesh(cam.pose, K1, 90, 160, view, { altCapDeg: cap });
      const where = `roll ${roll}, cap ${cap.toFixed(4)} between ${levels[n].toFixed(4)} and ${levels[n + 1].toFixed(4)}`;
      assert.equal(capped.length, expected.length, `${where}: ${capped.length} triangles kept, ${expected.length} expected`);
      assert.deepEqual(capped, expected, where);
      if (roll === 0) continue;
      const over = alts.map(a => a.map(v => v > cap));
      for (let v = 0; v < 3; v++) if (over.some(o => o[v] && !o[(v + 1) % 3] && !o[(v + 2) % 3])) sole[v]++;
    }
  }
  assert.ok(sole.every(c => c > 0), `each vertex position must be the only one over the cap in some swept case: ${sole.join(', ')}`);
});

test('a triangle over the pole is dropped: no triangle is wider than 180 ribbon degrees', () => {
  const view = viewFor(0), limit = 180 * view.pxPerDeg;
  for (const pitch of [62, 70, 80, 90]) {
    const cam = cameraAt(0, pitch), tris = liveFrameMesh(cam.pose, K1, 90, 160, view);
    assert.ok(tris.length < 64, `pitch ${pitch}: ${tris.length} triangles, the pole triangles are still there`);
    for (const t of tris) {
      const xs = [t.dst[0], t.dst[2], t.dst[4]];
      assert.ok(Math.max(...xs) - Math.min(...xs) <= limit, `pitch ${pitch}: a triangle spans ${(Math.max(...xs) - Math.min(...xs)) / view.pxPerDeg} degrees`);
      assert.ok(t.src.every(Number.isFinite) && t.dst.every(Number.isFinite), 'finite');
    }
  }
});

// ---- ribbonPoint -----------------------------------------------------------

test('ribbonPoint puts the view centre at half the width, altTop at row 0, and wraps the azimuth the short way', () => {
  const view = viewFor(0);
  assert.deepEqual(ribbonPoint(view, 0, 60), [195, 0]);
  const [x, y] = ribbonPoint(view, 10, 0);
  near(x, 195 + 26, 1e-9); near(y, 156, 1e-9);
  near(ribbonPoint(view, 0, -10)[1], 182, 1e-9, 'altBottom is the ribbon height');
  // A view centred on 350 shows azimuth 10 twenty degrees to the right, and a view centred on 10 shows 350 to the left.
  near(ribbonPoint(viewFor(350), 10, 60)[0], 195 + 20 * 2.6, 1e-9, '10 from 350');
  near(ribbonPoint(viewFor(10), 350, 60)[0], 195 - 20 * 2.6, 1e-9, '350 from 10');
  // Equivalent azimuths land on one pixel, and the far side of the circle is 180 away from the centre.
  near(ribbonPoint(view, -170, 0)[0], ribbonPoint(view, 190, 0)[0], 1e-9, '-170 is 190');
  near(ribbonPoint(view, 725, 0)[0], ribbonPoint(view, 5, 0)[0], 1e-9, '725 is 5');
  near(Math.abs(ribbonPoint(view, 180, 0)[0] - 195), 180 * 2.6, 1e-9, 'the antipode');
  // A different scale and a different top.
  const zoomed: RibbonView = { centreAz: 100, altTop: 30, altBottom: 0, pxPerDeg: 4, widthPx: 200 };
  assert.deepEqual(ribbonPoint(zoomed, 105, 12.5), [120, 70]);
});

// ---- affineFromTriangle ----------------------------------------------------

test('affineFromTriangle returns setTransform(a, b, c, d, e, f) in canvas order', () => {
  const t: LiveTriangle = { src: [0, 0, 1, 0, 0, 1], dst: [10, 20, 13, 21, 8, 25] };
  // x' = a x + c y + e and y' = b x + d y + f, so the image of (1, 0) is (a + e, b + f) and of (0, 1) is (c + e, d + f).
  assert.deepEqual(affineFromTriangle(t), [3, 1, -2, 5, 10, 20]);
  const scaled: LiveTriangle = { src: [2, 3, 6, 3, 2, 11], dst: [100, 50, 108, 50, 100, 66] };
  const m = affineFromTriangle(scaled);
  for (let i = 0; i < 6; i++) near(m[i], [2, 0, 0, 2, 96, 44][i], 1e-12, `scale ${i}`);
});

test('affineFromTriangle maps the source vertices onto the destination vertices within 1e-9', () => {
  const random = rng(7);
  let tried = 0;
  while (tried < 200) {
    const src = Array.from({ length: 6 }, () => random() * 160) as LiveTriangle['src'];
    const dst = Array.from({ length: 6 }, () => random() * 1000 - 300) as LiveTriangle['dst'];
    const det = (src[2] - src[0]) * (src[5] - src[1]) - (src[4] - src[0]) * (src[3] - src[1]);
    if (Math.abs(det) < 50) continue;
    tried++;
    const m = affineFromTriangle({ src, dst });
    for (let v = 0; v < 3; v++) {
      const [x, y] = apply(m, src[2 * v], src[2 * v + 1]);
      near(x, dst[2 * v], 1e-9, `x of vertex ${v}`); near(y, dst[2 * v + 1], 1e-9, `y of vertex ${v}`);
    }
  }
  // And the triangles of a real mesh, which is what the overlay canvas is given.
  const cam = cameraAt(222, 23, 4), view = viewFor(222);
  for (const t of liveFrameMesh(cam.pose, K1, 90, 160, view)) {
    const m = affineFromTriangle(t);
    for (let v = 0; v < 3; v++) {
      const [x, y] = apply(m, t.src[2 * v], t.src[2 * v + 1]);
      near(x, t.dst[2 * v], 1e-9, 'mesh x'); near(y, t.dst[2 * v + 1], 1e-9, 'mesh y');
    }
  }
});

test('affineFromTriangle of a source triangle with no area is the zero matrix at the first vertex, not NaN', () => {
  const m = affineFromTriangle({ src: [1, 1, 2, 2, 3, 3], dst: [7, 9, 1, 1, 5, 5] });
  assert.deepEqual(m, [0, 0, 0, 0, 7, 9]);
});

// ---- Declarations ----------------------------------------------------------

test('liveFrame.ts imports only rotation and the shared types, types by import type, and does not log', () => {
  const source = readFileSync(new URL('../liveFrame.ts', import.meta.url), 'utf8');
  // Split on either line ending: a checkout with core.autocrlf=true leaves a \r on every line, and the anchored
  // `;$` patterns below would then match nothing. This is a check on what the file imports, not on its line endings.
  const imports = source.split(/\r?\n/).filter(l => /^import\b/.test(l));
  assert.equal(imports.length, 2, imports.join(' | '));
  assert.ok(imports.some(l => /^import \{[^}]*\} from '\.\/rotation';$/.test(l)));
  assert.ok(imports.some(l => /^import type \{[^}]*\} from '\.\/types';$/.test(l)));
  assert.ok(!/console\./.test(source), 'no logging in a module that holds a pose');
  assert.notEqual(source.charCodeAt(0), 0xfeff, 'no BOM');
});

console.log(`liveFrame.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
