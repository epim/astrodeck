# Gallery performance and stable selections

Follow-up to the [gallery review](2026-09-19-gallery.md). Implements issues [#79](https://github.com/epim/astrodeck/issues/79), [#80](https://github.com/epim/astrodeck/issues/80), and [#81](https://github.com/epim/astrodeck/issues/81), plus the related defects below. Work was isolated from other sessions and used synthetic files. Nothing was deployed to the rig.

## Changes

### Metadata survives restarts (#79)

The gallery stores parsed FITS metadata in `_gallery_index/metadata-v1.sqlite3` under the capture directory. Each fresh listing reconciles file stamps with this cache. Unchanged frames need no header reads, including after a process restart. New, changed, moved, and removed files are reconciled; a bounded scan does not mistake unseen files for deleted files.

The index is disposable, capped at 200,000 entries, and excluded from the library. Corrupt SQLite caches rebuild automatically. An unavailable or read-only cache falls back to reading headers. Only the gallery's selected header fields are persisted; site cards and image pixels are not stored in the index.

The first-ever index build still reads every header. New searches still walk filesystem entries to discover external changes; later pages do not.

### Preview rendering is bounded (#80)

Identical in-flight thumbnail requests share one result. Thumbnail, full viewer, precompute, and backfill work share a one-image render budget. Warm cache hits bypass rendering and its queue.

HTTP rendering uses a dedicated worker, so waiting cold previews do not occupy the server's general worker pool. At most 16 distinct HTTP render jobs can be pending. Excess requests receive 503 with Retry-After; the tile's existing retry behavior handles this. A cancelled request does not cancel a shared render, and failures release ownership so a later retry can succeed.

Viewer resolution remains independent of thumbnail resolution. Existing JPEG caches use a new version key and are regenerated as needed.

### Pagination and bulk actions use one listing (#81)

The first page returns an opaque `snapshot` identifier and `next_cursor`. Later pages use that cursor to read the same ordered set, without another scan. Equal timestamps have a deterministic path tie-breaker. New captures cannot shift older rows between pages, and the UI also deduplicates appended paths defensively.

Snapshots hold metadata in temporary files, with a compact offset array in memory. Retained snapshots expire after 15 idle minutes and are bounded to 32 listings and 256 MiB in total. An expired, evicted, restarted, or wrong-filter cursor receives 409 with instructions to refresh; it never silently restarts pagination against a different set. Offset-only requests remain available for older API clients, but stable paging requires the new cursor.

Select-all enumeration, downloads, and trash requests carry the listing identifier. Before a bulk action, the server verifies membership and file versions. New captures are excluded; changed or missing selected files require a refreshed selection. The UI validates downloads before navigating, so an expired selection can be explained on the page. Snapshots contain metadata, not copies of FITS pixels; validation happens before the action.

## Related issues found and fixed

- [#84](https://github.com/epim/astrodeck/issues/84): server and browser preview keys rounded modification times, allowing stale images after subsecond replacements. Both now use full file versions; older-server timestamps retain their fractional precision.
- [#86](https://github.com/epim/astrodeck/issues/86): a transient FITS read failure was cached as permanently missing metadata. Failed reads are retried and are not persisted as valid metadata.
- [#87](https://github.com/epim/astrodeck/issues/87): the new selection validation initially compared Windows directory-entry inode zero with the inode returned by a direct stat. Version stamps now account for that platform difference. Tests caught this before deployment.

## Measurements

Reproduce with `PYTHONPATH=server python tools/gallery_benchmark.py --frames 5000`, using the project Python environment. The tool creates its own temporary synthetic FITS files and starts a separate process to test restart behavior. It never reads the configured capture library or connects equipment.

Measured on the Windows development PC:

| Operation | Result |
| --- | --- |
| Initial index build, 5,000 frames | 4,034.8 ms; 5,000 header reads |
| Fresh process using persisted index | 76.9 ms; zero header reads |
| Full first page and snapshot | 95.0 ms |
| Next page from that snapshot | 0.8 ms |
| Search matching one filename | 62.9 ms |
| Eight simultaneous identical cold previews | One render |

The earlier baseline took 4,132 ms after restart and performed eight renders for the same concurrent-preview workload. These are local synthetic measurements, not claims about Pi storage, network latency, or full-resolution camera decode time.

## Validation

- Combined server regression run: **227 passed, 2 skipped**, covering gallery operations, metadata, snapshot pagination/actions, previews, file containment, sync, backfill budgets, and capture-geometry warnings.
- After adding cancellation, failed-render retry, queue-status and selection-bound coverage, the final focused gallery-performance suite passed **18 tests**. This overlaps the combined run.
- **143 UI checks passed** across nine targeted suites, including stable cursor requests, selection URLs, stale-response handling, duplicate-path defense, and versioned preview URLs.
- Vite production build passed, with the existing large-chunk warning.
- Full TypeScript checking reports only the existing five errors in reserved photosphere/horizon files, including [#78](https://github.com/epim/astrodeck/issues/78). Those files were not changed.

Primary regression file: `server/tests/test_gallery_performance.py`. No live rig endpoints were exercised.
