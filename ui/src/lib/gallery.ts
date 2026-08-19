// lib/gallery.ts — the pure half of the image gallery (gallery design
// 2026-08-03). Query building, size/count formatting, selection arithmetic and
// every sentence the view speaks about a destructive action live here, store-
// free and React-free, so they can be tested without a browser.
//
// The view imports these; nothing here imports the view, the store, or
// lib/base.ts. base.ts reads `window.location` AT MODULE LOAD, so importing it
// would make this module un-loadable under `tsx` in Node and take the tests
// with it — every URL here is therefore an app-absolute path + query, and the
// caller prefixes `BASE` (the same split ReportView uses for its bundle.zip
// link).

import type {
  GalleryFrame,
  GalleryNight,
} from "../types";

// ============================================================================
// SIZES AND COUNTS
// ============================================================================

/** Binary-prefix size with one decimal from ~1 GB up: 41017345024 -> "38.2 GB".
 *
 *  1024, not 1000, and labelled "GB" rather than "GiB" — deliberately. This
 *  number's whole job is to let someone decide whether to start a download, and
 *  the thing they will compare it against is their own file manager's free-space
 *  figure, which is 1024-based on Windows and macOS alike. A decimal "41.0 GB"
 *  beside a disk that reports 38.2 is a number that reads as wrong even though
 *  it is right. The design's own worked example (41017345024 bytes -> "38.2 GB")
 *  is 1024-based for the same reason, and there is a test pinning that pair.
 *
 *  Bytes and kB stay whole: "512 bytes" and "3.7 bytes" are not equally useful. */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${Math.round(n)} bytes`;
  const units = ["kB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  // One decimal from MB up, none for kB. Nobody sizing a download reads the
  // second decimal, and a whole-number kB keeps a per-frame size ("513 kB")
  // from looking more precise than the filesystem it came from.
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Thousands-grouped integer: 1284 -> "1,284".
 *
 *  Hand-grouped rather than `toLocaleString()` because the grouping character
 *  would then follow the browser's locale while every other number in this app
 *  is rendered with a plain comma — and because a test that asserts a locale's
 *  output passes or fails depending on which machine runs it. */
export function fmtCount(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const neg = n < 0;
  const digits = String(Math.abs(Math.round(n)));
  let out = "";
  for (let i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ",";
    out += digits[i];
  }
  return neg ? `-${out}` : out;
}

/** "1,284 frames, 38.2 GB" — the one phrase that turns a bulk action from a
 *  surprise into a decision. Singular when it is one frame, because "1 frames"
 *  is the tell of a number nobody looked at. */
export function fmtFrameCost(count: number, bytes: number): string {
  const noun = count === 1 ? "frame" : "frames";
  return `${fmtCount(count)} ${noun}, ${fmtBytes(bytes)}`;
}

// ============================================================================
// SELECTION
// ============================================================================

/**
 * What a bulk action applies to. Two shapes, because the server accepts two and
 * the difference is not cosmetic:
 *
 *   "filter" — whatever the current search + night range returns, named by the
 *     FILTER ITSELF. The set can be 50 000 frames and the URL stays 60
 *     characters, which is the only way "select all" can work at library scale.
 *   "picked" — an explicit list of relative paths the user ticked. Exact, but
 *     every path rides in the URL, so it is bounded (see PICKED_URL_BUDGET).
 *
 * The server's `path` parameter DEFINES the set and makes it ignore the filter,
 * so these two are genuinely exclusive — never send both and hope.
 */
export type GallerySelection =
  | { mode: "filter"; q: string; nightFrom: string; nightTo: string }
  | { mode: "picked"; paths: string[] };

/** Query string (leading "?", or "" when empty) for the frames LISTING. */
export function framesQuery(opts: {
  q?: string; nightFrom?: string; nightTo?: string;
  offset?: number; limit?: number;
}): string {
  const p = new URLSearchParams();
  if (opts.q) p.set("q", opts.q);
  if (opts.nightFrom) p.set("night_from", opts.nightFrom);
  if (opts.nightTo) p.set("night_to", opts.nightTo);
  if (opts.offset) p.set("offset", String(opts.offset));
  if (opts.limit) p.set("limit", String(opts.limit));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/**
 * Query string for the routes that take a SELECTION — summary and download.zip.
 * Both take the identical parameter set on purpose: hand them the same string
 * and the size on the button cannot disagree with the bytes on the wire.
 *
 * URLSearchParams encodes a space as "+", which Starlette's `parse_qsl` decodes
 * back to a space — which matters here and not elsewhere, because the real
 * capture tree has target folders with spaces in their names.
 */
export function selectionQuery(sel: GallerySelection): string {
  const p = new URLSearchParams();
  if (sel.mode === "picked") {
    for (const path of sel.paths) p.append("path", path);
  } else {
    if (sel.q) p.set("q", sel.q);
    if (sel.nightFrom) p.set("night_from", sel.nightFrom);
    if (sel.nightTo) p.set("night_to", sel.nightTo);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** App-absolute paths. Prefix with BASE at the call site (see the header). */
export const framesPath = (o: Parameters<typeof framesQuery>[0]): string =>
  `/api/gallery/frames${framesQuery(o)}`;
export const nightsPath = (): string => "/api/gallery/nights";
// No `summaryPath`: /api/gallery/summary exists for callers that have no
// listing (a script pricing a stream), but this UI always has one — the frames
// response carries `total`/`bytes` for the WHOLE filtered set from the same
// server-side resolver download.zip uses, so asking for them again would be a
// second library walk for two numbers already on screen.
export const downloadPath = (sel: GallerySelection): string =>
  `/api/gallery/download.zip${selectionQuery(sel)}`;
/**
 * `v` is the frame's mtime and exists only to bust the BROWSER cache.
 *
 * The server keys its own thumbnail cache on path+mtime+width, so re-capturing
 * to the same path yields a different image — but the response also carries
 * `max-age=3600`, so without a changing URL the browser would keep showing the
 * old thumbnail for an hour and the server-side correctness would never reach
 * the screen. FastAPI ignores query parameters a route does not declare, so this
 * costs the server nothing.
 */
export const thumbPath = (path: string, width = 256, v?: number): string =>
  `/api/gallery/thumb?path=${encodeURIComponent(path)}&w=${width}` +
  (v != null && Number.isFinite(v) ? `&v=${Math.round(v)}` : "");
export const filePath = (path: string): string =>
  `/api/gallery/file?path=${encodeURIComponent(path)}`;

/**
 * How long a gallery URL may get. 6000 characters is the conservative floor
 * across the stack this actually traverses: Apache's LimitRequestLine default is
 * 8190, nginx's large_client_header_buffers line is 8k, and the relay tunnel in
 * front of a remote rig is a third party with limits nobody here controls. A
 * request that dies at the proxy produces a network error with no server-side
 * trace, which is the worst failure to debug from a dark field.
 */
export const PICKED_URL_BUDGET = 6000;

/**
 * Can this selection be downloaded as one link, and if not, why not — in words
 * the user can act on.
 *
 * A bulk download MUST be a plain navigation (the session is a cookie, so a
 * navigation authenticates, and fetching a 38 GB zip into a Blob would put the
 * whole thing back in memory — the exact failure the streamed backend exists to
 * avoid). A navigation carries its selection in the URL, so a hand-picked set
 * has a hard ceiling that "select all in filter" does not: the filter form names
 * the same frames in a handful of characters.
 *
 * This never silently truncates. Sending the first N of a ticked selection would
 * produce a zip that looks complete and is not.
 */
export function downloadPlan(
  sel: GallerySelection,
  count: number,
): { ok: true; href: string } | { ok: false; reason: string } {
  if (count <= 0) {
    return { ok: false, reason: "Nothing is selected — tick some frames, or use Select all." };
  }
  const href = downloadPath(sel);
  if (href.length > PICKED_URL_BUDGET) {
    return {
      ok: false,
      reason:
        `${fmtCount(count)} hand-picked frames need a ${fmtCount(href.length)}-character ` +
        `link, and proxies refuse links past about ${fmtCount(PICKED_URL_BUDGET)}. ` +
        `Use "Select all in filter" — that sends the filter itself, not the list, ` +
        `at any size — or download these in smaller batches.`,
    };
  }
  return { ok: true, href };
}

/**
 * The most frames one Move-to-trash will submit.
 *
 * Unlike the download, delete has no URL ceiling (it is a POST body) — the limit
 * here is the SERVER side of it: the route renames every path in one worker
 * thread inside one request, so an unbounded selection turns into a request that
 * appears hung and cannot be cancelled, on the one action where a user most
 * wants to know whether it happened. 2000 renames is well under a second on a
 * spinning disk and keeps the response synchronous and truthful.
 */
export const TRASH_BATCH_CAP = 2000;

export function trashBatchReason(count: number): string | null {
  if (count <= TRASH_BATCH_CAP) return null;
  return `Deleting ${fmtCount(count)} frames at once isn't offered — the limit is ` +
    `${fmtCount(TRASH_BATCH_CAP)} per action, so the request stays fast enough to ` +
    "tell you it worked. Narrow the night range and repeat.";
}

