// PlanTargets.tsx - the target list, its search, its ordering and its mosaics.
//
// The rebuild of `views/SequenceView.tsx:1010-1400` (the Targets panel).
//
// THE LOCK IS ENFORCED, NOT DESCRIBED. The note at the top of this list says
// that during a run nothing here adds or removes a target, a step or a
// template, that the catalog search is off, and that per-target scheduling is
// frozen. Every one of those clauses is a promise the code keeps: `addTarget`
// refuses at its first line, every structural control carries the same
// `lockedReason`, and the search affordance is replaced by a locked chip
// carrying that sentence rather than by a `fieldset disabled` that removes it
// from the page. The first draft of the legacy note failed exactly this twice -
// it claimed targets could not be added while the search in its own header
// happily added them.
//
// "ON THIS PAGE" IS LOAD-BEARING. The Sky hub's SEND TO PLAN deliberately stays
// live during a run and carries its own warning, so an unqualified "targets
// cannot be added" would be false the moment someone frames a panel next door.
//
// THE ORDER RESPONSE IS VERIFIED BEFORE IT IS APPLIED. `recommended_order`
// indexes the REQUEST array; a wrong length, an out-of-range index or a
// repeated one would silently drop or duplicate targets, so the whole thing is
// checked as a permutation and refused loudly otherwise.

import { useState, type JSX, type ReactNode } from "react";

import { api } from "../../../../api";
import CatalogSearch from "../../../../components/atlas/CatalogSearch";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { nextStep } from "../../../../components/sequence/stepDefaults";
import { uid } from "../../../../lib/ids";
import { applyStepsToGroup } from "../../../../lib/planGroups";
import { templateSteps, type SequenceTemplate } from "../../../../lib/sequenceTemplates";
import { defaultSchedule, useStatus, useStore } from "../../../../store";
import type {
  CatalogEntry, ExposureStep, SequencePlan, Target, VisibilityNight,
} from "../../../../types";
import { explainLock } from "../../../shell/explain";
import { ActionButton, Card, Chip, EmptyCard, Label, LockNote, Mono } from "../../../ui";
import { PlanTargetCard } from "./PlanTargetCard";
import { fmtHoursMinutes, planBlocks, targetFrames, targetSeconds } from "./planModel";

