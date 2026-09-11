// QuickActions.tsx - COOL/WARM and PARK/UNPARK, the two things worth doing
// from the device LIST rather than from inside a sheet (plan A.8).
//
// THREE THINGS THIS FILE IS CAREFUL ABOUT.
//
//  1. PARK IS NOT GATED ON THE MOTION IT ENDS. It is the abort: it takes a slew
//     over and stows it. Gating it on the `goto` lane - the obvious reading of
//     "don't let two motions overlap" - would remove the escape hatch exactly
//     when it is needed, which is a bug the inventory records three separate
//     times. Only a park ALREADY IN FLIGHT blocks it, and the title says what a
//     second press would do instead of hiding it.
//  2. A CAMERA THAT CANNOT COOL GETS NO COOL BUTTON. `can_cool === false` is a
//     real answer from the driver, so the pair renders an inert NO COOLER chip
//     rather than a button that would be refused - and the row keeps its shape
//     instead of reflowing to one control.
//  3. THE LANE IS ARMED ON PRESS. The status frame is 2 s, so a control driven
//     only by `busy_lanes` looks dead for two seconds after a tap - which is
//     what made people press twice. `useBusyOrPending` covers the gap AND
//     expires, so a refused request cannot strand the button.

import type { JSX } from "react";
import { ActionButton, Pill } from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { api } from "../../../../api";
import { useBusyOrPending } from "../../../../lib/useBusy";
import { useConfig, useSequence, useStatus, useStore } from "../../../../store";
import type { CoolingConfig } from "../../../../types";
import { FLOW_OWNS_CAMERA, FLOW_OWNS_CAMERA_PAUSED } from "../sheets/camera";
import { FLOW_OWNS_MOUNT } from "../sheets/mount";

const PARKING_TITLE =
  "Parking - 30-60 s. Pressing again would cancel this park and start another.";
const FOREIGN_MOTION_TITLE =
  "The mount is already moving. Park takes that move over and stows it - and if "
  + "the move is itself a park, this restarts it.";

/** The shipped stop-ramp confirmation (views/CaptureView.tsx). Quoted, so its
 *  em-dash stays. */
const RAMP_STOPPED = "Warm ramp stopped - cooler off";

export function QuickActions(): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const seqState = useSequence()?.state;
  const flowOwns = seqState === "running" || seqState === "paused";

  const cam = status?.camera;
  const mount = status?.mount;

  // `setpoint_c` rides on the config block at runtime but is deliberately not
  // in `CoolingConfig` (the panel that edits the ramp must not write it - see
  // api/backends.ts setCoolingConfig). Read it defensively, then fall back to
  // what the camera is actually holding, then to the shipped default.
  const cooling = config?.cooling as (CoolingConfig & { setpoint_c?: number | null }) | undefined;
  const setpoint = typeof cooling?.setpoint_c === "number"
    ? cooling.setpoint_c
    : typeof cam?.cooler?.target_c === "number" ? cam.cooler.target_c : -10;
  const warmRate = typeof cooling?.warm_rate_c_per_min === "number"
    ? cooling.warm_rate_c_per_min : 2.0;

  const warming = !!cam?.warm?.active;
  const coolerOn = !!cam?.cooler?.on;

  const capture = useBusyOrPending("capture");
  const park = useBusyOrPending("park");
  const goto = useBusyOrPending("goto");

  const coolLock = useLock({
    cap: "control.capture",
    needsRole: "camera",
    busyLane: "capture",
    extra: flowOwns ? (seqState === "paused" ? FLOW_OWNS_CAMERA_PAUSED : FLOW_OWNS_CAMERA) : null,
  });
  // PARK: no `busyLane` here on purpose (see note 1). The park lane is checked
  // below as its own `extra`, which is the ONE motion that should stop it.
  const parkLock = useLock({
    cap: "control.mount",
    needsRole: "telescope",
    extra: park.busy ? PARKING_TITLE : null,
  });
  const unparkLock = useLock({
    cap: "control.mount",
    needsRole: "telescope",
    busyLane: "goto",
    extra: park.busy
      ? PARKING_TITLE
      : flowOwns ? FLOW_OWNS_MOUNT : null,
  });

  const toast = (level: string, message: string, opts?: { verbatim?: boolean }) =>
    useStore.getState().showToast(level, message, opts);
  const explain = (reason: string) => toast("warning", reason);

  const coolLabel = warming
    ? "STOP RAMP"
    : coolerOn ? "WARM CAMERA" : `COOL TO ${setpoint}°C`;

  const pressCool = () => void (async () => {
    capture.arm();
    try {
      if (warming) {
        await api.post("/api/camera/cooler", { on: false, ramp: false });
        toast("warning", RAMP_STOPPED);
      } else if (coolerOn) {
        await api.post("/api/camera/cooler", { on: false });
        toast("warning",
          `Warming at ${warmRate}°C/min - the capture gate closes until it is cold again.`);
      } else {
        await api.post("/api/camera/cooler", { on: true, target_c: setpoint });
        toast("info",
          `Cooling to ${setpoint}°C - ramp limited, capture unlocks when stable.`);
      }
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "the cooler refused", { verbatim: true });
    }
  })();

  const parked = !!mount?.parked;
  const pressPark = () => void (async () => {
    park.arm();
    try {
      await api.post(parked ? "/api/mount/unpark" : "/api/mount/park");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "the mount refused", { verbatim: true });
    }
  })();

  const parkTitle = park.busy
    ? PARKING_TITLE
    : !parked && (goto.busy || mount?.slewing) ? FOREIGN_MOTION_TITLE : undefined;

  return (
    <div style={{ display: "flex", gap: 8, flexShrink: 0 }} data-testid="quick-actions">
      {cam?.can_cool === false ? (
        <span style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Pill tone="dim" data-testid="no-cooler">NO COOLER</Pill>
        </span>
      ) : (
        <span style={{ flex: 1, display: "flex" }}>
          <ActionButton
            kind="secondary"
            full
            data-testid="quick-cool"
            busy={capture.busy}
            lockedReason={coolLock.lockedReason}
            onExplain={explain}
            onPress={pressCool}
          >
            {coolLabel}
          </ActionButton>
        </span>
      )}

      <span style={{ flex: 1, display: "flex" }} title={parkTitle}>
        <ActionButton
          kind="secondary"
          full
          data-testid="quick-park"
          busy={park.busy}
          lockedReason={parked ? unparkLock.lockedReason : parkLock.lockedReason}
          onExplain={explain}
          onPress={pressPark}
        >
          {parked ? "UNPARK" : "PARK MOUNT"}
        </ActionButton>
      </span>
    </div>
  );
}
