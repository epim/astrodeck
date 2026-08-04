// preflight.ts — single source of truth for run-readiness derivation
// (onboarding spec §3). Pure + unit-testable; reused by the inline strip and
// the modal. Includes the safety-monitor row (UX review #2) — see below.

import type {
  CheckItem,
  CheckStatus,
  PreflightAlt,
  RigStatus,
  SequencePlan,
  SiteInfo,
  Target,
} from "../types";
import { isStepExposureInvalid } from "./exposure";
import { planCalibrationGaps, type MasterRow } from "./calibrationLibrary";

/** In-place fix callbacks the readiness rows can invoke without leaving the view. */
export interface PreflightActions {
  startCooling?: () => void | Promise<void>;
  unpark?: () => void | Promise<void>;
  startGuiding?: () => void | Promise<void>;
}

// --- cooling set-point bounds (UX round-4 S4) --------------------------------
// A set-point outside this range is a typo, not a plan: it is the SAME range the
// manual cooler field on Capture already enforces (CaptureView COOLER_MIN_C /
// COOLER_MAX_C), and the plan editor never had one — so the field that decides a
// whole night was the looser of the two. It also catches anything below absolute
// zero, which is exactly what the plan editor's numeric field produces while you
// edit it ("-10" becomes "-1010" because the field cannot be emptied).
const SETPOINT_MIN_C = -60;
const SETPOINT_MAX_C = 40;
/** A thermoelectric cooler pulls roughly 35–45 °C below ambient; past that the
 *  set-point is not reachable from the temperature the sensor is reading now. */
