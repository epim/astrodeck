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
import { PreviewStage, type StageControls } from "../components/preview/PreviewStage";
import FocusPod, { POD_BOTTOM_PX, POD_DISC_PX, POD_MIN_STAGE_H } from "../components/focus/FocusPod";
import ActivityRing from "../components/ui/ActivityRing";
import CameraDial from "../components/ui/CameraDial";
import {
  BinningPicker, ExposurePicker, FilterPicker, GainPicker,
  cameraDialCategories,
} from "../components/ui/CameraPickers";
import { focusState } from "../lib/focusVerdict";
import { FocusVerdict, AutofocusVerdict } from "../components/preview/FocusVerdict";
import { BahtinovAid } from "../components/preview/BahtinovAid";
import { FrameStats } from "../components/preview/FrameStats";
import { Field, Led, LockedChip, LockedNote, Panel, Stat } from "../components/ui";
import { accessPhrase, useCanControlCapture } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { HELP } from "../help";
import StepDial from "../components/ui/StepDial";
import { nudgeLabel } from "../lib/stepDial";
import {
  MOVE_IN_FLIGHT_REASON, MOVE_SENDING_REASON, anchorBlocker, moveProgress,
  retireAfterMs, type FocuserCommand,
} from "../lib/focusMove";
import { useBusy } from "../lib/useBusy";

/** The magnitudes the dial offers. 1 for a final twiddle, 1000 to cross the
 *  whole critical zone on a 30k-step EAF. */
const STEP_VALUES = [1, 10, 100, 1000] as const;

/** How long the Bahtinov button waits for `status.bahtinov_active` to agree
 *  with the tap before it gives up and shows the rig's own answer again. Three
 *  status frames (the poll is 2s): long enough that one dropped frame does not
 *  flip the label back and forth, short enough that a request which never took
 *  cannot leave the control stuck on "Arming…". */
