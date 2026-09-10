// api/capture.ts: the one unsaved frame the rig is holding, and the decision to
// keep it (D-SES-4). Server: server/astrodeck/hub.py's promote buffer
// (`PendingSave`, `promotable_summary`, `promote_last_frame`), routed in
// api/app.py's capture block as `GET /api/capture/last` (`view.status`) and
// `POST /api/capture/last/save` (`control.capture`).
//
// WHY THE FRAME IS HELD AT ALL. A test exposure is how an operator decides
// whether the framing, the focus and the guiding are worth committing to, and
// until this route existed the answer "yes, keep that one" meant taking a
// SECOND exposure and hoping the sky, the seeing and the clouds had not moved.
// The pixels were already in memory; only the decision to write them was
// missing. `POST /api/capture` still decides at shutter time - this promotes
// the frame that decision threw away.
//
// ONE FRAME, PER CAMERA ROLE. The buffer holds exactly one, replaced on every
// capture and cleared by a saved one, because a 26 MP uint16 frame is ~50 MB
// and a queue of them is a queue of out-of-memory kills. That bound is the
// feature's shape rather than a limitation of it: "promote the LAST frame" is
// what an operator asks for when a throw-away exposure turns out to be worth
// keeping.
//
// `frame_id` IS AN INTERLOCK, NOT A SELECTOR. There is only one frame to save,
// so naming it changes nothing about WHICH frame is written - it changes what
// happens when the buffer has moved on underneath a card that is still showing
// the old one. Without it, a press on a stale screen silently writes an
// exposure the operator never looked at, under the settings and the target of
// the one they did. With it, that press is a refusal they can read. Send it.
//
// THREE REFUSALS, AND THEY ARE THREE DIFFERENT FACTS - which is why they are
// mapped onto a typed error carrying the server's `code` and never onto its
// message. "There is nothing to save", "you already saved that one" and "the
// rig is holding a newer one" ask for three different next actions (re-shoot /
// nothing / press again), and a client that reads the sentence instead of the
// code loses all three the first time somebody rewords a docstring.
//
// A 404 WITH NO CODE IS A DIFFERENT ANIMAL: it is FastAPI saying the route does
// not exist, i.e. an engine older than this feature. `isPromoteRouteMissing`
// exists so a screen can degrade to what it shipped with instead of offering a
// button that cannot work - see `next/hubs/rig/capture/lastFrame.ts`.

import { api, ApiError } from "../api";

/** `GET /api/capture/last` (`view.status`) - the held frame's IDENTITY and its
 *  settings, never its pixels (`hub.py`'s `promotable_summary`).
 *
 *  Every field but `available` and `saved` is null when nothing is held, so a
 *  caller reads `available` and not the presence of `id`. `saved` is always
 *  false and is on the wire rather than implied, so a row rendering the frame
 *  never has to infer it: a SAVED frame is not in this buffer at all. */
export interface LastFrame {
  available: boolean;
  /** Monotonic within the server process. The `frame_id` interlock's value. */
  id: number | null;
  /** Unix seconds, when the shutter closed - not when it was promoted. */
  ts: number | null;
  exposure_s: number | null;
  gain: number | null;
  binning: number | null;
  frame_type: string | null;
  /** What the operator typed at capture time; "" is a real answer. */
  target: string | null;
  /** The wheel's own name for the slot the frame was shot through. */
  filter: string | null;
  /** Always false: this endpoint only ever describes an UNSAVED frame. */
  saved: boolean;
}

/** The 200 from `POST /api/capture/last/save`.
 *
 *  `path` is CAPTURE-ROOT-RELATIVE - the only form of a frame's location that
 *  may leave the process - so it is a name to show, not a path to open. */
export interface PromotedFrame {
  saved: boolean;
  path: string;
  filter: string;
  /** The id of the frame that was written, i.e. the one that was interlocked. */
  id: number;
  /** The target the header was written with: the override when one was sent. */
  target: string;
  ts: number;
}

