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

import { useEffect, type CSSProperties, type JSX } from "react";

import { useStore } from "../../../../store";
import { buildHash, nav, useRoute } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { EmptyCard } from "../../../ui";
import { FlowCanvasSurface, FlowCanvasToolbar } from "./canvas";
import { FlowInspectorColumn, FlowPaletteRail, useOpenFlowPalette } from "./inspector";
import { CalibrationMatrixCard } from "./tonight";

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

/** The two boxes this file needs. Inline rather than in a stylesheet on
 *  purpose: `next.css` belongs to T-R7-0 and `canvas.css` to T-R7-1, and a
 *  cutover that added a third owner to either would be exactly the shared-file
 *  merge hazard the area stylesheets exist to prevent. Everything with a look
 *  is a primitive or an area class already. */
const HOST: CSSProperties = {
  display: "flex", flexDirection: "column", minHeight: 0, flex: 1, gap: 8,
};
const ROW: CSSProperties = {
  display: "flex", flex: 1, minHeight: 0, minWidth: 0,
};

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
    <div data-testid="session-flows-canvas" style={HOST}>
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
        <div style={ROW}>
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
        <div style={{ ...ROW, padding: 14 }}>
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
