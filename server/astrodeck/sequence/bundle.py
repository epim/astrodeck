"""PRO-10 stacker-ready interop export bundle — pure core (NO I/O).

Turns a finished :class:`~astrodeck.sequence.report.SessionReport` into a small,
honest *stacking bundle*: light subs grouped by target/filter/exposure/gain/
binning, each group paired with its matched master calibration (from PRO-1's
library, via a thin adapter), a per-sub weighting manifest, and a generated
one-click build script. Everything here is pure and deterministic — the route
(`api/app.py`) does all disk/zip I/O; ``is_local`` (the CAPTURE_DIR security
guard) is injected so the selection stays testable without a filesystem.

Design: ``docs/superpowers/specs/2026-07-23-export-bundles-design.md``.
"""
from __future__ import annotations

import csv
import io
import math
import shlex
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Protocol, runtime_checkable

from .report import FrameRecord, SessionReport, _is_light
from ..naming import sanitize_component

# The calibration kinds we ask the master library for, in a fixed display order.
_MASTER_KINDS: tuple[str, ...] = ("Dark", "Flat", "Bias")

#: Folder layouts the bundle knows how to emit (PRO-10 enrichment (b)).
#: ``grouped`` is v1 and is byte-for-byte unchanged; ``siril``/``app`` only
#: change the *relative dest strings* — the build script just replays them.
LAYOUTS: tuple[str, ...] = ("grouped", "siril", "app")

#: Siril idiom: lower-case plural sibling dirs next to ``lights/``.
_SIRIL_MASTER_DIR: dict[str, str] = {"Dark": "darks", "Flat": "flats",
                                     "Bias": "biases"}

#: Estimated-FWHM conversion factor (PRO-10 enrichment (c)). AstroDeck measures
#: **HFR**, never a fitted PSF FWHM, so the exported column is named ``fwhm_est``
#: and this constant is the honest first-order relation for round stars
#: (FWHM ~= 2*HFR). The exact factor depends on the PSF shape and sampling —
#: that uncertainty is precisely why the column carries the ``_est`` suffix and a
#: README/manifest caveat. When true FWHM is measured upstream, emit a real
#: ``fwhm`` column and retire this one.
HFR_TO_FWHM_K = 2.0


# --------------------------------------------------------------------- models

@dataclass(frozen=True)
class CalibKey:
    """What the master library is asked to match for one group + kind."""
    frame_type: str            # "Dark" | "Flat" | "Bias"
    exposure_s: float
    gain: int | None
    offset: int | None
    binning: int | None
    filter: str | None
    temp_c: float | None


@runtime_checkable
class MasterLibrary(Protocol):
    """PRO-1's contract as PRO-10 consumes it: given a :class:`CalibKey`, return
    the abs path of the best-matching master, or ``None``. The real PRO-1 object
    is bound through :class:`CalibrationLibraryAdapter`; :class:`NullMasterLibrary`
    stands in when no library is configured."""

    def match(self, key: CalibKey) -> str | None: ...


class NullMasterLibrary:
    """No-library stand-in — always ``None`` (bundle still groups + weights)."""

    def match(self, key: CalibKey) -> str | None:  # noqa: ARG002 - protocol shape
        return None


@dataclass(frozen=True)
class LightEntry:
    src: str                   # abs source path
    dest: str                  # bundle-relative dest path
    ts: float
    accepted: bool
    hfr: float | None
    ecc: float | None
    guide_rms: float | None
    sensor_temp_c: float | None
    altitude_deg: float | None
    weight: float              # 0..1, group-normalized (best sub = 1.0)
    #: ESTIMATED FWHM = HFR_TO_FWHM_K * hfr (we do not measure PSF FWHM).
    fwhm_est: float | None = None
    #: Advisory ``keep_threshold`` verdict. False = "this sub is in the
    #: relatively-worst tail of its group". NEVER removes the sub from the
    #: bundle or the build script — see ``build_bundle``.
    keep: bool = True


