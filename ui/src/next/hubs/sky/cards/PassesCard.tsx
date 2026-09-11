// PassesCard.tsx - when and where to look for a satellite (D-SKY-1).
//
// A satellite is the one target on this screen that is not a night. It is a
// five-minute event with a start, a peak and an end, and the only useful thing
// the rig can tell you about it is when to be outside and which way to face. So
// this card is a LIST OF EVENTS, not a status readout: no altitude now, no
// "in reach" chip, no plan summary.
//
// FOUR RULES IT KEEPS, all of them about not inventing a number:
//
//   0. THE WINDOW SHOWN IS THE VISIBLE ONE when the server can compute it
//      (`shownWindow` below). Horizon crossings are not visibility, and the
//      difference is the whole card: a pass that rises into the Earth's shadow
//      is up for six minutes and worth going outside for two.
//   1. `sunlit_fraction` is rendered as WORDS. It is a fraction of THIS pass,
//      so "62%" is a percentage of nothing a reader has been told - and when
//      the fraction is 0 the pass is not visible at all, which is a sentence
//      and not a smaller number.
//   2. The minutes-in-sunlight clause names ONLY the crossings the server
//      actually refined. `passes.py:291-306` leaves either one null when it
//      falls outside the refined window, so there are four branches and not
//      two: both crossings (and then their ORDER decides which half of the pass
//      is lit), one of each on its own, and neither - which says "partly
//      sunlit" rather than subtracting over a null and printing a confident
//      duration nobody measured.
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
  const minsIn = (t: number): number => Math.max(1, Math.round((t - p.start_unix) / 60));
  if (typeof enters === "number" && typeof leaves === "number") {
    // BOTH crossings refined, and their ORDER is the whole answer. This branch
    // used to assume the pass began sunlit and printed "sunlit for the first N
    // minutes" either way - so a satellite that rose in the Earth's shadow and
    // came out of it halfway across read as visible at exactly the minutes it
    // was not. `leaves` before `enters` means it rose dark; `enters` before
    // `leaves` means it rose lit and came back out again before it set.
    return leaves < enters
      ? `in shadow for the first ${minsIn(leaves)} of ${total} minutes, `
        + `then sunlit until minute ${minsIn(enters)}`
      : `sunlit for the first ${minsIn(enters)} of ${total} minutes, `
        + `then in shadow until minute ${minsIn(leaves)}`;
  }
  if (typeof enters === "number") {
    return `sunlit for the first ${minsIn(enters)} of ${total} minutes`;
  }
  if (typeof leaves === "number") {
    return `in shadow for the first ${minsIn(leaves)} of ${total} minutes, then sunlit`;
  }
  // A fraction strictly between 0 and 1 with neither crossing refined is a real
  // case, and the honest answer is the one that does not name a minute.
  return "partly sunlit";
}

/**
 * What `passes_busy` says. The server's own detail names the refusal, not the
 * cure, and the cure is the whole message here: the route runs one pass search
 * at a time because each is thousands of SGP4 evaluations, so the answer is to
 * wait rather than to change anything.
 */
