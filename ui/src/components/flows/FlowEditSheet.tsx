// FlowEditSheet.tsx — the tablet/phone edit sheet: `FlowInspector` in a bottom
// sheet. §C.10.
//
// Everything structural comes from the shared `Overlay` primitive — body
// portal, scrim, Escape, focus trap, the safe-area padding — which is what
// satisfies README §3's "Esc should close overlays (add in production)" without
// this file owning a single one of those. It must never be a `fixed inset-0`
// inside the view tree: four reviewers hit the same class of bug from four
// directions, and the measured causes (`animation-fill-mode: both` on the view
// wrapper, `backdrop-filter` on `.panel`, `.panel { position: relative }`) all
// turned an ancestor into the containing block and put the sheet outside the
// viewport.
//
// SIZE RIDES THE CSS VARS, NEVER A TAILWIND CLASS. `index.css` is unlayered, so
// `.overlay-surface { max-width: … }` beats any `sm:max-w-*` on the same element
// no matter the order — measured the hard way when the preflight rendered 788px
// wide on an 820px tablet. The sheet default is 85dvh; the design's 76dvh has to
// be passed as `--ov-max-h`.
import type { CSSProperties } from "react";
import { useStore } from "../../store";
import { Overlay } from "../ui";
import { NODE_DEFS } from "./nodeDefs";
import FlowInspector from "./FlowInspector";

export default function FlowEditSheet() {
  const editNode = useStore((s) => s.flows.editNode);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  // The node's TYPE only — a string, so editing a param in the sheet re-renders
  // the field row and not the sheet frame around it.
  const type = useStore((s) => (editNode
    ? s.flows.graph.nodes.find((n) => n.id === editNode)?.type ?? null
    : null));

  // A stale `editNode` (its node deleted from under it) has nothing to head the
  // sheet with, so there is no sheet.
  if (!editNode || !type) return null;
  const def = NODE_DEFS[type];
  const ink = `var(${def.colorVar})`;
  const close = () => setEditNode(null);

  return (
    <Overlay
      open
      variant="sheet"
      label="Edit stage"
      onClose={close}
      surfaceStyle={{ "--ov-max-h": "76dvh" } as CSSProperties}
      // `!pb-…`: `.overlay-safe-b` (authored, unlayered) already sets
      // padding-bottom to the safe-area inset on a footer-less sheet and would
      // beat this utility outright, dropping the design's 20px. Same value the
      // contract specifies, spelled so it actually lands.
      bodyClassName="px-4 pt-3.5 !pb-[calc(20px+env(safe-area-inset-bottom,0px))]"
      head={(
        // The marker goes on the head because the head ALWAYS renders and always
        // has a box; a marker on a conditional element is a marker the parity
        // harness can photograph the absence of and call a pass.
        <header data-flows-editsheet className="flex items-center gap-2 px-4 py-[13px]">
          <span
            className="flex-none w-[7px] h-[7px] rounded-[2px]"
            style={{ background: ink, boxShadow: `0 0 6px ${ink}` }}
            aria-hidden
          />
          <span className="flex-1 min-w-0 font-display font-semibold text-[11px] tracking-[0.16em]">
            {def.label}
          </span>
          {/* The prototype's ✕ is 44x36. The house floor is 44px square for
              anything driven at the scope, and this sheet is the tablet/phone
              surface — the taller box wins. */}
          <button
            type="button"
            onClick={close}
            aria-label="Close edit stage"
            className="flex-none min-w-[44px] min-h-[44px] rounded-[8px]
                       border border-line2 bg-transparent text-dim"
          >
            ✕
          </button>
        </header>
      )}
    >
      <FlowInspector variant="sheet" nodeId={editNode} />
    </Overlay>
  );
}
