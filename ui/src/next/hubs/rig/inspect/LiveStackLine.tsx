// LiveStackLine.tsx - the live-stack readout, rebuilt (wave R7, T-R7-19;
// replaces `components/preview/LiveStackReadout.tsx`, which is NOT edited and
// keeps serving `#/classic`).
//
// `Mono` for the count and the integration, `StatusPill` for the alignment
// state. The badge is the honest part: the frame count alone reads as success -
// it climbs whether the stack is well registered or riding on one star that is
// about to saturate - so when the alignment is anything other than a clean
// pattern match it says so, in words, next to the number that would otherwise
// reassure. A clean match stays silent, because a badge that always reads
// "matched" is chrome, and chrome is what teaches people to stop reading.
//
// `alignmentState` and `formatLiveStack` come from `lib/liveStack.ts`, which is
// LOGIC and shared, never re-derived here.
//
// SECOND MOUNT SITE. `hubs/rig/capture/CaptureControls.tsx` still mounts the
// legacy readout. That swap is a named follow-up of this task rather than part
// of it, so that no two wave-R7 tasks touch `hubs/rig/capture/`.

import type { JSX } from "react";
import type { PreviewInfo } from "../../../../types";
import { Mono, StatusPill } from "../../../ui";
import { liveStackLine } from "./inspectModel";

export function LiveStackLine({ preview }: { preview: PreviewInfo | null }): JSX.Element | null {
  const ls = preview?.livestack;
  if (!ls) return null;
  const m = liveStackLine(ls);
  return (
    <div className="nx-insp-livestack" aria-live="polite" data-testid="livestack-readout">
      <span className="nx-insp-livestack-row">
        <Mono size={11}>{m.headline}</Mono>
        {m.extras.map((x) => (
          <Mono key={x} size={10} tone="dim">· {x}</Mono>
        ))}
        {m.badge && <StatusPill text={m.badge.label} tone={m.badge.tone} data-testid="livestack-align" />}
      </span>
      {m.badge && <p className="nx-insp-note">{m.badge.detail}</p>}
    </div>
  );
}
