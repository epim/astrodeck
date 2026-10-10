// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Rotation maths for the horizon panorama scanner (SPEC-v2 3.2, 3.4).
//
// A camera rotation is a unit quaternion [w, x, y, z], camera frame to world
// (ENU) frame. The camera frame is x right, y up, looking along -z, so the
// rotation matrix is M = [right | up | -forward] (determinant +1) and the
// W3C device frame at screen angle 0 is the same frame. Radians inside the
// maths, degrees at every boundary a person or a test reads.
import { DEG, dot, orientationBasis } from '../photosphereGeometry';
import type { CameraBasis, Intrinsics, Quat, V3 } from './types';

const IDENTITY: Quat = [1, 0, 0, 0];
const clamp1 = (x: number) => Math.max(-1, Math.min(1, x));
/** Degrees into [0, 360). A value a rounding error below 0 lands on 0, not 360. */
const wrap360 = (deg: number) => ((deg % 360) + 360) % 360;
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

// ---- Intrinsics and field of view -----------------------------------------

/** Portrait intrinsics at pyramid level 0, 1 or 2: w = shortPx0 / 2^level, h = longPx0 / 2^level, f = fNorm * w. */
export function intrinsicsAt(level: 0 | 1 | 2, shortPx0: number, longPx0: number, fNorm: number): Intrinsics {
  const scale = 2 ** level, w = shortPx0 / scale, h = longPx0 / scale;
  return { w, h, f: fNorm * w, cx: w / 2, cy: h / 2 };
}

/** The prior is anchored on a 70 degree long axis and converted per granted aspect: 1.2694 at 9:16, 0.9521 at 3:4. */
export function priorFNorm(shortPx: number, longPx: number, longFovDeg = 70): number {
  return (longPx / shortPx) / 2 / Math.tan(longFovDeg * DEG / 2);
}

export function shortFovDeg(fNorm: number): number {
  return 2 * Math.atan(0.5 / fNorm) / DEG;
}

export function longFovDeg(fNorm: number, shortPx: number, longPx: number): number {
  return 2 * Math.atan((longPx / shortPx) / 2 / fNorm) / DEG;
}

// ---- Quaternions ----------------------------------------------------------

export function qmul(a: Quat, b: Quat): Quat {
  return [
    a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
    a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
    a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
    a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
  ];
}

/** Conjugate over the squared norm: the inverse of any non-zero quaternion, the conjugate of a unit one. */
export function qinv(q: Quat): Quat {
  const n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3];
  return [q[0] / n2, -q[1] / n2, -q[2] / n2, -q[3] / n2];
}

