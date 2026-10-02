// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framing/reg.ts - the registry ENTRY for the #/next framing sheet, and
// nothing else (#189 S4 item 5; spec 2026-09-23 flows mosaic, 2.1).
//
// WHY THIS IS ITS OWN FILE. `session/sheets/index.ts` is reached synchronously
// from `hubs/index.ts`, which is in the entry chunk, so every module it imports
// statically is paid for before first paint (D-FU-2). The sheet this entry
// names brings the Target modal with it: the survey canvas, the catalogue
// search, the night card and `framing.css`. None of that belongs in front of
// first paint to register one name, so the entry is `{ id, load }` with a
// dynamic `import()` and this file imports nothing but a type.
// `framing/__tests__/flowFrameSheet.test.tsx` holds that at run time: importing
// this module resolves no other module at all.
//
// THE KEY IS THE LITERAL "flowFrame", not `FlowFrameSheet.tsx`'s own
// `FLOW_FRAME_SHEET`, for the same reason `canvas/sheets.ts` spells
// "flowStages": reading the constant would mean importing the sheet module,
// which is the static import this file exists to avoid. `flowsDom.test.tsx`
// asserts the two agree, because a silent disagreement is a door that opens
// "THIS SHEET IS NOT BUILT YET".

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), hence the `flow` prefix. */
export const flowFrameSheets: SheetRegistry = {
  flowFrame: {
    id: "session/flows/framing/FlowFrameSheet",
    load: () => import("./FlowFrameSheet").then((m) => ({ default: m.FlowFrameSheet })),
  },
};
