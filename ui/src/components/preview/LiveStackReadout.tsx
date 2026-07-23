// NOV-1 Live View readout: "◉ 12 frames · 24 min integrated · 3 skipped".
// Renders nothing unless the shown preview carries a livestack block.
import type { PreviewInfo } from "../../types";
import { formatLiveStack } from "../../lib/liveStack";

export function LiveStackReadout({ preview }: { preview: PreviewInfo | null }) {
  const ls = preview?.livestack;
  if (!ls) return null;
  const f = formatLiveStack(ls);
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] mono text-accent" aria-live="polite">
      <span className="w-2 h-2 rounded-full bg-accent animate-pulse" aria-hidden />
      <span className="tabular-nums">{f.headline}</span>
      {f.rejected && <span className="text-dim">· {f.rejected}</span>}
    </span>
  );
}