/** Add/remove one path from a ticked set, returning a NEW set (the store-free
 *  half of the grid's selection state). */
export function togglePath(picked: ReadonlySet<string>, path: string): Set<string> {
  const next = new Set(picked);
  if (!next.delete(path)) next.add(path);
  return next;
}

/** Tick everything from ``anchor`` to ``path`` inclusive, in RENDERED order.
 *
 *  The gallery's whole purpose after a bad night is culling, and culling one
 *  checkbox at a time is what stops people doing it. A run that went wrong went
 *  wrong CONTIGUOUSLY - the cloud rolled in at frame 40 and never left - so the
 *  unit the operator actually wants to name is a range.
 *
 *  ADDITIVE, and that is the difference from a file manager. Windows Explorer
 *  REPLACES the selection on shift+click; here the flow is "sweep the bad run,
 *  then untick the two that came out fine", so a range must never clear ticks
 *  made outside it. Unticking afterwards is plain `togglePath`.
 *
 *  ``order`` is the rendered row order, not insertion order: "everything in
 *  between" means between on SCREEN, and the grid is filtered and sorted. Either
 *  endpoint missing from ``order`` (a row that scrolled out of a re-filtered
 *  page) leaves the set untouched rather than guessing at a span. */
export function selectRange(
  picked: ReadonlySet<string>,
  order: readonly string[],
  anchor: string,
  path: string,
): Set<string> {
  const a = order.indexOf(anchor);
  const b = order.indexOf(path);
  if (a < 0 || b < 0) return new Set(picked);
  const next = new Set(picked);
  for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(order[i]);
  return next;
}

