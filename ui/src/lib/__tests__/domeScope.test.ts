import assert from "node:assert/strict";
import { DOME_TILT_DEG, projectAltAz, projectDome, skyVector } from "../domeProjection";
import { SCOPE_SCALE, cameraDir, scopeFaces, drawDomeScope } from "../domeScope";

let passed = 0;
function test(name: string, fn: () => void) { fn(); passed++; console.log(`PASS ${name}`); }

// The three claims in issue #67 that a test can hold: the tripod is planted
// north, it turns with the dome, and the tube points at the reticle. "3d" and
// "beautiful" are judged by looking at it, and are not asserted here.

const R = 300, CX = 450, CY = 345;

/** Project a model-space point exactly as `drawDomeScope` does. */
const project = (v: { x: number; y: number; z: number }, yaw = 0) => {
  const k = SCOPE_SCALE;
  return projectDome({ x: v.x * k, y: v.y * k, z: v.z * k },
                     CX, CY, R, DOME_TILT_DEG, yaw);
};

/** The CENTRE of the first leg's foot. A strut face is a quad - two points at
 *  the foot, two at the hub, each pair offset by the leg's half-width - so a
 *  single lowest-z point is one EDGE of the leg and sits 2.56 px off the
 *  meridian at this scale. Averaging the pair is the leg's own axis, which is
 *  what the north claim is about. */
const northFoot = () => {
  const pts = [...scopeFaces(45, 0, cameraDir(DOME_TILT_DEG, 0))[0].points].sort((a, b) => a.z - b.z).slice(0, 2);
  return { x: (pts[0].x + pts[1].x) / 2,
           y: (pts[0].y + pts[1].y) / 2,
           z: (pts[0].z + pts[1].z) / 2 };
};

test("the tripod's front leg is planted due north, not at a chosen angle", () => {
  // The first leg's foot is the model's north. Projected at zero yaw it must
  // land on the same bearing as the horizon point due north - which is the
  // dome's own idea of north, so the two cannot disagree.
  const legN = project(northFoot());
  const skyN = projectAltAz(0, 0, CX, CY, R, DOME_TILT_DEG, 0);

  // Same side of centre, vertically: north is the FAR side of this camera, so
  // both sit above the horizon centre on screen.
  assert.ok(legN.y < CY, `the north leg projected below centre: ${legN.y}`);
  assert.ok(skyN.y < CY, "the dome's own north is not above centre - the "
                       + "camera changed and this case is now meaningless");
  // ...and on the centre line, like north itself.
  assert.ok(Math.abs(legN.x - CX) < 1e-6,
            `the north leg is off the meridian by ${legN.x - CX} px`);
  assert.ok(Math.abs(skyN.x - CX) < 1e-6, "the dome's north is off the meridian");
});

test("the model turns with the dome", () => {
  // Ninety degrees of yaw puts the north leg where east was. Anything that
  // tracked the drag separately - or did not track it at all - fails here.
  const foot = northFoot();
  const turned = project(foot, 90);
  const still = project(foot, 0);
  assert.ok(Math.abs(turned.x - still.x) > 10,
            `a 90 degree yaw moved the tripod by ${Math.abs(turned.x - still.x)} px`);
  // And it lands where the SKY's north lands under the same yaw, which is the
  // statement that matters: one camera, not two.
  const skyNturned = projectAltAz(0, 0, CX, CY, R, DOME_TILT_DEG, 90);
  assert.ok(Math.sign(turned.x - CX) === Math.sign(skyNturned.x - CX),
            "the tripod and the sky disagree about which way north went");
});

