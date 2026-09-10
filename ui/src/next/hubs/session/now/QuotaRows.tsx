// QuotaRows.tsx - the quota rules, READ-ONLY, beside the ledger
// (GAP-ANALYSIS section 7: "expose the quota rules read-only with the ledger,
// editable on tablet").
//
// EVERY ROW SAYS WHAT IT MEANS, not just what it is set to. "SKIP AFTER 3" is
// a number; "move on after 3 rejects in a row" is a rule. The right-hand chip
// goes to the plan editor and NOTHING here writes a value: the run executes the
// frozen copy of the plan it took at Run, so a phone edit would change a
// document the night is not reading while looking exactly like it had worked.
//
// THE COUNT-MODE WARNING IS THE ONE THING ON THIS SCREEN THAT CHANGES A NUMBER
// THE OPERATOR THOUGHT THEY HAD. With `count_mode: "attempts"` and any quality
// gate armed, "20 x 300s" quietly means "at most 20 attempts", of which an
// unknown number are rejected - and nothing reports the shortfall. The sentence
// is `SequenceView`'s own, gate-driven, and names the gates that made it true.

import type { JSX } from "react";

import { armedQualityGates, countShrinksSilently } from "../../../../components/sequence/stepDefaults";
import { usePlan } from "../../../../store";
import type { SequencePlan } from "../../../../types";
import { useBreakpoint } from "../../../breakpoint";
import { Chip, Label, Mono } from "../../../ui";
import { explainLock } from "../../../shell/explain";
import { nav } from "../../../router";
import { useActiveSession } from "./sessionData";

export const PHONE_EDIT_REASON = "The plan editor opens on a tablet or desktop.";

export interface QuotaRow { label: string; value: string; meaning: string }

/** The rows, pure. `0` means OFF for every gate here, and the row says "off"
 *  rather than printing a zero that reads like a threshold. */
export function quotaRows(plan: SequencePlan | null): QuotaRow[] {
  if (!plan) return [];
  const perFilter = new Map<string, number>();
  for (const t of plan.targets ?? []) {
    for (const s of t.steps ?? []) {
      if ((s.frame_type ?? "Light") !== "Light") continue;
      const f = s.filter || "-";
      perFilter.set(f, Math.max(perFilter.get(f) ?? 0, s.count));
    }
  }
  const counts = [...perFilter.values()];
  const countLine = counts.length === 0 ? "no light steps"
    : counts.every((c) => c === counts[0]) ? `${counts[0]} frames per filter`
      : `${Math.min(...counts)}-${Math.max(...counts)} frames per filter`;

  const mode = plan.count_mode ?? "attempts";
  const off = (n: number | null | undefined) => (n ?? 0) === 0;

  return [
    { label: "COUNT", value: countLine, meaning: "what each filter is asked for tonight" },
    {
      label: "COUNT MODE", value: mode,
      meaning: mode === "accepted"
        ? "counting accepted frames - rejects do not use up the count"
        : "counting attempts - a rejected frame still uses up its count",
    },
    {
      label: "MIN STARS", value: off(plan.min_stars) ? "off" : String(plan.min_stars),
      meaning: off(plan.min_stars) ? "no star floor" : `reject a frame under ${plan.min_stars} stars`,
    },
    {
      label: "MAX GUIDE RMS", value: off(plan.max_guide_rms) ? "off" : `${plan.max_guide_rms}"`,
      meaning: off(plan.max_guide_rms) ? "no guide ceiling" : `reject a frame over ${plan.max_guide_rms}"`,
    },
    {
      label: "MAX ECCENTRICITY",
      value: off(plan.max_eccentricity) ? "off" : String(plan.max_eccentricity),
      meaning: off(plan.max_eccentricity)
        ? "no elongation ceiling" : `reject a frame over ${plan.max_eccentricity}`,
    },
    {
      label: "HFR REJECT",
      value: off(plan.hfr_reject_factor) ? "off" : `${plan.hfr_reject_factor}x`,
      meaning: off(plan.hfr_reject_factor) ? "no HFR spike guard"
        : `reject a frame ${(((plan.hfr_reject_factor ?? 1) - 1) * 100).toFixed(0)}% above the running median`,
    },
    {
      label: "SKIP AFTER",
      value: off(plan.max_consecutive_rejects) ? "off" : String(plan.max_consecutive_rejects),
      meaning: off(plan.max_consecutive_rejects) ? "never moves on by itself"
        : `move on after ${plan.max_consecutive_rejects} rejects in a row`,
    },
    {
      label: "END NIGHT AFTER",
      value: off(plan.max_consecutive_rejects_night) ? "off" : String(plan.max_consecutive_rejects_night),
      meaning: off(plan.max_consecutive_rejects_night) ? "never ends the night by itself"
        : `end the night after ${plan.max_consecutive_rejects_night} rejects in a row`,
    },
  ];
}

export function QuotaRows(): JSX.Element | null {
  const { session } = useActiveSession();
  const localPlan = usePlan();
  const bp = useBreakpoint();
  const plan = session?.plan ?? localPlan;
  const rows = quotaRows(plan);
  if (rows.length === 0) return null;

  const editReason = bp === "phone" ? PHONE_EDIT_REASON : null;
  const openEditor = () => nav.sheet("planEditor");

  const totalFrames = (plan.targets ?? []).reduce(
    (a, t) => a + (t.steps ?? []).reduce((b, s) => b + s.count, 0), 0);

  return (
    <div data-testid="now-quotas" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <Label>QUOTAS</Label>
        <Chip
          onClick={openEditor}
          lockedReason={editReason}
          onExplain={explainLock}
          data-testid="quota-edit"
        >
          edit in plan
        </Chip>
      </div>
      {rows.map((r) => (
        <div
          key={r.label}
          data-testid={`quota-${r.label.replace(/\s+/g, "-").toLowerCase()}`}
          style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8, alignItems: "baseline" }}
        >
          <Label>{r.label}</Label>
          <span style={{ minWidth: 0 }}>
            <Mono size={10.5}>{r.value}</Mono>{" "}
            <Mono size={10} tone="dim">{r.meaning}</Mono>
          </span>
        </div>
      ))}

      {countShrinksSilently(plan) && (
        <div
          data-testid="now-count-warning"
          style={{
            border: "1px solid var(--warn)", borderRadius: 12, padding: "8px 10px",
            display: "flex", flexDirection: "column", gap: 6,
            background: "color-mix(in srgb, var(--warn) 8%, transparent)",
          }}
        >
          <Mono size={10.5}>
            Counting attempts with {armedQualityGates(plan).join(" + ")} armed: a rejected
            frame still uses up its count, so {totalFrames} requested frames can deliver
            fewer keepers - and nothing reports the shortfall.
          </Mono>
          <Chip
            onClick={openEditor}
            lockedReason={editReason}
            onExplain={explainLock}
            data-testid="quota-fix-count-mode"
          >
            Count accepted frames instead
          </Chip>
        </div>
      )}
    </div>
  );
}
