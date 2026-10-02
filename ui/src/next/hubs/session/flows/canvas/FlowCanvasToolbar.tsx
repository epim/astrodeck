// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowCanvasToolbar.tsx - the canvas's own 48 px row (wave R7 parity row A1).
//
// NINE THINGS, LEFT TO RIGHT, AND NOTHING THE SHELL ALREADY RENDERS. The shell
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
//   8. RUN / STOP, which reads CONTINUE over a dormant session
//   9. START OVER, beside CONTINUE only (#189 S5)
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
// THE COUNTS LINE HANGS UNDER THIS ROW (Revision 2 ruling 2, S4 orchestrator
// ruling 8). A flow saved before new blocks counted accepted subs still counts
// every sub taken, rejected ones included, until it is next saved, and the
// canvas says so in one line directly under SAVE - the control that ends it.
// It is a standing fact about the flow, not an event, so it is not a log line
// that scrolls out of the strip and not a toast that times out: it stays for
// as long as the graph on screen counts attempts, and it has no dismiss. It is
// read from the graph on every store write (`countsNotice`), because the save
// that switches the counts writes the switch into the graph on screen
// (`acceptCounts`) without reopening the flow; a line captured when the flow
// opened would go on promising a switch the save had already made. The phone
// stage list carries the same line from the same function.
//
// WHAT THE ROW IS NOT ALLOWED TO INVENT. Two numbers here could be printed
// with no server behind them. The ETA is the rig's own `progress.eta_s`, read
// from the sequence state while the run the rig is on is this flow's
// (`useFlowRunReadouts`, #189 S5), with "hops not yet costed" under it while
// that clock prices hops it has not measured; for any other run it is
// `run.etaS`, which has no publisher, so a null reads `-` and says why. And
// the validation pill prints GRAPH VALID only once the checker has actually
// answered, because a pill that went green before the first compile returned
// would be the exact green-while-wrong defect this project keeps paying for.
//
// RUN SAYS WHAT IT WILL DO (#189 S5, spec 5.9). With a dormant session the
// button reads `CONTINUE M31 MOSAIC (night 3, 412/1890 subs)`, the one copy
// every RUN surface prints (`useFlowRunControls().copy`, from the progress
// route alone), and START OVER sits beside it behind a confirm. Its first tap
// arms it with the same verb and numbers, `CONFIRM CONTINUE (night 3,
// 412/1890 subs)` (`runArm`, #474), so the label at the moment of commitment
// still says what the second tap does.
//
// AND WHAT AN ARMED AUTO-RESUME WILL DO (#473, S7 orchestrator ruling 1).
// While the session CONTINUE would carry on is armed and the flow was saved
// after the version it froze, a line under the row says that dusk will
// replay that version (`replayNotice`), since only CONTINUE applies the
// saved edits. The phone stage list and the classic editor carry the same
// line from the same function.

import { useRef, useState, type JSX } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  START_OVER_LABEL, useFlowRunControls, useFlowRunReadouts, type RunCopy,
} from "../../../../../components/flows/flowRunControls";
import { countsNotice } from "../../../../../components/flows/countsNotice";
import { replayNotice } from "../../../../../components/flows/replayNotice";
import type { FlowIssue } from "../../../../../lib/flowsApi";
import { accessPhrase, useCapability } from "../../../../../lib/caps";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { ActionButton, BannerCard, Chip, Label, Mono, Pill, Popover, type Tone } from "../../../../ui";
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

/** The word an armed RUN leads with, before the verb it confirms. */
export const RUN_ARM_WORD = "CONFIRM";

/** The two-tap arm of both #/next RUN buttons, this row's and the phone stage
 *  sheet's footer (#474): the one helper, so the two cannot confirm a press
 *  in different words.
 *
 *  RUN is the one control that starts the rig moving, so it is a two-tap arm.
 *  STOP is NOT, and gets no arm: emergency motion stops stay single-tap, the
 *  house rule for STOP / HALT / polar-STOP alike.
 *
 *  GATED ON `stopsOnPress`, NEVER ON `copy.verb` (#647). The copy's verb
 *  reads from `running`, which bridges the client's own optimistic latch for
 *  up to `RUN_PHASE_BRIDGE_MS` past a press (`flowRunControls.tsx`) - so a
 *  label can read STOP for a press `act()` would actually START (`ours`
 *  alone decides the action, #162). Skipping the confirm because the LABEL
 *  said STOP let a stale button silently start a brand new run with no
 *  CONFIRM RUN at all, which is #647. `stopsOnPress` is the same `ours` the
 *  action itself is decided on, so the confirm and the action can never
 *  disagree about which one this press is.
 *
 *  THE ARMED LABEL SAYS WHAT THE SECOND TAP DOES. `ActionButton` swaps the
 *  whole label for `arm.label` on the first tap, and this used to be a fixed
 *  "CONFIRM RUN": over a dormant session the night and the counts being
 *  continued vanished at the moment of commitment, and "RUN" sat beside
 *  START OVER, the press that really starts afresh. So the label is
 *  `CONFIRM <verb>` with the copy's parenthetical kept: "CONFIRM CONTINUE
 *  (night 3, 194/480 subs)", and "CONFIRM RUN" when there is nothing to
 *  continue.
 *
 *  THE NAME IS LEFT OUT. The label is one string in `.nx-btn-label`, which
 *  ellipsises from the RIGHT, so every character in front of the
 *  parenthetical is one more that pushes the night and the counts off a
 *  narrow button first. The flow's name is already on both surfaces, in the
 *  row's FLOW title and the sheet's title. */
