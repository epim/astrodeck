// HeaderControls.tsx — mobile-sized header controls (touch spec §11 header, R16/R27).
//
// A narrow-selector child so mounting it does NOT widen App's `useStore()`
// subscription (R27 / master Risk-14). Hosts the header-only controls:
//   - NIGHT: 44px icon toggle (R16 — NIGHT lives ONLY in the header, never the
//     More sheet, because it's toggled constantly). Shows the TARGET state icon.
//   - Brightness dimmer access: two >=44px steppers (one-handed field dimming)
//     plus an optional slider on >=sm. Drives the STORE-OWNED dimmer slice
//     (useBrightness + setBrightness, F-dimmer); the store is the single writer of
//     the `--screen-brightness` / `--scrim-opacity` vars + the day/night localStorage
//     memory, so the header stays in lockstep with App with no shared CSS round-trip.
//   - LOG: 44px button with an OUTLINE-RING error badge + count (R21 — not a
//     red-on-red fill).
//
// Every control is >=44px on coarse pointers. This component reads/writes only its
// own narrow slices (night, unseenError, openLog, toggleNight, brightness).

import { useStore, useBrightness } from "../store";
import { Icon } from "./icons";

export default function HeaderControls() {
  const night = useStore((s) => s.night);
  const toggleNight = useStore((s) => s.toggleNight);
  const openLog = useStore((s) => s.openLog);
  const unseenError = useStore((s) => s.unseenError);

  // Dimmer is store-owned (F-dimmer): subscribe to the active brightness and drive
  // it through setBrightness — the store is the single writer of the CSS vars, so
  // no local state / localStorage / applyBrightness here, and no drift vs App.
  const brightness = useBrightness();
  const setBrightness = useStore((s) => s.setBrightness);
  const brightPct = Math.round(brightness * 100);

  return (
    <div className="flex items-center gap-1.5">
      {/* brightness steppers (one-handed field dimming); slider on >=sm */}
      <input
        type="range"
        min={0.08}
        max={1}
        step={0.02}
        value={brightness}
        aria-label="Screen brightness"
        aria-valuetext={`${brightPct} percent`}
        onChange={(e) => setBrightness(Number(e.target.value))}
        className="dimmer w-20 hidden sm:block"
      />
      <span className="hidden sm:inline mono text-[10px] text-dim w-8 text-right tabular-nums">
        {brightPct}%
      </span>
      <button
        className="step-btn min-h-[44px] sm:min-h-0"
        aria-label={`Dim screen (currently ${brightPct}%)`}
        onClick={() => setBrightness(brightness - 0.06)}
      >
        −
      </button>
      <button
        className="step-btn min-h-[44px] sm:min-h-0"
        aria-label={`Brighten screen (currently ${brightPct}%)`}
        onClick={() => setBrightness(brightness + 0.06)}
      >
        +
      </button>

      {/* NIGHT — header-only icon toggle, 44px, shows TARGET state */}
      <button
        className="btn !py-1 !px-2.5 text-[10px] tap min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
        onClick={toggleNight}
        aria-label={night ? "Switch to day mode" : "Switch to night mode"}
        title={night ? "Switch to day mode" : "Switch to red night-vision mode"}
      >
        <Icon name={night ? "sun" : "moon"} size={14} />
        <span className="hidden sm:inline">{night ? "DAY" : "NIGHT"}</span>
      </button>

      {/* LOG — 44px, outline-ring error badge (R21) */}
      <button
        className="btn !py-1 !px-2.5 text-[10px] tap min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
        onClick={openLog}
        aria-label={
          unseenError > 0 ? `Open event log (${unseenError} unseen errors)` : "Open event log"
        }
      >
        <Icon name="alert" size={14} />
        <span className="hidden sm:inline">LOG</span>
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
