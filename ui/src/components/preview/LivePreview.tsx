// LivePreview.tsx — orchestrator that wires the store to the stage + toolbar +
// histogram + filmstrip + verdict (stream V). Drops into CaptureView in place of
// the old inline preview Panel. Also reused (compact) on Focus.
//
// Reads ONLY the narrow store hooks (perf — no broad useStore): usePreviews,
// useLivePreview, useSelectedPreviewId, useLivePreviewId, useViewport, useStretch,
// useOverlays, useHfrThresholds, useNight, useLinkDown.
import { useMemo, useRef, useState } from "react";
import {
  useHfrThresholds,
  useLinkDown,
  useLivePreview,
  useLivePreviewId,
  useNight,
  useOverlays,
  usePreviews,
  useSelectedPreviewId,
  useStore,
  useStretch,
  useViewport,
} from "../../store";
import { Panel } from "../ui";
import { LiveStackReadout } from "./LiveStackReadout";
import { PreviewMeta } from "./PreviewMeta";
import { PreviewStage, type StageControls } from "./PreviewStage";
import { PreviewToolbar } from "./PreviewToolbar";
import { StretchHistogram } from "./StretchHistogram";
import { FrameFilmstrip } from "./FrameFilmstrip";
import { FocusVerdict } from "./FocusVerdict";
import { FrameStats } from "./FrameStats";

export function LivePreview() {
  const previews = usePreviews();
  const live = useLivePreview();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();

  // Lane B — never show a broken/empty stage. Precedence for what to render:
  //  (1) the pinned/live frame (`live`) if the store has one;
  //  (2) else, if ANY capture happened this session, the MOST RECENT ring entry
  //      (the store can briefly have a populated `previews` ring but a null
  //      live/selected id — e.g. right after a reconnect or before the first
  //      livePreviewId latches);
  //  (3) else null → the stage paints the AstroDeck logo empty state.
  const shown = live ?? (previews.length ? previews[previews.length - 1] : null);
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const sequence = useStore((s) => s.sequence);

  const setViewport = useStore((s) => s.setViewport);
  const setStretch = useStore((s) => s.setStretch);
  const setOverlays = useStore((s) => s.setOverlays);
  const selectPreview = useStore((s) => s.selectPreview);

  const [stretchDragging, setStretchDragging] = useState(false);
  const controls = useRef<StageControls | null>(null);
  // The 1:1 loupe's state LIVES in PreviewStage (Decision F — ephemeral view
  // state, no store slice). We only mirror it here so the toolbar's toggle can
  // render its pressed/disabled state; the mirror is guarded so an unchanged
  // controls callback can never loop.
  const [loupe, setLoupe] = useState({ on: false, available: false });

  const pinned = selectedId != null && selectedId !== liveId;
  // count live arrivals since the pin (frames with id > pinned id)
  const newSincePinned = useMemo(() => {
    if (!pinned || selectedId == null) return 0;
    return previews.filter((p) => p.id > selectedId).length;
  }, [pinned, selectedId, previews]);

  // previous frame (for verdict trend) — the one before the shown one in the ring
  const prev = useMemo(() => {
    if (!shown) return null;
    const idx = previews.findIndex((p) => p.id === shown.id);
    return idx > 0 ? previews[idx - 1] : null;
  }, [shown, previews]);

  const scalePct = Math.round((viewport.scale || 1) * 100);
  const starsAvailable = !!shown?.star_list && shown.star_list.length > 0;
  const clipAvailable = !!shown && shown.data_is_linear && shown.full_well != null;

  return (
    // min-w-0 on the panel itself: this panel is the `1fr` item of CaptureView's
    // `md:grid-cols-[1fr_320px]`, and a grid item's default `min-width:auto`
    // means the track can never resolve narrower than the panel's min-content —
    // when that floor exceeds the track, the grid does not shrink, it OVERFLOWS,
    // which slides the 320px controls column off the right of a <main> that
    // clips (`overflow-x:hidden`) and therefore cannot be scrolled back to.
    // Measured today: the panel's min-content is 194px against a 312px track at
    // the md breakpoint, so the floor is not biting yet and this is insurance,
    // not a repair — but the failure it insures against is precisely the one
    // reported ("the width of the page got wider and now I can't see controls"),
    // and the histogram's own SVG (72px tall with a 256x72 viewBox) carries a
    // 256px intrinsic-ratio min-content contribution that keeps that floor high.
    <Panel className="min-w-0" title="Live Preview" right={
      <span className="flex items-center gap-3">
        <LiveStackReadout preview={shown} />
        <PreviewMeta preview={shown} />
      </span>
    }>
      <div className="flex flex-col gap-3">
        <FocusVerdict preview={shown} prev={prev} hfrGood={hfrGood} hfrWarn={hfrWarn} />

        <PreviewStage
          preview={shown}
          viewport={viewport}
          setViewport={setViewport}
          stretch={stretch}
          overlays={overlays}
          hfrGood={hfrGood}
          hfrWarn={hfrWarn}
          night={night}
          linkDown={linkDown}
          pinned={pinned}
          newSincePinned={newSincePinned}
          onReturnToLive={() => selectPreview(null)}
          stretchDragging={stretchDragging}
          onControls={(c) => {
            controls.current = c;
            setLoupe((p) =>
              p.on === c.loupeOn && p.available === c.loupeAvailable
                ? p
                : { on: c.loupeOn, available: c.loupeAvailable },
            );
          }}
        />

        <PreviewToolbar
          preview={shown}
          overlays={overlays}
          setOverlays={setOverlays}
          scalePct={scalePct}
          onZoomIn={() => controls.current?.zoomIn()}
          onZoomOut={() => controls.current?.zoomOut()}
          onFit={() => controls.current?.fit()}
          onHundred={() => controls.current?.hundred()}
          starsAvailable={starsAvailable}
          clipAvailable={clipAvailable}
          linkDown={linkDown}
          shareMeta={{ target: sequence.target, subs: sequence.progress?.frames_done }}
          stretch={stretch}
          loupeOn={loupe.on}
          loupeAvailable={loupe.available}
          onLoupe={(v) => controls.current?.setLoupeOn(v)}
        />

        {shown && <FrameStats preview={shown} hfrGood={hfrGood} hfrWarn={hfrWarn} />}

        <div className="border-t border-line pt-3">
          <StretchHistogram
            preview={shown}
            stretch={stretch}
            onStretch={setStretch}
            onDragChange={setStretchDragging}
          />
        </div>

        <div className="border-t border-line pt-3">
          <FrameFilmstrip
            previews={previews}
            shownId={selectedId}
            liveId={liveId}
            hfrGood={hfrGood}
            hfrWarn={hfrWarn}
            onSelect={selectPreview}
          />
        </div>
      </div>
    </Panel>
  );
}
