// lib/guideRms.ts — pure same-night per-provider RMS tagging + window selection
// (P5-T1 fix round C1/I2). The "guide" bus channel carries no provider tag, so
// store.ts stamps each incoming tick with the CURRENTLY-resolved guide provider
// (`status.providers.guide`) at ingest time. After the C1 server honesty fix
// that badge reports the ACTUAL serving guider (never the requested override —
// see providers.py::_resolve_guide), so these windows are truthful by
// construction: a tick can only be filed under the guider that really produced
// it.
//
// Extracted out of store.ts / GuideView (no React, no store import) so the
// tagging + read-back logic is unit-testable — see lib/__tests__/guideRms.test.ts,
// run directly under tsx per repo convention (mirrors lib/apiError.ts idiom).

import type { GuideStats } from "../types";
import type { RmsWindow } from "./rmsCompare";

/** The provider tag read off `status.providers.guide` at ingest time (a
 *  ProviderChoiceView is assignable to this — `reason` is simply ignored). */
export interface GuideProviderTag {
  kind: string;
  label: string;
}

/** One tagged RMS tick: the guide stats plus the resolved provider label and a
 *  receive timestamp. Keyed by provider kind in the map below. */
export type GuideRmsEntry = GuideStats & { providerLabel: string; atMs: number };

/** Per-provider-kind latest tagged tick. Session-only in the store. */
export type GuideRmsByKind = Partial<Record<string, GuideRmsEntry>>;

/** Fold a "guide" tick into the per-kind map, tagged by the currently-resolved
 *  provider (latest-per-kind, so each provider's window survives a switch to the
 *  other). When no provider has resolved yet (`choice` null/undefined or without
 *  a kind) the tick is DROPPED — a tick we can't attribute must never be filed
 *  under an arbitrary kind (that is exactly the mislabel C1 guards against).
 *  Pure: returns a NEW map, never mutates `prev`. */
export function tagGuideRms(
  prev: GuideRmsByKind,
  stats: GuideStats,
  choice: GuideProviderTag | null | undefined,
  atMs: number,
): GuideRmsByKind {
  if (!choice || !choice.kind) return prev;
  return {
    ...prev,
    [choice.kind]: { ...stats, providerLabel: choice.label, atMs },
  };
}

function toWindow(e: GuideRmsEntry): RmsWindow {
  return { label: e.providerLabel, rmsTotal: e.rms_total, samples: e.recent.length };
}

/** The two windows GuideView's head-to-head compares: the native-family window
 *  ("astrodeck" on real hardware, or "sim" on the sim rig — both are the SAME
 *  NativeGuider engine, only the badge differs) vs. the PHD2/NINA-bridge window
 *  ("backend"). Either is `undefined` until that provider has guided this
 *  session. */
export function selectGuideWindows(byKind: GuideRmsByKind): {
  native: RmsWindow | undefined;
  backend: RmsWindow | undefined;
} {
  const nativeEntry = byKind.astrodeck ?? byKind.sim;
  const backendEntry = byKind.backend;
  return {
    native: nativeEntry && toWindow(nativeEntry),
    backend: backendEntry && toWindow(backendEntry),
  };
}
