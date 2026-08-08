import { api } from "../api";
import { useStatus, useStore } from "../store";
import { polarTier } from "./polar";
import { nextInCycle } from "./focus/FocusPod";
import { useCanControlMount } from "../lib/caps";

/* The three things the Align screen made you scroll for, pinned where a thumb
   and one glance can reach them (operator feedback, 2026-08-07 00:38 and the
   12:50 refinement):

     1. WHAT IS HAPPENING RIGHT NOW. A solve can take 15 s and the reticle
        does not change while it runs, so "working" and "wedged" looked the
        same. The native driver publishes `activity` (exposing | solving)
        around every frame; this strip renders it as a live chip, and the
        reticle wears PolarSolveRing for the same fact where the eye is.
     2. HOW FAR OFF THE MOUNT IS. The five-em number lived in a panel below
        the fold on a phone — the posture this screen is used in is crouched
        at the tripod, phone in one hand, hex key in the other.
     3. THE SOLVE FRAME'S IMAGING SETTINGS, as SPEED DIALS — the Focus pod's
        cycling-badge idiom (one target, current value on its face, tap for
        the next preset), one dial each for exposure / gain / binning /
        filter. The first cut hid these behind a fold; on a night of failing
        solves the fold was one tap too many, and a dial that shows its value
        IS the summary the fold's chip was.

   The strip only exists while a session is live or has a verdict: an idle
   Align screen keeps its clean start state. The dials only while LIVE — a
   finished session has no next frame to apply them to. */

type QuickBarPolar = {
  state: string;
  phase?: "measuring" | "adjusting";
  point_index?: number;
  total_error: number;
  az_error: number;
  alt_error: number;
  activity?: "exposing" | "solving" | null;
  solve_settings?: SolveSettings;
};

export type SolveSettings = {
  exposure_s: number;
  gain: number;
  offset: number;
  binning: number;
  filter: string | null;
};

/* The server's defaults, mirrored so the dials render sane values before the
   first polar event carries `solve_settings` (the session only publishes them
   once something changes them). Kept in ONE place here; the server remains
   the authority the moment it speaks. */
const DEFAULTS: SolveSettings = {
  exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null,
};

const EXPOSURES = [0.3, 0.5, 1, 2, 3, 5];
const GAINS = [100, 200, 300, 400];
const BINS = [1, 2];

/** The filter dial's ring: as-is (null) first, then every non-opaque slot.
 *  Opaque slots are carriers with no glass — a solve through one is a dark
 *  frame — so they exist in the wheel but never in this cycle. */
export function nextFilter(filters: string[], current: string | null): string | null {
  const ring: (string | null)[] = [null, ...filters];
  const i = ring.findIndex((f) => f === current);
  return ring[(i + 1) % ring.length] ?? null;
}

