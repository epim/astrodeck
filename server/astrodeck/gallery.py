"""Gallery — browse / search / download / trash the capture library.

The store side of the 2026-08-03 gallery design. Everything here is synchronous
and stdlib+astropy only; the routes in ``api/app.py`` call it through
``asyncio.to_thread`` so a filesystem walk never blocks the event loop.

Four decisions in this file are load-bearing enough that changing them silently
breaks something a test cannot obviously see. They are marked GROUNDED and the
design doc (docs/superpowers/specs/2026-08-03-gallery-design.md) carries the
same reasoning.

**GROUNDED 1 — the night NEVER comes from the filename.** The default naming
template renders ``$$DATE$$``, the local *calendar* date, but an observing night
runs noon-to-noon. Deriving the night by parsing ``..._2026-06-15_235000_...``
splits every real session at midnight: the 23:50 frame files under one day and
the 00:10 frame under the next, so "last night" silently returns half a night
and looks like it worked. So the night is computed from the frame's TIMESTAMP
through :func:`events.night_key` — the same noon-rollover the ``$$NIGHT$$``
token and the night log already use. The timestamp is ``DATE-OBS`` (the exposure
instant, which is what an astronomer means) when the header can be read, and
mtime only as a fallback. ``tests/test_gallery.py`` pins this with a 23:50 +
00:10 pair whose FILENAMES disagree and whose nights must agree.

**GROUNDED 2 — bulk download streams.** Before this file there was no
``StreamingResponse`` in the server at all; the nearest precedent (the report
bundle) builds a whole zip in ``io.BytesIO`` and is safe only because its own
docstring says it deliberately contains no FITS. A filter result here is
routinely tens of GB. :func:`iter_zip` therefore feeds a generator: no temp
file, nothing fully buffered, every yielded chunk bounded by
``ZIP_CHUNK_BYTES``. Two ceilings it respects:

* the relay tunnels response bodies in 64 KiB frames with an *awaited* send, so
  chunking at ``remote.protocol.DEFAULT_MAX_PAYLOAD`` makes one yielded chunk
  map to exactly one relay frame and lets the WSS write backpressure reach all
  the way back to the disk read;
* ``ZIP_STORED``, never ``ZIP_DEFLATED``. FITS is dense integer data that
  deflate barely touches, and the CPU it costs is a Pi-class core the rig needs
  for guiding.

**GROUNDED 3 — the trash lives INSIDE the capture root, and that collides with
two existing sweeps.** Delete is a *rename*, which is only atomic on one volume,
so the trash has to be a sibling of the target folders. But the calibration
scanner rglobs every ``*.fits`` under the capture root, so a deleted flat would
be silently stacked back into a master — a deletion that does not take effect is
worse than one that fails. ``calibration/library.EXCLUDE_DIRS`` imports
:data:`TRASH_DIRNAME` from here for exactly that reason; do not inline the
string there. Factory reset also walks this tree: the trash is deliberately NOT
in ``factory_reset.PRESERVED_CAPTURE_ENTRIES``, because deleted frames are still
the tester's data and must go when "delete captures" is ticked.

**GROUNDED 4 — containment goes through :func:`persist.safe_subpath`.** Its
docstring already names "gallery frames" as its intended second caller. Purge
additionally re-resolves and re-checks against the trash root, because purge is
the one irreversible operation here and a string check alone cannot see a
symlink planted inside the trash.

``CAPTURE_DIR`` is resolved LIVE off ``hub.CAPTURE_DIR`` on every call. A
module-level ``from .hub import CAPTURE_DIR`` is a documented bug pattern in this
codebase: it snapshots the path at import and every test monkeypatch then writes
somewhere the code under test is not looking.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .events import bus, night_key
from .persist import safe_subpath
from .remote.protocol import DEFAULT_MAX_PAYLOAD

# --------------------------------------------------------------------- layout

#: Deleted frames are RENAMED under ``<CAPTURE_DIR>/_trash/<original rel path>``.
#: Imported by ``calibration.library.EXCLUDE_DIRS`` — see GROUNDED 3.
TRASH_DIRNAME = "_trash"

#: Lazily-rendered thumbnail cache. A derived artifact: safe to delete at any
#: time, and it goes with the captures on a factory reset because a thumbnail of
#: a deleted frame is garbage.
THUMBS_DIRNAME = "_gallery_thumbs"

#: Sidecar written beside every trashed file, recording where it came from and
#: when it was deleted. Suffix (not a parallel tree) so the pair moves together
#: and an orphan is obvious. ``.json``, so the calibration ``*.fits`` rglob would
#: not see it even if the exclude above were lost.
TRASH_INFO_SUFFIX = ".trashinfo.json"

#: Auto-purge horizon. A trashed frame older than this is deleted for real by
#: :func:`purge_expired`, which the app's TrashKeeper ticks.
TRASH_TTL_DAYS = 30

#: What counts as a frame. ``.fits`` is what this server writes; the other three
#: are here because ``factory_reset.capture_inventory`` already defines "frames"
#: as this set, and two surfaces disagreeing about what a frame is would make the
#: reset's on-screen count contradict the gallery's.
FRAME_SUFFIXES = frozenset({".fits", ".fit", ".fts", ".xisf"})

#: Top-level entries under the capture root that are NOT the user's frame
#: library. Pruned by FIRST relative component only, never by name at any depth:
#: a target folder called "logs" or "reports" is a legal thing to image into, and
#: pruning by bare name anywhere would make that user's frames invisible with no
#: error. Everything here lives at the root by construction.
#:
#:   _trash          deleted frames — listed by the trash routes, not the grid
#:   _gallery_thumbs this module's own thumbnail cache (JPEGs, not frames)
#:   _masters        built calibration masters — owned by CalibrationLibrary,
#:                   which has its own delete route and a manifest that a
#:                   gallery delete would leave dangling
#:   _solve          plate-solver scratch
#:   exports         bundle materialization — HARDLINKS to frames that are also
#:                   listed at their real path, so including it double-counts
#:                   every byte in the "38.2 GB" summary
#:   _survey/_survey_pack/_weather_tiles/logs/sessions/reports  caches + ledgers
SKIP_TOP_DIRS = frozenset({
    TRASH_DIRNAME, THUMBS_DIRNAME, "_masters", "_solve", "exports",
    "_survey", "_survey_pack", "_weather_tiles", "logs", "sessions", "reports",
})

#: Bounded walk. A capture root with more frames than this is a real index's
#: problem, not a walk's; the listing reports ``truncated`` so the UI can say so
#: rather than quietly showing a prefix of the library.
SCAN_MAX_FILES = 200_000

#: One yielded body chunk == one relay frame. See GROUNDED 2. Imported rather
#: than re-declared so the two can never drift.
ZIP_CHUNK_BYTES = DEFAULT_MAX_PAYLOAD

#: Thumbnail cache bounds (a Pi SD card is small and this is pure derived data).
THUMB_CACHE_MAX_FILES = 20_000
THUMB_CACHE_MAX_BYTES = 256 * 1024 * 1024

#: Newly-rendered thumbnails between cache-bound checks.
#:
#: The prune has to enumerate AND stat the whole cache directory before it can
#: decide it has nothing to do, so running it on every cache miss charged that to
#: every newly rendered tile, on top of the FITS decode, exactly while someone
#: scrolls a large library for the first time. The bound designed to protect a
#: Pi's SD card was what made the grid crawl.
#:
#: MEASURED 2026-08-03, one full pass over a cache of N thumbnails (NVMe, warm):
#:
#:       N        glob + stat      scandir      per render at 1-in-200
#:     200            1.8 ms       0.4 ms                     0.002 ms
#:    2000           15.4 ms       4.1 ms                     0.021 ms
#:   20000          162.8 ms      43.2 ms                     0.216 ms   <- the cap
#:
#: Both halves matter: ``scandir`` makes the pass ~4x cheaper (the directory
#: entry already carries size and mtime), and the counter makes it rare. 162 ms
#: per tile at the cap becomes 0.2 ms, and on the SD card the cap exists for —
#: where that column is seconds, not milliseconds — it is the difference between
#: a usable grid and an unusable one.
#:
#: The cost of being late is at most this many extra thumbnails above the bound:
#: single-digit MB of derived data, against the feature's scroll performance. The
#: first render of the process prunes, so a box that restarts with an already-
#: over-cap cache trims it without waiting for 200 more.
THUMB_PRUNE_EVERY = 200

#: The largest thumbnail the route will render. 768 rather than the old 512
#: because the grid is `minmax(140px, 1fr)` and a HiDPI desktop asks for ~2x the
#: CSS width — at 256 the picture arrived with fewer pixels than the tile had,
#: which is what "a pixelated mess" was. Keep in step with
#: `ui/src/lib/gallery.ts::THUMB_WIDTH_STEPS`, whose last step this clamps.
THUMB_MAX_WIDTH = 768

#: JPEG quality for gallery thumbnails.
#:
#: Higher than the 70 the live filmstrip uses, because these are different
#: pictures for a different job: a filmstrip thumb is glanced at for a second
#: and thrown away, while a gallery tile is what someone scans a night's work
#: through. Measured on a real 26 MP frame at 256 px: 70 gives 4.4 KB, 85 gives
#: 6.5 KB — two kilobytes to stop the faint stuff turning to mush, on files the
#: browser caches immutably (the key carries mtime).
#:
#: BOTH render paths use this. The warm path and the lazy path producing
#: different bytes for the same frame would mean a tile changed appearance
#: depending on whether anyone had scrolled past it before.
THUMB_QUALITY = 85

#: The widths WARMED on capture and by the backfill.
#:
#: Only the two a real client asks for. Rendering every step would triple the
#: work for widths nothing requests: a 1x desktop asks 256, a 2x desktop asks
#: 512, and those two cover every tile the grid actually draws. A width outside
#: this list still WORKS — it renders on demand exactly as before — it is simply
#: not pre-warmed, which is the difference between "slow once" and "broken".
PRECOMPUTE_WIDTHS: tuple[int, ...] = (256, 512)

_NIGHT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------- roots

def capture_root() -> Path:
    """``CAPTURE_DIR``, read LIVE. Never bind this at import — see the module
    docstring; every existing consumer resolves it the same way so a test
    monkeypatch of ``hub.CAPTURE_DIR`` wins."""
    from . import hub as hub_module
    return hub_module.CAPTURE_DIR


def relpath_under_capture(abs_path: str | Path | None) -> str | None:
    """The capture-root-relative POSIX path for ``abs_path``, or None.

    **This is the only form of a frame's location that may leave the process.**
    An absolute path names the observatory's filesystem layout — the account
    name, the drive, the directory scheme — to whoever receives it, and none of
    that is any client's business. The relative path is strictly more useful to
    them anyway: it is the handle ``/api/gallery/file`` and the sync manifest
    both take, while the absolute one is not accepted anywhere.

    None means "not in the library": a NINA/remote save on another host, a path
    outside the capture root, or nothing at all. Callers surface that as an
    ABSENT field — never as a fabricated relative path, and never by falling
    back to the absolute one.
    """
    if not abs_path:
        return None
    try:
        p = Path(abs_path).resolve()
        return p.relative_to(capture_root().resolve()).as_posix()
    except (OSError, ValueError):
        return None


def trash_root() -> Path:
    return capture_root() / TRASH_DIRNAME


def thumbs_root() -> Path:
    return capture_root() / THUMBS_DIRNAME


# ------------------------------------------------------- frame metadata cache

#: ``resolved path str -> (mtime, size, meta dict)``. The walk gives path/size/
#: mtime for free; target/filter/frame-type/exposure/DATE-OBS cost a FITS header
#: read, so they are cached and re-read only when mtime or size changes (a frame
#: is written once and never edited, so this is exact rather than heuristic).
_META_CACHE: dict[str, tuple[float, int, dict]] = {}

#: Hard cap. This process runs for weeks on a rig that keeps capturing, so an
#: unbounded dict is a slow leak. Clearing wholesale (rather than evicting an
#: LRU we would have to track) costs one re-scan of header reads and keeps the
#: cache a cache instead of a second index with its own bugs.
_META_CACHE_MAX = 100_000


def _parse_date_obs(value: object) -> float | None:
    """``DATE-OBS`` (FITS 4.0: ``YYYY-MM-DDThh:mm:ss[.sss]``, UTC implied, no
    zone designator) -> POSIX timestamp. ``None`` for anything unparseable.

    The card is written without a zone by ``imaging.fitsio.save_fits`` precisely
    because strict FITS forbids one, so a naive parse is UTC by contract — not by
    assumption. Reading it as local time would shift the night by up to a day for
    an observer far from Greenwich, which is the exact bug this whole night-key
    path exists to avoid."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().rstrip("Z")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _read_header_meta(path: Path) -> dict:
    """Target / filter / frame type / exposure / capture instant from the FITS
    header. Total: any read or parse failure returns empty fields, so a corrupt
    or half-written frame still appears in the grid (with its filename, size and
    mtime) instead of vanishing from the user's library."""
    meta = {"target": "", "filter": "", "frame_type": "", "exposure_s": None,
            "ts": None}
    try:
        from astropy.io import fits          # lazy: same as hub/calibration
        hdr = fits.getheader(path)
    except Exception:                        # noqa: BLE001 — see docstring
        return meta
    try:
        meta["target"] = str(hdr.get("OBJECT", "") or "").strip()
        meta["filter"] = str(hdr.get("FILTER", "") or "").strip()
        meta["frame_type"] = str(hdr.get("IMAGETYP", "") or "").strip()
        exp = hdr.get("EXPTIME")
        meta["exposure_s"] = float(exp) if exp is not None else None
        meta["ts"] = _parse_date_obs(hdr.get("DATE-OBS"))
    except (TypeError, ValueError):
        pass
    return meta


