// SkyHub.tsx - the app's home screen: point the phone at the sky, lock something
// in the reticle, start a night (hub-sky plan A.1, C, E).
//
// The stack, top to bottom, is the design's own and every row earns its place:
//
//   browse banner   only with no rig - and it names the ONE thing that needs one
//   status row      how many targets are in reach, how clear it is, which site
//   finder          the sky, or the survey imagery once FRAME is on
//   toolbar         AR CAMERA / MAP, FRAME / DONE / ADJUST, GYRO
//   framing card    FRAME mode only
//   lock card       what is in the reticle, and the three things to do with it
//   reach strip     everything else that is clear and up
//   dome card       the cloud between here and there, on the hemisphere (D-SKY-2)
//
// ONE MODEL, MANY CARDS. Every number on this screen is a function of the same
// six inputs (site, time, optics, weather, horizon, catalogue) and is derived
// once, in `useSkyModel`. The cards below take props. The failure that buys is
// specific: without it the reticle can call a target CLEAR while the strip two
// rows down calls the same target CLOUDED, and both would be "right".
//
// WHAT THIS FILE OWNS that the model does not: the three pieces of transient UI
// state that are not data - is the lens dial open, is the layers popover open,
// is FRAME on - plus the box measurement and the FRAME lifecycle. None of the
// three is persisted (plan F): a finder that reopens pointing where it was last
// night, mid-framing, is worse than one that reopens at tonight's best target.
//
// It also owns the `?lock=<id>` deep link, which is how the targets sheet and
// the catalog search aim the finder from outside it - see the block above the
// effect that consumes it.

import {
  useCallback, useEffect, useMemo, useRef, useState,
  type JSX, type PointerEvent as ReactPointerEvent,
} from "react";
import { Card, IconButton48 } from "../../ui";
import { buildHash, nav, useRoute } from "../../router";
import { useBreakpoint } from "../../breakpoint";
import { useLock } from "../../lib/gateHook";
import { finishesAt } from "../../lib/allocation";
import { fmtClock } from "../../lib/format";
import {
  SkyView,
  skyPrefs,
  useSkyModel,
  type PatchModel,
  type SkyKind,
  type SkyTarget,
} from "./finder";
import { KIND_LABEL, D2R, walkTrack, type TrackSample } from "./finder";
import { windSummary } from "../weather/dome/domeOverlay";
import { lstHours } from "../../../lib/altaz";
import { BrowseBanner } from "./cards/BrowseBanner";
import { StatusRow } from "./cards/StatusRow";
import { DomeCard, DOME_CARD_ID } from "./cards/DomeCard";
import { LensDial } from "./cards/LensDial";
import { LayersPopover, type LayerKey } from "./cards/LayersPopover";
import { LockCard } from "./cards/LockCard";
import { PatchCard } from "./cards/PatchCard";
import { ReachStrip } from "./cards/ReachStrip";
import { SkyGlyph } from "./cards/glyphs";
import { obstructedReason, ctaToast, type LockCta } from "./cards/lockCta";
import { FrameHost } from "./frame/FrameHost";
import { FramingCard } from "./frame/FramingCard";
import { FramedOverlay } from "./frame/FramedOverlay";
import { FrameTools, NO_OBJECT_REASON, NO_OPTICS_REASON } from "./frame/FrameTools";
import { SurveyPopover } from "./frame/SurveyPopover";
import { MosaicNightCard } from "./frame/MosaicNightCard";
import { PACK_POLL_MS, shouldPollPack, surveyDegradedText } from "./frame/degraded";
import {
  clampZoom, fitObjectZoom, frameFovDeg, matchCameraZoom, pinchZoom, pointerDist,
} from "./frame/zoom";
import { OVERLAP, fetchPanels, framedStrip, frameText } from "./frame/mosaic";
import { effectiveOptics } from "../../../lib/effective";
import { fovFromOptics, type OpticsLike } from "../../../lib/framing";
import { useSkyRegion, type SkyRow } from "../../../lib/skyRegion";
import { useCapability } from "../../../lib/caps";
import { resolveWheel } from "../../../components/flows/cyclePlanRows";
import { getPackStatus } from "../../../api/backends";
import {
  useConfig,
  useEquipConnected,
  useFraming,
  useNight,
  useSite,
  useStatus,
  useStore,
  useWeather,
} from "../../../store";
import type { CatalogEntry, PackStatus } from "../../../types";

// ------------------------------------------------------------------- copy

export const SECURE_REASON =
  "AR camera and gyro need a secure connection - set up in Connection";
export const NO_GYRO_REASON = "No orientation sensor here - drag the sky to pan.";

/**
 * When FRAME has nothing at all to work with.
 *
 * It used to fire whenever there was no LOCK, which meant survey imagery of an
 * uncatalogued patch was unreachable from the new UI (review #29) - the Atlas's
 * free-roam session, which `store.openFraming()` has always supported with no
 * argument, had no door. Now the reticle's own patch is enough, and the only
 * remaining refusal is a reticle aimed at ground: below the horizon there is no
 * RA and Dec to frame, so there is nothing to fetch imagery for.
 */
export const FRAME_NEEDS_AIM =
  "Aim above the horizon first - FRAME needs a target or a patch of sky to look at.";

/** Kept as the name the plan and the tests use; the sentence is the one above. */
export const FRAME_NEEDS_LOCK = FRAME_NEEDS_AIM;

/** What a `?frame=1` deep link says when there is no framing session to resume
 *  and nothing aimed at either. `mount.tsx`'s FRAME button always opens one
 *  first, so this is the hand-typed-URL case. */
export const FRAME_PARAM_NOTHING =
  "Nothing is framed yet - aim at a target and press FRAME.";

/** What a `#/sky?lock=<id>` deep link says when the ranking settles without the
 *  object it names. It prints the id the link asked for, because that is the
 *  only thing the user (or the support call reading the URL out) can act on. */
