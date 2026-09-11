// PlanAutomationSection.tsx - the twenty settings that decide what the rig does
// at 3am with nobody watching, rebuilt in the design's vocabulary.
//
// SIX DISCLOSURES, NOT ONE LIST OF TWENTY. The legacy column was a flat stack
// of `<label>` rows in a desktop side panel; this sheet is 420 px wide, and a
// flat twenty would be a scroll with no landmarks. Each group's closed row
// states what the group currently DOES ("unguided, no dither"), so the common
// case - checking that tonight is set up the way you left it - needs no taps at
// all. `automationModel.ts` owns those sentences and is tested without React.
//
// THE `About ...` POPUPS BECAME HINTS. Nine settings carried an `InfoDot` whose
// body is the only place the product explains what the setting means. An
// `InfoDot` opens on hover and tap, but it opens OVER the row on a 420 px
// column, and it is the first thing a hurried operator dismisses. The same
// sentences are now the `Field` hint under the control - always visible, never
// in the way, and reachable by a screen reader in reading order.
//
// EVERY NUMERIC IS THE `NumberField` PRIMITIVE, so the raw-string draft, the
// blank-is-rejected rule and the clamp are the same twenty times. `zeroMeans`
// renders `0 = off` only for the nine fields whose config really uses that
// convention - `dither_pixels` and the flip warning do not, and printing it on
// them would invent a setting the engine does not have.
//
// TWELVE OF THE TWENTY ARE LAYERED (#239). A `null` in the plan means "follow
// the rig's standards", so each of those rows carries the layer it is reading
// and, when the plan has overridden the rig, a one-tap way back. Without it
// there is no exit from an override, and the editor shows a number the rig may
// not hold - which is exactly how Polar ran simulated for weeks.

import { useState, type JSX } from "react";

import {
  armedQualityGates, countShrinksSilently,
} from "../../../../../components/sequence/stepDefaults";
import { planPolicy, POLICY_FIELDS, type PolicyField } from "../../../../../lib/standards";
import { isSequenceLive } from "../planModel";
import { useConfig, useSequence, useStore } from "../../../../../store";
import type { SequencePlan } from "../../../../../types";
import {
  Card, Chip, Disclosure, Label, LockNote, Mono, NumberField, Switch,
} from "../../../../ui";
import {
  AUTOMATION_EYEBROW, AUTOMATION_GROUPS, AUTOMATION_HINT, AUTOMATION_LABEL,
  AUTOMATION_ZERO_MEANS, groupSummary, runCopyNote,
  type AutomationKey, type Effective,
} from "./automationModel";
import "./automation.css";

