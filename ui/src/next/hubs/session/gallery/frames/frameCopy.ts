// frameCopy.ts - the words this area says, and the one place the legacy copy
// crosses into the new UI.
//
// PURE. No React, no store, no fetch - so the sentences that decide whether
// someone downloads a 26 MP FITS or bins a night's work can be asserted without
// a DOM.
//
// TWO JOBS.
//
// 1. THE EM-DASH BOUNDARY. `lib/gallery.ts` is shared with `#/classic` and
//    still writes em-dashes ("Not on disk any more - deleted or moved...",
//    "1,284 frames, 38.2 GB - ...", and the bare "-" `fmtBytes` returns for a
//    missing number). ARCHITECTURE non-negotiable 5 says hyphens in the new UI,
//    and R7 does not edit legacy modules, so the dash is normalised HERE, at
//    the one boundary every one of those strings crosses. `hyphenate` itself is
//    imported from the report area rather than written twice: it is a pure
//    module, and two copies of a punctuation rule is how the two screens drift.
//
// 2. THE FITS RULE, IN ONE SENTENCE. Raw FITS are `view.media`, which per
//    `lib/caps.ts`'s ROLE_CAPS is held by SYNCER and ADMIN - not by the
//    operator who ran the night. Every place this area mentions it derives the
//    phrase from `accessPhrase("view.media")`, so the note beside a locked
//    download names the same policy the server enforces. Naming only "admin"
//    would be the exact defect `accessPhrase` exists to prevent (ARCHITECTURE
//    section 8's amended Files-sheet rule).

import { accessPhrase } from "../../../../../lib/caps";
import {
  fmtBytes, fmtCount, fmtFrameCost, tileFailureCopy, type ThumbFailure,
} from "../../../../../lib/gallery";
import { hyphenate } from "../../report/reportModel";

/** The em-dash boundary for this area.
 *
 *  A BARE dash is the "no value" glyph (`fmtBytes(null)`), not a clause break,
 *  so it becomes a single hyphen rather than the spaced " - " a sentence needs.
 *  Everything else goes through `hyphenate` unchanged. */
export function hy(s: string): string {
  if (/^\s*[—–]\s*$/.test(s)) return "-";
  return hyphenate(s);
}

/** `fmtBytes`, hyphenated. "-" when the server sent no number. */
export function bytesLabel(n: number | null | undefined): string {
  return hy(fmtBytes(n));
}

/** `fmtCount`, hyphenated. */
export function countLabel(n: number | null | undefined): string {
  return hy(fmtCount(n));
}

/** "1,284 frames, 38.2 GB", hyphenated. */
export function costLabel(count: number, bytes: number): string {
  return hy(fmtFrameCost(count, bytes));
}

/** A nullable legacy sentence, hyphenated. */
export function lineOrNull(s: string | null | undefined): string | null {
  return s == null ? null : hy(s);
}

// ------------------------------------------------------------ the FITS rule

/** The capability that guards the raw files, named once. */
export const MEDIA_PHRASE = accessPhrase("view.media");

/** The note the frame library prints when the signed-in role cannot have the
 *  originals. It states the rule AND what still works, because the JPEG path is
 *  unrestricted and a bare refusal would read as "the gallery is broken". */
export const FITS_NOTE =
  `Raw FITS downloads need ${MEDIA_PHRASE} - every frame embeds the observatory's `
  + "coordinates. Thumbnails and the full-size preview are not restricted.";

/** The lock reason on a download control. Shorter than `FITS_NOTE`: it is read
 *  in a tooltip and a toast, where the second clause is noise. */
export const FITS_LOCK =
  `Downloading raw FITS needs ${MEDIA_PHRASE} - the files embed the observatory's coordinates.`;

/** The same fact as `FITS_LOCK`, shaped for `LockNote`, which prefixes
 *  "Read-only - ". One fact, two grammars, never two different policies. */
