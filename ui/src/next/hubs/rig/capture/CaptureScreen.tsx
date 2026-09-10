// CaptureScreen.tsx - RIG · CAPTURE, the manual bench (plan §F.1-F.8).
//
// "Straight to the camera - one command, one file, no flow." Everything on this
// screen is a PER-SHOT decision; the defaults every flow starts from live in the
// Camera sheet, and the note at the bottom says so, because an override that
// looks like a setting is how a night gets shot at the wrong gain.
//
// WHAT THIS SCREEN REFUSES TO PRETEND:
//
//  * There is NO VIDEO/SER CAPTURE in this backend. `/api/capture`,
//    `/api/capture/loop`, `/api/capture/stop` and `/api/capture/livestack/*` are
//    the whole camera surface; `RigStatus` carries `looping` and
//    `live_stack_active` and nothing video-shaped. The design's VIDEO · PLANETS
//    toggle is drawn and EXPLAINS; its readouts, RECORD, QUICK STACK and
//    DOWNLOAD SER are not built. A control that cannot act is worse than an
//    absence that has been explained.
//  * There is NO ROUTE that promotes an unsaved preview into the library, so
//    the result card's first verb is RE-SHOOT AND SAVE, not SAVE TO GALLERY.
//  * The PLATE-SOLVE-INTO-THE-FILE toggle is `config.solve_saved_lights` and
//    lives in Settings > Optics. This screen LINKS to it (through TargetField's
//    own offer) and does not host a second copy that could disagree.
//  * The mount's own coordinates are not evidence. A `pointing` field source
//    renders as unsolved rather than as a position claim.

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { api } from "../../../../api";
import {
  useStore, useStatus, usePolar, useSequence, useConfig, usePhotometry, usePreview,
  useLastLight, usePreviews, useLivePreview, usePrincipal, useEquipConnected, useWsPhase,
} from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { TargetField } from "../../../../components/capture/TargetField";
import { filterFace, fmtExposure } from "../../../../components/ui/CameraPickers";
import { filterMotion, type FilterCommand } from "../../../../lib/filterSlots";
import { filterSettingsPatch, filterSettingsSummary } from "../../../../lib/filterSettings";
import {
  FRAME_TYPES, FRAME_COACH, darkPrefillFrom, formatLightSummary, shouldOfferDarks,
  type FrameType,
} from "../../../../lib/calibration";
import { suggestSubLength } from "../../../../lib/photometry";
import { ActionButton, Card, Chip, Divider, Label, Mono, Segmented, Switch } from "../../../ui";
import { nav, useRoute } from "../../../router";
import { CaptureStage } from "./CaptureStage";
import { CaptureReadouts } from "./CaptureReadouts";
import { CaptureControls } from "./CaptureControls";
import { CoolerRow } from "./CoolerRow";
import { ResultCard, NO_TARGET_FLOW_REASON } from "./ResultCard";
import { useArm } from "./useArm";
import {
  accessReason, coolerReason, exposeReason, liveViewReason, loopReason, resetStackReason,
  singleReason, slewReason, stopReason, warmReason, POLAR_NOTICE, sequenceNotice,
  VIDEO_LOCK_REASON, NO_LAST_LIGHT_REASON, type CaptureGateInput,
} from "./captureGate";
import type { PreviewInfo } from "../../../../types";

/** The footnote, from the prototype's own `capNote` (still branch), with the
 *  em-dashes rewritten as hyphens and "Gallery > Manual" spelled with a `>`. */
export const CAPTURE_NOTE =
  "Per-shot overrides: gain, offset and binning here apply to this capture only - "
  + "the Camera sheet holds the defaults every flow starts from. Straight to the "
  + "camera - one command, one file, no flow. Tap a readout, drag the dial. The "
  + "cooler gate still applies: a new setpoint settles before the shutter fires. "
  + "Frames land in Gallery > Manual.";

export const SAVE_OFF_NOTE =
  "This frame is a preview only - nothing is written to the library.";

