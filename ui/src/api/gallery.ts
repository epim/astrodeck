// api/gallery.ts — typed wrappers for the /api/gallery/* routes this UI calls
// (gallery design 2026-08-03). Cookie auth is automatic; a non-2xx throws
// ApiError.
//
// The two BYTE-CARRYING ones — `/api/gallery/file` and
// `/api/gallery/download.zip` — are reached as plain navigations (an <a href>),
// never through `api.get`. The session is a cookie, so a navigation
// authenticates exactly as a fetch does, and fetching a bulk download would land
// the whole archive in a Blob in memory, undoing the streaming the server was
// built to do. lib/gallery.ts builds those two URLs; this module only exposes
// the JSON.
//
// Summary is used only to validate a frozen selection before downloading.

import { api } from "../api";
import { framesPath, nightsPath, selectionQuery, type GallerySelection } from "../lib/gallery";
import type {
  GalleryFramesPage,
  GalleryNightsResponse,
  GalleryPurgeResult,
  GalleryRestoreResult,
  GalleryTrashListing,
  GalleryTrashResult,
} from "../types";

export interface FramesQuery {
  q?: string;
  /** INCLUSIVE noon-to-noon night keys ("YYYY-MM-DD"), not calendar dates. A
   *  malformed bound is a 422 from the server, refused rather than ignored:
   *  silently dropping a bad bound returns the whole library and looks like a
   *  filter that worked. */
  nightFrom?: string;
  nightTo?: string;
  offset?: number;
  limit?: number;
  cursor?: string;
}

/** One page of the library, newest capture first. `total`/`bytes` on the
 *  response describe the WHOLE filtered set, not the page — which is why the
 *  grid can price a download without a second request. */
export const listFrames = (o: FramesQuery = {}): Promise<GalleryFramesPage> =>
  api.get<GalleryFramesPage>(framesPath(o));

/** Which nights actually exist (+ frames/bytes each), so the date filter offers
 *  real nights instead of a calendar where most dates return nothing. */
export const listNights = (): Promise<GalleryNightsResponse> =>
  api.get<GalleryNightsResponse>(nightsPath());

// Listing totals price the actions. On download, validateSelection checks that
// the snapshot still exists and its selected files have not changed. It does
// not rebuild the listing or include newly captured files.

/** Move frames to the trash (a rename inside CAPTURE_DIR — atomic, instant even
 *  for a 200 GB night). Paths are LIBRARY-relative. A POST body, not a query, so
 *  this one has no URL-length ceiling. */
export const trashFrames = (paths: string[], listing?: { snapshot?: string; q: string; nightFrom: string; nightTo: string }): Promise<GalleryTrashResult> =>
  api.post<GalleryTrashResult>("/api/gallery/trash", { paths, snapshot: listing?.snapshot,
    q: listing?.q, night_from: listing?.nightFrom, night_to: listing?.nightTo });

/** Validate a frozen selection before native navigation starts the download. */
export const validateSelection = (selection: GallerySelection): Promise<unknown> =>
  api.get(`/api/gallery/summary${selectionQuery(selection)}`);

export const listTrash = (): Promise<GalleryTrashListing> =>
  api.get<GalleryTrashListing>("/api/gallery/trash");

/** Just the number on the tab. The gallery asks for this on every open, and
 *  the full listing was 40 KB of rows over the relay to render one integer -
 *  the second-largest payload of a page load. `items` comes back empty; the
 *  panel fetches the real listing when it is opened. */
export const getTrashCount = (): Promise<GalleryTrashListing> =>
  api.get<GalleryTrashListing>("/api/gallery/trash?count_only=1")
    .then((t) => ({ ...t, items: t.items ?? [] }));

/** Paths here are TRASH-relative (what the listing returns), NOT library
 *  paths — a collision on delete suffixes the trash name, so the two differ. */
export const restoreTrashed = (paths: string[]): Promise<GalleryRestoreResult> =>
  api.post<GalleryRestoreResult>("/api/gallery/trash/restore", { paths });

/** Irreversible. `{all: true}` empties the bin; an empty `paths` is a 422 rather
 *  than an accidental empty-the-trash. */
export const purgeTrashed = (paths: string[]): Promise<GalleryPurgeResult> =>
  api.post<GalleryPurgeResult>("/api/gallery/trash/purge", { paths });

export const purgeAllTrashed = (): Promise<GalleryPurgeResult> =>
  api.post<GalleryPurgeResult>("/api/gallery/trash/purge", { all: true });

/** What a thumbnail backfill did. `truncated` means the library was longer than
 *  one scan — run it again to continue, because a partial pass that reported
 *  itself complete would leave frames permanently cold. */
export type ThumbBackfillResult = {
  frames: number;
  rendered: number;
  unrenderable: number;
  truncated?: boolean;
};

/** Render every missing gallery thumbnail up front.
 *
 *  Captures warm their own thumbnails from 0.2.72 on, so this is for the frames
 *  shot before that, and for any gap left by a restart mid-night. Idempotent
 *  and safe to repeat; slow the first time on a large library (a cold render is
 *  a ~1.5 s stretch over 26 megapixels). */
export const backfillThumbs = (): Promise<ThumbBackfillResult> =>
  api.post<ThumbBackfillResult>("/api/gallery/thumbs/backfill", {});
