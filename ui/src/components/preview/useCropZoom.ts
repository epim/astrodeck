// useCropZoom — debounced, quantized, cached, abortable sensor-1:1 crop fetches.
// (crop+render UI design §2.1, §4.3, risks R2/R3/R4)
//
// This is thin React glue over two PURE libs (lib/cropRoi.ts, lib/clipMask.ts).
// All the geometry lives there and is tested there; this file only owns the
// side effects, and every one of them is a guard against a real hazard:
//
//  R2 — request storms. We (1) never fetch while a gesture is in flight (a
//       DEBOUNCE_MS settle timer restarts on every viewport change), (2) QUANTIZE
//       the ROI to a lattice so a small pan produces the identical URL, (3) abort
//       the in-flight request the moment a newer ROI wins (AbortController), and
//       (4) serve repeats straight out of an LRU cache.
//  R3 — the server keeps the linear array for only the latest 1–2 frames, so a
//       crop CAN 404. On any failure we go quiet and leave the CSS-upscaled base
//       in place: no error chrome, no broken tile, no lie.
//  R4 — decoded crops are viewport-bounded and the cache is capped; object URLs
//       are revoked on eviction and on unmount.
import { useEffect, useMemo, useRef, useState } from "react";
import { u } from "../../lib/base";
import {
  centerSensorRoi,
  cropCacheKey,
  cropQuery,
  quantizeRoi,
  shouldCrop,
  visibleSensorRoi,
  type CropRoi,
  type RoiGeom,
} from "../../lib/cropRoi";

/** Gesture-settle window. Long enough that a pinch never fires mid-flight. */
const DEBOUNCE_MS = 150;
/** LRU cap (decoded crops are viewport-bounded, so this is a few MB at worst). */
const CACHE_MAX = 8;
/** Sensor px the opt-in loupe pulls when the user has NOT zoomed past native. */
const LOUPE_ROI = 256;

export interface CropPixels {
  data: Uint8ClampedArray;
  w: number;
  h: number;
}

export interface CropZoomState {
  /** object URL of the sensor-1:1 crop PNG, or null when none is in hand */
  url: string | null;
  /** the sensor-space ROI that URL covers */
  roi: CropRoi | null;
  /** RGBA of that crop, only when `wantPixels` (the clip mask) asked for it */
  pixels: CropPixels | null;
  /** true while a fetch for a newer ROI is outstanding (used for a subtle fade) */
  loading: boolean;
}

interface CacheEntry {
  url: string;
  roi: CropRoi;
  pixels: CropPixels | null;
}

const EMPTY: CropZoomState = { url: null, roi: null, pixels: null, loading: false };

