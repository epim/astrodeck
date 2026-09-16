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
import type { SheetProps } from "../../sheets";
import { ActionButton, Card, Label, Mono, Sheet } from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import {
  autoTraceSkyline, insertPoint, movePoint, removePoint, summary,
  type HorizonPoint,
} from "../../../lib/horizonModel";
import { useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { getSite, listLocations, updateLocation, type LocationInput } from "../../../../api/site";
import { setSafetyConfig } from "../../../../api/backends";
import type { SavedLocation } from "../../../../types";
import {
  FLOOR_ALT_DEG, GROUND_Y, HX, HY, SKY_Y, STRIP_H, STRIP_W,
  altFromY, azFromX, buildFillPathD, buildStrokePathD, hitTestPoint, toViewBox,
} from "./horizonStrip";
import {
  PhotosphereSweep, checkPhotosphereSupport, skyLumFromColumns,
} from "./photosphere";

type Source = "location" | "active";

export function HorizonSheet({ params, onClose, onBusyChange }: SheetProps & { onClose?: () => void; onBusyChange?: (busy:boolean) => void }): JSX.Element {
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
  const [infoOpen, setInfoOpen] = useState(false);

  const writeLocked = loading ? "Loading the horizon." : loadFailed ? "Reopen the horizon editor to retry loading before editing." : saving ? "Saving the horizon." : source === "location" ? opticsLocked : safetyLocked;
  const explainWrite = source === "location" ? explainOptics : explainSafety;
  useEffect(()=>{onBusyChange?.(loading||saving);},[loading,saving,onBusyChange]);

  // ------------------------------------------------------------------- load
  useEffect(() => {
    let dead = false;
    const siteParam = params.site;
    (async () => {
      setLoading(true);
      setLoadFailed(false);
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
            return;
          }
        }
        const { site } = await getSite();
        if (dead) return;
        setSource("active");
        setSourceLoc(null);
        setSiteName(site.name?.trim() || "the active site");
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

  // ---------------------------------------------------------------- persist
  const persist = async (next: HorizonPoint[]) => {
    if (writeLocked) { explainWrite(writeLocked); return; }
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
      } else {
        const safety = useStore.getState().config?.safety;
        if (!safety) throw new Error("Safety config not loaded yet - try again.");
        await setSafetyConfig({ ...safety, horizon: next.map((p) => [p.az, p.alt] as [number, number]) });
        await loadConfig();
      }
    } catch (e) {
      setPoints(prev);
      enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not save the horizon." });
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------ pointer i/o
  const dragRef = useRef<{ index: number; moved: boolean } | null>(null);

  const onStripPointerDown = (e: RPointerEvent<SVGSVGElement>) => {
    if (writeLocked) { explainWrite(writeLocked); return; }
    const rect = e.currentTarget.getBoundingClientRect();
    const { x, y } = toViewBox(e.clientX, e.clientY, rect);
    const hit = hitTestPoint(points, x, y);
    if (hit >= 0) {
      dragRef.current = { index: hit, moved: false };
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
      body: "Every point you marked here is deleted. The finder, the dome and the ranked list go back to assuming an open horizon at this site.",
      confirmLabel: "CLEAR", cancelLabel: "KEEP", tone: "danger",
    });
    if (!ok) return;
    await persist([]);
  };

  // ------------------------------------------------------------ photosphere
  const support = useMemo(() => checkPhotosphereSupport(), []);
  const [capturing, setCapturing] = useState(false);
  const [, setFrameTick] = useState(0);
  const [trace, setTrace] = useState<HorizonPoint[] | null>(null);
  const [traceNoTilt, setTraceNoTilt] = useState(false);
  const [adopted, setAdopted] = useState(false);
  const sweepRef = useRef<PhotosphereSweep | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const tickTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    sweepRef.current?.stop();
    sweepRef.current = null;
    if (tickTimer.current != null) clearInterval(tickTimer.current);
  }, []);

  const startCapture = async () => {
    if (sweepRef.current) return;
    if (!support.supported) {
      enqueueToast({ level: "warning", title: support.reason ?? "Photosphere capture is unavailable here." });
      return;
    }
    if (!videoRef.current || !canvasRef.current) return;
    const sweep = new PhotosphereSweep();
    sweepRef.current = sweep;
    setAdopted(false);
    setCapturing(true);
    try {
      await sweep.start(videoRef.current, canvasRef.current);
      if (sweepRef.current !== sweep) { sweep.stop(); return; }
      tickTimer.current = setInterval(() => setFrameTick((t) => t + 1), 400);
    } catch (e) {
      if (sweepRef.current !== sweep) return;
      sweep.stop();
      setCapturing(false);
      sweepRef.current = null;
      enqueueToast({
        level: "error",
        title: e instanceof Error && /denied/i.test(e.message)
          ? "Camera permission denied - MAP mode still works."
          : "No camera on this device - MAP mode still works.",
      });
    }
  };

  const stopAndTrace = () => {
    const sweep = sweepRef.current;
    if (!sweep) return;
    const cols = sweep.columns();
    const skyLum = skyLumFromColumns(cols);
    // altTop/altBottom are always the module's own 90/-10 defaults: this pass
    // bins by compass HEADING only and never reads the device's beta (tilt),
    // so the "assumes a level sweep" disclosure is unconditional, not just a
    // no-orientation-sensor fallback (see the report's photosphere scope note).
    setTrace(autoTraceSkyline(cols, skyLum));
    setTraceNoTilt(true);
    sweep.stop();
    if (tickTimer.current != null) { clearInterval(tickTimer.current); tickTimer.current = null; }
    sweepRef.current = null;
    setCapturing(false);
  };

  const adoptTrace = () => {
    if (!trace) return;
    setByHand(false);
    setAdopted(true);
    void persist(trace);
  };

  // ------------------------------------------------------------------ render
  const fillD = useMemo(() => buildFillPathD(points), [points]);
  const strokeD = useMemo(() => buildStrokePathD(points), [points]);
  const cardinals: [number, string, "start" | "middle" | "end"][] = [
    [0, "N", "start"], [90, "E", "middle"], [180, "S", "middle"],
    [270, "W", "middle"], [360, "N", "end"],
  ];

  return (
    <Sheet
      title={`HORIZON · ${siteName}`}
      sub={`${summary(points)} · ${byHand ? "by hand" : "from photosphere"}`}
      backLabel="DONE"
      onBack={onClose ?? (() => nav.back())}
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

      <div style={{ position: "relative" }}>
        <svg
          data-testid="horizon-strip"
          viewBox={`0 0 ${STRIP_W} ${STRIP_H}`}
          style={{ width: "100%", height: "auto", touchAction: "none", cursor: "crosshair", display: "block" }}
          onPointerDown={onStripPointerDown}
          onPointerMove={onStripPointerMove}
          onPointerUp={onStripPointerUp}
          onPointerCancel={onStripPointerUp}
          data-locked={writeLocked ? "true" : undefined}
        >
          {trace && trace.length > 0 && (
            <polyline
              points={trace.map((p) => `${HX(p.az)},${HY(p.alt)}`).join(" ")}
              fill="none" stroke="var(--accent, #00D2FF)" strokeDasharray="4 3" strokeWidth={1.4} opacity={0.85}
            />
          )}
          <line x1={0} x2={STRIP_W} y1={HY(FLOOR_ALT_DEG)} y2={HY(FLOOR_ALT_DEG)}
            stroke="rgba(255,180,84,.45)" strokeDasharray="2 3" strokeWidth={1} />
          {fillD && <path d={fillD} fill="rgba(255,84,112,.22)" stroke="none" />}
          {strokeD && <path d={strokeD} fill="none" stroke="#ff5470" strokeWidth={1.6} />}
          <line x1={0} x2={STRIP_W} y1={GROUND_Y} y2={GROUND_Y} stroke="rgba(140,160,220,.35)" strokeWidth={1} />
          {finderAz != null && (
            <line x1={HX(finderAz)} x2={HX(finderAz)} y1={SKY_Y} y2={GROUND_Y}
              stroke="white" strokeDasharray="2 3" opacity={0.6} />
          )}
          {points.map((p, i) => (
            <circle key={i} cx={HX(p.az)} cy={HY(p.alt)} r={7}
              fill="rgba(6,7,11,.85)" stroke={p.alt < 0 ? "#7683a5" : "#ff5470"} strokeWidth={1.6} />
          ))}
          {cardinals.map(([az, label, anchor], i) => (
            <text key={i} x={HX(az)} y={9} fontSize={9} textAnchor={anchor} fill="var(--text-faint, #7683a5)">
              {label}
            </text>
          ))}
        </svg>
        <div style={{
          position: "absolute", top: 4, left: 4, fontSize: 10, opacity: 0.85,
          fontFamily: "IBM Plex Mono, monospace", color: "var(--text-dim, #b6c0d8)",
        }}>
          tap to add · drag to move · tap a point to delete
        </div>
      </div>

      <video ref={videoRef} muted playsInline style={{ display: "none" }} />
      <canvas ref={canvasRef} style={{ display: "none" }} />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {finderAz != null && (
          <ActionButton kind="secondary" size="md" lockedReason={writeLocked} onExplain={explainWrite}
            onPress={() => { setByHand(true); void persist(insertPoint(points, finderAz, finderAlt)); }}
            data-testid="point-at-finder">
            {`+ POINT AT FINDER · ${Math.round(finderAz)}°`}
          </ActionButton>
        )}
        <ActionButton kind="ghost" size="md"
          lockedReason={writeLocked ?? (support.supported ? null : support.reason)}
          onExplain={(r) => enqueueToast({ level: "warning", title: r })}
          onPress={() => void startCapture()} data-testid="capture-photosphere">
          {trace || adopted ? "RE-CAPTURE" : "CAPTURE PHOTOSPHERE"}
        </ActionButton>
      </div>

      {capturing && (
        <Card tone="accent" data-testid="photosphere-capturing">
          <Mono size={11}>
            Camera: turn slowly on the spot; the app stitches as you go. Daylight works best - the
            skyline is found by binning.
          </Mono>
          <Mono size={10} tone="dim">
            {`frames ${sweepRef.current?.frameCount ?? 0} · heading ${Math.round(sweepRef.current?.currentHeading ?? 0)}°`}
          </Mono>
          <ActionButton kind="secondary" size="md" onPress={stopAndTrace} data-testid="stop-and-trace">
            STOP AND TRACE
          </ActionButton>
        </Card>
      )}

      {trace && (
        <Card data-testid="photosphere-card">
          <Label>{`PHOTOSPHERE · ${trace.length} points`}</Label>
          <Mono size={11}>
            Skyline found by binning the daytime frames. The dashed line is the proposal; adopt it,
            then nudge points where it missed a branch or a chimney.
          </Mono>
          {traceNoTilt && (
            <Mono size={10} tone="warn">
              No tilt sensor - the trace assumes a level sweep, so nudge the points that look wrong.
            </Mono>
          )}
          <ActionButton kind={adopted ? "secondary" : "primary"} size="md"
            lockedReason={writeLocked} onExplain={explainWrite} onPress={adoptTrace} data-testid="adopt-trace">
            {adopted ? "ADOPTED" : "ADOPT"}
          </ActionButton>
        </Card>
      )}

      <Mono size={10.5}>
        Tap the strip to add a point, drag a point to move it, tap a point to delete it. Everything
        under the red line is unreachable and is hidden on the finder, the dome and the ranked list.
        Where there is no data, AstroDeck assumes no obstruction.
      </Mono>

      <ActionButton kind="danger" size="lg" lockedReason={writeLocked} onExplain={explainWrite}
        onPress={() => void clearAll()} data-testid="clear-horizon">
        {`CLEAR THE HORIZON AT ${siteName.toUpperCase()}`}
      </ActionButton>

      {loading && <Mono size={10} tone="dim">loading…</Mono>}
      {saving && <Mono size={10} tone="dim">saving…</Mono>}
    </Sheet>
  );
}
