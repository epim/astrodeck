// set4.ts - T-SET-4's ten MORE-group sheets (ARCHITECTURE.md section 5; plan
// hub-weather-monitor-settings.md section E, "File plan and task partition").
//
// This file does NOT touch `hubs/settings/sheets.ts` - that registry is the
// Settings hub task's (T-SET-1's) to compose from this export plus T-SET-2's
// and T-SET-3's own. `sheets4` is the block T-SET-1 spreads in.
import type { SheetComponent } from "../../sheets";
import { SafetyTuningSheet } from "./SafetyTuningSheet";
import { StandardsSheet } from "./StandardsSheet";
import { CalibrationSheet } from "./CalibrationSheet";
import { NamingSheet } from "./NamingSheet";
import { WcsSheet } from "./WcsSheet";
import { SyncSheet } from "./SyncSheet";
import { SkyPackSheet } from "./SkyPackSheet";
import { RestrictedSheet } from "./RestrictedSheet";
import { LogExportSheet } from "./LogExportSheet";
import { FactoryResetSheet } from "./FactoryResetSheet";

export const sheets4: Record<string, SheetComponent> = {
  safetyTuning: SafetyTuningSheet,
  standards: StandardsSheet,
  calibration: CalibrationSheet,
  naming: NamingSheet,
  wcs: WcsSheet,
  sync: SyncSheet,
  skyPack: SkyPackSheet,
  restricted: RestrictedSheet,
  logExport: LogExportSheet,
  factoryReset: FactoryResetSheet,
};
