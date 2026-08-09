import { useRef } from "react";
import { useFrameSettings, useStatus, useStore } from "../store";
import { polarTier } from "./polar";
import {
  BinningPicker, ExposurePicker, FilterPicker, GainPicker, OffsetPicker,
} from "./ui/CameraPickers";
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
};

/* The solve frame's settings are no longer a field on the polar event and no
   longer mirrored here (#176). They are the `solve` SCOPE, seeded by the WS
   hello and replaced by the `frames` event, because:

     - a mirrored DEFAULTS const is a second source of truth, and after a
       reload it was the only one on screen — the server's live pin, which
       would drive the wheel on the next run, was invisible;
     - `PolarAlignSession.start()` resets its state to `_idle()`, which carries
       no solve_settings, and the client applies polar events wholesale — so
       the instant an alignment began every face here reverted to those
       defaults while the engine went on solving at the operator's values. */

/* Presets, formatting and the controls themselves are SHARED (CameraPickers):
   the camera behind a solve frame is the camera behind a focus frame, and a
   user who learns "tap EXP, pick 5s" here must not meet a different idiom on
   the next screen. Only the ceiling is restated, because the route enforces
   it and a client that offered more would be promising what the server
   refuses. */

/* WHY THIS ROW SURVIVED #179 (2026-08-08).
   Asked for directly: "the exp, gain, bin, and filt still exist above the tppa
   bullseye, and are now obviated by the speed dial. Remove those there." Three
   of the four are indeed on the dial over the reticle. The row stays anyway,
   and here is the whole of the argument, so it is not re-litigated blind:

     1. IT IS A READOUT, NOT ONLY A CONTROL. The dial's closed face carries the
        exposure and the gain and nothing else, so deleting this row would put
        binning, filter and offset two taps deep on the screen whose whole job
        is "is the solve frame working?". cameraSettingsAgreement.test.tsx
        grades exactly that — every surface must SHOW the server's exposure,
        gain and binning — and it reads this row's faces.
     2. OFFSET IS HERE BY NAME because of a defect from the day before: the
        dial was the only way to reach it, and `CameraDial` deletes itself
        below a 124px stage, i.e. on the phone polar alignment is done from.
        There is a test called "the Align screen's offset is REACHABLE, not
        just stored".
     3. THE CUSTOM EXPOSURE BOX has no equivalent on the dial. An OSC camera
        behind a narrowband filter legitimately solves at minutes per frame,
        and the dial's EXP ring is presets only.
     4. FilterPicker's `pendingNote` — "It moves when the alignment takes its
        next solve frame" — is the 2026-08-08 repair for a face that said "R"
        over a wheel parked on Oiii. The dial's FILT ring cannot say it.

   (1) and (4) are the ones that would need work elsewhere to lift: a dial
   whose face carried all five values, and a ring that could narrate a pending
   pin. Both live in CameraDial/RingPicker, not here. */
const EXPOSURE_MAX_S = 300;

export function PolarQuickBar({ polar }: { polar: QuickBarPolar }) {
  const showToast = useStore((s) => s.showToast);
  const status = useStatus();
  const canMount = useCanControlMount();
  const settings = useFrameSettings("solve");
  const put = useStore((s) => s.setFrameSettings);
  const setSolve = (patch: Parameters<typeof put>[1]) => put("solve", patch);
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

  /* Whether the rig HAS a wheel decides the grid width; which slots are
     offerable is FilterPicker's own business (it drops the opaque ones). */
  const hasWheel = (status?.filterwheel?.names ?? []).length > 0;

  const applyCustomExposure = () => {
    const v = Number(customExp.current?.value ?? "");
    if (!Number.isFinite(v) || v <= 0 || v > EXPOSURE_MAX_S) {
      showToast("error",
        `Custom exposure must be between 0 and ${EXPOSURE_MAX_S} seconds.`);
      return;
    }
    setSolve({ exposure_s: v });
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
            <div className={`grid gap-1.5 ${hasWheel ? "grid-cols-4" : "grid-cols-3"}`}>
              <ExposurePicker
                value={settings.exposure_s}
                className="w-full !justify-center"
                onPick={(s) => setSolve({ exposure_s: s })}
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
              </ExposurePicker>
              <GainPicker
                value={settings.gain}
                className="w-full !justify-center"
                onPick={(g) => setSolve({ gain: g })}
              />
              <BinningPicker
                value={settings.binning}
                max={status?.camera?.max_bin ?? 4}
                className="w-full !justify-center"
                onPick={(b) => setSolve({ binning: b })}
              />
              <FilterPicker
                value={settings.filter}
                align="right"
                className="w-full !justify-center"
                /* The ONE surface in the app where picking a filter does not
                   move the wheel: the pin is applied by _apply_solve_filter,
                   inside a solve frame. With the session idle nothing moves,
                   and on 2026-08-08 this face said "R" over a wheel on Oiii.
                   It now reads "Oiii → R" and says who will close the gap. */
                pendingNote="It moves when the alignment takes its next solve frame."
                onPick={(name) => setSolve({ filter: name })}
              />
              {/* Offset had no picker here at all: the only way to change a
                  solve frame's offset was the radial dial on the reticle,
                  which hides itself entirely on a stage under 124 px. It
                  wraps to a second row rather than squeezing five controls
                  onto a 412 px phone. */}
              <OffsetPicker
                value={settings.offset}
                className="w-full !justify-center"
                onPick={(o) => setSolve({ offset: o })}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default PolarQuickBar;
