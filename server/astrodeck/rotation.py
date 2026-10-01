"""Pure rotator angle & mechanical-range math.

Direct transcription of docs/native-parity/algorithms/nina-platesolving.md
§11.2 (get_target_mechanical_position / get_target_position) and §11.4
(angle equality). No I/O, no device knowledge — table-tested, and mirrored
in ui/src/lib/rotation.ts with the SAME test vectors.

The one exception is ``one_sided_moves`` (#526, below): the plan the hub's
rotate loop executes for a single move. It is server-only, since the UI
never commands a move itself, so it has no mirror and no shared vectors.

``sky_to_mechanical`` / ``mechanical_to_sky`` (R-4, #145) are server-only for
the same reason: they exist to carry the LEARNED sky/mechanical sign into a
move the hub is about to command, and the UI never commands one. The sign
itself is hub state (``Hub._rotator_sky_sign``), not a pure-math constant,
because it is MEASURED per rig, not derived — see ``Hub.learn_rotator_sign``.
"""
from __future__ import annotations

from dataclasses import dataclass

#: slack from the reference implementation (ANGLE_EQUALS_EPSILON)
_EPS = 1e-13

#: How far past its target a rotator move that runs against the approach
#: direction goes before it turns back (#526, H4 orchestrator ruling 3).
#:
#: WHY. On 2026-09-28 the rig's rotate loop commanded -5.7 degrees after the
#: meridian flip, the rotator reported that it had moved -5.7, and the next
#: solve read the camera +0.5 degrees from where it was: the reversal was
#: eaten by play in the train. The focuser has the same shape and the same
#: cure (``focus.approach``): arrive from one side every time, so the slack
#: is always taken up in the same direction. 5 degrees is the ruling's
#: first guess, not a measurement of the CAA's play, which only the rig can
#: give; a play wider than this is still taken up only partly.
ROTATOR_BACKLASH_DEG = 5.0

#: The mechanical sweep each ``range_type`` allows, from ``range_start_deg``
#: (the same spans ``target_mechanical_position`` folds into).
_SPAN_DEG = {"half": 180.0, "quarter": 90.0}


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


def sky_to_mechanical(sky_deg: float, mech_pos: float, sync_offset_deg: float,
                      sky_sign: int = 1) -> float:
    """The mechanical angle that puts the rotator at sky PA ``sky_deg``,
    anchored at the point ``(mech_pos, sync_offset_deg)`` already describes:
    ``mod360(mech_pos - sync_offset_deg)`` is what the rotator reads as sky
    right now (``Rotator.get_position``'s own formula), and this is that
    reading's inverse, extended by ``sky_sign`` (R-4, #145).

    ``sky_sign`` is which way the sky PA moves when the mechanism does: +1
    together (``sky = mech - offset``, what every rotator assumed before
    R-4), -1 opposite -- measured on this rig's CAA, pier west (#145): the
    solved PA ran one-for-one AGAINST the mechanical angle. Guessing +1 when
    the truth is -1 does not just offset the target, it reverses the sense
    of the correction, which is what turned five rotate attempts into a
    camera spun through more than a full revolution on 2026-08-08 (the sign
    was never measured before R-4).

    ANCHORING ON ``mech_pos``, not treating ``sync_offset_deg`` as a global
    sky-minus-mechanical constant, is what makes this correct for EITHER
    sign. A constant difference (``mech - sky = offset`` everywhere) is only
    the right invariant when ``sky_sign`` is +1; a -1 relationship keeps the
    SUM constant instead (``mech + sky``), and recovering that needs an
    actual anchor point -- the live ``mech_pos`` this was read at -- not the
    offset alone. See ``mechanical_to_sky`` for the inverse direction."""
    sky_now = mod360(mech_pos - sync_offset_deg)
    return mod360(mech_pos + sky_sign * (sky_deg - sky_now))


def mechanical_to_sky(mech_deg: float, mech_pos: float, sync_offset_deg: float,
                      sky_sign: int = 1) -> float:
    """The sky PA at mechanical angle ``mech_deg``: the inverse of
    ``sky_to_mechanical`` (same anchor, same ``sky_sign`` -- see its
    docstring)."""
    sky_now = mod360(mech_pos - sync_offset_deg)
    return mod360(sky_now + sky_sign * (mech_deg - mech_pos))


def map_sky_target(sky_target: float, mech_pos: float, sync_offset_deg: float,
                   range_type: str, range_start_deg: float, *,
                   sky_sign: int = 1) -> float:
    """§11.2 get_target_position: desired sky PA -> reachable sky PA given the
    mechanical range. ``sync_offset_deg`` is mechanical − sky at ``mech_pos``
    (Rotator ABC). ``sky_sign`` is R-4's learned sign (#145, see
    ``sky_to_mechanical``); the default +1 keeps every caller that predates
    R-4 -- api/app.py's manual move, this module's own pre-R-4 tests --
    reading exactly as it always did.

    ``mech_pos`` was accepted for signature parity alone back when the
    mapping assumed sign +1: that makes the sky<->mechanical relation a
    GLOBAL constant offset, so any anchor point gives the same answer. R-4
    changed that (``sky_to_mechanical``'s docstring): a -1 sign's invariant
    is a sum, not a difference, and recovering it needs a real anchor, which
    ``mech_pos`` now supplies."""
    position = mod360(sky_target)
    mech = sky_to_mechanical(position, mech_pos, sync_offset_deg, sky_sign)
    mech_tgt = target_mechanical_position(mech, range_type, range_start_deg)
    return mechanical_to_sky(mech_tgt, mech_pos, sync_offset_deg, sky_sign)


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


