// AtlasView — the Sky Atlas page shell (design spec §6). Owner E.
//
// Composes the feature-lane components around the store's FramingSession (the
// SSOT seeded by store.openFraming): SkyCanvas (left), SurveyControls + a mosaic
// control cluster + VisibilityPanel (right). The header carries the object name,
// an inline focal-length field (defaults from config.optics; on change PUT
// /api/optics then loadConfig), and a "calibrate from last solve" button.
//
// Layout (spec §6):
//   • desktop ≥lg : grid lg:grid-cols-[1fr_380px] — canvas left, planners right.
//   • phone       : PINNED canvas at the top, planners scroll beneath it so the
//                   overlay updates live while a slider is dragged (never hide the
//                   canvas behind tabs — spec §8 C3-A13).
//
// J2000 invariant: the session center is always J2000; we never mix the live
// JNow mount RA into the overlay (spec §9). openFraming seeds center from the
// catalog entry (J2000) or the mount's J2000 RA/Dec when free-roam.
//
// NOTE on the mosaic: the page owns rows/cols/overlap (they drive the live
// FovOverlay grid via the FramingSession with the byte-identical client mirror
// `mosaicGrid` from lib/framing.ts). "Send to Plan" now routes through the
// server `POST /api/framing/mosaic` (Owner C) so the slew targets are identical
// to the engine; on a network/500 failure it falls back to the client mirror so
// Send still works offline. The live drag overlay stays on the client mirror.

import { useCallback, useEffect, useMemo, useState, type JSX } from "react";
import {
  useStore,
  useFraming,
  useConfig,
  useSite,
  usePreview,
  useSequence,
  useNight,
} from "../store";
import { useShallow } from "zustand/react/shallow";
import type {
  CatalogEntry, MosaicPanel, MosaicResult, Optics, PackStatus, PreflightAlt, Target, VisibilityNight,
} from "../types";
import { getPackStatus } from "../api/backends";
import GotoStrip from "../components/GotoStrip";
import { ARCSEC_PER_RAD, fmtMicron } from "../lib/optics";
import {
  fovFromOptics,
  plausibilityHint,
  mosaicGrid,
  mosaicTotalFov,
  missingOpticsFields,
  deproject,
  wrapRaHours,
  type OpticsLike,
} from "../lib/framing";
import {
  effectiveOptics,
  entryOf,
  isProfileOverride,
  opticsKey,
  overrideProfileName,
} from "../lib/effective";
import { adjustedPa } from "../lib/rotation";
import { uid } from "../lib/ids";
import { useSkyRegion, type SkyRow } from "../lib/skyRegion";
import { ObjectCard } from "../components/atlas/ObjectCard";
import { SkyCanvas } from "../components/atlas/SkyCanvas";
import { SurveyControls } from "../components/atlas/SurveyControls";
import { VisibilityPanel } from "../components/atlas/VisibilityPanel";
import { CatalogSearch } from "../components/atlas/CatalogSearch";
import { TonightPicker } from "../components/atlas/TonightPicker";
import { Panel, Stat, Stepper, EmptyState, HonestButton, LockedChip } from "../components/ui";
import { Icon } from "../components/icons";
import { confirmDialog } from "../components/ConfirmDialog";
import { accessPhrase, useCanControlMount } from "../lib/caps";
import { useBusyOrPending } from "../lib/useBusy";
import { api } from "../api";
import { ClassicAtlasSky, type AtlasDisplay } from "../components/sky/ClassicAtlasSky";

// Per-image survey brightness (night-adaptation memory, spec §6) persists across
// sessions. Clamp mirrors store.ts readBright/clampBright (0.08 floor) so a
// stored value never blanks the canvas.
const SURVEY_BRIGHT_KEY = "astrodeck-survey-bright";
const clampSurveyBright = (v: number): number => Math.min(1, Math.max(0.08, v));
function readSurveyBright(): number {
  try {
    const n = Number(localStorage.getItem(SURVEY_BRIGHT_KEY));
    return Number.isFinite(n) && n > 0 ? clampSurveyBright(n) : 1;
  } catch {
    return 1;
  }
}

// DEFAULT_STEP shape mirrors SequenceView's (a single light step). Kept local so
// AtlasView doesn't import from a magnet view; the Plan accepts this as-is.
const ATLAS_DEFAULT_STEP = {
  filter: null,
  exposure_s: 60,
  gain: 100,
  offset: 30,
  binning: 1,
  count: 20,
  frame_type: "light",
};

function fmtAngle(deg: number): string {
  if (!(deg > 0)) return "—";
  if (deg < 1) return `${(deg * 60).toFixed(1)}′`;
  return `${deg.toFixed(2)}°`;
}

