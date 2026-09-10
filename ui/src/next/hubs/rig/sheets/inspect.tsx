// inspect.tsx - the full-screen INSPECT sheet (plan hub-session-capture.md F.9,
// GAP-ANALYSIS section 3 "Missing - preview tooling").
//
// WHAT THIS FILE IS. It is chrome, and almost nothing else. Every instrument on
// this screen already exists in `components/preview/*` and is mounted here with
// the props the existing orchestrator (`LivePreview.tsx`) passes it:
//
//   PreviewStage      the picture, and with it the pinch/drag/wheel gestures,
//                     the linear-path LUT canvas, the double-buffered NINA path,
//                     CropOverlay (zoom-to-fetch), ClipMaskLayer (CLIPPED),
//                     StarOverlay, TiltOverlay, FieldOverlay, BahtinovOverlay,
//                     Reticle, ScaleBar, SnrChip, LoupePanel (the 1:1
//                     magnifier), the pinned banner and the stale-link ribbon.
//   PreviewToolbar    zoom cluster, magnifier toggle, the annotations picker,
//                     and the whole Download disclosure (full-res PNG, share
//                     JPEG, stretched PNG, lossless PNG, FITS).
//   StretchHistogram  the log histogram, B/M/W, brightness/contrast, CLIPPED.
//   FrameStats        min / median / mean / max / sigma + HFR + stars.
//   FocusVerdict      the live focus judgement, in its documented precedence.
//   FrameFilmstrip    the horizontal strip of recent frames.
//   LiveStackReadout  the alignment badge and the bright-outlier pixel count.
//
// Re-implementing any of them here would fork behaviour that took several
// regressions to get right (the star-overlay tap floor, the pin that survives
// being trimmed out of the ring, the download rows that refuse to offer a byte
// the rig has already freed). So this file resolves WHICH frame is on screen,
// hands each component its props, and adds the two things the sheet itself owns:
// the capability note under the toolbar, and the outlier line.
//
// THE `view.media` NOTE (deviation D15). `PreviewToolbar` gates its FITS row on
// `preview.saved_local` only, because the server enforces `view.media` with a
// 403 and the toolbar has no capability prop. Adding one would fork the download
// gating that is kept in lockstep with the rig's own retention constants
// (PREVIEW_DISPLAY_KEEP / PREVIEW_LINEAR_KEEP). So the toolbar is mounted as it
// is and ONE line under it says who may take a FITS - derived from the same role
// table the server enforces via `accessPhrase`, never hand-written.

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { Sheet, Card, EmptyCard, Label, ListRow, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav, useRoute } from "../../../router";
import {
  useConfig,
  useHfrThresholds,
  useLinkDown,
  useLivePreview,
  useLivePreviewId,
  useNight,
  useOverlays,
  usePreviews,
  usePrincipal,
  useSelectedPreviewId,
  useStore,
  useStretch,
  useViewport,
} from "../../../../store";
import { accessPhrase, capAllowed } from "../../../../lib/caps";
import { u } from "../../../../lib/base";
import { shareQuery } from "../../../../lib/share";
import { PreviewStage, type StageControls } from "../../../../components/preview/PreviewStage";
import { PreviewToolbar } from "../../../../components/preview/PreviewToolbar";
import { PreviewMeta } from "../../../../components/preview/PreviewMeta";
import { StretchHistogram } from "../../../../components/preview/StretchHistogram";
import { FrameStats } from "../../../../components/preview/FrameStats";
import { FrameFilmstrip } from "../../../../components/preview/FrameFilmstrip";
import { FocusVerdict } from "../../../../components/preview/FocusVerdict";
import { LiveStackReadout } from "../../../../components/preview/LiveStackReadout";
import { StackInspect } from "./inspectStack";

/** The FITS row is `view.media` on the server. Composed from the role table, so
 *  this sentence cannot drift from what the server actually enforces. */
export const fitsAccessNote = (): string =>
  `FITS downloads need ${accessPhrase("view.media")}.`;

/** What the bright-outlier pixel count on a live-stacked sub actually is. The
 *  number alone reads as damage; it is the stacker throwing a satellite trail,
 *  an aircraft or a cosmic ray out of the stack, which is the stack working. */
