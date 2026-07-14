// TileEngine.tsx — the WebGL survey layer SkyCanvas mounts when a WebGL probe
// passes (tile-engine spec §4). Fetches raw HiPS tiles from /api/survey/tile,
// warps them through the exact TAN projection, upsamples from parent tiles so it
// never blanks. Pure math lives in healpix.ts / tileView.ts / tileCache.ts; this
// component is the loader queue + rAF draw loop over them.
//
// GL context loss is out of scope for this component (per the tile-engine
// brief): SkyCanvas's WebGL probe gates mounting, and a lost context simply
// stops producing frames until remount.
import { useCallback, useEffect, useRef, type JSX } from "react";
import { u } from "../../lib/base";
import { parentOf } from "../../lib/healpix";
import { tileOrderFor, visibleTiles, tileMesh, ancestorUV } from "../../lib/tileView";
import { planFetches, LruSet, NegativeCache } from "../../lib/tileCache";
import { initTileGL, type TileGL, type TileDraw } from "../../lib/tileGL";

export interface TileEngineProps {
  centerRaDeg: number;
  centerDecDeg: number;
  fovDeg: number;
  slug: string;
  onlineFetch: boolean;
  brightness: number;
  onFirstTile: () => void;
  onAllFailing: () => void;
}

const CONCURRENCY = 6;
const BITMAP_LRU = 256;
const NEG_TTL_MS = 45_000;
const PARENT_WALK = 5;

