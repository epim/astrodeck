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

import type { SheetRegistry } from "../../sheets";

// Both entries are dynamic imports (D-FU-2): the two settings sheets are a
// gear-tap away, not part of first paint, and neither is on the path a user
// takes to see whether it is clear tonight.

export const sheets: SheetRegistry = {
  weatherSettings: {
    id: "weather/sheets/WeatherSettingsSheet",
    load: () => import("./WeatherSettingsSheet").then((m) => ({ default: m.WeatherSettingsSheet })),
  },
  cloudmap: {
    id: "weather/sheets/CloudmapSheet",
    load: () => import("./CloudmapSheet").then((m) => ({ default: m.CloudmapSheet })),
  },
};
