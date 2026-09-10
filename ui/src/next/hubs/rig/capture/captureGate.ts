// captureGate.ts - every reason the manual-capture bench refuses a press, as
// PURE functions over the state the screen already holds.
//
// WHY A SEPARATE FILE. CaptureView resolved each gate to a REASON STRING inline
// (its own comment: "a blocked control has to say WHY on a channel that survives
// a fingertip") and the strings then only existed inside a 1969-line render. A
// reason that cannot be asserted without mounting the screen is a reason nobody
// tests, and the four that matter here - read-only, no camera, polar owns the
// camera, a sequence owns the camera - are exactly the ones a night depends on.
//
// PRIORITY, fixed once and shared by every control on the screen:
//   link down -> capability -> camera not connected -> polar -> sequence ->
//   invalid settings -> a start already in flight
// The first four come from `next/lib/gate.ts` (ARCHITECTURE.md #8's ONE lock
// helper) so this screen cannot drift from the rest of the app; the last three
// are Capture's own and live here.
//
// The CAPABILITY sentence is composed here rather than taken from `lockReason`
// on purpose: `lockReason` says "needs operator or admin access", which is true
// of forty controls, and the design's copy rule is that a string must carry
// information the pixels do not. "Capturing needs operator or admin access."
// names the verb that was refused.

import { accessPhrase, capAllowed } from "../../../../lib/caps";
import { isExposureInvalid } from "../../../../lib/exposure";
import { lockReason } from "../../../lib/gate";
import type { Principal, RigStatus, SequenceState, WsPhase } from "../../../../types";

/** Which of the three start verbs is waiting for the camera to answer. */
export type ArmKind = "single" | "loop" | "live";

/** Everything a gate here reads. Plain values, no store, no hooks - so every
 *  row of the table below is one assertion in `captureGate.test.ts`. */
export interface CaptureGateInput {
  principal: Principal | null;
  status: RigStatus | null;
  equipConnected: boolean;
  wsPhase: WsPhase;
  /** `polar.state` is running or paused - alignment owns the camera. */
  polarBusy: boolean;
  /** The sequence's own state; running AND paused both hold the camera. */
  seqState: SequenceState["state"] | null;
  /** The RAW draft strings, never numbers: "", "1e9" and "-3" have to be able
   *  to block the shutter, and binding these to a number is what silently
   *  turned a blank box into a 0 s exposure (CAP-02). */
  exposureRaw: string;
  gainRaw: string;
  maxGain: number | null;
  looping: boolean;
  pending: ArmKind | null;
}

// ------------------------------------------------------------------ the copy

export const NEEDS_CAPTURE_REASON = `Capturing needs ${accessPhrase("control.capture")}.`;
export const POLAR_REASON = "Polar alignment owns the camera right now";
export const SEQUENCE_REASON = "A sequence owns the camera - stop it first";
export const EXPOSURE_FIX_REASON = "Fix the exposure above first";
export const GAIN_FIX_REASON = "Fix the gain above first";
export const PENDING_REASON = "A capture is starting - waiting for the camera to answer";
export const LOOP_RUNNING_REASON = "A capture loop is running - press Stop first";
export const NO_STACK_REASON = "Start Live View first - there is no stack to reset yet";
export const NO_LAST_LIGHT_REASON = "Shoot some lights first - there is nothing to match yet";
export const COOLER_OFF_REASON = "The cooler is already off";

/** The two block NOTICES the screen prints under the controls. They are not the
 *  same strings as the button reasons and must not be collapsed into them: the
 *  reason says why THIS press was refused, the notice says what is going on. */
export const POLAR_NOTICE = "Can't capture during polar alignment - stop alignment first.";
export function sequenceNotice(seqState: SequenceState["state"] | null): string | null {
  if (seqState === "paused") return "Sequence paused - camera reserved.";
  if (seqState === "running") return "Sequence running - camera reserved.";
  return null;
}

/** F.1's ruling, verbatim. There is no video/SER capture in this backend:
 *  `/api/capture{,/loop,/stop,/livestack/*}` is the whole camera surface and
 *  `RigStatus` carries `looping` and `live_stack_active` and nothing
 *  video-shaped. The control is drawn because the design draws it, and it
 *  EXPLAINS instead of acting. */
export const VIDEO_LOCK_REASON =
  "Video capture is not available on this rig yet - the camera surface here is "
  + "single frames, a loop and Live View stacking.";

/** UX-28's bounds. A set-point outside these is a typo, not an intent. */
export const COOLER_MIN_C = -60;
export const COOLER_MAX_C = 40;
export const COOLER_RANGE_REASON =
  `Target must be a number between ${COOLER_MIN_C} and ${COOLER_MAX_C} °C`;

export const EXPOSURE_INVALID_MESSAGE = "Exposure must be 0-3600s";
export function gainInvalidMessage(maxGain: number | null | undefined): string {
  return `Gain must be 0-${maxGain && maxGain > 0 ? maxGain : "max"}`;
}

// -------------------------------------------------------------- the predicates

