// FlowTapWireBar.tsx — the armed tap-to-wire hint bar. §C.15, §F.1, ref
// `10b-phone-tap-to-wire-armed.png`.
//
// It exists ONLY while `tapWire` is armed, which is what makes
// `data-flows-wiring-armed` a truthful marker: the harness's state 10b waits
// for it, and a bar that were always mounted (merely hidden) would let that
// capture pass over an unarmed editor.
//
// THE SENTENCE IS VERBATIM, CODEPOINT BY CODEPOINT (§C.15):
//   "WIRING: " + def.label + " · " + port.label + " — tap an input port"
// U+00B7 MIDDLE DOT and U+2014 EM DASH, each with a space either side. It is
// built here rather than in the store because it is display text; the store
// holds only the two ids.
import type { JSX } from "react";

import { useStore } from "../../store";
import { NODE_DEFS } from "./nodeDefs";

/** The hint text, or null when the armed port cannot be named.
 *
 *  Exported so the exact codepoints can be asserted without a DOM. A missing
 *  node (deleted from under an armed wire) or a missing port returns null and
 *  the bar does not render — a bar reading "WIRING: undefined" is worse than no
 *  bar, because it claims an arm the graph can no longer complete. */
export function tapWireHint(
  label: string | null | undefined, portLabel: string | null | undefined,
): string | null {
  if (!label || !portLabel) return null;
  return `WIRING: ${label} · ${portLabel} — tap an input port`;
}

export default function FlowTapWireBar(): JSX.Element | null {
  // Two narrow string selectors, not the object: an unarmed editor compares
  // null === null and this component never re-renders while the operator pans,
  // types or watches a run.
  const from = useStore((s) => s.flows.tapWire?.from ?? null);
  const fromPort = useStore((s) => s.flows.tapWire?.fromPort ?? null);
  const type = useStore((s) => (s.flows.tapWire
    ? s.flows.graph.nodes.find((n) => n.id === s.flows.tapWire!.from)?.type ?? null
    : null));
  const tapPort = useStore((s) => s.flowsTapPort);

  if (!from || !fromPort || !type) return null;
  const def = NODE_DEFS[type];
  const port = [...def.outs, ...def.ins].find((p) => p.id === fromPort);
  const text = tapWireHint(def.label, port?.label);
  if (!text) return null;

  return (
    <div
      data-flows-wiring-armed
      // Above the tab bar, below nothing — it is the only thing between the
      // graph and the bar while a wire is half-made.
      className="absolute inset-x-0 bottom-0 z-[7] flex items-center gap-2 px-3 py-2.5
                 border-t border-accent2 bg-[color-mix(in_srgb,var(--accent)_10%,var(--bg))]"
    >
      <span className="flex-1 min-w-0 truncate font-mono text-[10.5px] text-accent">
        {text}
      </span>
      <button
        type="button"
        // 44px floor: this is driven at the scope, in the dark, one-handed.
        className="btn shrink-0 !text-[10.5px] !tracking-[0.12em] !px-3 min-h-[44px]"
        // Re-tapping the ARMED port is the slice's own cancel path (an armed
        // output tapped again clears `tapWire` and connects nothing), so CANCEL
        // needs no second mechanism that could disagree with it.
        onClick={() => tapPort(from, fromPort, "out")}
      >
        CANCEL
      </button>
    </div>
  );
}
