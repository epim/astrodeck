// FlowTapWireBar.tsx - the armed tap-to-wire hint bar (wave R7 parity row A8).
//
// It exists ONLY while `tapWire` is armed, which is what makes
// `data-flows-wiring-armed` a truthful marker: a bar that were always mounted
// and merely hidden would let a capture of "wiring armed" pass over an unarmed
// editor.
//
// CANCEL has no mechanism of its own. Re-tapping the armed OUTPUT is the slice's
// own cancel path (`flowsTapPort` clears `tapWire` and connects nothing), so
// this button calls exactly that - a second mechanism could disagree with it.

import type { JSX } from "react";

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { useStore } from "../../../../../store";
import { ActionButton, Mono } from "../../../../ui";
import { tapWireHint } from "./canvasModel";

export function FlowTapWireBar(): JSX.Element | null {
  // Narrow selectors, not the object: an unarmed editor compares null === null
  // and this component never re-renders while the operator scrolls, types or
  // watches a run.
  const from = useStore((s) => s.flows.tapWire?.from ?? null);
  const fromPort = useStore((s) => s.flows.tapWire?.fromPort ?? null);
  const type = useStore((s) => (s.flows.tapWire
    ? s.flows.graph.nodes.find((n) => n.id === s.flows.tapWire!.from)?.type ?? null
    : null));
  const tapPort = useStore((s) => s.flowsTapPort);

  if (!from || !fromPort || !type) return null;
  const def = NODE_DEFS[type];
  if (!def) return null;
  const port = [...def.outs, ...def.ins].find((p) => p.id === fromPort);
  const text = tapWireHint(def.label, port?.label);
  // A bar reading "WIRING: undefined" is worse than no bar: it claims an arm the
  // graph can no longer complete.
  if (!text) return null;

  return (
    <div className="nx-flow-tapwire" data-flows-wiring-armed data-testid="flow-tapwire">
      <span className="nx-flow-tapwire-text">
        <Mono size={10.5} tone="accent">{text}</Mono>
      </span>
      <ActionButton
        kind="ghost"
        data-testid="flow-tapwire-cancel"
        onPress={() => tapPort(from, fromPort, "out")}
      >
        CANCEL
      </ActionButton>
    </div>
  );
}

export default FlowTapWireBar;
