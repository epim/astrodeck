// preflight.ts — single source of truth for run-readiness derivation
// (onboarding spec §3). Pure + unit-testable; reused by the inline strip, the
// modal, and (later) the safety monitor.

import type {
  CheckItem,
  CheckStatus,
  PreflightAlt,
  RigStatus,
  SequencePlan,
  SiteInfo,
  Target,
} from "../types";

/** In-place fix callbacks the readiness rows can invoke without leaving the view. */
export interface PreflightActions {
  startCooling?: () => void | Promise<void>;
  unpark?: () => void | Promise<void>;
  startGuiding?: () => void | Promise<void>;
}

const WORD: Record<CheckStatus, string> = {
  ok: "READY",
  warn: "WARN",
  blocked: "FIX",
  checking: "CHECKING",
  skipped: "NOT NEEDED",
  disabled: "SET LOCATION",
};

function lightTargets(plan: SequencePlan): Target[] {
  return plan.targets.filter((t) => !t.calibration);
}

function conn(status: RigStatus | null, role: string): boolean {
  return !!status?.connected?.[role]?.connected;
}

/** Build the readiness checklist. Verdict = blocked>warn/disabled>ok. */
export function buildPreflight(
  status: RigStatus | null,
  plan: SequencePlan,
  site: SiteInfo,
  altById: Record<string, PreflightAlt | undefined>,
  actions: PreflightActions = {},
): CheckItem[] {
  const items: CheckItem[] = [];
  const lights = lightTargets(plan);
  const checking = status === null;

  const push = (
    id: string,
    label: string,
    status_: CheckStatus,
    extra?: Partial<CheckItem>,
  ) => items.push({ id, label, status: status_, word: WORD[status_], ...extra });

  // --- camera ---
  if (checking) push("camera", "Camera", "checking");
  else if (conn(status, "camera")) push("camera", "Camera", "ok");
  else
    push("camera", "Camera", "blocked", {
      fix: { label: "Connect", view: "connect" },
    });

  // --- mount ---
  if (checking) push("mount", "Mount", "checking");
  else if (lights.length === 0) push("mount", "Mount", "skipped");
  else if (conn(status, "telescope")) {
    if (status?.mount === undefined)
      push("mount", "Mount", "warn", { detail: { value: "not reporting yet" } });
    else if (status.mount.parked)
      push("mount", "Mount", "ok", { detail: { value: "parked — will unpark on start" } });
    else push("mount", "Mount", "ok");
  } else
    push("mount", "Mount", "blocked", {
      detail: { value: "no telescope connected" },
      fix: { label: "Connect", view: "connect" },
    });

  // --- guiding ---
  if (!plan.guide) push("guiding", "Guiding", "skipped");
  else if (checking) push("guiding", "Guiding", "checking");
  else if (status?.guider)
    push("guiding", "Guiding", "ok", { detail: { value: "will start after slew" } });
  else
    push("guiding", "Guiding", "warn", {
      detail: { value: "no guider — frames will be unguided" },
    });

  // --- cooling ---
  if (plan.cool_to == null) push("cooling", "Cooling", "skipped");
  else if (checking) push("cooling", "Cooling", "checking");
  else if (!status?.camera?.can_cool)
    push("cooling", "Cooling", "blocked", {
      detail: { value: "plan wants cooling; camera can't cool" },
    });
  else {
    const temp = status.camera.temperature;
    const target = plan.cool_to;
    if (temp == null)
      push("cooling", "Cooling", "warn", { detail: { value: "temperature unknown" } });
    else if (Math.abs(temp - target) <= 1.0)
      push("cooling", "Cooling", "ok", {
        detail: { value: `${temp.toFixed(1)}`, unit: "°C" },
      });
    else
      push("cooling", "Cooling", "warn", {
        detail: { value: `${temp.toFixed(1)} → ${target}`, unit: "°C" },
        fix: { label: "Start cooling now", onClick: actions.startCooling, inPlace: true },
      });
  }

  // --- horizon ---
  if (site.is_default)
    push("horizon", "Horizon", "disabled", {
      fix: { label: "Set location", view: "settings" },
    });
  else if (lights.length === 0) push("horizon", "Horizon", "skipped");
  else {
    let blocked: { name: string; alt: number } | null = null;
    let low: { name: string; alt: number } | null = null;
    let anyFetched = false;
    for (const t of lights) {
      const pf = altById[t.name];
      if (!pf) continue;
      anyFetched = true;
      if (pf.verdict === "below" && pf.alt != null) {
        blocked = { name: t.name, alt: pf.alt };
        break;
      }
      if (pf.verdict === "low" && pf.alt != null && !low) low = { name: t.name, alt: pf.alt };
    }
    if (blocked)
      push("horizon", "Horizon", "blocked", {
        detail: { value: `${blocked.name} below horizon (${Math.round(blocked.alt)}°)` },
      });
    else if (low)
      push("horizon", "Horizon", "warn", {
        detail: { value: `${low.name} low (${Math.round(low.alt)}°)` },
      });
    else if (!anyFetched)
      push("horizon", "Horizon", "warn", { detail: { value: "altitude unverified" } });
    else push("horizon", "Horizon", "ok");
  }

  // --- focuser ---
  const wantsAf = plan.targets.some((t) => t.autofocus_first);
  if (checking && (wantsAf || conn(status, "focuser")))
    push("focuser", "Focuser", "checking");
  else if (conn(status, "focuser")) push("focuser", "Focuser", "ok");
  else if (wantsAf)
    push("focuser", "Focuser", "warn", {
      detail: { value: "AF requested, no focuser — will skip" },
    });
  else push("focuser", "Focuser", "skipped");

  // --- filters ---
  const namedFilters = new Set<string>();
  for (const t of plan.targets)
    for (const s of t.steps) if (s.filter) namedFilters.add(s.filter);
  if (namedFilters.size === 0) push("filters", "Filters", "skipped");
  else if (checking) push("filters", "Filters", "checking");
  else {
    const wheel = status?.filterwheel;
    if (!wheel)
      push("filters", "Filters", "warn", {
        detail: { value: "no filter wheel — filter steps ignored" },
      });
    else {
      const have = new Set(wheel.names);
      const missing = [...namedFilters].filter((f) => !have.has(f));
      if (missing.length === 0) push("filters", "Filters", "ok");
      else
        push("filters", "Filters", "blocked", {
          detail: { value: `wheel missing: ${missing.join(", ")}` },
        });
    }
  }

  // --- disk ---
  const disk = status?.disk;
  if (checking) push("disk", "Disk", "checking");
  else if (!disk) push("disk", "Disk", "skipped");
  else if (disk.critical)
    push("disk", "Disk", "blocked", {
      detail: { value: `${disk.free_gb.toFixed(0)}`, unit: "GB free" },
    });
  else if (disk.low)
    push("disk", "Disk", "warn", {
      detail: { value: `${disk.free_gb.toFixed(0)}`, unit: "GB free" },
    });
  else
    push("disk", "Disk", "ok", {
      detail: { value: `${disk.free_gb.toFixed(0)}`, unit: "GB free" },
    });

  return items;
}

/** Overall verdict: blocked if any blocked; else warn if any warn/disabled; else ok. */
export function preflightVerdict(items: CheckItem[]): "ok" | "warn" | "blocked" {
  if (items.some((i) => i.status === "blocked")) return "blocked";
  if (items.some((i) => i.status === "warn" || i.status === "disabled")) return "warn";
  return "ok";
}
