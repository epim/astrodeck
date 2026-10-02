// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowWizardSheet.tsx - the #/next `flowWizard` sheet: Send to Flow Wizard
// (#196; spec 2026-09-23 flows mosaic, Revision 2 ruling 4, D13, section 8 S6).
//
// NO FORK. The stepped wizard is ONE shared component,
// `components/flows/wizard/SendToWizardSheet.tsx`, which the classic Atlas
// mounts too (through `SendToWizardHost`). This file is the adapter between
// the router's sheet contract (`{ params, depth }`) and that component's props,
// and nothing else: no step, no copy, no request. A #/next copy of the wizard
// would be the second implementation of every rule in it (what a step asks,
// the one body GENERATE posts, the RUN lock), and the day one was fixed the
// other would still ship the defect.
//
// THE WIZARD STAYS LAZY TWICE OVER (D-FU-2). `reg.ts` fetches this module
// with a dynamic `import()`, and this module mounts the shared door
// `SendToWizardSheetLazy`, which fetches the sheet only when it renders. So a
// door that imports `openFlowWizard` below statically (the Sky FRAME) pays for
// this adapter, the model and the router, never for the sheet or
// `wizard.css`.
//
// THE PREFILL IS IN THE ROUTE. `openFlowWizard` writes the door's framing into
// the hash as `wz_*` params (wizardModel `wizardParams`), and the sheet reads
// it back (`prefillFromParams`), so a reload or a shared link reopens the same
// wizard with the same framing. The door's own params are kept beside them:
// route params are one map for the whole stack, and the screen under the
// wizard still needs its own.
//
// WHAT DIFFERS FROM THE CLASSIC HOST, and is all this file decides:
//
//   - CLOSE and EDIT FRAMING pop this sheet, which lands on the door that
//     opened it with its framing as it was (the Sky FRAME keeps its framing
//     in the store's session);
//   - OPEN IN EDITOR goes to the route a freshly created flow opens at
//     (`newFlowRoute`: the canvas, or the stage list on a phone), the one the
//     NEW FLOW sheet uses, after the shared sheet has opened the flow in the
//     store ("open first, then navigate");
//   - a started run goes to Session - Now, where a run is watched and
//     stopped, as the Sky flow card's RUN does.
//
// THE POP IS GUARDED as `FlowFrameSheet`'s is: it pops only while this sheet
// is on top, so a close that lands after something else was pushed pops that
// other sheet never. Escape reaches it only through the shared sheet's
// Overlay, which takes the key on `document` in the capture phase before
// `SheetHost`'s window handler sees it.

import { Suspense, useMemo, type JSX } from "react";

import { SendToWizardSheetLazy } from "../../../../../components/flows/wizard";
import {
  prefillFromParams, wizardParams, withoutWizardParams, type WizardPrefill,
} from "../../../../../components/flows/wizard/wizardModel";
import { useBreakpoint } from "../../../../breakpoint";
import { currentRoute, nav } from "../../../../router";
import { EmptyCard } from "../../../../ui";
import type { SheetProps } from "../../../sheets";
import { newFlowRoute } from "../create/wizard";

/** The sheet's own name in the route and the registry. `reg.ts` spells it as
 *  a literal key (it may not import this module), and the sheet's test
 *  asserts the two are the same word. */
export const FLOW_WIZARD_SHEET = "flowWizard";

/** What the slot shows between the route naming this sheet and the wizard's
 *  chunk arriving. */
export const WIZARD_LOADING = "Loading the flow wizard.";

/** The params a door opens the wizard with: its own route's params, less any
 *  wizard key a previous opening left, and the framing as `wz_*` keys. */
export function flowWizardParams(prefill: WizardPrefill, keep: Record<string, string>): Record<string, string> {
  return { ...withoutWizardParams(keep), ...wizardParams(prefill) };
}

/** Open Send to Flow Wizard on `prefill`, over whatever screen is showing. */
export function openFlowWizard(prefill: WizardPrefill): void {
  nav.sheet(FLOW_WIZARD_SHEET, flowWizardParams(prefill, currentRoute().params));
}

/** CLOSE and EDIT FRAMING: pop this sheet, but only while it is still the
 *  top of the stack (see the header). Exported so the guard can be graded
 *  directly: under a second sheet the router unmounts this one, so no press
 *  of the sheet's own buttons can reach it there. */
export function closeWizard(): void {
  const sheets = currentRoute().sheets;
  if (sheets[sheets.length - 1] === FLOW_WIZARD_SHEET) nav.back();
}

export function FlowWizardSheet({ params }: SheetProps): JSX.Element {
  const phone = useBreakpoint() === "phone";
  // Keyed on the FRAMING'S params alone, so one opening keeps one prefill
  // object and the operator's answers: a re-render, or a param of the door's
  // changing under the wizard, remounts nothing. A new opening with another
  // framing is a new wizard.
  const key = JSON.stringify(wizardParams(prefillFromParams(params)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const prefill = useMemo(() => prefillFromParams(params), [key]);
  // THE SLOT'S OWN FRAME, `.nx-sheet` and `.nx-sheet-body`, as `flowFrame`
  // has it (#384): the loading card sits inside the gutter every sheet has,
  // not on the screen's corner. The wizard itself portals out of it into the
  // overlay host, so under the wizard the frame is empty.
  return (
    <div className="nx-sheet" style={{ flex: 1 }} data-testid="session-flow-wizard">
      <div className="nx-sheet-body">
        <Suspense fallback={<EmptyCard title="FLOW WIZARD" hint={WIZARD_LOADING} />}>
          <SendToWizardSheetLazy
            key={key}
            prefill={prefill}
            onClose={closeWizard}
            onEditFraming={closeWizard}
            onOpenInEditor={(id) => nav.go(newFlowRoute(id, phone))}
            onStarted={() => nav.hub("session", "now")}
          />
        </Suspense>
      </div>
    </div>
  );
}

export default FlowWizardSheet;
