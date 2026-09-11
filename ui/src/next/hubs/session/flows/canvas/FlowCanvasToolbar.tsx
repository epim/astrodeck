// FlowCanvasToolbar.tsx - the canvas's own 48 px row (wave R7 parity row A1).
//
// EIGHT THINGS, LEFT TO RIGHT, AND NOTHING THE SHELL ALREADY RENDERS. The shell
// owns the wordmark, the flows pill with its count, the rig chips, the
// backend/provider badge (`header-backend`), the role chip and the night toggle
// at every breakpoint, so this row carries only what is about THIS flow:
//
//   1. the flow name (eyebrow FLOW plus a truncating mono)
//   2. PLAN
//   3. the validation pill, with the checker's own issue list behind it
//   4. ETA, while a run is live
//   5. the save-state pill - SAVED / UNSAVED EDITS / READ ONLY
//   6. SAVE
//   7. TONIGHT
//   8. RUN / STOP
//
// SAVE IS HERE BECAUSE THE CANVAS HAD NO SAVE AT ALL (whole-branch review, R5
// P0). `flowsSave` and `flowsCloseEditor` existed, the store tracked `dirty`,
// and nothing under `next/**` called either: every edit on this surface was
// dropped on the floor by BACK, by the FLOWS sub-nav chip and by a reload,
// silently. The way OUT now saves (`openFlow.ts`), and this is the way to save
// WITHOUT leaving - which is what an operator wants before pressing RUN, since
// RUN starts the stored flow.
//
// AND THAT IS WHY RUN IS LOCKED WHILE THE GRAPH IS DIRTY. `POST
// /api/flows/{id}/run` compiles the SAVED record; the validation pill grades the
// DRAFT (`flowsApi.compileDraft`). Unsaved, those are two different graphs, so
// the pill would be describing something RUN cannot start. The refusal and the
// pill's `DRAFT:` prefix are one decision taken in `canvasModel.ts`
// (`unsavedRunReason`, `checksWord`), so the two controls cannot drift.
//
// FOUR LEGACY CONTROLS DO NOT COME ACROSS, each for a stated reason:
//   * `< LIBRARY` - the Flows screen's own `flows-canvas-back` button and the
//     FLOWS sub-nav chip already lead there, and a third door to one place is
//     how two of them end up disagreeing.
//   * the provider badge - `shell/Header.tsx` renders `header-backend` at every
//     breakpoint.
//   * the library count sub-line - the shell's flows pill and the Flows screen's
//     own right-hand line already carry it.
//   * the `i` design-notes button - DELETED (wave section 6.1 defect 2). It
//     wrote `ui.notesOpen`, which `flowsSlice.ts` declares and NOTHING in the
//     repository reads: a control with a promise and no panel behind it.
//
// PLAN IS A ROUTE, NOT A LEGACY VIEW SWITCH (defect 1). The legacy button calls
// `useStore.getState().setView("sequence")`, which ARCHITECTURE.md section 9
// forbids from the new UI and which only works today because `legacyBridge.ts`
// maps it back to a hash. This calls `nav.sheet("planEditor")` directly.
//
// WHAT THE ROW IS NOT ALLOWED TO INVENT. Two numbers here have no server behind
// them: `run.etaS` has no publisher, so a null reads `-` and says why; and the
// validation pill prints GRAPH VALID only once the checker has actually
// answered, because a pill that went green before the first compile returned
// would be the exact green-while-wrong defect this project keeps paying for.

import { useRef, useState, type JSX } from "react";
import { useShallow } from "zustand/react/shallow";

import { useFlowRunControls } from "../../../../../components/flows/flowRunControls";
import type { FlowIssue } from "../../../../../lib/flowsApi";
import { accessPhrase, useCapability } from "../../../../../lib/caps";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { ActionButton, Chip, Label, Mono, Pill, Popover, type Tone } from "../../../../ui";
import { NxIcon } from "../../../../icons";
import {
  CHECKS_CLEAN_WHY, CHECKS_DRAFT_WHY, CHECKS_UNKNOWN_WHY, ETA_UNREPORTED,
  PLAN_TITLE, checksTone, checksWord, formatEta, lossCount, lossesWhy,
  saveLockReason, saveStateTone, saveStateWord, tonightLockReason,
  unsavedRunReason, worstLoss,
} from "./canvasModel";