const OUTLIER_NOTE =
  "Clipped pixels are bright outliers the stack rejected - a satellite trail, an aircraft or a cosmic ray hit.";

export function InspectSheet({ params }: SheetProps): JSX.Element {
  const src = params.src === "stack" ? "stack" : "preview";
  const route = useRoute();

  // Switching source is a REPLACE, not a push: LAST SUB is changing what this
  // one sheet is looking at, and a Back press should leave Inspect rather than
  // walk back through the two things it showed.
  const goPreview = () => {
    const path = `/${route.hub}${route.sub ? `/${route.sub}` : ""}` +
      `${route.sheets.length ? `/${route.sheets.join("/")}` : ""}?src=preview`;
    nav.replace(path);
  };

  if (src === "stack") {
    return (
      <Sheet
        title="INSPECT"
        sub="session stack"
        icon={<NxIcon name="eye" size={18} />}
        live="the run's colour composite"
        onBack={() => nav.back()}
        data-testid="rig-inspect"
      >
        <StackInspect onLastSub={goPreview} />
      </Sheet>
    );
  }
  return <PreviewInspect params={params} />;
}

// ---------------------------------------------------------------- src=preview

function PreviewInspect({ params }: { params: Record<string, string> }): JSX.Element {
  const previews = usePreviews();
  const live = useLivePreview();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const config = useConfig();
  const principal = usePrincipal();
  const sequence = useStore((s) => s.sequence);
  const captureTarget = useStore((s) => s.captureTarget);

  const setViewport = useStore((s) => s.setViewport);
  const setStretch = useStore((s) => s.setStretch);
  const setOverlays = useStore((s) => s.setOverlays);
  const selectPreview = useStore((s) => s.selectPreview);

  // `?id=` is the DEEP LINK, not a second source of truth. Applying it through
  // `selectPreview` once means the store stays the only place that knows which
  // frame is pinned, so tapping a filmstrip tile afterwards is not fighting the
  // URL - and "return to live" is not undone by the param on the next render.
  const appliedId = useRef<string | null>(null);
  useEffect(() => {
    const raw = params.id;
    if (raw == null || raw === appliedId.current) return;
    appliedId.current = raw;
    const n = Number(raw);
    if (Number.isFinite(n)) selectPreview(n);
  }, [params.id, selectPreview]);

  // Same precedence as `LivePreview`: the pinned-or-live frame, else the newest
  // entry in the ring (the store can hold frames with no live id right after a
  // reconnect), else nothing. Inspect deliberately does NOT reach for the
  // gallery stand-in `LivePreview` uses - there is nothing to inspect about a
  // frame this browser never received the statistics for.
  const shown = live ?? (previews.length ? previews[previews.length - 1] : null);

  const pinned = selectedId != null && selectedId !== liveId;
  const newSincePinned = useMemo(() => {
    if (!pinned || selectedId == null) return 0;
    return previews.filter((p) => p.id > selectedId).length;
  }, [pinned, selectedId, previews]);

  const prev = useMemo(() => {
    if (!shown) return null;
    const idx = previews.findIndex((p) => p.id === shown.id);
    return idx > 0 ? previews[idx - 1] : null;
  }, [shown, previews]);

  const [stretchDragging, setStretchDragging] = useState(false);
  const controls = useRef<StageControls | null>(null);
  // The loupe's state lives inside PreviewStage; this mirror exists only so the
  // toolbar's toggle can render pressed/unavailable. Guarded so an unchanged
  // controls callback cannot loop.
  const [loupe, setLoupe] = useState({ on: false, available: false });

  const scalePct = Math.round((viewport.scale || 1) * 100);
  const starsAvailable = !!shown?.star_list && shown.star_list.length > 0;
  const clipAvailable = !!shown && shown.data_is_linear && shown.full_well != null;
  // Nothing on the stage means the zoom cluster moves nothing, and a percentage
  // that steps over a picture that does not is the toolbar asserting a lie.
  const zoomReason = shown ? null : "Nothing on the stage to zoom yet";

  const canMedia = capAllowed(principal, "view.media");
  const shareTarget = captureTarget || sequence.target || "";
  const shareSubs = sequence.progress?.frames_done;
  const shareHref = shown
    ? u(`/api/preview/${shown.id}/share.jpg${shareQuery(shareTarget, shareSubs)}`)
    : null;

  const solveSaved = config?.solve_saved_lights;

  const footer = shareHref ? (
    // A plain <a download>, never a fetch into a Blob: iOS serves one file at a
    // time in the foreground, and the cookie on the navigation is the auth.
    <a
      className="nx-btn"
      data-kind="secondary"
      data-size="lg"
      data-full="true"
      data-testid="inspect-share"
      href={shareHref}
      download={`firstlight_${shown?.id}.jpg`}
      // `.nx-btn` was written for a <button>; an anchor arrives underlined.
      style={{ textDecoration: "none" }}
    >
      <span className="nx-btn-glyph"><NxIcon name="share" size={16} /></span>
      <span className="nx-btn-label">SAVE FIRST LIGHT</span>
    </a>
  ) : undefined;

  return (
    <Sheet
      title="INSPECT"
      sub={
        pinned
          ? `frame #${shown?.id ?? "?"} pinned${newSincePinned ? ` · ${newSincePinned} newer since` : ""}`
          : "live frame"
      }
      icon={<NxIcon name="eye" size={18} />}
      live={<PreviewMeta preview={shown} />}
      onBack={() => nav.back()}
      footer={footer}
      data-testid="rig-inspect"
    >
      {!shown ? (
        <EmptyCard
          title="NO FRAME TO INSPECT"
          hint="The next exposure lands here with its histogram, stars and statistics."
          data-testid="inspect-empty"
        />
      ) : (
        <>
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
            compact={false}
            minHeight={380}
            bottomRightReserve={0}
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
            zoomReason={zoomReason}
            onZoomIn={() => controls.current?.zoomIn()}
            onZoomOut={() => controls.current?.zoomOut()}
            onFit={() => controls.current?.fit()}
            onHundred={() => controls.current?.hundred()}
            starsAvailable={starsAvailable}
            clipAvailable={clipAvailable}
            linkDown={linkDown}
            shareMeta={{ target: shareTarget, subs: shareSubs }}
            stretch={stretch}
            loupeOn={loupe.on}
            loupeAvailable={loupe.available}
            onLoupe={(v) => controls.current?.setLoupeOn(v)}
          />

          {!canMedia && (
            <div data-testid="inspect-media-note">
              <Mono size={10.5} tone="dim">{fitsAccessNote()}</Mono>
            </div>
          )}

          <div>
            <Label>STRETCH</Label>
            <StretchHistogram
              preview={shown}
              stretch={stretch}
              onStretch={setStretch}
              onDragChange={setStretchDragging}
            />
          </div>

          <div>
            <Label>FRAME</Label>
            <FrameStats preview={shown} hfrGood={hfrGood} hfrWarn={hfrWarn} />
          </div>

          <FocusVerdict preview={shown} prev={prev} hfrGood={hfrGood} hfrWarn={hfrWarn} />

          {shown.livestack && (
            <Card tone="default" data-testid="inspect-livestack">
              <LiveStackReadout preview={shown} />
              {(shown.livestack.clipped ?? 0) > 0 && (
                <p className="nx-empty-hint" style={{ margin: "6px 0 0" }}>{OUTLIER_NOTE}</p>
              )}
            </Card>
          )}

          <div>
            <Label>RECENT FRAMES</Label>
            <FrameFilmstrip
              previews={previews}
              shownId={selectedId}
              liveId={liveId}
              hfrGood={hfrGood}
              hfrWarn={hfrWarn}
              onSelect={selectPreview}
            />
          </div>

          {/* Read-only here on purpose: the toggle's home is Settings > Optics,
              and two places to set one flag is how they end up disagreeing. */}
          <ListRow
            icon={<NxIcon name="optics" size={16} />}
            title="PLATE SOLVE EVERY SAVED LIGHT"
            sub={
              solveSaved == null
                ? "not reported by this rig"
                : solveSaved
                  ? "on - every saved light gets WCS written into it"
                  : "off - saved lights carry no WCS"
            }
            right={
              <Mono size={10.5} tone="dim">
                {solveSaved == null ? "-" : solveSaved ? "ON" : "OFF"}
              </Mono>
            }
            chevron
            onPress={() => nav.sheet("optics")}
            data-testid="inspect-solve-row"
          />
        </>
      )}
    </Sheet>
  );
}
