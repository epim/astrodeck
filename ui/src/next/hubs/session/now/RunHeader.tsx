// RunHeader.tsx - what is being shot, and what the rig is doing about it.
//
// Two lines and a pill (plan section A.1; screenshot 10). Everything in it is
// read from the store by this component, and nothing is passed in but the
// density - so the phone screen and the desktop SessionColumn render the same
// derivation rather than two that can drift.
//
// THE KIND AND THE COUNT ARE THE RUN'S, NEVER THE STORE'S PLAN (#468). The
// server executes the copy of the plan it took at Run (`SequenceView.tsx:
// 1496-1499`), and that copy is the SESSION's frozen plan; the store's Plan is
// the classic editor's, which a flow run never touches. Both lines used to
// fall back on it whenever the session was not in hand, and after a flow's
// mosaic ended - when the engine clears `session` and `group` on its terminal
// publish - the column called a four-panel flow a "quick session". So the
// kind is the state's `group` ("mosaic") or the session plan's target count,
// and when neither is there the line names the run by its `plan_name` rather
// than guessing what kind it was. The target count is dropped, not guessed,
// on the same terms.
//
// A MOSAIC'S TARGET IS A PANEL OF N, NOT ITEM K OF N. A rotating mosaic visits
// every panel on every pass, so "M31 1-2 · 2 of 4" (the panel's index in the
// plan) read as a place in a queue that does not exist. The group names how
// many panels there are, and the line says the current one is one of them.
// Across a meridian wait the line names the mosaic and no panel, whatever the
// operator is served: the panel a waiting group names is the one it last shot
// or the one it hops to at the crossing, which timestamps a transit (spec 6.9;
// `runStage` in runCopy.ts keeps the same rule for the flow monitors).
//
// A PAUSED RUN HAS NO FINISH TIME, and the line says so in words instead of
// printing the last ETA the engine happened to publish.

import { Fragment, type JSX } from "react";

import { fmtClock, fmtDuration } from "../../../../lib/eta";
import { formatScheduleStatus } from "../../../../lib/scheduleStatus";
import { useSeq, useStatus, useStore } from "../../../../store";
import type { SequenceGroupState, SequenceState } from "../../../../types";
import { Mono, StatusPill } from "../../../ui";
import { phaseOf } from "./phase";
import { useActiveSession } from "./sessionData";
import { useCampaign } from "./useCampaign";
import { useEta } from "./useEta";
import { useNowIncidents, useNowMs } from "./useNowIncidents";

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

type RunLineState = Pick<SequenceState, "target" | "plan_name" | "target_index" | "group">;

/** The group when the state carries one, else null. */
function groupOf(seq: RunLineState): SequenceGroupState | null {
  return seq.group && typeof seq.group === "object" ? seq.group : null;
}

/** Whether the line may name the group's current panel: not across a meridian
 *  wait, and not before the group's first visit, when `panel` is null. */
function panelNamed(g: SequenceGroupState): boolean {
  return !g.meridian_wait && typeof g.panel === "string" && g.panel.trim() !== "";
}

/** What the run is OF, by name: the target the engine names, or the mosaic's
 *  own name while no panel may be named. */
export function runWhere(seq: RunLineState): string {
  const target = seq.target || seq.plan_name || "SESSION";
  const g = groupOf(seq);
  if (!g || panelNamed(g)) return target;
  return (typeof g.name === "string" && g.name.trim()) || target;
}

/** The TARGET LINE, from the sequence state and the session plan's target
 *  count (`sessionTargets`, null while no session is in hand). A mosaic's
 *  current panel is "one of N panels"; across a wait the mosaic is "N panels";
 *  a run of several targets outside a group is "k of N"; anything else is its
 *  name alone. */
export function runTargetLine(seq: RunLineState, sessionTargets: number | null): string {
  const where = runWhere(seq);
  const g = groupOf(seq);
  if (g) {
    const n = finite(g.panels_total) && g.panels_total > 1 ? g.panels_total : null;
    if (n == null) return where;
    return panelNamed(g) ? `${where} · one of ${n} panels` : `${where} · ${n} panels`;
  }
  if (sessionTargets != null && sessionTargets > 1) {
    return `${where} · ${(seq.target_index ?? 0) + 1} of ${sessionTargets}`;
  }
  return where;
}

/** The run's KIND, or null when the state and the session both say nothing
 *  (a run that has ended: the engine publishes `session=None` on every
 *  terminal state and drops `group` with `_group_active`). */
export function runKind(group: SequenceGroupState | null | undefined,
                        sessionTargets: number | null): string | null {
  if (group && typeof group === "object") return "mosaic";
  if (sessionTargets == null) return null;
  return sessionTargets > 1 ? "night plan" : "quick session";
}

export function RunHeader({ compact = false }: { compact?: boolean }): JSX.Element {
  const seq = useSeq();
  const status = useStatus();
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
  const planTargets = session?.plan?.targets;
  const sessionTargets = Array.isArray(planTargets) ? planTargets.length : null;
  const group = groupOf(seq);
  const targetLine = campaign
    ? `${runWhere(seq)} · campaign night ${campaign.night} of ~${campaign.totalNights}`
    : runTargetLine(seq, sessionTargets);

  // -------------------------------------------------------------- kind line
  const kind = campaign ? "campaign" : runKind(group, sessionTargets);
  const bits: string[] = [];
  if (kind) bits.push(kind);
  else if (seq.plan_name && seq.plan_name !== seq.target) bits.push(seq.plan_name);
  if (campaign) bits.push(campaign.scope);
  // The pass, only while the group may name one: it is withheld from a viewer
  // across a meridian wait, and not printed for an operator there either.
  if (group && !group.meridian_wait && finite(group.pass)) bits.push(`pass ${group.pass}`);
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
          {/* The kind line WRAPS between its parts and never cuts one. Its last
              part is the finish time, and in the desktop column, beside the
              pill, an ellipsis took exactly that off "mosaic · pass 1 · 1m 13s
              · finishes ~19:43" (#468). */}
          <div data-testid="now-kind-line">
            <Mono size={10} tone="dim">
              {bits.map((b, i) => (
                <Fragment key={i}>
                  {i > 0 && " · "}
                  <span style={{ whiteSpace: "nowrap" }}>{b}</span>
                </Fragment>
              ))}
            </Mono>
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
