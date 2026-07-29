// CalibrationTolerancesPanel.tsx — how far a master dark/flat/bias may be from
// the light it is applied to, and how the stacker combines them (PRO-1).
//
// Config-file-and-API-only until now. These decide whether last week's darks get
// reused tonight — and reusing a master from the wrong temperature is worse than
// having no master at all, because the result looks processed rather than
// obviously broken.
//
// The relational rule (temp bin >= temp tolerance) is checked here for a live
// warning AND server-side in set_calibration, because a warning the user can
// click past is not a guard.

import { useEffect, useState, type JSX } from "react";
import type { CalibrationConfig } from "../../types";
import { setCalibrationConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field } from "../ui";
import { Icon } from "../icons";

const DEFAULTS: CalibrationConfig = {
  exposure_tol_pct: 5,
  temp_tol_c: 2,
  temp_bin_c: 5,
  stack_sigma: 3,
  max_stack_frames: 100,
};

export default function CalibrationTolerancesPanel(): JSX.Element {
  const config = useConfig();
  // Absent on the WS bootstrap payload — fall back to the same defaults the
  // server model declares, so the panel is never blank on a cold open.
  const stored = config?.calibration ?? DEFAULTS;
  const canEdit = useCan("config.site_optics");

  const [draft, setDraft] = useState<CalibrationConfig>(stored);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const seed = JSON.stringify(stored);
  useEffect(() => {
    setDraft(JSON.parse(seed) as CalibrationConfig);
    setErr(null);
    setSavedAt(null);
  }, [seed]);

  const patch = (p: Partial<CalibrationConfig>) =>
    setDraft((d) => ({ ...d, ...p }));
  const dirty = JSON.stringify(draft) !== seed;
  const ro = !canEdit;

  // The one rule pydantic can't express: a bin narrower than the match tolerance
  // means two frames can match each other and still land in different stacks.
  const binTooNarrow = draft.temp_bin_c < draft.temp_tol_c;

  const save = async () => {
    if (busy || !dirty || binTooNarrow) return;
    setErr(null);
    setBusy(true);
    try {
      await setCalibrationConfig(draft);
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.status === 403
            ? `Changing these needs ${accessPhrase("config.site_optics")}.`
            : e.message || "Could not save."
          : "Could not save.",
      );
    } finally {
      setBusy(false);
    }
  };

  const num = (
    label: string, hint: string, key: keyof CalibrationConfig,
    min: number, max: number, step: number,
  ) => (
    <Field label={label} hint={hint}>
      <input
        className="field"
        type="number"
        min={min}
        max={max}
        step={step}
        value={draft[key]}
        disabled={busy || ro}
        onChange={(e) => {
          const raw = Number(e.target.value);
          patch({
            [key]: Number.isFinite(raw) ? Math.min(max, Math.max(min, raw)) : min,
          } as Partial<CalibrationConfig>);
        }}
      />
    </Field>
  );

  return (
    <Panel
      title="Matching and stacking"
      right={
        <span className="text-[11px] text-dim">
          {draft.exposure_tol_pct}% · ±{draft.temp_tol_c}°C
        </span>
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-1">
        How close a master has to be to the light it corrects before it is
        reused. Loosen these to get more nights out of one set of darks; tighten
        them if you see residual amp glow or mismatched noise.
      </p>

      <div className="grid gap-3 sm:grid-cols-2 pt-3 border-t border-line mt-3">
        {num(
          "Exposure tolerance (%)",
          "A master dark may differ from the light's exposure by this much. Dark current scales with time, so 5% is tight and 20% starts to show.",
          "exposure_tol_pct", 0, 100, 1,
        )}
        {num(
          "Temperature tolerance (°C)",
          "A master may differ from the light's sensor temperature by this much. Dark current roughly doubles every 6°C, so this is the setting that matters most.",
          "temp_tol_c", 0, 50, 0.5,
        )}
        {num(
          "Temperature bin (°C)",
          "Width of the bucket frames are grouped into when a master is built. Must be at least the tolerance above, or frames that matched end up in different stacks.",
          "temp_bin_c", 0, 50, 0.5,
        )}
        {num(
          "Sigma clip",
          "How far from the median a pixel may be before it is thrown out when combining. Lower rejects more aggressively — good against satellites, bad if you have few frames.",
          "stack_sigma", 0.5, 10, 0.5,
        )}
        {num(
          "Frames per master (max)",
          "Cap on how many frames go into one master. Past a point more frames buy almost no noise reduction and cost real memory.",
          "max_stack_frames", 1, 1000, 10,
        )}
      </div>

      {binTooNarrow && (
        <p className="text-[11px] text-bad inline-flex items-start gap-1.5 mt-3 max-w-xl">
          <Icon name="alert" size={13} className="shrink-0 mt-0.5" />
          The temperature bin ({draft.temp_bin_c}°C) is narrower than the match
          tolerance ({draft.temp_tol_c}°C). Two frames could match each other and
          still be stacked separately, halving the depth of every master. Raise
          the bin to at least {draft.temp_tol_c}°C.
        </p>
      )}

      {canEdit && (
        <div className="flex items-center gap-3 pt-4 border-t border-line mt-4">
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={save}
            disabled={busy || !dirty || binTooNarrow}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className="btn min-h-[44px] sm:min-h-0"
            onClick={() => setDraft({ ...DEFAULTS })}
            disabled={busy || JSON.stringify(draft) === JSON.stringify(DEFAULTS)}
          >
            Reset to defaults
          </button>
          {dirty && !busy && !binTooNarrow && (
            <span className="text-[11px] text-warn">Unsaved changes</span>
          )}
          {savedAt && !dirty && !busy && !err && (
            <span className="text-[11px] text-good inline-flex items-center gap-1.5">
              <Icon name="check" size={13} /> Saved
            </span>
          )}
        </div>
      )}

      {!canEdit && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-4">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            Changing these needs {accessPhrase("config.site_optics")}. The
            current settings are shown for reference.
          </span>
        </div>
      )}

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
    </Panel>
  );
}
