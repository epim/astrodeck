// SafetyTuningSheet.tsx - Settings > MORE > Safety (ARCHITECTURE.md section 5;
// plan hub-weather-monitor-settings.md section C.6, row SAFETY).
//
// The rig hub owns the SAFETY DEVICE sheet (live readings + the trip chain,
// opened by name below - `nav.sheet("safety")`). This tuning sheet is the
// three CONFIG panels that decide what "unsafe" means and what happens when
// it trips: sun avoidance + the roof interlocks (SafetyPanel), the altitude /
// twilight / warm-ramp limits (SafetyLimitsPanel), and the run-recovery
// policy (EscalationPanel). Each panel is mounted UNCHANGED and self-gates on
// its own capability (config.safety / config.solar_override / config.alerts)
// with its own accessPhrase() lock note - this sheet adds no lock note of its
// own on top of theirs (plan C.6: "the new sheet adds nothing on top; do not
// double up a lock note").
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet, ListRow } from "../../../ui";
import SafetyPanel from "../../../../components/settings/SafetyPanel";
import SafetyLimitsPanel from "../../../../components/settings/SafetyLimitsPanel";
import EscalationPanel from "../../../../components/settings/EscalationPanel";

export function SafetyTuningSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-safetyTuning"
      title="SAFETY"
      sub="sun avoidance, limits, what happens when a limit trips"
      icon={<NxIcon name="safety" />}
      onBack={nav.back}
    >
      <ListRow
        icon={<NxIcon name="safety" />}
        title="LIVE SAFETY STATUS"
        sub="readings and the trip chain, on the rig's own device sheet"
        chevron
        onPress={() => nav.sheet("safety")}
      />
      <SafetyPanel />
      <SafetyLimitsPanel />
      <EscalationPanel />
    </Sheet>
  );
}