@dataclass(frozen=True)
class Group:
    dir: str                   # "<TARGET>/<FILTER>/<EXP>s_g<GAIN>_bin<BIN>"
    target: str
    filter: str | None
    exposure_s: float
    gain: int | None
    binning: int | None
    lights: tuple[LightEntry, ...]
    masters: dict[str, str]        # kind.lower() -> bundle-relative dest
    master_sources: dict[str, str] # kind.lower() -> abs source path
    missing_masters: tuple[str, ...]  # title-case kinds with no match


@dataclass(frozen=True)
class Bundle:
    report_id: str
    plan_name: str
    layout: str
    groups: tuple[Group, ...]
    warnings: tuple[str, ...]
    weight_altitude: bool = False  # was the opt-in sin(alt) term folded in?
    #: Normalized-weight cutoff in [0,1] the caller asked for (None = no tail
    #: flagging; every light keeps ``keep=True``).
    keep_threshold: float | None = None


# --------------------------------------------------- PRO-1 library adapter

class CalibrationLibraryAdapter:
    """Adapts PRO-1's :class:`~astrodeck.calibration.library.CalibrationLibrary`
    (which exposes ``list_masters()`` + a ``best_master`` matcher, NOT a
    ``match(key)`` method) to PRO-10's :class:`MasterLibrary` Protocol.

    Kept here (only calls ``list_masters()``) so it is unit-testable against a
    fake lib without any filesystem. PRO-1 is frozen — this is the single, thin
    integration seam. Mapping rules (design §"integration seam"):

    * bundle ``CalibKey.frame_type`` is title-case ("Dark"/"Flat"/"Bias"); PRO-1
      frame types are upper-case — we upper-case on the way in.
    * ``filter`` is "" for Dark/Bias (only Flat keys on filter).
    * Dark/Flat delegate to PRO-1's :func:`best_master` (returns the record; we
      take ``.path``). ``best_master`` has no BIAS predicate, so Bias does a
      direct best-effort scan (exact gain/offset/binning, temp within
      ``MatchTolerance().temp_tol_c``, nearest temp wins)."""

    def __init__(self, lib) -> None:
        self._lib = lib

    def match(self, key: CalibKey) -> str | None:
        try:
            from ..calibration.matcher import (LightNeed, MatchTolerance,
                                               best_master)
        except Exception:
            return None
        ft = (key.frame_type or "").upper()          # DARK | FLAT | BIAS
        try:
            masters = self._lib.list_masters()
        except Exception:
            return None
        if not masters:
            return None
        if ft == "BIAS":
            return self._match_bias(key, masters, MatchTolerance())
        if ft not in ("DARK", "FLAT"):
            return None
        # Flat keys on filter; Dark/Bias ignore it (design: filter "" for those).
        filt = (key.filter or "") if ft == "FLAT" else ""
        need = LightNeed(exposure_s=key.exposure_s, gain=key.gain,
                         offset=key.offset, temp_c=key.temp_c,
                         binning=key.binning, filter=filt)
        try:
            rec = best_master(need, masters, MatchTolerance(), ft)
        except Exception:
            return None
        return rec.path if rec is not None else None

    @staticmethod
    def _match_bias(key: CalibKey, masters, tol) -> str | None:
        """Nearest-temp BIAS with exact gain/offset/binning (best_master has no
        BIAS predicate). ``None`` temps pass the tolerance check."""
        best = None
        best_dtemp = None
        for m in masters:
            if getattr(m, "frame_type", None) != "BIAS":
                continue
            if m.gain != key.gain or m.offset != key.offset or m.binning != key.binning:
                continue
            if key.temp_c is None or m.temp_c is None:
                dtemp = 0.0
            else:
                dtemp = abs(key.temp_c - m.temp_c)
                if dtemp > tol.temp_tol_c:
                    continue
            if best is None or dtemp < best_dtemp:
                best, best_dtemp = m, dtemp
        return best.path if best is not None else None


# --------------------------------------------------------------- weighting

