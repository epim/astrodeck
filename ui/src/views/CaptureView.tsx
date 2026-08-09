import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  useStore, useStatus, usePolar, useLivePreviewId, useSequence, useLastLight,
  usePhotometry, usePreview, useEgainLearn, useFrameDraft, useConfig,
} from "../store";
import { LivePreview } from "../components/preview/LivePreview";
import CameraDial from "../components/ui/CameraDial";
import { cameraDialCategories } from "../components/ui/CameraPickers";
import GuideFramePreview from "../components/GuideFramePreview";
import {
  Field, Led, LockedChip, LockedNote, Panel, SegmentedControl, Stat, Toggle,
} from "../components/ui";
import { accessPhrase, useCanControlCapture } from "../lib/caps";
import { isExposureInvalid } from "../lib/exposure";
import { CAPTURE_PRESETS } from "../lib/capturePresets";
import PickerButton from "../components/ui/PickerButton";
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
import { TargetField } from "../components/capture/TargetField";
import { captureFilterDialCategory } from "../components/capture/captureFilterDial";
import { filterMotion, type FilterCommand } from "../lib/filterSlots";
import { warmReadout } from "../lib/cooling";
import { useBusyLanes } from "../lib/useBusy";
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

// How long the click-side "starting" latch may outlive the POST.
//
// Loop and Live View are the two controls whose engaged look is pure SERVER
// truth (`status.looping`, `status.live_stack_active`) and therefore up to one
// 2 s status frame behind the tap. `pending` used to be dropped the instant the
// POST resolved (~40 ms), so for the rest of that frame the loop WAS running
// and its own button looked untouched and re-pressable. The second tap is not a
// harmless duplicate: `/api/capture/loop` -> hub.start_loop CANCELS the exposure
// in progress and awaits its teardown before spawning a replacement (a 300 s sub
// is silently thrown away), and a second Live View tap re-enters the START
// branch — `liveStackOn` is still false — and assigns a brand-new LiveStacker,
// discarding the accumulated stack that is the whole point of the feature.
//
// So the latch is held from ACCEPTANCE until the rig confirms, with a grace so a
// start that never takes cannot latch the control for the night. Same shape and
// the same 6 s as lib/useBusy's `useBusyOrPending`; written here rather than
// with that hook because Stop has to be able to DROP the latch, which the hook
// does not expose, and because the truth each control hands over to is the one
// it already renders from rather than a second signal beside it.
const START_CONFIRM_GRACE_MS = 6000;

// UX-28: a cooler set-point outside this range (or blank/non-numeric) is almost
// certainly a typo; block it rather than POST a NaN that serializes to null.
const COOLER_MIN_C = -60;
const COOLER_MAX_C = 40;

// ------------------------------------------------- guide-preview verdict (#115)
/** What to say under the Guide cam panel about the picture in it, or null.
 *
 * status.guide_camera carries FOUR distinguishable outcomes and this screen used
 * to read one of them. The hub has published `preview_ok` since the #115 fix,
 * and its own docstrings describe the third state "as delivered to the user" —
 * but nothing above the server declared or read the key, so the state existed
 * only inside the process that claimed to have shipped it. That is the same
 * shape as the bug the fix was for: a positive claim outrunning its evidence.
 *
 * Written as a function rather than nested ternaries in JSX so the four cases
 * are visible at once and a fifth cannot be added by accident.
 *
 * SCOPE, stated because the last version of this claimed delivery without it:
 * <GuideFramePreview/> is mounted on THREE screens — here, GuideView and
 * PolarView — and this line is rendered under exactly one of them. Those two
 * views and the component itself are owned by other lanes this run and are not
 * edited here, so on Guide and Polar all four outcomes are still invisible.
 * The line belongs inside GuideFramePreview, which owns the picture, the poll
 * and the open/closed state; putting it there fixes all three at once and is
 * the follow-up this comment exists to hand over.
 *
 * @param srcName the device the SERVER says produced the frame
 *   (status.guide_camera.preview_source), NOT guide_camera.name. The hub picks
 *   those two by opposite rules — name prefers the guide-camera device, the
 *   preview prefers a connected guider — so on a rig with both, `name` is the
 *   camera that was never asked while the bytes came from the guider.
 */
function guidePreviewLine(reason: string, ok: boolean | undefined, srcName: string):
  { tone: string; text: string } | null {
  // A refusal has no picture to qualify, so it wins outright and says which of
  // the several possible nothings this is, in the server's own words.
  if (reason) return { tone: "text-warn", text: reason };
  // Checked. Says the one thing the picture cannot say about itself: a guide
  // frame is mostly black by nature, so "it looks dark" is evidence of nothing —
  // what the user needs to know is whether the darkness was MEASURED or merely
  // encoded. On 2026-08-01 it was encoded, from a buffer with no variation in it.
  if (ok === true) {
    return { tone: "text-dim",
             text: "Checked — this frame varies, so a dark preview is the sky and not an empty buffer." };
  }
  // Delivered, and the server could not read the bytes it forwarded. Not a
  // refusal (a browser may render what our decoder would not open) and not a
  // vouching either — the honest middle, which used to be published as silence
  // and was therefore indistinguishable from nobody having asked.
  if (ok === false) {
    return { tone: "text-warn",
             text: `Not checked — ${srcName} sent bytes this server could not decode. Your browser may render them; nothing here vouches for what is in them.` };
  }
  // Nobody has asked this camera in the last few seconds — the normal state of a
  // collapsed panel. Silence, because a permanent "not asked yet" line under an
  // idle panel reads as a fault on a rig that has none, and because the case it
  // used to be confused with (above) now says so itself.
  return null;
}

