// FlowsCanvasHost.tsx - the Flows canvas inside the hub body, at 768 px and up
// (plan section D.3; ARCHITECTURE.md section 4, "the Flows canvas fills the hub
// body"). Wave R7's cutover (T-R7-20) is what this file is.
//
// WHAT CHANGED, AND WHY IT IS A CUTOVER RATHER THAN A RESTYLE. Until now this
// file mounted `components/flows/FlowsView` whole: one legacy component that
// rendered `FlowHeader`, then EITHER the library OR the editor, with `FlowWizard`
// and `QuickFlow` kept mounted above them. That bought the whole canvas in one
// line, and it cost three things this wave closes:
//
//   * a SECOND LIBRARY. At tablet and desktop `FlowsView` opened on its own MY
//     FLOWS grid, stacked on top of the hub's own list - two screens with the
//     same title, the same rows and different chrome (wave R7 section 6.1
//     defect 7). The canvas now opens on a FLOW and never on a library; the
//     hub's `FlowsScreen` is the one library at every breakpoint.
//   * DUPLICATED SHELL CHROME. `FlowHeader` carried a wordmark, a provider badge
//     and a library count that `shell/Header.tsx` and the FLOWS sub-nav chip
//     already render at every breakpoint. The rebuilt toolbar carries the six
//     things the shell does NOT have (section 4).
//   * FOUR OVERLAYS DRIVEN BY BOOLEANS. `ui.wizardOpen`, `ui.quickOpen`,
//     `ui.tonightOpen` and `ui.paletteOpen` are store flags that no route can
//     name, so none of those screens survived a reload or a share. They are
//     sheets now, which is route state.
//
// THE LAYOUT. One 48 px toolbar over a row: the palette rail (desktop only, it
// is a docked 192 px column), the canvas surface, and the inspector column
// (desktop only, a docked 284 px column). At tablet the surface carries its own
// floating + ADD STAGE and the inspector is reached through the pencil on a
// stage card, which opens the `flowNode` sheet in the right-hand panel - so
// neither is lost, they are just not docked where there is no room for them.
//
// THE WAY OUT IS STILL EXPLICIT. BACK to MY FLOWS is a button, not only the
// sub-nav chip, because a chip does not look like a back button.

import { useEffect, type JSX } from "react";

import { useStore } from "../../../../store";
import { buildHash, nav, useRoute } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { EmptyCard } from "../../../ui";
import { FlowCanvasSurface, FlowCanvasToolbar } from "./canvas";
import { FlowInspectorColumn, FlowPaletteRail, useOpenFlowPalette } from "./inspector";
import { CalibrationMatrixCard } from "./tonight";
import "./canvas/canvas.css";

/** The legacy `?open=` value that meant "open the canvas on its library
 *  screen". The canvas has no library any more - `FlowsScreen` is it - so this
 *  now means "no flow named", and the host says so rather than drawing an empty
 *  graph that would accept stages it could never save. Kept as a constant
 *  because a bookmarked or hand-typed hash still carries it. */
export const CANVAS_LIBRARY = "library";

/** What the canvas says when the route names no flow. Reachable for real: the
 *  bookmark above, and a `?open=` pointing at a flow that has since been
 *  deleted. */
export const CANVAS_NO_FLOW_HINT =
  "The canvas edits one flow at a time. Pick a flow in MY FLOWS to open it here.";

/* THE TWO BOXES THIS FILE NEEDS LIVE IN `canvas/canvas.css` NOW.
 *
 * They were inline styles, on the argument that a cutover should not add a
 * third owner to a shared stylesheet. That argument was right about `next.css`
 * and wrong about `canvas.css`, which this area owns - and it cost the P1 this
 * change closes: an inline `flex: 1` cannot say "and here is the height to fall
 * back on when the ancestor chain hands me none", so at tablet, where the row
 * holds only the surface (whose children are all absolutely positioned), the
 * row measured 716 x 0 and the canvas was a blank pane with sixteen stage cards
 * inside it. The rules, and the whole diagnosis, are in `canvas.css`'s host
 * section; this file just names them.
 */

