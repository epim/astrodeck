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
import type { CatalogEntry, MosaicPanel, MosaicResult, Optics, PackStatus, Target, VisibilityNight } from "../types";
import { getPackStatus } from "../api/backends";
import { ARCSEC_PER_RAD } from "../lib/optics";
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
import { adjustedPa } from "../lib/rotation";
import { uid } from "../lib/ids";
import { SkyCanvas } from "../components/atlas/SkyCanvas";
import { SurveyControls } from "../components/atlas/SurveyControls";
import { VisibilityPanel } from "../components/atlas/VisibilityPanel";
import { CatalogSearch } from "../components/atlas/CatalogSearch";
import { Panel, Stat, Stepper, EmptyState } from "../components/ui";
import { Icon } from "../components/icons";
import { confirmDialog } from "../components/ConfirmDialog";
import { api } from "../api";

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
function AtlasEmpty({
  onFreeRoam,
  onPick,
}: {
  onFreeRoam: () => void;
  onPick: (e: CatalogEntry) => void;
}): JSX.Element {
  return (
    <div className="grid place-items-center min-h-[60vh] p-4">
      <div className="panel p-8 max-w-md text-center">
        <EmptyState
          icon="atlas"
          title="Frame a target"
          hint="Search a target right here, pick one from the Mount catalog, or free-roam the sky. Overlay your camera's field, plan a mosaic, and check tonight's visibility."
          action={
            <div className="flex flex-col items-center gap-3 mt-2">
              <CatalogSearch onPick={onPick} placeholder="Search catalog — e.g. M 31" />
              <button type="button" className="btn btn-accent btn-touch" onClick={onFreeRoam}>
                Free-roam the sky
              </button>
            </div>
          }
        />
      </div>
    </div>
  );
}

