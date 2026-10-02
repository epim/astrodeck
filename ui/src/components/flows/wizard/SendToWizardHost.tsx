// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// SendToWizardHost.tsx - the classic UI's mount for Send to Flow Wizard (#196;
// spec 2026-09-23 flows mosaic, Revision 2 ruling 4, D13, D-FU-2).
//
// A door (the classic Atlas's SEND TO FLOW WIZARD) renders this with the
// framing it holds, and nothing else: the stepped sheet is the ONE shared
// component (SendToWizardSheet.tsx), which the #/next `flowWizard` sheet
// mounts too, and this file adapts it to the classic shell and forks none of
// it. What differs from #/next is exactly what the sheet asks its host for:
//
//   - EDIT FRAMING closes the wizard, which leaves the operator on the door's
//     framing under it; the Atlas holds the framing in its own session, so
//     nothing is lost and SEND TO FLOW WIZARD reopens from the framing as it
//     then is;
//   - OPEN IN EDITOR closes the wizard and shows the Flows view, on the flow
//     the sheet has already opened in the store;
//   - a started run shows the Flows view as well, where the open flow's
//     header carries STOP and its log says which ledger the night went into.
//
// THE SHEET STAYS LAZY. This module imports the door (`index.ts`), whose
// `SendToWizardSheetLazy` fetches the sheet only when it renders, so a view
// that imports this host statically pays for this file, not for the sheet.
// `sendToWizardSheet.test.tsx` holds that with a resolve hook.
//
// A SHEET THAT FAILS TO LOAD OR THROWS says so inside its own Overlay (the
// boundary below) instead of reaching the view's boundary, whose reload
// replaces the whole Atlas: the framing the operator made is still there
// behind a closed wizard.

import { Component, Suspense, type JSX, type ReactNode } from "react";
import { Overlay } from "../../Overlay";
import { useStore } from "../../../store";
import { SendToWizardSheetLazy, type WizardPrefill } from "./index";

/** What the wizard's slot says while its chunk arrives. */
export const WIZARD_LOADING = "Loading the flow wizard.";

/** What a sheet that failed to load or threw says instead. Nothing was
 *  generated unless the review had already said "Saved as". */
export const WIZARD_FAILED =
  "The flow wizard could not open. The framing is still here: close this, then reload the page to try again.";

class WizardBoundary extends Component<
  { onClose: () => void; children: ReactNode }, { error: string | null }
> {
  state: { error: string | null } = { error: null };

  static getDerivedStateFromError(e: unknown): { error: string } {
    return { error: e instanceof Error ? e.message : String(e) };
  }

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <Overlay open label="Send to Flow Wizard" onClose={this.props.onClose} variant="center">
        <div data-testid="wizard-failed" className="flex flex-col gap-3 p-4 text-[12px] leading-[1.5]">
          <p>{WIZARD_FAILED}</p>
          <p className="font-mono text-[10.5px] text-faint break-words">{this.state.error}</p>
          <button type="button" className="btn self-start" onClick={this.props.onClose}>CLOSE</button>
        </div>
      </Overlay>
    );
  }
}

export interface SendToWizardHostProps {
  /** The framing to hand over, or null for no wizard. */
  prefill: WizardPrefill | null;
  onClose: () => void;
  /** Back to the door's framing. Defaults to closing the wizard, which is
   *  what the Atlas needs: its framing is under the wizard. */
  onEditFraming?: () => void;
}

export function SendToWizardHost({ prefill, onClose, onEditFraming }: SendToWizardHostProps): JSX.Element | null {
  const setView = useStore((s) => s.setView);
  if (prefill === null) return null;
  const toFlows = () => { onClose(); setView("flows"); };
  return (
    <WizardBoundary onClose={onClose}>
      <Suspense
        fallback={(
          <Overlay open label="Send to Flow Wizard" onClose={onClose} variant="center">
            <p className="p-4 text-[12px]" data-testid="wizard-loading">{WIZARD_LOADING}</p>
          </Overlay>
        )}
      >
        <SendToWizardSheetLazy
          prefill={prefill}
          onClose={onClose}
          onEditFraming={onEditFraming ?? onClose}
          onOpenInEditor={toFlows}
          onStarted={toFlows}
        />
      </Suspense>
    </WizardBoundary>
  );
}

export default SendToWizardHost;
