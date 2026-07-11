"""Pure rotator angle & mechanical-range math.

Direct transcription of docs/native-parity/algorithms/nina-platesolving.md
§11.2 (get_target_mechanical_position / get_target_position) and §11.4
(angle equality). No I/O, no device knowledge — table-tested, and mirrored
in ui/src/lib/rotation.ts with the SAME test vectors.
"""
from __future__ import annotations

#: slack from the reference implementation (ANGLE_EQUALS_EPSILON)
_EPS = 1e-13


def mod360(a: float) -> float:
    """Euclidean mod 360 — result always in [0, 360)."""
    return a % 360.0


def angle_equals(a: float, b: float, tol: float) -> bool:
    """§11.4: equal within tol degrees, wrap-aware (mod 360)."""
    d = abs(mod360(a) - mod360(b))
    t = tol % 360.0
    return (d - t) <= _EPS or ((360.0 - d) - t) <= _EPS


def angle_equals_mod180(a: float, b: float, tol: float) -> bool:
    """§11.4 'oneEightyIsEqual': a frame rotated 180° is the same framing."""
    d = abs(a % 180.0 - b % 180.0)
    t = tol % 180.0
    return (d - t) <= _EPS or ((180.0 - d) - t) <= _EPS


def target_mechanical_position(p: float, range_type: str,
                               range_start_deg: float) -> float:
    """§11.2: map a mechanical angle into the allowed sweep. ``p`` in [0,360)."""
    d = mod360(p - range_start_deg)
    if range_type == "half":
        t = p if d < 180.0 else p + 180.0
    elif range_type == "quarter":
        if d < 90.0:
            t = p
        elif d < 180.0:
            t = p + 270.0
        elif d < 270.0:
            t = p + 180.0
        else:
            t = p + 90.0
    else:  # "full"
        t = p
    return mod360(t)


def map_sky_target(sky_target: float, mech_pos: float, sync_offset_deg: float,
                   range_type: str, range_start_deg: float) -> float:
    """§11.2 get_target_position: desired sky PA -> reachable sky PA given the
    mechanical range. ``sync_offset_deg`` is mechanical − sky (Rotator ABC).
    ``mech_pos`` is accepted for signature clarity/parity but the mapping only
    needs the offset."""
    del mech_pos  # parity signature; offset alone determines the mapping
    position = mod360(sky_target)
    mech = mod360(position + sync_offset_deg)
    mech_tgt = target_mechanical_position(mech, range_type, range_start_deg)
    return mod360(mech_tgt - sync_offset_deg + 360.0)


def shortest_rotation(target_deg: float, orientation_deg: float,
                      range_type: str) -> float:
    """§11.3: signed distance to move. Commanded absolute sky angle is
    ``mod360(orientation + distance)``.

    FULL range considers the 180°-rotated frame when it is closer (camera
    frames are PA-ambiguous mod 180). Limited ranges must NOT collapse mod-180
    — the target was already range-mapped and the twin may be out of range —
    so they get plain shortest-signed normalization into (-180, 180].
    """
    distance = target_deg - orientation_deg
    if range_type == "full":
        m = mod360(distance) % 180.0
        m2 = m - 180.0
        return m if m < abs(m2) else m2
    d = mod360(distance)
    return d - 360.0 if d > 180.0 else d
