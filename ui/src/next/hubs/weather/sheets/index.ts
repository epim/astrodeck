// weather/sheets/index.ts - the WEATHER hub's sheet registry
// (ARCHITECTURE.md section 5).
//
// Two names, both GLOBAL across the app: Settings > MORE links to them by name
// (`nav.sheet("weatherSettings")`, `nav.sheet("cloudmap")`) and so does this
// hub's own header gear and its two off-state cards. One name is one component,
// and `hubs/index.ts` throws at module load if any other hub registers either
// of these names with a different component - a clash found at the tap that
// opens the wrong screen reads as a routing bug.
//
// This replaces the placeholder `weather/sheets.ts`: a file and a directory of
// the same name would both answer `import "./weather/sheets"`, and which one
// won would depend on the resolver.

import type { SheetComponent } from "../../sheets";
import { WeatherSettingsSheet } from "./WeatherSettingsSheet";
import { CloudmapSheet } from "./CloudmapSheet";

export const sheets: Record<string, SheetComponent> = {
  weatherSettings: WeatherSettingsSheet,
  cloudmap: CloudmapSheet,
};
