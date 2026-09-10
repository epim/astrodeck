// FrameTile.tsx - one thumbnail in the frame library, rebuilt in the new UI's
// own vocabulary (wave R7, area H: `components/gallery/FrameTile.tsx`).
//
// The legacy file is NOT deleted and NOT edited - `#/classic` still renders it.
// What is SHARED is the logic that took a measured failure to get right:
// `useInView` (the observer), `lib/thumbQueue.ts` (the concurrency cap and the
// shared 429 cooldown) and `lib/gallery.ts` (widths, failure classification,
// copy). What is REBUILT is the picture, the corners and the caption.
//
// THREE THINGS THIS FILE STILL HAS TO GET RIGHT, all carried over verbatim
// because each of them is a measured incident, not a preference:
//
// 1. NOTHING LOADS UNTIL IT IS NEARLY ON SCREEN. A page is up to 500 rows and
//    a viewport shows a dozen; 500 <img> tags would fire 500 thumbnail
//    requests at a Pi-class box, each possibly a cold FITS decode. The
//    observer holds the tile's whole subtree back until it is within a
//    screen's reach, and then stops watching - unloading a picture to save
//    nothing would just make scrolling back up flash.
//
// 2. AND THEN NOT ALL AT ONCE. Measured against the relay 2026-08-10: 41 tiles
//    inside the observer margin set 41 `src`s in one animation frame, 19 came
//    back 429 and ZERO tiles rendered. `acquire()` caps the in-flight count and
//    one tile's 429 parks every other pending tile, because the token bucket is
//    per-IP and therefore a fact about the page, not about this frame.
//
// 3. A FRAME WHOSE FILE IS GONE RENDERS AS MISSING, NOT AS A BROKEN IMAGE.
//    `<img onerror>` cannot say WHY, and the two whys are opposite verdicts:
//    404 means the file is not there (nothing to download, the listing is
//    stale), 422 means the file IS there and this server cannot render it (the
//    FITS is intact - the download must still be offered). So a failed <img> is
//    followed by ONE probe fetch whose only product is a status code. The happy
//    path stays a plain <img>: no Blob to revoke, and HTTP caching applies.

import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";

import { useInView } from "../../../../../components/gallery/FrameTile";
import { u } from "../../../../../lib/base";
import {
  filePath, frameSubtitle, nightVsFilename, retryableThumb, thumbFailure,
  thumbPath, thumbWidthFor, type ThumbFailure,
} from "../../../../../lib/gallery";
import { acquire, noteRateLimited, noteSucceeded } from "../../../../../lib/thumbQueue";
import type { GalleryFrame } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { Checkbox22 } from "../../../../ui";
import { bytesLabel, tileCopy, tileTitle } from "./frameCopy";

/** The grid is `minmax(140px, 1fr)`, so a tile is ~140-200 CSS px depending on
 *  how the row divides. Asking off a fixed hint rather than a measured width is
 *  deliberate: the server caches on path+mtime+width, and a width that tracked
 *  every viewport pixel would miss that cache on every resize and re-render a
 *  26-megapixel frame to answer it. `thumbWidthFor` rounds to a step anyway. */
const TILE_CSS_WIDTH_HINT = 200;

/** How many times a tile re-queues after a 429 before it reports the failure.
 *  Four attempts against the queue's escalating backoff spans about 20 s, far
 *  longer than any burst the grid itself can create. */
const MAX_RATE_RETRIES = 4;

export type TileState = "loading" | "ok" | ThumbFailure;

// ---------------------------------------------------------------- presentation

/**
 * The tile as a pure function of its state - no observer, no fetch, no store.
 * Split out so the failure copy (the part with a wrong answer that costs a user
 * a night) can be asserted directly without a DOM or a network.
 */