export default function CaptureView() {
  const status = useStatus();
  const polar = usePolar();
  const sequence = useSequence();
  const liveId = useLivePreviewId();
  const showToast = useStore((s) => s.showToast);
  const noteLightFrame = useStore((s) => s.noteLightFrame);
  const lastLight = useLastLight();
  const canCapture = useCanControlCapture(); // viewer => preview visible, controls read-only
  // #182: `solve_saved_lights` is what decides WHICH "not identified yet" the
  // target field explains — "nothing has solved since the slew" or "per-frame
  // solving is switched off", which have different fixes.
  const config = useConfig();
  const setView = useStore((s) => s.setView);

  // --- NOV-4 photometry profile + Suggest settings (photometry/SNR design §3 Task 4) ---
  const photometryProfile = usePhotometry();
  const setPhotometry = useStore((s) => s.setPhotometry);
  const livePreview = usePreview();

  // --- the `capture` frame scope (#176) --------------------------------------
  // These were four `useState`s seeded from "2"/"120"/"30"/"1": invisible to
  // every other screen, and back to those constants on every reload. They are
  // now the shared `capture` scope — but each box keeps a local DRAFT STRING,
  // because the guards below (`isExposureInvalid`, `gainInvalid`) read the RAW
  // string so that "", "1e9" and "-3" can block the shutter. Bind an input to a
  // number and all three stop guarding, and a blank box becomes a 0 s exposure:
  // the blank frame plus the misleading "few stars" that CAP-02 was filed for.
  //
  // So: the draft holds the string, the store only ever holds a parsed number,
  // and a commit (blur / Enter / before a shot) moves one to the other.
  const expDraft = useFrameDraft("capture", "exposure_s");
  const gainDraft = useFrameDraft("capture", "gain");
  const offsetDraft = useFrameDraft("capture", "offset");
  const binDraft = useFrameDraft("capture", "binning");
  const exposure = expDraft.text;
  const gain = gainDraft.text;
  const offset = offsetDraft.text;
  const binning = binDraft.text;
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  /** A DECISION (a preset, a dial pick, a prefill) — not a keystroke — so it
   *  goes straight to the scope and every draft follows it. */
  const setCapture = (patch: Parameters<typeof setFrameSettings>[1]) =>
    setFrameSettings("capture", patch);
  /** Push every pending draft into the scope. Called before a shot so what the
   *  shutter uses and what the rest of the app can see are the same numbers,
   *  even when the operator never left the field they typed in. */
  const commitDrafts = () => {
    expDraft.commit(); gainDraft.commit();
    offsetDraft.commit(); binDraft.commit();
  };
  // UX #5: this defaulted to OFF, so a beginner could press FIRST LIGHT then
  // LOOP, watch pictures appear all night, and find nothing on disk in the
  // morning — the switch carried no help text, no (i), and no mention in the
  // setup checklist. The novice-safe default is to KEEP what you shot; the
  // expert case (framing/test frames you don't want to keep) is one tap away
  // and is now the thing that's explained, rather than the other way round.
  const [save, setSave] = useState(true);
  // #182 — the operator's typed name, IN THE STORE. It used to be
  // `useState("")` here, and `App.tsx` renders <ViewBoundary key={view}/>, so
  // every tab switch remounted this view and silently wiped a name the operator
  // had typed. The derived identification is a DIFFERENT value that lives on
  // `preview.field`; the two are never merged (see TargetField's header).
  const target = useStore((s) => s.captureTarget);
  const setTarget = useStore((s) => s.setCaptureTarget);
  // The cooler set-point box. It used to be a hardcoded "-10" that never looked
  // at the camera: on a rig already holding -20 the panel read "target -20.0"
  // beside a box saying -10, and Set — which reads as "apply what is shown" —
  // commanded -10, warming the sensor and buying another ten-minute cool-down
  // nobody asked for. It now FOLLOWS the device (effect below) until somebody
  // types in it, the same draft-then-commit shape the dew and power sliders
  // use: the device wins whenever nobody is editing, and a typed value survives
  // every 2s status frame until it is sent.
  const [coolerTarget, setCoolerTargetRaw] = useState("-10");
  const coolerTargetEdited = useRef(false);
  const setCoolerTarget = (v: string) => {
    coolerTargetEdited.current = true;
    setCoolerTargetRaw(v);
  };
  // The dew slider is a COMMAND, not a reading: no camera backend exposes a
  // dew-heater read-back and the hub publishes only `has_dew_heater`, so the
  // level after a reload is genuinely unknown. `dewSent` is what THIS browser
  // last got the server to accept — the only thing this screen can honestly
  // say about the heater — and the caption below the slider says so.
  const [dew, setDew] = useState(0);
  const [dewSent, setDewSent] = useState<number | null>(null);
  const dewDirty = useRef(false); // an edit is pending a release
  const [filterEditOpen, setFilterEditOpen] = useState(false); // UX-05 slot-name modal
  // The filter change this session asked for, and a 1Hz clock to age it.
  // Same shape and the same reason as FocusView's `cmd`: POST
  // /api/filterwheel/position spawns a task and answers immediately, so without
  // holding the request there is nothing on screen between the tap and the
  // carousel landing several seconds later. See lib/filterSlots.filterMotion.
  const [filterCmd, setFilterCmd] = useState<FilterCommand | null>(null);
  const [filterNow, setFilterNow] = useState(() => Date.now());
  // Photometry fields are a once-per-camera setup, so they start closed.
  const [photAdvanced, setPhotAdvanced] = useState(false);
  const [camAdvanced, setCamAdvanced] = useState(false); // Advanced disclosure (egain)
  // Live View's satellite-trail rejection. Held here, not in the panel, because
  // the START request is issued by onLiveView below.
  const [liveClipSigma, setLiveClipSigma] = useState(4);
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
  // Have we SEEN the rig report a capture lane since the exposure now on screen
  // was accepted? Declared here rather than beside the effect that reads it
  // (external abort, UX #11) because `arm` below has to clear it — see there.
  const sawCaptureLane = useRef(false);
  // The status frame that was on screen when that exposure was accepted, so the
  // effect can tell "the rig has answered" from "the previous frame's lane is
  // still standing". Read through a ref, not `arm`'s closure: the POST resolves
  // ~40 ms later and a frame may have landed in between.
  const armStatusRef = useRef<unknown>(null);
  const statusRef = useRef(status);
  statusRef.current = status;

  const cam = status?.camera;
  // UX-27: offer bins 1..max_bin from the camera's reported ceiling instead of a
  // hardcoded [1,2,4]. Clamp to a sane 1–8 in case a backend reports garbage.
  const maxBin = Math.min(8, Math.max(1, cam?.max_bin ?? 4));
  const binOptions = Array.from({ length: maxBin }, (_, i) => i + 1);
  // The fan-out dial over the preview edits the SAME four boxes the Exposure
  // panel below does — one builder, one set of presets (components/ui/
  // CameraPickers). Offset is a text entry there too, because its useful
  // values are a continuum a preset ring cannot cover.
  // The filter in the beam RIGHT NOW, by name. `cameraDialCategories` requires
  // it (#176) so that no dial can render a pin as though it were the wheel.
  const fwCurrentName = (() => {
    const w = status?.filterwheel;
    return typeof w?.position === "number" ? w.names?.[w.position] ?? null : null;
  })();
  // Capture's own FILT ring (#181/#179) — a COMMAND that moves the wheel now,
  // and the one filter control in the app that offers the blackout slots. Both
  // rules, and why they are exceptions rather than oversights, live in
  // components/capture/captureFilterDial.ts beside their tests.
  //
  // The Filter Wheel panel below is NOT removed: the operator has accepted the
  // duplication for now, and the panel additionally carries the slot-name editor
  // the dial has no room for.
  const captureFilterCategory = captureFilterDialCategory(
    status?.filterwheel, (slot) => moveFilterTo(slot));

  const captureDial = cameraDialCategories({
    values: {
      exposure_s: Number(exposure) || 2, gain: Number(gain) || 0,
      binning: Number(binning) || 1, offset: Number(offset) || 0,
    },
    maxBin: cam?.max_bin ?? 4,
    // No `filters`/`onFilter` here: the shared builder DROPS blackout slots, on
    // purpose and for a good reason (a focus sweep through a carrier with no
    // glass hangs). Capture is the one screen where parking on the blackout slot
    // is a deliberate workflow — it is how you shoot darks with a wheel — so its
    // FILT ring is built below instead, from the full slot list, with the
    // blackout slots NAMED as such.
    currentFilter: fwCurrentName,
    onExposure: (v) => setCapture({ exposure_s: v }),
    onGain: (v) => setCapture({ gain: v }),
    onBinning: (v) => setCapture({ binning: v }),
    onOffset: (v) => setCapture({ offset: v }),
  }).concat(captureFilterCategory ? [captureFilterCategory] : []);

  const cooler = cam?.cooler; // CoolerInfo | undefined (older status / no cooler)
  // Warm-down ramp (2026-08-04). Warming now takes ~10 minutes instead of being
  // instantaneous, so the panel has to show it: a Warm button that looks like it
  // did nothing gets pressed again, or gets "fixed" by pressing Cool.
  const warm = warmReadout(cam?.warm);
  // Track the camera's own set-point while the box is untouched, so what Set
  // would command and what the camera is holding can never silently disagree.
  // Rounded to 0.1 °C because that is the precision the Stat beside it prints;
  // a raw float would put "-19.999999" in an editable field.
  //
  // ONLY WHILE THE CAMERA IS ACTUALLY HOLDING THAT NUMBER. `cooler.target_c` is
  // the DRIVER's live set-point, not the user's intent, and there are two states
  // where those are different things:
  //   - during a warm ramp, hub._warm_ramp re-asserts set_cooler(True, setpoint)
  //     every 15 s while walking the set-point from -20 up to ambient, so an
  //     unconditional follow drags the box up with it and "Set" — pressed to
  //     abort the ramp, which hub.py:382 documents as an expected flow — would
  //     command the ramp's own -3.2 instead of the -20 the user is looking at;
  //   - after it, the driver keeps reporting the last asserted set-point, so a
  //     cooler that is OFF at +12 °C would put room temperature in the box and
  //     "Cool" would switch the TEC on against an above-ambient target. That one
  //     is persistent — it survives to the next evening.
  // Following only an ON, not-warming cooler keeps finding #13's case (a rig
  // actively holding -20) and drops both of these.
  const deviceTargetC = cooler?.target_c;
  const coolerHolding = !!cooler?.on && !warm?.active;
  useEffect(() => {
    if (coolerTargetEdited.current || deviceTargetC == null || !coolerHolding) return;
    setCoolerTargetRaw(String(Number(deviceTargetC.toFixed(1))));
  }, [deviceTargetC, coolerHolding]);
  // UX-28: only send a finite, in-range set-point. Number("") is 0 and
  // Number("x") is NaN (→ null over JSON); guard both so "Cool" never posts
  // target_c:null with on:true.
  const coolerTargetNum = Number(coolerTarget);
  const coolerTargetInvalid =
    coolerTarget.trim() === "" || !Number.isFinite(coolerTargetNum) ||
    coolerTargetNum < COOLER_MIN_C || coolerTargetNum > COOLER_MAX_C;
  // Is there a set-point in the box that the camera has NOT been told about?
  //
  // This is the whole defect window of the Cool/Set button. While the cooler is
  // on and the box is untouched the effect above keeps the two equal, so there
  // is nothing owed; the moment somebody types -25 over a camera holding -20,
  // the button that would send it is the only thing on the panel that can say
  // so. It used to say the opposite — accent chrome plus aria-pressed="true"
  // driven by `cooler.on`, i.e. lit BECAUSE the cooler was running, which is the
  // one thing it does not control.
  //
  // Derived from the two numbers rather than from `coolerTargetEdited`, which is
  // a ref and so cannot drive a render at all; this also self-corrects when the
  // driver clamps the request, and when another client changes the set-point.
  // Gated on `coolerHolding` for the same reason the tracking effect is: during
  // a warm ramp the two differ BY DESIGN (the ramp walks the driver's set-point
  // to ambient) and Set there means "abort the ramp", not "apply an edit".
  const coolerEditPending =
    coolerHolding && !coolerTargetInvalid && deviceTargetC != null &&
    Math.abs(coolerTargetNum - deviceTargetC) >= 0.05;

  // Cool-down progress (2026-08-07). Cooling has a target and a measurable
  // rate (this TEC was clocked at ~5 °C/min pulling down), so the ramp is
  // honestly determinate: fraction of the gap closed since the ramp started,
  // and an ETA from the rate actually observed over the last minute — never
  // an assumed one (#154 is what assuming an ambient cost). Client-side ONLY
  // from numbers the status poll already carries.
  const coolTemp = cam?.temperature ?? null;
  const coolingToTarget = !!cooler?.on && !warm?.active && !cooler?.at_target
    && coolTemp != null && deviceTargetC != null && coolTemp > deviceTargetC + 0.3;
  const coolHist = useRef<Array<[number, number]>>([]);
  const coolStartTemp = useRef<number | null>(null);
  useEffect(() => {
    if (coolTemp == null) return;
    const now = Date.now();
    coolHist.current = [...coolHist.current.filter(([ts]) => now - ts < 120_000),
                        [now, coolTemp]];
  }, [coolTemp]);
  useEffect(() => {
    if (coolingToTarget && coolStartTemp.current == null) {
      coolStartTemp.current = coolTemp;
    } else if (!coolingToTarget) {
      coolStartTemp.current = null;
    }
  }, [coolingToTarget, coolTemp]);
  let coolPct: number | null = null;
  let coolEtaMin: number | null = null;
  if (coolingToTarget && coolTemp != null && deviceTargetC != null) {
    const start = coolStartTemp.current ?? coolTemp;
    const span = start - deviceTargetC;
    if (span > 0.5) {
      coolPct = Math.max(0, Math.min(100, ((start - coolTemp) / span) * 100));
    }
    const h = coolHist.current;
    const anchor = h.find(([ts]) => Date.now() - ts > 20_000);
    if (anchor) {
      const ratePerMin = (coolTemp - anchor[1]) / ((Date.now() - anchor[0]) / 60_000);
      if (ratePerMin < -0.1) {
        coolEtaMin = (coolTemp - deviceTargetC) / -ratePerMin;
      }
    }
  }
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

  // Which preset the settings on screen ACTUALLY ARE — not which one was last
  // tapped.
  //
  // This used to be `lastPreset`, a record of the last tap, and every other path
  // that writes these four boxes left it standing: Suggest settings rewrites the
  // exposure, "Match last lights" and the end-of-loop darks prefill rewrite all
  // four (and flip frameType to Dark/Bias, where a "Galaxy" preset is nonsense),
  // and a hand edit rewrites whichever one you typed in. The collapsed button
  // could be read as shorthand for "the last one you applied"; the listbox row
  // could not — `aria-selected` plus the • glyph is a selection assertion, and a
  // screen reader announced "Galaxy, selected" while none of Galaxy's four
  // values was still set. Derived instead, so the mark can only ever name
  // settings that are loaded, and says "custom" when they match no preset.
  const activePreset = CAPTURE_PRESETS.find((p) =>
    Number(exposure) === p.exposure_s && Number(gain) === p.gain &&
    Number(offset) === p.offset && Number(binning) === p.binning) ?? null;

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
    setCapture({ exposure_s: s.suggestedS });
    showToast("success", `Suggested ${s.suggestedS}s — ${s.reason}`);
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  /** Move the wheel to `slot`, and hold the request so BOTH controls that can
   *  issue it — the dial's FILT ring and the Filter Wheel panel — narrate the
   *  same move. Two copies of this would be two `filterCmd` writers disagreeing
   *  about which slot is in flight; there is one, and they share it. */
  const moveFilterTo = (slot: number) => {
    const w = status?.filterwheel;
    if (!w) return;
    setFilterCmd({ slot, startedAt: Date.now(), from: w.position });
    setFilterNow(Date.now());
    act(async () => {
      try {
        await api.post("/api/filterwheel/position", { position: slot });
      } catch (e) {
        // The command never reached the wheel, so there is no move to narrate —
        // leaving it would pulse "→ L" over a request the server refused.
        setFilterCmd(null);
        throw e;
      }
    });
  };

  // What the server knows about the guide preview right now:
  //
  //   preview_reason   no picture, and which of the several nothings this is
  //   preview_ok true  a picture whose pixels the server decoded and found to vary
  //   preview_ok false bytes it forwarded but could not inspect — no claim either way
  //   neither key      nobody has asked this camera in the last few seconds
  //   preview_source   which device produced that frame (rides with preview_ok)
  //
  // All of these expire server-side, so none ever describes a camera that has
  // since been fixed or unplugged.
  //
  // The name comes from `preview_source` — the device that answered — and NOT
  // from `guide_camera.name`, which is chosen by the opposite rule (device
  // first, guider second) and so names the wrong instrument on any rig carrying
  // both. `preview_ok: false` can only come from the guider branch, so the
  // sentence is wrong on precisely the rigs that have a guide camera assigned
  // as well — where it read "ZWO ASI sent bytes we could not decode" about a
  // camera the server never asked — and happened to be right only where there
  // is no guide-camera device for `name` to disagree with. Being right by the
  // absence of a second device is not the same as being right.
  const guidePreviewLineNow = guidePreviewLine(
    status?.guide_camera?.preview_reason ?? "",
    status?.guide_camera?.preview_ok,
    status?.guide_camera?.preview_source ?? "the guide camera");

  // --- filter wheel: what the Slot button reads while the carousel turns ---
  // `moving` rides the raw status event (hub publishes status.filterwheel.moving
  // in its own try, so it is ABSENT on a backend that cannot say — undefined
  // means "watch the position", not "stopped").
  const fw = status?.filterwheel;
  const fwMotion = filterMotion(
    filterCmd, fw?.position, fw?.moving, fw?.names ?? [], filterNow);
  // Age the command only while something is unresolved — a settled wheel must
  // not keep a timer alive behind a screen the user is watching for an hour.
  const fwWaiting = !!filterCmd && !fwMotion.problem && fw?.position !== filterCmd.slot;
  useEffect(() => {
    if (!fwWaiting) return;
    const t = setInterval(() => setFilterNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [fwWaiting]);
  // Drop the command once the wheel has landed on it: from here the slot NAME is
  // the whole answer, which is exactly what was asked for.
  useEffect(() => {
    if (filterCmd && fw?.position === filterCmd.slot && !fw?.moving) setFilterCmd(null);
  }, [filterCmd, fw?.position, fw?.moving]);
  // Which row carries the dot. Mid-turn that is the slot you ASKED for (the
  // button agrees, and the reported position is the old slot — or, on an ASCOM
  // wheel, the clamped -1 sentinel, i.e. nothing at all). With the wheel turning
  // for a sequence rather than for us, no row: marking a slot we cannot know
  // would be the guess this whole change exists to stop.
  const fwSelected = fwMotion.pulsing
    ? (filterCmd ? [String(filterCmd.slot)] : [])
    : [String(fw?.position ?? -1)];

  // UX round-4 S4 ("success reported before it is earned"). The exposure bar used
  // to be armed BEFORE the POST, and a 409 deliberately left it running on the
  // theory that a 409 always means "the capture lock is held, so SOME capture is
  // genuinely in flight". It doesn't: `hub.require("camera")` raises DeviceError
  // and `_err` maps that to 409 too, so "no camera connected" is byte-identical
  // at the client to "a capture is already running". Measured on a rejected
  // Single: "Exposing…" for the exposure, then a striped "DOWNLOADING…" bar for
  // the full 60 s watchdog, for a frame that never existed.
  //
  // The fix is ordering, not error parsing: arm the bar only once the server has
  // ACCEPTED the exposure. A rejected request then has nothing to unwind (so it
  // cannot stomp a genuinely-running frame either — the case the old comment was
  // protecting), and the tap is acknowledged meanwhile by the control's own
  // "Starting…" state rather than by a fictional frame.
  //
  // `pending` is ALSO the engaged state of the control that started it, and it
  // outlives the POST for Loop and Live View — see START_CONFIRM_GRACE_MS and
  // the handover effect below.
  const [pending, setPending] = useState<null | "single" | "loop" | "live">(null);
  // Bumped by Stop, so a request that was already in flight when the user
  // pressed Stop cannot come back and arm a bar for a frame they cancelled.
  const armGenRef = useRef(0);
  const arm = async (kind: "single" | "loop" | "live", path: string, payload: object,
                     len: number) => {
    // Commit any box the operator typed in and never blurred, so the numbers
    // this shot uses are the numbers every other surface can see. Without it
    // there is a window where Capture shoots 300 s and Focus still reads 2 s —
    // the same disagreement, in miniature.
    commitDrafts();
    const gen = ++armGenRef.current;
    setPending(kind);
    try {
      await api.post(path, payload);
    } catch (e) {
      showToast("error", (e as Error).message);
      setPending(null);
      return; // nothing was armed — never draw progress for a refused exposure
    }
    if (armGenRef.current !== gen) { setPending(null); return; }
    // "We have seen the lane" has to mean "seen it since THIS POST", or the
    // guard below protects the wrong exposure. A frame that ends the honest way
    // — a new preview id — never delivers the lane-ABSENT frame that clears the
    // flag, so it stays set from the frame before; the next status frame that
    // was serialised before this POST then reads as "the rig stopped capturing"
    // and kills a bar armed 40 ms ago. Cleared HERE, on acceptance, and not in
    // beginExposure(): Loop restarts through beginExposure once per frame, and
    // forgetting the lane between frames would leave a loop stopped on another
    // device narrating nothing at all.
    sawCaptureLane.current = false;
    armStatusRef.current = statusRef.current;
    beginExposure(len);
    // Single's engaged state becomes LOCAL on the line above — the bar is armed,
    // and the button reads "Exposing…" off `phase` from here — so its latch has
    // done its job. Loop and Live View have no local truth to hand over to: they
    // render from `status.looping` / `status.live_stack_active`, which is a
    // status frame away, so their latch stays up until the effect below sees the
    // rig agree (or the grace expires).
    if (kind === "single") setPending(null);
  };

  // Hand the starting latch over to the rig, or expire it. The moment the server
  // reports the loop / the live stack, the control's own engaged state is true
  // and the latch is dropped; if the rig never says so — a start that failed
  // after acceptance, a dropped link, an op that ended inside one frame — the
  // grace releases it rather than leaving the control locked all night. This is
  // the `finally` that used to run 40 ms after the tap, moved onto the only
  // signal that can honestly retire it.
  useEffect(() => {
    if (pending == null || pending === "single") return;
    if (pending === "loop" && looping) { setPending(null); return; }
    if (pending === "live" && liveStackOn) { setPending(null); return; }
    const t = window.setTimeout(() => setPending(null), START_CONFIRM_GRACE_MS);
    return () => window.clearTimeout(t);
  }, [pending, looping, liveStackOn]);

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
  // Kept for a server too old to publish `busy_lanes`; where the rig can say,
  // the lane effect below supersedes this and also covers the exposing phase.
  useEffect(() => {
    if (!looping && phase !== "idle") {
      // give the last in-flight frame a beat; if not exposing, just idle.
      if (phase === "downloading") setPhase("idle");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [looping]);

  // ------------------------------------------------- external abort (UX #11)
  // The bar is armed by OUR POST and retired by a NEW preview id. Neither
  // happens when the capture is stopped somewhere else — the other tablet, the
  // phone in a pocket, the rig's own Stop. The POST resolved a minute ago and
  // no frame will ever arrive, so this screen counted a frame nobody was
  // taking down to zero, flipped to "downloading…", and sixty seconds later
  // accused a healthy camera of dropping it.
  //
  // The rig publishes the answer on every status frame: `capture` while a
  // single frame is in flight, `looping` while a loop is (lib/useBusy). Its
  // DISAPPEARANCE is the honest end of the frame — but only once we have SEEN
  // it, because a status frame older than our POST says nothing about our POST,
  // and acting on it would kill the bar we armed 40 ms ago. `undefined` (a
  // server that does not publish lanes) is an unknown answer, never "idle".
  //
  // "Seen" is per-exposure and per-FRAME, which takes two guards, not one:
  //   - `arm` clears the flag on acceptance, so the lane the PREVIOUS frame was
  //     seen holding cannot vouch for this one (a frame that ends the honest
  //     way, via a new preview id, never delivers the lane-absent frame that
  //     would otherwise clear it);
  //   - and the status frame that was ALREADY on screen when we posted cannot
  //     re-arm it. That frame is the previous frame's lane still standing — it
  //     is 0-2 s stale by construction, and re-running this effect on the phase
  //     change would otherwise read it as live confirmation of an exposure the
  //     rig has not answered for yet.
  // Identity, not a timestamp: handleEvent replaces the whole `status` object by
  // reference on every status poll and leaves it alone for guide/focus ticks
  // (store.ts:1918), so `status === armStatusRef.current` is exactly "no status
  // frame has arrived since the POST was accepted".
  const lanes = useBusyLanes();
  const captureLane = lanes == null
    ? undefined
    : lanes.includes("capture") || lanes.includes("looping");
  useEffect(() => {
    if (captureLane === undefined) return;
    if (status === armStatusRef.current) return; // predates our POST
    if (captureLane) { sawCaptureLane.current = true; return; }
    if (!sawCaptureLane.current) return;
    sawCaptureLane.current = false;
    if (phase === "idle") return; // finished the honest way — a frame arrived
    setPhase("idle");
    showToast("info",
      "Capture stopped on the rig — this exposure ended without a frame.");
    // `status` is a dep so the guard above can tell one frame from the next;
    // it is one identity compare per 2 s frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, captureLane, phase]);

  // tear down on unmount
  useEffect(
    () => () => {
      if (tickRef.current != null) window.clearInterval(tickRef.current);
    },
    [],
  );

  const onSingle = () => {
    if (captureBlocked || !canCapture || exposureInvalid || gainInvalid || pending) return;
    void arm("single", "/api/capture", body, exposureS);
  };
  const onLoop = () => {
    if (captureBlocked || !canCapture || exposureInvalid || gainInvalid || pending) return;
    // A fresh Light loop starting is a new batch — clear the once-per-batch
    // darks-nudge guard so onStop can offer again for THIS batch.
    if (frameType === "Light") offeredRef.current = false;
    void arm("loop", "/api/capture/loop", body, exposureS);
  };
  const onStop = () => {
    if (!canCapture) return;
    setStopPressed(true);
    window.setTimeout(() => setStopPressed(false), 220);
    armGenRef.current++;   // void any accept still in flight (see `arm`)
    setPending(null);      // …and drop the starting latch it may have raised
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
        setCapture({ exposure_s: Number(p.exposure), gain: Number(p.gain),
                     offset: Number(p.offset), binning: Number(p.binning) });
        if (p.coolerTarget) setCoolerTarget(p.coolerTarget);
        showToast("info", "Darks set up — cap the scope, then press Single or Loop.");
      });
    }
    act(() => api.post("/api/capture/stop"));
  };
  // NOV-1 Live View: toggle arms the server-side stacker + starts the loop;
  // toggling off disarms + stops. Reset clears the accumulator, keeps arming.
  const onLiveView = () => {
    if (!canCapture) return;
    // The OFF half runs first and is deliberately not gated on `pending`. Since
    // that latch now lives for up to a status frame, gating the stop path on it
    // would leave a lit "Live View · on" that silently did nothing for six
    // seconds — the defect this whole change is about, reintroduced from the
    // other side. Nothing about an unrelated start makes ending the stack wrong.
    if (liveStackOn) {
      // Retire the bar here, the way Stop does: this tap ends the loop, so
      // letting the lane effect discover it would narrate the user's own press
      // back at them as "stopped on the rig".
      armGenRef.current++;
      setPending(null);
      setPhase("idle");
      act(() => api.post("/api/capture/livestack/stop"));
      return;
    }
    if (captureBlocked || exposureInvalid || gainInvalid || pending) return;
    void arm("live", "/api/capture/livestack/start",
             { ...body, frame_type: "Light", clip_sigma: liveClipSigma }, exposureS);
  };
  const onResetStack = () => { if (canCapture && liveStackOn) act(() => api.post("/api/capture/livestack/reset")); };

  // --- dew heater: draft while dragging, send once on release --------------
  // The commit used to hang off onMouseUp + onTouchEnd, which between them miss
  // the keyboard entirely: a user who focused the slider and held Right watched
  // the number climb to 90 and sent nothing. `onPointerUp` covers mouse, touch
  // AND pen in one handler and `onKeyUp` covers the arrows — the same pair
  // PowerView's PWM sliders already use.
  const commitDew = (v: number) => {
    if (!canCapture || !dewDirty.current) return; // nothing was edited
    dewDirty.current = false;
    act(async () => {
      await api.post("/api/camera/dew-heater", { power: v });
      setDewSent(v);
    });
  };
  // An interrupted drag (the browser claiming the gesture for a scroll, the
  // page being hidden) delivers pointercancel INSTEAD of pointerup, so nothing
  // is sent. Snap back to the last level that WAS sent rather than leaving the
  // thumb parked on a number the heater never heard.
  const cancelDew = () => {
    if (!dewDirty.current) return;
    dewDirty.current = false;
    setDew(dewSent ?? 0);
  };

  const inFlight = phase !== "idle";
  const fillPct = phase === "exposing"
    ? Math.min(100, (elapsed / expLenRef.current) * 100)
    : 100;
  const remaining = Math.max(0, expLenRef.current - elapsed);
  // The request is out but unanswered: the camera has NOT accepted the exposure
  // yet, so the control says "Starting…" and no bar is drawn. Distinct from
  // `inFlight`, which means a frame the server accepted is actually running.
  // The same sentence is FocusPod's and FocusView's (one vocabulary for one
  // state), so it stays on the branch it is literally true of — Single, whose
  // latch is dropped the moment the POST is accepted. Loop and Live View hold
  // theirs PAST acceptance, waiting on a different answer, and say so below.
  const startingReason =
    "Waiting for the camera to accept this exposure — no frame has started yet";

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
  // A start the rig has accepted but not yet confirmed still owns the camera,
  // and `pending` already blocks every one of these handlers. Say so instead of
  // leaving a live-looking button whose press does nothing for a status frame.
  // The control that RAISED the latch has its own "Starting…" branch below and
  // is matched before this reason is ever consulted.
  const startPendingReason =
    pending ? "A capture is starting — waiting for the camera to answer" : null;
  // Shared by every control that starts an exposure.
  const exposeReason =
    readOnlyReason
    ?? (polarBusy ? "Polar alignment owns the camera right now"
      : seqOwnsCamera ? "A sequence owns the camera — stop it first"
        : settingsReason)
    ?? startPendingReason;
  const singleReason =
    exposeReason ?? (looping ? "A capture loop is running — press Stop first" : null);
  const loopReason = exposeReason;
  const coolReason =
    readOnlyReason ?? (coolerTargetInvalid
      ? `Target must be a number between ${COOLER_MIN_C} and ${COOLER_MAX_C} °C`
      : null);
  // While a ramp is running the cooler is still ON (its setpoint is climbing),
  // so the old "already off" test would leave Warm live and re-pressable — which
  // is exactly the double-press the server now has to defend against. During a
  // ramp the button becomes Stop instead (see the panel below), so this reason
  // only has to cover the genuinely-idle case.
  const warmReason =
    readOnlyReason ?? (!warm?.active && cooler != null && !cooler.on
      ? "The cooler is already off" : null);

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_320px] xl:grid-cols-[1fr_360px]">
      {/* live-preview overhaul: stage + zoom/pan + stretch + overlays + filmstrip */}
      {/* h-fit IS LOad-BEARING. This div is a GRID ITEM, so without it the
          default `align-items: stretch` makes it as tall as the settings column
          beside it -- measured at 1599px against a 670px preview. CameraDial
          anchors its disc to `bottom: 12` of this box, so the dial rendered
          ~900px below the image, off the bottom of the screen, on every desktop
          viewport. It was present, visible and unreachable.
          No DOM test can catch this: jsdom has no layout, so the dial's
          ResizeObserver never fires, `box` stays null, and dialRadius returns
          the full radius in every test that has ever run. */}
      <div className="relative h-fit">
        <LivePreview />
        {/* Camera settings where the thumb is while the eye is on the frame —
            the same dial the Align reticle and the Focus stage carry. */}
        {canCapture && (
          <CameraDial
            label="Camera settings"
            summary={`${exposure}s g${gain}`}
            categories={captureDial}
          />
        )}
      </div>

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
                data-frame-field="exposure_s"
                value={exposure}
                readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
                aria-invalid={exposureInvalid}
                onChange={(e) => expDraft.setText(e.target.value)}
                onBlur={expDraft.commit}
                onKeyDown={(e) => { if (e.key === "Enter") expDraft.commit(); }}
              />
              {exposureInvalid && (
                <p className="text-[11px] text-bad mt-1">Exposure must be 0–3600s</p>
              )}
            </Field>
            <Field label={`Gain${cam?.max_gain ? ` (max ${cam.max_gain})` : ""}`} hint={HELP.gain}>
              <input
                className={`field ${gainInvalid ? "border-bad" : ""}`}
                data-frame-field="gain"
                value={gain}
                readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
                aria-invalid={gainInvalid}
                onChange={(e) => gainDraft.setText(e.target.value)}
                onBlur={gainDraft.commit}
                onKeyDown={(e) => { if (e.key === "Enter") gainDraft.commit(); }}
              />
              {gainInvalid && (
                <p className="text-[11px] text-bad mt-1">
                  Gain must be 0{cam?.max_gain ? `–${cam.max_gain}` : " or more"}
                </p>
              )}
            </Field>
            <Field label="Offset" hint={HELP.offset}>
              <input className="field" data-frame-field="offset" value={offset} readOnly={!canCapture}
                aria-readonly={!canCapture || undefined}
                onChange={(e) => offsetDraft.setText(e.target.value)}
                onBlur={offsetDraft.commit}
                onKeyDown={(e) => { if (e.key === "Enter") offsetDraft.commit(); }} />
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
                <select className="field" data-frame-field="binning" value={binning} onChange={(e) => setCapture({ binning: Number(e.target.value) })}>
                  {binOptions.map((b) => <option key={b} value={b}>{b}×{b}</option>)}
                </select>
              )}
            </Field>
          </div>

          {/* Presets — ONE button that opens the list (QA: "presets should be
               a button that opens a picker"). Six permanent 44px chips were
               most of a rail, for a control you touch once a session.
               The button and the checked row both name the preset the four
               boxes ARE (`activePreset`), not the one last tapped — see the
               derivation above; "custom" is what "these are not any preset"
               looks like, and no row carries the dot in that state. */}
          <div className="mt-3">
            <PickerButton
              label="Preset"
              summary={activePreset?.label ?? "custom"}
              options={CAPTURE_PRESETS.map((p) => ({
                id: p.id, label: p.label, hint: p.blurb,
              }))}
              selected={activePreset ? [activePreset.id] : []}
              disabled={!!readOnlyReason}
              disabledReason={readOnlyReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(id) => {
                const p = CAPTURE_PRESETS.find((x) => x.id === id);
                if (!p) return;
                setCapture({ exposure_s: p.exposure_s, gain: p.gain,
                             offset: p.offset, binning: p.binning });
                showToast("info", `Preset: ${p.label}`);
              }}
            />
          </div>

          {/* ---- Camera photometry profile (NOV-4/PRO-6 shared input, §1.3): a small
               persisted client-only egain/read-noise/bias-ADU profile. Feeds Suggest
               settings below + the Sequence/Monitor SNR readouts (photometry.ts, the
               tested core — no math duplicated here). All-zero = inert; never a wrong
               number, only an honest "add these" prompt downstream. ---- */}
          {/* Camera photometry, behind a disclosure (QA). These are gain,
               read noise and bias: three numbers you look up ONCE for a camera
               and then never touch again. They were permanently occupying the
               capture screen, above the controls used every single frame. */}
          <div className="mt-3 border-t border-line pt-3">
            <button
              type="button"
              className="label mb-2 flex items-center gap-1.5"
              aria-expanded={photAdvanced}
              onClick={() => setPhotAdvanced((v) => !v)}
            >
              <span aria-hidden>{photAdvanced ? "▾" : "▸"}</span>
              Camera photometry
            </button>
            {photAdvanced && (
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
            )}
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
              {/* #182 — was a bare text box that was the app's ONLY way of ever
                  knowing what it was pointed at. Now an override over a value
                  the sky proposes; the three states live in lib/fieldIdentity. */}
              <TargetField
                value={target}
                onChange={setTarget}
                field={livePreview?.field}
                perFrameSolving={!!config?.solve_saved_lights}
                frameType={frameType}
                readOnly={!canCapture}
                readOnlyReason={readOnlyReason}
                onOpenSolveSettings={() => setView("settings")}
              />
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
                    setCapture({ exposure_s: Number(p.exposure),
                                 gain: Number(p.gain),
                                 offset: Number(p.offset),
                                 binning: Number(p.binning) });
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
            {pending === "single" ? (
              <button
                className="btn tap-lg min-h-[56px]"
                aria-disabled aria-busy
                aria-label={startingReason}>
                Starting…
              </button>
            ) : inFlight && !looping ? (
              // `aria-busy`, NOT `aria-pressed`. Single is a shutter — a
              // momentary action with no engaged state to persist and no second
              // press that could release it — and it carried aria-disabled AND
              // aria-pressed together, so for the length of the exposure a
              // screen reader announced the shutter as a latched, unavailable
              // TOGGLE. The accent chrome and the progress bar below stay: they
              // are accurate, and it is only the affirmative claim of a pressed
              // toggle that is false. The Looping branch below keeps
              // aria-pressed, because Loop genuinely is a latched mode whose off
              // switch is Stop.
              <button
                className="btn btn-accent border-accent tap-lg min-h-[56px]"
                aria-disabled aria-busy
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
            ) : pending === "loop" ? (
              // Held from the tap until `status.looping` lands (or the grace
              // expires) — NOT until the POST resolves. Loop's engaged look is
              // pure server truth and is a status frame behind the tap, so for
              // up to two seconds the loop was running underneath a button that
              // looked untouched; the second tap that invited cancels the
              // exposure in progress and throws it away.
              <button
                className="btn tap-lg min-h-[56px]"
                aria-disabled aria-busy
                aria-label="Starting — the loop has been requested and the rig has not reported it running yet">
                Starting…
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
            {/* The control that RAISED the latch is matched first: `exposeReason`
                now names a pending start too, and a lock glyph on the button you
                just pressed says "blocked", the opposite of the truth. Same
                latch as Loop's and for the same reason — a second tap here does
                not restart anything, it assigns a fresh LiveStacker and throws
                away everything accumulated so far. */}
            {pending === "live" ? (
              <button className="btn tap min-h-[44px]" aria-disabled aria-busy
                aria-label="Starting — Live View has been requested and the rig has not reported the stack yet">
                Starting…
              </button>
            ) : exposeReason && !liveStackOn ? (
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

          {/* Satellite-trail rejection. Folded in beside the Live View buttons
              rather than given a panel of its own: it is one number, it only
              matters to somebody already using Live View, and it is applied at
              START, so this is the moment it is relevant. */}
          <details className="mt-2">
            <summary className="label cursor-pointer select-none min-h-11 flex items-center">
              Live View options
            </summary>
            <label className="flex items-center gap-2 mt-2">
              <span className="label shrink-0">reject trails above</span>
              <input
                className="field !w-20"
                type="number"
                min={0}
                max={20}
                step={0.5}
                value={liveClipSigma}
                aria-label="Satellite rejection sigma"
                onChange={(e) =>
                  setLiveClipSigma(Math.min(20, Math.max(0, Number(e.target.value) || 0)))
                }
              />
              <span className="label shrink-0">sigma</span>
            </label>
            <p className="text-[11px] text-dim leading-snug mt-1 max-w-md">
              A pixel this far above the running average is treated as a satellite
              or aircraft trail and kept out of the stack. Lower rejects more; 0
              turns it off, which is what you want if the thing you are imaging is
              itself moving.
              {liveStackOn && " Applied the next time you start Live View."}
            </p>
          </details>

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
        {/* The panel drives an <img>, so all it can observe about a failure is
            that the load errored — the server's named 404 detail never reaches
            it and every distinct nothing renders as "Guide camera frame
            unavailable". So the server's verdict rides status.guide_camera
            instead, and this is where it gets said (guidePreviewLine, above,
            holds the four cases). ONE live region, so a screen reader hears one
            announcement per change of outcome rather than one per state.

            Each outcome carries its own WORDS as well as its own colour: under
            the red night theme every token is a red, so a distinction that lives
            only in hue does not survive the theme it is most needed in (S3).

            ONE of the three screens that mount this panel — Guide and Polar
            mount it too and still show none of the four outcomes. See
            guidePreviewLine's header: the line belongs in GuideFramePreview,
            which is another lane's file this run. */}
        {guidePreviewLineNow && (
          <p role="status" aria-live="polite"
            className={`mono text-[11px] -mt-1 px-1 leading-snug ${guidePreviewLineNow.tone}`}>
            {guidePreviewLineNow.text}
          </p>
        )}

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
            {/* One picker, not one 56px button per slot. An 8-slot wheel put
                eight permanent targets on the capture screen for a control you
                touch once per filter change. The button names the slot the
                wheel is ON, so the state is still a glance — and a blackout
                slot says so in its row, because parking on it manually is
                legitimate but mistaking it for an imaging filter is not.

                While a change is in flight the button names the slot you ASKED
                for and pulses (`.blink`) until the wheel reports it has stopped
                turning. It is not a fixed-duration animation: it is driven by
                status.filterwheel.moving, so a jammed wheel stops pulsing and
                says so rather than finishing a reassuring loop on schedule. */}
            <PickerButton
              label="Slot"
              summary={fwMotion.summary}
              className={fwMotion.pulsing ? "blink !border-accent !text-accent" : ""}
              options={status.filterwheel.names.map((name, i) => ({
                id: String(i),
                label: status.filterwheel!.opaque?.[i] ? `${name} — blackout` : name,
                hint: status.filterwheel!.opaque?.[i]
                  ? "Blocks the light path — for dark frames"
                  : undefined,
              }))}
              selected={fwSelected}
              disabled={!!readOnlyReason}
              disabledReason={readOnlyReason}
              onBlocked={(r) => showToast("warning", r)}
              onPick={(id) => moveFilterTo(Number(id))}
            />
            {/* prefers-reduced-motion kills .blink, so the pulse alone is not
                allowed to be the only cue: the summary above reads "→ L" while
                turning, and a wheel that never got there says so here. */}
            {fwMotion.problem && (
              <p role="status" aria-live="polite" className="mono text-[11px] text-warn mt-2.5">
                {fwMotion.problem}
              </p>
            )}
          </Panel>
        )}
        {status?.filterwheel && (
          <FilterNamesModal
            open={filterEditOpen}
            onClose={() => setFilterEditOpen(false)}
            names={status.filterwheel.names}
            offsets={status.filterwheel.offsets ?? []}
            opaque={status.filterwheel.opaque ?? []}
            narrowband={status.filterwheel.narrowband ?? []}
            position={status.filterwheel.position}
            canLearn={!!status.focuser}
            learnDisabledReason={
              !canCapture ? "this is a read-only session"
                : captureBlocked ? "a sequence or polar alignment owns the camera"
                  : looping ? "a capture loop is running"
                    : !status.focuser ? "no focuser is connected"
                      : null
            }
            onLearn={async (refSlot, req) => {
              await api.post("/api/filterwheel/learn-offsets",
                             { ref_slot: refSlot, ...req });
              showToast("info", "Learning filter offsets…");
            }}
            onSave={async (names, offsets, opaque, narrowband) => {
              await api.post("/api/filterwheel/names",
                             { names, offsets, opaque, narrowband });
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
                {/* A ramping cooler IS on and IS holding a setpoint, so the LED
                    and the "at target" chip both read normally — and both would
                    be describing a setpoint that is deliberately walking away
                    from the one the user chose. The warming chip is what tells
                    them apart, and it outranks "at target" for the same reason. */}
                {warm?.active ? (
                  <span className="px-1.5 py-0.5 text-[10px] tracking-wider uppercase border border-accent/50 text-accent">
                    warming
                  </span>
                ) : cooler?.on && cooler.at_target && (
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

            {/* The ramp, made determinate: fraction of the gap closed since
                cooling started + an ETA from the rate actually observed. A
                cool-down is minutes of nothing visibly changing, and "is it
                even working" is the question this answers. */}
            {coolingToTarget && (
              <div className="mt-2" data-cooling-progress>
                {coolPct != null && (
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${coolPct}%` }} />
                  </div>
                )}
                <p className="text-[10px] text-faint mono mt-1" aria-live="polite">
                  cooling {coolTemp?.toFixed(1)}° → {deviceTargetC?.toFixed(1)}°
                  {coolEtaMin != null
                    ? ` · ~${coolEtaMin < 1.5 ? "a minute" : `${Math.round(coolEtaMin)} min`} left`
                    : " · measuring the rate…"}
                </p>
              </div>
            )}
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
                // NO aria-pressed. This is a one-way apply: from OFF the press
                // moves it false->true, and the second press — in the "Set"
                // identity, its accessible name having silently changed under
                // the user — cannot move it back, because the off action is the
                // separate Warm button beside it. A toggle that only latches is
                // not a toggle, and `cooler.on` is already carried honestly two
                // rows up by the LED and the word "Cooling".
                //
                // The accent chrome is repointed at the one thing this button
                // does own: whether the number in the box has been SENT. Lit now
                // means "there is a set-point here the camera has not been told
                // about" — so typing -25 over a camera holding -20 lights the
                // control that would send it, instead of finding it already
                // dressed as applied.
                <button
                  className={`btn tap min-h-[44px] w-full ${coolerEditPending ? "btn-accent border-accent" : ""}`}
                  aria-label={coolerEditPending && deviceTargetC != null
                    ? `Set — send ${coolerTargetNum} °C to the camera, which is holding ${deviceTargetC.toFixed(1)} °C`
                    : undefined}
                  onClick={() => act(async () => {
                    await api.post("/api/camera/cooler", { on: true, target_c: coolerTargetNum });
                    // Sent: the camera is the authority on this number again, so
                    // let the box resume tracking it (a driver that clamps the
                    // request must be able to say so in the field, not only in
                    // the readout beside it).
                    coolerTargetEdited.current = false;
                  })}>
                  {cooler?.on ? "Set" : "Cool"}
                </button>
              )}
              {/* Warming is no longer instantaneous — it is a ~10 minute ramp
                  (see lib/cooling.ts). So the button has two jobs: start one,
                  and get out of one. `ramp:false` is the server's explicit
                  "switch it off NOW" escape hatch; it is the OLD behaviour, and
                  it is offered because a user who wants the camera off their
                  mount in the next thirty seconds is entitled to that call —
                  deliberately, with the title saying what it costs, not by
                  accident the way the whole product used to do it. */}
              {warm?.active ? (
                <button
                  className="btn tap min-h-[44px] w-full border-accent text-accent"
                  title="Stop the ramp and switch the cooler off now. The sensor will then equalise with the air on its own."
                  onClick={() => act(async () => {
                    await api.post("/api/camera/cooler", { on: false, ramp: false });
                    showToast("info", "Warm ramp stopped — cooler off");
                  })}>
                  Stop ramp
                </button>
              ) : warmReason ? (
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
            {/* The unsent set-point, in words. The accent chrome above is the
                glance; this is the channel that survives the red palette, where
                accent-vs-plain on a 1px border is close to nothing, and it is
                also the only place the two numbers are put side by side — the
                "target" Stat shows the camera's and the box shows the user's,
                three controls apart, with nothing saying they disagree.
                Phrased as the two numbers and what the button does, NOT as "not
                sent yet": between the POST and the status frame that confirms
                it, "not sent yet" would itself be false for a second or two —
                the same shape of lie, from the other side. The camera holding a
                different number is true throughout that window. */}
            {coolerEditPending && deviceTargetC != null && (
              <p className="text-[11px] text-warn mt-2 leading-snug">
                The camera is holding {deviceTargetC.toFixed(1)} °C — Set sends
                the {coolerTargetNum} °C in the box.
              </p>
            )}
            {/* ---- warm-down ramp progress (2026-08-04). The panel's only
                 previous answer to "is it warming?" was the cooler LED going
                 off, which happened instantly because the TEC was being cut
                 dead. Now there is a ten-minute process to report, and the
                 finished state lingers ~3 min on status so this does not blink
                 out the moment it completes. ---- */}
            {warm && (
              <div className="mt-3 border-t border-line pt-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-xs ${warm.unramped ? "text-warn" : "text-ink"}`}>
                    {warm.headline}
                  </span>
                  {warm.active && (
                    <span className="mono text-[11px] text-dim">{warm.pct}%</span>
                  )}
                </div>
                {warm.active && (
                  <div className="h-2 bg-black/30 border border-line2 mt-1.5"
                       role="progressbar" aria-valuenow={warm.pct}
                       aria-valuemin={0} aria-valuemax={100}
                       aria-label="Camera warm-down progress">
                    <div className="h-full bg-[var(--accent)]"
                         style={{ width: `${warm.pct}%` }} />
                  </div>
                )}
                {warm.detail && (
                  <p className={`text-[11px] mt-1.5 ${warm.unramped ? "text-warn" : "text-dim"}`}>
                    {warm.detail}
                  </p>
                )}
              </div>
            )}

            {coolerTargetInvalid && (
              <p className="text-[11px] text-bad mt-2">
                Target must be a number between {COOLER_MIN_C} and {COOLER_MAX_C} °C
              </p>
            )}

            {cam.has_dew_heater && (
              <div className="mt-4 border-t border-line pt-3">
                <div className="flex justify-between mb-1.5">
                  <span className="label">dew heater</span>
                  {/* The slider's own position, which is what a slider readout
                      is for. What it does NOT mean is stated underneath. */}
                  <span className="mono text-xs text-accent">{dew}%</span>
                </div>
                {/* A range input has no `readOnly`, and native `disabled` would
                    drop it (and its reason) out of the a11y tree. Keep it
                    reachable, announce it as disabled, inert its handlers, and
                    state the reason underneath (UX #24). */}
                <input type="range" min={0} max={100} value={dew}
                  aria-label="dew heater power to set"
                  aria-valuetext={`${dew}%`}
                  aria-disabled={!canCapture || undefined}
                  className={`w-full h-11 accent-(--accent) touch-none ${canCapture ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                  onChange={(e) => {
                    if (!canCapture) return;
                    dewDirty.current = true; // released → commitDew sends it
                    setDew(Number(e.target.value));
                  }}
                  onPointerUp={(e) => commitDew(Number(e.currentTarget.value))}
                  onKeyUp={(e) => commitDew(Number(e.currentTarget.value))}
                  onPointerCancel={cancelDew} />
                {/* Said out loud because the silent version cost the user real
                    dew. Nothing in the stack reports a heater level back — not
                    the ASIAIR camera, not the native adapters, and the hub
                    publishes only `has_dew_heater` — so after a reload the thumb
                    sat at 0 beside a heater still running at 80, and "turning it
                    on" turned it DOWN. Until the server can answer, the screen
                    says which of the two things this number is. */}
                <p className="text-[11px] text-dim mt-1 leading-snug">
                  {dewSent == null
                    ? "Your camera doesn't report its heater level, so this starts at zero rather than where the heater is. Nothing is sent until you move it."
                    : `Last set to ${dewSent}% from this browser. There is no read-back from this camera, so that is what was asked for, not what the heater is doing.`}
                </p>
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