/** A fresh `[]` in a selector defeats `useShallow` on every first render, so the
 *  empty case is one frozen module-level value. */
const EMPTY_ISSUES: readonly FlowIssue[] = Object.freeze([]);

/** RUN is the one control on this row that starts the rig moving, so it is a
 *  two-tap arm. STOP is NOT: emergency motion stops stay single-tap, which is
 *  the house rule for STOP / HALT / polar-STOP alike. */
export const RUN_ARM_LABEL = "CONFIRM RUN";

/** SAVE's own label and its accessible name. A plain word: the pill beside it
 *  carries the state, so the button does not have to change its own text. */
export const SAVE_LABEL = "SAVE";

export function FlowCanvasToolbar(): JSX.Element {
  // Primitive selectors, so a pan, a stage-status tick or a log line re-renders
  // none of this row. The one derived list goes through `useShallow`.
  const name = useStore((s) => s.flows.record?.name ?? "");
  const etaS = useStore((s) => s.flows.run.etaS);
  const checked = useStore((s) => s.flows.compiled !== null);
  // Also the parity harness's state marker: it waits for `data-flows-run` to
  // reach a phase word rather than sleeping. The toolbar already re-renders on
  // the phase through `useFlowRunControls`, so the subscription is free here and
  // would not be on the surface.
  const phase = useStore((s) => s.flows.run.phase);
  const issues = useStore(useShallow((s) => s.flows.compiled?.issues ?? EMPTY_ISSUES));
  // THE LOSSES, AS TWO PRIMITIVES. The pill used to grade `issues` alone, so a
  // compile that said "nothing will bind the dome or close it on an unsafe
  // reading during this run" left it reading GRAPH VALID in green while that
  // DANGER row sat two inches below on the same screen. Notes are not counted:
  // a clean flow with ten of them stays green, which is the other half of the
  // same fix.
  const losses = useStore((s) =>
    lossCount(s.flows.compiled?.unmapped, s.flows.compiled?.structural));
  const worst = useStore((s) =>
    worstLoss(s.flows.compiled?.unmapped, s.flows.compiled?.structural));
  // The two facts SAVE and RUN both read. `dirty` is the store's own edit flag,
  // written by every graph action in `flowsSlice`; `readonly` is the server's
  // word on an example flow, which `flowsSave` refuses before the network.
  const dirty = useStore((s) => s.flows.dirty);
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const save = useStore((s) => s.flowsSave);

  // RUN/STOP is the shared hook, never transcribed: it owns the abort route
  // (`/api/sequence/abort`, not a flows route), the rule that a timed-out abort
  // is not a failed abort, and the 409 `unmapped` confirm.
  const { running, reason: hookRunReason, explain, act } = useFlowRunControls();

  // Order matters: the rig's own refusals (no capability, no camera, link down)
  // outrank ours, because a viewer who also has an unsaved edit is refused for
  // the reason that will still be true after they save. STOP is never blocked by
  // an unsaved edit - the mount is moving.
  const runReason = hookRunReason ?? (running ? null : unsavedRunReason(dirty, readonly));

  // Tonight is gated on view.site_derived, NOT view.status: an audit of this
  // codebase recovered the observatory to 2.9 km from three viewer-legal
  // requests, which is why `/api/flows/{id}/tonight` sits behind the
  // derived-site capability.
  const canViewSiteDerived = useCapability("view.site_derived");

  const [issuesOpen, setIssuesOpen] = useState(false);
  const checksRef = useRef<HTMLSpanElement | null>(null);

  const openChecks = issues.length;
  const checksText = checksWord(checked, openChecks, dirty, losses);
  const checksPillTone: Tone = checksTone(checked, openChecks, dirty, worst);
  const saveReason = saveLockReason(dirty, readonly);
  const stateWord = saveStateWord(dirty, readonly);

  return (
    <div className="nx-flow-toolbar" data-testid="flow-toolbar" data-flows-run={phase}>
      <span className="nx-flow-toolbar-name">
        <Label size={10}>FLOW</Label>
        <span className="nx-flow-toolbar-title" title={name || undefined}>
          <Mono size={12} data-testid="flow-toolbar-name">{name || "no flow open"}</Mono>
        </span>
      </span>

      <Chip data-testid="flow-plan" onClick={() => nav.sheet("planEditor")}>
        <span title={PLAN_TITLE}>PLAN</span>
      </Chip>

      {/* Never colour alone: the WORD changes with the state, and the list
          behind it is the checker's own sentences. */}
      <span ref={checksRef} className="nx-flow-toolbar-checks">
        <Pill
          data-testid="flow-checks"
          tone={checksPillTone}
          ariaLabel={`Graph checks: ${checksText}`}
          onClick={() => setIssuesOpen((v) => !v)}
        >
          {checksText}
        </Pill>
      </span>
      <Popover
        open={issuesOpen}
        anchorRef={checksRef}
        onClose={() => setIssuesOpen(false)}
        data-testid="flow-checks-list"
      >
        {/* The draft sentence rides ABOVE the verdict rather than replacing it:
            the issues are still the checker's, they just describe a graph the
            rig has not been given yet. */}
        {checked && dirty && <p className="nx-flow-issue">{CHECKS_DRAFT_WHY}</p>}
        {/* A loss is not a check, so it gets its own line rather than a row in
            the checker's list - and it says WHERE the sentences are, because
            the pill's count is a number and the FLOW column has the words. */}
        {checked && losses > 0 && <p className="nx-flow-issue">{lossesWhy(losses)}</p>}
        {!checked ? (
          <p className="nx-flow-issue">{CHECKS_UNKNOWN_WHY}</p>
        ) : openChecks === 0 ? (
          // Suppressed while there are losses: "nothing to flag" is true of the
          // CHECKER and reads as an all-clear for the graph.
          losses > 0 ? null : <p className="nx-flow-issue">{CHECKS_CLEAN_WHY}</p>
        ) : (
          <ul className="nx-flow-issues">
            {issues.map((i, n) => <li key={`${i.text}-${n}`} className="nx-flow-issue">{i.text}</li>)}
          </ul>
        )}
      </Popover>

      <span className="nx-flow-toolbar-spacer" />

      {/* Running only. The ETA is kept even though the Session column carries
          one, because that column is hidden while the Session hub is open -
          which is exactly when this canvas is on screen. */}
      {running && (
        <span
          className="nx-flow-toolbar-eta"
          data-testid="flow-eta"
          title={etaS == null ? ETA_UNREPORTED : undefined}
        >
          <Label size={10}>ETA</Label>
          <Mono size={12}>{formatEta(etaS)}</Mono>
        </span>
      )}

      {/* The dirty cue, in a word. It sits beside SAVE so the state and the
          control that changes it read as one thing. */}
      <span data-testid="flow-save-state">
        <Pill tone={saveStateTone(dirty, readonly)} ariaLabel={`This flow: ${stateWord}`}>
          {stateWord}
        </Pill>
      </span>

      <ActionButton
        kind="secondary"
        data-testid="flow-save"
        glyph={<NxIcon name="check" size={15} />}
        lockedReason={saveReason}
        onExplain={explain}
        onPress={() => { void save(); }}
        ariaLabel={`${SAVE_LABEL} this flow`}
      >
        {SAVE_LABEL}
      </ActionButton>

      <ActionButton
        kind="ghost"
        data-testid="flow-tonight"
        glyph={<NxIcon name="clock" size={15} />}
        lockedReason={canViewSiteDerived ? null : tonightLockReason(accessPhrase("view.site_derived"))}
        onExplain={explain}
        onPress={() => nav.sheet("flowTonight")}
      >
        TONIGHT
      </ActionButton>

      <ActionButton
        kind={running ? "danger" : "primary"}
        data-testid="flow-run"
        glyph={<NxIcon name={running ? "stop" : "play"} size={15} />}
        lockedReason={runReason}
        onExplain={explain}
        arm={running ? undefined : { label: RUN_ARM_LABEL }}
        onPress={act}
      >
        {running ? "STOP" : "RUN"}
      </ActionButton>
    </div>
  );
}

export default FlowCanvasToolbar;
