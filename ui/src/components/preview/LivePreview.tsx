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
import { useLastSessionFrame } from "./useLastSessionFrame";

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
  //  (3) else null → the stage paints the AstroDeck logo empty state, UNLESS
  //      (4) below can find something honest to put there instead.
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

  // (4) — the fourth precedence step, BELOW the two above. The ring is filled
  // only by WebSocket preview events received during THIS browser session, so
  // opening Capture mid-run finds it empty and used to show the logo for up to
  // a full sub while the rig was imaging. When there is nothing of our own to
  // paint and a sequence is live, fetch the last frame that run actually saved
  // and show it, clearly marked as not-live, until a real preview arrives.
  //
  // `enabled: !shown` is the whole ordering guarantee: the moment (1) or (2)
  // has something, the stand-in is retired and cannot come back. The hook also
  // drops a reply that lands after a live frame — see its header.
  //
  // `linkDown` goes in because a frozen `sequence` still says "running" and its
  // frozen `server_now_ms` makes an hours-old frame measure as seconds old, so
  // the age gate cannot see the staleness by itself. The stage says so too (the
  // link-down ribbon rides over the empty state as well as over a live frame),
  // but the honest thing is not to go and fetch one in the first place.
  const stageBox = useRef<HTMLDivElement>(null);
  const lastCaptured = useLastSessionFrame({
    enabled: !shown,
    sequence,
    linkDown,
    stageRef: stageBox,
  });

  // Why the zoom cluster is inert, in the operator's terms, or null when it is
  // not. The stage's pan/zoom lives in the `.preview-transform` layer, and both
  // empty-state pictures — the logo and the stand-in — are painted outside it,
  // so the buttons move nothing. That was invisible while the empty state was a
  // logo; with a real picture on the stage a readout stepping 100% -> 125% over
  // an image that does not move is the toolbar asserting something false.
  const zoomReason = shown
    ? null
    : lastCaptured
      ? "This is a saved frame, not the live view — zoom returns with the next sub"
      : "Nothing on the stage to zoom yet";

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
      // `min-w-0` here too: this row holds the mono meta line (frame size,
      // exposure, gain, bin), which on the real camera settings ("6252x4176 -
      // 60s - gain 125 - bin 1") is long enough that without it the row
      // refused to shrink and ran off the right of a phone screen instead of
      // wrapping. The `right` div above it (Panel) already shrinks; this makes
      // the row it wraps do the same.
      <span className="flex items-center gap-3 min-w-0">
        <LiveStackReadout preview={shown} />
        <PreviewMeta preview={shown} />
      </span>
    }>
      <div className="flex flex-col gap-3">
        {/* `min-w-0` wraps FocusVerdict from the outside rather than editing its
            markup: the verdict's own row is `flex flex-wrap`, which already
            lets its text wrap once given a properly-shrunk box, but this panel
            sits in a CSS grid column that can only shrink that far if nothing
            between here and the grid item refuses to. Belt-and-suspenders
            alongside the grid-item fix in CaptureView.tsx. */}
        <div className="min-w-0">
          <FocusVerdict preview={shown} prev={prev} hfrGood={hfrGood} hfrWarn={hfrWarn} />
        </div>

        {/* The wrapper exists to be MEASURED. The stage is `w-full` inside it,
            so this box's clientWidth is the stage's CSS width — which is what
            decides how many pixels to ask the server to render for the stand-in
            above. The stage measures itself too, but only once its own
            ResizeObserver has fired, which is after the point we need it. */}
        <div ref={stageBox}>
          <PreviewStage
            preview={shown}
            lastCaptured={lastCaptured}
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
        </div>

        <PreviewToolbar
          preview={shown}
          overlays={overlays}
          setOverlays={setOverlays}
          scalePct={scalePct}
          zoomReason={zoomReason}
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
