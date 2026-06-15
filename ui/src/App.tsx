import { useEffect } from "react";
import type { JSX } from "react";
import { useStore, type ViewName } from "./store";
import { connectWs } from "./ws";
import { Icon, type IconName } from "./components/icons";
import { Led } from "./components/ui";
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

// IA reorder (master-plan Risk-10 canonical 8-entry order, Align before Mount) +
// a Settings header/nav entry. Batch 2 finalizes icons; nav icons resolve through
// the single icons.tsx module (orchestrator override of Risk-6).
const NAV: { id: ViewName; label: string; icon: IconName }[] = [
  { id: "connect", label: "Rig", icon: "rig" },
  { id: "polar", label: "Align", icon: "align" },
  { id: "mount", label: "Mount", icon: "mount" },
  { id: "focus", label: "Focus", icon: "focus" },
  { id: "capture", label: "Capture", icon: "capture" },
  { id: "guide", label: "Guide", icon: "guide" },
  { id: "sequence", label: "Plan", icon: "plan" },
  { id: "power", label: "Power", icon: "power" },
  { id: "settings", label: "Settings", icon: "settings" },
];

// Placeholder for views whose file has not landed yet (settings/monitor/atlas are
// built in later batches). Guarding here keeps VIEWS a total Record<ViewName,…> so
// the union stays exhaustive without importing a not-yet-present module.
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
  settings: () => <PlaceholderView label="Settings" />,
  monitor: () => <PlaceholderView label="Monitor" />,
  atlas: () => <PlaceholderView label="Sky Atlas" />,
};

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

  useEffect(() => {
    connectWs();
  }, []);

  const Active = VIEWS[view];
  const camConnected = !!status?.connected?.camera?.connected;
  const mountConnected = !!status?.connected?.telescope?.connected;
  const seqRunning = sequence.state === "running" || sequence.state === "paused";
  const seqError = sequence.state === "error";
  const dim = linkDown || telemetryStale;

  return (
    <div className="h-full flex flex-col">
      {/* ------------------------------------------------ top status strip */}
      <header className="flex items-center gap-4 px-4 h-12 border-b border-line bg-raise/70 backdrop-blur shrink-0">
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
        <button
          className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
          onClick={toggleNight}
          title="Toggle red night vision mode"
        >
          <Icon name={night ? "sun" : "moon"} size={14} />
          {night ? "DAY" : "NIGHT"}
        </button>
        <button
          className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
          onClick={openLog}
          aria-label={unseenError > 0 ? `Open event log (${unseenError} unseen errors)` : "Open event log"}
        >
          <Icon name="alert" size={14} />
          LOG
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

      <div className="flex flex-1 min-h-0">
        {/* ---------------------------------------------------- left rail */}
        <nav className="hidden sm:flex flex-col w-[72px] border-r border-line bg-raise/40 py-2 shrink-0">
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
              {n.id === "capture" && camConnected && <span className="absolute top-2 right-3"><Led on /></span>}
              {n.id === "mount" && mountConnected && <span className="absolute top-2 right-3"><Led on /></span>}
              {n.id === "sequence" && seqError && (
                <span className="absolute top-2 right-3 led led-bad blink-alert" />
              )}
              {n.id === "sequence" && !seqError && seqRunning && (
                <span className="absolute top-2 right-3 blink"><Led on warn={sequence.state === "paused"} /></span>
              )}
            </button>
          ))}
        </nav>

        {/* ------------------------------------------------- main content */}
        <main
          className={`flex-1 overflow-y-auto p-4 pb-20 sm:pb-4 ${dim ? "opacity-60 transition-opacity" : "transition-opacity"}`}
          key={view}
        >
          <div className="view-enter max-w-[1500px] mx-auto">
            <Active />
          </div>
        </main>

        {/* Log drawer: docked column lg+, bottom sheet below — self-manages via
            store.logOpen; renders nothing when closed. */}
        <LogDrawer />
      </div>

      {/* --------------------------------------------- mobile bottom nav */}
      <nav className="sm:hidden flex border-t border-line bg-raise shrink-0 fixed bottom-0 inset-x-0 z-20">
        {NAV.map((n) => (
          <button key={n.id} onClick={() => setView(n.id)}
            className={`flex-1 flex flex-col items-center gap-0.5 py-2
              ${view === n.id ? "text-accent" : "text-dim"}`}>
            <Icon name={n.icon} size={18} />
            <span className="text-[8px] tracking-widest uppercase">{n.label}</span>
          </button>
        ))}
      </nav>

      {/* Toasts: top-center on phone, bottom-right desktop — self-manages via store. */}
      <Toasts />
    </div>
  );
}
