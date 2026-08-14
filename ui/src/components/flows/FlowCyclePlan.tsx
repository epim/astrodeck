// FlowCyclePlan.tsx — the FILTER CYCLE slot table, one row per filter in the rig.
//
// The 2026-08-14 export replaced a text box with this: "one row per filter in
// the RIG'S WHEEL (from the equipment panel - never a hand-typed filter name),
// checkbox to include + right-aligned exposure field (s) when included; row
// order follows the wheel".
//
// It is the only control on this surface that is neither a select nor a text
// box, and it earns that by REMOVING a way to be wrong rather than adding a
// capability: a filter name typed by hand can name a slot the wheel does not
// have, and the run finds out in the dark. See cyclePlanRows.ts for the row
// model and for why blackout slots are not offered.
import { useStore } from "../../store";
import { cyclePlanRows, resolveWheel, setSlotExposure, toggleSlot } from "./cyclePlanRows";

export default function FlowCyclePlan({ nodeId, fieldKey, value }: {
  nodeId: string;
  fieldKey: string;
  value: string | number | undefined;
}) {
  const setParam = useStore((s) => s.flowsSetParam);
  // Narrow selectors, one primitive each: this row re-renders when the WHEEL
  // changes, not on every status frame the rig publishes.
  const names = useStore((s) => s.status?.filterwheel?.names);
  const opaque = useStore((s) => s.status?.filterwheel?.opaque);

  const { filters: wheel, fromRig } = resolveWheel(names, opaque);
  const rows = cyclePlanRows(wheel, value);
  const write = (next: string) => setParam(nodeId, fieldKey, next);

  return (
    <div className="flex flex-col gap-1" data-flow-cycleplan={nodeId}>
      {rows.map((r) => (
        <div key={r.filter} className="flex items-center gap-2 min-w-0">
          <label className="flex items-center gap-2 min-w-0 flex-1 cursor-pointer">
            <input
              type="checkbox"
              className="flex-none accent-[var(--accent)] w-[15px] h-[15px]"
              checked={r.on}
              onChange={() => write(toggleSlot(wheel, value, r.filter))}
              aria-label={`${r.filter} in the cycle`}
            />
            <span className="font-mono text-[11px] truncate">{r.filter}</span>
          </label>
          {/* The exposure box appears only for a ticked row. An exposure on a
              slot nobody selected is a number with nothing to spend it on, and
              it would read as though the filter were in the cycle. */}
          {r.on ? (
            <span className="flex items-center gap-1 flex-none">
              <input
                type="text"
                inputMode="numeric"
                className="field !text-[11px] !py-[5px] !px-[7px] w-[54px] text-right"
                value={r.exposure}
                onChange={(e) => write(setSlotExposure(wheel, value, r.filter, e.target.value))}
                aria-label={`${r.filter} exposure, seconds`}
              />
              <span className="font-mono text-[10px] text-faint">s</span>
            </span>
          ) : (
            <span className="font-mono text-[10px] text-faint flex-none">off</span>
          )}
        </div>
      ))}
      {/* SAY WHERE THE ROWS CAME FROM. With no wheel attached these are the
          handoff's default set, and an operator who reads them as their own rig
          would build a cycle around filters they do not own. */}
      {!fromRig && (
        <span className="font-mono text-[10px] text-faint leading-snug">
          no named filters from the wheel - showing the default set
        </span>
      )}
    </div>
  );
}