def _meta_for(path: Path, mtime: float, size: int) -> dict:
    key = str(path)
    hit = _META_CACHE.get(key)
    if hit is not None and hit[0] == mtime and hit[1] == size:
        return hit[2]
    meta = _read_header_meta(path)
    if len(_META_CACHE) >= _META_CACHE_MAX:
        _META_CACHE.clear()
    _META_CACHE[key] = (mtime, size, meta)
    return meta


def clear_meta_cache() -> None:
    """Drop the header cache. For tests, and for anything that rewrites frames
    under a path it already listed within the same mtime granularity."""
    _META_CACHE.clear()


# ------------------------------------------------------------------- the walk

#: Symlinked directories already reported. Bounded by the number of links in the
#: tree, and it exists so the diagnostic below is a fact stated once rather than
#: a line per listing request.
_SYMLINK_WARNED: set[str] = set()


def _warn_skipped_symlink(path: str) -> None:
    if path in _SYMLINK_WARNED:
        return
    _SYMLINK_WARNED.add(path)
    bus.log("warning",
            f"gallery: {path} is a symlink (or a broken one) and is not "
            f"followed, so any frames under it are absent from the gallery and "
            f"from its totals. Point ASTRODECK_CAPTURE_DIR at the real "
            f"directory, or bind-mount it there.",
            "gallery")


