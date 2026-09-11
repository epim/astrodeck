// focuser.tsx - the FOCUSER device sheet (plan hub-rig.md B.5, deviations
// E3-E6). Route `#/rig/devices/focuser`.
//
// WHAT THIS SHEET IS FOR. Everything about the drawtube that is not the live
// picture: where it is, how to move it, what a sweep would do before you press
// it, what the last sweep decided, and the two rig-level rules that make the
// engine refocus on its own. The picture itself, the pod over it and the
// Bahtinov SPIKES overlay live on Rig - Capture, because they need
// `PreviewStage`'s shared transform; this sheet owns the Bahtinov VERDICT (the
// number you read standing at the scope) and the start/stop that arms it.
//
// FOUR SEAMS TRANSCRIBED RATHER THAN REWRITTEN, each of which was a bug once:
//
//   clampPos           `views/FocusView.tsx:307-317` - a negative or past-max
//                      target is a user error worth catching before the trip.
//   sendingRef         `:330-338` - a plain useState cannot close the window
//                      between the tap and the server's answer, so two taps in
//                      one React batch both reach the rig and the second is a
//                      409 off the `focuser` lane, which throws away the FIRST
//                      move's target and its stall clock.
//   moveProgress /     `lib/focusMove.ts` - "pressing Go looked exactly like
//   retireAfterMs      not pressing Go" (2026-07-31), and a finished command
//                      left lying around narrates the ENGINE's next move as the
//                      user's stale Go.
//   deriveAutofocusParams  `:731-747` - ONE object builds the summary line and
//                      the request, so what is printed before the tap cannot
//                      differ from what is sent by it.
//   the shutter        `views/FocusView.tsx:498-519, 556-559, 1291-1355` - SINGLE
//                      / LOOP / STOP at the `focus` frame scope, verbatim:
//                        `POST /api/capture`      focusCaptureBody({exposureS, gain, binning})
//                        `POST /api/capture/loop` the same body
//                        `POST /api/capture/stop` no body
//                      with `singleReason = captureReason ?? (looping ? "A
//                      capture loop is running - press Stop first" : null)` and a
//                      STOP that is gated on the ACCESS FLOOR ONLY, never on a
//                      lane - it is the only way to end a loop, and gating it on
//                      the lane it exists to end is the bug the inventory records
//                      three times. Without this row the focus exposure, gain and
//                      binning could only reach a live frame by accident (r4 #11):
//                      `focusCaptureBody` appeared once, inside `applyPreset`,
//                      behind `if (!looping) return`, so it could only RESTART a
//                      loop somebody else had started from Rig - Capture at the
//                      `capture` scope, while AF_SETTINGS_NOTE and the
//                      `captureBlocked` chain both pointed at a Single that was
//                      not there.
//
// DEVIATIONS FROM THE DESIGN (plan E4-E6), all of the same shape - the design
// draws a control for a verb the engine does not have:
//
//   E4  "on filter change" is not a refocus rule: the engine SHIFTS the focuser
//       by the filter's stored offset instead, so the control is that switch.
//   E5/E6  "every 60 min" is a per-PLAN frame count and "HFR +15%" is a frame-
//       QUALITY gate on the Safety sheet. Both render as read-only rows that
//       tap through to where they are actually edited, because a chip that
//       cannot be toggled here would be a lie about what this screen owns.
//
// E3 IS CLOSED, AND WHAT REPLACED IT (D-RIG-2, task T-U7b-5). This sheet used
// to OMIT the design's TEMPERATURE COMPENSATION toggle, and its test asserted
// the string's absence, because no such field, coefficient or loop existed
// anywhere in the server: the only temperature-driven behaviour was
// `standards.refocus_on_temp_delta_c`, a refocus TRIGGER. The engine now has
// the offset loop too (`server/astrodeck/focus/tempcomp.py`, on the bus at
// `focuser.temp_comp`, written through `POST /api/config {focus}`), so the
// block is here - and the two live one card apart on purpose, with
// `TEMP_COMP_PRECEDENCE` quoted between them, because they sound like the same
// setting and are not: one nudges between frames for the cost of a move, the
// other stops and spends minutes re-measuring, and BOTH run.
//
// The sign is the part of this block that costs a night if it is wrong, so the
// rule is written out beside the number instead of being inferred from a minus
// sign - see `lib/tempComp.ts`, which owns every sentence in the block.

import {
  useCallback, useEffect, useMemo, useRef, useState, type JSX, type ReactNode,
} from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import {
  ActionButton, BannerCard, Card, Chip, Disclosure, EmptyCard, Field, Label,
  ListRow, Mono, NumberField, ReadoutGrid, ReadoutTile, Sheet, Stepper2, Switch,
  TextInput,
} from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { api } from "../../../../api";
import {
  clearProfileOverrides, listDrivers, setFocusConfig, setStandardsConfig,
} from "../../../../api/backends";
import { accessPhrase, useCanConfigBackend, useCanControlCapture }
  from "../../../../lib/caps";
import { useBusy } from "../../../../lib/useBusy";
import {
  deriveAutofocusParams, focusButtonState, readFocusFailure,
} from "../../../../lib/autofocus";
import {
  FOCUS_EXPOSURE_PRESETS, FRAME_READOUT_GRACE_MS, focusCaptureBody,
  sweepPreviewNote, sweepReadiness,
} from "../../../../lib/focusCapture";
// The polar sentence and its two-channel test, shared with Rig - Capture rather
// than re-spelled here: one alignment, one string (r4 #25).
import { POLAR_REASON, isPolarBusy } from "../capture/captureGate";
import {
  MOVE_IN_FLIGHT_REASON, MOVE_SENDING_REASON, anchorBlocker, moveProgress,
  retireAfterMs, type FocuserCommand,
} from "../../../../lib/focusMove";
import { defocusMessage, focusState } from "../../../../lib/focusVerdict";
import { standardsOrDefault } from "../../../../lib/standards";
import { entryOf, isProfileOverride, providerKey, valueOf } from "../../../../lib/effective";
import { writeProviderOverride } from "../../../../lib/providerSave";
import { providerWriteNote, providerWriteTarget } from "../../../../lib/providerWrite";
import { eligibleTaskDrivers } from "../../../../lib/equipment";
import { VCurve, type FocusFit } from "../../../../components/graphs";
// The rebuilt sweep verdict (T-R7-19). The legacy `components/preview/
// FocusVerdict` is untouched and still serves `#/classic`; importing the area
// ROOT rather than the file is what carries `inspect.css` with it.
import { AutofocusLine } from "../inspect";
import {
  DOUBLES_IT_NOTE, NO_FOCUS_BLOCK_REASON, NO_THERMOMETER_REASON,
  TEMP_COMP_PRECEDENCE, effectSentence, nextMoveTile, referenceLine,
  signSentence,
} from "../lib/tempComp";
import { bahtinovAid } from "../../../../lib/bahtinov";
import {
  useConfig, useFocus, useFrameSettings, useHfrThresholds,
  useLastAutofocusResult, useLivePreview, usePlan, usePolar, usePreviews,
  useProviders, useSequence, useStatus, useStore,
} from "../../../../store";
import type { DriverInfo, TempCompConfig } from "../../../../types";

/** The aid arms server-side immediately but `status.bahtinov_active` only
 *  arrives on the next 2 s status frame, so the button renders an inert face
 *  meanwhile (`views/FocusView.tsx:80-85`). It EXPIRES for the same reason
 *  `useBusyOrPending` does: a request that never took effect (a 403, a dropped
 *  connection, the aid disarmed from another client) must not leave the control
 *  dead. */
export const BAHTINOV_PENDING_GRACE_MS = 6000;

/** Flow ownership (plan section 0.5), adapted from `FocusView.tsx:904-918` with
 *  the "Plan screen" pointer repointed at the new IA. */
export const FLOW_OWNS_CAMERA =
  "A run has the camera. Focusing needs the camera to itself, so these controls "
  + "stay locked until the run stops. Stop or pause it on Session - Now; nothing "
  + "here will interrupt it for you.";
export const FLOW_OWNS_CAMERA_PAUSED =
  "A run has the camera (paused between frames). Focusing needs the camera to "
  + "itself, so these controls stay locked until the run stops. Stop or pause it "
  + "on Session - Now; nothing here will interrupt it for you.";
export const LOOP_OWNS_CAMERA =
  "A live loop is running and feeding this preview. Taking a single frame or "
  + "starting autofocus takes the camera over and stops it.";

