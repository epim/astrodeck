// CalibrationTolerancesEditor.tsx - how far a master may be from the light it
// corrects, and how the stacker combines them (wave R7, T-R7-14; plan section
// 3.F16, cutover table section 7).
//
// It replaces `components/settings/CalibrationTolerancesPanel.tsx` at its one
// mount inside the new UI, `settings/sheets/CalibrationSheet.tsx`. The legacy
// file is untouched; `#/classic` still renders it.
//
// WHY THIS ONE KEEPS ITS SAVE BUTTON while the escalation editor next door lost
// hers. These five numbers are not independent: `temp_bin_c` must be at least
// `temp_tol_c`, and the server refuses the write when it is not. Committing
// each field the instant it blurs would make the ORDER of two edits decide
// whether they are accepted - raising the tolerance before the bin would be
// rejected, raising the bin first would not - and the user has no way to know
// that. A draft lets both numbers move together and states the rule while they
// are being typed. The relational check runs here for the live warning AND
// server-side in `set_calibration`, because a warning the user can click past
// is not a guard.
//
// WHAT DID CHANGE.
//
// 1. NO NATIVE `disabled`. The legacy panel disabled all five inputs, SAVE and
//    RESET (`disabled={busy || ro}`, `disabled={busy || !dirty || binTooNarrow}`).
//    Every one is now `lockedReason` + `onExplain`, so a press says which of the
//    four different reasons applies rather than presenting one grey rectangle
//    for all of them.
// 2. `Number(e.target.value)` PER KEYSTROKE IS GONE. The legacy input parsed
//    and clamped on every change, so typing "0.5" into the tolerance clamped
//    "0." to the minimum mid-word and typing "" wrote the minimum. `NumberField`
//    holds its own text and parses at blur or Enter, and rejects a blank rather
//    than reading it as a number.
// 3. NO `sm:grid-cols-2`. The rows are one column that cannot overflow a 360 px
//    sheet.

import { useEffect, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setCalibrationConfig } from "../../../../../api/backends";
import { useConfig, useStore } from "../../../../../store";
import type { CalibrationConfig } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { useLock } from "../../../../lib/gateHook";
import { isLocalOnly, LOCAL_ONLY_REASON } from "../../../../lib/gate";
import { ActionButton, Card, Label, LockNote, Mono, NumberField } from "../../../../ui";
import {
  binTooNarrow, binWarning, TOL_ALREADY_DEFAULT_REASON, TOL_BIN_REASON, TOL_BUSY_REASON,
  TOL_CLEAN_REASON, TOL_DEFAULTS, TOL_DIRTY_NOTE, TOL_INTRO, TOL_RESET_LABEL, TOL_SAVE_FAILED,
  TOL_SAVE_LABEL, TOL_SAVED_NOTE, TOL_TITLE, toleranceForbidden, toleranceLockSentence,
  TOLERANCE_FIELDS, toleranceSummary,
} from "./calibrationModel";

export function CalibrationTolerancesEditor(): JSX.Element {
  const config = useConfig();
  // Absent on the WS bootstrap payload until the block has been written once -
  // fall back to the same defaults the server model declares, so the editor is
  // never blank on a cold open.
  const stored = config?.calibration ?? TOL_DEFAULTS;
  // `needsLan`: SAVE and RESET write `POST /api/config/calibration`, on the rig's
  // LAN-only fence (`app.py` `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES`, /api/config).
  const lock = useLock({ cap: "config.site_optics", needsLan: true });

  const [draft, setDraft] = useState<CalibrationConfig>(stored);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const seed = JSON.stringify(stored);
  useEffect(() => {
    setDraft(JSON.parse(seed) as CalibrationConfig);
    setErr(null);
    setSaved(false);
  }, [seed]);

  const dirty = JSON.stringify(draft) !== seed;
  const atDefaults = JSON.stringify(draft) === JSON.stringify(TOL_DEFAULTS);
  const narrow = binTooNarrow(draft);

  const patch = (p: Partial<CalibrationConfig>): void =>
    setDraft((d) => ({ ...d, ...p }));

  const save = async (): Promise<void> => {
    if (busy || !dirty || narrow) return;
    setErr(null);
    setBusy(true);
    try {
      await setCalibrationConfig(draft);
      await useStore.getState().loadConfig();
      setSaved(true);
    } catch (e) {
      // `isLocalOnly` FIRST. A `local_only` 403 is the relay fence, not a
      // capability this caller is missing, and `toleranceForbidden()` would
      // tell an admin they need admin access.
      setErr(isLocalOnly(e) ? LOCAL_ONLY_REASON
        : e instanceof ApiError
          ? (e.status === 403 ? toleranceForbidden() : e.message || TOL_SAVE_FAILED)
          : TOL_SAVE_FAILED);
    } finally {
      setBusy(false);
    }
  };

  const busyReason = busy ? TOL_BUSY_REASON : null;
  const fieldLock = lock.lockedReason ?? busyReason;
  const saveLock = fieldLock ?? (narrow ? TOL_BIN_REASON : null) ?? (dirty ? null : TOL_CLEAN_REASON);
  const resetLock = fieldLock ?? (atDefaults ? TOL_ALREADY_DEFAULT_REASON : null);
  const noteReason = lock.lockedReason ? toleranceLockSentence(lock.lockedReason) : null;

  return (
    <Card data-testid="cal-tolerances">
      <div className="nx-cal-col">
        <div className="nx-cal-head">
          <Label size={11}>{TOL_TITLE}</Label>
          <Mono size={10}>{toleranceSummary(draft)}</Mono>
        </div>

        <p className="nx-cal-intro">{TOL_INTRO}</p>

        <div className="nx-cal-fields">
          {TOLERANCE_FIELDS.map((f) => (
            <NumberField
              key={f.key}
              data-testid={`cal-tolerance-${f.key}`}
              label={f.label}
              unit={f.unit}
              hint={f.hint}
              ariaLabel={f.aria}
              value={draft[f.key]}
              min={f.min}
              max={f.max}
              step={f.step}
              integer={f.integer}
              lockedReason={fieldLock}
              onExplain={lock.onExplain}
              onCommit={(n) => patch({ [f.key]: n } as Partial<CalibrationConfig>)}
            />
          ))}
        </div>

        {narrow && (
          <p className="nx-cal-warn" data-testid="cal-bin-warning">
            <NxIcon name="info" size={12} />
            {binWarning(draft)}
          </p>
        )}

        <div className="nx-cal-actions">
          <ActionButton
            kind="primary"
            data-testid="cal-save"
            busy={busy}
            lockedReason={saveLock}
            onExplain={lock.onExplain}
            onPress={() => { void save(); }}
          >
            {TOL_SAVE_LABEL}
          </ActionButton>
          <ActionButton
            kind="ghost"
            data-testid="cal-reset"
            lockedReason={resetLock}
            onExplain={lock.onExplain}
            onPress={() => { setDraft({ ...TOL_DEFAULTS }); }}
          >
            {TOL_RESET_LABEL}
          </ActionButton>
        </div>

        {dirty && !busy && (
          <p className="nx-cal-warn" data-testid="cal-dirty">{TOL_DIRTY_NOTE}</p>
        )}
        {saved && !dirty && !busy && err == null && (
          <p className="nx-cal-good" data-testid="cal-saved">{TOL_SAVED_NOTE}</p>
        )}
        {err != null && (
          <p className="nx-cal-err" data-testid="cal-tol-error">
            <NxIcon name="info" size={12} />
            {err}
          </p>
        )}

        <LockNote reason={noteReason} data-testid="cal-tol-lock" />
      </div>
    </Card>
  );
}
