// domeScope.ts - the little telescope that stands in the middle of the dome
// and points where the mount points (issue #67).
//
// WHY IT IS BUILT FROM 3D POINTS AND NOT DRAWN AS AN ICON.
//
// The dome already has one camera: `projectDome` takes a vector in the
// observer's frame (+x East, +y North, +z up), applies the panel's yaw, then
// the fixed `DOME_TILT_DEG` elevation, and hands back a canvas point plus a
// depth. Every cloud cell, track and horizon point on that canvas comes
// through it.
//
// So the model goes through it too. Each strut is two vectors in that frame,
// projected by the SAME function, which makes three things true for free and
// none of them by coincidence:
//
//   - the tripod is planted due north because its front leg IS `+y`, not
//     because someone chose an angle that looked north at the default yaw;
//   - it turns with the dome, because the yaw is applied to the model's
//     vectors exactly as it is to the sky's;
//   - the tube points at the reticle because the tube's direction IS
//     `skyVector(alt, az)` - the same call the pointing cross is drawn from.
//
// Any other construction has a second copy of the camera in it, and the two
// drift the first time the tilt changes.
//
// It is orthographic like the rest of the dome, so there is no perspective to
// sell the depth. What sells it is occlusion order (painter's algorithm on the
// projected depth) and a flat two-tone shade per face, which is enough at the
// 280-400 px this dome occupies on a phone.

import { projectDome, skyVector, type SkyVec } from "./domeProjection";

/** The model's size as a fraction of the dome radius. The tripod's feet sit at
 *  this radius on the ground plane, so the whole thing is a small object in the
 *  middle of a big hemisphere - which is what it is. Chosen by drawing it: much
 *  under 0.1 and the tube reads as a smudge at phone width, much over 0.2 and
 *  it starts to cover the low southern sky, which is where the obstructions
 *  the operator is looking for live. */
export const SCOPE_SCALE = 0.155;

/** How far the tube extends from the mount hub, in the same units. Shorter than
 *  the tripod is wide, so the model reads as a stubby refractor rather than as
 *  an arrow - an arrow would be a second reticle, and there is already one. */
const TUBE_LEN = 1.05;
/** Half-width of the tube, and of the legs. */
const TUBE_R = 0.17;
const LEG_R = 0.078;   // thicker than it looks like it needs to be: a leg
                       // pointing at the camera is billboarded edge-on and
                       // reads as a stray whisker at 0.055.
/** Height of the mount hub above the feet. */
const HUB_H = 0.62;

export interface ScopeColors {
  /** The lit side of a face. */
  light: string;
  /** The shaded side. */
  dark: string;
  /** The outline, which is what keeps the model readable against cloud. */
  line: string;
}

export const SCOPE_COLORS: ScopeColors = {
  light: "rgba(214,222,236,0.96)",
  dark: "rgba(120,132,154,0.94)",
  line: "rgba(8,10,16,0.85)",
};

type V3 = { x: number; y: number; z: number };

const add = (a: V3, b: V3): V3 => ({ x: a.x + b.x, y: a.y + b.y, z: a.z + b.z });
const mul = (a: V3, k: number): V3 => ({ x: a.x * k, y: a.y * k, z: a.z * k });
const sub = (a: V3, b: V3): V3 => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });

function norm(a: V3): V3 {
  const m = Math.hypot(a.x, a.y, a.z) || 1;
  return { x: a.x / m, y: a.y / m, z: a.z / m };
}