export const lockNotListed = (id: string): string => `${id} is not in tonight's list`;

/**
 * How long a `?lock=` deep link waits for the id to appear in the ranked list.
 *
 * The targets sheet and the catalog search both aim by navigating here, and the
 * hub can be MOUNTED before either catalogue source has answered - so the id is
 * absent for a beat on every single deep link. Toasting on the first render
 * would mean every working link also called itself broken.
 */
export const LOCK_WAIT_MS = 2500;

const DESIGN_BOX_PX = 370;
const MAX_BOX_PX = 720;

interface FrameState {
  on: boolean;
  set: boolean;
  id: string | null;
}

/** A `SkyTarget` in the shape `store.openFraming` wants. The catalogued extent
 *  travels with it, so SkyCanvas draws the real ellipse: all three catalogue
 *  sources send `size_arcmin` and the finder now carries it through the merge.
 *  A row that carried none still hands over 0, and 0 draws no ellipse - which
 *  is the honest outcome of not knowing the size rather than a guessed one. */
export function entryOf(t: SkyTarget): CatalogEntry {
  return {
    id: t.id,
    name: t.name,
    type: t.kind,
    ra_hours: t.ra_hours,
    dec_deg: t.dec_deg,
    mag: null,
    size_arcmin: t.sizeArcmin ?? 0,
  };
}

/**
 * A free-roam framing's identity: the patch it was framed at, spelled exactly
 * as the quick sheet is opened with it (`pressPatch`'s `name`).
 *
 * ONE STRING, TWO JOBS, and they have to be the same string. It is the plan's
 * `mosaic_group` (so re-framing the same patch replaces its panels instead of
 * appending a second set) and it is what `framingMatches` compares against on
 * the quick sheet (so a patch's own mosaic is the one that reaches the plan and
 * another patch's is not).
 */
export function patchId(patch: PatchModel): string {
  return `${patch.raStr} ${patch.decStr}`;
}

