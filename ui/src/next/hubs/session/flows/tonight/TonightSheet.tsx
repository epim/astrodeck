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

import { useEffect, useMemo, type JSX } from "react";

import type { TonightTab } from "../../../../../components/flows/flowsTypes";
import { capAllowed } from "../../../../../lib/caps";
import { runIsLive } from "../../../../../lib/lastSessionFrame";
import { usePrincipal, useResumeArm, useSeq, useStore } from "../../../../../store";
import { NxIcon } from "../../../../icons";
import { nav } from "../../../../router";
import { explainLock } from "../../../../shell/explain";
import { ActionButton, LockNote, Mono, Segmented, Sheet } from "../../../../ui";
import type { SheetProps } from "../../../sheets";
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

export function FlowTonightSheet({ params }: SheetProps): JSX.Element {
  const tab = useStore((s) => s.flows.ui.tonightTab);
  const setUi = useStore((s) => s.flowsSetUi);
  const fetchTonight = useStore((s) => s.flowsFetchTonight);
  const flowsOpen = useStore((s) => s.flowsOpen);
  const flowId = useStore((s) => s.flows.record?.id ?? "");
  const flowName = useStore((s) => s.flows.record?.name ?? "");
  const payload = useStore((s) => s.flows.tonight);
  const loading = useStore((s) => s.flows.tonightLoading);
  const error = useStore((s) => s.flows.tonightError);

  const principal = usePrincipal();
  const locked = capAllowed(principal, "view.site_derived") ? null : TONIGHT_LOCK_REASON;

  // Parked, without a second request: `GET /api/sequence/resume-arm` already
  // returns the auto_resume-armed dormant session, and `SessionStore.armed()`
  // IS "dormant and auto_resume". Asking another route for the same fact would
  // be a chance for the two to disagree.
  const seq = useSeq();
  const resumeArm = useResumeArm();
  const armed = resumeArm?.armed ?? null;
  const parked = !runIsLive(seq) && armed != null
    && armed.origin === "flow" && armed.origin_id === flowId && flowId !== "";

  // A deep link carries the flow it was opened for. Idempotent: re-opening the
  // flow already on the canvas would discard an unsaved edit and re-run the
  // compile for nothing, so this fires only when they differ.
  const wantId = params.id ?? "";
  useEffect(() => {
    if (locked || !wantId || wantId === flowId) return;
    void flowsOpen(wantId);
  }, [locked, wantId, flowId, flowsOpen]);

  // Re-resolved when the sheet opens on a flow, not cached: this is an answer
  // about a specific instant, and a sheet reopened two hours later would
  // otherwise show a window that has since closed. `tab` is deliberately NOT a
  // dependency - four views of one payload are not four questions.
  useEffect(() => {
    if (locked || !flowId) return;
    void fetchTonight();
  }, [locked, flowId, fetchTonight]);

  // Memoised on the payload: the sheet re-renders on every store tick that
  // touches the flows slice, and `readTonight` walks every target curve and
  // every story row.
  const read = useMemo(() => readTonight(payload), [payload]);
  const live = nightLine(read?.night ?? null);

  let body: JSX.Element;
  if (locked) {
    body = (
      <p className="nx-tn-note" data-testid="tonight-locked">
        Dusk, astronomical dark, the moon and every target window are worked out
        from where the rig is standing, so none of the four tabs can be drawn
        without that access. Nothing was requested from the rig.
      </p>
    );
  } else if (!flowId) {
    body = <p className="nx-tn-note" data-testid="tonight-no-flow">{TONIGHT_NO_FLOW}</p>;
  } else if (tab === "plan") {
    // PLAN does not touch /tonight at all - it renders the compile - so it
    // answers even while the site is unset and every other tab is refusing.
    body = <TonightPlanBlock />;
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
      sub={flowName || "no flow open"}
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

        {body}
      </div>
    </Sheet>
  );
}

export default FlowTonightSheet;
