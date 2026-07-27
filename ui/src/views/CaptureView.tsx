import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import {
  useStore, useStatus, usePolar, useLivePreviewId, useSequence, useLastLight,
  usePhotometry, usePreview, useEgainLearn,
} from "../store";
import { LivePreview } from "../components/preview/LivePreview";
import GuideFramePreview from "../components/GuideFramePreview";
import {
  Field, Led, LockedChip, LockedNote, Panel, SegmentedControl, Stat, Toggle,
} from "../components/ui";
import { accessPhrase, useCanControlCapture } from "../lib/caps";
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
  // UX #5: this defaulted to OFF, so a beginner could press FIRST LIGHT then
  // LOOP, watch pictures appear all night, and find nothing on disk in the
  // morning — the switch carried no help text, no (i), and no mention in the
  // setup checklist. The novice-safe default is to KEEP what you shot; the
  // expert case (framing/test frames you don't want to keep) is one tap away
  // and is now the thing that's explained, rather than the other way round.
  const [save, setSave] = useState(true);
  const [target, setTarget] = useState("");
  const [coolerTarget, setCoolerTarget] = useState("-10");
  const [dew, setDew] = useState(0);
  const [filterEditOpen, setFilterEditOpen] = useState(false); // UX-05 slot-name modal
  const [camAdvanced, setCamAdvanced] = useState(false); // Advanced disclosure (egain)
  const egainLearn = useEgainLearn();
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
  const liveStackOn = !!status?.live_stack_active; // NOV-1: server truth (survives reload)
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
  // NOV-1 Live View: toggle arms the server-side stacker + starts the loop;
  // toggling off disarms + stops. Reset clears the accumulator, keeps arming.
  const onLiveView = () => {
    if (captureBlocked || !canCapture || exposureInvalid || gainInvalid) return;
    if (liveStackOn) { act(() => api.post("/api/capture/livestack/stop")); return; }
    beginExposure(exposureS);
    act(() => api.post("/api/capture/livestack/start", { ...body, frame_type: "Light" }));
  };
  const onResetStack = () => { if (canCapture && liveStackOn) act(() => api.post("/api/capture/livestack/reset")); };

  const inFlight = phase !== "idle";
  const fillPct = phase === "exposing"
    ? Math.min(100, (elapsed / expLenRef.current) * 100)
    : 100;
  const remaining = Math.max(0, expLenRef.current - elapsed);

  // ------------------------------------------------- UX #24: stated reasons
  // A blocked control has to say WHY on a channel that survives a fingertip.
  // `title=` does not fire on touch, and the native `disabled` attribute takes
  // the control AND its reason out of the accessibility tree — so every gate on
  // this screen resolves to a REASON STRING here and is rendered through the
  // house `LockedChip` (dim + lock glyph + aria-disabled + aria-label + a
  // tap-reachable tooltip) instead of `disabled`. A null reason means "usable".
  const readOnlyReason = canCapture
    ? null
    : `Read-only session — ${accessPhrase("control.capture")} required`;
  const settingsReason =
    exposureInvalid ? "Fix the exposure above first"
      : gainInvalid ? "Fix the gain above first"
        : null;
  // Shared by every control that starts an exposure.
  const exposeReason =
    readOnlyReason
    ?? (polarBusy ? "Polar alignment owns the camera right now"
      : seqOwnsCamera ? "A sequence owns the camera — stop it first"
        : settingsReason);
  const singleReason =
    exposeReason ?? (looping ? "A capture loop is running — press Stop first" : null);
  const loopReason = exposeReason;
  const coolReason =
    readOnlyReason ?? (coolerTargetInvalid
      ? `Target must be a number between ${COOLER_MIN_C} and ${COOLER_MAX_C} °C`
      : null);
  const warmReason =
    readOnlyReason ?? (cooler != null && !cooler.on ? "The cooler is already off" : null);

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_320px] xl:grid-cols-[1fr_360px]">
      {/* live-preview overhaul: stage + zoom/pan + stretch + overlays + filmstrip */}
      <LivePreview />

      <div className="flex flex-col gap-4">
        {/* ------------------------------------------------- exposure ctl */}
        <Panel title="Exposure" right={!canCapture && <ReadOnlyBadge />}>
          {/* UX #24: the header pill says "VIEW ONLY" but keeps the WHY in a
              `title=`, which a fingertip never fires. State it as text. */}
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mb-3" />}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Exposure (s)" hint={HELP.exposure}>
              {/* `readOnly`, not `disabled` (§11.8): it blocks the edit exactly as
                  hard, but keeps the field focusable and announced. */}
              <input
                className={`field ${exposureInvalid ? "border-bad" : ""}`}
                value={exposure}
                readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
                aria-invalid={exposureInvalid}
                onChange={(e) => setExposure(e.target.value)}
              />
              {exposureInvalid && (
                <p className="text-[11px] text-bad mt-1">Exposure must be 0–3600s</p>
              )}
            </Field>
            <Field label={`Gain${cam?.max_gain ? ` (max ${cam.max_gain})` : ""}`} hint={HELP.gain}>
              <input
                className={`field ${gainInvalid ? "border-bad" : ""}`}
                value={gain}
                readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
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
              <input className="field" value={offset} readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
                onChange={(e) => setOffset(e.target.value)} />
            </Field>
            <Field label="Binning" hint={HELP.binning}>
              {/* A <select> has no `readOnly`, so the locked state swaps in the
                  house locked stand-in showing the current value rather than a
                  native `disabled` select the a11y tree cannot explain. */}
              {readOnlyReason ? (
                <LockedChip reason={readOnlyReason} className="btn w-full">
                  {binning}×{binning}
                </LockedChip>
              ) : (
                <select className="field" value={binning} onChange={(e) => setBinning(e.target.value)}>
                  {binOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
                </select>
              )}
            </Field>
          </div>

          {/* ---- NOV-4 beginner capture presets (photometry/SNR design §3 Task 4):
               one-tap exposure/gain/offset/binning fill via the same setter path as
               "Match last lights" below. Pure data from lib/capturePresets. ---- */}
          <div className="flex flex-wrap gap-2 mt-3">
            {CAPTURE_PRESETS.map((p) => (readOnlyReason ? (
              <LockedChip key={p.id} reason={`${p.blurb} — ${readOnlyReason}`}
                className="btn tap min-h-[44px] !px-3">
                {p.label}
              </LockedChip>
            ) : (
              <button
                key={p.id}
                className="btn tap min-h-[44px] !px-3"
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
            )))}
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
                    {/* Was `disabled title="From camera"` — the reason lived only in
                        a tooltip that never fires on touch, AND the field was gone
                        from the a11y tree. `readOnly` keeps both. */}
                    <input className="field" value={camEgain!.toFixed(3)}
                      readOnly aria-readonly
                      aria-label="Gain in electrons per ADU — reported by the camera, not editable" />
                  </div>
                ) : (
                  <input className="field" inputMode="decimal"
                    value={photometryProfile.egain || ""}
                    readOnly={!canCapture}
                    aria-readonly={!canCapture || undefined}
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
                  readOnly={!canCapture}
                  aria-readonly={!canCapture || undefined}
                  placeholder="2.0"
                  onChange={(e) => setPhotometry({ readNoiseE: Number(e.target.value) || 0 })} />
              </Field>
              <Field label="Bias (ADU)"
                hint="Median of a Bias frame (offset pedestal). Defaults to 0 — slightly overestimates sky, which is safe.">
                <input className="field" inputMode="decimal"
                  value={photometryProfile.biasAdu || ""}
                  readOnly={!canCapture}
                  aria-readonly={!canCapture || undefined}
                  placeholder="0"
                  onChange={(e) => setPhotometry({ biasAdu: Number(e.target.value) || 0 })} />
              </Field>
            </div>
            {canSuggest ? (
              <button className="btn tap min-h-[44px] mt-3" onClick={onSuggest}>
                Suggest settings
              </button>
            ) : (
              // UX #24: was a hand-dimmed span whose reason lived only in `title=`.
              // LockedChip carries the same words in aria-label AND in a tooltip
              // with a TAP path, which is the only channel a tablet has.
              <LockedChip
                className="btn tap min-h-[44px] mt-3"
                reason={
                  effectiveEgain <= 0 || photometryProfile.readNoiseE <= 0
                    ? "Add camera gain + read noise above to enable Suggest"
                    : "Take a light frame first — Suggest needs a linear preview"
                }>
                Suggest settings
              </LockedChip>
            )}
          </div>

          {/* UX #5: the switch is now ON by default AND says what each state
              means — the state is a WORD (showState) as well as a position, so
              it survives the night palette, and the consequence is stated in
              prose instead of being left to be discovered the next morning. */}
          <div className="mt-3 border-t border-line pt-3">
            <div className="flex items-center gap-3">
              {/* `Toggle` uses the native `disabled` attribute internally, so the
                  locked state swaps the switch for the house locked stand-in
                  rather than a dead switch with no announced reason (UX #24). */}
              {readOnlyReason ? (
                <LockedChip reason={readOnlyReason} className="text-xs">
                  Save FITS to library · {save ? "ON" : "OFF"}
                </LockedChip>
              ) : (
                <>
                  <Toggle checked={save} onChange={setSave}
                    label="Save FITS to library" showState />
                  <span className="text-xs text-dim">save FITS to library</span>
                </>
              )}
            </div>
            <p className="text-[11px] text-dim mt-1.5 leading-snug">
              {save
                ? "Every frame you shoot here is written to the library on disk. Turn this off for framing and test shots you don't want to keep."
                : "Frames are shown on screen only — nothing is written to disk. Turn this on before a session you want to keep."}
            </p>
          </div>
          {save && (
            <div className="mt-2">
              <Field label="Target name"
                hint="Names the folder and the files on disk. Leave it blank and the frames still save, just without a target name.">
                <input className="field" placeholder="M42" value={target}
                  readOnly={!canCapture} aria-readonly={!canCapture || undefined}
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
            {/* SegmentedControl disables its own buttons internally; the reason is
                ours to state, and it has to be TEXT (UX #24). */}
            {readOnlyReason && <LockedNote reason={readOnlyReason} className="mt-2" />}
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
                // UX #24: the reason used to be `title=` only. LockedChip states it
                // in aria-label and in a tooltip a tap can open.
                <LockedChip reason="Shoot some lights first — there is nothing to match yet"
                  className="btn tap min-h-[44px] mt-2">
                  Match last lights
                </LockedChip>
              )
            )}
          </div>

          {/* ---- capture buttons. Single/Loop show an active state in flight and are
               BLOCKED during polar alignment; Stop flashes pressed + stays 1-tap. ---- */}
          <div className="grid grid-cols-3 gap-2 mt-4">
            {/* UX #24: Single/Loop collapsed FIVE separate blocking conditions into
                one native `disabled`, which greys the button and says nothing — and
                on a tablet there is no hover with which to ask. Each condition now
                resolves to a sentence.
                The IN-FLIGHT states are deliberately NOT locked stand-ins: their
                label ("Exposing…", "Looping…") already IS the reason, and a lock
                glyph on a running exposure would read as "blocked", the opposite of
                the truth. They keep the accented running chrome, drop the native
                `disabled`, and carry aria-disabled + a spoken reason. */}
            {inFlight && !looping ? (
              <button
                className="btn btn-accent border-accent tap-lg min-h-[56px]"
                aria-disabled aria-pressed
                aria-label={`${phase === "downloading" ? "Reading out" : "Exposing"} — a frame is already in progress`}>
                {phase === "downloading" ? "Reading…" : "Exposing…"}
              </button>
            ) : singleReason ? (
              <LockedChip reason={singleReason} className="btn tap-lg min-h-[56px] justify-center">
                Single
              </LockedChip>
            ) : (
              <button className="btn btn-accent tap-lg min-h-[56px]" onClick={onSingle}>
                Single
              </button>
            )}
            {looping ? (
              <button
                className="btn btn-accent border-accent tap-lg min-h-[56px]"
                aria-disabled aria-pressed
                aria-label="Looping — the capture loop is already running; press Stop to end it">
                Looping…
              </button>
            ) : loopReason ? (
              <LockedChip reason={loopReason} className="btn tap-lg min-h-[56px] justify-center">
                Loop
              </LockedChip>
            ) : (
              <button className="btn tap-lg min-h-[56px]" onClick={onLoop}>
                Loop
              </button>
            )}
            {/* Stop is urgent -> stays 1-tap (R9), enlarged for touch. Disabled for
                viewers (no capture to stop — they can't have started one).

                UX #14 / S3. STOP and LOOP were the same 90×56 ghost box with the
                same fill and borders 0.05 alpha apart; in night mode their text
                colours measure ~1.26:1 of each other, so the abort control was
                separated from the control next to it by HUE ALONE. Three
                NON-HUE channels now carry it, so it is findable by silhouette:
                  · GLYPH  — the filled square, the same shape `.led-bad` uses
                             for "bad" so the vocabulary is already learned
                  · WEIGHT — a 2px border where every neighbour is 1px
                  · FILL   — the only FILLED box in the row
                The fill is deliberately a capped-luminance wash rather than a
                bright solid: Mount's full-width solid STOP is the object the
                novice measured as the brightest thing on a dark-adapted screen,
                which is the same failure from the other side. */}
            {readOnlyReason ? (
              <LockedChip reason={readOnlyReason}
                className="btn btn-danger tap-lg min-h-[56px] !border-2 justify-center">
                Stop
              </LockedChip>
            ) : (
              <button
                className={`btn btn-danger tap-lg min-h-[56px] !border-2 inline-flex items-center justify-center gap-1.5 ${stopPressed ? "scale-95 brightness-110" : ""}`}
                style={{ background: "color-mix(in srgb, var(--danger-ink) 15%, transparent)" }}
                aria-pressed={stopPressed}
                onClick={onStop}>
                {/* fill-current makes it a SOLID square — `.led-bad`'s shape,
                    which is the app's existing "this one is the bad one" mark. */}
                <Icon name="stop" size={13} className="shrink-0 fill-current" aria-hidden />
                Stop
              </button>
            )}
          </div>

          {/* ---- Live View (NOV-1): server-side running-mean EAA stack. The toggle
               reflects server truth (status.live_stack_active), so a reload mid-stack
               stays lit. Reset is honest-disabled (§11.8) until armed. ---- */}
          <div className="grid grid-cols-2 gap-2 mt-2">
            {exposeReason && !liveStackOn ? (
              <LockedChip reason={exposeReason} className="btn tap min-h-[44px] justify-center">
                Live View
              </LockedChip>
            ) : (
              <button
                className={`btn tap min-h-[44px] ${liveStackOn ? "btn-accent border-accent" : ""}`}
                aria-pressed={liveStackOn}
                title="Stack subs into one continuously brightening image"
                onClick={onLiveView}>
                {liveStackOn ? "Live View · on" : "Live View"}
              </button>
            )}
            {liveStackOn ? (
              <button className="btn tap min-h-[44px]" onClick={onResetStack}>Reset stack</button>
            ) : (
              // UX #24: the reason was `title=` only — invisible to a fingertip.
              <LockedChip reason="Start Live View first — there is no stack to reset yet"
                className="btn tap min-h-[44px]">
                Reset stack
              </LockedChip>
            )}
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
              {status.filterwheel.names.map((name, i) => (readOnlyReason ? (
                <LockedChip key={`${i}-${name}`} reason={`Move to ${name} — ${readOnlyReason}`}
                  className={`btn tap min-h-[44px] !px-3 min-w-[56px] ${i === status.filterwheel!.position ? "btn-accent" : ""}`}>
                  {name}
                </LockedChip>
              ) : (
                <button key={`${i}-${name}`}
                  className={`btn tap min-h-[44px] !px-3 min-w-[56px] ${i === status.filterwheel!.position ? "btn-accent" : ""}`}
                  onClick={() => act(() => api.post("/api/filterwheel/position", { position: i }))}>
                  {name}
                </button>
              )))}
            </div>
          </Panel>
        )}
        {status?.filterwheel && (
          <FilterNamesModal
            open={filterEditOpen}
            onClose={() => setFilterEditOpen(false)}
            names={status.filterwheel.names}
            offsets={status.filterwheel.offsets ?? []}
            position={status.filterwheel.position}
            canLearn={!!status.focuser}
            learnDisabledReason={
              !canCapture ? "this is a read-only session"
                : captureBlocked ? "a sequence or polar alignment owns the camera"
                  : looping ? "a capture loop is running"
                    : !status.focuser ? "no focuser is connected"
                      : null
            }
            onLearn={async (refSlot) => {
              await api.post("/api/filterwheel/learn-offsets", { ref_slot: refSlot });
              showToast("info", "Learning filter offsets…");
            }}
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

            {/* Phone-feedback fix: readouts + set-point on their own line, the two
                actions on a DELIBERATE two-up grid underneath. These five controls
                measure ~409px of content (54+60+91+67+89 plus four 12px gaps) and
                the panel is only 324px wide at 390 and 346 at 412 — so a single
                `flex-wrap` row could never hold them and always spat WARM out onto
                a line of its own, hard left, orphaned from COOL. It is not a
                shrink-the-buttons problem: 409 > 346 at every phone width, and the
                44px floor is not negotiable. Two rows is the honest layout, and
                pairing the buttons in a grid means they move together or not at
                all — flex can never orphan one again. */}
            <div className="flex items-end flex-wrap gap-3">
              <Stat label="sensor" value={cam.temperature?.toFixed(1) ?? "—"} unit="°C"
                tone={cam.temperature != null && cam.temperature < 0 ? "good" : undefined} />
              {cooler?.target_c != null && (
                <Stat label="target" value={cooler.target_c.toFixed(1)} unit="°C" />
              )}
              <Field label="Target °C" hint={HELP.coolTo}>
                <input className={`field !w-20 ${coolerTargetInvalid ? "border-bad" : ""}`}
                  value={coolerTarget}
                  readOnly={!canCapture} aria-readonly={!canCapture || undefined}
                  aria-invalid={coolerTargetInvalid}
                  onChange={(e) => setCoolerTarget(e.target.value)} />
              </Field>
            </div>
            {/* UX #24 — WARM was the finding's named example: natively disabled
                whenever the cooler is already off, with no aria-label, no title
                and no visible note. On the tablet it was a dim, dead, silent
                box. Both cooler buttons still state their blocker. */}
            <div className="grid grid-cols-2 gap-3 mt-3">
              {coolReason ? (
                <LockedChip reason={coolReason} className="btn tap min-h-[44px] w-full justify-center">
                  {cooler?.on ? "Set" : "Cool"}
                </LockedChip>
              ) : (
                <button
                  className={`btn tap min-h-[44px] w-full ${cooler?.on ? "btn-accent border-accent" : ""}`}
                  aria-pressed={!!cooler?.on}
                  onClick={() => act(() => api.post("/api/camera/cooler", { on: true, target_c: coolerTargetNum }))}>
                  {cooler?.on ? "Set" : "Cool"}
                </button>
              )}
              {warmReason ? (
                <LockedChip reason={warmReason} className="btn tap min-h-[44px] w-full justify-center">
                  Warm
                </LockedChip>
              ) : (
                <button
                  className="btn tap min-h-[44px] w-full"
                  onClick={() => act(() => api.post("/api/camera/cooler", { on: false }))}>
                  Warm
                </button>
              )}
            </div>
            {coolerTargetInvalid && (
              <p className="text-[11px] text-bad mt-2">
                Target must be a number between {COOLER_MIN_C} and {COOLER_MAX_C} °C
              </p>
            )}

            {cam.has_dew_heater && (
              <div className="mt-4 border-t border-line pt-3">
                <div className="flex justify-between mb-1.5">
                  <span className="label">dew heater</span>
                  <span className="mono text-xs text-accent">{dew}%</span>
                </div>
                {/* A range input has no `readOnly`, and native `disabled` would
                    drop it (and its reason) out of the a11y tree. Keep it
                    reachable, announce it as disabled, inert its handlers, and
                    state the reason underneath (UX #24). */}
                <input type="range" min={0} max={100} value={dew}
                  aria-label="dew heater power"
                  aria-disabled={!canCapture || undefined}
                  className={`w-full h-11 accent-(--accent) touch-none ${canCapture ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                  onChange={(e) => { if (canCapture) setDew(Number(e.target.value)); }}
                  onMouseUp={() => { if (canCapture) act(() => api.post("/api/camera/dew-heater", { power: dew })); }}
                  onTouchEnd={() => { if (canCapture) act(() => api.post("/api/camera/dew-heater", { power: dew })); }} />
                {readOnlyReason && <LockedNote reason={readOnlyReason} className="mt-1" />}
              </div>
            )}
          </Panel>
        )}

        {/* ------------------------------------- Sensor gain (e-/ADU + learn)
             WAS titled "Camera" and rendered NOTHING but an "▸ Advanced" button
             when collapsed — a titled container with no content, which is worse
             than no container: it claims a whole panel of a novice's screen and
             answers no question. Renamed to what it actually holds, and the ONE
             value it exists for (e-/ADU + where that number came from) is now
             stated inline in the collapsed state.

             It is NOT folded into Exposure (the reviewer's other option) on
             purpose: measuring gain is an expert path for Alpaca/NINA rigs whose
             driver reports nothing, and hoisting it into the primary capture
             form would regress a novice-safe default into an advanced one
             (progressive-disclosure rule). The value is now visible with zero
             configuration; only the measurement flow stays behind the disclosure.
             Honest-disabled (§11.8): dimmed + aria-disabled + a STATED reason. */}
        {cam && (() => {
          const measuring = egainLearn?.state === "running";
          const reason = !canCapture
            ? "this is a read-only session"
            : captureBlocked
              ? "a sequence or polar alignment owns the camera"
              : looping
                ? "a capture loop is running"
                : measuring
                  ? "a measurement is already running"
                  : null;
          const learned = cam.egain_learned ?? {};
          const gainKey = String(Math.round(Number(gain) || 0));
          const learnedHere = learned[gainKey];
          const shown = cam.egain || learnedHere;
          // Provenance is a WORD, never a colour or a bare number (house rule:
          // status is never colour-only) — and "not measured yet" is stated
          // rather than left as a lone em dash.
          const provenance = cam.egain
            ? "from driver"
            : learnedHere
              ? `measured at gain ${gainKey}`
              : "not known yet — measure it below";
          return (
            <Panel title="Sensor gain" right={!canCapture && <ReadOnlyBadge />}>
              {/* Collapsed-state content: the value + its provenance, inline. */}
              <div className="flex flex-wrap gap-3 items-end">
                <Stat label="e-/ADU" value={shown ? shown.toFixed(3) : "—"} />
                <span className="text-[11px] text-dim mb-0.5 inline-flex items-center gap-1.5">
                  <Icon name="info" size={11} className="shrink-0" aria-hidden />
                  {provenance}
                </span>
              </div>
              <p className="text-[11px] text-dim leading-snug mt-2">
                How many electrons one ADU is worth. AstroDeck uses it for the
                noise and SNR readouts; most drivers report it and there is
                nothing to do here.
              </p>
              <button
                className="btn !px-2 !py-1 text-[11px] mt-3"
                aria-expanded={camAdvanced}
                onClick={() => setCamAdvanced((v) => !v)}>
                {camAdvanced ? "▾ Advanced" : "▸ Advanced"}
              </button>
              {camAdvanced && (
                <div className="mt-3 flex flex-col gap-2">
                  <p className="text-[11px] text-dim leading-snug">
                    Measures your camera's true gain (e-/ADU) from a few flat and
                    dark frames — for rigs whose driver reports nothing. Point at
                    an evenly lit surface first; takes about a minute.
                  </p>
                  <button
                    className={`btn tap min-h-[44px] self-start ${reason ? "opacity-50 cursor-default" : ""}`}
                    aria-disabled={reason ? true : undefined}
                    onClick={reason ? undefined : () => act(async () => {
                      await api.post("/api/camera/egain/learn", { gain: Number(gainKey) });
                      showToast("info", `Measuring gain at ${gainKey}…`);
                    })}>
                    {measuring
                      ? `Measuring… ${egainLearn?.step ?? 0}/${egainLearn?.of ?? 0}`
                      : `Measure gain (e-/ADU) at gain ${gainKey}`}
                  </button>
                  {/* The reason as visible text, not title= (never fires on touch). */}
                  {reason && (
                    <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
                      <Icon name="lock" size={11} aria-hidden /> Unavailable — {reason}.
                    </p>
                  )}
                  {Object.keys(learned).length > 0 && (
                    <p className="mono text-[11px] text-dim">
                      measured: {Object.entries(learned)
                        .map(([g, v]) => `g${g}=${Number(v).toFixed(2)}`).join("  ")}
                    </p>
                  )}
                  <p className="text-[10px] text-dim">
                    A gain reported by the driver always overrides a measured one.
                  </p>
                </div>
              )}
            </Panel>
          );
        })()}
      </div>
    </div>
  );
}
