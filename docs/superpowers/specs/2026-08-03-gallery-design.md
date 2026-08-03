# Image Gallery — design

Requested 2026-08-03: a sidebar view over everything AstroDeck has captured,
with filename search, a date filter, bulk download of whatever the filter
returned, and delete-to-Trash with a 30-day auto-purge plus a purge-now option.

Grounded against the real code and the real captures tree on the rig that day.
Two findings changed the design before a line was written; they are marked
**GROUNDED** below.

---

## What is actually on disk

`CAPTURE_DIR` comes from `ASTRODECK_CAPTURE_DIR` or defaults to `captures/`
beside the package (`hub.py:204`). The default template is:

```
$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$.fits
```

so the tree is **flat-per-target** — `M42/Light_M42_2026-06-15_153141_0001.fits`
— with target folders that contain spaces and near-duplicates. 293 FITS on the
rig today.

**Resolve `CAPTURE_DIR` live off `hub_module.CAPTURE_DIR`, never as a
module-level `from ..hub import CAPTURE_DIR`.** Every existing consumer does
this so a test monkeypatch wins; the module-level binding is a documented bug
pattern in this codebase.

---

## GROUNDED #1 — the date filter cannot parse filenames

The default template renders `$$DATE$$`, the local **calendar** date. The
noon-rollover **night** is computed one line away in `hub.py` and exists as a
reusable `events.night_key()`, but the default filename does not carry it.

An astronomer's "last night" spans midnight. A date filter that parses the
filename would therefore **split every real session in half** — the frames
before midnight filed under one day, the frames after under the next. That is
worse than useless: it silently returns half a night and looks like it worked.

**So the filter derives the night from the frame's timestamp through
`events.night_key()`, never from its name.** `night_key` is already imported
into the API and already used as a query-param filter elsewhere, so this is the
house convention rather than a new one. Prefer the FITS `DATE-OBS` header when
cheap; fall back to mtime.

A follow-up worth raising separately: the default template should probably use
`$$NIGHT$$`. Changing it is not this feature's call — it would split the
existing tree across two conventions.

## GROUNDED #2 — the gallery introduces streaming to this server

`grep -rn "StreamingResponse" server/astrodeck/` returns **nothing**. The
closest precedent, the report bundle, builds an entire zip in `io.BytesIO` and
returns the bytes — safe only because that bundle deliberately contains no FITS
at all, which its own docstring says out loud.

Bulk download of a filter result is potentially many GB, so it must stream:
a generator feeding `StreamingResponse`, no temp file, nothing fully buffered.

Two ceilings to respect:

- the relay tunnels responses in **64 KiB frames with awaited backpressure**, so
  chunk at or below that and never outrun it;
- FITS compresses poorly. Use `ZIP_STORED`, not `ZIP_DEFLATED` — deflate costs
  CPU on a Pi-class box to save a few percent on data that is already dense.

**Show the count and total bytes before the download starts.** "Download 1,284
frames, 38.2 GB" is information the user cannot otherwise get, and it is the
difference between a deliberate action and a surprise.

---

## Privacy — why the gallery is authenticated

Confirmed in `imaging/fitsio.py`: every frame embeds **`SITELAT`, `SITELONG`,
`SITEELEV`**, and `OBJCTALT`/`AIRMASS` disclose the site indirectly because they
are computed from lat/lon. A gallery download therefore hands over the
observatory's location, and no route here may be anonymous.

Capability split, using the meanings `capabilities.py` already documents:

| action | capability | why |
|---|---|---|
| list + thumbnails | `view.preview` | its comment: "downsized preview frames (NOT raw FITS)" |
| download FITS, single or bulk | `view.media` | its comment: "raw FITS / full-res science (bulk)" |
| delete, restore, purge | `control.capture` | mutates the capture volume |

---

## Thumbnails

Neither existing store is reusable:

- the **preview ring** is pure in-memory, capped at 50, wiped on restart;
- **session thumbs** are real files on disk, but only for *sequence* frames —
  manual captures have none.

So the gallery generates thumbnails lazily on first view and caches them keyed
by path + mtime, with an intersection-observer grid so a 50k-frame library does
not render 50k images. Reuse `to_jpeg`/`to_thumb` from `imaging/processing.py`
rather than writing a third renderer.

---

## Trash

Delete = **rename** into a trash root, preserving the relative path, plus a
sidecar recording the original path and `deleted_at`. Rename is atomic on one
volume, which is why the trash lives inside `CAPTURE_DIR`.

**GROUNDED:** that placement collides with two existing sweeps. The calibration
scanner `rglob`s every `*.fits` under the capture dir, and factory reset walks
the same tree. The trash directory must be added to the calibration scanner's
`EXCLUDE_DIRS`, or deleted flats and darks will be silently stacked back into
masters — a deletion that does not take effect is worse than one that fails.

**Restore is in scope even though it was not requested.** A bin without restore
is just a delayed delete, and "Trash" is a word that promises undo.

Auto-purge at 30 days runs on the existing periodic loop. It must resolve real
paths and refuse anything that is not inside the trash root — reuse
`persist.safe_subpath`, whose docstring already names "gallery frames" as its
intended second caller.

**Purge-now is destructive and irreversible.** Two-step, showing count and total
size: "Permanently delete 1,284 files, 38.2 GB."

---

## Known consequence, deliberately accepted

Deleting a FITS **orphans the session ledger and the report** that reference it.
Nothing in the codebase handles that inverse today. The gallery should not
silently repair it; it should say so — a frame whose file is gone renders as
missing in the report rather than as a broken link. Repairing the ledger is a
separate item.

---

## Nav

One entry appended to the **end** of `NAV` in `App.tsx`; the standing rule in
that comment block is "land the order once, append entries thereafter", and
Monitor / Tonight / Reports / Help were all appended without reordering.

Five files: `types.ts` (the `ViewName` union — it lives there, not in
`store.ts`, to avoid an import cycle), `App.tsx` (the NAV entry), the view
itself, the icon if a new one is needed, and the nav test that enforces this.

---

## Listing performance

There is no index; listing is a filesystem walk. That is acceptable for 293
frames and not for 50k. Start with a walk plus an mtime-keyed in-process cache,
and add a real index only when measurement says to. Do not build SQLite on
speculation — but do measure and record the number, so the decision is made on
evidence rather than deferred forever.

---

## Testing

- The night-boundary case is the one that matters most: frames at 23:50 and
  00:10 must land in the **same** night, and a filename-parsing implementation
  must fail this test.
- Bulk download must stream — assert the response is not fully materialised.
- Purge must refuse a path outside the trash root; add it to the corpus in
  `tests/test_path_traversal.py` rather than writing new vectors.
- Deleted flats must not reappear in a calibration build.
