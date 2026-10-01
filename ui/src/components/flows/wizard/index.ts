// index.ts - Send to Flow Wizard's door (#196; spec 2026-09-23 flows mosaic,
// Revision 2 ruling 4, D13, 2.1's lazy-door rule D-FU-2).
//
// THE SHEET IS LOADED LAZILY. It brings the stepped sheet, its stylesheet and
// the compile reader with it, none of which a door (the Atlas, the Sky FRAME)
// needs until the operator presses SEND TO FLOW WIZARD. A caller mounts
// `SendToWizardSheetLazy` inside a Suspense boundary (the classic
// `SendToWizardHost` does, with an error boundary); a #/next sheet registry
// entry uses `loadSendToWizardSheet` through its own adapter. Nothing here
// imports the sheet's module statically, so importing this file costs its
// importer one `lazy()` object and no sheet code.
//
// The prefill TYPE is re-exported type-only, which is erased at build: a door
// can name `WizardPrefill` from here without importing a line of the model.

import { lazy } from "react";

export type { SendToWizardSheetProps } from "./SendToWizardSheet";
export type { WizardPrefill } from "./wizardModel";

/** The sheet's module, for a loader that wants the module itself. */
export const loadSendToWizardSheet = () => import("./SendToWizardSheet");

/** The sheet as a lazy component:
 *  `<SendToWizardSheetLazy prefill onClose onOpenInEditor onStarted />`. */
export const SendToWizardSheetLazy = lazy(loadSendToWizardSheet);
