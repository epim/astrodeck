import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "../components/icons";
import {
  useStore,
  useStatus,
  useFocus,
  useLastAutofocusResult,
  useHfrThresholds,
  useLinkDown,
  useLivePreview,
  useLivePreviewId,
  useNight,
  useOverlays,
  usePlan,
  usePolar,
  usePreviews,
  useProviders,
  useSelectedPreviewId,
  useSequence,
  useStretch,
  useViewport,
} from "../store";
import type { FocusEvent } from "../types";
import { VCurve, type FocusFit } from "../components/graphs";
import {
  AF_DEFAULT_GAIN, AF_FALLBACK_BIN, afResultAgeLabel, deriveAutofocusParams,
  plainFocusVerdict, focusButtonState, readFocusFailure,
} from "../lib/autofocus";
import {
  FOCUS_DEFAULT_GAIN, FOCUS_EXPOSURE_PRESETS, focusCaptureBlocker, focusCaptureBody,
  frameWaitNote, presetAction, sweepPreviewNote, sweepReadiness,
} from "../lib/focusCapture";
import { isExposureInvalid } from "../lib/exposure";
import { ProviderBadge } from "../components/ProviderBadge";
import { PreviewStage } from "../components/preview/PreviewStage";
import { FocusVerdict, AutofocusVerdict } from "../components/preview/FocusVerdict";
import { BahtinovAid } from "../components/preview/BahtinovAid";
import { FrameStats } from "../components/preview/FrameStats";
import { Field, LockedChip, LockedNote, Panel, Stat } from "../components/ui";
import { accessPhrase, useCanControlCapture } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { HELP } from "../help";
import StepDial from "../components/ui/StepDial";
import { nudgeLabel } from "../lib/stepDial";
import { anchorBlocker, moveProgress, type FocuserCommand } from "../lib/focusMove";

/** The magnitudes the dial offers. 1 for a final twiddle, 1000 to cross the
 *  whole critical zone on a 30k-step EAF. */
const STEP_VALUES = [1, 10, 100, 1000] as const;

/**
 * minus · dial · plus — the thumb row (design doc §Thumb zones).
 *
 * This replaced a 3x2 grid of fixed nudges. The grid's real cost was not its
 * size but where it forced itself to live: six 44px targets cannot sit beside a
 * preview, so they sat below it, so every adjustment was scroll-down / tap /
 * scroll-up / look — the complaint that started this whole design.
 */
function StepRow({
  step,
  onStep,
  reason,
  onNudge,
  onBlocked,
}: {
  step: number;
  onStep: (v: number) => void;
  /** Why nudging is unavailable, or null. Never a bare boolean: a blocked
   *  control must be able to say why (house rule §11.8). */
  reason: string | null;
  onNudge: (delta: number) => void;
  onBlocked: (reason: string) => void;
}): JSX.Element {
  const nudge = (sign: 1 | -1) => {
    if (reason) { onBlocked(reason); return; }
    onNudge(sign * step);
  };
  const btn = (sign: 1 | -1) => (
    <button
      type="button"
      className={`btn tap min-h-[44px] mono !normal-case justify-center flex-1 ${
        reason ? "opacity-40" : ""
      }`}
      aria-disabled={reason ? true : undefined}
      aria-label={`Move focuser ${nudgeLabel(step, sign)} steps${reason ? ` — ${reason}` : ""}`}
      title={reason ?? `Move ${nudgeLabel(step, sign)} steps`}
      onClick={() => nudge(sign)}
    >
      {sign > 0 ? "+" : "−"}
    </button>
  );
  return (
    <div className="flex items-stretch gap-2 mb-3">
      {btn(-1)}
      <StepDial
        values={STEP_VALUES}
        value={step}
        onChange={onStep}
        ariaLabel="Focuser step size"
        disabled={!!reason}
        disabledReason={reason}
        onBlocked={onBlocked}
      />
      {btn(1)}
    </div>
  );
}