/** The gain guard, on the RAW string, mirroring the exposure guard (UX-43): a
 *  blank or out-of-range gain must BLOCK the capture, not silently POST 0. */
export function isGainInvalid(raw: string, maxGain: number | null | undefined): boolean {
  const n = Number(raw);
  return raw.trim() === "" || !Number.isFinite(n) || n < 0
    || (!!maxGain && maxGain > 0 && n > maxGain);
}

/** A cooler set-point the panel may send: finite, in range, not blank. */
export function isCoolerTargetInvalid(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() === "" || !Number.isFinite(n) || n < COOLER_MIN_C || n > COOLER_MAX_C;
}

function slice(inp: CaptureGateInput) {
  return {
    principal: inp.principal,
    status: inp.status,
    equipConnected: inp.equipConnected,
    wsPhase: inp.wsPhase,
  };
}

/** Link down -> capability -> camera not connected. The floor under every
 *  control on this screen, including the ones that do not expose (Stop, the
 *  cooler, the dew heater, the wheel), because none of them can reach a rig
 *  this browser cannot talk to either. */
export function accessReason(inp: CaptureGateInput): string | null {
  const link = lockReason({}, slice(inp));
  if (link) return link;                        // "the rig is not reachable"
  if (!capAllowed(inp.principal, "control.capture")) return NEEDS_CAPTURE_REASON;
  return lockReason({ needsRole: "camera" }, slice(inp)); // "connect a camera first"
}

/** The aim button's own gate. NOT `accessReason`: slewing is `control.mount`
 *  and needs the TELESCOPE connected, and a `control.capture` holder who cannot
 *  drive the mount must be told the truth about which one is missing. */
export function slewReason(
  inp: CaptureGateInput, opts: { runOwnsMount: boolean },
): string | null {
  const link = lockReason({}, slice(inp));
  if (link) return link;
  if (!capAllowed(inp.principal, "control.mount")) {
    return `Slewing needs ${accessPhrase("control.mount")}.`;
  }
  const role = lockReason({ needsRole: "telescope" }, slice(inp));
  if (role) return role;
  if (opts.runOwnsMount) return SESSION_OWNS_MOUNT_REASON;
  return null;
}

export const SESSION_OWNS_MOUNT_REASON = "A session owns the mount - pause it first.";

/** Shared by every control that STARTS an exposure. */
export function exposeReason(inp: CaptureGateInput): string | null {
  const access = accessReason(inp);
  if (access) return access;
  if (inp.polarBusy) return POLAR_REASON;
  // PAUSED still holds the camera between frames - it is not released back to
  // manual control, and racing it just 409s at the capture lock (r1 CAP-01).
  if (inp.seqState === "running" || inp.seqState === "paused") return SEQUENCE_REASON;
  if (isExposureInvalid(inp.exposureRaw)) return EXPOSURE_FIX_REASON;
  if (isGainInvalid(inp.gainRaw, inp.maxGain)) return GAIN_FIX_REASON;
  // A start the rig has accepted but not yet confirmed still owns the camera.
  if (inp.pending) return PENDING_REASON;
  return null;
}

/** Single adds one row Loop does not have: a running loop must be stopped
 *  first, because `/api/capture` would queue behind it. */
export function singleReason(inp: CaptureGateInput): string | null {
  return exposeReason(inp) ?? (inp.looping ? LOOP_RUNNING_REASON : null);
}

export function loopReason(inp: CaptureGateInput): string | null {
  return exposeReason(inp);
}

/** Live View's OFF half is deliberately NOT gated on `pending` (CaptureView's
 *  own comment: gating the stop path would leave a lit toggle that silently did
 *  nothing for six seconds). Ending a stack is never wrong. */
export function liveViewReason(inp: CaptureGateInput, liveStackOn: boolean): string | null {
  if (liveStackOn) return accessReason(inp);
  return exposeReason(inp);
}

/** Stop only needs the access floor: it must stay pressable while the very
 *  conditions the other gates refuse on (a loop, a pending start) are true. */
export function stopReason(inp: CaptureGateInput): string | null {
  return accessReason(inp);
}

export function resetStackReason(inp: CaptureGateInput, liveStackOn: boolean): string | null {
  return accessReason(inp) ?? (liveStackOn ? null : NO_STACK_REASON);
}

export function coolerReason(inp: CaptureGateInput, targetRaw: string): string | null {
  return accessReason(inp) ?? (isCoolerTargetInvalid(targetRaw) ? COOLER_RANGE_REASON : null);
}

/** While a ramp runs the cooler is still ON (its set-point is climbing), so the
 *  old "already off" test left Warm live and re-pressable. During a ramp the
 *  slot becomes STOP RAMP instead, so this only covers the genuinely-idle case. */
export function warmReason(
  inp: CaptureGateInput,
  opts: { rampActive: boolean; coolerPresent: boolean; coolerOn: boolean },
): string | null {
  const access = accessReason(inp);
  if (access) return access;
  if (!opts.rampActive && opts.coolerPresent && !opts.coolerOn) return COOLER_OFF_REASON;
  return null;
}
