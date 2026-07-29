// Pure formatting for the Live View readout (NOV-1). No React, tsx-tested.
import type { LiveStackInfo } from "../types";

export function integratedLabel(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1).replace(/\.0$/, "")} h`;
}

export function formatLiveStack(ls: LiveStackInfo): {
  frames: string; integrated: string; rejected: string; headline: string;
} {
  const n = Math.max(0, Math.floor(ls.frames));
  const frames = `${n} ${n === 1 ? "frame" : "frames"}`;
  const integrated = integratedLabel(ls.integrated_s);
  const r = Math.max(0, Math.floor(ls.rejected));
  const rejected = r > 0 ? `${r} skipped` : "";
  return { frames, integrated, rejected, headline: `${frames} · ${integrated} integrated` };
}

export type AlignTone = "good" | "warn" | "bad";

/** How the last sub was aligned, or null when there is nothing worth saying.
 *
 * Null for a clean match is deliberate: a badge that permanently reads "matched"
 * is chrome, and the frame count beside it already proves the stack is running.
 * This speaks only when the alignment is not what the user would assume.
 *
 * It has to speak at all because a stack aligned on ONE star looks identical on
 * screen to a well-registered one until it slowly smears — and by the time the
 * smear is visible, the session is over.
 */
export function alignmentState(
  ls: LiveStackInfo | null | undefined,
): { label: string; detail: string; tone: AlignTone } | null {
  if (!ls || ls.frames === 0) return null;
  switch (ls.reason) {
    case "weak_align":
      return {
        label: "weak align",
        detail:
          "Aligned on a single star — no matching star pattern was found. It " +
          "holds, but if that star saturates or clouds over, the stack shifts. " +
          "More stars in frame, or a longer sub, fixes it.",
        tone: "warn",
      };
    case "reseed":
    case "size":
      return {
        label: "restarted",
        detail:
          ls.reason === "size"
            ? "The frame size changed, so the stack started again from this sub."
            : "The field moved too far to be the same framing, so the stack " +
              "started again from this sub. The earlier subs were discarded.",
        tone: "warn",
      };
    case "drift":
      return {
        label: "drifting",
        detail:
          "This sub was too far off to stack. If it keeps happening, check tracking.",
        tone: "bad",
      };
    case "no_match":
      return {
        label: "no match",
        detail:
          "The stars in this sub did not match the stack — usually cloud, or " +
          "the scope was moved.",
        tone: "bad",
      };
    case "no_stars":
      return {
        label: "no stars",
        detail:
          "Nothing to align on: cloud, a closed cover, or badly out of focus.",
        tone: "bad",
      };
    default:
      return null;   // a clean constellation match needs no announcement
  }
}
