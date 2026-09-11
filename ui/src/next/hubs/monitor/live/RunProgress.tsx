// RunProgress.tsx - how far along the run is, and when it ends.
//
// MONITOR > LIVE shipped without any of this (review #18). The semantics had
// moved to `hubs/session/now/{useEta,useSubFrame}`, but `SessionColumn` mounts
// those at tablet and desktop only - so on a PHONE, the device Monitor exists
// for, the 3 a.m. glance screen could not say how many frames were in, which
// target was being shot, or when the night's work would be done. The legacy
// `MonitorView` header + progress panel carried all of it
// (`MonitorView.tsx:645-666, 849-895`).
//
// THE TWO HOOKS ARE IMPORTED, NEVER RE-IMPLEMENTED. Both encode bugs already
// paid for and a second copy would drift off them:
//
//   useEta      anchors the countdown ONCE per server frame, so it neither
//               restarts every render nor drifts for the whole night, and it
//               refuses to print a finish time for a PAUSED run.
//   useSubFrame anchors the exposure at the frame boundary and ticks on the
//               CLIENT clock - re-deriving the skew every tick subtracts two
//               frozen numbers and cancels to zero, which is how the old
//               sub-frame bar read 0.0 s for five minutes. It also reports
//               `joinedMidFrame`, so an elapsed figure that is a FLOOR renders
//               as one.
//
// PAUSING IS NOT A WIRE STATE. The route returns the instant the flag is set
// while the exposure keeps running, so "pause requested" and "paused" are only
// distinguishable if the client works out how much shutter is left - which is
// exactly what tells someone whether they may walk out to the scope. Same rule,
// same words as `now/RunControls.tsx`.

import type { JSX } from "react";

import { fmtClock, fmtCountdown, fmtDuration } from "../../../../lib/eta";
import { formatScheduleStatus } from "../../../../lib/scheduleStatus";
import { useSeq, useStatus } from "../../../../store";
// The two hook MODULES, not `now/index.ts`: the barrel re-exports `NowScreen`
// and its whole graph, and Monitor needs neither.
import { useEta } from "../../session/now/useEta";
import { useSubFrame } from "../../session/now/useSubFrame";
import { Bar, Card, Label, Mono, Pill } from "../../../ui";

const LIVE_STATES = new Set(["running", "holding", "paused", "aborting"]);

/** The filter in front of the sensor right now, or null. A frame counter with
 *  no filter beside it was the legacy header's own omission-by-request
 *  (`MonitorView.tsx:605-609`); the wheel names its slots and the position
 *  indexes them. */
function filterWord(
  wheel: { position?: number; names?: string[] } | undefined,
): string | null {
  if (!wheel || wheel.position == null) return null;
  return wheel.names?.[wheel.position] ?? null;
}

export function RunProgress(): JSX.Element | null {
  const seq = useSeq();
  const status = useStatus();
  const eta = useEta(1000);

  const state = seq.state;
  const live = LIVE_STATES.has(state);
  const progress = seq.progress;

  const paused = state === "paused";
  const aborting = state === "aborting";
  const sub = useSubFrame(progress, paused);
  // PAUSING: the engine says paused, and a shutter is still open.
  const pausing = paused && sub.remainingS != null;

  // The wait line explains why imaging is NOT running (window closed, waiting
  // for altitude, a hold) and outranks the progress numbers when it is present,
  // so it renders even for a run the state machine calls idle.
  const wait = formatScheduleStatus(seq.schedule, seq.live, Date.now() / 1000);

  if (!live && !wait) return null;

  const filter = filterWord(status?.filterwheel);
  const target = seq.target || seq.plan_name || "no target named";

  // The finish slot. A cancelled run stops receiving an ETA, so the last one it
  // did send counts down to a completion that will never arrive; a paused run
  // has no honest finish at all. Both say what IS happening rather than going
  // blank, which reads as "over".
  const finish = (() => {
    if (aborting) return { big: "--:--", small: "stopping - no finish time", tone: "warn" as const };
    if (pausing) {
      return {
        big: fmtCountdown(sub.remainingS as number),
        small: "shutter open - this frame first",
        tone: "warn" as const,
      };
    }
    if (paused) return { big: "--:--", small: "no ETA while paused", tone: "warn" as const };
    if (eta.remainingS == null || eta.finishAtMs == null) {
      return { big: "--:--", small: "no ETA yet - the engine has not measured enough frames", tone: "dim" as const };
    }
    return {
      big: fmtCountdown(eta.remainingS),
      small: `${eta.confident ? "" : "~"}done ${fmtClock(eta.finishAtMs)}`,
      tone: "accent" as const,
    };
  })();

  return (
    <Card padding={12} data-testid="monitor-progress">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <Label>{pausing ? "PAUSING" : "RUN PROGRESS"}</Label>
            <Mono size={11} data-testid="monitor-progress-target">
              {target}
              {filter ? ` · ${filter}` : ""}
            </Mono>
          </div>
          {live && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
              <span
                style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: 20,
                  fontVariantNumeric: "tabular-nums",
                  color: finish.tone === "warn" ? "var(--warn)"
                    : finish.tone === "accent" ? "var(--accent)" : "var(--text-faint)",
                }}
                data-testid="monitor-progress-eta"
              >
                {finish.big}
              </span>
              <Mono size={10} tone="dim">{finish.small}</Mono>
            </div>
          )}
        </div>

        {pausing && (
          <Pill tone="warn" data-testid="monitor-pausing">
            {`pausing - ${fmtCountdown(sub.remainingS as number)} of shutter left, and the run does not stop until it lands`}
          </Pill>
        )}

        {progress && (
          <>
            <Bar value={Math.max(0, Math.min(1, progress.percent / 100))} height={6} tone="accent"
              label="Run progress" />
            {/* The exposure in flight. Kept up through PAUSING: the frame the
                pause interrupted is still being taken, and this is the only
                thing on screen that shows it running down. */}
            {sub.fraction != null && (
              <Bar value={sub.fraction} height={3} tone={pausing ? "warn" : "good"}
                label="Current exposure" />
            )}
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
              <Mono size={13} data-testid="monitor-progress-frames">
                {`${progress.frames_done}/${progress.frames_total} frames · ${progress.percent}%`}
              </Mono>
              <Mono size={10} tone="dim">
                {sub.elapsedLabel
                  ? `${sub.elapsedLabel} into this sub`
                  : `${fmtDuration(progress.elapsed_s)} elapsed`}
              </Mono>
            </div>
            {progress.rejected > 0 && (
              <Mono size={10} tone="dim">
                {`${progress.rejected} flagged (HFR/cloud check - frames kept)`}
              </Mono>
            )}
          </>
        )}

        {wait && (
          <Mono size={10.5} tone={wait.tone === "warn" ? "warn" : undefined}
            data-testid="monitor-progress-wait">
            {wait.text}
          </Mono>
        )}
      </div>
    </Card>
  );
}
