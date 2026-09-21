import assert from "node:assert/strict";
import { DOME_TILT_DEG, projectAltAz, projectDome } from "../domeProjection";
import {
  SCOPE_SCALE, TUBE_SCREEN, cameraDir, drawDomeScope, tripodLegs, tubeOutline,
} from "../domeScope";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

// The claims in issue #67 that a test can hold: the tripod is planted north, it
// turns with the dome, and the tube points at the reticle. "3d" and "beautiful"
// are judged by looking at the render, and are not asserted here - which is not
// a formality. The first version passed every geometry case in this file while
// projecting the tube to an invisible hairline, and the second foreshortened it
// to an unreadable stub at low southern azimuths. Both were found by rendering
// it and looking at it.

const R = 300, CX = 450, CY = 345;

/** Project a tripod-space point exactly as `drawDomeScope` does. */
const project = (v: { x: number; y: number; z: number }, yaw = 0) => {
  const k = SCOPE_SCALE;
  return projectDome({ x: v.x * k, y: v.y * k, z: v.z * k },
                     CX, CY, R, DOME_TILT_DEG, yaw);
};

/** The CENTRE of the first leg's foot. A leg quad is two points at the foot and
 *  two at the hub, each pair offset by the leg's half-width, so a single
 *  lowest-z point is one EDGE of the leg and sits off the meridian. */
const northFoot = (yaw = 0) => {
  const { legs } = tripodLegs(cameraDir(DOME_TILT_DEG, yaw));
  const pts = [...legs[0].points].sort((a, b) => a.z - b.z).slice(0, 2);
  return { x: (pts[0].x + pts[1].x) / 2,
           y: (pts[0].y + pts[1].y) / 2,
           z: (pts[0].z + pts[1].z) / 2 };
};

/** The mount head, projected - where the tube is hung. */
const hubAt = (yaw = 0) =>
  project(tripodLegs(cameraDir(DOME_TILT_DEG, yaw)).hub, yaw);

test("the tripod's front leg is planted due north, not at a chosen angle", () => {
  const legN = project(northFoot());
  const skyN = projectAltAz(0, 0, CX, CY, R, DOME_TILT_DEG, 0);
  // North is the FAR side of this camera, so both sit above the horizon centre.
  assert.ok(legN.y < CY, `the north leg projected below centre: ${legN.y}`);
  assert.ok(skyN.y < CY, "the dome's own north is not above centre - the camera "
                       + "changed and this case is now meaningless");
  assert.ok(Math.abs(legN.x - CX) < 1e-6,
            `the north leg is off the meridian by ${legN.x - CX} px`);
});

test("the tripod turns with the dome", () => {
  const turned = project(northFoot(90), 90);
  const still = project(northFoot(0), 0);
  assert.ok(Math.abs(turned.x - still.x) > 10,
            `a 90 degree yaw moved the tripod by ${Math.abs(turned.x - still.x)} px`);
  // ...and the same way the SKY's north went, which is the statement that
  // matters: one camera, not two.
  const skyNturned = projectAltAz(0, 0, CX, CY, R, DOME_TILT_DEG, 90);
  assert.ok(Math.sign(turned.x - CX) === Math.sign(skyNturned.x - CX),
            "the tripod and the sky disagree about which way north went");
});

test("the tube points at the reticle, at every attitude", () => {
  // In SCREEN space, which is where the claim now lives: the tube's bearing
  // from the mount head is the bearing from the mount head to the drawn cross.
  for (const yaw of [0, 90, 215]) {
    for (let az = 0; az < 360; az += 30) {
      for (const alt of [8, 45, 88]) {
        const h = hubAt(yaw);
        const aim = projectAltAz(alt, az, CX, CY, R, DOME_TILT_DEG, yaw);
        let dx = aim.x - h.x, dy = aim.y - h.y;
        const m = Math.hypot(dx, dy) || 1;
        dx /= m; dy /= m;

        const [body] = tubeOutline({ x: h.x, y: h.y }, dx, dy, R * TUBE_SCREEN);
        const tip = { x: (body[1].x + body[2].x) / 2,
                      y: (body[1].y + body[2].y) / 2 };
        const toTip = Math.atan2(tip.y - h.y, tip.x - h.x);
        const toRet = Math.atan2(aim.y - h.y, aim.x - h.x);
        let d = Math.abs(toTip - toRet);
        if (d > Math.PI) d = 2 * Math.PI - d;
        assert.ok(d < 1e-6,
          `yaw ${yaw} alt ${alt} az ${az}: the tube points `
          + `${(d * 180 / Math.PI).toFixed(2)} degrees off the reticle`);
      }
    }
  }
});

