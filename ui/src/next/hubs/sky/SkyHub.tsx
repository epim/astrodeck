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

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
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
import { KIND_LABEL } from "./finder";
import { BrowseBanner } from "./cards/BrowseBanner";
import { StatusRow } from "./cards/StatusRow";
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
import { OVERLAP, fetchPanels, framedStrip, frameText } from "./frame/mosaic";
import { effectiveOptics } from "../../../lib/effective";
import { fovFromOptics, type OpticsLike } from "../../../lib/framing";
import { useSkyRegion, type SkyRow } from "../../../lib/skyRegion";
import { useCapability } from "../../../lib/caps";
import { resolveWheel } from "../../../components/flows/cyclePlanRows";
import {
  useConfig,
  useEquipConnected,
  useFraming,
  useNight,
  useStatus,
  useStore,
} from "../../../store";
import type { CatalogEntry } from "../../../types";

// ------------------------------------------------------------------- copy

export const SECURE_REASON =
  "AR camera and gyro need a secure connection - set up in Connection";
export const NO_GYRO_REASON = "No orientation sensor here - drag the sky to pan.";
export const FRAME_NEEDS_LOCK =
  "Aim at a target first - FRAME needs something in the reticle.";

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
  const canViewWeather = useCapability("view.weather");
  const enqueueToast = useStore((s) => s.enqueueToast);
  const openFraming = useStore((s) => s.openFraming);
  const setFraming = useStore((s) => s.setFraming);

  const [lensOpen, setLensOpen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [frame, setFrame] = useState<FrameState>({ on: false, set: false, id: null });
  const [pool, setPool] = useState<string[]>(() => skyPrefs.getPool());
  const [frameMode] = useState<"survey" | "schematic">(() => skyPrefs.getFrameMode());
  const [surveyBright] = useState<number>(() => skyPrefs.getSurveyBright());
  const [surveyDegraded, setSurveyDegraded] = useState(false);
  const quick = useMemo(() => skyPrefs.getQuick(), []);

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

  // ---- FRAME mode ---------------------------------------------------------
  const frameTarget = frame.id;
  const region = useSkyRegion(
    framing?.center ?? { ra_hours: 0, dec_deg: 0 },
    framing?.fovZoomDeg ?? 0,
    frame.on && framing != null,
  );

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
    enqueueToast({
      level: "success",
      title: `Framing kept - ${frameText(cols, rows, f.rotation_deg)} goes into the flow.`,
    });
  }, [fov.fov_x_deg, fov.fov_y_deg, setFraming, enqueueToast]);

  const clearFrame = useCallback(() => {
    setFraming({ mosaic: { rows: 1, cols: 1, overlap: OVERLAP }, rotation_deg: 0, panels: [] });
    setFrame({ on: false, set: false, id: null });
    enqueueToast({ level: "info", title: "Framing removed - the flow centres on the catalogue position." });
  }, [setFraming, enqueueToast]);

  const frameLabel = frame.on ? "DONE" : frame.set ? "ADJUST" : "FRAME";
  const frameReason = frame.on || lock ? null : FRAME_NEEDS_LOCK;
  const onFramePress = () => {
    if (frame.on) { void finishFrame(); return; }
    const t = lock ?? model.reachList[0];
    if (!t) return;
    enterFrame(t, frame.set && frame.id === t.id);
  };

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
        lockId={lock?.id ?? null}
        canViewWeather={canViewWeather}
        onExplain={onExplain}
      />

      <div ref={wrapRef} style={{ position: "relative", minWidth: 0 }}>
        {frame.on && framing ? (
          <FrameHost
            framing={framing}
            optics={mergedOptics}
            night={night}
            mode={frameMode}
            imageBrightness={surveyBright}
            surveyDegraded={surveyDegraded}
            onlineFetch={config?.survey?.online_fetch ?? false}
            mount={status?.mount ?? null}
            rotator={status?.rotator ?? null}
            pointingWhere={status?.mount ? `${status.mount.ra_str} ${status.mount.dec_str}` : null}
            skyRows={region.rows}
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
          onCoords={() => nav.sheet("coords")}
          imageReason={capture.lockedReason}
          onExplain={onExplain}
        />
      )}

      <ReachStrip
        reachList={model.reachList}
        onAim={(t) => model.setView({ az: t.azNow, alt: t.altNow, trackId: t.id })}
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
