// RECONSTRUCTED (lane 1A owns this file) — restored to spec after an isolation
// type-check overwrote the original. 1A's canonical version takes precedence at
// merge. See reliability spec §7.3.
import type {
  BackendLink,
  DiskInfo,
  MeridianInfo,
  NinaHealth,
  RigStatus,
  SafetyState,
  SequenceState,
  WeatherState,
} from "../types";
import type { ProvidersStatus } from "../store";
import type { IconName } from "../components/icons";

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

// ============================================================================
// HEALTH STRIP (implementation brief §5 / doc 03-personas-roles-attention §5 /
// doc 04-failure-2am-ux-spec §6 rec 1). The single "is my night OK?" verdict:
// folds safety, disk, backend_links, meridian, nina_link, status.providers and
// the engine's end_reason into ranked Tier-1 (Notice — persistent amber chip,
// non-blocking) / Tier-2 (Act — sticky red banner, never auto-dismissed)
// issues. Tier-0 (nothing wrong) is the empty array — MonitorView renders one
// calm ambient line for it, nothing loud. Pure + DOM-free (this module never
// touches `window`) so it runs directly with `npx tsx`, same as the polar/
// autofocus verdict suites — no jsdom, no component mount required.
// ============================================================================
export interface HealthIssue {
  tier: 1 | 2;
  icon: IconName;
  text: string;
}

export function deriveHealthIssues(input: {
  safety: SafetyState | null;
  weather?: WeatherState | null;
  disk?: DiskInfo;
  meridian?: MeridianInfo | null;
  ninaLink?: { active: boolean; healthy: boolean; warming_up: boolean } | null;
  backendLinks?: BackendLink[];
  bootConnectFailed?: boolean;
  providers?: ProvidersStatus | null;
  seqState?: SequenceState["state"];
  endReason?: string | null;
  wsConnected: boolean;
  telemetryStale?: boolean;
}): HealthIssue[] {
  const {
    safety,
    weather,
    disk,
    meridian,
    ninaLink,
    backendLinks,
    bootConnectFailed,
    providers,
    seqState,
    endReason,
    wsConnected,
    telemetryStale,
  } = input;
  const issues: HealthIssue[] = [];

  // ---------------------------------------------------------- tier 2 (act)
  if (!wsConnected) {
    issues.push({
      tier: 2,
      icon: "alert",
      text: "Link down — reconnecting; the sequence keeps running locally",
    });
  }
  if (safety?.reading?.stale) {
    issues.push({ tier: 2, icon: "alert", text: "Safety reading stale — treating as unsafe" });
  } else if (safety?.reading && safety.reading.is_safe === false) {
    const why = safety.reading.reason || safety.reading.source || "unknown cause";
    issues.push({ tier: 2, icon: "alert", text: `Unsafe — ${why}` });
  }
  if (disk?.critical) {
    issues.push({
      tier: 2,
      icon: "alert",
      text: `Disk critical — ${disk.free_gb.toFixed(1)} GB free`,
    });
  }
  if (seqState === "aborted" || seqState === "error") {
    const word = seqState === "error" ? "failed" : "aborted";
    issues.push({
      tier: 2,
      icon: "x",
      text: `Sequence ${word}${endReason ? ` — ${endReason}` : ""}`,
    });
  }
  const droppedRole = (backendLinks ?? []).find((l) => l.attempted && !l.ok);
  if (droppedRole) {
    issues.push({ tier: 2, icon: "alert", text: `${droppedRole.role} disconnected` });
  }
  // meridian: "due" is only ever emitted once flip_enabled is true (a GEM with
  // flip disabled reports the DISTINCT `flip_disabled` status instead — see
  // MeridianStatus); the pier-risk case that's actually at risk RIGHT NOW is a
  // disabled flip with the clock already at/under zero.
  if (meridian?.status === "flip_disabled" && (meridian.hours_to_flip ?? 1) <= 0) {
    issues.push({ tier: 2, icon: "alert", text: "Meridian flip due but disabled — pier risk" });
  }

  // ------------------------------------------------------- tier 1 (notice)
  // Telemetry stale (UX-40): socket up but no status frame for a while, so every
  // value on the dashboard is seconds old. Amber notice, matching the
  // ConnectionBanner — so "Night looks OK" no longer contradicts it.
  if (telemetryStale && wsConnected) {
    issues.push({ tier: 1, icon: "clock", text: "Telemetry stale — values may be seconds old" });
  }
  if (disk?.low && !disk.critical) {
    issues.push({
      tier: 1,
      icon: "alert",
      text: `Disk getting low — ${disk.free_gb.toFixed(1)} GB free`,
    });
  }
  if (ninaLink?.active && !ninaLink.healthy && !ninaLink.warming_up) {
    issues.push({ tier: 1, icon: "alert", text: "NINA link unhealthy" });
  }
  if (meridian?.status === "flip_disabled" && (meridian.hours_to_flip ?? 1) > 0) {
    issues.push({ tier: 1, icon: "alert", text: "Meridian flip disabled — pier risk near meridian" });
  }
  if (bootConnectFailed) {
    issues.push({ tier: 1, icon: "info", text: "Rig partially connected on boot — check status below" });
  }
  for (const [cap, label] of [
    ["autofocus", "Autofocus"],
    ["polar_align", "Polar alignment"],
    ["solve", "Plate solving"],
  ] as const) {
    const choice = providers?.[cap];
    if (choice?.kind === "unavailable") {
      issues.push({ tier: 1, icon: "info", text: `${label} unavailable — ${choice.reason}` });
    }
  }
  // weather (sub-project C §10): ADVISORY — always Tier-1 amber, never Tier-2
  // red (red is for safety/disk/link). "Night looks OK" therefore requires no
  // active cloud alert. Non-holders never carry a weather slice at all.
  if (weather?.alert) {
    issues.push({
      tier: 1,
      icon: "alert",
      text: `high cloud forecast tonight (peak ${weather.alert.peak_pct}%)`,
    });
  }

  return issues;
}
