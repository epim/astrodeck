// FlowsCanvasHost.tsx - the Flows canvas inside the hub body, at 768 px and up
// (plan section D.3; ARCHITECTURE.md section 4, "the Flows canvas fills the hub
// body").
//
// `components/flows/FlowsView` IS the route entry: it renders `FlowHeader`, the
// library or the editor, and keeps `FlowWizard` and `QuickFlow` mounted above
// them (`FlowsView.tsx:48-71`). It takes no props and reads its own state, so
// mounting it whole is the whole of this file - and that is the point. The
// canvas, its pan/zoom/wire gestures, the inspector, the palette, the doctor
// chip, the TONIGHT panel and the run controls all arrive intact because
// nothing here re-implements any of them.
//
// DEVIATION D11, NAMED. `FlowHeader` is on the layout-bound list: it "explicitly
// says it duplicates content already in App.tsx's outer header ... a new layout
// has to make a real decision here" (`inventory-session-monitor.md` section 9).
// The decision for this wave is to carry it, because it holds LIBRARY, PLAN, the
// provider badge, the three-state validation chip, the ETA, TONIGHT and RUN -
// none of which the next shell's header has. Dropping it to avoid a duplicated
// wordmark would drop seven controls to fix a cosmetic overlap. Flagged for the
// hub-8 chrome pass.
//
// THE WAY OUT IS EXPLICIT. `FlowHeader`'s own LIBRARY button returns to the
// canvas's library screen, not to this hub's list, so without the row below an
// operator who opened a flow would have no visible way back to MY FLOWS - only
// the sub-nav chip, which does not look like a back button.

import { useEffect, type JSX } from "react";

import FlowsView from "../../../../components/flows/FlowsView";
import { useStore } from "../../../../store";
import { nav } from "../../../router";

/** `?open=library` opens the canvas on its library screen rather than on a
 *  flow. A real flow id opens that flow. */
export const CANVAS_LIBRARY = "library";

export interface FlowsCanvasHostProps {
  /** The `?open=` route param: a flow id, or `library`. */
  open: string;
}

export function FlowsCanvasHost({ open }: FlowsCanvasHostProps): JSX.Element {
  const flowsOpen = useStore((s) => s.flowsOpen);
  const flowsSetUi = useStore((s) => s.flowsSetUi);
  const openId = useStore((s) => s.flows.record?.id ?? null);

  useEffect(() => {
    if (!open || open === CANVAS_LIBRARY) {
      flowsSetUi({ screen: "library" });
      return;
    }
    // Idempotent: re-opening the flow already on the canvas would discard an
    // unsaved edit and re-run the compile for nothing.
    if (openId === open) return;
    void flowsOpen(open);
  }, [open, openId, flowsOpen, flowsSetUi]);

  return (
    <div
      data-testid="session-flows-canvas"
      style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1, gap: 8 }}
    >
      <button
        type="button"
        data-testid="flows-canvas-back"
        className="nx-sheet-back"
        style={{ alignSelf: "flex-start" }}
        onClick={() => nav.go("/session/flows")}
        aria-label="Back to my flows"
      >
        <span aria-hidden="true">&lsaquo; </span>MY FLOWS
      </button>
      <FlowsView />
    </div>
  );
}

export default FlowsCanvasHost;
