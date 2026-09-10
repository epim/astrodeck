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
//
// THREE DELIBERATE DEVIATIONS FROM THE DESIGN (plan E3-E6), all of the same
// shape - the design draws a control for a verb the engine does not have:
//
//   E3  TEMPERATURE COMPENSATION is OMITTED. There is no temperature-
//       compensation field, coefficient or loop anywhere in the server; the
//       only temperature-driven focus behaviour is
//       `standards.refocus_on_temp_delta_c`, a refocus TRIGGER, not a per-degree
//       step. The design's "-14 steps per °C" describes a feature that does not
//       exist, and a switch for it would be a promise nothing keeps. What is
//       real - the tube reading and the drift threshold - is the TUBE tile and
//       the REFOCUS AFTER stepper. `rigFocuserDom.test.tsx` asserts the string
//       is absent, so re-adding a dead toggle turns the suite red.
//   E4  "on filter change" is not a refocus rule: the engine SHIFTS the focuser
//       by the filter's stored offset instead, so the control is that switch.
//   E5/E6  "every 60 min" is a per-PLAN frame count and "HFR +15%" is a frame-
//       QUALITY gate on the Safety sheet. Both render as read-only rows that
//       tap through to where they are actually edited, because a chip that
//       cannot be toggled here would be a lie about what this screen owns.

import {
  useCallback, useEffect, useMemo, useRef, useState, type JSX, type ReactNode,
} from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import {
  ActionButton, BannerCard, Card, Chip, EmptyCard, Field, Label, ListRow, Mono,
  ReadoutGrid, ReadoutTile, Sheet, Stepper2, Switch, TextInput,
} from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { api } from "../../../../api";
import { listDrivers, setStandardsConfig } from "../../../../api/backends";
import { accessPhrase, useCanConfigBackend, useCanControlCapture }
  from "../../../../lib/caps";
import { useBusy } from "../../../../lib/useBusy";
import {
  deriveAutofocusParams, focusButtonState, readFocusFailure,
} from "../../../../lib/autofocus";
import {
  FOCUS_EXPOSURE_PRESETS, focusCaptureBody, sweepPreviewNote, sweepReadiness,
} from "../../../../lib/focusCapture";
import {
  MOVE_IN_FLIGHT_REASON, MOVE_SENDING_REASON, anchorBlocker, moveProgress,
  retireAfterMs, type FocuserCommand,
} from "../../../../lib/focusMove";
import { defocusMessage, focusState } from "../../../../lib/focusVerdict";
import { standardsOrDefault } from "../../../../lib/standards";
import { entryOf, providerKey, valueOf } from "../../../../lib/effective";
import { writeProviderOverride } from "../../../../lib/providerSave";
import { providerWriteNote, providerWriteTarget } from "../../../../lib/providerWrite";
import { eligibleTaskDrivers } from "../../../../lib/equipment";
import { VCurve, type FocusFit } from "../../../../components/graphs";
import { AutofocusVerdict } from "../../../../components/preview/FocusVerdict";
import { bahtinovAid } from "../../../../lib/bahtinov";
import {
  useConfig, useFocus, useFrameSettings, useHfrThresholds,
  useLastAutofocusResult, useLivePreview, usePlan, usePreviews, useProviders,
  useSequence, useStatus, useStore,
} from "../../../../store";
import type { DriverInfo } from "../../../../types";

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

export const AF_SETTINGS_NOTE =
  "Focus frames are not saved. Autofocus sweeps at this gain and binning, and at "
  + "this exposure too once a frame shows stars - so shoot something that works "
  + "before you sweep.";

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

  // One second hand for the whole sheet, stopped as soon as nothing is waiting.
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting]);

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
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
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
      showToast("error", (e as Error).message);
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

  // The one blocker that decides whether "tap a preset, then Single" is even
  // possible: this sheet has no shutter of its own, so it names Rig - Capture's.
  const captureBlocked = first(
    canFocus ? null : `Read-only session - ${accessPhrase("control.capture")} required`,
    !cam ? "No camera is connected - assign one on ADD A DEVICE" : null,
    seqOwnsCamera ? "A sequence owns the camera - stop it first" : null,
    sweeping ? "Autofocus owns the camera until the sweep finishes" : null,
  );
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
    laneCapture.lockedReason, laneLooping.lockedReason, flowOwns,
  );
  const coarseReason = first(
    focuserReason, capRoleCamera.lockedReason, laneCapture.lockedReason,
    looping ? "A capture loop is running - press Stop first" : null,
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
    looping ? first(seqOwnsCamera ? "A sequence owns the camera - stop it first" : null,
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
      showToast("error", e.message);
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
    capRole.lockedReason, capRoleCamera.lockedReason,
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

      {/* 3. The jogs. */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 6 }}>
        {jog(-100, "IN 100")}
        {jog(-10, "IN 10")}
        {jog(10, "OUT 10")}
        {jog(100, "OUT 100")}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 6 }}>
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
      </Card>

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
            <AutofocusVerdict
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
