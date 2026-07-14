// tileCache.ts — pure loader bookkeeping for the tile engine (tile-engine
// spec §4): fetch priority (distance from view center), the per-frame fetch
// plan (visible tiles + ancestor fallbacks), an insertion-ordered LRU
// (Map-backed), and a TTL negative cache. No DOM.
import { parentOf, pixUV2ang } from "./healpix";

const DEG = Math.PI / 180;

/** Angular distance (deg) from a tile's center to the view center — the loader
 *  queue's priority key (smaller = fetch sooner). */
export function tilePriority(
  npix: number, order: number, centerRaDeg: number, centerDecDeg: number,
): number {
  const c = pixUV2ang(order, npix, 0.5, 0.5);
  const a = Math.sin(c.decDeg * DEG) * Math.sin(centerDecDeg * DEG)
    + Math.cos(c.decDeg * DEG) * Math.cos(centerDecDeg * DEG)
      * Math.cos((c.raDeg - centerRaDeg) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, a))) / DEG;
}

/** One fetch job: a tile at (order, npix) — either a visible native tile or an
 *  ancestor fetched as an upsampling fallback. */
export interface FetchJob {
  order: number;
  npix: number;
}

/** Per-frame fetch plan (tile-engine spec §4). Visible tiles are ordered
 *  nearest-first; each not-yet-resident tile is followed IMMEDIATELY by its
 *  parentOf chain (finest→coarsest, up to `maxLevels`, clamped at order 0),
 *  stopping at the first ancestor already resident (a coarser fill exists, so
 *  nothing coarser is worth fetching). Because a tile's ancestors are queued
 *  right after it — not after every other native tile — the nearest tile's
 *  best-available ancestor competes for a concurrency slot in the SAME frame,
 *  so a pan into fresh sky whose native tiles 404 (offline / capped pack) still
 *  pulls a drawable parent within one round-trip. Deduped by (order,npix);
 *  tiles whose own texture is resident contribute nothing. `resident(order,
 *  npix)` = a drawable bitmap is already cached (NOT merely in flight). */
export function planFetches(
  tiles: number[], order: number,
  centerRaDeg: number, centerDecDeg: number,
  resident: (order: number, npix: number) => boolean,
  maxLevels: number,
): FetchJob[] {
  const byPri = [...tiles].sort((a, b) =>
    tilePriority(a, order, centerRaDeg, centerDecDeg)
    - tilePriority(b, order, centerRaDeg, centerDecDeg));
  const plan: FetchJob[] = [];
  const seen = new Set<string>();
  const push = (o: number, n: number): void => {
    const k = `${o}/${n}`;
    if (seen.has(k)) return;
    seen.add(k);
    plan.push({ order: o, npix: n });
  };
  for (const npix of byPri) {
    if (resident(order, npix)) continue; // native tile drawn already — nothing to fetch
    push(order, npix);
    let p = npix;
    for (let lv = 1; lv <= maxLevels && order - lv >= 0; lv++) {
      p = parentOf(p);
      if (resident(order - lv, p)) break; // a drawable fill exists at/above here
      push(order - lv, p);
    }
  }
  return plan;
}

/** Insertion-ordered LRU. get() promotes recency; set() evicts the oldest past
 *  the cap (calling onEvict so callers can free ImageBitmaps / GL textures). */
export class LruSet<V> {
  private map = new Map<string, V>();
  constructor(private cap: number, private onEvict?: (v: V) => void) {}

  has(key: string): boolean {
    return this.map.has(key);
  }

  get(key: string): V | undefined {
    const v = this.map.get(key);
    if (v !== undefined) {
      this.map.delete(key);
      this.map.set(key, v);
    }
    return v;
  }

  set(key: string, v: V): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, v);
    while (this.map.size > this.cap) {
      const oldest = this.map.keys().next().value as string;
      const ev = this.map.get(oldest) as V;
      this.map.delete(oldest);
      this.onEvict?.(ev);
    }
  }

  clear(): void {
    if (this.onEvict) for (const v of this.map.values()) this.onEvict(v);
    this.map.clear();
  }

  get size(): number {
    return this.map.size;
  }
}

/** TTL negative cache: a key marked failed at `now` is blocked until now+ttl.
 *  A lapsed entry is dropped on the read that clears it. */
export class NegativeCache {
  private until = new Map<string, number>();
  constructor(private ttlMs: number) {}

  mark(key: string, now: number): void {
    this.until.set(key, now + this.ttlMs);
  }

  blocked(key: string, now: number): boolean {
    const t = this.until.get(key);
    if (t === undefined) return false;
    if (t <= now) {
      this.until.delete(key);
      return false;
    }
    return true;
  }
}
