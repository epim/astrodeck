// PassesCard.tsx - when and where to look for a satellite (D-SKY-1).
//
// A satellite is the one target on this screen that is not a night. It is a
// five-minute event with a start, a peak and an end, and the only useful thing
// the rig can tell you about it is when to be outside and which way to face. So
// this card is a LIST OF EVENTS, not a status readout: no altitude now, no
// "in reach" chip, no plan summary.
//
// THREE RULES IT KEEPS, all of them about not inventing a number:
//
//   1. `sunlit_fraction` is rendered as WORDS. It is a fraction of THIS pass,
//      so "62%" is a percentage of nothing a reader has been told - and when
//      the fraction is 0 the pass is not visible at all, which is a sentence
//      and not a smaller number.
//   2. The minutes-in-sunlight clause is computed only when BOTH shadow
//      crossings are non-null. `passes.py:291-306` leaves them null when the
//      crossing falls outside the refined window, and a subtraction over a null
//      would print a confident duration nobody measured.
//   3. The stale-elements sentence is `elements.note`, VERBATIM from the
//      server. It carries the drift magnitude ("a pass time this old can be a
//      minute out") and it ends by naming Sky settings, which is where the
//      REFRESH ELEMENTS control lives. Re-wording it would drop both.
//
// Props only, like every other card in this hub: it fetches nothing and reads
// no store, so the same card serves the lock card on the finder and the
// ephemeris section of the targets sheet.

import type { JSX } from "react";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import { fmtClock } from "../../../lib/format";
import type { EphemerisCacheState, SatellitePass } from "../../../../types";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";

/** Compass point for an azimuth - the same 16-point rose the wind line uses, so
 *  "W" on this card and "W" on the finder mean the same arc of sky. */
const POINTS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];

export function azPoint(deg: number): string {
  if (!Number.isFinite(deg)) return "-";
  const i = Math.round((((deg % 360) + 360) % 360) / 22.5) % 16;
  return POINTS[i];
}

/** "4 min 20 s" - a pass is minutes long, so seconds are not noise. */
export function passDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return m === 0 ? `${rest} s` : rest === 0 ? `${m} min` : `${m} min ${rest} s`;
}

/**
 * What the sunlight does over this pass, in words.
 *
 * The three cases are three different pieces of advice: go out and look; do not
 * bother, it is in the Earth's shadow the whole way; go out and look at the
 * first part of it. The middle one is the one that matters - an invisible pass
 * that rendered as "0% sunlit" is a row that looks like every other row.
 */
export function sunlitClause(p: SatellitePass): string {
  const f = p.sunlit_fraction;
  if (!(f > 0)) return "in shadow the whole pass - not visible";
  if (f >= 1) return "sunlit throughout";
  const enters = p.enters_shadow_unix;
  const leaves = p.leaves_shadow_unix;
  const total = Math.max(1, Math.round(p.duration_s / 60));
  if (typeof enters === "number" && typeof leaves === "number") {
    // Both crossings inside the window: the lit stretch is whatever is NOT
    // between them, and the pass starts lit if it enters shadow after it rises.
    const litMin = Math.max(1, Math.round((enters - p.start_unix) / 60));
    return `sunlit for the first ${litMin} of ${total} minutes`;
  }
  if (typeof enters === "number") {
    const litMin = Math.max(1, Math.round((enters - p.start_unix) / 60));
    return `sunlit for the first ${litMin} of ${total} minutes`;
  }
  if (typeof leaves === "number") {
    const darkMin = Math.max(1, Math.round((leaves - p.start_unix) / 60));
    return `in shadow for the first ${darkMin} of ${total} minutes, then sunlit`;
  }
  // A fraction strictly between 0 and 1 with neither crossing refined is a real
  // case, and the honest answer is the one that does not name a minute.
  return "partly sunlit";
}

export interface PassesCardProps {
  /** null = nobody has answered yet; [] = the search ran and found none. */
  passes: SatellitePass[] | null;
  /** The element cache the passes were computed from, for the staleness note. */
  elements: EphemerisCacheState | null;
  /** The server's own sentences, rendered verbatim. */
  notes: string[];
  loading: boolean;
  error: string | null;
  /** Why this principal may not have the list at all - the server's withheld
   *  sentence, never a locally written cap phrase. */
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  onRefresh?: () => void;
  /** Why RETRY cannot be pressed, when it cannot. */
  refreshReason?: string | null;
  /** What the passes are for, so the card names it without being handed the
   *  whole row. */
  name: string;
}

