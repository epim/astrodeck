// coach.ts — F-G-1: pure `hasSeen` helpers (first-run wizard design spec §3,
// Task 1). Persisted localStorage key `astrodeck-coach-seen` holds a JSON
// object `{ [key]: true }`. Kept DOM-free/pure so it's testable via plain
// `npx tsx` (no jsdom in this repo) — the store slice (store.ts) is the only
// thing that touches `localStorage` directly, mirroring the `autoMonitor`
// persist template (store.ts AUTO_MONITOR_KEY).

export type SeenMap = Record<string, true>;
export const COACH_SEEN_KEY = "astrodeck-coach-seen";
export const WIZARD_SEEN_KEY = "first-run-wizard";

export function parseSeen(raw: string | null): SeenMap {
  if (!raw) return {};
  try {
    const o = JSON.parse(raw) as unknown;
    if (!o || typeof o !== "object" || Array.isArray(o)) return {};
    const out: SeenMap = {};
    for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
      if (v === true) out[k] = true;
    }
    return out;
  } catch {
    return {};
  }
}
export function serializeSeen(m: SeenMap): string { return JSON.stringify(m); }
export function withSeen(m: SeenMap, key: string): SeenMap {
  return m[key] ? m : { ...m, [key]: true };
}

export interface Box { left: number; top: number; width: number; height: number }
export function spotlightRect(target: Box, pad = 8): Box {
  return {
    left: target.left - pad, top: target.top - pad,
    width: target.width + pad * 2, height: target.height + pad * 2,
  };
}