export const FITS_LOCK_NOTE =
  `downloading raw FITS needs ${MEDIA_PHRASE} - the files embed the observatory's coordinates`;

/** Why the bin cannot be read or emptied by this role. The whole trash surface
 *  is `control.capture` on the server (`app.py` GET and POST
 *  `/api/gallery/trash`), so this is one sentence, not three. */
export const TRASH_LOCK =
  `Reading and restoring the bin needs ${accessPhrase("control.capture")}.`;

/** Why a single frame cannot be binned from the viewer. */
export const DELETE_LOCK =
  `Deleting needs ${accessPhrase("control.capture")}.`;

/** Why the live stack cannot be switched on or reset by this role. */
export const STACK_LOCK =
  `Switching the stack on needs ${accessPhrase("control.capture")}.`;

/** `STACK_LOCK` shaped for `LockNote`. */
export const STACK_LOCK_NOTE =
  `switching the stack on or resetting it needs ${accessPhrase("control.capture")}`;

// ------------------------------------------------------------------- tiles

/** The words on a failed tile, hyphenated. `downloadable` decides whether the
 *  tile still offers the FITS: a frame we cannot RENDER is not a frame that is
 *  MISSING, and only the missing one loses its download. */
export function tileCopy(
  failure: ThumbFailure, status?: number,
): { label: string; hint: string; downloadable: boolean } {
  const c = tileFailureCopy(failure, status);
  return { label: c.label, hint: hy(c.hint), downloadable: c.downloadable };
}

/** Everything a mouse user gets from hovering, for everyone else too: the full
 *  relative path (the grid truncates it), the night that decided the filing,
 *  the wall clock and the size.
 *
 *  The clock is the SERVER's `local_clock`, never `new Date(frame.ts)`: the
 *  night beside it was computed in the rig's timezone and a browser on the
 *  relay is in its own. Two clocks in one sentence is a sentence that lies to
 *  everyone who is not sitting next to the mount. */
export function tileTitle(
  path: string, night: string, clock: string, bytes: number, rollover: string | null,
): string {
  return `${path}\n${night} - captured ${clock} rig time - ${bytesLabel(bytes)}`
    + (rollover ? `\n${hy(rollover)}` : "");
}

// ------------------------------------------------------------------ viewer

/** The viewer's live line: which render is on screen and whether a sharper one
 *  is still coming. "sizing" is a real state - the box has not been measured
 *  yet - and saying so beats an empty header for the frame it lasts. */
export function viewerLive(width: number | null, loading: boolean, failed: boolean): string {
  if (failed) return "cached preview - the full render was refused";
  if (width == null) return "sizing to this screen";
  return loading ? `${width}px render loading` : `${width}px render`;
}

/** Why the full render did not arrive, and what the user still has. */
export const VIEWER_FAIL_HINT =
  "The picture above is the cached preview. The FITS on disk is untouched and still downloadable.";

// ------------------------------------------------------------------- trash

/** The bin's standing explanation. The disk sentence is the one people miss:
 *  deleting to make room tonight does nothing until the bin is emptied. */
export function trashIntro(ttlDays: number): string {
  return `Deleted frames move here and purge automatically after ${ttlDays} days. `
    + "The disk space is not freed until they purge, so if you deleted them to make "
    + "room tonight, empty the trash too.";
}

/** The per-row line: where it goes back to, when it dies, how big it is. */
export function trashRowSub(original: string, deleted: string, purges: string, bytes: number): string {
  // The ORIGINAL path, not the trash path: "where does it go back to" is the
  // question a restore raises, and the trash path is bookkeeping the user never
  // chose (it can even carry a -1 collision suffix).
  return `from ${original} - deleted ${deleted} - ${purges} - ${bytesLabel(bytes)}`;
}

/** The one row state that changes what an action will do. Words, not a hue:
 *  `:root.night` collapses warn and bad onto the same coral. */
export const NOT_RESTORABLE = "cannot restore - a file already sits at that path";
