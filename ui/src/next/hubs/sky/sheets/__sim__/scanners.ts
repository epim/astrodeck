// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The scanners the replay can drive, by name (SPEC-v2 7.4).
//
//   node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir> [--scanner legacy|pano]
//
// The registry, not the flag, is what knows which scanners exist: `--scanner`
// is looked up here and an unknown name is an error that lists these. Each
// entry is a LOADER, not a factory, for two reasons. A literal import of a
// module that does not exist yet fails `tsc` (TS2307), which is why only
// `legacy` is registered today and `pano` joins when `panoAdapter.ts` exists
// (T22). And a scanner module may reach for a browser global when it loads
// (the replay has always imported the legacy one only after its harness had
// put its stand-ins in place), so `replayCase` awaits the loader after the
// harness exists and never before.
import type { ScannerFactory } from './scannerUnderTest';

export const SCANNERS: Readonly<Record<string, () => Promise<ScannerFactory>>> = {
  legacy: async () => (await import('./legacyAdapter')).legacyFactory,
};
