// FrameViewer — the picture someone opened, at the size of the screen showing it.
//
// Asked for 2026-08-19: tap the centre of a tile, get the frame big; rotate the
// phone and get the size that rotation now needs.
//
// TWO IMAGES, DELIBERATELY. The 256px grid tile is already in the browser cache,
// so it appears instantly (upscaled and a little soft) and the full render fades
// in over it. The alternative — an empty box for the second or two a 26 MP
// stretch takes — is the "grid of grey placeholders" complaint all over again,
// just one frame at a time.
import { useCallback, useEffect, useRef, useState } from "react";
import { BASE } from "../../lib/base";
import { thumbPath } from "../../lib/gallery";
import {
  neededDeviceWidth, shouldRefetch, viewPath, viewWidthFor,
} from "../../lib/frameView";
import { Icon } from "../icons";
import type { GalleryFrame } from "../../types";

/** The rig's frames are 3:2; used only until the real bytes report their own
 *  aspect, which the placeholder thumb does within a frame or two. */
const ASSUMED_ASPECT = 1.5;

export default function FrameViewer({ frame, onClose }: {
  frame: GalleryFrame;
  onClose: () => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [aspect, setAspect] = useState(ASSUMED_ASPECT);
  const [width, setWidth] = useState<number | null>(null);
  const [sharpLoaded, setSharpLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  // Recompute on mount, on resize, and on rotation. `resize` alone is not
  // enough on iOS, where an orientation change can settle after the event;
  // `orientationchange` alone misses a desktop window drag.
  const measure = useCallback(() => {
    const box = boxRef.current?.getBoundingClientRect();
    if (!box) return;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const need = viewWidthFor(neededDeviceWidth(box.width, box.height, aspect, dpr));
    setWidth((cur) => (shouldRefetch(cur, need) ? need : cur));
  }, [aspect]);

  useEffect(() => {
    measure();
    const onResize = () => measure();
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    // iOS settles the new viewport a beat after the event fires.
    const settle = () => window.setTimeout(measure, 250);
    window.addEventListener("orientationchange", settle);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
      window.removeEventListener("orientationchange", settle);
    };
  }, [measure]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const placeholder = `${BASE}${thumbPath(frame.path, 256, frame.mtime)}`;
  const sharp = width ? `${BASE}${viewPath(frame.path, width, frame.mtime)}` : null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/90 overlay-scrim flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label={`Frame ${frame.name}`}
      onClick={onClose}
    >
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-dim shrink-0">
        <span className="truncate font-mono">{frame.name}</span>
        <span className="ml-auto tabular-nums">
          {width ? `${width}px` : "sizing…"}
          {sharp && !sharpLoaded && !failed ? " · loading" : ""}
        </span>
        <button
          type="button"
          className="btn !px-2 min-h-11"
          aria-label="Close"
          onClick={(e) => { e.stopPropagation(); onClose(); }}
        >
          <Icon name="x" size={14} />
        </button>
      </div>

      <div ref={boxRef} className="relative flex-1 min-h-0 flex items-center justify-center p-2">
        {/* already cached by the grid — appears immediately */}
        <img
          src={placeholder}
          alt=""
          aria-hidden
          className={`absolute max-w-full max-h-full object-contain transition-opacity duration-300
            ${sharpLoaded ? "opacity-0" : "opacity-100"}`}
          draggable={false}
          onLoad={(e) => {
            const el = e.currentTarget;
            if (el.naturalWidth && el.naturalHeight) {
              setAspect(el.naturalWidth / el.naturalHeight);
            }
          }}
        />
        {sharp && !failed && (
          <img
            key={sharp}
            src={sharp}
            alt={`Frame ${frame.name}`}
            className={`relative max-w-full max-h-full object-contain transition-opacity duration-300
              ${sharpLoaded ? "opacity-100" : "opacity-0"}`}
            draggable={false}
            onClick={(e) => e.stopPropagation()}
            onLoad={() => { setSharpLoaded(true); setFailed(false); }}
            onError={() => setFailed(true)}
          />
        )}
        {failed && (
          <div className="relative flex flex-col items-center gap-1 text-center px-4">
            <Icon name="alert" size={20} className="text-warn" />
            <span className="text-[11px] text-warn font-display tracking-[0.14em]">
              COULD NOT RENDER
            </span>
            <span className="text-[11px] text-dim">
              The tile above is the cached preview. The FITS is still downloadable.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
