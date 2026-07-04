// FocusVerdict.tsx — plain-language focus verdict line (decisions log #8, §5).
// (stream V)
//
// "Focus: GOOD — median HFR 2.3 px (3.1″) · lower is sharper ↓" with a trend arrow
// vs the previous frame, and a one-line saturation ACTION when the frame clips
// (every warning carries an action — §12.6). NEVER color alone: word + arrow + text.
import type { PreviewInfo } from "../../types";
import { Icon } from "../icons";

// ============================================================ AUTOFOCUS VERDICT
// The autofocus-RESULT verdict (implementation brief §3 / design-reference §02):
// "Focus — excellent · HFR 1.82 px · 2.4″ · R² 0.997 · hyperbolic" — verdict word
// first, raw numbers second (brief §0.1). Distinct from FocusVerdict above, which
// judges the CURRENT live frame; this judges the completed sweep from the engine's
// best HFR + fit R² + state. Pure mapping is exported for unit testing.
export type AfLevel = "excellent" | "good" | "soft" | "failed" | "pending";

export function autofocusLevel(args: {
  state?: string;
  hfr?: number | null;
  r2?: number | null;
  hfrGood: number;
  hfrWarn: number;
}): AfLevel {
  const { state, hfr, r2, hfrGood, hfrWarn } = args;
  if (state === "failed") return "failed";
  if (state !== "done" || hfr == null) return "pending";
  // excellent needs a tight HFR AND a confident fit (R²≥0.98); when the provider
  // emits no R² (e.g. a backend/NINA result), judge on HFR alone.
  if (hfr <= hfrGood && (r2 == null || r2 >= 0.98)) return "excellent";
  if (hfr <= hfrWarn) return "good";
  return "soft";
}

const AF_CHIP: Record<AfLevel, { word: string; text: string; border: string; bg: string }> = {
  excellent: { word: "excellent", text: "text-accent", border: "border-accent2", bg: "bg-accent-fill" },
  good: { word: "good", text: "text-good", border: "border-good", bg: "bg-raise" },
  soft: { word: "soft", text: "text-warn", border: "border-warn", bg: "bg-raise" },
  failed: { word: "failed", text: "text-bad", border: "border-bad", bg: "bg-raise" },
  pending: { word: "—", text: "text-dim", border: "border-line2", bg: "bg-raise" },
};

export function AutofocusVerdict({
  state, hfr, r2, method, pixelScaleArcsec, hfrGood, hfrWarn, message,
}: {
  state?: string;
  hfr?: number | null;
  r2?: number | null;
  method?: string | null;
  pixelScaleArcsec?: number | null;
  hfrGood: number;
  hfrWarn: number;
  message?: string | null;
}) {
  const level = autofocusLevel({ state, hfr, r2, hfrGood, hfrWarn });
  const chip = AF_CHIP[level];
  const word = level === "pending" && state === "running" ? "measuring…" : chip.word;

  let detail = "";
  if (level === "failed") {
    detail = message || "no V-curve minimum — check stars in frame";
  } else if (level === "pending") {
    detail = state === "running" ? "sweeping focus" : "awaiting result";
  } else if (hfr != null) {
    const arcsec = pixelScaleArcsec != null ? ` · ${(hfr * pixelScaleArcsec).toFixed(2)}″` : "";
    const r2s = r2 != null ? ` · R² ${r2.toFixed(3)}` : "";
    const m = method ? ` · ${method}` : "";
    detail = `HFR ${hfr.toFixed(2)} px${arcsec}${r2s}${m}`;
  }

  return (
    <div
      className={`inline-flex items-baseline gap-2.5 rounded-md border px-3 py-2 ${chip.border} ${chip.bg}`}
      role="status"
      aria-live="polite"
    >
      <b className={`text-[0.8rem] font-semibold uppercase tracking-wide ${chip.text}`}>Focus — {word}</b>
      {detail && <span className="mono text-xs text-dim">{detail}</span>}
    </div>
  );
}

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