export function runArm(copy: RunCopy, stopsOnPress: boolean): { label: string } | undefined {
  if (stopsOnPress) return undefined;
  return { label: [`${RUN_ARM_WORD} ${copy.verb}`, copy.detail].filter((p) => p !== "").join(" ") };
}

/** SAVE's own label and its accessible name. A plain word: the pill beside it
 *  carries the state, so the button does not have to change its own text. */
export const SAVE_LABEL = "SAVE";

/** The run line's box: one row, the parts on a shared baseline. `minWidth: 0`
 *  lets it narrow inside `.nx-btn-label`, whose `overflow: hidden` already
 *  lets the label itself give up width. */
const RUN_WORDS_STYLE = { display: "flex", alignItems: "baseline", gap: "0.5em", minWidth: 0 } as const;
/** The flow's name: the one part that gives up width, with an ellipsis. */
const RUN_NAME_STYLE = {
  minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
} as const;
/** The verb and the parenthetical: never shrunk, never wrapped. */
const RUN_FIXED_STYLE = { flex: "none", whiteSpace: "nowrap" } as const;

/** The RUN button's words in the #/next chrome: the one copy (`runCopy`),
 *  drawn so that a narrow button cuts the flow's NAME with an ellipsis and
 *  never the parenthetical, which carries the night and the counts being
 *  continued. `.nx-btn-label` alone would ellipsise the whole line from the
 *  right, taking the counts first.
 *
 *  THE SPACES ARE TEXT. The gap is what the eye sees, but a gap is not a
 *  character, so without the " " between the parts the button's text, and
 *  the name a screen reader builds from it, would run "CONTINUEM31 MOSAIC".
 *  A white-space-only run between flex items is not rendered, so the spaces
 *  cost the layout nothing.
 *
 *  RUN and STOP are the bare word, exactly as before. Inline styles, as
 *  canvas.css is the canvas surface's stylesheet; shared with the phone stage
 *  sheet's footer, so the two #/next RUN buttons draw one line one way. */
export function RunCopyWords({ copy }: { copy: RunCopy }): JSX.Element {
  if (copy.verb !== "CONTINUE") return <>{copy.verb}</>;
  return (
    <span style={RUN_WORDS_STYLE} data-testid="run-copy">
      <span style={RUN_FIXED_STYLE} data-testid="run-copy-verb">{copy.verb}</span>
      {copy.name !== "" && (
        <>{" "}<span style={RUN_NAME_STYLE} data-testid="run-copy-name">{copy.name}</span></>
      )}
      {copy.detail !== "" && (
        <>{" "}<span style={RUN_FIXED_STYLE} data-testid="run-copy-detail">{copy.detail}</span></>
      )}
    </span>
  );
}

export function FlowCanvasToolbar(): JSX.Element {
  // Primitive selectors, so a pan, a stage-status tick or a log line re-renders
  // none of this row. The one derived list goes through `useShallow`.
  const name = useStore((s) => s.flows.record?.name ?? "");
  // The rig's ETA while its run is this flow's, and `run.etaS` otherwise, as
  // primitives through `useShallow`.
  const { etaS, etaNote } = useFlowRunReadouts();
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
  // A string or null, so exact under Object.is: a pan or a status tick
  // re-renders nothing here.
  const countsLine = useStore((s) => countsNotice(s.flows.graph, s.flows.countsNote));
  // The replay line (#473, S7 orchestrator ruling 1), also a string or null:
  // the progress answer's armed session against the record's saved time, so
  // a save that moves `updated_ts` raises it and the re-read after CONTINUE
  // (a live session, so not armed, frozen at the saved version) takes it
  // down, each without a reopen.
  const replayLine = useStore((s) => replayNotice(s.flows.progress, s.flows.record));

  // RUN/STOP is the shared hook, never transcribed: it owns the abort route
  // (`/api/sequence/abort`, not a flows route), the rule that a timed-out abort
  // is not a failed abort, and the 409 `unmapped` confirm.
  const {
    running, reason: hookRunReason, explain, act, copy, startOver, stopsOnPress,
  } = useFlowRunControls();

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

  const row = (
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
          {etaNote && (
            <span data-testid="flow-eta-note">
              <Mono size={10} tone="dim">{etaNote}</Mono>
            </span>
          )}
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
        arm={runArm(copy, stopsOnPress)}
        onPress={act}
      >
        <RunCopyWords copy={copy} />
      </ActionButton>

      {/* START OVER, beside CONTINUE and only beside it (spec 5.9). Its guard
          is the confirm `startOver` asks, not an arm: the confirm says what
          the press leaves behind, which a second tap cannot. Locked for the
          same reasons RUN is, unsaved edits included, since it starts the
          stored flow too. */}
      {copy.verb === "CONTINUE" && (
        <ActionButton
          kind="secondary"
          data-testid="flow-start-over"
          lockedReason={runReason}
          onExplain={explain}
          onPress={startOver}
        >
          {START_OVER_LABEL}
        </ActionButton>
      )}
    </div>
  );

  // Siblings of the row, not inside it: the row is 48 px of controls, and the
  // host's flex column gives each line its own height under them. The replay
  // line is a warning, not a fact about counting: the night will shoot
  // something other than the graph on screen unless CONTINUE is pressed.
  return (
    <>
      {row}
      {countsLine && (
        <BannerCard tone="info" text={countsLine} data-testid="flow-canvas-counts" />
      )}
      {replayLine && (
        <BannerCard tone="warn" text={replayLine} data-testid="flow-canvas-replay" />
      )}
    </>
  );
}

export default FlowCanvasToolbar;
