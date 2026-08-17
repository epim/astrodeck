// StandardsPanel.tsx — Settings: the rig's standards for a usable frame.
//
// WHY THIS PANEL EXISTS (#239 stage A). These seven settings lived on the PLAN,
// which meant a flow-built night could not have them at all: `flows/to_plan.py`
// sets eight plan fields and every quality gate took the model default of
// "off". So a graph-drawn night ran with no star floor, no guide-RMS ceiling,
// no eccentricity ceiling and no temp-drift refocus, silently, and there was
// nowhere in Flows to say otherwise. Here they apply to EVERY night.
//
// A plan may still override any of them for one night — `null` on the plan
// means "inherit what is set here", and the plan editor shows which is which.
//
// Honest-disabled (idiom §11.8): controls stay visible with aria-disabled and a
// VISIBLE reason rather than the native `disabled` attribute, which hides the
// reason from assistive tech.
import { useEffect, useState, type JSX } from "react";
import { setStandardsConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field, LockedNote, Toggle, lockedProps } from "../ui";
import {
  STANDARDS_NUMBER_FIELDS, standardsOrDefault,
} from "../../lib/standards";
import type { StandardsConfig } from "../../types";

export default function StandardsPanel(): JSX.Element {
  const config = useConfig();
  const canEdit = useCan("config.safety");
  const saved = standardsOrDefault(config?.standards);

  const LOCK = `Changing this needs ${accessPhrase("config.safety")}.`;

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Numbers are free text while typing and committed on blur, so a half-typed
  // value never round-trips to the server.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => {
    setDrafts(Object.fromEntries(
      STANDARDS_NUMBER_FIELDS.map((f) => [f.key, String(saved[f.key])])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.standards]);

  const save = async (next: StandardsConfig) => {
    if (busy || !canEdit) return;
    setBusy(true); setErr(null);
    try {
      await setStandardsConfig(next);
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? LOCK : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  const commitNumber = (key: keyof StandardsConfig) => {
    const raw = drafts[key as string];
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0) {
      setDrafts((d) => ({ ...d, [key]: String(saved[key]) }));
      return;
    }
    if (n === saved[key]) return;
    void save({ ...saved, [key]: n });
  };

  return (
    <Panel title="Imaging standards">
      <p className="text-xs text-dim mb-3">
        What counts as a usable frame on this rig, and when a night gives up.
        These apply to every night, including ones built in Flows. A plan can
        override any of them for a single night.
      </p>

      {/* `Field` is a flex COLUMN - label above, children below - so the input
          and its unit have to share one row of their own, or the unit lands on
          a line of its own under the box. `field` is the house input class;
          `input` is not one, which is why these first rendered as bare numbers
          with no box while every neighbouring panel had proper controls. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {STANDARDS_NUMBER_FIELDS.map((f) => (
          <Field key={f.key as string} label={f.label} hint={f.hint}>
            <span className="inline-flex items-center gap-2 min-w-0">
              <input
                type="text"
                inputMode="decimal"
                className="field !w-24"
                value={drafts[f.key as string] ?? ""}
                onChange={(e) => setDrafts(
                  (d) => ({ ...d, [f.key]: e.target.value }))}
                onBlur={() => commitNumber(f.key)}
                {...lockedProps(canEdit ? null : LOCK)}
              />
              {f.unit ? (
                <span className="text-xs text-dim shrink-0">{f.unit}</span>
              ) : null}
            </span>
          </Field>
        ))}
      </div>

      {/* Toggle's `label` is the ARIA label only - it renders nothing visible -
          so the row supplies its own text, the same shape SafetyLimitsPanel's
          "Ramp at all" row uses. Without this the panel showed a bare switch
          reading ON with no word saying what was on. `showState` because the
          panels either side of this one all show the ON/OFF word; state is
          never carried by colour alone here. */}
      <div className="flex items-start justify-between gap-3 mt-4 pt-3 border-t border-line2">
        <div className="min-w-0">
          <div className="text-sm text-ink">Apply per-filter focus offsets</div>
          <p className="text-[11px] text-dim max-w-md">
            Shift the focuser by the filter&apos;s stored offset when the wheel
            moves, so a filter change does not cost a refocus.
          </p>
        </div>
        <Toggle
          label="Apply per-filter focus offsets"
          checked={saved.apply_filter_offsets}
          onChange={(v) => void save({ ...saved, apply_filter_offsets: v })}
          showState
          {...lockedProps(canEdit ? null : LOCK)} />
      </div>

      {/* The plan editor's door (#239 stage B). Plan left the nav rail, so the
          sentence above about a plan overriding these has to say WHERE. */}
      <p className="text-xs text-dim mt-3">
        A single night can override any of these in the{" "}
        <button
          type="button"
          className="underline text-accent"
          onClick={() => useStore.getState().setView("sequence")}
        >
          plan editor
        </button>
        , where an overridden setting is marked and can be handed back to the rig.
      </p>

      {!canEdit && <LockedNote reason={LOCK} />}
      {err && <p className="text-xs text-bad mt-2">{err}</p>}
    </Panel>
  );
}
