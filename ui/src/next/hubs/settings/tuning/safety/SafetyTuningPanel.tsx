// SafetyTuningPanel.tsx - the body of Settings > MORE > SAFETY after the
// reduction (wave R7, T-R7-9; plan section 3.F1 and ruling 3).
//
// WHAT WAS HERE AND WHY IT IS NOT ANY MORE. This sheet used to mount
// `SafetyPanel` (766 ln) and `SafetyLimitsPanel` (751 ln) whole. Wave 1's
// `hubs/rig/sheets/safety.tsx` had already rebuilt, natively, every control
// both of them carry except three: sky-hold, the unsafe action and its two
// streak counts, the give-up minutes, the master switch, the sun cone and its
// double gate, the altitude floor and zenith keep-out, the obstruction wedges,
// twilight, the horizon link, pier limits, poll-each-frame, the meridian
// warning, all four roof interlocks, the dead-man rows and the simulator. So
// this sheet was a SECOND editor for the same config block, in different
// chrome, with its own draft and its own Save - two screens that can disagree
// about what the rig will do when it rains.
//
// The three things it did own:
//   * the safety PRESET picker - rebuilt here (`safetyPresets.ts`);
//   * `Warm ramp` and `Warm ramp C/min` - which live on the CAMERA sheet, next
//     to the cooler they describe, and already do (`rig/sheets/camera.tsx`,
//     markers `warm-ramp` / `ramp-rate`). This sheet links there;
//   * the escalation policy - rebuilt as `EscalationEditor`, which this sheet
//     and the rig safety sheet now share.
//
// Everything else is a link to the screen that owns it. A link, not a copy: the
// rows below name what is on the other side, so a setting is never discovered
// only by guessing that a device sheet holds it.

import { useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setSafetyConfig } from "../../../../../api/backends";
import { accessPhrase } from "../../../../../lib/caps";
import { useConfig, useStore } from "../../../../../store";
import type { SafetyConfig } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { useLock } from "../../../../lib/gateHook";
import { nav } from "../../../../router";
import { Card, EmptyCard, Label, ListRow, LockNote, Mono, Segmented } from "../../../../ui";
import { EscalationEditor } from "./EscalationEditor";
import {
  CUSTOM_NOT_PICKABLE, PRESET_BLURB, PRESET_LABEL, PRESET_ORDER, presetLine, presetPatch,
  type PresetName,
} from "./safetyPresets";

const PRESET_HINT =
  "A starting point for what happens when a limit trips. Changing any of those settings on the "
  + "safety sheet moves this to CUSTOM by itself.";

const PRESET_SAVE_FAILED = "Could not save. The rig still has the preset shown.";

export const LIVE_LINK_SUB =
  "what the monitor is reading right now, the streak so far, and the chain the rig will run";

export const TRIP_LINK_SUB =
  "hold on a cloudy sky, the unsafe action, how many readings it takes, resume and its streak, the "
  + "give-up minutes, and the master switch";

export const LIMITS_LINK_SUB =
  "sun exclusion cone, altitude floor and zenith keep-out, obstruction wedges, twilight, horizon, "
  + "pier limits, check-before-every-frame, the meridian warning and the roof interlocks";

export const WARM_LINK_SUB =
  "the warm-up switch and its degrees per minute live on the camera sheet, beside the cooler they "
  + "describe";

/** The sheet's ONE read-only sentence. Two capabilities are edited here and
 *  `LockNote` is deliberately singular, so this folds them: same reason, one
 *  clause; different reasons, one clause each. It starts lower-case because
 *  `LockNote` prefixes "Read-only - ". */
export function tuningLockSentence(
  safetyReason: string | null,
  alertsReason: string | null,
): string | null {
  const preset = "changing the safety preset (config.safety)";
  const policy = "the recovery policy (config.alerts)";
  if (safetyReason && alertsReason) {
    return safetyReason === alertsReason
      ? `${preset} or ${policy} ${safetyReason}`
      : `${preset} ${safetyReason}; ${policy} ${alertsReason}`;
  }
  if (safetyReason) return `${preset} ${safetyReason}`;
  if (alertsReason) return `changing ${policy} ${alertsReason}`;
  return null;
}