/** A zero quaternion has no direction and becomes the identity. */
export function qnormalize(q: Quat): Quat {
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  return n === 0 ? IDENTITY : [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** v + w t + u x t, with u the vector part and t = 2 u x v. Assumes a unit quaternion. */
export function qrotate(q: Quat, v: V3): V3 {
  const u: V3 = [q[1], q[2], q[3]];
  const c = cross(u, v);
  const t: V3 = [2 * c[0], 2 * c[1], 2 * c[2]];
  const d = cross(u, t);
  return [v[0] + q[0] * t[0] + d[0], v[1] + q[0] * t[1] + d[1], v[2] + q[0] * t[2] + d[2]];
}

/** Shortest arc: q and -q are the same rotation, so b is negated when the dot product is negative. */
export function slerp(a: Quat, b: Quat, u: number): Quat {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const sign = d < 0 ? -1 : 1;
  d *= sign;
  let ka: number, kb: number;
  if (d > 1 - 1e-12) {
    // Parallel to rounding error: sin(theta) is not worth dividing by.
    ka = 1 - u; kb = u;
  } else {
    const theta = Math.acos(d), s = Math.sin(theta);
    ka = Math.sin((1 - u) * theta) / s; kb = Math.sin(u * theta) / s;
  }
  kb *= sign;
  return qnormalize([ka * a[0] + kb * b[0], ka * a[1] + kb * b[1], ka * a[2] + kb * b[2], ka * a[3] + kb * b[3]]);
}

/** Rotation vector (axis times angle, radians) to quaternion. */
export function expSO3(v: V3): Quat {
  const angle = Math.hypot(v[0], v[1], v[2]);
  const s = angle < 1e-8 ? 0.5 : Math.sin(angle / 2) / angle;
  return [Math.cos(angle / 2), v[0] * s, v[1] * s, v[2] * s];
}

/** Quaternion to rotation vector, radians, on the shorter of its two representations (angle in [0, pi]). */
export function logSO3(q: Quat): V3 {
  const sign = q[0] < 0 ? -1 : 1;
  const w = sign * q[0], x = sign * q[1], y = sign * q[2], z = sign * q[3];
  const s = Math.hypot(x, y, z);
  if (s < 1e-12) return [2 * x, 2 * y, 2 * z];
  const k = 2 * Math.atan2(s, w) / s;
  return [x * k, y * k, z * k];
}

// ---- Camera basis and the W3C device orientation --------------------------

/** M = [right | up | -forward]: the camera looks along its own -z, so the third column is -forward. */
export function quatFromBasis(b: CameraBasis): Quat {
  const c2: V3 = [-b.forward[0], -b.forward[1], -b.forward[2]];
  const m00 = b.right[0], m10 = b.right[1], m20 = b.right[2];
  const m01 = b.up[0], m11 = b.up[1], m21 = b.up[2];
  const m02 = c2[0], m12 = c2[1], m22 = c2[2];
  const trace = m00 + m11 + m22;
  let w: number, x: number, y: number, z: number;
  // Shepperd's method: branch on the largest of the trace and the diagonal so the square root is never of a small number.
  if (trace > 0) {
    const s = 2 * Math.sqrt(trace + 1);
    w = s / 4; x = (m21 - m12) / s; y = (m02 - m20) / s; z = (m10 - m01) / s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    w = (m21 - m12) / s; x = s / 4; y = (m01 + m10) / s; z = (m02 + m20) / s;
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    w = (m02 - m20) / s; x = (m01 + m10) / s; y = s / 4; z = (m12 + m21) / s;
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = s / 4;
  }
  // q and -q are one rotation; w >= 0 makes the result comparable without a sign test.
  const q = qnormalize([w, x, y, z]);
  return q[0] < 0 ? [-q[0], -q[1], -q[2], -q[3]] : q;
}

export function basisFromQuat(q: Quat): CameraBasis {
  return { right: qrotate(q, [1, 0, 0]), up: qrotate(q, [0, 1, 0]), forward: qrotate(q, [0, 0, -1]) };
}

/** The camera frame is the W3C device frame at screen angle 0, so no screen rotation is applied. */
export function quatFromDeviceOrientation(alphaDeg: number, betaDeg: number, gammaDeg: number): Quat {
  return quatFromBasis(orientationBasis(alphaDeg, betaDeg, gammaDeg, 0));
}

/** Below this |cos beta| the pose is at gimbal lock, and below this |cos gamma| gamma is -90. Both are 6e-8 degrees,
 *  which is the rounding noise of a matrix entry (1e-16) relative to a value of 1e-9, so nothing real is lost. */
const EULER_EPS = 1e-9;

/** The inverse of `quatFromDeviceOrientation`, after the W3C "rotation matrix to Euler angles" conversion:
 *  alpha [0, 360), beta [-180, 180), gamma [-90, 90). The matrix columns are the device axes in the world, so
 *  m12 = -cos(beta) sin(alpha), m22 = cos(beta) cos(alpha), m32 = sin(beta), m31 = -cos(beta) sin(gamma) and
 *  m33 = cos(beta) cos(gamma). With cos(gamma) >= 0 on the gamma range, the sign of m33 is the sign of cos(beta).
 *  Two cases the spec writes as exact zeros are thresholds here, because a float matrix is never exactly singular:
 *  at gimbal lock (beta = +-90) only alpha +- gamma is determined and it is returned as alpha with gamma 0, and at
 *  gamma = -90 the sign of cos(beta) comes from m31. m33 is small whenever cos(beta) is, so "m33 is zero" means
 *  small next to |cos beta| = hypot(m31, m33), never small in absolute terms. */
export function deviceOrientationFromQuat(q: Quat): { alpha: number; beta: number; gamma: number } {
  const x = qrotate(q, [1, 0, 0]), y = qrotate(q, [0, 1, 0]), z = qrotate(q, [0, 0, 1]);
  const m11 = x[0], m21 = x[1], m31 = x[2];
  const m12 = y[0], m22 = y[1], m32 = y[2];
  const m33 = z[2];
  let alpha: number, beta: number, gamma: number;
  if (Math.hypot(m12, m22) < EULER_EPS) {
    alpha = Math.atan2(m21, m11);
    beta = m32 > 0 ? Math.PI / 2 : -Math.PI / 2;
    gamma = 0;
  } else {
    const gammaIsMinus90 = Math.abs(m33) <= EULER_EPS * Math.hypot(m31, m33);
    const inside = gammaIsMinus90 ? m31 > 0 : m33 > 0;   // beta within [-90, 90]
    if (inside) {
      alpha = Math.atan2(-m12, m22);
      beta = Math.asin(clamp1(m32));
      gamma = gammaIsMinus90 ? -Math.PI / 2 : Math.atan2(-m31, m33);
    } else {
      alpha = Math.atan2(m12, -m22);
      beta = -Math.asin(clamp1(m32));
      beta += beta >= 0 ? -Math.PI : Math.PI;
      gamma = gammaIsMinus90 ? -Math.PI / 2 : Math.atan2(m31, -m33);
    }
  }
  return { alpha: wrap360(alpha / DEG), beta: beta / DEG, gamma: gamma / DEG };
}

// ---- Heading, elevation, roll, separation ---------------------------------

/** Rotation about world +z by -deg. Azimuth runs clockwise from north, so this ADDS deg to every azimuth:
 *  headingDeg(qmul(worldYaw(d), q)) = headingDeg(q) + d. */
export function worldYaw(deg: number): Quat {
  const half = -deg * DEG / 2;
  return [Math.cos(half), 0, 0, Math.sin(half)];
}

/** Azimuth of the optical axis, [0, 360). Straight up or down has no azimuth and reads 0: within 1e-9 of the pole
 *  (6e-8 degrees) the horizontal part of the axis is float noise, and atan2 of noise is an arbitrary angle. */
export function headingDeg(q: Quat): number {
  const f = qrotate(q, [0, 0, -1]);
  if (Math.hypot(f[0], f[1]) < 1e-9) return 0;
  return wrap360(Math.atan2(f[0], f[1]) / DEG);
}

export function elevationDeg(q: Quat): number {
  const f = qrotate(q, [0, 0, -1]);
  return Math.atan2(f[2], Math.hypot(f[0], f[1])) / DEG;
}

/** The camera's right vector is horizontal when the phone is level; positive when the right edge is raised. */
export function rollDeg(q: Quat): number {
  const r = qrotate(q, [1, 0, 0]);
  return Math.atan2(r[2], Math.hypot(r[0], r[1])) / DEG;
}

/** atan2 of the cross and dot products, which keeps its precision where acos of a dot near 1 loses it. */
export function axisSeparationDeg(a: Quat, b: Quat): number {
  const fa = qrotate(a, [0, 0, -1]), fb = qrotate(b, [0, 0, -1]);
  const c = cross(fa, fb);
  return Math.atan2(Math.hypot(c[0], c[1], c[2]), dot(fa, fb)) / DEG;
}

/** The rotation angle of qinv(a) . b, [0, 180]. */
export function angleBetweenDeg(a: Quat, b: Quat): number {
  const d = qmul(qinv(a), b);
  return 2 * Math.atan2(Math.hypot(d[1], d[2], d[3]), Math.abs(d[0])) / DEG;
}

/** Copied from photospherePose.ts:30 so the new scanner does not import the old one. The original stays. */
export function poseSeparation(a: CameraBasis, b: CameraBasis): number {
  return Math.max(...(['right', 'up', 'forward'] as const).map(axis =>
    Math.acos(Math.max(-1, Math.min(1, dot(a[axis], b[axis])))) * 180 / Math.PI));
}

// ---- Pinhole projection and the rotation homography -----------------------

/** Pixel to unit ray in the camera frame (x right, y up, looking along -z). Image y runs down, so it is negated. */
export function unprojectPixel(k: Intrinsics, x: number, y: number): V3 {
  const vx = (x - k.cx) / k.f, vy = -(y - k.cy) / k.f;
  const n = Math.hypot(vx, vy, 1);
  return [vx / n, vy / n, -1 / n];
}

/** Camera-frame direction to pixel. Null for a direction at or behind the image plane (a ray's depth is -z). */
export function projectCamera(k: Intrinsics, ray: V3): [x: number, y: number] | null {
  const depth = -ray[2];
  if (!(depth > 0)) return null;
  return [k.cx + k.f * ray[0] / depth, k.cy - k.f * ray[1] / depth];
}

/** 3x3 row-major matrix of a unit quaternion. */
function matrixFromQuat(q: Quat): number[] {
  const [w, x, y, z] = q;
  return [
    1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
    2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
    2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
  ];
}

/** H = Kb R_BA Ka^-1, 3x3 row-major, maps a homogeneous pixel of image a to the pixel of image b that sees the same
 *  direction, up to scale. `qBA` takes camera-a vectors to camera-b vectors (b from a): with camera-to-world poses
 *  P_a and P_b it is qinv(P_b) . P_a, and its inverse takes b-pixels to a-pixels. R is written in the camera
 *  frame (y up, looking along -z) and pixels are y down, looking along +z, so R enters as D R D with D = diag(1, -1, -1);
 *  that is the same rotation in the image frame, and H is exact for every pixel, not only near the centre. */
export function rotationHomography(ka: Intrinsics, kb: Intrinsics, qBA: Quat): Float64Array {
  const r = matrixFromQuat(qBA), d = [1, -1, -1];
  const m = new Float64Array(9);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) m[i * 3 + j] = d[i] * d[j] * r[i * 3 + j];
  // Ka^-1 = [[1/fa, 0, -cxa/fa], [0, 1/fa, -cya/fa], [0, 0, 1]]; Kb = [[fb, 0, cxb], [0, fb, cyb], [0, 0, 1]].
  const kaInv = [1 / ka.f, 0, -ka.cx / ka.f, 0, 1 / ka.f, -ka.cy / ka.f, 0, 0, 1];
  const kbm = [kb.f, 0, kb.cx, 0, kb.f, kb.cy, 0, 0, 1];
  const mul = (a: ArrayLike<number>, b: ArrayLike<number>) => {
    const out = new Float64Array(9);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++)
      out[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
    return out;
  };
  return mul(kbm, mul(m, kaInv));
}

// ---- Horn's absolute orientation ------------------------------------------

/** Eigenvector of the largest eigenvalue of a symmetric 4x4 matrix, by cyclic Jacobi rotations. The matrix is scaled to
 *  unit Frobenius norm first (the eigenvectors do not change), so the stopping rule below is relative to the matrix:
 *  weights from inverse covariances span many decades, and an absolute rule stops a small enough matrix before its
 *  first sweep. A zero matrix has no preferred direction and gives the first axis, which is the identity rotation. */
function largestEigenvector4(n: number[][]): number[] {
  const scale = Math.hypot(...n.flat());
  if (scale === 0) return [1, 0, 0, 0];
  const a = n.map(row => row.map(x => x / scale));
  const v = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  for (let sweep = 0; sweep < 50; sweep++) {
    let off = 0;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 4; q++) off += a[p][q] * a[p][q];
    if (off < 1e-30) break;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 4; q++) {
      if (a[p][q] === 0) continue;
      const theta = (a[q][q] - a[p][p]) / (2 * a[p][q]);
      const t = (theta < 0 ? -1 : 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < 4; k++) {
        const akp = a[k][p], akq = a[k][q];
        a[k][p] = c * akp - s * akq; a[k][q] = s * akp + c * akq;
      }
      for (let k = 0; k < 4; k++) {
        const apk = a[p][k], aqk = a[q][k];
        a[p][k] = c * apk - s * aqk; a[q][k] = s * apk + c * aqk;
      }
      for (let k = 0; k < 4; k++) {
        const vkp = v[k][p], vkq = v[k][q];
        v[k][p] = c * vkp - s * vkq; v[k][q] = s * vkp + c * vkq;
      }
    }
  }
  let best = 0;
  for (let i = 1; i < 4; i++) if (a[i][i] > a[best][best]) best = i;
  return [v[0][best], v[1][best], v[2][best], v[3][best]];
}

