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

import type { JSX } from "react";

import { useStore } from "../../../../../store";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { PALETTE_TYPES } from "../../../../../components/flows/palette";
import { nav } from "../../../../router";
import { NxIcon } from "../../../../icons";
import { EmptyCard, Sheet } from "../../../../ui";
import type { SheetComponent, SheetProps } from "../../../sheets";
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
        <FlowInspectorColumn variant="sheet" nodeId={node.id} />
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
 *  are GLOBAL across the app (`hubs/index.ts` throws on a collision). */
export const flowInspectorSheets: Record<string, SheetComponent> = {
  flowNode: FlowNodeSheet,
  flowPalette: FlowPaletteSheet,
};
