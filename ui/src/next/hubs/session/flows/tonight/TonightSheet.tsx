// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// TonightSheet.tsx - "what is this flow actually going to do tonight?", in
// four readings of the same answer (parity row A18): the night as a picture,
// the night as sentences, the plan the engine will literally run, and how much
// of the campaign is already banked.
//
// THE REFUSAL IS THE PRODUCT. `GET /api/flows/{id}/tonight` can legitimately
// answer `{ok: false, reason: "No observatory site is set, so there is no night
// to resolve ..."}`, and `tonight.py` refuses instead of guessing because "the
// alternative - a plausible dusk from a default site - is the failure this
// whole panel exists to avoid: an operator planning an evening around a number
// nobody measured". So `reason` is rendered as PROSE, never as error chrome,
// and it is never merged with a TRANSPORT failure (the request did not arrive),
// which is a different thing and says so.
//
// THE CAP IS PART OF THE PRODUCT TOO. The route is gated on `view.site_derived`
// rather than `view.status` because an audit of this codebase recovered the
// observatory to 2.9 km from three viewer-legal requests. A role that does not
// hold it sees this sheet, sees the four tabs, and is told exactly why they are
// not drawn - and no request leaves the browser.
//
// TAB SWITCHING DOES NOT REFETCH. The answer is about one instant, so it is
// re-resolved when the sheet OPENS (and when the open flow changes), not when
// the reader moves between four views of the same payload.
//
// THE READ SAVES THE CANVAS FIRST (#688). `flowsFetchTonight` stores the edit
// and compiles it before it asks the route, because the route describes the
// STORED flow and PLAN is the compile: before that, deleting a block and
// adding two others left STORY and PLAN describing the flow as it was last
// saved until the flow was left and opened again. What this sheet adds is the
// two cases where the stored flow still is not the canvas once the flush is
// done: an example (its edits are never saved) and an edit the save did not
// keep, each said above the tabs that read the stored flow.

import { useEffect, useMemo, useState, type JSX } from "react";

import { compiledIsCurrent } from "../../../../../components/flows/flowsSlice";
import { tonightStoredNote, type TonightTab } from "../../../../../components/flows/flowsTypes";
import { capAllowed } from "../../../../../lib/caps";
import { runIsLive } from "../../../../../lib/lastSessionFrame";
import { usePrincipal, useResumeArm, useSeq, useStore } from "../../../../../store";
import { NxIcon } from "../../../../icons";
import { nav } from "../../../../router";
import { explainLock } from "../../../../shell/explain";
import { ActionButton, LockNote, Mono, Segmented, Sheet } from "../../../../ui";
import type { SheetProps } from "../../../sheets";
import { flowOpenFailure, libraryErrorNow, openFlowById } from "../openFlow";
import { TonightCampaignCard } from "./TonightCampaignCard";
import { TonightPlanBlock } from "./TonightPlanBlock";
import { TonightStoryList } from "./TonightStoryList";
import { TonightTimelineCard } from "./TonightTimelineCard";
import {
  TONIGHT_LOCK_REASON, TONIGHT_TABS, TONIGHT_TAB_LABEL, TONIGHT_TAB_SUB,
  nightLine, readTonight, resumesLine,
} from "./tonightModel";
import "./tonight.css";

/** Said when no flow is open at all - the sheet has an id-shaped route but its
 *  source is the flow on the canvas, and a night resolved for nothing would be
 *  a night about nothing. */
export const TONIGHT_NO_FLOW =
  "No flow is open, so there is no graph to resolve a night for. Open a flow from MY FLOWS first.";

/** Shown while the open this sheet asked for (`?open=`) is still out, in place
 *  of the four tabs (#553). Read-only, so a wrong flow drawn here cannot
 *  START anything the way a stray RUN could - but it can still MISLEAD: this
 *  sheet used to resolve and draw whatever flow was already open under a
 *  route naming a different one, the moment its own open failed to land
 *  (`flowsOpen` swallows its failure and leaves the previous record in
 *  place). `mine` is the one gate every read of the open record now goes
 *  through. */
export const TONIGHT_OPENING = "This flow has not opened yet.";

