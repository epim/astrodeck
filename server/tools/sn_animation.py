"""One image per night from a target's captures, an animation of them, and a
relative light curve of a transient in the field.

Written for SN 2026aaiv in NGC 7331, which the rig re-shoots night after night.
It runs in daylight, reads the captures directory, writes beside it, and never
touches a device or the running server.

    python tools/sn_animation.py --captures "C:/.../captures/NGC 7331" \
        --bias  "C:/.../captures/Calibration" \
        --flats "C:/.../captures/Flats" \
        --sn-ra 339.2670 --sn-dec 34.4159

What it does, per night (local noon to local noon):

    read the night's subs in the chosen filter
    calibrate  (master bias, per-filter master flat, per-frame pedestal)
    register   every sub to the night's FIRST sub  (180-degree flip detected)
    register   that first sub to the FIRST NIGHT's first sub
    combine    a sigma-clipped mean over a window around the crop
    register   the night's stack to the first night's stack (residual)
    stretch    with the FIRST night's parameters, so nights are comparable
    crop, annotate, write <night>.png

then the GIF (and an MP4 when ffmpeg is on PATH), and — when the supernova's
RA/Dec is given and ASTAP solves the reference frame — lightcurve.csv and
lightcurve.png.

The reduction lives in ``astrodeck.imaging.nightstack``; this file is argument
parsing, orchestration and logging. See docs/sn-animation.md.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

# Run from a checkout without installing: server/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrodeck.imaging import nightstack as ns          # noqa: E402
from astrodeck.imaging.fitsio import write_wcs          # noqa: E402

#: Extra pixels kept around the final crop while stacking, so the residual
#: night-to-night registration has somewhere to shift into and so a sub that
#: drifted does not push its NaN border into the picture. 192 px is about
#: 3 arcminutes at this rig's 0.968 arcsec/px — comfortably more than the
#: 100-200 px of night-to-night centring difference that the night-reference
#: registration has already taken out.
STACK_MARGIN_PX = 192

#: Aperture and sky annulus for the relative photometry, in pixels. At
#: 0.968 arcsec/px an 8 px radius is ~7.7 arcsec, a few times the seeing disc.
DEFAULT_APERTURE_PX = 8.0
DEFAULT_ANNULUS_PX = (14.0, 24.0)


# --------------------------------------------------------------------------


@dataclass
class NightResult:
    night: date
    used: int
    dropped: int
    score_lo: float
    score_hi: float
    flipped: int
    crop: np.ndarray            # float32, the final window, linear counts


def parse_crop(text: str) -> tuple[int, int]:
    """``1600x1100`` -> ``(1100, 1600)`` — height, width, numpy order."""
    try:
        w, h = (int(v) for v in text.lower().split("x", 1))
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"--crop wants WIDTHxHEIGHT, got {text!r}")
    if w < 32 or h < 32:
        raise argparse.ArgumentTypeError("--crop is too small to be useful")
    return h, w


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sn_animation",
        description="Per-night stacks, an animation and a relative light curve.")
    p.add_argument("--captures", required=True, type=Path,
                   help="the TARGET's capture directory (holds the Light_*.fits)")
    p.add_argument("--filter", default="L", dest="filter_name",
                   help="single filter to reduce (default L; colour is not supported)")
    p.add_argument("--out", type=Path, default=None,
                   help="output directory (default <captures>/../_animation/<target>)")
    p.add_argument("--bias", type=Path, default=None, help="directory of bias frames")
    p.add_argument("--flats", type=Path, default=None, help="directory of flat frames")
    p.add_argument("--sn-ra", type=float, default=None,
                   help="transient RA in DEGREES (enables the light curve)")
    p.add_argument("--sn-dec", type=float, default=None,
                   help="transient Dec in DEGREES")
    p.add_argument("--comparison", type=int, default=5,
                   help="how many comparison stars to pick automatically")
    p.add_argument("--crop", type=parse_crop, default=(1100, 1600),
                   metavar="WIDTHxHEIGHT", help="crop size (default 1600x1100)")
    p.add_argument("--frame-ms", type=int, default=700,
                   help="milliseconds per animation frame")
    p.add_argument("--aperture", type=float, default=DEFAULT_APERTURE_PX,
                   help="photometry aperture radius in pixels")
    p.add_argument("--annulus", type=float, nargs=2, default=list(DEFAULT_ANNULUS_PX),
                   metavar=("INNER", "OUTER"), help="sky annulus radii in pixels")
    p.add_argument("--min-score", type=float, default=ns.MIN_REGISTRATION_SCORE,
                   help=f"drop a sub below this registration score "
                        f"(default {ns.MIN_REGISTRATION_SCORE})")
    p.add_argument("--bin", type=int, default=ns.DEFAULT_BIN, dest="bin_factor",
                   help="binning for the coarse correlation")
    p.add_argument("--min-frames", type=int, default=1,
                   help="skip a night with fewer usable subs than this")
    return p


# --------------------------------------------------------------------------
# Plate solving (the one place this tool shells out)
# --------------------------------------------------------------------------

def solve_reference(fits_path: Path, *, ra_hint: float | None,
                    dec_hint: float | None, fov_deg: float | None, log):
    """Plate-solve one FITS with ASTAP and stamp the WCS back into it.

    Solving the reference FRAME rather than the reference STACK is deliberate:
    every night is registered onto that frame's pixel grid, so its WCS is the
    stack's WCS, and it is an ordinary uint16 light of exactly the kind ASTAP
    already solves on this rig every night. Nothing has to be re-encoded and
    no assumption is made about which bit depths the solver accepts.

    Returns an ``astropy.wcs.WCS`` or None. Every failure is a None with a
    logged reason: no solver, no solve and no light curve are survivable; a
    crash in a daylight reduction is not.
    """
    from astrodeck.solve import AstapSolver, find_astap

    exe = find_astap()
    if not exe:
        log("ASTAP not found (set ASTAP_PATH); skipping the light curve")
        return None
    try:
        result = asyncio.run(AstapSolver(exe).solve(
            fits_path, ra_hint=ra_hint, dec_hint=dec_hint, fov_deg_hint=fov_deg))
    except Exception as exc:                       # pragma: no cover - defensive
        log(f"ASTAP raised {exc!r}; skipping the light curve")
        return None
    if not result.success or result.wcs is None:
        log(f"ASTAP did not solve {fits_path.name}: {result.message}")
        return None
    write_wcs(fits_path, result.wcs)
    from astropy.io import fits
    from astropy.wcs import WCS
    log(f"solved {fits_path.name}: {result.pixel_scale_arcsec:.3f} arcsec/px, "
        f"rotation {result.rotation_deg:.2f} deg")
    return WCS(fits.getheader(fits_path))


def _fov_hint(header, shape) -> float | None:
    """Field height in degrees from FOCALLEN and XPIXSZ, when both are there."""
    try:
        focal = float(header.get("FOCALLEN", 0) or 0)
        pixsz = float(header.get("XPIXSZ", 0) or 0)
    except (TypeError, ValueError):
        return None
    if focal <= 0 or pixsz <= 0:
        return None
    scale = 206.265 * pixsz / focal              # arcsec per pixel
    return scale * shape[0] / 3600.0


def _ra_dec_hint(header) -> tuple[float | None, float | None]:
    try:
        ra = header.get("RA", None)
        dec = header.get("DEC", None)
        if ra is None or dec is None:
            return None, None
        return float(ra), float(dec)
    except (TypeError, ValueError):
        return None, None


# --------------------------------------------------------------------------


def _expand(bounds: tuple[int, int, int, int], margin: int,
            shape: tuple[int, int]) -> tuple[int, int, int, int]:
    y0, x0, y1, x1 = bounds
    return (max(0, y0 - margin), max(0, x0 - margin),
            min(shape[0], y1 + margin), min(shape[1], x1 + margin))


def run(args, *, solve_fn=solve_reference, log=print) -> int:
    captures = Path(args.captures)
    frames, notes = ns.scan_frames(captures, filter_name=args.filter_name)
    for n in notes[:20]:
        log(f"note: {n}")
    if len(notes) > 20:
        log(f"note: ... and {len(notes) - 20} more skipped files")
    if not frames:
        log(f"no {args.filter_name} light frames under {captures}")
        return 2

    target = frames[0].target
    out_dir = Path(args.out) if args.out else \
        captures.parent / "_animation" / target
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{len(frames)} {args.filter_name} frames of {target} -> {out_dir}")

    by_night = ns.group_by_night(frames)
    log(f"{len(by_night)} nights: " +
        ", ".join(f"{d.isoformat()}({len(v)})" for d, v in by_night.items()))

    cal_notes: list[str] = []
    bias, flats = ns.load_masters(args.bias, args.flats, [args.filter_name],
                                  notes=cal_notes)
    for n in cal_notes:
        log(f"calibration: {n}")
    flat = flats.get(args.filter_name.upper())
    if bias is None and flat is None:
        log("calibration: none available; frames get a scalar pedestal only")

    # --- the global reference: the first night's first sub -----------------
    nights = list(by_night)
    ref_night = nights[0]
    ref_ref = by_night[ref_night][0]
    ref_raw, ref_header = ns.load_frame(ref_ref.path)
    ref_data = ns.calibrate(ref_raw, bias=bias, flat=flat)
    del ref_raw
    height, width = ref_data.shape
    log(f"reference frame {ref_ref.path.name} ({width}x{height})")

    # --- where the supernova is, if we can find out ------------------------
    sn_yx = None
    wcs = None
    if args.sn_ra is not None and args.sn_dec is not None:
        ref_copy = out_dir / "reference.fits"
        shutil.copyfile(ref_ref.path, ref_copy)
        ra_hint, dec_hint = _ra_dec_hint(ref_header)
        if ra_hint is None:
            ra_hint, dec_hint = args.sn_ra, args.sn_dec
        wcs = solve_fn(ref_copy, ra_hint=ra_hint, dec_hint=dec_hint,
                       fov_deg=_fov_hint(ref_header, ref_data.shape), log=log)
    if wcs is not None:
        try:
            x, y = wcs.wcs_world2pix([[args.sn_ra, args.sn_dec]], 0)[0]
        except Exception as exc:                   # pragma: no cover - defensive
            log(f"WCS could not place the transient ({exc!r})")
        else:
            if 0 <= x < width and 0 <= y < height:
                sn_yx = (float(y), float(x))
                log(f"transient at reference pixel x={x:.1f} y={y:.1f}")
            else:
                log(f"transient falls outside the reference frame "
                    f"(x={x:.1f} y={y:.1f}); no light curve")

    ch, cw = args.crop
    centre = sn_yx if sn_yx else (height / 2.0, width / 2.0)
    crop = ns.crop_bounds(ref_data.shape, centre[0], centre[1], ch, cw)
    stack_bounds = _expand(crop, STACK_MARGIN_PX, ref_data.shape)
    local = (crop[0] - stack_bounds[0], crop[1] - stack_bounds[1],
             crop[2] - stack_bounds[0], crop[3] - stack_bounds[1])
    log(f"crop {crop[3] - crop[1]}x{crop[2] - crop[0]} at "
        f"x={crop[1]}..{crop[3]} y={crop[0]}..{crop[2]}"
        + ("  (centred on the transient)" if sn_yx else "  (frame centre)"))

    # --- per night ---------------------------------------------------------
    results: list[NightResult] = []
    reference_stack = None
    for night in nights:
        night_frames = by_night[night]
        night_ref = night_frames[0]
        if night == ref_night:
            night_align = ns.Alignment(0, 0, float("inf"), False)
            night_ref_data = ref_data
        else:
            raw, _ = ns.load_frame(night_ref.path)
            night_ref_data = ns.calibrate(raw, bias=bias, flat=flat)
            del raw
            night_align = ns.register_translation(
                ref_data, night_ref_data, bin_factor=args.bin_factor)
            if night_align.score < args.min_score:
                log(f"{night} SKIPPED: its reference sub does not register "
                    f"against night one (score {night_align.score:.2f})")
                continue

        paths, aligns, scores, flips = [], [], [], 0
        dropped = 0
        for f in night_frames:
            if f is night_ref:
                own = ns.Alignment(0, 0, float("inf"), False)
            else:
                raw, _ = ns.load_frame(f.path)
                data = ns.calibrate(raw, bias=bias, flat=flat)
                del raw
                own = ns.register_translation(night_ref_data, data,
                                              bin_factor=args.bin_factor)
                del data
                if own.score < args.min_score:
                    dropped += 1
                    continue
                scores.append(own.score)
            combined = ns.compose(night_align, own)
            flips += int(combined.flipped)
            paths.append(f.path)
            aligns.append(combined)

        if len(paths) < max(1, args.min_frames):
            log(f"{night} SKIPPED: {len(paths)} usable subs "
                f"(min {max(1, args.min_frames)})")
            continue

        seq = ns.FitsFrames(paths, bias=bias, flat=flat)
        stack = ns.combine(seq, aligns, bounds=stack_bounds)
        if reference_stack is None:
            residual = ns.Alignment(0, 0, float("inf"), False)
            reference_stack = stack
        else:
            residual = ns.register_translation(
                ns.fill_nan(reference_stack), ns.fill_nan(stack),
                bin_factor=max(1, args.bin_factor // 2), try_flip=False)
            if residual.score < args.min_score:
                log(f"{night}: stack-to-stack residual not believed "
                    f"(score {residual.score:.2f}); using the frame alignment")
                residual = ns.Alignment(0, 0, residual.score, False)
        night_crop = ns.place_crop(stack, residual, local)

        lo = min(scores) if scores else float("nan")
        hi = max(scores) if scores else float("nan")
        span = "n/a (single sub)" if not scores else f"{lo:.1f}-{hi:.1f}"
        log(f"{night}: {len(paths)} subs used, {dropped} dropped for score, "
            f"score {span}, {flips} flipped, "
            f"night shift dy={night_align.dy} dx={night_align.dx}"
            f"{' FLIPPED' if night_align.flipped else ''}, "
            f"residual dy={residual.dy} dx={residual.dx}")
        gap = float(np.mean(~np.isfinite(night_crop)))
        if gap > 0.001:
            # The crop is a window on the FIRST night's pixel grid. A night
            # centred far enough away simply has no data over part of it, and
            # that reads as a black band in the PNG rather than as an error, so
            # say so here: the fix is a smaller --crop, not a bug report.
            log(f"{night}: WARNING {gap * 100:.1f}% of the crop is outside this "
                f"night's coverage (it will be black); use a smaller --crop")
        results.append(NightResult(night, len(paths), dropped, lo, hi, flips,
                                   night_crop))

    if not results:
        log("no night produced a stack")
        return 3

    # --- one stretch for every night ---------------------------------------
    params = ns.stretch_params(results[0].crop)
    log(f"shared stretch: black={params.black:.1f} white={params.white:.1f} "
        f"softening={params.softening:.0f} (from {results[0].night})")

    sn_local = None
    if sn_yx:
        sn_local = (sn_yx[0] - crop[0], sn_yx[1] - crop[1])

    marks = []
    if sn_local:
        marks.append(ns.Mark(sn_local[1], sn_local[0], 18.0, "SN"))

    from PIL import Image
    pngs, frames8 = [], []
    for r in results:
        img8 = ns.annotate(ns.stretch(r.crop, params),
                           f"{r.night.isoformat()}   {r.used} x {args.filter_name}",
                           marks)
        path = out_dir / f"{r.night.isoformat()}.png"
        Image.fromarray(img8, mode="RGB").save(path)
        pngs.append(path)
        frames8.append(img8)
    log(f"wrote {len(pngs)} night stacks")

    gif = ns.assemble_gif(frames8, out_dir / "animation.gif", args.frame_ms)
    log(f"wrote {gif}")
    mp4 = ns.assemble_mp4(frames8, out_dir / "animation.mp4", args.frame_ms)
    log(f"wrote {mp4}" if mp4 else
        "ffmpeg is not on PATH, so no MP4 was written (the GIF is complete)")

    # --- the light curve ---------------------------------------------------
    if sn_local is None:
        log("no transient position, so no light curve "
            "(pass --sn-ra/--sn-dec and make sure ASTAP is installed)")
        return 0

    comparisons = ns.find_comparison_stars(
        results[0].crop, args.comparison, aperture_px=args.aperture,
        annulus=tuple(args.annulus), exclude=[sn_local])
    if not comparisons:
        log("no usable comparison stars in the crop; no light curve")
        return 0
    log(f"{len(comparisons)} comparison stars, chosen on {results[0].night}: " +
        ", ".join(f"({x:.0f},{y:.0f})" for y, x in comparisons))

    rows = []
    for r in results:
        ph = ns.relative_flux(r.crop, sn_local[0], sn_local[1], comparisons,
                              aperture_px=args.aperture,
                              annulus=tuple(args.annulus))
        rows.append((r.night.isoformat(), r.used, ph.ratio, ph.error))
        log(f"{r.night}: ratio {ph.ratio:.5f} +- {ph.error:.5f} "
            f"({ph.n_comparison} comparisons)")

    csv_path = out_dir / "lightcurve.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["night", "n_frames", "ratio", "err"])
        for night, n, ratio, err in rows:
            w.writerow([night, n,
                        "" if not math.isfinite(ratio) else f"{ratio:.6g}",
                        "" if not math.isfinite(err) else f"{err:.6g}"])
    log(f"wrote {csv_path}")

    plot = ns.plot_lightcurve([(n, ratio, err) for n, _, ratio, err in rows],
                              out_dir / "lightcurve.png",
                              title=f"{target} relative flux ({args.filter_name})")
    log(f"wrote {plot}")
    return 0


def main(argv=None, *, solve_fn=solve_reference, log=print) -> int:
    args = build_parser().parse_args(argv)
    return run(args, solve_fn=solve_fn, log=log)


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
