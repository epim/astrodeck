// lib/bahtinov.ts — pure verdict mapping for the NOV-12 Bahtinov focus aid.
// No React/DOM: npx-tsx testable (eta.test.ts precedent). Mirrors the
// plainFocusVerdict {tone,headline,detail} shape in autofocus.ts.
import type { BahtinovInfo } from "../types";

export type BahtTone = "good" | "warn" | "bad" | "neutral";
export interface BahtVerdict {
  tone: BahtTone;
  headline: string;
  detail: string;
}

export function bahtinovAid(info: BahtinovInfo | null | undefined): BahtVerdict {
  if (!info)
    return { tone: "neutral", headline: "Bahtinov focus", detail: "Waiting for a frame…" };
  if (!info.valid)
    return { tone: "neutral", headline: "Line up the star", detail: info.reason };
  if (info.in_focus)
    return {
      tone: "good",
      headline: "PERFECT — locked",
      detail: "The middle spike is centered — you're focused.",
    };
  const off = info.offset_px != null ? `${Math.abs(info.offset_px).toFixed(1)} px` : "";
  const turn = info.direction ? `turn ${info.direction.toUpperCase()} a little` : "nudge the focuser";
  const tone: BahtTone =
    info.offset_px != null && Math.abs(info.offset_px) > info.tol_px * 4 ? "bad" : "warn";
  return {
    tone,
    headline: `Not yet — ${turn}`,
    detail: `Middle spike ${off} ${info.side ?? ""} of the crossing.`,
  };
}
