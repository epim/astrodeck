// HelpSheet.tsx - Settings > ABOUT > Help and troubleshooting (plan section
// C.8), deep-linked as `#/settings/help?topic=<TroubleshootTopic>`.
//
// HelpView is mounted WHOLE and UNEDITED - its own "Setup guide" panel still
// calls the legacy `openWizard()` store action, which this task does not
// rewire (out of directory; plan C.8 leaves the real rewire to
// `legacyBridge.ts` mapping `store.wizardOpen` -> `#/settings/general/setup`,
// a T0.1 file). Instead this sheet adds its OWN "Setup guide" row ABOVE
// HelpView, so the route to the setup sheet exists from here even before
// that bridge is confirmed landed - named in the task report as the plan
// requires.
//
// The `topic` query param drives HelpView's existing deep-link behaviour
// (scroll into view + a 2.5s highlight, then a one-shot `clearHelpTopic()`)
// by setting the SAME store field HelpView already reads (`store.helpTopic`)
// through the SAME action (`openHelp`) every other caller uses - no new
// state, no second copy of "which topic is active" to drift from HelpView's
// own.
import { useEffect, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Card, ListRow, Sheet } from "../../../ui";
import { useStore } from "../../../../store";
import { TROUBLESHOOTING } from "../../../../lib/troubleshoot";
import type { TroubleshootTopic } from "../../../../types";
import HelpView from "../../../../views/HelpView";

const VALID_TOPICS = new Set<string>(TROUBLESHOOTING.map((e) => e.topic));

export function HelpSheet({ params }: SheetProps): JSX.Element {
  const topic = params.topic;

  // Fires once per distinct `?topic=`: a fresh deep link with a NEW topic
  // re-triggers HelpView's scroll+highlight; re-rendering this sheet with the
  // same topic (e.g. a parent re-render) does not re-fire it.
  useEffect(() => {
    if (topic && VALID_TOPICS.has(topic)) {
      useStore.getState().openHelp(topic as TroubleshootTopic);
    }
  }, [topic]);

  return (
    <Sheet
      data-testid="settings-help"
      title="HELP AND TROUBLESHOOTING"
      icon={<NxIcon name="info" />}
      onBack={nav.back}
    >
      <Card>
        <ListRow
          title="SETUP GUIDE"
          sub="reopen the first-time setup wizard"
          chevron
          onPress={() => nav.sheet("setup")}
          data-testid="row-setup-guide"
        />
      </Card>
      <HelpView />
    </Sheet>
  );
}