// Empty-state shell when the Atlas is reached with no active session (e.g. direct
// nav before picking an object). Search opens a fresh session; free-roam opens
// centered on the mount/0,0.
//
// UX-2026-07-28 S5 (measured 606px wide on 390/412/440 phones, `main` is
// overflow-x-hidden so the right 216px was UNREACHABLE by any gesture): this card
// had NO definite width, and two separate shrink-to-fit chains then sized it from
// its own content instead of from the screen.
//
//   1. the wrapper was `grid place-items-center`. An implicit grid track is
//      auto-sized to its item's MAX-CONTENT, so the track grew past the 390px
//      column and `max-w-xl` merely capped the damage at 576. (`w-full` alone
//      does not help — 100% then resolves against that 576px track. Measured:
//      still 606.)
//   2. `.empty-state` (ui.tsx) is a column flex with `align-items: center`, so
//      whatever is passed as `action` is shrink-to-fit too — and the picker
//      rows' `truncate` (white-space: nowrap) gives that subtree a 472px
//      MIN-content, which a shrink-to-fit parent must honour. Measured: fixing
//      only the wrapper still left main.scrollWidth 431 on a 390px phone.
//
// So the actions are a plain block child of the card, not EmptyState's `action`
// slot: a block inherits the card's DEFINITE width, and inside a definite width
// flex shrinking does its job and the rows ellipsise instead of pushing.
// EmptyState keeps what it is for — icon, title, hint.
function AtlasEmpty({
  onFreeRoam,
  onPick,
}: {
  onFreeRoam: () => void;
  onPick: (e: CatalogEntry) => void;
}): JSX.Element {
  return (
    <div className="flex items-center justify-center min-h-[60vh] p-4">
      <div className="panel w-full max-w-xl p-4 sm:p-8 text-center">
        <EmptyState
          icon="atlas"
          title="Frame a target"
          hint="Search a target right here, pick one from the Mount catalog, or free-roam the sky. Overlay your camera's field, plan a mosaic, and check tonight's visibility."
        />
        <div className="flex flex-col gap-3 mt-1">
          <CatalogSearch
            onPick={onPick}
            placeholder="Search catalog - e.g. M 31"
            className="w-full"
          />
          <button type="button" className="btn btn-accent btn-touch w-full" onClick={onFreeRoam}>
            Free-roam the sky
          </button>
          <div className="mt-2 pt-3 border-t border-line2">
            <TonightPicker onPick={onPick} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AtlasView(): JSX.Element {
  return <ClassicAtlasSky>{display => <AtlasWorkspace display={display}/>}</ClassicAtlasSky>;
}

function AtlasWorkspace({ display }: { display: AtlasDisplay }): JSX.Element {
  const framing = useFraming();
  const config = useConfig();
  const site = useSite();
  const preview = usePreview();
  const sequence = useSequence();
  const night = useNight();

  // Camera-merged optics (server effective_optics; same source FocusView uses).
  // useShallow: the dict is rebuilt every WS tick but its fields are primitives.
  const statusOptics = useStore(useShallow((s) => s.status?.optics));
  // Rotator sub-status for the PA-honesty note (CAA §5.3). Narrow + shallow so a
  // 2 s status poll that leaves the rotator unchanged doesn't re-render the page.
  const statusRotator = useStore(useShallow((s) => s.status?.rotator ?? null));
  // UX-2026-07-26 #18: framing has to be able to hand off to the mount. One
  // boolean is all this page needs — is there a mount at all.
  const mountConnected = useStore((s) => s.status?.mount != null);
  // Live pointing (2026-07-31): the mount's REPORTED position, which SkyCanvas
  // draws as a sky-anchored footprint. Narrowed to the five fields the drawing
  // uses and shallow-compared, so a 2 s status tick that leaves the mount where
  // it was does not re-render this page — and, more importantly, so the
  // footprint moves ONLY when the hardware reports that it moved. Nothing here
  // is ever set from the target; `gotoFraming` below posts a slew and returns.
  const statusMount = useStore(
    useShallow((s) => {
      const m = s.status?.mount;
      return m
        ? {
            ra_hours: m.ra_hours, dec_deg: m.dec_deg,
            ra_str: m.ra_str, dec_str: m.dec_str, slewing: m.slewing,
          }
        : null;
    }),
  );
  const canMount = useCanControlMount();

  const setFraming = useStore((s) => s.setFraming);
  const openFraming = useStore((s) => s.openFraming);
  const addTargetsToPlan = useStore((s) => s.addTargetsToPlan);
  const setView = useStore((s) => s.setView);
  const loadConfig = useStore((s) => s.loadConfig);
  const enqueueToast = useStore((s) => s.enqueueToast);

  // Lifted visibility night (VisibilityPanel → here) so the mosaic reality-check
  // can cross-reference best_window / set time (spec §6).
  //
  // It carries the CENTRE it was computed for. The panel debounces 300 ms and
  // then waits on an astropy round trip, so for most of a drag the last good
  // night describes where the frame used to be — and everything built from it
  // (the below-limit banner, the Send confirmation's peak altitude) said so with
  // a straight face. Comparing the centre is what makes "tonight" mean tonight
  // AT THIS POINT; a mismatch is not data, it is a stale answer to a question
  // nobody is asking any more.
  const [vis, setVis] = useState<{
    ra: number; dec: number; night: VisibilityNight | null;
  }>({ ra: NaN, dec: NaN, night: null });
  const onVisNight = useCallback(
    (night: VisibilityNight | null, forCenter: { ra_hours: number; dec_deg: number }) =>
      setVis({ ra: forCenter.ra_hours, dec: forCenter.dec_deg, night }),
    [],
  );

  // Survey fetch failure -> degraded (last good frame stays up; SkyCanvas
  // retries with backoff). NEVER flips the view to schematic (wave-1 §1.4).
  const [surveyDegraded, setSurveyDegraded] = useState(false);
  // config.survey.online_fetch gates online-only surveys/stretch (offline-pack
  // spec §6); AtlasView already holds config = useConfig() above.
  const onlineFetch = config?.survey?.online_fetch ?? false;
  const [packStatus, setPackStatus] = useState<PackStatus | null>(null);
  // Poll pack status every 2s only while degraded with online fetch off — the
  // only state in which the banner copy depends on it (offline-pack spec §6).
  useEffect(() => {
    if (!surveyDegraded || onlineFetch) return;
    let live = true;
    const tick = () => {
      getPackStatus().then((p) => { if (live) setPackStatus(p); }).catch(() => {});
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => { live = false; window.clearInterval(id); };
  }, [surveyDegraded, onlineFetch]);

  // Fetching-aware empty-state copy: only overrides the default banner when no
  // pack is present (a pack IS present -> the default "upstream hiccup" copy is
  // correct, offline-pack spec §6).
  const degradedText =
    surveyDegraded && !onlineFetch && packStatus && !packStatus.present
      ? packStatus.fetching
        ? `Downloading offline sky pack… ${packStatus.fetching.done}/${packStatus.fetching.total}`
        : "No survey source — download the offline sky pack in Settings, or enable online fetch."
      : undefined;
  // Per-image brightness (night-adaptation memory) lives in the page; persisted to
  // localStorage (clamped 0.08 floor) so the dark-adapted level survives a reload.
  const [imageBrightness, setImageBrightness] = useState(readSurveyBright);
  const [cameraFovLock, setCameraFovLock] = useState(false);

  // Persist the survey brightness whenever it changes (clamped on write).
  useEffect(() => {
    try {
      localStorage.setItem(SURVEY_BRIGHT_KEY, String(clampSurveyBright(imageBrightness)));
    } catch {
      /* quota / unavailable — keep in-memory */
    }
  }, [imageBrightness]);

  // The inline focal-length field. Seeded from config optics; user-editable. We
  // PUT on commit (blur / Enter), not per keystroke.
  const optics: Optics | null = config?.optics ?? null;
  const computed = config?.optics_computed ?? null;

  // The optics the frame is DRAWN from (wave-1 §3.1).
  //
  // #129: this used to take `focal_length_mm` from the GLOBAL config block
  // unconditionally, while pixel/sensor fell back to the live camera readout.
  // But an active profile's optics block replaces the whole object server-side,
  // and that replaced object is what the solve FOV hint sent to ASTAP is built
  // from. So with a profile override the rectangle on the sky, the mosaic panel
  // positions and the gear label all described a telescope the rig was not
  // using — while the solver used the other one. `effectiveOptics` reads the
  // WINNING layer and keeps the camera fallback underneath it for the bootstrap
  // case where the provenance block has not arrived yet.
  const liveOptics = statusOptics ?? computed;
  const mergedOptics: OpticsLike | null = useMemo(
    () => effectiveOptics(config, optics, liveOptics),
    [config, optics, liveOptics],
  );
  // Which layer supplies the focal length, for the gear label + the note beside
  // the inline fields. The inline fields below still EDIT global (they PUT
  // /api/optics), so when a profile is in force the user has to be told that
  // what they type is not what is being drawn.
  const focalEntry = entryOf(config, opticsKey("focal_length_mm"));
  const opticsPinned = isProfileOverride(focalEntry);

  const [focalDraft, setFocalDraft] = useState<string>("");
  const [savingFocal, setSavingFocal] = useState(false);
  // The same in-flight state for the other four optics boxes, which had neither
  // it nor a revert: a rejected PUT left the typed number sitting in the field
  // while the rectangle on the sky was still drawn from the stored one.
  //
  // Per FIELD rather than one flag for all four, unlike `savingFocal`, because
  // these commit on blur — which fires as focus lands on the NEXT box. A single
  // flag would disable the box the user has just tabbed into and eat the digits
  // they are already typing.
  const [savingField, setSavingField] = useState<keyof Optics | null>(null);
  // Optics live behind a gear: per-rig facts, not per-session controls.
  const [opticsOpen, setOpticsOpen] = useState(false);
  // The gear's label. It reports the RIG rather than saying "Optics",
  // because a collapsed control that hides its value just moves the
  // question one tap away — and focal length is the number most likely to
  // be wrong after a reducer or a different scope.
  const opticsSummary = useMemo(() => {
    const f = mergedOptics?.focal_length_mm || 0;
    const px = mergedOptics?.pixel_size_um || 0;
    if (!f && !px) return "set up optics";
    const parts: string[] = [];
    if (f) parts.push(`${Math.round(f)}mm`);
    if (px) parts.push(`${px.toFixed(2)}µm`);
    return parts.join(" · ");
  }, [mergedOptics]);
  // Inline pixel-size + sensor drafts (same shape as focalDraft). Empty/0 commits
  // "use camera" — the server merge fills them from the connected camera (§3.2).
  const [pixelDraft, setPixelDraft] = useState<string>("");
  const [sensorWDraft, setSensorWDraft] = useState<string>("");
  const [sensorHDraft, setSensorHDraft] = useState<string>("");
  // A4 (P2-T3 review F2): optional guide-scope focal length. Empty clears it
  // (server stores null); a value commits through the shared optics PUT.
  const [guideFocalDraft, setGuideFocalDraft] = useState<string>("");
  // In-flight guard for Send-to-Plan — blocks a double-tap from double-adding a
  // single target (the server round-trip is async).
  const [sending, setSending] = useState(false);

  // ---- Atlas → mount handoff in-flight state (#18) ----
  //
  // This used to be a plain `slewing` flag set around `await api.post(...)`.
  // That route `_spawn`s the "goto" lane and returns `{"started": "goto"}` the
  // instant the task is CREATED, so the promise resolved in ~40 ms and the flag
  // cleared while the mount was still swinging: the button read "Go to this
  // target", live and re-pressable, for the whole 30–90 s slew-and-centre. The
  // tap it invited hit `_spawn`'s 409 and was painted as "Couldn't slew" — a red
  // error over a slew that was succeeding. Only the rig knows, and it says so on
  // every 2 s frame; `arm()` covers the gap until the first frame lands.
  const { busy: gotoBusy, arm: armGoto } = useBusyOrPending("goto");
  // The preflight GET and its confirm dialog run BEFORE any lane exists, and
  // that window is two awaits long — long enough for a second tap to get all
  // the way to the server. This is the guard for that window alone.
  const [gotoPreparing, setGotoPreparing] = useState(false);

  // Seed/refresh the focal draft whenever config optics changes.
  useEffect(() => {
    if (optics) setFocalDraft(String(optics.focal_length_mm || ""));
  }, [optics?.focal_length_mm]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seed/refresh the pixel + sensor drafts on config change (0 -> empty field).
  useEffect(() => {
    if (!optics) return;
    setPixelDraft(String(optics.pixel_size_um || ""));
    setSensorWDraft(String(optics.sensor_width_px || ""));
    setSensorHDraft(String(optics.sensor_height_px || ""));
  }, [optics?.pixel_size_um, optics?.sensor_width_px, optics?.sensor_height_px]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seed/refresh the guide-scope focal draft on config change (A4).
  useEffect(() => {
    setGuideFocalDraft(String(optics?.guide_focal_length_mm || ""));
  }, [optics?.guide_focal_length_mm]);

  // A survey-source change is a fresh chance — clear the degraded flag.
  useEffect(() => {
    setSurveyDegraded(false);
  }, [framing?.survey]);

  // Effective focal override (the draft, when a positive number) feeds the FOV.
  const focalOverride = useMemo(() => {
    const n = Number(focalDraft);
    return Number.isFinite(n) && n > 0 ? n : undefined;
  }, [focalDraft]);

  const fov = useMemo(
    () => fovFromOptics(mergedOptics, focalOverride),
    [mergedOptics, focalOverride],
  );
  const haveOptics = fov.fov_x_deg > 0 && fov.fov_y_deg > 0;
  const plausibility = plausibilityHint(fov.pixel_scale_arcsec);
  const frameFovDeg = Math.max(fov.fov_x_deg, fov.fov_y_deg);

  // "Match camera" is a claim about the CAMERA, so it cannot outlive the optics
  // it was matched to. The zoom is applied ONCE, when the switch goes on; the
  // frame it was matched to moves on its own afterwards — a focal-length or
  // sensor keystroke (focalOverride feeds `fov` per keystroke), and, with no
  // user interaction at all, a 2 s status frame carrying new camera optics from
  // a reconnect or another screen. The "Your camera" rectangle resizes on the
  // canvas immediately, the zoom does not follow, and the switch went on
  // reading ON over a view that visibly no longer matched.
  //
  // CLEARED rather than re-applied, for two reasons: the InfoDot beside it
  // promises "turn off to go back to your previous zoom", i.e. a one-shot apply
  // with a memory rather than a live mode; and re-zooming on every keystroke of
  // a half-typed focal length would jerk the sky under the user's hands. This is
  // the same resolution the zoom path already carries at setZoom below — every
  // way the view can stop matching the camera ends the match.
  //
  // `prev_zoom_deg` is deliberately left alone: the OFF branch is the only
  // reader, it is unreachable without turning the switch ON again first, and
  // that re-save overwrites it with the zoom the user is actually looking at.
  useEffect(() => {
    if (!cameraFovLock) return;
    setCameraFovLock(false);
    // frameFovDeg ONLY: this fires when the camera changes underneath the
    // match, never when the flag itself does.
  }, [frameFovDeg]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- focal-length commit: PUT /api/optics then re-GET config (spec §6) ----
  // The value-taking committer is the real worker — calibrate passes the computed
  // focal DIRECTLY (no setState→read round-trip, which closed over a stale draft
  // and saved the old value). commitFocal() is the blur/Enter wrapper.
  const commitFocalValue = useCallback(
    async (n: number) => {
      if (!optics) return;
      if (!Number.isFinite(n) || n <= 0) {
        setFocalDraft(String(optics.focal_length_mm || ""));
        return;
      }
      if (n === optics.focal_length_mm) return;
      setSavingFocal(true);
      try {
        const next: Optics = { ...optics, focal_length_mm: n };
        await api.put("/api/optics", { optics: next, version: config?.version ?? null });
        await loadConfig();
      } catch (e) {
        enqueueToast({
          level: "error",
          title: "Couldn't save focal length",
          detail: (e as Error).message,
        });
        setFocalDraft(String(optics.focal_length_mm || ""));
      } finally {
        setSavingFocal(false);
      }
    },
    [optics, config?.version, loadConfig, enqueueToast],
  );

  const commitFocal = useCallback(
    () => commitFocalValue(Number(focalDraft)),
    [commitFocalValue, focalDraft],
  );

  // Commit any optics field(s): PUT /api/optics then re-GET config. Empty/0
  // means "use camera" (server merge, config.py). Same optimistic-concurrency
  // version token as the focal committer.
  //
  // `revert` restores the draft the patch came from. Without it a rejected save
  // (403, a version clash, the box offline) left the typed number on screen with
  // the old one still driving the FOV rectangle beside it — two different
  // answers to "what is my sensor", one of them fiction. Per-field rather than
  // reseeding every draft, so a failed pixel-size save cannot also throw away a
  // sensor width the user has typed but not yet committed.
  const commitOpticsPatch = useCallback(
    async (patch: Partial<Optics>, revert: () => void) => {
      if (!optics) return;
      // Every call site sends exactly one field; this is which box is in flight.
      const field = Object.keys(patch)[0] as keyof Optics;
      setSavingField(field);
      try {
        const next: Optics = { ...optics, ...patch };
        await api.put("/api/optics", { optics: next, version: config?.version ?? null });
        await loadConfig();
      } catch (e) {
        enqueueToast({
          level: "error",
          title: "Couldn't save optics",
          detail: (e as Error).message,
        });
        revert();
      } finally {
        setSavingField((f) => (f === field ? null : f));
      }
    },
    [optics, config?.version, loadConfig, enqueueToast],
  );

  // Per-field blur/Enter committers. Empty -> 0 ("use camera"); a value equal to
  // the stored one is a no-op (no needless PUT); an invalid entry reverts the
  // draft. Sensor dims round to whole pixels.
  const commitPixel = useCallback(() => {
    const raw = pixelDraft.trim();
    const n = raw === "" ? 0 : Number(raw);
    if (!Number.isFinite(n) || n < 0) {
      setPixelDraft(String(optics?.pixel_size_um || ""));
      return;
    }
    if (n === (optics?.pixel_size_um ?? 0)) return;
    void commitOpticsPatch({ pixel_size_um: n }, () =>
      setPixelDraft(String(optics?.pixel_size_um || "")),
    );
  }, [pixelDraft, optics?.pixel_size_um, commitOpticsPatch]);

  // Guide-scope focal length (A4): empty commits null ("no guide optics
  // configured"), unlike the main/pixel/sensor fields' empty-means-0 idiom —
  // there is no "use camera" fallback for a guide SCOPE's focal length.
  const commitGuideFocal = useCallback(() => {
    const raw = guideFocalDraft.trim();
    const n = raw === "" ? null : Number(raw);
    if (n !== null && (!Number.isFinite(n) || n <= 0)) {
      setGuideFocalDraft(String(optics?.guide_focal_length_mm || ""));
      return;
    }
    if (n === (optics?.guide_focal_length_mm ?? null)) return;
    void commitOpticsPatch({ guide_focal_length_mm: n }, () =>
      setGuideFocalDraft(String(optics?.guide_focal_length_mm || "")),
    );
  }, [guideFocalDraft, optics?.guide_focal_length_mm, commitOpticsPatch]);

  const commitSensorW = useCallback(() => {
    const raw = sensorWDraft.trim();
    const n = raw === "" ? 0 : Math.round(Number(raw));
    if (!Number.isFinite(n) || n < 0) {
      setSensorWDraft(String(optics?.sensor_width_px || ""));
      return;
    }
    if (n === (optics?.sensor_width_px ?? 0)) return;
    void commitOpticsPatch({ sensor_width_px: n }, () =>
      setSensorWDraft(String(optics?.sensor_width_px || "")),
    );
  }, [sensorWDraft, optics?.sensor_width_px, commitOpticsPatch]);

  const commitSensorH = useCallback(() => {
    const raw = sensorHDraft.trim();
    const n = raw === "" ? 0 : Math.round(Number(raw));
    if (!Number.isFinite(n) || n < 0) {
      setSensorHDraft(String(optics?.sensor_height_px || ""));
      return;
    }
    if (n === (optics?.sensor_height_px ?? 0)) return;
    void commitOpticsPatch({ sensor_height_px: n }, () =>
      setSensorHDraft(String(optics?.sensor_height_px || "")),
    );
  }, [sensorHDraft, optics?.sensor_height_px, commitOpticsPatch]);

  // "from camera" affordance: config value is 0 AND the live merged readout has a
  // positive camera-sourced value — show it as the placeholder + a small chip.
  const cameraFed = liveOptics?.source === "camera" || liveOptics?.source === "mixed";
  const pxFromCam = cameraFed && (optics?.pixel_size_um ?? 0) <= 0 && (liveOptics?.pixel_size_um ?? 0) > 0;
  const wFromCam = cameraFed && (optics?.sensor_width_px ?? 0) <= 0 && (liveOptics?.sensor_width_px ?? 0) > 0;
  const hFromCam = cameraFed && (optics?.sensor_height_px ?? 0) <= 0 && (liveOptics?.sensor_height_px ?? 0) > 0;

  // ---- calibrate from last solve (spec §6, reducer/barlow-proof) ----
  // fl_mm = ARCSEC_PER_RAD · pixel_size_um / last_solve_pixel_scale. The last
  // solve's pixel scale rides on the live preview (PreviewInfo.pixel_scale_arcsec);
  // the camera pixel size comes from the computed optics.
  const lastSolveScale = preview?.pixel_scale_arcsec ?? null;
  const pixelSizeUm = computed?.pixel_size_um ?? optics?.pixel_size_um ?? 0;
  const canCalibrate = !!lastSolveScale && lastSolveScale > 0 && pixelSizeUm > 0;

  const calibrateFromSolve = useCallback(() => {
    if (!canCalibrate || !lastSolveScale) return;
    const fl = (ARCSEC_PER_RAD * pixelSizeUm) / lastSolveScale;
    if (fl > 0) {
      // Reflect the field AND commit the computed value directly — no setState→
      // read round-trip (that closed over the stale draft and saved nothing).
      setFocalDraft(fl.toFixed(1));
      void commitFocalValue(fl);
    }
  }, [canCalibrate, lastSolveScale, pixelSizeUm, commitFocalValue]);

  // free-roam entry from the empty state
  const onFreeRoam = useCallback(() => openFraming(undefined), [openFraming]);
  // The atlas opens directly on the survey. No target or equipment is required.
  useEffect(() => { if (!framing) onFreeRoam(); }, [framing, onFreeRoam]);

  // Stable identities: SkyCanvas's fetch effect depends on these via loadSurvey.
  const onSurveyError = useCallback(() => setSurveyDegraded(true), []);
  const onSurveyLoad = useCallback(() => setSurveyDegraded(false), []);

  // ---- what is actually in this patch of sky (#111 / #183) ----------------
  // Above the `if (!framing)` return, because hooks cannot be conditional. The
  // hook itself is gated by `enabled`, so with no session it does nothing at
  // all — no request, no timer.
  //
  // One REGION per patch of sky, not one query per view: the rows are fetched
  // for a circle 1.6x the viewport and re-projected locally on every frame, so
  // a pan costs a gnomonic per row and no network at all until the view leaves
  // the circle. See lib/skyRegion.ts for the whole argument.
  const regionCenter = useMemo(
    () => ({
      ra_hours: framing?.center.ra_hours ?? 0,
      dec_deg: framing?.center.dec_deg ?? 0,
    }),
    [framing?.center.ra_hours, framing?.center.dec_deg],
  );
  const region = useSkyRegion(regionCenter, framing?.fovZoomDeg ?? 0, !!framing);
  const [selectedObject, setSelectedObject] = useState<SkyRow | null>(null);
  const visibleRows = region.rows.filter(row => display.objects && display.accepts(row));
  useEffect(() => {
    if (selectedObject && (!display.objects || !display.accepts(selectedObject))) setSelectedObject(null);
  }, [display, selectedObject]);

  if (!framing) {
    return <AtlasEmpty onFreeRoam={onFreeRoam} onPick={openFraming} />;
  }

  const { center, rotation_deg, survey, stretch, fovZoomDeg, mosaic, target } = framing;
  const mode: "survey" | "schematic" = !display.imagery || survey === "schematic" ? "schematic" : "survey";

  // ---- session patchers routed into setFraming ----
  const setCenter = (ra_hours: number, dec_deg: number) =>
    setFraming({ center: { ra_hours: wrapRaHours(ra_hours), dec_deg } });
  const setRotation = (deg: number) => setFraming({ rotation_deg: deg });
  // Every zoom that is not the "Match camera" toggle's own — the slider, Fit
  // object, a pinch, a wheel, +/- — ends the match. Two things had to change
  // together: the switch went on reading ON over a view that no longer matched
  // the camera, and `prev_zoom_deg` went on holding the zoom from before the
  // match, so turning the switch OFF threw away every zoom made since and
  // teleported the sky back. Clearing both makes the toggle mean what it shows
  // and makes "off" mean "undo the match", not "undo the last ten minutes".
  const setZoom = (deg: number) => {
    setCameraFovLock(false);
    setFraming({ fovZoomDeg: deg, prev_zoom_deg: undefined });
  };
  const setSurvey = (s: string) => setFraming({ survey: s });
  const setMosaic = (patch: Partial<typeof mosaic>) =>
    setFraming({ mosaic: { ...mosaic, ...patch } });

  // Search-pick with a live session: swap the framed target + recenter on it,
  // keeping the user's survey/zoom/rotation/mosaic setup (wave-2 §2).
  const pickSearchTarget = (entry: CatalogEntry) =>
    setFraming({
      target: entry,
      center: { ra_hours: entry.ra_hours, dec_deg: entry.dec_deg },
    });

  // The same move, from a marker tapped on the map instead of a search result.
  //
  // `alt`/`az` are OMITTED rather than filled with a placeholder. The type
  // declares them because /api/catalog attaches them for a caller holding
  // view.site_derived, but nothing in the app reads them off a framing target,
  // and the sky-region payload deliberately carries no such pair (it would make
  // a pannable map a coordinate oracle for the rig's location). An absent field
  // is honest; a 0 would be a claim that the object is on the horizon due
  // north. `mag: 99` is this app's existing "unmeasured" sentinel, the same one
  // a typed-coordinate target carries.
  const frameRow = (row: SkyRow) =>
    setFraming({
      target: {
        id: row.id,
        name: row.label,
        type: row.type,
        ra_hours: row.ra_hours,
        dec_deg: row.dec_deg,
        mag: row.mag ?? 99,
        size_arcmin: row.size_arcmin,
      } as CatalogEntry,
      center: { ra_hours: row.ra_hours, dec_deg: row.dec_deg },
    });

  // Recenter on the origin object, or — in free-roam — on the live mount position
  // (consistent with openFraming's free-roam seed). No-op only if free-roam AND
  // the mount status isn't available yet.
  const recenter = () => {
    if (target) {
      setCenter(target.ra_hours, target.dec_deg);
      return;
    }
    const m = useStore.getState().status?.mount;
    if (m) setCenter(m.ra_hours, m.dec_deg);
  };

  // Center-nudge by ±1 frame (±0.05° when no optics). dx East, dy North (frames).
  const nudge = (dxFrames: number, dyFrames: number) => {
    const stepX = haveOptics ? fov.fov_x_deg : 0.05;
    const stepY = haveOptics ? fov.fov_y_deg : 0.05;
    const sky = deproject(
      dxFrames * stepX,
      dyFrames * stepY,
      center.ra_hours,
      center.dec_deg,
    );
    setCenter(sky.ra_hours, sky.dec_deg);
  };

  // FOV lock (spec §6): on -> save the current zoom + apply camera FOV x1.6;
  // off -> restore the saved zoom (clamped) if present, else keep current.
  const onCameraFovLock = (locked: boolean) => {
    setCameraFovLock(locked);
    const clamp = (v: number) => Math.min(10, Math.max(0.1, v));
    if (locked) {
      if (frameFovDeg > 0)
        setFraming({ prev_zoom_deg: fovZoomDeg, fovZoomDeg: clamp(frameFovDeg * 1.6) });
    } else if (framing.prev_zoom_deg != null) {
      setZoom(clamp(framing.prev_zoom_deg));
    }
  };

  // ---- mosaic panels (dual-path: Send POSTs /api/framing/mosaic; the client
  // mirror `mosaicGrid` is both the live overlay source and the offline fallback) ----
  const rows = mosaic.rows;
  const cols = mosaic.cols;
  const overlap = mosaic.overlap;
  const panelCount = rows * cols;
  const total = mosaicTotalFov(cols, rows, overlap, fov.fov_x_deg, fov.fov_y_deg);

  const seqRunning = sequence.state === "running" || sequence.state === "paused";

  // The rounded fetch key VisibilityPanel computes tonight for — mirrored here
  // (same rounding, wave-1 §2) so the page can tell a night that describes THIS
  // centre from one that describes where the frame used to be.
  const visRa = Math.round(center.ra_hours * 1000) / 1000;
  const visDec = Math.round(center.dec_deg * 100) / 100;
  const visFresh = vis.ra === visRa && vis.dec === visDec;
  const visNight = visFresh ? vis.night : null;
  // We HAD an answer and the frame has since moved off it. Not "unknown" (that
  // is the first load, where the panel below shows its own skeleton) — this is
  // the window in which the old numbers would otherwise still be on screen.
  const visRecomputing = !visFresh && vis.night != null;

  // Below-limit / set-time advisory drives the Send override gate (spec §6).
  // Null night = no claim: during the recompute the banner says it is checking
  // rather than repeating the last point's verdict about this one.
  const belowLimit = visNight?.never_rises_above_limit ?? false;
  // Group id for mosaic dedupe: a catalog target groups by its id; a free-roam
  // session groups by the stable per-session freeroamId (seeded in openFraming) so
  // a multi-panel free-roam mosaic groups in the Plan and re-framing REPLACES its
  // panels instead of appending duplicates (C1-C2).
  const groupId = target?.id ?? framing.freeroamId;

  // Map canonical panels (server or client-mirror) → Target[]. Naming/flags are
  // identical on both paths so a server-vs-fallback Send is indistinguishable in
  // the Plan (spec §5: the server is canonical; the mirror is the offline twin).
  const panelsToTargets = (panels: MosaicPanel[]): Target[] => {
    const baseName = target?.id ?? target?.name ?? "Sky";
    return panels.map((p) => ({
      id: uid(),                          // stable identity (sessions spec §1)
      name: panelCount > 1 ? `${baseName} ${p.row + 1}-${p.col + 1}` : baseName,
      ra_hours: p.ra_hours, // already %24-wrapped (server emits ra % 24)
      dec_deg: p.dec_deg,
      center: true,
      autofocus_first: p.row === 0 && p.col === 0,
      calibration: false,
      rotation_deg,
      mosaic_group: panelCount > 1 ? groupId : undefined,
      steps: [{ ...ATLAS_DEFAULT_STEP, id: uid() }],
    }));
  };

  // Client-mirror panels — the offline fallback AND the live overlay source. Kept
  // byte-identical to the server mosaic engine (lib/framing.ts mirrors framing.py).
  const computePanelsLocal = (): MosaicPanel[] =>
    mosaicGrid({
      ra_hours: center.ra_hours,
      dec_deg: center.dec_deg,
      rows,
      cols,
      overlap,
      rotation_deg,
      fov_x_deg: fov.fov_x_deg,
      fov_y_deg: fov.fov_y_deg,
    });

  // Send always re-runs the SERVER so the slew targets are byte-identical to the
  // engine; on a network/500 failure we fall back to the client mirror so Send
  // still works offline (spec §5). The live drag overlay never depends on this.
  const computePanels = async (): Promise<Target[]> => {
    try {
      const res = await api.post<MosaicResult>("/api/framing/mosaic", {
        ra_hours: center.ra_hours,
        dec_deg: center.dec_deg,
        rows,
        cols,
        overlap,
        rotation_deg,
        fov_x_deg: fov.fov_x_deg,
        fov_y_deg: fov.fov_y_deg,
      });
      return panelsToTargets(res.panels);
    } catch {
      // offline / server error — the client mirror is canonical-equivalent.
      return panelsToTargets(computePanelsLocal());
    }
  };

  // ---- the camera angle this page actually COMMANDS ----
  // One expression, read by the Go-to body, the run's toast and the "will
  // rotate" note below, so the promise and the request cannot drift apart.
  //
  // 2026-07-31: they had drifted. `gotoFraming` posted /api/mount/goto without
  // `rotation_deg`, and that field is the ONLY trigger for rotation — the server
  // runs `rotate_to_pa` solely when `rotation_deg is not None and rot is not None
  // and rot.connected` (hub.goto_and_center). So the rotator was never asked to
  // turn from this page, while a panel below promised it would. The live pointing
  // frame now on this canvas is what made that legible: it could travel with the
  // mount and never turn, because nothing had commanded a turn.
  //
  // Null below 0.5°, not 0: `rotation_deg` starts at 0 for every framing session,
  // so posting it unconditionally would bolt a plate-solve rotate loop onto every
  // "just show me this" tap — minutes of motion nobody asked for. Above the
  // dial's own dead-band it is a real request and gets sent.
  const commandedPaDeg = rotation_deg > 0.5 ? rotation_deg : null;
  // Whether that request can reach hardware. `status.rotator` is published only
  // when a rotator exists AND reports connected (hub.status) — the exact
  // condition goto_and_center rotates under — so this predicts the real outcome
  // instead of the hoped-for one.
  const willRotate = commandedPaDeg != null && statusRotator != null;

  const sendToPlan = async () => {
    if (!haveOptics || seqRunning || sending) return;
    if (belowLimit) {
      // App confirm dialog (night-safe, 44px, non-suppressible) — NOT window.confirm
      // (a bright OS dialog destroys dark adaptation). Mirrors MountView.doGoto.
      const ok = await confirmDialog({
        title: "Below tonight's limit",
        body: `${target?.name ?? "This target"} doesn't rise above ${Math.round(
          visNight?.alt_limit_deg ?? 30,
        )}° tonight (peaks ${(visNight?.transit_alt ?? 0).toFixed(0)}°). Add anyway?`,
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Add anyway",
        confirmPrimary: true, // PLAN-01-gemini: proceeding is the intended action
      });
      if (!ok) return;
    }
    setSending(true);
    try {
      const targets = await computePanels();
      addTargetsToPlan(targets, panelCount > 1 ? groupId : undefined);
      enqueueToast({
        level: "success",
        title:
          panelCount > 1
            ? `${panelCount} panels added to Plan`
            : "Target added to Plan",
        // Only send the user to the camera when nothing else will turn it. This
        // told every rig to hand-set the angle, rotator or not — which now sits
        // one panel away from a note saying the rotator gets sent that angle.
        detail:
          commandedPaDeg == null
            ? undefined
            : willRotate
              ? `Each panel slews, rotates to PA ${Math.round(commandedPaDeg)}° and centres before it exposes.`
              : `Set your camera to PA ${Math.round(commandedPaDeg)}° before this run — there's no rotator to do it.`,
      });
      setView("sequence");
    } finally {
      setSending(false);
    }
  };

  // ---- Atlas → mount handoff (UX-2026-07-26 #18) ----
  // The novice's flow dead-ended here: the app says "Tap one to frame it", and
  // then the ONLY forward control on the framed page was ADD TARGET TO PLAN —
  // a 20-row automation wall. The capability existed the whole time (Mount's
  // search box does it), it was the handoff that was missing.
  //
  // Deliberately the SAME guard MountView.doGoto uses — live altitude re-queried
  // at the tap (never the stale catalog row), below-horizon refused outright,
  // low-horizon confirmed with the actual number, unknown site confirmed — so
  // "GOTO from Atlas" and "GOTO from Mount" cannot diverge in safety.
  // Slews to the framed CENTRE, which is what the overlay on screen shows; for
  // a freshly picked target that is the target's own J2000 position.
  const gotoFraming = async () => {
    if (!canMount || !mountConnected || gotoBusy || gotoPreparing) return;
    // Set BEFORE the first await, not after the preflight and its dialog: that
    // gap is a network round trip long, and a second tap inside it used to make
    // it all the way to the server and come back as a red 409 beside the first
    // tap's green success.
    setGotoPreparing(true);
    try {
      await runGoto();
    } finally {
      setGotoPreparing(false);
    }
  };

  // The body, split out only so the `gotoPreparing` try/finally above wraps
  // every exit from it — including the four early returns the dialogs take.
  const runGoto = async () => {
    const name = target?.name ?? target?.id ?? "This position";
    let pf: PreflightAlt | null = null;
    try {
      pf = await api.get<PreflightAlt>(
        `/api/sequence/preflight?ra_hours=${center.ra_hours}&dec_deg=${center.dec_deg}`,
      );
    } catch {
      pf = null; // the server horizon guard is still the net
    }
    if (!pf || pf.verdict === "unknown") {
      const ok = await confirmDialog({
        title: "Location not set",
        body: "Altitude can't be checked until you set your location in Settings. Slew anyway?",
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    } else if (pf.verdict === "below") {
      await confirmDialog({
        title: "Below the visible horizon",
        body: pf.alt != null
          ? `${name} is at ${pf.alt}° — below the horizon, so it isn't visible now.`
          : `${name} is below the horizon, so it isn't visible now.`,
        tone: "danger",
        mode: "ok", // single dismiss, no slew
      });
      return;
    } else if (pf.verdict === "low") {
      const ok = await confirmDialog({
        title: "Low on the horizon",
        body: pf.alt != null
          ? `${name} is only ${pf.alt}° up — expect heavy atmosphere and possible obstructions. Slew anyway?`
          : `${name} is low on the horizon — expect heavy atmosphere and possible obstructions. Slew anyway?`,
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    }
    try {
      await api.post("/api/mount/goto", {
        ra_hours: center.ra_hours,
        dec_deg: center.dec_deg,
        center: true,
        force: pf?.verdict === "low",
        // The rotation half of the framing, sent so the hardware actually
        // performs it. null = no angle was asked for, which the server reads as
        // "leave the rotator alone". When it is set, the live frame on the canvas
        // turns because THIS made the rotator turn — the frame is still only ever
        // a readout, and it reports the turn rather than causing the look of one.
        rotation_deg: commandedPaDeg,
      });
      // The POST resolving means the lane was CREATED, nothing more. Arm the
      // local latch so the button reads busy for the ≤2 s until the rig's own
      // status frame names the lane, then the server's answer takes over and
      // holds it for the real duration of the slew.
      armGoto();
      enqueueToast({
        level: "success",
        title: `Slewing to ${name}`,
        // What the rig will do, in the order it will do it — including the case
        // where the angle was asked for and no rotator can serve it, which is
        // the user's cue to turn the camera by hand before the frame matches.
        detail: willRotate
          ? `It will plate-solve, rotate to PA ${Math.round(commandedPaDeg!)}° and re-centre when it arrives.`
          : commandedPaDeg != null
            ? `It will plate-solve and re-centre when it arrives. No rotator is connected, so set the camera to PA ${Math.round(commandedPaDeg)}° yourself.`
            : "It will plate-solve and re-centre when it arrives.",
      });
    } catch (e) {
      enqueueToast({ level: "error", title: "Couldn't slew", detail: (e as Error).message });
    }
  };

  // Why "add to plan" is locked, as a sentence a finger can be told. Ordered by
  // what the user has to do about it: a run in progress is a wait, missing optics
  // is a field to fill. Null = the control is live.
  const sendLock = seqRunning
    ? "A run is in progress. It fixed its target list when it started, so anything added now would sit in the plan unshot — stop the run, then add this."
    : !haveOptics
      ? `No frame size yet, so there is nothing to place on the sky. Still missing: ${missingOpticsFields(
          mergedOptics,
        ).join(", ")} — fill those in at the top of this page, or connect the camera and it fills them for you.`
      : null;

  // Honest-disabled reason (§11.8) — dim + aria-disabled + a STATED reason, never
  // the native `disabled` attribute and never `title=` as the only channel.
  const gotoReason = !mountConnected
    ? "No mount connected — connect your rig on Equipment first."
    : !canMount
      ? `Slewing the mount needs ${accessPhrase("control.mount")}.`
      : null;
  // Server truth (the "goto" lane is in flight) OR our own preflight window.
  const gotoInFlight = gotoBusy || gotoPreparing;

  // crosses-the-meridian-ish hint: a wide mosaic near transit. We don't have a
  // per-panel ephemeris here, so this stays advisory text only when a rotation is
  // set on a multi-panel grid (the honest "expect a stitch seam" note, spec §6).
  const headerName = target?.name ?? "Free roam";

  return (
    <div className="flex flex-col gap-3">
      {/* a goto in flight (or a mount that stopped executing slews) narrates
          itself above the fold on the screen that launched it */}
      <GotoStrip />
      {/* ----------------------------------------------------------- header
          UX-2026-07-26 #55: this whole cluster (title, focal length, pixel
          size, sensor W/H, guide-scope FL, CALIBRATE) used to sit DIRECTLY on
          the photographic wallpaper — dim grey labels and thin field borders
          over a bright nebula, with per-pixel contrast wherever the nebula
          happened to be. It is a control surface, so it gets the app's own
          control surface: the same `.panel` every other cluster on this page
          already sits on. */}
      <header className="panel p-3 sm:p-4 flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h1 className="font-display text-lg text-accent tracking-wide truncate">
            {headerName}
          </h1>
          <p className="text-[12px] text-dim mono">
            {target ? target.type : "explore the sky"} ·{" "}
            {fmtAngle(fov.fov_x_deg)}×{fmtAngle(fov.fov_y_deg)} frame
          </p>
        </div>
        <CatalogSearch onPick={pickSearchTarget} />
        {/* right-align the optics fields on desktop only; on phone this spacer
            would force each field onto its own full-width row (they read as huge
            stacked boxes) — let them pack tightly instead. */}
        <div className="hidden lg:block flex-1" />
        {/* optics fields — compact 2-col grid on phone (was 5 full-width stacked
            boxes eating the screen); `sm:contents` dissolves the wrapper so the
            header's own flex flow is unchanged on wider screens. */}
        {/* Equipment behind a gear (QA: "all equipment selection behind a
            gear ... the flow doesn't make sense"). Focal length, pixel size and
            sensor size are per-RIG facts: entered when a scope is built and
            then never again. Sitting them permanently between the target and
            "Go to this target" put the least-used controls in the middle of the
            most-used path. Closed, the header reads target -> go. */}
        <button
          type="button"
          className={`btn tap min-h-[36px] !px-2.5 shrink-0 ${opticsOpen ? "border-accent text-accent" : ""}`}
          aria-expanded={opticsOpen}
          // #129: the gear label now reports the WINNING layer's numbers, so it
          // can disagree with the boxes it opens. The accessible name says which
          // profile put them there; the trailing "*" is the visible marker (a
          // word-length one does not survive this header on a phone) and the
          // banner inside the drawer is the footnote it points at.
          aria-label={
            opticsPinned
              ? `Camera and telescope specs — ${opticsSummary}, pinned by equipment profile ${overrideProfileName(focalEntry) ?? "(unnamed)"}`
              : "Camera and telescope specs"
          }
          title={opticsSummary}
          onClick={() => setOpticsOpen((v) => !v)}
        >
          <Icon name="settings" size={14} />
          <span className="ml-1.5 mono text-[11px] !normal-case">
            {opticsSummary}
            {opticsPinned && <span aria-hidden>*</span>}
          </span>
        </button>
        {opticsOpen && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 w-full sm:contents">
        {/* inline focal-length field — self-contained optics (spec §6 C1-B1) */}
        <label className="flex flex-col gap-1 items-start min-w-0">
          <span className="label">Focal length</span>
          <span className="inline-flex items-stretch">
            <input
              type="number"
              inputMode="decimal"
              min={1}
              step={1}
              value={focalDraft}
              disabled={!optics || savingFocal}
              onChange={(e) => setFocalDraft(e.target.value)}
              onBlur={() => void commitFocal()}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera focal length in millimetres"
              className="field field-rig mono text-right"
            />
            <span className="inline-flex items-center field-rig-suffix border border-l-0 border-line2 bg-bg text-dim">
              mm
            </span>
          </span>
        </label>
        {/* pixel-size + sensor fields — 0/empty commits "use camera" (§3.2). The
            "from camera" chip + placeholder appear when a connected camera fills
            the field the config leaves blank. */}
        <label className="flex flex-col gap-1 items-start min-w-0">
          <span className="label inline-flex flex-wrap items-center gap-1 min-w-0">
            Pixel size
            {pxFromCam && (
              <span className="text-[10px] text-dim border border-line2 px-1">from camera</span>
            )}
          </span>
          <span className="inline-flex items-stretch">
            <input
              type="number"
              inputMode="decimal"
              min={0}
              step={0.01}
              value={pixelDraft}
              placeholder={pxFromCam ? fmtMicron(liveOptics?.pixel_size_um ?? 0) : undefined}
              disabled={!optics || savingField === "pixel_size_um"}
              onChange={(e) => setPixelDraft(e.target.value)}
              onBlur={commitPixel}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera pixel size in micrometres (0 uses the connected camera)"
              className="field field-rig mono text-right"
            />
            <span className="inline-flex items-center field-rig-suffix border border-l-0 border-line2 bg-bg text-dim">
              µm
            </span>
          </span>
        </label>
        <label className="flex flex-col gap-1 items-start min-w-0">
          <span className="label inline-flex flex-wrap items-center gap-1 min-w-0">
            Sensor W
            {wFromCam && (
              <span className="text-[10px] text-dim border border-line2 px-1">from camera</span>
            )}
          </span>
          <span className="inline-flex items-stretch">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              value={sensorWDraft}
              placeholder={wFromCam ? String(liveOptics?.sensor_width_px ?? "") : undefined}
              disabled={!optics || savingField === "sensor_width_px"}
              onChange={(e) => setSensorWDraft(e.target.value)}
              onBlur={commitSensorW}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera sensor width in pixels (0 uses the connected camera)"
              className="field field-rig mono text-right"
            />
            <span className="inline-flex items-center field-rig-suffix border border-l-0 border-line2 bg-bg text-dim">
              px
            </span>
          </span>
        </label>
        <label className="flex flex-col gap-1 items-start min-w-0">
          <span className="label inline-flex flex-wrap items-center gap-1 min-w-0">
            Sensor H
            {hFromCam && (
              <span className="text-[10px] text-dim border border-line2 px-1">from camera</span>
            )}
          </span>
          <span className="inline-flex items-stretch">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              value={sensorHDraft}
              placeholder={hFromCam ? String(liveOptics?.sensor_height_px ?? "") : undefined}
              disabled={!optics || savingField === "sensor_height_px"}
              onChange={(e) => setSensorHDraft(e.target.value)}
              onBlur={commitSensorH}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera sensor height in pixels (0 uses the connected camera)"
              className="field field-rig mono text-right"
            />
            <span className="inline-flex items-center field-rig-suffix border border-l-0 border-line2 bg-bg text-dim">
              px
            </span>
          </span>
        </label>
        {/* A4 (P2-T3 review F2): optional guide-scope focal length, independent
            of the main imaging-train focal length above. Feeds the native
            guider's image_scale_arcsec (server-side); no live FOV effect here. */}
        <label className="flex flex-col gap-1 items-start min-w-0">
          <span className="label inline-flex flex-wrap items-center gap-1 min-w-0">Guide scope FL</span>
          <span className="inline-flex items-stretch">
            <input
              type="number"
              inputMode="decimal"
              min={0}
              step={1}
              value={guideFocalDraft}
              placeholder="optional"
              disabled={!optics || savingField === "guide_focal_length_mm"}
              onChange={(e) => setGuideFocalDraft(e.target.value)}
              onBlur={commitGuideFocal}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Guide scope focal length in millimetres (optional; feeds real guide-scale arcsec stats)"
              className="field field-rig mono text-right"
            />
            <span className="inline-flex items-center field-rig-suffix border border-l-0 border-line2 bg-bg text-dim">
              mm
            </span>
          </span>
        </label>
        {/* House rule §11.8: a control a user could want to press is never
            natively `disabled` with its reason only in `title=` — that attribute
            never fires on the tablet this product is designed around. */}
        {canCalibrate ? (
          <button
            type="button"
            className="btn btn-touch"
            onClick={calibrateFromSolve}
          >
            <Icon name="refresh" size={14} />
            <span className="ml-1">Calibrate from last solve</span>
          </button>
        ) : (
          <LockedChip
            reason="Plate-solve a frame first (Mount → Solve & Sync), then this back-computes the focal length from the solved pixel scale."
            className="btn btn-touch"
          >
            <Icon name="refresh" size={14} />
            <span className="ml-1">Calibrate from last solve</span>
          </LockedChip>
        )}
        </div>
        )}
      </header>

      {/* ------------------------------------------- point the scope at it (#18)
          The forward exit from framing. Sits directly under the header, above
          the mosaic/plan machinery, because "show me this now" is the question a
          first-timer arrives on this page with. */}
      <div className="panel p-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        {gotoReason ? (
          <LockedChip reason={gotoReason} className="btn btn-touch">
            <Icon name="mount" size={14} />
            <span className="ml-1">Go to this target</span>
          </LockedChip>
        ) : (
          <button
            type="button"
            className="btn btn-accent btn-touch"
            onClick={() => void gotoFraming()}
            // Natively disabled, unlike the §11.8 locks around it: this is not a
            // standing refusal with a reason to state, it is the rig being busy
            // doing the thing the button asked for — and the label and the line
            // beside it both say so while it lasts.
            disabled={gotoInFlight}
            aria-busy={gotoInFlight}
          >
            <Icon name="mount" size={14} />
            <span className="ml-1">
              {gotoBusy
                ? "Slewing…"
                : gotoPreparing
                  ? "Checking altitude…"
                  : "Go to this target"}
            </span>
          </button>
        )}
        <p className="text-[12px] text-dim leading-snug min-w-0 flex-1" aria-live="polite">
          {gotoReason ??
            (gotoBusy ? (
              // Why the button is dead, while it is dead. A slew-and-centre is
              // 30–90 s of plate-solve-and-correct, and the rig — not this tap —
              // is what says when it is over.
              <>
                The mount is on its way. It plate-solves and corrects when it gets
                there{willRotate ? `, then turns the camera to PA ${Math.round(commandedPaDeg!)}°` : ""},
                and this button comes back when the rig reports the move finished.
              </>
            ) : (
              <>
                Points the scope at the framed centre and plate-solves to re-centre
                when it arrives.
                {willRotate
                  ? ` PA ${Math.round(commandedPaDeg!)}° goes with it, so the rotator turns on this tap too.`
                  : ""}{" "}
                Altitude is re-checked at the tap.
              </>
            ))}
        </p>
      </div>

      {/* default-site nudge (ties to the hardcoded-SF P0; alt still computes) */}
      {site?.is_default && (
        /* `bg-black/20` let the wallpaper read straight through an advisory
           (same legibility cause as #55) — the app's raised surface is opaque. */
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-raise px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            Using a default location — set yours in Settings for accurate
            altitude and visibility.
          </span>
        </div>
      )}

      {/* #129 — the frame on the sky is drawn from the profile, the boxes above
          edit global. Placed here rather than inside the optics drawer because
          the rectangle is drawn whether or not the drawer is open, and this is
          the row the eye lands on between the map and the controls. Names the
          profile and the two focal lengths, because "overridden" alone would
          leave the user comparing a number they can see against one they
          cannot. Clearing it lives in Settings → Imaging train, which is where
          the whole optics block (all seven fields) can be dropped at once. */}
      {opticsPinned && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-warn/60 border-dashed bg-raise px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            This frame is drawn at{" "}
            <span className="mono">
              {Math.round(mergedOptics?.focal_length_mm ?? 0)}mm
            </span>{" "}
            from equipment profile “{overrideProfileName(focalEntry) ?? "(unnamed)"}”
            {typeof focalEntry?.config === "number" &&
              focalEntry.config !== focalEntry.value && (
                <>
                  , not the{" "}
                  <span className="mono">{Math.round(focalEntry.config)}mm</span>{" "}
                  in the box above
                </>
              )}
            . The boxes above edit the global setting; drop the profile's optics
            in Settings → Imaging train to make them take effect.
          </span>
        </div>
      )}

      {/* no-optics CTA banner — names the ACTUAL missing fields (wave-1 §3.2) */}
      {!haveOptics && (
        /* `bg-black/20` let the wallpaper read straight through an advisory
           (same legibility cause as #55) — the app's raised surface is opaque. */
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-raise px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            Framing needs your optics — missing{" "}
            {missingOpticsFields(mergedOptics).join(", ")}. Set them here, or
            connect your camera to fill pixel/sensor automatically.
          </span>
        </div>
      )}

      {/* -------------------------------------------- desktop split / phone stack */}
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        {/* canvas — scrolls inline with the page on phone; on desktop it sits in
            the left grid column. (Was `sticky top-0 z-10 bg-bg/0` on phone, which
            pinned the map and let the planner controls scroll *behind* the
            transparent map — they vanished. Normal in-flow scroll is what users
            expect; a proper pinned-map affordance can be designed later.) */}
        <div className="min-w-0">
          <SkyCanvas
            overlayControls={display.controls}
            center={center}
            rotationDeg={rotation_deg}
            survey={survey}
            stretch={stretch}
            fovZoomDeg={fovZoomDeg}
            optics={mergedOptics}
            focalMmOverride={focalOverride}
            mosaic={mosaic}
            catalogTarget={target}
            night={night}
            mode={mode}
            imageBrightness={imageBrightness}
            surveyDegraded={surveyDegraded}
            degradedText={degradedText}
            onlineFetch={onlineFetch}
            // where the scope IS — raw telemetry, passed straight through. The
            // canvas draws it; nothing animates it toward `center`.
            pointing={statusMount}
            rotator={statusRotator}
            pointingWhere={
              statusMount ? `${statusMount.ra_str} ${statusMount.dec_str}` : null
            }
            // what is out there, and which of it the card is open on
            skyRows={visibleRows}
            selectedObjectId={selectedObject?.id ?? null}
            onPickObject={setSelectedObject}
            onCenterChange={setCenter}
            onRotate={setRotation}
            onZoom={setZoom}
            onSurveyError={onSurveyError}
            onSurveyLoad={onSurveyLoad}
          />
        </div>

        {/* planners — scroll beneath the pinned canvas on phone */}
        <div className="flex flex-col gap-4 min-w-0">
          {/* FIRST in this column, which puts it at the top of the side panel
              on desktop and DIRECTLY UNDER THE CANVAS on a phone — the user
              has just touched the sky, so the answer belongs where their
              finger already is. A popover was the alternative and is the wrong
              shape here: it would fight the gesture this canvas exists for,
              and on a 390px phone a popover over the map IS the map. */}
          <Panel title={selectedObject ? "This object" : "What's in view"}>
            <div className="flex flex-col gap-2">
              {!selectedObject && region.rows.length > 0 && visibleRows.length === 0 ? <p className="text-[12px] text-dim">{display.objects ? 'No objects match your filters in this view.' : 'Object overlays are hidden. Turn them on in Layers.'}</p> : <ObjectCard
                row={selectedObject}
                frameFovDeg={frameFovDeg}
                degraded={region.degraded}
                loading={region.loading}
                emptyRegion={!region.loading && region.rows.length === 0}
                onFrame={(row) => {
                  frameRow(row);
                  setSelectedObject(null);
                }}
                onClose={() => setSelectedObject(null)}
              />}
              {region.error && (
                <p className="text-[12px] text-warn leading-snug">{region.error}</p>
              )}
              {/* A dense field (the Virgo cluster, the Sagittarius star clouds)
                  holds more catalogued objects than any budget can mark. Saying
                  so is the difference between "this is everything here" and
                  "this is the top of a longer list" — and the marked ones are
                  the top, not an arbitrary slice. */}
              {region.truncated && !selectedObject && (
                <p className="text-[12px] text-dim leading-snug">
                  More is catalogued here than can be marked at once — these are
                  the brightest and largest. Zoom in for the rest.
                </p>
              )}
              {/* Server-side refusals that are not shaped like a row: a body
                  whose ephemeris failed this second, the Moon withheld from a
                  caller who cannot be told where this rig stands. Whatever this
                  panel will not say, the screen has to invent. */}
              {region.notes.map((n) => (
                <p key={n} className="text-[12px] text-dim leading-snug">{n}</p>
              ))}
            </div>
          </Panel>

          <Panel title="Survey & framing">
            <SurveyControls
              survey={survey}
              fovZoomDeg={fovZoomDeg}
              rotationDeg={rotation_deg}
              imageBrightness={imageBrightness}
              cameraFovLock={cameraFovLock}
              frameFovDeg={frameFovDeg}
              pixelScaleArcsec={fov.pixel_scale_arcsec}
              plausibility={plausibility}
              catalogTarget={target}
              haveOptics={haveOptics}
              hasTarget={!!target}
              onlineFetch={onlineFetch}
              onSurveyChange={setSurvey}
              onZoom={setZoom}
              onRotate={setRotation}
              onImageBrightness={setImageBrightness}
              onCameraFovLock={onCameraFovLock}
              onNudge={nudge}
              onRecenter={recenter}
            />
          </Panel>

          {/* ---------- mosaic cluster (MosaicPanel lane absent — inline shell) ---------- */}
          <Panel title="Mosaic">
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-end gap-3">
                <Stepper
                  label="Rows"
                  value={rows}
                  onChange={(v) => setMosaic({ rows: Math.round(v) })}
                  min={1}
                  max={10}
                  disabled={!haveOptics}
                />
                <Stepper
                  label="Cols"
                  value={cols}
                  onChange={(v) => setMosaic({ cols: Math.round(v) })}
                  min={1}
                  max={10}
                  disabled={!haveOptics}
                />
                <Stepper
                  label="Overlap"
                  value={Math.round(overlap * 100)}
                  onChange={(v) => setMosaic({ overlap: Math.min(0.5, Math.max(0, v / 100)) })}
                  min={0}
                  max={50}
                  step={5}
                  unit="%"
                  disabled={!haveOptics}
                />
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <Stat
                  label="Total field"
                  glyph={<Icon name="grid" size={12} />}
                  value={
                    haveOptics
                      ? `${fmtAngle(total.total_fov_x_deg)}×${fmtAngle(total.total_fov_y_deg)}`
                      : "—"
                  }
                  hint="Tangent-plane extent the deprojected panels cover (not raw degrees of RA)."
                />
                <Stat
                  label="Panels"
                  glyph={<span aria-hidden>#</span>}
                  value={panelCount}
                  unit={panelCount > 1 ? `${rows}×${cols}` : "single"}
                />
              </div>

              {/* rotation honesty note — reality-aware (CAA spec §5.3), and
                  written from `commandedPaDeg` so it can only ever describe an
                  angle this page actually sends. It used to say "automatically
                  on slew" while Go-to omitted rotation_deg entirely. */}
              {commandedPaDeg != null && (statusRotator ? (
                <p className="text-[12px] text-dim leading-snug">
                  Go to this target — and every slew in a run — sends PA{" "}
                  {Math.round(commandedPaDeg)}° to {statusRotator.name}, which
                  rotates before it centres.
                  {(() => {
                    const cfg = { range_type: "full" as const, range_start_deg: 0,
                                  ...(config?.rotator ?? {}) };
                    const h = adjustedPa(commandedPaDeg, statusRotator, cfg);
                    return h.adjusted ? (
                      <span className="text-warn">
                        {" "}⚠ Outside the range of motion — it will image
                        as {Math.round(h.target)}°.
                      </span>
                    ) : null;
                  })()}
                </p>
              ) : (
                <p className="text-[12px] text-dim leading-snug">
                  Camera angle is manual — set your camera to PA{" "}
                  {Math.round(commandedPaDeg)}° before the run; there is no rotator
                  in the rig.
                </p>
              ))}

              {/* below-limit reality check from the lifted night. While the
                  centre is moving this says so instead of repeating the verdict
                  for the point the frame has already left — that verdict was
                  routinely the opposite one (drag a target down to the horizon
                  and it kept reading "fine" for the whole drag). */}
              {visRecomputing ? (
                <p className="text-[12px] text-dim leading-snug" aria-live="polite">
                  Checking tonight’s altitude for the new centre…
                </p>
              ) : belowLimit ? (
                <div className="flex items-start gap-1.5 text-[12px] text-warn">
                  <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
                  <span>
                    {headerName} stays below{" "}
                    {Math.round(visNight?.alt_limit_deg ?? 30)}° tonight — Sending
                    will ask you to confirm.
                  </span>
                </div>
              ) : null}

              {/* House rule §11.8: the one forward control on this panel used the
                  native `disabled` attribute with its reason only in `title=` —
                  which never fires on the phone and tablet this page is used
                  from, so a finger got a dead button and no sentence. It stays
                  pressable and ANSWERS now; the two static banners on this page
                  already carry the standing explanation, so pressing it adds the
                  one thing they don't — that THIS tap did nothing, and why. */}
              <HonestButton
                className="btn btn-accent btn-touch w-full"
                reason={sendLock}
                onExplain={(r) =>
                  enqueueToast({ level: "warning", title: "Not added to the plan", detail: r })
                }
                onClick={() => void sendToPlan()}
              >
                {sending
                  ? "Adding…"
                  : panelCount > 1
                    ? `Send ${panelCount} panels to Plan`
                    : "Add target to Plan"}
              </HonestButton>
              {seqRunning && (
                <p className="text-[12px] text-warn leading-snug">
                  A sequence is running — the engine snapshots its plan at start,
                  so additions won't be picked up mid-run.
                </p>
              )}
            </div>
          </Panel>

          {/* ---------- visibility (lifts the night up for the reality check) ---------- */}
          {/* rounded to the panel's own fetch key so pans don't re-render it */}
          <VisibilityPanel
            ra_hours={visRa}
            dec_deg={visDec}
            altLimit={site?.horizon_min_deg ?? 30}
            onNight={onVisNight}
          />
        </div>
      </div>
    </div>
  );
}

// keep the named export available too (parity with the other atlas components)
export { AtlasView };
