// Filmstrip.tsx - the recent-frame strip, rebuilt (wave R7, T-R7-19; replaces
// `components/preview/FrameFilmstrip.tsx`, which is NOT edited and keeps serving
// `#/classic`).
//
// The tiles are `ReadoutTile`s: the primitive already carries the design's
// selected state as an accent BORDER plus a marker bar (never hue alone, which
// stops existing under `:root.night`), and it is already a 44 px-plus button
// with an accessible name. The server thumbnail is the tile's value, through
// `u()` like every other `<img src>` in the app, so the cookie carries the auth.
//
// ROLE. The legacy strip was a `role="listbox"` of `role="option"`s. That
// promises the listbox keyboard model - roving focus, Home/End, type-ahead -
// which it did not implement, and its `aria-selected` was regularly false on
// EVERY option (see `stageFrameId`). This is a labelled group of toggle buttons,
// which is what it actually is: each tile is focusable, `aria-pressed` says
// which frame the stage is painting, and Tab moves between them natively.
//
// AUTO-SCROLL follows the LIVE end, and only while the user has not touched the
// strip in the last second - a strip that snaps back under a fingertip cannot be
// scrolled at all.

import { useEffect, useRef, type JSX } from "react";
import type { PreviewInfo } from "../../../../types";
import { u } from "../../../../lib/base";
import { EmptyCard, Mono, ReadoutTile } from "../../../ui";
import { ageStr, hfrGlyph, stageFrameId } from "./inspectModel";

export function Filmstrip({ previews, shownId, liveId, hfrGood, hfrWarn, onSelect }: {
  previews: PreviewInfo[];
  /** The pin (`store.selectedPreviewId`), which may name a frame the ring has
   *  already trimmed away. */
  shownId: number | null;
  liveId: number | null;
  hfrGood: number;
  hfrWarn: number;
  onSelect: (id: number | null) => void;
}): JSX.Element {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastTouch = useRef(0);

  const newestId = previews.length ? previews[previews.length - 1].id : null;
  const stageId = stageFrameId(previews, shownId, liveId);
  const following = stageId == null || stageId === newestId;

  useEffect(() => {
    if (!following) return;
    if (performance.now() - lastTouch.current < 1000) return;
    const el = scrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [previews.length, following]);

  if (previews.length === 0) {
    return (
      <EmptyCard
        title="NO FRAMES YET"
        hint="Each exposure joins this strip as it lands; tap one to hold it on the stage."
        data-testid="frame-filmstrip"
      />
    );
  }

  const nowMs = Date.now();

  return (
    <div
      className="nx-insp-strip"
      ref={scrollRef}
      role="group"
      aria-label="Recent frames"
      onPointerDown={() => { lastTouch.current = performance.now(); }}
      onWheel={() => { lastTouch.current = performance.now(); }}
      data-testid="frame-filmstrip"
    >
      {previews.map((p) => {
        const isShown = p.id === stageId;
        const isLive = p.id === liveId;
        const g = hfrGlyph(p.hfr, hfrGood, hfrWarn);
        const hfrText = p.hfr != null ? p.hfr.toFixed(1) : "--";
        return (
          <ReadoutTile
            key={p.id}
            className="nx-insp-tile"
            label={isLive ? "LIVE" : `#${p.id}`}
            value={(
              <span className="nx-insp-thumb">
                <img src={u(`/api/preview/${p.id}/thumb.jpg`)} alt="" loading="lazy" />
              </span>
            )}
            sub={`${g.ch} ${hfrText} · ${ageStr(p.ts, nowMs)} ago`}
            tone={g.tone}
            selected={isShown}
            // Tapping the live frame returns to live rather than pinning it, so
            // the strip cannot strand the stage on a frame that is about to be
            // trimmed out from under it.
            onSelect={() => onSelect(isLive ? null : p.id)}
            ariaLabel={`Frame ${p.id}, HFR ${hfrText}, ${ageStr(p.ts, nowMs)} ago`}
            data-testid={`frame-tile-${p.id}`}
          />
        );
      })}
      <span className="nx-insp-strip-end">
        <Mono size={10} tone="dim">newest</Mono>
      </span>
    </div>
  );
}
