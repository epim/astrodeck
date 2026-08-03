// components/gallery/FrameTile.tsx — one thumbnail in the gallery grid, plus the
// lazy-loading machinery that makes a large library survivable.
//
// TWO THINGS THIS FILE EXISTS TO GET RIGHT
//
// 1. NOTHING LOADS UNTIL IT IS NEARLY ON SCREEN. The grid renders one page
//    (<=500 rows), but rendering 500 <img> tags would fire 500 thumbnail
//    requests at a Pi-class box for the dozen tiles a viewport actually shows —
//    each of which may be a cold FITS decode. An IntersectionObserver holds the
//    `src` back until the tile is within a screen's reach, and then stops
//    watching: once a picture is loaded, unloading it to save nothing would just
//    make scrolling back up flash.
//
// 2. A FRAME WHOSE FILE IS GONE RENDERS AS MISSING, NOT AS A BROKEN IMAGE.
//    `<img onerror>` cannot tell you WHY, and the two whys are opposite
//    verdicts: 404 means the file is not there (nothing to download, and the
//    listing is stale), 422 means the file is there and this server cannot
//    render it (the FITS is intact — the download must still be offered). So a
//    failed <img> is followed by ONE probe fetch whose only job is to read the
//    status code. The happy path stays a plain <img>: it never allocates a Blob,
//    the browser owns the decoded-image memory, and HTTP caching applies —
//    fetch-into-an-object-URL would hand us hundreds of blobs to revoke by hand.

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import type { JSX } from "react";
import { Icon } from "../icons";
import { BASE } from "../../lib/base";
import {
  filePath,
  fmtBytes,
  fmtClockSec,
  frameSubtitle,
  nightVsFilename,
  thumbFailure,
  thumbPath,
  tileFailureCopy,
  type ThumbFailure,
} from "../../lib/gallery";
import type { GalleryFrame } from "../../types";

/** How far outside the viewport a tile starts loading. One screen-ish: far
 *  enough that a normal scroll never shows an empty tile, near enough that a
 *  flick through 500 rows does not request all of them. */
export const TILE_ROOT_MARGIN = "400px";

export type TileState = "loading" | "ok" | ThumbFailure;

/**
 * `[ref, inView]` — true once the element has been within `rootMargin` of the
 * viewport, and true forever after (see the header: un-loading is a flash for no
 * gain). Fails OPEN when IntersectionObserver is missing: a grid of permanently
 * blank tiles is a worse outcome than some extra requests.
 */
export function useInView<T extends Element>(
  rootMargin: string = TILE_ROOT_MARGIN,
): [MutableRefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          io.disconnect();
        }
      },
      { rootMargin },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [rootMargin]);
  return [ref, inView];
}

// ---------------------------------------------------------------- presentation

/**
 * The tile as a pure function of its state — no observer, no fetch, no store.
 * Split out so the failure copy (the part with a wrong answer that costs a user
 * a night) can be asserted directly in a test without a DOM or a network.
 */
