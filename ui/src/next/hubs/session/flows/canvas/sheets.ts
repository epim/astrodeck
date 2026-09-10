// canvas/sheets.ts - the sheets this area registers.
//
// One name, `flowStages`: the phone's stage list, monitor and tap-to-wire
// footer. It is a NAMED export rather than an edit to the session hub's registry
// because that file is composed from several tasks' halves - a shared registry
// edited by two tasks is how a merge silently drops a sheet, with the file
// resolving, the app building, and one name simply not in the map any more.
//
// Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a collision),
// which is why this is a plain word rather than a hub-prefixed one.
//
// NOTHING IS IMPORTED FROM THE SHEET MODULE HERE, and that is the whole point of
// the `{ id, load }` shape (D-FU-2). `hubs/index.ts` is in the entry chunk and
// imports every hub's registry synchronously, so a registry module that pulled
// its components in with a static import would put them - and their transitive
// `store` / `nodeDefs` / `autoLayout` tree - in front of first paint. The key
// below is therefore the literal `"flowStages"` rather than the sheet module's
// own `FLOW_STAGES_SHEET` constant; `flowsDom.test.tsx` asserts the two agree,
// because a silent disagreement would be a route that opens nothing.

import type { SheetRegistry } from "../../../sheets";

export const flowCanvasSheets: SheetRegistry = {
  flowStages: {
    id: "session/flows/canvas/FlowStagesPhoneSheet",
    load: () => import("./FlowStagesPhoneSheet").then((m) => ({ default: m.FlowStagesPhoneSheet })),
  },
};
