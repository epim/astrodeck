// PlanEditor.tsx - the plan editor, rebuilt in the design's vocabulary.
//
// This is the root of the area and the only file that imports `plan.css`
// (wave R7's CSS rule: `next.css` has one owner, every rebuilt area carries its
// own sheet, and that sheet is imported by the area's root, never by NextApp).
//
// ONE COLUMN, ORDERED BY WHAT THE USER TOUCHES. The legacy view is a two-column
// grid whose right column carries the library, the automation, the rules, the
// sessions, the pre-flight strip and Run. A 420 px sheet has one column, so the
// order is the decision: what the run is doing, the offer to resume, whether the
// rig is ready, which plan this is, the targets, then the settings that apply to
// the NEXT run. START lives in the sheet's sticky footer, because the legacy
// layout put it below twenty automation controls and finding it cost the wrong
// thirty seconds on the night a run had to be restarted.
//
// THREE THINGS THIS ROOT OWNS BECAUSE THEY MUST EXIST EXACTLY ONCE.
//
// 1. THE RUN-LOCK NOTES. Three sentences about what a live run does and does
//    not read (`SequenceView.tsx:1052-1057`, `:1496-1499`, `:1758-1759`): the
//    server is executing the COPY of the plan it took at Run, so edits here
//    reach the NEXT run. Their WORDING lives in `planModel.ts` so there is one
//    copy of each; the structure note is rendered by `PlanTargets` against the
//    controls it governs, the rules note here, and the automation note by
//    T-R7-6's own section, which already states it. One statement of a fact is
//    a fact; two are a maintenance problem.
//
// 2. THE 5 s UNDO. Deletes and the two fan-out edits (steps-to-all-panels, a
//    starter recipe) are instant-apply plus an undo, never a hold: a reversible
//    edit is not a destructive one, and charging a one-second hold for every
//    routine edit is how an editor becomes unusable in gloves.
//
// 3. THE CAPABILITY SENTENCE THE THREE SECTIONS RENDER. All three took
//    `lockedReason={null}` - a literal, not a decision - so a viewer got a fully
//    live automation column, when/then rule editor and session ledger. The draft
//    writes are local, so nothing 403s while you edit; the refusal arrives at
//    START, after the twenty settings have been changed and cannot be saved. One
//    reason, from `useCanControlMount()`, passed to all three, so the sections
//    say the same thing at the same moment START does.
//
// The start itself lives in `planStart.ts`, because the footer needs the same
// decision and a second copy is the "prices one thing, fires another" defect.

import { useEffect, useRef, useState, type JSX } from "react";

import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import {
  skyElectronsPerSub, skyLimitedSubSeconds, skyRateEPerSec,
} from "../../../../lib/photometry";
import { formatScheduleStatus } from "../../../../lib/scheduleStatus";
import {
  defaultSchedule, useAtlasBannerPending, usePhotometry, usePreview, useSeq, useSite, useStore,
} from "../../../../store";
import type { Schedule, SequencePlan, Target } from "../../../../types";
import { explainLock } from "../../../shell/explain";
import { ActionButton, BannerCard, LockNote, Mono } from "../../../ui";

// ---------------------------------------------------------------------------
// COMPOSITION BLOCK - the three sections T-R7-6 owns. This task never creates a
// file in `plan/automation/**`, `plan/instructions/**` or `plan/sessions/**`;
// it composes what those directories export through their own `index.ts`. Each
// takes `{ lockedReason, onExplain }` and reads and writes the plan draft
// through the store, exactly as this file does.
// ---------------------------------------------------------------------------
import { PlanAutomationSection } from "./automation";
import { PlanInstructionsSection } from "./instructions";
import { PlanSessionsSection } from "./sessions";
// ---------------------------------------------------------------------------

import { PlanLibrary } from "./PlanLibrary";
import { PlanPreflight, PlanStartFooter } from "./PlanPreflight";
import { PlanResume } from "./PlanResume";
import { PlanRunHeader } from "./PlanRunHeader";
import { PlanTargets } from "./PlanTargets";
import {
  instructionsLockNote, isSequenceLive, runningPlanName, structureLockNote,
  useRecoverable, UNDO_MS, type UndoState,
} from "./planModel";
import { usePlanStart } from "./planStart";

import "./plan.css";

/** Which target's schedule is open, as a route param. The layer is route state
 *  so a reload lands back on it and the browser's Back closes it, exactly like
 *  a registered sheet; it is not a registered sheet only because this task does
 *  not own the Session hub's sheet registry. */
export const SCHEDULE_PARAM = "schedule";

