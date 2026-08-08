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
//
// SIZE IS NOT FIXED: the box is whatever `lib/cropRoi.loupeBoxSize()` says the
// MEASURED stage box can afford (a 172px panel was ~48% of a 360px phone
// stage). The caller passes it; this file just lays it out.
//
// TWO LAYOUTS, one for each stage. The full one puts a 44px "Copy region" row
// under the window. `dense` collapses that row onto the window itself, because
// on a `compact` stage (Focus, 3:2, floored at 230px tall) those 44px are the
// whole difference between a magnifier and none: the panel has to end above the
// focus pod's disc, and 88 + 62 does not, while 88 + 32 does. Nothing is
// withdrawn — the ROI numbers stay on screen and the copy keeps a finger path.
import { useState } from "react";
import { LOUPE_BOX_MAX, LOUPE_CHROME_PX, type CropRoi } from "../../lib/cropRoi";
import { LockedChip } from "../ui";

export function LoupePanel({
  url,
  roi,
  centerX,
  centerY,
  previewId,
  loading,
  size = LOUPE_BOX_MAX,
  dense = false,
}: {
  url: string | null;
  roi: CropRoi | null;
  /** viewport-centre in SENSOR px — what the loupe is centred on */
  centerX: number;
  centerY: number;
  previewId: number;
  loading: boolean;
  /** 1:1 box edge in CSS px — from `loupeBoxSize(stageWidth)` */
  size?: number;
  /** Collapse the 44px copy row (see LOUPE_CHROME_V_DENSE_PX). The 1:1 window
   *  becomes the copy target itself — at ≥88px it is already well over the tap
   *  floor — and the ROI drops to one 10px line. Used on a `compact` stage,
   *  where those 44px are the difference between a loupe and no loupe. */
  dense?: boolean;
}) {
  const LOUPE = size;
  /** null = idle · "ok" = copied · "fail" = the browser refused. */
  const [outcome, setOutcome] = useState<null | "ok" | "fail">(null);

  const roiText = roi ? `x=${roi.x} y=${roi.y} w=${roi.w} h=${roi.h}` : "";

  const copy = () => {
    if (!roiText) return;
    const settle = (r: "ok" | "fail") => {
      setOutcome(r);
      window.setTimeout(() => setOutcome(null), r === "ok" ? 1600 : 3500);
    };
    // `navigator.clipboard` is undefined in a NON-SECURE CONTEXT, and AstroDeck's
    // normal deployment is exactly that: plain http to a box on the LAN. The old
    // `navigator.clipboard?.writeText(...)` optional-chained straight to
    // `undefined` there, so the whole press evaluated to nothing — no copy, no
    // error, no feedback, on every field install that isn't localhost. Try the
    // async API, fall back to the legacy selection copy (which DOES work over
    // http), and if both refuse, SAY SO rather than pretending it worked.
    const nav = navigator.clipboard;
    if (nav?.writeText) {
      void nav.writeText(roiText).then(
        () => settle("ok"),
        () => settle(legacyCopy(roiText) ? "ok" : "fail"),
      );
      return;
    }
    settle(legacyCopy(roiText) ? "ok" : "fail");
  };

  const windowBox = (
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
  );

  // DENSE (compact stage): the 44px caption row is what does not fit, so the
  // WINDOW carries the copy action — it is ≥88px square, i.e. twice the tap
  // floor — and the ROI drops to one 10px line. Nothing is silently withdrawn:
  // the numbers are still on screen and the copy still has a finger path. With
  // no crop in hand there is no action to offer, so the window stays inert and
  // the line says which of the two reasons it is (§11.8: never a dead control,
  // and never an unexplained blank either).
  if (dense) {
    return (
      <div className="panel p-1.5 flex flex-col gap-1" style={{ width: LOUPE + LOUPE_CHROME_PX }}>
        {roi ? (
          <button
            type="button"
            onClick={copy}
            aria-label={`Copy the sensor region ${roiText} to the clipboard`}
            title="Copy the sensor ROI to the clipboard"
            className="tap block"
            style={{ lineHeight: 0 }}
          >
            {windowBox}
          </button>
        ) : (
          windowBox
        )}
        <span
          className={`mono text-[10px] tabular-nums leading-tight truncate ${
            outcome === "ok" ? "text-accent" : outcome === "fail" ? "text-warn" : "text-dim"
          }`}
        >
          {outcome === "ok"
            ? "✓ Copied"
            : outcome === "fail"
              ? "✕ Copy blocked"
              : roi
                ? `${roi.x},${roi.y} ${roi.w}×${roi.h} · #${previewId}`
                : loading
                  ? `Fetching… · #${previewId}`
                  : `No crop · #${previewId}`}
        </span>
        <span className="sr-only" aria-live="polite">
          {outcome === "ok"
            ? `Copied sensor region ${roiText}`
            : outcome === "fail"
              ? "This browser refused the clipboard. The region is printed above — copy it by hand."
              : ""}
        </span>
      </div>
    );
  }

  return (
    <div className="panel p-1.5 flex flex-col gap-1" style={{ width: LOUPE + LOUPE_CHROME_PX }}>
      {windowBox}
      {/* The ROI line. Three things were wrong with it as one control:
          (1) it was 94x25 CSS px — well under the 44px floor, on the smallest
              text in the app (10px), in the dark, with gloves;
          (2) its PURPOSE ("this copies") and its blocked reason ("No ROI yet")
              both lived only in `aria-label`/`title`, neither of which a
              fingertip can reach — so a sighted touch user saw an unexplained
              row of numbers;
          (3) with no ROI it was still a live <button> that silently did
              nothing when pressed. A control that no-ops with no feedback is
              the defect; the honest form is the house LockedChip (dim +
              aria-disabled + a stated reason).
          Every state now says what it is in WORDS on screen. A tooltip is a
          second channel here, not the only one — the LockedChip's bubble opens
          on hover and focus, but a TAP inside PreviewStage does not reach it
          (the stage's `touch-action: pan-y` swallows the pointerup the Tooltip
          machine listens for), so no state may depend on the bubble alone. */}
      {roi ? (
        <button
          type="button"
          onClick={copy}
          aria-label={`Copy the sensor region ${roiText} to the clipboard`}
          title="Copy the sensor ROI to the clipboard"
          className="tap min-h-[44px] w-full flex flex-col justify-center gap-0.5
            text-left text-dim hover:text-ink"
        >
          <span
            className={`text-[10px] leading-tight uppercase tracking-wide ${
              outcome === "ok" ? "text-accent" : outcome === "fail" ? "text-warn" : ""
            }`}
          >
            {outcome === "ok" ? "✓ Copied" : outcome === "fail" ? "✕ Copy blocked" : "Copy region"}
          </span>
          <span className="mono text-[10px] tabular-nums leading-tight break-all">
            {roi.x},{roi.y} {roi.w}×{roi.h} @1:1 · #{previewId}
          </span>
        </button>
      ) : (
        <LockedChip
          reason={
            loading
              ? "Nothing to copy yet — still fetching the sensor crop for this frame."
              : "Nothing to copy — no sensor crop is available for this frame."
          }
          className="!px-1 w-full text-[10px] leading-tight"
        >
          <span>{loading ? "Fetching…" : "Nothing to copy"} · #{previewId}</span>
        </LockedChip>
      )}
      <span className="sr-only" aria-live="polite">
        {outcome === "ok"
          ? `Copied sensor region ${roiText}`
          : outcome === "fail"
            ? "This browser refused the clipboard. The region is printed above — copy it by hand."
            : ""}
      </span>
    </div>
  );
}

/** Pre-async-clipboard copy path. `document.execCommand("copy")` is deprecated
 *  but it is the ONLY clipboard route that works in a non-secure context, which
 *  is how AstroDeck is normally reached (plain http to the box at the scope).
 *  Returns whether the copy actually happened, so the caller can be honest
 *  either way. Guarded end-to-end: an exception must never take the loupe down
 *  over a convenience feature. */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    // Off-screen but still selectable; `display:none` would break execCommand.
    ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
