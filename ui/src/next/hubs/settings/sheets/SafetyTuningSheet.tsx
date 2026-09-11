// SafetyTuningSheet.tsx - Settings > MORE > Safety (ARCHITECTURE.md section 5;
// plan hub-weather-monitor-settings.md section C.6, row SAFETY; wave R7,
// T-R7-9 and ruling 3).
//
// REDUCED, 2026-09-10. This sheet used to mount `SafetyPanel`,
// `SafetyLimitsPanel` and `EscalationPanel` whole. The first two were a SECOND
// editor for the same `config.safety` block that `hubs/rig/sheets/safety.tsx`
// already rebuilt natively in wave 1 - two screens, two drafts, two Save
// buttons, one rig, and no way to tell from either of them which one the
// engine last heard from. They are unmounted here. What is left is the two
// things the rig sheet does not own (the preset, and the run-recovery policy,
// both rebuilt under `../tuning/safety/`) plus named links to the screens that
// own the rest: the SAFETY MONITOR device sheet, and the camera sheet for the
// warm ramp.
//
// The panel below carries its own single lock note for both capabilities it
// edits (`config.safety`, `config.alerts`), so this sheet adds none on top
// (plan C.6: "do not double up a lock note").
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { SafetyTuningPanel } from "../tuning/safety";

export function SafetyTuningSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-safetyTuning"
      title="SAFETY"
      sub="the preset, what happens when something fails, and where every limit lives"
      icon={<NxIcon name="safety" />}
      onBack={nav.back}
    >
      <SafetyTuningPanel />
    </Sheet>
  );
}
