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

/** The two names, as constants, for the surfaces that have to ASK whether one
 *  of these sheets is on the glass rather than open it.
 *
 *  Two of them do: `FlowsCanvasHost` stands its docked inspector down while the
 *  stage sheet is showing the same editor in the panel beside it, and the phone
 *  stage list carries `?open=` through both. A literal `"flowNode"` spelled at
 *  each of those sites is a route check that silently stops matching the day the
 *  name changes - it does not fail, it just never fires again. */
export const FLOW_NODE_SHEET = "flowNode";
export const FLOW_PALETTE_SHEET = "flowPalette";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), hence the `flow` prefix. `{ id, load }`, never the component:
 *  the `import()` is what keeps the area out of the entry chunk. */
export const flowInspectorSheets: SheetRegistry = {
  [FLOW_NODE_SHEET]: {
    id: "session/flows/inspector/sheets:FlowNodeSheet",
    load: () => import("./sheets").then((m) => ({ default: m.FlowNodeSheet })),
  },
  [FLOW_PALETTE_SHEET]: {
    id: "session/flows/inspector/sheets:FlowPaletteSheet",
    load: () => import("./sheets").then((m) => ({ default: m.FlowPaletteSheet })),
  },
};
