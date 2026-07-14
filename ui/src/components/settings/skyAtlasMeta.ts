// skyAtlasMeta.ts — pure helpers for the Settings "Sky Atlas" card (offline-pack
// spec §6). Kept out of the component so the inline-assert test harness (npx
// tsx, no DOM) can exercise them directly.
import type { PackFetchProgress, PackStatus } from "../../types";

export function packProgressPct(f: PackFetchProgress): number {
  return f.total > 0 ? Math.min(100, Math.round((100 * f.done) / f.total)) : 0;
}

export function packStatusLabel(s: PackStatus | null): string {
  if (!s) return "Offline sky pack: checking…";
  if (s.fetching) return `Offline sky pack: downloading ${s.fetching.done}/${s.fetching.total}…`;
  if (!s.present) return "Offline sky pack: not downloaded";
  const mb = s.bytes != null ? `${Math.round(s.bytes / 1048576)} MB` : "size unknown";
  const when = s.fetched_at != null
    ? new Date(s.fetched_at * 1000).toISOString().slice(0, 10) : "unknown date";
  return `Offline sky pack: ${mb}, order ${s.order ?? "?"}, fetched ${when}`;
}
