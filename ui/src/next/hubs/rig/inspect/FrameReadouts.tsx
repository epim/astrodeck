// FrameReadouts.tsx - `PreviewMeta` and `FrameStats`, rebuilt as the design's
// readout grids (wave R7, T-R7-19). The two legacy files are NOT edited and keep
// serving `#/classic`.
//
// `PreviewMeta` was one `mono` line of four numbers with a `title=` mirror of
// itself; `FrameStats` was a five-column and a three-column CSS grid of `Stat`.
// Both are verbatim `ReadoutGrid` shapes, which is the whole reason the legacy
// pair had to be rebuilt at all - leaving them was the only thing still
// importing `Panel` chrome into this sheet.
//
// `--` IS A REAL READING. A statistic the rig did not send prints two dashes,
// never a number this component made up. `Number(null)` is 0, which is finite,
// plausible and wrong: a median of 0 ADU says the sensor read nothing, and an
// operator would act on that. `statText()` is where that decision lives, and a
// test grades it.

import type { JSX } from "react";
import type { PreviewInfo } from "../../../../types";
import { ReadoutGrid, ReadoutTile } from "../../../ui";
import { metaTiles, statTiles } from "./inspectModel";

/** Size / exposure / gain / bin, with the OSC pattern riding under the size when
 *  the frame is a colour sub (a mono preview of a Bayer frame is a different
 *  picture, and nothing else on this sheet says so). */
export function FrameMetaRow({ preview }: { preview: PreviewInfo }): JSX.Element {
  return (
    <ReadoutGrid cols={4} data-testid="preview-meta">
      {metaTiles(preview).map((t) => (
        <ReadoutTile key={t.label} label={t.label} value={t.value} sub={t.sub} />
      ))}
    </ReadoutGrid>
  );
}

/** min / median / mean / max / sigma, then HFR / HFR in arcseconds / stars when
 *  the frame carries them.
 *
 *  The max tile's warn tone is `full_well`-derived, never a hardcoded 65535, and
 *  it names the well depth in its sub - a camera that does not publish one gets
 *  no verdict rather than a guessed one. */
export function FrameStatsGrid({ preview, hfrGood, hfrWarn }: {
  preview: PreviewInfo;
  hfrGood: number;
  hfrWarn: number;
}): JSX.Element {
  return (
    <ReadoutGrid cols={4} data-testid="frame-stats">
      {statTiles(preview, hfrGood, hfrWarn).map((t) => (
        <ReadoutTile key={t.label} label={t.label} value={t.value} sub={t.sub} tone={t.tone} />
      ))}
    </ReadoutGrid>
  );
}
