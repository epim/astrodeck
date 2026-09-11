// PlanTargetCard.tsx - one target and its capture steps.
//
// The rebuild of `views/SequenceView.tsx:1066-1310` (`renderTarget`). It keeps
// every control that card had and fixes what a 420 px sheet cannot carry:
//
// * THE STEP EDITOR IS NOT A FIXED GRID. The legacy row was
//   `grid-cols-[76px_90px_70px_60px_50px_60px_auto]` - 454 px of fixed tracks,
//   which is what pushed the plan's right-hand column off an 820 px tablet and
//   made "count" unreachable by any finger gesture. Here each field is its own
//   labelled box in an auto-fitting grid that reflows to one column.
//
// * A FILTER IS PICKED FROM THE WHEEL, NEVER TYPED. The chips are the wheel's
//   own names; blackout slots never appear as a choice, because a carrier with
//   no glass is a light block and not a filter. For a Dark or a Bias the "no
//   filter" chip says the blackout slot's real name instead, since that is what
//   the wheel will actually drive to.
//
// * WHAT THE RUN LOCK REACHES AND WHAT IT DOES NOT. During a run the structure
//   controls (add step, remove step, delete, templates, apply-to-all, schedule)
//   are locked with one sentence, while frame type, filter, exposure, gain,
//   binning and count stay live - they apply to the NEXT run, which is what the
//   note above the list says. Every clause of that note is enforced here.
//
// * THE SKY-LIMITED ADVISORY IS LIGHT-ONLY. "read-noise limited" is meaningless
//   for a Dark, a Flat or a Bias, and rendering it there would be a confident
//   wrong answer about a frame that has no sky in it.

import type { JSX, ReactNode } from "react";

import { EXPOSURE_MAX_S, isStepExposureInvalid } from "../../../../lib/exposure";
import { filterSettingsPatchByName } from "../../../../lib/filterSettings";
import { integrationByFilter, subLengthVerdict, type SubVerdict } from "../../../../lib/photometry";
import { scheduleSummary } from "../../../../lib/schedule";
import { SEQUENCE_TEMPLATES, type SequenceTemplate } from "../../../../lib/sequenceTemplates";
import type { FilterSettingsSource } from "../../../../lib/filterSettings";
import { FLAT_ADU_TARGET, frameTypeDefaults } from "../../../../components/sequence/stepDefaults";
import { defaultSchedule } from "../../../../store";
import type { ExposureStep, Target } from "../../../../types";
import { explainLock } from "../../../shell/explain";
import {
  ActionButton, Card, Chip, Disclosure, Divider, Label, ListRow, Mono, NumberField, Pill,
  Segmented, Switch,
} from "../../../ui";
import { filterColor } from "../now";
import { TargetSpark } from "./TargetSpark";
import { fmtHoursMinutes, targetFrames, targetSeconds } from "./planModel";

/** The frame types the step editor can script. The backend's IMAGETYP header
 *  and the shutter wiring already honour exactly these four. */
const FRAME_TYPES = ["Light", "Dark", "Flat", "Bias"] as const;

export interface PlanTargetCardProps {
  target: Target;
  /** Index into `plan.targets`, so every callback names one target without the
   *  card having to hold the plan. */
  ti: number;
  altLimit: number;
  /** The wheel's full slot list and its blackout mask, from the driver. */
  filters: string[];
  filterOpaque: boolean[];
  wheel: FilterSettingsSource | null | undefined;
  /** The rotator, only to say whether a target's PA will be reached
   *  automatically or has to be set by hand before the run. */
  hasRotator: boolean;
  /** One sky-limited sub length for the whole card, from the live preview. */
  stepSkyLimitedS: number | null;
  /** Why the STRUCTURE is locked (a run in progress), or null. */
  structureLocked: string | null;
  /** The engine's own schedule line, on the ACTIVE target only. */
  runtimeSchedule?: ReactNode;
  /** True when this panel belongs to a mosaic with more than one panel. */
  inMosaic: boolean;
  onPatchTarget: (patch: Partial<Target>) => void;
  onPatchStep: (si: number, patch: Partial<ExposureStep>) => void;
  onAddStep: () => void;
  onRemoveStep: (si: number) => void;
  onDelete: () => void;
  onApplyTemplate: (tpl: SequenceTemplate) => void;
  onApplyGroupSteps: () => void;
  onOpenSchedule: () => void;
  onFrameInAtlas: () => void;
}

