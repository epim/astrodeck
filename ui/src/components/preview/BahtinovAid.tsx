// NOV-12 Bahtinov focus verdict render. Mirrors FocusVerdict's structure — a
// word + tone class inside role="status" aria-live="polite" (never color alone).
// All logic lives in the pure, tsx-tested lib/bahtinov.ts.
import type { PreviewInfo } from "../../types";
import { bahtinovAid, type BahtTone } from "../../lib/bahtinov";

const TONE: Record<BahtTone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  neutral: "text-dim",
};

export function BahtinovAid({ preview }: { preview: PreviewInfo | null }) {
  const b = preview?.bahtinov;
  const v = bahtinovAid(b);
  const offset =
    b?.valid && b.offset_px != null ? `${b.offset_px.toFixed(1)} px` : null;
  return (
    <div role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-2">
        <div className={`text-base font-semibold ${TONE[v.tone]}`}>{v.headline}</div>
        {offset && <div className={`mono text-sm ${TONE[v.tone]}`}>{offset}</div>}
      </div>
      <div className="text-xs text-dim mt-0.5">{v.detail}</div>
    </div>
  );
}
