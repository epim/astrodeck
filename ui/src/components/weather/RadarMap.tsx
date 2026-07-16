// RadarMap.tsx — mini slippy radar/satellite map + scope-pointing overlay
// (weather spec §11). From scratch (no map lib): absolutely-positioned <img>
// grid over the SERVER-SIDE IEM proxy (/api/weather/tile/... — the browser
// NEVER contacts IEM), pure Web-Mercator math from lib/mercator.ts, gestures
// ported from SkyCanvas (drag-pan, native non-passive wheel trap, keyboard).
// Night mode: the tile layer gets filter var(--img-filter) directly, exactly
// as survey imagery is dimmed (index.css:115). Broken tiles hide themselves
// (B thumb-fallback idiom). Controls are word-labeled — never hue alone.
import { useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as RKeyboardEvent,
  PointerEvent as RPointerEvent,
} from "react";
import { useSite, useStore } from "../../store";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { u } from "../../lib/base";
import {
  CLOUD_DECKS_KM,
  clampLat,
  clampZoom,
  destPoint,
  latToTileY,
  lonToTileX,
  pierceDistanceKm,
  TILE_SIZE,
  tileXToLon,
  tileYToLat,
} from "../../lib/mercator";

const MAP_H = 320; // px cell height; width tracks the panel
const RADAR_TTL_S = 240; // matches the server radar TTL (spec §6/§11)

type Layer = "radar" | "satellite";

/** Narrow wedge from (x,y) along a compass bearing (deg; 0 = north = up on a
 *  north-up map), fixed 42 px screen length, ±7° half-angle. */
function wedgePath(x: number, y: number, bearingDeg: number): string {
  const len = 42;
  const tip = (angDeg: number) => {
    const a = ((angDeg - 90) * Math.PI) / 180; // bearing 0 -> screen-up
    return { x: x + len * Math.cos(a), y: y + len * Math.sin(a) };
  };
  const l = tip(bearingDeg - 7);
  const r = tip(bearingDeg + 7);
  return `M${x},${y} L${l.x.toFixed(1)},${l.y.toFixed(1)} L${r.x.toFixed(1)},${r.y.toFixed(1)} Z`;
}