export function PassesCard({
  passes,
  elements,
  notes,
  loading,
  error,
  lockedReason = null,
  onExplain,
  onRefresh,
  refreshReason = null,
  name,
}: PassesCardProps): JSX.Element {
  const explain = onExplain ?? (() => { /* a card with no explain hatch is read-only */ });
  // The stale sentence is shown only when the SERVER says the elements are
  // stale, and then it is the server's own. `note` is null when the cache is
  // neither absent nor stale (`elements.py:247-269`), so this is one condition
  // and not two.
  const staleNote = elements?.stale === true ? elements.note : null;

  return (
    <Card tone="default" data-testid="sky-passes">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Label size={11}>{`PASSES - ${name.toUpperCase()}`}</Label>
          <Mono size={10} tone="dim">next 24 h</Mono>
        </div>

        {lockedReason != null ? (
          <p
            data-testid="sky-passes-withheld"
            style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)", margin: 0 }}
          >
            {lockedReason}
          </p>
        ) : (
          <>
            {loading && (
              <Mono size={10.5} tone="dim">
                {"Working out the passes - this walks the orbit second by second."}
              </Mono>
            )}

            {error != null && (
              <div role="alert" style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
                <p style={{ fontSize: 11.5, color: "var(--warn, #ffb454)", margin: 0 }}>{error}</p>
                {onRefresh && (
                  <ActionButton
                    kind="secondary"
                    data-testid="sky-passes-retry"
                    lockedReason={refreshReason}
                    onExplain={explain}
                    onPress={onRefresh}
                  >
                    TRY AGAIN
                  </ActionButton>
                )}
              </div>
            )}

            {!loading && error == null && passes != null && passes.length === 0 && (
              <p
                data-testid="sky-passes-none"
                style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)", margin: 0 }}
              >
                {"Nothing clears the horizon in the next 24 hours. A satellite in a high or badly-timed orbit can go days without a pass from here."}
              </p>
            )}

            {passes != null && passes.map((p) => (
              <PassRow key={`${p.norad_id}-${p.start_unix}`} p={p} />
            ))}
          </>
        )}

        {staleNote && (
          <p
            data-testid="sky-passes-stale"
            style={{ fontSize: 11, lineHeight: 1.5, color: "var(--warn, #ffb454)", margin: 0 }}
          >
            {staleNote}
          </p>
        )}

        {notes.map((n) => (
          <p
            key={n}
            data-testid="sky-passes-note"
            style={{ fontSize: 11, lineHeight: 1.5, color: "var(--text-3, #7683a5)", margin: 0 }}
          >
            {n}
          </p>
        ))}

      </div>
    </Card>
  );
}

function PassRow({ p }: { p: SatellitePass }): JSX.Element {
  const clock = (unix: number): string => fmtClock(unix * 1000, unix * 1000);
  return (
    <div
      data-testid="sky-pass-row"
      data-norad={p.norad_id}
      style={{
        display: "flex", flexDirection: "column", gap: 3,
        padding: "8px 0", borderTop: "1px solid rgba(120,140,200,.12)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontFamily: MONO, fontSize: 11 }}>
        <span style={{ color: "var(--text-3, #7683a5)" }}>RISE</span>
        <span style={{ color: "var(--text, #e8ecf7)" }}>{`${clock(p.start_unix)} ${azPoint(p.start_az)}`}</span>
        <span aria-hidden="true" style={{ color: "var(--text-3, #7683a5)" }}>-&gt;</span>
        <span style={{ color: "var(--text-3, #7683a5)" }}>PEAK</span>
        <span style={{ color: "var(--accent, #00d2ff)" }}>
          {`${clock(p.peak_unix)} ${Math.round(p.max_alt_deg)} deg ${azPoint(p.peak_az)}`}
        </span>
        <span aria-hidden="true" style={{ color: "var(--text-3, #7683a5)" }}>-&gt;</span>
        <span style={{ color: "var(--text-3, #7683a5)" }}>SET</span>
        <span style={{ color: "var(--text, #e8ecf7)" }}>{`${clock(p.end_unix)} ${azPoint(p.end_az)}`}</span>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontFamily: MONO, fontSize: 10, color: "var(--text-3, #7683a5)" }}>
        <span>{passDuration(p.duration_s)}</span>
        <span data-testid="sky-pass-sunlit">{sunlitClause(p)}</span>
      </div>
    </div>
  );
}
