// FlowZoomCluster.tsx — the canvas's bottom-left `− / % / + / FIT` cluster.
// Contract §C.4 (placement), §D.2 (the button maths), refs 02 and 08.
//
// TWO DELIBERATE ODDITIES, BOTH REPRODUCED RATHER THAN REPAIRED.
//
// 1. The buttons do NOT re-anchor. `zoomBy` changes `zoom` and leaves `pan`
//    alone, so `+`/`−` pivot on the WORLD ORIGIN while the wheel pivots under
//    the cursor. Two controls for one operation that move the graph differently
//    reads like a bug; §D.2 says reproduce it verbatim and §G-24 is the open
//    question. Anchoring the buttons on the canvas centre would be a one-line
//    "fix" and would move every reference capture's graph.
//
// 2. 1.15 and 0.87 are not reciprocal — press `+` then `−` and you land at
//    1.0005× where you started, drifting a little on every pair. Same ruling.
//
// The percent readout is the only place the zoom level is ever shown, and the
// captures pin its text: `46%` in 02, 03, 07 and 08. `Math.round(zoom * 100)`.
import { useStore } from "../../store";
import { clampZoom } from "./geometry";

/** §D.2. Not `1/0.87` and not `1/1.15` — see the header. */
const ZOOM_STEP_IN = 1.15;
const ZOOM_STEP_OUT = 0.87;

export interface FlowZoomClusterProps {
  /** Fit-to-view. The canvas owns this because `fitView()` needs the canvas
   *  element's measured box, which only the canvas has a ref to. */
  onFit: () => void;
}

/** Shared chrome for the three buttons. No focus styling: `index.css:341` puts
 *  ONE `:focus-visible` ring on every button in the app, at a specificity above
 *  Tailwind's, and a second one here would either double it or fight it. */
const BTN =
  "h-[34px] rounded-lg border border-line2 bg-raise text-ink cursor-pointer " +
  "hover:border-accent hover:text-accent transition-colors";

/** The `− / % / + / FIT` cluster, `left:12 bottom:44 z-5` inside the canvas.
 *
 *  ⚠ 34px controls, below the house 44px touch floor, and this is a control you
 *  press in the dark at the scope. §G-20 covers exactly this — it lists both
 *  "bump on coarse pointers" and "buy the hit area InfoDot-style
 *  (`-m-[15px] p-[15px]`, footprint unchanged)" as candidate answers and is
 *  UNRESOLVED. Picking either here would settle it, so the design's size is what
 *  renders, matching FlowLogStrip's 30px bar and FlowNodeCard's 20px ✎.
 *
 *  ⚠ Absolute, never fixed, and never inside a `.panel`: §D.6 records that
 *  `backdrop-filter` makes a panel a containing block, and a `fixed inset-0`
 *  probe inside one resolved to 700x1515. The canvas is the containing block
 *  this cluster wants. */
export default function FlowZoomCluster({ onFit }: FlowZoomClusterProps) {
  // Exact selectors: a number and a stable action. A pan does not re-render this
  // cluster, and a node-status tick does not either.
  const zoom = useStore((s) => s.flows.zoom);
  const setZoom = useStore((s) => s.flowsSetZoom);

  // No `pan` argument — that IS the non-re-anchoring behaviour, item 1 above.
  const zoomBy = (f: number) => setZoom(clampZoom(zoom * f));

  return (
    <div className="absolute left-3 bottom-11 z-[5] flex items-center gap-1.5">
      <button
        type="button"
        aria-label="Zoom out"
        title="Zoom out"
        onClick={() => zoomBy(ZOOM_STEP_OUT)}
        className={`${BTN} min-w-[34px] font-mono text-[15px] leading-none`}
      >
        {/* U+2212 MINUS SIGN, not a hyphen — it is the same optical weight as
            the `+` beside it, which a hyphen is not. */}
        −
      </button>

      {/* Not a control: the readout is the answer to "how far in am I", and the
          title says what the number counts, which the bare "46%" does not. */}
      <span
        title="Canvas zoom"
        className="h-[34px] flex items-center px-2 rounded-lg border border-line
                   bg-panel font-mono text-[10.5px] text-dim tabular-nums"
      >
        {Math.round(zoom * 100)}%
      </span>

      <button
        type="button"
        aria-label="Zoom in"
        title="Zoom in"
        onClick={() => zoomBy(ZOOM_STEP_IN)}
        className={`${BTN} min-w-[34px] font-mono text-[15px] leading-none`}
      >
        +
      </button>

      {/* No aria-label: "FIT" is the visible label and overriding it with a
          longer accessible name breaks WCAG 2.5.3 (the same trap §F.4 flags for
          relabelling ADD STAGE to ADD NODE). The `title` adds detail without
          replacing the name. */}
      <button
        type="button"
        title="Fit the whole graph in view"
        onClick={onFit}
        className={`${BTN} px-2.5 font-display font-semibold text-[9.5px] tracking-[0.12em]`}
      >
        FIT
      </button>
    </div>
  );
}