export function useCropZoom(opts: {
  previewId: number | null;
  geom: RoiGeom;
  /** linear path only — /crop 404s for NINA/pre-stretched frames */
  enabled: boolean;
  /** decode RGBA too (only the clip mask needs it) */
  wantPixels: boolean;
  /** the opt-in 1:1 loupe is open — keep a small centre crop alive even at fit */
  loupe?: boolean;
}): CropZoomState {
  const { previewId, geom, enabled, wantPixels, loupe = false } = opts;

  const [state, setState] = useState<CropZoomState>(EMPTY);
  const cache = useRef<Map<string, CacheEntry>>(new Map());

  // ---- target ROI (pure) ----
  const target = useMemo<CropRoi | null>(() => {
    if (!enabled || previewId == null) return null;
    // Past display-native: the visible box, so the CSS upscale stops being a lie.
    // Otherwise: nothing at all — UNLESS the expert opened the loupe, which wants
    // real pixels without zooming and gets a small bounded centre crop instead.
    const raw = shouldCrop(geom)
      ? visibleSensorRoi(geom)
      : loupe
        ? centerSensorRoi(geom, LOUPE_ROI)
        : null;
    if (!raw) return null;
    return quantizeRoi(raw, geom.dataW, geom.dataH);
  }, [enabled, previewId, geom, loupe]);

  const key = target && previewId != null ? cropCacheKey(previewId, target) : null;

  // ---- drop everything when the frame changes ----
  // The linear array behind /crop is retained for only the latest 1–2 frames, so
  // a crop from an older id is not merely stale, it is unreusable.
  useEffect(() => {
    return () => {
      for (const e of cache.current.values()) URL.revokeObjectURL(e.url);
      cache.current.clear();
      setState(EMPTY);
    };
  }, [previewId]);

  // ---- unmount sweep ----
  useEffect(() => {
    const held = cache.current;
    return () => {
      for (const e of held.values()) URL.revokeObjectURL(e.url);
      held.clear();
    };
  }, []);

  // ---- debounce -> cache -> fetch ----
  useEffect(() => {
    if (key == null || target == null || previewId == null) {
      // zoomed back out (or off the linear path): stop showing a crop at once.
      setState((s) => (s.url == null && !s.loading ? s : EMPTY));
      return;
    }

    const hit = cache.current.get(key);
    if (hit && (!wantPixels || hit.pixels)) {
      touch(cache.current, key, hit);
      setState({ url: hit.url, roi: hit.roi, pixels: hit.pixels, loading: false });
      return;
    }

    setState((s) => (s.loading ? s : { ...s, loading: true }));

    const ac = new AbortController();
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          let entry = cache.current.get(key);
          if (!entry) {
            const res = await fetch(u(`/api/preview/${previewId}/crop${cropQuery(target)}`), {
              signal: ac.signal,
            });
            // R3: a 404 here is EXPECTED (linear expired / NINA frame). Degrade
            // silently to the CSS-upscaled base — never surface an error.
            if (!res.ok) throw new Error(`crop ${res.status}`);
            const blob = await res.blob();
            if (cancelled) return;
            entry = { url: URL.createObjectURL(blob), roi: target, pixels: null };
            put(cache.current, key, entry);
          }
          if (wantPixels && !entry.pixels) {
            entry.pixels = await decodePixels(entry.url);
          }
          if (cancelled) return;
          setState({ url: entry.url, roi: entry.roi, pixels: entry.pixels, loading: false });
        } catch {
          if (cancelled) return;
          // keep whatever we already show; just stop claiming to be loading.
          setState((s) => ({ ...s, loading: false }));
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      ac.abort(); // supersede: a newer ROI cancels the older request
      clearTimeout(timer);
    };
  }, [key, target, previewId, wantPixels]);

  return state;
}

/** move-to-end (most recently used) */
function touch(map: Map<string, CacheEntry>, key: string, entry: CacheEntry) {
  map.delete(key);
  map.set(key, entry);
}

function put(map: Map<string, CacheEntry>, key: string, entry: CacheEntry) {
  map.set(key, entry);
  while (map.size > CACHE_MAX) {
    const oldest = map.keys().next();
    if (oldest.done) break;
    const victim = map.get(oldest.value);
    map.delete(oldest.value);
    if (victim) URL.revokeObjectURL(victim.url);
  }
}

/** Decode the crop into RGBA for the per-pixel clip mask. Viewport-bounded, so
 *  this is a few hundred k px at worst — no worker needed. */
async function decodePixels(url: string): Promise<CropPixels | null> {
  const img = new Image();
  img.decoding = "async";
  img.src = url;
  try {
    if (img.decode) await img.decode();
    else await new Promise<void>((res, rej) => {
      img.onload = () => res();
      img.onerror = () => rej(new Error("decode"));
    });
  } catch {
    return null;
  }
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  if (!w || !h) return null;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(img, 0, 0);
  try {
    const id = ctx.getImageData(0, 0, w, h);
    return { data: id.data, w, h };
  } catch {
    return null; // tainted canvas / OOM — the frame-level indicator still holds
  }
}
