# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-29 (a) / #168: one FOV function serves both UIs and the server, and a
recorded-but-never-applied focal reducer must not make any of them disagree.

Before the fix, ``ui/src/next/lib/fov.ts``'s ``fovDeg`` multiplied the optics'
``reducer`` into the effective focal length while ``ui/src/lib/framing.ts``'s
``fovFromOptics`` and the server never did -- so a mosaic tiled from config and
a UI's preview of that SAME config could disagree by the reducer factor
whenever one was recorded. ``config.Optics.reducer`` (config.py:97-111) and
``config.f_ratio``'s doc are explicit that the reducer is recorded, never
applied: ``focal_length_mm`` alone drives every FOV reader.

This pins the server side of that contract: ``catalog.framing.fov_deg_from_optics``
reads an ``Optics`` instance and must (a) match ``config.fov_deg`` called
directly on the same focal length/pixel size/sensor dims, and (b) be
unaffected by the ``reducer`` field entirely. The TS mirror of this same
config lives in ``ui/src/next/lib/__tests__/w3FovOneFormula.test.ts``
(500mm, 3.76um, 6248x4176 APS-C -- the same fixture ``test_framing.py``'s
sibling client test and ``framing.test.ts``'s "fovFromOptics sanity" use), so
one config is pinned to the same field across both UIs and the server.

Named mutant: have ``fov_deg_from_optics`` multiply the reducer in --
``_config_fov_deg(optics.focal_length_mm * optics.reducer, ...)`` -- and
``test_fov_deg_from_optics_ignores_the_reducer`` goes red: ``expected 2.69...,
got 3.36...`` (0.8x reducer -> a 1/0.8 = 1.25x wider field).
"""
from __future__ import annotations

from astrodeck.catalog.framing import fov_deg_from_optics
from astrodeck.config import Optics, fov_deg

# The SAME config ui/src/lib/__tests__/framing.test.ts's "fovFromOptics
# sanity" fixture and the new ui/src/next/lib/__tests__/w3FovOneFormula.test.ts
# use: a 500mm scope, 3.76um pixels, 6248x4176 (APS-C-ish).
FOCAL_MM = 500.0
PIXEL_UM = 3.76
W_PX = 6248
H_PX = 4176


def test_fov_deg_from_optics_matches_config_fov_deg_directly():
    """No reducer recorded (the default, 1.0): the catalog reader must return
    exactly what calling ``config.fov_deg`` on the raw numbers gives -- it is
    a direct delegation, not a second formula that could drift from it."""
    optics = Optics(focal_length_mm=FOCAL_MM, pixel_size_um=PIXEL_UM,
                     sensor_width_px=W_PX, sensor_height_px=H_PX)
    got = fov_deg_from_optics(optics)
    want = fov_deg(FOCAL_MM, PIXEL_UM, W_PX, H_PX)
    assert got == want


def test_fov_deg_from_optics_ignores_the_reducer():
    """#168: a recorded 0.8x reducer must not change the field at all -- the
    rig keeps framing at the explicit ``focal_length_mm`` until "USE THE
    REDUCED FOCAL LENGTH" folds the reducer into it."""
    native = Optics(focal_length_mm=FOCAL_MM, pixel_size_um=PIXEL_UM,
                     sensor_width_px=W_PX, sensor_height_px=H_PX, reducer=1.0)
    reduced = Optics(focal_length_mm=FOCAL_MM, pixel_size_um=PIXEL_UM,
                      sensor_width_px=W_PX, sensor_height_px=H_PX, reducer=0.8)

    fw_native, fh_native, _ = fov_deg_from_optics(native)
    fw_reduced, fh_reduced, _ = fov_deg_from_optics(reduced)

    assert fw_reduced == fw_native, (
        f"a recorded reducer changed the width field: {fw_reduced!r} != {fw_native!r}"
    )
    assert fh_reduced == fh_native, (
        f"a recorded reducer changed the height field: {fh_reduced!r} != {fh_native!r}"
    )
    # Anchors to the known worked value (same tolerance as framing.test.ts's
    # "fovFromOptics sanity (500mm, 3.76um, APS-C)").
    assert abs(fw_native - 2.69) < 0.05
    assert abs(fh_native - 1.80) < 0.05