def _walk_frames(root: Path) -> Iterator[tuple[str, os.stat_result]]:
    """``(relative posix path, stat)`` for every frame file under ``root``.

    ``os.scandir`` rather than ``rglob``: on both Windows and Linux the directory
    entry already carries the size and mtime, so a 50k-frame library costs one
    directory read per folder instead of 50k extra ``stat`` syscalls.

    Symlinks are skipped entirely. ``safe_subpath`` refuses a symlink that
    escapes the root (its ``resolve()`` backstop), so listing one would put a row
    in the grid whose thumbnail and download both 404 — a broken tile with no
    explanation. Not listing it is the same answer, given honestly.

    But it is only honest if it is SAID. A rig whose target folder is a symlink
    to a NAS mount is a plausible layout, and the silent version of this rule is
    an empty gallery and a "0 frames, 0 bytes" summary with no diagnostic
    anywhere — the user cannot tell "nothing captured" from "your library is
    behind a link this walk will not follow". So a skipped symlinked DIRECTORY
    (and a broken link, which is what an unmounted NAS looks like) is logged,
    once per path per process: the walk runs on every listing request, and a line
    per request would bury the night log."""
    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue                          # unreadable folder: skip, not fatal
        for entry in entries:
            try:
                if entry.is_symlink():
                    # A symlinked regular file is one absent row. A symlinked
                    # DIRECTORY — or a BROKEN link, which is what an unmounted
                    # NAS looks like — can be the user's entire library, and
                    # ``is_file()`` is False for both. That is the case worth a
                    # line in the log.
                    if not entry.is_file():
                        _warn_skipped_symlink(entry.path)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    # Prune infrastructure by FIRST component only (see
                    # SKIP_TOP_DIRS): `prefix == ()` means this dir is a direct
                    # child of the capture root.
                    if not prefix and entry.name in SKIP_TOP_DIRS:
                        continue
                    stack.append((Path(entry.path), prefix + (entry.name,)))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if Path(entry.name).suffix.lower() not in FRAME_SUFFIXES:
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            yield "/".join(prefix + (entry.name,)), st


def _row(root: Path, rel: str, st: os.stat_result) -> dict:
    """One grid row. ``ts`` is the capture instant the night is derived from and
    is returned to the client on purpose: a user who wonders why a 00:10 frame
    is filed under yesterday can see the timestamp that decided it.

    ``local_date`` and ``local_clock`` are that same instant rendered in the
    OBSERVATORY's timezone, and they exist because the client cannot compute
    them. ``night`` comes from :func:`events.night_key`, which is
    ``time.localtime`` on THIS box; a browser reaching the rig through the relay
    is in its own timezone, so a UI that formatted ``ts`` itself would compare
    London's calendar date against Arizona's night and announce a rollover on
    every frame in the library — at a wall-clock time the frame was never taken
    at. The one sentence written to REMOVE the night/filename confusion would
    become its biggest source. So both dates leave here already in the rig's
    clock, one line from the night that was derived from the same ``localtime``.
    """
    meta = _meta_for(root.joinpath(*rel.split("/")), st.st_mtime, st.st_size)
    ts = meta.get("ts") or st.st_mtime
    local = time.localtime(ts)
    folder, _, name = rel.rpartition("/")
    return {
        "path": rel,
        "name": name,
        "folder": folder,
        "night": night_key(ts),
        "ts": ts,
        # Rig-local, matching `night` above. See the docstring.
        "local_date": time.strftime("%Y-%m-%d", local),
        "local_clock": time.strftime("%H:%M", local),
        "target": meta.get("target") or folder,
        "filter": meta.get("filter") or "",
        "frame_type": meta.get("frame_type") or "",
        "exposure_s": meta.get("exposure_s"),
        "bytes": st.st_size,
        "mtime": st.st_mtime,
    }


#: Serializes concurrent scans so the second one finds a warm header cache
#: instead of re-reading every header alongside the first. The gallery view opens
#: with several requests at once (frames + nights + summary), which without this
#: multiplies the cold cost by the number of them. Held only for the walk, and
#: the walk is already off the event loop in a worker thread.
_SCAN_LOCK = threading.Lock()


