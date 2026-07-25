// LoupePanel — the advanced 1:1 pixel-peep loupe (opt-in, off by default).
// (crop+render UI design §1.4, §4.5, Decision D)
//
// PROGRESSIVE DISCLOSURE: a novice never sees this. It only mounts when the user
// turns on the toolbar's "1:1" toggle, which is itself honest-disabled off the
// linear path.
//
// Decision D — it follows the VIEWPORT CENTRE and reuses the crop the zoom layer
// already fetched, so it adds essentially zero traffic. (Cursor-follow would
// re-fetch on every mouse move; that's a later idea, not this one.)
//
// DUMB RENDER SHELL: it positions an <img> and prints numbers. No fetching, no
// geometry — that all lives in lib/cropRoi.ts + useCropZoom.
import { useState } from "react";
import type { CropRoi } from "../../lib/cropRoi";

const LOUPE = 160; // CSS px; at 1:1 that is 160 real sensor pixels

export function LoupePanel({
  url,
  roi,
  centerX,
  centerY,
  previewId,
  loading,
}: {
  url: string | null;
  roi: CropRoi | null;
  /** viewport-centre in SENSOR px — what the loupe is centred on */
  centerX: number;
  centerY: number;
  previewId: number;
  loading: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const roiText = roi ? `x=${roi.x} y=${roi.y} w=${roi.w} h=${roi.h}` : "";

  const copy = () => {
    if (!roiText) return;
    void navigator.clipboard?.writeText(roiText).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      },
      () => {
        /* clipboard denied — the numbers are on screen either way */
      },
    );
  };

  return (
    <div className="panel p-1.5 flex flex-col gap-1" style={{ width: LOUPE + 12 }}>
      <div
        className="relative overflow-hidden bg-black/60 border border-line"
        style={{ width: LOUPE, height: LOUPE }}
      >
        {url && roi ? (
          <img
            src={url}
            alt=""
            aria-hidden
            draggable={false}
            className="astro absolute"
            style={{
              // 1 sensor px == 1 CSS px, centred on the viewport centre
              left: LOUPE / 2 - (centerX - roi.x),
              top: LOUPE / 2 - (centerY - roi.y),
              width: roi.w,
              height: roi.h,
              imageRendering: "pixelated",
            }}
          />
        ) : (
          // Honest empty state (R3): the linear source is kept for only the
          // latest 1–2 frames, so a crop CAN be unavailable. Say so rather than
          // showing an upscaled blur and calling it 1:1.
          <div className="absolute inset-0 flex items-center justify-center text-center px-2 text-dim text-[10px] leading-tight">
            {loading ? "Fetching sensor pixels…" : "Sensor pixels unavailable for this frame"}
          </div>
        )}
        {/* centre crosshair so the expert knows exactly which pixel is sampled */}
        <div className="absolute inset-0 pointer-events-none" aria-hidden>
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-accent/40" />
          <div className="absolute top-1/2 left-0 right-0 h-px bg-accent/40" />
        </div>
      </div>
      <button
        type="button"
        onClick={copy}
        title={roiText ? "Copy the sensor ROI to the clipboard" : "No ROI yet"}
        className="mono text-[10px] text-dim tabular-nums text-left leading-tight hover:text-ink"
      >
        {roi ? (
          <>
            {roi.x},{roi.y} {roi.w}×{roi.h} @1:1
          </>
        ) : (
          "— @1:1"
        )}
        <span className="text-dim"> · #{previewId}</span>
        {copied && <span className="text-accent"> copied</span>}
      </button>
    </div>
  );
}
