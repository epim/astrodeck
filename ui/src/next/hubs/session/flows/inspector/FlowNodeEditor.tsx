// FlowNodeEditor.tsx - the selected stage: what it is, what it does, its
// parameters, and DELETE STAGE (parity row A11's node half).
//
// Shared by the 284 px inspector column and the `flowNode` sheet, because they
// are the same object at two widths - `variant` carries the touch floor, not a
// second implementation.
//
// THE CALIBRATION MATRIX IS A SLOT, NOT AN IMPORT. The legacy inspector renders
// `<CalibrationMatrix>` inside a `calib` node's block. That component is rebuilt
// by T-R7-3 in a sibling directory this task may not import (wave R7 section 5:
// the batch-1 flows tasks import nothing from each other; T-R7-20 composes). So
// the slot is a prop: the cutover passes the rebuilt matrix in and the control
// arrives with it. Named in this task's report as the one row it cannot close
// alone.

import type { CSSProperties, JSX, ReactNode } from "react";

import { useStore } from "../../../../../store";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { ActionButton, Label, LockNote, Mono } from "../../../../ui";
import { FlowFieldRow, type FlowFieldVariant } from "./FlowFieldRow";

export interface FlowNodeEditorProps {
  id: string;
  variant?: FlowFieldVariant;
  /** Rendered above the field rows for a `calib` node - see the file header. */
  calibSlot?: ReactNode;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
}

export function FlowNodeEditor({
  id, variant = "column", calibSlot, lockedReason = null, onExplain,
}: FlowNodeEditorProps): JSX.Element | null {
  // The one subscription to the node record on this surface; every field row
  // gets its value passed down rather than opening its own.
  const node = useStore((s) => s.flows.graph.nodes.find((n) => n.id === id));
  const select = useStore((s) => s.flowsSelect);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  if (!node) return null;
  const def = NODE_DEFS[node.type];
  if (!def) return null;
  const ink = { "--nx-node-ink": `var(${def.colorVar})` } as CSSProperties;

  const onDelete = () => {
    // `flowsDeleteSel` deletes `sel`, and in the sheet the node on screen is
    // `editNode` - which is NOT necessarily the selection. Pointing the
    // selection at the node being shown first is what stops DELETE STAGE from
    // deleting a different stage than the one whose name is at the top of the
    // sheet. (The slice then clears `editNode` itself, which closes the sheet.)
    select({ kind: "node", id });
    deleteSel();
  };

  return (
    <>
      <div className="nx-flowins-head" style={ink}>
        <span className="nx-flowins-dot" aria-hidden="true" />
        <span className="nx-flowins-name">
          <Label size={11}>{def.label}</Label>
        </span>
        {/* DOME CONTROL and FLAT PANEL read RIG here while the palette files
            them under EQUIPMENT. Both sides agree; it is not a bug. */}
        <span className="nx-flowins-cat">{def.cat}</span>
      </div>

      <p className="nx-flowins-desc">{def.desc}</p>

      {/* The reason is stated on the surface that refuses, not only on the one
          the operator came from: the sheet can be opened on a phone without ever
          passing the column's FLOW overview. */}
      <LockNote reason={lockedReason} data-testid="flow-node-readonly-note" />

      {node.type === "calib" && calibSlot}

      {def.fields.map((f) => (
        <FlowFieldRow
          // KEYED BY NODE, not just by field. Two nodes of the same type have
          // the same field list, so a bare `f.key` reconciles the rows in place
          // when the selection moves between them - and a row's in-progress text
          // buffer would survive the switch and show the previous node's half
          // typed value over this one's stored param.
          key={`${node.id}:${f.key}`}
          nodeId={node.id}
          nodeType={node.type}
          field={f}
          value={node.params[f.key]}
          variant={variant}
          lockedReason={lockedReason}
          onExplain={onExplain}
        />
      ))}

      <div className="nx-flowins-delete">
        <ActionButton
          kind="danger"
          size={variant === "sheet" ? "lg" : "md"}
          full
          // Two-tap, unlike the legacy single-tap button: deleting a stage takes
          // every wire into and out of it with it, there is no undo on this
          // surface, and the same `arm` idiom already guards RUN. The first press
          // says what the second one will do.
          arm={{ label: "CONFIRM DELETE" }}
          onPress={onDelete}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="flow-node-delete"
        >
          DELETE STAGE
        </ActionButton>
      </div>

      {/* The card footer the canvas draws, restated where the parameters are
          edited: it is the one line that says what these numbers add up to. */}
      <Mono size={10} tone="dim">{def.sum(node.params)}</Mono>
    </>
  );
}

export default FlowNodeEditor;
