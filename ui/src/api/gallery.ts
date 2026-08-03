// api/gallery.ts — typed wrappers for the ten /api/gallery/* routes (gallery
// design 2026-08-03). Cookie auth is automatic; a non-2xx throws ApiError.
//
// The two BYTE-CARRYING routes are deliberately absent from this file:
// `/api/gallery/file` and `/api/gallery/download.zip` are reached as plain
// navigations (an <a href>), never through `api.get`. The session is a cookie,
// so a navigation authenticates exactly as a fetch does — and fetching a bulk
// download would land the whole archive in a Blob in memory, undoing the
// streaming the server was built to do. lib/gallery.ts builds those two URLs;
// this module only exposes the JSON.

import { api } from "../api";
import {
  framesPath,
  nightsPath,
  summaryPath,
  type GallerySelection,
} from "../lib/gallery";
import type {
  GalleryFramesPage,
  GalleryNightsResponse,
  GalleryPurgeResult,
  GalleryRestoreResult,
  GallerySummary,
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

/** What a download of this exact selection would be. Takes the same parameters
 *  as download.zip so the number on the button and the bytes on the wire come
 *  from one resolver and cannot drift apart. */
export const gallerySummary = (sel: GallerySelection): Promise<GallerySummary> =>
  api.get<GallerySummary>(summaryPath(sel));

/** Move frames to the trash (a rename inside CAPTURE_DIR — atomic, instant even
 *  for a 200 GB night). Paths are LIBRARY-relative. A POST body, not a query, so
 *  this one has no URL-length ceiling. */
export const trashFrames = (paths: string[]): Promise<GalleryTrashResult> =>
  api.post<GalleryTrashResult>("/api/gallery/trash", { paths });

export const listTrash = (): Promise<GalleryTrashListing> =>
  api.get<GalleryTrashListing>("/api/gallery/trash");

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