export interface PlanSectionProps {
  /** Null when this role may write the plan. Anything else is rendered once, at
   *  the top, and passed to all twenty controls. */
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

/** The policy fields, as a set, so the row renderer can ask in O(1) whether a
 *  key is layered without another list to keep in step. */
const LAYERED = new Set<string>(POLICY_FIELDS);

/** Which keys are toggles. Everything else in a group is a number. */
const TOGGLES = new Set<AutomationKey>([
  "guide", "recover_guiding", "apply_filter_offsets", "meridian_flip",
  "safety_check", "park_when_done", "warm_cooler_when_done", "count_mode",
]);

export function PlanAutomationSection({ lockedReason, onExplain }: PlanSectionProps): JSX.Element {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const config = useConfig();
  const sequence = useSequence();
  // THE AREA'S OWN PREDICATE, not a fourth hand-written list of states. This
  // read `running || paused || aborting` and dropped `holding` - the state a
  // weather or safety hold parks a run in, which is exactly when an operator
  // opens this column to change what the rig does next. Through a hold, every
  // one of these twenty controls moved with no note saying the live run cannot
  // see the edit, and the run resumed on its frozen copy.
  const running = isSequenceLive(sequence.state);
  const runningPlanName = sequence.plan_name ?? plan?.name ?? "A plan";

  // Which group is open. One at a time: on a 420 px column two open groups push
  // the third off the screen, and the summaries mean nothing has to be open to
  // read the setup.
  const [openGroup, setOpenGroup] = useState<string | null>(null);

  const pol = (f: PolicyField) => planPolicy(
    plan as unknown as Record<string, unknown>, config, f);

  // The effective value of every layered field in one object, so the pure
  // summary builder never has to know about the store or the config shape.
  const eff: Effective = {};
  for (const f of POLICY_FIELDS) eff[f] = pol(f).value;

  const patch = (p: Partial<SequencePlan>) => { if (plan) setPlan({ ...plan, ...p }); };

  const numValue = (key: AutomationKey): number => {
    if (LAYERED.has(key)) return Number(pol(key as PolicyField).value ?? 0);
    if (key === "cool_to") return plan?.cool_to ?? 0;
    const raw = plan ? (plan as unknown as Record<string, unknown>)[key] : 0;
    return typeof raw === "number" ? raw : 0;
  };

  const boolValue = (key: AutomationKey): boolean => {
    if (key === "count_mode") return (plan?.count_mode ?? "attempts") === "accepted";
    if (key === "safety_check") return plan?.safety_check ?? true;
    if (LAYERED.has(key)) return pol(key as PolicyField).value === true;
    const raw = plan ? (plan as unknown as Record<string, unknown>)[key] : false;
    return raw === true;
  };

  /** Whole-number fields. Frames, minutes, stars and reject counts have no
   *  fractional meaning, and the legacy column rounded every one of them. */
  const isInteger = (key: AutomationKey): boolean =>
    key === "dither_every" || key === "dither_pixels" || key === "autofocus_every"
    || key === "min_stars" || key === "max_consecutive_rejects"
    || key === "max_consecutive_rejects_night";

  const commitNumber = (key: AutomationKey, n: number): void => {
    switch (key) {
      // `cool_to` is the one field where the number is a temperature and BLANK
      // is the off state, so it has no `min` and no `zeroMeans`: 0 °C is a real
      // setpoint, and the chip below is how it is turned off.
      case "cool_to": patch({ cool_to: n }); break;
      case "max_eccentricity": patch({ max_eccentricity: Math.min(1, Math.max(0, n)) }); break;
      default: patch({ [key]: Math.max(0, n) } as unknown as Partial<SequencePlan>); break;
    }
  };

  const commitToggle = (key: AutomationKey, v: boolean): void => {
    if (key === "count_mode") { patch({ count_mode: v ? "accepted" : "attempts" }); return; }
    patch({ [key]: v } as unknown as Partial<SequencePlan>);
  };

  /** Says which layer the value came from, and hands it back on a tap. Without
   *  the tap there is no way out of an override once one is made
   *  (`SequenceView.tsx:192`'s `RigChip`, rebuilt as a `Chip`). */
  const layerChip = (key: AutomationKey): JSX.Element | null => {
    if (!LAYERED.has(key)) return null;
    const p = pol(key as PolicyField);
    if (p.inherited) {
      return (
        <span className="nx-planauto-layer" data-testid={`plan-layer-${key}`}
          title="Follows the rig's standards (Settings, Safety, Imaging standards)">
          <Mono size={10} tone="dim">follows the rig</Mono>
        </span>
      );
    }
    return (
      <Chip
        data-testid={`plan-layer-${key}`}
        lockedReason={lockedReason}
        onExplain={onExplain}
        onClick={() => patch({ [key]: null } as unknown as Partial<SequencePlan>)}
      >
        this plan overrides the rig - follow it again
      </Chip>
    );
  };

  const row = (key: AutomationKey): JSX.Element => {
    const label = AUTOMATION_LABEL[key];
    const hint = AUTOMATION_HINT[key];
    const testid = `plan-guard-${key}`;

    if (TOGGLES.has(key)) {
      return (
        <div className="nx-planauto-row" key={key}>
          <Switch
            checked={boolValue(key)}
            onChange={(v) => commitToggle(key, v)}
            label={label}
            note={hint}
            lockedReason={lockedReason}
            onExplain={onExplain}
            data-testid={testid}
          />
          {layerChip(key)}
        </div>
      );
    }

    const coolOff = key === "cool_to" && plan?.cool_to == null;
    const coolHint = key !== "cool_to" ? undefined
      : coolOff
        ? "Cooling is off - the sensor is left at ambient. Type a temperature to arm it."
        : "0 °C is a real setpoint, so use the chip below to turn cooling off.";

    return (
      <div className="nx-planauto-row" key={key}>
        <NumberField
          label={AUTOMATION_EYEBROW[key]}
          ariaLabel={label}
          value={numValue(key)}
          onCommit={(n) => commitNumber(key, n)}
          integer={isInteger(key)}
          min={key === "cool_to" ? undefined : 0}
          max={key === "max_eccentricity" ? 1 : undefined}
          step={key === "max_eccentricity" ? 0.05 : undefined}
          hint={coolHint ?? hint}
          zeroMeans={AUTOMATION_ZERO_MEANS[key]}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid={testid}
        />
        {key === "cool_to" && (
          <Chip
            data-testid={coolOff ? "plan-cool-arm" : "plan-cool-off"}
            lockedReason={lockedReason}
            onExplain={onExplain}
            onClick={() => patch({ cool_to: coolOff ? 0 : null })}
          >
            {coolOff ? "arm cooling at 0 °C" : "turn cooling off"}
          </Chip>
        )}
        {layerChip(key)}
      </div>
    );
  };

  const gates = plan ? armedQualityGates(plan) : [];
  const totalFrames = (plan?.targets ?? []).reduce(
    (a, t) => a + (t.steps ?? []).reduce((b, s) => b + s.count, 0), 0);

  return (
    <Card className="nx-planauto" data-testid="plan-automation">
      <div className="nx-planauto-head">
        <Label>AUTOMATION</Label>
        <Mono size={10} tone="dim">what the rig does with nobody watching</Mono>
      </div>

      <LockNote reason={lockedReason} data-testid="plan-automation-lock" />

      {running && (
        <p className="nx-planauto-runnote" data-testid="plan-automation-run-note">
          {runCopyNote(runningPlanName)}
        </p>
      )}

      {AUTOMATION_GROUPS.map((g) => (
        <Disclosure
          key={g.id}
          summary={g.title}
          sub={groupSummary(g.id, plan, eff)}
          open={openGroup === g.id}
          onToggle={(o) => setOpenGroup(o ? g.id : null)}
          data-testid="plan-automation-group"
        >
          <div className="nx-planauto-group" data-group={g.id}>
            {g.keys.map(row)}
            {g.id === "quality" && plan && countShrinksSilently(plan) && (
              <div className="nx-planauto-warn" data-testid="plan-count-warning">
                <Mono size={10.5}>
                  Counting attempts with {gates.join(" + ")} armed: a rejected frame still
                  uses up its count, so {totalFrames} requested frames can deliver fewer
                  keepers - and nothing reports the shortfall.
                </Mono>
                <Chip
                  data-testid="plan-count-fix"
                  lockedReason={lockedReason}
                  onExplain={onExplain}
                  onClick={() => patch({ count_mode: "accepted" })}
                >
                  count accepted frames instead
                </Chip>
              </div>
            )}
          </div>
        </Disclosure>
      ))}
    </Card>
  );
}
