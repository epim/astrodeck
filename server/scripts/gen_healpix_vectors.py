"""Emit golden HEALPix vectors from astropy-healpix (server truth) for the TS
port's tsx test (tile-engine spec §2). Run from server/:

    ./.venv/Scripts/python.exe scripts/gen_healpix_vectors.py

Writes ui/src/lib/__tests__/healpix.vectors.json (committed). The (u,v)->sky
convention is pinned to hips_local.py (column<-odd bits, row<-even bits, no
vertical flip; lines 32-33, 52-59, 106-107, 115): u runs along columns (odd
bits -> iy -> dy), v along rows (even bits -> ix -> dx). So healpix_to_lonlat
is called with dx=v, dy=u.
"""
from __future__ import annotations

import json
from pathlib import Path

import astropy.units as u
from astropy_healpix import HEALPix, healpix_to_lonlat

OUT = (Path(__file__).resolve().parents[2]
       / "ui" / "src" / "lib" / "__tests__" / "healpix.vectors.json")

# ang2pix coverage: equatorial band, polar caps, near band boundaries
# (arcsin(2/3) ~= +/-41.81 deg — sampled just off it to avoid exact-tie flakiness),
# both poles, RA wrap (0.1 / 359.9).
DECS = [0.0, 15.0, -15.0, 30.0, -30.0, 41.80, 41.82, -41.80, -41.82,
        60.0, -60.0, 85.0, -85.0, 89.99, -89.99]
# RA samples: 0.1 / 359.9 exercise the RA wrap. 90.1 (not the "nice" 90.0) is a
# deliberate off-tie interior sample: RA that is an exact multiple of 45 deg
# lands on a HEALPix face boundary/corner, where the point sits bit-exactly on a
# pixel edge. There, astropy-healpix and the CANONICAL HEALPix reference
# (Healpix_Base's int(temp1+-temp2)) break the measure-zero tie in OPPOSITE
# directions, so ang2pix on exactly 45*k deg is convention-dependent, not a well
# -defined truth. This is the same tie-flakiness the DECS above dodge around the
# arcsin(2/3) band; 90.1 keeps face-5 interior coverage without the singularity.
RAS = [0.1, 90.1, 200.0, 359.9]

# Note: astropy-healpix 2.0.0 accepts dx/dy exactly at the 0.0 and 1.0 tile
# edges (verified), so the (u,v) grid below is emitted at the true corners with
# no inward nudge — the TS port is gated at the exact edge values.


def _interleave(ix: int, iy: int) -> int:
    b = 0
    for i in range(16):
        b |= ((ix >> i) & 1) << (2 * i)
        b |= ((iy >> i) & 1) << (2 * i + 1)
    return b


def _central_pixel(order: int, face: int) -> int:
    nside = 2 ** order
    half = nside // 2
    return face * nside * nside + _interleave(half, half)


def main() -> int:
    ang2pix: list[dict] = []
    for order in range(0, 10):
        hp = HEALPix(nside=2 ** order, order="nested")
        for dec in DECS:
            for ra in RAS:
                npix = int(hp.lonlat_to_healpix(ra * u.deg, dec * u.deg))
                ang2pix.append({"order": order, "raDeg": ra, "decDeg": dec, "npix": npix})

    uv_grid = [0.0, 0.25, 0.5, 1.0]
    pixuv: list[dict] = []
    for order in (0, 3, 6, 9):
        for face in range(12):
            npix = _central_pixel(order, face)
            for vv in uv_grid:          # v along rows (even/ix) -> dx
                for uu in uv_grid:      # u along cols (odd/iy)  -> dy
                    lon, lat = healpix_to_lonlat(
                        npix, nside=2 ** order, dx=vv, dy=uu, order="nested")
                    pixuv.append({"order": order, "npix": int(npix),
                                  "u": uu, "v": vv,
                                  "raDeg": float(lon.to_value(u.deg)),
                                  "decDeg": float(lat.to_value(u.deg))})

    # 3 parity vectors around M31 (ra 10.68 deg, dec 41.27 deg) for Task 7's
    # cross-check test.
    order = 8
    hp = HEALPix(nside=2 ** order, order="nested")
    m31 = int(hp.lonlat_to_healpix(10.68 * u.deg, 41.27 * u.deg))
    parity: list[dict] = []
    for uu, vv in [(0.5, 0.5), (0.25, 0.75), (1.0, 0.0)]:
        lon, lat = healpix_to_lonlat(
            m31, nside=2 ** order, dx=vv, dy=uu, order="nested")
        parity.append({"order": order, "npix": m31, "u": uu, "v": vv,
                       "raDeg": float(lon.to_value(u.deg)),
                       "decDeg": float(lat.to_value(u.deg))})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"ang2pix": ang2pix, "pixUV2ang": pixuv,
                               "parity": parity}, indent=1), encoding="utf-8")
    print(f"wrote {OUT} — ang2pix={len(ang2pix)} pixUV2ang={len(pixuv)} parity={len(parity)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
