// RECONSTRUCTED (lane 1A owns this file) — restored to spec after an isolation
// type-check overwrote the original. 1A's canonical version takes precedence at
// merge. See reliability spec §7.3.
import type { NinaHealth, RigStatus } from "../types";

/** Pure: derive the NINA link health from the latest status frame. */
export function deriveNinaHealth(status: RigStatus | null): NinaHealth {
  const nl = status?.nina_link;
  if (status?.mode !== "nina" || !nl?.active) {
    return { active: false, ageMs: null, state: "na", lastError: null };
  }
  if (nl.warming_up) {
    return { active: true, ageMs: null, state: "warming", lastError: null };
  }
  if (nl.last_error && (nl.last_ok_age_s == null || nl.last_ok_age_s > 15)) {
    return {
      active: true,
      ageMs: nl.last_ok_age_s != null ? nl.last_ok_age_s * 1000 : null,
      state: "error",
      lastError: nl.last_error,
    };
  }
  if (nl.last_ok_age_s == null) {
    return { active: true, ageMs: null, state: "down", lastError: nl.last_error };
  }
  const ageMs = nl.last_ok_age_s * 1000;
  return {
    active: true,
    ageMs,
    state: nl.healthy ? "ok" : "stale",
    lastError: nl.last_error,
  };
}

/** Human-readable tooltip for the NINA LED. */
export function ninaTitle(h: NinaHealth): string {
  if (!h.active) return "NINA bridge inactive";
  const age = h.ageMs != null ? `${Math.round(h.ageMs / 1000)} s ago` : "never";
  switch (h.state) {
    case "ok": return `NINA link healthy (last reply ${age})`;
    case "warming": return "NINA connecting…";
    case "stale": return `NINA telemetry slow (last reply ${age})`;
    case "error": return `NINA error: ${h.lastError ?? "unknown"}`;
    case "down": return `No NINA reply (last reply ${age})`;
    default: return "NINA";
  }
}
