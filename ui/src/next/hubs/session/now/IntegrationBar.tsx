// IntegrationBar.tsx - one bar, segment per filter, width = planned time.
//
// The segment WIDTHS are planned seconds and the FILLS are accepted frames, so
// the shape of the bar is the night's shape: a wide dim L segment beside three
// narrow full ones says the luminance is the thing that is behind, which is the
// decision this bar exists to support.
//
// ONE SEGMENT PER DISTINCT STEP, not per target. `compile_plan` copies every
// capture step onto every target in a pool and only one of them is shot on a
// given night, so summing per target promises four times the integration the
// rig can deliver - the direction of error that costs a project a week. The
// server's own `_budget` makes exactly this cut.
//
// `progress.rejected` IS NOT A LOSS, and the line under the legend says so in
// the words the Monitor already uses. A rejected frame is still on disk and can
// be regraded; presenting it as damage would be wrong twice over.
//
// WITHOUT A SESSION LEDGER there is no per-filter breakdown to be had, so the
// bar falls back to frames_done/frames_total as ONE segment AND SAYS SO. A
// single-colour bar that looked like the segmented one would be a claim about
// filters nobody made.

import type { JSX } from "react";

import { fmtIntegration } from "../../../../api/sessionStack";
import { useSeq } from "../../../../store";
import { Bar, Label, Mono } from "../../../ui";
import type { BarSegment } from "../../../ui";
import { acceptedByFilter, filterColor, plannedByFilter, tonightNightKey } from "./filters";
import { useActiveSession } from "./sessionData";

export function IntegrationBar({ legend = true }: { legend?: boolean }): JSX.Element | null {
  const seq = useSeq();
  const { session } = useActiveSession();
  const progress = seq.progress;

  const planned = plannedByFilter(session?.plan);
  const accepted = acceptedByFilter(session, tonightNightKey(session));
  const exposureOf = new Map<string, number>();
  for (const t of session?.plan?.targets ?? []) {
    for (const s of t.steps ?? []) {
      if (!exposureOf.has(s.filter || "-")) exposureOf.set(s.filter || "-", s.exposure_s);
    }
  }

  const hasLedger = planned.length > 0 && session != null;

  let segments: BarSegment[];
  let doneS: number;
  let plannedS: number;

  if (hasLedger) {
    segments = planned.map((p) => {
      const done = Math.min(p.count, accepted.get(p.filter) ?? 0);
      return {
        frac: Math.max(1, p.plannedS),
        fill: p.count > 0 ? done / p.count : 0,
        color: filterColor(p.filter),
        label: `${p.filter} ${done}/${p.count}`,
      };
    });
    plannedS = planned.reduce((a, p) => a + p.plannedS, 0);
    doneS = planned.reduce(
      (a, p) => a + Math.min(p.count, accepted.get(p.filter) ?? 0) * (exposureOf.get(p.filter) ?? 0), 0);
  } else {
    if (!progress || progress.frames_total <= 0) return null;
    segments = [{
      frac: 1,
      fill: progress.frames_done / progress.frames_total,
      color: "var(--accent)",
      label: `${progress.frames_done}/${progress.frames_total} frames`,
    }];
    const exp = progress.current_exposure_s ?? 0;
    doneS = progress.frames_done * exp;
    plannedS = progress.frames_total * exp;
  }

  return (
    <div
      data-testid="now-integration"
      style={{
        border: "1px solid var(--line)", borderRadius: 16, padding: "12px 14px",
        background: "var(--bg-panel)", display: "flex", flexDirection: "column", gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <Label>INTEGRATION</Label>
        <Mono size={10} tone="dim">
          {fmtIntegration(doneS)} of {fmtIntegration(plannedS)} planned
        </Mono>
      </div>
      <Bar
        height={14}
        segments={legend ? segments : segments.map((s) => ({ ...s, label: undefined }))}
        label="Integration by filter"
        data-testid="integration-bar"
      />
      {!hasLedger && (
        <Mono size={10} tone="dim">per-filter breakdown needs the session ledger</Mono>
      )}
      {progress != null && progress.rejected > 0 && (
        <span data-testid="now-rejected-line">
          <Mono size={10} tone="dim">
            {progress.rejected} flagged (HFR/cloud check - frames kept)
          </Mono>
        </span>
      )}
    </div>
  );
}
