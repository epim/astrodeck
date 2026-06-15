// FrameFilmstrip.tsx — thumbnail-backed frame history (stream T).
// (spec §5 "Filmstrip", decisions #11, §10 Monitor reuse)
//
//  - Tiles are server-thumb-backed (/api/preview/{id}/thumb.jpg). No "expired"
//    tiles, no "cache" copy. Empty => labeled placeholder.
//  - Each tile: HFR number + good/warn/bad GLYPH (●/▲/■ — not color alone, §11.1)
//    + relative age. LIVE tile gets a static accent border + "LIVE".
//  - Newest right; auto-scroll to end only when following live AND no user
//    interaction in the last ~1s (§5, §9).
//  - Tap => selectPreview(id). The currently-shown frame gets a focus ring.
import { useEffect, useRef } from "react";
import type { PreviewInfo } from "../../types";
import { EmptyState } from "../ui";

function ageStr(tsSec: number, nowMs: number): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - tsSec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

function hfrGlyph(hfr: number | undefined, good: number, warn: number): { ch: string; cls: string } {
  if (hfr == null) return { ch: "·", cls: "text-dim" };
  if (hfr <= good) return { ch: "●", cls: "text-good" };
  if (hfr <= warn) return { ch: "▲", cls: "text-warn" };
  return { ch: "■", cls: "text-bad" };
}

export function FrameFilmstrip({
  previews,
  shownId,
  liveId,
  hfrGood,
  hfrWarn,
  onSelect,
}: {
  previews: PreviewInfo[];
  shownId: number | null;
  liveId: number | null;
  hfrGood: number;
  hfrWarn: number;
  onSelect: (id: number | null) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastTouch = useRef(0);
  const following = shownId == null || shownId === liveId;

  // auto-scroll to end when following live + no recent user interaction
  useEffect(() => {
    if (!following) return;
    if (performance.now() - lastTouch.current < 1000) return;
    const el = scrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [previews.length, following]);

  if (previews.length === 0) {
    return (
      <EmptyState size="inline" icon="capture" title="Frame history will appear here." />
    );
  }

  const nowMs = Date.now();

  return (
    <div
      ref={scrollRef}
      className="flex gap-1.5 overflow-x-auto pb-1"
      onPointerDown={() => (lastTouch.current = performance.now())}
      onWheel={() => (lastTouch.current = performance.now())}
      role="listbox"
      aria-label="Frame history"
    >
      {previews.map((p) => {
        const isShown = p.id === shownId || (shownId == null && p.id === liveId);
        const isLive = p.id === liveId;
        const g = hfrGlyph(p.hfr, hfrGood, hfrWarn);
        return (
          <button
            key={p.id}
            role="option"
            aria-selected={isShown}
            onClick={() => onSelect(isLive ? null : p.id)}
            className={`relative shrink-0 border bg-black/50 ${
              isShown ? "border-accent outline outline-1 outline-accent" : "border-line"
            }`}
            style={{ width: 56, height: 44 }}
            title={`Frame #${p.id} · HFR ${p.hfr?.toFixed(2) ?? "—"} · ${ageStr(p.ts, nowMs)} ago`}
          >
            <img
              src={`/api/preview/${p.id}/thumb.jpg`}
              alt=""
              className="astro w-full h-full object-cover"
              loading="lazy"
            />
            {isLive && (
              <span className="absolute top-0 left-0 preview-label !text-[8px] px-0.5 bg-accent2/60">
                LIVE
              </span>
            )}
            <span className="absolute bottom-0 right-0 preview-label !text-[8px] px-0.5 flex items-center gap-0.5">
              <span className={g.cls} aria-hidden>
                {g.ch}
              </span>
              {p.hfr != null ? p.hfr.toFixed(1) : ""}
            </span>
            <span className="absolute bottom-0 left-0 preview-label !text-[8px] px-0.5">{ageStr(p.ts, nowMs)}</span>
          </button>
        );
      })}
    </div>
  );
}