/** Horn's closed-form absolute orientation: the unit quaternion q that minimises the weighted sum of |b_i - q a_i q*|^2,
 *  from the largest eigenvector of the 4x4 matrix built from S = sum w a b^T. The rays need not be unit length.
 *  No pairs gives the identity; fewer than two non-parallel pairs leave the rotation about their axis undetermined.
 *  The result has w >= 0. */
export function rotationFromRays(a: readonly V3[], b: readonly V3[], w?: readonly number[]): Quat {
  if (a.length !== b.length || (w !== undefined && w.length !== a.length)) {
    throw new RangeError('rotationFromRays: a, b and w must have the same length');
  }
  let sxx = 0, sxy = 0, sxz = 0, syx = 0, syy = 0, syz = 0, szx = 0, szy = 0, szz = 0;
  for (let i = 0; i < a.length; i++) {
    const wi = w === undefined ? 1 : w[i];
    const [ax, ay, az] = a[i], [bx, by, bz] = b[i];
    sxx += wi * ax * bx; sxy += wi * ax * by; sxz += wi * ax * bz;
    syx += wi * ay * bx; syy += wi * ay * by; syz += wi * ay * bz;
    szx += wi * az * bx; szy += wi * az * by; szz += wi * az * bz;
  }
  const e = largestEigenvector4([
    [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
    [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
    [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
    [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
  ]);
  const q = qnormalize([e[0], e[1], e[2], e[3]]);
  return q[0] < 0 ? [-q[0], -q[1], -q[2], -q[3]] : q;
}
