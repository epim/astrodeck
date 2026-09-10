// RunHeader.tsx - what is being shot, and what the rig is doing about it.
//
// Two lines and a pill (plan section A.1; screenshot 10). Everything in it is
// read from the store by this component, and nothing is passed in but the
// density - so the phone screen and the desktop SessionColumn render the same
// derivation rather than two that can drift.
//
// THE TARGET LINE COUNTS THE PLAN THE ENGINE IS RUNNING, not the one in the
// editor. The server executes the copy of the plan it took at Run
// (`SequenceView.tsx:1496-1499`), so "1 of 3" off the local plan would be a
// count of something else entirely the moment anyone edits it. When the two
// cannot be shown to be the same plan, the suffix is DROPPED rather than
// guessed.
//
// A PAUSED RUN HAS NO FINISH TIME, and the line says so in words instead of
// printing the last ETA the engine happened to publish.

import type { JSX } from "react";

import { fmtClock, fmtDuration } from "../../../../lib/eta";
import { formatScheduleStatus } from "../../../../lib/scheduleStatus";
import { usePlan, useSeq, useStatus, useStore } from "../../../../store";
import { Mono, StatusPill } from "../../../ui";
import { phaseOf } from "./phase";
import { useActiveSession } from "./sessionData";
import { useCampaign } from "./useCampaign";
import { useEta } from "./useEta";
import { useNowIncidents, useNowMs } from "./useNowIncidents";

/** The plan the ENGINE is running, and how many targets are in it - or null
 *  when this browser cannot show the two are the same plan. */
export function runningTargetCount(
  sessionTargets: number | null, localName: string, runningName: string | undefined,
  localTargets: number,
): number | null {
  if (sessionTargets != null) return sessionTargets;
  if (runningName && localName && runningName === localName) return localTargets;
  return null;
}

export function RunHeader({ compact = false }: { compact?: boolean }): JSX.Element {
  const seq = useSeq();
  const status = useStatus();
  const plan = usePlan();
  const focusRunning = useStore((s) => s.focus?.state === "running");
  const { session } = useActiveSession();
  const { campaign } = useCampaign();
  const { incidents } = useNowIncidents();
  const eta = useEta();
  const nowMs = useNowMs(30_000);

  const phase = phaseOf({
    state: seq.state,
    detail: seq.detail,
    scheduleState: seq.schedule?.state ?? null,
    busyLanes: status?.busy_lanes ?? [],
    focusRunning,
    frameStartedAtMs: seq.progress?.frame_started_at_ms ?? null,
    incident: incidents.length ? { pill: incidents[0].pill, color: incidents[0].color } : null,
  });

  // ------------------------------------------------------------ target line
  const target = seq.target || seq.plan_name || "SESSION";
  let targetLine = target;
  if (campaign) {
    targetLine = `${target} · campaign night ${campaign.night} of ~${campaign.totalNights}`;
  } else {
    const count = runningTargetCount(
      session?.plan?.targets?.length ?? null, plan.name, seq.plan_name, plan.targets.length,
    );
    if (count != null && count > 1) {
      targetLine = `${target} · ${(seq.target_index ?? 0) + 1} of ${count}`;
    }
  }

  // -------------------------------------------------------------- kind line
  const multiTarget = (session?.plan?.targets?.length ?? plan.targets.length) > 1;
  const kind = campaign ? "campaign" : multiTarget ? "night plan" : "quick session";
  const bits: string[] = [kind];
  if (campaign) bits.push(campaign.scope);
  if (seq.progress) bits.push(fmtDuration(seq.progress.elapsed_s));
  if (eta.paused) {
    bits.push("no ETA while paused");
  } else if (eta.finishAtMs != null) {
    bits.push(`finishes ${eta.confident ? "" : "~"}${fmtClock(eta.finishAtMs, nowMs)}`);
  }

  // The engine's own "why nothing is happening" sentence, under the pill. It is
  // the ScheduleChip and the Monitor's wait line, kept.
  const wait = formatScheduleStatus(seq.schedule, seq.live, Math.floor(nowMs / 1000));

  return (
    <div data-testid="now-run-header" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8,
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
          <div
            className="nx-display"
            data-testid="now-target-line"
            style={{
              fontSize: compact ? 13 : 15, letterSpacing: ".1em",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}
          >
            {targetLine}
          </div>
          <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            <Mono size={10} tone="dim">{bits.join(" · ")}</Mono>
          </div>
        </div>
        <StatusPill
          text={phase.text}
          tone={phase.tone}
          color={phase.color}
          pulse={phase.pulse}
          data-testid="now-phase-pill"
        />
      </div>
      {wait && (
        <div data-testid="now-wait-line">
          <Mono size={10} tone={wait.tone === "warn" ? "warn" : "dim"}>{wait.text}</Mono>
        </div>
      )}
    </div>
  );
}