export default function AtlasView(): JSX.Element {
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

  const setFraming = useStore((s) => s.setFraming);
  const openFraming = useStore((s) => s.openFraming);
  const addTargetsToPlan = useStore((s) => s.addTargetsToPlan);
  const setView = useStore((s) => s.setView);
  const loadConfig = useStore((s) => s.loadConfig);
  const enqueueToast = useStore((s) => s.enqueueToast);

  // Lifted visibility night (VisibilityPanel → here) so the mosaic reality-check
  // can cross-reference best_window / set time (spec §6).
  const [visNight, setVisNight] = useState<VisibilityNight | null>(null);

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

  // Merge for the FOV gate (wave-1 §3.1): config override wins (nonzero), else
  // the camera-reported value from the live merged readout. Focal stays the
  // config/draft value (focalOverride still applies via fovFromOptics).
  const liveOptics = statusOptics ?? computed;
  const mergedOptics: OpticsLike | null = useMemo(() => {
    if (!optics) return null;
    return {
      focal_length_mm: optics.focal_length_mm,
      pixel_size_um: optics.pixel_size_um || liveOptics?.pixel_size_um || 0,
      sensor_width_px: optics.sensor_width_px || liveOptics?.sensor_width_px || 0,
      sensor_height_px: optics.sensor_height_px || liveOptics?.sensor_height_px || 0,
    };
  }, [optics, liveOptics]);

  const [focalDraft, setFocalDraft] = useState<string>("");
  const [savingFocal, setSavingFocal] = useState(false);
  // Inline pixel-size + sensor drafts (same shape as focalDraft). Empty/0 commits
  // "use camera" — the server merge fills them from the connected camera (§3.2).
  const [pixelDraft, setPixelDraft] = useState<string>("");
  const [sensorWDraft, setSensorWDraft] = useState<string>("");
  const [sensorHDraft, setSensorHDraft] = useState<string>("");
  // In-flight guard for Send-to-Plan — blocks a double-tap from double-adding a
  // single target (the server round-trip is async).
  const [sending, setSending] = useState(false);

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
  const commitOpticsPatch = useCallback(
    async (patch: Partial<Optics>) => {
      if (!optics) return;
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
    void commitOpticsPatch({ pixel_size_um: n });
  }, [pixelDraft, optics?.pixel_size_um, commitOpticsPatch]);

  const commitSensorW = useCallback(() => {
    const raw = sensorWDraft.trim();
    const n = raw === "" ? 0 : Math.round(Number(raw));
    if (!Number.isFinite(n) || n < 0) {
      setSensorWDraft(String(optics?.sensor_width_px || ""));
      return;
    }
    if (n === (optics?.sensor_width_px ?? 0)) return;
    void commitOpticsPatch({ sensor_width_px: n });
  }, [sensorWDraft, optics?.sensor_width_px, commitOpticsPatch]);

  const commitSensorH = useCallback(() => {
    const raw = sensorHDraft.trim();
    const n = raw === "" ? 0 : Math.round(Number(raw));
    if (!Number.isFinite(n) || n < 0) {
      setSensorHDraft(String(optics?.sensor_height_px || ""));
      return;
    }
    if (n === (optics?.sensor_height_px ?? 0)) return;
    void commitOpticsPatch({ sensor_height_px: n });
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

  // Stable identities: SkyCanvas's fetch effect depends on these via loadSurvey.
  const onSurveyError = useCallback(() => setSurveyDegraded(true), []);
  const onSurveyLoad = useCallback(() => setSurveyDegraded(false), []);

  if (!framing) {
    return <AtlasEmpty onFreeRoam={onFreeRoam} onPick={openFraming} />;
  }

  const { center, rotation_deg, survey, stretch, fovZoomDeg, mosaic, target } = framing;
  const mode: "survey" | "schematic" = survey === "schematic" ? "schematic" : "survey";

  // ---- session patchers routed into setFraming ----
  const setCenter = (ra_hours: number, dec_deg: number) =>
    setFraming({ center: { ra_hours: wrapRaHours(ra_hours), dec_deg } });
  const setRotation = (deg: number) => setFraming({ rotation_deg: deg });
  const setZoom = (deg: number) => setFraming({ fovZoomDeg: deg });
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

  // Below-limit / set-time advisory drives the Send override gate (spec §6).
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
        detail:
          rotation_deg > 0.5
            ? `Set your camera to PA ${Math.round(rotation_deg)}° before this run.`
            : undefined,
      });
      setView("sequence");
    } finally {
      setSending(false);
    }
  };

  // crosses-the-meridian-ish hint: a wide mosaic near transit. We don't have a
  // per-panel ephemeris here, so this stays advisory text only when a rotation is
  // set on a multi-panel grid (the honest "expect a stitch seam" note, spec §6).
  const headerName = target?.name ?? "Free roam";

  return (
    <div className="flex flex-col gap-3">
      {/* ----------------------------------------------------------- header */}
      <header className="flex flex-wrap items-end gap-x-4 gap-y-2">
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
        <div className="flex-1" />
        {/* inline focal-length field — self-contained optics (spec §6 C1-B1) */}
        <label className="flex flex-col gap-1">
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
              className="field btn-touch w-24 mono text-right"
            />
            <span className="inline-flex items-center px-2 border border-l-0 border-line2 bg-bg text-dim text-xs">
              mm
            </span>
          </span>
        </label>
        {/* pixel-size + sensor fields — 0/empty commits "use camera" (§3.2). The
            "from camera" chip + placeholder appear when a connected camera fills
            the field the config leaves blank. */}
        <label className="flex flex-col gap-1">
          <span className="label inline-flex items-center gap-1">
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
              placeholder={pxFromCam ? String(liveOptics?.pixel_size_um ?? "") : undefined}
              disabled={!optics}
              onChange={(e) => setPixelDraft(e.target.value)}
              onBlur={commitPixel}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera pixel size in micrometres (0 uses the connected camera)"
              className="field btn-touch w-20 mono text-right"
            />
            <span className="inline-flex items-center px-2 border border-l-0 border-line2 bg-bg text-dim text-xs">
              µm
            </span>
          </span>
        </label>
        <label className="flex flex-col gap-1">
          <span className="label inline-flex items-center gap-1">
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
              disabled={!optics}
              onChange={(e) => setSensorWDraft(e.target.value)}
              onBlur={commitSensorW}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera sensor width in pixels (0 uses the connected camera)"
              className="field btn-touch w-20 mono text-right"
            />
            <span className="inline-flex items-center px-2 border border-l-0 border-line2 bg-bg text-dim text-xs">
              px
            </span>
          </span>
        </label>
        <label className="flex flex-col gap-1">
          <span className="label inline-flex items-center gap-1">
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
              disabled={!optics}
              onChange={(e) => setSensorHDraft(e.target.value)}
              onBlur={commitSensorH}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
              aria-label="Camera sensor height in pixels (0 uses the connected camera)"
              className="field btn-touch w-20 mono text-right"
            />
            <span className="inline-flex items-center px-2 border border-l-0 border-line2 bg-bg text-dim text-xs">
              px
            </span>
          </span>
        </label>
        <button
          type="button"
          className="btn btn-touch"
          onClick={calibrateFromSolve}
          disabled={!canCalibrate}
          title={
            canCalibrate
              ? "Back-compute focal length from the last plate solve's pixel scale"
              : "Plate-solve a frame first (Mount → Solve & Sync)"
          }
        >
          <Icon name="refresh" size={14} />
          <span className="ml-1">Calibrate from last solve</span>
        </button>
      </header>

      {/* default-site nudge (ties to the hardcoded-SF P0; alt still computes) */}
      {site?.is_default && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-black/20 px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            Using a default location — set yours in Settings for accurate
            altitude and visibility.
          </span>
        </div>
      )}

      {/* no-optics CTA banner — names the ACTUAL missing fields (wave-1 §3.2) */}
      {!haveOptics && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-black/20 px-2 py-1">
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
        {/* canvas — pinned at top on phone (first in DOM, sticky there) */}
        <div className="lg:static sticky top-0 z-10 bg-bg/0">
          <SkyCanvas
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
            onCenterChange={setCenter}
            onRotate={setRotation}
            onZoom={setZoom}
            onSurveyError={onSurveyError}
            onSurveyLoad={onSurveyLoad}
          />
        </div>

        {/* planners — scroll beneath the pinned canvas on phone */}
        <div className="flex flex-col gap-4 min-w-0">
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

              {/* rotation honesty note — reality-aware (CAA spec §5.3) */}
              {rotation_deg > 0.5 && (statusRotator ? (
                <p className="text-[12px] text-dim leading-snug">
                  Camera will rotate to PA {Math.round(rotation_deg)}°
                  automatically on slew ({statusRotator.name}).
                  {(() => {
                    const cfg = { range_type: "full" as const, range_start_deg: 0,
                                  ...(config?.rotator ?? {}) };
                    const h = adjustedPa(rotation_deg, statusRotator, cfg);
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
                  {Math.round(rotation_deg)}° before the run; there is no rotator
                  in the rig.
                </p>
              ))}

              {/* below-limit reality check from the lifted night */}
              {belowLimit && (
                <div className="flex items-start gap-1.5 text-[12px] text-warn">
                  <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
                  <span>
                    {headerName} stays below{" "}
                    {Math.round(visNight?.alt_limit_deg ?? 30)}° tonight — Sending
                    will ask you to confirm.
                  </span>
                </div>
              )}

              <button
                type="button"
                className="btn btn-accent btn-touch w-full"
                disabled={!haveOptics || seqRunning || sending}
                title={
                  seqRunning
                    ? "Stop the running sequence before adding targets"
                    : !haveOptics
                      ? "Set a focal length first"
                      : undefined
                }
                onClick={() => void sendToPlan()}
              >
                {sending
                  ? "Adding…"
                  : panelCount > 1
                    ? `Send ${panelCount} panels to Plan`
                    : "Add target to Plan"}
              </button>
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
            ra_hours={Math.round(center.ra_hours * 1000) / 1000}
            dec_deg={Math.round(center.dec_deg * 100) / 100}
            altLimit={site?.horizon_min_deg ?? 30}
            onNight={setVisNight}
          />
        </div>
      </div>
    </div>
  );
}

// keep the named export available too (parity with the other atlas components)
export { AtlasView };
