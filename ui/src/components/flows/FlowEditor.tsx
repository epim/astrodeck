// FlowEditor.tsx — the editor host (contract §A.1, §C.4, §C.13, §F.1).
//
// It owns three markers and one layout decision, and nothing else. Every
// surface it places already exists and already owns its own store reads, so
// this file subscribes to exactly two primitives: the run phase (which is a
// marker value) and the phone tab (which is a layout branch). A node status
// tick, a pan, a param edit and a log line all re-render nothing here.
//
// WHY THE OVERLAYS ARE MOUNTED HERE. `FlowEditSheet`, `FlowPaletteSheet` and
// `TonightPanel` each read their own open-flag and render null when closed, so
// they cost nothing mounted — but SOMETHING has to mount them, and until this
// file landed nothing did. They portal through `Overlay` (never `fixed inset-0`
// inside the view tree), so their position in this tree is irrelevant to where
// they paint; what matters is that they are inside the editor's lifetime, so
// closing the editor cannot leave a sheet stranded over the library.
//
// WHY `onPick` IS COMPUTED HERE. `FlowPalette` deliberately refuses to guess a
// drop position (§C.7): only the editor knows whether a canvas is mounted and
// where its centre is. `flowCanvasDropPoint(tier)` reads the live canvas box;
// its documented fallback, `PALETTE_FALLBACK_DROP`, is the prototype's (120,120)
// and stacks stages on top of each other, which is why the real point is tried
// first.
import { useCallback, type JSX } from "react";

import { useStore } from "../../store";
import type { FlowNodeType } from "./flowsTypes";
import type { FlowTier } from "./geometry";
import FlowCanvas, { flowCanvasDropPoint } from "./FlowCanvas";
import { FlowPalette, PALETTE_FALLBACK_DROP } from "./FlowPalette";
import FlowPaletteSheet from "./FlowPaletteSheet";
import FlowInspector from "./FlowInspector";
import FlowEditSheet from "./FlowEditSheet";
import TonightPanel from "./TonightPanel";
import FlowPhoneTabs from "./FlowPhoneTabs";
import FlowPhoneGraph from "./FlowPhoneGraph";
import FlowPhoneMonitor from "./FlowPhoneMonitor";
import FlowTapWireBar from "./FlowTapWireBar";

export interface FlowEditorProps {
  /** Resolved once by `FlowsView` (§C.1) and threaded down, so only one
   *  component in the tree subscribes to the two media queries. */
  tier: FlowTier;
}

export default function FlowEditor({ tier }: FlowEditorProps): JSX.Element {
  // Narrow, primitive-returning selectors. `phase` is a string that is ALSO the
  // harness's marker value (§F.1: state 07 waits ≤180 s for
  // `[data-flows-run='holding']`), so it has to be subscribed even though
  // nothing else on this surface reads it.
  const phase = useStore((s) => s.flows.run.phase);
  const phoneTab = useStore((s) => s.flows.ui.phoneTab);
  const addNode = useStore((s) => s.flowsAddNode);

  const phone = tier === "phone";

  const onPick = useCallback(
    (type: FlowNodeType) => {
      addNode(type, flowCanvasDropPoint(tier) ?? PALETTE_FALLBACK_DROP);
    },
    [addNode, tier],
  );

  return (
    <div
      data-flows-tab="editor"
      // README §IA names all three strings; the phone one is distinct because
      // the phone surface is a different editor, not a narrow one.
      data-screen-label={phone ? "Flow editor (phone)" : "Flow editor"}
      data-flows-run={phase}
      className="fill-grow flex flex-col min-h-0 min-w-0"
    >
      {phone ? (
        // PHONE — three tabs, one of which IS the tablet/desktop canvas
        // (README §5: "identical to tablet/desktop canvas"). The tab bar is
        // outside the scroll region so it never scrolls away.
        <>
          <div className="flex-1 flex min-h-0 min-w-0 relative">
            {phoneTab === "flow" && <FlowPhoneGraph />}
            {phoneTab === "canvas" && <FlowCanvas tier={tier} />}
            {phoneTab === "monitor" && <FlowPhoneMonitor />}
            {/* Armed tap-to-wire is a property of the FLOW tab only; the bar
                renders null unless `tapWire` is set, and switching tabs clears
                it (§C.15). */}
            <FlowTapWireBar />
          </div>
          <FlowPhoneTabs />
        </>
      ) : (
        <div className="flex-1 flex min-h-0 min-w-0">
          {/* DESKTOP — palette rail ∣ canvas ∣ inspector column. At tablet the
              rail and the column are both replaced by sheets: the canvas's own
              `+ ADD STAGE` float opens the palette, and the node's ✎ opens the
              edit sheet (README §3: the ✎ is "the ONLY thing that opens the
              edit sheet on tablet/phone"). */}
          {tier === "desktop" && <FlowPalette variant="rail" onPick={onPick} />}
          <FlowCanvas tier={tier} />
          {tier === "desktop" && <FlowInspector variant="column" />}
        </div>
      )}

      <FlowPaletteSheet onPick={onPick} />
      <FlowEditSheet />
      <TonightPanel />
    </div>
  );
}
