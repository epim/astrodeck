// FocusLine.tsx - the focus verdicts, rebuilt (wave R7, T-R7-19; replaces
// `components/preview/FocusVerdict.tsx`, which is NOT edited and keeps serving
// `#/classic`).
//
// Two verdicts, deliberately kept apart. `FocusLine` judges the CURRENT live
// frame; `AutofocusLine` judges the completed SWEEP from the engine's best HFR,
// the fit R-squared and the run state. They disagree legitimately - a sweep can
// have finished excellently on a frame that has since drifted - and merging them
// would make one of the two lie.
//
// `StatusPill` + `Mono`: the verdict WORD first, the numbers second. Never
// colour alone, and never a warning without the action that clears it. The
// precedence (grossly defocused, then saturation, then few stars, then the HFR
// band) lives in `focusLine()` and is graded there; this file only paints it.
//
// SECOND MOUNT SITE. `hubs/rig/sheets/focuser.tsx` still mounts the legacy
// `AutofocusVerdict`. `AutofocusLine` is exported here for it; the swap is a
// named follow-up of this task, because that file is not in this task's
// ownership.

import type { JSX } from "react";
import type { PreviewInfo } from "../../../../types";
import { Mono, StatusPill } from "../../../ui";
import { autofocusLine, focusLine } from "./inspectModel";

export function FocusLine({ preview, prev, hfrGood, hfrWarn }: {
  preview: PreviewInfo | null;
  /** The frame before this one, for the sharper / softer trend. */
  prev: PreviewInfo | null;
  hfrGood: number;
  hfrWarn: number;
}): JSX.Element {
  const m = focusLine(preview, prev, hfrGood, hfrWarn);
  return (
    <div className="nx-insp-verdict" role="status" aria-live="polite" data-testid="focus-verdict">
      <span className="nx-insp-verdict-row">
        <StatusPill text={m.word} tone={m.tone} />
        <Mono size={11} tone="dim">{m.detail}</Mono>
      </span>
      {m.trend && (
        <span className="nx-insp-verdict-row">
          <Mono size={10} tone={m.trend.tone}>{m.trend.word}</Mono>
        </span>
      )}
      {m.action && <p className="nx-insp-note" data-tone="warn">{m.action}</p>}
    </div>
  );
}

/** The completed sweep's verdict: "FOCUS EXCELLENT - HFR 1.82 px, 2.4", R2 0.997,
 *  hyperbolic". Exported for `hubs/rig/sheets/focuser.tsx`'s follow-up swap. */
export function AutofocusLine({
  state, hfr, r2, method, pixelScaleArcsec, hfrGood, hfrWarn, message,
}: {
  state?: string;
  hfr?: number | null;
  r2?: number | null;
  method?: string | null;
  pixelScaleArcsec?: number | null;
  hfrGood: number;
  hfrWarn: number;
  message?: string | null;
}): JSX.Element {
  const m = autofocusLine({ state, hfr, r2, method, pixelScaleArcsec, hfrGood, hfrWarn, message });
  return (
    <div className="nx-insp-verdict" role="status" aria-live="polite" data-testid="autofocus-verdict">
      <span className="nx-insp-verdict-row">
        <StatusPill text={`FOCUS ${m.word}`} tone={m.tone} pulse={state === "running"} />
        {m.detail && <Mono size={11} tone="dim">{m.detail}</Mono>}
      </span>
    </div>
  );
}
