import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import {
  useStore, useStatus, usePolar, useLivePreviewId, useSequence, useLastLight,
  usePhotometry, usePreview,
} from "../store";
import { LivePreview } from "../components/preview/LivePreview";
import GuideFramePreview from "../components/GuideFramePreview";
import { Field, Led, Panel, SegmentedControl, Stat, Toggle } from "../components/ui";
import { useCanControlCapture } from "../lib/caps";
import { isExposureInvalid } from "../lib/exposure";
import { CAPTURE_PRESETS } from "../lib/capturePresets";
import { suggestSubLength } from "../lib/photometry";
import {
  FRAME_TYPES,
  FRAME_COACH,
  darkPrefillFrom,
  shouldOfferDarks,
  formatLightSummary,
  type FrameType,
} from "../lib/calibration";
import { confirmDialog } from "../components/ConfirmDialog";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { Icon } from "../components/icons";
import { FilterNamesModal } from "../components/capture/FilterNamesModal";
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

// UX-30: a failed single-frame readout never produces a new live-preview id, so
// the "downloading…" bar would spin forever. Bound the download phase with a
// watchdog — generous enough for a slow USB2 full-frame readout, short enough
// that a genuine stall surfaces instead of hanging.
const DOWNLOAD_WATCHDOG_MS = 60_000;

// UX-28: a cooler set-point outside this range (or blank/non-numeric) is almost
// certainly a typo; block it rather than POST a NaN that serializes to null.
const COOLER_MIN_C = -60;
const COOLER_MAX_C = 40;

