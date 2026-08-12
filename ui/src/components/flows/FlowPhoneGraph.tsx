// FlowPhoneGraph.tsx — the phone FLOW tab: the real graph, auto-laid for one
// thumb. §C.15, README §5, ref `10-phone-flow-autograph-390px.png`.
//
// NO PAN, NO ZOOM, NO FREE DRAG. The page just scrolls vertically. Everything
// spatial is decided by `computeAutoLayout` (already built and already pinned
// by `autoLayout.test.ts` against the M16 capture's measured y positions), so
// this file owns exactly two things the layout cannot: measuring the CONTAINER
// and handing tap-to-wire down to the ports.
//
// WHY THE WIDTH IS MEASURED AND NOT `window.innerWidth` (§C.15). At 667px —
// phone landscape — the app's 72px rail is present (`hidden sm:flex`) and `p-4`
// takes 32 more, leaving ~563px. A layout computed off the window would put the
// right-hand column 104px off the screen, and the phone-landscape band
// (667–932px) is exactly the one this app has stranded users in before.
//
// WHY THE `+ ADD STAGE` BUTTON IS PINNED AT THE GRAPH'S END rather than
// floating: there is no canvas to float over here, and the graph scrolls.
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { useStore } from "../../store";
import type { PortDir } from "./geometry";
import { computeAutoLayout } from "./autoLayout";
import { NODE_DEFS } from "./nodeDefs";
import FlowNodeCard from "./FlowNodeCard";
import FlowWireLayer from "./FlowWireLayer";
import FlowWireDelete from "./FlowWireDelete";

export default function FlowPhoneGraph(): JSX.Element {
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  const selEdgeId = useStore((s) =>
    s.flows.sel?.kind === "edge" ? s.flows.sel.id : null);
  const tapPort = useStore((s) => s.flowsTapPort);
  const setUi = useStore((s) => s.flowsSetUi);

  const boxRef = useRef<HTMLDivElement | null>(null);
  // Seeded at 390 — the reference capture's width — so the FIRST paint is a
  // plausible layout rather than a 300px-clamped one that jumps a frame later.
  const [width, setWidth] = useState(390);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    setWidth(el.clientWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const layout = useMemo(
    () => computeAutoLayout({ nodes, edges }, width, NODE_DEFS),
    [nodes, edges, width],
  );

  const onTapPort = useCallback(
    (nodeId: string, portId: string, dir: PortDir) => tapPort(nodeId, portId, dir),
    [tapPort],
  );

  const edge = selEdgeId ? edges.find((e) => e.id === selEdgeId) ?? null : null;

  return (
    <div
      ref={boxRef}
      data-flows-phone-tab="flow"
      className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden relative"
      style={{
        backgroundImage:
          "linear-gradient(rgba(6,7,11,.86),rgba(6,7,11,.93)),url('/bg_nebula.png')",
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      {/* One positioned box the height the layout asked for, so the scroll
          region is exactly as tall as the graph plus its tail. */}
      <div className="relative" style={{ height: layout.height }}>
        <FlowWireLayer tier="phone" auto positions={layout.pos} />
        {nodes.map((n) => (
          <FlowNodeCard
            key={n.id}
            node={n}
            phone
            auto
            x={layout.pos[n.id]?.x}
            y={layout.pos[n.id]?.y}
            onTapPort={onTapPort}
          />
        ))}
        {edge && <FlowWireDelete edge={edge} tier="phone" auto positions={layout.pos} />}
      </div>

      <div className="px-3.5 pb-6">
        <button
          type="button"
          onClick={() => setUi({ paletteOpen: true })}
          className="w-full min-h-[44px] rounded-[10px] border border-accent2
                     bg-accent-fill text-accent cursor-pointer
                     font-display font-semibold text-[11px] tracking-[0.12em]"
        >
          + ADD STAGE
        </button>
      </div>
    </div>
  );
}