export const PASSES_BUSY_NOTE =
  "Another pass search is already running - the rig does one at a time. "
  + "Try again in a moment.";

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
  /**
   * `ApiError.code` behind `error`, when there is one.
   *
   * TWO 409s, TWO FACES. `satellites_unavailable` means the rig has no site
   * set: the sentence is the server's and is worth showing, but TRY AGAIN
   * beside it would offer a retry that cannot succeed until somebody sets a
   * site, so it renders as a withheld STATE. `passes_busy` is the opposite -
   * nothing is wrong and waiting IS the fix - so it keeps the retry and gets
   * its own sentence. Anything else is a failure and keeps the failure face.
   */
  errorCode?: string | null;
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
  errorCode = null,
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

  // A refusal with nothing to retry reads as the WITHHELD state, in the
  // server's own words, rather than as a failure with a dead button beside it.
  const withheld = lockedReason ?? (errorCode === "satellites_unavailable" ? error : null);
  // A refusal that waiting fixes keeps the retry and says what to wait for.
  const shownError = withheld != null
    ? null
    : errorCode === "passes_busy" ? PASSES_BUSY_NOTE : error;

  return (
    <Card tone="default" data-testid="sky-passes">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Label size={11}>{`PASSES - ${name.toUpperCase()}`}</Label>
          <Mono size={10} tone="dim">next 24 h</Mono>
        </div>

        {withheld != null ? (
          <p
            data-testid="sky-passes-withheld"
            style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)", margin: 0 }}
          >
            {withheld}
          </p>
        ) : (
          <>
            {loading && (
              <Mono size={10.5} tone="dim">
                {"Working out the passes - this walks the orbit second by second."}
              </Mono>
            )}

            {shownError != null && (
              <div role="alert" data-error-code={errorCode ?? undefined} style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
                <p style={{ fontSize: 11.5, color: "var(--warn, #ffb454)", margin: 0 }}>{shownError}</p>
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

            {!loading && shownError == null && passes != null && passes.length === 0 && (
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

/**
 * THE WINDOW WORTH GOING OUTSIDE FOR, when the server can name it.
 *
 * `start_unix`/`end_unix` are HORIZON crossings: the satellite is above the
 * skyline between them, which is not the same as being visible. A pass that
 * rises into the Earth's shadow is up for six minutes and worth watching for
 * two. FIX-S1 adds `visible_start_unix`/`visible_end_unix` - the up-AND-lit-AND-
 * dark window, bisected the same way - and this card prefers them the moment
 * they arrive.
 *
 * The fields are OPTIONAL on `SatellitePass` and are read as such: an engine
 * older than the release that added them sends neither, and this card must
 * render the horizon pair in that case rather than a blank. A half-answer - one
 * field without the other, or an end at or before the start - is not a window
 * either, and takes the same path.
 */
export function shownWindow(p: SatellitePass): {
  start: number; end: number; visible: boolean;
} {
  const vs = typeof p.visible_start_unix === "number" ? p.visible_start_unix : null;
  const ve = typeof p.visible_end_unix === "number" ? p.visible_end_unix : null;
  if (vs !== null && ve !== null && ve > vs) return { start: vs, end: ve, visible: true };
  return { start: p.start_unix, end: p.end_unix, visible: false };
}

function PassRow({ p }: { p: SatellitePass }): JSX.Element {
  const clock = (unix: number): string => fmtClock(unix * 1000, unix * 1000);
  const win = shownWindow(p);
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
        <span style={{ color: "var(--text-3, #7683a5)" }}>{win.visible ? "VISIBLE" : "RISE"}</span>
        {/* The azimuth belongs to the HORIZON crossing and only to it. Printing
            `start_az` beside a visible-window time would put a compass point on
            an instant nobody computed one for, which is the kind of number that
            sends someone out facing the wrong way. */}
        <span style={{ color: "var(--text, #e8ecf7)" }}>
          {win.visible ? clock(win.start) : `${clock(win.start)} ${azPoint(p.start_az)}`}
        </span>
        <span aria-hidden="true" style={{ color: "var(--text-3, #7683a5)" }}>-&gt;</span>
        <span style={{ color: "var(--text-3, #7683a5)" }}>PEAK</span>
        <span style={{ color: "var(--accent, #00d2ff)" }}>
          {`${clock(p.peak_unix)} ${Math.round(p.max_alt_deg)} deg ${azPoint(p.peak_az)}`}
        </span>
        <span aria-hidden="true" style={{ color: "var(--text-3, #7683a5)" }}>-&gt;</span>
        <span style={{ color: "var(--text-3, #7683a5)" }}>{win.visible ? "UNTIL" : "SET"}</span>
        <span style={{ color: "var(--text, #e8ecf7)" }}>
          {win.visible ? clock(win.end) : `${clock(win.end)} ${azPoint(p.end_az)}`}
        </span>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontFamily: MONO, fontSize: 10, color: "var(--text-3, #7683a5)" }}>
        <span>{passDuration(win.visible ? win.end - win.start : p.duration_s)}</span>
        <span data-testid="sky-pass-sunlit">{sunlitClause(p)}</span>
        {/* The horizon pair is not dropped when the visible window replaces it
            above: the compass points are what you face, and they only exist on
            these two instants. */}
        {win.visible && (
          <span data-testid="sky-pass-horizon">
            {`above the horizon ${clock(p.start_unix)} ${azPoint(p.start_az)} `
              + `to ${clock(p.end_unix)} ${azPoint(p.end_az)}`}
          </span>
        )}
      </div>
    </div>
  );
}
