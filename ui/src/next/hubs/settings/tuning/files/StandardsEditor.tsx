// StandardsEditor.tsx - the IMAGING STANDARDS sheet's body, rebuilt in the
// design's own vocabulary (wave R7, T-R7-13; replaces the mounted
// `components/settings/StandardsPanel.tsx`, which is NOT edited and keeps
// serving `#/classic`).
//
// WHY THE PANEL EXISTS AT ALL (#239 stage A). These seven settings lived on the
// PLAN, which meant a flow-built night could not have them: `flows/to_plan.py`
// sets eight plan fields and every quality gate took the model default of
// "off", so a graph-drawn night ran with no star floor, no guide-RMS ceiling,
// no eccentricity ceiling and no temp-drift refocus, silently. Here they apply
// to EVERY night, and a plan may still override any one of them for a single
// night (`null` on the plan means "inherit what is set here").
//
// THE FIELD LIST IS NOT RESTATED. `lib/standards.ts`'s
// `STANDARDS_NUMBER_FIELDS` carries the keys, labels, units and hints, and an
// anti-drift test compares that list with `server/astrodeck/config.py`. This
// file renders the list; typing it out again here would be a second list for
// the drift test to miss.
//
// THE FOOTER LINK NOW CALLS `nav.sheet("planEditor")` DIRECTLY. The legacy
// button called `useStore().setView("sequence")`, which `ARCHITECTURE.md`
// section 9 forbids from the new UI - it worked only because
// `next/legacyBridge.ts:54` maps that view name back to a hash. The bridge
// mapping STAYS (it is what `#/classic` and the legacy panel still need); this
// rebuild simply stops going the long way round.

import { useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import { setStandardsConfig } from "../../../../../api/backends";
import { accessPhrase } from "../../../../../lib/caps";
import { STANDARDS_NUMBER_FIELDS, standardsOrDefault } from "../../../../../lib/standards";
import type { StandardsConfig } from "../../../../../types";
import { useConfig, useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { useLock } from "../../../../lib/gateHook";
import { Card, Label, LockNote, NumberField, Switch } from "../../../../ui";
import {
  filesLockSentence, OFFSETS_LABEL, OFFSETS_NOTE, PLAN_LINK_AFTER, PLAN_LINK_BEFORE,
  PLAN_LINK_LABEL, plainDashes, STANDARDS_BLURB, STANDARDS_INPUT, STANDARDS_SUBJECT,
} from "./filesModel";
import "./files.css";

export function StandardsEditor(): JSX.Element {
  const config = useConfig();
  const saved = standardsOrDefault(config?.standards);

  const gate = useLock({ cap: "config.safety" });
  const lock = filesLockSentence(
    gate.lockedReason, accessPhrase("config.safety"), STANDARDS_SUBJECT,
  );
  const explain = gate.onExplain;

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async (next: StandardsConfig): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      // Wholesale replace: `POST /api/config {standards}` swaps the whole
      // block, so a partial body would drop the fields this editor is not
      // touching back to their defaults.
      await setStandardsConfig(next);
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (e.status === 403 ? `Refused - ${accessPhrase("config.safety")} is needed here.`
          : e.message)
        : "Could not save.");
    } finally { setBusy(false); }
  };

  return (
    <div className="nx-files-stack" data-testid="standards-editor">
      <Card>
        <div className="nx-files-col">
          <Label size={11}>WHAT COUNTS AS A USABLE FRAME</Label>
          <p className="nx-files-note">{STANDARDS_BLURB}</p>

          <div className="nx-files-grid">
            {STANDARDS_NUMBER_FIELDS.map((f) => {
              const shape = STANDARDS_INPUT[f.key] ?? { integer: false, step: 1 };
              return (
                <NumberField
                  key={f.key as string}
                  label={f.label}
                  ariaLabel={f.unit ? `${f.label}, ${f.unit}` : f.label}
                  value={saved[f.key] as number}
                  onCommit={(n) => void save({ ...saved, [f.key]: n })}
                  unit={f.unit}
                  min={0}
                  max={shape.max}
                  step={shape.step}
                  integer={shape.integer}
                  hint={plainDashes(f.hint)}
                  lockedReason={lock}
                  onExplain={explain}
                  data-testid={`standards-${f.key as string}`}
                />
              );
            })}
          </div>
        </div>
      </Card>

      <Card>
        <Switch
          checked={saved.apply_filter_offsets}
          onChange={(v) => void save({ ...saved, apply_filter_offsets: v })}
          label={OFFSETS_LABEL}
          note={OFFSETS_NOTE}
          lockedReason={lock}
          onExplain={explain}
          data-testid="standards-offsets"
        />
      </Card>

      {/* The plan editor's door (#239 stage B). The sentence above says a plan
          can override these, so this one has to say WHERE - and get there. */}
      <p className="nx-files-note" data-testid="standards-plan-note">
        {PLAN_LINK_BEFORE}
        <button
          type="button"
          className="nx-files-link"
          onClick={() => nav.sheet("planEditor")}
          data-testid="standards-plan-link"
        >
          {PLAN_LINK_LABEL}
        </button>
        {PLAN_LINK_AFTER}
      </p>

      {err && <p className="nx-files-err" data-testid="standards-error">{err}</p>}
      <LockNote reason={lock} data-testid="standards-lock" />
    </div>
  );
}
