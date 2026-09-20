// horizon.tsx - the HORIZON EDITOR sheet (T-SKY-4, plan A.15). Shared between
// the Sky and Settings hubs (ARCHITECTURE.md section 5: one component,
// registered once).
//
// Persistence takes one of two branches, decided at load time from what the
// server actually returned (never guessed from a version number):
//
//   - `params.site` names a saved location -> the edit writes THAT location's
//     own `horizon_points` (`PUT /api/locations/{id}`). Applying the location
//     again (Sites sheet) is what copies it into the engine.
//   - otherwise (the active site, or a location id that vanished) -> the edit
//     writes the engine's live obstruction line directly
//     (`POST /api/config {safety}`, echoing the whole block). This is also
//     the ONLY path on a server that predates S1 (`horizon_points` absent
//     from `GET /api/site` entirely) - S1 or not, "the active site's line" is
//     always `config.safety.horizon`, so one code path covers both, and only
//     the sub-line's honesty note differs (H.9).
import {
  useEffect, useMemo, useRef, useState,
  type JSX, type PointerEvent as RPointerEvent,
} from "react";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import type { SheetProps } from "../../sheets";
import { ActionButton, Card, Label, Mono, Sheet } from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import {
  insertPoint, movePoint, removePoint, summary,
  type HorizonPoint,
} from "../../../lib/horizonModel";
import { useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { getSite, listLocations, updateLocation, applyLocation, type LocationInput } from "../../../../api/site";
import { setSafetyConfig } from "../../../../api/backends";
import type { SavedLocation } from "../../../../types";
import {
  FLOOR_ALT_DEG, GROUND_Y, HX, HY, SKY_Y, STRIP_H, STRIP_W,
  altFromY, azFromX, buildFillPathD, buildStrokePathD, hitTestPoint, toViewBox,
} from "./horizonStrip";
import {
  PhotosphereSweep, checkPhotosphereSupport, traceSkyCoverage, OVERHEAD_BAND,
} from "./photosphere";
import { PhotosphereDome } from './PhotosphereDome';
import { readPanorama, writePanorama } from './photosphereStorage';

type Source = "location" | "active";

/** Why the lens picker is locked. Opening a camera stream takes a moment and a
 *  second open while the first is in flight strands both. */
const CAMERA_LOCKED = "Opening the camera. The lens can be changed once it is ready.";

export function HorizonSheet({ params, onClose, onBusyChange, guided = false, onSaved, onDirty }: SheetProps & { onClose?: () => void; onBusyChange?: (busy:boolean) => void; guided?: boolean; onSaved?: () => void; onDirty?: () => void }): JSX.Element {
  const loadConfig = useStore((s) => s.loadConfig);
  const enqueueToast = useStore((s) => s.enqueueToast);
  // Branch-aware (this file's header comment): `persist()` below takes one of
  // two routes depending on `source` - PUT /api/locations/{id}
  // (config.site_optics, app.py:3488-3489) when editing a saved location's
  // own polyline, or POST /api/config {safety} (config.safety; the field-cap
  // table at app.py:3250 maps the "safety" block to CAP_CONFIG_SAFETY) when
  // editing the active site's live obstruction line. A single
  // `config.safety`-only lock (as shipped) let a config.safety holder
  // without config.site_optics see a saved location's write as unlocked and
  // then be refused server-side - the control claimed a permission it did
  // not have. `POST /api/locations/{id}/apply` (app.py:3556-3560), which
  // copies a saved location's line into the engine, carries the same
  // config.site_optics declaration as the PUT, so this pairing lines up with
  // that route too. `gate.ts`'s `GateInput` takes ONE `cap` (do not widen
  // it), so this is two separate locks with the caller picking the one for
  // the active branch.
  const { lockedReason: safetyLocked, onExplain: explainSafety } = useLock({ cap: "config.safety" });
  const { lockedReason: opticsLocked, onExplain: explainOptics } = useLock({ cap: "config.site_optics" });

  const [loading, setLoading] = useState(true);
  const [loadFailed,setLoadFailed]=useState(false);
  const [siteName, setSiteName] = useState("the active site");
  const [source, setSource] = useState<Source>("active");
  const [sourceLoc, setSourceLoc] = useState<SavedLocation | null>(null);
  const [legacy, setLegacy] = useState(false);
  const [points, setPoints] = useState<HorizonPoint[]>([]);
  const [byHand, setByHand] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [reviewDraft, setReviewDraft] = useState(false);
  const [panorama, setPanorama] = useState<string | null>(null);
  const [reviewZoom, setReviewZoom] = useState(1);
  const [panPosition,setPanPosition]=useState(0);
  const panoramaScroll=useRef<HTMLDivElement|null>(null);
  const reviewAz=useRef(180);
  const [photoKey, setPhotoKey] = useState<string | null>(null);
  const [photoStored, setPhotoStored] = useState(false);

  const writeLocked = loading ? "Loading the horizon." : loadFailed ? "Reopen the horizon editor to retry loading before editing." : saving ? "Saving the horizon." : guided ? opticsLocked ?? safetyLocked : source === "location" ? opticsLocked : safetyLocked;
  const explainWrite = source === "location" ? explainOptics : explainSafety;

  // ------------------------------------------------------------------- load
  useEffect(() => {
    let dead = false;
    const siteParam = params.site;
    (async () => {
      setLoading(true);
      setLoadFailed(false);
      setPanorama(null); setPhotoKey(null); setPhotoStored(false); setReviewDraft(false); setAlignmentReport(null);
      try {
        if (siteParam && siteParam !== "current") {
          const locs = await listLocations();
          if (dead) return;
          const loc = locs.find((l) => l.id === siteParam);
          if (loc) {
            setSource("location");
            setSourceLoc(loc);
            setSiteName(loc.name);
            setLegacy(false);
            setPoints((loc.horizon_points ?? []).map(([az, alt]) => ({ az, alt })));
            setByHand(true);
            setPhotoKey(`location:${loc.id}`);
            return;
          }
        }
        const { site } = await getSite();
        if (dead) return;
        setSource("active");
        setSourceLoc(null);
        setSiteName(site.name?.trim() || "the active site");
        if(typeof site.latitude==='number' && typeof site.longitude==='number') setPhotoKey(`active:${site.latitude}:${site.longitude}:${site.elevation_m ?? 0}`);
        if (Object.prototype.hasOwnProperty.call(site, "horizon_points")) {
          setLegacy(false);
          setPoints((site.horizon_points ?? []).map(([az, alt]) => ({ az, alt })));
        } else {
          setLegacy(true);
          const safety = useStore.getState().config?.safety;
          setPoints((safety?.horizon ?? []).map(([az, alt]) => ({ az, alt })));
        }
        setByHand(true);
      } catch (e) {
        if (!dead) {
          setLoadFailed(true);
          enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not load the horizon." });
        }
      } finally {
        if (!dead) setLoading(false);
      }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.site]);

  useEffect(()=>{
    if(!photoKey)return;
    let cancelled=false;
    void readPanorama(photoKey).then(image=>{
      if(!cancelled&&image){setPanorama(current=>current ?? image);setPhotoStored(true);}
    });
    return()=>{cancelled=true;};
  },[photoKey]);

  // ---------------------------------------------------------------- persist
  const persist = async (next: HorizonPoint[], commit = false) => {
    if (writeLocked) { explainWrite(writeLocked); return; }
    if ((guided || reviewDraft) && !commit) { setPoints(next); setDirty(true); setSaved(false); onDirty?.(); return; }
    const prev = points;
    setPoints(next);
    setSaving(true);
    try {
      if (source === "location" && sourceLoc) {
        const body: LocationInput = {
          name: sourceLoc.name,
          latitude: sourceLoc.latitude,
          longitude: sourceLoc.longitude,
          elevation_m: sourceLoc.elevation_m,
          horizon_min_deg: sourceLoc.horizon_min_deg,
          horizon_points: next.map((p) => [p.az, p.alt] as [number, number]),
        };
        const updated = await updateLocation(sourceLoc.id, body);
        setSourceLoc(updated);
        if (guided) {
          await applyLocation(sourceLoc.id);
          await loadConfig();
        }
      } else {
        const safety = useStore.getState().config?.safety;
        if (!safety) throw new Error("Safety config not loaded yet - try again.");
        await setSafetyConfig({ ...safety, horizon: next.map((p) => [p.az, p.alt] as [number, number]) });
        await loadConfig();
      }
      setDirty(false); setSaved(true); onSaved?.();
    } catch (e) {
      setPoints(prev);
      enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not save the horizon." });
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------ pointer i/o
  const dragRef = useRef<{ index: number; moved: boolean; original: HorizonPoint[] } | null>(null);

  const onStripPointerDown = (e: RPointerEvent<SVGSVGElement>) => {
    if (writeLocked) { explainWrite(writeLocked); return; }
    const rect = e.currentTarget.getBoundingClientRect();
    const { x, y } = toViewBox(e.clientX, e.clientY, rect);
    const hit = panorama ? points.reduce((best,p,i)=>{
      const dist=Math.hypot((HX(p.az)-x)*rect.width/STRIP_W,(HY(p.alt)-y)*rect.height/STRIP_H);
      return dist<best.distance?{index:i,distance:dist}:best;
    },{index:-1,distance:18}).index : hitTestPoint(points, x, y);
    if (hit >= 0) {
      dragRef.current = { index: hit, moved: false, original: points };
      const el = e.currentTarget as unknown as { setPointerCapture?: (id: number) => void };
      try { el.setPointerCapture?.(e.pointerId); } catch { /* jsdom has none */ }
      return;
    }
    setByHand(true);
    void persist(insertPoint(points, azFromX(x), altFromY(y)));
  };

  const onStripPointerMove = (e: RPointerEvent<SVGSVGElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const { x, y } = toViewBox(e.clientX, e.clientY, rect);
    d.moved = true;
    setPoints((cur) => movePoint(cur, d.index, azFromX(x), altFromY(y)));
  };

  const onStripPointerUp = (e: RPointerEvent<SVGSVGElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    const el = e.currentTarget as unknown as { releasePointerCapture?: (id: number) => void };
    try { el.releasePointerCapture?.(e.pointerId); } catch { /* jsdom has none */ }
    if (!d) return;
    setByHand(true);
    if (d.moved) void persist(points);
    else void persist(removePoint(points, d.index));
  };

  // ------------------------------------------------------------- + finder
  const finderAz = params.az != null && Number.isFinite(Number(params.az)) ? Number(params.az) : null;
  const finderAlt = params.alt != null && Number.isFinite(Number(params.alt)) ? Number(params.alt) : 0;

  // -------------------------------------------------------------- clear all
  const clearAll = async () => {
    if (writeLocked) { explainWrite(writeLocked); return; }
    const ok = await confirmDialog({
      title: `Clear the horizon at ${siteName}?`,
      body: guided ? "This clears the draft. Choose Save horizon to replace the saved horizon with an open sky." : "Every point you marked here is deleted. The finder, the dome and the ranked list go back to assuming an open horizon at this site.",
      confirmLabel: "CLEAR", cancelLabel: "KEEP", tone: "danger",
    });
    if (!ok) return;
    await persist([]);
  };

  // ------------------------------------------------------------ photosphere
  const support = useMemo(() => checkPhotosphereSupport(), []);
  // A measured value can be offered through a shareable setup link. Applying
  // it is explicit and local to the selected camera; a URL never changes it.
  const suggestedLens=useMemo(()=>{
    const value=Number(new URLSearchParams(window.location.hash.split('?')[1]??'').get('cameraFov'));
    return value>=35&&value<=100?value:null;
  },[]);
  const [capturing, setCapturing] = useState(false);
  const [openingCamera, setOpeningCamera] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [, setFrameTick] = useState(0);
  const [trace, setTrace] = useState<HorizonPoint[] | null>(null);
  const [traceUncertain, setTraceUncertain] = useState(0);
  const [manualOverhead, setManualOverhead] = useState(false);
  const [alignmentReport,setAlignmentReport]=useState<string|null>(null);
  const [lensAngleDraft,setLensAngleDraft]=useState('60');
  const [adopted, setAdopted] = useState(false);
  const sweepRef = useRef<PhotosphereSweep | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const tickTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const capturePanel = useRef<HTMLDivElement | null>(null);
  const horizonStrip = useRef<SVGSVGElement | null>(null);
  useEffect(()=>{
    const el=panoramaScroll.current;if(capturing||!panorama||!el)return;
    const max=el.scrollWidth-el.clientWidth;
    el.scrollLeft=Math.max(0,Math.min(max,reviewAz.current/360*el.scrollWidth-el.clientWidth/2));
    setPanPosition(max>0?el.scrollLeft/max*100:0);
  },[panorama,reviewZoom,capturing]);
  const cameraChoice = useRef<string | undefined>(undefined);
  useEffect(() => {
    try { cameraChoice.current = localStorage.getItem("astrodeck.photosphere.camera") ?? undefined; } catch { /* private browsing */ }
  }, []);
  useEffect(() => { if (trace) (panoramaScroll.current??horizonStrip.current)?.scrollIntoView?.({ block: "center", inline:"nearest", behavior: "smooth" }); }, [trace]);
  useEffect(()=>{onBusyChange?.(loading||saving||capturing);},[loading,saving,capturing,onBusyChange]);
  useEffect(() => {
    if (capturing) {
      capturePanel.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      capturePanel.current?.focus({ preventScroll: true });
    }
  }, [capturing]);

  useEffect(() => () => {
    sweepRef.current?.stop();
    sweepRef.current = null;
    if (tickTimer.current != null) clearInterval(tickTimer.current);
  }, []);

  const cancelCapture = () => {
    sweepRef.current?.stop(); sweepRef.current = null;
    if (tickTimer.current != null) { clearInterval(tickTimer.current); tickTimer.current = null; }
    setCapturing(false); setOpeningCamera(false);
  };

  const startCapture = async (deviceId?: string) => {
    if (openingCamera || (sweepRef.current && !deviceId)) return;
    if (!support.supported) {
      enqueueToast({ level: "warning", title: support.reason ?? "Photosphere capture is unavailable here." });
      return;
    }
    if (!videoRef.current || !canvasRef.current) return;
    sweepRef.current?.stop();
    if (tickTimer.current != null) clearInterval(tickTimer.current);
    const sweep = new PhotosphereSweep();
    sweepRef.current = sweep;
    setAdopted(false); setCaptureError(null); setOpeningCamera(true);
    setCapturing(true);
    tickTimer.current = setInterval(() => setFrameTick((t) => t + 1), 350);
    try {
      await sweep.start(videoRef.current, canvasRef.current, deviceId ?? cameraChoice.current);
      if (sweepRef.current !== sweep) { sweep.stop(); return; }
      setLensAngleDraft(String(sweep.cameraViewAngle));
      if (sweep.activeCameraId) {
        cameraChoice.current = sweep.activeCameraId;
        try { localStorage.setItem("astrodeck.photosphere.camera", sweep.activeCameraId); } catch { /* private browsing */ }
      }
      setOpeningCamera(false);
    } catch (e) {
      if (sweepRef.current !== sweep) return;
      cancelCapture();
      const name = e instanceof Error ? e.name : "";
      if (name === "OverconstrainedError" || name === "NotFoundError") {
        cameraChoice.current = undefined;
        try { localStorage.removeItem("astrodeck.photosphere.camera"); } catch { /* private browsing */ }
      }
      setCaptureError(name === "NotAllowedError"
        ? "Camera access was blocked. Allow it in your browser’s site settings, then try again."
        : name === "NotReadableError" ? "The camera is busy. Close other camera apps, then try again."
        : e instanceof Error ? e.message : "Could not open the camera. Try again or draw the horizon by hand.");
    }
  };

  const stopAndTrace = () => {
    const sweep = sweepRef.current;
    if (!sweep || !sweep.frameCount) return;
    let image: string;
    try { image = sweep.panoramaImage(); }
    catch (error) { setCaptureError(error instanceof Error ? error.message : 'Could not prepare the panorama. Try again.'); return; }
    const proposal = traceSkyCoverage(sweep.columns());
    reviewAz.current=sweep.currentHeading;
    setPanorama(image); setReviewZoom(1); setReviewDraft(true);
    setPhotoStored(false);
    if(photoKey) void writePanorama(photoKey,image).then(setPhotoStored);
    setPoints(proposal.points); setByHand(false); setDirty(true); setSaved(false); onDirty?.();
    setTrace(proposal.points);
    setTraceUncertain(proposal.uncertainBins.length);
    setAlignmentReport(`data:application/json;charset=utf-8,${encodeURIComponent(sweep.alignmentReport())}`);
    setManualOverhead(sweep.usedManualOverhead);
    sweep.stop();
    if (tickTimer.current != null) { clearInterval(tickTimer.current); tickTimer.current = null; }
    sweepRef.current = null;
    setCapturing(false);
  };

  // ------------------------------------------------------------------ render
  const fillD = useMemo(() => buildFillPathD(points), [points]);
  const strokeD = useMemo(() => buildStrokePathD(points), [points]);
  const cardinals: [number, string, "start" | "middle" | "end"][] = [
    [0, "N", "start"], [90, "E", "middle"], [180, "S", "middle"],
    [270, "W", "middle"], [360, "N", "end"],
  ];
  const sweep = sweepRef.current;
  const coverage = Math.floor((sweep?.capturedTiles ?? 0) / (sweep?.totalTiles ?? 91) * 100);
  const scanning = sweep?.isRecording ?? false;
  const compassReady = sweep?.compassReady ?? false;
  const tiltReady = sweep?.tiltReady ?? false;
  const nextBand = sweep?.nextBand ?? 0;
  const elevation = Math.round(sweep?.currentAltitude ?? 0);
  const band = sweep?.currentBand;
  const remaining = sweep?.cells.filter(c=>!c.captured) ?? [];
  const onlyOverhead = remaining.length > 0 && remaining.every(c=>c.alt>78);
  const scanDirection = onlyOverhead ? "Point the rear camera straight up, with the screen facing the ground. It captures automatically. If it doesn’t register, keep it pointing up and tap Capture overhead."
    : `${sweep?.captureCue ?? 'Bring a blue dot into the centre ring.'} Green cells are captured. ${remaining.length} patches left.`;
  const scanHint = openingCamera ? "Opening camera… Allow camera access if your browser asks."
    : sweep?.error ?? (sweep?.complete ? "Overhead captured. The scan is complete—review the horizon next."
    : scanning && onlyOverhead ? scanDirection
    : !compassReady ? "Waiting for the compass. Move the phone gently. If this persists, check motion sensor access in your browser’s site settings, or cancel and draw the horizon."
    : scanning ? scanDirection
    : "Tap Start scan, then hold each blue dot in the centre ring until it turns green. Turn the phone around its camera lens rather than moving it around you. Look around and up to fill the dome.");

  return (
    <Sheet
      title={`HORIZON · ${siteName}`}
      sub={`${summary(points)} · ${byHand ? "by hand" : "from photosphere"}`}
      backLabel="DONE"
      onBack={guided ? undefined : onClose ?? (() => nav.back())}
      right={<ActionButton kind="ghost" size="md" ariaLabel="about the horizon" onPress={() => setInfoOpen((v) => !v)}>?</ActionButton>}
      data-testid="horizon-sheet"
    >
      {infoOpen && (
        <Card data-testid="horizon-info">
          <Mono size={11}>
            The line of trees, roofs and hills as seen from this exact spot. Anything below it is
            unreachable, so the finder, the dome and the ranked list hide it.
          </Mono>
        </Card>
      )}

      {legacy && (
        <Mono size={10.5} tone="warn">
          One horizon, shared by every site - per-site horizons arrive with the next rig update.
        </Mono>
      )}

      <div hidden={capturing}>
      {panorama && <div className="photosphere-review-heading">
        <div><strong>Your surroundings</strong><p>Drag the points to follow the tops of trees and roofs. Save when the line matches your view.</p></div>
        <label>Zoom <input type="range" aria-label="Panorama zoom" min={1} max={4} step={.5} value={reviewZoom} onChange={e=>setReviewZoom(Number(e.target.value))}/></label>
      </div>}
      <div ref={panoramaScroll} className={panorama ? 'photosphere-editor-scroll' : undefined}
        onScroll={e=>{if(capturing)return;const el=e.currentTarget,max=el.scrollWidth-el.clientWidth;setPanPosition(max>0?el.scrollLeft/max*100:0);if(el.scrollWidth)reviewAz.current=(el.scrollLeft+el.clientWidth/2)/el.scrollWidth*360;}}>
      <div style={{ position: "relative", width: panorama ? 1040*reviewZoom : undefined }}>
        <svg
          ref={horizonStrip}
          data-testid="horizon-strip"
          viewBox={`0 0 ${STRIP_W} ${STRIP_H}`}
          preserveAspectRatio={panorama ? 'none' : undefined}
          style={{ width: "100%", maxWidth: panorama ? 'none' : undefined, marginInline: panorama ? 0 : undefined, height: panorama ? 300*reviewZoom : "auto", touchAction: "none", cursor: "crosshair", display: "block" }}
          onPointerDown={onStripPointerDown}
          onPointerMove={onStripPointerMove}
          onPointerUp={onStripPointerUp}
          onPointerCancel={() => { if(dragRef.current) setPoints(dragRef.current.original); dragRef.current=null; }}
          data-locked={writeLocked ? "true" : undefined}
        >
          {panorama && <>
            <defs><pattern id="photosphere-unscanned" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#1a2833"/><rect width="4" height="4" fill="#293b48"/><rect x="4" y="4" width="4" height="4" fill="#293b48"/></pattern></defs>
            <rect x={0} y={SKY_Y} width={STRIP_W} height={HY(-10)-SKY_Y} fill="url(#photosphere-unscanned)"/>
            <image data-testid="horizon-panorama" href={panorama} x={0} y={SKY_Y} width={STRIP_W} height={HY(-10)-SKY_Y} preserveAspectRatio="none"/>
          </>}
          {trace && trace.length > 0 && (
            <polyline
              points={trace.map((p) => `${HX(p.az)},${HY(p.alt)}`).join(" ")}
              fill="none" stroke="var(--accent, #00D2FF)" strokeDasharray="4 3" strokeWidth={1.4} opacity={0.85}
            />
          )}
          <line x1={0} x2={STRIP_W} y1={HY(FLOOR_ALT_DEG)} y2={HY(FLOOR_ALT_DEG)}
            stroke="rgba(255,180,84,.45)" strokeDasharray="2 3" strokeWidth={1} />
          {fillD && <path d={fillD} fill={panorama ? 'rgba(10,12,24,.25)' : "rgba(255,84,112,.22)"} stroke="none" />}
          {strokeD && <path d={strokeD} data-testid="editable-horizon-line" fill="none" stroke={panorama ? '#ffdb8b' : "#ff5470"} strokeWidth={panorama ? 1 : 1.6} />}
          <line x1={0} x2={STRIP_W} y1={GROUND_Y} y2={GROUND_Y} stroke="rgba(140,160,220,.35)" strokeWidth={1} />
          {finderAz != null && (
            <line x1={HX(finderAz)} x2={HX(finderAz)} y1={SKY_Y} y2={GROUND_Y}
              stroke="white" strokeDasharray="2 3" opacity={0.6} />
          )}
          {points.map((p, i) => (
            <circle key={i} data-testid="horizon-edit-point" cx={HX(p.az)} cy={HY(p.alt)} r={panorama ? 3 : 7}
              fill="rgba(6,7,11,.85)" stroke={panorama ? '#ffdb8b' : p.alt < 0 ? "#7683a5" : "#ff5470"} strokeWidth={panorama ? 1 : 1.6} />
          ))}
          {cardinals.map(([az, label, anchor], i) => (
            <text key={i} x={HX(az)} y={9} fontSize={9} textAnchor={anchor} fill="var(--text-faint, #7683a5)">
              {label}
            </text>
          ))}
        </svg>
      </div>
      </div>
      {panorama && <label className="photosphere-pan">Look around <input type="range" aria-label="Panorama position" min={0} max={100} step={.1} value={panPosition}
        onChange={e=>{const value=Number(e.target.value);setPanPosition(value);const el=panoramaScroll.current;if(el)el.scrollLeft=(el.scrollWidth-el.clientWidth)*value/100;}}/></label>}
      <p className="photosphere-detail">Tap to add · drag to move · tap a point to delete{panorama ? ' · Slide Look around to see the rest of the scan. The checkerboard marks areas that weren’t photographed.' : ''}</p>
      <div className="guided-horizon-actions">
        {(guided || reviewDraft) && <ActionButton kind="primary" size="lg" lockedReason={writeLocked} onExplain={explainWrite} onPress={() => void persist(points, true)}><NxIcon name="check" size={20}/>{saving ? "Saving…" : "Save horizon"}</ActionButton>}
        <ActionButton kind="danger" size="md" lockedReason={writeLocked} onExplain={explainWrite} onPress={() => void clearAll()} data-testid="clear-horizon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg>Clear horizon
        </ActionButton>
      </div>
      {(guided || reviewDraft) && <p role="status" className="text-sm text-dim">{dirty ? "Your changes haven't been saved yet." : saved ? "Horizon saved." : "Check the line, then save it. An empty line means you have an unobstructed horizon."}</p>}
      {panorama && <p className="photosphere-detail">{photoStored ? 'This photo is saved in this browser for this site.' : 'This photo is held in the editor while you work; download a copy to keep it.'} The horizon points are saved separately to AstroDeck.</p>}
      </div>

      <canvas ref={canvasRef} style={{ display: "none" }} />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {finderAz != null && (
          <ActionButton kind="secondary" size="md" lockedReason={writeLocked} onExplain={explainWrite}
            onPress={() => { setByHand(true); void persist(insertPoint(points, finderAz, finderAlt)); }}
            data-testid="point-at-finder">
            {`+ POINT AT FINDER · ${Math.round(finderAz)}°`}
          </ActionButton>
        )}
        {!capturing && <ActionButton kind="primary" size="lg"
          lockedReason={writeLocked ?? (support.supported ? null : support.reason)}
          onExplain={(r) => enqueueToast({ level: "warning", title: r })}
          onPress={() => void startCapture()} data-testid="capture-photosphere">
          <NxIcon name="camera" size={22}/>{trace || adopted ? "Scan surroundings again" : "Scan surroundings with camera"}
        </ActionButton>}
      </div>
      {!capturing && <p className="text-sm text-dim">Scan from the telescope’s position and height. Look around and up, including nearby roofs and tall trees. Daylight works best. You can also draw the line by hand above.</p>}

      {captureError && <p role="alert" className="photosphere-error">{captureError}</p>}
      <div ref={capturePanel} tabIndex={-1} className="photosphere-capture" hidden={!capturing} data-scanning={scanning} data-testid="photosphere-capturing">
        <div className="photosphere-preview">
          <video ref={videoRef} muted playsInline autoPlay aria-label="Live surroundings camera" data-testid="photosphere-video" />
          <PhotosphereDome sweep={sweep} active={capturing && !openingCamera}/>
          <div className="photosphere-bearing">{tiltReady ? band === OVERHEAD_BAND ? `${elevation}° up · Overhead` : `${elevation}° up · ${compassReady ? ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((sweep?.currentHeading ?? 0) / 45) % 8] : "Waiting for compass"}` : "Waiting for tilt sensor"}</div>
          {openingCamera && <div className="photosphere-opening">Opening camera…</div>}
        </div>
        <p role="status" className="photosphere-hint">{scanHint}</p>
        <div className="photosphere-sky-coverage" role="progressbar" aria-label="Sky coverage, horizon to overhead" aria-valuemin={0} aria-valuemax={100} aria-valuenow={coverage}>
          <div className="photosphere-progress-track"><span style={{width:`${coverage}%`}}/></div>
        </div>
        <p className="photosphere-detail">{`${coverage}% captured`} · Blue: still to scan. Green: captured. {sweep?.overheadCaptured ? 'Overhead captured' : 'Remember to look overhead.'}</p>
        {!scanning && (sweep?.cameraChoices.length ?? 0) > 0 && <label className="photosphere-camera-choice">Camera
          {openingCamera
            // There is no `readOnly` for a select, and the native `disabled`
            // attribute takes the control AND its reason out of the
            // accessibility tree, so the user cannot focus it to ask why.
            // Locked, it renders as a focusable chip carrying the current lens
            // and the reason (ARCHITECTURE.md non-negotiable 6). `scanning` is
            // already excluded by the guard on this whole block.
            ? <button type="button" className="nx-btn nx-locked" data-kind="secondary"
                aria-disabled="true" data-locked="true" title={CAMERA_LOCKED}
                aria-label={`Camera for horizon scan - ${CAMERA_LOCKED}`}
                onClick={() => enqueueToast({ level: "warning", title: CAMERA_LOCKED })}>
                <span className="nx-btn-label">
                  {sweep?.cameraChoices.find(c => c.deviceId === sweep?.activeCameraId)?.label
                    ?? "Default rear camera"}
                </span>
              </button>
            : <select aria-label="Camera for horizon scan" value={sweep?.activeCameraId ?? ""}
                onChange={e => void startCapture(e.target.value)}>
                {!sweep?.activeCameraId && <option value="">Default rear camera</option>}
                {sweep?.cameraChoices.map(c => <option key={c.deviceId} value={c.deviceId}>{c.label}</option>)}
              </select>}
          <small>Choose another rear camera if this lens is blurry or noisy. Lens names come from your phone.</small>
        </label>}
        {!scanning && !openingCamera && sweep?.previewReady && <>
          {suggestedLens!==null && (!sweep.hasLensCalibration || sweep.cameraViewAngle!==suggestedLens) && <div className="photosphere-error">
            <p>A view angle fitted to your scan is ready to test. Use the same camera lens and zoom. This is a trial correction, not a full camera calibration.</p>
            <ActionButton kind="secondary" size="md" onPress={()=>{sweep.setCameraViewAngle(suggestedLens);setLensAngleDraft(String(sweep.cameraViewAngle));setFrameTick(t=>t+1);}}>Try scan angle · {suggestedLens}°</ActionButton>
          </div>}
          <details className="photosphere-detail"><summary>Camera alignment · {sweep.hasLensCalibration?'saved view angle':'estimated view angle'}</summary>
            <p>The camera’s view angle determines where roofs and trees appear in the scan. This estimate may need calibration for your lens.</p>
            <label>View angle across the short edge (degrees) <input type="number" min={35} max={100} step={.1}
              aria-label="Camera view angle in degrees" value={lensAngleDraft} onChange={e=>setLensAngleDraft(e.target.value)}
              onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}
              onBlur={()=>{sweep.setCameraViewAngle(Number(lensAngleDraft));setLensAngleDraft(String(sweep.cameraViewAngle));setFrameTick(t=>t+1);}}/></label>
            <p>Saved for this camera in this browser. Changing the lens or zoom may require a new measurement.</p>
          </details>
        </>}
        <div className="photosphere-actions">
          {scanning && (onlyOverhead || nextBand === OVERHEAD_BAND) && !sweep?.overheadCaptured && <ActionButton kind="primary" size="lg"
            ariaLabel="Capture overhead with the rear camera pointing straight up"
            onPress={() => { sweep?.captureOverhead(); setFrameTick(t => t + 1); }} data-testid="capture-overhead">
            <NxIcon name="camera" size={22}/>Capture overhead
          </ActionButton>}
          {!scanning ? <ActionButton kind="primary" size="lg" onPress={() => { sweep?.begin(); setFrameTick(t => t + 1); }}
            lockedReason={openingCamera ? "Opening camera" : !compassReady ? "Waiting for compass" : sweep?.error ?? null}
            data-testid="start-horizon-scan">Start scan</ActionButton>
            : <ActionButton kind="primary" size="lg" onPress={stopAndTrace}
              lockedReason={sweep?.frameCount ? null : "Capture a patch of your surroundings first."} data-testid="stop-and-trace">{sweep?.complete ? 'Scan complete' : 'Review partial scan'}</ActionButton>}
          <ActionButton kind="secondary" size="md" onPress={cancelCapture}>Cancel scan</ActionButton>
        </div>
      </div>

      {trace && !capturing && (
        <Card data-testid="photosphere-card">
          <Label>{`PHOTOSPHERE · ${trace.length} points`}</Label>
          <Mono size={11}>
            The gold line is ready to edit. The dashed blue line keeps the original estimate for comparison.
          </Mono>
          <Mono size={10} tone="warn">Camera lens angles vary. Check the estimated heights before saving this line for telescope planning. The line blocks everything below the highest obstruction, including gaps beneath branches or overhangs.</Mono>
          {manualOverhead && <p className="photosphere-detail">You captured overhead by hand. Check that this part of the horizon matches what’s directly above the telescope.</p>}
          {traceUncertain > 0 && <p className="photosphere-error" role="status">{traceUncertain} directions could not be measured reliably. They’re marked blocked up to 90° until you correct them. Rescan in daylight or draw their height by hand.</p>}
          {panorama && <a className="photosphere-download" href={panorama} download="astrodeck-surroundings.png">Download panorama</a>}
          {alignmentReport && <details className="photosphere-detail"><summary>Help diagnose a scrambled image</summary>
            <p>Save a small set of camera pictures and their recorded angles. This stays on your phone unless you choose to share the file. It includes photos of your surroundings.</p>
            <a className="photosphere-download" href={alignmentReport} download="astrodeck-scan-alignment.json">Download alignment report</a>
          </details>}
        </Card>
      )}

      {!capturing && <><Mono size={10.5}>
        Tap the strip to add a point, drag a point to move it, tap a point to delete it. Everything
        under the line is unreachable and is hidden on the finder, the dome and the ranked list.
        Where there is no data, AstroDeck assumes no obstruction.
      </Mono>

      </>}

      {loading && <Mono size={10} tone="dim">loading…</Mono>}
      {saving && <Mono size={10} tone="dim">saving…</Mono>}
    </Sheet>
  );
}