/** Count + bytes of the ticked paths, summed from the rows already on screen.
 *  Rows the grid has not loaded contribute nothing, which is correct: a path can
 *  only be ticked from a row that was rendered. */
export function pickedTotals(
  picked: ReadonlySet<string>,
  rows: readonly GalleryFrame[],
): { count: number; bytes: number } {
  let count = 0;
  let bytes = 0;
  for (const r of rows) {
    if (!picked.has(r.path)) continue;
    count++;
    bytes += r.bytes || 0;
  }
  return { count, bytes };
}

// ============================================================================
// TILE STATE
// ============================================================================

/** What a thumbnail request's failure MEANS. The distinction is the whole
 *  reason the grid probes the status instead of letting `<img onerror>` shrug:
 *
 *    404  the file is gone — deleted or moved since this listing loaded. There
 *         is nothing to download and saying "no preview" would be a lie.
 *    422  the file is there and this server cannot render it (a .xisf, a FITS
 *         with an exotic BITPIX). The FITS is intact and downloadable.
 *
 *  Anything else is a transport/permission problem, which is a third thing again
 *  and must not be dressed up as either of the first two. */
export type ThumbFailure = "missing" | "unrenderable" | "error";

export function thumbFailure(status: number): ThumbFailure {
  if (status === 404) return "missing";
  if (status === 422) return "unrenderable";
  // 429 is deliberately NOT here. It is the one status that says nothing about
  // the frame — the relay's per-IP bucket pushed back and the picture is fine.
  // Rendering "PREVIEW FAILED" for it told the operator their library was
  // broken when the truth was "ask again in a second"; see `retryableThumb`
  // and lib/thumbQueue.ts. Measured 2026-08-10: 19 of 41 desktop tiles.
  return "error";
}

