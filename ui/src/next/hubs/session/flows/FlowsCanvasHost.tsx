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
//
// AND THE WAY OUT NOW SAVES (whole-branch review, R5 P0). It did not: this file
// wrote `ui.screen` and navigated, and nothing under `next/**` called
// `flowsSave` or `flowsCloseEditor` at all - so every edit made on the canvas
// was dropped by BACK, by the FLOWS sub-nav chip and by a reload, with no word
// of it anywhere. `leaveFlowEditor()` in `../openFlow.ts` is the legacy
// header's own door ("saves first, then reloads the library",
// `FlowHeader.tsx:167-177`), and every exit goes through it: this button, the
// sub-nav chip and the browser Back button (caught by `FlowsScreen`'s own
// effect), and the phone sheet's BACK.

import { useEffect, useRef, type JSX } from "react";

import { useStore } from "../../../../store";
import { buildHash, nav, useRoute } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { EmptyCard } from "../../../ui";
import { FlowCanvasSurface, FlowCanvasToolbar } from "./canvas";
import {
  FLOW_NODE_SHEET, FlowInspectorColumn, FlowPaletteRail, useOpenFlowPalette,
} from "./inspector";
import { CalibrationMatrixCard } from "./tonight";
import { leaveFlowEditor } from "./openFlow";
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
  /** The stage editor is open as a sheet, which at desktop is the right-hand
   *  panel beside this column. */
  const nodeSheetOpen = route.sheets.includes(FLOW_NODE_SHEET);

  // AND WHEN THE LAST SHEET CLOSES, FOR THE SAME REASON. `nav.closeSheet` drops
  // the params along with the final sheet (`router.ts`: "params: sheets.length ?
  // r.params : {}"), so the way BACK out of the stage editor clears `?open=`
  // exactly as the way in did. Without this the canvas would be left on screen
  // over a URL naming the list again - and the departure effect below would read
  // that as the operator leaving and close the flow they just finished editing.
  const sheetsBefore = useRef(sheetCount);
  const sheetJustClosed = sheetCount === 0 && sheetsBefore.current > 0;
  useEffect(() => { sheetsBefore.current = sheetCount; }, [sheetCount]);

  useEffect(() => {
    if (!openId || (sheetCount === 0 && !sheetJustClosed)) return;
    if (route.params.open === openId) return;
    nav.replace(buildHash({
      hub: route.hub, sub: route.sub, sheets: route.sheets,
      params: { ...route.params, open: openId },
    }));
  }, [openId, sheetCount, sheetJustClosed, route]);

  // SOMEONE LEFT WITHOUT USING THE DOOR.
  //
  // The FLOWS sub-nav chip builds `#/session/flows` with no sheets and no params
  // at all (`shell/SubNav.tsx:36`), and so does the browser's own Back button off
  // this route. Neither can call `back()` - the chip is the shell's control and
  // the Back button is the browser's - so the canvas is still on screen, drawn
  // from `flows.ui.screen` alone, over a URL that claims the list is showing.
  // Before this, that was also how an edit was lost: the chip cleared `?open=`,
  // the operator pressed it to get back to MY FLOWS, and the graph went with it.
  //
  // The host is mounted for as long as the editor is up, so it is the one place
  // that can see this happen. It takes the same exit its own button does.
  //
  // READ OFF THE ROUTE, NOT OFF THE `open` PROP. The prop is the screen's
  // `canvasId`, which falls back to `flows.ui.screen` when the query string
  // names nothing - which is precisely the state this effect exists to catch, so
  // asking the prop would answer "a flow is named" every single time and the
  // effect would never fire. `library` is the legacy bookmark for "no flow", so
  // it counts as naming none.
  const routeOpen = route.params.open ?? "";
  const routeNamesFlow = routeOpen !== "" && routeOpen !== CANVAS_LIBRARY;
  //
  // NOT WHEN A SHEET HAS JUST CLOSED. That produces the same route - no sheets,
  // no params - and it means the opposite thing: the operator pressed BACK out
  // of the stage editor to return to the graph. The sheet count one render ago
  // is what tells the two apart, and the restore above puts `?open=` back on
  // that path so the next render is an ordinary one.
  const strandedByNav = !routeNamesFlow && sheetCount === 0 && !sheetJustClosed;
  useEffect(() => {
    if (!strandedByNav) return;
    setUi({ screen: "library" });
    void leaveFlowEditor();
  }, [strandedByNav, setUi]);

  const back = (): void => {
    // The store's own "which face is Flows showing" flag, written by
    // `flowsOpen` (-> "editor") and read by `FlowsScreen` to keep the canvas up
    // while a sheet has cleared the query string. Leaving it on "editor" here
    // would make the list unreachable: the screen would re-open the canvas the
    // moment this navigation landed.
    setUi({ screen: "library" });
    // NAVIGATE FIRST, THEN SAVE, and the order is load-bearing both ways.
    // `leaveFlowEditor` clears `flows.record`, and this host's own effect
    // re-opens whatever `?open=` still names the moment `openId` goes null - so
    // saving while the canvas is still mounted would fetch the flow straight
    // back. Going the other way costs nothing: the close runs on the store, not
    // on this component, so unmounting it does not cancel the PUT.
    nav.go("/session/flows");
    void leaveFlowEditor();
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
              `FlowNodeEditor` renders it only for a `calib` node.

              NOT WHILE THE `flowNode` SHEET IS OPEN. At desktop the sheet is the
              right-hand panel and renders the SAME inspector, with the same
              calibration slot inside it - two mounts, two `GET
              /api/calibration/health` on one press of one pencil, and two copies
              of one editor on screen disagreeing the moment either is mid-edit.
              The sheet is the one the operator just asked for, so the docked
              column stands down for as long as it is up. */}
          {desktop && !nodeSheetOpen && (
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
