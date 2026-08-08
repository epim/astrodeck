import { useRef } from "react";
import { api } from "../api";
import { useStatus, useStore } from "../store";
import { polarTier } from "./polar";
import PickerButton from "./ui/PickerButton";
import { useCanControlMount } from "../lib/caps";

/* The three things the Align screen made you scroll for, pinned where a thumb
   and one glance can reach them (operator feedback 2026-08-07, refined twice):

     1. WHAT IS HAPPENING RIGHT NOW — the activity chip (the reticle wears
        PolarSolveRing for the same fact where the eye is).
     2. HOW FAR OFF THE MOUNT IS — the number, az/alt split included at every
        width (20:09: on a tall phone the Total-error panel can still sit
        below the reticle, and this sticky bar is then the only readout).
     3. THE SOLVE FRAME'S IMAGING SETTINGS as PICKERS — the Capture screen's
        PickerButton idiom (20:09: cycling badges made a 0.3→120 s change
        eleven taps; a picker is two). One each for exposure / gain / binning
        / filter, plus a custom exposure box, because an OSC camera behind a
        narrowband filter legitimately solves at minutes per frame.

   An OPERATOR sees the bar whenever the Align screen is open — the dials are
   for the NEXT frame, and the next frame includes the first one. A viewer's
   idle screen stays clean; their live bar carries status and number only. */

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

/* The server's defaults, mirrored so the pickers render sane values before
   the first polar event carries `solve_settings`. The server remains the
   authority the moment it speaks. */
const DEFAULTS: SolveSettings = {
  exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null,
};

/* 20:09 operator list, verbatim, plus the sub-second pair the defaults live
   in. The long tail is not decoration: narrowband-over-OSC alignments really
   do solve at 2-5 minutes per frame. The route's ceiling matches (300). */
const EXPOSURES = [0.3, 0.5, 1, 2, 5, 10, 15, 30, 60, 90, 120, 180, 300];
const GAINS = [0, 50, 100, 150, 200, 250, 300, 400, 500];
const BINS = [1, 2, 3, 4];
const EXPOSURE_MAX_S = 300;

const fmtExp = (e: number) => e < 60 ? `${e}s` : `${e / 60}m`;

export function PolarQuickBar({ polar }: { polar: QuickBarPolar }) {
  const showToast = useStore((s) => s.showToast);
  const status = useStatus();
  const canMount = useCanControlMount();
  /* Uncontrolled on purpose: the value is read ONCE, at Set — there is no
     render that depends on the keystrokes, so controlling it would only buy
     re-renders of the whole bar per character. */
  const customExp = useRef<HTMLInputElement>(null);

  const live = polar.state === "running" || polar.state === "paused"
    || polar.state === "pausing";
  const hasVerdict = polar.state === "done" || polar.state === "error";
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
     phase, which beats the bare state — each is a finer-grained truth. */
  const activity = polar.activity
    ? { text: polar.activity === "exposing" ? "capturing…" : "solving…", blink: true }
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

  const applyCustomExposure = () => {
    const v = Number(customExp.current?.value ?? "");
    if (!Number.isFinite(v) || v <= 0 || v > EXPOSURE_MAX_S) {
      showToast("error",
        `Custom exposure must be between 0 and ${EXPOSURE_MAX_S} seconds.`);
      return;
    }
    put({ exposure_s: v });
    if (customExp.current) customExp.current.value = "";
    // PickerButton closes on Escape (its own window listener) — the one way a
    // child can ask the panel to close without a new prop contract.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  };

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

          {/* the number — visible without scrolling, at EVERY width: on a
              tall phone the Total-error panel sits below the reticle, and
              this is then the only arcmin readout on the first screenful */}
          <span className={`ml-auto font-display font-semibold text-xl mono tabular-nums ${numberTone}`}>
            {numberText}
          </span>
          {hasNumber && (
            <span className="text-[10px] text-faint mono tabular-nums">
              az {Math.abs(polar.az_error).toFixed(1)}′ · alt {Math.abs(polar.alt_error).toFixed(1)}′
            </span>
          )}
        </div>

        {canMount && (
          <div className="mt-1.5 pt-1.5 border-t border-line"
            role="group" aria-label="solve frame settings">
            {/* FOUR EQUAL COLUMNS, not a wrapping flex row: on a 412px phone
                the flex row broke FILT onto a second line by itself. A grid
                keeps one tidy row of thumb-sized buttons at every width. */}
            <div className={`grid gap-1.5 ${filters.length > 0 ? "grid-cols-4" : "grid-cols-3"}`}>
              <PickerButton
                label="EXP"
                summary={fmtExp(settings.exposure_s)}
                className="w-full !justify-center"
                columns={3}
                options={EXPOSURES.map((e) => ({ id: String(e), label: fmtExp(e) }))}
                selected={[String(settings.exposure_s)]}
                onPick={(id) => put({ exposure_s: Number(id) })}
              >
                {/* narrowband-over-OSC rigs need values no list predicts */}
                <div className="col-span-3 border-t border-line mt-1 pt-2 flex items-center gap-1.5">
                  <input
                    ref={customExp}
                    className="field flex-1 min-w-0 mono text-xs"
                    inputMode="decimal"
                    placeholder="custom s"
                    aria-label="Custom exposure in seconds"
                    defaultValue=""
                    onKeyDown={(e) => { if (e.key === "Enter") applyCustomExposure(); }}
                  />
                  <button type="button" className="btn min-h-[40px] text-[11px] !px-3"
                    onClick={applyCustomExposure}>
                    Set
                  </button>
                </div>
              </PickerButton>
              <PickerButton
                label="GAIN"
                summary={String(settings.gain)}
                className="w-full !justify-center"
                columns={3}
                options={GAINS.map((v) => ({ id: String(v), label: String(v) }))}
                selected={[String(settings.gain)]}
                onPick={(id) => put({ gain: Number(id) })}
              />
              <PickerButton
                label="BIN"
                summary={`${settings.binning}×${settings.binning}`}
                className="w-full !justify-center"
                columns={2}
                options={BINS.map((b) => ({ id: String(b), label: `${b}×${b}` }))}
                selected={[String(settings.binning)]}
                onPick={(id) => put({ binning: Number(id) })}
              />
              {filters.length > 0 && (
                <PickerButton
                  label="FILT"
                  summary={settings.filter ?? "as-is"}
                  className="w-full !justify-center"
                  align="right"
                  columns={2}
                  options={[
                    { id: "", label: "as-is",
                      hint: "leave the filter wheel where it sits" },
                    ...filters.map((f) => ({ id: f, label: f })),
                  ]}
                  selected={[settings.filter ?? ""]}
                  onPick={(id) => put({ filter: id === "" ? null : id })}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default PolarQuickBar;
