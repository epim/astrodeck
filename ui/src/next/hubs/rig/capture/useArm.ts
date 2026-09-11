// useArm.ts - the acceptance-gated progress latch, lifted from CaptureView.tsx
// (610-646 and the four effects around it) with its guarantees intact.
//
// THE ONE RULE THIS FILE EXISTS FOR: the bar is armed only AFTER the server has
// ACCEPTED the exposure, never before. A rejected request then has nothing to
// unwind. It used to be the other way round, and a 409 deliberately left the bar
// running on the theory that a 409 always means "some capture is genuinely in
// flight" - it does not: `hub.require("camera")` raises DeviceError and `_err`
// maps THAT to 409 too, so "no camera connected" is byte-identical at the client
// to "a capture is already running". Measured on a rejected Single: "Exposing"
// for the exposure, then a striped download bar for the full 60 s watchdog, for
// a frame that never existed.
//
// The other three behaviours that came with it, none optional:
//
//  * The progress FSM is LOCAL, not server truth (idle -> exposing ->
//    downloading -> idle, 100 ms tick), because the capture POST is
//    fire-and-forget and the only honest completion signal the client has is a
//    NEW live-preview id arriving in the store. It is retired by that id or by
//    the DOWNLOAD_WATCHDOG_MS ceiling - without which a dropped readout spins
//    the striped bar forever (UX-30).
//  * EXTERNAL-ABORT detection: the rig publishes `capture`/`looping` in
//    `busy_lanes`, and the DISAPPEARANCE of that lane is the honest end of a
//    frame stopped somewhere else. "Seen" is per-exposure AND per-frame, which
//    takes two guards: `arm` clears the flag on acceptance, and the status frame
//    already on screen when we posted cannot re-arm it (identity compare on the
//    WHOLE status object - store.ts replaces it by reference every poll and
//    leaves it alone for guide/focus ticks, so `status === armStatusRef.current`
//    is exactly "no status frame has arrived since the POST was accepted").
//  * The loop / Live View "starting" latch OUTLIVES the POST by up to
//    START_CONFIRM_GRACE_MS or until the rig confirms, because a second tap is
//    not a harmless duplicate: `/api/capture/loop` CANCELS the exposure in
//    progress (a 300 s sub silently thrown away) and a second Live View tap
//    assigns a brand-new LiveStacker, discarding the accumulated stack that is
//    the whole point of the feature. Single's latch drops on acceptance because
//    from there it has local truth (`phase`).
//
// COUNT is this screen's own addition and is handled here rather than by the
// caller, because it can only be sequenced off the same completion signal:
// `/api/capture` takes no count, so N frames are N POSTs, each fired when the
// previous one has landed. Firing them together would just queue behind the
// capture lock and 409.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../../api";
import { useStore, useStatus, useLivePreviewId } from "../../../../store";
import { useBusyLanes } from "../../../../lib/useBusy";
import type { ArmKind } from "./captureGate";

export type CapturePhase = "idle" | "exposing" | "downloading";

/** UX-30: generous enough for a slow USB2 full-frame readout, short enough that
 *  a genuine stall surfaces instead of hanging. */
export const DOWNLOAD_WATCHDOG_MS = 60_000;
/** How long the click-side "starting" latch may outlive the POST. */
export const START_CONFIRM_GRACE_MS = 6000;

export const EXTERNAL_ABORT_MESSAGE =
  "Capture stopped on the rig - this exposure ended without a frame.";
export const READOUT_TIMEOUT_MESSAGE =
  "Frame readout timed out - no image arrived. The camera may have dropped the frame.";

export interface ArmController {
  phase: CapturePhase;
  /** Which verb is waiting for the camera to answer, or null. */
  pending: ArmKind | null;
  /** Seconds into the exposure now on screen. */
  elapsed: number;
  /** 0..100 for the bar; 100 through the indeterminate download phase. */
  fillPct: number;
  /** Seconds left in the exposure now on screen. */
  remaining: number;
  /** The exposure length of the frame on screen. */
  exposureLen: number;
  /** Which frame of a COUNT batch is running, 1-based, or null. */
  batch: { index: number; total: number } | null;
  inFlight: boolean;
  /** Post, and arm ONLY on acceptance. `count` > 1 sequences further POSTs off
   *  the completion signal. */
  arm: (kind: ArmKind, path: string, payload: object, len: number, count?: number) => Promise<void>;
  /** Stop's half: void any accept still in flight, drop the latch and the bar,
   *  and abandon the rest of a batch. */
  drop: () => void;
}

