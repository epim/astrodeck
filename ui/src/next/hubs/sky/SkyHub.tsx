// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// SkyHub.tsx - the app's home screen: point the phone at the sky, lock something
// in the reticle, start a night (hub-sky plan A.1, C, E).
//
// The stack, top to bottom, is the design's own and every row earns its place:
//
//   browse banner   only with no rig - and it names the ONE thing that needs one
//   status row      how many targets are in reach, how clear it is, which site
//   finder          the whole pannable sky in ATLAS, the schematic reticle in
//                   MAP, or the survey imagery once FRAME is on
//   toolbar         ATLAS, MAP / AR CAMERA, FRAME / DONE / ADJUST, GYRO
//   framing card    FRAME mode only
//   lock card       what is in the reticle, and the three things to do with it
//   reach strip     everything else that is clear and up (not in ATLAS)
//   dome card       the cloud between here and there, on the hemisphere (D-SKY-2)
//
// ATLAS IS THE DEFAULT, on every device (`finder/prefs.ts DEFAULT_MODE`). What
// the user opens the app to see is the sky with tonight's targets on it, not a
// reticle already aimed at one of them - "the atlas view that's populated by
// the targets", and the schematic finder one press away for when there is
// something to aim at. A device that presses MAP is remembered; the bridge's
// `?mode=atlas` still works and is still consumed on arrival.
//
// So ATLAS mounts `atlas/AtlasHost` in place of the box, and the same ranked
// list MAP draws is drawn ON it as the design's label pills, with the finder's
// aim as the reticle. That is why the lock card is on BOTH modes now: there is
// a reticle on screen in both, and the card answers about it. The FRAME cluster
// and the reach strip are still MAP's alone - the first is controls for a
// rectangle that is not on screen, the second is a list of things that ARE on
// screen in the atlas. See the ATLAS block below.
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
import { usePlanning } from "../../lib/planning";
import {
  SkyView,
  skyPrefs,
  useSkyModel,
  type PatchModel,
  type SkyKind,
  type SkyTarget,
} from "./finder";
import { KIND_LABEL, displayName, fullName, kindOf, paletteFor } from "./finder";
import type { Placement } from "./finder/model";
import {
  LOCK_ROW_KEYS, lockRowFromParams,
  type CatalogRowLike, type LockTarget,
} from "./finder/targets";
import { windSummary } from "../weather/dome/domeOverlay";
import { altAzOf } from "../../../lib/altaz";
import { BrowseBanner } from "./cards/BrowseBanner";
import { StatusRow } from "./cards/StatusRow";
import { DomeCard, DOME_CARD_ID } from "./cards/DomeCard";
import { LensDial } from "./cards/LensDial";
import { LayersPopover, type LayerKey } from "./cards/LayersPopover";
import { LockCard, lockCardCta, type LockCardCta } from "./cards/LockCard";
import { PatchCard } from "./cards/PatchCard";
import { ReachStrip } from "./cards/ReachStrip";
import { SkyGlyph } from "./cards/glyphs";
import { obstructedReason, ctaToast } from "./cards/lockCta";
import { AtlasHost } from "./atlas/AtlasHost";
import { FrameHost } from "./frame/FrameHost";
import { FramingCard } from "./frame/FramingCard";
import { FramedOverlay } from "./frame/FramedOverlay";
import { FrameTools, NO_OBJECT_REASON, NO_OPTICS_REASON } from "./frame/FrameTools";
import { SurveyPopover } from "./frame/SurveyPopover";
import { MosaicNightCard } from "./frame/MosaicNightCard";
import { PACK_POLL_MS, shouldPollPack, surveyDegradedText } from "./frame/degraded";
import {
  ZOOM_MAX, clampZoom, fitObjectZoom, frameFovDeg, matchCameraZoom, pinchZoom, pointerDist,
} from "./frame/zoom";
import { fetchPanels, framedStrip, framingPrefill, frameText } from "./frame/mosaic";
import { openFlowWizard } from "../session/flows/wizard";
import { effectiveOptics } from "../../../lib/effective";
import { believedPointing, believedRaDec } from "../../../lib/slewController";
import { DEFAULT_OVERLAP, fovFromOptics, type OpticsLike } from "../../../lib/framing";
import { useSkyRegion, type SkyRow } from "../../../lib/skyRegion";
import { resolveRoleConnected, useCapability } from "../../../lib/caps";
import {
  OSC_LABEL, finishLabel, hoursLabel, oscCount, resolveQuickHours, wheelModel,
} from "./sheets/quickModel";
import { hoursToDawn } from "./sheets/quickNightArc";
import { FRAMING_REMOVED, framingKeptDetail } from "./sheets/quickCopy";
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
 * argument, had no door. Now the reticle's own patch is enough, and with
 * coordinates to place it this is the only remaining refusal: a reticle aimed
 * at ground, where below the horizon there is no RA and Dec to frame.
 *
 * WITH NO COORDINATES AT ALL this sentence is wrong (#568): a default site's
 * placeholder, or a role the precise location is hidden from, still has a
 * reticle that can read above the horizon, and the fix is not "aim up" but
 * "save a site" (or nothing an operator can do, for the hidden-role case).
 * `frameReason` below reaches for `model.placementNote` first, and only falls
 * back to this sentence once there ARE coordinates to judge the aim against.
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

/** Why SINGLE FRAME refuses a satellite. The number is the one that decides it:
 *  a low-orbit pass moves about four degrees of sky per second, so a sub of any
 *  useful length is a streak across the frame rather than a picture of it. */
export const SAT_NO_SINGLE =
  "A pass crosses about four degrees of sky a second, so one sub is a streak - "
  + "open the pass list for when and where to look.";

/** Why + PLAN refuses a satellite. Tonight's pool is a list of deep-sky targets
 *  a flow shoots in turn; a pass is a five-minute event on a fixed clock and
 *  there is no slot in the plan that means "be outside at 21:04". */
export const SAT_NO_PLAN =
  "Tonight's plan queues deep-sky targets for a flow. A pass lasts minutes at a "
  + "fixed time, so it belongs in the pass list, not the pool.";

/** What a `#/sky?lock=<id>` deep link says when the ranking settles without the
 *  object it names. It prints the id the link asked for, because that is the
 *  only thing the user (or the support call reading the URL out) can act on. */
export const lockNotListed = (id: string): string => `${id} is not in tonight's list`;

/**
 * What the same deep link says when there IS no tonight's list (#466).
 *
 * `GET /api/catalog/tonight` answers 409 with no site saved, and a role
 * without `view.site_derived` is never ranked at all, so "not in tonight's
 * list" would blame a list that does not exist for leaving the object out.
 * The toast's detail is the model's own sentence for why the list is empty
 * (`rankingError`), verbatim, because it carries the fix.
 */
export const lockNoList = (id: string): string =>
  `${id} cannot be found without tonight's list`;

/**
 * What LOCK IN FINDER says when it locks the framed object with no tonight's
 * list to find it in (#466). The detail is `rankingError`, as above: the
 * object is locked and the reason the list is empty is said, instead of the
 * object being refused for it.
 */
export const lockHeld = (name: string): string =>
  `${name} is locked from the catalogue - there is no tonight's list to find it in`;

/** The status chip on a lock held from the catalogue. Nothing on that card
 *  was ranked: there is no transit and no cloud reading for an object the
 *  ranking never saw, and the chip says so rather than CLEAR. */
export const HELD_STATUS = "NOT RANKED";

/**
 * Why the primary is locked on a lock this ROLE cannot place (#504): a site is
 * saved and its coordinates are withheld from this principal, so the finder
 * has no altitude for the object and no horizon to judge it against. The card
 * reads POSITION HIDDEN FOR THIS ROLE; this is what a press or a hold says.
 */
export const unplacedReason = (name: string): string =>
  `${name} cannot be placed for this role, so nothing here can say whether it is up.`;

/** How close the finder's view must still be to where a hold aimed it for the
 *  hold to stand. An untouched view equals the aim; the smallest gesture moves
 *  it by a pixel of sky, which is 1/ppd degrees and ppd is at most 6.1 x 720 /
 *  370 = 11.9 px per degree (`ppdFor` at the widest box), so 0.084 degrees. A
 *  hundredth of a degree is an eighth of that, and far above float noise and
 *  the six decimals a region request rounds a centre to. */
const HELD_VIEW_EPS_DEG = 0.01;

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
export function entryOf(t: LockTarget): CatalogEntry {
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
 * The lock card's target for a catalogue object HELD when tonight's list
 * cannot carry it (#466, #504) - `entryOf` run backwards. The row is the one
 * LOCK IN FINDER framed or a targets-sheet search pick carried in the hash.
 *
 * `at` is the MODEL's placement (`SkyModel.place`): the object put on this sky
 * exactly as a row with its RA/Dec would be - its alt/az, whether the finder's
 * horizon rule hides it, and its window off the same walk to dawn - so the
 * card, the reticle and the dome agree about where it is and what is in the
 * way. Everything only the RANKING adds is absent rather than invented: no
 * cloud reading (null, which is not 0%), no transit and a zero score, under a
 * status chip that says the object was not ranked.
 *
 * WITH NO PLACEMENT (no site saved, or a role the coordinates are withheld
 * from, #503) the lock is an `UnplacedTarget`: no altitude, no window, and an
 * obstruction that is UNKNOWN, not false. Each was a placeholder once - the
 * window a `0` that printed "window 0m" (#508), the verdict a `false` that the
 * primary read as clear and offered IMAGE M31 for an object 44 degrees under
 * the horizon of a placeholder site - and a placeholder in a field a consumer
 * renders or gates on is a claim nothing keeps.
 */
export function heldTarget(
  row: CatalogRowLike,
  at: Placement | null,
  wheel: Parameters<typeof paletteFor>[2],
): LockTarget {
  const type = (row.type ?? "").trim();
  const kind = kindOf(row) ?? "nebula";
  // The finder's own two row-shape rules, so a held body is titled by its label
  // and not by its describe sentence, exactly as the same row is in the list.
  const full = fullName(row);
  const size = row.size_arcmin;
  const common = {
    id: row.id,
    name: displayName(row),
    full: full !== "" ? full : type,
    kind,
    ra_hours: row.ra_hours,
    dec_deg: row.dec_deg,
    cloudPct: null,
    clouded: false,
    color: "color-mix(in srgb, var(--text) 70%, transparent)",
    statusTxt: HELD_STATUS,
    palette: paletteFor(kind, type, wheel),
    // 0 is the catalogue's "no extent", and absent is the finder's.
    sizeArcmin: typeof size === "number" && size > 0 ? size : undefined,
    transitLabel: "-",
    score: 0,
    moonSepDeg: null,
  };
  if (at == null) {
    return { ...common, altNow: null, azNow: null, obstructed: null, windowMinutes: null };
  }
  return {
    ...common,
    altNow: at.altDeg,
    azNow: at.azDeg,
    obstructed: at.obstructed,
    windowMinutes: at.windowMinutes,
  };
}

/**
 * A free-roam framing's identity: the patch it was framed at, spelled exactly
 * as the quick sheet is opened with it (`pressPatch`'s `name`).
 *
 * It is what `framingMatches` compares against on the quick sheet, so a
 * patch's own mosaic is the one that sheet offers to Send to Flow Wizard and
 * another patch's is not. (It was also the Plan's `mosaic_group` for the
 * side channel S6 retired, #196.)
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
  /**
   * A DEFAULT SITE IS NO SITE (#466, #503, the class of #24 and #121 in the UI).
   *
   * `trackLat`/`trackLon` are for a CLAIM about the operator's sky: is this
   * centre above the horizon, may FRAME go. A fresh config's site is
   * `is_default: true` with placeholder coordinates, and reading them as a site
   * refused FRAME for a horizon at a place that is not the operator's, at some
   * hours of the day and not others. So they are null unless a site was saved,
   * which is what every consumer below already does with no site.
   *
   * There used to be a second pair, `placeLat`/`placeLon`: where the FINDER
   * placed the sky, which was the placeholder too, because `useSkyModel`
   * placed every row at whatever numbers the site block carried. The model no
   * longer places anything at the placeholder (#503), so the pair is retired:
   * whatever this file has to put on the finder's sky - a held lock, the aim of
   * a tap on the atlas - goes through `model.place`, which answers from the
   * model's own coordinates and answers null where it has none.
   */
  const siteSaved = site != null && site.is_default !== true;
  const trackLat = siteSaved && typeof site?.latitude === "number" ? site.latitude : null;
  const trackLon = siteSaved && typeof site?.longitude === "number" ? site.longitude : null;
  const [lensOpen, setLensOpen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [surveyOpen, setSurveyOpen] = useState(false);
  const surveyAnchor = useRef<HTMLElement | null>(null);
  const [frame, setFrame] = useState<FrameState>({ on: false, set: false, id: null });
  // THE POOL AND THE QUICK DEFAULTS ARE THE RIG'S, not this phone's (D-FU-1).
  // `usePlanning` holds one `GET /api/planning` for the whole app and falls
  // back to the browser keys on an engine that does not carry the block, so
  // this screen reads the same shortlist a tablet would.
  const { pool, putPool, quick } = usePlanning();
  // Both of these are PERSISTED CHOICES with a control that writes them again
  // (`SurveyPopover`). They were read-only `useState` seeds with no setter, so
  // `prefs.setFrameMode` and `prefs.setSurveyBright` were exported and never
  // called and the "explicit choice" FrameHost's header describes was whatever
  // an older build had left in localStorage (review #31).
  const [frameMode, setFrameModeState] = useState<"survey" | "schematic">(() => skyPrefs.getFrameMode());
  const [surveyBright, setSurveyBrightState] = useState<number>(() => skyPrefs.getSurveyBright());
  const [surveyDegraded, setSurveyDegraded] = useState(false);
  const [pack, setPack] = useState<PackStatus | null>(null);
  // THE FOURTH OVERLAY, and it lives here rather than in `useSkyModel` because
  // the model's three (cloud, horizon, wind) are things the FINDER draws and
  // this one is a thing the ATLAS fetches. It shares their storage key - it is
  // the same question, under the same stack icon - and `prefs.setLayers` /
  // `prefs.setSurveyLayer` each write only their own fields so the two owners
  // cannot overwrite each other. Per device, never the rig's: which pictures
  // this phone downloads is not a fact about the telescope.
  const [surveyLayer, setSurveyLayerState] = useState<boolean>(() => skyPrefs.getSurveyLayer());

  const setFrameMode = useCallback((m: "survey" | "schematic") => {
    setFrameModeState(m);
    skyPrefs.setFrameMode(m);
  }, []);
  const setSurveyBright = useCallback((v: number) => {
    setSurveyBrightState(v);
    skyPrefs.setSurveyBright(v);
  }, []);
  const setSurveyLayer = useCallback((on: boolean) => {
    setSurveyLayerState(on);
    skyPrefs.setSurveyLayer(on);
    // A layer coming back on is a fresh chance for the tiles, exactly as a
    // survey change is (`SurveyPopover`'s `onSurvey` clears it for the same
    // reason): without this, the banner from the last time the imagery failed
    // outlives the state it described.
    if (on) setSurveyDegraded(false);
  }, []);

  // ---- `#/sky?lock=<id>`, the way every other screen aims this one ---------
  //
  // The targets sheet's rows and the catalog search's picks both aim by
  // navigating to `#/sky?lock=<id>` (sheets/targets.tsx `aim()`, and `aimRow()`
  // for a search pick, which carries the catalogue row beside the id - #504):
  // a sheet is route state, not a child of the screen underneath it, so the id
  // cannot be handed over as a prop and travels in the hash instead - which
  // also makes the choice a deep link support can read out over the phone.
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
    // The row a search pick carried goes WITH the id (#504): left behind, it
    // would sit in the URL describing a lock that has already been taken, and
    // hand the next bare `?lock=` a row that is not its own.
    for (const k of LOCK_ROW_KEYS) delete params[k];
    nav.replace(buildHash({ hub: r.hub, sub: r.sub, sheets: r.sheets, params }));
  }, []);

  const targets = model.targets;
  const aimReady = model.aimReady;
  const rankingError = model.rankingError;
  const rankingErrorRef = useRef(rankingError);
  rankingErrorRef.current = rankingError;
  const placeRef = useRef(model.place);
  placeRef.current = model.place;

  /**
   * A LOCK HELD FROM THE CATALOGUE (#466, #504).
   *
   * The deep link aims at an id in the merged ranking, and tonight's list is
   * that ranking's main source. With no site saved `GET /api/catalog/tonight`
   * answers 409, so an object the operator searched for and framed in the atlas
   * was in no list the finder had, and LOCK IN FINDER refused it ("M31 is not in
   * tonight's list") - after which the finder's FRAME framed the reach list's
   * first object instead, under a card titled with that object's name.
   *
   * So when the list cannot be computed and the link brings a catalogue ROW for
   * its id, the hub holds that row as the lock: the finder follows its id (and
   * is aimed at it, where there is a placement to aim at), the lock card and
   * FRAME name it, and the toast says why the list is empty instead of refusing
   * the object. Two doors bring a row: LOCK IN FINDER, whose row is the framing
   * session's target, and a targets-sheet search pick, whose row travels in the
   * hash (`finder/targets.ts lockRowParams`). `view` is where the finder's aim
   * landed, captured on the render after the aim (the model clamps an aim below
   * the horizon, and the hold compares against what it stored, not what it
   * asked for).
   */
  const [held, setHeld] = useState<{ entry: CatalogRowLike; view: { az: number; alt: number } | null } | null>(null);

  useEffect(() => {
    if (lockParam == null || lockParam === "") {
      handledLockRef.current = null;
      return;
    }
    if (handledLockRef.current === lockParam) return;
    const t = targets.find((x) => x.id === lockParam);
    if (t) {
      // A lock the ranking carries supersedes any hold.
      setHeld(null);
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
    // NO LIST TO FIND IT IN, and the link brought the object's row: the one a
    // search pick carried in the hash, or else the framing session's (LOCK IN
    // FINDER hands over `framing.target`). Hold it. Only then - with a list
    // that simply does not carry the id, the refusal below is still the honest
    // answer, row or no row.
    //
    // WITH OR WITHOUT A PLACEMENT (#504). A role that sees no coordinates, and
    // since #503 a default site, has no altitude for the object, and the hold
    // used to require one - so both fell through to the refusal. The finder is
    // then not aimed (there is nowhere on its sky to aim at), but it still
    // tracks the id, which is what keeps the hold standing, and the card names
    // the object with no altitude on it rather than refusing it.
    const carried = lockRowFromParams(routeRef.current.params);
    const framed = useStore.getState().framing?.target;
    const row: CatalogRowLike | null = carried != null && carried.id === lockParam
      ? carried
      : framed != null && framed.id === lockParam ? framed : null;
    if (rankingError != null && row != null) {
      handledLockRef.current = lockParam;
      const at = placeRef.current(row.ra_hours, row.dec_deg);
      setViewRef.current(at != null
        ? { az: at.azDeg, alt: at.altDeg, trackId: row.id }
        : { trackId: row.id });
      setHeld({ entry: row, view: null });
      toastRef.current({ level: "info", title: lockHeld(displayName(row)), detail: rankingError });
      clearLockParam();
      return;
    }
    // Not there YET is the normal case for the first beat after a mount, so the
    // refusal is on a timer. When it fires, the ranking has settled without it:
    // say so once, and clear the param rather than leaving a link that will try
    // again on every render. With no list at all it says that, and why.
    const timer = setTimeout(() => {
      if (handledLockRef.current === lockParam) return;
      handledLockRef.current = lockParam;
      const why = rankingErrorRef.current;
      toastRef.current(why != null
        ? { level: "warning", title: lockNoList(lockParam), detail: why }
        : { level: "warning", title: lockNotListed(lockParam) });
      clearLockParam();
    }, LOCK_WAIT_MS);
    return () => clearTimeout(timer);
  }, [lockParam, targets, aimReady, clearLockParam, rankingError]);

  // Where the hold's aim landed, read on the render after it (see above).
  const modelAz = model.az;
  const modelAlt = model.alt;
  useEffect(() => {
    if (held && held.view == null) setHeld({ ...held, view: { az: modelAz, alt: modelAlt } });
  }, [held, modelAz, modelAlt]);

  /**
   * The hold stands while the finder is still where it put it, following the
   * held id. A marker tap, a reach chip, a deep link or an aim-anywhere tap
   * changes the tracked id; a drag moves the view. Either is the operator
   * choosing something else, and the reticle's own lock takes over.
   *
   * While it stands it WINS over the reticle: with no site the aim can be
   * clamped at the horizon's edge, and whatever catalogue row happens to sit
   * under the reticle there is not the object the operator locked. The model's
   * own row for the held id is used when it has one, since its numbers are the
   * ranking's; otherwise the catalogue row, placed where the finder places it.
   */
  const heldActive = held != null && model.trackId === held.entry.id
    && (held.view == null
      || (Math.abs(modelAz - held.view.az) < HELD_VIEW_EPS_DEG
        && Math.abs(modelAlt - held.view.alt) < HELD_VIEW_EPS_DEG));
  const wheelForHold = status?.filterwheel ?? null;
  const modelPlace = model.place;
  const heldLock: LockTarget | null = useMemo(() => {
    if (!heldActive || held == null) return null;
    const own = targets.find((x) => x.id === held.entry.id);
    if (own) return own;
    // THE MODEL PLACES IT, as it places every row it has no server alt/az for:
    // the same altitude, the same horizon rule (`trackCtx`: the drawn line and
    // the flat floor only while the horizon layer is on) and the same walk to
    // dawn for its window (#508). The fallback runs whenever the region round
    // the aim does not carry the object, and an object far below the horizon
    // is the usual such case: its aim is clamped at -12 and the region is asked
    // about the sky above it (32.5 degrees above M31 at -44.5).
    //
    // With no coordinates - a default site (#503) or a role they are withheld
    // from - `place` answers null and the lock is UNPLACED: no altitude, no
    // window and no verdict, because "behind the horizon" and "up" are both
    // claims about a sky nobody can compute here.
    return heldTarget(held.entry, modelPlace(held.entry.ra_hours, held.entry.dec_deg), wheelForHold);
  }, [heldActive, held, targets, modelPlace, wheelForHold]);

  const lock: LockTarget | null = heldLock ?? model.lock;
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

  // `believedPointing` is null while the mount does not know where it points:
  // it then reports its HOME position, the pole, and a marker drawn there is a
  // claim about the tube that nothing backs (#791, #144).
  const believed = believedPointing(status?.mount);
  const pointing = believed && believed.alt >= 0 ? believed : null;

  // The same gate for the atlas and frame footprint and its caption, which draw
  // the mount's RA/Dec (#913): the mount block is handed down only while the
  // mount knows where it points, and the caption's position text goes with it.
  const raDec = believedRaDec(status?.mount);
  const footprintMount = raDec ? status?.mount ?? null : null;
  const footprintWhere = raDec ? `${raDec.ra_str} ${raDec.dec_str}` : null;

  /**
   * THE DOME'S ARCS ARE THE MODEL'S, not this file's.
   *
   * This hub used to re-walk the lock's path to dawn itself - 29 steps of
   * trigonometry over the model's own inputs, written here because the finder's
   * own `model.track` is already PROJECTED into the flat sky box and cannot be
   * put on a hemisphere. `useSkyModel.dome.tracks` is that walk, published: the
   * lock - or, with nothing catalogued under the reticle, the reticle's own
   * patch - then the best of the ranked list behind it, capped at six, each
   * carrying the label the dome prints. One walker, one horizon mask, one set
   * of inputs, so the arc on the dome and the arc on the finder cannot disagree
   * about where an object goes, which is the whole reason the walk moved.
   */
  const horizonPoints = model.horizonPoints;
  const modelNowMs = model.nowMs;

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
  // The catalogue for the patch the CANVAS is showing - so it runs for ATLAS
  // as well as FRAME, and for neither while the schematic finder is up (the
  // finder has its own markers from the model).
  const canvasOn = (frame.on || model.mode === "atlas") && framing != null;
  const region = useSkyRegion(
    framing?.center ?? { ra_hours: 0, dec_deg: 0 },
    framing?.fovZoomDeg ?? 0,
    canvasOn,
  );

  // The single-frame field of view, which is what FIT OBJECT falls back to and
  // what MATCH CAMERA zooms to. Zero means the rig's optics are unknown, and
  // both controls then say so instead of zooming to a guess.
  const oneFrameDeg = frameFovDeg(fov.fov_x_deg, fov.fov_y_deg);

  // The offline pack's state, polled ONLY while the survey is degraded with no
  // online source - the one state whose banner copy depends on it
  // (`AtlasView.tsx:234-244` does exactly this, and for the same reason).
  // ATLAS mode runs its OWN copy of this poll, scoped to `AtlasHost`'s mount,
  // so this one stands down there rather than the two of them asking the rig
  // the same question twice every two seconds.
  useEffect(() => {
    if (!frame.on || model.mode === "atlas" || !shouldPollPack(surveyDegraded, onlineFetch)) return;
    let live = true;
    const tick = (): void => {
      getPackStatus().then((p) => { if (live) setPack(p); }).catch(() => { /* the copy falls back */ });
    };
    tick();
    const id = setInterval(tick, PACK_POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, [frame.on, model.mode, surveyDegraded, onlineFetch]);

  const enterFrame = useCallback(
    (t: LockTarget, keep: boolean) => {
      if (!keep) {
        openFraming(entryOf(t));
        // A fresh session: one frame, no angle, no kept panels, at the ONE
        // overlap every framing starts from (`DEFAULT_OVERLAP`, spec 2.4). This
        // line used to re-set the store's 25% seed to 15%, the number the card's
        // copy printed, so a Sky framing reached the wizard at another overlap
        // than the same object framed in the Atlas.
        setFraming({ mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP }, rotation_deg: 0, panels: [] });
      }
      // A HELD LOCK STAYS HELD through FRAME (#466). Aiming with `trackId:
      // null` would end the hold the moment FRAME opened, and DONE would come
      // back to whatever sat under the reticle instead of the object framed. So
      // the held id stays tracked and the hold re-reads where this aim lands.
      const keepHold = held != null && held.entry.id === t.id;
      // An unplaced lock has nowhere on the finder's sky to aim at, so only the
      // tracked id is written and the view stays where it is (#503).
      const trackId = keepHold ? t.id : null;
      model.setView(t.altNow != null && t.azNow != null
        ? { az: t.azNow, alt: t.altNow, trackId }
        : { trackId });
      if (keepHold) setHeld({ entry: held.entry, view: null });
      setFrame({ on: true, set: keep, id: t.id });
    },
    [openFraming, setFraming, model, held],
  );

  /**
   * FREE-ROAM: survey imagery of a patch the catalogue is silent about
   * (review #29).
   *
   * `store.openFraming()` with no entry is the Atlas's own free-roam door and
   * has been all along - it seeds a session with no `target` and a stable
   * `freeroamId` for the session to be recognised by. The new UI simply never
   * opened it. The centre is then moved to the RETICLE's patch
   * rather than left on the mount, because the reticle is what the user is
   * looking at and the mount may be parked.
   */
  const enterFreeRoam = useCallback(
    (patch: PatchModel) => {
      openFraming();
      setFraming({
        center: { ra_hours: patch.ra_hours, dec_deg: patch.dec_deg },
        // THE ID IS THE PATCH, not the mount. `openFraming` derives its
        // `freeroamId` from wherever the mount happens to be pointing, which is
        // two things wrong at once: the id names coordinates the session is not
        // at, and two free-roam sessions started from one parked position share
        // it. This is the string the quick sheet is opened with (`pressPatch`'s
        // `name`), which is what lets `framingMatches` recognise a patch's own
        // framing there.
        freeroamId: patchId(patch),
        mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP },
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
    // The ENGINE's panel centres, kept on the framing session: the finder
    // draws the kept grid from them (`FramedOverlay`) and the quick sheet
    // counts them to tell a kept mosaic from a single frame. Nothing reaches a
    // night from here: the user has framed a target, not chosen one.
    setFraming({ panels });
    setFrame((s) => ({ ...s, on: false, set: true }));
    // WHAT THE TOAST MAY CLAIM. It used to name the quick sheet's GENERATE FLOW
    // and the Plan targets that queued (#275), a side channel S6 retired
    // (#196); it now names the way forward that exists, SEND TO FLOW WIZARD.
    // The words are `framingKeptDetail`'s, read with the other Sky mosaic
    // strings by `mosaicCopyPanelFirst.test.ts`.
    enqueueToast({
      level: "success",
      title: `Framing kept - ${frameText(cols, rows, f.rotation_deg)}.`,
      detail: framingKeptDetail(panels.length),
    });
  }, [fov.fov_x_deg, fov.fov_y_deg, setFraming, enqueueToast]);

  const clearFrame = useCallback(() => {
    setFraming({ mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP }, rotation_deg: 0, panels: [] });
    setFrame({ on: false, set: false, id: null });
    enqueueToast({ level: "info", title: FRAMING_REMOVED });
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

  // ---- ATLAS mode ---------------------------------------------------------
  //
  // The fourth button on the toolbar. It swaps the finder box for the classic
  // pannable survey canvas (`atlas/AtlasHost`) over the SAME framing session
  // FRAME uses - the Atlas and FRAME have always been two doors onto one
  // session (`frame/FrameHost`'s header says so), and ATLAS is the third.
  //
  // It is a MODE, not a route: `finder/prefs.ts SkyMode` carries `"atlas"`, so
  // the phone remembers which of the four this device last chose, exactly as
  // it already remembered AR CAMERA versus MAP - and `DEFAULT_MODE` makes it
  // the one a device that has never chosen opens in.
  const atlasOn = model.mode === "atlas";

  // Which finder mode ATLAS came from, so leaving it goes BACK rather than to
  // a mode the user never chose. Assigned during render (the same idiom
  // `routeRef` uses above) because it has to be current inside a press handler
  // that runs before any effect would have updated it.
  //
  // THE SEED MATTERS NOW THAT ATLAS IS THE DEFAULT. A phone that has never
  // pressed MAP has no stored finder half at all, and the hard-coded `"map"`
  // this used to start at was invisible while the atlas was only ever a choice
  // - it is the ONLY way out of the atlas now. `prefs.getFinderMode` answers
  // from the stored mode when it is one of the two; the fallback is this
  // device's own, which `prefs.ts` cannot compute because it knows nothing
  // about cameras or secure contexts.
  const deviceFinderMode: "cam" | "map" =
    model.secureContext && model.cameraSupported ? "cam" : "map";
  const finderModeRef = useRef<"cam" | "map" | null>(null);
  if (model.mode !== "atlas") finderModeRef.current = model.mode;
  else if (finderModeRef.current == null) {
    finderModeRef.current = skyPrefs.getFinderMode(deviceFinderMode);
  }
  const finderMode = finderModeRef.current ?? deviceFinderMode;

  // ATLAS always has a session to draw. `store.openFraming()` with NO entry is
  // the classic free-roam door (`AtlasView.tsx:570`) and seeds exactly what the
  // Atlas always seeded: the centre from the mount, the zoom from the optics,
  // the survey from night mode. This covers the two ways into ATLAS that are
  // not a button press - the persisted preference on a cold start, and the
  // `?mode=atlas` deep link.
  useEffect(() => {
    if (atlasOn && useStore.getState().framing == null) openFraming();
  }, [atlasOn, openFraming]);

  /**
   * AND THEN IT HAS SOMETHING TO SHOW, which is a different problem.
   *
   * `openFraming()` seeds the centre from the MOUNT, because that is what the
   * classic Atlas meant by free roam: you opened it while pointed somewhere.
   * As the opening screen of the app that is the wrong seed and it is wrong in
   * the worst way - a parked mount reads 0h +0, a rig with no mount at all
   * reports nothing, and either way the first thing a user sees is a correct
   * picture of a patch of sky with nothing in it and no hint that anywhere else
   * would be better.
   *
   * So: once the ranking has settled, the atlas opens on the finder's own lock
   * - tonight's best target, the same object the reticle auto-aims at - and
   * frames it, so the where-line names it, LOCK IN FINDER and FRAME both mean
   * it, and the lock card under the canvas is about the thing on the canvas.
   *
   * IT FOLLOWS UNTIL THE USER CLAIMS THE VIEW, which is not the same as firing
   * once. The finder keeps auto-aiming while nobody has touched it - the ranked
   * list arrives in two waves and the top target moves - so a one-shot seed
   * captured whichever object was on top at second 1.5 and then sat there while
   * the reticle walked away: measured on the probe rig, the atlas naming a
   * comet while the lock card underneath said NGC 3148, nine degrees apart.
   * Following costs nothing (a write only when the centre actually changes) and
   * ends the instant anything else claims the view - a pan, a zoom, a search
   * pick, a marker tap, an aim, or a framing session that arrived with an
   * object already on it. `atlasSeededRef` is that claim; it is cleared on the
   * way OUT, so the rule holds on every entry and not only the first.
   */
  const atlasSeededRef = useRef(false);
  /** True once the seed owns `framing.target`, so a later pass can tell ITS own
   *  object from one the user (or `Rig > Mount`'s FRAME) put there. */
  const atlasFollowRef = useRef(false);
  const claimAtlasView = useCallback(() => { atlasSeededRef.current = true; }, []);
  const aimReadyNow = model.aimReady;
  useEffect(() => {
    if (!atlasOn) {
      atlasSeededRef.current = false;
      atlasFollowRef.current = false;
      return;
    }
    if (atlasSeededRef.current || !aimReadyNow) return;
    const f = useStore.getState().framing;
    if (f == null) return;              // the seeding effect above runs first
    // A framing that arrived with an object on it is somebody else's choice.
    if (f.target && !atlasFollowRef.current) { atlasSeededRef.current = true; return; }
    if (!lock) return;                  // nothing ranked yet: leave it where it is
    // BY VALUE, not by identity: `lock` is a fresh object on every ranking
    // recompute, and a write per recompute would be a new framing session (and
    // a new region fetch) every thirty seconds for a reticle that never moved.
    if (f.target?.id === lock.id
      && f.center.ra_hours === lock.ra_hours
      && f.center.dec_deg === lock.dec_deg) return;
    atlasFollowRef.current = true;
    setFraming({
      target: entryOf(lock),
      center: { ra_hours: lock.ra_hours, dec_deg: lock.dec_deg },
      // AND IT OPENS AS A SKY, not as a crop. `openFraming`'s zoom seed is the
      // CAMERA's field times 1.6 - 59 arcminutes on the rig this was measured
      // on - which is the right question for framing a sensor and the wrong one
      // for the screen the app opens in: one object, a rectangle, and black.
      // The atlas's own widest field is what "look at the sky" means, and every
      // way back in is one gesture (pinch, wheel, or FRAME's MATCH CAMERA).
      // Only ever on a session with nothing framed - a framing the user set up
      // keeps its own zoom, which is the branch above this one.
      fovZoomDeg: ZOOM_MAX,
    });
  }, [atlasOn, aimReadyNow, lock, setFraming]);

  // ---- `#/sky?mode=atlas`, where the classic Atlas lands -------------------
  //
  // `legacyBridge.ts` maps the classic `atlas` view onto this URL, so an old
  // `#/atlas` bookmark and the classic root's own Atlas link both open the
  // pannable sky instead of the schematic finder.
  //
  // IGNORED WHILE FRAME IS ON, and that is not a special case - it is this
  // screen's own write coming back. `store.openFraming()` sets `view: "atlas"`,
  // so pressing FRAME here travels out through the bridge and returns as this
  // parameter a tick later; without the guard the FRAME button would drop the
  // user into ATLAS instead.
  //
  // Consumed with `nav.replace`, exactly as `?lock=` and `?frame=1` are, and
  // for the same two reasons: a later re-render would otherwise re-enter ATLAS
  // after the user left it, and Back from the next screen would do it again.
  const modeParam = route.params.mode ?? null;
  const handledModeRef = useRef<string | null>(null);
  const clearModeParam = useCallback(() => {
    const r = routeRef.current;
    const params = { ...r.params };
    delete params.mode;
    nav.replace(buildHash({ hub: r.hub, sub: r.sub, sheets: r.sheets, params }));
  }, []);
  const frameOnRef = useRef(frame.on);
  frameOnRef.current = frame.on;
  const setModeRef = useRef(model.setMode);
  setModeRef.current = model.setMode;

  useEffect(() => {
    if (modeParam !== "atlas") { handledModeRef.current = null; return; }
    if (handledModeRef.current === modeParam) return;
    handledModeRef.current = modeParam;
    clearModeParam();
    if (frameOnRef.current) return;
    setModeRef.current("atlas");
  }, [modeParam, clearModeParam]);

  const pressAtlas = useCallback(() => {
    if (atlasOn) { model.setMode(finderMode); return; }
    // Seeded in the SAME batch as the mode flip, so the canvas never renders a
    // frame with nothing to draw on the way in.
    if (useStore.getState().framing == null) openFraming();
    model.setMode("atlas");
  }, [atlasOn, finderMode, model, openFraming]);

  /**
   * LOCK IN FINDER - the round trip the classic Atlas never had.
   *
   * It aims through `#/sky?lock=<id>`, the same hash the targets sheet and the
   * catalog search use, so a lock from the atlas ends in exactly the state a
   * tap on the marker would (centred, tracked, auto-aim off) and an object the
   * ranking does not carry gets the same one-line refusal instead of silence.
   * When there is no ranking to carry it - no site saved, so tonight's list is
   * a 409 - the hub holds the framed object as the lock instead (#466, `held`
   * above), because refusing it then refuses every object there is.
   *
   * MAP rather than `finderMode`: a lock is a thing you read off the reticle,
   * and AR CAMERA needs the phone physically pointed at that patch of sky
   * before it shows anything at all.
   */
  const lockInFinder = useCallback(() => {
    const t = useStore.getState().framing?.target;
    if (!t) return;
    model.setMode("map");
    nav.go(`/sky?lock=${encodeURIComponent(t.id)}`);
  }, [model]);

  /** A search result or a tapped marker: recentre the atlas on it and make it
   *  the framed object, so LOCK IN FINDER and FRAME both name it. */
  const atlasPick = useCallback(
    (entry: CatalogEntry) => {
      claimAtlasView();
      setFraming({ target: entry, center: { ra_hours: entry.ra_hours, dec_deg: entry.dec_deg } });
    },
    [setFraming, claimAtlasView],
  );

  /**
   * The same move from a marker on the canvas rather than a search result.
   *
   * `alt`/`az` are OMITTED rather than filled in, verbatim from
   * `AtlasView.tsx:639-651`: the type declares them because `/api/catalog`
   * attaches them for a caller holding `view.site_derived`, but the sky-region
   * payload deliberately carries no such pair - a pannable map must not be a
   * coordinate oracle for the rig's location. An absent field is honest; a 0
   * would claim the object sits on the horizon due north. `mag: 99` is this
   * app's existing "unmeasured" sentinel.
   */
  const atlasPickRow = useCallback(
    (row: SkyRow | null) => {
      if (!row) return;
      atlasPick({
        id: row.id,
        name: row.label,
        type: row.type,
        ra_hours: row.ra_hours,
        dec_deg: row.dec_deg,
        mag: row.mag ?? 99,
        size_arcmin: row.size_arcmin,
      } as CatalogEntry);
    },
    [atlasPick],
  );

  /**
   * A TAP ON ONE OF TONIGHT'S TARGETS, on the atlas.
   *
   * It does exactly what a tap on a MAP marker does - aims the finder at the
   * object and tracks it, which is what makes `model.lock` that object and puts
   * the lock card under the canvas - and it additionally frames it here, the
   * same move a search result makes, so LOCK IN FINDER and FRAME name what the
   * user just pressed rather than whatever was framed before.
   */
  const atlasPickTarget = useCallback(
    (t: SkyTarget) => {
      claimAtlasView();
      model.setView({ az: t.azNow, alt: t.altNow, trackId: t.id });
      setFraming({ target: entryOf(t), center: { ra_hours: t.ra_hours, dec_deg: t.dec_deg } });
    },
    [model, setFraming, claimAtlasView],
  );

  /**
   * AIM ANYWHERE: a tap on empty sky.
   *
   * "if I select a random point in space because i'm trying to discover
   * something new, i'd like to see that track on the dome" - so the tap has to
   * reach the FINDER's aim and not just the atlas's centre, because everything
   * that follows a bare patch hangs off the reticle: `model.patch` is what the
   * patch card's IMAGE THIS PATCH sends, what the COORDINATES sheet opens with,
   * and what the dome draws a walk for.
   *
   * Three writes, one gesture, and each is needed:
   *   * the FINDER's aim, converted to alt/az here because that is the pair
   *     `setView` speaks and this is the one screen that starts from RA/Dec.
   *     `trackId: null` because a patch of sky is not an object to follow.
   *   * the framing CENTRE, so FRAME opens on the patch that was tapped and the
   *     canvas stays under the finger instead of the reticle walking off it.
   *   * the framing TARGET cleared, because there is no longer a catalogued
   *     object on this session - leaving the last one would have LOCK IN FINDER
   *     offering an object the reticle is no longer anywhere near.
   *
   * With no coordinates there is no alt/az to compute and the finder cannot
   * be aimed at all; the atlas still moves, and `model.placementNote` is
   * already on screen saying why the rest of it cannot. The conversion is the
   * model's own placement (`model.place`), a round trip through the frame the
   * model draws in. A DEFAULT site is one of the no-coordinates cases now
   * (#503): the finder used to be aimed at the placeholder's alt/az, and the
   * reticle then read "UP" or "BEHIND HORIZON" for a sky that is not the
   * operator's.
   */
  const aimAtSky = useCallback(
    (raHours: number, decDeg: number) => {
      claimAtlasView();
      setFraming({
        target: undefined,
        center: { ra_hours: raHours, dec_deg: decDeg },
        freeroamId: `Sky ${raHours.toFixed(2)}h ${decDeg >= 0 ? "+" : ""}${decDeg.toFixed(1)}°`,
      });
      const at = model.place(raHours, decDeg);
      if (at == null) return;
      model.setView({ az: at.azDeg, alt: at.altDeg, trackId: null });
    },
    [setFraming, claimAtlasView, model],
  );

  /**
   * The catalogue rows the CANVAS draws, minus the ones the finder's own pills
   * already name.
   *
   * ONE OBJECT, ONE LABEL. `SkyCanvas` annotates the patch from
   * `GET /api/catalog/region` and the atlas now also draws tonight's ranked
   * targets as the design's label pills - and the ranked ones are, by
   * construction, catalogued, so every target in view would otherwise carry two
   * names on one 390 px screen. The pill wins because it carries more: the
   * altitude, the cloud/obstruction colour, and a tap that locks. Everything
   * the ranking does not cover is still the canvas's own, tap included.
   */
  const targetIds = useMemo(() => new Set(model.targets.map((t) => t.id)), [model.targets]);
  const atlasRows = useMemo(
    () => region.rows.filter((r) => !targetIds.has(r.id)),
    [region.rows, targetIds],
  );

  /** The model's own sentences about the ranking, for the atlas to print. Not
   *  re-worded here: `rankingError` names why the list is empty and
   *  `placementNote` names why most of the sky cannot be placed for this role,
   *  and they are two different silences. */
  const rankingNotes = useMemo(
    () => [model.rankingError, model.placementNote].filter((s): s is string => !!s),
    [model.rankingError, model.placementNote],
  );

  /**
   * Is the atlas centre above the horizon?
   *
   * FRAME needs a patch of SKY: below the horizon there is nothing to point a
   * camera at, which is the same rule `frameReason` applies to the reticle. The
   * survey itself is drawable anywhere, so this gates only the hand-off.
   *
   * With no site there is no claim to make, so the control stays live rather
   * than refusing on arithmetic it cannot do - and a DEFAULT site is no site
   * (#466): `trackLat` is null for one, so a placeholder's horizon cannot lock
   * FRAME at the hours it happens to have the centre below it.
   */
  const atlasFrameReason = useMemo(() => {
    if (!framing || trackLat === null || trackLon === null) return null;
    const { altDeg } = altAzOf(
      framing.center.ra_hours, framing.center.dec_deg, trackLat, trackLon, modelNowMs / 1000,
    );
    return altDeg > 0 ? null : FRAME_NEEDS_AIM;
  }, [framing, trackLat, trackLon, modelNowMs]);

  /** FRAME from the atlas: back to the finder, framing what is on screen. The
   *  mosaic grid, the rotation dial and the panel arithmetic all live in FRAME
   *  mode, and they are the reason this button exists rather than a second copy
   *  of them here. */
  const atlasFrame = useCallback(() => {
    model.setMode(finderMode);
    resumeFrame();
  }, [model, finderMode, resumeFrame]);

  // In ATLAS the toolbar's FRAME button does what the atlas's own FRAME button
  // does - hands the centre on screen to FRAME mode - so it reads FRAME rather
  // than DONE, and refuses for the same reason (a centre below the horizon).
  const frameLabel = model.mode === "atlas"
    ? "FRAME"
    : frame.on ? "DONE" : frame.set ? "ADJUST" : "FRAME";
  // A patch of sky is enough now: free-roam is what the third branch below is.
  //
  // TWO WAYS TO HAVE NOTHING TO FRAME, and they need different sentences
  // (#568). `model.patch` is null both when the reticle is aimed at ground
  // (`view.alt < 0`) and when there are no coordinates to place it with at all
  // (`!haveCoords`, finder/model.ts) - a default site's placeholder, or a
  // role this site's precise location is hidden from. Since H4-USKY (#503) the
  // second case is the common one on a fresh config, and FRAME_NEEDS_AIM's
  // "aim above the horizon" is false there: the reticle can be at alt 45 and
  // FRAME is still refused. `model.placementNote` is the model's own sentence
  // for that silence - the same one the patch card prints - and it already
  // names the right fix (set the site, or nothing a locked-out role can do);
  // FRAME_NEEDS_AIM is kept for when there ARE coordinates and the reticle is
  // simply aimed at ground.
  const frameReason = model.mode === "atlas"
    ? atlasFrameReason
    : frame.on || lock || model.patch ? null : (model.placementNote ?? FRAME_NEEDS_AIM);
  const onFramePress = () => {
    if (model.mode === "atlas") { atlasFrame(); return; }
    if (frame.on) { void finishFrame(); return; }
    const t = lock ?? model.reachList[0];
    if (t) { enterFrame(t, frame.set && frame.id === t.id); return; }
    // ADJUST on a kept free-roam framing RESUMES it. Re-entering through
    // `enterFreeRoam` would call `openFraming` again and throw away the mosaic
    // and the angle the button is offering to adjust.
    if (frame.set && frame.id === null && resumeFrame()) return;
    if (model.patch) { enterFreeRoam(model.patch); return; }
  };

  // ---- the plan summary the primary CTA prints ----------------------------
  //
  // THROUGH THE QUICK SHEET'S OWN SEAM, not a second one. This button OPENS
  // that sheet, so the line under the label and the line the sheet's GENERATE
  // FLOW prints have to describe the same night. Three things came apart when
  // it did not:
  //
  //   * `resolveWheel` hands back the ASSUMED seven names for a rig with no
  //     wheel, so a one-shot-colour camera on a refractor read "7 filters"
  //     while the sheet one tap away built ONE channel and posted
  //     `filters: []`. `wheelModel` is the seam that knows the difference
  //     (`oneChannel`/`source`), and it is the one `quick.tsx:270` uses.
  //   * `quick.hours` is the STORED number and is meaningless while
  //     `quick.dawn` is set - the window is then tonight's dawn, which is a
  //     different length every night. `resolveQuickHours` is the one rule.
  //   * the raw float printed "5.216388888h". `hoursLabel` renders the window
  //     the way the sheet's own stop labels do, and `finishLabel` renders the
  //     clock with the day-delta suffix suppressed, so a session that ends
  //     after midnight does not read "00:14 (+1d)" on every phone.
  const fw = status?.filterwheel;
  // A STRING SIGNATURE, not the live block: `status.filterwheel` is a fresh
  // object on every 2 s status frame, so anything keyed on it directly would
  // rebuild this label thirty times a minute for a wheel that never moved.
  const wheelKey = fw
    ? [
        (fw.names ?? []).join("|"),
        (fw.opaque ?? []).map((b) => (b ? 1 : 0)).join(""),
        (fw.narrowband ?? []).map((b) => (b ? 1 : 0)).join(""),
        (fw.exposures ?? []).join(","),
      ].join("::")
    : "";
  // The house helper, not a raw `status.connected.camera` read: a bridged rig
  // reports its roles on `backend_links` instead, and `wheelModel` needs to
  // tell "a rig with a camera and no wheel" (one channel) from "no rig to ask"
  // (the assumed seven).
  const cameraConnected = useStore((s) => resolveRoleConnected(
    "camera", s.status?.backend_links, s.status?.connected, s.equipConnected,
  ).connected);
  const quickOn = quick.on;
  const quickExp = quick.exp;
  const wheel = useMemo(
    () => wheelModel(fw, quickOn, quickExp, cameraConnected),
    // `wheelKey` stands in for `fw` deliberately - see the signature above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wheelKey, quickOn, quickExp, cameraConnected],
  );
  const dawnH = hoursToDawn(model.visibility, model.nowMs);
  const planHours = resolveQuickHours(quick.hours, quick.dawn, dawnH);
  const planSummary = useMemo(() => {
    const oneSlot = wheel.source === "one-slot" ? (wheel.slots[0] ?? null) : null;
    const oscExposure = oneSlot?.exposure ?? quickExp[OSC_LABEL] ?? 120;
    const checkedCount = wheel.slots.filter((s) => s.checked).length;
    const middle = wheel.oneChannel
      ? `${oscExposure}s × ${oscCount(planHours, oscExposure)}`
      : checkedCount === 0
        ? "pick a filter"
        // "assumed" is the no-rig-to-ask case: the seven names are this app's,
        // not a wheel's, and a bare "7 filters" on a laptop would be a claim
        // about hardware nobody has plugged in.
        : wheel.source === "assumed"
          ? `${checkedCount} filters assumed`
          : `${checkedCount} filters`;
    return `${hoursLabel(planHours, dawnH)} · ${middle} · `
      + `finishes ${finishLabel(model.nowMs, planHours)}`;
  }, [wheel, quickExp, planHours, dawnH, model.nowMs]);

  // ---- actions ------------------------------------------------------------
  const goQuick = (t: LockTarget) => nav.sheet("quick", { target: t.id });
  const goVideo = (t: LockTarget) =>
    nav.go(
      `/rig/capture?mode=video&target=${encodeURIComponent(t.name)}` +
      `&ra=${t.ra_hours}&dec=${t.dec_deg}`,
    );
  /**
   * A SATELLITE LOCK, which today cannot happen and one day will.
   *
   * `finder/targets.ts SATELLITE_MARKERS` is `false`, so no satellite is ever
   * drawn on the reticle and this branch is unreachable - but `lockCta` ALREADY
   * answers `kind: "passes"` for one, and without the case below the default
   * arm sent it to the quick sheet, which would queue a deep-sky night on a
   * body that has left the frame before the first sub finishes. The whole
   * point of that constant is that flipping it is the ONLY change needed, so
   * every branch behind it is written now rather than found at the eyepiece.
   *
   * The pass list lives in the targets sheet (it owns the `/api/satellites/
   * passes` fetch and the withheld-reason copy), so the id travels in the hash
   * the same way `?lock=` does.
   */
  const goPasses = (t: LockTarget) => nav.sheet("targets", { sat: t.id });

  const pressPrimary = (cta: LockCardCta) => {
    if (!lock) return;
    if (cta.kind !== "site" && cta.kind !== "unplaced") {
      const t = ctaToast(cta.kind);
      if (t) enqueueToast({ level: t.level, title: t.title });
    }
    switch (cta.kind) {
      case "connect":
        nav.go("/rig/devices");
        return;
      case "site":
        // The site pill's own sheet: the fix for a lock nobody could place is
        // a saved site, and this is where one is saved (#503).
        nav.sheet("sites");
        return;
      case "video":
        goVideo(lock);
        return;
      case "passes":
        goPasses(lock);
        return;
      case "obstructed":
      case "unplaced":
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
    putPool(next);
  };

  // The x beside + PLAN. It exists only for the one state in which the button
  // itself stopped being a toggle (`LockCard`'s `showRemove`), and it writes the
  // pool through the same `putPool` the toggle does - the pool is the rig's, not
  // this phone's, so there is no local copy to keep in step.
  const pressRemoveFromPool = () => {
    if (!lock) return;
    putPool(pool.filter((id) => id !== lock.id));
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
  //
  // SET A SITE FIRST is live for the same reason (#503): it opens the site
  // sheet, commands nothing, and a saved site is the one thing that would let
  // the card say whether the object is up. POSITION HIDDEN FOR THIS ROLE is
  // the opposite case - nothing this reader can press fixes it - so it is
  // locked with the sentence that says why. The card and this line read the
  // same `lockCardCta`, so the reason can never belong to another case.
  const ctaConnect = !equipConnected;
  const lockCtaKind = lock ? lockCardCta(lock, equipConnected, siteSaved).kind : null;
  const primaryReason = ctaConnect || lockCtaKind === "site"
    ? null
    : capture.lockedReason ?? (lock?.obstructed === true
      ? obstructedReason(lock.name, model.siteName)
      : lock != null && lockCtaKind === "unplaced" ? unplacedReason(lock.name) : null);
  // Both secondaries refuse a SATELLITE before they refuse anything else - see
  // `goPasses` above for why the branch exists while `SATELLITE_MARKERS` is
  // false. The primary needs no such clause: `lockCta` already routes a
  // satellite to its own `passes` case, which is the one useful answer.
  const satLock = lock?.kind === "satellite";
  const singleReason = satLock ? SAT_NO_SINGLE : capture.lockedReason;
  const planReason = satLock ? SAT_NO_PLAN : null;

  // MAP is the only mode a desktop has: no camera worth pointing at the sky and
  // no orientation sensor, so the toggle is hidden rather than offered and
  // refused (plan E).
  const arPossible = model.secureContext && (model.cameraSupported || model.gyroSupported);
  const showModeToggle = bp !== "desktop" || arPossible;
  const modeReason = model.secureContext ? null : SECURE_REASON;
  /** Where the AR CAMERA / MAP button goes: out of ATLAS it returns to the
   *  finder mode the user left; inside the finder it toggles the two. */
  const modeTarget: "cam" | "map" = atlasOn
    ? finderMode
    : finderMode === "cam" ? "map" : "cam";
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

  // THE TOOLBAR AS A VALUE, because ATLAS needs it in a different place.
  // At 390 px the pannable sky is taller than the viewport, so a toolbar left
  // below it puts the only way OUT of the atlas off the bottom of the screen.
  // It goes above the canvas in ATLAS and stays below the box everywhere else,
  // where the finder is short enough that the design's own order holds.
  const toolbar = (
    <div style={{ display: "flex", gap: 8 }}>
      {/* ATLAS FIRST, because it is what the screen opens in. The row reads in
          the order a user meets it - the sky you are looking at, then the
          reticle, then the two things you do with the reticle - and the button
          for the CURRENT mode sitting first is the one a thumb finds without
          reading.

          NEVER LOCKED, and that is a fact about the server rather than a
          decision: the three requests behind the atlas -
          `/api/survey/tile/...`, `/api/survey/cutout.jpg` and
          `/api/survey/pack` - are all `view.status`, and it needs no site, no
          target and no connected device. A viewer on a rig with nothing
          plugged in gets the same sky an admin does, which is the whole
          reason the classic Atlas was reachable before anything was set up. */}
      <IconButton48
        glyph={<SkyGlyph name="atlas" />}
        label="ATLAS"
        active={atlasOn}
        onExplain={onExplain}
        onPress={pressAtlas}
        className="nx-sky-tool"
        data-testid="sky-atlas-mode"
      />
      {showModeToggle && (
        // WHILE ATLAS IS ON this button is the way back, and it names the
        // finder mode it will return to rather than toggling between the two
        // - a user who went to the atlas from AR CAMERA gets AR CAMERA back.
        // Outside ATLAS it is the same toggle it has always been: the label
        // names the CURRENT mode and a press swaps to the other one.
        <IconButton48
          glyph={<SkyGlyph name={finderMode === "cam" ? "arcamera" : "map"} />}
          label={finderMode === "cam" ? "AR CAMERA" : "MAP"}
          active={model.mode === "cam"}
          // Locked only when the press would actually OPEN the camera, which
          // is the condition the reason names - not merely when the label
          // says MAP. Coming back from ATLAS to MAP opens nothing.
          lockedReason={modeTarget === "cam" ? modeReason : null}
          onExplain={onExplain}
          onPress={() => model.setMode(modeTarget)}
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
  );

  // A COUNT ONLY OF WHAT WAS PLACED (#544, the #503 residual). With no
  // coordinates (`placementNote` set) and no row the rig placed itself, the
  // reach count is 0 because nothing could be judged, not because nothing is
  // up, so the status row and the patch card print no count at all. A role
  // without the coordinates that the rig placed rows for is still counted:
  // those rows were judged, by the rig.
  const reachCount = model.placementNote != null && model.targets.length === 0
    ? null
    : model.reachCount;

  return (
    <div data-testid="hub-sky" style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      {!equipConnected && <BrowseBanner />}

      <StatusRow
        reachCount={reachCount}
        clearPct={model.clearPct}
        siteName={model.siteName}
        onDome={onDome}
      />

      {atlasOn && toolbar}

      <div
        ref={wrapRef}
        style={{ position: "relative", minWidth: 0 }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {atlasOn ? (
          // ATLAS wins over FRAME while it is on. The framing session is left
          // exactly as it was, so MAP (or the atlas's own FRAME button) puts
          // the mosaic and the angle back where the user left them - ATLAS
          // suspends FRAME, it never ends it.
          framing && (
            <AtlasHost
              framing={framing}
              optics={mergedOptics}
              night={night}
              mode={frameMode}
              imageBrightness={surveyBright}
              surveyDegraded={surveyDegraded}
              onlineFetch={onlineFetch}
              mount={footprintMount}
              rotator={status?.rotator ?? null}
              pointingWhere={footprintWhere}
              skyRows={atlasRows}
              region={{ degraded: region.degraded, truncated: region.truncated, error: region.error }}
              selectedObjectId={framing.target?.id ?? null}
              targets={model.targets}
              lockId={lock?.id ?? null}
              kindIcon={model.kindIcon}
              onPickTarget={atlasPickTarget}
              rankingNotes={rankingNotes}
              aim={model.patch}
              onAimSky={aimAtSky}
              survey={surveyLayer}
              onLayers={openLayers}
              onPick={atlasPick}
              onPickRow={atlasPickRow}
              onCenterChange={(ra, dec) => {
                // A pan or a keyboard nudge is the user claiming the view, so
                // the opening seed above never drags it back.
                claimAtlasView();
                setFraming({ center: { ra_hours: ra, dec_deg: dec } });
              }}
              onRotate={(deg) => setFraming({ rotation_deg: deg })}
              onZoom={(f) => { claimAtlasView(); setFraming({ fovZoomDeg: f }); }}
              onSurveyError={() => setSurveyDegraded(true)}
              onSurveyLoad={() => setSurveyDegraded(false)}
              // The SKY DATA sheet is Settings' own, so this NAVIGATES there the
              // way the finder's optics and connection notes do rather than
              // pulling another hub's sheet on top of the Sky hub.
              onSurveySource={() => nav.go("/settings/general/skyPack")}
              onLockInFinder={lockInFinder}
              onFrame={atlasFrame}
              frameReason={atlasFrameReason}
              onExplain={onExplain}
            />
          )
        ) : frame.on && framing ? (
          <FrameHost
            framing={framing}
            optics={mergedOptics}
            night={night}
            mode={frameMode}
            imageBrightness={surveyBright}
            surveyDegraded={surveyDegraded}
            degradedText={surveyDegradedText(onlineFetch, pack)}
            onlineFetch={onlineFetch}
            mount={footprintMount}
            rotator={status?.rotator ?? null}
            pointingWhere={footprintWhere}
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

      {!atlasOn && toolbar}

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

      {/* WHY THE FINDER IS EMPTY, when there are no coordinates to place its
          sky with (#503): no saved site, or a role they are withheld from. The
          atlas prints the same sentence among its own notes, so this card is
          for the finder modes only. Without it a default site's MAP was a
          blank box and a reach count of zero, with nothing to say that the
          sky had not been placed rather than that nothing was up. */}
      {!atlasOn && model.placementNote && (
        <Card tone="dashed" data-testid="sky-placement-note">
          <div style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5 }}>
            {model.placementNote}
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

      {/* The FRAME cluster belongs to the FINDER's framing session. ATLAS
          suspends it rather than ending it, so these come back untouched the
          moment the user leaves the atlas - but a mosaic grid and a rotation
          dial under a whole-sky canvas would be controls for a rectangle that
          is not on screen. */}
      {frame.on && framing && !atlasOn && (
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
            // THE FRAME'S FORWARD ACTION (#196, spec section 8 S6): the shared
            // wizard, pushed over the sky with this framing in its route, so
            // CLOSE and EDIT FRAMING pop back to FRAME exactly as it was. It is
            // never locked here: whatever the wizard cannot do on this rig (no
            // optics for a grid, no capture capability to GENERATE) it says on
            // its own step, where the operator can answer it.
            onSendToWizard={() => openFlowWizard(framingPrefill(framing, fov))}
            onExplain={onExplain}
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

      {/* THE LOCK CARD IS ON BOTH MODES NOW, because the reticle is. ATLAS
          draws the finder's aim as a marker on the sky and a tap moves it, so
          the question "what is locked and what do I do with it" has the same
          answer in both modes and belongs in the same place under both - a
          user who taps M31 on the atlas must not have to press MAP to find the
          button that images it.

          The REACH STRIP stays behind: it is a list of things that are NOT on
          screen, offered because the schematic finder shows one patch of sky at
          a time. The atlas shows them, as pills, in their real positions. */}
      {(lock ? (
        <LockCard
          lock={lock}
          equipConnected={equipConnected}
          siteSaved={siteSaved}
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
          onRemoveFromPool={pressRemoveFromPool}
          primaryReason={primaryReason}
          singleReason={singleReason}
          planReason={planReason}
          onExplain={onExplain}
        />
      ) : (
        <PatchCard
          patch={model.patch}
          reachCount={reachCount}
          targetCount={model.targets.length}
          placementNote={model.placementNote}
          onImagePatch={pressPatch}
          onFramePatch={enterFreeRoam}
          framed={framedForPatch}
          onAdjustFrame={() => { if (!resumeFrame() && model.patch) enterFreeRoam(model.patch); }}
          onClearFrame={clearFrame}
          onCoords={openCoords}
          imageReason={capture.lockedReason}
          onExplain={onExplain}
        />
      ))}

      {/* NOT ON AN UNPLACEABLE SKY WITH NOTHING ON IT (#503). The strip's
          empty state says everything is "behind your horizon or under cloud",
          which is a verdict about every object at once - and with no
          coordinates nothing was judged at all. The placement note above says
          what is true instead. A strip with chips in it still shows: those
          are rows the rig placed itself, which a role without the site's
          coordinates can still be given. */}
      {!atlasOn && (model.placementNote == null || model.reachList.length > 0) && (
        <ReachStrip
          reachList={model.reachList}
          onAim={(t) => model.setView({ az: t.azNow, alt: t.altNow, trackId: t.id })}
        />
      )}

      {/* LAST, and below the reach strip, which is where the design's stack
          ends. It is the only card on this screen that answers a question about
          somewhere OTHER than the reticle, and putting it above the lock card
          would push the three things you came here to press below the fold on a
          phone. */}
      <DomeCard
        canViewWeather={canViewWeather}
        pointing={pointing}
        // An unplaced lock has no point on the hemisphere to mark (#503).
        target={lock && lock.altNow != null && lock.azNow != null
          ? { alt: lock.altNow, az: lock.azNow, name: lock.name }
          : null}
        horizon={horizonPoints}
        wind={domeWind}
        tracks={model.dome.tracks}
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
        // Only on the atlas: the schematic finder draws no imagery, so a switch
        // for it there would be a control with nothing behind it.
        survey={atlasOn ? { on: surveyLayer, onToggle: setSurveyLayer } : null}
        note={model.layersNote}
        windNote={model.windNote}
        weatherReason={model.weatherAllowed ? null : "needs operator or admin access"}
        onExplain={onExplain}
      />
    </div>
  );
}