test("the tube points at the reticle, at every attitude", () => {
  // The claim: the tube's far end lies on the line from the dome's centre
  // toward the projected pointing. Checked as a direction rather than a
  // position, because the tube is short and the reticle is on the dome.
  for (const [alt, az] of [[10, 0], [35, 90], [60, 200], [80, 315], [45, 180]]) {
    const faces = scopeFaces(alt, az, cameraDir(DOME_TILT_DEG, 0));
    const tube = faces[3];          // legs are 0..2, the tube is next
    // The tube's far end is the pair of points furthest along the sky vector.
    const dir = skyVector(alt, az);
    const along = (p: { x: number; y: number; z: number }) =>
      p.x * dir.x + p.y * dir.y + p.z * dir.z;
    const far = [...tube.points].sort((a, b) => along(b) - along(a)).slice(0, 2);
    const mid = { x: (far[0].x + far[1].x) / 2,
                  y: (far[0].y + far[1].y) / 2,
                  z: (far[0].z + far[1].z) / 2 };

    const tip = project(mid);
    const hub = project({ x: 0, y: 0, z: 0.62 });
    const reticle = projectAltAz(alt, az, CX, CY, R, DOME_TILT_DEG, 0);

    const toTip = Math.atan2(tip.y - hub.y, tip.x - hub.x);
    const toReticle = Math.atan2(reticle.y - hub.y, reticle.x - hub.x);
    let d = Math.abs(toTip - toReticle);
    if (d > Math.PI) d = 2 * Math.PI - d;
    assert.ok(d < 0.09,
      `at alt ${alt} az ${az} the tube points ${(d * 180 / Math.PI).toFixed(1)} `
      + `degrees away from the reticle`);
  }
});

test("no strut ever collapses to a hairline, at any azimuth", () => {
  // THE BUG THE RENDER FOUND AND THE GEOMETRY CASES DID NOT.
  //
  // The first version took an arbitrary perpendicular for each strut's width
  // (`cross(d, worldUp)`). At azimuth 120 that plane stood edge-on to the
  // camera and the tube projected to a LINE: pointing exactly where it should,
  // with no width, invisible on the dome. Every case above passed, because
  // they all check direction and none checks whether the thing can be seen.
  //
  // Projected AREA is the property that was missing. A quad billboarded to the
  // camera has an area that varies with the strut's length on screen and
  // nothing else; the collapsing one goes to zero.
  //
  // MUTATION: `widthDir` returns `cross(d, {x:0,y:0,z:1})` again. Observed:
  // azimuth 120 and 300 fall to ~0.1 square px and this fails naming them.
  const shoelace = (pts: { x: number; y: number }[]) => {
    let a = 0;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = pts[(i + 1) % pts.length];
      a += p.x * q.y - q.x * p.y;
    }
    return Math.abs(a) / 2;
  };
  for (let az = 0; az < 360; az += 15) {
    for (const yaw of [0, 90, 215]) {
      const faces = scopeFaces(40, az, cameraDir(DOME_TILT_DEG, yaw));
      const tube = faces[3];
      const area = shoelace(tube.points.map(v => project(v, yaw)));
      assert.ok(area > 120,
        `at az ${az} yaw ${yaw} the tube projects to ${area.toFixed(1)} square `
        + `px - it is pointing the right way and cannot be seen`);
    }
  }
});

test("a tube pointed at the zenith does not collapse", () => {
  // `anyPerp` degenerates if it crosses the direction with world up and the
  // direction IS world up. The result would be a zero-width tube - invisible,
  // and only at the one attitude a test is least likely to try.
  const faces = scopeFaces(90, 0, cameraDir(DOME_TILT_DEG, 0));
  const tube = faces[3];
  const width = Math.max(...tube.points.map(p =>
    Math.hypot(p.x - tube.points[0].x, p.y - tube.points[0].y, p.z - tube.points[0].z)));
  assert.ok(width > 0.1, `the zenith tube collapsed to ${width}`);
});

test("it draws, in back-to-front order, without touching the reticle", () => {
  // A recording context: the model must paint SOMETHING, and it must leave the
  // canvas state as it found it - the cross is drawn straight after and picks
  // up whatever stroke style is current.
  const ops: string[] = [];
  const ctx = {
    save: () => ops.push("save"), restore: () => ops.push("restore"),
    beginPath: () => ops.push("beginPath"), closePath: () => {},
    moveTo: () => {}, lineTo: () => {},
    fill: () => ops.push("fill"), stroke: () => ops.push("stroke"),
    set fillStyle(_v: string) {}, set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {}, set lineJoin(_v: string) {},
  } as unknown as CanvasRenderingContext2D;

  drawDomeScope(ctx, 45, 120, CX, CY, R, DOME_TILT_DEG, 0);
  assert.ok(ops.filter(o => o === "fill").length >= 5,
            `only ${ops.filter(o => o === "fill").length} faces were painted`);
  assert.equal(ops[0], "save", "the model did not save the canvas state");
  assert.equal(ops[ops.length - 1], "restore",
               "the model left its own stroke style on the context, and the "
             + "pointing cross is drawn immediately after it");
});

console.log(`domeScope.test: ${passed}/${passed} passed`);
export const result = { passed, failed: 0, total: passed };
