import { api } from "../api";
import { useStore, useStatus, usePolar } from "../store";
import { PolarReticle } from "../components/polar";
import { Panel } from "../components/ui";

export default function PolarView() {
  const polar = usePolar();
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const running = polar.state === "running" || polar.state === "paused";

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  const az = polar.az_error, alt = polar.alt_error, total = polar.total_error;
  const tone = total < 1 ? "good" : total < 5 ? "warn" : "bad";
  const hasError = polar.state !== "idle";

  // direction conventions (validated against a live TPPA run when on-sky)
  const azDir = az < 0 ? "E" : "W";
  const azArrow = az < 0 ? "◀" : "▶";
  const altWord = alt > 0 ? "lower scope" : "raise scope";
  const altArrow = alt > 0 ? "▼" : "▲";

  const sourceLabel = polar.source === "nina" ? "NINA TPPA"
    : polar.source === "sim" ? "simulator" : null;
  const willUseNina = !!status?.connected?.telescope && status?.mode === "nina";

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <Panel title="Polar Alignment"
        right={
          <span className={`text-[11px] tracking-widest uppercase ${
            polar.state === "done" ? "text-good"
              : polar.state === "running" ? "text-accent blink"
              : polar.state === "paused" ? "text-warn"
              : polar.state === "error" ? "text-bad" : "text-dim"}`}>
            {polar.state}{sourceLabel ? ` · ${sourceLabel}` : ""}
          </span>
        }>
        <PolarReticle az={az} alt={alt} />
        <p className="text-center text-xs text-dim mt-2 min-h-4">{polar.message || " "}</p>
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="Error">
          <div className="flex items-baseline justify-center gap-3 mb-4">
            <span className="label">total</span>
            <span className={`font-display font-semibold text-4xl mono ${
              tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : "text-bad"}`}>
              {hasError ? `${total.toFixed(1)}'` : "—"}
            </span>
          </div>
          <div className="flex flex-col">
            <div className="flex items-center gap-3 border-t border-line py-2.5">
              <span className="label w-16">Azimuth</span>
              <span className="text-dim text-xs">turn AZ knob</span>
              <span className="text-accent text-base ml-auto">{hasError ? azArrow : ""}</span>
              <span className="mono text-sm w-16 text-right">
                {hasError ? `${Math.abs(az).toFixed(1)}' ${azDir}` : "—"}
              </span>
            </div>
            <div className="flex items-center gap-3 border-t border-line py-2.5">
              <span className="label w-16">Altitude</span>
              <span className="text-dim text-xs">{hasError ? altWord : "alt bolt"}</span>
              <span className="text-accent text-base ml-auto">{hasError ? altArrow : ""}</span>
              <span className="mono text-sm w-16 text-right">
                {hasError ? `${Math.abs(alt).toFixed(1)}'` : "—"}
              </span>
            </div>
          </div>
          {polar.progress > 0 && polar.progress < 1 && (
            <div className="progress-track mt-4">
              <div className="progress-fill" style={{ width: `${polar.progress * 100}%` }} />
            </div>
          )}
        </Panel>

        <Panel title="Control">
          <p className="text-xs text-dim mb-3 leading-relaxed">
            {willUseNina
              ? "Runs NINA's Three-Point Polar Alignment on your rig: it rotates in RA, plate-solves, and streams the live error here as you adjust the mount's altitude/azimuth bolts."
              : "No NINA rig bridged — this runs the built-in simulator so you can see the full alignment flow. Bridge to NINA on the Rig page to align real hardware."}
          </p>
          <div className="flex flex-col gap-2">
            <button className="btn btn-accent" disabled={running}
              onClick={() => act(() => api.post("/api/polar/start"))}>
              ⊕ Start Alignment
            </button>
            <div className="grid grid-cols-2 gap-2">
              {polar.state === "paused" ? (
                <button className="btn" disabled={!running}
                  onClick={() => act(() => api.post("/api/polar/resume"))}>Resume</button>
              ) : (
                <button className="btn" disabled={!running}
                  onClick={() => act(() => api.post("/api/polar/pause"))}>Pause</button>
              )}
              <button className="btn btn-danger" disabled={!running}
                onClick={() => act(() => api.post("/api/polar/stop"))}>Stop</button>
            </div>
          </div>
          {polar.state === "done" && (
            <p className="text-good text-xs mono mt-3">✓ aligned to {total.toFixed(1)}' total error</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