def sub_weight(hfr: float | None, ecc: float | None, rms: float | None, *,
               min_hfr: float | None, min_rms: float | None,
               altitude_deg: float | None = None,
               weight_altitude: bool = False) -> float:
    """Raw 0..1 quality score for one sub (higher = better), the mean of the
    available per-metric sub-scores where 1 = best-in-group:

    * ``s_hfr = min_hfr / hfr``  (smaller HFR is sharper)
    * ``s_ecc = 1 - ecc``        (ecc already 0 = round = best)
    * ``s_rms = min_rms / rms``  (smaller guide RMS is better)
    * ``s_alt = sin(altitude)``  (OPT-IN, ``weight_altitude``; transparency
      proxy — 1 at zenith, 0 at/below the horizon, since a higher sub sees
      through less air). Off by default: v1 ships the honest sharpness/
      roundness/RMS weight and exports altitude informational-only (§4
      decision 2); this is the documented follow-up term.

    A sub with NO usable metric scores a neutral ``1.0`` (never penalized to 0
    for being un-measured)."""
    scores: list[float] = []
    if hfr is not None and min_hfr is not None and hfr > 0:
        scores.append(min(1.0, min_hfr / hfr))
    if ecc is not None:
        scores.append(max(0.0, min(1.0, 1.0 - ecc)))
    if rms is not None and min_rms is not None and rms > 0:
        scores.append(min(1.0, min_rms / rms))
    if weight_altitude and altitude_deg is not None:
        scores.append(max(0.0, min(1.0, math.sin(math.radians(altitude_deg)))))
    return sum(scores) / len(scores) if scores else 1.0


# ----------------------------------------------------------------- builder

def _group_dir(target: str, filt: str | None, exp: float,
               gain: int | None, binning: int | None) -> str:
    """Relative group directory, every component path-sanitized so a crafted
    target/filter can never escape (reuses the naming sanitizer)."""
    t = sanitize_component(target or "Unknown", "loose") or "Unknown"
    f = sanitize_component(filt or "NoFilter", "strict") or "NoFilter"
    g = gain if gain is not None else "NA"
    b = binning if binning is not None else "NA"
    leaf = f"{exp:g}s_g{g}_bin{b}"
    return f"{t}/{f}/{leaf}"


def _median_or_none(values: list[float]) -> float | None:
    return float(median(values)) if values else None


# ------------------------------------------------------------ layout variants

def _lights_dir(layout: str, gdir: str) -> str:
    """The per-group directory light subs land in, per layout.

    * ``grouped`` / ``siril`` — ``<group>/lights`` (Siril's own idiom).
    * ``app`` — ``<group>/Light`` (AstroPixelProcessor groups by capitalized
      frame *type*)."""
    return f"{gdir}/Light" if layout == "app" else f"{gdir}/lights"


def _light_dest(layout: str, gdir: str, name: str) -> str:
    """Bundle-relative dest for one light sub. ``name`` is already a bare
    filename (``Path(...).name``), so no component can escape."""
    return f"{_lights_dir(layout, gdir)}/{name}"


def _master_dest(layout: str, gdir: str, kind: str) -> str:
    """Bundle-relative dest for one master, per layout (``kind`` title-case).

    * ``grouped`` — one SHARED top-level ``masters/master<Kind>.fits`` (v1).
    * ``siril``   — PER-GROUP ``<group>/darks|flats|biases/master<Kind>.fits``
      so Siril's per-sequence calibration finds it beside ``lights/``.
    * ``app``     — PER-GROUP ``<group>/Dark|Flat|Bias/master<Kind>.fits``.

    Per-group layouts intentionally write one copy per group: each group's
    calibration is independent, and the dedup concern only ever applied to
    grouped's single shared path."""
    if layout == "siril":
        return f"{gdir}/{_SIRIL_MASTER_DIR[kind]}/master{kind}.fits"
    if layout == "app":
        return f"{gdir}/{kind}/master{kind}.fits"
    return f"masters/master{kind}.fits"


# ------------------------------------------------------------------ fwhm est

