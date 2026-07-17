import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore, useStatus, usePolar, useLivePreviewId, useSequence } from "../store";
import { LivePreview } from "../components/preview/LivePreview";
import GuideFramePreview from "../components/GuideFramePreview";
import { Field, Led, Panel, Stat, Toggle } from "../components/ui";
import { useCanControlCapture } from "../lib/caps";
import { isExposureInvalid } from "../lib/exposure";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { HELP } from "../help";

// ---------------------------------------------------------------- capture phase
// Local-only capture progress machine (item 5). The capture POST is fire-and-forget
// on the backend; the ONLY honest completion signal the client has is a NEW live
// preview id arriving in the store (useLivePreviewId). So we drive a per-frame bar
// purely from local React state and reset it when the store's live id advances:
//
//   idle  --Single-->  exposing (bar fills 0→1 over exposure_s, stepped ~10Hz)
//                        └─ elapsed≥exposure_s ─→ downloading (striped indeterminate)
//                                                  └─ new live id ─→ idle
//
// For Loop we keep the bar CYCLING while status.looping: each new frame id restarts
// the exposing phase. We never claim a percentage during readout (download is
// indeterminate) — that would lie about transfer time we can't measure client-side.
type CapturePhase = "idle" | "exposing" | "downloading";

export default function CaptureView() {
  const status = useStatus();
  const polar = usePolar();
  const sequence = useSequence();
  const liveId = useLivePreviewId();
  const showToast = useStore((s) => s.showToast);
  const canCapture = useCanControlCapture(); // viewer => preview visible, controls read-only

  const [exposure, setExposure] = useState("2");
  const [gain, setGain] = useState("120");
  const [offset, setOffset] = useState("30");
  const [binning, setBinning] = useState("1");
  const [save, setSave] = useState(false);
  const [target, setTarget] = useState("");
  const [coolerTarget, setCoolerTarget] = useState("-10");
  const [dew, setDew] = useState(0);

  // --- capture feedback state ---
  const [phase, setPhase] = useState<CapturePhase>("idle");
  const [elapsed, setElapsed] = useState(0); // seconds into the current exposure
  const [stopPressed, setStopPressed] = useState(false); // brief pressed flash on Stop
  const expStartRef = useRef(0); // performance.now() when the frame's exposure began
  const expLenRef = useRef(1); // exposure_s of the in-flight frame
  const tickRef = useRef<number | null>(null);

  const cam = status?.camera;
  const cooler = cam?.cooler; // CoolerInfo | undefined (older status / no cooler)
  const looping = !!status?.looping;
  const polarBusy = polar.state === "running" || polar.state === "paused";
  // A sequence (incl. PAUSED — it still holds the camera between frames, not
  // released back to manual control) owns the camera end-to-end; manual
  // Single/Loop racing it just 409s at the capture lock (r1 CAP-01 / R2-CAP-01).
  const seqOwnsCamera = sequence.state === "running" || sequence.state === "paused";
  const captureBlocked = polarBusy || seqOwnsCamera; // can't expose while blocked

  // Exposure ≤0 silently produced a blank frame + a misleading "few stars"
  // error downstream (CAP-02-gemini / r1 CAP-01-neg); an absurd/unbounded
  // value (incl. scientific notation, which is a finite number and would
  // otherwise pass) is just as wrong (R3-CAP-02 note) — block both here,
  // before any request is built.
  const exposureNum = Number(exposure);
  const exposureInvalid = isExposureInvalid(exposure);
  const exposureS = exposureInvalid ? 1 : exposureNum;
  const body = {
    exposure_s: exposureS,
    gain: Number(gain) || 0,
    offset: Number(offset) || 0,
    binning: Number(binning) || 1,
    save,
    target,
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      // A backend 409 (e.g. polar alignment running) or any failure surfaces as a
      // toast; also unwind the local "in flight" state so the bar doesn't hang.
      setPhase("idle");
      showToast("error", (e as Error).message);
    }
  };

  // Begin (or restart) the exposing phase. Records the wall-clock start + the frame
  // length so the rAF/interval tick can fill the bar. Used by Single AND by Loop's
  // per-frame restart.
  const beginExposure = (len: number) => {
    expStartRef.current = performance.now();
    expLenRef.current = Math.max(0.1, len);
    setElapsed(0);
    setPhase("exposing");
  };

  // The 10Hz tick: advance `elapsed`; once it crosses the exposure length, flip to
  // the indeterminate "downloading" striped bar and wait for the new-frame signal.
  useEffect(() => {
    if (phase !== "exposing") {
      if (tickRef.current != null) {
        window.clearInterval(tickRef.current);
        tickRef.current = null;
      }
      return;
    }
    const step = () => {
      const e = (performance.now() - expStartRef.current) / 1000;
      setElapsed(e);
      if (e >= expLenRef.current) setPhase("downloading");
    };
    tickRef.current = window.setInterval(step, 100);
    return () => {
      if (tickRef.current != null) {
        window.clearInterval(tickRef.current);
        tickRef.current = null;
      }
    };
  }, [phase]);

  // Completion detection: a NEW live-preview id means the frame finished + decoded.
  // - Single: reset to idle.
  // - Loop:   if we're still looping, immediately restart the exposing bar so it
  //           cycles frame-to-frame; otherwise idle.
  // We seed the baseline on mount so an old id already in the store doesn't
  // instantly "complete" the first capture.
  const lastSeenIdRef = useRef<number | null>(liveId);
  useEffect(() => {
    if (liveId === lastSeenIdRef.current) return;
    lastSeenIdRef.current = liveId;
    // Only react if we were actually mid-capture (avoids resetting when a frame
    // arrives from an unrelated source while idle).
    if (phase === "idle") return;
    if (looping) beginExposure(exposureS);
    else setPhase("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveId]);

  // When looping flips OFF (Stop, or sequence end), drop any cycling bar to idle.
  useEffect(() => {
    if (!looping && phase !== "idle") {
      // give the last in-flight frame a beat; if not exposing, just idle.
      if (phase === "downloading") setPhase("idle");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [looping]);

  // tear down on unmount
  useEffect(
    () => () => {
      if (tickRef.current != null) window.clearInterval(tickRef.current);
    },
    [],
  );

  const onSingle = () => {
    if (captureBlocked || !canCapture || exposureInvalid) return;
    beginExposure(exposureS);
    act(() => api.post("/api/capture", body));
  };
  const onLoop = () => {
    if (captureBlocked || !canCapture || exposureInvalid) return;
    beginExposure(exposureS);
    act(() => api.post("/api/capture/loop", body));
  };
  const onStop = () => {
    if (!canCapture) return;
    setStopPressed(true);
    window.setTimeout(() => setStopPressed(false), 220);
    setPhase("idle");
    act(() => api.post("/api/capture/stop"));
  };

  const inFlight = phase !== "idle";
  const fillPct = phase === "exposing"
    ? Math.min(100, (elapsed / expLenRef.current) * 100)
    : 100;
  const remaining = Math.max(0, expLenRef.current - elapsed);

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_320px] xl:grid-cols-[1fr_360px]">
      {/* live-preview overhaul: stage + zoom/pan + stretch + overlays + filmstrip */}
      <LivePreview />

      <div className="flex flex-col gap-4">
        {/* ------------------------------------------------- exposure ctl */}
        <Panel title="Exposure" right={!canCapture && <ReadOnlyBadge />}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Exposure (s)">
              <input
                className={`field ${exposureInvalid ? "border-bad" : ""}`}
                value={exposure}
                disabled={!canCapture}
                aria-invalid={exposureInvalid}
                onChange={(e) => setExposure(e.target.value)}
              />
              {exposureInvalid && (
                <p className="text-[11px] text-bad mt-1">Exposure must be 0–3600s</p>
              )}
            </Field>
            <Field label={`Gain${cam?.max_gain ? ` (max ${cam.max_gain})` : ""}`}>
              <input className="field" value={gain} disabled={!canCapture} onChange={(e) => setGain(e.target.value)} />
            </Field>
            <Field label="Offset" hint={HELP.offset}>
              <input className="field" value={offset} disabled={!canCapture} onChange={(e) => setOffset(e.target.value)} />
            </Field>
            <Field label="Binning" hint={HELP.binning}>
              <select className="field" value={binning} disabled={!canCapture} onChange={(e) => setBinning(e.target.value)}>
                {[1, 2, 4].map((b) => <option key={b} value={b}>{b}×{b}</option>)}
              </select>
            </Field>
          </div>
          <div className="flex items-center gap-3 mt-3">
            <Toggle checked={save} onChange={setSave} disabled={!canCapture} label="Save FITS to library" />
            <span className="text-xs text-dim">save FITS to library</span>
          </div>
          {save && (
            <div className="mt-2">
              <Field label="Target name">
                <input className="field" placeholder="M42" value={target} disabled={!canCapture}
                  onChange={(e) => setTarget(e.target.value)} />
              </Field>
            </div>
          )}

          {/* ---- capture buttons. Single/Loop show an active state in flight and are
               BLOCKED during polar alignment; Stop flashes pressed + stays 1-tap. ---- */}
          <div className="grid grid-cols-3 gap-2 mt-4">
            <button
              className={`btn tap-lg min-h-[56px] ${phase === "exposing" || phase === "downloading" ? "btn-accent border-accent" : "btn-accent"}`}
              aria-pressed={inFlight && !looping}
              disabled={!canCapture || looping || captureBlocked || exposureInvalid}
              onClick={onSingle}>
              {inFlight && !looping
                ? (phase === "downloading" ? "Reading…" : "Exposing…")
                : "Single"}
            </button>
            <button
              className={`btn tap-lg min-h-[56px] ${looping ? "btn-accent border-accent" : ""}`}
              aria-pressed={looping}
              disabled={!canCapture || looping || captureBlocked || exposureInvalid}
              onClick={onLoop}>
              {looping ? "Looping…" : "Loop"}
            </button>
            {/* Stop is urgent -> stays 1-tap (R9), enlarged for touch. Disabled for
                viewers (no capture to stop — they can't have started one). */}
            <button
              className={`btn btn-danger tap-lg min-h-[56px] ${stopPressed ? "scale-95 brightness-110" : ""}`}
              aria-pressed={stopPressed}
              disabled={!canCapture}
              onClick={onStop}>
              Stop
            </button>
          </div>

          {/* ---- per-frame progress (item 5a). Exposing → deterministic fill that
               resets each frame; Downloading → indeterminate striped bar. Hidden
               when idle and not looping. ---- */}
          {(inFlight || looping) && (
            <div className="mt-3">
              <div
                className="progress-track !h-2.5"
                role="meter"
                aria-label="capture progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={phase === "downloading" ? undefined : Math.round(fillPct)}>
                {phase === "downloading" ? (
                  <div className="progress-stripes" />
                ) : (
                  <div
                    className="progress-fill !transition-none"
                    style={{ width: `${fillPct}%` }}
                  />
                )}
              </div>
              <div className="flex justify-between mt-1 text-[11px] text-dim mono">
                <span className="uppercase tracking-wider">
                  {phase === "downloading"
                    ? "downloading…"
                    : phase === "exposing"
                      ? "exposing"
                      : looping
                        ? "looping"
                        : ""}
                </span>
                {phase === "exposing" && <span>{remaining.toFixed(1)}s</span>}
              </div>
            </div>
          )}

          {/* ---- polar-alignment block notice (item 5c). ---- */}
          {polarBusy && (
            <p className="text-[11px] text-warn mt-2 leading-snug">
              Can&rsquo;t capture during polar alignment — stop alignment first.
            </p>
          )}
          {/* ---- sequence-ownership block notice (R2-CAP-01 / DOC-CAP-01): a running
               OR paused sequence still owns the camera between frames, so manual
               Single/Loop must read as blocked here instead of 409ing after the tap. ---- */}
          {seqOwnsCamera && (
            <p className="text-[11px] text-warn mt-2 leading-snug">
              {sequence.state === "paused" ? "Sequence paused" : "Sequence running"} — camera reserved.
            </p>
          )}

          {looping && !inFlight && (
            <p className="text-[11px] text-accent mt-2 blink tracking-widest uppercase">● looping</p>
          )}
        </Panel>

        {/* histogram + stretch now live inside <LivePreview/> (the stretch is
            coupled to the image, so a decoupled read-only histogram would lie) */}

        {/* ------------------------------------------------- guide-cam preview (item 6)
            Shared-lane panel: collapsible, self-polls GET /api/guide/frame.png only
            while toggled on. Lets the user glance at the guide field from Capture. */}
        <GuideFramePreview />

        {/* ------------------------------------------------------- filter */}
        {status?.filterwheel && (
          <Panel title="Filter Wheel" right={!canCapture && <ReadOnlyBadge />}>
            <div className="flex flex-wrap gap-2">
              {status.filterwheel.names.map((name, i) => (
                <button key={name}
                  disabled={!canCapture}
                  className={`btn tap min-h-[44px] !px-3 min-w-[56px] ${i === status.filterwheel!.position ? "btn-accent" : ""}`}
                  onClick={() => act(() => api.post("/api/filterwheel/position", { position: i }))}>
                  {name}
                </button>
              ))}
            </div>
          </Panel>
        )}

        {/* ------------------------------------------------------- cooler */}
        {cam?.can_cool && (
          <Panel title="Cooler" right={!canCapture && <ReadOnlyBadge />}>
            {/* ---- cooling state indicator (item 3). LED+badge encode ON/OFF by
                 shape+text (night palette collapses color), with live power% and an
                 "at target" chip. Gracefully degrades when cooler is absent. ---- */}
            <div className="flex items-center justify-between gap-2 mb-3">
              <span className="inline-flex items-center gap-2">
                <Led state={cooler?.on ? "on" : "off"} label={cooler?.on ? "cooling on" : "cooling off"} />
                <span className={`mono text-xs tracking-wider uppercase ${cooler?.on ? "text-accent" : "text-dim"}`}>
                  {cooler?.on ? "Cooling" : "Off"}
                </span>
              </span>
              <span className="inline-flex items-center gap-2">
                {cooler?.on && cooler.can_report_power && cooler.power != null && (
                  <span className="mono text-xs text-dim">{Math.round(cooler.power)}%</span>
                )}
                {cooler?.on && cooler.at_target && (
                  <span className="px-1.5 py-0.5 text-[10px] tracking-wider uppercase border border-good/50 text-good">
                    at target
                  </span>
                )}
              </span>
            </div>

            <div className="flex items-end flex-wrap gap-3">
              <Stat label="sensor" value={cam.temperature?.toFixed(1) ?? "—"} unit="°C"
                tone={cam.temperature != null && cam.temperature < 0 ? "good" : undefined} />
              {cooler?.target_c != null && (
                <Stat label="target" value={cooler.target_c.toFixed(1)} unit="°C" />
              )}
              <Field label="Target °C" hint={HELP.coolTo}>
                <input className="field !w-20" value={coolerTarget} disabled={!canCapture}
                  onChange={(e) => setCoolerTarget(e.target.value)} />
              </Field>
              <button
                className={`btn tap min-h-[44px] ${cooler?.on ? "btn-accent border-accent" : ""}`}
                aria-pressed={!!cooler?.on}
                disabled={!canCapture || !!cooler?.on}
                onClick={() => act(() => api.post("/api/camera/cooler", { on: true, target_c: Number(coolerTarget) }))}>
                {cooler?.on ? "Cooling" : "Cool"}
              </button>
              <button
                className="btn tap min-h-[44px]"
                disabled={!canCapture || (cooler != null && !cooler.on)}
                onClick={() => act(() => api.post("/api/camera/cooler", { on: false }))}>
                Warm
              </button>
            </div>

            {cam.has_dew_heater && (
              <div className="mt-4 border-t border-line pt-3">
                <div className="flex justify-between mb-1.5">
                  <span className="label">dew heater</span>
                  <span className="mono text-xs text-accent">{dew}%</span>
                </div>
                <input type="range" min={0} max={100} value={dew}
                  aria-label="dew heater power"
                  disabled={!canCapture}
                  className={`w-full h-11 accent-(--accent) touch-none ${canCapture ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                  onChange={(e) => { setDew(Number(e.target.value)); }}
                  onMouseUp={() => act(() => api.post("/api/camera/dew-heater", { power: dew }))}
                  onTouchEnd={() => act(() => api.post("/api/camera/dew-heater", { power: dew }))} />
              </div>
            )}
          </Panel>
        )}
      </div>
    </div>
  );
}