def follow_fraction(turned_deg: float, commanded_deg: float) -> float:
    """What fraction of a commanded move the camera's solved angle actually
    reached: ``abs(turned_deg) / abs(commanded_deg)`` (D-05, #594).

    Shared by ``Hub._rotate_to_pa_attempts``'s per-move follow check and
    ``Hub.rotator_self_test``'s nightly dry run -- both ask the exact same
    question (did the camera turn as far as it was told to), against
    DIFFERENT thresholds for different reasons (see ``ROTATE_FOLLOW_
    FRACTION`` and ``ROTATOR_SELF_TEST_FOLLOW_FRACTION`` in hub.py). Sharing
    this one comparison is what keeps the two from quietly drifting apart on
    what "followed" even means, while each keeps its own number.

    ``commanded_deg`` of exactly 0 reads as a full 1.0 (nothing commanded
    cannot be under-followed) rather than dividing by zero -- no caller
    commands a zero-size move today, but a pure function should not raise on
    an input its own domain allows."""
    if commanded_deg == 0.0:
        return 1.0
    return abs(turned_deg) / abs(commanded_deg)


def mechanical_travel(current_mech: float, target_mech: float) -> float:
    """The signed mechanical travel of a move from ``current_mech`` to
    ``target_mech``: positive is increasing mechanical angle. The shortest
    way, tied at exactly 180 degrees to +180, as ``SimRotator.move_mechanical``
    travels.

    NO RANGE ARGUMENT, and none is needed: a half or quarter range spans 180
    degrees or less, so between two angles inside it the shortest way is
    always the way that stays inside it. A first version worked the travel
    out inside the range and a mutant removing that branch changed nothing,
    because it was the same number by construction."""
    raw = mod360(target_mech - current_mech)
    return raw if raw <= 180.0 else raw - 360.0


@dataclass(frozen=True)
class RotatorMoves:
    """The absolute mechanical angles to command, in order, for one move,
    and ``skipped``: why the one-side approach was not made, in words, or
    None when it was made or not needed."""
    moves: tuple[float, ...]
    skipped: str | None = None


def one_sided_moves(current_mech: float, target_mech: float,
                    range_type: str, range_start_deg: float, *,
                    backlash_deg: float = ROTATOR_BACKLASH_DEG) -> RotatorMoves:
    """How to reach ``target_mech`` from ``current_mech`` so that the last leg
    of the move always turns the same way (#526, H4 orchestrator ruling 3).

    THE APPROACH DIRECTION IS INCREASING MECHANICAL ANGLE. A move that
    travels that way already arrives from the right side, and is one move.
    A move that travels the other way goes ``backlash_deg`` past its target
    and comes back to it, so its last leg turns the same way as every other
    move's, and the play in the train is always taken up on the same side.
    Sky and mechanical angles differ by the sync offset alone, so a -5.7
    degree correction of the sky angle is a -5.7 degree mechanical travel,
    and becomes two moves.

    THE OVERSHOOT NEVER LEAVES THE MECHANICAL RANGE. On a half or quarter
    range a target within ``backlash_deg`` of the range's start would send
    the overshoot across the edge the range exists to keep the rotator off.
    There the move is made direct, and ``skipped`` says so: arriving
    against the approach direction costs less than a cable wrapped round
    the train.

    A FULL RANGE HAS AN EDGE TOO, at mechanical 0 (H4-HUB verifier). A
    rotator reports its mechanical angle in [0, 360), and one with travel
    limits (the CAA has min and max degree limits) does not wrap through 0:
    asked for 358 from 3, it turns 355 degrees up, not 5 down. An overshoot
    from a target just above 0 would then swing the train nearly a full
    turn and back, for a move that direct is a few degrees. Whether the
    CAA wraps is unmeasured, so the overshoot never crosses 0 either; on a
    rotator that does wrap, the cost is a direct move for a target within
    ``backlash_deg`` above 0, said in the log.

    Pure: angles in, angles out. ``backlash_deg`` 0 or less turns the rule
    off, and every move is direct."""
    target = mod360(target_mech)
    travel = mechanical_travel(current_mech, target)
    if backlash_deg <= 0.0 or travel >= 0.0:
        return RotatorMoves((target,))
    span = _SPAN_DEG.get(range_type)
    edge = mod360(range_start_deg) if span is not None else 0.0
    if mod360(target - edge) < backlash_deg:
        where = (f"the start of the {range_type} range at {edge:.2f}°"
                 if span is not None else
                 "mechanical 0°, where a rotator with travel limits turns "
                 "the long way rather than wrapping")
        return RotatorMoves((target,), skipped=(
            f"the one-side approach was skipped at the range edge: the move "
            f"to mechanical {target:.2f}° travels against the approach "
            f"direction, and a {backlash_deg:g}° overshoot would cross "
            f"{where}, so the move is made direct"))
    return RotatorMoves((mod360(target - backlash_deg), target))