export default function CaptureView() {
  const status = useStatus();
  const polar = usePolar();
  const sequence = useSequence();
  const liveId = useLivePreviewId();
  const showToast = useStore((s) => s.showToast);
  const noteLightFrame = useStore((s) => s.noteLightFrame);
  const lastLight = useLastLight();
  const canCapture = useCanControlCapture(); // viewer => preview visible, controls read-only

  // --- NOV-4 photometry profile + Suggest settings (photometry/SNR design §3 Task 4) ---
  const photometryProfile = usePhotometry();
  const setPhotometry = useStore((s) => s.setPhotometry);
  const livePreview = usePreview();

  const [exposure, setExposure] = useState("2");
  const [gain, setGain] = useState("120");
  const [offset, setOffset] = useState("30");
  const [binning, setBinning] = useState("1");
  const [save, setSave] = useState(false);
  const [target, setTarget] = useState("");
  const [coolerTarget, setCoolerTarget] = useState("-10");
  const [dew, setDew] = useState(0);
  const [filterEditOpen, setFilterEditOpen] = useState(false); // UX-05 slot-name modal
  // Calibration quick-action (calibration-capture spec §1.3): which frame type
  // Single/Loop will shoot. Manual capture defaults to Light (today's only
  // behavior); the shutter follows this via body.frame_type -> hub.capture.
  const [frameType, setFrameType] = useState<FrameType>("Light");

  // --- capture feedback state ---
  const [phase, setPhase] = useState<CapturePhase>("idle");
  const [elapsed, setElapsed] = useState(0); // seconds into the current exposure
  const [stopPressed, setStopPressed] = useState(false); // brief pressed flash on Stop
  const expStartRef = useRef(0); // performance.now() when the frame's exposure began
  const expLenRef = useRef(1); // exposure_s of the in-flight frame
  const tickRef = useRef<number | null>(null);
  // Mirrors `frameType` each render (same pattern as expLenRef) so the
  // liveId-advance completion effect and onStop read it without a stale closure.
  const frameTypeRef = useRef<FrameType>("Light");
  frameTypeRef.current = frameType;
  // Guards the end-of-session "take matching darks?" nudge to once per Light
  // loop batch; reset when a fresh Light loop begins (see onLoop).
  const offeredRef = useRef(false);

  const cam = status?.camera;
  // UX-27: offer bins 1..max_bin from the camera's reported ceiling instead of a
  // hardcoded [1,2,4]. Clamp to a sane 1–8 in case a backend reports garbage.
  const maxBin = Math.min(8, Math.max(1, cam?.max_bin ?? 4));
  const binOptions = Array.from({ length: maxBin }, (_, i) => i + 1);
  const cooler = cam?.cooler; // CoolerInfo | undefined (older status / no cooler)
  // UX-28: only send a finite, in-range set-point. Number("") is 0 and
  // Number("x") is NaN (→ null over JSON); guard both so "Cool" never posts
  // target_c:null with on:true.
  const coolerTargetNum = Number(coolerTarget);
  const coolerTargetInvalid =
    coolerTarget.trim() === "" || !Number.isFinite(coolerTargetNum) ||
    coolerTargetNum < COOLER_MIN_C || coolerTargetNum > COOLER_MAX_C;
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
  // UX-43: enforce the gain ceiling the label already advertises (cam.max_gain),
  // mirroring the exposure guard — a blank/out-of-range gain must block the
  // capture, not silently POST 0 or an over-range value the driver rejects.
  const gainNum = Number(gain);
  const gainInvalid =
    gain.trim() === "" || !Number.isFinite(gainNum) || gainNum < 0 ||
    (!!cam?.max_gain && gainNum > cam.max_gain);
  const body = {
    exposure_s: exposureS,
    gain: gainInvalid ? 0 : gainNum,
    offset: Number(offset) || 0,
    binning: Number(binning) || 1,
    save,
    target,
    frame_type: frameType,
  };

  // --- NOV-4 Suggest settings (photometry/SNR design §3 Task 4) ---
  // egain prefers the manually-entered profile value; falls back to the
  // camera-reported one (Task 7's status.camera.egain, native adapters only)
  // when the profile is still empty. Never silently overrides a user entry.
  const camEgain = status?.camera?.egain;
  const usingCameraEgain = photometryProfile.egain <= 0 && !!camEgain && camEgain > 0;
  const effectiveEgain = usingCameraEgain ? camEgain! : photometryProfile.egain;
  const linearMedian = livePreview && livePreview.data_is_linear
    ? livePreview.stats.median
    : null;
  const canSuggest = effectiveEgain > 0 && photometryProfile.readNoiseE > 0 && linearMedian != null;
  const onSuggest = () => {
    if (!canSuggest || linearMedian == null || !livePreview) return;
    const s = suggestSubLength({
      medianAdu: linearMedian, biasAdu: photometryProfile.biasAdu, egain: effectiveEgain,
      readNoiseE: photometryProfile.readNoiseE, exposureS: livePreview.exposure_s,
    });
    if (!s.ok || s.suggestedS == null) { showToast("warning", s.reason); return; }
    setExposure(String(s.suggestedS));
    showToast("success", `Suggested ${s.suggestedS}s — ${s.reason}`);
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      // A 409 means the capture lock is already held (a raced double-tap on
      // Single, a running sequence, polar alignment, ...) — some capture may
      // genuinely be in flight and its progress state must not be stomped,
      // so only unwind to idle for OTHER failures. Always surface the toast.
      const status = e instanceof ApiError ? e.status : undefined;
      if (status !== 409) setPhase("idle");
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
    // A Light frame just landed via OUR Single/Loop — bank it (calibration
    // capture spec §1.3). Dark/Flat/Bias frames never feed the "last lights"
    // snapshot the darks nudge / "Match last lights" prefill read from.
    if (frameTypeRef.current === "Light") {
      noteLightFrame({
        exposureS,
        gain: gainInvalid ? 0 : gainNum,
        offset: Number(offset) || 0,
        binning: Number(binning) || 1,
        tempC: cam?.temperature ?? null,
      });
    }
    if (looping) beginExposure(exposureS);
    else setPhase("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveId]);

  // UX-30: watchdog the download phase so a dropped/failed readout can't leave
  // the striped bar spinning forever. Cleared automatically when the frame lands
  // (phase leaves "downloading") or the component unmounts.
  useEffect(() => {
    if (phase !== "downloading") return;
    const t = window.setTimeout(() => {
      setPhase("idle");
      showToast("error", "Frame readout timed out — no image arrived. The camera may have dropped the frame.");
    }, DOWNLOAD_WATCHDOG_MS);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

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
    if (captureBlocked || !canCapture || exposureInvalid || gainInvalid) return;
    beginExposure(exposureS);
    act(() => api.post("/api/capture", body));
  };
  const onLoop = () => {
    if (captureBlocked || !canCapture || exposureInvalid || gainInvalid) return;
    // A fresh Light loop starting is a new batch — clear the once-per-batch
    // darks-nudge guard so onStop can offer again for THIS batch.
    if (frameType === "Light") offeredRef.current = false;
    beginExposure(exposureS);
    act(() => api.post("/api/capture/loop", body));
  };
  const onStop = () => {
    if (!canCapture) return;
    setStopPressed(true);
    window.setTimeout(() => setStopPressed(false), 220);
    setPhase("idle");
    // End-of-session nudge (calibration-capture spec §1.3): stopping a Light
    // loop with a bankable batch of lights offers to switch to Dark + prefill.
    // Guarded to once per batch (offeredRef, reset in onLoop) — never nags
    // after a Single, and never re-offers on a second Stop tap.
    const wasLightLoop = looping && frameTypeRef.current === "Light";
    if (wasLightLoop && shouldOfferDarks(lastLight) && !offeredRef.current) {
      offeredRef.current = true;
      void confirmDialog({
        title: "Take matching darks?",
        body: `You shot ${formatLightSummary(lastLight)} — shoot matching darks now?`,
        confirmLabel: "Set up darks",
        cancelLabel: "Not now",
        confirmPrimary: true,
      }).then((ok) => {
        if (!ok) return;
        const p = darkPrefillFrom(lastLight);
        setFrameType("Dark");
        setExposure(p.exposure);
        setGain(p.gain);
        setOffset(p.offset);
        setBinning(p.binning);
        if (p.coolerTarget) setCoolerTarget(p.coolerTarget);
        showToast("info", "Darks set up — cap the scope, then press Single or Loop.");
      });
    }
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
              <input
                className={`field ${gainInvalid ? "border-bad" : ""}`}
                value={gain}
                disabled={!canCapture}
                aria-invalid={gainInvalid}
                onChange={(e) => setGain(e.target.value)}
              />
              {gainInvalid && (
                <p className="text-[11px] text-bad mt-1">
                  Gain must be 0{cam?.max_gain ? `–${cam.max_gain}` : " or more"}
                </p>
              )}
            </Field>
            <Field label="Offset" hint={HELP.offset}>
              <input className="field" value={offset} disabled={!canCapture} onChange={(e) => setOffset(e.target.value)} />
            </Field>
            <Field label="Binning" hint={HELP.binning}>
              <select className="field" value={binning} disabled={!canCapture} onChange={(e) => setBinning(e.target.value)}>
                {binOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
              </select>
            </Field>
          </div>

          {/* ---- NOV-4 beginner capture presets (photometry/SNR design §3 Task 4):
               one-tap exposure/gain/offset/binning fill via the same setter path as
               "Match last lights" below. Pure data from lib/capturePresets. ---- */}
          <div className="flex flex-wrap gap-2 mt-3">
            {CAPTURE_PRESETS.map((p) => (
              <button
                key={p.id}
                className="btn tap min-h-[44px] !px-3"
                disabled={!canCapture}
                title={p.blurb}
                onClick={() => {
                  setExposure(String(p.exposure_s));
                  setGain(String(p.gain));
                  setOffset(String(p.offset));
                  setBinning(String(p.binning));
                  showToast("info", `Preset: ${p.label}`);
                }}>
                {p.label}
              </button>
            ))}
          </div>

          {/* ---- Camera photometry profile (NOV-4/PRO-6 shared input, §1.3): a small
               persisted client-only egain/read-noise/bias-ADU profile. Feeds Suggest
               settings below + the Sequence/Monitor SNR readouts (photometry.ts, the
               tested core — no math duplicated here). All-zero = inert; never a wrong
               number, only an honest "add these" prompt downstream. ---- */}
          <div className="mt-3 border-t border-line pt-3">
            <div className="label mb-2">Camera photometry</div>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Gain (e-/ADU)"
                hint="Your camera's sensor gain in electrons/ADU at the gain setting above — from the read-noise harness or the camera datasheet.">
                {usingCameraEgain ? (
                  <div className="flex items-center gap-1.5">
                    <input className="field" value={camEgain!.toFixed(3)} disabled title="From camera" />
                  </div>
                ) : (
                  <input className="field" inputMode="decimal"
                    value={photometryProfile.egain || ""}
                    disabled={!canCapture}
                    placeholder="0.25"
                    onChange={(e) => setPhotometry({ egain: Number(e.target.value) || 0 })} />
                )}
                {usingCameraEgain && (
                  <p className="text-[10px] text-dim mt-1 uppercase tracking-wider">from camera</p>
                )}
              </Field>
              <Field label="Read noise (e-)"
                hint="Your camera's read noise in electrons at this gain — from the read-noise harness or datasheet.">
                <input className="field" inputMode="decimal"
                  value={photometryProfile.readNoiseE || ""}
                  disabled={!canCapture}
                  placeholder="2.0"
                  onChange={(e) => setPhotometry({ readNoiseE: Number(e.target.value) || 0 })} />
              </Field>
              <Field label="Bias (ADU)"
                hint="Median of a Bias frame (offset pedestal). Defaults to 0 — slightly overestimates sky, which is safe.">
                <input className="field" inputMode="decimal"
                  value={photometryProfile.biasAdu || ""}
                  disabled={!canCapture}
                  placeholder="0"
                  onChange={(e) => setPhotometry({ biasAdu: Number(e.target.value) || 0 })} />
              </Field>
            </div>
            {canSuggest ? (
              <button className="btn tap min-h-[44px] mt-3" onClick={onSuggest}>
                Suggest settings
              </button>
            ) : (
              // Honest-disabled idiom (§11.8): dim + lock glyph + aria-disabled +
              // title, never native `disabled` — matches "Match last lights" below.
              <span
                className="btn tap min-h-[44px] mt-3 opacity-40 inline-flex items-center gap-1.5 cursor-not-allowed"
                aria-disabled
                title={
                  effectiveEgain <= 0 || photometryProfile.readNoiseE <= 0
                    ? "Add camera gain + read noise above to enable Suggest"
                    : "Take a light frame first — Suggest needs a linear preview"
                }>
                <Icon name="lock" size={12} /> Suggest settings
              </span>
            )}
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

          {/* ---- calibration quick-action (calibration-capture spec §1.3): frame-type
               picker + coach text + "Match last lights" one-tap prefill. Single/Loop
               follow `frameType` via body.frame_type -> hub.capture's shutter. ---- */}
          <div className="mt-3">
            <SegmentedControl<FrameType>
              ariaLabel="frame type"
              options={FRAME_TYPES.map((f) => ({ value: f, label: f }))}
              value={frameType}
              onChange={setFrameType}
              disabled={!canCapture}
            />
            {frameType !== "Light" && (
              <p className="text-[11px] text-dim mt-2 leading-snug">{FRAME_COACH[frameType]}</p>
            )}
            {(frameType === "Dark" || frameType === "Bias") && (
              lastLight ? (
                <button
                  className="btn tap min-h-[44px] mt-2"
                  onClick={() => {
                    const p = darkPrefillFrom(lastLight);
                    setFrameType(p.frameType);
                    setExposure(p.exposure);
                    setGain(p.gain);
                    setOffset(p.offset);
                    setBinning(p.binning);
                    if (p.coolerTarget) setCoolerTarget(p.coolerTarget);
                  }}>
                  Match last lights
                </button>
              ) : (
                // Honest-disabled idiom (§11.8): dim + lock glyph + aria-disabled +
                // title, never native `disabled` — matches PreviewToolbar's Toggle.
                <span
                  className="btn tap min-h-[44px] mt-2 opacity-40 inline-flex items-center gap-1.5 cursor-not-allowed"
                  aria-disabled
                  title="Shoot some lights first">
                  <Icon name="lock" size={12} /> Match last lights
                </span>
              )
            )}
          </div>

          {/* ---- capture buttons. Single/Loop show an active state in flight and are
               BLOCKED during polar alignment; Stop flashes pressed + stays 1-tap. ---- */}
          <div className="grid grid-cols-3 gap-2 mt-4">
            <button
              className={`btn tap-lg min-h-[56px] ${phase === "exposing" || phase === "downloading" ? "btn-accent border-accent" : "btn-accent"}`}
              aria-pressed={inFlight && !looping}
              disabled={!canCapture || looping || captureBlocked || exposureInvalid || gainInvalid || inFlight}
              onClick={onSingle}>
              {inFlight && !looping
                ? (phase === "downloading" ? "Reading…" : "Exposing…")
                : "Single"}
            </button>
            <button
              className={`btn tap-lg min-h-[56px] ${looping ? "btn-accent border-accent" : ""}`}
              aria-pressed={looping}
              disabled={!canCapture || looping || captureBlocked || exposureInvalid || gainInvalid}
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
          <Panel title="Filter Wheel"
            right={
              <span className="flex items-center gap-2">
                {!canCapture && <ReadOnlyBadge />}
                {canCapture && (
                  <button className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-dim hover:text-accent"
                    aria-label="Edit filter slot names"
                    title="Edit filter slot names"
                    onClick={() => setFilterEditOpen(true)}>
                    <Icon name="settings" size={18} />
                  </button>
                )}
              </span>
            }>
            <div className="flex flex-wrap gap-2">
              {status.filterwheel.names.map((name, i) => (
                <button key={`${i}-${name}`}
                  disabled={!canCapture}
                  className={`btn tap min-h-[44px] !px-3 min-w-[56px] ${i === status.filterwheel!.position ? "btn-accent" : ""}`}
                  onClick={() => act(() => api.post("/api/filterwheel/position", { position: i }))}>
                  {name}
                </button>
              ))}
            </div>
          </Panel>
        )}
        {status?.filterwheel && (
          <FilterNamesModal
            open={filterEditOpen}
            onClose={() => setFilterEditOpen(false)}
            names={status.filterwheel.names}
            offsets={status.filterwheel.offsets ?? []}
            onSave={async (names, offsets) => {
              await api.post("/api/filterwheel/names", { names, offsets });
              showToast("success", "Filter names saved");
            }}
          />
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
                <input className={`field !w-20 ${coolerTargetInvalid ? "border-bad" : ""}`}
                  value={coolerTarget} disabled={!canCapture}
                  aria-invalid={coolerTargetInvalid}
                  onChange={(e) => setCoolerTarget(e.target.value)} />
              </Field>
              <button
                className={`btn tap min-h-[44px] ${cooler?.on ? "btn-accent border-accent" : ""}`}
                aria-pressed={!!cooler?.on}
                disabled={!canCapture || coolerTargetInvalid}
                onClick={() => act(() => api.post("/api/camera/cooler", { on: true, target_c: coolerTargetNum }))}>
                {cooler?.on ? "Set" : "Cool"}
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
