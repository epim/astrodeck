# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PRO-1 calibration matcher — pure predicates + coverage (no I/O).

The AUTHORITATIVE match rule (mirrored, tested, in the TS pre-flight). A light is
covered when a DARK master matches (gain/offset/binning exact, exposure within a
percentage tolerance, temp within a °C tolerance — temp skipped when unknown on
either side) AND a FLAT master matches (filter/binning/gain exact, and the
rotator's mechanical angle within ``ROTATION_TOL_DEG``, wrap-aware — skipped
when unknown on either side; flat exposure is auto-solved to ADU so it is not
an identity). Bias is optional in v1."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..rotation import angle_equals

#: How far the camera may have turned before a flat stops being this flat.
#:
#: Derived, not picked. A flat corrects dust shadows, and a mote sitting r from
#: the optical axis moves r·Δθ across the sensor when the rotator turns. At the
#: edge of an APS-C sensor (r ≈ 14 mm) 1° drags a shadow 0.24 mm — about 65 px
#: at 3.76 µm, which is already the diameter of a mote's own out-of-focus
#: shadow. Past roughly one degree, then, a flat stops dividing out the mote
#: that is there and starts dividing out one that is not: a dark ring beside a
#: bright one, in every frame, permanently. Under a degree the shadow still
#: overlaps itself and the correction degrades smoothly rather than inverting.
#:
#: Compared on the MECHANICAL angle (the ROTMECH card, #176), never the sky
#: position angle: dust follows the metal, and the sky angle moves whenever the
#: rotator is re-synced. Not a constraint at all when either side's angle is
#: unknown — a rig with no rotator writes no ROTMECH, and a flat shot before
#: the card existed has none — the same "unknown is not a constraint" reading
#: ``_temp_ok`` applies to temperature.
#:
#: ONE NUMBER: the health matrix (``flows.calibration_health``) imports this
#: name rather than keep a copy, so the panel and the pipeline cannot part
#: company over what "the same angle" means.
ROTATION_TOL_DEG = 1.0


@dataclass(frozen=True)
class MatchTolerance:
    exposure_tol_pct: float = 5.0
    temp_tol_c: float = 2.0
    #: See ``ROTATION_TOL_DEG``. A field, so a site can replace it and so the
    #: health matrix can hand its own ``rotation_tol_deg`` to the one rule.
    rotator_tol_deg: float = ROTATION_TOL_DEG


@dataclass(frozen=True)
class LightNeed:
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str
    #: The rotator's mechanical angle the light is (or will be) shot at; None
    #: when unknown, which is no constraint on a flat (#176). Trails and
    #: defaults, so every six-field ``LightNeed(...)`` built by hand still works.
    rotator_mech_deg: float | None = None


@dataclass(frozen=True)
class MasterRecord:
    id: str
    frame_type: str        # DARK|BIAS|FLAT
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str
    frame_count: int
    path: str
    built_ts: float
    #: The master's own mechanical angle: the circular mean of its frames'
    #: ROTMECH, or None for a flat with no card (every flat shot before #176),
    #: which matches any light. Trails and defaults for the same reason.
    rotator_mech_deg: float | None = None


@dataclass(frozen=True)
class Gap:
    filter: str
    exposure_s: float
    gain: int
    binning: int
    missing: tuple[str, ...]   # subset of ("dark", "flat")
    #: The angle of the light the gap is for, so a flat gap at one angle of a
    #: mosaic says which angle it is. None for a need with no known angle.
    rotator_mech_deg: float | None = None


def _within_pct(a: float, b: float, pct: float) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) <= abs(b) * (pct / 100.0)


def _temp_ok(need_t: float | None, master_t: float | None, tol_c: float) -> bool:
    if need_t is None or master_t is None:
        return True                       # temp not a constraint when unknown
    return abs(need_t - master_t) <= tol_c


def _known(angle: float | None) -> bool:
    return angle is not None and math.isfinite(angle)


def _rotator_ok(need: LightNeed, m: MasterRecord, tol_deg: float) -> bool:
    """Is the master's mechanical angle the light's, within ``tol_deg``?

    Wrap-aware through ``rotation.angle_equals``: 359.6 and 0.4 are 0.8 degree
    apart, and a plain subtraction says 359.2. Unknown on either side is no
    constraint, as ``_temp_ok`` reads temperature. A non-finite value is
    unknown too: NaN compares false with everything, so letting it through
    would refuse every flat, which is a constraint nobody wrote."""
    if not _known(need.rotator_mech_deg) or not _known(m.rotator_mech_deg):
        return True
    return angle_equals(need.rotator_mech_deg, m.rotator_mech_deg, tol_deg)


def _rotator_gap(need: LightNeed, m: MasterRecord, tol_deg: float) -> float:
    """How far the master's angle is from the light's, for ranking: the
    wrapped difference in degrees; ``tol_deg`` when only the master's is
    unknown (it matches, but a flat measured at the light's own angle is
    better evidence); 0.0 when the light's is unknown (nothing to rank by)."""
    if not _known(need.rotator_mech_deg):
        return 0.0
    if not _known(m.rotator_mech_deg):
        return tol_deg
    d = abs(need.rotator_mech_deg % 360.0 - m.rotator_mech_deg % 360.0)
    return min(d, 360.0 - d)


def dark_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool:
    return (m.frame_type == "DARK" and m.gain == need.gain and m.offset == need.offset
            and m.binning == need.binning
            and _within_pct(need.exposure_s, m.exposure_s, tol.exposure_tol_pct)
            and _temp_ok(need.temp_c, m.temp_c, tol.temp_tol_c))


def flat_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool:
    return (m.frame_type == "FLAT" and m.filter == need.filter
            and m.binning == need.binning and m.gain == need.gain
            and _rotator_ok(need, m, tol.rotator_tol_deg))


def best_master(need: LightNeed, masters: list[MasterRecord], tol: MatchTolerance,
                frame_type: str) -> MasterRecord | None:
    """Closest matching master of ``frame_type`` (nearest mechanical angle
    first, which only a flat has; then DARK ranked by |Δexposure| then
    |Δtemp|, most frames as tiebreak), or None."""
    pred = {"DARK": dark_matches, "FLAT": flat_matches}[frame_type]
    cands = [m for m in masters if pred(need, m, tol)]
    if not cands:
        return None

    def rank(m: MasterRecord):
        dang = _rotator_gap(need, m, tol.rotator_tol_deg)
        dexp = abs(need.exposure_s - m.exposure_s)
        dtemp = (0.0 if (need.temp_c is None or m.temp_c is None)
                 else abs(need.temp_c - m.temp_c))
        return (dang, dexp, dtemp, -m.frame_count)

    return sorted(cands, key=rank)[0]


def coverage_for(needs: list[LightNeed], masters: list[MasterRecord],
                 tol: MatchTolerance) -> list[Gap]:
    """One Gap per uncovered need. ``[]`` when ``masters`` is empty (no nag)."""
    if not masters:
        return []                          # empty library never nags
    gaps: list[Gap] = []
    for n in needs:
        missing: list[str] = []
        if best_master(n, masters, tol, "DARK") is None:
            missing.append("dark")
        if best_master(n, masters, tol, "FLAT") is None:
            missing.append("flat")
        if missing:
            gaps.append(Gap(filter=n.filter, exposure_s=n.exposure_s, gain=n.gain,
                            binning=n.binning, missing=tuple(missing),
                            rotator_mech_deg=n.rotator_mech_deg))
    return gaps
