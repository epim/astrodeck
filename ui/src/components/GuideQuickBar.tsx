/* GuideQuickBar — the guide screen's sticky glance + speed dials (2026-08-07).

   Guiding runs for hours and is checked from a phone; calibration is a
   30-60 s pulse walk that used to render as one busy button. This strip is
   the PolarQuickBar pattern for both: state chip, the RMS numbers toned
   honestly in their own unit, the calibration walk narrated step by step
   (leg chip + a pulse ring that fills on each pulse's own duration + the
   star's measured walk as a crosshair plot), and speed dials for the guide
   camera's exposure / gain / offset / binning.

   The dials write the `guide` SCOPE (#176), which the native guider reads PER
   EXPOSURE — a change lands on the very next guide frame, mid-anything. Its
   OWN scope, and that is the point: a guide camera's exposure is legitimately
   not the imaging camera's, so these dials must never inherit Capture's
   five-minute frame. Before any of it the loop's exposure was a
   constructor-frozen 2.0 s no UI could reach: when the guide star fades the
   fix is a longer exposure NOW, and there was no dial to reach for (the
   #156-shaped trap). OFFSET was the same trap, still live, until 2026-08-08
   (#187). Binning is refused server-side while active (409 with the reason);
   the store rolls the face back and the toast relays it.

   Rendered whenever a guider is connected: the dials must be reachable
   BEFORE the first calibration, not only during a session. */
import { type JSX } from "react";
import { useFrameSettings, useGuide, useStatus, useStore } from "../store";
import ActivityRing from "./ui/ActivityRing";
import {
  BinningPicker, ExposurePicker, GainPicker, OffsetPicker,
  GUIDE_EXPOSURE_PRESETS_S,
} from "./ui/CameraPickers";
import { useCanControlGuide } from "../lib/caps";

/** The star's measured walk during calibration, as a crosshair plot.
 *  An orthogonal L is a mount; a smeared diagonal is flexure or a wrong
 *  parity — visible here long before the calibration report says so. */
export function CalWalkPlot({ walk }: { walk: [number, number][] }): JSX.Element {
  const S = 88, half = S / 2;
  const span = Math.max(4, ...walk.map(([x, y]) => Math.max(Math.abs(x), Math.abs(y))));
  const k = (half - 6) / span;
  const pts = walk.map(([x, y]) => `${(half + x * k).toFixed(1)},${(half + y * k).toFixed(1)}`);
  return (
    <svg viewBox={`0 0 ${S} ${S}`} width={S} height={S} data-cal-walk
      role="img" aria-label={`calibration walk, ${walk.length} points`}>
      <line x1={0} y1={half} x2={S} y2={half} stroke="var(--line)" strokeWidth={1} />
      <line x1={half} y1={0} x2={half} y2={S} stroke="var(--line)" strokeWidth={1} />
      {pts.length > 1 && (
        <polyline points={pts.join(" ")} fill="none"
          stroke="var(--accent)" strokeWidth={1.2} opacity={0.8} />
      )}
      {walk.length > 0 && (
        <circle cx={half + walk[walk.length - 1][0] * k}
          cy={half + walk[walk.length - 1][1] * k}
          r={2.5} fill="var(--accent)" />
      )}
    </svg>
  );
}

