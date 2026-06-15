// FocusVerdict.tsx — plain-language focus verdict line (decisions log #8, §5).
// (stream V)
//
// "Focus: GOOD — median HFR 2.3 px (3.1″) · lower is sharper ↓" with a trend arrow
// vs the previous frame, and a one-line saturation ACTION when the frame clips
// (every warning carries an action — §12.6). NEVER color alone: word + arrow + text.
import type { PreviewInfo } from "../../types";
import { Icon } from "../icons";

function verdictWord(hfr: number, good: number, warn: number): { word: string; tone: "good" | "warn" | "bad" } {
  if (hfr <= good) return { word: "GOOD", tone: "good" };
  if (hfr <= warn) return { word: "FAIR", tone: "warn" };
  return { word: "POOR", tone: "bad" };
}

export function FocusVerdict({
  preview,
  prev,
  hfrGood,
  hfrWarn,
}: {
  preview: PreviewInfo | null;
  prev: PreviewInfo | null;
  hfrGood: number;
  hfrWarn: number;
}) {
  if (!preview) {
    return (
      <div className="text-xs text-dim" aria-live="polite">
        Focus: <span className="text-faint">awaiting first frame</span>
      </div>
    );
  }
  const hfr = preview.hfr;
  const fewStars = (preview.stars ?? 0) < 3 || hfr == null;
  const fw = preview.full_well;
  const clipped = fw != null && preview.data_is_linear && preview.stats.max >= fw;

  if (fewStars) {
    return (
      <div className="text-xs flex flex-wrap items-center gap-x-2 gap-y-1" aria-live="polite">
        <span className="text-warn font-medium inline-flex items-center gap-1">
          <Icon name="alert" size={13} /> Few stars
        </span>
        <span className="text-dim">— check focus/clouds, or lengthen the exposure</span>
      </div>
    );
  }

  const v = verdictWord(hfr!, hfrGood, hfrWarn);
  const toneClass = v.tone === "good" ? "text-good" : v.tone === "warn" ? "text-warn" : "text-bad";
  const prevHfr = prev?.hfr ?? null;
  let trend: "up" | "down" | "flat" = "flat";
  if (prevHfr != null && hfr != null) {
    if (hfr < prevHfr - 0.05) trend = "down"; // sharper
    else if (hfr > prevHfr + 0.05) trend = "up"; // softer
  }
  const arcsec = preview.pixel_scale_arcsec != null ? ` (${(hfr! * preview.pixel_scale_arcsec).toFixed(1)}″)` : "";

  return (
    <div className="text-xs flex flex-wrap items-center gap-x-2 gap-y-1" aria-live="polite">
      <span className="text-dim">Focus:</span>
      <span className={`font-medium ${toneClass}`}>{v.word}</span>
      <span className="text-dim mono">
        median HFR {hfr!.toFixed(2)} px{arcsec}
      </span>
      <span className="text-dim inline-flex items-center gap-1">
        {trend === "down" && (
          <span className="text-good inline-flex items-center gap-0.5">
            <Icon name="arrow-down" size={12} /> sharper
          </span>
        )}
        {trend === "up" && (
          <span className="text-warn inline-flex items-center gap-0.5">
            <Icon name="arrow-up" size={12} /> softer
          </span>
        )}
        · lower is sharper
      </span>
      {clipped && (
        <span className="text-warn inline-flex items-center gap-1 basis-full sm:basis-auto">
          <Icon name="alert" size={12} /> Stars saturated — shorten exposure or lower gain
        </span>
      )}
    </div>
  );
}
