// HelpSheet.tsx - Settings > ABOUT > Help and troubleshooting, deep-linked as
// `#/settings/help?topic=<TroubleshootTopic>`.
//
// IT NO LONGER MOUNTS `views/HelpView`. Wave R7 (T-R7-16) rebuilds that view in
// the design's own vocabulary under `hubs/settings/help/**`, sharing the two
// LOGIC modules it always rendered (`lib/troubleshoot.ts` TROUBLESHOOTING and
// `help.ts` HELP) and re-implementing only presentation. The legacy file is
// untouched and `#/classic/help` still mounts it.
//
// THE SECOND SETUP-GUIDE ROW IS GONE. This sheet used to render its own
// "SETUP GUIDE" `ListRow` straight to `nav.sheet("setup")`, added while
// `views/HelpView`'s own `openWizard()` button had nothing listening
// (`legacyBridge.ts:24-26` records the same defect from the other side: one
// live row and one dead one about 200 px apart). The bridge landed;
// `openWizard()` now navigates to `/settings/general/setup` and is spent with
// `closeWizard()`, so the rebuilt SETUP GUIDE card's single button is the one
// door - and it is the one whose action carries the resume-not-restart
// semantics (`wizardStepId: null` resolves to the first incomplete step).
//
// The `topic` query param still drives the deep link by setting the SAME store
// field the area reads (`store.helpTopic`) through the SAME action every other
// caller uses (`openHelp`) - no new state, no second copy of "which topic is
// active" to drift out of sync.

import { useEffect, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useStore } from "../../../../store";
import { TROUBLESHOOTING } from "../../../../lib/troubleshoot";
import type { TroubleshootTopic } from "../../../../types";
import { HelpArea } from "../help";

const VALID_TOPICS = new Set<string>(TROUBLESHOOTING.map((e) => e.topic));

export function HelpSheet({ params }: SheetProps): JSX.Element {
  const topic = params.topic;

  // Fires once per distinct `?topic=`: a fresh deep link with a NEW topic
  // re-triggers the scroll + highlight; re-rendering this sheet with the same
  // topic (e.g. a parent re-render) does not re-fire it.
  useEffect(() => {
    if (topic && VALID_TOPICS.has(topic)) {
      useStore.getState().openHelp(topic as TroubleshootTopic);
    }
  }, [topic]);

  return (
    <Sheet
      data-testid="settings-help"
      title="HELP AND TROUBLESHOOTING"
      sub={`${TROUBLESHOOTING.length} problems, walked through`}
      icon={<NxIcon name="info" />}
      onBack={nav.back}
      backLabel="SETTINGS"
    >
      <HelpArea />
    </Sheet>
  );
}