export function TileSurface({
  frame, state, status, selected, selectable, canDownload, onToggle, onOpen,
  thumbSrc, onImgLoad, onImgError,
}: {
  frame: GalleryFrame;
  state: TileState;
  /** HTTP status behind a failure, for the "PREVIEW FAILED" sentence. */
  status?: number;
  selected: boolean;
  /** false when the caller can neither delete nor download - with no bulk
   *  action available, a tick box is a control that does nothing. */
  selectable: boolean;
  /** `view.media`. Without it there is no FITS download to offer at all. */
  canDownload: boolean;
  /** `shift` is true for a shift+click, which the grid turns into a range
   *  select. `Checkbox22` reports only the next boolean, so the modifier is
   *  read off the native click in the CAPTURE phase on the way down to it -
   *  React runs an ancestor's capture handler before the target's own onClick,
   *  and a keyboard space carries `shiftKey: false`, which is the right
   *  answer. */
  onToggle: (shift: boolean) => void;
  /** Open the frame in the viewer. Absent = no viewer (the tile is inert in
   *  the centre, which is how it behaved before the viewer existed). */
  onOpen?: () => void;
  /** Absent until the tile has scrolled into reach; that IS the lazy load. */
  thumbSrc?: string;
  onImgLoad?: () => void;
  onImgError?: () => void;
}): JSX.Element {
  const shift = useRef(false);
  const failed = state === "missing" || state === "unrenderable" || state === "error";
  const copy = failed ? tileCopy(state as ThumbFailure, status) : null;
  const gone = state === "missing";
  const title = tileTitle(
    frame.path, frame.night, frame.local_clock, frame.bytes, nightVsFilename(frame),
  );

  return (
    <div
      className="nx-frames-tile"
      data-testid="gallery-tile"
      data-tile-state={state}
      data-selected={selected ? "true" : "false"}
    >
      <div className="nx-frames-pic" title={title}>
        <span className="nx-frames-ph" aria-hidden="true">
          <NxIcon name="eye" size={26} strokeWidth={1} />
        </span>

        {thumbSrc && !failed && (
          <img
            className="nx-frames-img"
            data-ready={state === "ok" ? "true" : "false"}
            src={thumbSrc}
            alt={`Preview of ${frame.name}`}
            draggable={false}
            onLoad={onImgLoad}
            onError={onImgError}
          />
        )}

        {copy && (
          // Status by GLYPH plus WORDS, never by hue: the night palette
          // collapses bad and warn toward the same coral, so a red border alone
          // would say nothing at 2am.
          <div className="nx-frames-fail" data-gone={gone ? "true" : "false"}>
            <NxIcon name={gone ? "x" : "info"} size={18} />
            <span className="nx-frames-fail-word">{copy.label}</span>
            <span className="nx-frames-fail-hint">{copy.hint}</span>
          </div>
        )}

        {/* OPEN: the centre of the picture only. Declared BEFORE the corners so
            the 44 px tick box and download sit above it in paint order. */}
        {onOpen && !gone && (
          <button
            type="button"
            className="nx-frames-open"
            aria-label={`Open ${frame.name}`}
            title={`Open ${frame.name}`}
            onClick={(e) => { e.stopPropagation(); onOpen(); }}
          />
        )}

        {selectable && (
          <span
            className="nx-frames-tickwrap"
            onClickCapture={(e) => { shift.current = e.shiftKey === true; }}
          >
            <Checkbox22
              className="nx-frames-tick"
              data-testid="gallery-tile-tick"
              checked={selected}
              onChange={() => onToggle(shift.current)}
              label={<span className="nx-frames-sr">{`Select ${frame.name}`}</span>}
            />
          </span>
        )}

        {/* Absent when the file is gone: offering a download for a frame the
            server just said is not there is the broken-image lie in another
            costume. */}
        {canDownload && !gone && (
          <a
            className="nx-frames-dl"
            href={u(filePath(frame.path))}
            download={frame.name}
            title={`Download ${frame.name} (${bytesLabel(frame.bytes)})`}
            aria-label={`Download ${frame.name}, ${bytesLabel(frame.bytes)}`}
          >
            <NxIcon name="download" size={16} />
          </a>
        )}
      </div>

      <div className="nx-frames-cap">
        <div className="nx-frames-capname" title={frame.path}>{frame.name}</div>
        <div className="nx-frames-capline">
          <span className="nx-frames-captext">{frameSubtitle(frame)}</span>
          <span className="nx-frames-capsize">{bytesLabel(frame.bytes)}</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- container

export function FrameTile(props: {
  frame: GalleryFrame;
  selected: boolean;
  selectable: boolean;
  canDownload: boolean;
  onToggle: (shift: boolean) => void;
  onOpen?: () => void;
}): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>();
  const [state, setState] = useState<TileState>("loading");
  const [status, setStatus] = useState<number | undefined>(undefined);
  // Bumped once, and only once, when the probe contradicts the <img>. It rides
  // in the URL so React mounts a FRESH <img> rather than reusing the element
  // the browser has already recorded as failed.
  const [attempt, setAttempt] = useState(0);
  // Bumped on every RETRYABLE failure (429/503). Separate from `attempt`, which
  // is the one-shot img-vs-probe disagreement: a rate limit is not a
  // disagreement and must not consume that single retry.
  const [rateRetry, setRateRetry] = useState(0);
  // `false` until the queue hands this tile a slot. Gating the SRC (rather than
  // the fetch) keeps the happy path a plain <img>.
  const [slotted, setSlotted] = useState(false);
  const releaseRef = useRef<(() => void) | null>(null);
  const { frame } = props;
  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const width = thumbWidthFor(TILE_CSS_WIDTH_HINT, dpr);
  const src = u(thumbPath(frame.path, width, frame.mtime))
    + (attempt ? `&r=${attempt}` : "")
    + (rateRetry ? `&q=${rateRetry}` : "");

  // A new path (or a re-capture at the same path) is a new picture: reset, so a
  // recycled tile never keeps the previous frame's verdict.
  useEffect(() => {
    setState("loading");
    setStatus(undefined);
    setAttempt(0);
    setRateRetry(0);
  }, [frame.path, frame.mtime]);

  // Take a queue slot once the tile is in view, and give it back on unmount so
  // a fast scroll past a hundred tiles cannot leak the whole cap away.
  useEffect(() => {
    if (!inView) return;
    const { slot, cancel } = acquire();
    let alive = true;
    void slot.then((release) => {
      releaseRef.current = release;
      if (!alive) { release(); return; }
      setSlotted(true);
    });
    return () => {
      alive = false;
      cancel();
      releaseRef.current?.();
      releaseRef.current = null;
    };
    // `rateRetry` re-queues the tile behind everyone else, which is the point:
    // a rate-limited tile must not jump the queue it just overflowed.
  }, [inView, frame.path, frame.mtime, rateRetry]);

  // The probe. One request whose only product is a status code. Guarded on
  // `alive` because a fast scroll unmounts tiles mid-flight.
  const probe = (): (() => void) => {
    let alive = true;
    // EVERY path out of this probe must give the slot back. A terminal tile
    // that keeps its slot shrinks the cap permanently, and enough of them
    // deadlock the grid - the same wall of empty boxes this whole mechanism
    // exists to remove, arrived at from the opposite direction.
    const settle = (next: TileState, code?: number) => {
      releaseRef.current?.();
      releaseRef.current = null;
      setStatus(code);
      setState(next);
    };
    void fetch(src, { credentials: "same-origin" })
      .then((res) => {
        if (!alive) return;
        // "LATER" IS NOT A VERDICT. A 429 says the relay's per-IP bucket is
        // empty, which is a fact about the page, not about this frame. Tell the
        // queue (so every other pending tile also backs off) and re-queue rather
        // than painting PREVIEW FAILED over a picture that is perfectly fine.
        if (retryableThumb(res.status)) {
          const ra = Number(res.headers.get("Retry-After"));
          noteRateLimited(Number.isFinite(ra) ? ra : null);
          if (rateRetry < MAX_RATE_RETRIES) {
            releaseRef.current?.();
            releaseRef.current = null;
            setStatus(undefined);
            setState("loading");
            setSlotted(false);
            setRateRetry((n) => n + 1);
          } else {
            // Bounded: a link still saying no after this many tries is reported
            // honestly rather than retried forever.
            settle("error", res.status);
          }
          return;
        }
        if (!res.ok) {
          settle(thumbFailure(res.status), res.status);
          return;
        }
        noteSucceeded();
        // The image failed but the server is serving it - a transient drop on
        // field WiFi. Retry ONCE with a changed URL; a second disagreement is
        // reported as the failure it is. The slot is HELD across that retry: it
        // is the same tile asking for the same bytes.
        if (attempt === 0) {
          setStatus(res.status);
          setAttempt(1);
          setState("loading");
        } else {
          settle("error", undefined);
        }
      })
      .catch(() => {
        if (!alive) return;
        settle("error", undefined);
      });
    return () => { alive = false; };
  };
  const cancelProbe = useRef<(() => void) | null>(null);
  useEffect(() => () => cancelProbe.current?.(), []);

  return (
    <div ref={ref}>
      {inView ? (
        <TileSurface
          {...props}
          state={state}
          status={status}
          // Empty until the queue says go: an <img> with no src makes no
          // request, which is the whole mechanism.
          thumbSrc={slotted ? src : ""}
          onImgLoad={() => {
            noteSucceeded();
            releaseRef.current?.();
            releaseRef.current = null;
            setState("ok");
          }}
          onImgError={() => {
            // Hold the slot through the probe: it is the same request, and
            // releasing here would let another tile start while this one is
            // still occupying the link.
            cancelProbe.current?.();
            cancelProbe.current = probe();
          }}
        />
      ) : (
        <div className="nx-frames-skel" aria-hidden="true" />
      )}
    </div>
  );
}

/** The grid the tiles sit in. One class, so the column rule and the thumbnail
 *  width hint above cannot drift apart in two files. */
export function FrameGrid({ children }: { children: ReactNode }): JSX.Element {
  return <div className="nx-frames-grid" data-testid="gallery-grid">{children}</div>;
}
