// FrameViewer.tsx - the picture someone opened, at the size of the screen
// showing it (wave R7, area H: `components/gallery/FrameViewer.tsx`).
//
// WHY IT IS A SHEET AND NOT A SCRIM. The legacy viewer was a bare
// `fixed inset-0` overlay with a tiny x in the corner and a click-anywhere
// dismiss, which on a tablet meant every attempt to pan the picture closed it.
// The new one is the design's `Sheet`: the same BACK pill every other sheet
// has, the frame's name as the title, and a live line that says which render is
// on screen. It is still full-viewport rather than the 420 px panel, because
// the whole point of opening a frame is the size of the screen showing it.
//
// TWO IMAGES, DELIBERATELY. The 256 px grid tile is already in the browser
// cache, so it appears instantly (upscaled and a little soft) and the full
// render fades in over it. The alternative - an empty box for the second or two
// a 26 MP stretch takes - is the "grid of grey placeholders" complaint all over
// again, one frame at a time.
//
// THE DELETE. Opening a frame is when someone decides it is a dud, so the bin
// is offered here rather than only behind a tick box two screens away. It is
// `control.capture` (the server gates `POST /api/gallery/trash` on it), it goes
// through the same recoverable confirmation the library's bulk delete uses, and
// without the capability it is honest-disabled with the reason - never hidden,
// and never live enough to answer a press with a 403.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import { ApiError } from "../../../../../api";
import { trashFrames } from "../../../../../api/gallery";
import { u } from "../../../../../lib/base";
import { useCanControlCapture } from "../../../../../lib/caps";
import { confirmDialog } from "../../../../../components/ConfirmDialog";
import {
  neededDeviceWidth, shouldRefetch, viewPath, viewWidthFor,
} from "../../../../../lib/frameView";
import { filePath, thumbPath, trashConfirmCopy } from "../../../../../lib/gallery";
import { useStore } from "../../../../../store";
import type { GalleryFrame } from "../../../../../types";
import { NxIcon } from "../../../../icons";
import { explainLock } from "../../../../shell/explain";
import { ActionButton, LockNote, Mono, Sheet } from "../../../../ui";
import {
  DELETE_LOCK, FITS_LOCK, FITS_LOCK_NOTE, bytesLabel, hy, VIEWER_FAIL_HINT,
  viewerLive,
} from "./frameCopy";

/** The rig's frames are 3:2; used only until the real bytes report their own
 *  aspect, which the placeholder thumb does within a frame or two. */
const ASSUMED_ASPECT = 1.5;

export function FrameViewer({ frame, canDownload, trashTtlDays = 30, onClose, onTrashed }: {
  frame: GalleryFrame;
  /** `view.media`. Without it there is no FITS download to offer at all - the
   *  JPEG on screen is `view.preview` and stays available to everyone. */
  canDownload: boolean;
  /** How long the bin keeps a frame, for the confirmation's own sentence. */
  trashTtlDays?: number;
  onClose: () => void;
  /** Fired after this frame reached the bin, so the grid behind can re-read. */
  onTrashed?: () => void;
}): JSX.Element {
  const canDelete = useCanControlCapture();
  const enqueueToast = useStore((s) => s.enqueueToast);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [aspect, setAspect] = useState(ASSUMED_ASPECT);
  const [width, setWidth] = useState<number | null>(null);
  const [sharpLoaded, setSharpLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

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

  const placeholder = u(thumbPath(frame.path, 256, frame.mtime));
  const sharp = width ? u(viewPath(frame.path, width, frame.mtime)) : null;

  const deleteReason = !canDelete ? DELETE_LOCK
    : busy ? "The bin is still answering the last request." : null;

  async function onDelete(): Promise<void> {
    const copy = trashConfirmCopy(1, frame.bytes, trashTtlDays);
    const go = await confirmDialog({
      title: hy(copy.title), body: hy(copy.body), tone: "warn",
      mode: "confirm", confirmLabel: "Move to trash",
    });
    if (!go) return;
    setBusy(true);
    try {
      const r = await trashFrames([frame.path]);
      if (r.failed.length) {
        enqueueToast({
          level: "warning", title: "The rig refused this frame",
          detail: r.failed[0]?.reason ?? undefined,
        });
        return;
      }
      enqueueToast({
        level: "success",
        title: `${frame.name} is in the trash`,
        detail: `${bytesLabel(r.bytes)} recoverable for ${trashTtlDays} days - the space is not freed until it purges.`,
      });
      onTrashed?.();
      onClose();
    } catch (e) {
      enqueueToast({
        level: "error", title: "Delete failed",
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="nx-frames-viewer" data-testid="gallery-viewer">
      <Sheet
        title={frame.name}
        icon={<NxIcon name="eye" size={18} />}
        sub={`${frame.night} - ${frame.local_clock} rig time - ${bytesLabel(frame.bytes)}`}
        live={<span data-testid="gallery-viewer-live">{viewerLive(width, sharp != null && !sharpLoaded, failed)}</span>}
        onBack={onClose}
        backLabel="LIBRARY"
        footer={
          <div className="nx-frames-actions">
            {canDownload ? (
              <a className="nx-btn" data-kind="ghost" data-testid="gallery-viewer-download"
                href={u(filePath(frame.path))} download={frame.name}>
                <span className="nx-btn-label">{`DOWNLOAD FITS ${bytesLabel(frame.bytes)}`}</span>
              </a>
            ) : (
              <ActionButton
                kind="ghost"
                data-testid="gallery-viewer-download"
                lockedReason={FITS_LOCK}
                onExplain={explainLock}
                onPress={() => { /* locked */ }}
              >
                DOWNLOAD FITS
              </ActionButton>
            )}
            <span className="nx-frames-spacer" />
            <ActionButton
              kind="danger"
              data-testid="gallery-viewer-delete"
              busy={busy}
              lockedReason={deleteReason}
              onExplain={explainLock}
              onPress={() => void onDelete()}
            >
              MOVE TO TRASH
            </ActionButton>
          </div>
        }
      >
        <div ref={boxRef} className="nx-frames-viewbox">
          {/* Already cached by the grid, so it appears immediately. */}
          <img
            className="nx-frames-viewimg"
            data-role="cache"
            data-ready={sharpLoaded ? "true" : "false"}
            src={placeholder}
            alt=""
            aria-hidden="true"
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
              className="nx-frames-viewimg"
              data-role="sharp"
              data-ready={sharpLoaded ? "true" : "false"}
              data-testid="gallery-viewer-img"
              src={sharp}
              alt={`Frame ${frame.name}`}
              draggable={false}
              onLoad={() => { setSharpLoaded(true); setFailed(false); }}
              onError={() => setFailed(true)}
            />
          )}
          {failed && (
            <div className="nx-frames-viewfail">
              <NxIcon name="info" size={18} />
              <span className="nx-frames-fail-word">COULD NOT RENDER</span>
              <Mono size={10.5} tone="dim">{VIEWER_FAIL_HINT}</Mono>
            </div>
          )}
        </div>
        {!canDownload && <LockNote reason={FITS_LOCK_NOTE} data-testid="gallery-viewer-fitsnote" />}
      </Sheet>
    </div>
  );
}