export interface FlowsCanvasHostProps {
  /** The `?open=` route param: a flow id, or `library` for none. */
  open: string;
}

export function FlowsCanvasHost({ open }: FlowsCanvasHostProps): JSX.Element {
  const route = useRoute();
  const flowsOpen = useStore((s) => s.flowsOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  const openId = useStore((s) => s.flows.record?.id ?? null);
  const desktop = useBreakpoint() === "desktop";
  const openPalette = useOpenFlowPalette();

  const named = open && open !== CANVAS_LIBRARY ? open : null;

  useEffect(() => {
    if (!named) return;
    // Idempotent: re-opening the flow already on the canvas would discard an
    // unsaved edit and re-run the compile for nothing.
    if (openId === named) return;
    void flowsOpen(named);
  }, [named, openId, flowsOpen]);

  // PUT `?open=` BACK WHEN A SHEET TAKES IT AWAY.
  //
  // `nav.sheet(name, params)` builds the whole hash from the params it is
  // HANDED (`router.ts`), so opening the stage editor from a node card - which
  // passes `{ node }` - drops `?open=<flow>` from the URL. Nothing visible
  // breaks, because `flows.ui.screen` keeps the canvas on screen underneath the
  // sheet, but the address bar would then claim the Flows LIST is showing: copy
  // it, send it, reload it, and the flow is gone. One `replace` (no history
  // entry) restores it. Guarded on a sheet being open so it cannot fight BACK,
  // which navigates deliberately to the list with no sheets at all.
  const sheetCount = route.sheets.length;
  useEffect(() => {
    if (!openId || sheetCount === 0) return;
    if (route.params.open === openId) return;
    nav.replace(buildHash({
      hub: route.hub, sub: route.sub, sheets: route.sheets,
      params: { ...route.params, open: openId },
    }));
  }, [openId, sheetCount, route]);

  const back = (): void => {
    // The store's own "which face is Flows showing" flag, written by
    // `flowsOpen` (-> "editor") and read by `FlowsScreen` to keep the canvas up
    // while a sheet has cleared the query string. Leaving it on "editor" here
    // would make the list unreachable: the screen would re-open the canvas the
    // moment this navigation landed.
    setUi({ screen: "library" });
    nav.go("/session/flows");
  };

  return (
    <div data-testid="session-flows-canvas" className="nx-flow-host">
      <button
        type="button"
        data-testid="flows-canvas-back"
        className="nx-sheet-back"
        style={{ alignSelf: "flex-start" }}
        onClick={back}
        aria-label="Back to my flows"
      >
        <span aria-hidden="true">&lsaquo; </span>MY FLOWS
      </button>

      <FlowCanvasToolbar />

      {named ? (
        <div className="nx-flow-row">
          {/* The rail is docked only where 192 px of it does not eat the graph.
              At tablet the same list is one tap away as the `flowPalette`
              sheet, which is what the surface's + ADD STAGE opens. */}
          {desktop && <FlowPaletteRail variant="rail" />}

          <FlowCanvasSurface
            showAddStage={!desktop}
            onAddStage={openPalette}
          />

          {/* LIBRARY HEALTH rides into the inspector here rather than inside it:
              the inspector area may not import a sibling R7 area, so the calib
              stage's matrix is a slot the cutover fills (see `tonight/index.ts`).
              `FlowNodeEditor` renders it only for a `calib` node. */}
          {desktop && (
            <FlowInspectorColumn
              variant="column"
              calibSlot={<CalibrationMatrixCard />}
            />
          )}
        </div>
      ) : (
        <div className="nx-flow-row" data-empty="true">
          <EmptyCard
            data-testid="flows-canvas-no-flow"
            title="NO FLOW OPEN"
            hint={CANVAS_NO_FLOW_HINT}
          />
        </div>
      )}
    </div>
  );
}

export default FlowsCanvasHost;
