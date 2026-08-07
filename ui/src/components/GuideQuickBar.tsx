/* GuideQuickBar — the guide screen's sticky glance + speed dials (2026-08-07).

   Guiding runs for hours and is checked from a phone; calibration is a
   30-60 s pulse walk that used to render as one busy button. This strip is
   the PolarQuickBar pattern for both: state chip, the RMS numbers toned
   honestly in their own unit, the calibration walk narrated step by step
   (leg chip + a pulse ring that fills on each pulse's own duration + the
   star's measured walk as a crosshair plot), and speed dials for the guide
   camera's exposure / gain / binning.

   The dials PUT /api/guide/camera-settings, which the native guider reads
   PER EXPOSURE — a change lands on the very next guide frame, mid-anything.
   Before this the loop's exposure was a constructor-frozen 2.0 s no UI could
   reach: when the guide star fades the fix is a longer exposure NOW, and
   there was no dial to reach for (the #156-shaped trap). Binning is refused
   server-side while active (409 with the reason); the toast relays it.

   Rendered whenever a guider is connected: the dials must be reachable
   BEFORE the first calibration, not only during a session. */
import { useEffect, useState, type JSX } from "react";
import { api } from "../api";
import { useGuide, useStatus, useStore } from "../store";
import { LockedChip } from "./ui";
import ActivityRing from "./ui/ActivityRing";
import { nextInCycle } from "./focus/FocusPod";
import { useCanControlGuide } from "../lib/caps";

const EXPOSURES = [0.5, 1, 1.5, 2, 3, 5];
const GAINS = [100, 200, 300, 400];
const BINS = [1, 2];

type CamSettings = { exposure_s: number; gain: number; binning: number };

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

  // Seed the dials from the server once; every PUT answer re-seeds, so the
  // faces always show what the next frame will actually use.
  const [cam, setCam] = useState<CamSettings | null>(null);
  useEffect(() => {
    let dead = false;
    void api.get("/api/guide/camera-settings")
      .then((s) => { if (!dead) setCam(s as CamSettings); })
      .catch(() => {});
    return () => { dead = true; };
  }, []);

  const stats = guide ?? status?.guider ?? null;
  const connected = !!status?.guider || !!guide;
  if (!connected) return null;

  const phase = (stats?.phase as string) || (stats?.guiding ? "guiding" : "idle");
  const calibrating = phase === "calibrating";
  const live = stats?.guiding || ["finding", "calibrating", "settling"].includes(phase);
  const unit = stats?.is_arcsec === true ? "″" : "px";

  const put = (patch: Partial<CamSettings>) => {
    void api.put("/api/guide/camera-settings", patch)
      .then((s) => setCam(s as CamSettings))
      .catch((e) => showToast("error", (e as Error).message));
  };

  const dial = (key: string, face: string, aria: string,
                patch: Partial<CamSettings>) => canGuide ? (
    <button
      key={key}
      type="button"
      data-guide-dial={key}
      className="btn tap mono !normal-case justify-center px-2 min-h-[40px] min-w-[48px]"
      aria-label={aria}
      onClick={() => put(patch)}
    >
      {face}
    </button>
  ) : (
    <LockedChip key={key}
      reason="Changing guide-camera settings needs guiding control access."
      className="btn mono !normal-case justify-center !px-2 min-h-[40px] min-w-[48px]">
      <span className="text-[11px]">{face}</span>
    </LockedChip>
  );

  const nextExp = cam ? nextInCycle(EXPOSURES, cam.exposure_s) : EXPOSURES[0];
  const nextGain = cam ? nextInCycle(GAINS, cam.gain) : GAINS[0];
  const nextBin = cam ? nextInCycle(BINS, cam.binning) : BINS[0];

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

        {/* the guide camera's speed dials */}
        <div className="mt-1.5 pt-1.5 border-t border-line"
          role="group" aria-label="guide camera speed dials">
          <div className="flex items-center gap-1.5 flex-wrap">
            {dial("exposure", cam ? `${cam.exposure_s}s` : "—s",
              `Guide exposure ${cam?.exposure_s ?? "unknown"} seconds — tap for ${nextExp}`,
              { exposure_s: nextExp })}
            {dial("gain", cam ? `g${cam.gain}` : "g—",
              `Guide gain ${cam?.gain ?? "unknown"} — tap for ${nextGain}`,
              { gain: nextGain })}
            {dial("binning", cam ? `b${cam.binning}` : "b—",
              `Guide binning ${cam?.binning ?? "unknown"} — tap for ${nextBin}. `
              + "Refused while guiding: it changes the calibration's pixel scale.",
              { binning: nextBin })}
            <span className="text-[10px] text-faint leading-tight ml-auto hidden sm:inline">
              applies from the next guide frame
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default GuideQuickBar;