export function SkyHub(): JSX.Element {
  const bp = useBreakpoint();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const layersAnchor = useRef<HTMLElement | null>(null);
  const [measured, setMeasured] = useState(0);

  // The finder is square-ish and width-driven, so its pixels-per-degree follows
  // the measured column: 370 on a phone, up to 720 at tablet and desktop, so the
  // same 60 degrees of sky fills the wider box (ARCHITECTURE section 4).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof window === "undefined") return;
    const read = () => setMeasured(el.clientWidth || 0);
    read();
    const RO = (window as unknown as { ResizeObserver?: new (cb: () => void) => { observe(t: Element): void; disconnect(): void } }).ResizeObserver;
    if (!RO) {
      window.addEventListener("resize", read);
      return () => window.removeEventListener("resize", read);
    }
    const ro = new RO(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const boxPx = Math.min(MAX_BOX_PX, Math.max(260, measured || DESIGN_BOX_PX));

  const model = useSkyModel(boxPx);
  const equipConnected = useEquipConnected();
  const status = useStatus();
  const config = useConfig();
  const night = useNight();
  const framing = useFraming();
  const weather = useWeather();
  const canViewWeather = useCapability("view.weather");
  const enqueueToast = useStore((s) => s.enqueueToast);
  const openFraming = useStore((s) => s.openFraming);
  const setFraming = useStore((s) => s.setFraming);

  const site = useSite();
  const [lensOpen, setLensOpen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [surveyOpen, setSurveyOpen] = useState(false);
  const surveyAnchor = useRef<HTMLElement | null>(null);
  const [frame, setFrame] = useState<FrameState>({ on: false, set: false, id: null });
  const [pool, setPool] = useState<string[]>(() => skyPrefs.getPool());
  // Both of these are PERSISTED CHOICES with a control that writes them again
  // (`SurveyPopover`). They were read-only `useState` seeds with no setter, so
  // `prefs.setFrameMode` and `prefs.setSurveyBright` were exported and never
  // called and the "explicit choice" FrameHost's header describes was whatever
  // an older build had left in localStorage (review #31).
  const [frameMode, setFrameModeState] = useState<"survey" | "schematic">(() => skyPrefs.getFrameMode());
  const [surveyBright, setSurveyBrightState] = useState<number>(() => skyPrefs.getSurveyBright());
  const [surveyDegraded, setSurveyDegraded] = useState(false);
  const [pack, setPack] = useState<PackStatus | null>(null);
  const quick = useMemo(() => skyPrefs.getQuick(), []);

  const setFrameMode = useCallback((m: "survey" | "schematic") => {
    setFrameModeState(m);
    skyPrefs.setFrameMode(m);
  }, []);
  const setSurveyBright = useCallback((v: number) => {
    setSurveyBrightState(v);
    skyPrefs.setSurveyBright(v);
  }, []);

  // ---- `#/sky?lock=<id>`, the way every other screen aims this one ---------
  //
  // The targets sheet's rows and the catalog search's picks both aim by
  // navigating to `#/sky?lock=<id>` (sheets/targets.tsx `aim()`): a sheet is
  // route state, not a child of the screen underneath it, so the id cannot be
  // handed over as a prop and travels in the hash instead - which also makes
  // the choice a deep link support can read out over the phone.
  //
  // THE PARAM IS CONSUMED, NOT LEFT LYING. It is cleared with `nav.replace` the
  // moment it has been acted on, for two reasons: a later re-render (the 30 s
  // clock, a lens toggle) would otherwise re-aim and drag the view back off
  // whatever the user had panned to, and Back from the next screen would land
  // on a URL that aims all over again.
  const route = useRoute();
  const lockParam = route.params.lock ?? null;
  const handledLockRef = useRef<string | null>(null);
  const routeRef = useRef(route);
  routeRef.current = route;
  const setViewRef = useRef(model.setView);
  setViewRef.current = model.setView;
  const toastRef = useRef(enqueueToast);
  toastRef.current = enqueueToast;

  const clearLockParam = useCallback(() => {
    const r = routeRef.current;
    const params = { ...r.params };
    delete params.lock;
    nav.replace(buildHash({ hub: r.hub, sub: r.sub, sheets: r.sheets, params }));
  }, []);

  const targets = model.targets;
  const aimReady = model.aimReady;
  useEffect(() => {
    if (lockParam == null || lockParam === "") {
      handledLockRef.current = null;
      return;
    }
    if (handledLockRef.current === lockParam) return;
    const t = targets.find((x) => x.id === lockParam);
    if (t) {
      // The finder's own aim call, so the deep link ends in exactly the state a
      // tap on the marker would: centred, tracked, and with the auto-aim off.
      setViewRef.current({ az: t.azNow, alt: t.altNow, trackId: t.id });
      // The param is HELD until the merged ranking has settled. The ranked
      // picks carry no alt/az and the region rows carry the server's, so a link
      // consumed on the first answer aims at a provisional position and then
      // stops following - the same couple of degrees the finder's own opening
      // aim used to be out by. While it is held, every new ranking re-aims.
      if (!aimReady) return;
      handledLockRef.current = lockParam;
      clearLockParam();
      return;
    }
    // Not there YET is the normal case for the first beat after a mount, so the
    // refusal is on a timer. When it fires, the ranking has settled without it:
    // say so once, and clear the param rather than leaving a link that will try
    // again on every render.
    const timer = setTimeout(() => {
      if (handledLockRef.current === lockParam) return;
      handledLockRef.current = lockParam;
      toastRef.current({ level: "warning", title: lockNotListed(lockParam) });
      clearLockParam();
    }, LOCK_WAIT_MS);
    return () => clearTimeout(timer);
  }, [lockParam, targets, aimReady, clearLockParam]);

  const lock = model.lock;
  const capture = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "capture" });
  const onExplain = capture.onExplain;

  // ---- the skydome card (D-SKY-2) -----------------------------------------
  //
  // BY ID, NOT BY REF. The id is on the card's own root and it IS the anchor:
  // the same string names the element for the probe, for `data-testid` and for
  // anything that ever links to `#sky-dome-card`. A ref would have to hang off
  // a wrapper this file added around the card, which puts the scroll target and
  // the anchor on two different elements the first time either one moves.
  const onDome = useCallback(() => {
    const el = typeof document === "undefined"
      ? null
      : document.getElementById(DOME_CARD_ID);
    if (!el) return;
    // Reduced motion is a vestibular setting, not a taste in polish
    // (ARCHITECTURE section 6). A smooth scroll past six cards is precisely the
    // motion it asks us not to make.
    const reduce = typeof window !== "undefined"
      && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, []);

  /**
   * The dome's canvas height, in CSS pixels.
   *
   * `SkyDome` sizes itself as `r = min((w - 24) / 2, (h - 28) / (1 + sin 32))`
   * - 1 + sin 32 = 1.52992 is the dome's real vertical extent, which is taller
   * than the zenith (see `domeExtent`). So for any column width there is a
   * height beyond which the extra pixels are empty sky, and below which the
   * dome is squeezed.
   *
   * ONE NUMBER HAS TO COVER THE WHOLE PHONE BAND. At 390 px the body leaves
   * 358, the card's padding and border leave the canvas ~332, the
   * width-limited radius is 154, and the height that first reaches it is
   * 28 + 154 * 1.52992 = 264: 280 carries about 16 px of headroom there. At
   * 430 px (the large phones) the canvas is ~372, the width-limited radius is
   * 174, and saturating it would want 294 - so at 280 those phones are height
   * limited and every one of the 280 pixels is dome. 280 is the height that is
   * honest across the band rather than tuned to the narrowest member of it.
   *
   * Wider columns saturate much later: a 720 px box would not until 540, which
   * would make the LAST card taller than the finder at the top of the screen.
   * 0.78 of the measured box keeps it proportional to the finder, and the 360
   * ceiling keeps it a card rather than a second screen.
   */
  const domeHeight = bp === "phone" ? 280 : Math.min(360, Math.round(boxPx * 0.78));

  const domeWind = useMemo(() => windSummary(weather?.now ?? null), [weather?.now]);

  const pointing = status?.mount && typeof status.mount.alt === "number"
    && typeof status.mount.az === "number" && status.mount.alt >= 0
    ? { alt: status.mount.alt, az: status.mount.az }
    : null;

  /**
   * The lock's walk to dawn, in alt/az, for the dome to draw.
   *
   * `model.track` is the same walk ALREADY PROJECTED into the finder's flat sky
   * box (`TrackRender` is SVG polylines in box pixels), so it cannot be put on
   * a hemisphere; the alt/az samples behind it never leave `useSkyModel`'s
   * memo. Re-walking here is 29 steps of trigonometry over the model's own
   * inputs - the same `walkTrack`, the same horizon points - so the arc on the
   * dome and the arc on the finder cannot disagree about where the object goes.
   *
   * Two differences from the finder's own context, and both are `DomeScreen`'s.
   * The horizon mask is ALWAYS on, where the finder's follows the layers
   * popover: the overlay draws the profile on this dome unconditionally, so a
   * track coloured as if there were no mask would run red segments over open
   * sky and blue ones behind a tree line. And nothing is ever coloured "cloud
   * hold" - the cloud on this card is the dome itself, measured, and a forecast
   * hold painted over a measurement is worse than no hold at all.
   */
  const trackLat = typeof site?.latitude === "number" ? site.latitude : null;
  const trackLon = typeof site?.longitude === "number" ? site.longitude : null;
  const darkEnd = model.visibility?.dark_end_unix ?? null;
  const horizonPoints = model.horizonPoints;
  const horizonMinDeg = site?.horizon_min_deg ?? 0;
  const modelNowMs = model.nowMs;
  const domeTrack = useMemo<TrackSample[] | null>(() => {
    if (!lock || trackLat === null || trackLon === null) return null;
    if (typeof darkEnd !== "number") return null;
    const nowSec = modelNowMs / 1000;
    const hoursToDawn = (darkEnd - nowSec) / 3600;
    if (!(hoursToDawn > 0)) return null;
    const lst = lstHours(trackLon, nowSec);
    const samples = walkTrack(lock.dec_deg * D2R, (lst - lock.ra_hours) * 15 * D2R, {
      latDeg: trackLat,
      hoursToDawn,
      horizon: horizonPoints,
      horizonMinDeg,
      maskOn: true,
      holdAt: () => false,
    });
    return samples.length > 0 ? samples : null;
  }, [lock, trackLat, trackLon, darkEnd, modelNowMs, horizonPoints, horizonMinDeg]);

  // ---- optics, shared by the reticle, the framing meta and the mosaic call --
  const mergedOptics: OpticsLike | null = useMemo(
    () =>
      effectiveOptics(
        config,
        config?.optics ?? null,
        status?.optics ?? config?.optics_computed ?? null,
      ),
    [config, status],
  );
  const fov = useMemo(() => fovFromOptics(mergedOptics), [mergedOptics]);
  const onlineFetch = config?.survey?.online_fetch ?? false;

  // ---- FRAME mode ---------------------------------------------------------
  const frameTarget = frame.id;
  const region = useSkyRegion(
    framing?.center ?? { ra_hours: 0, dec_deg: 0 },
    framing?.fovZoomDeg ?? 0,
    frame.on && framing != null,
  );

  // The single-frame field of view, which is what FIT OBJECT falls back to and
  // what MATCH CAMERA zooms to. Zero means the rig's optics are unknown, and
  // both controls then say so instead of zooming to a guess.
  const oneFrameDeg = frameFovDeg(fov.fov_x_deg, fov.fov_y_deg);

  // The offline pack's state, polled ONLY while the survey is degraded with no
  // online source - the one state whose banner copy depends on it
  // (`AtlasView.tsx:234-244` does exactly this, and for the same reason).
  useEffect(() => {
    if (!frame.on || !shouldPollPack(surveyDegraded, onlineFetch)) return;
    let live = true;
    const tick = (): void => {
      getPackStatus().then((p) => { if (live) setPack(p); }).catch(() => { /* the copy falls back */ });
    };
    tick();
    const id = setInterval(tick, PACK_POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, [frame.on, surveyDegraded, onlineFetch]);

  const enterFrame = useCallback(
    (t: SkyTarget, keep: boolean) => {
      if (!keep) {
        openFraming(entryOf(t));
        // `openFraming` seeds 25% overlap (the Atlas's default) while the README
        // formula, the prototype and the sentence this card PRINTS all use 15%.
        // Correcting it here rather than trusting the seed is the difference
        // between the copy and the panel pitch agreeing.
        setFraming({ mosaic: { rows: 1, cols: 1, overlap: OVERLAP }, rotation_deg: 0, panels: [] });
      }
      model.setView({ az: t.azNow, alt: t.altNow, trackId: null });
      setFrame({ on: true, set: keep, id: t.id });
    },
    [openFraming, setFraming, model],
  );

  /**
   * FREE-ROAM: survey imagery of a patch the catalogue is silent about
   * (review #29).
   *
   * `store.openFraming()` with no entry is the Atlas's own free-roam door and
   * has been all along - it seeds a session with no `target` and a stable
   * `freeroamId` so a multi-panel mosaic still groups in the plan. The new UI
   * simply never opened it. The centre is then moved to the RETICLE's patch
   * rather than left on the mount, because the reticle is what the user is
   * looking at and the mount may be parked.
   */
  const enterFreeRoam = useCallback(
    (patch: PatchModel) => {
      openFraming();
      setFraming({
        center: { ra_hours: patch.ra_hours, dec_deg: patch.dec_deg },
        // THE GROUP ID IS THE PATCH, not the mount. `openFraming` derives its
        // `freeroamId` from wherever the mount happens to be pointing, which is
        // two things wrong at once: the id names coordinates the session is not
        // at, and two free-roam sessions started from one parked position share
        // it - so framing a second patch would REPLACE the first one's panels in
        // the plan. This is also the string the quick sheet is opened with
        // (`pressPatch`'s `name`), which is what lets `framingMatches` recognise
        // a patch's own framing there.
        freeroamId: patchId(patch),
        mosaic: { rows: 1, cols: 1, overlap: OVERLAP },
        rotation_deg: 0,
        panels: [],
      });
      setFrame({ on: true, set: false, id: null });
    },
    [openFraming, setFraming],
  );

  /** Resume FRAME on the session the store already holds - what `?frame=1`
   *  means, and what `mount.tsx`'s FRAME button set up before navigating. */
  const resumeFrame = useCallback(() => {
    const f = useStore.getState().framing;
    if (!f) return false;
    setFrame({ on: true, set: f.panels.length > 0, id: f.target?.id ?? null });
    return true;
  }, []);

  const finishFrame = useCallback(async () => {
    const f = useStore.getState().framing;
    if (!f) { setFrame((s) => ({ ...s, on: false, set: true })); return; }
    const { rows, cols } = f.mosaic;
    const panels = await fetchPanels({
      ra_hours: f.center.ra_hours,
      dec_deg: f.center.dec_deg,
      rows,
      cols,
      // The SESSION's overlap, never the constant: re-asserting 0.15 here would
      // make the request right even when the framing it was computed from is
      // wrong, and the panel pitch on screen is what the user framed by.
      overlap: f.mosaic.overlap,
      rotation_deg: f.rotation_deg,
      fov_x_deg: fov.fov_x_deg,
      fov_y_deg: fov.fov_y_deg,
    });
    // The ENGINE's panel centres, kept on the framing session so the quick
    // sheet queues exactly what was drawn here. Nothing is added to the plan
    // yet: the user has framed a target, not chosen a night.
    setFraming({ panels });
    setFrame((s) => ({ ...s, on: false, set: true }));
    // WHAT THE TOAST MAY CLAIM. It used to say the framing "goes into the flow",
    // and nothing read `framing.panels` at all (review #3). It does now - the
    // quick sheet turns them into plan targets on GENERATE FLOW (plan H.6: the
    // engine's mosaic mechanism IS N targets sharing a `mosaic_group`, not a
    // flow stage) - so the sentence names the button that does it and the shape
    // it will take, rather than a stage that does not exist.
    enqueueToast({
      level: "success",
      title: `Framing kept - ${frameText(cols, rows, f.rotation_deg)}.`,
      detail: panels.length > 1
        ? `GENERATE FLOW queues all ${panels.length} panels as plan targets, one pass each.`
        : "GENERATE FLOW centres the night here instead of on the catalogue position.",
    });
  }, [fov.fov_x_deg, fov.fov_y_deg, setFraming, enqueueToast]);

  const clearFrame = useCallback(() => {
    setFraming({ mosaic: { rows: 1, cols: 1, overlap: OVERLAP }, rotation_deg: 0, panels: [] });
    setFrame({ on: false, set: false, id: null });
    enqueueToast({ level: "info", title: "Framing removed - the flow centres on the catalogue position." });
  }, [setFraming, enqueueToast]);

  // ---- the framing tools the Atlas had and the phone had lost -------------
  //
  // `SkyCanvas` writes `fovZoomDeg` from a WHEEL and the `+`/`-` KEYS. A phone
  // has neither, so before these the survey field of view was fixed for the
  // whole session on the only device this hub is designed for (review #27).
  const setZoom = useCallback((f: number) => setFraming({ fovZoomDeg: clampZoom(f) }), [setFraming]);

  const fitObject = useCallback(() => {
    const f = useStore.getState().framing;
    if (!f) return;
    setZoom(fitObjectZoom(f.target, oneFrameDeg));
  }, [setZoom, oneFrameDeg]);

  const matchCamera = useCallback(() => {
    if (!(oneFrameDeg > 0)) return;
    setZoom(matchCameraZoom(oneFrameDeg));
  }, [setZoom, oneFrameDeg]);

  /**
   * RECENTRE (review #35). After dragging the survey off the object there was
   * no way back but leaving FRAME and re-entering, which also threw away the
   * mosaic and the angle.
   *
   * A catalogued session goes back to the OBJECT; a free-roam one goes back to
   * where the mount is pointing, which is the only other centre it can name -
   * and the button's own sub-label says which, so it never promises the wrong
   * one (`SurveyControls.tsx:139` made the same distinction).
   */
  const recentre = useCallback(() => {
    const st = useStore.getState();
    const f = st.framing;
    if (!f) return;
    if (f.target) {
      setFraming({ center: { ra_hours: f.target.ra_hours, dec_deg: f.target.dec_deg } });
      return;
    }
    const m = st.status?.mount;
    if (m && typeof m.ra_hours === "number" && typeof m.dec_deg === "number") {
      setFraming({ center: { ra_hours: m.ra_hours, dec_deg: m.dec_deg } });
      return;
    }
    enqueueToast({
      level: "warning",
      title: "Nothing to recentre on - this framing has no object and the mount has not reported a position.",
    });
  }, [setFraming, enqueueToast]);

  /**
   * PINCH, on the wrapper rather than inside `SkyCanvas` (a file this task does
   * not own). Two pointers down means a zoom gesture: the ratio of the starting
   * span to the current one IS the field-of-view ratio, so fingers apart show
   * less sky - the same direction the wheel and `+` already take.
   *
   * Single-pointer events are left entirely alone, so SkyCanvas's own drag-to-
   * pan is untouched; this only ever reads the second pointer.
   */
  const pinchRef = useRef<{ pts: Map<number, { x: number; y: number }>; startDist: number; startFov: number } | null>(null);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (!frame.on) return;
    const st = pinchRef.current ?? { pts: new Map(), startDist: 0, startFov: 0 };
    st.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (st.pts.size === 2) {
      const [a, b] = [...st.pts.values()];
      st.startDist = pointerDist(a, b);
      st.startFov = useStore.getState().framing?.fovZoomDeg ?? 1;
    }
    pinchRef.current = st;
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const st = pinchRef.current;
    if (!st || !st.pts.has(e.pointerId)) return;
    st.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (st.pts.size !== 2 || !(st.startDist > 0)) return;
    const [a, b] = [...st.pts.values()];
    setZoom(pinchZoom(st.startFov, st.startDist, pointerDist(a, b)));
  };

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const st = pinchRef.current;
    if (!st) return;
    st.pts.delete(e.pointerId);
    // The gesture is over the moment it stops being a pinch: leaving the start
    // distance behind would make the NEXT second finger continue this zoom from
    // a span measured a minute ago.
    if (st.pts.size < 2) { st.startDist = 0; st.startFov = 0; }
    if (st.pts.size === 0) pinchRef.current = null;
  };

  const frameLabel = frame.on ? "DONE" : frame.set ? "ADJUST" : "FRAME";
  // A patch of sky is enough now: free-roam is what the third branch below is.
  const frameReason = frame.on || lock || model.patch ? null : FRAME_NEEDS_AIM;
  const onFramePress = () => {
    if (frame.on) { void finishFrame(); return; }
    const t = lock ?? model.reachList[0];
    if (t) { enterFrame(t, frame.set && frame.id === t.id); return; }
    // ADJUST on a kept free-roam framing RESUMES it. Re-entering through
    // `enterFreeRoam` would call `openFraming` again and throw away the mosaic
    // and the angle the button is offering to adjust.
    if (frame.set && frame.id === null && resumeFrame()) return;
    if (model.patch) { enterFreeRoam(model.patch); return; }
  };

  // ---- `#/sky?frame=1`, the way Rig > Mount's catalogue hands a row over ----
  //
  // `legacyBridge.ts:38` maps the classic `atlas` view onto this URL and
  // `mount.tsx:746 openFraming(r)` writes a framing session before navigating
  // here. Nothing read the param, so the FRAME button in the mount catalogue
  // landed the user on the schematic finder with FRAME off, no visible change,
  // and a framing session sitting unused in the store (review #28).
  //
  // Consumed with `nav.replace`, exactly as `?lock=` is, and for the same two
  // reasons: a later re-render would otherwise re-enter FRAME after the user
  // pressed DONE, and Back from the next screen would land on a URL that does
  // it all again.
  const frameParam = route.params.frame ?? null;
  const handledFrameRef = useRef<string | null>(null);
  const clearFrameParam = useCallback(() => {
    const r = routeRef.current;
    const params = { ...r.params };
    delete params.frame;
    nav.replace(buildHash({ hub: r.hub, sub: r.sub, sheets: r.sheets, params }));
  }, []);

  useEffect(() => {
    if (frameParam !== "1") { handledFrameRef.current = null; return; }
    if (handledFrameRef.current === frameParam) return;
    handledFrameRef.current = frameParam;
    clearFrameParam();
    if (resumeFrame()) return;
    // No session to resume: a hand-typed URL, or a bridge that fired before
    // anything was framed. Say so rather than opening an empty FRAME mode.
    toastRef.current({ level: "info", title: FRAME_PARAM_NOTHING });
  }, [frameParam, clearFrameParam, resumeFrame]);

  // ---- the plan summary the primary CTA prints ----------------------------
  const wheel = resolveWheel(status?.filterwheel?.names, status?.filterwheel?.opaque);
  const chosenFilters = wheel.filters.filter((f) => quick.on[f] !== false);
  const planSummary = useMemo(() => {
    const hours = quick.hours;
    const finish = fmtClock(finishesAt(Date.now(), hours));
    const middle = chosenFilters.length > 0 ? `${chosenFilters.length} filters` : "one-shot colour";
    return `${hours}h · ${middle} · finishes ${finish}`;
    // `chosenFilters.length` and not the array: a new array every render would
    // recompute the clock every render and make the label tick.
  }, [quick.hours, chosenFilters.length]);

  // ---- actions ------------------------------------------------------------
  const goQuick = (t: SkyTarget) => nav.sheet("quick", { target: t.id });
  const goVideo = (t: SkyTarget) =>
    nav.go(
      `/rig/capture?mode=video&target=${encodeURIComponent(t.name)}` +
      `&ra=${t.ra_hours}&dec=${t.dec_deg}`,
    );

  const pressPrimary = (cta: LockCta) => {
    if (!lock) return;
    const t = ctaToast(cta.kind);
    if (t) enqueueToast({ level: t.level, title: t.title });
    switch (cta.kind) {
      case "connect":
        nav.go("/rig/devices");
        return;
      case "video":
        goVideo(lock);
        return;
      case "obstructed":
        return;
      default:
        goQuick(lock);
    }
  };

  const pressSingle = () => {
    if (!lock) return;
    nav.go(
      `/rig/capture?target=${encodeURIComponent(lock.name)}` +
      `&ra=${lock.ra_hours}&dec=${lock.dec_deg}`,
    );
  };

  const pressPlan = () => {
    if (!lock) return;
    if (pool.length >= 2 && pool.includes(lock.id)) {
      nav.sheet("quick", { pool: pool.join(",") });
      return;
    }
    const next = pool.includes(lock.id) ? pool.filter((id) => id !== lock.id) : [...pool, lock.id];
    setPool(next);
    skyPrefs.setPool(next);
  };

  const pressPatch = (patch: PatchModel) =>
    nav.sheet("quick", {
      ra: String(patch.ra_hours),
      dec: String(patch.dec_deg),
      name: `${patch.raStr} ${patch.decStr}`,
    });

  /**
   * COORDINATES, with the reticle's aim in the hash (review #36).
   *
   * The sheet's USE FINDER reads `params.az`/`params.alt` - the same convention
   * the horizon editor uses (A.15) - and the ONE call site that opens it was
   * passing neither, so the button was honest-locked on every phone, forever,
   * with a comment in the sheet saying the wiring was somebody else's task.
   *
   * The route is the only channel available: a sheet is route state, not a child
   * of the screen underneath it, and the reticle's az/alt lives inside the
   * finder's own model and is deliberately not persisted (plan F).
   */
  const openCoords = () =>
    nav.sheet("coords", { az: model.az.toFixed(3), alt: model.alt.toFixed(3) });

  const setLens = (kind: SkyKind, on: boolean) => model.setLens(kind, on);
  const learnKind = (kind: SkyKind, text: string) =>
    enqueueToast({ level: "info", title: KIND_LABEL[kind], detail: text });

  const openLayers = () => {
    layersAnchor.current = (wrapRef.current?.querySelector("[data-sky-layers]") as HTMLElement | null) ?? null;
    setLayersOpen(true);
  };

  // ---- gate reasons -------------------------------------------------------
  // The CONNECT case is deliberately LIVE: pressing it is how you get a rig, so
  // locking it behind "connect a camera first" would be the app refusing to help
  // with the only thing wrong with it.
  const ctaConnect = !equipConnected;
  const primaryReason = ctaConnect
    ? null
    : capture.lockedReason ?? (lock?.obstructed ? obstructedReason(lock.name, model.siteName) : null);
  const singleReason = capture.lockedReason;

  // MAP is the only mode a desktop has: no camera worth pointing at the sky and
  // no orientation sensor, so the toggle is hidden rather than offered and
  // refused (plan E).
  const arPossible = model.secureContext && (model.cameraSupported || model.gyroSupported);
  const showModeToggle = bp !== "desktop" || arPossible;
  const modeReason = model.secureContext ? null : SECURE_REASON;
  const gyroReason = !model.secureContext
    ? SECURE_REASON
    : model.gyroSupported
      ? null
      : NO_GYRO_REASON;

  const framedForLock = frame.set && !frame.on && lock != null && frame.id === lock.id && framing != null
    ? framedStrip(framing.mosaic.cols, framing.mosaic.rows, framing.rotation_deg)
    : null;

  // The same strip for a free-roam framing, on the card that owns that patch.
  // Without it, FRAME HERE > DONE changes nothing visible and reads as a control
  // that did nothing - and the framing is still there, silently, feeding the
  // next quick session.
  const framedForPatch = frame.set && !frame.on && lock == null && frame.id === null
    && framing != null && model.patch != null
    && framing.freeroamId === patchId(model.patch)
    ? framedStrip(framing.mosaic.cols, framing.mosaic.rows, framing.rotation_deg)
    : null;

  const framedTargetMarker = frame.set && !frame.on && framing != null
    ? model.markers.find((m) => m.id === frame.id) ?? null
    : null;

  return (
    <div data-testid="hub-sky" style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      {!equipConnected && <BrowseBanner />}

      <StatusRow
        reachCount={model.reachCount}
        clearPct={model.clearPct}
        siteName={model.siteName}
        onDome={onDome}
      />

      <div
        ref={wrapRef}
        style={{ position: "relative", minWidth: 0 }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {frame.on && framing ? (
          <FrameHost
            framing={framing}
            optics={mergedOptics}
            night={night}
            mode={frameMode}
            imageBrightness={surveyBright}
            surveyDegraded={surveyDegraded}
            degradedText={surveyDegradedText(onlineFetch, pack)}
            onlineFetch={onlineFetch}
            mount={status?.mount ?? null}
            rotator={status?.rotator ?? null}
            pointingWhere={status?.mount ? `${status.mount.ra_str} ${status.mount.dec_str}` : null}
            skyRows={region.rows}
            region={{ degraded: region.degraded, truncated: region.truncated, error: region.error }}
            selectedObjectId={frameTarget}
            catalogTarget={framing.target}
            onPickObject={(row: SkyRow | null) => {
              if (row) model.setView({ trackId: row.id });
            }}
            onCenterChange={(ra, dec) => setFraming({ center: { ra_hours: ra, dec_deg: dec } })}
            onRotate={(deg) => setFraming({ rotation_deg: deg })}
            onZoom={(f) => setFraming({ fovZoomDeg: f })}
            onSurveyError={() => setSurveyDegraded(true)}
            onSurveyLoad={() => setSurveyDegraded(false)}
          />
        ) : (
          <>
            <SkyView
              model={model}
              boxPx={boxPx}
              frameOn={false}
              onOpenLens={() => setLensOpen(true)}
              onOpenLayers={openLayers}
            />
            {framedTargetMarker && framing && (
              <FramedOverlay
                boxW={model.boxW}
                boxH={model.boxH}
                frameW={model.reticle.w}
                frameH={model.reticle.h}
                cols={framing.mosaic.cols}
                rows={framing.mosaic.rows}
                overlap={framing.mosaic.overlap}
                rotationDeg={framing.rotation_deg}
                cx={framedTargetMarker.x}
                cy={framedTargetMarker.y}
                label={`${framedTargetMarker.name} · ${frameText(framing.mosaic.cols, framing.mosaic.rows, framing.rotation_deg)}`}
              />
            )}
          </>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        {showModeToggle && (
          <IconButton48
            glyph={<SkyGlyph name={model.mode === "cam" ? "arcamera" : "map"} />}
            label={model.mode === "cam" ? "AR CAMERA" : "MAP"}
            active={model.mode === "cam"}
            lockedReason={model.mode === "map" ? modeReason : null}
            onExplain={onExplain}
            onPress={() => model.setMode(model.mode === "cam" ? "map" : "cam")}
            className="nx-sky-tool"
            data-testid="sky-mode"
          />
        )}
        <IconButton48
          glyph={<SkyGlyph name="frame" />}
          label={frameLabel}
          active={frame.on || frame.set}
          lockedReason={frameReason}
          onExplain={onExplain}
          onPress={onFramePress}
          className="nx-sky-tool"
          data-testid="sky-frame"
        />
        <IconButton48
          glyph={<SkyGlyph name="gyro" />}
          label="GYRO"
          active={model.gyro}
          lockedReason={gyroReason}
          onExplain={onExplain}
          onPress={() => model.toggleGyro()}
          className="nx-sky-tool"
          data-testid="sky-gyro"
        />
      </div>

      {(model.cameraError || model.gyroError) && (
        <Card tone="dashed" data-testid="sky-sensor-note">
          <div style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5 }}>
            {model.cameraError ?? model.gyroError}
            {!model.secureContext && (
              <>
                {" "}
                <button
                  type="button"
                  onClick={() => nav.go("/settings/general/connection")}
                  style={{
                    border: 0, background: "transparent", color: "var(--accent)",
                    fontFamily: "'Chakra Petch', system-ui, sans-serif", fontWeight: 600,
                    fontSize: 10, letterSpacing: ".12em", cursor: "pointer", padding: 0,
                  }}
                >
                  CONNECTION ›
                </button>
              </>
            )}
          </div>
        </Card>
      )}

      {!model.reticle.haveOptics && model.reticle.missingNote && (
        <Card tone="dashed" data-testid="sky-optics-note">
          <div style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5 }}>
            {model.reticle.missingNote}{" "}
            <button
              type="button"
              onClick={() => nav.go("/settings/general/optics")}
              style={{
                border: 0, background: "transparent", color: "var(--accent)",
                fontFamily: "'Chakra Petch', system-ui, sans-serif", fontWeight: 600,
                fontSize: 10, letterSpacing: ".12em", cursor: "pointer", padding: 0,
              }}
            >
              OPTICS ›
            </button>
          </div>
        </Card>
      )}

      {frame.on && framing && (
        <>
          <FrameTools
            fovZoomDeg={framing.fovZoomDeg}
            fitReason={framing.target ? null : NO_OBJECT_REASON}
            opticsReason={oneFrameDeg > 0 ? null : NO_OPTICS_REASON}
            hasTarget={framing.target != null}
            surveyAnchorRef={surveyAnchor}
            onZoom={setZoom}
            onFit={fitObject}
            onMatchCamera={matchCamera}
            onRecentre={recentre}
            onSurvey={() => setSurveyOpen(true)}
            onExplain={onExplain}
          />

          <FramingCard
            targetName={framing.target?.name ?? frame.id ?? "this patch"}
            cols={framing.mosaic.cols}
            rows={framing.mosaic.rows}
            rotationDeg={framing.rotation_deg}
            fovXDeg={fov.fov_x_deg}
            fovYDeg={fov.fov_y_deg}
            overlap={framing.mosaic.overlap}
            rotator={status?.rotator ?? null}
            rotatorRange={{
              range_type: config?.rotator?.range_type ?? "full",
              range_start_deg: config?.rotator?.range_start_deg ?? 0,
            }}
            onMosaic={(cols, rows) => setFraming({ mosaic: { rows, cols, overlap: framing.mosaic.overlap } })}
            onRotate={(deg) => setFraming({ rotation_deg: deg })}
          />

          {/* What tonight looks like across the WHOLE mosaic, not just its
              centre - the panel row that never clears the horizon is invisible
              on a chart drawn for one point (review #33). */}
          <MosaicNightCard
            raHours={framing.center.ra_hours}
            decDeg={framing.center.dec_deg}
            rows={framing.mosaic.rows}
            cols={framing.mosaic.cols}
            overlap={framing.mosaic.overlap}
            rotationDeg={framing.rotation_deg}
            fovXDeg={fov.fov_x_deg}
            fovYDeg={fov.fov_y_deg}
            altLimitDeg={site?.horizon_min_deg ?? 0}
          />
        </>
      )}

      {lock ? (
        <LockCard
          lock={lock}
          equipConnected={equipConnected}
          planSummary={planSummary}
          framed={framedForLock}
          onAdjustFrame={() => enterFrame(lock, true)}
          onClearFrame={clearFrame}
          onPrimary={pressPrimary}
          onInfo={() => nav.sheet("brief", { id: lock.id })}
          onSingleFrame={pressSingle}
          onPlan={pressPlan}
          inPool={pool.includes(lock.id)}
          poolCount={pool.length}
          primaryReason={primaryReason}
          singleReason={singleReason}
          onExplain={onExplain}
        />
      ) : (
        <PatchCard
          patch={model.patch}
          reachCount={model.reachCount}
          targetCount={model.targets.length}
          onImagePatch={pressPatch}
          onFramePatch={enterFreeRoam}
          framed={framedForPatch}
          onAdjustFrame={() => { if (!resumeFrame() && model.patch) enterFreeRoam(model.patch); }}
          onClearFrame={clearFrame}
          onCoords={openCoords}
          imageReason={capture.lockedReason}
          onExplain={onExplain}
        />
      )}

      <ReachStrip
        reachList={model.reachList}
        onAim={(t) => model.setView({ az: t.azNow, alt: t.altNow, trackId: t.id })}
      />

      {/* LAST, and below the reach strip, which is where the design's stack
          ends. It is the only card on this screen that answers a question about
          somewhere OTHER than the reticle, and putting it above the lock card
          would push the three things you came here to press below the fold on a
          phone. */}
      <DomeCard
        canViewWeather={canViewWeather}
        pointing={pointing}
        target={lock ? { alt: lock.altNow, az: lock.azNow, name: lock.name } : null}
        horizon={horizonPoints}
        wind={domeWind}
        track={domeTrack}
        targetName={lock?.name ?? null}
        height={domeHeight}
        lockId={lock?.id ?? null}
        onExplain={onExplain}
      />

      {lensOpen && (
        <LensDial
          kinds={Object.keys(model.lens) as SkyKind[]}
          lens={model.lens}
          counts={model.kindCounts}
          icons={model.kindIcon}
          reachCount={model.reachCount}
          floorOnly={model.floorOnly}
          onToggle={setLens}
          onFloorOnly={(v) => model.setFloorOnly(v)}
          onLearn={learnKind}
          onTonight={() => { setLensOpen(false); nav.sheet("targets"); }}
          onClose={() => setLensOpen(false)}
        />
      )}

      <SurveyPopover
        open={surveyOpen}
        anchorRef={surveyAnchor}
        onClose={() => setSurveyOpen(false)}
        survey={framing?.survey ?? ""}
        mode={frameMode}
        brightness={surveyBright}
        onlineFetch={onlineFetch}
        onSurvey={(id) => {
          // A survey change is a fresh chance for the tiles: clearing the flag
          // here is `AtlasView.tsx:370`'s own rule, and without it a switch away
          // from an unreachable survey keeps showing the unreachable one's
          // banner over imagery that has just loaded.
          setSurveyDegraded(false);
          setFraming({ survey: id });
        }}
        onMode={setFrameMode}
        onBrightness={setSurveyBright}
        onExplain={onExplain}
      />

      <LayersPopover
        open={layersOpen}
        anchorRef={layersAnchor}
        onClose={() => setLayersOpen(false)}
        layers={model.layers}
        onToggle={(k: LayerKey, on) => model.setLayer(k, on)}
        note={model.layersNote}
        windNote={model.windNote}
        weatherReason={model.weatherAllowed ? null : "needs operator or admin access"}
        onExplain={onExplain}
      />
    </div>
  );
}