const BAHTINOV_PENDING_GRACE_MS = 6000;

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
  const focusControls = useRef<StageControls | null>(null);
  // The 1:1 magnifier's state LIVES in PreviewStage (crop+render Decision F —
  // ephemeral view state, no store slice). Mirrored here for the same reason
  // LivePreview mirrors it: the toolbar toggle has to render its pressed and
  // its honest-disabled state. The guard is what stops an unchanged controls
  // callback from looping.
  const [loupe, setLoupe] = useState({ on: false, available: false });
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
  // WHO ELSE IS DRIVING THE FOCUSER. `running` comes off the `focus` event
  // stream, which only reaches a client that was connected when the run
  // started: open this screen mid-sweep and every focuser control offers itself
  // as live, then 409s. The rig has said so on every 2s status frame all along —
  // both /api/focuser/autofocus and /api/focuser/coarse `_spawn` the SAME
  // `autofocus` lane (api/app.py), so one lane answers for both. Used for the
  // GATES only; the V-curve and the Result panel still follow the event stream,
  // because those are about a sweep whose data we actually have.
  // (Called unconditionally, then OR-ed: `running || useBusy(...)` would skip
  // the hook whenever a sweep IS running and change the hook order mid-run.)
  const afLaneBusy = useBusy("autofocus");
  const sweeping = running || afLaneBusy;
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
  // THE ROUND TRIP ITSELF. `cmd` is armed only after the POST resolves — the
  // ordering that stops a REFUSED move being drawn as a real one — so between
  // the tap and the answer there was no `cmd`, therefore no `waiting`, therefore
  // no blocked control and nothing on screen: the same "pressing Go looks like
  // not pressing Go" this whole section exists to remove, just narrower. The
  // target being sent, or null. Mirrors `capPending` four functions below.
  const [sending, setSending] = useState<number | null>(null);
  // …and the same latch as a ref, because the state one cannot close the window
  // it guards: `sending` is a render closure, so two taps dispatched inside a
  // single React batch (a double tap on a tablet, or a repeat-fire) both read
  // the value from BEFORE the first one, both pass, and both reach the rig —
  // the second earning a 409 off the `focuser` lane. A ref is written
  // synchronously inside the handler, so the second tap sees the first.
  const sendingRef = useRef(false);
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

  // ------------------------------------------------- the frame that never came
  // The tap, narrated: "exposing · 3s left" → "reading out…" → the verdict that
  // it is gone. Computed HERE rather than beside the paragraph that prints it,
  // because three other things need the verdict and one of them is a control.
  const frameWait = frameWaitNote({ startedAt: shotAt, exposureS: capExposureS, now });
  // THE FAILURE PATH, which this screen had none of. `shotAt` is cleared by a
  // new preview id and by Stop — the frame ARRIVING, or the user giving up. When
  // the frame simply never comes (USB drop, an aborted exposure, a camera error)
  // neither ever happens, so the Single button sat on `btn-accent` /
  // aria-pressed / "Exposing…" and the pod's SHOOT chip went on refusing with
  // "A frame is already exposing — press Stop to abandon it" for the rest of the
  // night, over a camera that had dropped the frame minutes earlier.
  //
  // The verdict already existed and was only ever PRINTED: `frameWaitNote` turns
  // `warn` once the frame is later than the exposure plus a full readout grace
  // (lib/focusCapture FRAME_READOUT_GRACE_MS, the same 60s as CaptureView's
  // DOWNLOAD_WATCHDOG_MS) and says "the camera may have dropped it". So the
  // screen computed that the frame was lost, said so, drew the pod ring's
  // dashed stalled signature for it — and went on refusing the one tap that
  // answers it. This applies that same verdict to the CONTROLS, and to nothing
  // else: the sentence and the stalled ring stay exactly as they are, because
  // they were the two things that were already right.
  //
  // `shotAt` itself is deliberately NOT cleared. Clearing it would take the
  // sentence and the ring away at the exact moment they became true and hand
  // back a Single button with no explanation of why the last one produced
  // nothing.
  const frameLost = frameWait?.tone === "warn";
  const exposing = shotAt != null && !frameLost;

  // ------------------------------------------- what the CAMERA is exposing at
  // `capExposure` is a box on this screen. It is seeded to "2" and never seeded
  // from the rig — so the moment a loop is running that this box did not just
  // start, the lit preset and the pod's exposure badge are claims about the
  // CAMERA made out of a local draft. Start a 10s loop on the Capture screen and
  // switch here, or simply reload the tab mid-loop, and "2s" renders filled with
  // aria-pressed=true while the provenance line two rows below reads "last frame
  // 10s · gain …" — two contradictory claims about one camera in one panel. This
  // file's own comment already states the invariant (applyPreset, below): "a lit
  // preset over a running loop is a claim about the camera, not about what is
  // typed in a box".
  //
  // So while a loop runs, the rig's own frames decide. `loopWanted` is the one
  // exception, and it is not a draft: a preset tap RESTARTS the loop, and once
  // the server has accepted that restart the loop IS at that exposure — it is
  // the frame already in flight that still carries the old length. It is dropped
  // the moment a frame agrees, when the loop stops, when the rig refuses the
  // restart, and after a grace, so a restart that never took cannot strand a
  // number nothing is shooting.
  const [loopWanted, setLoopWanted] = useState<number | null>(null);
  const loopExposureS = looping ? liveFrame?.exposure_s ?? null : null;
  useEffect(() => {
    if (loopWanted == null) return;
    if (!looping || loopExposureS === loopWanted) { setLoopWanted(null); return; }
    // Long enough for the frame already in flight to land and the first frame of
    // the restarted loop to follow it; short enough that a loop somebody else
    // has restarted since cannot keep claiming our number.
    const t = setTimeout(() => setLoopWanted(null), Math.max(15_000, (loopWanted + 20) * 1000));
    return () => clearTimeout(t);
  }, [loopWanted, looping, loopExposureS]);
  // The exposure this screen may claim the camera is using, or null when it
  // cannot honestly claim one: a loop is running and no frame of it has landed
  // yet, or the box is empty or not a number.
  const shownExposureS = looping
    ? loopWanted ?? loopExposureS
    : capExposureInvalid ? null : capExposureS;
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
  // RETIRE THE COMMAND once it has been answered. Held forever, it made every
  // LATER motion of the focuser — a sweep, a coarse walk, the sequencer, another
  // client — read as this command: "→ 22000" over a move nobody asked for, then
  // the orange "not moving — stopped at 18300, asked for 22000" six seconds
  // later. See lib/focusMove retireAfterMs for the delays and why a refusal
  // outstays a confirmation.
  const retire = retireAfterMs({
    settled: !!progress?.settled,
    tone: progress?.tone ?? null,
    sweepOwnsFocuser: sweeping,
    sequenceRunning: seqOwnsCamera,
  });
  useEffect(() => {
    if (cmd == null || retire == null) return;
    if (retire === 0) { setCmd(null); return; }
    const t = setTimeout(() => setCmd(null), retire);
    return () => clearTimeout(t);
  }, [cmd, retire]);
  // ONE second-hand for the whole view: the in-flight move AND the in-flight
  // exposure both need `now` to advance, and neither needs its own interval.
  // It stops once the frame has been declared lost as well as when one lands —
  // otherwise a dropped frame left a 1s timer running until the tab was closed,
  // counting up a number nobody is waiting on.
  useEffect(() => {
    if (!waiting && (shotAt == null || frameLost)) return; // settled: stop burning a timer
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting, shotAt, frameLost]);

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
      // The loop is now running at OUR exposure — the server took it — but its
      // first frame is a whole exposure away, and until one lands nothing else
      // on this screen can say what the camera is set to. Same claim, same
      // expiry, as a preset restart.
      else setLoopWanted(capExposureS);
    } catch (e) {
      showToast("error", (e as Error).message);
    } finally {
      setCapPending(null);
    }
  };
  // ------------------------------------------------------- Bahtinov, in flight
  // What we asked the aid to BE, until the rig says it is that. The route is
  // synchronous — it arms the aid and starts a live loop before it answers — but
  // `status.bahtinov_active` only reaches this screen on the next 2s status
  // frame, so between the tap and that frame the button still read "Bahtinov
  // focus" and a second finger sent the opposite command to a rig mid-arm.
  //
  // Same shape as lib/useBusy's useBusyOrPending, and it EXPIRES for the same
  // reason: a request that never took effect (a 403, a dropped connection, an
  // aid disarmed from another client) must not leave the control dead. Keyed on
  // the published flag rather than a busy lane because this aid has no lane —
  // arming is a state, not a task.
  const [bahtWanted, setBahtWanted] = useState<boolean | null>(null);
  useEffect(() => {
    if (bahtWanted == null) return;
    if (bahtOn === bahtWanted) { setBahtWanted(null); return; }
    const t = setTimeout(() => setBahtWanted(null), BAHTINOV_PENDING_GRACE_MS);
    return () => clearTimeout(t);
  }, [bahtWanted, bahtOn]);
  const toggleBahtinov = (want: boolean) => {
    setBahtWanted(want);
    void act(async () => {
      try {
        await api.post(
          want ? "/api/focuser/bahtinov/start" : "/api/focuser/bahtinov/stop",
          want ? { exposure_s: 1, gain: 100, binning: 1 } : {},
        );
      } catch (e) {
        // Refused (polar or a sequence owns the camera, or there is none): give
        // the button back NOW rather than after the grace, and let `act` say why.
        setBahtWanted(null);
        throw e;
      }
    });
  };

  const stopCapture = () => {
    setShotAt(null);   // nothing is in flight to narrate after a deliberate stop
    act(() => api.post("/api/capture/stop"));
  };
  // A preset tap while a loop is running RESTARTS the loop: hub.start_loop
  // closes over the exposure it was handed, so a tap that only moved a
  // highlight would leave the loop shooting the old length forever — a control
  // that looks applied and is ignored.
  //
  // Which is exactly what the blocked branch used to do. `if (!looping ||
  // captureReason) return` dropped the restart AFTER the box had already been
  // rewritten, so over a loop the rig would not restart — read-only, a sequence,
  // a sweep, a half-typed gain — the preset lit up and reported `aria-pressed`
  // for an exposure the camera was not using. So: decide first, and only claim
  // the highlight when the restart is actually issued.
  const applyPreset = (s: number) => {
    if (presetReason) { showToast("warning", presetReason); return; }
    const was = capExposure;
    const wasWanted = loopWanted;
    setCapExposure(String(s));
    if (!looping) return;
    // The restart is issued, so this IS what the loop is shooting from here on;
    // the frame still in flight carries the old length, and a highlight that
    // waited for the rig's next frame would sit on the previous exposure for
    // seconds after a deliberate tap.
    setLoopWanted(s);
    void api.post("/api/capture/loop", focusCaptureBody({
      exposureS: s, gain: capGainNum, binning: Number(capBin) || 1,
    })).catch((e: Error) => {
      // The loop is still shooting the old length, so the highlight goes back
      // to saying so — a lit preset over a running loop is a claim about the
      // camera, not about what is typed in a box.
      setCapExposure(was);
      setLoopWanted(wasWanted);
      showToast("error", e.message);
    });
  };

  const moveTo = async (p: number) => {
    // Belt and braces — the nudges, the dial and Go are all locked while a move
    // is in flight (focuserReason below), but a second one must not get through
    // here either. The server refuses it: /api/focuser/move `_spawn`s the
    // `focuser` lane without replace=True, so a second POST is a 409 — and that
    // 409 used to land in the catch below and clear `cmd`, throwing away the
    // FIRST move's target, its distance-to-go and its stall clock. A tap that
    // changed nothing on the rig blinded the one narrator watching the move
    // that WAS happening.
    if (waiting) { showToast("warning", MOVE_IN_FLIGHT_REASON); return; }
    // …and the window `waiting` cannot cover: from here to the server's answer
    // there is no `cmd` yet, so nothing above would stop a second tap. Read the
    // REF, not the state, so two taps inside one React batch cannot both pass.
    if (sendingRef.current) { showToast("warning", MOVE_SENDING_REASON); return; }
    const target = clampPos(p);
    const from = pos;
    sendingRef.current = true;
    setSending(target);
    try {
      await api.post("/api/focuser/move", { position: target });
    } catch (e) {
      // Nothing is claimed: the command never landed, so there is no move to
      // narrate — drawing "→ 22000" for it would be the failure this narrator
      // was written to remove, pointing the other way.
      showToast("error", (e as Error).message);
      return;
    } finally {
      // In the catch's path too: a refused move must hand the buttons straight
      // back, not leave them locked behind a request that is over.
      sendingRef.current = false;
      setSending(null);
    }
    // Armed only once the server has ACCEPTED it, which is also the ordering the
    // shutter above had to learn (a refused Single drew a full exposing →
    // downloading cycle for a frame that never existed).
    setCmd({ target, startedAt: Date.now(), from });
    setNow(Date.now());
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
      : sweeping ? "Autofocus is running — let the sweep finish first"
        // One move at a time, because the server allows exactly one: a second
        // POST is a 409 off the `focuser` lane, and the tap that earns it costs
        // the narration of the move already under way (see moveTo).
        : waiting ? MOVE_IN_FLIGHT_REASON
          // The same 409, in the window before the rig has answered the first
          // one at all. Stated rather than merely refused, so the tap is
          // visible while the round trip is open.
          : sending != null ? MOVE_SENDING_REASON
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
    autofocusRunning: sweeping, exposureInvalid: capExposureInvalid,
    gainInvalid: capGainInvalid, gainMax: cam?.max_gain ?? null,
  });
  const singleReason =
    captureReason ?? (looping ? "A capture loop is running — press Stop first" : null);
  // Tapping an exposure preset. Deliberately NOT `captureReason`: the preset is
  // the repair for an empty exposure box — it writes a number into it — so
  // refusing the tap for the very state it fixes would be a trap, which is why
  // `exposureInvalid` is dropped here. The gain box is not dropped: its value
  // rides in the restart's body, and a NaN gain reaches the wire as `null`.
  // When no loop is running there is nothing to restart and nothing to refuse,
  // beyond the read-only session that already makes the box itself read-only.
  const presetReason =
    readOnlyReason
    ?? (looping
      ? focusCaptureBlocker({
        readOnlyReason, hasCamera: !!cam, polarBusy, sequenceOwnsCamera: seqOwnsCamera,
        autofocusRunning: sweeping, exposureInvalid: false,
        gainInvalid: capGainInvalid, gainMax: cam?.max_gain ?? null,
      })
      : null);
  // "Find focus roughly first" walks the travel EXPOSING at every stop, so the
  // route refuses it for the same reasons Single is refused — coarse_focus 409s
  // on `engine.running or hub.looping` and requires a camera (api/app.py) — on
  // top of the focuser gate every other control here shares. It deliberately
  // does NOT inherit two of the hero's blockers: the Camera panel's exposure and
  // gain boxes (it posts no parameters at all; the server walks at its own
  // 6s / gain 300 / bin 2, focus/coarse.py), and `afReady.block`, which refuses
  // a sweep when nothing has been measured. Coarse is the way OUT of having no
  // measurable frame — blocking it for that would close the last door in the
  // room, which is the loop this button was added to break.
  const coarseReason =
    focuserReason
    ?? focusCaptureBlocker({
      readOnlyReason, hasCamera: !!cam, polarBusy, sequenceOwnsCamera: seqOwnsCamera,
      autofocusRunning: sweeping, exposureInvalid: false, gainInvalid: false,
    })
    ?? (looping ? "A capture loop is running — press Stop first" : null);
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

  // The sweep's gate and its tap, hoisted OUT of the Autofocus panel's IIFE.
  // The #125 pod offers an AF chip over the preview, and the one thing that
  // must never happen is two entry points that compute their own gate or build
  // their own request body — that is how a shortcut becomes "a second control
  // with its own behaviour", which this file already deleted once. One state
  // object, one handler, two places to press it.
  const afButton = focusButtonState({
    // `sweeping`, not `running`: a second sweep is a 409 off the `autofocus`
    // lane, and a tab opened mid-run has no `focus` event to know that.
    canFocus, hasFocuser: !!foc, running: sweeping, sweepBlock: afReady.block,
  });
  const runAutofocus = () => act(() => api.post("/api/focuser/autofocus", {
    ...afParams,
    ...(afAdvanced && afFilter !== "" ? { filter: Number(afFilter) } : {}),
  }));

  // The sweep's own settings, as the SHARED camera pickers (2026-08-07): the
  // camera behind a focus frame is the camera behind a solve frame, so the
  // control is the same control (components/ui/CameraPickers). This is what
  // makes the overexposure fix ("shorter exposure than 3s, less gain than
  // 200") actionable in two taps at the scope instead of a settings dig.
  //
  // A pick edits the SAME advanced state the typed fields edit — and OPENS
  // the panel first (seeded from the derived values, exactly as the gear
  // does), because values in a closed panel are not sent and a picker that
  // changed nothing would be the looks-applied-but-ignored control this file
  // already deleted once.
  const afOpenAdvanced = () => {
    if (afAdvanced) return;
    setAfExposure(String(afDerived.exposure_s));
    setAfGain(String(afDerived.gain));
    setAfStep(String(afDerived.step));
    setAfBin(String(afDerived.binning));
    setAfAdvanced(true);
  };
  const afSet = (kind: "exposure" | "gain" | "bin", value: number) => {
    afOpenAdvanced();
    if (kind === "exposure") setAfExposure(String(value));
    else if (kind === "gain") setAfGain(String(value));
    else setAfBin(String(value));
  };

  // The dial over the preview edits exactly what the panel's pickers edit —
  // one builder, one set of presets, two places to reach them.
  const afDialCategories = cameraDialCategories({
    values: {
      exposure_s: afParams.exposure_s, gain: afParams.gain,
      binning: afParams.binning, offset: 30,
      filter: afFilter === "" ? null : filterNames[Number(afFilter)] ?? null,
    },
    maxBin: afMaxBin,
    filters: filterNames.filter(Boolean),
    onExposure: (s) => afSet("exposure", s),
    onGain: (g) => afSet("gain", g),
    onBinning: (b) => afSet("bin", b),
    onFilter: (name) => {
      afOpenAdvanced();
      const i = name == null ? -1 : filterNames.indexOf(name);
      setAfFilter(i >= 0 ? String(i) : "");
    },
  });

  // The sweep's live narration off the focus bus slice (additive fields the
  // server publishes since 2026-08-07): what THIS point is doing, and which
  // point of how many. Cast, not typed — PolarView does the same for its
  // additive native fields.
  const fLive = focus as (typeof focus & {
    activity?: "exposing" | "measuring" | null;
    point_index?: number | null; points_planned?: number; exposure_s?: number;
  }) | null;

  // The SAME classifier FocusVerdict runs on the same frame, hoisted so the pod
  // is gated by it too. Handing the pod the HFR was never enough to keep the
  // corner and the header from disagreeing: past detect_stars' 15px box the
  // header prints NO number, because the one it has is the box's ceiling.
  const shownFocus = focusState(shown as {
    hfr?: number | null; stars?: number | null; defocus_r80?: number | null;
  });
  // How far through the in-flight single exposure we are, for the pod's ring.
  // Derived from the SAME `now` the move narrator and the exposure narrator
  // already advance — joining that one interval rather than starting a second
  // that would tick out of phase and draw a ring disagreeing with the countdown
  // printed under the Camera panel. 1s granularity on purpose: that is how
  // often `now` moves, and how often the sentence beside it changes.
  const exposureProgress = shotAt == null || capExposureS <= 0
    ? null
    : Math.min(1, Math.max(0, (now - shotAt) / (capExposureS * 1000)));
  const sweepNote = sweepPreviewNote({
    running, pointsMeasured: focus?.points?.length ?? 0, framesSinceStart: sweepFrames,
    // A loop started from the Capture screen keeps running through a sweep (the
    // server refuses a loop only for polar and sequences), so frames CAN land
    // here that are not the sweep's. Say whose they are rather than let them
    // stand in as proof the sweep is delivering.
    loopRunning: looping,
  });

  return (
    <>
      {/* SAY WHO HAS THE CAMERA, at the top, before anything is greyed out.
          A run holds the camera between frames as well as during them, so
          arriving here mid-run finds every control dead with no explanation on
          screen — the reason lived only in a disabled button's tooltip, which
          never fires on the tablet this is used from. The run is not stopped
          automatically: it is collecting the data of the night, and that is the
          operator's call to make deliberately, from the screen that owns it. */}
      {seqOwnsCamera && (
        <div className="panel p-3 mb-4 flex items-start gap-2 border-warn/60" role="status">
          <Led state="busy" label="sequence running" />
          <div className="min-w-0">
            <p className="text-warn text-sm font-medium">
              A run has the camera{sequence.state === "paused" ? " (paused between frames)" : ""}
            </p>
            <p className="text-dim text-xs mt-0.5">
              Focusing needs the camera to itself, so these controls stay locked
              until the run stops. Stop or abort it on the Plan screen, then come
              back — nothing here will interrupt it for you.
            </p>
          </div>
        </div>
      )}
      {!seqOwnsCamera && looping && (
        <div className="panel p-3 mb-4 flex items-start gap-2" role="status">
          <Led state="on" label="live loop running" />
          <p className="text-dim text-xs">
            A live loop is running and feeding this preview. Taking a single
            frame or starting autofocus takes the camera over and stops it.
          </p>
        </div>
      )}
    <div className="grid gap-4 md:grid-cols-[1fr_300px]">
      <div className="flex flex-col gap-4">
        {/* live preview so manual focus is not blind (spec §10) */}
        <Panel title="Live Preview" right={<FocusVerdict preview={shown} prev={prevFrame} hfrGood={hfrGood} hfrWarn={hfrWarn} />}>
          <div className="flex flex-col gap-2">
            {/* #125. The pod is a SIBLING of the stage, held together by this
                wrapper — never a descendant. usePreviewGestures binds its
                listeners imperatively to the stage root and honours the
                [data-no-pan] escape hatch ONLY in onPointerDown
                (usePreviewGestures.ts:112-113): the wheel handler calls
                preventDefault() unconditionally and the double-tap Fit/100%
                accelerator has no check at all, so a chip inside the stage
                would zoom the image under a scroll wheel and flip the zoom on a
                double tap. (PreviewStage takes no `children` prop either.) */}
            <div className="relative">
              <PreviewStage
                compact
                // The stage has to be tall enough to hold the pod's arc: at 3:2
                // a 390px phone gives 326×217 and the arc needs 230, and there
                // is no clipping between here and the Panel header, so a short
                // stage puts the two most-pressed chips over FocusVerdict. The
                // floor only bites below ~410px of viewport; above that 3:2
                // already gives more and the ratio wins as before.
                minHeight={POD_MIN_STAGE_H}
                // …and the stage cannot see the pod that sits on top of it, so
                // the strip its disc owns along the bottom edge is declared
                // here. The magnifier takes the TOP-right of a compact stage
                // and is sized to stop above that strip, which is what lets
                // both exist on a 390px phone at all.
                bottomRightReserve={POD_BOTTOM_PX + POD_DISC_PX}
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
                onControls={(c) => {
                  focusControls.current = c;
                  setLoupe((p) =>
                    p.on === c.loupeOn && p.available === c.loupeAvailable
                      ? p
                      : { on: c.loupeOn, available: c.loupeAvailable },
                  );
                }}
              />
              {/* Every prop below is state or a handler this screen ALREADY
                  owns. The pod adds no capability — it is the same shutter, the
                  same nudges, the same sweep, moved to where the thumb is while
                  the eye is on the frame. Where it is blocked it is blocked by
                  the same sentence the panel prints, because it is handed the
                  same sentence. */}
              <FocusPod
                hfr={shown?.hfr ?? null}
                prevHfr={prevFrame?.hfr ?? null}
                hfrState={shownFocus.kind}
                exposureProgress={exposureProgress}
                exposureNote={frameWait?.text ?? null}
                exposureNoteTone={frameWait?.tone ?? null}
                captureBlocked={captureReason}
                // The exposure the CAMERA is using, not the one in the box —
                // the badge sits over the picture, which makes it the most
                // load-bearing claim on the screen. null = nothing can be
                // claimed (the box is empty or not a number, or a loop is
                // running whose first frame has not landed); the badge then
                // reads "—" and its first tap writes a real preset into the box,
                // which is also the repair for the blocker that state causes.
                exposureS={shownExposureS}
                exposurePresets={FOCUS_EXPOSURE_PRESETS}
                stepValues={STEP_VALUES}
                onExposure={applyPreset}
                exposureReason={presetReason}
                onStep={setStep}
                looping={looping}
                starting={capPending}
                // `exposing`, not `shotAt != null`: past the readout grace the
                // frame is gone and the chip must hand the shutter back. The
                // ring keeps its `stalled` signature (it reads `shotAt` through
                // exposureProgress above) so the fault stays visible.
                exposing={exposing}
                step={step}
                shootReason={singleReason}
                loopReason={captureReason}
                stopReason={readOnlyReason}
                focuserReason={focuserReason}
                autofocusReason={afButton.reason}
                onShoot={() => void shoot("single", "/api/capture")}
                onLoop={() => void shoot("loop", "/api/capture/loop")}
                onStop={stopCapture}
                onNudge={(d) => moveTo(pos + d)}
                onAutofocus={runAutofocus}
              />
              {/* THE SWEEP'S CAMERA SETTINGS, over the frame they produce
                  (2026-08-08). The same fan-out dial the Align reticle
                  carries, fed by the same builder — tap the disc, the
                  categories bloom, tap one and its values replace them;
                  offset is a field because its useful values are a continuum.
                  Parked on the LEFT so it cannot collide with the FocusPod
                  disc in the lower-right corner. */}
              {canFocus && afReady.basis !== "backend" && (
                <CameraDial
                  label="Sweep settings"
                  summary={`${afParams.exposure_s}s g${afParams.gain}`}
                  side="left"
                  right={12}
                  bottom={38}
                  categories={afDialCategories}
                />
              )}
            </div>
            <div className="flex items-center gap-1 preview-toolbar">
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom out" onClick={() => focusControls.current?.zoomOut()}>−</button>
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom in" onClick={() => focusControls.current?.zoomIn()}>+</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.fit()}>Fit</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.hundred()}>100%</button>
              {/* THE MAGNIFIER, on the screen it is for. This row had −, +, Fit
                  and 100% and nothing else, so the sensor-1:1 view — the only
                  honest read on whether a star is actually sharp, since "100%"
                  here is 100% of a preview downscaled to 1400px — existed on
                  Capture and not on Focus. Same toggle, same state (it lives in
                  PreviewStage), and honest-disabled with its reason when the
                  frame carries no linear data to crop. */}
              {loupe.available ? (
                <button
                  type="button"
                  aria-pressed={loupe.on}
                  title="Magnifier — real sensor pixels at the centre of the view (the true focus check; 1:1)"
                  onClick={() => focusControls.current?.setLoupeOn(!loupe.on)}
                  className={`btn !px-2.5 min-h-11 inline-flex items-center gap-1 !text-[11px] ${loupe.on ? "btn-accent" : ""}`}
                >
                  <Icon name={loupe.on ? "check" : "focus"} size={12} />
                  Magnifier
                </button>
              ) : (
                <LockedChip
                  reason="The magnifier needs linear data — this frame came from NINA already stretched."
                  className="btn !px-2.5 !text-[11px]"
                >
                  Magnifier
                </LockedChip>
              )}
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
              button was simply dim and mute. LockedChip speaks it.
              The pending branch is #46: arming ALSO starts a live loop, and the
              flag that flips this label only arrives on the next 2s status
              frame, so the tap looked ignored for two seconds and a second
              finger sent /stop to a rig that was still arming. */}
          {bahtWanted != null ? (
            <button className="btn w-full tap min-h-11 mt-3" aria-disabled aria-busy
              aria-label={bahtWanted
                ? "Waiting for the rig to arm the Bahtinov aid — it starts a live loop first"
                : "Waiting for the rig to confirm the Bahtinov aid is off"}>
              {bahtWanted ? "Arming…" : "Stopping…"}
            </button>
          ) : (
            <button
              className={`btn w-full tap min-h-11 mt-3 ${!canFocus ? "opacity-40" : ""} ${bahtOn ? "btn-accent" : ""}`}
              aria-disabled={!canFocus || undefined}
              aria-pressed={bahtOn}
              aria-label={readOnlyReason
                ? `${bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"} — ${readOnlyReason}`
                : undefined}
              onClick={!canFocus ? undefined : () => toggleBahtinov(!bahtOn)}
            >
              {bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"}
            </button>
          )}
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

      {/* ORDERED BY WHAT YOU TOUCH WHILE WATCHING THE IMAGE, not by narrative.
          The DOM order (Result, Camera, Autofocus, Focuser) put the focuser
          nudges fourth — below the fold on a 900px-tall screen — so the two
          controls you actually alternate between, "take another frame" and
          "move the focuser", could not both be on screen with the picture they
          change. Focusing is a loop: shoot, look, nudge, repeat.
          Camera then Focuser now sit beside the preview; Autofocus and the
          Result read-out follow, because those you consult rather than drive.
          Done with flex `order` so the reading order in this file still matches
          the F5 design reference. */}
      <div className="flex flex-col gap-4">
        <Panel title="Result" className="order-4" right={<ProviderBadge cap="autofocus" />}>
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
        <Panel title="Camera" className="order-1" right={!canFocus && <ReadOnlyBadge />}>
          {readOnlyReason && <LockedNote reason={readOnlyReason} className="mb-3" />}
          <div className="grid grid-cols-5 gap-1 mb-3">
            {FOCUS_EXPOSURE_PRESETS.map((s) => {
              // `shownExposureS`, not the box: while a loop runs, a filled,
              // aria-pressed preset is a claim about the camera (see the block
              // beside `loopWanted`). When nothing can be claimed — a loop
              // running whose first frame has not landed — NONE of the five
              // lights up, which is the honest answer to "what is it shooting?".
              const on = shownExposureS != null && shownExposureS === s;
              // The hint changes with the loop, and the difference is the whole
              // point: "use it for the next frame" vs "restart what is running".
              const pa = presetAction(looping, s);
              // Dimmed + aria-disabled + a spoken reason rather than the native
              // attribute, and a toast on tap, exactly as the thumb row's
              // nudges do it (StepRow) — five 44px cells have no room for a
              // lock glyph each, but they still have to say why.
              return (
                <button
                  key={s}
                  type="button"
                  className={`btn tap min-h-[44px] !px-0 justify-center mono text-[11px] ${
                    presetReason ? "opacity-40" : ""} ${on ? "btn-accent border-accent" : ""}`}
                  aria-disabled={presetReason ? true : undefined}
                  aria-pressed={on}
                  aria-label={presetReason
                    ? `${s} second exposure — unavailable: ${presetReason}`
                    : `${s} second exposure — ${pa.hint}`}
                  title={presetReason ?? pa.hint}
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
            ) : exposing ? (
              // `exposing`, not `shotAt != null` — see `frameLost` above. This
              // branch used to have no exit but a frame or a deliberate Stop, so
              // a dropped frame left it here all night with aria-pressed on an
              // inert button, while the warn line two rows down already said the
              // camera had lost it.
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
        <Panel title="Autofocus" className="order-3" right={!canFocus && <ReadOnlyBadge />}>
          {(() => {
            // The sweep is blocked when nothing has been measured for it to
            // copy (lib/focusCapture sweepReadiness) — ranked below permission
            // and hardware, because it is the smallest of the three facts.
            //
            // Both of these are computed ONCE at component scope now (see the
            // hoist above `frameWait`), because the #125 pod presses the same
            // sweep from over the preview. Two call sites that each built their
            // own gate and their own request body is exactly the drift this
            // screen has already been burned by.
            const bs = afButton;
            // `afParams` is built once at render (above) and is the SAME object
            // the summary line prints, so the sentence under the button and the
            // request that goes out cannot drift apart. The filter rides along
            // only when the user picked one in the open settings panel.
            const onTap = runAutofocus;
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
                {/* The sweep narrating itself: which point, doing what, with
                    the exposure's own fill clock. A point is exposure + 2-4 s
                    of measurement, and a chart that only grows every ~8 s
                    reads as hung in between. */}
                {sweeping && fLive?.activity && (
                  <div className="flex items-center gap-3 mb-2" data-af-activity>
                    <ActivityRing
                      mode={fLive.activity === "exposing" ? "fill" : "orbit"}
                      seconds={fLive.exposure_s ?? afParams.exposure_s}
                      word={fLive.activity === "exposing" ? "capturing" : "measuring"}
                      resetKey={`${fLive.activity}-${fLive.point_index ?? "probe"}`}
                    />
                    <span className="text-[11px] tracking-widest uppercase text-dim"
                      aria-live="polite">
                      {fLive.point_index != null
                        ? `point ${fLive.point_index + 1} of ~${fLive.points_planned ?? 9}`
                        : "checking the field"}
                    </span>
                  </div>
                )}
                {/* Speed dials for the sweep itself — the two-tap path to
                    "shorter exposure, less gain" when a sweep clips, and to
                    "longer exposure, bin 1" when a field is thin. Hidden for a
                    backend (NINA) sweep, which ignores every parameter. */}
                {/* The flat pickers stay on the PANEL (a settled list, read at
                    a glance); the same settings are also on the fan-out dial
                    over the preview, which is where a thumb is while focusing.
                    One builder feeds both — see components/ui/CameraPickers. */}
                {afReady.basis !== "backend" && canFocus && (
                  <div className={`grid gap-1.5 mb-2 ${filterNames.length > 0 ? "grid-cols-4" : "grid-cols-3"}`}
                    role="group" aria-label="sweep settings" data-af-dials>
                    <ExposurePicker
                      value={afParams.exposure_s}
                      className="w-full !justify-center"
                      onPick={(s) => afSet("exposure", s)}
                    />
                    <GainPicker
                      value={afParams.gain}
                      className="w-full !justify-center"
                      onPick={(g) => afSet("gain", g)}
                    />
                    <BinningPicker
                      value={afParams.binning}
                      max={afMaxBin}
                      className="w-full !justify-center"
                      onPick={(b) => afSet("bin", b)}
                    />
                    {/* The wheel is shared with every other frame-shooting
                        screen, so it gets the shared picker too: the face
                        names the filter the sweep will run through, never an
                        abstraction. */}
                    <FilterPicker
                      value={afFilter === "" ? null
                        : filterNames[Number(afFilter)] ?? null}
                      align="right"
                      className="w-full !justify-center"
                      onPick={(name) => {
                        afOpenAdvanced();
                        const i = name == null ? -1 : filterNames.indexOf(name);
                        setAfFilter(i >= 0 ? String(i) : "");
                      }}
                    />
                  </div>
                )}
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
                {/* This was the file's last native `disabled`, and it collapsed
                    three blockers into a grey rectangle while missing the two
                    that bite: a capture loop and a running sequence both 409 the
                    route, and against either of those the button looked fully
                    live and bought a red toast. `coarseReason` (above) is the
                    same sentence chain every other control here uses. */}
                {coarseReason ? (
                  <LockedChip reason={coarseReason}
                    className="btn w-full tap min-h-[44px] mb-3 text-[11px] justify-center">
                    Find focus roughly first
                  </LockedChip>
                ) : (
                  <button
                    className="btn w-full tap min-h-[44px] mb-3 text-[11px]"
                    title="Step across the focuser's travel and stop where there are enough stars to autofocus"
                    onClick={() => act(() => api.post("/api/focuser/coarse", {}))}
                  >
                    Find focus roughly first
                  </button>
                )}
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

        <Panel title="Focuser" className="order-2" right={!canFocus && <ReadOnlyBadge />}>
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
                onClick={() => act(async () => {
                  await api.post("/api/focuser/halt");
                  // Only once the rig has taken it. After a deliberate Halt,
                  // "not moving — stopped at X" is technically true but reads as
                  // a fault report for something the user just did — so it goes.
                  // A halt LOST to wifi is the opposite case and used to look
                  // identical: clearing first threw away the target, the
                  // distance-to-go and the stall clock for a move that was still
                  // running, leaving nothing on screen until the error toast
                  // arrived up to 15s later (api.ts timeout).
                  setCmd(null);
                })}>
                <Icon name="stop" size={13} className="shrink-0 fill-current" aria-hidden />
                Halt
              </button>
            )}
          </div>
          {/* The move, narrated. `aria-live` because the whole point is that
              something changed without the user touching anything.

              The `sending` branch covers the round trip, where there is no
              `cmd` to narrate yet: without it the tap produced nothing at all
              until the rig answered, and the ellipsis is doing real work — it
              says the rig has not agreed to anything, which is exactly the
              claim "→ 12100" would make too early. */}
          {progress ? (
            <p role="status" aria-live="polite"
              className={`mono text-[11px] mt-2.5 ${
                progress.tone === "warn" ? "text-warn"
                  : progress.tone === "good" ? "text-good" : "text-accent"}`}>
              {progress.text}
            </p>
          ) : sending != null ? (
            <p role="status" aria-live="polite" aria-busy
              className="mono text-[11px] mt-2.5 text-dim">
              sending {sending}…
            </p>
          ) : null}

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
    </>
  );
}
