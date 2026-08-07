import { useState } from "react";
import { api } from "../api";
import { useStatus, useStore } from "../store";
import { polarTier } from "./polar";
import { useCanControlMount } from "../lib/caps";

/* The three things the Align screen made you scroll for, pinned where a thumb
   and one glance can reach them (operator feedback, 2026-08-07 00:38):

     1. WHAT IS HAPPENING RIGHT NOW. A solve can take 15 s and the reticle
        does not change while it runs, so "working" and "wedged" looked the
        same. The native driver now publishes `activity` (exposing | solving)
        around every frame; this strip renders it as a live chip.
     2. HOW FAR OFF THE MOUNT IS. The five-em number lived in a panel below
        the fold on a phone — the posture this screen is used in is crouched
        at the tripod, phone in one hand, hex key in the other.
     3. THE SOLVE FRAME'S IMAGING SETTINGS. Exposure/gain/bin/filter were
        hardcoded server-side until 2026-08-07; on a night of six failed
        solves in eleven minutes the operator had no move at all. They are
        live now (a change applies to the NEXT frame, mid-run), but they must
        not cost screen space when unneeded — so they fold behind one chip.

   The strip only exists while a session is live or has a verdict: an idle
   Align screen keeps its clean start state. */

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

/* The server's defaults, mirrored so the dial renders sane values before the
   first polar event carries `solve_settings` (the session only publishes them
   once something changes them). Kept in ONE place here; the server remains
   the authority the moment it speaks. */
const DEFAULTS: SolveSettings = {
  exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null,
};

const EXPOSURES = [0.3, 0.5, 1, 2, 3, 5];
const GAINS = [100, 200, 300, 400];
const BINS = [1, 2];

export function PolarQuickBar({ polar }: { polar: QuickBarPolar }) {
  const [open, setOpen] = useState(false);
  const showToast = useStore((s) => s.showToast);
  const status = useStatus();
  const canMount = useCanControlMount();

  const live = polar.state === "running" || polar.state === "paused"
    || polar.state === "pausing";
  const hasVerdict = polar.state === "done" || polar.state === "error";
  if (!live && !hasVerdict) return null;

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
     phase, which beats the bare state — each is a finer-grained truth. */
  const activity = polar.activity
    ? { text: polar.activity === "exposing" ? "exposing…" : "solving…", blink: true }
    : measuring
      ? { text: `measuring ${Math.min(3, (polar.point_index ?? -1) + 2)}/3`, blink: true }
      : polar.phase === "adjusting" && polar.state === "running"
        ? { text: "tracking your adjustments", blink: false }
        : { text: polar.state, blink: polar.state === "running" || polar.state === "pausing" };

  const settings = { ...DEFAULTS, ...(polar.solve_settings ?? {}) };
  const wheel = status?.filterwheel;
  /* Opaque slots are carriers with no glass — a solve through one is a dark
     frame. They exist in the wheel but not in this picker. */
  const filters = (wheel?.names ?? []).filter(
    (n, i) => n && !(wheel?.opaque?.[i] ?? false));

  const put = (patch: Partial<SolveSettings>) => {
    void api.put("/api/polar/solve-settings", patch).catch(
      (e) => showToast("error", (e as Error).message));
  };

  const chip = (active: boolean) =>
    `px-2 py-1 rounded text-[11px] mono tabular-nums border transition-colors ${
      active ? "border-accent text-accent bg-accent/10"
        : "border-line text-dim hover:text-ink"}`;

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

          {/* the settings fold */}
          <button
            className={`text-[11px] tracking-widest uppercase px-2 py-1 rounded border ${
              open ? "border-accent text-accent" : "border-line text-dim"}`}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}>
            {settings.exposure_s}s · g{settings.gain} · b{settings.binning}
            {settings.filter ? ` · ${settings.filter}` : ""}
          </button>
        </div>

        {open && (
          <div className="mt-2 pt-2 border-t border-line flex flex-col gap-2"
            role="group" aria-label="solve frame settings">
            {!canMount && (
              <p className="text-[11px] text-dim">
                Changing solve settings needs mount control access.
              </p>
            )}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="label w-14 shrink-0">Exposure</span>
              {EXPOSURES.map((e) => (
                <button key={e} disabled={!canMount}
                  className={chip(settings.exposure_s === e)}
                  onClick={() => put({ exposure_s: e })}>
                  {e}s
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="label w-14 shrink-0">Gain</span>
              {GAINS.map((g) => (
                <button key={g} disabled={!canMount}
                  className={chip(settings.gain === g)}
                  onClick={() => put({ gain: g })}>
                  {g}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="label w-14 shrink-0">Binning</span>
              {BINS.map((b) => (
                <button key={b} disabled={!canMount}
                  className={chip(settings.binning === b)}
                  onClick={() => put({ binning: b })}>
                  {b}×{b}
                </button>
              ))}
            </div>
            {filters.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="label w-14 shrink-0">Filter</span>
                <button disabled={!canMount}
                  className={chip(settings.filter == null)}
                  onClick={() => put({ filter: null })}>
                  as-is
                </button>
                {filters.map((f) => (
                  <button key={f} disabled={!canMount}
                    className={chip(settings.filter === f)}
                    onClick={() => put({ filter: f })}>
                    {f}
                  </button>
                ))}
              </div>
            )}
            <p className="text-[10px] text-faint leading-relaxed">
              Applies from the next solve frame — including mid-run. Longer
              exposure or more gain helps a solve that keeps failing; "as-is"
              leaves the filter wheel where it sits.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default PolarQuickBar;
