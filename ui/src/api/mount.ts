// api/mount.ts: the mount's RELATIVE move - "nudge the tube 10 arcminutes east"
// (D-RIG-4). Server: server/astrodeck/mount_offset.py (the geometry) routed as
// `POST /api/mount/nudge` in api/app.py's mount block.
//
// WHY A RELATIVE MOVE IS A SERVER ROUTE AND NOT ARITHMETIC UP HERE. The offset
// a nudge asks for is not the offset it commands: RA is not a great circle, so
// an angle ON THE SKY has to be divided by cos(dec) before it becomes an offset
// in right ascension (`mount_offset.py:67-100` - at dec 60 a 10-arcmin nudge
// east is 20 arcmin of RA, and at dec 89 it is 573). A client that did the sum
// itself would be correct at the equator and quietly, progressively wrong
// towards the pole, which is exactly where a person is most likely to be
// nudging: polar alignment, a circumpolar target. The route also runs the move
// through the SAME horizon and sun-cone guards a plain goto passes, so a nudge
// cannot walk the tube somewhere a goto would have refused.
//
// It replaces two client-side approximations at once. `SlewPad`'s NINA branch
// built a relative goto by adding a fixed 0.25 degrees to `ra_hours` with no
// cos(dec) term; the pulse branch drove `/api/mount/move` for 250 ms and hoped.
// Neither could say how far the mount actually went. This one is told in
// arcminutes and answers with the from/to it computed.

import { api, ApiError } from "../api";

/** The two axes `parse_nudge` accepts (`mount_offset.py:42`). */
export type NudgeAxis = "ra" | "dec";

/** `mount_offset.py:20-28`: below one arcminute a "correction" is inside the
 *  mount's own backlash and settle noise; above 600 (10 degrees) what the
 *  operator wants is a goto, and the goto route is right there. A picker whose
 *  stops stay inside these two numbers can never earn a 422. */
export const NUDGE_MIN_ARCMIN = 1;
export const NUDGE_MAX_ARCMIN = 600;

/** The sentence for the ONE refusal that means the picker itself is wrong. The
 *  server's own words are "a nudge is 1 to 600 arcminutes", which is true and
 *  is not what the tiles are labelled in - the operator picked `10 deg`, not
 *  `600`. Said in the units on screen, and exported so the sheet and its test
 *  assert the same string. */
export const NUDGE_OUT_OF_RANGE_NOTE = "A nudge is 1 arcminute to 10 degrees.";

/** What `POST /api/mount/nudge` answers with (202). `from`/`to` are J2000, the
 *  frame the goto route takes, and `arcmin` is the signed size the server
 *  parsed - read it back rather than trusting what was sent, because it is the
 *  number the mount was actually given. */
export interface NudgeResult {
  started: "goto";
  from: { ra_hours: number; dec_deg: number };
  to: { ra_hours: number; dec_deg: number };
  arcmin: number;
  /** True when `_MAX_RA_OFFSET_HOURS` cut the move short (`mount_offset.py`).
   *  The RA offset a sky angle needs is the angle divided by cos(dec), so near
   *  the pole an honest 600' request becomes hours of RA and the server clamps
   *  it - and until S2 landed these two fields, the response echoed the
   *  REQUESTED size and the toast said the mount had moved ten degrees it had
   *  not. OPTIONAL because an older engine sends neither: absent is "this
   *  engine cannot tell you", which is not the same as `false`, so the caller
   *  must branch on `clamped === true` and say nothing otherwise. */
  clamped?: boolean;
  /** What the mount was actually given, in arcminutes on the sky. Present with
   *  `clamped`; read it rather than recomputing, because the division that
   *  produced it is the server's. */
  achieved_arcmin?: number;
}

/** The toast for a nudge the pole clamped. `null` when nothing was clamped or
 *  when the engine does not report it - silence is the honest answer there, and
 *  a sentence built from the REQUESTED size would be the defect this closes.
 *
 *  It names both numbers and the reason, because the operator is about to press
 *  again: knowing the move saturated is what stops four more taps that each
 *  travel 19 arcminutes of the 600 asked for. */
export function nudgeClampNote(r: NudgeResult): string | null {
  if (r.clamped !== true || typeof r.achieved_arcmin !== "number") return null;
  const asked = Math.round(Math.abs(r.arcmin));
  const got = Math.round(Math.abs(r.achieved_arcmin));
  return `Moved ${got}' of the ${asked}' asked - the RA offset saturates near the pole.`;
}

/** `POST /api/mount/nudge` (`control.mount`, reaches `Telescope.slew`) - move
 *  the mount `arcmin` arcminutes along one axis from where it is now. East and
 *  north are positive; the CALLER applies the reverse-RA / reverse-Dec toggles
 *  before it gets here, so this wrapper sends exactly what it is given.
 *
 *  THE THREE REFUSALS, and they are told apart by `code`/`status`, never by
 *  matching a message:
 *    409 - the horizon guard, the sun cone, or the `goto` lane already busy.
 *          `ApiError.message` is the server's own sentence and is the only one
 *          that can name which of the three it was: show it VERBATIM.
 *    422 `code: "out_of_range"` - the size is outside 1..600. That is a defect
 *          in whatever picked the number, not something the operator did, so
 *          the caller shows `NUDGE_OUT_OF_RANGE_NOTE` and puts the picker back
 *          on its last legal stop (see `isNudgeOutOfRange`).
 *    409 from `_spawn` - another motion owns the lane; same verbatim treatment.
 *
 *  There is no 403 branch here on purpose: a control the caller has already
 *  honest-disabled on `control.mount` never reaches the wire. */
export const nudgeMount = (axis: NudgeAxis, arcmin: number): Promise<NudgeResult> =>
  api.post<NudgeResult>("/api/mount/nudge", { axis, arcmin });

/** The sentence for an engine that predates the route. The rig on the other end
 *  may be older than the UI - that is the normal case during a rollout - and a
 *  bare `Not Found` toast would read as a fault in the mount. The pad's own
 *  continuous slew still works, so the sentence says so. */
export const NUDGE_ABSENT_NOTE =
  "This engine does not carry the arcminute nudge yet - hold a pad key to slew instead.";

/** A 404 from this route means the ROUTE is not there: the mount checks
 *  (`_require_telescope`, the horizon guard, the sun cone) all answer 409, and
 *  a bad size answers 422, so nothing else on this path can produce one. */
export function isNudgeAbsent(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

/** Was this the 422 that means the PICKER is wrong?
 *
 *  Both halves are checked. `code` alone would also match a 409 that grew the
 *  same word, and the status alone would swallow any other 422 the route ever
 *  raises - a pydantic field violation on `axis`, say, which is a different
 *  defect and deserves its own message rather than a sentence about
 *  arcminutes. */
export function isNudgeOutOfRange(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && e.code === "out_of_range";
}
