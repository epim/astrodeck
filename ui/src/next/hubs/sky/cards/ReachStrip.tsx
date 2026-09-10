// ReachStrip.tsx - the horizontally scrolling row of everything that is clear
// and up right now (hub-sky plan A.9).
//
// It is the answer to "what else?", and it is deliberately the LAST thing on the
// screen: the finder above it argues for one target, and this row says what the
// other eleven were. Tapping a chip swings the finder there and draws its arc,
// which is the same action as tapping a row in the suggested-targets sheet - one
// verb, two places, because the strip is the sheet's first twelve rows without
// the trip through a sheet.
//
// The empty state is not "no targets". It says WHICH of the two filters emptied
// it (the lens, or the horizon and the cloud) and where each is changed, because
// an empty strip with an unexplained cause reads as a broken app rather than as
// a cloudy night.

import { useRef, type JSX } from "react";
import { EmptyCard } from "../../../ui";
import { windowLabel, type SkyTarget } from "../finder";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export const REACH_EMPTY_TITLE = "NOTHING IS CLEAR AND UP RIGHT NOW";
export const REACH_EMPTY_HINT =
  "Everything the lens shows is either behind your horizon or under cloud. " +
  "Tap the lens to show more kinds, or the site pill to change where you are.";

export interface ReachStripProps {
  reachList: SkyTarget[];
  onAim: (t: SkyTarget) => void;
}

export function ReachStrip({ reachList, onAim }: ReachStripProps): JSX.Element {
  const scroller = useRef<HTMLDivElement | null>(null);

  if (reachList.length === 0) {
    return (
      <div data-testid="sky-reach">
        <EmptyCard title={REACH_EMPTY_TITLE} hint={REACH_EMPTY_HINT} />
      </div>
    );
  }

  return (
    <div data-testid="sky-reach" style={{ display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
      <div
        style={{
          display: "flex", justifyContent: "space-between", padding: "0 2px",
          fontFamily: DISPLAY, fontWeight: 600, fontSize: 10,
          letterSpacing: ".2em", color: "var(--text-faint)",
        }}
      >
        <span>REACHABLE NOW</span>
        <span style={{ fontFamily: MONO, fontWeight: 400, letterSpacing: 0 }}>tap to aim</span>
      </div>
      <div style={{ position: "relative" }}>
        <div
          ref={scroller}
          style={{
            display: "flex", gap: 6, overflowX: "auto",
            paddingBottom: 2, paddingRight: 44, scrollBehavior: "smooth",
          }}
        >
          {reachList.map((t) => (
            <button
              key={t.id}
              type="button"
              data-reach-chip={t.id}
              onClick={() => onAim(t)}
              style={{
                flexShrink: 0, height: 44, padding: "0 12px", borderRadius: 12,
                border: `1px solid ${t.color}`, background: "var(--bg-raise)",
                color: "var(--text)", display: "flex", flexDirection: "column",
                justifyContent: "center", gap: 2, textAlign: "left", cursor: "pointer",
              }}
            >
              <span style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 10.5, letterSpacing: ".1em", color: t.color }}>
                {t.name}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                {Math.round(t.altNow)}° · {windowLabel(t.windowMinutes)}
              </span>
            </button>
          ))}
        </div>
        <button
          type="button"
          aria-label="more reachable targets"
          data-testid="sky-reach-more"
          onClick={() => scroller.current?.scrollBy({ left: 240, behavior: "smooth" })}
          style={{
            position: "absolute", right: 0, top: 0, width: 40, height: 44,
            borderRadius: 12, border: "1px solid var(--line-bright)",
            background: "linear-gradient(90deg,rgba(6,7,11,0),var(--bg) 40%)",
            color: "var(--text)", fontFamily: MONO, fontSize: 16, cursor: "pointer",
          }}
        >
          ›
        </button>
      </div>
    </div>
  );
}
