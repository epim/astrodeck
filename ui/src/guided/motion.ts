// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
import { api, ApiError } from "../api";
import { POSITION_UNKNOWN_CODE, positionKnown } from "../lib/slewController";
import { useStore } from "../store";
import { atPosition, equipmentBlocker, type SkyPosition } from "./setup";
import { ACTIVE_SEQUENCE } from "./model";

/** Check ownership immediately before a new guided command. The server still arbitrates. */
export function motionBlocker(s = useStore.getState()): string | null {
  if(ACTIVE_SEQUENCE.has(s.sequence.state))return "An imaging session is using the equipment. Finish or stop it before continuing.";
  if(["running","paused","pausing"].includes(s.polar.state))return "Finish or stop polar alignment before continuing.";
  if(s.focus?.state==="running"||s.filterOffsetsLearn?.state==="running")return "Wait for focus calibration to finish before continuing.";
  if(s.status?.mount?.slewing)return "Wait for the telescope to stop moving before continuing.";
  if(s.status?.looping)return "Stop the camera's exposure loop before continuing.";
  if(s.status?.busy || s.status?.busy_lanes?.length)return "The equipment is working on another task. Wait for it to finish before continuing.";
  return null;
}

/** Wait for telemetry that arrived after the request; HTTP acceptance is not arrival. */
export async function slewAndWait(target: SkyPosition, center: boolean, signal: AbortSignal) {
  if (signal.aborted) throw new Error("The move was cancelled before it started.");
  const reason = equipmentBlocker() ?? motionBlocker(); if (reason) throw new Error(reason);
  const before = useStore.getState().status;
  let observedStart=false;
  const unsubscribe=useStore.subscribe(state=>{
    if(state.status?.mount?.slewing || state.status?.busy_lanes?.includes("goto") ||
       (center && before?.mount?.pointing?.verified && state.status?.mount?.pointing?.verified===false)) observedStart=true;
  });
  try {
    await api.post("/api/mount/goto", {...target,center,force:false});
    const deadline = Date.now()+180000;
    while (!signal.aborted && Date.now()<deadline) {
      await new Promise(resolve => setTimeout(resolve,500));
      if (signal.aborted) break;
      const state = useStore.getState();
      if (equipmentBlocker(state)) throw new Error("The live connection was lost. Check the mount before trying again.");
      const mount = state.status?.mount;
      if (state.status !== before && mount && !mount.slewing && !mount.parked && !state.status?.busy_lanes?.includes("goto") && atPosition(mount,target) &&
          (observedStart || (!atPosition(before?.mount,target) && (!center || !before?.mount?.pointing?.verified)))) {
        if (!center || mount.pointing?.verified) return;
      }
      // The route answered {started} before the hub's own motion seam asked the
      // position gate again (#888): a latch set while the move waited for the
      // motion lock (an AM5 link reopen that read the home pole) sends nothing
      // and leaves this loop waiting for an arrival that cannot come (#986). The
      // status says so at once; end the wait in the refusal the pages already
      // recognise, so they show the attestation now, not after the deadline.
      if (!positionKnown(mount)) throw new ApiError("The mount no longer knows where the telescope points, so this move can't be confirmed.", 409, false, POSITION_UNKNOWN_CODE);
    }
    throw new Error(signal.aborted ? "Waiting stopped. Check the mount's current state." : "The telescope hasn't confirmed arrival. Check the mount before continuing.");
  } finally { unsubscribe(); }
}
