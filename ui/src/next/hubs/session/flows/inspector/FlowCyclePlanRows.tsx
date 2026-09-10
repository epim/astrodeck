// FlowCyclePlanRows.tsx - the FILTER CYCLE slot table, one row per filter in
// the rig's wheel (parity row A13).
//
// NOTHING HERE IS TYPED BY THE OPERATOR. The rows come from
// `status.filterwheel.names` / `.opaque` through `resolveWheel`, and the plan
// string is written by `toggleSlot` / `setSlotExposure`. A filter name is not a
// label - it lands in the FITS `FILTER` header, in the saved filename and in the
// calibration matcher's key - so a hand-typed `OIII` on a wheel whose slot reads
// `Oiii` asks for a filter that does not exist and the run finds out at 21:00.
// The whole row model, the blackout-slot exclusion and the default exposures
// live in `components/flows/cyclePlanRows.ts` and are not re-derived here.
//
// WHY THE EXPOSURE IS A `TextInput` AND NOT A `NumberField`. `NumberField`
// renders a per-row eyebrow, and seven identical "SECONDS" captions down a
// seven-row table is copy that carries nothing the column heading does not -
// the wave's own rule about strings. The behaviour `NumberField` exists to
// guarantee is preserved where the legacy surface already keeps it: the draft
// is held here and only committed on blur or Enter, and the commit goes through
// `setSlotExposure`, which REFUSES anything that is not a positive integer and
// leaves the stored plan alone. A cleared box therefore never commits a
// zero-second slot, which `to_plan` would refuse at RUN on a graph that looked
// fine on screen.

import { useState, type CSSProperties, type JSX } from "react";

import { useStore } from "../../../../../store";
import {
  cyclePlanRows, resolveWheel, setSlotExposure, toggleSlot,
} from "../../../../../components/flows/cyclePlanRows";
import { Checkbox22, Label, Mono, TextInput } from "../../../../ui";
import { filterInk } from "./fieldModel";

export interface FlowCyclePlanRowsProps {
  nodeId: string;
  fieldKey: string;
  value: string | number | undefined;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
}

export function FlowCyclePlanRows({
  nodeId, fieldKey, value, lockedReason = null, onExplain,
}: FlowCyclePlanRowsProps): JSX.Element {
  const setParam = useStore((s) => s.flowsSetParam);
  // Narrow selectors, one primitive each: this table re-renders when the WHEEL
  // changes, not on every status frame the rig publishes.
  const names = useStore((s) => s.status?.filterwheel?.names);
  const opaque = useStore((s) => s.status?.filterwheel?.opaque);
  // Per-slot text the operator is part way through typing. Keyed by filter, so
  // clearing one box to retype it cannot disturb another slot's stored value.
  const [draft, setDraft] = useState<Record<string, string>>({});

  const { filters: wheel, fromRig } = resolveWheel(names, opaque);
  const rows = cyclePlanRows(wheel, value);
  const write = (next: string) => setParam(nodeId, fieldKey, next);

  return (
    <div className="nx-cycplan" data-flow-cycleplan={nodeId}>
      <div className="nx-cycplan-head">
        <Label size={10}>FILTER</Label>
        <Label size={10}>SECONDS</Label>
      </div>

      {rows.map((r) => {
        const ink = filterInk(r.filter);
        const shown = draft[r.filter] ?? r.exposure;
        const commit = (raw: string) => {
          setDraft((d) => {
            const next = { ...d };
            delete next[r.filter];
            return next;
          });
          write(setSlotExposure(wheel, value, r.filter, raw));
        };
        return (
          <div className="nx-cycrow" key={r.filter} data-on={r.on ? "true" : "false"}>
            <Checkbox22
              checked={r.on}
              onChange={() => write(toggleSlot(wheel, value, r.filter))}
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid={`cycle-slot-${r.filter}`}
              label={(
                <span className="nx-cycrow-name">
                  <span
                    className="nx-cycrow-dot"
                    style={ink ? ({ "--nx-filter-ink": ink } as CSSProperties) : undefined}
                    aria-hidden="true"
                  />
                  {r.filter}
                </span>
              )}
            />
            {/* The exposure box appears only for a ticked row. An exposure on a
                slot nobody selected is a number with nothing to spend it on, and
                it would read as though the filter were in the cycle. */}
            {r.on ? (
              <span className="nx-cycrow-exp">
                <TextInput
                  value={shown}
                  mono
                  ariaLabel={`${r.filter} exposure, seconds`}
                  lockedReason={lockedReason}
                  onChange={(next) => setDraft((d) => ({ ...d, [r.filter]: next }))}
                  onBlur={commit}
                  onEnter={commit}
                  data-testid={`cycle-exp-${r.filter}`}
                />
                <span className="nx-flowfield-unit" aria-hidden="true">s</span>
              </span>
            ) : (
              <span className="nx-cycrow-off">
                <Mono size={10} tone="dim">off</Mono>
              </span>
            )}
          </div>
        );
      })}

      {/* SAY WHERE THE ROWS CAME FROM. With no wheel attached these are the
          handoff's default set, and an operator who read them as their own rig
          would build a cycle around filters they do not own. */}
      {!fromRig && (
        <p className="nx-cycplan-note">
          no named filters from the wheel - showing the default set
        </p>
      )}
    </div>
  );
}

export default FlowCyclePlanRows;