export default function FocusView() {
  const status = useStatus();
  const focus = useFocus();
  // Canonical latest-completed-run record (F5: R2-FOC-01/DOC-FOC-01) — lives in
  // the store (not local state), so it rehydrates on mount and survives
  // navigating away and back; only a NEW run's own terminal event replaces it.
  const afResult = useLastAutofocusResult();
  const showToast = useStore((s) => s.showToast);
  const canFocus = useCanControlCapture(); // focuser/autofocus is imaging-control class

  // Live preview so manual focus is not blind (spec §10). Read-only here: zoom/pan
  // + verdict, no stretch/overlay controls (those are the Capture surface).
  const shown = useLivePreview();
  const previews = usePreviews();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const setViewport = useStore((s) => s.setViewport);
  const selectPreview = useStore((s) => s.selectPreview);
  const focusControls = useRef<{ fit: () => void; hundred: () => void; zoomIn: () => void; zoomOut: () => void } | null>(
    null,
  );
  const pinned = selectedId != null && selectedId !== liveId;
  const prevFrame = useMemo(() => {
    if (!shown) return null;
    const idx = previews.findIndex((p) => p.id === shown.id);
    return idx > 0 ? previews[idx - 1] : null;
  }, [shown, previews]);
  const [absTarget, setAbsTarget] = useState("");
  // The dial's magnitude. 100 is the useful default on a 30k-step EAF: 10 is a
  // twiddle, 1000 crosses the whole critical zone.
  const [step, setStep] = useState<number>(100);
  const [afExposure, setAfExposure] = useState("2");
  const [afStep, setAfStep] = useState("350");
  // The sweep's gain had no field at all until now: the manual branch posted
  // the DERIVED gain even when the user was steering everything else. With no
  // live frame to copy that derived gain is 120, which is the number two sweeps
  // died on. A setting that decides whether the run can work needs a box.
  const [afGain, setAfGain] = useState(String(AF_DEFAULT_GAIN));
  // UX-25: per-filter / per-binning autofocus. "" filter = leave the wheel where
  // it is. Binning options track the camera's reported ceiling (UX-27 shape).
  const [afFilter, setAfFilter] = useState("");
  // Seeded from the derivation the moment the panel opens (toggleAfAdvanced), so
  // this initial value is only ever a placeholder — but it used to be "2", the
  // policy this file no longer holds, and a stale duplicate of a superseded
  // default is how the old value creeps back.
  const [afBin, setAfBin] = useState(String(AF_FALLBACK_BIN));
  const [afAdvanced, setAfAdvanced] = useState(false);

  // ------------------------------------------------------------ #113 camera
  // The Focus screen could not start the camera. Not "was awkward to" — there
  // was no control, so the live preview read "No capture yet" all night and the
  // sweep had nothing to copy its exposure/gain/binning from. Bin 1 by default:
  // a focus frame is thrown away, and binning trades the star profile detail
  // that HFR is measured from for a download speed nobody is waiting on here.
  const [capExposure, setCapExposure] = useState("2");
  const [capGain, setCapGain] = useState(String(FOCUS_DEFAULT_GAIN));
  const [capBin, setCapBin] = useState("1");
  const [capPending, setCapPending] = useState<null | "single" | "loop">(null);
  // ms epoch when the server ACCEPTED a single exposure — cleared when a frame
  // lands. Held so the wait can be narrated (lib/focusCapture frameWaitNote).
  const [shotAt, setShotAt] = useState<number | null>(null);

  const plan = usePlan();
  const polar = usePolar();
  const sequence = useSequence();
  // Who actually runs the sweep. A BACKEND (NINA) autofocus never receives our
  // exposure/gain/binning — focus/autofocus.py hands `kind == "backend"`
  // straight to the backend's own routine and drops the parameters — so the
  // panel must not claim them, and must not refuse a run over a frame the
  // backend was never going to look at.
  const afProvider = useProviders()?.autofocus ?? null;
  const afParamsSent = afProvider?.kind !== "backend";
  const afProviderLabel = afProvider?.label ?? "The backend";
  const foc = status?.focuser;
  const pos = foc?.position ?? 0;
  const running = focus?.state === "running";
  // NOV-12 Bahtinov aid armed state (server truth via poll_status).
  const bahtOn = status?.bahtinov_active ?? false;
  // UX-25: filter/binning for the autofocus sweep.
  const filterNames = status?.filterwheel?.names ?? [];
  const afMaxBin = Math.min(8, Math.max(1, status?.camera?.max_bin ?? 4));
  const afBinOptions = Array.from({ length: afMaxBin }, (_, i) => i + 1);

  // The additive `fit` (method/R²/curve/trendlines) rides on the raw `focus`
  // event; types.ts FocusEvent stays untouched, so read it via a cast — same
  // pattern the store uses for `status.providers` (useProviders). Only the
  // LIVE V-curve chart below reads this (it needs the in-progress sweep's
  // fit while `running`); the Result panel reads the store's persisted
  // `afResult` instead (see above) so it survives navigation.
  const focusExt = focus as (FocusEvent & { fit?: FocusFit | null }) | null;
  const focusFit = focusExt?.fit ?? null;
  // Pixel scale for the arcsec HFR in the verdict: prefer computed optics, fall
  // back to the live frame's scale when the sensor reports it.
  const pixelScale = status?.optics?.image_scale_arcsec_px ?? shown?.pixel_scale_arcsec ?? null;
  const refocusArmed = plan.autofocus_every > 0 || plan.refocus_on_temp_delta_c > 0;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  // UX-29: clamp both the relative nudge buttons and Go-to into [0, max] instead
  // of relying on a silent server clamp — a negative or past-max target is a
  // user error we can catch before the round-trip.
  const focMax = typeof foc?.max === "number" && foc.max > 0 ? foc.max : null;
  const clampPos = (p: number) => {
    const r = Math.max(0, Math.round(p));
    return focMax != null ? Math.min(focMax, r) : r;
  };

  // ------------------------------------------------- in-flight move feedback
  // POST /api/focuser/move returns `{started}` immediately — the move itself
  // runs as a background task. Until this existed, pressing Go looked EXACTLY
  // like not pressing Go, which is how a firmware-refused move went unnoticed
  // for two nights. Hold the commanded target and narrate it. See lib/focusMove.
  const [cmd, setCmd] = useState<FocuserCommand | null>(null);
  const [now, setNow] = useState(() => Date.now());
  // When `pos` last CHANGED: proof of motion for the backends whose is_moving()
  // cannot answer, and the reset for the stall clock on a long move.
  const [progressAt, setProgressAt] = useState(() => Date.now());
  const seenPos = useRef<number | null>(null);
  useEffect(() => {
    if (seenPos.current === pos) return;
    seenPos.current = pos;
    setProgressAt(Date.now());
    setNow(Date.now());
  }, [pos]);

  // ------------------------------------------------------ #113 camera wiring
  // The exposure box is seeded by the presets but stays typeable: the setting
  // that actually worked at the scope on 2026-07-31 was FOUR seconds, and a
  // fixed preset row would have made the one value that mattered unreachable.
  const cam = status?.camera;
  // The LIVE frame, not `shown`: `shown` follows the filmstrip pin, and a sweep
  // must not inherit the exposure of a frame the user happens to be looking at
  // from an hour ago. This is what autofocus copies and what the Camera panel
  // reports, so both describe the camera's current state rather than the stage's.
  const liveFrame = useMemo(
    () => (liveId == null ? null : previews.find((p) => p.id === liveId) ?? null),
    [liveId, previews],
  );
  const capExposureInvalid = isExposureInvalid(capExposure);
  const capExposureS = capExposureInvalid ? 2 : Number(capExposure);
  const capGainNum = Number(capGain);
  const capGainInvalid =
    capGain.trim() === "" || !Number.isFinite(capGainNum) || capGainNum < 0
    || (!!cam?.max_gain && capGainNum > cam.max_gain);
  const looping = !!status?.looping;
  const polarBusy = polar.state === "running" || polar.state === "paused";
  // A PAUSED sequence still holds the camera between frames — it is not handed
  // back to manual control (r1 CAP-01), so it blocks here exactly as on Capture.
  const seqOwnsCamera = sequence.state === "running" || sequence.state === "paused";

  // A frame landed: whatever single exposure we were waiting on is done.
  useEffect(() => { setShotAt(null); }, [liveId]);
  // Frames that have reached THIS screen since the current sweep began. If a
  // four-minute sweep leaves this at 0, the empty stage does not mean the
  // camera is idle, and the panel has to say which of the two it is.
  const [sweepFrames, setSweepFrames] = useState(0);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (running && !wasRunning.current) setSweepFrames(0);
    wasRunning.current = running;
  }, [running]);
  useEffect(() => {
    if (wasRunning.current) setSweepFrames((n) => n + 1);
  }, [liveId]);

  // Re-anchoring (EAFResetPostion): declare the position, move nothing.
  const [anchorOpen, setAnchorOpen] = useState(false);
  const [anchorText, setAnchorText] = useState("");

  const progress = moveProgress(cmd, foc ? pos : null, foc?.moving, now, progressAt);
  const waiting = !!cmd && !progress?.settled;
  // ONE second-hand for the whole view: the in-flight move AND the in-flight
  // exposure both need `now` to advance, and neither needs its own interval.
  useEffect(() => {
    if (!waiting && shotAt == null) return; // settled: stop burning a timer
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting, shotAt]);

  // -------------------------------------------------------- #113 shutter
  // The POST returns before the shutter opens, so nothing is claimed until the
  // server has ACCEPTED the exposure — the ordering CaptureView had to learn
  // the hard way (a refused Single drew a full "exposing → downloading" cycle
  // for a frame that never existed).
  const shoot = async (kind: "single" | "loop", path: string) => {
    // Belt and braces: the buttons are locked stand-ins when blocked, but a
    // NaN gain from a half-typed box must never reach the wire, where
    // JSON.stringify turns it into `null`.
    if (kind === "single" ? singleReason : captureReason) return;
    setCapPending(kind);
    try {
      await api.post(path, focusCaptureBody({
        exposureS: capExposureS, gain: capGainNum, binning: Number(capBin) || 1,
      }));
      if (kind === "single") { setShotAt(Date.now()); setNow(Date.now()); }
    } catch (e) {
      showToast("error", (e as Error).message);
    } finally {
      setCapPending(null);
    }
  };
  const stopCapture = () => {
    setShotAt(null);   // nothing is in flight to narrate after a deliberate stop
    act(() => api.post("/api/capture/stop"));
  };
  // A preset tap while a loop is running RESTARTS the loop: hub.start_loop
  // closes over the exposure it was handed, so a tap that only moved a
  // highlight would leave the loop shooting the old length forever — a control
  // that looks applied and is ignored.
  const applyPreset = (s: number) => {
    setCapExposure(String(s));
    if (!looping || captureReason) return;
    void api.post("/api/capture/loop", focusCaptureBody({
      exposureS: s, gain: capGainNum, binning: Number(capBin) || 1,
    })).catch((e: Error) => showToast("error", e.message));
  };

  const moveTo = async (p: number) => {
    const target = clampPos(p);
    setCmd({ target, startedAt: Date.now(), from: pos });
    setNow(Date.now());
    try {
      await api.post("/api/focuser/move", { position: target });
    } catch (e) {
      // The command never landed, so there is nothing in flight to narrate —
      // leaving it would draw "→ 22000" over a move that was never accepted.
      setCmd(null);
      showToast("error", (e as Error).message);
    }
  };

  // "Go to position" only checked non-empty string; non-numeric input (e.g.
  // "abc") produced NaN -> JSON.stringify serializes NaN to null, sending a
  // null position to the server. Guard on numeric validity too, matching
  // Capture's exposure guard (lib/exposure.ts).
  const absTargetNum = Number(absTarget);
  const absTargetInvalid = absTarget.trim() === "" || !Number.isFinite(absTargetNum);

  // ------------------------------------------------- UX #24: stated reasons
  // Every blocked focuser control resolves to a sentence here and is rendered
  // through the house `LockedChip` — dim + lock glyph + aria-disabled +
  // aria-label + a tooltip that opens on TAP. The native `disabled` attribute
  // is not used: it removes the control and its reason from the a11y tree, and
  // `title=` never fires on the tablet this rig is driven from.
  const readOnlyReason = canFocus
    ? null
    : `Read-only session — ${accessPhrase("control.capture")} required`;
  // Shared by every control that commands the focuser.
  const focuserReason =
    readOnlyReason
    ?? (!foc ? "No focuser is connected — connect one on the Equipment page"
      : running ? "Autofocus is running — let the sweep finish first"
        : null);
  const goReason =
    focuserReason ?? (absTargetInvalid
      ? "Type a position number in the box first"
      : null);
  const haltReason = readOnlyReason;
  const anchorReason = anchorBlocker({
    canFocus, hasFocuser: !!foc,
    supported: !!foc?.can_set_position,
    moving: !!foc?.moving,
    raw: anchorText, max: focMax,
  });
  // Shared by every control that opens the shutter from this screen (#113).
  // `singleReason` adds the one blocker Loop does not have: a running loop
  // already owns the camera, and /api/capture would 409 against its own frames.
  const captureReason = focusCaptureBlocker({
    readOnlyReason, hasCamera: !!cam, polarBusy, sequenceOwnsCamera: seqOwnsCamera,
    autofocusRunning: running, exposureInvalid: capExposureInvalid,
    gainInvalid: capGainInvalid, gainMax: cam?.max_gain ?? null,
  });
  const singleReason =
    captureReason ?? (looping ? "A capture loop is running — press Stop first" : null);
  // What stops the sweep getting the FRAME it copies from. `captureReason` is
  // the rig-level half; a running loop is not one of those — its next frame is
  // seconds away and satisfies the sweep by itself — but until that frame lands,
  // "tap Single" is still the wrong instruction, because Single is locked behind
  // the loop. Whatever this says, the sweep's refusal says the same thing, so
  // the hero can never blame a missing frame on a tap the user cannot make.
  const frameBlocker =
    captureReason
    ?? (looping ? "a capture loop is running — its first frame has not landed yet" : null);

  // ------------------------------------------- what the sweep would actually do
  // Derived at RENDER, not inside the tap handler, because the whole point is to
  // print it BEFORE the tap. The night this fixes, the sweep chose 2s / gain 120
  // / bin 2 from an empty preview and said nothing about it, twice.
  const afDerived = deriveAutofocusParams({
    focuserMax: focMax,
    maxBin: cam?.max_bin ?? null,
    maxGain: cam?.max_gain ?? null,
    liveExposureS: liveFrame?.exposure_s ?? null,
    liveGain: liveFrame?.gain ?? null,
    // Binning is copied like gain: it is a setting the user chose, and it is
    // the one of the three that this rig's working configuration differs on
    // (bin 1 finds 2100 stars where bin 2 finds a third of them).
    liveBinning: liveFrame?.binning ?? null,
    liveStars: liveFrame?.stars ?? null,
    liveHfr: liveFrame?.hfr ?? null,
    hasLiveFrame: !!liveFrame,
  });
  // With the settings panel OPEN the user is steering, so their fields win.
  // ONE place builds the numbers, so the line printed under the button and the
  // request that goes out cannot disagree — they are the same object.
  const posOr = (raw: string, fallback: number) => {
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : fallback;
  };
  const numOr = (raw: string, fallback: number) => {
    const n = Number(raw);
    // 0 is a legitimate gain on plenty of sensors, so this cannot use `||`.
    return raw.trim() !== "" && Number.isFinite(n) && n >= 0 ? n : fallback;
  };
  const afParams = afAdvanced
    ? {
        exposure_s: posOr(afExposure, afDerived.exposure_s),
        gain: numOr(afGain, afDerived.gain),
        step: posOr(afStep, afDerived.step),
        binning: posOr(afBin, afDerived.binning),
        steps_each_side: afDerived.steps_each_side,
      }
    : {
        exposure_s: afDerived.exposure_s, gain: afDerived.gain, step: afDerived.step,
        binning: afDerived.binning, steps_each_side: afDerived.steps_each_side,
      };
  const afReady = sweepReadiness({
    manual: afAdvanced, hasLiveFrame: !!liveFrame, liveStars: liveFrame?.stars ?? null,
    params: afParams, source: afDerived.source, paramsSent: afParamsSent,
    captureBlocked: frameBlocker,
  });
  // Opening the settings panel seeds it from what the button WOULD have done —
  // otherwise "these override what the button above would have chosen" is a
  // claim about numbers the panel isn't showing.
  const toggleAfAdvanced = () => {
    if (!afAdvanced) {
      setAfExposure(String(afDerived.exposure_s));
      setAfGain(String(afDerived.gain));
      setAfStep(String(afDerived.step));
      setAfBin(String(afDerived.binning));
    }
    setAfAdvanced(!afAdvanced);
  };

  const frameWait = frameWaitNote({ startedAt: shotAt, exposureS: capExposureS, now });
  const sweepNote = sweepPreviewNote({
    running, pointsMeasured: focus?.points?.length ?? 0, framesSinceStart: sweepFrames,
    // A loop started from the Capture screen keeps running through a sweep (the
    // server refuses a loop only for polar and sequences), so frames CAN land
    // here that are not the sweep's. Say whose they are rather than let them
    // stand in as proof the sweep is delivering.
    loopRunning: looping,
  });

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_300px]">
      <div className="flex flex-col gap-4">
        {/* live preview so manual focus is not blind (spec §10) */}
        <Panel title="Live Preview" right={<FocusVerdict preview={shown} prev={prevFrame} hfrGood={hfrGood} hfrWarn={hfrWarn} />}>
          <div className="flex flex-col gap-2">
            <PreviewStage
              compact
              preview={shown}
              viewport={viewport}
              setViewport={setViewport}
              stretch={stretch}
              overlays={overlays}
              hfrGood={hfrGood}
              hfrWarn={hfrWarn}
              night={night}
              linkDown={linkDown}
              pinned={pinned}
              newSincePinned={pinned && selectedId != null ? previews.filter((p) => p.id > selectedId).length : 0}
              onReturnToLive={() => selectPreview(null)}
              onControls={(c) => (focusControls.current = c)}
            />
            <div className="flex items-center gap-1 preview-toolbar">
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom out" onClick={() => focusControls.current?.zoomOut()}>−</button>
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom in" onClick={() => focusControls.current?.zoomIn()}>+</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.fit()}>Fit</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.hundred()}>100%</button>
            </div>
            {/* A four-minute sweep exposes continuously, and on 2026-07-31 this
                stage said "No capture yet" for every second of it — the sweep's
                frames are taken through camera.expose directly and the server
                publishes no `preview` event for them (unlike plate-solve and
                rotate, which both call hub._publish_preview). Until it does,
                say which situation this is rather than let an empty stage imply
                an idle camera. */}
            {sweepNote && (
              <p className="text-[11px] text-dim leading-snug" role="status" aria-live="polite">
                {sweepNote}
              </p>
            )}
            {shown && <FrameStats preview={shown} hfrGood={hfrGood} hfrWarn={hfrWarn} compact />}
          </div>
        </Panel>

        {/* NOV-12 Bahtinov focus aid — arm/disarm + the live signed offset and a
            go/stop verdict, sitting under the live preview it reads from. */}
        <Panel title="Bahtinov Focus" right={!canFocus && <ReadOnlyBadge />}>
          <BahtinovAid preview={shown} />
          {/* UX #24: the read-only reason was `title=` only — on a tablet the
              button was simply dim and mute. LockedChip speaks it. */}
          <button
            className={`btn w-full tap min-h-11 mt-3 ${!canFocus ? "opacity-40" : ""} ${bahtOn ? "btn-accent" : ""}`}
            aria-disabled={!canFocus || undefined}
            aria-pressed={bahtOn}
            aria-label={readOnlyReason
              ? `${bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"} — ${readOnlyReason}`
              : undefined}
            onClick={
              !canFocus
                ? undefined
                : () =>
                    act(() =>
                      api.post(
                        bahtOn ? "/api/focuser/bahtinov/stop" : "/api/focuser/bahtinov/start",
                        bahtOn ? {} : { exposure_s: 1, gain: 100, binning: 1 },
                      ),
                    )
            }
          >
            {bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"}
          </button>
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mt-2" />}
          <p className="text-[11px] text-dim mt-2 leading-relaxed">
            Put a Bahtinov mask on the scope and point at a bright star, then watch
            the middle spike offset drop to zero.
          </p>
        </Panel>

        <Panel title="V-Curve · HFR vs Position"
          right={running && <span className="text-accent text-[11px] blink tracking-widest uppercase">measuring…</span>}>
          <VCurve points={focus?.points ?? []} best={focus?.best ?? null} fit={focusFit} running={running} />
        </Panel>
      </div>

      <div className="flex flex-col gap-4">
        {/* Result panel — the verdict-first outcome lives in the right column
            above the Focuser, matching the design reference (F5). */}
        <Panel title="Result" right={<ProviderBadge cap="autofocus" />}>
          {running ? (
            <div className="text-accent text-sm blink">Measuring…</div>
          ) : afResult ? (
            <>
              {/* #114. This panel used to print the engine's raw token
                  (r_squared_below_threshold) in the chip DIRECTLY under a
                  sentence that contradicted it — "Not enough stars to lock
                  onto" — and the user, reasonably, followed the sentence and
                  spent the night chasing star count on a run that had failed on
                  the SHAPE of the curve. Now one thing is said twice: the
                  sentence explains the token, or the server's own `advice`
                  replaces it, and the chip keeps the token as a breadcrumb that
                  agrees with the words above it. */}
              {(() => {
                const pv = plainFocusVerdict({
                  state: afResult.state, hfr: afResult.best?.hfr ?? null,
                  r2: afResult.fit?.r2 ?? null, hfrGood, hfrWarn,
                  message: afResult.message, advice: afResult.advice,
                });
                const tone = pv.tone === "good" ? "text-good" : pv.tone === "warn" ? "text-warn"
                  : pv.tone === "bad" ? "text-bad" : "text-dim";
                return (
                  <div className="mb-2" role="status" aria-live="polite">
                    <div className={`text-base font-semibold ${tone}`}>{pv.headline}</div>
                    <div className="text-xs text-dim leading-snug">{pv.detail}</div>
                  </div>
                );
              })()}
              <AutofocusVerdict
                state={afResult.state}
                hfr={afResult.best?.hfr ?? null}
                r2={afResult.fit?.r2 ?? null}
                method={afResult.fit?.method ?? null}
                pixelScaleArcsec={pixelScale}
                hfrGood={hfrGood}
                hfrWarn={hfrWarn}
                // The short token when we recognise one (the sentence above now
                // says what it means), otherwise the server's own prose — never
                // the chip's canned "check stars in frame", which is the guess
                // that started this.
                message={readFocusFailure(afResult.message).code ?? afResult.message}
              />
              {afResult.state === "done" && afResult.best && (
                <div className="mt-3">
                  <Stat label="best position" value={afResult.best.position} />
                </div>
              )}
              {/* Persisted-run provenance (F5: R2-FOC-01/DOC-FOC-01) — this
                  line, like the verdict above, reads store.lastAutofocusResult
                  (not local state), so it is still here after navigating away
                  and back; only a NEW run's own terminal event replaces it. */}
              <p className="text-[11px] text-dim mt-2">
                {afResult.provider ? `${afResult.provider.label} · ` : ""}
                {afResult.filter ? `${afResult.filter} · ` : ""}
                {afResultAgeLabel(afResult.ts, Date.now())}
              </p>
            </>
          ) : (
            <div className="text-faint text-sm">Run autofocus to measure focus quality.</div>
          )}
        </Panel>

        {/* -------------------------------------------------------- #113 camera
            The half of this screen that was never built. Autofocus reads its
            exposure, gain and binning OFF THE LIVE FRAME, and until now there
            was no way to take one from here — so on 2026-07-31 the preview read
            "No capture yet" all night and two sweeps ran on 2s / gain 120 /
            bin 2 out of thin air. It sits directly above Autofocus because that
            is the order the two are used in, and the last line ties them
            together: what the sweep will copy is what this panel just shot. */}
        <Panel title="Camera" right={!canFocus && <ReadOnlyBadge />}>
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mb-3" />}
          <div className="grid grid-cols-5 gap-1 mb-3">
            {FOCUS_EXPOSURE_PRESETS.map((s) => {
              const on = Number(capExposure) === s;
              // The hint changes with the loop, and the difference is the whole
              // point: "use it for the next frame" vs "restart what is running".
              const pa = presetAction(looping, s);
              return (
                <button
                  key={s}
                  type="button"
                  className={`btn tap min-h-[44px] !px-0 justify-center mono text-[11px] ${on ? "btn-accent border-accent" : ""}`}
                  aria-pressed={on}
                  aria-label={`${s} second exposure — ${pa.hint}`}
                  title={pa.hint}
                  onClick={() => applyPreset(s)}
                >
                  {s}s
                </button>
              );
            })}
          </div>
          {/* The box stays typeable behind the presets: the exposure that
              actually worked at the scope was FOUR seconds, and a fixed row of
              five would have put the one value that mattered out of reach. */}
          <div className="grid grid-cols-2 gap-2 mb-3">
            <Field label="Exposure (s)">
              <input className="field mono" value={capExposure} inputMode="decimal"
                readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                onChange={(e) => setCapExposure(e.target.value)} />
            </Field>
            <Field label={cam?.max_gain ? `Gain (max ${cam.max_gain})` : "Gain"}>
              <input className="field mono" value={capGain} inputMode="numeric"
                readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                onChange={(e) => setCapGain(e.target.value)} />
            </Field>
            <Field label="Binning">
              {readOnlyReason ? (
                <LockedChip reason={readOnlyReason} className="btn w-full">
                  {capBin}×{capBin}
                </LockedChip>
              ) : (
                <select className="field" value={capBin}
                  onChange={(e) => setCapBin(e.target.value)}>
                  {afBinOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
                </select>
              )}
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {capPending === "single" ? (
              <button className="btn tap min-h-[48px]" aria-disabled aria-busy
                aria-label="Waiting for the camera to accept this exposure — no frame has started yet">
                Starting…
              </button>
            ) : shotAt != null ? (
              <button className="btn btn-accent border-accent tap min-h-[48px]"
                aria-disabled aria-pressed
                aria-label="A frame is already exposing — press Stop to abandon it">
                Exposing…
              </button>
            ) : singleReason ? (
              <LockedChip reason={singleReason} className="btn tap min-h-[48px] justify-center">
                Single
              </LockedChip>
            ) : (
              <button className="btn btn-accent tap min-h-[48px]"
                onClick={() => void shoot("single", "/api/capture")}>
                Single
              </button>
            )}
            {looping ? (
              <button className="btn btn-accent border-accent tap min-h-[48px]"
                aria-disabled aria-pressed
                aria-label="Looping — frames repeat until you press Stop">
                Looping…
              </button>
            ) : capPending === "loop" ? (
              <button className="btn tap min-h-[48px]" aria-disabled aria-busy
                aria-label="Waiting for the camera to accept this exposure — no frame has started yet">
                Starting…
              </button>
            ) : captureReason ? (
              <LockedChip reason={captureReason} className="btn tap min-h-[48px] justify-center">
                Loop
              </LockedChip>
            ) : (
              <button className="btn tap min-h-[48px]"
                onClick={() => void shoot("loop", "/api/capture/loop")}>
                Loop
              </button>
            )}
            {/* Stop is urgent motion-stop -> 1-tap (R9), and carries the same
                non-hue danger encoding as Capture's and the Halt beside it:
                filled square glyph + 2px border + capped-luminance fill. */}
            {readOnlyReason ? (
              <LockedChip reason={readOnlyReason}
                className="btn btn-danger tap min-h-[48px] !border-2 justify-center">
                Stop
              </LockedChip>
            ) : (
              <button
                className="btn btn-danger tap min-h-[48px] !border-2 inline-flex items-center justify-center gap-1.5"
                style={{ background: "color-mix(in srgb, var(--danger-ink) 15%, transparent)" }}
                onClick={stopCapture}>
                <Icon name="stop" size={13} className="shrink-0 fill-current" aria-hidden />
                Stop
              </button>
            )}
          </div>
          {captureReason && <LockedNote reason={captureReason} className="mt-2" />}
          {/* The tap, narrated. POST /api/capture returns before the shutter
              opens, so without this pressing Single looks exactly like not
              pressing it — the failure the Go button had, one panel down. */}
          {frameWait && (
            <p role="status" aria-live="polite"
              className={`mono text-[11px] mt-2 ${frameWait.tone === "warn" ? "text-warn" : "text-accent"}`}>
              {frameWait.text}
            </p>
          )}
          {/* The line that joins this panel to the one below it. */}
          <p className="mono text-[11px] text-faint mt-2 leading-snug">
            {liveFrame
              ? `last frame ${liveFrame.exposure_s}s · gain ${liveFrame.gain} · bin ${liveFrame.binning}`
                + (liveFrame.stars != null ? ` · ${liveFrame.stars} stars` : "")
              : "no frame yet — autofocus has nothing to copy its settings from"}
          </p>
          {/* Precise about WHICH of the three is copied unconditionally: gain
              and binning are settings the user chose, so the sweep takes them
              from any frame; the exposure is only copied once a frame proves it
              produced stars, because copying the exposure that measured four
              stars is copying the thing that did not work. */}
          <p className="text-[11px] text-dim mt-1 leading-snug">
            Focus frames are not saved. Autofocus sweeps at this gain and binning,
            and at this exposure too once a frame shows stars — so shoot something
            that works before you sweep.
          </p>
        </Panel>

        {/* Rail order (design doc): READOUT at the top nearest the image,
            the ACTION next, and the thumb row LAST because that is where a
            hand actually rests. Autofocus used to be the bottom-most panel,
            which on a phone is off the end of a scroll. */}
        <Panel title="Autofocus" right={!canFocus && <ReadOnlyBadge />}>
          {(() => {
            // The sweep is blocked when nothing has been measured for it to
            // copy (lib/focusCapture sweepReadiness) — ranked below permission
            // and hardware, because it is the smallest of the three facts.
            const bs = focusButtonState({
              canFocus, hasFocuser: !!foc, running, sweepBlock: afReady.block,
            });
            // `afParams` is built once at render (above) and is the SAME object
            // the summary line prints, so the sentence under the button and the
            // request that goes out cannot drift apart. The filter rides along
            // only when the user picked one in the open settings panel.
            const onTap = () => act(() => api.post("/api/focuser/autofocus", {
              ...afParams,
              ...(afAdvanced && afFilter !== "" ? { filter: Number(afFilter) } : {}),
            }));
            // UX #24: `focusButtonState` already computes excellent gating copy —
            // it was just handed to `title=`, which a fingertip never fires. The
            // hero keeps its own chrome (a lock-glyph 56px button, not a chip) and
            // the reason is now spoken by aria-label AND printed underneath.
            // ONE line: the action and its settings (design doc §Landscape —
            // rail). The button used to span the whole screen with a separate
            // "▸ Advanced" row beneath it, in a rail only 300px wide; the gear
            // now sits on the same line, which is both smaller and closer to
            // the thing it configures.
            return (
              <>
                <div className={`flex items-stretch gap-2 ${bs.disabled ? "mb-1.5" : "mb-3"}`}>
                  <button
                    className={`btn btn-accent flex-1 tap-lg min-h-[56px] ${bs.disabled ? "opacity-40" : ""}`}
                    aria-disabled={bs.disabled || undefined}
                    aria-label={bs.reason ? `${bs.label} — ${bs.reason}` : undefined}
                    onClick={bs.disabled ? undefined : onTap}
                  >
                    {bs.locked && <Icon name="lock" size={13} className="inline -mt-0.5 mr-1.5" />}
                    {!bs.locked && <Icon name="focus" size={14} className="inline -mt-0.5 mr-1.5" />}
                    {bs.label}
                  </button>
                  <button
                    className={`btn tap min-h-[56px] px-3 ${afAdvanced ? "border-accent text-accent" : ""}`}
                    aria-expanded={afAdvanced}
                    aria-label="Autofocus settings"
                    title="Autofocus settings"
                    onClick={toggleAfAdvanced}
                  >
                    <Icon name="settings" size={16} />
                  </button>
                </div>
                {/* What the sweep will actually do, BEFORE it is tapped, and
                    where those numbers came from. Two four-minute runs on
                    2026-07-31 swept at 2s / gain 120 / bin 2 — copied from a
                    preview that had never taken a frame — and neither the
                    button, the panel nor the result said so. */}
                <p className="text-[11px] mb-1.5 leading-snug">
                  {/* mono only when the summary IS numbers; the backend case is
                      a sentence and mono would make it read as a code string. */}
                  <span className={afReady.basis === "backend" ? "text-dim" : "mono text-dim"}>
                    {afReady.summary}
                  </span>
                  <span className="text-faint"> — {afReady.provenance}</span>
                </p>
                {afReady.warn && (
                  <p className="text-[11px] text-warn mb-3 leading-snug">{afReady.warn}</p>
                )}
                {/* The way OUT of the loop that cost a night: autofocus needs
                    stars to start, a badly-defocused rig has none, and until
                    now the only tool offered for getting to rough focus was
                    autofocus itself. This walks the travel counting stars and
                    stops the moment a position has enough to hand over. It is
                    offered permanently rather than only after a failure —
                    someone who knows the rig is miles out should not have to
                    fail once to be told about it. */}
                <button
                  className="btn w-full tap min-h-[44px] mb-3 text-[11px]"
                  disabled={running || !foc || !canFocus}
                  title="Step across the focuser's travel and stop where there are enough stars to autofocus"
                  onClick={() => act(() => api.post("/api/focuser/coarse", {}))}
                >
                  Find focus roughly first
                </button>
                {bs.reason && <LockedNote reason={bs.reason} className="mb-3" />}
              </>
            );
          })()}

          {afAdvanced && (
            <>
              <div className="grid grid-cols-2 gap-3 mb-4">
                <Field label="Exposure (s)">
                  <input className="field" value={afExposure}
                    readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                    onChange={(e) => setAfExposure(e.target.value)} />
                </Field>
                <Field label="Step size" hint={HELP.stepSize}>
                  <input className="field" value={afStep}
                    readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                    onChange={(e) => setAfStep(e.target.value)} />
                </Field>
                {/* Gain had no field at all: the manual branch posted the
                    DERIVED gain even with every other value hand-typed, and
                    with no live frame that derived gain is 120 — the number
                    that found 8 stars where 220 finds 2100. */}
                <Field label={cam?.max_gain ? `Gain (max ${cam.max_gain})` : "Gain"}>
                  <input className="field" value={afGain} inputMode="numeric"
                    readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                    onChange={(e) => setAfGain(e.target.value)} />
                </Field>
                {filterNames.length > 0 && (
                  <Field label="Filter">
                    {/* <select> has no `readOnly`; the locked state shows the value
                        through the house locked stand-in instead of a native
                        `disabled` select with no reachable reason. */}
                    {readOnlyReason ? (
                      <LockedChip reason={readOnlyReason} className="btn w-full">
                        {afFilter === "" ? "current" : filterNames[Number(afFilter)] ?? "current"}
                      </LockedChip>
                    ) : (
                      <select className="field" value={afFilter}
                        onChange={(e) => setAfFilter(e.target.value)}>
                        <option value="">current</option>
                        {filterNames.map((name, i) => <option key={`${i}-${name}`} value={i}>{name}</option>)}
                      </select>
                    )}
                  </Field>
                )}
                <Field label="Binning">
                  {readOnlyReason ? (
                    <LockedChip reason={readOnlyReason} className="btn w-full">
                      {afBin}×{afBin}
                    </LockedChip>
                  ) : (
                    <select className="field" value={afBin}
                      onChange={(e) => setAfBin(e.target.value)}>
                      {afBinOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
                    </select>
                  )}
                </Field>
              </div>
              {/* NO second run button here. There used to be one an inch below
                  the hero, and two buttons that both say "run autofocus" is a
                  question the user has to answer before every run. These fields
                  now feed the ONE button above — see the note below. */}
              {/* On a backend-run sweep these boxes reach nothing: the server
                  hands `kind == "backend"` to the backend's own autofocus and
                  drops every parameter. A panel that accepts input it cannot
                  deliver is the same failure as a button that does nothing. */}
              {afParamsSent ? (
                <p className="text-[11px] text-dim leading-snug">
                  These override what the button above would have chosen. Close this
                  panel to go back to automatic settings.
                </p>
              ) : (
                <LockedNote
                  reason={`${afProviderLabel} runs this sweep with its own exposure, gain and binning — nothing typed here is sent`}
                />
              )}
            </>
          )}
          <p className="text-[11px] text-dim mt-3 leading-relaxed">
            Sweeps 4 steps each side of current position, measures star HFR,
            fits the V-curve and drives to its minimum.
          </p>
          <p className="mono text-[11px] text-faint mt-2">
            {refocusArmed
              ? `Refocus armed:${plan.autofocus_every > 0 ? ` every ${plan.autofocus_every} frames` : ""}` +
                `${plan.autofocus_every > 0 && plan.refocus_on_temp_delta_c > 0 ? " ·" : ""}` +
                `${plan.refocus_on_temp_delta_c > 0 ? ` Δtemp ${plan.refocus_on_temp_delta_c}°C` : ""}`
              : "Refocus: manual only (set cadence in the plan)"}
          </p>
        </Panel>

        <Panel title="Focuser" right={!canFocus && <ReadOnlyBadge />}>
          {/* The header pill keeps its WHY in a `title=`; say it out loud here. */}
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mb-3" />}
          <div className="flex items-end justify-between mb-4">
            <Stat label="position" value={foc ? pos : "—"} />
            <Stat label="max" value={foc?.max ?? "—"} />
            <Stat label="temp" value={foc?.temperature?.toFixed(1) ?? "—"} unit="°C" />
          </div>
          {/* The thumb row (design doc §Thumb zones). Six buttons became three
              controls: minus, the magnitude dial, plus. Held in two hands the
              thumbs rest at the bottom corners, so that is where - and + go,
              with the rarely-changed dial between them as the one thing you
              have to look at. */}
          <StepRow
            step={step}
            onStep={setStep}
            reason={focuserReason}
            onNudge={(d) => moveTo(pos + d)}
            onBlocked={(r) => showToast("warning", r)}
          />
          <div className="grid grid-cols-[1fr_auto_auto] gap-2 items-end">
            <Field label="Go to position">
              <input className="field" placeholder={String(pos)} value={absTarget}
                readOnly={!canFocus} aria-readonly={!canFocus || undefined}
                onChange={(e) => setAbsTarget(e.target.value)} />
            </Field>
            {/* UX #24 — GO was the finding's named example: four different
                blockers collapsed into one native `disabled`, with no title, no
                aria-label and no note. It now names the one that applies. */}
            {goReason ? (
              <LockedChip reason={goReason} className="btn tap min-h-[44px] justify-center">
                Go
              </LockedChip>
            ) : (
              <button className="btn tap min-h-[44px]"
                onClick={() => moveTo(absTargetNum)}>Go</button>
            )}
            {/* Halt is urgent motion-stop -> stays 1-tap (R9). Disabled for viewers
                (they can't have a focuser move in flight to halt).

                UX #14 / S3: the SAME non-hue danger encoding Capture's Stop now
                carries (filled-square glyph + 2px border + a capped-luminance
                fill), so "stop the thing" has one silhouette across the app
                instead of a different red outline per view. */}
            {haltReason ? (
              <LockedChip reason={haltReason}
                className="btn btn-danger tap min-h-[44px] !border-2 justify-center">
                Halt
              </LockedChip>
            ) : (
              <button
                className="btn btn-danger tap min-h-[44px] !border-2 inline-flex items-center justify-center gap-1.5"
                style={{ background: "color-mix(in srgb, var(--danger-ink) 15%, transparent)" }}
                onClick={() => {
                  // Drop the commanded target: after a deliberate Halt,
                  // "not moving — stopped at X" is technically true but reads
                  // as a fault report for something the user just did.
                  setCmd(null);
                  act(() => api.post("/api/focuser/halt"));
                }}>
                <Icon name="stop" size={13} className="shrink-0 fill-current" aria-hidden />
                Halt
              </button>
            )}
          </div>
          {/* The move, narrated. `aria-live` because the whole point is that
              something changed without the user touching anything. */}
          {progress && (
            <p role="status" aria-live="polite"
              className={`mono text-[11px] mt-2.5 ${
                progress.tone === "warn" ? "text-warn"
                  : progress.tone === "good" ? "text-good" : "text-accent"}`}>
              {progress.text}
            </p>
          )}

          {/* Re-anchoring. A stepper focuser's position is a COUNT with no
              physical meaning until something anchors it, and this one resets
              to 0 when it loses power — the drawtube stays put while every
              saved position silently changes meaning. This is the repair, and
              it is deliberately behind a disclosure: it rewrites what all those
              numbers mean, which is not something to do with a stray tap. */}
          {foc?.can_set_position && (
            <div className="mt-3 pt-3 border-t border-line">
              {!anchorOpen ? (
                <button className="btn w-full tap min-h-[36px] text-[11px]"
                  onClick={() => { setAnchorOpen(true); setAnchorText(String(pos)); }}>
                  Set current position…
                </button>
              ) : (
                <>
                  <p className="text-[11px] text-dim leading-snug mb-2">
                    Tells the focuser it is at this number. <strong>Nothing
                    moves.</strong> Use it when the count has been lost: put the
                    drawtube somewhere you know first, then say where that is.
                  </p>
                  <div className="flex items-stretch gap-2">
                    <input className="field mono flex-1" value={anchorText}
                      inputMode="numeric"
                      aria-label="New position value for the focuser"
                      onChange={(e) => setAnchorText(e.target.value)} />
                    {anchorReason ? (
                      <LockedChip reason={anchorReason}
                        className="btn tap min-h-[36px] justify-center">
                        Set
                      </LockedChip>
                    ) : (
                      <button className="btn btn-accent tap min-h-[36px]"
                        onClick={() => act(async () => {
                          await api.post("/api/focuser/set-position",
                                         { position: Number(anchorText) });
                          // The commanded-move narration is about a target that
                          // no longer refers to the same place — drop it rather
                          // than leave it pointing at the old frame.
                          setCmd(null);
                          setAnchorOpen(false);
                        })}>
                        Set
                      </button>
                    )}
                    <button className="btn tap min-h-[36px]"
                      onClick={() => setAnchorOpen(false)}>Cancel</button>
                  </div>
                  {anchorReason && <LockedNote reason={anchorReason} className="mt-2" />}
                </>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