export function useArm(opts: {
  /** Push every pending draft into the store before the shot, so what the
   *  shutter uses and what the rest of the app can see are the same numbers. */
  commitDrafts: () => void;
  /** A frame LANDED (a new live-preview id while a capture was on screen). */
  onFrameLanded?: () => void;
}): ArmController {
  const status = useStatus();
  const liveId = useLivePreviewId();
  const showToast = useStore((s) => s.showToast);
  const looping = !!status?.looping;
  const liveStackOn = !!status?.live_stack_active;

  const [phase, setPhase] = useState<CapturePhase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [pending, setPending] = useState<ArmKind | null>(null);
  const [batch, setBatch] = useState<{ index: number; total: number } | null>(null);

  const expStartRef = useRef(0);
  const expLenRef = useRef(1);
  const tickRef = useRef<number | null>(null);
  const armGenRef = useRef(0);
  const sawCaptureLane = useRef(false);
  const armStatusRef = useRef<unknown>(null);
  const statusRef = useRef(status);
  statusRef.current = status;
  /** The rest of a COUNT batch: the same body, re-fired when a frame lands. */
  const batchRef = useRef<
    {
      left: number; total: number; index: number;
      path: string; payload: object; len: number;
    } | null
  >(null);
  const landedRef = useRef(opts.onFrameLanded);
  landedRef.current = opts.onFrameLanded;
  const commitRef = useRef(opts.commitDrafts);
  commitRef.current = opts.commitDrafts;

  const beginExposure = useCallback((len: number) => {
    expStartRef.current = typeof performance !== "undefined" ? performance.now() : Date.now();
    expLenRef.current = Math.max(0.1, len);
    setElapsed(0);
    setPhase("exposing");
  }, []);

  /** Post, and arm ONLY on acceptance. `plan` is what is left of a COUNT batch
   *  AFTER this frame, carried through the POST rather than left in the ref: the
   *  ref is written on ACCEPTANCE, so a refusal mid-batch abandons the batch for
   *  the same reason it draws no bar - nothing was armed. */
  const armCore = useCallback(async (
    kind: ArmKind, path: string, payload: object, len: number,
    plan: { left: number; total: number; index: number } | null,
  ) => {
    commitRef.current();
    const gen = ++armGenRef.current;
    setPending(kind);
    try {
      await api.post(path, payload);
    } catch (e) {
      showToast("error", (e as Error).message, { verbatim: true });
      setPending(null);
      batchRef.current = null;
      setBatch(null);
      return; // nothing was armed - never draw progress for a refused exposure
    }
    if (armGenRef.current !== gen) { setPending(null); return; }
    // Cleared HERE, on acceptance, not in beginExposure: Loop restarts through
    // beginExposure once per frame, and forgetting the lane between frames would
    // leave a loop stopped on another device narrating nothing at all.
    sawCaptureLane.current = false;
    armStatusRef.current = statusRef.current;
    batchRef.current = plan ? { ...plan, path, payload, len } : null;
    setBatch(plan ? { index: plan.index, total: plan.total } : null);
    beginExposure(len);
    if (kind === "single") setPending(null);
  }, [beginExposure, showToast]);

  const arm = useCallback((
    kind: ArmKind, path: string, payload: object, len: number, count = 1,
  ) => armCore(kind, path, payload, len,
    kind === "single" && count > 1 ? { left: count - 1, total: count, index: 1 } : null),
  [armCore]);

  const drop = useCallback(() => {
    armGenRef.current++;      // void any accept still in flight (see `arm`)
    setPending(null);         // and drop the starting latch it may have raised
    setPhase("idle");
    batchRef.current = null;
    setBatch(null);
  }, []);

  // Hand the starting latch over to the rig, or expire it. If the rig never
  // says so - a start that failed after acceptance, a dropped link, an op that
  // ended inside one frame - the grace releases it rather than leaving the
  // control locked all night.
  useEffect(() => {
    if (pending == null || pending === "single") return;
    if (pending === "loop" && looping) { setPending(null); return; }
    if (pending === "live" && liveStackOn) { setPending(null); return; }
    const t = setTimeout(() => setPending(null), START_CONFIRM_GRACE_MS);
    return () => clearTimeout(t);
  }, [pending, looping, liveStackOn]);

  // The 10 Hz tick: advance `elapsed`; once it crosses the exposure length flip
  // to the indeterminate download phase and wait for the new-frame signal. We
  // never claim a percentage during readout - that would lie about a transfer
  // time the client cannot measure.
  useEffect(() => {
    if (phase !== "exposing") {
      if (tickRef.current != null) { clearInterval(tickRef.current); tickRef.current = null; }
      return;
    }
    const step = () => {
      const now = typeof performance !== "undefined" ? performance.now() : Date.now();
      const e = (now - expStartRef.current) / 1000;
      setElapsed(e);
      if (e >= expLenRef.current) setPhase("downloading");
    };
    tickRef.current = setInterval(step, 100) as unknown as number;
    return () => {
      if (tickRef.current != null) { clearInterval(tickRef.current); tickRef.current = null; }
    };
  }, [phase]);

  // Completion: a NEW live-preview id means the frame finished and decoded. We
  // seed the baseline on mount so an id already in the store cannot instantly
  // "complete" the first capture.
  const lastSeenIdRef = useRef<number | null>(liveId);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const loopingRef = useRef(looping);
  loopingRef.current = looping;
  useEffect(() => {
    if (liveId === lastSeenIdRef.current) return;
    lastSeenIdRef.current = liveId;
    if (phaseRef.current === "idle") return; // a frame from an unrelated source
    landedRef.current?.();
    if (loopingRef.current) { beginExposure(expLenRef.current); return; }
    const b = batchRef.current;
    if (b && b.left > 0) {
      // Cleared before the re-arm so a second frame landing inside the POST's
      // window cannot fire the same plan twice; `armCore` writes it back from
      // the plan it was handed, on acceptance.
      batchRef.current = null;
      // The next frame of the batch is a fresh POST through the SAME latch, so
      // a refusal mid-batch drops the bar exactly as the first one would.
      void armCore("single", b.path, b.payload, b.len,
        { left: b.left - 1, total: b.total, index: b.index + 1 });
      return;
    }
    batchRef.current = null;
    setBatch(null);
    setPhase("idle");
  }, [liveId, armCore, beginExposure]);

  // UX-30 watchdog on the download phase.
  useEffect(() => {
    if (phase !== "downloading") return;
    const t = setTimeout(() => {
      setPhase("idle");
      batchRef.current = null;
      setBatch(null);
      showToast("error", READOUT_TIMEOUT_MESSAGE);
    }, DOWNLOAD_WATCHDOG_MS);
    return () => clearTimeout(t);
  }, [phase, showToast]);

  // Kept for a server too old to publish `busy_lanes`; where the rig can say,
  // the lane effect below supersedes this and also covers the exposing phase.
  useEffect(() => {
    if (!looping && phaseRef.current === "downloading") setPhase("idle");
  }, [looping]);

  // ------------------------------------------------- external abort (UX #11)
  const lanes = useBusyLanes();
  const captureLane = lanes == null
    ? undefined
    : lanes.includes("capture") || lanes.includes("looping");
  useEffect(() => {
    if (captureLane === undefined) return;
    if (status === armStatusRef.current) return; // predates our POST
    if (captureLane) { sawCaptureLane.current = true; return; }
    if (!sawCaptureLane.current) return;
    sawCaptureLane.current = false;
    if (phase === "idle") return; // finished the honest way - a frame arrived
    setPhase("idle");
    batchRef.current = null;
    setBatch(null);
    showToast("info", EXTERNAL_ABORT_MESSAGE);
  }, [status, captureLane, phase, showToast]);

  useEffect(() => () => {
    if (tickRef.current != null) clearInterval(tickRef.current);
  }, []);

  const inFlight = phase !== "idle";
  const fillPct = phase === "exposing"
    ? Math.min(100, (elapsed / expLenRef.current) * 100)
    : 100;

  return {
    phase,
    pending,
    elapsed,
    fillPct,
    remaining: Math.max(0, expLenRef.current - elapsed),
    exposureLen: expLenRef.current,
    batch,
    inFlight,
    arm,
    drop,
  };
}