/** True for a status the tile should WAIT on rather than report as a verdict.
 *  429 (rate limited) and 503 (busy) both mean "later", not "broken". */
export function retryableThumb(status: number): boolean {
  return status === 429 || status === 503;
}

/**
 * The thumbnail width to ask for, given the tile's CSS width.
 *
 * A tile is ~147 CSS px in the desktop grid (`minmax(140px, 1fr)`), and on a
 * HiDPI display that is ~294 REAL pixels — so the 256 px this used to request
 * was upscaled on the exact screens most likely to be looking at it. That is
 * the "pixelated mess": not the resampling filter (Pillow's reduction is
 * area-weighted and fine), just too few pixels.
 *
 * Rounded up to a step so the server's path+mtime+width cache key takes a
 * handful of values instead of one per viewport width — a continuously-sized
 * request would miss the cache on every window resize and re-render a 26 MP
 * frame each time.
 */
export function thumbWidthFor(cssWidth: number, dpr = 1): number {
  const want = Math.ceil(Math.max(1, cssWidth) * Math.max(1, dpr));
  for (const step of THUMB_WIDTH_STEPS) if (want <= step) return step;
  return THUMB_WIDTH_STEPS[THUMB_WIDTH_STEPS.length - 1];
}

/** The widths the server will render and cache. Kept in step with
 *  `gallery.PRECOMPUTE_WIDTHS` on the server; a server test asserts they match.
 *
 *  ONE WIDTH, DELIBERATELY. This was [256, 384, 512, 768], which meant a phone
 *  at DPR 2.6 asked for 768 — the one width nothing pre-warmed — and paid a full
 *  50 MB FITS read plus a 26-megapixel stretch per tile: 2.3s each, 8s with
 *  several in flight, and a grid of grey placeholders. Reported from the rig
 *  2026-08-19.
 *
 *  Warming every step would have fixed the speed, but the operator compared
 *  256/384/768 rendered into a real 445-device-pixel tile and judged them near
 *  identical, at 0.5 MB versus 5.6 MB across 200 tiles. So the grid asks for one
 *  size, every device gets a cache hit, and the cache holds one entry per frame
 *  instead of four.
 *
 *  The full-resolution frame is a click away; this is the scanning grid. */
export const THUMB_WIDTH_STEPS = [256] as const;

/** The words on a failed tile. `label` is the chip drawn over the empty frame;
 *  `hint` is the sentence under it. `downloadable` decides whether the tile
 *  still offers the FITS — a frame we cannot RENDER is not a frame that is
 *  MISSING, and only the missing one loses its download. */
export function tileFailureCopy(
  failure: ThumbFailure,
  status?: number,
): { label: string; hint: string; downloadable: boolean } {
  if (failure === "missing") {
    return {
      label: "FILE GONE",
      hint: "Not on disk any more — deleted or moved since this list loaded. Refresh to drop it.",
      downloadable: false,
    };
  }
  if (failure === "unrenderable") {
    return {
      label: "NO PREVIEW",
      hint: "AstroDeck can't render this file. The frame itself is intact — download still works.",
      downloadable: true,
    };
  }
  return {
    label: "PREVIEW FAILED",
    hint: status
      ? `The thumbnail request returned ${status}. The frame is still listed and downloadable.`
      : "The thumbnail request failed. The frame is still listed and downloadable.",
    downloadable: true,
  };
}

/** `L · 300s · Light`, skipping whatever the header did not say. Never returns
 *  "" — an unreadable header is a fact worth printing, because it is why the
 *  target column fell back to the folder name. */