export function GuideQuickBar(): JSX.Element | null {
  const status = useStatus();
  const guide = useGuide();
  const guideCal = useStore((s) => s.guideCal);
  const showToast = useStore((s) => s.showToast);
  const canGuide = useCanControlGuide();

  // The `guide` SCOPE (#176) — its OWN scope, and that is the point: a guide
  // camera's exposure is legitimately not the imaging camera's, so these dials
  // must never inherit Capture's five-minute frame. It replaces a local cache
  // seeded by a cold GET; the store is seeded by the WS hello and replaced by
  // the `frames` event, so a second tab changing the guide exposure is visible
  // here without a refetch.
  const cam = useFrameSettings("guide");
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const put = (patch: Parameters<typeof setFrameSettings>[1]) =>
    setFrameSettings("guide", patch);

  const stats = guide ?? status?.guider ?? null;
  const connected = !!status?.guider || !!guide;
  if (!connected) return null;

  const phase = (stats?.phase as string) || (stats?.guiding ? "guiding" : "idle");
  const calibrating = phase === "calibrating";
  const live = stats?.guiding || ["finding", "calibrating", "settling"].includes(phase);
  const unit = stats?.is_arcsec === true ? "″" : "px";

  const lockReason = canGuide ? null
    : "Changing guide-camera settings needs guiding control access.";
  /* Binning is refused server-side while a session is live (the calibration
     measured px/ms in the CURRENT binning's pixels), so say so HERE too
     rather than letting the tap earn a 409. */
  const binReason = lockReason ?? (live
    ? "Stop guiding to change binning — the calibration was measured in the "
      + "current binning's pixels."
    : null);

  return (
    <div className="sticky top-0 z-20 -mx-1 px-1" data-guide-quickbar>
      <div className="border border-line rounded bg-panel/95 backdrop-blur px-3 py-2">
        <div className="flex items-center gap-3 min-h-8">
          <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${
            phase === "lost" ? "bg-bad"
              : live ? "bg-accent blink" : "bg-accent"}`} />
          <span className="text-[11px] tracking-widest uppercase text-dim truncate"
            aria-live="polite">
            {calibrating && guideCal
              ? `calibrating · ${guideCal.dir ?? guideCal.leg ?? ""} step ${guideCal.step}`
              : phase}
          </span>

          {/* the numbers — RMS while guiding, in their honest unit */}
          {stats?.guiding && (
            <span className="ml-auto flex items-baseline gap-2">
              <span className="font-display font-semibold text-xl mono tabular-nums text-accent">
                {stats.rms_total.toFixed(2)}{unit}
              </span>
              <span className="text-[10px] text-faint mono tabular-nums hidden sm:inline">
                RA {stats.rms_ra.toFixed(2)} · Dec {stats.rms_dec.toFixed(2)}
              </span>
            </span>
          )}

          {/* the pulse ring — each calibration pulse's own duration */}
          {calibrating && guideCal && (
            <span className={stats?.guiding ? "" : "ml-auto"}>
              <ActivityRing
                mode="fill"
                seconds={Math.max(0.2, guideCal.ms / 1000)}
                word="pulsing"
                resetKey={guideCal.step}
              />
            </span>
          )}
        </div>

        {/* the star's measured walk, while it walks */}
        {calibrating && guideCal && guideCal.walk.length > 1 && (
          <div className="mt-1.5 pt-1.5 border-t border-line flex items-center gap-3">
            <CalWalkPlot walk={guideCal.walk} />
            <p className="text-[10px] text-faint leading-relaxed max-w-[240px]">
              The star's measured walk. A clean calibration traces an
              L — two straight legs at right angles. A smeared diagonal
              means flexure or a dragging cable.
            </p>
          </div>
        )}

        {/* The guide camera's settings — the SAME pickers the Align and Focus
            screens use (CameraPickers), with the guide camera's own exposure
            range. One idiom per rig, not one per screen. */}
        <div className="mt-1.5 pt-1.5 border-t border-line"
          role="group" aria-label="guide camera settings">
          <div className="grid grid-cols-4 gap-1.5">
            <ExposurePicker
              value={cam.exposure_s}
              presets={GUIDE_EXPOSURE_PRESETS_S}
              className="w-full !justify-center"
              disabled={!canGuide}
              disabledReason={lockReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(s) => put({ exposure_s: s })}
            />
            <GainPicker
              value={cam.gain}
              className="w-full !justify-center"
              disabled={!canGuide}
              disabledReason={lockReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(g) => put({ gain: g })}
            />
            <BinningPicker
              value={cam.binning}
              /* The guide-camera status block carries no max_bin (types.ts);
                 4 is the picker's own ceiling and the server validates. */
              max={4}
              className="w-full !justify-center"
              disabled={!!binReason}
              disabledReason={binReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(b) => put({ binning: b })}
            />
            {/* #187: the offset the guide loop has applied to every exposure
                since it was written. It had no config field and the route
                answered a literal 30, so the constructor default was the only
                value it could ever have — the same shape as the frozen 2.0 s
                exposure that shipped dead beside it. */}
            <OffsetPicker
              value={cam.offset}
              align="right"
              className="w-full !justify-center"
              disabled={!canGuide}
              disabledReason={lockReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(o) => put({ offset: o })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default GuideQuickBar;