/** The codes `POST /api/capture/last/save` refuses with. Branch on these, never
 *  on the sentence beside them.
 *
 *  - `nothing_to_promote` (404) - the buffer is empty and nothing was ever
 *    saved from it. Re-shooting is the only way forward.
 *  - `already_saved` (409) - this frame IS on disk; the press was a second one.
 *    Not an error to apologise for: the operator got what they asked for.
 *  - `frame_id_mismatch` (409) - a newer frame is held than the one named. The
 *    server's sentence carries BOTH ids, which is the only place that number
 *    appears, so show it or re-read the buffer rather than re-wording it. */
export type PromoteRefusalCode =
  | "nothing_to_promote"
  | "already_saved"
  | "frame_id_mismatch";

/** A refusal from the promote route, with the server's `code` kept as a field.
 *
 *  A separate class rather than a bare `ApiError` so a caller cannot forget to
 *  check the code before reading the message: catching this type IS the check.
 *  Anything else the route can answer with - a 401, a 500, a dead link - stays
 *  an `ApiError` and reaches the caller unchanged, because those are not
 *  refusals of the request but failures of it. */
export class PromoteRefused extends Error {
  readonly code: PromoteRefusalCode;
  readonly status: number;
  constructor(message: string, code: PromoteRefusalCode, status: number) {
    super(message);
    this.name = "PromoteRefused";
    this.code = code;
    this.status = status;
  }
}

function refusalCode(code: string | undefined): PromoteRefusalCode | null {
  return code === "nothing_to_promote" || code === "already_saved"
    || code === "frame_id_mismatch"
    ? code
    : null;
}

/** True when the error is this ENGINE not having the promote surface at all -
 *  a plain FastAPI 404 with no `code` beside it.
 *
 *  The distinction matters because the two 404s want opposite screens: a
 *  `nothing_to_promote` says "that frame is gone, re-shoot", while a missing
 *  route says "this rig cannot do this, here is what it always could". Reading
 *  them as one produces a button that offers to save on an engine with no
 *  route to save through. */
export function isPromoteRouteMissing(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.code == null;
}

/** What `promoteLastFrame` may send. Both keys are optional and BOTH ARE ONLY
 *  SENT WHEN THE CALLER PASSED THEM - see the function. */
export interface PromoteOptions {
  /** Rename the field on the way to disk. The ONE value that may change after
   *  the fact: everything else in the header was a measurement, frozen when the
   *  shutter closed. Overriding it re-runs the identification cards server-side
   *  so OBJECT and the cards stay consistent with each other. */
  target?: string;
  /** The `id` from `GET /api/capture/last`, as the interlock described in this
   *  module's header. Omitted means "save whatever is held", which is what a
   *  caller that never read the buffer is actually asking for. */
  frame_id?: number;
}

/** `GET /api/capture/last` - what the rig is holding, or an explicit nothing.
 *
 *  Answers 200 either way: `available: false` is the empty case, and a 404 from
 *  here means the ROUTE is absent (`isPromoteRouteMissing`), not the frame. */
export const getLastFrame = (): Promise<LastFrame> =>
  api.get<LastFrame>("/api/capture/last");

/** `POST /api/capture/last/save` - write the held frame to disk.
 *
 *  Requires `control.capture`. Deliberately NOT relay-fenced: it writes one
 *  frame already in memory on the rig to the rig's own disk, exactly like
 *  `POST /api/capture`, and fencing it would mean "keep that one" is yes at the
 *  scope and no from the sofa.
 *
 *  It SENDS ONLY THE KEYS THE CALLER PASSED. `target: ""` is the server's own
 *  "keep what they typed", so an unconditional `target` would be harmless - but
 *  `frame_id` is not: a forged `undefined`-turned-`null` would read as "save
 *  whatever is held" and quietly defeat the interlock this module exists to
 *  carry. One rule for both keys is one rule to keep.
 *
 *  Throws `PromoteRefused` for the three refusal codes and a plain `ApiError`
 *  for everything else. */
export async function promoteLastFrame(
  opts: PromoteOptions = {},
): Promise<PromotedFrame> {
  const body: Record<string, unknown> = {};
  if (opts.target !== undefined) body.target = opts.target;
  if (opts.frame_id !== undefined) body.frame_id = opts.frame_id;
  try {
    return await api.post<PromotedFrame>("/api/capture/last/save", body);
  } catch (e) {
    if (e instanceof ApiError) {
      const code = refusalCode(e.code);
      if (code) throw new PromoteRefused(e.message, code, e.status);
    }
    throw e;
  }
}