/** The footer, verbatim from the design fragment (`17-device-focuser.html`). */
export const FOCUSER_FOOTER =
  "Per-filter offsets from the wheel apply on every filter change. Manual steps "
  + "are for a quick check between targets; during capture they wait for the "
  + "frame boundary.";

/** The two rules this sheet DOES own, in the words StandardsPanel already uses
 *  for the first (`components/settings/StandardsPanel.tsx:113-116`). */
export const FILTER_OFFSET_NOTE =
  "Shift the focuser by the filter's stored offset when the wheel moves, so a "
  + "filter change does not cost a refocus.";

/** The three numbers this sheet shoots at, and where the frames go. Focus
 *  frames are diagnostics: `focusCaptureBody` sends `save: false` and it is not
 *  a toggle, because `hub.start_loop` has no save parameter at all. */
export const FOCUS_FRAME_NOTE =
  "Single and Loop shoot at the exposure, gain and binning in AUTOFOCUS "
  + "SETTINGS below - the same three numbers the sweep copies. Focus frames are "
  + "not saved to the library; they land on the live stage on Rig - Capture, "
  + "where a tap opens the frame for a closer look.";

export const LOOP_RUNNING_REASON = "A capture loop is running - press STOP first";
export const LOOP_ALREADY_REASON = "The loop is already running - press STOP to end it";
export const SHUTTER_STARTING_REASON =
  "Waiting for the camera to accept this exposure - no frame has started yet";

/** What to say between the tap and the frame arriving.
 *
 *  `POST /api/capture` returns the moment the task is spawned - before the
 *  shutter opens - so without this, pressing SINGLE looks exactly like not
 *  pressing it for the length of the exposure. That is the failure the Go button
 *  one card up was filed for, on the control beside it.
 *
 *  Transcribed from `lib/focusCapture.ts:152-175` `frameWaitNote` rather than
 *  imported, for one reason: all three of its sentences carry em-dashes, and
 *  ARCHITECTURE non-negotiable 5 is "hyphens, never em-dashes, in UI strings".
 *  Same three states, the same `FRAME_READOUT_GRACE_MS`, and the same refusal
 *  that prints BOTH numbers - "no frame" is not actionable, "no frame in 74 s
 *  for a 4 s exposure" points at the camera link. */
export function shutterWait(p: {
  /** ms epoch when the server ACCEPTED the exposure, or null. */
  startedAt: number | null;
  exposureS: number;
  now: number;
}): { text: string; tone: "dim" | "warn" } | null {
  if (p.startedAt == null) return null;
  const elapsedMs = Math.max(0, p.now - p.startedAt);
  const exposureMs = Math.max(0, p.exposureS * 1000);
  if (elapsedMs < exposureMs) {
    return { text: `exposing · ${Math.ceil((exposureMs - elapsedMs) / 1000)} s left`, tone: "dim" };
  }
  if (elapsedMs < exposureMs + FRAME_READOUT_GRACE_MS) {
    return { text: "reading out…", tone: "dim" };
  }
  return {
    text: `no frame in ${Math.round(elapsedMs / 1000)} s for a ${p.exposureS} s exposure `
      + "- the camera may have dropped it",
    tone: "warn",
  };
}

export const AF_SETTINGS_NOTE =
  "Focus frames are not saved. Autofocus sweeps at this gain and binning, and at "
  + "this exposure too once a frame shows stars - so press SINGLE above and check "
  + "the frame before you sweep.";

export const ANCHOR_NOTE =
  "Tells the focuser it is at this number. Nothing moves. Use it when the count "
  + "has been lost: put the drawtube somewhere you know first, then say where "
  + "that is.";

export const BAHTINOV_HINT =
  "Put a Bahtinov mask on the scope and point at a bright star, then watch the "
  + "middle spike offset drop to zero.";

const NBSP_THIN = " ";

/** 14318 -> "14 318". The design's own formatting (`logic.js:735`), which uses
 *  a thin space rather than a comma because the number is a step count, not a
 *  quantity, and a comma reads as a decimal point in half the world. */
export function groupSteps(n: number): string {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP_THIN);
}

const num = (raw: string): number | null => {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) ? n : null;
};

/** The first non-null reason, in the gate's own priority order. `useLock` takes
 *  ONE lane, and several controls here are blocked by three; calling it once per
 *  lane and folding the answers keeps the documented order
 *  (link down -> cap -> role -> busy lane -> extra) instead of letting a
 *  caller-supplied `extra` outrank a lane that is genuinely busy. */
function first(...reasons: (string | null | undefined)[]): string | null {
  for (const r of reasons) if (r) return r;
  return null;
}

/** A paragraph note. `nx-sheet-sub` ellipsises on one line, which is right for
 *  the header and wrong for a sentence, so the sheet's own notes carry the
 *  plan's footer-note type (11.5 px, `--text-faint`, line-height 1.5). */
function Note({ children, tone = "dim", ...rest }: {
  children: ReactNode;
  tone?: "dim" | "warn" | "bad" | "good";
  "data-testid"?: string;
}): JSX.Element {
  const color = tone === "warn" ? "var(--warn)"
    : tone === "bad" ? "var(--bad)"
      : tone === "good" ? "var(--good)" : "var(--text-faint)";
  return (
    <p
      style={{ fontSize: "11.5px", lineHeight: 1.5, color, padding: "0 2px", margin: 0 }}
      data-testid={rest["data-testid"]}
    >
      {children}
    </p>
  );
}