def scan(root: Path | None = None, *, limit: int = SCAN_MAX_FILES
         ) -> tuple[list[dict], bool]:
    """Every frame under the capture root, newest capture first.

    Returns ``(rows, truncated)``.

    MEASURED 2026-08-03 on a synthetic copy of the rig's real library shape (293
    frames across three target folders, Windows, warm page cache):

        cold (every FITS header read once)   1381 ms   ≈ 4.7 ms / frame
        warm (mtime+size unchanged)             4 ms

    Recorded rather than guessed because the design's instruction was explicit:
    start with a walk plus an mtime-keyed cache, and reach for a real index only
    when measurement says to — but MEASURE, so the decision is evidence and not a
    deferral. What the numbers say: 1.4 s once after a restart is fine at 293
    frames, and the cost is linear in the header reads, so a 50k-frame library
    would pay roughly four minutes on the first request. That is the trigger. The
    fix when it arrives is a persisted version of ``_META_CACHE`` (the warm path
    is already 350x cheaper), not SQLite for its own sake."""
    root = capture_root() if root is None else root
    rows: list[dict] = []
    truncated = False
    if not root.is_dir():
        return rows, truncated
    with _SCAN_LOCK:
        for rel, st in _walk_frames(root):
            if len(rows) >= limit:
                truncated = True
                break
            rows.append(_row(root, rel, st))
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows, truncated


def valid_night(value: str) -> bool:
    return bool(_NIGHT_RE.match(value or ""))


def filter_rows(rows: Iterable[dict], *, q: str = "", night_from: str = "",
                night_to: str = "") -> list[dict]:
    """Substring + night-range filter.

    ``q`` matches the whole RELATIVE path case-insensitively, not just the
    basename, so typing a target folder name finds that target's frames — which
    is what a flat-per-target tree makes people try first.

    The night bounds are plain string compares because ``YYYY-MM-DD`` sorts
    lexicographically iff it sorts chronologically, and both ends are INCLUSIVE:
    ``night_from == night_to`` is "that one night", which is the single most
    common thing anyone asks this filter for."""
    needle = (q or "").strip().lower()
    lo = (night_from or "").strip()
    hi = (night_to or "").strip()
    out = []
    for r in rows:
        if needle and needle not in r["path"].lower():
            continue
        if lo and r["night"] < lo:
            continue
        if hi and r["night"] > hi:
            continue
        out.append(r)
    return out


def nights_index(rows: Iterable[dict]) -> list[dict]:
    """``[{night, frames, bytes}]`` newest first — the date picker's source. The
    UI can then offer only nights that exist instead of a free calendar where
    most dates return nothing."""
    agg: dict[str, dict] = {}
    for r in rows:
        e = agg.setdefault(r["night"], {"night": r["night"], "frames": 0, "bytes": 0})
        e["frames"] += 1
        e["bytes"] += r["bytes"]
    return sorted(agg.values(), key=lambda e: e["night"], reverse=True)


def resolve_selection(paths: Iterable[str]) -> tuple[list[dict], list[dict]]:
    """Explicit client-supplied relative paths -> rows, plus per-path refusals.

    Used by download/summary when the user hand-picked frames instead of taking
    the whole filter result. Every path is contained by ``safe_subpath`` and must
    land on a real frame file; a refusal is REPORTED, never silently dropped, so
    a selection that partially fails cannot masquerade as a complete download."""
    root = capture_root()
    rows: list[dict] = []
    failed: list[dict] = []
    seen: set[str] = set()
    for raw in paths:
        rel = (raw or "").strip().replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        try:
            target = safe_subpath(root, rel)
        except KeyError:
            failed.append({"path": raw, "reason": "not a path inside the capture library"})
            continue
        parts = rel.split("/")
        if parts[0] in SKIP_TOP_DIRS:
            failed.append({"path": raw, "reason": f"{parts[0]} is not part of the frame library"})
            continue
        if target.suffix.lower() not in FRAME_SUFFIXES:
            failed.append({"path": raw, "reason": "not a frame file"})
            continue
        try:
            st = target.stat()
        except OSError:
            failed.append({"path": raw, "reason": "no longer on disk"})
            continue
        rows.append(_row(root, rel, st))
    return rows, failed


def summarize(rows: Iterable[dict]) -> dict:
    """``{count, bytes}`` — the "1,284 frames, 38.2 GB" the UI must show BEFORE a
    bulk download or a purge starts. It is the difference between a deliberate
    action and a surprise, and it is information the user has no other way to
    get."""
    count = 0
    total = 0
    for r in rows:
        count += 1
        total += int(r.get("bytes") or 0)
    return {"count": count, "bytes": total}


# ------------------------------------------------------------- streamed zip