/** The hand-off the Sky finder makes. A NAME alone cannot be slewed to -
 *  `POST /api/mount/goto` takes `ra_hours` and `dec_deg` - so a hand-off that
 *  carried only a name offers USE (adopt the name for the FITS OBJECT card and
 *  the folder) and sends the operator back to SKY to aim. */
export function aimPlan(opts: {
  paramTarget: string | null;
  ra: number | null;
  dec: number | null;
  adopted: string;
}): { kind: "sky" | "use" | "slew"; label: string } {
  const name = opts.paramTarget ?? opts.adopted;
  if (opts.paramTarget && opts.paramTarget !== opts.adopted) {
    return { kind: "use", label: `USE ${opts.paramTarget.toUpperCase()}` };
  }
  if (name && opts.ra != null && opts.dec != null) {
    return { kind: "slew", label: `SLEW → ${name.toUpperCase()}` };
  }
  return { kind: "sky", label: "AIM IN SKY" };
}

export function CaptureScreen(): JSX.Element {
  const route = useRoute();
  const status = useStatus();
  const polar = usePolar();
  const sequence = useSequence();
  const config = useConfig();
  const principal = usePrincipal();
  const equipConnected = useEquipConnected();
  const wsPhase = useWsPhase();
  const photometry = usePhotometry();
  const livePreview = usePreview();
  const previews = usePreviews();
  const liveFrame = useLivePreview();
  const lastLight = useLastLight();

  const showToast = useStore((s) => s.showToast);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const noteLightFrame = useStore((s) => s.noteLightFrame);
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const target = useStore((s) => s.captureTarget);
  const setTarget = useStore((s) => s.setCaptureTarget);
  const settings = useStore((s) => s.frameSettings.capture);

  const onExplain = useCallback((reason: string) => {
    if (reason) enqueueToast({ level: "warning", title: reason });
  }, [enqueueToast]);

  const setCapture = useCallback(
    (patch: Parameters<typeof setFrameSettings>[1]) => setFrameSettings("capture", patch),
    [setFrameSettings],
  );

  // ------------------------------------------------------------ local state
  // COUNT has no home in `FrameSettings` and should not have one: it is a
  // per-invocation argument, not a camera setting.
  const [count, setCount] = useState(1);
  const [save, setSave] = useState(true);
  const [frameType, setFrameType] = useState<FrameType>("Light");
  const [clipEnabled, setClipEnabled] = useState(true);
  const [clipSigma, setClipSigma] = useState(4);
  const [coolerTargetText, setCoolerTargetTextRaw] = useState("-10");
  const coolerEdited = useRef(false);
  const setCoolerTargetText = useCallback((v: string) => {
    coolerEdited.current = true;
    setCoolerTargetTextRaw(v);
  }, []);
  const [filterCmd, setFilterCmd] = useState<FilterCommand | null>(null);
  const [filterNow, setFilterNow] = useState(() => Date.now());
  /** What the last accepted shot WAS, so the result card describes the frame
   *  that landed rather than whatever is on the dials now. */
  const [lastShot, setLastShot] = useState<
    { count: number; exposureS: number; filter: string; gain: number; saved: boolean } | null
  >(null);
  const offeredRef = useRef(false);

  const cam = status?.camera;
  const wheel = status?.filterwheel;
  const looping = !!status?.looping;
  const liveStackOn = !!status?.live_stack_active;
  const wheelName = typeof wheel?.position === "number"
    ? (wheel.names?.[wheel.position] ?? null) : null;
  const face = filterFace(wheelName, settings.filter);
  const shownPreview: PreviewInfo | null =
    liveFrame ?? (previews.length ? previews[previews.length - 1] : null);

  // ------------------------------------------------------------------ gates
  const gate: CaptureGateInput = {
    principal,
    status,
    equipConnected,
    wsPhase,
    polarBusy: polar.state === "running" || polar.state === "paused",
    seqState: sequence.state,
    exposureRaw: String(settings.exposure_s),
    gainRaw: String(settings.gain),
    maxGain: cam?.max_gain ?? null,
    looping,
    pending: null,
  };

  // ------------------------------------------------------------- the shutter
  const bodyRef = useRef<{ save: boolean; count: number }>({ save: true, count: 1 });
  const frameTypeRef = useRef<FrameType>("Light");
  frameTypeRef.current = frameType;

  const arm = useArm({
    commitDrafts: () => { /* the drafts commit themselves on blur and Enter */ },
    onFrameLanded: () => {
      // A Light frame landed via OUR Single/Loop - bank it. Dark/Flat/Bias never
      // feed the "last lights" snapshot the darks nudge and the prefill read.
      if (frameTypeRef.current === "Light") {
        noteLightFrame({
          exposureS: settings.exposure_s,
          gain: settings.gain,
          offset: settings.offset,
          binning: settings.binning,
          tempC: cam?.temperature ?? null,
        });
      }
      setLastShot({
        count: bodyRef.current.count,
        exposureS: settings.exposure_s,
        filter: face.face,
        gain: settings.gain,
        saved: bodyRef.current.save,
      });
    },
  });

  const gateNow: CaptureGateInput = { ...gate, pending: arm.pending };

  /** The POST body, verbatim (CaptureView.tsx:442-450). The guards above have
   *  already refused an exposure <= 0 or unbounded and an out-of-range gain, so
   *  nothing invalid can reach this object. */
  const buildBody = (overrideSave?: boolean) => ({
    exposure_s: settings.exposure_s,
    gain: settings.gain,
    offset: settings.offset,
    binning: settings.binning,
    save: overrideSave ?? save,
    target,
    frame_type: frameType,
  });

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  const fire = (overrideSave?: boolean) => {
    bodyRef.current = { save: overrideSave ?? save, count };
    setLastShot(null);
    void arm.arm("single", "/api/capture", buildBody(overrideSave), settings.exposure_s, count);
  };

  const onLoop = () => {
    // A fresh Light loop is a new batch - clear the once-per-batch darks-nudge
    // guard so Stop can offer again for THIS batch.
    if (frameType === "Light") offeredRef.current = false;
    bodyRef.current = { save, count: 1 };
    void arm.arm("loop", "/api/capture/loop", buildBody(), settings.exposure_s);
  };

  const onLiveView = () => {
    if (liveStackOn) {
      // Retire the bar here, the way Stop does: this tap ends the stack, so
      // letting the lane effect discover it would narrate the user's own press
      // back at them as "stopped on the rig".
      arm.drop();
      void act(() => api.post("/api/capture/livestack/stop"));
      return;
    }
    bodyRef.current = { save, count: 1 };
    void arm.arm("live", "/api/capture/livestack/start", {
      ...buildBody(), frame_type: "Light",
      ...(clipEnabled ? { clip_sigma: clipSigma } : {}),
    }, settings.exposure_s);
  };

  const onStop = () => {
    const wasLightLoop = looping && frameTypeRef.current === "Light";
    arm.drop();
    if (wasLightLoop && shouldOfferDarks(lastLight) && !offeredRef.current) {
      offeredRef.current = true;
      void confirmDialog({
        title: "Take matching darks?",
        body: `You shot ${formatLightSummary(lastLight)} - shoot matching darks now?`,
        confirmLabel: "Set up darks",
        cancelLabel: "Not now",
        confirmPrimary: true,
      }).then((ok) => {
        if (!ok) return;
        const p = darkPrefillFrom(lastLight);
        setFrameType("Dark");
        setCapture({
          exposure_s: Number(p.exposure), gain: Number(p.gain),
          offset: Number(p.offset), binning: Number(p.binning),
        });
        if (p.coolerTarget) setCoolerTargetText(p.coolerTarget);
        showToast("info", "Darks set up - cap the scope, then press CAPTURE or LOOP.");
      });
    }
    void act(() => api.post("/api/capture/stop"));
  };

  // --------------------------------------------------------- the filter wheel
  const moveFilterTo = useCallback((slot: number) => {
    const w = status?.filterwheel;
    if (!w) return;
    // #215: this filter's own saved settings become the screen's AT THE MOMENT
    // OF THE PICK, which is the only moment the operator is looking - and it is
    // ANNOUNCED, because a control that changes two things you did not touch is
    // indistinguishable from a bug until you find it in a header.
    const pins = filterSettingsPatch(w, slot);
    if (Object.keys(pins).length) {
      setCapture(pins);
      showToast("info",
        `${w.names?.[slot] || `slot ${slot + 1}`}: `
        + `${filterSettingsSummary(w, slot)} (its saved settings)`);
    }
    setFilterCmd({ slot, startedAt: Date.now(), from: w.position });
    setFilterNow(Date.now());
    void act(async () => {
      try {
        await api.post("/api/filterwheel/position", { position: slot });
      } catch (e) {
        // The command never reached the wheel, so there is no move to narrate.
        setFilterCmd(null);
        throw e;
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, setCapture, showToast]);

  const fwMotion = filterMotion(
    filterCmd, wheel?.position, wheel?.moving, wheel?.names ?? [], filterNow);
  const fwWaiting = !!filterCmd && !fwMotion.problem && wheel?.position !== filterCmd.slot;
  useEffect(() => {
    if (!fwWaiting) return;
    const t = setInterval(() => setFilterNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [fwWaiting]);
  useEffect(() => {
    if (filterCmd && wheel?.position === filterCmd.slot && !wheel?.moving) setFilterCmd(null);
  }, [filterCmd, wheel?.position, wheel?.moving]);

  // ------------------------------------------------------------- sub-length
  const camEgain = cam?.egain;
  const usingCameraEgain = photometry.egain <= 0 && !!camEgain && camEgain > 0;
  const effectiveEgain = usingCameraEgain ? camEgain! : photometry.egain;
  const linearMedian = livePreview && livePreview.data_is_linear
    ? livePreview.stats.median : null;
  const suggestion = useMemo(() => {
    if (!(effectiveEgain > 0) || !(photometry.readNoiseE > 0) || linearMedian == null
      || !livePreview) return null;
    return suggestSubLength({
      medianAdu: linearMedian, biasAdu: photometry.biasAdu, egain: effectiveEgain,
      readNoiseE: photometry.readNoiseE, exposureS: livePreview.exposure_s,
    });
  }, [effectiveEgain, photometry.readNoiseE, photometry.biasAdu, linearMedian, livePreview]);
  const suggestReason = suggestion
    ? null
    : !(effectiveEgain > 0) || !(photometry.readNoiseE > 0)
      ? "Add camera gain and read noise in the Camera sheet to enable Suggest"
      : "Take a light frame first - Suggest needs a linear preview";
  const onSuggest = () => {
    if (!suggestion) return;
    if (!suggestion.ok || suggestion.suggestedS == null) {
      showToast("warning", suggestion.reason); return;
    }
    setCapture({ exposure_s: suggestion.suggestedS });
    showToast("success", `Suggested ${suggestion.suggestedS}s - ${suggestion.reason}`);
  };

  // ------------------------------------------------------------- the aim row
  const paramTarget = route.params.target || null;
  const ra = route.params.ra != null ? Number(route.params.ra) : null;
  const dec = route.params.dec != null ? Number(route.params.dec) : null;
  const runOwnsMount = sequence.state === "running" || sequence.state === "paused";
  const plan = aimPlan({
    paramTarget,
    ra: ra != null && Number.isFinite(ra) ? ra : null,
    dec: dec != null && Number.isFinite(dec) ? dec : null,
    adopted: target,
  });
  // Three different verbs, three different gates: navigating to SKY needs
  // nothing, adopting a NAME is a capture setting, and a SLEW is the mount.
  const aimReason = plan.kind === "sky" ? null
    : plan.kind === "use" ? accessReason(gateNow)
      : slewReason(gateNow, { runOwnsMount });
  const onAim = () => {
    if (plan.kind === "sky") { nav.hub("sky"); return; }
    if (plan.kind === "use") {
      if (paramTarget) {
        setTarget(paramTarget);
        showToast("info", `${paramTarget} named for this capture - it rides in the FITS OBJECT card.`);
      }
      return;
    }
    const name = paramTarget ?? target;
    void act(async () => {
      await api.post("/api/mount/goto", { ra_hours: ra, dec_deg: dec, center: true });
      setTarget(name);
      showToast("success", `Slewing to ${name} - solve and centre.`);
    });
  };

  // --------------------------------------------------------------- the gates
  const readOnly = accessReason(gateNow);
  const notices: string[] = [];
  if (gateNow.polarBusy) notices.push(POLAR_NOTICE);
  const seqNote = sequenceNotice(sequence.state);
  if (seqNote) notices.push(seqNote);
  if (fwMotion.problem) notices.push(fwMotion.problem);

  const resultActionReason = exposeReason(gateNow);
  const flowReason = resultActionReason ?? (target ? null : NO_TARGET_FLOW_REASON);

  return (
    <div
      data-testid="rig-capture"
      style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px" }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10,
      }}>
        <Label size={11}>MANUAL CAPTURE</Label>
        <Mono size={10} tone="dim">
          {arm.inFlight ? "exposing..."
            : target ? `no flow · on ${target}`
              : "no flow · straight to the camera"}
        </Mono>
      </div>

      <div role="group" aria-label="Capture mode" style={{ display: "flex", gap: 6 }}>
        <div style={{ flex: 1, display: "flex" }}>
          <Chip active onClick={() => { /* already here */ }} className="nx-cap-mode">
            STILL · FRAMES
          </Chip>
        </div>
        <div style={{ flex: 1, display: "flex" }}>
          <Chip
            active={false}
            onClick={() => { /* refused - see lockedReason */ }}
            lockedReason={VIDEO_LOCK_REASON}
            onExplain={onExplain}
            data-testid="capture-video"
          >
            VIDEO · PLANETS
          </Chip>
        </div>
      </div>

      <CaptureStage
        exposureS={settings.exposure_s}
        gain={settings.gain}
        binning={settings.binning}
        filterFace={face.face}
        target={target}
        looping={looping}
        exposing={arm.phase === "exposing"}
        downloading={arm.phase === "downloading"}
        fillPct={arm.fillPct}
        elapsedS={arm.elapsed}
        aim={{ label: plan.label, onPress: onAim, lockedReason: aimReason }}
        onExplain={onExplain}
      />

      <CaptureReadouts
        status={status}
        count={count}
        setCount={setCount}
        moveFilterTo={moveFilterTo}
        filterNote={fwMotion.problem ?? (fwMotion.pulsing ? fwMotion.summary : null)}
        lockedReason={readOnly}
        onExplain={onExplain}
        onOpenWheel={() => nav.sheet("wheel")}
        skyLimitedS={suggestion?.skyLimitedS ?? null}
        onSuggest={onSuggest}
        suggestReason={suggestReason}
      />

      <Divider />

      <Segmented<FrameType>
        label="Frame type"
        options={FRAME_TYPES.map((t) => ({ value: t, label: t.toUpperCase() }))}
        value={frameType}
        onChange={setFrameType}
        lockedReason={readOnly}
        onExplain={onExplain}
        data-testid="capture-frametype"
      />
      <Mono size={10} tone="dim">{FRAME_COACH[frameType]}</Mono>
      {(frameType === "Dark" || frameType === "Bias") && (
        <ActionButton
          kind="ghost"
          onPress={() => {
            // The lock below already refuses this press without a snapshot; the
            // guard is here too because a type predicate is not a promise.
            if (!shouldOfferDarks(lastLight)) return;
            const p = darkPrefillFrom(lastLight);
            setCapture({
              exposure_s: Number(p.exposure), gain: Number(p.gain),
              offset: Number(p.offset), binning: Number(p.binning),
            });
            if (p.coolerTarget) setCoolerTargetText(p.coolerTarget);
          }}
          lockedReason={readOnly ?? (shouldOfferDarks(lastLight) ? null : NO_LAST_LIGHT_REASON)}
          onExplain={onExplain}
          data-testid="capture-match-lights"
        >
          MATCH LAST LIGHTS
        </ActionButton>
      )}

      <Switch
        checked={save}
        onChange={setSave}
        label="SAVE FITS TO LIBRARY"
        note={save ? undefined : SAVE_OFF_NOTE}
        lockedReason={readOnly}
        onExplain={onExplain}
        data-testid="capture-save"
      />

      <TargetField
        value={target}
        onChange={setTarget}
        field={shownPreview?.field ?? null}
        perFrameSolving={!!config?.solve_saved_lights}
        frameType={frameType}
        readOnly={!!readOnly}
        readOnlyReason={readOnly}
        onOpenSolveSettings={() => nav.go("/settings/general/optics")}
      />

      <CaptureControls
        count={count}
        exposureS={settings.exposure_s}
        filter={face.face}
        looping={looping}
        liveStackOn={liveStackOn}
        inFlight={arm.inFlight}
        pending={arm.pending}
        batch={arm.batch}
        remainingS={arm.remaining}
        singleReason={singleReason(gateNow)}
        loopReason={loopReason(gateNow)}
        liveReason={liveViewReason(gateNow, liveStackOn)}
        stopReason={stopReason(gateNow)}
        resetReason={resetStackReason(gateNow, liveStackOn)}
        onCapture={() => fire()}
        onLoop={onLoop}
        onLiveView={onLiveView}
        onStop={onStop}
        onResetStack={() => void act(() => api.post("/api/capture/livestack/reset"))}
        clipEnabled={clipEnabled}
        setClipEnabled={setClipEnabled}
        clipSigma={clipSigma}
        setClipSigma={setClipSigma}
        preview={shownPreview}
        notices={notices}
        onExplain={onExplain}
      />

      {lastShot && !arm.inFlight && (
        <ResultCard
          preview={shownPreview}
          saved={lastShot.saved}
          count={lastShot.count}
          exposureS={lastShot.exposureS}
          filter={lastShot.filter}
          gain={lastShot.gain}
          onReshootAndSave={() => fire(true)}
          onOpenLibrary={() => nav.sheet("files", { src: "manual" })}
          onRetake={() => fire()}
          onMakeFlow={() => nav.go(
            `/sky/quickSession?filter=${encodeURIComponent(face.face)}`
            + `&exp=${lastShot.exposureS}&hours=1`
            + (target ? `&target=${encodeURIComponent(target)}` : ""),
          )}
          actionReason={resultActionReason}
          flowReason={flowReason}
          onExplain={onExplain}
        />
      )}

      <Divider />

      <CoolerRow
        status={status}
        coolReason={coolerReason(gateNow, coolerTargetText)}
        warmReason={warmReason(gateNow, {
          rampActive: !!cam?.warm?.active,
          coolerPresent: cam?.cooler != null,
          coolerOn: !!cam?.cooler?.on,
        })}
        accessReason={readOnly}
        onExplain={onExplain}
        onCooler={(b) => void act(() => api.post("/api/camera/cooler", b))}
        onDew={(power) => void act(() => api.post("/api/camera/dew-heater", { power }))}
        targetText={coolerTargetText}
        setTargetText={setCoolerTargetText}
        edited={coolerEdited.current}
      />

      <Card tone="dashed">
        <Mono size={11} tone="dim">{CAPTURE_NOTE}</Mono>
      </Card>
      <Mono size={10} tone="dim">
        {`Next frame: ${count} × ${fmtExposure(settings.exposure_s)} · ${face.face}`}
      </Mono>
    </div>
  );
}
