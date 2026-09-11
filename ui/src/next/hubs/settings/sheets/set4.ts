// set4.ts - T-SET-4's ten MORE-group sheets (ARCHITECTURE.md section 5; plan
// hub-weather-monitor-settings.md section E, "File plan and task partition").
//
// This file does NOT touch `hubs/settings/sheets.ts` - that registry is the
// Settings hub task's (T-SET-1's) to compose from this export plus T-SET-2's
// and T-SET-3's own. `sheets4` is the block T-SET-1 spreads in.
import type { SheetRegistry } from "../../sheets";

export const sheets4: SheetRegistry = {
  safetyTuning: { id: "settings/sheets/SafetyTuningSheet", load: () => import("./SafetyTuningSheet").then((m) => ({ default: m.SafetyTuningSheet })) },
  standards: { id: "settings/sheets/StandardsSheet", load: () => import("./StandardsSheet").then((m) => ({ default: m.StandardsSheet })) },
  calibration: { id: "settings/sheets/CalibrationSheet", load: () => import("./CalibrationSheet").then((m) => ({ default: m.CalibrationSheet })) },
  naming: { id: "settings/sheets/NamingSheet", load: () => import("./NamingSheet").then((m) => ({ default: m.NamingSheet })) },
  wcs: { id: "settings/sheets/WcsSheet", load: () => import("./WcsSheet").then((m) => ({ default: m.WcsSheet })) },
  sync: { id: "settings/sheets/SyncSheet", load: () => import("./SyncSheet").then((m) => ({ default: m.SyncSheet })) },
  skyPack: { id: "settings/sheets/SkyPackSheet", load: () => import("./SkyPackSheet").then((m) => ({ default: m.SkyPackSheet })) },
  restricted: { id: "settings/sheets/RestrictedSheet", load: () => import("./RestrictedSheet").then((m) => ({ default: m.RestrictedSheet })) },
  logExport: { id: "settings/sheets/LogExportSheet", load: () => import("./LogExportSheet").then((m) => ({ default: m.LogExportSheet })) },
  factoryReset: { id: "settings/sheets/FactoryResetSheet", load: () => import("./FactoryResetSheet").then((m) => ({ default: m.FactoryResetSheet })) },
};
