// FlowZoomCluster.tsx - the canvas's zoom trio and its percent readout
// (wave R7 parity row A9).
//
// TWO DELIBERATE ODDITIES, BOTH REPRODUCED RATHER THAN REPAIRED.
//
// 1. The buttons do NOT re-anchor. `zoomBy` changes `zoom` and leaves `pan`
//    alone, so `+`/`-` pivot on the WORLD ORIGIN while the wheel pivots under
//    the cursor. Anchoring the buttons on the canvas centre would be a one-line
//    "fix" and would move every reference capture's graph.
// 2. 1.15 and 0.87 are not reciprocal - press `+` then `-` and you land at
//    1.0005x where you started, drifting a little on every pair. Same ruling.
//
// The percent readout is the only place the zoom level is ever shown, and the
// reference captures pin its text (`46%`). It is not a control: its title says
// what the number counts, which the bare "46%" does not.
//
// The design's vocabulary for a canvas toolbar is the `IconButton48` trio, so
// the 34 px legacy cluster comes up to the 48 px house target on the way past.

import type { JSX } from "react";

import { clampZoom } from "../../../../../components/flows/geometry";
import { useStore } from "../../../../../store";
import { IconButton48, Mono } from "../../../../ui";
import { NxIcon } from "../../../../icons";

const ZOOM_STEP_IN = 1.15;
const ZOOM_STEP_OUT = 0.87;

export interface FlowZoomClusterProps {
  /** Fit-to-view. The surface owns it because `fitView()` needs the canvas
   *  element's measured box, which only the surface has a ref to. */
  onFit: () => void;
}

export function FlowZoomCluster({ onFit }: FlowZoomClusterProps): JSX.Element {
  // Exact selectors: a number and a stable action. A pan does not re-render this
  // cluster, and a stage-status tick does not either.
  const zoom = useStore((s) => s.flows.zoom);
  const setZoom = useStore((s) => s.flowsSetZoom);

  // No `pan` argument - that IS the non-re-anchoring behaviour, item 1 above.
  const zoomBy = (f: number): void => setZoom(clampZoom(zoom * f));

  return (
    <div className="nx-flow-zoom" data-testid="flow-zoom">
      <IconButton48
        glyph={<NxIcon name="minus" size={18} />}
        label="OUT"
        data-testid="flow-zoom-out"
        onPress={() => zoomBy(ZOOM_STEP_OUT)}
      />
      <span className="nx-flow-zoom-pct" title="Canvas zoom, as a percentage of full size">
        <Mono size={10.5} tone="dim">{`${Math.round(zoom * 100)}%`}</Mono>
      </span>
      <IconButton48
        glyph={<NxIcon name="plus" size={18} />}
        label="IN"
        data-testid="flow-zoom-in"
        onPress={() => zoomBy(ZOOM_STEP_IN)}
      />
      <IconButton48
        glyph={<NxIcon name="layers" size={18} />}
        label="FIT"
        data-testid="flow-zoom-fit"
        onPress={onFit}
      />
    </div>
  );
}

export default FlowZoomCluster;