export function SafetyTuningPanel(): JSX.Element {
  const config = useConfig();
  const safety = (config?.safety ?? null) as SafetyConfig | null;
  const safetyLock = useLock({ cap: "config.safety" });
  const alertsLock = useLock({ cap: "config.alerts" });

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pick = async (name: PresetName): Promise<void> => {
    const patch = presetPatch(name);
    if (!safety || busy || !patch) return;
    setBusy(true);
    setErr(null);
    try {
      // Wholesale replace, and the NUMBERS not just the label: the server
      // re-derives `preset` from the five fields on every read
      // (config.py `_derive_preset_label`), so a body carrying only the label
      // comes straight back as whatever the untouched numbers already said.
      await setSafetyConfig({ ...safety, ...patch } as SafetyConfig);
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403
          ? `Refused - ${accessPhrase("config.safety")} is needed here.`
          : e.message || PRESET_SAVE_FAILED)
        : PRESET_SAVE_FAILED);
    } finally {
      setBusy(false);
    }
  };

  const busyReason = busy ? "the preset is still being saved" : null;
  const presetLock = safetyLock.lockedReason ?? busyReason;
  const noteReason = tuningLockSentence(safetyLock.lockedReason, alertsLock.lockedReason);

  return (
    <div className="nx-safetune-stack">
      <ListRow
        icon={<NxIcon name="safety" size={16} />}
        title="LIVE SAFETY STATUS"
        sub={LIVE_LINK_SUB}
        chevron
        onPress={() => nav.sheet("safety")}
        data-testid="safety-tuning-live-link"
      />

      <Card data-testid="safety-preset-card">
        <div className="nx-safetune-col">
          <Label size={11}>Safety preset</Label>
          {safety ? (
            <>
              <Segmented
                options={PRESET_ORDER.map((p) => ({
                  value: p,
                  label: PRESET_LABEL[p],
                  // CUSTOM is a REPORT, not a choice: the server derives it
                  // from the numbers, so there is nothing to send that produces
                  // it. Shown, so the current state is visible; locked with the
                  // reason, so pressing it is not a silent no-op.
                  lockedReason: p === "custom" ? CUSTOM_NOT_PICKABLE : null,
                }))}
                value={safety.preset}
                onChange={(v: PresetName) => void pick(v)}
                label="Safety preset"
                lockedReason={presetLock}
                onExplain={safetyLock.onExplain}
                data-testid="safety-preset-seg"
              />
              <Mono size={10.5} tone="dim">
                {PRESET_BLURB[safety.preset] ?? PRESET_BLURB.custom}
              </Mono>
              <Mono size={10.5} tone="dim" data-testid="safety-preset-line">
                {presetLine(safety)}
              </Mono>
              <Mono size={10} tone="dim">{PRESET_HINT}</Mono>
              {err && <Mono size={10.5} tone="bad">{err}</Mono>}
            </>
          ) : (
            <EmptyCard
              title="NO SAFETY BLOCK YET"
              hint="The rig has not sent its safety configuration, so the preset cannot be read or set."
              data-testid="safety-preset-empty"
            />
          )}
        </div>
      </Card>

      <ListRow
        icon={<NxIcon name="gauge" size={16} />}
        title="WHEN A LIMIT TRIPS"
        sub={TRIP_LINK_SUB}
        chevron
        onPress={() => nav.sheet("safety")}
        data-testid="safety-tuning-trip-link"
      />
      <ListRow
        icon={<NxIcon name="horizon" size={16} />}
        title="LIMITS THE RIG ENFORCES"
        sub={LIMITS_LINK_SUB}
        chevron
        onPress={() => nav.sheet("safety")}
        data-testid="safety-tuning-limits-link"
      />
      <ListRow
        icon={<NxIcon name="camera" size={16} />}
        title="WARM RAMP"
        sub={WARM_LINK_SUB}
        chevron
        onPress={() => nav.sheet("camera")}
        data-testid="safety-tuning-warm-link"
      />

      {/* The editor prints no note of its own here: this sheet edits two
          capabilities and prints one sentence covering both, below. */}
      <EscalationEditor showLockNote={false} />

      <LockNote reason={noteReason} data-testid="safety-tuning-lock" />
    </div>
  );
}
