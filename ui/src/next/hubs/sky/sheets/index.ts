// sky/sheets/index.ts - the SKY hub's sheet registry (ARCHITECTURE.md section 5).
//
// Seven names, four owners, ONE module each. `sites` and `horizon` are shared
// with the Settings hub and are registered from both maps under the SAME module
// id - which is the contract working, not a collision. `hubs/index.ts` checks
// the ids at module load precisely because the other case (two DIFFERENT
// modules under one name) makes the same URL open different screens depending
// on which registry composed last, and by the time a user notices it looks like
// a routing bug rather than a registry one.
//
// This file replaced a `sky/sheets.ts` placeholder. A `.ts` file and a directory
// of the same name are both resolvable for `import "./sky/sheets"`, and the FILE
// wins - so leaving it behind would have shadowed every sheet here with an empty
// map and left the hub's own hash pointing at "THIS SHEET IS NOT BUILT YET".

import type { SheetRegistry } from "../../sheets";

// EVERY ENTRY IS A DYNAMIC IMPORT (D-FU-2). Nothing here is fetched until the
// hash names the sheet, and the seven modules below are seven chunks rather
// than seven more kilobytes in front of first paint. `SHEETS[name]` still
// answers synchronously - the map is built at module load, and what it holds
// is a `lazy()` component, which is truthy before its module has arrived.

export const sheets: SheetRegistry = {
  targets: { id: "sky/sheets/targets", load: () => import("./targets").then((m) => ({ default: m.TargetsSheet })) },
  brief: { id: "sky/sheets/brief", load: () => import("./brief").then((m) => ({ default: m.BriefSheet })) },
  quick: { id: "sky/sheets/quick", load: () => import("./quick").then((m) => ({ default: m.QuickSessionSheet })) },
  flow: { id: "sky/sheets/flow", load: () => import("./flow").then((m) => ({ default: m.FlowCardSheet })) },
  // `sites` and `horizon` carry these exact ids in the Settings registry too:
  // one id, one module, two hubs pointing at it.
  sites: { id: "sky/sheets/sites", load: () => import("./sites").then((m) => ({ default: m.SitesSheet })) },
  horizon: { id: "sky/sheets/horizon", load: () => import("./horizon").then((m) => ({ default: m.HorizonSheet })) },
  coords: { id: "sky/sheets/coords", load: () => import("./coords").then((m) => ({ default: m.CoordsSheet })) },
};
