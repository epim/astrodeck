// difficulty.ts — pure display helpers for the beginner difficulty tag (NOV-3).
// The tier is DERIVED SERVER-SIDE (catalog/difficulty.py); this module only turns
// it into a label / glyph / tone / hint. Glyph is the primary channel, tone the
// secondary one (the night palette collapses good/warn/bad toward coral — design
// spec §8), mirroring lib/visibility.ts's moonSepGlyph/moonSepTone.

import type { DifficultyTier } from "../types";
export type { DifficultyTier };

/** Tiers a first-timer should reach for by default (the picker's beginner filter). */
export const BEGINNER_TIERS: DifficultyTier[] = ["easy", "moderate"];

export function difficultyLabel(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "Easy";
    case "moderate": return "Moderate";
    case "hard": return "Hard";
  }
}

/** Filled -> open circle as difficulty rises; the shape carries the meaning in
 *  night mode where tone color does not survive. */
export function difficultyGlyph(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "●";       // ● filled
    case "moderate": return "◐";   // ◐ half
    case "hard": return "○";       // ○ open
  }
}

export function difficultyTone(t: DifficultyTier): "good" | "warn" | "bad" {
  switch (t) {
    case "easy": return "good";
    case "moderate": return "warn";
    case "hard": return "bad";
  }
}

export function difficultyHint(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "Bright and well-sized - a great first target.";
    case "moderate": return "Doable, but dimmer or smaller - expect more subs.";
    case "hard": return "Faint or low surface brightness - for experienced rigs.";
  }
}

export function isBeginnerFriendly(t: DifficultyTier): boolean {
  return BEGINNER_TIERS.includes(t);
}

/** Resolve the difficulty tier of the CURRENTLY-IMAGED target (polish grab-bag
 *  (c2)): match the running sequence's target string against catalog entries the
 *  client already holds (e.g. the framing session's origin object), by id or name,
 *  case- and whitespace-insensitively.
 *
 *  Returns `null` whenever it cannot be sure — no target, no entries, no match, or
 *  a matched entry that carries no server-derived tier. The caller then renders
 *  NOTHING: a difficulty badge on the wrong object would be worse than no badge. */
export function tierForTargetName(
  target: string | null | undefined,
  entries: readonly { id?: string; name?: string; difficulty?: DifficultyTier }[],
): DifficultyTier | null {
  const key = (s: string | null | undefined) =>
    (s ?? "").toLowerCase().replace(/\s+/g, "");
  const k = key(target);
  if (!k) return null;
  for (const e of entries) {
    if (!e) continue;
    if ((key(e.id) && key(e.id) === k) || (key(e.name) && key(e.name) === k)) {
      return e.difficulty ?? null;
    }
  }
  return null;
}