test("the tube never foreshortens away, whatever it is aimed at", () => {
  // THE BUG THE SECOND RENDER FOUND. An honestly-3D tube is orthographically
  // foreshortened, and south is straight at this camera, so a low southern
  // pointing collapsed to a stub nobody could identify. The tube is drawn in
  // screen space at a CONSTANT length now, which is the entire reason that
  // decision exists - so it is asserted here rather than left to the drawing.
  //
  // MUTATION: scale the length by the projected distance to the reticle, i.e.
  // let it foreshorten honestly again. Observed: the spread assertion fails,
  // reporting a range instead of a constant.
  const lens: number[] = [];
  for (let az = 0; az < 360; az += 15) {
    for (const alt of [5, 12, 40, 80]) {
      const h = hubAt(0);
      const aim = projectAltAz(alt, az, CX, CY, R, DOME_TILT_DEG, 0);
      let dx = aim.x - h.x, dy = aim.y - h.y;
      const m = Math.hypot(dx, dy) || 1;
      const [body] = tubeOutline({ x: h.x, y: h.y }, dx / m, dy / m,
                                 R * TUBE_SCREEN);
      const tip = { x: (body[1].x + body[2].x) / 2,
                    y: (body[1].y + body[2].y) / 2 };
      lens.push(Math.hypot(tip.x - h.x, tip.y - h.y));
    }
  }
  const lo = Math.min(...lens), hi = Math.max(...lens);
  assert.ok(hi - lo < 1e-6,
    `the tube's drawn length varies between ${lo.toFixed(1)} and ${hi.toFixed(1)} px`);
  assert.ok(lo > 0.5 * R * TUBE_SCREEN,
    `the tube is only ${lo.toFixed(1)} px long against a dome radius of ${R}`);
});

test("the dew shield is at the sky end and the focuser at the other", () => {
  // Which end is which is the difference between "a telescope" and "a stick",
  // and it is the pair of cues that made the reworked version readable.
  // Pointing straight up the screen: the shield sits beyond the body's far
  // edge, the focuser behind it.
  const len = R * TUBE_SCREEN;
  const [body, shield, focuser] = tubeOutline({ x: 0, y: 0 }, 0, -1, len);
  const tipY = (body[1].y + body[2].y) / 2;
  const backY = (body[0].y + body[3].y) / 2;
  // CENTROIDS, not "some corner is past the tip". A first version of this
  // asserted the minimum y of each piece, and a mutation that moved half the
  // shield to the back of the tube sailed through it - one remaining corner
  // beyond the objective was enough. Where the piece sits ON AVERAGE is the
  // claim being made.
  const midY = (pts: { y: number }[]) =>
    pts.reduce((t, p) => t + p.y, 0) / pts.length;
  assert.ok(midY(shield) < tipY,
            `the dew shield sits at y ${midY(shield).toFixed(1)}, not beyond the `
            + `objective at ${tipY.toFixed(1)}`);
  assert.ok(midY(focuser) > tipY,
            "the focuser is not behind the objective");
  assert.ok(backY > tipY, "the tube is inside out");
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
  assert.ok(ops.filter(o => o === "fill").length >= 7,
            `only ${ops.filter(o => o === "fill").length} pieces were painted`);
  assert.equal(ops[0], "save", "the model did not save the canvas state");
  assert.equal(ops[ops.length - 1], "restore",
               "the model left its own stroke style on the context");
});

console.log(`domeScope.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
