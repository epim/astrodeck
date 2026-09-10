// inspector/reg.ts - the registry ENTRIES for this area's two sheets, and
// nothing else (wave R7 follow-up T-R7-21a item 16).
//
// WHY THIS IS ITS OWN FILE. `session/sheets/index.ts` is reached synchronously
// from `hubs/index.ts`, which is in the entry chunk, so every module it imports
// statically is paid for before first paint (D-FU-2). The entries used to live
// in `sheets.tsx` beside the two sheet COMPONENTS, so importing them for their
// `{ id, load }` pairs dragged `FlowInspectorColumn`, `FlowPaletteRail`, the
// calibration matrix, `nodeDefs`, `palette` and `inspector.css` into the entry
// chunk - the whole area, eagerly, to register two names. `canvas/sheets.ts`
// already had this shape; these entries now match it.
//
// The area CSS stays where the components are (`FlowInspectorColumn.tsx`,
// `FlowPaletteRail.tsx`, `sheets.tsx`), so it arrives with the chunk that needs
// it and never before.

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), hence the `flow` prefix. `{ id, load }`, never the component:
 *  the `import()` is what keeps the area out of the entry chunk. */
export const flowInspectorSheets: SheetRegistry = {
  flowNode: {
    id: "session/flows/inspector/sheets:FlowNodeSheet",
    load: () => import("./sheets").then((m) => ({ default: m.FlowNodeSheet })),
  },
  flowPalette: {
    id: "session/flows/inspector/sheets:FlowPaletteSheet",
    load: () => import("./sheets").then((m) => ({ default: m.FlowPaletteSheet })),
  },
};