function cross(a: V3, b: V3): V3 {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

/** Where the dome's camera is, expressed in the MODEL's own frame.
 *
 *  `projectDome` rotates a vector by +yaw and then views it from a fixed
 *  camera due south at `tiltDeg`, whose view direction is `(0, cos t, -sin t)`.
 *  The model is rotated by that same +yaw, so the camera as the MODEL sees it
 *  is that direction rotated back by -yaw. */
export function cameraDir(tiltDeg: number, yawDeg: number): V3 {
  const t = (tiltDeg * Math.PI) / 180;
  const v: V3 = { x: 0, y: Math.cos(t), z: -Math.sin(t) };
  const w = (-yawDeg * Math.PI) / 180;
  const cw = Math.cos(w), sw = Math.sin(w);
  return { x: v.x * cw - v.y * sw, y: v.x * sw + v.y * cw, z: v.z };
}

/** The width direction for a strut along `d`: perpendicular to the strut AND
 *  to the view, which is the widest that strut can ever appear.
 *
 *  THE FIRST VERSION USED AN ARBITRARY PERPENDICULAR (`cross(d, up)`), and the
 *  render showed why that is wrong: at azimuth 120 the chosen plane stood
 *  edge-on to the camera and the tube collapsed to a hairline. It was correct
 *  geometry that could not be seen, which is the failure mode a geometry test
 *  passes straight through - the tube pointed exactly where it should, with no
 *  width. Billboarding removes the whole class: the quad always faces the
 *  camera, so the only thing that changes with attitude is its length.
 *
 *  Degenerate only when the strut points straight at the camera, where any
 *  perpendicular is as good as another and the strut is a dot regardless. */
function widthDir(d: V3, view: V3): V3 {
  const c = cross(d, view);
  if (Math.hypot(c.x, c.y, c.z) > 1e-6) return norm(c);
  const up: V3 = { x: 0, y: 0, z: 1 };
  const c2 = cross(d, up);
  if (Math.hypot(c2.x, c2.y, c2.z) > 1e-6) return norm(c2);
  return norm(cross(d, { x: 0, y: 1, z: 0 }));
}

/** One flat face of the model: the observer-frame points of its outline, and
 *  whether it takes the lit or the shaded tone. */
interface Face {
  points: V3[];
  lit: boolean;
}

/** A capsule from `a` to `b` of radius `rad`, as a quad plus an end cap, in the
 *  plane most nearly facing the camera. Two faces rather than a cylinder mesh:
 *  at this size the silhouette is all that reads, and a mesh would be a lot of
 *  arithmetic to produce the same forty pixels. */
function strut(a: V3, b: V3, rad: number, lit: boolean, view: V3): Face[] {
  const d = norm(sub(b, a));
  const side = mul(widthDir(d, view), rad);
  return [{
    points: [add(a, side), add(b, side), sub(b, side), sub(a, side)],
    lit,
  }];
}

/**
 * The model, as faces in the observer's frame, for a mount pointing at
 * `alt`/`az`.
 *
 * Pure geometry - no canvas, no projection, no colour. The test drives this
 * and checks the tube really points where it says; the drawing below is then
 * only ordering and fill.
 */
export function scopeFaces(altDeg: number, azDeg: number,
                           view: V3 = { x: 0, y: 1, z: 0 }): Face[] {
  const hub: V3 = { x: 0, y: 0, z: HUB_H };
  const faces: Face[] = [];

  // Three legs, planted at 120 degree intervals with the FIRST one due north
  // (+y). That is the "tripod is set to point north" of the request, and it is
  // true by construction rather than by a chosen angle.
  // SHADED BY WHICH SIDE FACES YOU, not by index. `i === 0` lit the north leg
  // whatever the yaw, so a turn of the dome moved the highlight to the back of
  // the model and the depth cue inverted. The camera looks along `view` toward
  // the observer, so a point with a negative dot is on the near side.
  const nearSide = (p: V3) => p.x * view.x + p.y * view.y + p.z * view.z < 0;
  for (let i = 0; i < 3; i++) {
    const a = (i * 2 * Math.PI) / 3;
    const foot: V3 = { x: Math.sin(a), y: Math.cos(a), z: 0 };
    faces.push(...strut(foot, hub, LEG_R, nearSide(foot), view));
  }

  // The tube, through the hub, so a little of it shows behind the mount the
  // way a real OTA does in its rings.
  const dir = skyVector(altDeg, azDeg) as unknown as V3;
  const front = add(hub, mul(dir, TUBE_LEN));
  const back = add(hub, mul(dir, -TUBE_LEN * 0.28));
  faces.push(...strut(back, front, TUBE_R, nearSide(front), view));

  // The objective end, a short bar across the tube, which is what makes the
  // far end read as an aperture rather than as a blunt cut.
  const acrossRaw = widthDir(dir, view);
  const across = mul(acrossRaw, TUBE_R * 1.35);
  faces.push({
    points: [add(front, across), sub(front, across),
             sub(add(front, mul(dir, 0.12)), across),
             add(add(front, mul(dir, 0.12)), across)],
    lit: false,
  });

  return faces;
}

/** Mean projected depth of a face, for the painter's ordering. */
function faceDepth(f: Face, project: (v: V3) => { depth: number }): number {
  let sum = 0;
  for (const p of f.points) sum += project(p).depth;
  return sum / f.points.length;
}

/**
 * Draw the model at the dome's centre.
 *
 * `cx`,`cy`,`r`,`tiltDeg`,`yawDeg` are the panel's own, unchanged - the model
 * scales with the dome and turns with it because it is projected by the dome's
 * camera, not because anything here tracks the drag.
 */
export function drawDomeScope(
  ctx: CanvasRenderingContext2D,
  altDeg: number, azDeg: number,
  cx: number, cy: number, r: number,
  tiltDeg: number, yawDeg: number,
  colors: ScopeColors = SCOPE_COLORS,
): void {
  const s = r * SCOPE_SCALE;
  // The model's own units are fractions of `s`, so it is projected as if it
  // were a sky vector of length `s/r` - which is exactly what `projectDome`
  // does with a non-unit vector, and is why this needs no second camera.
  const project = (v: V3) =>
    projectDome(mul(v, s / r) as unknown as SkyVec, cx, cy, r, tiltDeg, yawDeg);

  const faces = scopeFaces(altDeg, azDeg, cameraDir(tiltDeg, yawDeg));
  // Back to front. Without this the tripod's far leg paints over the tube and
  // the whole thing reads as a flat tangle.
  faces.sort((a, b) => faceDepth(a, project) - faceDepth(b, project));

  ctx.save();
  ctx.lineJoin = "round";
  for (const f of faces) {
    ctx.beginPath();
    f.points.forEach((p, i) => {
      const q = project(p);
      if (i === 0) ctx.moveTo(q.x, q.y);
      else ctx.lineTo(q.x, q.y);
    });
    ctx.closePath();
    ctx.fillStyle = f.lit ? colors.light : colors.dark;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = colors.line;
    ctx.stroke();
  }
  ctx.restore();
}
