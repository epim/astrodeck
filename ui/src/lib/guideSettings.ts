// guideSettings.ts — guide-algorithm pick lists + dossier §15 defaults for
// the native guider's per-axis algorithm selection (RA/Dec).
//
// Canonical algorithm-kind strings match the Rust/PyO3 wiring exactly
// (native/crates/astrodeck-native/src/lib.rs `parse_algo_kind`;
// server/astrodeck/guide/native.py `_build_engine_config`'s
// `ra_algorithm`/`dec_algorithm` config keys): "hysteresis" |
// "resist_switch" | "lowpass" | "lowpass2" | "z_filter" | "ppec". PPEC is
// RA-only (dossier §6.8; PHD2 `mount.cpp:227-240` — present in
// `RA_ALGORITHMS`, absent from `DEC_ALGORITHMS`/`AO_ALGORITHMS`), so it is
// omitted from DEC_GUIDE_ALGORITHMS below.
//
// No per-algorithm PARAMETER override exists yet in the engine config
// surface — only the algorithm KIND flows through
// EngineConfig::ra_algorithm/dec_algorithm today (every algorithm
// constructs at its dossier §15 default). GUIDE_ALGORITHM_DEFAULTS is
// display-only until a settings-editor UI and a matching config-key
// allowlist land.

export type GuideAlgorithmKind =
  | "hysteresis"
  | "resist_switch"
  | "lowpass"
  | "lowpass2"
  | "z_filter"
  | "ppec";

export interface GuideAlgorithmOption {
  value: GuideAlgorithmKind;
  label: string;
}

/** RA algorithm pick list, upstream-verbatim membership + order (PHD2
 *  `RA_ALGORITHMS`, `mount.cpp:227-230` — includes ResistSwitch on RA). */
export const RA_GUIDE_ALGORITHMS: readonly GuideAlgorithmOption[] = [
  { value: "hysteresis", label: "Hysteresis (default)" },
  { value: "lowpass", label: "Lowpass" },
  { value: "lowpass2", label: "Lowpass 2" },
  { value: "resist_switch", label: "Resist Switch" },
  { value: "ppec", label: "Predictive PEC (Gaussian Process)" },
  { value: "z_filter", label: "Z Filter" },
];

/** Dec algorithm pick list, upstream-verbatim membership + order (PHD2
 *  `DEC_ALGORITHMS`, `mount.cpp:231-234` — includes Hysteresis on Dec;
 *  PPEC is RA-only, see module doc). */
export const DEC_GUIDE_ALGORITHMS: readonly GuideAlgorithmOption[] = [
  { value: "hysteresis", label: "Hysteresis" },
  { value: "lowpass", label: "Lowpass" },
  { value: "lowpass2", label: "Lowpass 2" },
  { value: "resist_switch", label: "Resist Switch (default)" },
  { value: "z_filter", label: "Z Filter" },
];

/** `DefaultRaGuideAlgorithm` (dossier §6/§17; `scope.cpp`). */
export const DEFAULT_RA_ALGORITHM: GuideAlgorithmKind = "hysteresis";
/** `DefaultDecGuideAlgorithm` (dossier §6/§17; `scope.cpp`). */
export const DEFAULT_DEC_ALGORITHM: GuideAlgorithmKind = "resist_switch";

/** dossier §15 per-algorithm defaults, display-only (see module doc). */
export interface GuideAlgorithmParamDefaults {
  minMove: number;
  [param: string]: number;
}

export const GUIDE_ALGORITHM_DEFAULTS: Readonly<
  Record<GuideAlgorithmKind, GuideAlgorithmParamDefaults>
> = {
  hysteresis: { minMove: 0.2, hysteresis: 0.1, aggression: 0.7 },
  resist_switch: { minMove: 0.2, aggression: 1.0 },
  lowpass: { minMove: 0.2, slopeWeight: 5.0 },
  lowpass2: { minMove: 0.2, aggressiveness: 80 },
  z_filter: { minMove: 0.1, expFactor: 2.0 },
  ppec: { minMove: 0.2 },
};

export function isValidRaAlgorithm(kind: string): kind is GuideAlgorithmKind {
  return RA_GUIDE_ALGORITHMS.some((o) => o.value === kind);
}

export function isValidDecAlgorithm(kind: string): kind is GuideAlgorithmKind {
  return DEC_GUIDE_ALGORITHMS.some((o) => o.value === kind);
}