export function TileEngine(props: TileEngineProps): JSX.Element {
  const {
    centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch, brightness,
    onFirstTile, onAllFailing,
  } = props;

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glRef = useRef<TileGL | null>(null);
  const bitmaps = useRef(new LruSet<ImageBitmap>(BITMAP_LRU, (b) => b.close()));
  const negcache = useRef(new NegativeCache(NEG_TTL_MS));
  const inflight = useRef(new Map<string, AbortController>());
  const dirty = useRef(true);
  const firstDrawn = useRef(false);
  const everDrew = useRef(false);
  const consecFail = useRef(0);
  // False after unmount: a fetch that outraces its abort (e.g. past the body
  // read when cleanup runs) can neither repopulate caches nor fire callbacks.
  const live = useRef(true);
  // Single shared one-shot timer that re-dirties the loop once negative-cache
  // entries expire. Without it, once every visible tile is negative-cached
  // and inflight is empty, the rAF loop has no dirty source left (dirty is
  // only set on init/prop-change/fetch-success/fetch-failure) and idles
  // forever: recovery after the pack/network comes back needs user
  // interaction, and on a static view with <8 visible tiles onAllFailing can
  // never fire. One shared timer (not per-tile) is enough because the +250ms
  // slack guarantees every entry marked while it is pending has expired by
  // the time it fires.
  const wakeTimer = useRef<number | null>(null);

  // Latest view snapshot for the rAF loop (avoids re-subscribing the loop).
  const view = useRef({ centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch });
  view.current = { centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch };

  const keyOf = (order: number, npix: number): string => `${slug}/${order}/${npix}`;

  // Init GL once. If it returns null (should not, given SkyCanvas's probe) the
  // loop simply draws nothing.
  useEffect(() => {
    live.current = true; // re-arm across StrictMode remounts
    const c = canvasRef.current;
    if (c) glRef.current = initTileGL(c);
    dirty.current = true;
    const flightMap = inflight.current;
    const bmp = bitmaps.current;
    return () => {
      live.current = false;
      glRef.current?.dispose();
      glRef.current = null;
      flightMap.forEach((a) => a.abort());
      flightMap.clear();
      bmp.clear();
      if (wakeTimer.current !== null) { window.clearTimeout(wakeTimer.current); wakeTimer.current = null; }
    };
  }, []);

  // Prop changes dirty the scene.
  useEffect(() => { dirty.current = true; }, [centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch]);

  // A survey (slug) change resets first-tile / failure bookkeeping.
  useEffect(() => {
    firstDrawn.current = false;
    everDrew.current = false;
    consecFail.current = 0;
    if (wakeTimer.current !== null) { window.clearTimeout(wakeTimer.current); wakeTimer.current = null; }
  }, [slug]);

  const enqueue = useCallback((order: number, npix: number) => {
    const key = keyOf(order, npix);
    if (bitmaps.current.has(key) || inflight.current.has(key)) return;
    if (negcache.current.blocked(key, performance.now())) return;
    if (inflight.current.size >= CONCURRENCY) return; // retried next dirty frame
    const ac = new AbortController();
    inflight.current.set(key, ac);
    void (async () => {
      try {
        const res = await fetch(u(`/api/survey/tile/${key}.jpg`), { signal: ac.signal });
        if (!res.ok) throw new Error(String(res.status));
        const bmp = await createImageBitmap(await res.blob());
        if (!live.current) { bmp.close(); return; }
        bitmaps.current.set(key, bmp);
        consecFail.current = 0;
        dirty.current = true;
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        if (!live.current) return;
        negcache.current.mark(key, performance.now());
        // Arm the shared wake timer so the loop retries after this entry
        // (and any others marked before it fires) expires from negcache,
        // even with no further user interaction.
        if (wakeTimer.current === null) {
          wakeTimer.current = window.setTimeout(() => {
            wakeTimer.current = null;
            dirty.current = true;
          }, NEG_TTL_MS + 250);
        }
        consecFail.current += 1;
        // Re-dirty so the next frame enqueues the next-priority tiles (the
        // failed key is negative-cached); without this, concurrency (6) would
        // cap the consecutive-failure count below the 8-failure threshold.
        dirty.current = true;
        if (consecFail.current >= 8 && !everDrew.current) onAllFailing();
      } finally {
        inflight.current.delete(key);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, onAllFailing]);

  // rAF dirty-driven draw loop.
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (!dirty.current) return;
      const gl = glRef.current;
      const canvas = canvasRef.current;
      if (!gl || !canvas) return;
      dirty.current = false;

      const v = view.current;
      const dpr = window.devicePixelRatio || 1;
      const cssPx = canvas.clientWidth || 360;
      const sizePx = Math.round(cssPx * dpr);
      if (canvas.width !== sizePx) { canvas.width = sizePx; canvas.height = sizePx; }

      const order = tileOrderFor(v.fovDeg, cssPx);
      const tiles = visibleTiles(v.centerRaDeg, v.centerDecDeg, v.fovDeg, cssPx, order);

      // Fetch plan for this frame: each not-yet-resident visible tile followed
      // by its ancestor chain (up to PARENT_WALK levels, stopping at the first
      // resident ancestor). Ancestors are first-class fetch jobs so a pan into
      // fresh sky whose native tiles 404 (offline, capped pack) still pulls the
      // best-available parent within one round-trip and upsamples it — the
      // Task-4 watch item that the old code only reused ancestors already
      // cached from earlier zoomed-out views.
      const plan = planFetches(
        tiles, order, v.centerRaDeg, v.centerDecDeg,
        (o, n) => bitmaps.current.has(keyOf(o, n)), PARENT_WALK,
      );

      // Abort fetches for keys that left the plan (pans, zooms, and survey
      // switches all cancel stale loads and free their concurrency slots).
      // `want` now covers planned ANCESTOR keys too, so an in-flight parent
      // load queued last frame is not cancelled the very next frame.
      const want = new Set(plan.map(({ order: o, npix: n }) => keyOf(o, n)));
      for (const [k, ac] of inflight.current) {
        if (!want.has(k)) { ac.abort(); inflight.current.delete(k); }
      }

      const draws: TileDraw[] = [];

      for (const npix of tiles) {
        const mesh = tileMesh(order, npix, v.centerRaDeg, v.centerDecDeg, v.fovDeg, cssPx);
        const dpos = new Float32Array(mesh.positions.length);
        for (let i = 0; i < dpos.length; i++) dpos[i] = mesh.positions[i] * dpr;

        const ownKey = keyOf(order, npix);
        const ownBmp = bitmaps.current.get(ownKey);
        if (ownBmp) {
          const tex = gl.texFor(ownKey) ?? gl.uploadTile(ownKey, ownBmp);
          draws.push({ positions: dpos, uvs: mesh.uvs, indices: mesh.indices, texture: tex });
          continue;
        }
        // Walk parents up to 5 levels for the nearest loaded ancestor.
        let p = npix;
        for (let lv = 1; lv <= PARENT_WALK && order - lv >= 0; lv++) {
          p = parentOf(p);
          const ak = keyOf(order - lv, p);
          const ab = bitmaps.current.get(ak);
          if (ab) {
            const tex = gl.texFor(ak) ?? gl.uploadTile(ak, ab);
            draws.push({
              positions: dpos, uvs: ancestorUV(npix, lv, mesh.uvs),
              indices: mesh.indices, texture: tex,
            });
            break;
          }
        }
        // else: no texture at any level -> black this frame (arrives later).
      }

      gl.drawTiles(draws, sizePx);

      // Schedule fetches in plan order: nearest tile then its ancestor chain,
      // then the next tile, etc. (the +15% margin ring is the prefetch set).
      for (const { order: o, npix: n } of plan) enqueue(o, n);

      if (draws.length > 0) {
        everDrew.current = true;
        if (!firstDrawn.current) { firstDrawn.current = true; onFirstTile(); }
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enqueue, onFirstTile]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="absolute inset-0 w-full h-full"
      style={{ filter: `brightness(${brightness})` }}
    />
  );
}
