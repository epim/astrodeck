// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// IntegrationBar.tsx - one bar, segment per filter, width = planned time.
//
// The segment WIDTHS are planned seconds and the FILLS are accepted frames, so
// the shape of the bar is the night's shape: a wide dim L segment beside three
// narrow full ones says the luminance is the thing that is behind, which is the
// decision this bar exists to support.
//
// THE PLAN IS THE SESSION'S, the frozen copy the engine runs, and never the
// store's Plan, which is the classic editor's and knows nothing of a flow run
// (#468).
//
// ONE SEGMENT PER DISTINCT STEP, not per target, EXCEPT A MOSAIC'S PANELS.
// `compile_plan` copies every capture step onto every target in a pool and only
// one of them is shot on a given night, so summing per target promises four
// times the integration the rig can deliver - the direction of error that costs
// a project a week. The server's own `_budget` makes exactly this cut. A mosaic
// is the opposite case (`_budget`, spec S3 item 5): EVERY panel owes the step,
// each its own quota, so a panel's steps count in full, and are neither deduped
// against another target's nor used to dedupe one. Deduped as a pool, a 2x2 of
// 20 x 10 s per panel read "10s of 3m planned" for 80 x 10 s of work (#468).
//
// THE FILLS ARE WHAT THE SESSION HAS BANKED, over all its nights. The planned
// side is the session's whole quota, and a CONTINUE on night three measured
// against tonight's frames alone would read as a project that had not started.
//
// `progress.rejected` IS NOT A LOSS, and the line under the legend says so in
// the words the Monitor already uses. A rejected frame is still on disk and can
// be regraded; presenting it as damage would be wrong twice over.
//
// WITHOUT A SESSION LEDGER there is no per-filter breakdown to be had, so the
// bar falls back to frames_done/frames_total as ONE segment AND SAYS SO. A
// single-colour bar that looked like the segmented one would be a claim about
// filters nobody made. Its total counts SUBS, not seconds: the frame counter
// is all the state carries then, and the terminal publish of a run that has
// ended carries no exposure at all, which is how "0s of 0s planned" came to
// sit under a run that planned 80 x 10 s and banked one (#468).

import type { JSX } from "react";

import { fmtIntegration } from "../../../../api/sessionStack";
import { fmtCount } from "../../../../lib/gallery";
import { useSeq } from "../../../../store";
import type { SequencePlan } from "../../../../types";
import { Bar, Label, Mono } from "../../../ui";
import type { BarSegment } from "../../../ui";
import {
  acceptedByFilter, exposureByFilter, filterColor, plannedByFilter, type PlannedFilter,
} from "./filters";
import { subsPhrase } from "./LiveStack";
import { useActiveSession } from "./sessionData";

/** The plan's planned shutter per filter: `plannedByFilter`'s pool cut over
 *  every target that is not a mosaic panel, and each panel's steps in full.
 *  A panel is a target whose `mosaic_group` names one of the plan's groups,
 *  the rule the server's progress route uses (`flows/progress.py`). */
export function plannedShutter(plan: SequencePlan | null | undefined): PlannedFilter[] {
  if (!plan) return [];
  const groups = new Set((plan.groups ?? []).map((g) => g.id));
  const targets = plan.targets ?? [];
  const isPanel = (t: { mosaic_group?: string }) =>
    typeof t.mosaic_group === "string" && groups.has(t.mosaic_group);
  const out = new Map<string, PlannedFilter>();
  for (const row of plannedByFilter({ ...plan, targets: targets.filter((t) => !isPanel(t)) })) {
    out.set(row.filter, { ...row, stepIds: [...row.stepIds] });
  }
  for (const t of targets) {
    if (!isPanel(t)) continue;
    for (const s of t.steps ?? []) {
      if ((s.frame_type ?? "Light") !== "Light") continue;
      const filter = s.filter || "-";
      const row = out.get(filter) ?? { filter, count: 0, plannedS: 0, stepIds: [] as string[] };
      if (s.id) row.stepIds.push(s.id);
      row.count += s.count;
      row.plannedS += s.count * s.exposure_s;
      out.set(filter, row);
    }
  }
  return [...out.values()];
}

export function IntegrationBar({ legend = true }: { legend?: boolean }): JSX.Element | null {
  const seq = useSeq();
  const { session } = useActiveSession();
  const progress = seq.progress;

  const planned = plannedShutter(session?.plan);
  const accepted = acceptedByFilter(session, null);
  const exposureOf = exposureByFilter(session?.plan);

  const hasLedger = planned.length > 0 && session != null;

  let segments: BarSegment[];
  let total: string;

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
    const plannedS = planned.reduce((a, p) => a + p.plannedS, 0);
    const doneS = planned.reduce(
      (a, p) => a + Math.min(p.count, accepted.get(p.filter) ?? 0) * (exposureOf.get(p.filter) ?? 0), 0);
    total = `${fmtIntegration(doneS)} of ${fmtIntegration(plannedS)} planned`;
  } else {
    if (!progress || progress.frames_total <= 0) return null;
    segments = [{
      frac: 1,
      fill: progress.frames_done / progress.frames_total,
      color: "var(--accent)",
      label: `${progress.frames_done}/${progress.frames_total} frames`,
    }];
    total = `${fmtCount(progress.frames_done)} of ${subsPhrase(progress.frames_total)}`;
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
        <Mono size={10} tone="dim" data-testid="integration-total">{total}</Mono>
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
