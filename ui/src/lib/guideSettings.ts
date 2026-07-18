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

/** RA algorithm pick list (dossier §6/§17; PHD2 `RA_ALGORITHMS`). */
export const RA_GUIDE_ALGORITHMS: readonly GuideAlgorithmOption[] = [
  { value: "hysteresis", label: "Hysteresis (default)" },
  { value: "lowpass", label: "Lowpass" },
  { value: "lowpass2", label: "Lowpass 2" },
  { value: "z_filter", label: "Z Filter" },
  { value: "ppec", label: "Predictive PEC (Gaussian Process)" },
];

/** Dec algorithm pick list — PPEC is RA-only, see module doc. */
export const DEC_GUIDE_ALGORITHMS: readonly GuideAlgorithmOption[] = [
  { value: "resist_switch", label: "Resist Switch (default)" },
  { value: "lowpass", label: "Lowpass" },
  { value: "lowpass2", label: "Lowpass 2" },
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
