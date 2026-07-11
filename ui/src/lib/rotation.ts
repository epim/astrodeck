// rotation.ts — pure rotator angle & mechanical-range math.
//
// Direct transcription of docs/native-parity/algorithms/nina-platesolving.md
// §11.2 (get_target_mechanical_position / get_target_position) and §11.4
// (angle equality). No I/O, no device knowledge — table-tested, line-for-line
// mirror of server/astrodeck/rotation.py with the SAME test vectors. JS `%`
// is signed (unlike Python's, which is euclidean for a positive modulus), so
// every place the server uses Python's `%` this file goes through a euclidean
// mod helper instead.

//: slack from the reference implementation (ANGLE_EQUALS_EPSILON)
const _EPS = 1e-13;

/** Euclidean mod 360 — result always in [0, 360). */
export function mod360(a: number): number {
  return ((a % 360) + 360) % 360;
}

/** Euclidean mod 180 — same signed-`%` fix as mod360, used only inside the
 *  mod-180 equality below (Python's `a % 180.0` is already euclidean there). */
function mod180(a: number): number {
  return ((a % 180) + 180) % 180;
}

/** §11.4: equal within tol degrees, wrap-aware (mod 360). */
export function angleEquals(a: number, b: number, tol: number): boolean {
  const d = Math.abs(mod360(a) - mod360(b));
  const t = mod360(tol);
  return d - t <= _EPS || 360.0 - d - t <= _EPS;
}

/** §11.4 'oneEightyIsEqual': a frame rotated 180° is the same framing. */
export function angleEqualsMod180(a: number, b: number, tol: number): boolean {
  const d = Math.abs(mod180(a) - mod180(b));
  const t = mod180(tol);
  return d - t <= _EPS || 180.0 - d - t <= _EPS;
}

/** §11.2: map a mechanical angle into the allowed sweep. `p` in [0,360). */
export function targetMechanicalPosition(
  p: number,
  rangeType: "full" | "half" | "quarter",
  rangeStartDeg: number,
): number {
  const d = mod360(p - rangeStartDeg);
  let t: number;
  if (rangeType === "half") {
    t = d < 180.0 ? p : p + 180.0;
  } else if (rangeType === "quarter") {
    if (d < 90.0) t = p;
    else if (d < 180.0) t = p + 270.0;
    else if (d < 270.0) t = p + 180.0;
    else t = p + 90.0;
  } else {
    // "full"
    t = p;
  }
  return mod360(t);
}

/** §11.2 get_target_position: desired sky PA -> reachable sky PA given the
 *  mechanical range. `syncOffsetDeg` is mechanical − sky (Rotator ABC).
 *  `mechPos` is accepted for signature clarity/parity but the mapping only
 *  needs the offset. */
export function mapSkyTarget(
  skyTarget: number,
  mechPos: number,
  syncOffsetDeg: number,
  rangeType: "full" | "half" | "quarter",
  rangeStartDeg: number,
): number {
  void mechPos; // parity signature; offset alone determines the mapping
  const position = mod360(skyTarget);
  const mech = mod360(position + syncOffsetDeg);
  const mechTgt = targetMechanicalPosition(mech, rangeType, rangeStartDeg);
  return mod360(mechTgt - syncOffsetDeg + 360.0);
}

/** §11.3: signed distance to move. Commanded absolute sky angle is
 *  `mod360(orientation + distance)`.
 *
 *  FULL range considers the 180°-rotated frame when it is closer (camera
 *  frames are PA-ambiguous mod 180). Limited ranges must NOT collapse mod-180
 *  — the target was already range-mapped and the twin may be out of range —
 *  so they get plain shortest-signed normalization into (-180, 180]. */
export function shortestRotation(
  targetDeg: number,
  orientationDeg: number,
  rangeType: "full" | "half" | "quarter",
): number {
  const distance = targetDeg - orientationDeg;
  if (rangeType === "full") {
    const m = mod360(distance) % 180.0;
    const m2 = m - 180.0;
    return m < Math.abs(m2) ? m : m2;
  }
  const d = mod360(distance);
  return d > 180.0 ? d - 360.0 : d;
}

// offset = mechanical − sky (the server Rotator ABC's sync_offset_deg is not
// on the wire; both live values are, so derive it).
export function adjustedPa(
  targetPa: number,
  rot: { sky_deg: number; mech_deg: number },
  cfg: { range_type: "full" | "half" | "quarter"; range_start_deg: number },
): { target: number; adjusted: boolean } {
  const offset = mod360(rot.mech_deg - rot.sky_deg);
  const target = mapSkyTarget(targetPa, rot.mech_deg, offset,
    cfg.range_type, cfg.range_start_deg);
  return { target, adjusted: !angleEquals(target, mod360(targetPa), 0.1) };
}
