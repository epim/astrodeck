// SessionColumn.tsx - the desktop's persistent Session / Now, condensed
// (ARCHITECTURE.md section 4, plan section E.4).
//
// IT MOUNTS THE HUB'S OWN COMPONENTS. Not copies of them, not a reduced
// re-render of the same numbers - the SAME seven exports from
// `hubs/session/now/`, at a different density. Each one reads the store itself
// and takes only layout props, which is the whole reason that contract exists:
// two implementations of the phase mapping would eventually disagree, and the
// column is the thing someone glances at while working on another hub, so it
// is the copy they would believe.
//
// It is hidden while the Session hub itself is open (`NextApp` decides that,
// not this file): two live stacks side by side is not redundancy, it is a
// second thing to check against the first.

import type { JSX } from "react";

import { useStore } from "../../store";
import { ActionButton } from "../ui";
import { nav } from "../router";
import {
  IncidentStack, IntegrationBar, LiveStack, NowEmpty, RunControls, RunHeader, VitalsBand,
} from "../hubs/session/now";

/** The same predicate the Now screen uses: a finished run is still worth
 *  showing - what it MADE is the thing being glanced at - and only a rig that
 *  has never been asked to do anything gets the empty card. */
const SHOWS_A_RUN = new Set([
  "running", "holding", "paused", "aborting", "nina_native",
  "complete", "aborted", "error",
]);

export function SessionColumn(): JSX.Element {
  const state = useStore((s) => s.sequence.state);
  const hasProgress = useStore((s) => s.sequence.progress != null);
  const live = SHOWS_A_RUN.has(state) && hasProgress;

  if (!live) {
    return (
      <aside className="nx-session-col" aria-label="Session" data-testid="session-column">
        <NowEmpty compact />
        <ActionButton kind="secondary" full onPress={() => nav.go("/session/now")}>
          OPEN SESSION
        </ActionButton>
      </aside>
    );
  }

  return (
    <aside className="nx-session-col" aria-label="Session" data-testid="session-column">
      <RunHeader compact />
      <IncidentStack compact />
      <LiveStack height={180} />
      <VitalsBand cells={4} />
      <IntegrationBar legend={false} />
      <RunControls size="md" />
      <ActionButton kind="ghost" full onPress={() => nav.go("/session/now")}>
        OPEN SESSION
      </ActionButton>
    </aside>
  );
}