class _ZipSink:
    """A write-only sink ``zipfile`` can target, draining into the generator.

    It deliberately implements ``write``/``tell``/``flush`` and NOT ``seek``.
    ``ZipFile`` probes for ``seek`` and, finding none, switches to data
    descriptors (it writes each member's sizes AFTER the member instead of
    rewinding to patch the local header) — which is exactly the streamable ZIP
    shape we need. Adding a ``seek`` method here would silently turn that off and
    the writer would try to rewind a stream that has already left the process.

    It also deliberately does NOT implement ``__len__``, and ``pending()`` exists
    instead of the obvious dunder. ``ZipFile`` guards every operation with
    ``if not self.fp: raise ValueError("Attempt to use ZIP archive that was
    already closed")`` — and an object with ``__len__`` returning 0 is FALSY. A
    sink with an empty buffer therefore read as a closed archive, and the first
    ``open()`` after construction failed with a message about closing that has
    nothing to do with the cause. The symptom was a 22-byte "valid" zip
    containing nothing at all."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._offset = 0

    def write(self, data: bytes) -> int:
        self._buf.extend(data)
        self._offset += len(data)
        return len(data)

    def flush(self) -> None:
        return None

    def tell(self) -> int:
        return self._offset

    def pending(self) -> int:
        """Bytes buffered but not yet handed to the generator."""
        return len(self._buf)

    def take(self, n: int) -> bytes:
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def iter_zip(members: Iterable[tuple[Path, str]], *,
             chunk_bytes: int = ZIP_CHUNK_BYTES) -> Iterator[bytes]:
    """Stream a ``ZIP_STORED`` archive of ``(source path, archive name)`` pairs.

    Never materializes the archive, and never yields more than ``chunk_bytes`` at
    once, so a 38 GB download costs one 64 KiB buffer plus one open file handle
    regardless of size, and each yielded chunk maps to exactly one awaited relay
    frame (GROUNDED 2).

    NOTHING in here may raise: the HTTP status and headers are already on the
    wire by the time the first chunk is produced, so an exception mid-body
    truncates the archive with a 200 already sent. A source that disappeared or
    cannot be read is skipped and logged — the user gets a valid zip that is
    missing a frame, which their unzip tool will tell them, rather than a corrupt
    one that it will not."""
    sink = _ZipSink()

    def drain(final: bool = False) -> Iterator[bytes]:
        while sink.pending() >= chunk_bytes:
            yield sink.take(chunk_bytes)
        if final and sink.pending():
            yield sink.take(sink.pending())

    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True)
    try:
        for src, arcname in members:
            try:
                st = src.stat()
                info = zipfile.ZipInfo(arcname,
                                       date_time=time.localtime(st.st_mtime)[:6])
                info.compress_type = zipfile.ZIP_STORED
                # Set before open(): the writer decides ZIP64 per member from
                # this, and a >4 GiB frame written as non-ZIP64 is an archive
                # that unzips to garbage.
                info.file_size = st.st_size
                info.external_attr = 0o644 << 16
                handle = zf.open(info, "w")
            except (OSError, ValueError) as e:
                bus.log("warning", f"gallery download skipped {arcname}: {e}",
                        "gallery")
                continue
            try:
                with open(src, "rb") as fh:
                    while True:
                        block = fh.read(chunk_bytes)
                        if not block:
                            break
                        handle.write(block)
                        yield from drain()
            except OSError as e:
                bus.log("warning", f"gallery download truncated {arcname}: {e}",
                        "gallery")
            # Closed here rather than in a `finally`, deliberately. A client that
            # gives up on a 38 GB download makes starlette close this generator,
            # which throws GeneratorExit at the `yield` above — and yielding the
            # drain from a `finally` at that moment raises "generator ignored
            # GeneratorExit", turning a normal disconnect into a logged crash.
            # On a disconnect there is nobody left to read the bytes anyway, and
            # the handles are reclaimed when the generator is collected.
            try:
                handle.close()              # writes this member's data descriptor
            except (OSError, ValueError) as e:
                bus.log("warning", f"gallery download could not finish "
                                   f"{arcname}: {e}", "gallery")
            yield from drain()
    finally:
        try:
            zf.close()                      # central directory — never yields
        except (OSError, ValueError) as e:  # pragma: no cover - defensive
            bus.log("error", f"gallery download could not be finalized: {e}",
                    "gallery")
    yield from drain(final=True)


def zip_members(rows: Iterable[dict]) -> list[tuple[Path, str]]:
    """Rows -> ``(absolute source, archive name)``. The archive preserves the
    relative path, so unzipping reproduces the target-folder layout the user
    already knows instead of dumping 1,284 files into one directory."""
    root = capture_root()
    return [(root.joinpath(*r["path"].split("/")), r["path"]) for r in rows]


# ------------------------------------------------------------------ thumbnails

#: Renders since the last cache-bound check. ``itertools.count.__next__`` is a
#: single bytecode under the GIL, so concurrent renders cannot lose a tick or
#: hand two threads the same number — and even if they did, the only consequence
#: is a prune one render early or late.
_thumb_render_seq = itertools.count()


def _thumb_prune_due() -> bool:
    """True every ``THUMB_PRUNE_EVERY``-th newly-rendered thumbnail, starting
    with the first one of the process. See ``THUMB_PRUNE_EVERY``."""
    return next(_thumb_render_seq) % THUMB_PRUNE_EVERY == 0


def _thumb_cache_path(rel: str, mtime: float, width: int) -> Path:
    """Cache key = path + mtime + width, hashed. Hashing gives a guaranteed-safe
    flat filename (hex only) for a relative path that contains separators and
    user-chosen target names, and folding mtime into the NAME rather than
    comparing it later means a re-captured frame at the same path can never serve
    the old thumbnail."""
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()
    return thumbs_root() / f"{digest}_{int(mtime)}_{int(width)}.jpg"


def _prune_thumb_cache(directory: Path) -> None:
    """Bound the cache by file count and total bytes, oldest first. Best-effort:
    the cache is an optimization, so an I/O hiccup leaves it as-is rather than
    failing the request that happened to trigger the prune.

    ``os.scandir`` rather than ``glob`` + ``stat``, for the same reason
    :func:`_walk_frames` uses it: the directory entry already carries size and
    mtime on Windows, so a full cache costs one directory read instead of 20 000
    extra syscalls. It is still O(N) — that is why the caller runs it on a
    counter (``THUMB_PRUNE_EVERY``) and not on every cache miss."""
    try:
        entries = []
        try:
            scan_it = os.scandir(directory)
        except OSError:
            return                            # no cache dir yet: nothing to bound
        with scan_it:
            for entry in scan_it:
                if not entry.name.endswith(".jpg"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, Path(entry.path)))
        total = sum(e[1] for e in entries)
        count = len(entries)
        if count <= THUMB_CACHE_MAX_FILES and total <= THUMB_CACHE_MAX_BYTES:
            return
        entries.sort(key=lambda e: e[0])
        for _m, size, p in entries:
            if count <= THUMB_CACHE_MAX_FILES and total <= THUMB_CACHE_MAX_BYTES:
                break
            try:
                p.unlink()
                total -= size
                count -= 1
            except OSError:
                count -= 1                  # can't remove it; don't spin on it
    except OSError:
        pass


def thumbnail(rel: str, *, width: int = 256) -> bytes:
    """JPEG thumbnail for one frame, rendered on first view and cached on disk.

    Neither existing thumbnail store is reusable, which is why this exists: the
    live-preview ring is memory-only and capped at 50 frames (so it is empty
    after a restart and never held last week's frames at all), and session thumbs
    are real files but exist ONLY for frames a sequence produced — a manual
    capture has none. This is the third store and the LAST one; the rendering
    itself reuses ``imaging.processing.to_thumb`` rather than adding a third
    renderer to go with it.

    Raises ``KeyError`` for a path outside the library and ``FileNotFoundError``
    when the frame is gone; the route maps both to 404. Raises ``ValueError``
    when the file is not renderable (e.g. an ``.xisf`` we can list but cannot
    decode) so the grid can show a "no preview" tile instead of a broken image."""
    root = capture_root()
    # Normalize the separator FIRST. ``safe_subpath`` treats a backslash as a
    # separator wherever the server runs, so ``_trash\x.fits`` resolves into the
    # trash — while a naive ``rel.split("/")[0]`` on that same string yields the
    # whole path and matches no infrastructure name. Contained either way, but
    # the two guards must agree on what the first component IS.
    rel = (rel or "").replace("\\", "/")
    path = safe_subpath(root, rel)           # raises KeyError on any escape
    if (rel.split("/")[0] in SKIP_TOP_DIRS
            or path.suffix.lower() not in FRAME_SUFFIXES):
        raise KeyError(rel)
    st = path.stat()                         # FileNotFoundError -> 404
    width = max(32, min(int(width), THUMB_MAX_WIDTH))
    cached = _thumb_cache_path(rel, st.st_mtime, width)
    try:
        return cached.read_bytes()
    except OSError:
        pass
    try:
        from astropy.io import fits
        from .imaging.processing import to_thumb
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
        if data is None:
            raise ValueError("frame has no image data")
        jpeg = to_thumb(data, max_width=width, quality=THUMB_QUALITY)
    except ValueError:
        raise
    except Exception as e:                   # noqa: BLE001 — unreadable == no preview
        raise ValueError(f"cannot render {rel}: {e}") from e
    if not jpeg:
        raise ValueError(f"cannot render {rel}")
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(jpeg)
        tmp.replace(cached)                  # atomic publish: never serve a partial
        if _thumb_prune_due():               # amortised — see THUMB_PRUNE_EVERY
            _prune_thumb_cache(cached.parent)
    except OSError:
        pass                                 # cache miss forever beats a 500
    return jpeg


def thumb_is_cached(rel: str, width: int) -> bool:
    """True iff the thumbnail for ``rel`` at ``width`` is already on disk.

    Derived from the file, never from a flag: the cache key carries the frame's
    mtime, so a re-captured frame at the same path answers False without anyone
    having to remember to invalidate anything.
    """
    try:
        root = capture_root()
        rel = (rel or "").replace("\\", "/")
        path = safe_subpath(root, rel)
        width = max(32, min(int(width), THUMB_MAX_WIDTH))
        return _thumb_cache_path(rel, path.stat().st_mtime, width).exists()
    except (KeyError, OSError):
        return False


def precompute(rel: str, widths: "tuple[int, ...] | None" = None) -> int:
    """Render and cache ``rel``'s thumbnails ahead of anyone asking. Returns how
    many were actually rendered (0 when they were all already cached).

    ONE READ AND ONE STRETCH FOR ALL THE WIDTHS. The expensive part is not the
    disk or the JPEG: it is ``auto_stretch`` over 26 megapixels, measured at
    1.44 s of the 1.49 s a cold thumbnail costs. Calling ``thumbnail`` once per
    width would pay that per width, and this runs over a whole library.

    The cache KEY still comes from ``_thumb_cache_path``, the same function the
    route reads through, because that is the property that actually matters: a
    warm cache the reader cannot find is indistinguishable from a cold one. What
    is shared is the key, not the loop.

    Never raises. A frame that cannot be rendered (an ``.xisf`` we can list but
    not decode) is not an error at warm time; it is a tile that will say "no
    preview" when someone eventually looks at it, which is already handled.
    """
    want = [w for w in (widths or PRECOMPUTE_WIDTHS)
            if not thumb_is_cached(rel, w)]
    if not want:
        return 0
    try:
        root = capture_root()
        rel_n = (rel or "").replace("\\", "/")
        path = safe_subpath(root, rel_n)
        if (rel_n.split("/")[0] in SKIP_TOP_DIRS
                or path.suffix.lower() not in FRAME_SUFFIXES):
            return 0
        st = path.stat()
        from astropy.io import fits
        from .imaging.processing import auto_stretch, _encode
        import numpy as _np
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
        if data is None:
            return 0
        img01 = auto_stretch(_np.asarray(data))      # the 1.44 s, paid once
    except Exception:                        # noqa: BLE001 — warming is optional
        return 0
    made = 0
    for w in want:
        try:
            w = max(32, min(int(w), THUMB_MAX_WIDTH))
            jpeg = _encode(img01, max_width=w, fmt="JPEG", quality=THUMB_QUALITY)[0]
            if not jpeg:
                continue
            cached = _thumb_cache_path(rel_n, st.st_mtime, w)
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(f".{w}.tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(cached)              # atomic: never serve a partial
            made += 1
        except Exception:                    # noqa: BLE001
            continue
    return made


def backfill(*, widths: "tuple[int, ...] | None" = None,
             limit: int = 0,
             progress=None) -> dict:
    """Warm every listable frame's thumbnails. Returns a summary dict.

    This is the "suspenders" half of the belt-and-suspenders: capture warms new
    frames from now on, and this closes the gap for everything shot before the
    write-through existed — or during any window where it was skipped, dropped
    or interrupted. Safe to run repeatedly; already-cached widths cost a stat.

    ``progress(done, total, made)`` is called every 25 frames when supplied, so
    a long run over a 642-frame library can say something rather than appear
    hung for twenty minutes.
    """
    rows, truncated = scan()
    if limit > 0:
        rows = rows[:limit]
    total, made, failed = len(rows), 0, 0
    for i, r in enumerate(rows, 1):
        rel = r.get("path") or ""
        try:
            n = precompute(rel, widths)
            made += n
            if n == 0 and not thumb_is_cached(rel, (widths or PRECOMPUTE_WIDTHS)[0]):
                failed += 1
        except Exception:                    # noqa: BLE001 — one bad frame is not a run
            failed += 1
        if progress is not None and (i % 25 == 0 or i == total):
            progress(i, total, made)
    # SAY WHEN THE COVERAGE WAS BOUNDED. A backfill that quietly stopped at
    # SCAN_MAX_FILES reads exactly like one that finished, and the frames past
    # the cap stay cold forever while the summary says everything is warm.
    return {"frames": total, "rendered": made, "unrenderable": failed,
            "truncated": bool(truncated) or (limit > 0 and limit < total)}


# ----------------------------------------------------------------------- trash

def _info_path(target: Path) -> Path:
    return target.with_name(target.name + TRASH_INFO_SUFFIX)


def _unique_destination(dest: Path) -> Path:
    """A free path in the trash for ``dest``.

    Needed because delete/restore/delete of the same frame, or two captures that
    reused a filename across a restore, would otherwise land on the same trash
    path — and ``os.replace`` overwrites silently, so the first deletion would
    disappear with no error. The sidecar records the true original, so a
    suffixed trash name still restores to the right place."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for n in range(1, 10000):
        candidate = dest.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("trash destination is full")


def trash_frames(paths: Iterable[str]) -> dict:
    """Move frames to the trash. Returns ``{trashed, failed, bytes}``.

    Delete is a RENAME, not a copy-then-unlink: the trash is inside CAPTURE_DIR
    precisely so the rename stays on one volume, where it is atomic. There is no
    window in which the frame exists twice or not at all, and a 200 GB night
    "deletes" instantly and costs no extra disk."""
    root = capture_root()
    troot = trash_root()
    trashed: list[dict] = []
    failed: list[dict] = []
    total = 0
    for raw in paths:
        rel = (raw or "").strip().replace("\\", "/")
        try:
            src = safe_subpath(root, rel)
        except KeyError:
            failed.append({"path": raw, "reason": "not a path inside the capture library"})
            continue
        parts = rel.split("/")
        if parts[0] in SKIP_TOP_DIRS:
            # Refuses ``_trash/...`` (already deleted), ``_masters/...`` (the
            # calibration library owns those and its manifest would dangle) and
            # every cache directory.
            failed.append({"path": raw, "reason": f"{parts[0]} is not part of the frame library"})
            continue
        if src.suffix.lower() not in FRAME_SUFFIXES:
            failed.append({"path": raw, "reason": "not a frame file"})
            continue
        try:
            size = src.stat().st_size
        except OSError:
            failed.append({"path": raw, "reason": "no longer on disk"})
            continue
        try:
            dest = _unique_destination(troot.joinpath(*parts))
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
        except OSError as e:
            failed.append({"path": raw, "reason": f"could not move to trash: {e}"})
            continue
        deleted_at = time.time()
        try:
            _info_path(dest).write_text(json.dumps({
                "original": rel, "deleted_at": deleted_at, "bytes": size,
            }, indent=2), encoding="utf-8")
        except OSError:
            # The sidecar is recoverable metadata, not the deletion itself. Its
            # absence degrades restore to "put it back where it sits in the
            # trash", which for the normal case is the same answer.
            pass
        trashed.append({"path": _trash_rel(troot, dest), "original": rel,
                        "bytes": size, "deleted_at": deleted_at})
        total += size
    if trashed:
        bus.log("warning",
                f"gallery: {len(trashed)} frame(s) moved to trash "
                f"({total} bytes) — auto-purge in {TRASH_TTL_DAYS} days",
                "gallery")
    return {"trashed": trashed, "failed": failed, "bytes": total}


def _trash_rel(troot: Path, path: Path) -> str:
    try:
        return path.relative_to(troot).as_posix()
    except ValueError:                       # pragma: no cover - defensive
        return path.name


def list_trash() -> dict:
    """Everything in the trash, newest deletion first, with what it would cost to
    keep and when it goes.

    ``expires_at`` and ``restorable`` are computed here rather than in the UI: a
    bin that cannot say "this disappears in 12 days" or "this one cannot go back,
    something else is at that path now" is a bin you cannot trust."""
    troot = trash_root()
    items: list[dict] = []
    total = 0
    root = capture_root()
    if not troot.is_dir():
        return {"items": items, "count": 0, "bytes": 0, "ttl_days": TRASH_TTL_DAYS}
    for rel, st in _walk_frames(troot):
        path = troot.joinpath(*rel.split("/"))
        info = {}
        try:
            info = json.loads(_info_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        original = str(info.get("original") or rel)
        deleted_at = float(info.get("deleted_at") or st.st_mtime)
        restorable = True
        try:
            restorable = not safe_subpath(root, original).exists()
        except (KeyError, OSError):
            restorable = False
        items.append({
            "path": rel,
            "original": original,
            "name": rel.rpartition("/")[2],
            "deleted_at": deleted_at,
            "expires_at": deleted_at + TRASH_TTL_DAYS * 86400,
            "bytes": st.st_size,
            "restorable": restorable,
        })
        total += st.st_size
    items.sort(key=lambda i: i["deleted_at"], reverse=True)
    return {"items": items, "count": len(items), "bytes": total,
            "ttl_days": TRASH_TTL_DAYS}


def restore_frames(paths: Iterable[str]) -> dict:
    """Move trashed frames back to their original paths.

    In scope even though the request did not ask for it: a bin without restore is
    a delayed delete, and "Trash" is a word that promises undo."""
    root = capture_root()
    troot = trash_root()
    restored: list[dict] = []
    failed: list[dict] = []
    for raw in paths:
        rel = (raw or "").strip().replace("\\", "/")
        try:
            src = safe_subpath(troot, rel)
        except KeyError:
            failed.append({"path": raw, "reason": "not a path inside the trash"})
            continue
        if not src.is_file():
            failed.append({"path": raw, "reason": "not in the trash"})
            continue
        if src.suffix.lower() not in FRAME_SUFFIXES:
            # Mirrors the same check in `trash_frames`, and it is not symmetry
            # for its own sake: without it, restoring `<frame>.trashinfo.json`
            # moves the SIDECAR into the live library and leaves the frame in the
            # bin with no metadata. `purge_expired` then falls back to st_mtime,
            # which the rename into the trash preserved and which is therefore
            # the CAPTURE time — so a frame shot two months ago and deleted today
            # is purged on the very next sweep, destroying exactly the data the
            # 30-day grace period exists to protect. The UI only ever sends
            # `item.path`, so this needs a hand-crafted request; it is still the
            # only safety net the delete path has.
            failed.append({"path": raw, "reason": "not a frame file"})
            continue
        original = rel
        try:
            info = json.loads(_info_path(src).read_text(encoding="utf-8"))
            original = str(info.get("original") or rel)
        except (OSError, ValueError):
            pass                              # sidecar lost: restore in place
        try:
            dest = safe_subpath(root, original)
        except KeyError:
            failed.append({"path": raw, "reason": "recorded original path is not restorable"})
            continue
        if dest.exists():
            failed.append({"path": raw,
                           "reason": f"a file already exists at {original}"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
        except OSError as e:
            failed.append({"path": raw, "reason": f"could not restore: {e}"})
            continue
        try:
            _info_path(src).unlink(missing_ok=True)
        except OSError:
            pass
        restored.append({"path": rel, "restored_to": original})
    if restored:
        bus.log("info", f"gallery: {len(restored)} frame(s) restored from trash",
                "gallery")
    return {"restored": restored, "failed": failed}


def _purge_one(troot_resolved: Path, path: Path) -> int:
    """Delete ONE trashed file for real. Returns bytes freed.

    Raises ``KeyError`` for anything that does not resolve to a real path inside
    the trash root. This is the last gate before an irreversible unlink, and it
    RESOLVES first: the string checks in ``safe_subpath`` cannot see a symlink
    planted inside the trash that points at the live library (or at
    ``/etc``), and purge is the one operation here with no undo."""
    resolved = path.resolve()
    if not resolved.is_relative_to(troot_resolved):
        raise KeyError(str(path))
    freed = 0
    try:
        freed = resolved.stat().st_size
    except OSError:
        pass
    resolved.unlink()
    info = _info_path(resolved)
    try:
        if info.resolve().is_relative_to(troot_resolved):
            info.unlink(missing_ok=True)
    except OSError:
        pass
    return freed


def purge_paths(paths: Iterable[str]) -> dict:
    """Permanently delete specific trashed frames. ``{purged, bytes, failed}``.

    Every path is checked twice — ``safe_subpath`` against the trash root for the
    string-level vectors, then ``_purge_one``'s resolve-and-contain for the ones
    no string check can see."""
    troot = trash_root()
    try:
        troot_resolved = troot.resolve()
    except OSError:                          # pragma: no cover - defensive
        return {"purged": 0, "bytes": 0, "failed": []}
    purged = 0
    freed = 0
    failed: list[dict] = []
    for raw in paths:
        rel = (raw or "").strip().replace("\\", "/")
        try:
            target = safe_subpath(troot, rel)
            freed += _purge_one(troot_resolved, target)
            purged += 1
        except KeyError:
            failed.append({"path": raw, "reason": "not a path inside the trash"})
        except OSError as e:
            failed.append({"path": raw, "reason": f"could not delete: {e}"})
    if purged:
        bus.log("warning",
                f"gallery: {purged} frame(s) permanently deleted ({freed} bytes)",
                "gallery")
    return {"purged": purged, "bytes": freed, "failed": failed}


def purge_all() -> dict:
    """Empty the trash now. Destructive and irreversible — the UI two-steps this
    and shows the measured count and size first."""
    troot = trash_root()
    if not troot.is_dir():
        return {"purged": 0, "bytes": 0, "failed": []}
    return purge_paths([rel for rel, _st in _walk_frames(troot)])


def purge_expired(*, ttl_days: int = TRASH_TTL_DAYS, now: float | None = None) -> dict:
    """Delete trashed frames older than ``ttl_days``. The auto-purge.

    Age comes from the sidecar's ``deleted_at``, not the file's mtime: mtime is
    preserved by the rename into the trash, so it is the CAPTURE time, and using
    it would purge a two-month-old frame the instant it was deleted — destroying
    exactly the data the 30-day grace period exists to protect. mtime is only the
    fallback for an item whose sidecar was lost."""
    troot = trash_root()
    if not troot.is_dir():
        return {"purged": 0, "bytes": 0, "failed": []}
    cutoff = (time.time() if now is None else now) - ttl_days * 86400
    doomed: list[str] = []
    for rel, st in _walk_frames(troot):
        path = troot.joinpath(*rel.split("/"))
        deleted_at = st.st_mtime
        try:
            info = json.loads(_info_path(path).read_text(encoding="utf-8"))
            deleted_at = float(info.get("deleted_at") or st.st_mtime)
        except (OSError, ValueError, TypeError):
            pass
        if deleted_at < cutoff:
            doomed.append(rel)
    if not doomed:
        return {"purged": 0, "bytes": 0, "failed": []}
    return purge_paths(doomed)


class TrashKeeper:
    """The auto-purge ticker: one asyncio task, started with the app.

    Cadence is hours because the job is a 30-DAY horizon — waking more often
    cannot make a deletion happen sooner and only costs a directory walk on a
    Pi. The first tick runs at start so a box that is rebooted daily still
    purges; without it a rig that never stays up for six hours would keep
    expired frames forever.

    Why this is its own task rather than a hook on an existing loop: the two
    always-on loops in this server are the AlertDispatcher's wall-clock driver
    (which pings the external dead-man's switch — a filesystem walk that stalls
    it turns into a false "rig is down" alert) and ResumeArm/WeatherService,
    which are sequence and forecast services. Attaching disk housekeeping to the
    deadman loop trades a real safety signal for a scheduler we do not need; the
    shape below is the same one those three services already use, so it adds a
    pattern to nobody's head."""

    #: 6 hours. Four chances a day to notice a 30-day expiry is ample.
    INTERVAL_S = 6 * 3600.0

    def __init__(self, *, ttl_days: int = TRASH_TTL_DAYS,
                 interval_s: float | None = None):
        self.ttl_days = ttl_days
        self.interval_s = self.INTERVAL_S if interval_s is None else interval_s
        self._task = None

    def start(self) -> None:
        import asyncio
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        import asyncio
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def tick(self) -> dict:
        """One sweep, off the event loop. Total: a purge failure is logged and
        the loop keeps running — housekeeping must never take the server down."""
        import asyncio
        result = await asyncio.to_thread(purge_expired, ttl_days=self.ttl_days)
        await asyncio.to_thread(sweep_orphan_sidecars)
        if result.get("purged"):
            bus.log("info",
                    f"gallery: auto-purged {result['purged']} frame(s) older "
                    f"than {self.ttl_days} days from the trash", "gallery")
        return result

    async def _run(self) -> None:
        import asyncio
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:           # noqa: BLE001 — service must never die
                bus.log("warning", f"gallery trash purge failed: {e}", "gallery")
            await asyncio.sleep(self.interval_s)


def sweep_orphan_sidecars() -> int:
    """Remove ``*.trashinfo.json`` files whose frame is gone (restored or purged
    by hand). Cheap, and it keeps ``bytes`` on the trash listing honest."""
    troot = trash_root()
    removed = 0
    if not troot.is_dir():
        return 0
    for p in troot.rglob(f"*{TRASH_INFO_SUFFIX}"):
        frame = p.with_name(p.name[: -len(TRASH_INFO_SUFFIX)])
        if not frame.exists():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
