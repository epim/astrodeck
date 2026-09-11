// NowScreen.tsx - SESSION / NOW: the vertical order of `07-session-running.html`.
//
// Run header, campaign ledger, incident, pool chips, the 250 px live stack,
// channel strip, vitals, integration, armed rules, files/stack, pause/stop -
// in that order, top to bottom, with each block absent rather than empty when
// it has nothing to say.
//
// WHAT COUNTS AS "A RUN TO SHOW" is wider than "a run that is moving. The
// engine leaves `state` on its terminal word until the next run starts, and
// what that run MADE is still the thing the person opening this screen at
// breakfast wants - so a finished run keeps its header, its picture and its
// FILES button, and loses only the controls that would act on a rig that has
// already stopped. `NO SESSION RUNNING` is for a rig that has genuinely never
// been asked to do anything this process, and it answers the three questions
// that state actually raises (see `NowEmpty`).

import type { JSX } from "react";

import { useSeq } from "../../../../store";
import { ArmedRules } from "./ArmedRules";
import { CampaignLedger } from "./CampaignLedger";
import { ChannelStrip } from "./ChannelStrip";
import { IncidentStack } from "./IncidentStack";
import { IntegrationBar } from "./IntegrationBar";
import { Interrupted } from "./Interrupted";
import { LiveStack } from "./LiveStack";
import { NowBanners } from "./NowBanners";
import { NowEmpty } from "./NowEmpty";
import { PoolChips } from "./PoolChips";
import { RunControls } from "./RunControls";
import { RunHeader } from "./RunHeader";
import { VitalsBand } from "./VitalsBand";

const SHOWS_A_RUN = new Set([
  "running", "holding", "paused", "aborting", "nina_native",
  "complete", "aborted", "error",
]);

// THE STATE IS THE WHOLE PREDICATE (review #56). It used to also require
// `progress != null`, which is a different question: the engine publishes
// `state: "running"` the moment a run is accepted and attaches `progress` only
// on the first frame boundary, so for the seconds between them - a slew, a
// filter change, a first 300 s sub - the screen said NO SESSION RUNNING over a
// working rig, and said it hardest at exactly the moment someone is watching to
// see whether their press took. `state` alone cannot make that mistake: the
// store's own resting value is "idle", which is not in this set, so a rig that
// has genuinely never been asked to do anything still gets `NowEmpty`. Every
// block below reads `progress` through `?.` and renders its absent case, which
// is what makes dropping the clause safe rather than merely shorter.
export function NowScreen(): JSX.Element {
  const seq = useSeq();
  const hasRun = SHOWS_A_RUN.has(seq.state);

  return (
    <div
      data-testid="session-now"
      style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px" }}
    >
      <NowBanners />
      <Interrupted />
      {hasRun ? (
        <>
          <RunHeader />
          <CampaignLedger />
          <IncidentStack />
          <PoolChips />
          <LiveStack height={250} />
          <ChannelStrip />
          <VitalsBand cells={4} />
          <IntegrationBar legend />
          <ArmedRules />
          <RunControls size="lg" />
        </>
      ) : (
        <NowEmpty />
      )}
    </div>
  );
}