export function PlanTargetCard(p: PlanTargetCardProps): JSX.Element {
  const t = p.target;
  const lock = { lockedReason: p.structureLocked, onExplain: explainLock };
  // The slots you can actually image THROUGH. `filters` keeps the full list
  // because the wheel's position indexes into it.
  const lightFilters = p.filters.filter((_, i) => !p.filterOpaque[i]);
  const darkSlotName = p.filters.find((_, i) => p.filterOpaque[i]) ?? null;
  const frames = targetFrames(t);
  const seconds = targetSeconds(t);
  const bad = t.steps
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => isStepExposureInvalid(s.exposure_s, s.frame_type));

  return (
    <Card data-testid="plan-target-row">
      <div className="nx-plan-stack">
        {/* --------------------------------------------------------- head */}
        <div className="nx-plan-head">
          <span className="nx-plan-head-text">
            <span className="nx-plan-name">{t.name}</span>
            <Mono size={10.5} tone="dim">
              {t.ra_hours.toFixed(3)}h {t.dec_deg >= 0 ? "+" : ""}{t.dec_deg.toFixed(2)} deg
              {frames > 0 ? ` · ${frames} frames · ${fmtHoursMinutes(seconds)}` : " · no steps yet"}
            </Mono>
          </span>
          <TargetSpark ra_hours={t.ra_hours} dec_deg={t.dec_deg} altLimit={p.altLimit} />
        </div>

        {p.runtimeSchedule}

        {t.rotation_deg != null && t.rotation_deg > 0.5 && (
          <Pill tone="accent" data-testid="plan-target-pa"
            ariaLabel={p.hasRotator
              ? `Position angle ${Math.round(t.rotation_deg)} degrees, reached automatically at target start`
              : `Position angle ${Math.round(t.rotation_deg)} degrees, set the camera by hand before the run`}>
            PA {Math.round(t.rotation_deg)} DEG{p.hasRotator ? " · AUTO" : " · SET BY HAND"}
          </Pill>
        )}

        {/* Per-filter integration for THIS target's own steps. */}
        {t.steps.length > 0 && (
          <div className="nx-plan-chips">
            {integrationByFilter(t.steps).map((f) => (
              <Mono key={f.filter} size={10} tone="dim">
                {f.filter} {fmtHoursMinutes(f.seconds)}
              </Mono>
            ))}
          </div>
        )}

        {/* ------------------------------------------------------ toggles */}
        <Switch
          checked={t.center}
          onChange={(v) => p.onPatchTarget({ center: v })}
          label="Centre on this target"
          note="Plate-solves and re-centres before the first frame."
          data-testid="plan-target-center"
        />
        <Switch
          checked={t.autofocus_first}
          onChange={(v) => p.onPatchTarget({ autofocus_first: v })}
          label="Autofocus first"
          note="Runs one focus sweep when this target starts."
          data-testid="plan-target-af"
        />
        <Switch
          checked={t.calibration}
          onChange={(v) => p.onPatchTarget({ calibration: v })}
          label="Calibration frames"
          note="Darks and bias: no slew, no focus, no guiding."
          data-testid="plan-target-cal"
        />

        {/* ------------------------------------------------------ actions */}
        <div className="nx-plan-row">
          <Chip onClick={p.onOpenSchedule} {...lock} data-testid="plan-target-schedule">
            {scheduleSummary(t.schedule ?? defaultSchedule())}
          </Chip>
          {/* Framing works offline - it needs no device - so it is never
              locked, not even mid-run: looking at where a target sits changes
              nothing about the run. */}
          <Chip onClick={p.onFrameInAtlas} data-testid="plan-target-frame">FRAME IN SKY</Chip>
          {p.inMosaic && (
            <Chip onClick={p.onApplyGroupSteps} {...lock} data-testid="plan-target-apply-all">
              STEPS TO ALL PANELS
            </Chip>
          )}
          <span style={{ marginLeft: "auto" }}>
            <ActionButton
              kind="danger"
              onPress={p.onDelete}
              ariaLabel={`Delete target ${t.name}`}
              {...lock}
              data-testid="plan-target-delete"
            >
              DELETE
            </ActionButton>
          </span>
        </div>

        {/* ---------------------------------------------------- templates
            Rows, not chips: each recipe's blurb is the thing a first-timer
            needs and a chip has nowhere to put it. Collapsed, because a plan
            with six targets would otherwise repeat the same list six times. */}
        <Disclosure
          summary="STARTER RECIPES"
          sub={`${SEQUENCE_TEMPLATES.length} · replaces this target's steps`}
          data-testid="plan-target-templates"
        >
          {SEQUENCE_TEMPLATES.map((tpl) => (
            <ListRow
              key={tpl.id}
              title={tpl.label}
              sub={tpl.blurb}
              onPress={() => p.onApplyTemplate(tpl)}
              chevron
              {...lock}
              data-testid={`plan-template-${tpl.id}`}
            />
          ))}
        </Disclosure>

        <Divider />

        {/* -------------------------------------------------------- steps */}
        {t.steps.length === 0 && (
          <p className="nx-plan-note">
            No steps yet. ADD STEP writes one that follows the wheel's parked filter.
          </p>
        )}

        {t.steps.map((s, si) => {
          const invalid = isStepExposureInvalid(s.exposure_s, s.frame_type);
          const isLight = (s.frame_type ?? "Light") === "Light";
          const verdict: SubVerdict = isLight
            ? subLengthVerdict(s.exposure_s, p.stepSkyLimitedS)
            : "unknown";
          const emptySlotLabel = darkSlotName && ["Dark", "Bias"].includes(s.frame_type ?? "Light")
            ? darkSlotName
            : "NO FILTER";
          return (
            <div key={s.id ?? si} className="nx-plan-step" data-invalid={invalid ? "true" : "false"}
              data-testid="plan-step-row">
              <div className="nx-plan-row">
                <Label size={10}>STEP {si + 1}</Label>
                <Mono size={10} tone="dim">
                  {((s.count * s.exposure_s) / 60).toFixed(0)} min
                </Mono>
                <span style={{ marginLeft: "auto" }}>
                  <ActionButton
                    kind="ghost"
                    onPress={() => p.onRemoveStep(si)}
                    ariaLabel={`Remove step ${si + 1} of ${t.name}`}
                    {...lock}
                    data-testid={`plan-step-remove-${si}`}
                  >
                    REMOVE
                  </ActionButton>
                </span>
              </div>

              <Segmented<string>
                label={`Frame type, step ${si + 1} of ${t.name}`}
                value={s.frame_type ?? "Light"}
                onChange={(v) => p.onPatchStep(si, frameTypeDefaults(s, v))}
                options={FRAME_TYPES.map((ft) => ({ value: ft, label: ft.toUpperCase() }))}
                data-testid={`plan-step-type-${si}`}
              />

              <div className="nx-plan-chips" role="group"
                aria-label={`Filter, step ${si + 1} of ${t.name}`}>
                <Chip
                  active={(s.filter ?? "") === ""}
                  onClick={() => p.onPatchStep(si, { filter: null })}
                  data-testid={`plan-step-filter-none-${si}`}
                >
                  {emptySlotLabel}
                </Chip>
                {lightFilters.map((f) => (
                  <Chip
                    key={f}
                    active={s.filter === f}
                    onClick={() => p.onPatchStep(si, {
                      filter: f,
                      // Picking a filter that has its own saved exposure/gain
                      // fills them into THIS step, here, where they are visible
                      // and editable before anything is saved. The engine never
                      // reads them, so what is on this row is what the night runs.
                      ...filterSettingsPatchByName(p.wheel, f),
                    })}
                    data-testid={`plan-step-filter-${si}-${f}`}
                  >
                    <span aria-hidden="true" style={{
                      display: "inline-block", width: 8, height: 8, borderRadius: 999,
                      background: filterColor(f), marginRight: 6,
                    }} />
                    {f}
                  </Chip>
                ))}
              </div>

              <div className="nx-plan-fields">
                <NumberField
                  label="EXPOSURE"
                  unit="s"
                  value={s.exposure_s}
                  min={0}
                  max={EXPOSURE_MAX_S}
                  onCommit={(n) => p.onPatchStep(si, { exposure_s: n })}
                  ariaLabel={`Exposure in seconds, step ${si + 1} of ${t.name}`}
                  hint={invalid ? `1 to ${EXPOSURE_MAX_S} s, or 0 for a Bias frame` : undefined}
                  data-testid={`plan-step-exposure-${si}`}
                />
                <NumberField
                  label="GAIN"
                  value={s.gain}
                  integer
                  min={0}
                  onCommit={(n) => p.onPatchStep(si, { gain: n })}
                  ariaLabel={`Gain, step ${si + 1} of ${t.name}`}
                  data-testid={`plan-step-gain-${si}`}
                />
                <NumberField
                  label="COUNT"
                  unit="x"
                  value={s.count}
                  integer
                  min={1}
                  onCommit={(n) => p.onPatchStep(si, { count: n })}
                  ariaLabel={`Frame count, step ${si + 1} of ${t.name}`}
                  data-testid={`plan-step-count-${si}`}
                />
              </div>

              <Segmented<number>
                label={`Binning, step ${si + 1} of ${t.name}`}
                value={s.binning}
                onChange={(v) => p.onPatchStep(si, { binning: v })}
                options={[1, 2, 4].map((b) => ({ value: b, label: `${b}x` }))}
                data-testid={`plan-step-bin-${si}`}
              />

              {/* Flat auto-exposure. 0 keeps today's fixed-exposure behaviour;
                  above 0 the exposure is solved to that median ADU against the
                  panel. The field says which of those it is doing. */}
              {s.frame_type === "Flat" && (
                <NumberField
                  label="TARGET ADU"
                  value={s.adu_target ?? 0}
                  integer
                  min={0}
                  zeroMeans="auto-exposure off, the exposure above is used as-is"
                  hint={`${FLAT_ADU_TARGET} is about 38 percent of a 16-bit well`}
                  onCommit={(n) => p.onPatchStep(si, { adu_target: n })}
                  ariaLabel={`Target ADU for flat auto-exposure, step ${si + 1} of ${t.name}`}
                  data-testid={`plan-step-adu-${si}`}
                />
              )}

              {verdict === "too_short" && (
                <p className="nx-plan-note" data-tone="warn">
                  Read-noise limited - the sky reaches this sensor's noise floor at about
                  {" "}{Math.round(p.stepSkyLimitedS!)} s here.
                </p>
              )}
              {verdict === "good" && (
                <p className="nx-plan-note">Sky-limited - longer subs buy no depth tonight.</p>
              )}
              {verdict === "long" && (
                <p className="nx-plan-note">
                  Longer than the sky needs - shorter subs would lose nothing and risk less.
                </p>
              )}
            </div>
          );
        })}

        {/* Name the offending steps. "fix the highlighted step" is fine on a
            three-step plan and useless on a twelve-step one. */}
        {bad.length > 0 && (
          <p className="nx-plan-note" data-tone="bad" data-testid="plan-step-invalid">
            Exposure must be 1 to {EXPOSURE_MAX_S} s, or 0 for a Bias frame. Fix
            {" "}{bad.map(({ s, i }) =>
              `step ${i + 1} (${s.frame_type || "Light"}${s.filter ? ` ${s.filter}` : ""})`).join(", ")}.
          </p>
        )}

        <ActionButton
          kind="secondary"
          full
          onPress={p.onAddStep}
          ariaLabel={t.steps.length > 0
            ? "Add a step - copies the last step's exposure, gain, binning and count"
            : "Add a step"}
          {...lock}
          data-testid="plan-add-step"
        >
          ADD STEP
        </ActionButton>
      </div>
    </Card>
  );
}
