// Plain-language guiding narration + words verdict for novices (NOV-7 design
// doc, docs/superpowers/specs/2026-07-23-guiding-narration-design.md §1.5).
//
// A single pure function that turns the guider's current state into two
// human sentences: what the guider is doing right now (phaseText) and
// whether the guiding is good enough, with honest units (verdict). The
// GuideView render is a thin binding — see §1.6.

export type GuidePhase =
  | "idle" | "finding" | "calibrating" | "settling" | "guiding" | "lost";

export interface GuideNarrationInput {
  connected: boolean;    // !!status?.guider || !!guide  (GuideView.tsx:44)
  phase?: string;        // GuideStats.phase; "" / absent ⇒ unknown (fallback)
  guiding: boolean;      // GuideStats.guiding
  rmsTotal: number;      // GuideStats.rms_total
  isArcsec: boolean;     // caller passes stats?.is_arcsec !== false (GuideView.tsx:50)
  imageScale: number;    // GuideStats.image_scale ?? 0  (arcsec/px, 0 = unknown)
  hasSamples: boolean;   // (recent?.length ?? 0) > 0
}

export type NarrationTone = "good" | "warn" | "bad" | "neutral";

export interface GuideNarration {
  phaseText: string;         // "Guiding well — you can relax"
  tone: NarrationTone;       // colors both the phase line and the verdict
  verdict: string | null;    // "0.7 px ≈ 1.1″ — good for 3-minute subs" | null
}

const PRIME = "″"; // ″  (matches GuideView.tsx:51 UX-35)

// D1: display heuristic, not physics — a later product tweak is a one-line
// edit here. Tuned so the brief's 1.1″ example lands in the "3-minute subs"
// band.
const QUALITY_EXCELLENT_MAX_ARCSEC = 0.6;
const QUALITY_GOOD_MAX_ARCSEC = 1.2;
const QUALITY_WARN_MAX_ARCSEC = 2.0;

function quality(arc: number): { clause: string; tone: NarrationTone } {
  if (arc <= QUALITY_EXCELLENT_MAX_ARCSEC) {
    return { clause: "excellent — long subs are fine", tone: "good" };
  }
  if (arc <= QUALITY_GOOD_MAX_ARCSEC) {
    return { clause: "good for 3-minute subs", tone: "good" };
  }
  if (arc <= QUALITY_WARN_MAX_ARCSEC) {
    return { clause: "OK for short subs", tone: "warn" };
  }
  return { clause: "high — expect some star trailing", tone: "bad" };
}

export function guideNarration(i: GuideNarrationInput): GuideNarration {
  if (!i.connected) {
    return { phaseText: "No guider connected", tone: "neutral", verdict: null };
  }

  const p = i.phase;
  if (p === "lost") {
    return { phaseText: "Lost the guide star — trying to recover", tone: "bad", verdict: null };
  }
  if (p === "finding") {
    return { phaseText: "Finding a guide star…", tone: "neutral", verdict: null };
  }
  if (p === "calibrating") {
    return { phaseText: "Calibrating the guider…", tone: "neutral", verdict: null };
  }
  if (p === "settling") {
    return { phaseText: "Settling after the move…", tone: "warn", verdict: null };
  }

  const guiding = p === "guiding" || (!p && i.guiding);
  if (!guiding) {
    return { phaseText: "Ready to guide", tone: "neutral", verdict: null };
  }

  if (!i.hasSamples || i.rmsTotal <= 0) {
    return { phaseText: "Guiding — measuring…", tone: "neutral", verdict: null };
  }

  // verdict only when we can speak in arcsec honestly (§1.4) — the px case
  // defers to the existing UX-15 note.
  let verdict: string | null = null;
  let tone: NarrationTone = "neutral";
  let text = "Guiding well — you can relax";
  if (i.isArcsec) {
    const arc = i.rmsTotal;
    const q = quality(arc);
    tone = q.tone;
    const px = i.imageScale > 0 ? arc / i.imageScale : null;
    verdict =
      (px != null
        ? `${px.toFixed(1)} px ≈ ${arc.toFixed(1)}${PRIME}`
        : `${arc.toFixed(1)}${PRIME}`) +
      ` — ${q.clause}`;
    text =
      tone === "good" ? "Guiding well — you can relax"
      : tone === "warn" ? "Guiding — still settling down"
      : "Guiding, but the error is high";
  }
  return { phaseText: text, tone, verdict };
}