def fwhm_from_hfr(hfr: float | None, k: float = HFR_TO_FWHM_K) -> float | None:
    """ESTIMATED FWHM from a measured HFR (``None`` in -> ``None`` out).

    This is ``k * hfr``, NOT a fitted PSF FWHM — AstroDeck has no FWHM producer.
    Exported as ``fwhm_est`` with an explicit caveat so nobody keys a
    SubframeSelector expression on it believing it was measured."""
    if hfr is None:
        return None
    try:
        return round(float(hfr) * float(k), 4)
    except (TypeError, ValueError):
        return None


def build_bundle(report: SessionReport, library: MasterLibrary, *,
                 is_local: Callable[[str], bool],
                 layout: str = "grouped",
                 weight_altitude: bool = False,
                 keep_threshold: float | None = None) -> Bundle:
    """Build a :class:`Bundle` from a finished report + a master library.

    1. Select light subs with a truthy, ``is_local`` ``saved_path``.
    2. Group by (target, filter, exposure, gain, binning).
    3. Weight each sub within its group and normalize so the best sub = 1.0.
       ``weight_altitude`` (opt-in, off by default) folds a ``sin(alt)``
       transparency term into that weight (§4 decision 2 follow-up).
    4. Match a Dark/Flat/Bias master per group via ``library.match``.

    ``layout`` (one of :data:`LAYOUTS`) only changes the relative dest strings.
    ``keep_threshold`` is an optional normalized-weight cutoff in ``[0,1]``:
    a sub whose (already group-normalized, best=1.0) weight is BELOW it gets
    ``keep=False``. That flag is **advisory metadata only** — it never removes a
    sub from the bundle, the manifest, or the build script; the user's stacker
    decides. ``None`` (default) keeps every sub, i.e. today's behavior.

    Raises ``ValueError`` on an unknown layout or an out-of-range threshold
    (the route maps that to 400).

    Pure + deterministic (groups + subs keep report order); no numpy, O(subs)."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r} — expected one of "
                         + ", ".join(LAYOUTS))
    if keep_threshold is not None:
        try:
            keep_threshold = float(keep_threshold)
        except (TypeError, ValueError):
            raise ValueError("keep_threshold must be a number in [0, 1]")
        if not (0.0 <= keep_threshold <= 1.0) or keep_threshold != keep_threshold:
            raise ValueError("keep_threshold must be in [0, 1] "
                             "(weights are group-normalized, best = 1.0)")
    warnings: list[str] = []
    if isinstance(library, NullMasterLibrary):
        warnings.append("No master library configured — masters were not matched.")

    # 1 + 2: select locals, group in first-seen order.
    grouped: dict[tuple, list[FrameRecord]] = {}
    order: list[tuple] = []
    for fr in report.frames:
        if not _is_light(fr.frame_type):
            continue
        if not fr.saved_path or not is_local(fr.saved_path):
            continue
        key = (fr.target, fr.filter, fr.exposure_s, fr.gain, fr.binning)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(fr)

    groups: list[Group] = []
    for key in order:
        target, filt, exp, gain, binning = key
        frames = grouped[key]

        hfrs = [f.hfr for f in frames if f.hfr is not None and f.hfr > 0]
        rmss = [f.guide_rms_total for f in frames
                if f.guide_rms_total is not None and f.guide_rms_total > 0]
        min_hfr = min(hfrs) if hfrs else None
        min_rms = min(rmss) if rmss else None

        raw = [sub_weight(f.hfr, f.ecc, f.guide_rms_total,
                          min_hfr=min_hfr, min_rms=min_rms,
                          altitude_deg=f.altitude_deg,
                          weight_altitude=weight_altitude) for f in frames]
        max_raw = max(raw) if raw else 0.0
        norm = [(w / max_raw) if max_raw > 0 else 1.0 for w in raw]

        gdir = _group_dir(target, filt, exp, gain, binning)
        lights: list[LightEntry] = []
        for fr, w in zip(frames, norm):
            dest = _light_dest(layout, gdir, Path(fr.saved_path).name)
            wr = round(w, 4)
            lights.append(LightEntry(
                src=fr.saved_path, dest=dest, ts=fr.ts, accepted=fr.accepted,
                hfr=fr.hfr, ecc=fr.ecc, guide_rms=fr.guide_rms_total,
                sensor_temp_c=fr.sensor_temp_c, altitude_deg=fr.altitude_deg,
                weight=wr, fwhm_est=fwhm_from_hfr(fr.hfr),
                # Threshold applies WITHIN the group, after normalization, so it
                # culls the relatively-worst subs of this stack rather than
                # comparing across dissimilar filters/exposures.
                keep=(keep_threshold is None or wr >= keep_threshold)))

        # Representative group values for the calibration key.
        temp_c = _median_or_none([f.sensor_temp_c for f in frames
                                  if f.sensor_temp_c is not None])
        offset = next((f.offset for f in frames if f.offset is not None), None)

        masters: dict[str, str] = {}
        master_sources: dict[str, str] = {}
        missing: list[str] = []
        for kind in _MASTER_KINDS:
            ck = CalibKey(frame_type=kind, exposure_s=exp, gain=gain,
                          offset=offset, binning=binning, filter=filt,
                          temp_c=temp_c)
            path = None
            try:
                path = library.match(ck)
            except Exception:
                path = None
            if path:
                masters[kind.lower()] = _master_dest(layout, gdir, kind)
                master_sources[kind.lower()] = path
            else:
                missing.append(kind)

        groups.append(Group(
            dir=gdir, target=target, filter=filt, exposure_s=exp, gain=gain,
            binning=binning, lights=tuple(lights), masters=masters,
            master_sources=master_sources, missing_masters=tuple(missing)))

    return Bundle(report_id=report.id, plan_name=report.plan_name, layout=layout,
                  groups=tuple(groups), warnings=tuple(warnings),
                  weight_altitude=weight_altitude,
                  keep_threshold=keep_threshold)


# --------------------------------------------------------------- serializers

def manifest_json(bundle: Bundle) -> dict:
    """Full manifest with per-light rows — the authoritative bundle description."""
    return {
        "report_id": bundle.report_id,
        "plan_name": bundle.plan_name,
        "layout": bundle.layout,
        "weight_altitude": bundle.weight_altitude,
        "keep_threshold": bundle.keep_threshold,
        # Stated in the machine-readable manifest too, not just the README, so a
        # downstream script can never mistake fwhm_est for a measured PSF FWHM.
        "fwhm_est_note": (f"fwhm_est = {HFR_TO_FWHM_K:g} * hfr — ESTIMATED from the "
                          "measured HFR, not a fitted PSF FWHM"),
        "keep_note": ("keep=false is advisory only (weight < keep_threshold); "
                      "no sub is removed from the bundle or the build script"),
        "warnings": list(bundle.warnings),
        "groups": [
            {
                "dir": g.dir,
                "target": g.target,
                "filter": g.filter,
                "exposure_s": g.exposure_s,
                "gain": g.gain,
                "binning": g.binning,
                "kept_count": sum(1 for l in g.lights if l.keep),
                "masters": dict(g.masters),
                "missing_masters": list(g.missing_masters),
                "lights": [
                    {
                        "src": l.src,
                        "dest": l.dest,
                        "ts": l.ts,
                        "accepted": l.accepted,
                        "keep": l.keep,
                        "hfr": l.hfr,
                        "fwhm_est": l.fwhm_est,
                        "ecc": l.ecc,
                        "guide_rms": l.guide_rms,
                        "sensor_temp_c": l.sensor_temp_c,
                        "altitude_deg": l.altitude_deg,
                        "weight": l.weight,
                    }
                    for l in g.lights
                ],
            }
            for g in bundle.groups
        ],
    }


def bundle_summary(bundle: Bundle) -> dict:
    """Slim per-group summary for the UI preview — NO per-light rows, so a
    2000-frame report still yields a small JSON."""
    return {
        "report_id": bundle.report_id,
        "plan_name": bundle.plan_name,
        "layout": bundle.layout,
        "weight_altitude": bundle.weight_altitude,
        "keep_threshold": bundle.keep_threshold,
        "warnings": list(bundle.warnings),
        "groups": [
            {
                "dir": g.dir,
                "target": g.target,
                "filter": g.filter,
                "exposure_s": g.exposure_s,
                "gain": g.gain,
                "binning": g.binning,
                "light_count": len(g.lights),
                "accepted_count": sum(1 for l in g.lights if l.accepted),
                # One scalar per group instead of a 2000-row weight vector, so
                # the UI can say "38 of 42 kept" without the client recomputing.
                "kept_count": sum(1 for l in g.lights if l.keep),
                "masters": {
                    "dark": "dark" in g.masters,
                    "flat": "flat" in g.masters,
                    "bias": "bias" in g.masters,
                },
            }
            for g in bundle.groups
        ],
    }


_CSV_COLS = ["target", "filter", "exposure_s", "gain", "binning", "accepted",
             "keep", "hfr", "fwhm_est", "ecc", "guide_rms", "sensor_temp_c",
             "altitude_deg", "weight", "dest", "src"]


def weights_csv(bundle: Bundle) -> str:
    """One row per light (SubframeSelector/Siril-friendly); blank for ``None``."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLS)
    for g in bundle.groups:
        for l in g.lights:
            row = {
                "target": g.target,
                "filter": g.filter,
                "exposure_s": g.exposure_s,
                "gain": g.gain,
                "binning": g.binning,
                "accepted": l.accepted,
                "keep": l.keep,
                "hfr": l.hfr,
                "fwhm_est": l.fwhm_est,
                "ecc": l.ecc,
                "guide_rms": l.guide_rms,
                "sensor_temp_c": l.sensor_temp_c,
                "altitude_deg": l.altitude_deg,
                "weight": l.weight,
                "dest": l.dest,
                "src": l.src,
            }
            w.writerow(["" if row[c] is None else row[c] for c in _CSV_COLS])
    return buf.getvalue()


