import assert from "node:assert/strict";
import { DOME_TILT_DEG, projectAltAz } from "../domeProjection";
import { TUBE_SCREEN, drawDomeScope, tubeOutline } from "../domeScope";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

// What the glyph has to say, and all it has to say (issue #67): which way the
// mount is pointing. The tripod, the mount head, the dew shield and the focuser
// were each added to make it more recognisable and each made it busier; they
// are gone, and the taper carries the direction on its own.
//
// "Looks like a telescope" is judged by rendering it and looking. That is not a
// formality here - three separate versions passed every geometry case in this
// file while being unreadable on the dome: one projected the tube to an
// invisible hairline, one foreshortened it to a stub at low southern azimuths,
// and one was a legible tangle of grey rectangles.

const R = 300, CX = 450, CY = 345;

test("the wide end points at the reticle, at every attitude", () => {
  // The whole claim, in screen space, where it lives. The glyph's bearing from
  // the dome centre is the bearing from the dome centre to the drawn cross.
  for (const yaw of [0, 90, 215]) {
    for (let az = 0; az < 360; az += 30) {
      for (const alt of [8, 45, 88]) {
        const aim = projectAltAz(alt, az, CX, CY, R, DOME_TILT_DEG, yaw);
        let dx = aim.x - CX, dy = aim.y - CY;
        const m = Math.hypot(dx, dy) || 1;
        const pts = tubeOutline({ x: CX, y: CY }, dx / m, dy / m, R * TUBE_SCREEN);
        // pts[1] and pts[2] are the two corners of the WIDE end.
        const tip = { x: (pts[1].x + pts[2].x) / 2, y: (pts[1].y + pts[2].y) / 2 };
        const toTip = Math.atan2(tip.y - CY, tip.x - CX);
        const toRet = Math.atan2(aim.y - CY, aim.x - CX);
        let d = Math.abs(toTip - toRet);
        if (d > Math.PI) d = 2 * Math.PI - d;
        assert.ok(d < 1e-6,
          `yaw ${yaw} alt ${alt} az ${az}: the glyph points `
          + `${(d * 180 / Math.PI).toFixed(2)} degrees off the reticle`);
      }
    }
  }
});

test("the wide end is the FAR end - the taper is not backwards", () => {
  // Pointing straight up the screen, the objective corners must be above the
  // eyepiece corners AND further apart. A glyph that tapers the other way is
  // still a glyph, still points along the right line, and tells the reader the
  // opposite thing.
  //
  // MUTATION: swap R_BACK and R_FRONT. Observed: the width assertion fails,
  // 14.4 px against 46.5.
  const len = R * TUBE_SCREEN;
  const pts = tubeOutline({ x: 0, y: 0 }, 0, -1, len);
  const wide = Math.hypot(pts[1].x - pts[2].x, pts[1].y - pts[2].y);
  const narrow = Math.hypot(pts[0].x - pts[3].x, pts[0].y - pts[3].y);
  assert.ok(wide > narrow * 2,
    `the objective end is ${wide.toFixed(1)} px across and the eyepiece end `
    + `${narrow.toFixed(1)} - the taper does not read as a direction`);
  const tipY = (pts[1].y + pts[2].y) / 2;
  const backY = (pts[0].y + pts[3].y) / 2;
  assert.ok(tipY < backY, "the glyph is inside out: the wide end is behind");
});

test("the glyph never foreshortens away, whatever it is aimed at", () => {
  // THE BUG A RENDER FOUND. An honestly-3D tube is orthographically
  // foreshortened, and south is straight at this camera, so a low southern
  // pointing collapsed to a stub nobody could identify. The glyph is drawn in
  // screen space at a CONSTANT length now, which is the entire reason that
  // decision exists - so it is asserted rather than left to the drawing.
  //
  // MUTATION: scale the length by the projected distance to the reticle, i.e.
  // let it foreshorten honestly again. Observed: the spread assertion fails,
  // reporting a range instead of a constant.
  const lens: number[] = [];
  for (let az = 0; az < 360; az += 15) {
    for (const alt of [5, 12, 40, 80]) {
      const aim = projectAltAz(alt, az, CX, CY, R, DOME_TILT_DEG, 0);
      let dx = aim.x - CX, dy = aim.y - CY;
      const m = Math.hypot(dx, dy) || 1;
      const pts = tubeOutline({ x: CX, y: CY }, dx / m, dy / m, R * TUBE_SCREEN);
      const tip = { x: (pts[1].x + pts[2].x) / 2, y: (pts[1].y + pts[2].y) / 2 };
      lens.push(Math.hypot(tip.x - CX, tip.y - CY));
    }
  }
  const lo = Math.min(...lens), hi = Math.max(...lens);
  assert.ok(hi - lo < 1e-6,
    `the glyph's drawn length varies between ${lo.toFixed(1)} and ${hi.toFixed(1)} px`);
  assert.ok(lo > 0.5 * R * TUBE_SCREEN,
    `the glyph is only ${lo.toFixed(1)} px long against a dome radius of ${R}`);
});