export function PolarQuickBar({ polar }: { polar: QuickBarPolar }) {
  const showToast = useStore((s) => s.showToast);
  const status = useStatus();
  const canMount = useCanControlMount();

  const live = polar.state === "running" || polar.state === "paused"
    || polar.state === "pausing";
  const hasVerdict = polar.state === "done" || polar.state === "error";
  /* The dials are for the NEXT frame, and the next frame includes the first
     one: the whole point is setting 2 s BEFORE the run, not after six failed
     solves (2026-08-07 19:53 — the first cut hid them until a session was
     live, which is the Guide screen's own lesson applied backwards). So an
     OPERATOR sees the bar, dials always; a viewer's idle screen stays clean —
     locked dials on a screen they cannot start anything from are furniture. */
  if (!live && !hasVerdict && !canMount) return null;

  const measuring = polar.phase === "measuring";
  const total = polar.total_error;
  const hasNumber = !measuring && Number.isFinite(total) && total > 0;
  const tier = polarTier(total);
  const numberTone = !hasNumber ? "text-faint"
    : tier === "excellent" ? "text-good"
      : tier === "good" ? "text-warn" : "text-bad";

  /* Degrees once a number stops being a bolt adjustment: 96′ reads as noise,
     1.6° reads as the fact it is. Below 10′ the arcminute is the unit the
     bolts are turned by. */
  const numberText = !hasNumber ? "—"
    : total >= 60 ? `${(total / 60).toFixed(1)}°` : `${total.toFixed(1)}′`;

  /* One activity chip, priority-ordered: the per-frame activity beats the
     phase, which beats the bare state — each is a finer-grained truth.
     "capturing", not "exposing": the same word the ring under it uses. */
  const activity = polar.activity
    ? { text: polar.activity === "exposing" ? "capturing…" : "solving…", blink: true }
    : measuring
      ? { text: `measuring ${Math.min(3, (polar.point_index ?? -1) + 2)}/3`, blink: true }
      : polar.phase === "adjusting" && polar.state === "running"
        ? { text: "tracking your adjustments", blink: false }
        : { text: polar.state, blink: polar.state === "running" || polar.state === "pausing" };

  const settings = { ...DEFAULTS, ...(polar.solve_settings ?? {}) };
  const wheel = status?.filterwheel;
  const filters = (wheel?.names ?? []).filter(
    (n, i) => n && !(wheel?.opaque?.[i] ?? false));

  const put = (patch: Partial<SolveSettings>) => {
    void api.put("/api/polar/solve-settings", patch).catch(
      (e) => showToast("error", (e as Error).message));
  };

  /* The dials — FocusPod's cycling-badge idiom exactly: one target, the
     current value on its face, a tap moves to the next preset, and a change
     applies to the NEXT solve frame, including mid-run. `nextInCycle` walks
     to the next value ABOVE a custom current rather than snapping. */
  const nextExposure = nextInCycle(EXPOSURES, settings.exposure_s);
  const nextGain = nextInCycle(GAINS, settings.gain);
  const nextBin = nextInCycle(BINS, settings.binning);
  const nextFilt = nextFilter(filters, settings.filter);

  /* Only ever rendered inside the canMount-gated row below — a viewer's bar
     carries the status and the number, never dead controls. */
  const dial = (
    key: string, face: string, aria: string, patch: Partial<SolveSettings>,
  ) => (
    <button
      key={key}
      type="button"
      data-polar-dial={key}
      className="btn tap mono !normal-case justify-center px-2 min-h-[40px] min-w-[48px]"
      aria-label={aria}
      onClick={() => put(patch)}
    >
      {face}
    </button>
  );

  return (
    /* Sticky under the app header; z below toasts/dialogs. backdrop keeps the
       page content readable as it slides beneath. */
    <div className="sticky top-0 z-20 -mx-1 px-1">
      <div className="border border-line rounded bg-panel/95 backdrop-blur px-3 py-2">
        <div className="flex items-center gap-3 min-h-8">
          {/* activity — the "is it doing anything" answer */}
          <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${
            polar.state === "error" ? "bg-bad"
              : polar.state === "done" ? "bg-good"
                : activity.blink ? "bg-accent blink" : "bg-accent"}`} />
          <span className="text-[11px] tracking-widest uppercase text-dim truncate"
            aria-live="polite">
            {activity.text}
          </span>

          {/* the number — visible without scrolling, tinted by the verdict */}
          <span className={`ml-auto font-display font-semibold text-xl mono tabular-nums ${numberTone}`}>
            {numberText}
          </span>
          {hasNumber && (
            <span className="text-[10px] text-faint mono tabular-nums hidden sm:inline">
              az {Math.abs(polar.az_error).toFixed(1)}′ · alt {Math.abs(polar.alt_error).toFixed(1)}′
            </span>
          )}
        </div>

        {canMount && (
          <div className="mt-1.5 pt-1.5 border-t border-line"
            role="group" aria-label="solve frame speed dials">
            <div className="flex items-center gap-1.5 flex-wrap">
              {dial("exposure", `${settings.exposure_s}s`,
                `Exposure ${settings.exposure_s} seconds — tap for ${nextExposure}`,
                { exposure_s: nextExposure })}
              {dial("gain", `g${settings.gain}`,
                `Gain ${settings.gain} — tap for ${nextGain}`,
                { gain: nextGain })}
              {dial("binning", `b${settings.binning}`,
                `Binning ${settings.binning}×${settings.binning} — tap for ${nextBin}×${nextBin}`,
                { binning: nextBin })}
              {filters.length > 0 && dial("filter", settings.filter ?? "as-is",
                `Filter ${settings.filter ?? "as-is"} — tap for ${nextFilt ?? "as-is"}`,
                { filter: nextFilt })}
              <span className="text-[10px] text-faint leading-tight ml-auto hidden sm:inline">
                applies from the next frame
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default PolarQuickBar;