def _layout_tree_lines(layout: str) -> list[str]:
    """The illustrative tree for one layout (pure strings, no new data)."""
    g = "<TARGET>/<FILTER>/<EXP>s_g<GAIN>_bin<BIN>"
    if layout == "siril":
        return [f"{g}/lights/*.fits",
                f"{g}/darks/masterDark.fits   {g}/flats/masterFlat.fits",
                f"{g}/biases/masterBias.fits",
                "(masters sit beside lights/ so Siril's per-sequence calibration finds them)"]
    if layout == "app":
        return [f"{g}/Light/*.fits",
                f"{g}/Dark/masterDark.fits   {g}/Flat/masterFlat.fits",
                f"{g}/Bias/masterBias.fits",
                "(APP groups by capitalized frame type; masters live with the group)"]
    return [f"{g}/lights/*.fits",
            "masters/masterDark.fits  masters/masterFlat.fits  ...",
            "(one shared masters/ folder at the top level)"]


def readme_text(bundle: Bundle) -> str:
    """Human-readable orientation: what the bundle is + how to materialize it."""
    lines: list[str] = []
    lines.append(f"AstroDeck stacking bundle — {bundle.plan_name or bundle.report_id}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(
        "This bundle describes how to lay out your light subs and matched master")
    lines.append(
        "calibration frames for stacking (PixInsight / Siril / APP). It does NOT")
    lines.append(
        "contain the FITS themselves — run build.sh (macOS/Linux) or build.ps1")
    lines.append(
        "(Windows) from an empty folder and it copies your existing captures into")
    lines.append(f"a clean '{bundle.layout}' tree:")
    lines.append("")
    for tline in _layout_tree_lines(bundle.layout):
        lines.append("  " + tline)
    lines.append("")
    lines.append("Files:")
    lines.append("  manifest.json  full per-sub detail (paths, metrics, weights)")
    lines.append("  weights.csv    one row per sub — feed to SubframeSelector/Siril")
    lines.append("  build.sh /.ps1 materialize the tree from your captures")
    lines.append("")
    lines.append("Columns worth knowing:")
    lines.append(f"  fwhm_est  ESTIMATED FWHM = {HFR_TO_FWHM_K:g} x hfr. AstroDeck measures HFR,")
    lines.append("            NOT a fitted PSF FWHM — the exact factor depends on your PSF")
    lines.append("            and sampling. Use 'hfr' as the primary sharpness column; treat")
    lines.append("            fwhm_est as a convenience for tools that want FWHM-ish numbers.")
    if bundle.keep_threshold is None:
        lines.append("  keep      always true here (no keep_threshold was requested).")
    else:
        lines.append(f"  keep      false when a sub's normalized weight < {bundle.keep_threshold:g}")
        lines.append("            (weights are per-group, best sub = 1.0). This is ADVISORY:")
        lines.append("            NOTHING is deleted and the build script still lays out every")
        lines.append("            sub. Filter on it in your stacker if you want the tail gone.")
    lines.append("")
    lines.append("If AstroDeck materialized this tree for you (POST .../bundle/materialize),")
    lines.append("the exported files are HARDLINKS to your originals wherever the filesystem")
    lines.append("allows it — they share one inode, so editing an exported FITS in place also")
    lines.append("changes the original. Treat the export tree as read-only. (Where a hardlink")
    lines.append("isn't possible — a different filesystem — a real copy is made instead.)")
    lines.append("")
    if bundle.warnings:
        lines.append("Warnings:")
        for wmsg in bundle.warnings:
            lines.append(f"  - {wmsg}")
        lines.append("")
    lines.append("Groups:")
    if not bundle.groups:
        lines.append("  (none — no local light subs were available to bundle)")
    for g in bundle.groups:
        matched = ", ".join(sorted(g.masters)) or "none"
        missing = ", ".join(g.missing_masters) or "none"
        kept = sum(1 for l in g.lights if l.keep)
        subs = (f"{len(g.lights)} subs" if bundle.keep_threshold is None
                else f"{len(g.lights)} subs, {kept} kept")
        lines.append(f"  {g.dir}  ({subs})  "
                     f"masters: {matched}  missing: {missing}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------- materialize plan

@dataclass(frozen=True)
class MaterializeItem:
    """One planned (src -> dest) placement for the materialize route."""
    src: str                   # abs source path (provably under CAPTURE_DIR for
                               # lights — build_bundle's ``is_local`` filter; a
                               # master may live in a shared library elsewhere)
    dest: str                  # ABSOLUTE destination, already containment-checked
    group_dir: str             # the bundle group this item belongs to
    kind: str                  # "light" | "dark" | "flat" | "bias"
    refused: str | None = None #: non-None => MUST NOT be written (escape guard)


def bundle_materialize_plan(bundle: Bundle,
                            root: str | Path) -> tuple[MaterializeItem, ...]:
    """Plan the on-disk layout of a bundle under ``root`` — pure, no writes.

    SECURITY (design R1, the highest-severity risk in this feature): the route
    that consumes this plan writes to the capture box's filesystem, so an
    arbitrary-write must be impossible. Two independent guards:

    1. Every ``dest`` here is built by joining ``root`` (which the route derives
       from ``CAPTURE_DIR / "exports" / _slug(report_id)`` — a sanitized slug,
       NEVER the raw path parameter) with a bundle-relative path whose every
       component came out of ``sanitize_component`` / ``Path(...).name``.
    2. Defense in depth, we do not trust (1): each joined path is resolved and
       re-checked for containment under ``root``. Anything that escapes is
       returned with ``refused`` set and the caller must skip it — we return it
       rather than dropping it silently so the route can report the refusal.

    ``Path.resolve()`` is the one filesystem-touching call in this module; it is
    deliberate — normalizing symlinks is exactly what makes the containment check
    meaningful, and it mirrors ``hub._is_local_save``."""
    base = Path(root)
    try:
        base_res = base.resolve()
    except (OSError, ValueError):        # pragma: no cover - pathological root
        base_res = base

    def _item(src: str, rel: str, group_dir: str, kind: str) -> MaterializeItem:
        refused: str | None = None
        try:
            dest = (base / rel).resolve()
            if not dest.is_relative_to(base_res):
                refused = f"destination escapes the export directory: {rel!r}"
                dest = base_res
        except (OSError, ValueError) as e:
            refused = f"unusable destination {rel!r}: {e}"
            dest = base_res
        return MaterializeItem(src=src, dest=str(dest), group_dir=group_dir,
                               kind=kind, refused=refused)

    items: list[MaterializeItem] = []
    for g in bundle.groups:
        for l in g.lights:
            items.append(_item(l.src, l.dest, g.dir, "light"))
        for kind_lc, rel in g.masters.items():
            src = g.master_sources.get(kind_lc)
            if not src:
                continue
            items.append(_item(src, rel, g.dir, kind_lc))
    return tuple(items)


# ---------------------------------------------------------------- build script

def build_script(bundle: Bundle, shell: str = "sh") -> str:
    """Generate an injection-safe build script (``shell`` in {"sh","ps1"}).

    Every source/dest path is a filename the user chose — potentially adversarial
    (spaces, ``;``, ``$(...)``, backticks, quotes, ``& rmdir``, newlines). It is
    ALWAYS quoted/escaped so it can only ever be a literal path argument, never a
    command: POSIX via :func:`shlex.quote`; PowerShell via single-quoted literals
    with ``'`` doubled and ``-LiteralPath`` (no wildcard/variable expansion)."""
    if shell == "ps1":
        return _build_ps1(bundle)
    return _build_sh(bundle)


def _build_sh(bundle: Bundle) -> str:
    q = shlex.quote
    out: list[str] = ["#!/bin/sh"]
    out.append("# AstroDeck stacking bundle — materialize lights + masters here.")
    out.append("# Every path below is shell-quoted; run from an empty directory.")
    out.append("set -eu")
    out.append("")
    for g in bundle.groups:
        out.append(f"# --- {g.dir}")
        out.append(f"mkdir -p {q(_lights_dir(bundle.layout, g.dir))}")
        for l in g.lights:
            out.append(f"cp -- {q(l.src)} {q(l.dest)}")
        for kind_lc, dest in g.masters.items():
            src = g.master_sources.get(kind_lc)
            if not src:
                continue
            out.append(f"mkdir -p {q(str(Path(dest).parent))}")
            out.append(f"cp -- {q(src)} {q(dest)}")
        out.append("")
    return "\n".join(out)


def _ps1_lit(value: str) -> str:
    """A PowerShell single-quoted string literal: only ``'`` is special inside,
    escaped by doubling. Nothing else (``$``, backtick, ``;``, newline, ...) is
    interpreted in a single-quoted literal, so this is injection-safe."""
    return "'" + value.replace("'", "''") + "'"


def _build_ps1(bundle: Bundle) -> str:
    out: list[str] = ["# PowerShell — AstroDeck stacking bundle."]
    out.append("# Every path is a single-quoted literal (-LiteralPath); "
               "run from an empty directory.")
    out.append("$ErrorActionPreference = 'Stop'")
    out.append("")
    for g in bundle.groups:
        out.append(f"# --- {g.dir}")
        out.append("New-Item -ItemType Directory -Force -Path "
                   f"{_ps1_lit(_lights_dir(bundle.layout, g.dir))} | Out-Null")
        for l in g.lights:
            out.append(f"Copy-Item -LiteralPath {_ps1_lit(l.src)} "
                       f"-Destination {_ps1_lit(l.dest)}")
        for kind_lc, dest in g.masters.items():
            src = g.master_sources.get(kind_lc)
            if not src:
                continue
            out.append("New-Item -ItemType Directory -Force -Path "
                       f"{_ps1_lit(str(Path(dest).parent))} | Out-Null")
            out.append(f"Copy-Item -LiteralPath {_ps1_lit(src)} "
                       f"-Destination {_ps1_lit(dest)}")
        out.append("")
    return "\n".join(out)
