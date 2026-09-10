// SetupGuideCard.tsx - the setup guide's permanent door (wave R7, T-R7-16).
//
// FIRST on the sheet, and that ordering is the whole reason the card exists.
// `views/HelpView.tsx:11-41` records the measured QA blocker: `openWizard()`
// had one caller outside the auto-open effect, auto-open needs
// `!seenWizard && siteIsDefault && !equipConnected`, and so the moment a tester
// connected a rig and dismissed the docked bar, every route back into the guide
// disappeared at once. Help is where the re-entry belongs because Help is a
// destination, not a state: reachable connected or not, first run or tenth.
//
// ONE BUTTON, and it calls `openWizard()` and nothing else, on purpose. That
// action sets `wizardStepId: null`; a null override resolves to the FIRST
// INCOMPLETE step off the live snapshot, so reopening RESUMES rather than
// restarts and there is no second copy of "what is done" to drift. In the new
// UI `next/legacyBridge.ts:120-126` turns that flag into `/settings/general/setup`
// and spends it with `closeWizard()`, so the same press that used to dock a bar
// now opens the setup sheet - whose five rows are derived live
// (`general/useSetup.ts`), which is what makes the promise below true.
//
// The previous mount also rendered its OWN "SETUP GUIDE" row navigating
// straight to `nav.sheet("setup")`, added while the bridge was unconfirmed
// (`HelpSheet.tsx:5-11`). The bridge landed; two doors 200 px apart to the same
// sheet is one door too many, and the surviving one is the one whose action
// carries the resume semantics.

import type { JSX } from "react";
import { ActionButton, Card, Label } from "../../../ui";
import { NxIcon } from "../../../icons";
import { useStore } from "../../../../store";

/** What the guide will actually ask for. The five step titles come from
 *  `general/setupSteps.ts` - said here in lower case because the reader has not
 *  opened the sheet yet and this is a sentence, not a row. */
export const SETUP_WHAT =
  "Five steps for a new rig: pair the rig computer, connect the devices, " +
  "set the optics, set the site and horizon, take a first frame.";

/** The promise. Every clause is enforced by the setup sheet's own derivation:
 *  each row's done state is recomputed from the current snapshot, so nothing is
 *  re-asked and nothing stays ticked once it stops being true. */
export const SETUP_RESUME =
  "Reopening never starts you over: steps you have already finished stay " +
  "ticked, and the guide points at the first one that is not.";

export function SetupGuideCard(): JSX.Element {
  const openWizard = useStore((s) => s.openWizard);

  return (
    <section className="nx-help-block" aria-label="Setup guide">
      <Label size={11}>SETUP GUIDE</Label>
      <Card>
        <p className="nx-help-p">{SETUP_WHAT}</p>
        <p className="nx-help-promise">
          <span className="nx-help-promise-glyph" aria-hidden="true">
            <NxIcon name="check" size={12} strokeWidth={2.2} />
          </span>
          <span>{SETUP_RESUME}</span>
        </p>
        <div className="nx-help-cta">
          <ActionButton
            kind="primary"
            glyph={<NxIcon name="info" size={14} />}
            onPress={openWizard}
            data-testid="help-setup-guide"
          >
            Open the setup guide
          </ActionButton>
        </div>
      </Card>
    </section>
  );
}
