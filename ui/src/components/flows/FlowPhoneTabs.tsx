// FlowPhoneTabs.tsx — the phone editor's bottom tab bar. §C.15, README §5,
// ref `10-phone-flow-autograph-390px.png` / `11-phone-monitor-390px.png`.
//
// Exactly three tabs, in this order: FLOW · CANVAS · MONITOR. Default FLOW.
// Active = a 2px `--accent` TOP border plus accent ink — two cues, so the state
// is never carried by colour alone.
//
// SWITCHING TABS CLEARS `tapWire` (§C.15). An arm is a half-finished sentence
// that only the FLOW tab can finish: leaving it set while the operator is on
// MONITOR means the next port they touch, minutes later, completes a wire they
// have forgotten starting.
//
// `role="tab"` and NOT `SegmentedControl`: the harness resolves its phone-tab
// step as `get_by_role("tab").or_(get_by_role("button"))` and the house
// segmented control emits `role="radio"`, which matches neither — the same
// finding the Tonight panel's tabs record.
import type { JSX } from "react";
import { useCallback } from "react";

import { useStore } from "../../store";
import type { FlowPhoneTab } from "./flowsTypes";
import { handleRadioKeyDown, rovingTabIndex } from "../../lib/radiogroup";

const TABS: { id: FlowPhoneTab; label: string }[] = [
  { id: "flow", label: "FLOW" },
  { id: "canvas", label: "CANVAS" },
  { id: "monitor", label: "MONITOR" },
];

export default function FlowPhoneTabs(): JSX.Element {
  const tab = useStore((s) => s.flows.ui.phoneTab);
  const setUi = useStore((s) => s.flowsSetUi);

  const go = useCallback(
    (id: FlowPhoneTab) => setUi({ phoneTab: id }),
    [setUi],
  );

  return (
    <nav
      role="tablist"
      aria-label="Flow editor"
      className="flex-none flex border-t border-line bg-raise/80 backdrop-blur-[10px]"
      // The bar sits on the home-indicator edge; without this the last row of
      // pixels is under the gesture area on every modern phone.
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
    >
      {TABS.map((t, i) => {
        const on = tab === t.id;
        const active = TABS.findIndex((x) => x.id === tab);
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={on}
            // Roving tabindex: one stop for the whole bar, arrows move within
            // it — the house key map, shared with every other radio/tab group.
            tabIndex={rovingTabIndex(i, active)}
            onKeyDown={(e) => handleRadioKeyDown(e, i, TABS.length, (n) => go(TABS[n].id))}
            onClick={() => go(t.id)}
            className={`flex-1 h-[50px] flex items-center justify-center cursor-pointer
              border-t-2 font-display font-semibold text-[10px] tracking-[0.16em]
              ${on ? "border-accent text-accent" : "border-transparent text-dim"}`}
          >
            {t.label}
          </button>
        );
      })}
    </nav>
  );
}
