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
import { PreviewMeta } from "./PreviewMeta";
import { PreviewStage } from "./PreviewStage";
import { PreviewToolbar } from "./PreviewToolbar";
import { StretchHistogram } from "./StretchHistogram";
import { FrameFilmstrip } from "./FrameFilmstrip";
import { FocusVerdict } from "./FocusVerdict";
import { FrameStats } from "./FrameStats";

type StageControls = { fit: () => void; hundred: () => void; zoomIn: () => void; zoomOut: () => void };

export function LivePreview() {
  const previews = usePreviews();
  const shown = useLivePreview();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();

  const setViewport = useStore((s) => s.setViewport);
  const setStretch = useStore((s) => s.setStretch);
  const setOverlays = useStore((s) => s.setOverlays);
  const selectPreview = useStore((s) => s.selectPreview);

  const [stretchDragging, setStretchDragging] = useState(false);
  const controls = useRef<StageControls | null>(null);

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
    <Panel title="Live Preview" right={<PreviewMeta preview={shown} />}>
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
          onControls={(c) => (controls.current = c)}
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