export function FocuserSheet(_p: SheetProps): JSX.Element {
  const status = useStatus();
  const foc = status?.focuser;
  const cam = status?.camera;
  const wheel = status?.filterwheel;
  const config = useConfig();
  const standards = standardsOrDefault(config?.standards);
  const plan = usePlan();
  const sequence = useSequence();
  const providers = useProviders();
  const focus = useFocus();
  const lastAf = useLastAutofocusResult();
  const previews = usePreviews();
  const live = useLivePreview();
  const thresholds = useHfrThresholds();
  const focusFrame = useFrameSettings("focus");
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const showToast = useStore((s) => s.showToast);
  const loadConfig = useStore((s) => s.loadConfig);

  const canFocus = useCanControlCapture();
  const canConfigBackend = useCanConfigBackend();

  const polar = usePolar();
  // r4 #25: polar alignment SPAWNS ITS OWN LANE (server/astrodeck/hub.py:297) and
  // holds the camera for the whole alignment. Nothing on this sheet named it, so
  // AUTOFOCUS NOW, FIND FOCUS ROUGHLY FIRST and BAHTINOV START all pressed
  // through an alignment and came back as a raw 409.
  const polarOwns = isPolarBusy(polar.state, status?.busy_lanes) ? POLAR_REASON : null;

  const sweeping = useBusy("autofocus");
  const looping = !!status?.looping;
  const seqOwnsCamera = sequence.state === "running" || sequence.state === "paused";
  const flowOwns = seqOwnsCamera
    ? (sequence.state === "paused" ? FLOW_OWNS_CAMERA_PAUSED : FLOW_OWNS_CAMERA)
    : null;

  const pos = foc?.position ?? null;
  const focMax = typeof foc?.max === "number" && foc.max > 0 ? foc.max : null;
  // views/FocusView.tsx:307-317 - client-side clamp before the server's own.
  const clampPos = useCallback((p: number) => {
    const r = Math.max(0, Math.round(p));
    return focMax != null ? Math.min(focMax, r) : r;
  }, [focMax]);

  // ------------------------------------------------------------ the gate
  // One call per lane, folded in the documented order. `useLock({})` is the
  // link-down probe: with no cap, role or lane it answers only "the rig is not
  // reachable", which must outrank even a hand-composed reason below.
  const link = useLock({});
  const capRole = useLock({ cap: "control.capture", needsRole: "focuser" });
  const capRoleCamera = useLock({ cap: "control.capture", needsRole: "camera" });
  const laneFocuser = useLock({ busyLane: "focuser" });
  const laneAutofocus = useLock({ busyLane: "autofocus" });
  const laneOffsets = useLock({ busyLane: "filter_offsets" });
  const laneCapture = useLock({ busyLane: "capture" });
  const laneLooping = useLock({ busyLane: "looping" });
  const configLock = useLock({ cap: "config.safety" });
  const providerLock = useLock({ cap: "config.backend" });
  const onExplain = link.onExplain;

  // --------------------------------------------------- a move, and its story
  const [cmd, setCmd] = useState<FocuserCommand | null>(null);
  const [sending, setSending] = useState<number | null>(null);
  // …and the same latch as a ref, because the state one cannot close the window
  // it guards: two taps inside one React batch both read the pre-tap value.
  const sendingRef = useRef(false);
  const [now, setNow] = useState(() => Date.now());
  const [progressAt, setProgressAt] = useState(() => Date.now());
  const seenPos = useRef<number | null>(null);
  useEffect(() => {
    if (seenPos.current === pos) return;
    seenPos.current = pos ?? null;
    setProgressAt(Date.now());
  }, [pos]);

  const progress = moveProgress(cmd, pos, foc?.moving, now, progressAt);
  const waiting = !!cmd && !(progress?.settled ?? false);

  // The shutter's own clock, declared here so the one second hand below covers
  // both things this sheet can be waiting for: a move, and a frame.
  const [shotAt, setShotAt] = useState<number | null>(null);

  // One second hand for the whole sheet, stopped as soon as nothing is waiting.
  useEffect(() => {
    if (!waiting && shotAt == null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting, shotAt]);

  // Command retirement (lib/focusMove.ts:132-166): a settled confirmation
  // lingers, a refusal outstays it, and a sweep taking the focuser retires the
  // command NOW so the engine's own motion is never narrated as the user's Go.
  const retire = cmd
    ? retireAfterMs({
      settled: progress?.settled ?? false,
      tone: progress?.tone ?? null,
      sweepOwnsFocuser: sweeping,
      sequenceRunning: seqOwnsCamera,
    })
    : null;
  useEffect(() => {
    if (cmd == null || retire == null) return;
    if (retire === 0) { setCmd(null); return; }
    const t = setTimeout(() => setCmd(null), retire);
    return () => clearTimeout(t);
  }, [cmd, retire]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message, { verbatim: true }); }
  };

  const moveTo = async (p: number) => {
    if (waiting) { showToast("warning", MOVE_IN_FLIGHT_REASON); return; }
    if (sendingRef.current) { showToast("warning", MOVE_SENDING_REASON); return; }
    const target = clampPos(p);
    const from = pos ?? target;
    sendingRef.current = true;
    setSending(target);
    try {
      await api.post("/api/focuser/move", { position: target });
    } catch (e) {
      // Nothing is claimed: the command never landed, so there is no move to
      // narrate - drawing "-> 22000" for it would be the failure this narrator
      // exists to remove, pointing the other way.
      showToast("error", (e as Error).message, { verbatim: true });
      return;
    } finally {
      sendingRef.current = false;
      setSending(null);
    }
    setCmd({ target, startedAt: Date.now(), from });
    setNow(Date.now());
  };

  // -------------------------------------------------------------- reasons
  const moveExtra = first(
    flowOwns,
    waiting ? MOVE_IN_FLIGHT_REASON : null,
    sending != null ? MOVE_SENDING_REASON : null,
  );
  const focuserReason = first(
    capRole.lockedReason, laneFocuser.lockedReason, laneAutofocus.lockedReason,
    laneOffsets.lockedReason, moveExtra,
  );
  // HALT is never blocked by a lane - it is the only way to end a move early,
  // and gating it on the lane it exists to end is the exact bug the inventory
  // records three times (plan section C, "Never-blocked controls").
  const haltReason = capRole.lockedReason;

  const [absTarget, setAbsTarget] = useState("");
  const absTargetNum = num(absTarget);
  const goReason = first(
    focuserReason,
    absTargetNum == null ? "Type a position number in the box first" : null,
  );

  // ------------------------------------------------ what a sweep would do
  const liveStars = live?.stars ?? null;
  const afDerived = deriveAutofocusParams({
    focuserMax: focMax,
    maxBin: cam?.max_bin ?? null,
    maxGain: cam?.max_gain ?? null,
    liveExposureS: live?.exposure_s ?? null,
    liveGain: live?.gain ?? null,
    liveBinning: live?.binning ?? null,
    liveStars,
    liveHfr: live?.hfr ?? null,
    hasLiveFrame: !!live,
    serverSweep: foc?.sweep ?? null,
  });

  const [afOpen, setAfOpen] = useState(false);
  const [afStep, setAfStep] = useState<number | null>(null);
  const [afEachSide, setAfEachSide] = useState<number | null>(null);
  const [afFilter, setAfFilter] = useState<number | null>(null);
  const stepValue = afStep ?? foc?.sweep?.step ?? afDerived.step;
  const eachSideValue = afEachSide ?? foc?.sweep?.steps_each_side ?? afDerived.steps_each_side;

  const afParamsSent = (providers?.autofocus?.kind ?? "astrodeck") !== "backend";
  const afParams = afOpen
    ? {
      exposure_s: focusFrame.exposure_s,
      gain: focusFrame.gain,
      step: stepValue,
      binning: focusFrame.binning,
      steps_each_side: eachSideValue,
    }
    : {
      exposure_s: afDerived.exposure_s, gain: afDerived.gain, step: afDerived.step,
      binning: afDerived.binning, steps_each_side: afDerived.steps_each_side,
    };

  // The one blocker that decides whether "press SINGLE" is even possible - and
  // it now describes THIS sheet's own shutter, two cards down, rather than
  // Rig - Capture's (r4 #11). Order is by how big a fact it is about the rig:
  // permission, then hardware, then who else owns the camera
  // (`lib/focusCapture.ts:94-121`, re-spelled with hyphens for the copy rule).
  const captureBlocked = first(
    canFocus ? null : `Read-only session - ${accessPhrase("control.capture")} required`,
    !cam ? "No camera is connected - assign one on ADD A DEVICE" : null,
    polarOwns,
    seqOwnsCamera ? "A sequence owns the camera - stop it first" : null,
    // The sweep exposes continuously for minutes; a manual frame would queue
    // behind the exposure guard and land whenever the sweep let go of it.
    sweeping ? "Autofocus owns the camera until the sweep finishes" : null,
  );

  // -------------------------------------------------- the shutter (r4 #11)
  // SINGLE / LOOP / STOP at the `focus` frame scope: the row
  // `views/FocusView.tsx:1291-1355` has always had, on the same three endpoints
  // and the same body (`focusCaptureBody`, which sends `save: false` and does
  // not offer a toggle - `hub.start_loop` has no save parameter at all).
  const [shutterPending, setShutterPending] = useState<null | "single" | "loop">(null);
  const singleShutterReason = first(
    link.lockedReason, captureBlocked, capRoleCamera.lockedReason,
    // Single's one extra row, which Loop does not have: `/api/capture` would
    // queue behind a running loop's own frames and 409 at the capture lock.
    looping ? LOOP_RUNNING_REASON : null,
  );
  const loopShutterReason = first(
    link.lockedReason, captureBlocked, capRoleCamera.lockedReason,
  );
  // STOP is the ACCESS FLOOR ONLY - no lane, ever. It has to stay pressable
  // while the very conditions the other two refuse on (a loop, a capture in
  // flight, a sweep) are true, because it is the only thing that ends them.
  const stopShutterReason = capRoleCamera.lockedReason;

  const shoot = (kind: "single" | "loop") => {
    const blocked = kind === "single" ? singleShutterReason : loopShutterReason;
    // Belt and braces: both buttons are honest-disabled, but a number nothing
    // measured must never reach the wire, where JSON.stringify turns NaN into
    // `null` and the camera gets a body it cannot read.
    if (blocked) { onExplain(blocked); return; }
    setShutterPending(kind);
    void (async () => {
      try {
        await api.post(
          kind === "single" ? "/api/capture" : "/api/capture/loop",
          focusCaptureBody({
            exposureS: focusFrame.exposure_s,
            gain: focusFrame.gain,
            binning: focusFrame.binning,
          }),
        );
        // Narrated only on ACCEPTANCE: POST /api/capture returns the moment the
        // task is spawned, before the shutter opens, so a claim made before the
        // answer is a claim about a frame that may never have started.
        if (kind === "single") { setShotAt(Date.now()); setNow(Date.now()); }
      } catch (e) {
        showToast("error", (e as Error).message, { verbatim: true });
      } finally {
        setShutterPending(null);
      }
    })();
  };
  const stopCapture = () => {
    setShotAt(null);   // nothing is in flight to narrate after a deliberate stop
    void act(() => api.post("/api/capture/stop"));
  };
  // A frame arrived, so the wait is over - whoever the frame belonged to.
  const liveFrameId = live?.id ?? null;
  const seenFrameId = useRef(liveFrameId);
  useEffect(() => {
    if (liveFrameId === seenFrameId.current) return;
    seenFrameId.current = liveFrameId;
    setShotAt(null);
  }, [liveFrameId]);
  const frameWait = shutterWait({
    startedAt: shotAt, exposureS: focusFrame.exposure_s, now,
  });
  const afReady = sweepReadiness({
    manual: afOpen,
    hasLiveFrame: !!live,
    liveStars,
    params: afParams,
    source: afDerived.source,
    paramsSent: afParamsSent,
    captureBlocked,
  });

  const afButton = focusButtonState({
    canFocus, hasFocuser: !!foc, running: sweeping, sweepBlock: afReady.block,
  });
  // focusButtonState's own sentence outranks the gate's cap phrasing here on
  // purpose: it is the sentence the shipped Focus screen has always used for
  // this button ("Read-only - focusing needs operator access"), and the sweep's
  // hardware/permission facts are bigger than any lane.
  const afReason = first(
    link.lockedReason, afButton.reason, capRoleCamera.lockedReason,
    polarOwns, laneCapture.lockedReason, laneLooping.lockedReason, flowOwns,
  );
  const coarseReason = first(
    focuserReason, capRoleCamera.lockedReason, polarOwns, laneCapture.lockedReason,
    looping ? LOOP_RUNNING_REASON : null,
  );

  const runAutofocus = () => void act(() => api.post("/api/focuser/autofocus", {
    ...afParams,
    ...(afFilter != null ? { filter: afFilter } : {}),
  }));

  // A preset tap while a loop is running RESTARTS the loop: `hub.start_loop`
  // closes over the exposure it was handed, so a tap that only moved a
  // highlight would leave the loop shooting the old length forever.
  const presetReason = first(
    capRoleCamera.lockedReason,
    looping ? first(polarOwns,
      seqOwnsCamera ? "A sequence owns the camera - stop it first" : null,
      sweeping ? "Autofocus owns the camera until the sweep finishes" : null) : null,
  );
  const applyPreset = (s: number) => {
    if (presetReason) { onExplain(presetReason); return; }
    const was = focusFrame.exposure_s;
    setFrameSettings("focus", { exposure_s: s });
    if (!looping) return;
    void api.post("/api/capture/loop", focusCaptureBody({
      exposureS: s, gain: focusFrame.gain, binning: focusFrame.binning,
    })).catch((e: Error) => {
      setFrameSettings("focus", { exposure_s: was });
      showToast("error", e.message, { verbatim: true });
    });
  };

  // ---------------------------------------------------- the Bahtinov latch
  const bahtOn = !!status?.bahtinov_active;
  const [bahtWanted, setBahtWanted] = useState<boolean | null>(null);
  useEffect(() => {
    if (bahtWanted == null) return;
    if (bahtOn === bahtWanted) { setBahtWanted(null); return; }
    const t = setTimeout(() => setBahtWanted(null), BAHTINOV_PENDING_GRACE_MS);
    return () => clearTimeout(t);
  }, [bahtWanted, bahtOn]);
  const bahtReason = first(
    capRole.lockedReason, capRoleCamera.lockedReason, polarOwns,
    laneCapture.lockedReason, laneLooping.lockedReason, flowOwns,
  );
  const toggleBahtinov = (want: boolean) => {
    setBahtWanted(want);
    void act(async () => {
      try {
        await api.post(
          want ? "/api/focuser/bahtinov/start" : "/api/focuser/bahtinov/stop",
          want
            ? { exposure_s: focusFrame.exposure_s, gain: focusFrame.gain, binning: 1 }
            : {},
        );
      } catch (e) {
        setBahtWanted(null);
        throw e;
      }
    });
    // The SPIKES are drawn inside PreviewStage's transform, which is Rig -
    // Capture's surface. Arming here and leaving the user on a sheet with no
    // picture would be arming an aid they cannot see.
    if (want) nav.hub("rig", "capture");
  };
  const baht = bahtinovAid(live?.bahtinov ?? null);

  // ------------------------------------------------------- the re-anchor
  const [anchorOpen, setAnchorOpen] = useState(false);
  const [anchorText, setAnchorText] = useState("");
  const anchorReason = first(link.lockedReason, anchorBlocker({
    canFocus,
    hasFocuser: !!foc,
    supported: !!foc?.can_set_position,
    moving: !!foc?.moving,
    raw: anchorText,
    max: focMax,
  }), laneFocuser.lockedReason);

  // --------------------------------------------------- the provider picker
  // Only fetched when the row could be USED: a viewer cannot change task
  // routing, so asking the rig for a driver list on their behalf is a request
  // with no reader. (It is also what makes the viewer test's "no request"
  // assertion mean something.)
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  useEffect(() => {
    if (!canConfigBackend) return;
    let live2 = true;
    void listDrivers()
      .then((r) => { if (live2) setDrivers(r.drivers ?? []); })
      .catch(() => { /* the row degrades to auto + the resolved label */ });
    return () => { live2 = false; };
  }, [canConfigBackend]);

  const afEntry = entryOf(config, providerKey("autofocus"));
  const afTarget = providerWriteTarget(afEntry);
  const afWriteNote = providerWriteNote(afTarget);
  const afProviderValue = valueOf<string>(
    config, providerKey("autofocus"), config?.providers?.autofocus ?? "auto",
  );
  const pickProvider = (value: string) => {
    void act(async () => {
      await writeProviderOverride({
        cap: "autofocus", value, target: afTarget, globals: config?.providers,
      });
    });
  };

  // An `autofocus` pin written into a profile could be EDITED but never REMOVED
  // (review #24): the only unpin on this branch was polar's. Same call, same
  // copy, and honest-disabled rather than hidden for a non-holder - a pinned row
  // with no statement of who can unpin it reads as unremovable.
  const [afUnpinning, setAfUnpinning] = useState(false);
  const dropAfPin = () => {
    const id = afEntry?.profile_id;
    if (!id || afUnpinning) return;
    setAfUnpinning(true);
    void act(async () => {
      try {
        await clearProfileOverrides(id, { providers: ["autofocus"] });
        await loadConfig();
      } finally {
        setAfUnpinning(false);
      }
    });
  };

  // ------------------------------------------------------ standards writes
  const writeStandards = (patch: Partial<typeof standards>) => {
    void act(async () => {
      await setStandardsConfig({ ...standards, ...patch });
      await loadConfig();
    });
  };

  // The drift threshold is a STEPPER over a config block, which is a different
  // problem from the switch beside it: `standards` only moves when `loadConfig`
  // lands, so three quick presses would each start from the same stale number
  // and the rig would end up 0.5 higher instead of 1.5. Hold the value locally,
  // debounce the write, and let the server's answer - or its refusal - take the
  // local copy away again.
  //
  // THE TIMER IS NOT CLEARED ON UNMOUNT, deliberately, for the reason
  // `sheets/wheel.tsx:232-237` gives about the filter offsets: the debounce
  // exists to COALESCE presses, not to cancel them. A user who steps the
  // threshold and immediately presses BACK would otherwise lose the edit with
  // nothing on screen to say so, which is the silent-discard shape this branch
  // keeps finding. The trailing write still lands; its `setTempDelta(null)` on
  // an unmounted tree is a React no-op, while its error toast still reaches the
  // store and is seen on whatever screen replaced this one.
  const [tempDelta, setTempDelta] = useState<number | null>(null);
  const tempDeltaTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownDelta = tempDelta ?? standards.refocus_on_temp_delta_c;
  const bumpTempDelta = (v: number) => {
    setTempDelta(v);
    if (tempDeltaTimer.current) clearTimeout(tempDeltaTimer.current);
    tempDeltaTimer.current = setTimeout(() => {
      tempDeltaTimer.current = null;
      void act(async () => {
        try {
          await setStandardsConfig({ ...standards, refocus_on_temp_delta_c: v });
          await loadConfig();
        } finally {
          setTempDelta(null);
        }
      });
    }, 400);
  };

  // ------------------------------------------- temperature compensation (D-RIG-2)
  //
  // TWO SOURCES, ON PURPOSE. What the loop IS DOING comes off the status bus
  // (`focuser.temp_comp`, republished every 2 s): the switch, the coefficient,
  // the reference the engine re-anchored after its last sweep, and the move the
  // next frame boundary would make. What only the CONFIG knows - the per-move
  // backstop and the deadband - comes off `config.focus`. Reading the reference
  // from the bus rather than from `config` matters: the engine re-anchors it
  // itself (`SequenceEngine._capture_focus_temp`), so a screen showing the
  // config's copy would be showing a reference the rig moved on from.
  const tc = foc?.temp_comp ?? null;
  const cfgFocus = config?.focus ?? null;
  const cfgTc = cfgFocus?.temp_comp ?? null;

  // Same problem, same shape as `bumpTempDelta` above: `config` only moves when
  // `loadConfig` lands, so two edits inside the debounce window would each start
  // from the same stale block and the second would undo the first. Hold the
  // patch locally, merge into it, write once, and let the server's answer - or
  // its refusal - take the local copy away again.
  //
  // Its timer is NOT cleared on unmount either, and here the argument is
  // stronger than for the threshold above: the trailing write re-reads the
  // focus block, merges the pending patch and posts the WHOLE block, so
  // cancelling it on BACK would throw away a coefficient the operator typed and
  // leave the engine compensating with the old sign - visible only as a night
  // of drifting focus. The `setTcDraft` / `setTcResetKey` calls that follow are
  // no-ops on an unmounted tree; the toast on a refusal is not.
  const [tcDraft, setTcDraft] = useState<Partial<TempCompConfig>>({});
  const tcPending = useRef<Partial<TempCompConfig>>({});
  const tcTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Bumped on every REFUSED temp-comp write, so the boxes below throw away the
   *  typed draft and show the rig's own number again. Clearing `tcDraft` gets
   *  there only while the optimistic patch is what moved `value`; a refusal
   *  that never moved it at all (no `focus` block to merge into, a 4xx on a
   *  field whose committed number matched the config) would otherwise leave the
   *  typed number sitting in the box looking saved. `NumberField.resetKey`. */
  const [tcResetKey, setTcResetKey] = useState(0);
  const writeTempComp = (patch: Partial<TempCompConfig>) => {
    tcPending.current = { ...tcPending.current, ...patch };
    setTcDraft(tcPending.current);
    if (tcTimer.current) clearTimeout(tcTimer.current);
    tcTimer.current = setTimeout(() => {
      tcTimer.current = null;
      const sending = tcPending.current;
      tcPending.current = {};
      void (async () => {
        try {
          // A FRESH READ FIRST, then the whole block. `POST /api/config {focus}`
          // REPLACES `focus` (`config_store.set_focus`), so a body built from a
          // block we had not just read would blank `approach_overshoot_steps` -
          // the EAF backlash correction, which nothing on any screen sets - and
          // would push back a reference the engine had re-anchored since.
          await loadConfig();
          const block = useStore.getState().config?.focus ?? null;
          if (!block) {
            showToast("error", NO_FOCUS_BLOCK_REASON);
            setTcResetKey((n) => n + 1);
            return;
          }
          await setFocusConfig({
            ...block,
            temp_comp: { ...block.temp_comp, ...sending },
          });
          await loadConfig();
        } catch (e) {
          showToast("error", (e as Error).message, { verbatim: true });
          setTcResetKey((n) => n + 1);
        } finally {
          setTcDraft({});
        }
      })();
    }, 400);
  };

  const tcEnabled = tcDraft.enabled ?? tc?.enabled ?? false;
  const tcCoefficient = tcDraft.steps_per_c ?? tc?.steps_per_c ?? 0;
  const tcMaxStep = tcDraft.max_step_per_move ?? cfgTc?.max_step_per_move ?? null;
  const tcDeadband = tcDraft.deadband_steps ?? cfgTc?.deadband_steps ?? null;

  // ------------------------------------------------------------- readouts
  const shownFocus = focusState(live as {
    hfr?: number | null; stars?: number | null; defocus_r80?: number | null;
  } | null);
  const scale = live?.pixel_scale_arcsec ?? null;
  const hfrText = (hfr: number): string =>
    scale != null ? `${(hfr * scale).toFixed(2)}″` : `${hfr.toFixed(2)} px`;

  const liveLine = (() => {
    switch (shownFocus.kind) {
      case "no-frame": return { text: "no frame yet", tone: "dim" as const };
      case "few-stars": return { text: "too few stars", tone: "warn" as const };
      case "defocused":
        return {
          text: `defocused · blob ${Math.round(2 * shownFocus.r80)} px`,
          tone: "bad" as const,
        };
      case "soft":
        return {
          text: `soft · stars ${Math.round(2 * shownFocus.r80)} px across`,
          tone: "warn" as const,
        };
      default:
        return shownFocus.hfr <= thresholds.good
          ? { text: `in focus · HFR ${hfrText(shownFocus.hfr)}`, tone: "good" as const }
          : { text: "HFR drifting · refocus advised", tone: "warn" as const };
    }
  })();

  const focuserName = status?.connected?.focuser?.name ?? null;
  const wheelName = !wheel?.moving && wheel?.current ? wheel.current : null;
  const tube = foc?.temperature ?? null;

  // The compensation block's own gates. Order is the sheet's house order
  // (`captureBlocked` above): permission, then hardware, then who else is about
  // to touch the thing. The autofocus lane is on every write, not only the two
  // the plan named, because a sweep RE-ANCHORS the reference when it finishes -
  // so a write landing across one would carry a block that the engine has
  // already moved on from.
  const tcWriteReason = first(
    configLock.lockedReason,
    cfgFocus ? null : NO_FOCUS_BLOCK_REASON,
    laneAutofocus.lockedReason,
  );
  // Arming the loop and re-anchoring both need a reading to work from; editing
  // the numbers does not, so a rig whose focuser has no thermometer can still be
  // set up for the one that will.
  const tcArmReason = first(
    configLock.lockedReason,
    tube == null ? NO_THERMOMETER_REASON : null,
    cfgFocus ? null : NO_FOCUS_BLOCK_REASON,
    laneAutofocus.lockedReason,
  );
  const tcReanchorReason = first(
    tcArmReason,
    pos == null ? "The focuser has not reported a position yet" : null,
  );
  const tcNext = nextMoveTile(tc, pos, tcMaxStep);

  // ------------------------------------------------------------- V-curve
  // The live slice while a sweep runs, the persisted last completed run
  // otherwise, so the chart is not empty on a sheet opened between runs.
  const chart = focus ?? lastAf;
  const chartFit = (chart as { fit?: FocusFit | null } | null)?.fit ?? null;
  const [sweepStartedFrames, setSweepStartedFrames] = useState<number | null>(null);
  useEffect(() => {
    if (sweeping) setSweepStartedFrames((v) => (v == null ? previews.length : v));
    else setSweepStartedFrames(null);
  }, [sweeping, previews.length]);
  const previewNote = sweepPreviewNote({
    running: sweeping,
    pointsMeasured: chart?.points?.length ?? 0,
    framesSinceStart: sweepStartedFrames == null
      ? 0 : Math.max(0, previews.length - sweepStartedFrames),
    loopRunning: looping,
  });
  const lastAfClock = lastAf
    ? new Date(lastAf.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  const afFailure = lastAf?.state === "failed"
    ? readFocusFailure(lastAf.message)
    : { code: null, explain: null };

  const jog = (delta: number, label: string) => (
    <ActionButton
      key={label}
      kind="secondary"
      onPress={() => void moveTo((pos ?? 0) + delta)}
      lockedReason={focuserReason}
      onExplain={onExplain}
      ariaLabel={`${label} steps`}
      full
    >
      {label}
    </ActionButton>
  );

  const planEvery = typeof (plan as { autofocus_every?: number })?.autofocus_every === "number"
    ? (plan as { autofocus_every: number }).autofocus_every
    : 0;
  const hfrFactor = config?.escalation?.hfr_reject_factor ?? 0;
  const hfrAction = config?.escalation?.hfr_reject_action ?? "warn";

  const providerOptions = useMemo(() => {
    const rows = eligibleTaskDrivers("autofocus", drivers)
      .map((d) => ({ value: d.id, label: d.label }));
    const known = new Set(["auto", ...rows.map((r) => r.value)]);
    // A stored value no longer offered stays listed (the legacy "backend"
    // alias, or a driver that went unreachable) rather than silently vanishing.
    const sticky = known.has(afProviderValue)
      ? []
      : [{
        value: afProviderValue,
        label: afProviderValue === "backend" ? "Backend (legacy)" : afProviderValue,
      }];
    return [{ value: "auto", label: "Auto" }, ...rows, ...sticky];
  }, [drivers, afProviderValue]);

  return (
    <Sheet
      data-testid="rig-focuser"
      title="FOCUSER"
      backLabel="RIG"
      icon={<NxIcon name="focuser" size={18} />}
      live={(
        <span data-tone={liveLine.tone} style={{ color: "var(--nx-tone)" }}>
          {liveLine.text}
        </span>
      )}
      onBack={() => nav.back()}
    >
      {/* 13. Ownership banners, before anything they lock. */}
      {flowOwns && <BannerCard tone="warn" text={flowOwns} />}
      {!seqOwnsCamera && looping && <BannerCard tone="info" text={LOOP_OWNS_CAMERA} />}

      {!foc && (
        <EmptyCard
          title="No focuser connected"
          hint="Assign a focuser on ADD A DEVICE, then connect the rig. The controls below stay on screen so you can see what this rig would offer."
          action={(
            <ActionButton kind="secondary" onPress={() => nav.sheet("addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          )}
        />
      )}

      {/* 1. The three readouts. No dial: this sheet has no editable scalar the
          dial would own - the autofocus geometry lives in its own disclosure. */}
      <ReadoutGrid cols={3}>
        <ReadoutTile
          label="POSITION"
          value={pos != null ? groupSteps(pos) : "--"}
          sub={[
            focuserName ? `steps · ${focuserName}` : "steps",
            focMax != null ? `/ ${groupSteps(focMax)}` : null,
          ].filter(Boolean).join(" ")}
        />
        <ReadoutTile
          label="HFR"
          value={live?.hfr != null ? hfrText(live.hfr) : "--"}
          sub={wheelName ? `last ${wheelName} sub` : "last frame"}
        />
        <ReadoutTile
          label="TUBE"
          value={tube != null ? `${tube.toFixed(1)}°C` : "no sensor"}
          sub={shownDelta > 0
            ? `refocus at ${shownDelta}°C drift`
            : "no temperature rule"}
        />
      </ReadoutGrid>
      {/* The one place an older engine has to be told apart from a switched-off
          loop: `focuser.temp_comp` is ABSENT before S7c, and rendering a switch
          with nothing to bind it to would invent a state the rig does not have.
          It sits beside the TUBE tile because that reading is what compensation
          would have followed. */}
      {foc && !tc && (
        <Note data-testid="tempcomp-absent">
          This engine does not drive the focuser from temperature yet.
        </Note>
      )}

      {/* 2. The V-curve, and what the sweep's own frames are doing. */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <Label>
            {sweeping
              ? "V-CURVE · MEASURING…"
              : `V-CURVE · LAST AUTOFOCUS ${lastAfClock ?? "NEVER RUN"}`}
          </Label>
          <Mono tone="dim">
            {chart?.best?.position != null ? `best ${groupSteps(chart.best.position)}` : "no best yet"}
          </Mono>
        </div>
        <VCurve
          points={chart?.points ?? []}
          best={chart?.best ?? null}
          fit={chartFit}
          running={sweeping}
        />
        {previewNote && <Note tone="warn">{previewNote}</Note>}
      </Card>

      {/* 2b. The shutter, at the `focus` frame scope (r4 #11). Above the jogs
          and above the sweep because focusing is a loop - shoot, look, nudge,
          repeat - and because `sweepReadiness`'s refusal says "press SINGLE
          above", which has to be true of where it actually is. */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <Label>FOCUS FRAME</Label>
          <Mono tone="dim" data-testid="focus-frame-summary">
            {`${focusFrame.exposure_s} s · gain ${focusFrame.gain} · bin ${focusFrame.binning}`}
          </Mono>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 6 }}>
          <ActionButton
            kind="primary"
            full
            busy={shutterPending === "single"}
            onPress={() => shoot("single")}
            lockedReason={shutterPending === "single"
              ? SHUTTER_STARTING_REASON : singleShutterReason}
            onExplain={onExplain}
            data-testid="focus-single"
          >
            {shutterPending === "single" ? "STARTING…" : "SINGLE"}
          </ActionButton>
          <ActionButton
            kind="secondary"
            full
            busy={shutterPending === "loop"}
            onPress={() => shoot("loop")}
            lockedReason={shutterPending === "loop"
              ? SHUTTER_STARTING_REASON
              : looping ? LOOP_ALREADY_REASON : loopShutterReason}
            onExplain={onExplain}
            data-testid="focus-loop"
          >
            {shutterPending === "loop" ? "STARTING…" : looping ? "LOOPING" : "LOOP"}
          </ActionButton>
          <ActionButton
            kind="danger"
            full
            onPress={stopCapture}
            lockedReason={stopShutterReason}
            onExplain={onExplain}
            data-testid="focus-stop"
          >
            STOP
          </ActionButton>
        </div>
        {frameWait && (
          <Note tone={frameWait.tone} data-testid="focus-frame-wait">{frameWait.text}</Note>
        )}
        <Note>{FOCUS_FRAME_NOTE}</Note>
      </Card>

      {/* 3. The jogs, finest first. The 1-step pair is the legacy `STEP_VALUES =
          [1, 10, 100, 1000]` row that this sheet had dropped to 10/100/1000:
          the EAF's backlash is tens of steps, so a single step is not how you
          travel - it is how you confirm the drawtube answers at all. */}
      <div className="nx-btn-grid" data-cols="2">
        {jog(-1, "IN 1")}
        {jog(1, "OUT 1")}
      </div>
      {/* Four across the panel is 77 px a button; `nx-btn-grid`'s tighter side
          padding (next.css) is what keeps OUT 100 off "OUT 1…". */}
      <div className="nx-btn-grid" data-cols="4" data-testid="focus-jogs-100">
        {jog(-100, "IN 100")}
        {jog(-10, "IN 10")}
        {jog(10, "OUT 10")}
        {jog(100, "OUT 100")}
      </div>
      <div className="nx-btn-grid" data-cols="2">
        {jog(-1000, "IN 1000")}
        {jog(1000, "OUT 1000")}
      </div>
      {progress && (
        <Mono tone={progress.tone === "warn" ? "warn" : progress.tone === "good" ? "good" : "dim"}>
          <span data-testid="focuser-move-progress">{progress.text}</span>
        </Mono>
      )}

      {/* 4. Absolute move, and the escape hatch beside it. */}
      <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Field label="GO TO POSITION">
            <TextInput
              value={absTarget}
              onChange={setAbsTarget}
              placeholder={pos != null ? String(pos) : "steps"}
              mono
              type="text"
              ariaLabel="Focuser target position in steps"
              lockedReason={capRole.lockedReason}
            />
          </Field>
        </div>
        <ActionButton
          kind="secondary"
          onPress={() => { if (absTargetNum != null) void moveTo(absTargetNum); }}
          lockedReason={goReason}
          onExplain={onExplain}
        >
          GO
        </ActionButton>
        <ActionButton
          kind="danger"
          onPress={() => void act(() => api.post("/api/focuser/halt"))}
          lockedReason={haltReason}
          onExplain={onExplain}
          ariaLabel="Halt the focuser"
        >
          HALT
        </ActionButton>
      </div>

      {/* 5. Re-anchor. Rendered only where the device can do it. */}
      {foc?.can_set_position && (
        <Card>
          <ActionButton
            kind="ghost"
            onPress={() => setAnchorOpen(!anchorOpen)}
            ariaLabel="Set the focuser's current position"
          >
            {anchorOpen ? "SET CURRENT POSITION - CLOSE" : "SET CURRENT POSITION…"}
          </ActionButton>
          {anchorOpen && (
            <>
              <Note>{ANCHOR_NOTE}</Note>
              <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Field label="THIS POSITION IS">
                    <TextInput
                      value={anchorText}
                      onChange={setAnchorText}
                      mono
                      ariaLabel="The number the focuser should call its current position"
                      lockedReason={capRole.lockedReason}
                    />
                  </Field>
                </div>
                <ActionButton
                  kind="secondary"
                  onPress={() => {
                    const n = num(anchorText);
                    if (n == null) return;
                    void act(async () => {
                      await api.post("/api/focuser/set-position", { position: Math.round(n) });
                      setAnchorText("");
                      setAnchorOpen(false);
                    });
                  }}
                  lockedReason={anchorReason}
                  onExplain={onExplain}
                >
                  SET
                </ActionButton>
              </div>
            </>
          )}
        </Card>
      )}

      {/* 6. The sweep, and what it would do - printed BEFORE the tap, from the
          same object the request is built from. */}
      <Card>
        <Note data-testid="focuser-sweep-summary">{afReady.summary}</Note>
        <Note>{afReady.provenance}</Note>
        {afReady.warn && <Note tone="warn">{afReady.warn}</Note>}
        <ActionButton
          kind="primary"
          size="lg"
          full
          busy={sweeping}
          onPress={runAutofocus}
          lockedReason={afReason}
          onExplain={onExplain}
        >
          {sweeping ? "FOCUSING…" : "AUTOFOCUS NOW"}
        </ActionButton>
        <ActionButton
          kind="secondary"
          full
          onPress={() => void act(() => api.post("/api/focuser/coarse", {}))}
          lockedReason={coarseReason}
          onExplain={onExplain}
        >
          FIND FOCUS ROUGHLY FIRST
        </ActionButton>
        {shownFocus.kind === "defocused" || shownFocus.kind === "soft"
          ? <Note tone="warn">{defocusMessage(shownFocus.r80)}</Note>
          : null}
      </Card>

      {/* 7. The sweep's own settings. */}
      <Card>
        <ActionButton
          kind="ghost"
          onPress={() => setAfOpen(!afOpen)}
          ariaLabel="Autofocus settings"
        >
          {afOpen ? "AUTOFOCUS SETTINGS - CLOSE" : "AUTOFOCUS SETTINGS…"}
        </ActionButton>
        {afOpen && (
          <>
            <Field
              label="STEP SIZE"
              hint={foc?.sweep?.measured
                ? "measured from the last sweep"
                : "the shipped default - no sweep has completed yet"}
            >
              <Stepper2
                label="Autofocus step size in focuser steps"
                value={stepValue}
                onChange={(v) => setAfStep(v)}
                step={10}
                min={20}
                max={1500}
                lockedReason={capRole.lockedReason}
                onExplain={onExplain}
              />
            </Field>
            <Field label="STEPS EACH SIDE" hint="how far either side of centre the sweep brackets">
              <Stepper2
                label="Sweep points each side of centre"
                value={eachSideValue}
                onChange={(v) => setAfEachSide(v)}
                step={1}
                min={1}
                max={12}
                lockedReason={capRole.lockedReason}
                onExplain={onExplain}
              />
            </Field>
            {/* EXPOSURE / GAIN / BINNING are the shared `focus` SCOPE - the
                same three numbers the next frame from this screen is shot at
                and the ones the sweep copies. Deliberately not three private
                useStates: that is how one screen came to carry two exposures.
                Every write goes through `applyPreset` for the exposure, because
                over a running loop a new exposure RESTARTS the loop and a
                control that only moved a number would be ignored by the
                camera. */}
            <Field label="EXPOSURE" hint="the focus scope - the same frame this screen and the sweep shoot">
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {FOCUS_EXPOSURE_PRESETS.map((s) => (
                  <Chip
                    key={s}
                    active={focusFrame.exposure_s === s}
                    onClick={() => applyPreset(s)}
                    lockedReason={presetReason}
                    onExplain={onExplain}
                  >
                    {`${s} s`}
                  </Chip>
                ))}
                <Stepper2
                  label="Focus frame exposure in seconds"
                  value={focusFrame.exposure_s}
                  onChange={applyPreset}
                  step={1}
                  min={1}
                  max={120}
                  format={(v) => `${v} s`}
                  lockedReason={presetReason}
                  onExplain={onExplain}
                />
              </div>
            </Field>
            <Field label="GAIN">
              <Stepper2
                label="Focus frame gain"
                value={focusFrame.gain}
                onChange={(v) => setFrameSettings("focus", { gain: v })}
                step={10}
                min={0}
                max={cam?.max_gain && cam.max_gain > 0 ? cam.max_gain : 600}
                lockedReason={first(capRoleCamera.lockedReason, laneCapture.lockedReason, flowOwns)}
                onExplain={onExplain}
              />
            </Field>
            <Field label="BINNING">
              <Stepper2
                label="Focus frame binning"
                value={focusFrame.binning}
                onChange={(v) => setFrameSettings("focus", { binning: v })}
                step={1}
                min={1}
                max={cam?.max_bin && cam.max_bin > 0 ? cam.max_bin : 4}
                format={(v) => `${v}x${v}`}
                lockedReason={first(capRoleCamera.lockedReason, laneCapture.lockedReason, flowOwns)}
                onExplain={onExplain}
              />
            </Field>
            {wheel?.names?.length ? (
              <Field label="PIN A FILTER" hint="which filter the sweep runs through; the rig keeps whatever is loaded otherwise">
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <Chip active={afFilter == null} onClick={() => setAfFilter(null)}>
                    NO PIN
                  </Chip>
                  {wheel.names.map((n, i) => (
                    <Chip
                      key={`${n}-${i}`}
                      active={afFilter === i}
                      onClick={() => setAfFilter(i)}
                    >
                      {n || `#${i + 1}`}
                    </Chip>
                  ))}
                </div>
              </Field>
            ) : null}
            {!afParamsSent && (
              <Note tone="warn">
                {`${providers?.autofocus?.label ?? "The backend"} runs this sweep with its own `
                  + "exposure, gain and binning - nothing typed here is sent"}
              </Note>
            )}
            <Note>{AF_SETTINGS_NOTE}</Note>
          </>
        )}
      </Card>

      {/* 8. Who runs the sweep. */}
      <Card>
        <ListRow
          title="AUTOFOCUS PROVIDER"
          sub={providers?.autofocus?.reason ?? "resolved when the rig answers"}
          right={<Mono tone="accent">{providers?.autofocus?.label ?? "auto"}</Mono>}
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {providerOptions.map((o) => (
            <Chip
              key={o.value}
              active={afProviderValue === o.value}
              onClick={() => pickProvider(o.value)}
              lockedReason={providerLock.lockedReason}
              onExplain={onExplain}
            >
              {o.label}
            </Chip>
          ))}
        </div>
        {afWriteNote && <Note tone="warn">{afWriteNote}</Note>}
        {/* HONEST-DISABLED, not hidden: ARCHITECTURE section 8 says nothing is
            hidden, and a pinned row a non-holder cannot unpin still has to say
            who can. */}
        {isProfileOverride(afEntry) && afEntry?.profile_id && (
          <div style={{ marginTop: 6 }}>
            <ActionButton
              kind="ghost"
              busy={afUnpinning}
              lockedReason={canConfigBackend ? null : providerLock.lockedReason}
              onExplain={onExplain}
              onPress={dropAfPin}
              data-testid="focuser-provider-unpin"
            >
              CLEAR THE PROFILE PIN
            </ActionButton>
            <Mono size={10.5} tone="dim">
              Clearing it hands this row back to the global setting.
            </Mono>
          </div>
        )}
      </Card>

      {/* 8b. TEMPERATURE COMPENSATION (D-RIG-2). Directly above AUTOFOCUS RUNS
          WHEN, and ending with the precedence sentence, because the card below
          holds the refocus TRIGGER and those two settings are the pair the
          sentence exists to tell apart. */}
      {tc && (
        <Card data-testid="focuser-tempcomp">
          <Switch
            label="TEMPERATURE COMPENSATION"
            note="Nudges the focuser between frames as the tube cools, for the cost of one short move and no frames."
            checked={tcEnabled}
            onChange={(v) => writeTempComp({ enabled: v })}
            lockedReason={tcArmReason}
            onExplain={onExplain}
            data-testid="tempcomp-switch"
          />
          <NumberField
            label="STEPS PER DEGREE"
            value={tcCoefficient}
            onCommit={(v) => writeTempComp({ steps_per_c: v })}
            unit="steps/°C"
            min={-500}
            max={500}
            step={1}
            zeroMeans="off even when the switch is on"
            ariaLabel="Temperature compensation coefficient, focuser steps per degree Celsius"
            lockedReason={tcWriteReason}
            onExplain={onExplain}
            resetKey={tcResetKey}
            data-testid="tempcomp-coefficient"
          />
          {/* The sign, in words, twice over: the rule that never changes, then
              what THIS number does tonight. A reader who never works out which
              way "positive" points still cannot set it backwards. */}
          <Note data-testid="tempcomp-sign">{signSentence(tcCoefficient)}</Note>
          <Note data-testid="tempcomp-effect">{effectSentence(tcCoefficient)}</Note>
          <ReadoutGrid cols={3}>
            <ReadoutTile
              label="REFERENCE"
              value={tc.reference_temp_c != null ? `${tc.reference_temp_c.toFixed(1)}°C` : "none"}
              sub={tc.reference_position != null
                ? `at ${groupSteps(tc.reference_position)} steps`
                : "set by the next autofocus"}
              ariaLabel={referenceLine(tc)}
            />
            <ReadoutTile
              label="NOW"
              value={tube != null ? `${tube.toFixed(1)}°C` : "no sensor"}
              sub={pos != null ? `at ${groupSteps(pos)} steps` : "no position"}
            />
            {/* Straight off the wire. `predicted_position` is the server running
                its own rule table against the live reading; re-deriving it here
                would mean a second copy of the deadband, the clamp and the
                travel limits, and the first time they disagreed this tile would
                be describing a move the engine is not going to make. */}
            <ReadoutTile
              label="NEXT MOVE"
              value={tcNext.value}
              sub={tcNext.sub || undefined}
              data-testid="tempcomp-next"
            />
          </ReadoutGrid>
          {/* The engine's own words for what the last boundary decided - nine
              rules end in "nothing happened", and a no-op with no explanation is
              indistinguishable from a feature that is not wired up. */}
          {tc.last_reason && (
            <Mono size={10.5} tone="dim">
              <span data-testid="tempcomp-reason">{tc.last_reason}</span>
            </Mono>
          )}
          {tc.last_move_steps != null && tc.last_move_steps !== 0 && (
            <Mono size={10.5} tone="dim">
              {`last move ${tc.last_move_steps > 0 ? "+" : ""}${tc.last_move_steps} steps`}
            </Mono>
          )}
          <ActionButton
            kind="secondary"
            full
            onPress={() => {
              if (tube == null || pos == null) return;
              writeTempComp({ reference_temp_c: tube, reference_position: Math.round(pos) });
            }}
            lockedReason={tcReanchorReason}
            onExplain={onExplain}
            data-testid="tempcomp-reanchor"
          >
            ANCHOR THE REFERENCE HERE
          </ActionButton>
          <Note>
            {tube != null && pos != null
              ? `Sets the reference to ${tube.toFixed(1)} C at ${groupSteps(pos)} steps, the `
                + "reading above. Nothing moves, and the next autofocus re-anchors it again."
              : referenceLine(tc)}
          </Note>
          <Note tone="warn">{DOUBLES_IT_NOTE}</Note>
          <Disclosure
            summary="ADVANCED"
            sub={tcMaxStep != null && tcDeadband != null
              ? `limit ${tcMaxStep} · deadband ${tcDeadband}`
              : "waiting for the rig"}
            data-testid="tempcomp-advanced"
          >
            {/* These two are the only settings in the block the STATUS BUS does
                not carry, so with no `config.focus` in hand there is no number
                to show. Printing the shipped defaults instead would be showing
                the operator a limit that is not this rig's. */}
            {!cfgTc && <Note tone="warn">{NO_FOCUS_BLOCK_REASON}</Note>}
            {cfgTc && (
            <NumberField
              label="MOST STEPS PER MOVE"
              value={tcMaxStep ?? 200}
              onCommit={(v) => writeTempComp({ max_step_per_move: v })}
              unit="steps"
              min={1}
              max={5000}
              step={10}
              integer
              hint="the backstop under a wrong coefficient or a glitching thermometer: a longer move is shortened to this, and the drift it did not cover is left for the refocus trigger"
              ariaLabel="Largest compensation move, focuser steps"
              lockedReason={tcWriteReason}
              onExplain={onExplain}
              resetKey={tcResetKey}
              data-testid="tempcomp-maxstep"
            />
            )}
            {cfgTc && (
            <NumberField
              label="DEADBAND"
              value={tcDeadband ?? 5}
              onCommit={(v) => writeTempComp({ deadband_steps: v })}
              unit="steps"
              min={0}
              max={500}
              step={1}
              integer
              zeroMeans="every move is made, however short"
              hint="shorter moves are skipped: under the EAF's backlash they turn the motor and not the tube"
              ariaLabel="Compensation deadband, focuser steps"
              lockedReason={tcWriteReason}
              onExplain={onExplain}
              resetKey={tcResetKey}
              data-testid="tempcomp-deadband"
            />
            )}
          </Disclosure>
          {/* Verbatim from `TEMP_COMP_PRECEDENCE`, the string the server keeps so
              that the engine, the docs and this screen cannot drift apart. */}
          <Note data-testid="tempcomp-precedence">{TEMP_COMP_PRECEDENCE}</Note>
        </Card>
      )}

      {/* 9. AUTOFOCUS RUNS WHEN. Two switches this sheet owns, two rows it does
          not - see the header (E4-E6). */}
      <Card>
        <Label>AUTOFOCUS RUNS WHEN</Label>
        <Switch
          label="SHIFT BY FILTER OFFSET ON A CHANGE"
          note={FILTER_OFFSET_NOTE}
          checked={standards.apply_filter_offsets}
          onChange={(v) => writeStandards({ apply_filter_offsets: v })}
          lockedReason={configLock.lockedReason}
          onExplain={onExplain}
        />
        <Field
          label={shownDelta > 0
            ? `REFOCUS AFTER ${shownDelta}°C OF DRIFT`
            : "REFOCUS ON TEMPERATURE DRIFT - OFF"}
          hint="0 turns the rule off; the tube reading above is what it watches"
        >
          <Stepper2
            label="Refocus after this much tube temperature drift"
            value={shownDelta}
            onChange={bumpTempDelta}
            step={0.5}
            min={0}
            max={50}
            format={(v) => `${v}°C`}
            lockedReason={configLock.lockedReason}
            onExplain={onExplain}
          />
        </Field>
        <ListRow
          title={planEvery > 0
            ? `REFOCUS EVERY ${planEvery} FRAMES`
            : "REFOCUS EVERY N FRAMES - OFF"}
          sub="set per night in the plan"
          chevron
          tone="dim"
          onPress={() => nav.go("/session/flows/planEditor")}
        />
        <ListRow
          title={hfrFactor > 0
            ? `HFR GATE x${hfrFactor} - ${hfrAction}`
            : "HFR GATE - OFF"}
          sub="a frame-quality gate, not a refocus trigger - edit on the Safety sheet"
          chevron
          tone="dim"
          onPress={() => nav.sheet("safety")}
        />
      </Card>

      {/* 11. The Bahtinov verdict. The spikes are drawn on Rig - Capture. */}
      <Card>
        <ListRow
          title="BAHTINOV MODE"
          sub={`${baht.headline} · ${baht.detail}`}
          tone={baht.tone === "neutral" ? undefined : baht.tone}
          right={(
            <ActionButton
              kind={bahtOn ? "danger" : "secondary"}
              onPress={() => toggleBahtinov(!bahtOn)}
              lockedReason={bahtWanted != null
                ? (bahtWanted ? "Arming…" : "Stopping…")
                : bahtReason}
              onExplain={onExplain}
            >
              {bahtWanted != null
                ? (bahtWanted ? "ARMING…" : "STOPPING…")
                : bahtOn ? "STOP" : "START"}
            </ActionButton>
          )}
        />
        <Note>{BAHTINOV_HINT}</Note>
      </Card>

      {/* 12. The last COMPLETED run, which survives navigation. */}
      <Card>
        <Label>RESULT</Label>
        {lastAf ? (
          <>
            <AutofocusLine
              state={lastAf.state}
              hfr={lastAf.best?.hfr ?? null}
              r2={lastAf.fit?.r2 ?? null}
              method={lastAf.fit?.method ?? null}
              pixelScaleArcsec={scale}
              hfrGood={thresholds.good}
              hfrWarn={thresholds.warn}
              message={lastAf.message}
            />
            {lastAf.advice && <Note>{lastAf.advice}</Note>}
            {afFailure.explain && <Note tone="warn">{afFailure.explain}</Note>}
            {lastAf.filter && <Mono tone="dim">{`through ${lastAf.filter}`}</Mono>}
          </>
        ) : (
          <Note>Run autofocus to measure focus quality.</Note>
        )}
      </Card>

      <ActionButton kind="ghost" full onPress={() => nav.hub("rig", "capture")}>
        OPEN LIVE VIEW
      </ActionButton>

      {/* 14. Footer. */}
      <Note data-testid="focuser-footer">{FOCUSER_FOOTER}</Note>
    </Sheet>
  );
}
