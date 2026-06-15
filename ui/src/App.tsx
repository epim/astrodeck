import { useCallback, useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { useStore, type ViewName } from "./store";
import { connectWs } from "./ws";
import { Icon, type IconName } from "./components/icons";
import { HoldButton, Led } from "./components/ui";
import ConnectionBanner from "./components/ConnectionBanner";
import HealthLeds from "./components/HealthLeds";
import Toasts from "./components/Toasts";
import LogDrawer from "./components/LogDrawer";
import ConnectView from "./views/ConnectView";
import CaptureView from "./views/CaptureView";
import FocusView from "./views/FocusView";
import MountView from "./views/MountView";
import PolarView from "./views/PolarView";
import GuideView from "./views/GuideView";
import SequenceView from "./views/SequenceView";
import PowerView from "./views/PowerView";
import MonitorView from "./views/MonitorView";

// IA reorder (master-plan Risk-10 canonical 8-entry order, Align before Mount) +
// header/nav entries for Settings (placeholder) and Monitor (real this batch).
// Risk-10: "land the order once, append entries thereafter" — Monitor is an
// APPENDED entry (no reorder, no Power eviction; monitor spec E1/E2). Nav glyphs
// resolve through the single icons.tsx module (Batch-2 2B icon plan).
const NAV: { id: ViewName; label: string; icon: IconName }[] = [
  { id: "connect", label: "Rig", icon: "rig" },
  { id: "polar", label: "Align", icon: "align" },
  { id: "mount", label: "Mount", icon: "mount" },
  { id: "focus", label: "Focus", icon: "focus" },
  { id: "capture", label: "Capture", icon: "capture" },
  { id: "guide", label: "Guide", icon: "guide" },
  { id: "sequence", label: "Plan", icon: "plan" },
  { id: "power", label: "Power", icon: "power" },
  { id: "monitor", label: "Monitor", icon: "monitor" },
  { id: "settings", label: "Settings", icon: "settings" },
];

// Placeholder for views whose file has not landed yet (settings/atlas are built in
// later batches). Guarding here keeps VIEWS a total Record<ViewName,…> so the union
// stays exhaustive without importing a not-yet-present module. Monitor IS mounted
// for real this batch (2E's MonitorView).
function PlaceholderView({ label }: { label: string }): JSX.Element {
  return (
    <div className="panel p-6 max-w-md mx-auto mt-10 text-center">
      <h2 className="panel-title mb-2">{label}</h2>
      <p className="text-dim text-sm">This view is coming in a later build.</p>
    </div>
  );
}

const VIEWS: Record<ViewName, () => JSX.Element> = {
  connect: ConnectView,
  capture: CaptureView,
  focus: FocusView,
  mount: MountView,
  polar: PolarView,
  guide: GuideView,
  sequence: SequenceView,
  power: PowerView,
  monitor: MonitorView,
  settings: () => <PlaceholderView label="Settings" />,
  atlas: () => <PlaceholderView label="Sky Atlas" />,
};

// ============================================================================
// Global brightness dimmer — APP-OWNED state (store owner 2A did not land the
// display slice this wave, so the dimmer lives here per the DIMMER CONTRACT:
// App.tsx sets documentElement --screen-brightness (clamp 0.08..1) +
// --scrim-opacity (= clamp(0, 1 - brightness*1.05, 0.92)) on every change and at
// init; index.css consumes them; the values mirror index.html's pre-paint script
// so first paint never flashes). Separate day/night brightness memory persists to
// localStorage; toggling mode swaps to the other memory (design-system §7.5).
// `locked` is NEVER persisted (a lock must not survive reload, design-system §3.3).
// ============================================================================
const BRIGHT_DAY_KEY = "astrodeck-bright-day";
const BRIGHT_NIGHT_KEY = "astrodeck-bright-night";

const clampB = (v: number): number => Math.min(1, Math.max(0.08, v));

function readBright(key: string, def: number): number {
  try {
    const n = Number(localStorage.getItem(key));
    return Number.isFinite(n) && n > 0 ? clampB(n) : def;
  } catch {
    return def;
  }
}

function applyBrightness(v: number): void {
  const b = clampB(v);
  const d = document.documentElement;
  d.style.setProperty("--screen-brightness", String(b));
  // scrim deepens past what filter:brightness can do (OLED black-pixel safe).
  d.style.setProperty("--scrim-opacity", String(Math.min(0.92, Math.max(0, 1 - b * 1.05))));
}

/** Lock / touch-guard placeholder hook (Batch-3 3B fills in slew-forceStop +
 *  wake-lock + unlock gesture). Today it is a no-op carrier so the lock button +
 *  overlay exist and App.tsx owns the affordance; `lockAvailable` stays false
 *  until reliability's error-render ships (master Risk-6), so the control is a
 *  visible-but-inert placeholder, never trapping the user. */
function useTouchGuard(): { locked: boolean; lock: () => void; unlock: () => void } {
  const [locked, setLocked] = useState(false);
  return {
    locked,
    lock: useCallback(() => setLocked(true), []),
    unlock: useCallback(() => setLocked(false), []),
  };
}

export default function App() {
  // Split selectors (reliability §13 / Risk-14 perf P0): each subscription is a
  // single slice, so a guide tick (mutates only `guide`) no longer re-renders the
  // whole tree. Chrome components self-subscribe to their own slices.
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const night = useStore((s) => s.night);
  const toggleNight = useStore((s) => s.toggleNight);
  const sequence = useStore((s) => s.sequence);
  const status = useStore((s) => s.status);
  const linkDown = useStore((s) => s.wsPhase !== "up");
  const telemetryStale = useStore((s) => s.telemetryStale);
  const openLog = useStore((s) => s.openLog);
  const unseenError = useStore((s) => s.unseenError);
  const runBanner = useStore((s) => s.runBanner);
  const dismissRunBanner = useStore((s) => s.dismissRunBanner);

  // --- brightness dimmer state (app-owned; see header comment) -----------------
  const [dayBrightness, setDayBrightness] = useState(() => readBright(BRIGHT_DAY_KEY, 1));
  const [nightBrightness, setNightBrightness] = useState(() => readBright(BRIGHT_NIGHT_KEY, 0.45));
  const brightness = night ? nightBrightness : dayBrightness;
  // Brief mode-change announce pill ("Night · 45%"), aria-live polite (§7.6).
  const [announce, setAnnounce] = useState<string | null>(null);
  const announceTimer = useRef<number | null>(null);
  const guard = useTouchGuard();

  const setBrightness = useCallback(
    (raw: number) => {
      const v = clampB(raw);
      applyBrightness(v);
      const isNight = useStore.getState().night;
      try {
        localStorage.setItem(isNight ? BRIGHT_NIGHT_KEY : BRIGHT_DAY_KEY, String(v));
      } catch {
        /* quota / unavailable — keep in-memory */
      }
      if (isNight) setNightBrightness(v);
      else setDayBrightness(v);
    },
    [],
  );

  const resetBrightness = useCallback(() => setBrightness(1), [setBrightness]);

  // Mode toggle: flip night via the store (it owns the .night class + persistence),
  // then swap the applied brightness to the new mode's remembered value + announce.
  const onToggleNight = useCallback(() => {
    toggleNight();
    const isNight = useStore.getState().night;
    const b = isNight ? nightBrightness : dayBrightness;
    applyBrightness(b);
    setAnnounce(`${isNight ? "Night" : "Day"} · ${Math.round(b * 100)}%`);
    if (announceTimer.current != null) clearTimeout(announceTimer.current);
    announceTimer.current = window.setTimeout(() => setAnnounce(null), 1200);
  }, [toggleNight, nightBrightness, dayBrightness]);

  // Apply the correct brightness for the active mode on mount and whenever the
  // active value changes (keeps the CSS var in lockstep with React state; the
  // index.html pre-paint already set a correct first-paint value, this re-asserts
  // after hydration and on every adjustment).
  useEffect(() => {
    applyBrightness(brightness);
  }, [brightness]);

  // Always-reachable escape hatch: Shift+B resets brightness to 1.0 (§7.2). Never
  // trapped behind a dimmed-out thumb.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && (e.key === "B" || e.key === "b") && !e.repeat) {
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return; // don't hijack typing
        e.preventDefault();
        resetBrightness();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [resetBrightness]);

  useEffect(() => {
    connectWs();
  }, []);

  useEffect(() => () => { if (announceTimer.current != null) clearTimeout(announceTimer.current); }, []);

  const Active = VIEWS[view];
  const camConnected = !!status?.connected?.camera?.connected;
  const mountConnected = !!status?.connected?.telescope?.connected;
  const seqRunning = sequence.state === "running" || sequence.state === "paused";
  const seqError = sequence.state === "error";
  const dim = linkDown || telemetryStale;
  const brightPct = Math.round(brightness * 100);
  // Surface the run banner only when a run is active and the user is NOT already on
  // the Monitor (no point nagging while they watch it). Auto-SELECT is handled in
  // the store's guarded rising-edge logic (monitor §3.2) — the banner is the
  // never-forced, always-dismissible affordance (resolves A3/B6).
  const showRunBanner = !!runBanner?.active && view !== "monitor";

  return (
    <div className="h-full">
      {/* ============================================================ .dim-content
          header + nav + main + log drawer; gets filter:brightness via CSS. The
          starfield (day + night) is on .dim-content per 2B index.css, so it dims
          WITH the UI — no body/#root starfield here. */}
      <div className="dim-content h-full flex flex-col">
        {/* ---------------------------------------------- top status strip */}
        <header className="flex items-center gap-3 px-4 h-12 border-b border-line bg-raise/70 backdrop-blur shrink-0">
          <h1 className="font-display font-semibold tracking-[0.3em] text-accent text-sm select-none">
            ASTRO<span className="text-ink">DECK</span>
          </h1>
          {status?.mode && status.mode !== "none" && (
            <span className="hidden sm:inline px-2 py-0.5 border border-line2 text-[9px]
              tracking-[0.18em] uppercase text-accent font-display font-medium"
              title={`Backend: ${status.mode}`}>
              {status.mode === "nina" ? "NINA" : status.mode === "alpaca" ? "ALPACA" : "SIM"}
            </span>
          )}
          <div className={`hidden md:flex items-center gap-4 text-xs mono text-dim min-w-0 overflow-hidden
            ${dim ? "opacity-40 saturate-50 transition-opacity" : "transition-opacity"}`}>
            {status?.mount && (
              <>
                <span className="truncate">{status.mount.ra_str}</span>
                <span className="truncate">{status.mount.dec_str}</span>
                <span>ALT {status.mount.alt.toFixed(0)}°</span>
                <span className={status.mount.tracking ? "text-good" : "text-warn"}>
                  {status.mount.parked ? "PARKED" : status.mount.slewing ? "SLEWING"
                    : status.mount.tracking ? "TRACKING" : "IDLE"}
                </span>
              </>
            )}
            {status?.camera?.temperature != null && (
              <span>{status.camera.temperature.toFixed(1)}°C</span>
            )}
            {status?.guider?.guiding && (
              <span className="text-good">RMS {status.guider.rms_total.toFixed(2)}"</span>
            )}
            {seqRunning && sequence.progress && (
              <span className="text-accent">
                SEQ {sequence.progress.frames_done}/{sequence.progress.frames_total}
              </span>
            )}
          </div>
          <div className="flex-1" />

          {/* dimmer cluster: slider + numeric % + glove steppers (each >=44px hit
              area). The reset affordance lives in .overlay-top so it is NEVER
              dimmed (the always-reachable escape hatch). On phones (<640px) the
              slider + % readout collapse to save header width, but the two glove
              steppers STAY visible so a one-handed field user can still DIM with
              one tap (resolves P1-6 — the dimmer was previously hidden sm:flex,
              leaving no way to reduce brightness on the primary form factor). */}
          <div className="flex items-center gap-1.5">
            <input
              type="range" min={0.08} max={1} step={0.02} value={brightness}
              aria-label="Screen brightness"
              aria-valuetext={`${brightPct} percent`}
              onChange={(e) => setBrightness(Number(e.target.value))}
              className="dimmer w-20 hidden sm:block"
            />
            <span className="hidden sm:inline mono text-[10px] text-dim w-8 text-right tabular-nums">{brightPct}%</span>
            <button className="step-btn" aria-label={`Dim screen (currently ${brightPct}%)`}
              onClick={() => setBrightness(brightness - 0.06)}>−</button>
            <button className="step-btn" aria-label={`Brighten screen (currently ${brightPct}%)`}
              onClick={() => setBrightness(brightness + 0.06)}>+</button>
          </div>

          {/* single mode indicator: shows the TARGET state (night?sun:moon) */}
          <button
            className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
            onClick={onToggleNight}
            aria-label={night ? "Switch to day mode" : "Switch to night mode"}
            title={night ? "Switch to day mode" : "Switch to red night-vision mode"}
          >
            <Icon name={night ? "sun" : "moon"} size={14} />
            <span className="hidden sm:inline">{night ? "DAY" : "NIGHT"}</span>
          </button>

          {/* lock / touch-guard (Batch-3 placeholder; inert today, never traps) */}
          <button
            className="btn !py-1 !px-2.5 min-h-[44px] sm:min-h-0 inline-flex items-center"
            onClick={guard.lock}
            aria-label="Lock screen (touch guard)"
            title="Lock screen (touch guard)"
          >
            <Icon name="lock" size={14} />
          </button>

          <button
            className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
            onClick={openLog}
            aria-label={unseenError > 0 ? `Open event log (${unseenError} unseen errors)` : "Open event log"}
          >
            <Icon name="alert" size={14} />
            <span className="hidden sm:inline">LOG</span>
            {unseenError > 0 && (
              <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1
                rounded-full bg-bad text-[9px] font-bold leading-none text-black/90">
                {unseenError > 99 ? "99+" : unseenError}
              </span>
            )}
          </button>
          <HealthLeds />
        </header>

        {/* ConnectionBanner renders null when the link is up and telemetry fresh. */}
        <ConnectionBanner />

        {/* Persistent run banner (monitor §3.2 / B7): never-forced, always
            dismissible affordance to open the Monitor while a run is active.
            >=44px action target. Auto-SELECT (if enabled) happens in the store. */}
        {showRunBanner && (
          <div className="flex items-center gap-3 px-4 py-2 border-b border-line bg-accent2/15 shrink-0 text-xs">
            <span className="blink shrink-0"><Led state="busy" label="Sequence running" /></span>
            <span className="min-w-0 truncate text-ink">
              <span className="text-accent font-display tracking-wider">SEQUENCE RUNNING</span>
              {runBanner?.plan_name && <span className="text-dim"> · {runBanner.plan_name}</span>}
              {typeof runBanner?.percent === "number" && (
                <span className="mono text-dim"> · {Math.round(runBanner.percent)}%</span>
              )}
            </span>
            <div className="flex-1" />
            <button
              className="btn btn-accent !py-1.5 !px-3 text-[10px] min-h-[44px] sm:min-h-0"
              onClick={() => setView("monitor")}
            >
              OPEN LIVE
            </button>
            <button
              className="btn !py-1.5 !px-2.5 min-h-[44px] sm:min-h-0 inline-flex items-center"
              onClick={dismissRunBanner}
              aria-label="Dismiss sequence-running banner"
              title="Dismiss"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        )}

        <div className="flex flex-1 min-h-0">
          {/* -------------------------------------------------- left rail */}
          <nav className="hidden sm:flex flex-col w-[72px] border-r border-line bg-raise/40 py-2 shrink-0 overflow-y-auto">
            {NAV.map((n) => (
              <button
                key={n.id}
                onClick={() => setView(n.id)}
                className={`flex flex-col items-center gap-1 py-3 transition-colors relative cursor-pointer
                  ${view === n.id ? "text-accent" : "text-dim hover:text-ink"}`}
              >
                {view === n.id && <span className="absolute left-0 top-2 bottom-2 w-[2px] bg-accent shadow-[0_0_8px_var(--glow)]" />}
                <Icon name={n.icon} size={20} />
                <span className="text-[9px] tracking-[0.18em] font-display font-medium uppercase">{n.label}</span>
                {n.id === "capture" && camConnected && <span className="absolute top-2 right-3"><Led state="on" /></span>}
                {n.id === "mount" && mountConnected && <span className="absolute top-2 right-3"><Led state="on" /></span>}
                {n.id === "sequence" && seqError && (
                  <span className="absolute top-2 right-3 led led-bad blink-alert" />
                )}
                {n.id === "sequence" && !seqError && seqRunning && (
                  <span className="absolute top-2 right-3 blink">
                    <Led state={sequence.state === "paused" ? "warn" : "busy"} />
                  </span>
                )}
              </button>
            ))}
          </nav>

          {/* ----------------------------------------------- main content */}
          <main
            className={`flex-1 overflow-y-auto p-4 pb-20 sm:pb-4 ${dim ? "opacity-60 transition-opacity" : "transition-opacity"}`}
            key={view}
          >
            <div className="view-enter max-w-[1500px] mx-auto w-full min-h-full flex flex-col">
              <Active />
            </div>
          </main>

          {/* Log drawer: docked column lg+, bottom sheet below — self-manages via
              store.logOpen; renders nothing when closed. */}
          <LogDrawer />
        </div>

        {/* ------------------------------------------- mobile bottom nav */}
        <nav className="sm:hidden flex border-t border-line bg-raise shrink-0 fixed bottom-0 inset-x-0 z-20 overflow-x-auto">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => setView(n.id)}
              className={`flex-1 min-w-[44px] flex flex-col items-center gap-0.5 py-2 min-h-[52px]
                ${view === n.id ? "text-accent" : "text-dim"}`}>
              <Icon name={n.icon} size={20} />
              <span className="text-[8px] tracking-widest uppercase">{n.label}</span>
            </button>
          ))}
        </nav>
      </div>

      {/* ================================================================ .dim-scrim
          fixed multiply darkener above content, below overlays; opacity tracks the
          dimmer (--scrim-opacity). pointer-events:none via CSS. */}
      <div className="dim-scrim" aria-hidden />

      {/* ================================================================ .overlay-top
          fixed z-50, NEVER dimmed: toasts, lock overlay, brightness reset, the
          mode-change announce pill. pointer-events gated to children via CSS. */}
      <div className="overlay-top">
        {/* Toasts: top-center on phone, bottom-right desktop — self-manages. */}
        <Toasts />

        {/* Always-reachable brightness reset (the escape hatch, never dimmed). Only
            offered while the screen is meaningfully dimmed so it isn't chrome. */}
        {brightness < 0.95 && (
          <button
            className="fixed bottom-3 left-1/2 -translate-x-1/2 sm:left-auto sm:right-3 sm:translate-x-0
              btn btn-accent !py-1.5 !px-3 text-[10px] min-h-[44px] inline-flex items-center gap-1.5 z-50"
            onClick={resetBrightness}
            title="Reset screen brightness to 100% (Shift+B)"
          >
            <Icon name="sun" size={14} /> 100%
          </button>
        )}

        {/* Mode-change announce pill (1.2s, aria-live polite). */}
        {announce && (
          <div
            className="fixed top-14 left-1/2 -translate-x-1/2 panel px-3 py-1.5 text-[11px] mono text-ink z-50"
            aria-live="polite"
          >
            {announce}
          </div>
        )}

        {/* Lock / touch-guard overlay (Batch-3 placeholder). Full-screen scrim with
            a hold-to-unlock; long-press unlock also a future brightness-restore
            hook. Renders nothing until locked. */}
        {guard.locked && (
          <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4
            bg-bg/85 backdrop-blur-sm">
            <div className="panel-title">SCREEN LOCKED</div>
            <p className="text-dim text-xs max-w-[28ch] text-center">
              Touch guard active. Hold to unlock.
            </p>
            <HoldButton onConfirm={guard.unlock} label="Unlock screen">
              {(b) => (
                <button
                  {...b}
                  className="btn btn-accent relative overflow-hidden min-h-[48px] !px-6 inline-flex items-center gap-2"
                >
                  <span
                    className="absolute inset-y-0 left-0 bg-accent/25"
                    style={{ width: `${b.progress * 100}%` }}
                    aria-hidden
                  />
                  <Icon name="unlock" size={16} />
                  {b.armed ? "PRESS AGAIN" : b.hintLabel}
                </button>
              )}
            </HoldButton>
          </div>
        )}
      </div>
    </div>
  );
}
