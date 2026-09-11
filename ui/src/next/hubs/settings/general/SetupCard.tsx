// SetupCard.tsx - the FIRST-TIME SETUP card at the top of Settings - GENERAL
// (`proto/22-settings.html`, `a_setupPending`).
//
// Shown only while setup is incomplete AND the guide has not been dismissed
// (`lib/coach.ts`'s `astrodeck-coach-seen` map, key `first-run-wizard` - the
// same key the legacy docked wizard has always used, so a user who finished
// setup in the old UI is not shown this again).
//
// The ring is the shipped `RingGauge`, not a hand-rolled circle: the prototype
// draws `stroke-dasharray: (done/5)*113 113` on `r=18`, which is the same arc
// the primitive computes from `value/max` - and one implementation of the arc
// is one place for it to be wrong.

import type { JSX } from "react";
import { Card, Mono, RingGauge } from "../../../ui";
import { nav } from "../../../router";
import { useHasSeen } from "../../../../store";
import { WIZARD_SEEN_KEY } from "../../../../lib/coach";
import { SetupGlyph } from "./glyphs";
import { nextLine, type SetupView } from "./setupSteps";

const ROW = {
  display: "flex", alignItems: "center", gap: "12px",
  width: "100%", border: 0, background: "transparent",
  color: "var(--text)", textAlign: "left", cursor: "pointer", padding: 0,
} as const;

const TEXT = {
  flex: 1, display: "flex", flexDirection: "column", gap: "2px", minWidth: 0,
} as const;

const TITLE = {
  fontFamily: '"Chakra Petch", sans-serif', fontWeight: 600,
  fontSize: "11px", letterSpacing: ".14em",
} as const;

const SUB = {
  fontFamily: '"IBM Plex Mono", monospace', fontSize: "10px",
  color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis",
} as const;

/** `view` is a PROP, not a second `useSetupFacts()` call: the hook carries the
 *  `GET /api/profiles` read, and mounting it twice on one screen would fire the
 *  request twice for one answer. `GeneralScreen` already holds it. */
export function SetupCard({ view }: { view: SetupView }): JSX.Element | null {
  const dismissed = useHasSeen(WIZARD_SEEN_KEY);

  if (view.complete || dismissed) return null;

  return (
    <Card tone="accent" data-testid="setup-card">
      <button
        type="button"
        style={ROW}
        onClick={() => nav.sheet("setup")}
        aria-label={`First-time setup, ${view.doneCount} of 5 done. ${nextLine(view)}`}
      >
        <RingGauge
          value={view.doneCount}
          min={0}
          max={5}
          label="OF 5"
          size={44}
          data-testid="setup-ring"
        />
        <span style={TEXT}>
          <span style={TITLE}>FIRST-TIME SETUP</span>
          <span style={SUB} data-testid="setup-next">{nextLine(view)}</span>
        </span>
        <span aria-hidden="true" style={{ color: "var(--text-faint)" }}>
          <SetupGlyph size={18} />
        </span>
        <Mono size={12}>&rsaquo;</Mono>
      </button>
    </Card>
  );
}
