"""PRO-1 master calibration library — scans CAPTURE_DIR, builds masters, matches.

The store mirrors ``plans.PlanLibrary`` (a manifest written atomically via
``persist.write_json_atomic``) and resolves ``CAPTURE_DIR`` LIVE through an
injected getter (never bound at import) so the test monkeypatch of
``hub.CAPTURE_DIR`` is honored, exactly like ``hub._counter_file``.

Bounded memory is real (this runs on a Pi): ``build_master_streamed`` memmaps
each source FITS and streams row-strips through the pure stacker, so peak memory
is ``strip_rows × width × n_frames × 4 B`` — independent of full-frame size —
rather than a naive ``np.stack`` of every full frame."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..events import bus
from ..gallery import THUMBS_DIRNAME, TRASH_DIRNAME
from ..persist import read_json_or, safe_id_path, write_json_atomic
from .keys import CAL_FRAME_TYPES, CalKey, key_from_header, key_index_id
from .matcher import Gap, LightNeed, MasterRecord, MatchTolerance, coverage_for
from .stacker import stack_frames

MASTERS_DIRNAME = "_masters"
MANIFEST_NAME = "masters.json"
#: Directories under the capture root that ``_bucket_raw`` must not walk.
#:
#: ``_trash`` is here for a correctness reason, not tidiness. The gallery deletes
#: a frame by RENAMING it into ``<CAPTURE_DIR>/_trash`` (inside the capture root
#: because a rename is only atomic on one volume), and this scanner rglobs EVERY
#: ``*.fits`` under that root. Without the exclusion, a flat or dark the user
#: deleted yesterday is still found here and stacked straight back into a master
#: — the frame is gone from the gallery, its bad data is not gone from the
#: calibration it feeds, and nothing anywhere reports a problem. A deletion that
#: does not take effect is worse than one that fails. Pinned by
#: ``tests/test_gallery.py::test_deleted_flat_does_not_reappear_in_a_master``.
#: The name is imported from ``gallery`` rather than repeated as a literal so the
#: two cannot drift.
EXCLUDE_DIRS = {MASTERS_DIRNAME, "_solve", TRASH_DIRNAME, THUMBS_DIRNAME}
MANIFEST_SCHEMA = 1

#: The card ``imaging.darks`` stamps on a calibration frame it judged.
#:
#: FALSE means the frame was measured and contradicted: a "dark" pinned at the
#: sensor ceiling, or full of stars — a light leak, a slot flagged opaque that
#: is not. Such a frame is skipped here. Subtracting a master built from white
#: frames does not merely degrade a light, it erases it.
#:
#: ABSENT means unjudged, and MUST read as usable. Every dark taken before the
#: check existed lacks the card, and defaulting the other way would silently
#: delete a working library on upgrade day.
DARK_OK_CARD = "DARKOK"

_MASTER_FIELDS = ("id", "frame_type", "exposure_s", "gain", "offset", "temp_c",
                  "binning", "filter", "frame_count", "path", "built_ts")


def _rejected_by_dark_check(header) -> bool:
    """True when this frame's header says the dark check CONTRADICTED it.

    Only an explicit false reads as a rejection. A missing card is an unjudged
    frame, not a bad one, and astropy gives a FITS logical card back as a
    ``bool`` — but a header hand-written by another tool may carry ``0`` or
    ``"F"``, so those are honoured too rather than silently passing through as
    truthy strings."""
    val = header.get(DARK_OK_CARD, True)
    if isinstance(val, str):
        return val.strip().upper() in ("F", "FALSE", "0", "NO")
    return val is False or val == 0


@dataclass(frozen=True)
class BuildReport:
    masters_built: int
    frames_indexed: int
    buckets: int


@dataclass
class _Bucket:
    key: CalKey
    paths: list[Path]


def _valid_row(r: object) -> bool:
    return isinstance(r, dict) and all(k in r for k in _MASTER_FIELDS)


def _write_master_fits(data: np.ndarray, out_path: Path, key: CalKey,
                       frame_count: int) -> None:
    from astropy.io import fits           # lazy (hub precedent)
    hdu = fits.PrimaryHDU(data.astype(np.float32))
    h = hdu.header
    h["IMAGETYP"] = f"Master {key.frame_type.title()}"
    h["EXPTIME"] = key.exposure_s
    h["GAIN"] = key.gain
    h["OFFSET"] = key.offset
    if key.temp_c is not None:
        h["CCD-TEMP"] = key.temp_c
    h["XBINNING"] = key.binning
    h["YBINNING"] = key.binning
    if key.filter:
        h["FILTER"] = key.filter
    h["NFRAMES"] = (frame_count, "source frames stacked")
    h["MASTER"] = (True, "AstroDeck master calibration frame")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(out_path, overwrite=True)


def build_master_streamed(paths: list[Path], out_path: Path, *, method: str,
                          sigma: float, max_frames: int, strip_rows: int,
                          key: CalKey, frame_count_hint: int | None = None) -> int:
    """Memmap sources, stack per row-strip (bounded memory), write a float32
    master with the key baked into the header. Returns frames used."""
    from astropy.io import fits
    if not paths:
        raise ValueError("no source frames for master")
    # Evenly subsample so a huge folder can't OOM the Pi (cap = max_frames).
    if len(paths) > max_frames:
        idx = np.linspace(0, len(paths) - 1, max_frames).astype(int)
        use = [paths[i] for i in idx]
    else:
        use = list(paths)
    # memmap=False (NOT a full-frame load): ``.section[y0:y1]`` reads ONLY that
    # row-strip's bytes from disk and applies BZERO/BSCALE. A memmap-open image
    # with BZERO/BSCALE (uint16 stored as int16+BZERO — every save_fits frame)
    # refuses to scale a section (``strict_memmap``), so we open unmapped and let
    # ``.section`` do bounded partial reads. Peak RAM = one strip × n_frames.
    hduls = [fits.open(p, memmap=False) for p in use]
    try:
        h, w = hduls[0][0].shape
        out = np.empty((h, w), dtype=np.float32)
        step = max(1, int(strip_rows))
        for y0 in range(0, h, step):
            y1 = min(y0 + step, h)
            strips = [np.asarray(hd[0].section[y0:y1], dtype=np.float32)
                      for hd in hduls]
            out[y0:y1] = stack_frames(strips, method=method, sigma=sigma)
    finally:
        for hd in hduls:
            hd.close()
    _write_master_fits(out, out_path, key, len(use))
    return len(use)


def _plan_needs(plan) -> list[LightNeed]:
    """De-duplicated LightNeed per distinct (exposure, gain, offset, binning,
    filter) across all NON-calibration targets; ``temp_c = plan.cool_to``."""
    temp = getattr(plan, "cool_to", None)
    seen: set[tuple] = set()
    needs: list[LightNeed] = []
    for tg in getattr(plan, "targets", []):
        if getattr(tg, "calibration", False):
            continue
        for s in tg.steps:
            filt = s.filter or ""
            sig = (s.exposure_s, s.gain, s.offset, s.binning, filt)
            if sig in seen:
                continue
            seen.add(sig)
            needs.append(LightNeed(exposure_s=s.exposure_s, gain=s.gain,
                                   offset=s.offset, temp_c=temp,
                                   binning=s.binning, filter=filt))
    return needs


class CalibrationLibrary:
    """uuid-free, key-derived master store under ``<CAPTURE_DIR>/_masters``.

    ``capture_dir`` is a getter (not a Path) so the store resolves the capture
    root LIVE on every call — the test monkeypatch of ``hub.CAPTURE_DIR`` and the
    real deploy both work without rebinding."""

    def __init__(self, capture_dir: Callable[[], Path]):
        self._capture_dir = capture_dir

    def masters_dir(self) -> Path:
        return self._capture_dir() / MASTERS_DIRNAME

    def _manifest_path(self) -> Path:
        return self.masters_dir() / MANIFEST_NAME

    def _bucket_raw(self, temp_bin_width: float
                    ) -> tuple[dict[str, _Bucket], list[tuple[Path, str]]]:
        """``(buckets, rejected)`` — the frames that will be stacked, and the
        ones the dark check contradicted, each with the evidence sentence off
        its own header. The rejects are RETURNED rather than dropped so the
        caller can say what it left out; a scanner that silently indexes fewer
        frames than the folder holds is how a bad library looks healthy."""
        from astropy.io import fits
        root = self._capture_dir()
        buckets: dict[str, _Bucket] = {}
        rejected: list[tuple[Path, str]] = []
        if not root.exists():
            return buckets, rejected
        for p in sorted(root.rglob("*.fits")):
            rel = p.relative_to(root)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            try:
                header = fits.getheader(p)
                key = key_from_header(header)
            except Exception:
                continue
            if key is None or key.frame_type not in CAL_FRAME_TYPES:
                continue
            # The dark check's verdict, read off the file — no extra I/O, since
            # the header is already open. See DARK_OK_CARD: absent = usable.
            if _rejected_by_dark_check(header):
                rejected.append((p, str(header.get("DARKWHY", "")).strip()))
                continue
            kid = key_index_id(key, temp_bin_width)
            b = buckets.get(kid)
            if b is None:
                buckets[kid] = _Bucket(key=key, paths=[p])
            else:
                b.paths.append(p)
        return buckets, rejected

    def scan_raw(self, temp_bin_width: float) -> dict[str, list[Path]]:
        buckets, _rejected = self._bucket_raw(temp_bin_width)
        return {kid: b.paths for kid, b in buckets.items()}

    def build(self, *, sigma: float = 3.0, temp_bin_width: float = 5.0,
              max_frames: int = 100, strip_rows: int = 64) -> BuildReport:
        buckets, rejected = self._bucket_raw(temp_bin_width)
        if rejected:
            # Named, not counted. "3 frames skipped" tells the operator nothing
            # they can act on; the evidence sentence off the frame's own header
            # is what identifies an empty slot ticked opaque.
            why = rejected[0][1] or "the dark check contradicted it"
            bus.log("warning",
                    f"{len(rejected)} calibration frame(s) left out of the "
                    f"masters because they are not darks — e.g. "
                    f"{rejected[0][0].name}: {why}", "calibration")
        records: list[MasterRecord] = []
        indexed = 0
        for kid, bucket in buckets.items():
            key = bucket.key
            # ENFORCEMENT, not belt-and-braces. `kid` carries a filter name that
            # came out of a FITS header this process did not necessarily write
            # (_bucket_raw rglobs every *.fits under the capture dir), and the
            # next two calls are mkdir(parents=True) + writeto(overwrite=True).
            # key_index_id now sanitizes that component, but the delete path a
            # few lines below has always routed through safe_id_path while this
            # one did not — the write side is the dangerous half, so it gets the
            # same guard. A refused bucket is skipped, never silently relocated.
            try:
                out = safe_id_path(self.masters_dir(), kid, ".fits")
            except KeyError:
                continue
            method = "median" if key.frame_type == "BIAS" else "sigma_clip"
            n = build_master_streamed(
                bucket.paths, out, method=method, sigma=sigma,
                max_frames=max_frames, strip_rows=strip_rows, key=key)
            indexed += len(bucket.paths)
            records.append(MasterRecord(
                id=kid, frame_type=key.frame_type, exposure_s=key.exposure_s,
                gain=key.gain, offset=key.offset, temp_c=key.temp_c,
                binning=key.binning, filter=key.filter, frame_count=n,
                path=str(out), built_ts=time.time()))
        self._save_manifest(records)
        return BuildReport(masters_built=len(records), frames_indexed=indexed,
                           buckets=len(buckets))

    def list_masters(self) -> list[MasterRecord]:
        raw = read_json_or(self._manifest_path(), {})
        rows = raw.get("masters", []) if isinstance(raw, dict) else []
        return [MasterRecord(**{k: r[k] for k in _MASTER_FIELDS})
                for r in rows if _valid_row(r)]

    def _save_manifest(self, records: list[MasterRecord]) -> None:
        write_json_atomic(self._manifest_path(), {
            "schema_version": MANIFEST_SCHEMA,
            "masters": [vars(r) for r in records]})

    def delete(self, master_id: str) -> None:
        path = safe_id_path(self.masters_dir(), master_id, ".fits")  # KeyError on escape
        if path.exists():
            path.unlink()
        self._save_manifest([m for m in self.list_masters() if m.id != master_id])

    def coverage(self, plan, tol: MatchTolerance, temp_bin_width: float) -> list[Gap]:
        masters = self.list_masters()
        needs = _plan_needs(plan)
        return coverage_for(needs, masters, tol)
