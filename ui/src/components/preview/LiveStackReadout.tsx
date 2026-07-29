// NOV-1 Live View readout: "◉ 12 frames · 24 min integrated · 3 skipped".
// Renders nothing unless the shown preview carries a livestack block.
//
// The alignment badge beside it is the honest part. The frame count alone reads
// as success — it climbs whether the stack is well registered or riding on one
// star that is about to saturate — so when the alignment is anything other than
// a clean pattern match, it says so, in words, next to the count that would
// otherwise reassure. A clean match stays silent (alignmentState returns null):
// a badge that always reads "matched" is chrome, not information.
import type { PreviewInfo } from "../../types";
import { alignmentState, formatLiveStack } from "../../lib/liveStack";

export function LiveStackReadout({ preview }: { preview: PreviewInfo | null }) {
  const ls = preview?.livestack;
  if (!ls) return null;
  const f = formatLiveStack(ls);
  const align = alignmentState(ls);
  const toneClass =
    align?.tone === "warn" ? "text-warn border-warn/60" : "text-bad border-bad/60";
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1.5 text-[11px] mono text-accent"
      aria-live="polite"
    >
      <span className="w-2 h-2 rounded-full bg-accent animate-pulse" aria-hidden />
      <span className="tabular-nums">{f.headline}</span>
      {f.rejected && <span className="text-dim">· {f.rejected}</span>}
      {(ls.clipped ?? 0) > 0 && (
        <span className="text-dim">· {ls.clipped!.toLocaleString()} px clipped</span>
      )}
      {align && (
        // Shape + word, never colour alone — the night palette collapses
        // warn and bad toward the same coral.
        <span
          className={`text-[9px] tracking-[0.14em] uppercase px-1.5 py-0.5 border ${toneClass}`}
        >
          {align.label}
        </span>
      )}
      {align && (
        // The reason rides on its own line rather than in a title=, which never
        // fires on a touch screen — and this is exactly the state a phone user
        // needs to read.
        <span className="basis-full text-[10px] text-dim font-sans leading-snug max-w-md">
          {align.detail}
        </span>
      )}
    </span>
  );
}