export function TileSurface({
  frame, state, status, selected, selectable, canDownload, onToggle,
  thumbSrc, onImgLoad, onImgError,
}: {
  frame: GalleryFrame;
  state: TileState;
  /** HTTP status behind a failure, for the "PREVIEW FAILED" sentence. */
  status?: number;
  selected: boolean;
  /** false when the caller can neither delete nor download — with no bulk
   *  action available, a tick box is a control that does nothing. */
  selectable: boolean;
  /** view.media. Without it there is no FITS download to offer at all. */
  canDownload: boolean;
  onToggle: () => void;
  /** Absent until the tile has scrolled into reach; that IS the lazy load. */
  thumbSrc?: string;
  onImgLoad?: () => void;
  onImgError?: () => void;
}): JSX.Element {
  const failed = state === "missing" || state === "unrenderable" || state === "error";
  const copy = failed ? tileFailureCopy(state as ThumbFailure, status) : null;
  const gone = state === "missing";
  const rollover = nightVsFilename(frame);

  // Everything a mouse user gets from hovering, spelled out for everyone else
  // too: the full relative path (the grid truncates it), the night that decided
  // the filing, the wall clock, and the size.
  const title =
    `${frame.path}\n${frame.night} · captured ${fmtClockSec(frame.ts)} · ${fmtBytes(frame.bytes)}` +
    (rollover ? `\n${rollover}` : "");

  return (
    <div
      className={`relative flex flex-col border bg-panel/60 min-w-0
        ${selected ? "border-accent" : gone ? "border-dashed border-bad/60" : "border-line"}`}
      data-tile-state={state}
      data-selected={selected ? "true" : undefined}
    >
      {/* ---- picture area. aspect-square, so row heights are equal before a
              single image has arrived and the page does not reflow as they
              land. */}
      <div className="relative aspect-square bg-black/40 overflow-hidden" title={title}>
        {/* Placeholder underneath: visible through an <img> that has no bytes
            yet, and the whole picture for a tile that never gets any. */}
        <span className="absolute inset-0 flex items-center justify-center text-faint" aria-hidden>
          <Icon name="gallery" size={28} strokeWidth={1} />
        </span>
        {thumbSrc && !failed && (
          <img
            src={thumbSrc}
            alt={`Preview of ${frame.name}`}
            className={`relative w-full h-full object-cover transition-opacity duration-200
              ${state === "ok" ? "opacity-100" : "opacity-0"}`}
            draggable={false}
            onLoad={onImgLoad}
            onError={onImgError}
          />
        )}
        {copy && (
          // Status by GLYPH + WORDS, never by hue: the night palette collapses
          // bad/warn toward the same coral, so a red border alone would say
          // nothing at 2am.
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-2 text-center">
            <Icon name={gone ? "x" : "alert"} size={20} className={gone ? "text-bad" : "text-warn"} />
            <span className={`text-[10px] font-display tracking-[0.14em] ${gone ? "text-bad" : "text-warn"}`}>
              {copy.label}
            </span>
            <span className="text-[10px] leading-tight text-dim">{copy.hint}</span>
          </div>
        )}

        {/* ---- tick box. A real <input type="checkbox"> — a div with
                role="checkbox" needs its own keyboard handling and gets it wrong
                more often than not. 44px hit area around a 16px box. */}
        {selectable && (
          <label
            className="absolute top-0 left-0 w-11 h-11 flex items-center justify-center cursor-pointer"
            title={selected ? `Deselect ${frame.name}` : `Select ${frame.name}`}
          >
            <input
              type="checkbox"
              checked={selected}
              onChange={onToggle}
              aria-label={`Select ${frame.name}`}
              className="w-4 h-4 accent-[var(--accent)]"
            />
          </label>
        )}

        {/* ---- single-frame download. Absent when the file is gone: offering a
                download for a frame the server just said is not there is the
                broken-image lie in another costume. */}
        {canDownload && !gone && (
          <a
            href={`${BASE}${filePath(frame.path)}`}
            download={frame.name}
            className="absolute top-0 right-0 w-11 h-11 flex items-center justify-center
              text-dim hover:text-accent"
            title={`Download ${frame.name} (${fmtBytes(frame.bytes)})`}
            aria-label={`Download ${frame.name}, ${fmtBytes(frame.bytes)}`}
          >
            <Icon name="download" size={16} />
          </a>
        )}
      </div>

      {/* ---- caption. The filename is the identity the user recognises, so it
              gets the width; everything derived sits under it at half size. */}
      <div className="px-1.5 py-1 min-w-0">
        <div className="mono text-[10px] text-ink truncate" title={frame.path}>
          {frame.name}
        </div>
        <div className="flex items-center gap-1.5 text-[10px] text-dim min-w-0">
          <span className="truncate">{frameSubtitle(frame)}</span>
          <span className="ml-auto shrink-0 tabular-nums">{fmtBytes(frame.bytes)}</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- container

export default function FrameTile(props: {
  frame: GalleryFrame;
  selected: boolean;
  selectable: boolean;
  canDownload: boolean;
  onToggle: () => void;
}): JSX.Element {
  const [ref, inView] = useInView<HTMLDivElement>();
  const [state, setState] = useState<TileState>("loading");
  const [status, setStatus] = useState<number | undefined>(undefined);
  // Bumped once, and only once, when the probe contradicts the <img> (see
  // below). It rides in the URL so React mounts a FRESH <img> rather than
  // reusing the element the browser has already recorded as failed.
  const [attempt, setAttempt] = useState(0);
  const { frame } = props;
  const src = `${BASE}${thumbPath(frame.path, 256, frame.mtime)}`
    + (attempt ? `&r=${attempt}` : "");

  // A new path (or a re-capture at the same path) is a new picture: reset, so a
  // recycled tile never keeps the previous frame's verdict.
  useEffect(() => {
    setState("loading");
    setStatus(undefined);
    setAttempt(0);
  }, [frame.path, frame.mtime]);

  // The probe. One request whose only product is a status code — see the header:
  // 404 and 422 are opposite verdicts and <img> reports neither. Guarded on
  // `alive` because a fast scroll unmounts tiles mid-flight.
  const probe = () => {
    let alive = true;
    void fetch(src, { credentials: "same-origin" })
      .then((res) => {
        if (!alive) return;
        setStatus(res.status);
        if (!res.ok) {
          setState(thumbFailure(res.status));
          return;
        }
        // The image failed but the server is serving it — a transient drop on
        // field WiFi. Retry ONCE with a changed URL; a second disagreement is
        // reported as the failure it is rather than looped over, which is how a
        // grid ends up hammering a Pi.
        if (attempt === 0) {
          setAttempt(1);
          setState("loading");
        } else {
          setStatus(undefined);
          setState("error");
        }
      })
      .catch(() => {
        if (!alive) return;
        setStatus(undefined);
        setState("error");
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
          thumbSrc={src}
          onImgLoad={() => setState("ok")}
          onImgError={() => {
            cancelProbe.current?.();
            cancelProbe.current = probe();
          }}
        />
      ) : (
        // Reserve the tile's box before it loads so the scrollbar does not jump
        // as the grid fills in behind the user's thumb.
        <div className="border border-line bg-panel/40 aspect-square" aria-hidden />
      )}
    </div>
  );
}