export default function RadarMap() {
  const site = useSite();
  const mount = useStore((s) => s.status?.mount);
  const siteLat = typeof site?.latitude === "number" ? site.latitude : null;
  const siteLon = typeof site?.longitude === "number" ? site.longitude : null;

  const [layer, setLayer] = useState<Layer>("radar");
  const [zoom, setZoom] = useState(7);
  const [center, setCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [bust, setBust] = useState(() => Math.floor(Date.now() / (RADAR_TTL_S * 1000)));
  const [width, setWidth] = useState(0);
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  const boxRef = useRef<HTMLDivElement>(null);

  // Center defaults to the site; the recenter button returns to it (spec §11).
  useEffect(() => {
    if (center === null && siteLat !== null && siteLon !== null) {
      setCenter({ lat: siteLat, lon: siteLon });
    }
  }, [center, siteLat, siteLon]);

  // Track the rendered width (mosaic cells are fluid).
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // ---- pointer drag-pan (SkyCanvas idiom, in the mercator tile plane) ----
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startCenter: { lat: number; lon: number };
  } | null>(null);

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    if (!center) return;
    try {
      boxRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current = { startX: e.clientX, startY: e.clientY, startCenter: center };
  };
  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const n = Math.pow(2, zoom);
    let cx = lonToTileX(d.startCenter.lon, zoom) - (e.clientX - d.startX) / TILE_SIZE;
    let cy = latToTileY(d.startCenter.lat, zoom) - (e.clientY - d.startY) / TILE_SIZE;
    cx = ((cx % n) + n) % n; // wrap the antimeridian
    cy = Math.max(0, Math.min(n, cy)); // clamp at the mercator poles
    setCenter({ lat: clampLat(tileYToLat(cy, zoom)), lon: tileXToLon(cx, zoom) });
  };
  const onPointerUp = (e: RPointerEvent<HTMLDivElement>) => {
    try {
      boxRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current = null;
  };

  // ---- wheel zoom (native, non-passive — the SkyCanvas page-scroll trap:
  // React registers synthetic onWheel PASSIVE, so preventDefault would be
  // ignored and the page would scroll under the map). Refs keep the handler
  // current without re-attaching per zoom change.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault(); // honored: registered with passive: false
      setZoom(clampZoom(zoomRef.current + (e.deltaY > 0 ? -1 : 1)));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  // ---- keyboard nudge/zoom ----
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    if (!center) return;
    const stepPx = 64;
    let dx = 0;
    let dy = 0;
    switch (e.key) {
      case "ArrowLeft": dx = -stepPx; break;
      case "ArrowRight": dx = stepPx; break;
      case "ArrowUp": dy = -stepPx; break;
      case "ArrowDown": dy = stepPx; break;
      case "+":
      case "=":
        setZoom(clampZoom(zoom + 1));
        e.preventDefault();
        return;
      case "-":
        setZoom(clampZoom(zoom - 1));
        e.preventDefault();
        return;
      default:
        return;
    }
    e.preventDefault();
    const n = Math.pow(2, zoom);
    let cx = lonToTileX(center.lon, zoom) + dx / TILE_SIZE;
    let cy = latToTileY(center.lat, zoom) + dy / TILE_SIZE;
    cx = ((cx % n) + n) % n;
    cy = Math.max(0, Math.min(n, cy));
    setCenter({ lat: clampLat(tileYToLat(cy, zoom)), lon: tileXToLon(cx, zoom) });
  };

  // ---- tile grid (ceil(viewport/256)+1 overscan; broken tiles hidden) ----
  const tiles: { key: string; id: string; src: string; left: number; top: number }[] = [];
  if (center && width > 0) {
    const n = Math.pow(2, zoom);
    const cx = lonToTileX(center.lon, zoom);
    const cy = latToTileY(center.lat, zoom);
    const x0 = Math.floor(cx - width / (2 * TILE_SIZE)) - 1;
    const x1 = Math.floor(cx + width / (2 * TILE_SIZE)) + 1;
    const y0 = Math.floor(cy - MAP_H / (2 * TILE_SIZE)) - 1;
    const y1 = Math.floor(cy + MAP_H / (2 * TILE_SIZE)) + 1;
    for (let tx = x0; tx <= x1; tx++) {
      for (let ty = y0; ty <= y1; ty++) {
        if (ty < 0 || ty >= n) continue;
        const wx = ((tx % n) + n) % n;
        const id = `${layer}/${zoom}/${wx}/${ty}?${bust}`;
        if (broken[id]) continue;
        tiles.push({
          key: `${tx}:${ty}:${layer}:${bust}`,
          id,
          // proxy only — cache-busting query rolls with the server TTL
          src: u(`/api/weather/tile/${layer}/${zoom}/${wx}/${ty}.png?t=${bust}`),
          left: Math.round((tx - cx) * TILE_SIZE + width / 2),
          top: Math.round((ty - cy) * TILE_SIZE + MAP_H / 2),
        });
      }
    }
  }

  // ---- overlay projection (px within the box) ----
  const toPx = (lat: number, lon: number): { x: number; y: number } | null => {
    if (!center || width === 0) return null;
    const n = Math.pow(2, zoom);
    let dx = lonToTileX(lon, zoom) - lonToTileX(center.lon, zoom);
    if (dx > n / 2) dx -= n; // shortest wrap
    if (dx < -n / 2) dx += n;
    const dy = latToTileY(clampLat(lat), zoom) - latToTileY(center.lat, zoom);
    return { x: dx * TILE_SIZE + width / 2, y: dy * TILE_SIZE + MAP_H / 2 };
  };

  const hasPointing =
    !!mount && Number.isFinite(mount.alt) && Number.isFinite(mount.az);
  const sitePx = siteLat !== null && siteLon !== null ? toPx(siteLat, siteLon) : null;

  // Sight-line pierce points (spec §11): where the line of sight crosses each
  // cloud deck, at destPoint(site, az, deck_km / tan(alt)). HIGH is the
  // primary marker — the exact radar/satellite pixel that decides whether
  // subs survive; low/mid are dots on the same ray. Ray length IS the
  // inclination readout, physically.
  const pierce: { deck: "low" | "mid" | "high"; x: number; y: number }[] = [];
  if (hasPointing && siteLat !== null && siteLon !== null && mount) {
    for (const deck of ["low", "mid", "high"] as const) {
      const dist = pierceDistanceKm(mount.alt, CLOUD_DECKS_KM[deck]);
      if (dist === null) continue; // clamp: <3° alt or >150 km hidden
      const p = destPoint(siteLat, siteLon, mount.az, dist);
      const px = toPx(p.lat, p.lon);
      if (px) pierce.push({ deck, ...px });
    }
  }
  const farthest = pierce.length > 0 ? pierce[pierce.length - 1] : null;

  return (
    <Panel className="col-span-full lg:col-span-6" title="Radar">
      <div className="data-dim flex flex-col gap-2">
        {/* controls: word-labeled buttons + readout chip (hue-free) */}
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          {(["radar", "satellite"] as const).map((l) => (
            <button
              key={l}
              type="button"
              className={`btn !py-0.5 text-[11px] ${layer === l ? "btn-accent" : ""}`}
              aria-pressed={layer === l}
              onClick={() => {
                setLayer(l);
                setBroken({});
              }}
            >
              {l === "radar" ? "Radar" : "IR satellite"}
            </button>
          ))}
          <button
            type="button"
            className="btn !py-0.5 text-[11px]"
            onClick={() => {
              setBust(Math.floor(Date.now() / (RADAR_TTL_S * 1000)));
              setBroken({});
            }}
          >
            <Icon name="refresh" size={11} className="inline mr-1" />
            refresh
          </button>
          <button
            type="button"
            className="btn !py-0.5 text-[11px]"
            disabled={siteLat === null}
            onClick={() => {
              if (siteLat !== null && siteLon !== null) {
                setCenter({ lat: siteLat, lon: siteLon });
              }
            }}
          >
            recenter
          </button>
          <span className="mono text-dim border border-line px-1.5 py-0.5">
            {hasPointing && mount
              ? `Az ${Math.round(mount.az)}° · Alt ${Math.round(mount.alt)}°`
              : "no mount"}
          </span>
        </div>

        <div
          ref={boxRef}
          className="relative overflow-hidden outline-none border border-line select-none touch-none"
          style={{ height: MAP_H, cursor: "grab" }}
          tabIndex={0}
          role="application"
          aria-label="Radar map around the observing site (drag to pan, wheel or +/- to zoom, arrows to nudge)"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
        >
          {/* tile layer — night-dimmed exactly like survey imagery (spec §11) */}
          <div className="absolute inset-0" style={{ filter: "var(--img-filter)" }} aria-hidden>
            {tiles.map((t) => (
              <img
                key={t.key}
                src={t.src}
                alt=""
                draggable={false}
                width={TILE_SIZE}
                height={TILE_SIZE}
                style={{ position: "absolute", left: t.left, top: t.top, maxWidth: "none" }}
                onError={() => setBroken((b) => ({ ...b, [t.id]: true }))}
              />
            ))}
          </div>

          {/* scope location + orientation overlay (spec §11) */}
          <svg
            className="absolute inset-0 pointer-events-none"
            width={width}
            height={MAP_H}
            aria-hidden
          >
            {sitePx && (
              <g>
                {/* site marker: scope glyph (circle + tripod stem) */}
                <circle cx={sitePx.x} cy={sitePx.y} r={5} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
                <line x1={sitePx.x} y1={sitePx.y + 5} x2={sitePx.x} y2={sitePx.y + 11} stroke="var(--accent)" strokeWidth={1.6} />
                {/* azimuth wedge (map is north-up) */}
                {hasPointing && mount && (
                  <path d={wedgePath(sitePx.x, sitePx.y, mount.az)} fill="var(--accent)" opacity={0.35} />
                )}
                {/* dashed sight-line ray to the farthest visible pierce point */}
                {farthest && (
                  <line
                    x1={sitePx.x}
                    y1={sitePx.y}
                    x2={farthest.x}
                    y2={farthest.y}
                    stroke="var(--warn)"
                    strokeWidth={1}
                    strokeDasharray="4 3"
                  />
                )}
                {pierce.map((p) =>
                  p.deck === "high" ? (
                    <g key={p.deck}>
                      {/* PRIMARY: crosshair on the high deck */}
                      <line x1={p.x - 7} y1={p.y} x2={p.x + 7} y2={p.y} stroke="var(--warn)" strokeWidth={1.4} />
                      <line x1={p.x} y1={p.y - 7} x2={p.x} y2={p.y + 7} stroke="var(--warn)" strokeWidth={1.4} />
                      <circle cx={p.x} cy={p.y} r={4} fill="none" stroke="var(--warn)" strokeWidth={1.4} />
                      <text x={p.x + 9} y={p.y - 6} fill="var(--warn)" fontSize={10} fontFamily="IBM Plex Mono">
                        high cloud
                      </text>
                    </g>
                  ) : (
                    <circle key={p.deck} cx={p.x} cy={p.y} r={2.5} fill="var(--warn)" opacity={0.8} />
                  ),
                )}
              </g>
            )}
          </svg>
        </div>

        <p className="text-[10px] text-dim">
          Radar/satellite: Iowa Environmental Mesonet · radar is ~5 min delayed
        </p>
      </div>
    </Panel>
  );
}