export function frameSubtitle(f: GalleryFrame): string {
  const parts: string[] = [];
  if (f.filter) parts.push(f.filter);
  if (f.exposure_s != null && Number.isFinite(f.exposure_s)) {
    parts.push(f.exposure_s >= 1 ? `${Math.round(f.exposure_s)}s` : `${f.exposure_s}s`);
  }
  if (f.frame_type) parts.push(f.frame_type);
  return parts.length ? parts.join(" · ") : "header unreadable";
}

/**
 * The sentence a frame needs when its NIGHT and its CALENDAR DATE disagree, and
 * null when they agree.
 *
 * This is GROUNDED #1 made visible. The default naming template stamps the
 * calendar date into the filename, but the filter groups by the noon-to-noon
 * night — so a frame named `..._2026-06-16_001200_...` is filed under
 * 2026-06-15, and to anyone reading the filename that looks like the filter is
 * broken. It is not; the filename is the thing that splits the session. Saying
 * so on the frame that shows the discrepancy is cheaper than a help page nobody
 * opens.
 *
 * BOTH DATES COME FROM THE SERVER, and this module deliberately owns no clock.
 * `night` is computed with the RIG's `localtime`; a browser reaching the rig
 * through the relay is in its own timezone. Formatting `ts` here — with a rig in
 * Arizona and a user in London, say — would put essentially every frame's
 * calendar date one day ahead of its night and print a wall-clock time the frame
 * was never taken at. The sentence written to REMOVE the night/filename
 * confusion would have become its largest source, on this feature's headline
 * correctness axis. So the row carries `local_date`/`local_clock` already in the
 * observatory's clock, one line from the night derived from the same call.
 */
export function nightVsFilename(f: GalleryFrame): string | null {
  if (!f.local_date || f.local_date === f.night) return null;
  return `Shot at ${f.local_clock} on ${f.local_date} rig time, so it belongs to the ` +
    `night of ${f.night} (a night runs noon to noon). The filename carries the ` +
    `calendar date.`;
}

// ============================================================================
// NIGHT FILTER
// ============================================================================

/** `2026-06-17 · 41 frames · 20.5 GB` — a night option that says what picking it
 *  would get you, so the range is chosen on evidence rather than by poking. */
export function nightOptionLabel(n: GalleryNight): string {
  return `${n.night} · ${fmtCount(n.frames)} ${n.frames === 1 ? "frame" : "frames"} · ${fmtBytes(n.bytes)}`;
}

/** What the current night bounds actually mean, in words. Both ends are
 *  inclusive server-side and an equal pair is the common "just that night"
 *  case, which is worth saying because it also names the downloaded zip. */
export function nightRangeLabel(from: string, to: string): string {
  if (!from && !to) return "All nights";
  if (from && from === to) return `Night of ${from}`;
  if (from && to) return `Nights ${from} to ${to}, inclusive`;
  if (from) return `${from} and later`;
  return `Up to and including ${to}`;
}

/** True when tonight's key is absent from the index — i.e. nothing has been
 *  captured yet on the night that is currently running. The picker must say
 *  that rather than offering a "Tonight" button that filters to nothing. */
export function tonightHasFrames(current: string, nights: readonly GalleryNight[]): boolean {
  return nights.some((n) => n.night === current);
}

// ============================================================================
// DESTRUCTIVE-ACTION COPY
// ============================================================================

/**
 * The consequence this feature accepts and must not hide.
 *
 * A session report and its ledger reference frames by path. Deleting a frame
 * leaves those rows pointing at nothing, and NOTHING in the codebase repairs
 * that — the report renders the frame as missing. The design's instruction was
 * explicit: do not silently repair it, say it where the user can see it. So it
 * rides in the delete confirmation, where the decision is being made, and again
 * on the trash panel, where the evidence is still recoverable.
 */
export const LEDGER_ORPHAN_NOTE =
  "Session reports and ledgers that reference these frames are not updated. " +
  "Those frames will show as missing in the report, and nothing repairs that.";

/** Delete-to-trash confirmation. Recoverable, so this is the lighter of the two
 *  dialogs — but it still names the size, because "delete 1,284 frames" and
 *  "free 38.2 GB" are different decisions. */