// ---------------------------------------------------------------- edit model
// The per-axis selection the GuideView settings drawer edits and PUTs to
// `/api/guide/settings`. Each axis carries its algorithm KIND plus the dossier
// §15 default parameter set for display/tuning. Only the algorithm kind flows to
// the engine today (see the module doc); the params are display-only until a
// config-key allowlist lands, but they are clamped so an out-of-range value can
// never be persisted or shown.

export interface AxisGuideSettings {
  algorithm: GuideAlgorithmKind;
  params: GuideAlgorithmParamDefaults;
}

export interface GuideSettings {
  ra: AxisGuideSettings;
  dec: AxisGuideSettings;
  /** Static Dec backlash-compensation seed pulse (ms) added on a Dec direction
   *  reversal (dossier §10.1; the engine's `blc_pulse_ms` config key). 0 =
   *  disabled, matching PHD2's shipped default. It is an engine-level Dec-axis
   *  property (backlash is mechanical, not per-algorithm), so it lives here
   *  rather than in an axis's params. The ADAPTIVE size controller (dossier
   *  §10.2) is not implemented (D4). */
  blcPulseMs: number;
}

/** A FRESH default GuideSettings: RA Hysteresis (0.7/0.1/0.2), Dec Resist Switch
 *  (1.0) — the dossier §15 PHD2 defaults. Deep-copies GUIDE_ALGORITHM_DEFAULTS so
 *  a caller mutating the result never poisons the shared default table. */
export function defaultGuideSettings(): GuideSettings {
  return {
    ra: {
      algorithm: DEFAULT_RA_ALGORITHM,
      params: { ...GUIDE_ALGORITHM_DEFAULTS[DEFAULT_RA_ALGORITHM] },
    },
    dec: {
      algorithm: DEFAULT_DEC_ALGORITHM,
      params: { ...GUIDE_ALGORITHM_DEFAULTS[DEFAULT_DEC_ALGORITHM] },
    },
    blcPulseMs: 0, // static BLC disabled by default (PHD2's shipped default)
  };
}

/** Aggression/gain hard cap (PHD2 `GuideAlgorithmHysteresis::SetAggression`
 *  range; dossier §15). */
const MAX_AGGRESSION = 2.0;
/** Hysteresis hard cap (PHD2 clamps hysteresis strictly below 1.0). */
const MAX_HYSTERESIS = 0.99;
/** Static BLC seed-pulse hard cap (ms). Far above any real Dec backlash pulse;
 *  the engine raises its Dec ceiling to admit the seed (dossier §10.1). */
const MAX_BLC_PULSE_MS = 10000;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Clamp the static BLC seed pulse: a non-negative INTEGER ms, capped. A
 *  non-number or NaN falls back to 0 (disabled). */
function clampBlcPulse(v: number): number {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return clamp(Math.round(v), 0, MAX_BLC_PULSE_MS);
}

function validateAxis(
  axis: AxisGuideSettings,
  isValid: (k: string) => boolean,
  which: "RA" | "Dec",
): AxisGuideSettings {
  if (!isValid(axis.algorithm)) {
    throw new Error(`unknown ${which} guide algorithm: ${axis.algorithm}`);
  }
  const params: GuideAlgorithmParamDefaults = { ...axis.params };
  for (const key of Object.keys(params)) {
    const val = params[key];
    if (typeof val !== "number" || Number.isNaN(val)) continue;
    if (key === "aggression") params[key] = clamp(val, 0, MAX_AGGRESSION);
    else if (key === "hysteresis") params[key] = clamp(val, 0, MAX_HYSTERESIS);
    else params[key] = Math.max(0, val); // minMove and the rest are non-negative
  }
  return { algorithm: axis.algorithm, params };
}

/** Validate + clamp a GuideSettings: rejects an unknown per-axis algorithm name
 *  (PPEC on Dec included, since it is RA-only), clamps aggression to <= 2.0,
 *  hysteresis to <= 0.99, and every param to >= 0, and clamps the static BLC
 *  pulse to a non-negative integer ms (<= 10000). Returns a clamped COPY;
 *  throws on an unknown algorithm. */
export function validateGuideSettings(s: GuideSettings): GuideSettings {
  return {
    ra: validateAxis(s.ra, isValidRaAlgorithm, "RA"),
    dec: validateAxis(s.dec, isValidDecAlgorithm, "Dec"),
    blcPulseMs: clampBlcPulse(s.blcPulseMs),
  };
}
