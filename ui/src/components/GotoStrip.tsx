/* GotoStrip — a goto is not a stuck button (2026-08-07).

   A goto is a slew plus up to three solve→sync→re-slew rounds: up to 3 s of
   shutter and 5-15 s of ASTAP per round, shown until now as nothing but a
   busy button. The server narrates the shared solve path on the `mount`
   channel (hub.solve_and_sync) and the centering loop publishes its attempt
   ordinals and its inert-mount verdict; this strip renders all of it, sticky,
   on the screens that launch gotos (Atlas, Mount).

   The stuck verdict outranks everything and is deliberately loud: two
   bit-identical residuals mean the mount is NOT EXECUTING SLEWS, and on
   2026-08-06 that fact lived only in a log line while an operator kept
   working a mount that was not moving. It stays until the next goto starts
   (the store clears it on the next `centering` event).

   Renders nothing when there is nothing in flight — both screens keep their
   idle layouts. */
import type { JSX } from "react";
import { useStore, useStatus } from "../store";
import { useBusy } from "../lib/useBusy";
import ActivityRing from "./ui/ActivityRing";
import { Led } from "./ui";

export default function GotoStrip(): JSX.Element | null {
  const mountOp = useStore((s) => s.mountOp);
  const gotoBusy = useBusy("goto");
  const slewing = useStatus()?.mount?.slewing === true;

  const stuck = mountOp?.stuck === true;
  const live = gotoBusy || slewing || (mountOp?.activity != null);
  if (!live && !stuck) return null;

  if (stuck) {
    return (
      <div className="sticky top-0 z-20 -mx-1 px-1">
        <div className="flex items-start gap-3 border border-bad bg-bad/10 px-4 py-3 rounded"
          role="alert">
          <Led state="bad" label="mount not moving" />
          <div className="min-w-0">
            <p className="text-bad text-sm font-medium">
              The mount is not executing slews
            </p>
            <p className="text-dim text-xs mt-0.5">
              Two centering attempts landed
              {mountOp?.error_arcmin != null
                ? ` ${mountOp.error_arcmin.toFixed(1)}′ off target` : " off target"},
              unchanged — the correction slew changed nothing. Check the mount is
              unparked, tracking, and clear of its limits, then slew again.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const activity = mountOp?.activity;
  const text = activity === "exposing" ? "capturing…"
    : activity === "solving" ? "solving…"
      : slewing ? "slewing…"
        : mountOp?.attempt ? `centering ${mountOp.attempt}/3` : "goto…";

  return (
    <div className="sticky top-0 z-20 -mx-1 px-1" data-goto-strip>
      <div className="border border-line rounded bg-panel/95 backdrop-blur px-3 py-2
                      flex items-center gap-3 min-h-10">
        <span className="inline-block w-2 h-2 rounded-full shrink-0 bg-accent blink" />
        <span className="text-[11px] tracking-widest uppercase text-dim truncate"
          aria-live="polite">
          {text}
        </span>
        {mountOp?.attempt != null && activity != null && (
          <span className="text-[10px] text-faint mono">
            attempt {mountOp.attempt}/3
          </span>
        )}
        <span className="ml-auto">
          {activity != null && (
            <ActivityRing
              mode={activity === "exposing" ? "fill" : "orbit"}
              seconds={mountOp?.exposure_s ?? 3}
              word={activity === "exposing" ? "capturing" : "solving"}
              resetKey={activity}
            />
          )}
        </span>
      </div>
    </div>
  );
}
