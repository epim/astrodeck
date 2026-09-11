// sheets.tsx - the two Flows sheets this task registers (parity rows A15 and
// A14's sheet half).
//
//   flowNode    the stage editor: the rebuilt `FlowEditSheet`. On tablet and
//               phone the pencil on a node card is the ONLY door to it, which is
//               why selection (`flows.sel`) and editing (`flows.editNode`) are
//               separate state - a tap on the canvas must not open a sheet over
//               the graph.
//   flowPalette the add-stage list as a sheet.
//
// SHEETS ARE ROUTE STATE in the new UI (ARCHITECTURE section 5), so the sheet
// opens because the hash names it and closes because BACK, Escape or the browser
// Back button pops it. `flows.editNode` still says WHICH stage, because that is
// the store field every canvas control already writes; BACK clears it, so the
// route and the store cannot disagree about whether a stage is being edited.
//
// THE CALIBRATION MATRIX ARRIVES HERE AT THE CUTOVER (T-R7-20). `tonight/`
// exports `CalibrationMatrixCard` beside its own sheet precisely because LIBRARY
// HEALTH has two homes - the TONIGHT sheet's PLAN tab, and a CALIBRATION QUEUE
// stage's editor - and the inspector area was not allowed to import a sibling
// area while the four were built in parallel. The cutover is what wires the
// second mount, and it wires it in BOTH places the editor renders: the desktop
// column (`FlowsCanvasHost`) and this sheet, which is the tablet and phone door
// to the same stage. One of the two would have been a breakpoint-shaped hole.

import type { JSX } from "react";

import { useStore } from "../../../../../store";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { PALETTE_TYPES } from "../../../../../components/flows/palette";
import { nav } from "../../../../router";
import { NxIcon } from "../../../../icons";
import { EmptyCard, Sheet } from "../../../../ui";
import type { SheetProps } from "../../../sheets";
import { CalibrationMatrixCard } from "../tonight/CalibrationMatrixCard";
import { FlowInspectorColumn } from "./FlowInspectorColumn";
import { ADD_STAGE_TITLE, FlowPaletteRail } from "./FlowPaletteRail";
import "./inspector.css";

/** What a stage sheet says when the stage is gone.
 *
 *  Reachable for real: a deep link to `flowNode` with nothing being edited, and
 *  a stage deleted from under the sheet by the DELETE STAGE button in it. An
 *  empty sheet with no sentence would read as a broken screen. */
export const NO_STAGE_HINT =
  "Open a flow on the canvas and tap the pencil on a stage to edit it. "
  + "On a phone, tap a stage in the flow's stage list.";

export function FlowNodeSheet({ params }: SheetProps): JSX.Element {
  const editNode = useStore((s) => s.flows.editNode);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  // `?node=` wins when the route carries one, so a deep link opens on the stage
  // it names rather than on whatever was last edited.
  const id = params.node ?? editNode;
  // The node's TYPE and summary only - editing a param must re-render the field
  // row, not the sheet frame around it.
  const node = useStore((s) => (id ? s.flows.graph.nodes.find((n) => n.id === id) : undefined));
  const def = node ? NODE_DEFS[node.type] : undefined;

  const close = () => {
    // Clear the store field FIRST: `nav.back()` unmounts this sheet, and a stale
    // `editNode` left behind would re-open it on the next route change.
    setEditNode(null);
    nav.back();
  };

  return (
    <Sheet
      data-testid="session-flow-node"
      title={def ? def.label : "STAGE"}
      sub={def ? def.cat : "nothing selected"}
      live={def && node ? def.sum(node.params) : undefined}
      icon={<NxIcon name="flows" size={18} />}
      backLabel="FLOW"
      onBack={close}
    >
      {def && node ? (
        <FlowInspectorColumn
          variant="sheet"
          nodeId={node.id}
          calibSlot={<CalibrationMatrixCard />}
        />
      ) : (
        <div className="nx-flownode-empty">
          <EmptyCard
            data-testid="flow-node-empty"
            title="NO STAGE TO EDIT"
            hint={NO_STAGE_HINT}
          />
        </div>
      )}
    </Sheet>
  );
}

export function FlowPaletteSheet(_props: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="session-flow-palette"
      title={ADD_STAGE_TITLE}
      sub={`${PALETTE_TYPES.length} stage types`}
      icon={<NxIcon name="plus" size={18} />}
      backLabel="FLOW"
      onBack={() => nav.back()}
    >
      {/* One tap, one stage, back to the graph - the legacy palette's own rule. */}
      <FlowPaletteRail variant="sheet" onPicked={() => nav.back()} />
    </Sheet>
  );
}

/** Registered by the cutover task into the SESSION hub's sheet registry. Names
 *  are GLOBAL across the app (`hubs/index.ts` throws on a collision).
 *
 *  `{ id, load }`, not the components: sheets are code-split (D-FU-2), so a
 *  registry hands `hubs/index.ts` a module identity and one line that fetches
 *  it. `id` is what the duplicate check compares - two hubs registering one
 *  name each build their own `lazy()` wrapper, and comparing those by identity
 *  would report the contract working as a collision. Both sheets live in THIS
 *  module, so both ids name it and the export disambiguates them, the same way
 *  `rig/sheets/index.ts` spells its in-file `demo` sheet. */
/** The registry entries live in `reg.ts` - a component-free module - because
 *  `session/sheets/index.ts` is in the entry chunk and importing THIS file for
 *  them would put both sheets, the inspector column, the palette rail, the
 *  calibration matrix and `inspector.css` in front of first paint. Re-exported
 *  here so the area barrel and every existing importer still resolve. */
export { flowInspectorSheets } from "./reg";
