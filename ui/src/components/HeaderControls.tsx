// HeaderControls.tsx — mobile-sized header controls (touch spec §11 header, R16/R27).
//
// A narrow-selector child so mounting it does NOT widen App's `useStore()`
// subscription (R27 / master Risk-14). Hosts the header-only controls:
//   - Brightness: a single slider (the +/- steppers were removed — product
//     decision — leaving the slider as the sole dimmer control). It drives the
//     STORE-OWNED dimmer slice (useBrightness + setBrightness, F-dimmer); the
//     store is the single writer of `--screen-brightness` and clamps the slider to
//     a 0.5 floor so the screen can never be dimmed to unreadable. On wide headers
//     (lg+) the slider is inline; below lg it collapses into an overflow popover so
//     it no longer keeps its full footprint and squeezes the status strip
//     (R2-WEA-03).
//   - NIGHT: 44px icon toggle (R16 — NIGHT lives ONLY in the header, never buried).
//     Shows the TARGET state icon.
//   - LOG: 44px button with an OUTLINE-RING error badge + count (R21). Kept inline
//     at every width so the error badge is never hidden inside an overflow.
//
// Every control is >=44px on coarse pointers. This component reads/writes only its
// own narrow slices (night, unseenError, openLog, toggleNight, brightness).

import { useEffect, useRef, useState } from "react";
import { useStore, useBrightness } from "../store";
import { Icon } from "./icons";

// Screen-brightness slider + %-readout. Rendered inline on wide headers (lg+) and
// inside the overflow popover on narrow ones, so there is ONE control definition.
// min=0.5 mirrors the store's clampBright floor (unreadable-dim is impossible).
function BrightnessSlider() {
  const brightness = useBrightness();
  const setBrightness = useStore((s) => s.setBrightness);
  const brightPct = Math.round(brightness * 100);
  return (
    <>
      <input
        type="range"
        min={0.5}
        max={1}
        step={0.02}
        value={brightness}
        aria-label="Screen brightness"
        aria-valuetext={`${brightPct} percent`}
        onChange={(e) => setBrightness(Number(e.target.value))}
        className="dimmer flex-1 min-w-[96px]"
      />
      <span className="mono text-[10px] text-dim w-9 text-right tabular-nums shrink-0">
        {brightPct}%
      </span>
    </>
  );
}

export default function HeaderControls() {
  const night = useStore((s) => s.night);
  const toggleNight = useStore((s) => s.toggleNight);
  const openLog = useStore((s) => s.openLog);
  const unseenError = useStore((s) => s.unseenError);

  // Overflow popover for the brightness slider below lg. Dismiss on outside
  // pointerdown / Escape (mirrors the Tooltip machine, minus hover-intent).
  const [dimOpen, setDimOpen] = useState(false);
  const dimRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!dimOpen) return;
    const onDoc = (e: PointerEvent) => {
      if (dimRef.current && !dimRef.current.contains(e.target as Node)) setDimOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDimOpen(false);
    };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [dimOpen]);

  return (
    <div className="flex items-center gap-1.5">
      {/* brightness — inline on wide headers (room for the full footprint) */}
      <div className="hidden lg:flex items-center gap-1.5">
        <BrightnessSlider />
      </div>

      {/* brightness — overflow trigger + popover below lg. The popover is NOT
          `.panel`: the unlayered `.panel { position: relative }` would beat the
          layered `absolute` utility (the tooltip war story), so it's styled with
          design tokens directly. The header sets `relative z-20` so this absolute
          child paints above the main content. */}
      <div className="lg:hidden relative" ref={dimRef}>
        <button
          type="button"
          className="step-btn min-h-[44px] sm:min-h-0"
          aria-label="Screen brightness"
          aria-haspopup="dialog"
          aria-expanded={dimOpen}
          onClick={() => setDimOpen((o) => !o)}
        >
          <Icon name="brightness" size={16} />
        </button>
        {dimOpen && (
          <div
            className="absolute right-0 top-full mt-1.5 z-30 w-56 flex items-center gap-2
              bg-raise border border-line2 rounded-lg p-3 shadow-[0_6px_20px_rgba(0,0,0,0.55)]"
          >
            <BrightnessSlider />
          </div>
        )}
      </div>

      {/* NIGHT — header-only icon toggle, 44px, shows TARGET state */}
      <button
        className="btn !py-1 !px-2.5 text-[10px] tap min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
        onClick={toggleNight}
        aria-label={night ? "Switch to day mode" : "Switch to night mode"}
        title={night ? "Switch to day mode" : "Switch to red night-vision mode"}
      >
        <Icon name={night ? "sun" : "moon"} size={14} />
        <span className="hidden lg:inline">{night ? "DAY" : "NIGHT"}</span>
      </button>

      {/* LOG — 44px, outline-ring error badge (R21), always inline so the badge is
          never buried in an overflow */}
      <button
        className="btn !py-1 !px-2.5 text-[10px] tap min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
        onClick={openLog}
        aria-label={
          unseenError > 0 ? `Open event log (${unseenError} unseen errors)` : "Open event log"
        }
      >
        <Icon name="alert" size={14} />
        <span className="hidden lg:inline">LOG</span>
        {unseenError > 0 && (
          <span
            className="inline-flex items-center justify-center min-w-[16px] h-4 px-1
              rounded-full border border-bad text-bad text-[9px] font-bold leading-none tabular-nums"
          >
            {unseenError > 99 ? "99+" : unseenError}
          </span>
        )}
      </button>
    </div>
  );
}