export function FlowTonightSheet({ params }: SheetProps): JSX.Element {
  const tab = useStore((s) => s.flows.ui.tonightTab);
  const setUi = useStore((s) => s.flowsSetUi);
  const fetchTonight = useStore((s) => s.flowsFetchTonight);
  const flowId = useStore((s) => s.flows.record?.id ?? "");
  const flowName = useStore((s) => s.flows.record?.name ?? "");
  const payload = useStore((s) => s.flows.tonight);
  const loading = useStore((s) => s.flows.tonightLoading);
  const error = useStore((s) => s.flows.tonightError);
  const dirty = useStore((s) => s.flows.dirty);
  const readonly = useStore((s) => s.flows.record?.readonly === true);
  // PLAN reads `flows.compiled`, which `flowsFetchTonight` brings up to date
  // with the canvas as part of the flush it makes before it reads (#688). Until
  // that compile lands, the one in hand is of an earlier graph.
  const planPending = useStore((s) => s.flows.tonightLoading && !compiledIsCurrent(s.flows));

  const principal = usePrincipal();
  const locked = capAllowed(principal, "view.site_derived") ? null : TONIGHT_LOCK_REASON;

  // A deep link carries the flow it was opened for, in `?open=` - the same
  // param name every other sheet this canvas opens reads (FlowStagesPhoneSheet
  // itself, FlowsCanvasHost, FlowsScreen, FlowFrameSheet); this sheet used to
  // read `?id=` instead, so FlowStagesPhoneSheet's TONIGHT row (which, like
  // every row out of it, carries `?open=`) never actually named a flow here
  // (backlog WP-08, #553). Idempotent: re-opening the flow already on the
  // canvas would discard an unsaved edit and re-run the compile for nothing,
  // so this fires only when they differ.
  const wantId = params.open ?? "";

  /** The last open this sheet asked for that did not land: which flow, the
   *  record that was open when it was asked, and the reason. A new attempt
   *  starts exactly when `wantId` or `flowId` changes, so a failure recorded
   *  under an earlier one no longer matches. */
  const [failure, setFailure] = useState<
    { id: string; from: string | null; reason: string } | null
  >(null);

  // THROUGH `openFlowById`, NOT A BARE `flowsOpen` (#553). `flowsOpen`
  // swallows its own failure and leaves the record that was open before in
  // place, so a failed read of the flow this route names used to resolve and
  // draw the NIGHT of whatever flow was open before, under a link naming a
  // different one - read-only, so it misleads rather than starts anything,
  // but it is the same defect class. `mine` below is the one gate every read
  // of the open record goes through. A late answer to an earlier attempt
  // (another id, or the record changed under it) is dropped.
  useEffect(() => {
    if (locked || !wantId || wantId === flowId) return;
    let current = true;
    const before = libraryErrorNow();
    void openFlowById(wantId).then((landed) => {
      if (current && !landed) setFailure({ id: wantId, from: flowId, reason: flowOpenFailure(before) });
    });
    return () => { current = false; };
  }, [locked, wantId, flowId]);

  // A route with no `?open=` draws whatever is open (the canvas's own TONIGHT
  // row, which never names a flow the store does not already hold); one that
  // does is "mine" only once its own open has landed.
  const mine = wantId === "" || wantId === flowId;
  const failed = !mine && failure !== null && failure.id === wantId && failure.from === flowId
    ? failure.reason : null;

  // Parked, without a second request: `GET /api/sequence/resume-arm` already
  // returns the auto_resume-armed dormant session, and `SessionStore.armed()`
  // IS "dormant and auto_resume". Asking another route for the same fact would
  // be a chance for the two to disagree. Gated on `mine`: while waiting,
  // `flowId` is another flow's, and a resume line about it would describe the
  // wrong session under this route's title.
  const seq = useSeq();
  const resumeArm = useResumeArm();
  const armed = resumeArm?.armed ?? null;
  const parked = mine && !runIsLive(seq) && armed != null
    && armed.origin === "flow" && armed.origin_id === flowId && flowId !== "";

  // Re-resolved when the sheet opens on a flow, not cached: this is an answer
  // about a specific instant, and a sheet reopened two hours later would
  // otherwise show a window that has since closed. `tab` is deliberately NOT a
  // dependency - four views of one payload are not four questions. Gated on
  // `mine` too: while waiting, `flowId` is not this route's flow, and asking
  // for ITS tonight would draw an answer about the wrong one the moment it
  // arrived.
  useEffect(() => {
    if (locked || !flowId || !mine) return;
    void fetchTonight();
  }, [locked, flowId, mine, fetchTonight]);

  // Memoised on the payload: the sheet re-renders on every store tick that
  // touches the flows slice, and `readTonight` walks every target curve and
  // every story row. Read only once this sheet's own flow is open - while
  // waiting, `payload` is another flow's cached answer (or none).
  const read = useMemo(() => (mine ? readTonight(payload) : null), [mine, payload]);
  const live = mine ? nightLine(read?.night ?? null) : "";

  // Said once the flush is done (`dirty` means nothing while the PUT is still
  // out), only over a payload that is drawn, and never on PLAN, which renders
  // a compile of the canvas itself. `!read` is what holds it back while an
  // edited flow is being saved: `flowsFetchTonight` clears the answer in hand
  // for exactly that flow, so nothing is drawn until the PUT has answered. An
  // example's answer is kept (nothing replaces it), and its note is true from
  // the first frame.
  const storedNote = locked || !mine || !flowId || tab === "plan" || error || !read
    ? null : tonightStoredNote(readonly, dirty);

  let body: JSX.Element;
  if (locked) {
    body = (
      <p className="nx-tn-note" data-testid="tonight-locked">
        Dusk, astronomical dark, the moon and every target window are worked out
        from where the rig is standing, so none of the four tabs can be drawn
        without that access. Nothing was requested from the rig.
      </p>
    );
  } else if (!mine) {
    // NOTHING OF THE OPEN RECORD (#553): not its night, its plan or its
    // campaign, every one of which is another flow's (or none) while this is
    // up. Takes priority over PLAN's own "answers even while the site is
    // unset" - a compile of the wrong flow is not an exception to that rule.
    body = (
      <p className="nx-tn-note" data-testid={failed !== null ? "tonight-open-failed" : "tonight-opening"}>
        {failed ?? TONIGHT_OPENING}
      </p>
    );
  } else if (!flowId) {
    body = <p className="nx-tn-note" data-testid="tonight-no-flow">{TONIGHT_NO_FLOW}</p>;
  } else if (tab === "plan") {
    // PLAN does not touch /tonight at all - it renders the compile - so it
    // answers even while the site is unset and every other tab is refusing.
    // Not the compile in hand while the flush's own is still out: that is the
    // plan of the flow as last compiled, which is the report this fixes.
    body = planPending ? (
      <p className="nx-tn-note" data-testid="tonight-plan-pending">
        Compiling this graph - the plan appears when the server answers.
      </p>
    ) : <TonightPlanBlock />;
  } else if (error) {
    body = (
      <div className="nx-tn-stack" data-testid="tonight-error">
        <p className="nx-tn-note">
          Tonight could not be read from the rig: {error}. Nothing below would be
          about tonight, so nothing is drawn.
        </p>
        <ActionButton
          kind="secondary"
          onPress={() => { void fetchTonight(); }}
          data-testid="tonight-retry"
        >
          RETRY
        </ActionButton>
      </div>
    );
  } else if (!read) {
    body = (
      <p className="nx-tn-note" data-testid="tonight-pending">
        {loading
          ? "Resolving tonight - dusk, astronomical dark, the moon, and each target's window."
          : "Tonight has not been resolved for this flow yet."}
      </p>
    );
  } else if (tab === "campaign") {
    // CAMPAIGN, like STORY, survives a refusal: it reads the GRAPH, not the
    // ephemeris, so "no site is set" does not stop it saying which members owe
    // what. A tab that blanked on a refusal it does not depend on would look
    // like a bug in the campaign rather than in the site.
    body = <TonightCampaignCard campaign={read.campaign} />;
  } else if (tab === "story") {
    // On a refusal the server already carries `reason` as a story row, so STORY
    // needs no special case: the sentence arrives through the same path as
    // every other one.
    body = <TonightStoryList story={read.story} brief={read.brief} />;
  } else if (!read.ok) {
    body = (
      <p className="nx-tn-note" data-testid="tonight-refusal">{read.reason}</p>
    );
  } else {
    body = (
      <TonightTimelineCard
        night={read.night}
        flats={read.flats}
        moon={read.moon}
        targets={read.targets}
      />
    );
  }

  return (
    <Sheet
      data-testid="session-flow-tonight"
      title="TONIGHT"
      sub={mine ? flowName || "no flow open" : undefined}
      live={live === "" ? undefined : live}
      icon={<NxIcon name="clock" size={18} />}
      onBack={() => nav.back()}
    >
      <div className="nx-tn-body">
        <Segmented<TonightTab>
          label="Tonight view"
          data-testid="tonight-tab"
          className="nx-tn-tabs"
          options={TONIGHT_TABS.map((t) => ({
            value: t,
            label: TONIGHT_TAB_LABEL[t],
            sub: TONIGHT_TAB_SUB[t],
          }))}
          value={tab}
          onChange={(t) => setUi({ tonightTab: t })}
          lockedReason={locked}
          onExplain={explainLock}
        />

        <LockNote reason={locked} data-testid="tonight-lock" />

        {parked && (
          <Mono size={10} tone="accent2" data-testid="tonight-parked">
            {resumesLine(read?.night.dusk_unix ?? null)}
          </Mono>
        )}

        {storedNote && (
          <p className="nx-tn-note" data-testid="tonight-stored-note">{storedNote}</p>
        )}

        {body}
      </div>
    </Sheet>
  );
}

export default FlowTonightSheet;