test("it turns with the dome, because the reticle does", () => {
  // There is no 3D left in the glyph, so "turns with the dome" is no longer a
  // property of a model - it is inherited from the projection that places the
  // cross. Worth an assertion anyway: it is the behaviour the issue asked for,
  // and a future version that hard-coded a bearing would pass every case above.
  const at = (yaw: number) => {
    const aim = projectAltAz(30, 60, CX, CY, R, DOME_TILT_DEG, yaw);
    return Math.atan2(aim.y - CY, aim.x - CX);
  };
  assert.ok(Math.abs(at(0) - at(90)) > 0.2,
            "a 90 degree yaw did not move the target bearing at all");
});

test("the one direction with no bearing on screen still draws", () => {
  // WHERE THAT ACTUALLY IS, which took a mutation to find out. The first
  // version of this case used the zenith, on the assumption that the top of
  // the sky is the middle of the dome. It is not: `upDot` is
  // `y*sin(t) + z*cos(t)`, so the point that lands on the dome's centre is the
  // one with `upDot == 0`, which due south is altitude == the camera tilt, 32
  // degrees. The zenith projects a full `cos(t)` ABOVE centre and has a
  // perfectly good bearing.
  //
  // So the case exercised nothing, and deleting the guard it exists for left
  // it green. It now points at the place the bearing is genuinely 0/0.
  //
  // MUTATION: drop the guard, leaving a bare `dx /= m`. Observed: 0/0 gives
  // NaN, the outline is all NaN, and the finite checks in the recording
  // context below fail.
  //
  // I also tried widening the guard to half a pixel, on the theory that the
  // projection would land NEAR the centre rather than on it and the bearing
  // would be decided by rounding noise. It does land on it exactly, so that
  // was a fix for a problem that does not exist; the assertion above is what
  // established it.
  let filled = 0;
  const ctx = {
    save: () => {}, restore: () => {}, beginPath: () => {}, closePath: () => {},
    moveTo: (x: number, y: number) => { assert.ok(Number.isFinite(x) && Number.isFinite(y)); },
    lineTo: (x: number, y: number) => { assert.ok(Number.isFinite(x) && Number.isFinite(y)); },
    fill: () => { filled++; }, stroke: () => {},
    set fillStyle(_v: string) {}, set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {}, set lineJoin(_v: string) {},
  } as unknown as CanvasRenderingContext2D;
  const centre = projectAltAz(DOME_TILT_DEG, 180, CX, CY, R, DOME_TILT_DEG, 0);
  assert.ok(Math.hypot(centre.x - CX, centre.y - CY) < 1e-9,
    `altitude ${DOME_TILT_DEG} due south is supposed to project onto the dome's `
    + `centre and lands ${Math.hypot(centre.x - CX, centre.y - CY).toFixed(3)} px `
    + `away - this case is not testing the degenerate bearing any more`);
  drawDomeScope(ctx, DOME_TILT_DEG, 180, CX, CY, R, DOME_TILT_DEG, 0);
  drawDomeScope(ctx, 90, 0, CX, CY, R, DOME_TILT_DEG, 0);
  assert.equal(filled, 2, "the glyph did not draw at the degenerate bearing");

  // ...and it points the DEFINED way, not wherever the noise fell.
  const pts = tubeOutline({ x: CX, y: CY }, 0, -1, R * TUBE_SCREEN);
  const want = { x: (pts[1].x + pts[2].x) / 2, y: (pts[1].y + pts[2].y) / 2 };
  let seen: { x: number; y: number } | null = null;
  const probe = {
    save: () => {}, restore: () => {}, beginPath: () => {}, closePath: () => {},
    moveTo: () => {},
    lineTo: (x: number, y: number) => { seen = seen ?? { x, y }; },
    fill: () => {}, stroke: () => {},
    set fillStyle(_v: string) {}, set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {}, set lineJoin(_v: string) {},
  } as unknown as CanvasRenderingContext2D;
  drawDomeScope(probe, DOME_TILT_DEG, 180, CX, CY, R, DOME_TILT_DEG, 0);
  const first: { x: number; y: number } = seen!;
  assert.ok(Math.hypot(first.x - pts[1].x, first.y - pts[1].y) < 1e-6,
    `at the degenerate bearing the glyph pointed somewhere arbitrary: `
    + `${first.x.toFixed(2)},${first.y.toFixed(2)} against the defined `
    + `${pts[1].x.toFixed(2)},${pts[1].y.toFixed(2)} (objective at `
    + `${want.x.toFixed(2)},${want.y.toFixed(2)})`);
});

test("it draws, and leaves the canvas as it found it", () => {
  // The pointing cross is drawn immediately after this and picks up whatever
  // stroke style is current.
  const ops: string[] = [];
  const ctx = {
    save: () => ops.push("save"), restore: () => ops.push("restore"),
    beginPath: () => {}, closePath: () => {}, moveTo: () => {}, lineTo: () => {},
    fill: () => ops.push("fill"), stroke: () => ops.push("stroke"),
    set fillStyle(_v: string) {}, set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {}, set lineJoin(_v: string) {},
  } as unknown as CanvasRenderingContext2D;

  drawDomeScope(ctx, 45, 120, CX, CY, R, DOME_TILT_DEG, 0);
  assert.deepEqual(ops, ["save", "fill", "stroke", "restore"],
                   `the glyph is no longer one filled outline: ${ops.join(",")}`);
});

console.log(`domeScope.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
