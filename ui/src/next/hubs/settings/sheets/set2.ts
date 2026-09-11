// set2.ts - T-SET-2's two sheets, handed to the SETTINGS hub as one object.
//
// The hub composes the registry (`hubs/settings/sheets.ts`); this file is the
// seam between that composition and this task's directory, so the two can land
// independently without either editing the other's file. Names are GLOBAL
// across the app (ARCHITECTURE.md section 5), which is why they are spelled
// here once rather than at each `nav.sheet(...)` call site.

import type { SheetRegistry } from "../../sheets";

export const sheets2: SheetRegistry = {
  connection: {
    id: "settings/sheets/ConnectionSheet",
    load: () => import("./ConnectionSheet").then((m) => ({ default: m.ConnectionSheet })),
  },
  optics: {
    id: "settings/sheets/OpticsSheet",
    load: () => import("./OpticsSheet").then((m) => ({ default: m.OpticsSheet })),
  },
};

export default sheets2;
