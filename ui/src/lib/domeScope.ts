// domeScope.ts - the little telescope that stands in the middle of the dome
// and points where the mount points (issue #67).
//
// TWO FRAMES, ON PURPOSE, AND THIS IS THE WHOLE DESIGN.
//
// The tripod is real 3D. Its legs are vectors in the observer's frame (+x East,
// +y North, +z up) pushed through `projectDome` - the dome's own camera, the
// one every cloud cell and track goes through. So the front leg IS `+y` and is
// therefore planted north, and the whole stand turns with the dome because the
// panel's yaw is applied to its vectors exactly as it is to the sky's. Neither
// is a chosen angle, and neither can drift from the dome, because there is only
// one camera.
//
// The TUBE is drawn in screen space, along the projected bearing to the
// reticle, at a CONSTANT length.
//
// That is a deliberate lie about one quantity and it is worth being plain about
// which. A tube that is honestly 3D foreshortens, and this projection is
// orthographic, so a scope pointing near the camera - anywhere low in the south
// - collapses to a stub. The first version did exactly that, and the render of
// it was unreadable: at alt 12 az 200 you could not tell what you were looking
// at. Foreshortening is the correct behaviour of a real object and the wrong
// behaviour for a 40-pixel indicator whose whole job is to be recognisable at a
// glance.
//
// So the tube keeps what a reader uses - its BEARING on screen, the same line
// their eye follows from the mount to the cross - and gives up what they cannot
// use, its apparent length. It is an icon that agrees with the geometry rather
// than a model of it.
//
// What sells the rest is silhouette, not shading: a tapered tube, a dew shield
// at the objective, a focuser stub at the back, a mount head where it meets the
// stand. At the 280-400 px this dome occupies on a phone, outline is all that
// reads - two-tone faces on a correct 3D mesh read as grey confetti, which is
// what the first version looked like.

import { projectDome, skyVector, type SkyVec } from "./domeProjection";

/** Tripod foot radius, as a fraction of the dome radius. */
export const SCOPE_SCALE = 0.15;
/** Tube length on screen, as a fraction of the dome radius. Constant - see the
 *  header. Long enough to read as a tube beside a tripod this wide. */
export const TUBE_SCREEN = 0.30;

/** Height of the mount head above the feet, in tripod-radius units. */
const HUB_H = 0.78;
const LEG_R = 0.085;

export interface ScopeColors {
  light: string;
  dark: string;
  line: string;
}

export const SCOPE_COLORS: ScopeColors = {
  light: "rgba(226,232,242,0.97)",
  dark: "rgba(126,139,163,0.95)",
  line: "rgba(8,10,16,0.9)",
};

type V3 = { x: number; y: number; z: number };
type P2 = { x: number; y: number };

const norm = (a: V3): V3 => {
  const m = Math.hypot(a.x, a.y, a.z) || 1;
  return { x: a.x / m, y: a.y / m, z: a.z / m };
};
const sub = (a: V3, b: V3): V3 => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
const cross = (a: V3, b: V3): V3 => ({
  x: a.y * b.z - a.z * b.y,
  y: a.z * b.x - a.x * b.z,
  z: a.x * b.y - a.y * b.x,
});

/** Where the dome's camera is, in the MODEL's frame.
 *
 *  `projectDome` rotates a vector by +yaw and then views it from a fixed camera
 *  due south at `tiltDeg`, whose view direction is `(0, cos t, -sin t)`. The
 *  model is rotated by that same +yaw, so the camera as the model sees it is
 *  that direction rotated back by -yaw. */
export function cameraDir(tiltDeg: number, yawDeg: number): V3 {
  const t = (tiltDeg * Math.PI) / 180;
  const v: V3 = { x: 0, y: Math.cos(t), z: -Math.sin(t) };
  const w = (-yawDeg * Math.PI) / 180;
  const cw = Math.cos(w), sw = Math.sin(w);
  return { x: v.x * cw - v.y * sw, y: v.x * sw + v.y * cw, z: v.z };
}

/** Width direction for a leg: perpendicular to it AND to the view, so the leg
 *  is as wide as it can be rather than edge-on. An arbitrary perpendicular
 *  collapses a leg to a hairline at some azimuths - correct geometry that
 *  cannot be seen, which is the mistake the tube used to make too. */
function widthDir(d: V3, view: V3): V3 {
  const c = cross(d, view);
  if (Math.hypot(c.x, c.y, c.z) > 1e-6) return norm(c);
  const c2 = cross(d, { x: 0, y: 0, z: 1 });
  if (Math.hypot(c2.x, c2.y, c2.z) > 1e-6) return norm(c2);
  return norm(cross(d, { x: 0, y: 1, z: 0 }));
}

export interface Leg { points: V3[]; near: boolean }

/** The three legs and the hub, in the observer's frame. Pure geometry, so a
 *  test can check the front leg really is north. */
