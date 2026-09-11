// degraded.ts - what FRAME mode says when the survey or the catalogue is not
// answering, and WHY it is not (review #30, #32).
//
// THE BANNER USED TO NAME ONE CAUSE FOR FOUR DIFFERENT FAILURES. `FrameHost`
// passed `degradedText={DEGRADED_NO_SOURCE}` unconditionally - "No survey source
// - download the offline sky pack in Settings" - so a transient CDS hiccup on a
// rig with online fetch ON sent the user to download a pack it does not need,
// while a pack that was 40% through downloading said nothing about the download.
// `AtlasView.tsx:233-255` only ever used that sentence when the pack was
// genuinely absent AND online fetch was off, and it polled the pack status every
// 2 s so the progress copy was live.
//
// SkyCanvas's own fallback ("No sky survey available - download the offline pack
// or enable online fetch") has the same defect, so this module answers for every
// case rather than returning undefined for some of them and inheriting it.
//
// The catalogue half is separate and unrelated in cause: `region.degraded` says
// the SERVER answered from the 64 curated objects because the bulk deep-sky file
// did not load. A sky with no labels and a sky whose labels did not load look
// identical, which is exactly why it gets a sentence.

import type { PackStatus } from "../../../../types";

/** Kept for the case it was always right about: no pack, no online fetch. */
export const DEGRADED_NO_SOURCE =
  "No survey source - download the offline sky pack in Settings, or turn on online fetch.";

export const DEGRADED_ONLINE =
  "Survey tiles are not coming through - the last image stays up while it retries. "
  + "Check the rig's connection, or download the offline sky pack in Settings to work offline.";

export const DEGRADED_PACK_PRESENT =
  "The offline sky pack is installed but these tiles did not load - this patch may be "
  + "outside the pack's coverage. Turn on online fetch in Settings to reach the rest.";

export const DEGRADED_PACK_UNKNOWN =
  "No survey source, and the sky pack has not reported its state - open Settings > Sky pack.";

/** "Downloading offline sky pack - 412/1024" - the progress copy and its
 *  numbers, which is the whole reason the caller polls. */
export function packProgressText(done: number, total: number): string {
  return `Downloading offline sky pack - ${done}/${total}`;
}

/**
 * The sentence for the state the rig is actually in.
 *
 * Never returns undefined: falling through to SkyCanvas's own default would put
 * the offline-pack sentence back on the online-fetch case, which is the defect.
 */
export function surveyDegradedText(
  onlineFetch: boolean,
  pack: PackStatus | null,
): string {
  if (onlineFetch) return DEGRADED_ONLINE;
  if (pack == null) return DEGRADED_PACK_UNKNOWN;
  if (pack.fetching) return packProgressText(pack.fetching.done, pack.fetching.total);
  if (pack.present) return DEGRADED_PACK_PRESENT;
  return DEGRADED_NO_SOURCE;
}

/** How often the pack status is re-read while the survey is degraded and there
 *  is no online source - `AtlasView.tsx:243` polls at exactly this cadence, and
 *  only in that state, because it is the only state whose copy moves. */
export const PACK_POLL_MS = 2000;

export function shouldPollPack(surveyDegraded: boolean, onlineFetch: boolean): boolean {
  return surveyDegraded && !onlineFetch;
}

// ------------------------------------------------------------- the catalogue

/** `ObjectCard.tsx:98-100`, verbatim apart from the house hyphen rule. */
export const CATALOG_DEGRADED =
  "The deep-sky pack didn't load, so only the 64 built-in objects can be marked. "
  + "Most of the sky will look empty until it is restored.";

/** `AtlasView.tsx:1400-1403`, same. */
export const CATALOG_DENSE =
  "More is catalogued here than can be marked at once - these are the brightest and "
  + "largest. Zoom in for the rest.";

export interface RegionNoteInput {
  degraded: boolean;
  truncated: boolean;
  error: string | null;
}

/**
 * The catalogue's own notes for this patch, in the order they matter.
 *
 * Three different absences with three different fixes, so they are three
 * sentences rather than one greyed marker layer: a broken catalogue (restore
 * the pack), a dense field (zoom in) and a failed request (it will come back).
 */
export function regionNotes(r: RegionNoteInput): string[] {
  const out: string[] = [];
  if (r.degraded) out.push(CATALOG_DEGRADED);
  if (r.truncated) out.push(CATALOG_DENSE);
  if (r.error) out.push(r.error);
  return out;
}