export function trashConfirmCopy(count: number, bytes: number, ttlDays: number): {
  title: string; body: string;
} {
  return {
    title: `Move ${fmtFrameCost(count, bytes)} to the trash?`,
    body:
      `They stay recoverable from the Trash panel for ${ttlDays} days, then purge ` +
      `automatically. The disk space is not freed until they purge. ${LEDGER_ORPHAN_NOTE}`,
  };
}

/** Purge confirmation — the irreversible one. Count and total size in the
 *  TITLE, where a hold-to-confirm dialog puts the thing you are agreeing to,
 *  and the word "permanently" is not softened. */
export function purgeConfirmCopy(count: number, bytes: number): {
  title: string; body: string;
} {
  return {
    title: `Permanently delete ${fmtCount(count)} ${count === 1 ? "file" : "files"}, ${fmtBytes(bytes)}?`,
    body:
      "This erases them from disk. There is no restore after this, and no second " +
      "copy anywhere else on the rig. Hold the button to confirm.",
  };
}

/** Why a bulk control is inert right now, or null when it is live. Order
 *  matters: a capability the caller does not hold is a permanent answer and
 *  outranks "nothing is selected", which they can fix in a second. */
export function bulkDisabledReason(
  count: number,
  capabilityPhrase: string | null,
  verb: string,
): string | null {
  if (capabilityPhrase) return `${verb} needs ${capabilityPhrase}.`;
  if (count <= 0) return "Nothing is selected.";
  return null;
}

/** The listing walk hit its ceiling. Said as a number and a consequence, never
 *  as a bare "truncated" badge: a prefix presented as a library is how someone
 *  concludes a night is missing. */
export function truncatedNote(shown: number): string {
  return `Only the first ${fmtCount(shown)} frames were walked — this is a prefix of the ` +
    "library, not all of it. Narrow the search or the night range to see the rest.";
}

/** Cold-scan honesty. The first listing after a server restart reads every FITS
 *  header (measured ~4.7 ms each), so a big library's first load is slow and
 *  every load after it is not. A number on screen beats a spinner that looks
 *  hung. Returns null for the warm case, which is nearly every case. */
export function scanNote(scanMs: number, total: number): string | null {
  if (!Number.isFinite(scanMs) || scanMs < 1500) return null;
  return `Read ${fmtCount(total)} frame headers in ${(scanMs / 1000).toFixed(1)}s — ` +
    "that is the cold cost after a server restart. Later listings come from cache.";
}

/** "in 12 days" / "tomorrow" / "today" / "any moment now" for an auto-purge
 *  deadline. The last one is honest about the sweep being periodic rather than
 *  instant: an expired item can still be sitting in the list. */
export function purgesIn(expiresAt: number, now: number): string {
  const secs = expiresAt - now;
  if (secs <= 0) return "purges on the next sweep";
  const days = Math.floor(secs / 86400);
  if (days >= 2) return `purges in ${days} days`;
  if (days === 1) return "purges tomorrow";
  const hours = Math.max(1, Math.round(secs / 3600));
  return `purges in ${hours} ${hours === 1 ? "hour" : "hours"}`;
}

/** "3 minutes ago" / "5 hours ago" / "12 days ago" for a deletion instant. */
export function deletedAgo(deletedAt: number, now: number): string {
  const secs = Math.max(0, now - deletedAt);
  if (secs < 90) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 90) return `${mins} minutes ago`;
  const hours = Math.round(secs / 3600);
  if (hours < 36) return `${hours} hours ago`;
  const days = Math.round(secs / 86400);
  return `${days} days ago`;
}

/** One line summarising a partially-successful bulk result, or null when
 *  everything worked. Partial failure is the case that must never be reported
 *  as success — the server names every refusal and so does this. */
export function partialFailureNote(
  ok: number,
  failed: readonly { path: string; reason: string }[],
): string | null {
  if (!failed.length) return null;
  const first = failed[0];
  const rest = failed.length - 1;
  return `${fmtCount(ok)} done, ${fmtCount(failed.length)} refused — ` +
    `${first.path}: ${first.reason}${rest > 0 ? ` (and ${fmtCount(rest)} more)` : ""}`;
}