export function tripodLegs(view: V3): { legs: Leg[]; hub: V3 } {
  const hub: V3 = { x: 0, y: 0, z: HUB_H };
  const legs: Leg[] = [];
  for (let i = 0; i < 3; i++) {
    const a = (i * 2 * Math.PI) / 3;
    // i === 0 is (sin 0, cos 0) = (0, 1) - due NORTH, by construction.
    const foot: V3 = { x: Math.sin(a), y: Math.cos(a), z: 0 };
    const d = norm(sub(hub, foot));
    const w = widthDir(d, view);
    const s = { x: w.x * LEG_R, y: w.y * LEG_R, z: w.z * LEG_R };
    legs.push({
      points: [
        { x: foot.x + s.x, y: foot.y + s.y, z: foot.z + s.z },
        { x: hub.x + s.x, y: hub.y + s.y, z: hub.z + s.z },
        { x: hub.x - s.x, y: hub.y - s.y, z: hub.z - s.z },
        { x: foot.x - s.x, y: foot.y - s.y, z: foot.z - s.z },
      ],
      // Shaded by which side faces the viewer, not by index: lighting leg 0
      // whatever the yaw moved the highlight to the back of the model when the
      // dome turned, and inverted the depth cue.
      near: foot.x * view.x + foot.y * view.y + foot.z * view.z < 0,
    });
  }
  return { legs, hub };
}

/**
 * The tube's outline in screen space: the mount head at `hub`, a unit screen
 * direction `(ux, uy)` toward the reticle, and a length in px.
 *
 * Three closed polygons - body, dew shield, focuser - so the caller fills them
 * in order.
 */
export function tubeOutline(hub: P2, ux: number, uy: number, len: number): P2[][] {
  const vx = -uy, vy = ux;                       // screen basis across the tube
  const at = (a: number, b: number): P2 =>
    ({ x: hub.x + ux * a + vx * b, y: hub.y + uy * a + vy * b });

  const back = -0.26 * len;      // a little of the tube shows behind the mount
  const front = 0.90 * len;
  const rBack = 0.115 * len;
  const rFront = 0.145 * len;    // real tubes are wider at the sky end

  const body: P2[] = [
    at(back, rBack), at(front, rFront), at(front, -rFront), at(back, -rBack),
  ];
  // The dew shield. This is the single strongest "that is a telescope" cue at
  // small size; without it the tube reads as a crayon.
  const shield: P2[] = [
    at(front, 0.19 * len), at(len, 0.19 * len),
    at(len, -0.19 * len), at(front, -0.19 * len),
  ];
  // The focuser, out of the back at right angles - the other cue, and the one
  // that says which end you are looking at.
  const focuser: P2[] = [
    at(back + 0.03 * len, rBack), at(back + 0.03 * len, rBack + 0.17 * len),
    at(back - 0.13 * len, rBack + 0.17 * len), at(back - 0.13 * len, rBack),
  ];
  return [body, shield, focuser];
}

function poly(ctx: CanvasRenderingContext2D, pts: P2[], fill: string, line: string) {
  ctx.beginPath();
  pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = 1.1;
  ctx.strokeStyle = line;
  ctx.stroke();
}

/**
 * Draw the scope at the dome's centre, aimed at `altDeg`/`azDeg`.
 *
 * `cx`,`cy`,`r`,`tiltDeg`,`yawDeg` are the panel's own, unchanged.
 */
export function drawDomeScope(
  ctx: CanvasRenderingContext2D,
  altDeg: number, azDeg: number,
  cx: number, cy: number, r: number,
  tiltDeg: number, yawDeg: number,
  colors: ScopeColors = SCOPE_COLORS,
): void {
  const view = cameraDir(tiltDeg, yawDeg);
  const s = r * SCOPE_SCALE;
  const project = (v: V3): P2 => {
    const q = projectDome({ x: v.x * s / r, y: v.y * s / r, z: v.z * s / r } as SkyVec,
                          cx, cy, r, tiltDeg, yawDeg);
    return { x: q.x, y: q.y };
  };

  const { legs, hub } = tripodLegs(view);
  const hub2 = project(hub);

  // The tube's SCREEN bearing: from the mount head to where the reticle is
  // actually drawn. Taken from the projection rather than from a 3D tube, so it
  // is exactly the line the reader's eye follows to the cross.
  const aim = projectDome(skyVector(altDeg, azDeg) as unknown as SkyVec,
                          cx, cy, r, tiltDeg, yawDeg);
  let dx = aim.x - hub2.x, dy = aim.y - hub2.y;
  const m = Math.hypot(dx, dy);
  if (m < 1e-6) { dx = 0; dy = -1; } else { dx /= m; dy /= m; }

  ctx.save();
  ctx.lineJoin = "round";

  // Legs, far ones first so a near leg paints over a far one.
  for (const leg of [...legs].sort((a, b) => Number(a.near) - Number(b.near))) {
    poly(ctx, leg.points.map(project), leg.near ? colors.light : colors.dark,
         colors.line);
  }

  // The mount head, which stops the tube looking balanced on a point.
  const head = 0.30 * s;
  poly(ctx, [
    { x: hub2.x - head, y: hub2.y - head * 0.62 },
    { x: hub2.x + head, y: hub2.y - head * 0.62 },
    { x: hub2.x + head * 0.72, y: hub2.y + head * 0.62 },
    { x: hub2.x - head * 0.72, y: hub2.y + head * 0.62 },
  ], colors.dark, colors.line);

  // ...then the tube over it.
  const [body, shield, focuser] = tubeOutline(hub2, dx, dy, r * TUBE_SCREEN);
  poly(ctx, focuser, colors.dark, colors.line);
  poly(ctx, body, colors.light, colors.line);
  poly(ctx, shield, colors.dark, colors.line);

  ctx.restore();
}