const MAX_COOLER_DELTA_C = 45;

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
  masters: MasterRow[] = [],
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

  // --- safety (UX REVIEW #2) -------------------------------------------------
  // FIRST row, because it is the go/no-go input: preflight used to report a
  // green READY while `/api/safety/state` said `is_safe:false, "rain detected"`,
  // and the run it green-lit produced two frames under rain before the engine's
  // 3-poll debounce paused it. A go/no-go screen that omits the go/no-go input
  // is worse than no screen.
  //
  // `status.safety` is the FLAT server SafetyReading forwarded on every 2s poll
  // (types.ts:113) — the same reading the engine's own gate reads, so the row
  // and the engine can never disagree. Fail-CLOSED exactly like that gate: a
  // stale/non-reporting monitor is UNSAFE, not "probably fine". Mirrors the
  // server's `unsafe` blocking warning in POST /api/sequence/preflight.
  //
  // Escape hatch (house rule: a block must always be defeatable by an explicit,
  // honest action, never by pretending): the row is gated on `plan.safety_check`,
  // so a user who deliberately runs without the safety gate turns THAT off and
  // the row reads NOT NEEDED instead of blocking. No monitor connected = no row
  // opinion (skipped) — most rigs have no safety device and must not be nagged.
  const safety = status?.safety;
  if (!plan.safety_check)
    push("safety", "Safety", "skipped", {
      // Names what is off, because the toggle only governs the WEATHER gate —
      // the mount's altitude floor, zenith keep-out and pier guard run on every
      // slew regardless (engine._safety_gate). "Safety gate off" read as though
      // the rig were entirely unguarded, which was both alarming and untrue.
      detail: { value: "weather gate off in this plan (mount limits still apply)" },
    });
  else if (checking) push("safety", "Safety", "checking");
  else if (safety == null)
    push("safety", "Safety", "skipped", { detail: { value: "no safety monitor" } });
  else if (safety.stale)
    push("safety", "Safety", "blocked", {
      detail: { value: "monitor not reporting — treated as UNSAFE" },
      fix: { label: "Safety settings", view: "settings" },
    });
  else if (!safety.is_safe)
    push("safety", "Safety", "blocked", {
      detail: { value: `UNSAFE — ${safety.reason || safety.source || "unsafe condition"}` },
      fix: { label: "Safety settings", view: "settings" },
    });
  else
    push("safety", "Safety", "ok", {
      detail: { value: safety.source ? `${safety.source} reports safe` : "conditions safe" },
    });

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
  // UX round-4 S4 ("success reported before it is earned"). A set-point the
  // camera cannot reach used to be a WARN, so the run started, cooled for the
  // full cool_timeout_s, and then — with the default escalation (require_cooling
  // off) — shot lights at whatever temperature the sensor happened to be at. Ten
  // minutes of a clear night and a night of mismatched darks, with nothing on
  // screen having said so. Two of those cases are knowable BEFORE the run:
  //   · a set-point outside what a cooled camera can hold (or below absolute
  //     zero — the plan editor's numeric field turns -10 into -1010 mid-edit)
  //     is not a warning, it is a value to fix, so it BLOCKS;
  //   · a set-point further below the CURRENT sensor temperature than a TEC can
  //     pull stays a warning — ambient falls overnight and cameras differ — but
  //     it now states the deadline and what happens when it expires, instead of
  //     printing a target that reads like a promise.
  const coolTimeoutMin = Math.max(1, Math.round((plan.cool_timeout_s || 600) / 60));
  if (plan.cool_to == null) push("cooling", "Cooling", "skipped");
  else if (checking) push("cooling", "Cooling", "checking");
  else if (!status?.camera?.can_cool)
    push("cooling", "Cooling", "blocked", {
      detail: { value: "plan wants cooling; camera can't cool" },
    });
  else if (
    !Number.isFinite(plan.cool_to)
    || plan.cool_to < SETPOINT_MIN_C
    || plan.cool_to > SETPOINT_MAX_C
  )
    push("cooling", "Cooling", "blocked", {
      detail: {
        value: `${plan.cool_to} °C is outside what a cooled camera can hold (${SETPOINT_MIN_C} to ${SETPOINT_MAX_C} °C)`,
      },
    });
  else {
    const temp = status.camera.temperature;
    const target = plan.cool_to;
    const coolerOff = !status.camera.cooler?.on;
    if (temp == null)
      push("cooling", "Cooling", "warn", { detail: { value: "temperature unknown" } });
    else if (Math.abs(temp - target) <= 1.0)
      push("cooling", "Cooling", "ok", {
        detail: { value: `${temp.toFixed(1)}`, unit: "°C" },
      });
    else if (coolerOff && target < temp - MAX_COOLER_DELTA_C)
      // The sensor is sitting at ambient (cooler off), so this delta is the real
      // one the TEC would have to pull.
      push("cooling", "Cooling", "warn", {
        detail: {
          value: `${temp.toFixed(1)} → ${target} °C is ${Math.round(temp - target)}° of cooling; a cooler pulls about ${MAX_COOLER_DELTA_C}°. After ${coolTimeoutMin} min the run shoots at whatever it reached`,
        },
        fix: { label: "Start cooling now", onClick: actions.startCooling, inPlace: true },
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

  // --- calibration (PRO-1) --- coverage of the plan's lights against the built
  // master library. Skipped when no masters exist (never nag an empty library —
  // D4) or the plan has no lights. A gap is a non-blocking WARN (the modal
  // defaults to "Run anyway"); a missing master never prevents a run. Mirrors
  // matcher.py via planCalibrationGaps (Open Decision D3) so the row re-checks
  // live as the plan is edited, using the CalibrationConfig defaults.
  const lightsForCal = lightTargets(plan);
  if (masters.length === 0 || lightsForCal.length === 0)
    push("calibration", "Calibration", "skipped");
  else {
    const { missing } = planCalibrationGaps(plan, masters, {
      exposureTolPct: 5,
      tempTolC: 2,
    });
    if (missing.length === 0) push("calibration", "Calibration", "ok");
    else
      push("calibration", "Calibration", "warn", {
        detail: { value: `no ${missing.join(" · ")}` },
      });
  }

  // --- exposure --- (F-P0.1 follow-up: a 0/negative/absurd per-step exposure
  // sailed through the step editor with zero validation — unlike CaptureView's
  // manual field — and only 422'd at Run, surfacing as a raw error blob
  // (apiError.ts). Block Run here instead, before the request is even built;
  // reuses the shared bounds (isStepExposureInvalid/EXPOSURE_MAX_S), which
  // are frame-type aware so a legitimate 0s BIAS step never blocks a run
  // so the two surfaces can never silently disagree on what's valid.
  const badExposureSteps = plan.targets.flatMap((t) =>
    t.steps
      .filter((s) => isStepExposureInvalid(s.exposure_s, s.frame_type))
      .map((s) => `${t.name}${s.filter ? ` (${s.filter})` : ""}: ${s.exposure_s}s`),
  );
  if (badExposureSteps.length > 0)
    push("exposure", "Exposure", "blocked", {
      detail: {
        value:
          badExposureSteps.length === 1
            ? badExposureSteps[0]
            : `${badExposureSteps.length} steps out of range`,
      },
    });
  else push("exposure", "Exposure", "ok");

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
