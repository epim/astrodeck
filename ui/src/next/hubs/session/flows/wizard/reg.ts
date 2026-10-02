// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// wizard/reg.ts - the registry ENTRY for the #/next Send to Flow Wizard sheet,
// and nothing else (#196; spec 2026-09-23 flows mosaic, Revision 2 ruling 4,
// D13, D-FU-2).
//
// WHY THIS IS ITS OWN FILE. `session/sheets/index.ts` is reached synchronously
// from `hubs/index.ts`, which is in the entry chunk, so every module it imports
// statically is paid for before first paint (D-FU-2). The sheet this entry
// names brings the stepped wizard with it: its model, the compile reader and
// `wizard.css`. None of that belongs in front of first paint to register one
// name, so the entry is `{ id, load }` with a dynamic `import()` and this file
// imports nothing but a type. `wizard/__tests__/flowWizardSheet.test.tsx`
// holds that at run time: importing this module resolves no other module.
//
// THE KEY IS THE LITERAL "flowWizard", not `FlowWizardSheet.tsx`'s own
// `FLOW_WIZARD_SHEET`, for the reason `framing/reg.ts` spells "flowFrame":
// reading the constant would mean importing the sheet module. The test above
// asserts the two agree.

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), hence the `flow` prefix. Registered by the session hub and
 *  opened from any hub: the Sky FRAME's door pushes it over the sky. */
export const flowWizardSheets: SheetRegistry = {
  flowWizard: {
    id: "session/flows/wizard/FlowWizardSheet",
    load: () => import("./FlowWizardSheet").then((m) => ({ default: m.FlowWizardSheet })),
  },
};