export function PlanTargets({
  plan, setPlan, setPlanWithUndo, structureLocked, altLimit, stepSkyLimitedS,
  activeTargetIndex, runtimeSchedule, onOpenSchedule,
}: {
  plan: SequencePlan;
  setPlan: (p: SequencePlan) => void;
  /** Instant-apply plus a 5 s undo. Reversible edits are NOT holds. */
  setPlanWithUndo: (label: string, next: SequencePlan) => void;
  /** The one sentence for every structural control while a run owns the plan. */
  structureLocked: string | null;
  altLimit: number;
  stepSkyLimitedS: number | null;
  /** Which target the engine is on, so its card can carry the live schedule
   *  line. -1 when no run is live. */
  activeTargetIndex: number;
  runtimeSchedule: ReactNode;
  onOpenSchedule: (target: Target, ti: number) => void;
}): JSX.Element {
  const status = useStatus();
  const openFraming = useStore((s) => s.openFraming);
  const setFraming = useStore((s) => s.setFraming);
  const toast = useStore((s) => s.enqueueToast);
  const [ordering, setOrdering] = useState(false);
  const [pendingAdd, setPendingAdd] = useState(false);

  const filters = status?.filterwheel?.names ?? [];
  const filterOpaque = status?.filterwheel?.opaque ?? [];
  const wheelPosition = status?.filterwheel?.position;
  const blocks = planBlocks(plan.targets);

  const patchTarget = (ti: number, patch: Partial<Target>) =>
    setPlan({ ...plan, targets: plan.targets.map((t, i) => (i === ti ? { ...t, ...patch } : t)) });

  const patchStep = (ti: number, si: number, patch: Partial<ExposureStep>) =>
    patchTarget(ti, {
      steps: plan.targets[ti].steps.map((s, i) => (i === si ? { ...s, ...patch } : s)),
    });

  // A catalog pick checks tonight's visibility first and gates on a confirm
  // when the target never clears the site's limit. A visibility FAILURE must
  // never block the add - the catch falls through to the unconditional add.
  const addTarget = async (e: CatalogEntry) => {
    if (structureLocked) return;   // the enforced half of the note above
    if (pendingAdd) return;        // no double-add on a rapid double-tap
    setPendingAdd(true);
    try {
      let ok = true;
      try {
        const night = await api.get<VisibilityNight>(
          `/api/visibility?ra=${e.ra_hours}&dec=${e.dec_deg}&alt_limit=${altLimit}`,
        );
        if (night.never_rises_above_limit) {
          ok = await confirmDialog({
            title: "Below tonight's limit",
            body: `${e.name || e.id} does not rise above ${Math.round(night.alt_limit_deg)} `
              + `degrees tonight (peaks ${night.transit_alt.toFixed(0)}). Add it anyway?`,
            tone: "warn",
            mode: "confirm",
            confirmLabel: "Add anyway",
            confirmPrimary: true,
          });
        }
      } catch {
        /* the visibility check did not answer - add without a gate */
      }
      if (!ok) return;
      setPlan({
        ...plan,
        targets: [...plan.targets, {
          id: uid(),
          name: e.id,
          ra_hours: e.ra_hours,
          dec_deg: e.dec_deg,
          center: true,
          autofocus_first: true,
          calibration: false,
          // A new target's first step starts on the filter the wheel is
          // physically parked on, not "no filter".
          steps: [{ ...nextStep([], filters, wheelPosition, filterOpaque), id: uid() }],
          schedule: defaultSchedule(),
        }],
      });
    } finally {
      setPendingAdd(false);
    }
  };

  const orderByTonight = async () => {
    if (plan.targets.length < 2) return;
    setOrdering(true);
    try {
      const body = await api.post<{ recommended_order: number[] }>("/api/visibility/order", {
        targets: plan.targets.map((t) => ({
          name: t.name, ra_hours: t.ra_hours, dec_deg: t.dec_deg, mosaic_group: t.mosaic_group,
        })),
      });
      const order = body.recommended_order;
      const valid = Array.isArray(order)
        && order.length === plan.targets.length
        && order.every((i) => Number.isInteger(i) && i >= 0 && i < plan.targets.length)
        && new Set(order).size === order.length;
      if (!valid) {
        toast({
          level: "error",
          title: "Could not reorder",
          detail: "The server's ordering did not name every target exactly once, so nothing "
            + "was changed.",
        });
        return;
      }
      setPlan({ ...plan, targets: order.map((i) => plan.targets[i]) });
      toast({ level: "success", title: "Ordered by tonight's transits" });
    } catch (err) {
      toast({ level: "error", title: "Could not reorder", detail: (err as Error).message });
    } finally {
      setOrdering(false);
    }
  };

  // Framing needs no device, so it works offline and is never locked. The store
  // action seeds the session and moves the app to the Sky hub through
  // `legacyBridge` - the one navigation door reused plan logic already uses.
  const frameInSky = (id: string, t: { ra_hours: number; dec_deg: number; rotation_deg?: number }) => {
    const entry: CatalogEntry = {
      id, name: id, type: "", ra_hours: t.ra_hours, dec_deg: t.dec_deg,
      mag: 0, size_arcmin: 0, alt: 0, az: 0,
    };
    openFraming(entry);
    setFraming({ rotation_deg: t.rotation_deg ?? 0 });
  };

  const applyTemplate = (ti: number, tpl: SequenceTemplate) => {
    const lightFilters = filters.filter((_, i) => !filterOpaque[i]);
    setPlanWithUndo(
      `Applied "${tpl.label}" to ${plan.targets[ti].name}`,
      {
        ...plan,
        targets: plan.targets.map((t, i) =>
          i === ti ? { ...t, steps: templateSteps(tpl, lightFilters) } : t),
      },
    );
  };

  const card = (t: Target, ti: number): JSX.Element => (
    <PlanTargetCard
      key={t.id ?? ti}
      target={t}
      ti={ti}
      altLimit={altLimit}
      filters={filters}
      filterOpaque={filterOpaque}
      wheel={status?.filterwheel}
      hasRotator={status?.rotator != null}
      stepSkyLimitedS={stepSkyLimitedS}
      structureLocked={structureLocked}
      runtimeSchedule={ti === activeTargetIndex ? runtimeSchedule : undefined}
      inMosaic={t.mosaic_group != null
        && plan.targets.filter((x) => x.mosaic_group === t.mosaic_group).length > 1}
      onPatchTarget={(patch) => patchTarget(ti, patch)}
      onPatchStep={(si, patch) => patchStep(ti, si, patch)}
      onAddStep={() => patchTarget(ti, {
        steps: [...t.steps, { ...nextStep(t.steps, filters, wheelPosition, filterOpaque), id: uid() }],
      })}
      onRemoveStep={(si) => setPlanWithUndo("Removed a step", {
        ...plan,
        targets: plan.targets.map((tt, i) =>
          i === ti ? { ...tt, steps: tt.steps.filter((_, j) => j !== si) } : tt),
      })}
      onDelete={() => setPlanWithUndo(`Deleted ${t.name}`, {
        ...plan, targets: plan.targets.filter((_, i) => i !== ti),
      })}
      onApplyTemplate={(tpl) => applyTemplate(ti, tpl)}
      onApplyGroupSteps={() => {
        const group = t.mosaic_group!;
        const members = plan.targets.filter((x) => x.mosaic_group === group).length;
        setPlanWithUndo(
          `Applied steps to ${members} panel${members === 1 ? "" : "s"}`,
          { ...plan, targets: applyStepsToGroup(plan.targets, group, t.steps) },
        );
      }}
      onOpenSchedule={() => onOpenSchedule(t, ti)}
      onFrameInAtlas={() => frameInSky(t.name, t)}
    />
  );

  return (
    <div className="nx-plan-stack" data-testid="plan-targets">
      <Card>
        <div className="nx-plan-stack">
          <div className="nx-plan-row">
            <Label size={11}>TARGETS</Label>
            <Mono size={10} tone="dim">{plan.targets.length}</Mono>
            <span style={{ marginLeft: "auto" }}>
              <Chip
                onClick={() => void orderByTonight()}
                lockedReason={structureLocked
                  ?? (plan.targets.length < 2
                    ? "Ordering needs at least two targets."
                    : ordering ? "Still ordering." : null)}
                onExplain={explainLock}
                data-testid="plan-order-tonight"
              >
                {ordering ? "ORDERING" : "ORDER BY TONIGHT"}
              </Chip>
            </span>
          </div>

          {/* The search is the shared Atlas widget: one search in the app, with
              its zero state, its "Searching" state and its query-id guard. When
              the structure is locked it is REPLACED by a chip carrying the
              reason - not hidden, and not a dead input. */}
          {structureLocked ? (
            <Chip lockedReason={structureLocked} onExplain={explainLock}
              data-testid="plan-search-locked">
              ADD TARGET
            </Chip>
          ) : (
            <div className="nx-plan-search">
              <CatalogSearch
                onPick={(e) => { void addTarget(e); }}
                placeholder="+ add target - e.g. M 31"
                className="w-full"
              />
            </div>
          )}

          <LockNote reason={structureLocked} data-testid="plan-structure-lock" />
        </div>
      </Card>

      {plan.targets.length === 0 && (
        <EmptyCard
          data-testid="plan-targets-empty"
          title="EMPTY PLAN"
          hint={structureLocked
            ? "This plan has no targets, and a run owns the editor until it stops."
            : "Search the catalog above to add one. A target arrives with one step on the "
              + "filter the wheel is parked on."}
        />
      )}

      {blocks.map((blk, bi) => {
        if (blk.kind === "single") return card(plan.targets[blk.ti], blk.ti);
        const members = blk.members.map((i) => plan.targets[i]);
        const gFrames = members.reduce((a, t) => a + targetFrames(t), 0);
        const gSeconds = members.reduce((a, t) => a + targetSeconds(t), 0);
        return (
          <div key={`g-${blk.group}-${bi}`} className="nx-plan-group" data-testid="plan-mosaic-group">
            <div className="nx-plan-row">
              <Label size={11}>{blk.group}</Label>
              <Mono size={10} tone="dim">
                {blk.members.length} panel{blk.members.length === 1 ? "" : "s"} · {gFrames} frame
                {gFrames === 1 ? "" : "s"} · {fmtHoursMinutes(gSeconds)}
              </Mono>
            </div>
            <div className="nx-plan-row">
              <Chip onClick={() => frameInSky(blk.group, members[0])}
                data-testid="plan-mosaic-frame">FRAME IN SKY</Chip>
              <span style={{ marginLeft: "auto" }}>
                <ActionButton
                  kind="danger"
                  onPress={() => setPlanWithUndo(`Deleted mosaic ${blk.group}`, {
                    ...plan, targets: plan.targets.filter((t) => t.mosaic_group !== blk.group),
                  })}
                  ariaLabel={`Delete all ${blk.members.length} panels in ${blk.group}`}
                  lockedReason={structureLocked}
                  onExplain={explainLock}
                  data-testid="plan-mosaic-delete"
                >
                  DELETE GROUP
                </ActionButton>
              </span>
            </div>
            {blk.members.map((ti) => card(plan.targets[ti], ti))}
          </div>
        );
      })}
    </div>
  );
}
