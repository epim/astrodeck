import { useEffect, useState } from "react";
import { useStore, type ViewName } from "./store";
import { connectWs } from "./ws";
import { Led } from "./components/ui";
import ConnectView from "./views/ConnectView";
import CaptureView from "./views/CaptureView";
import FocusView from "./views/FocusView";
import MountView from "./views/MountView";
import PolarView from "./views/PolarView";
import GuideView from "./views/GuideView";
import SequenceView from "./views/SequenceView";
import PowerView from "./views/PowerView";

const NAV: { id: ViewName; label: string; icon: string }[] = [
  { id: "connect", label: "Rig", icon: "◈" },
  { id: "capture", label: "Capture", icon: "◉" },
  { id: "focus", label: "Focus", icon: "◎" },
  { id: "mount", label: "Mount", icon: "✛" },
  { id: "polar", label: "Align", icon: "⊕" },
  { id: "guide", label: "Guide", icon: "❖" },
  { id: "sequence", label: "Plan", icon: "≡" },
  { id: "power", label: "Power", icon: "⏻" },
];

const VIEWS: Record<ViewName, () => JSX.Element> = {
  connect: ConnectView, capture: CaptureView, focus: FocusView,
  mount: MountView, polar: PolarView, guide: GuideView,
  sequence: SequenceView, power: PowerView,
};

export default function App() {
  const { view, setView, night, toggleNight, wsConnected, status, sequence, toast } = useStore();
  const [logsOpen, setLogsOpen] = useState(false);
  const logs = useStore((s) => s.logs);

  useEffect(() => { connectWs(); }, []);

  const Active = VIEWS[view];
  const camConnected = !!status?.connected?.camera?.connected;
  const mountConnected = !!status?.connected?.telescope?.connected;
  const seqRunning = sequence.state === "running" || sequence.state === "paused";

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
        <div className="hidden md:flex items-center gap-4 text-xs mono text-dim min-w-0 overflow-hidden">
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
          className="btn !py-1 !px-2.5 text-[10px]"
          onClick={toggleNight}
          title="Toggle red night vision mode"
        >
          {night ? "◐ DAY" : "● NIGHT"}
        </button>
        <button className="btn !py-1 !px-2.5 text-[10px]" onClick={() => setLogsOpen(!logsOpen)}>
          LOG
        </button>
        <div className="flex items-center gap-1.5" title={wsConnected ? "Link up" : "Link down"}>
          <Led on={wsConnected} />
          <span className="label hidden sm:inline">{wsConnected ? "LINK" : "NO LINK"}</span>
        </div>
      </header>

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
              <span className="text-lg leading-none">{n.icon}</span>
              <span className="text-[9px] tracking-[0.18em] font-display font-medium uppercase">{n.label}</span>
              {n.id === "capture" && camConnected && <span className="absolute top-2 right-3"><Led on /></span>}
              {n.id === "mount" && mountConnected && <span className="absolute top-2 right-3"><Led on /></span>}
              {n.id === "sequence" && seqRunning && (
                <span className="absolute top-2 right-3 blink"><Led on warn={sequence.state === "paused"} /></span>
              )}
            </button>
          ))}
        </nav>

        {/* ------------------------------------------------- main content */}
        <main className="flex-1 overflow-y-auto p-4 pb-20 sm:pb-4" key={view}>
          <div className="view-enter max-w-[1500px] mx-auto">
            <Active />
          </div>
        </main>

        {/* ------------------------------------------------- log drawer */}
        {logsOpen && (
          <aside className="w-[340px] border-l border-line bg-raise/60 backdrop-blur p-3 overflow-y-auto hidden lg:block shrink-0">
            <h2 className="panel-title mb-2">Event Log</h2>
            <div className="flex flex-col gap-1.5">
              {[...logs].reverse().map((l, i) => (
                <div key={i} className="text-[11px] mono leading-snug">
                  <span className={
                    l.data.level === "error" ? "text-bad" :
                    l.data.level === "warning" ? "text-warn" : "text-accent2"
                  }>[{l.data.source}]</span>{" "}
                  <span className="text-ink/90">{l.data.message}</span>
                </div>
              ))}
              {logs.length === 0 && <p className="text-dim text-xs">no events yet</p>}
            </div>
          </aside>
        )}
      </div>

      {/* --------------------------------------------- mobile bottom nav */}
      <nav className="sm:hidden flex border-t border-line bg-raise shrink-0 fixed bottom-0 inset-x-0 z-20">
        {NAV.map((n) => (
          <button key={n.id} onClick={() => setView(n.id)}
            className={`flex-1 flex flex-col items-center gap-0.5 py-2
              ${view === n.id ? "text-accent" : "text-dim"}`}>
            <span className="text-base leading-none">{n.icon}</span>
            <span className="text-[8px] tracking-widest uppercase">{n.label}</span>
          </button>
        ))}
      </nav>

      {/* ------------------------------------------------------- toast */}
      {toast && (
        <div key={toast.key}
          className={`fixed bottom-16 sm:bottom-6 left-1/2 -translate-x-1/2 z-30 panel px-4 py-2 text-xs mono
            ${toast.level === "error" ? "text-bad" : "text-warn"}`}
          style={{ animation: "fade-up 0.2s ease both" }}
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}
