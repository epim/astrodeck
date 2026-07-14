// tileCache.ts — pure loader bookkeeping for the tile engine (tile-engine
// spec §4): fetch priority (distance from view center), an insertion-ordered
// LRU (Map-backed), and a TTL negative cache. No DOM.
import { pixUV2ang } from "./healpix";

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