/** Add or drop the schedule param on the CURRENT hash without disturbing the
 *  sheet stack. Written against `location.hash` rather than `nav.sheet` because
 *  `nav.sheet` REPLACES the whole param map, which would drop a deep link's
 *  other params on the way in and out. */
function setScheduleParam(value: string | null): void {
  if (typeof window === "undefined" || !window.location) return;
  const raw = window.location.hash || "#/session/flows/planEditor";
  const [path, query] = raw.replace(/^#/, "").split("?");
  const sp = new URLSearchParams(query ?? "");
  if (value == null) sp.delete(SCHEDULE_PARAM);
  else sp.set(SCHEDULE_PARAM, value);
  const q = sp.toString();
  window.location.hash = `#${path}${q ? `?${q}` : ""}`;
}

/** Close the schedule layer. Exported so the mount file's `Sheet` and the
 *  layer's own BACK use one door out. */
export function closePlanSchedule(): void {
  setScheduleParam(null);
}

/** The schedule layer's props when the route names a target, else null.
 *
 *  A HOOK, NOT A COMPONENT, so the mount file can choose between two whole
 *  `Sheet`s: a sheet inside a sheet would give the layer two headers and two
 *  BACK pills, and the one underneath would still carry the editor's START
 *  footer for a screen that cannot start anything. */
export function usePlanSchedule(params: Record<string, string>): {
  name: string;
  schedule: Schedule;
  lockedReason: string | null;
  onChange: (patch: Partial<Schedule>) => void;
} | null {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const seq = useSeq();

  const id = params[SCHEDULE_PARAM] ?? null;
  const index = id == null ? -1 : plan.targets.findIndex((t, i) => (t.id ?? String(i)) === id);
  if (index < 0) return null;

  const t = plan.targets[index];
  const live = isSequenceLive(seq.state);
  return {
    name: t.name,
    schedule: t.schedule ?? defaultSchedule(),
    lockedReason: live ? structureLockNote(runningPlanName(seq.plan_name, plan.name)) : null,
    onChange: (patch) => setPlan({
      ...plan,
      targets: plan.targets.map((x, i) => i === index
        ? { ...x, schedule: { ...(x.schedule ?? defaultSchedule()), ...patch } }
        : x),
    }),
  };
}

export function PlanEditorBody(): JSX.Element {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const seq = useSeq();
  const site = useSite();
  const atlasBannerPending = useAtlasBannerPending();
  const dismissAtlasBanner = useStore((s) => s.dismissAtlasBanner);
  const photometry = usePhotometry();
  const livePreview = usePreview();

  const [undo, setUndo] = useState<UndoState | null>(null);
  const undoTimer = useRef<number | null>(null);
  useEffect(() => () => { if (undoTimer.current != null) clearTimeout(undoTimer.current); }, []);

  const live = isSequenceLive(seq.state);
  const { rec, clear: clearRecoverable } = useRecoverable(live);
  const { items, verdict, startLockedReason, start } = usePlanStart();
  const canControl = useCanControlMount();

  /** The one sentence the three composed sections render. It is the SAME
   *  capability `POST /api/sequence/start` enforces, so the lock note beside a
   *  frozen control and the reason on START name one policy. */
  const planWriteReason = canControl
    ? null
    : `editing the plan needs ${accessPhrase("control.mount")}`;

  // The site's own horizon limit drives the per-target sparkline and the
  // tonight ordering. 30 degrees is the same fallback `/api/visibility` uses
  // when the site is unknown.
  const altLimit = site?.horizon_min_deg ?? 30;

  // ONE sky-limited sub length for the whole editor, from the live preview.
  // Read here rather than per step: the tested photometry core does the maths,
  // and a per-step sky measurement is not what the number means.
  const stepSkyLimitedS = (livePreview && livePreview.data_is_linear
    && photometry.egain > 0 && photometry.readNoiseE > 0)
    ? skyLimitedSubSeconds(
      photometry.readNoiseE,
      skyRateEPerSec(
        skyElectronsPerSub(livePreview.stats.median, photometry.biasAdu, photometry.egain),
        livePreview.exposure_s,
      ),
    )
    : null;

  const planName = runningPlanName(seq.plan_name, plan.name);
  const structureLocked = live ? structureLockNote(planName) : null;

  const setPlanWithUndo = (label: string, next: SequencePlan) => {
    if (undoTimer.current != null) clearTimeout(undoTimer.current);
    setUndo({ label, prev: plan });
    setPlan(next);
    undoTimer.current = window.setTimeout(() => setUndo(null), UNDO_MS);
  };
  const doUndo = () => {
    if (undoTimer.current != null) { clearTimeout(undoTimer.current); undoTimer.current = null; }
    if (undo) setPlan(undo.prev);
    setUndo(null);
  };

  // The engine's own autorun-schedule line, rendered on the ACTIVE target's
  // card - why imaging is not happening even though a run is live.
  const runtimeStatus = formatScheduleStatus(seq.schedule, seq.live, Date.now() / 1000);
  const runtimeSchedule = live && runtimeStatus
    ? <Mono size={10} tone={runtimeStatus.tone === "warn" ? "warn" : "dim"}>{runtimeStatus.text}</Mono>
    : null;

  const openSchedule = (t: Target, ti: number) => setScheduleParam(t.id ?? String(ti));

  return (
    <div className="nx-plan" data-testid="plan-editor-body">
      {/* One-shot hand-off banner: N panels arrived from the Sky hub. */}
      {atlasBannerPending != null && (
        <BannerCard
          tone="good"
          text={`${atlasBannerPending} ${atlasBannerPending === 1 ? "target" : "panels"} `
            + "added from the Sky hub"}
          onDismiss={dismissAtlasBanner}
          data-testid="plan-atlas-banner"
        />
      )}

      <PlanRunHeader
        plan={plan}
        recoverable={rec}
        onStart={start}
        startLockedReason={startLockedReason}
      />

      {/* The resume offer, only when no run panel above is carrying its own
          RESUME FROM FRAME N. Two resume buttons on one screen is how a user
          learns to distrust both. */}
      {rec && seq.state === "idle" && <PlanResume rec={rec} onDone={clearRecoverable} />}

      <PlanPreflight plan={plan} items={items} verdict={verdict} />

      {/* The library stays usable during a run: saving tomorrow night's plan
          while tonight runs is a thing people do, and the plan draft is local
          until SAVE writes it. */}
      <PlanLibrary plan={plan} setPlan={setPlan} lockedReason={null} />

      <PlanTargets
        plan={plan}
        setPlan={setPlan}
        setPlanWithUndo={setPlanWithUndo}
        structureLocked={structureLocked}
        altLimit={altLimit}
        stepSkyLimitedS={stepSkyLimitedS}
        activeTargetIndex={live ? (seq.target_index ?? -1) : -1}
        runtimeSchedule={runtimeSchedule}
        onOpenSchedule={openSchedule}
      />

      {/* --------------------------------------------------------------------
          COMPOSITION BLOCK (T-R7-6 owns the three sections; this task owns the
          run-lock notes that sit above two of them).
          -------------------------------------------------------------------- */}
      {/* The AUTOMATION run-lock note is NOT rendered here: T-R7-6's section
          states it inside itself (`plan-automation-run-note`, word for word the
          same sentence `automationLockNote` holds). Two copies of one fact is
          the maintenance problem this file's header names, so the note stays
          where it can sit against the controls it governs, and
          `automationLockNote` is exported for whoever needs the string. */}
      <PlanAutomationSection lockedReason={planWriteReason} onExplain={explainLock} />

      {/* The RULES note does live here, tight above the section rather than
          inside it: `PlanInstructionsSection`'s own lock copy is about the
          `control.mount` capability, and dressing "the run already started" up
          as a permissions problem would be a second false statement. */}
      <LockNote reason={live ? instructionsLockNote(planName) : null}
        data-testid="plan-instructions-run-lock" />
      <PlanInstructionsSection lockedReason={planWriteReason} onExplain={explainLock} />

      <PlanSessionsSection lockedReason={planWriteReason} onExplain={explainLock} />
      {/* ------------------------------------------------------------------ */}

      {undo && (
        <div className="nx-plan-undo" role="status" aria-live="polite" data-testid="plan-undo">
          <Mono size={11}>{undo.label}</Mono>
          <span style={{ marginLeft: "auto" }}>
            <ActionButton kind="secondary" onPress={doUndo} data-testid="plan-undo-button">
              UNDO
            </ActionButton>
          </span>
        </div>
      )}
    </div>
  );
}

/** The sheet's sticky footer. Separate because `Sheet` takes it as a prop and
 *  the mount file owns the `Sheet`; it reads the SAME `usePlanStart()`
 *  decision the body does. */
export function PlanEditorFooter(): JSX.Element {
  const { items, verdict, startLockedReason, viewOnly, start } = usePlanStart();
  return (
    <PlanStartFooter
      items={items}
      verdict={verdict}
      viewOnly={viewOnly}
      startLockedReason={startLockedReason}
      onStart={start}
    />
  );
}
