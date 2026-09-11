"""Pure autorun window resolution — no I/O, fully unit-testable (Batch 4b §1.6).

Resolves a per-target :class:`~astrodeck.sequence.models.Schedule` into concrete
start/stop unix timestamps for *tonight*, decides whether a target is ready /
waiting / closed / never-rises, and computes the effective altitude floor with a
wrap-interpolated horizon profile.

Design constraints baked in here (from the adversarial UX critiques):

* **Polar guard (C2-13):** ``next_sun_event`` walks a *bounded* loop (at most one
  sidereal day of coarse samples) and returns ``None`` when the sun never reaches
  the requested altitude at this latitude — never an infinite loop.
* **Wrap interpolation (C2-3):** the horizon is a list of sorted ``(az, alt)``
  control points; the effective floor at an azimuth is linearly interpolated
  across the 0↔360 seam, so a point at az=350 and az=10 blend smoothly through
  due-north.
* **Two distinct altitude concepts (C1-28):** ``Schedule.min_altitude_deg`` is the
  per-target *start gate* checked against the *target* altitude (here). The global
  pier-collision floor (``SafetyConfig.min_alt_deg`` vs *mount* altitude) is the
  engine's concern, not this module's.

All math reuses :mod:`astrodeck.catalog.coords` (``lst_hours``/``altaz``/
``sun_altaz``) so there is one source of sky-position truth.
"""
from __future__ import annotations

import math

import time
from typing import TYPE_CHECKING, Any

from ..catalog.coords import (altaz, angular_sep_deg,
                              hour_angle_h as coords_hour_angle_h, lst_hours,
                              moon_illumination, moon_radec, sun_altaz)

if TYPE_CHECKING:  # avoid an import cycle at runtime; only needed for typing
    from .models import Schedule, Target

# One sidereal day in seconds — the bound for the sun-event search (a real solar
# day is ~86400 s; one sidereal day is a safe, slightly-longer upper bound that
# still terminates at any latitude — C2-13).
_SIDEREAL_DAY_S = 86164.0905
# Coarse search step for the sun crossing (10 min). Matches ``coords.dark_window``
# precision — the twilight clock readout never needs better than ~minutes.
_SUN_STEP_S = 600.0
# Fine bisection refinement passes once a coarse bracket is found.
_REFINE_PASSES = 16

# Coarse step for the target peak-altitude scan across a window.
_PEAK_STEP_S = 600.0


def _lat_lon(site: dict[str, Any]) -> tuple[float, float]:
    """Extract (latitude, longitude) from a hub-style site dict."""
    return float(site["latitude"]), float(site["longitude"])


# --------------------------------------------------------------------- sun events

def sun_altitude(lat: float, lon: float, t: float) -> float:
    """The sun's altitude in degrees at unix time ``t`` for the site."""
    alt, _ = sun_altaz(lat, lon, t)
    return alt


def next_sun_event(lat: float, lon: float, alt_deg: float, after_t: float,
                   rising: bool) -> float | None:
    """Unix ts the sun next crosses ``alt_deg`` after ``after_t``.

    ``rising=True`` finds the next moment the sun climbs *through* ``alt_deg``
    (altitude increasing); ``rising=False`` finds the next moment it sinks through
    it (altitude decreasing). Used for dusk (sun *setting* through a twilight
    angle => ``rising=False``) and dawn (sun *rising* through it => ``rising=True``).

    **Returns ``None``** when the sun never reaches ``alt_deg`` in the crossing
    direction within one sidereal day — i.e. polar day/night where the sun stays
    permanently above or below the angle (C2-13). The loop is hard-bounded, so a
    high-latitude summer night cannot hang the scheduler.
    """
    steps = int(_SIDEREAL_DAY_S / _SUN_STEP_S) + 2
    prev_t = after_t
    prev_alt = sun_altitude(lat, lon, prev_t)
    for i in range(1, steps + 1):
        cur_t = after_t + i * _SUN_STEP_S
        cur_alt = sun_altitude(lat, lon, cur_t)
        crossed_up = prev_alt < alt_deg <= cur_alt
        crossed_down = prev_alt > alt_deg >= cur_alt
        if (rising and crossed_up) or (not rising and crossed_down):
            return _refine_crossing(lat, lon, alt_deg, prev_t, cur_t)
        prev_t, prev_alt = cur_t, cur_alt
    return None


def prev_sun_event(lat: float, lon: float, alt_deg: float, before_t: float,
                   rising: bool) -> float | None:
    """Unix ts the sun last crossed ``alt_deg`` *before* ``before_t``.

    The backward twin of :func:`next_sun_event`: same forward-time crossing
    convention (``rising=True`` => altitude increasing through ``alt_deg``,
    ``rising=False`` => sinking through it) but the search walks backward in
    coarse steps. This is what lets ``resolve_window`` anchor a boundary to
    *tonight* — a dusk that already happened this evening still opens the current
    window instead of rolling to tomorrow (the live re-resolution bug). Returns
    ``None`` (hard-bounded, polar-safe) when no crossing is found within a
    sidereal day.
    """
    steps = int(_SIDEREAL_DAY_S / _SUN_STEP_S) + 2
    next_t = before_t
    next_alt = sun_altitude(lat, lon, next_t)
    for i in range(1, steps + 1):
        cur_t = before_t - i * _SUN_STEP_S
        cur_alt = sun_altitude(lat, lon, cur_t)
        # evaluate the crossing in forward-time terms (cur_t -> next_t).
        crossed_up = cur_alt < alt_deg <= next_alt
        crossed_down = cur_alt > alt_deg >= next_alt
        if (rising and crossed_up) or (not rising and crossed_down):
            return _refine_crossing(lat, lon, alt_deg, cur_t, next_t)
        next_t, next_alt = cur_t, cur_alt
    return None


def _night_dawn(lat: float, lon: float, twilight_deg: float,
                now: float) -> float | None:
    """The dawn (sun rising through the twilight angle) that ends the current or
    imminent night — the next dawn at/after ``now``."""
    return next_sun_event(lat, lon, twilight_deg, now, rising=True)


def _night_dusk(lat: float, lon: float, twilight_deg: float,
                now: float) -> float | None:
    """The dusk (sun setting through the twilight angle) that *opens* the current
    or imminent night. Anchored to the paired dawn so it is the same night's dusk:
    the last dusk before the next dawn. When ``now`` is already inside the dark
    span this returns the dusk that is now in the PAST (so the frozen window is
    open, not ~23h in the future); when ``now`` is in daylight it returns the
    coming evening's dusk. Falls back to the next forward dusk if no dawn resolves
    (polar)."""
    dawn = _night_dawn(lat, lon, twilight_deg, now)
    if dawn is None:
        return next_sun_event(lat, lon, twilight_deg, now, rising=False)
    return prev_sun_event(lat, lon, twilight_deg, dawn, rising=False)


def observing_night(site: "dict | Any", twilight_deg: float | None = None,
                    now: float | None = None) -> tuple[float, float] | None:
    """``(dusk, dawn)`` for the current-or-imminent night, or None.

    **The one answer to "when is tonight?"** — for the auto-resume bound, for
    the cloud-forecast scan, and for anything that quotes a window to a human.
    Three callers each resolving their own horizon is how they came to disagree
    (2026-08-11: the boot banner, the modal and the veto described the same
    forecast three different ways).

    None means there is no night to speak of: a site still at its default
    coordinates (we do not know where the observer is, so we must not pretend
    to know when their night is) or a latitude in polar day.

    ``twilight_deg`` defaults to ``cfg.safety.twilight_deg`` — the operator's
    own definition of dark enough to image, not a constant, because a
    narrowband rig in a city and a broadband rig under Bortle 2 do not agree
    about when the night starts.

    NOT the dawn-park threshold. That daemon deliberately waits for a LATER sun
    angle (civil, -6°) because it is a hardware safety net, not a schedule: it
    must fire after imaging should already have stopped, never before. Merging
    the two would move the net earlier and make it fight the run it exists to
    survive. Two thresholds, two jobs, on purpose.
    """
    from ..config import config_store
    # hub.site is a dict, cfg.site is a pydantic Site. Both callers exist and
    # neither should have to convert, so read either shape here — once.
    get = site.get if isinstance(site, dict) else (
        lambda k, d=None: getattr(site, k, d))
    if get("is_default", False):
        return None
    try:
        lat = float(get("latitude", 0.0) or 0.0)
        lon = float(get("longitude", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if twilight_deg is None:
        cfg = config_store.cfg()
        twilight_deg = cfg.safety.twilight_deg if cfg else -12.0
    t_now = time.time() if now is None else now
    dawn = _night_dawn(lat, lon, twilight_deg, t_now)
    dusk = _night_dusk(lat, lon, twilight_deg, t_now)
    if dusk is None or dawn is None:
        return None
    return dusk, dawn


def dark_enough(site: "dict | Any", twilight_deg: float | None = None,
                now: float | None = None) -> bool:
    """Is the Sun below the operator's imaging twilight RIGHT NOW?

    A MEASUREMENT, not arithmetic on a resolved window — and the first draft of
    this got that wrong in a way worth recording. It asked ``now >= dawn`` from
    :func:`observing_night`, which can never be true: that function anchors to
    the "current or imminent" night, so the moment you step past dawn it hands
    back TOMORROW's pair and the comparison resets. A bound that cannot fire is
    the same defect as the unbounded loop it was written to close.

    Reading the sun's altitude has no such edge. It is also the honest question:
    "can this rig image?" is a fact about the sky at this instant, not about
    which side of a boundary a stored timestamp falls on.

    Bounds BOTH ends. The afternoon has the same problem the morning did — a
    default ``Schedule`` says ``start_mode="now"``, so a session armed at 15:00
    was equally "open" — and one predicate closes both.

    TRUE when the site is unset. "We cannot tell" must not stand every
    automation down; the per-target schedule and the weather veto still apply.
    Fail-open here, fail-closed in the gates that actually move hardware.
    """
    from ..config import config_store
    get = site.get if isinstance(site, dict) else (
        lambda k, d=None: getattr(site, k, d))
    if get("is_default", False):
        return True
    if twilight_deg is None:
        cfg = config_store.cfg()
        twilight_deg = cfg.safety.twilight_deg if cfg else -12.0
    try:
        lat = float(get("latitude", 0.0) or 0.0)
        lon = float(get("longitude", 0.0) or 0.0)
    except (TypeError, ValueError):
        return True
    t_now = time.time() if now is None else now
    return sun_altitude(lat, lon, t_now) < twilight_deg


# There is deliberately NO `night_has_ended` here. One was written, to let the
# stand-down line say "the night is over" after dawn and something else in the
# afternoon — and it hit the SAME re-anchoring trap twice: past dawn,
# observing_night returns tomorrow's pair, so no comparison against dusk or
# dawn can tell the morning-after from the afternoon-before. Discriminating
# them properly needs the sun's direction of travel, which is real work in
# service of one word.
#
# The message was reworded instead, to something true at any not-dark hour:
# "it is not dark, and this session still owes N frames." A predicate that
# exists only to pick an adjective is not worth a function that can be wrong.


def _refine_crossing(lat: float, lon: float, alt_deg: float,
                     lo_t: float, hi_t: float) -> float:
    """Bisect a bracketed sun-altitude crossing to ~second precision."""
    lo_alt = sun_altitude(lat, lon, lo_t)
    for _ in range(_REFINE_PASSES):
        mid_t = (lo_t + hi_t) / 2.0
        mid_alt = sun_altitude(lat, lon, mid_t)
        # keep the half-interval that still straddles alt_deg
        if (lo_alt < alt_deg) == (mid_alt < alt_deg):
            lo_t, lo_alt = mid_t, mid_alt
        else:
            hi_t = mid_t
    return (lo_t + hi_t) / 2.0


# --------------------------------------------------------------------- horizon

def interp_wrap(horizon: list[tuple[float, float]] | None, az: float) -> float:
    """Linearly-interpolated horizon altitude at azimuth ``az`` (degrees).

    ``horizon`` is a list of ``(az, alt)`` control points. Interpolation wraps
    across the 0↔360 seam: between the last point (e.g. az=350) and the first
    (e.g. az=10) the floor blends through due-north (C2-3). Returns ``0.0`` for an
    empty/None horizon (no profile => no extra floor).
    """
    if not horizon:
        return 0.0
    pts = sorted((float(a) % 360.0, float(h)) for a, h in horizon)
    if len(pts) == 1:
        return pts[0][1]
    az = float(az) % 360.0
    # find the bracketing pair, wrapping the last->first segment through 360.
    for i in range(len(pts)):
        a0, h0 = pts[i]
        a1, h1 = pts[(i + 1) % len(pts)]
        span = (a1 - a0) % 360.0
        if span == 0:
            continue
        delta = (az - a0) % 360.0
        if delta <= span:
            frac = delta / span
            return h0 + (h1 - h0) * frac
    return pts[0][1]


def nogo_floor(nogo_box: list[dict] | None, az: float) -> float:
    """The floor imposed by no-go wedges at azimuth ``az``; 0.0 outside them all.

    A wedge is ``{"az_min", "az_max", "alt_max"}``: inside the azimuth range, the
    mount must stay ABOVE ``alt_max``. This is a HARD-EDGED obstruction (a pier,
    a wall, the neighbour's roof) — distinct from ``horizon``, whose control
    points interpolate, so a pier at az 180 would bleed a sloping floor across
    the whole southern sky. Overlapping wedges take the highest floor.

    Wraps the 0↔360 seam: ``az_min=350, az_max=10`` is the 20° wedge through
    due north, not the 340° complement.
    """
    if not nogo_box:
        return 0.0
    az = float(az) % 360.0
    floor = 0.0
    for box in nogo_box:
        try:
            a0 = float(box["az_min"]) % 360.0
            a1 = float(box["az_max"]) % 360.0
            alt = float(box["alt_max"])
        except (KeyError, TypeError, ValueError):
            continue        # a malformed wedge is ignored, never a crash mid-run
        span = (a1 - a0) % 360.0
        # A zero span is a degenerate wedge (az_min == az_max). Treat it as the
        # FULL circle rather than an empty one: the alternative silently drops a
        # guard the user believes is armed.
        if span == 0.0 or (az - a0) % 360.0 <= span:
            floor = max(floor, alt)
    return floor


#: No ceiling. 90 degrees is the zenith, so a limit there constrains nothing —
#: the honest "unset" value, and the default, so existing rigs are unaffected.
NO_CEILING_DEG = 90.0


def effective_ceiling(max_alt_deg: float | None) -> float:
    """The altitude a target must stay BELOW.

    A floor is not enough. Many mounts foul themselves near the zenith rather
    than near the horizon: on a strain-wave mount with a long imaging train the
    camera reaches the tripod legs while the optics are still pointing at open
    sky, and every altitude limit in this file is a MINIMUM, so nothing stopped
    it. Observed on this rig 2026-07-30 — the scope was resting against the
    tripod at high altitude, and no guard had an opinion.

    Note the wedge field ``alt_max`` is NOT this: despite the name it is a
    FLOOR (``nogo_floor`` — "inside this azimuth range, stay ABOVE"). That
    misnaming is exactly why a zenith limit looked like it already existed.
    """
    if max_alt_deg is None:
        return NO_CEILING_DEG
    try:
        v = float(max_alt_deg)
    except (TypeError, ValueError):
        return NO_CEILING_DEG
    # Clamp rather than reject: a nonsense value must not silently disarm the
    # guard, and must not make every target unreachable either.
    return min(NO_CEILING_DEG, max(0.0, v))


def effective_floor(min_alt_deg: float, horizon: list[tuple[float, float]] | None,
                    az: float, nogo_box: list[dict] | None = None) -> float:
    """``max(min_alt_deg, interp_wrap(horizon, az), nogo_floor(nogo_box, az))`` —
    the floor a target must clear at azimuth ``az`` (C2-3).

    The ``nogo_box`` term was specified with the rest of the safety block but
    never wired: the field persisted, the UI never showed it, and nothing read
    it, so a user who configured a pier guard had none. It defaults to None so
    every existing caller keeps its exact previous answer.
    """
    return max(float(min_alt_deg), interp_wrap(horizon, az),
               nogo_floor(nogo_box, az))


# --------------------------------------------------------------------- windows

def _resolve_event_ts(mode: str, offset_min: int, time_str: str | None,
                      lat: float, lon: float, twilight_deg: float,
                      now: float) -> float | None:
    """Resolve one schedule boundary (dusk/dawn/time) to a unix ts *for tonight*.

    dusk/dawn anchor to the current-or-imminent night's bracketing sun events
    (:func:`_night_dusk` / :func:`_night_dawn`), searching backward as well as
    forward so a dusk that already passed still opens the window (fixes the
    ~23h-in-the-future re-resolution). ``time`` resolves ``"HH:MM"`` to the
    occurrence nearest ``now`` (within ±12h), so an evening start already past
    stays tonight instead of rolling to tomorrow."""
    if mode == "now":
        return now
    if mode == "none":
        return None
    if mode == "dusk":
        base = _night_dusk(lat, lon, twilight_deg, now)
        return None if base is None else base + offset_min * 60.0
    if mode == "dawn":
        base = _night_dawn(lat, lon, twilight_deg, now)
        return None if base is None else base + offset_min * 60.0
    if mode == "time":
        return _clock_time_near_now(time_str, now)
    return None


def _clock_time_near_now(time_str: str | None, now: float) -> float | None:
    """The unix ts of local ``HH:MM`` on the occurrence *nearest* ``now`` (within
    ±12h). Unlike a strictly-forward roll, an evening start-time a few minutes
    past ``now`` resolves to tonight (in the past) rather than +23h tomorrow — so
    a frozen window opens correctly (§1.6 window-freeze fix)."""
    if not time_str:
        return None
    try:
        hh, mm = (int(x) for x in time_str.split(":", 1))
    except (ValueError, AttributeError):
        return None
    lt = time.localtime(now)
    candidate = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0,
                             0, 0, -1))
    # snap to the occurrence within (now-12h, now+12h] — the "tonight" instance.
    while candidate - now > 43200.0:
        candidate -= 86400.0
    while now - candidate > 43200.0:
        candidate += 86400.0
    return candidate


def resolve_window(sched: "Schedule", site: dict[str, Any], twilight_deg: float,
                   now: float) -> tuple[float | None, float | None]:
    """``(start_ts, stop_ts)`` for tonight.

    ``start_mode``: ``now`` => ``now``; ``dusk``/``dawn`` => the twilight crossing
    + ``start_offset_min``; ``time`` => the next ``start_time``. ``stop_mode``:
    ``none`` => no stop (``None``); ``dawn``/``time`` similarly. ``max_run_min``,
    when set, also caps the stop to ``start + max_run_min`` (whichever is sooner).
    A boundary that cannot be resolved (e.g. polar dusk) yields ``None`` there.

    NB: boundaries anchor to *tonight* (backward + forward sun search / nearest
    clock occurrence). The engine resolves this ONCE at run start and freezes the
    ``(start, stop)`` pair, then compares live ``now`` against the frozen window —
    so a dawn that passes mid-run closes the window instead of re-resolving into
    tomorrow (§1.6 / gating_status ``window=`` param).
    """
    lat, lon = _lat_lon(site)
    start = _resolve_event_ts(sched.start_mode, sched.start_offset_min,
                              sched.start_time, lat, lon, twilight_deg, now)
    stop = _resolve_event_ts(sched.stop_mode, sched.stop_offset_min,
                             sched.stop_time, lat, lon, twilight_deg, now)
    # max_run_min caps the window relative to the resolved start.
    if sched.max_run_min and start is not None:
        cap = start + sched.max_run_min * 60.0
        stop = cap if stop is None else min(stop, cap)
    return start, stop


# --------------------------------------------------------------------- meridian

def hour_angle_h(ra_hours: float, lon_deg: float, now: float | None = None) -> float:
    """Signed hour angle HA = LST - RA, wrapped to ``(-12, 12]``.

    Negative => target is EAST of the meridian (rising toward transit); positive
    => WEST (past transit). Single source of the HA truth shared by the meridian
    countdown (:func:`hours_to_meridian_flip`) and the PRO-14 hour-angle gate
    (:func:`constraint_gate`).

    THE BODY MOVED to ``catalog.coords`` so the device layer can read the same
    one (the AM5's destination-pier prediction needs it, and a driver importing
    from the sequence engine is a layering this package does not otherwise
    have). This delegates rather than duplicating: two hour-angle functions is
    how the simulator ended up with two pier-side oracles that disagreed."""
    return coords_hour_angle_h(ra_hours, lon_deg, now)


#: Degrees of clearance the tube must keep above the horizon at its LOWEST point
#: before a meridian flip is judged unnecessary. This is real clearance, not a
#: rounding allowance: skipping a flip is the one direction of this decision that
#: can put a tube into a pier, so the margin has to cover a mount whose geometry
#: we do not model.
MERIDIAN_POLE_CLEARANCE_DEG = 10.0


def lower_culmination_deg(dec_deg: float, lat_deg: float) -> float:
    """Altitude of a target at LOWER culmination (hour angle 12h), in degrees.

    ``sin(alt) = sin(lat)sin(dec) + cos(lat)cos(dec)cos(180) = -cos(lat + dec)``,
    so this is ``asin(-cos(lat + dec))`` — exact in both hemispheres and for a
    target on the wrong side of the equator (which lands far below the horizon,
    as it should). It reduces to the familiar ``lat + dec - 90`` whenever that
    sum lies in ``[0, 180]``, which is every circumpolar northern case.

    Why this number: the tube sits ``90 - dec`` degrees off the polar axis and
    sweeps a cone of that half-angle as the mount turns, so the lowest altitude
    the tube ever reaches over a full rotation IS the target's lower culmination.
    """
    return math.degrees(math.asin(
        max(-1.0, min(1.0, -math.cos(math.radians(lat_deg + dec_deg))))))


def flip_unnecessary_over_pole(
        dec_deg: float | None, lat_deg: float | None,
        clearance_deg: float = MERIDIAN_POLE_CLEARANCE_DEG) -> bool:
    """Whether a meridian flip is pointless for this target from this site.

    True only when the tube never points below the horizon ANYWHERE in the
    mount's rotation — i.e. the target's lower culmination clears
    ``clearance_deg``. A tube that never points down cannot be pointing at the
    pier, the tripod or the ground, so the meridian crossing at the top of the
    circle is just the top of a circle the mount can follow the whole way round.

    NOT ``dec > lat``. That only says the target crosses on the pole side of the
    zenith, which is weaker and wrong at low latitudes: from the equator a
    dec +15 target culminates north of the zenith while its tube sweeps a
    75-degree cone reaching 75 degrees BELOW the horizon.

    HONEST ABSENCE FAILS SAFE. An unreadable declination or latitude means the
    geometry is unknown, and not knowing has to mean taking the flip.
    """
    try:
        dec = float(dec_deg)              # type: ignore[arg-type]
        lat = float(lat_deg)              # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if math.isnan(dec) or math.isnan(lat) or math.isinf(dec) or math.isinf(lat):
        return False
    return lower_culmination_deg(dec, lat) >= float(clearance_deg)


def hours_to_meridian_flip(ra_hours: float, lon_deg: float,
                           now: float | None = None) -> float:
    """Server-side hours until the target at ``ra_hours`` reaches the meridian
    flip point, derived from the hour angle ``HA = LST - RA``.

    This is the SAME math the hub's Monitor uses (``hub._compute_meridian``): the
    single source of the flip countdown so the engine and the UI agree. It is the
    *authoritative* countdown because no production mount driver reports a usable
    value — NINA's ``TimeToMeridianFlip`` is provably never negative (it wraps
    ~0→12h at the crossing) and Alpaca/sim report ``None`` — so an engine that
    waited for the device to report ``<= 0`` would never flip (the live bug that
    tracks a GEM counterweight-up into the pier).

    Sign convention (wrapped to ``(-12, 12]``): **positive** while the target is
    still EAST of the meridian (counting down to the flip); **<= 0** once it has
    crossed and a German mount is tracking counterweight-up and must flip.
    """
    return -hour_angle_h(ra_hours, lon_deg, now)


#: Minutes before meridian transit at which a GEM flip is triggered.
#:
#: NOT zero, and this is the whole fix. Measured on the rig's AM5: tracking was
#: refused at -7.6 min (NGC 7129, 2026-08-21) and -4.7 min (NGC 6946,
#: 2026-08-22), both BEFORE transit, both answering :Te# with 0. A flip
#: triggered at the crossing is always too late by several minutes.
#:
#: 10 minutes clears the worse of the two measurements with a 2.4 minute margin
#: and costs, when it was not strictly needed, one re-slew and one re-centre -
#: about four minutes. The asymmetry is the argument: four minutes against the
#: rest of the night.
#:
#: THE DATES ARE THE MORNING SIDE OF THE NIGHT. NGC 7129 refused at 00:44 on
#: 2026-08-21, i.e. during the night of 08-20/21; NGC 6946 refused at 23:35
#: LOCAL ON 2026-08-21, i.e. the evening that opened the night of 08-21/22. The
#: -4.7 min hour angle reproduces from 2026-08-21 23:35 and does NOT reproduce
#: from 2026-08-22 23:35 (that evening the same target is only -0.8 min out),
#: which matters because that number is the tighter of the two measurements the
#: lead is sized against.
#:
#: 2.4 minutes is NOT the whole margin: the frame loop can put a dither, a
#: filter change and a five-minute autofocus sweep between two flip checks, and
#: none of that fits in a one-exposure window. The engine closes that gap by
#: re-checking the flip AFTER those events rather than by inflating this number
#: - see `SequenceEngine._maybe_meridian_flip`'s caller.
MERIDIAN_FLIP_LEAD_MIN = 10.0

#: The upper bound on a plan's own lead. An hour is already far more than any
#: mount's limit needs; past that the setting stops being a lead and starts
#: being "flip immediately, always", which is a different (and unasked-for)
#: behaviour. Enforced by the ``SequencePlan`` field, not here, so a bad value
#: is refused at the edge instead of silently clamped mid-run.
MERIDIAN_FLIP_LEAD_MAX_MIN = 60.0

#: How far PAST transit a target may already be at acquisition and still arm
#: the flip, in MINUTES, on top of the plan's own flip lead.
#:
#: THE ARMING HAS TO MOVE WITH THE TRIGGER. `_flip_armed` gates the flip AND
#: its decline log, so a trigger that fires earlier than the latch arms is a
#: silent skip - which is exactly how 2026-08-21 and 08-22 produced no flip
#: decision in the log at all. The trigger now fires ``lead`` minutes before
#: transit, so the latch has to arm at least that far past it, or a target
#: acquired inside its own lead window never flips at all.
#:
#: THE MARGIN IS FIVE MINUTES AND NOT AN HOUR, and the difference is measured
#: cost. Arming a whole hour past transit was tried first, on the argument that
#: the mount's own idea of the meridian is drifted. It is - but the flip
#: countdown is computed from the TARGET's catalogue RA and the site longitude
#: (`hours_to_meridian_flip`), never from the mount's readout, so the drift
#: does not reach this decision at all. What a flat hour does reach is every
#: re-acquisition inside that hour: `_setup_target` re-runs on a safety-pause
#: resume, a roof reopen and a cloud-hold release, and each one armed a latch
#: that fired a full stop-guide / re-slew / re-solve / re-centre /
#: guider-recalibrate / post-flip-autofocus - about eight minutes of sky - on a
#: mount that `goto_and_center` had placed on the correct side seconds earlier.
#: Five minutes covers the case the design actually named (a target acquired
#: inside the lead window, plus the few minutes a slew and an initial autofocus
#: take between the countdown being read and the frame loop starting) and stops
#: there.
FLIP_ARM_MARGIN_MIN = 5.0


def flip_should_arm(hours_to_flip: float,
                    lead_min: float | None = None) -> bool:
    """Whether a target acquired with this countdown owes a meridian flip.

    ``hours_to_flip`` is :func:`hours_to_meridian_flip` - positive while the
    target is still east of the meridian. ``lead_min`` is the plan's own flip
    lead (:data:`MERIDIAN_FLIP_LEAD_MIN` when not given), because the boundary
    is defined RELATIVE TO THE TRIGGER: see :data:`FLIP_ARM_MARGIN_MIN`.
    """
    try:
        h = float(hours_to_flip)
    except (TypeError, ValueError):
        return False
    if math.isnan(h):
        return False
    lead = MERIDIAN_FLIP_LEAD_MIN if lead_min is None else lead_min
    try:
        lead = float(lead)
    except (TypeError, ValueError):
        lead = MERIDIAN_FLIP_LEAD_MIN
    if lead != lead:                            # NaN
        lead = MERIDIAN_FLIP_LEAD_MIN
    lead = min(max(0.0, lead), MERIDIAN_FLIP_LEAD_MAX_MIN)
    return h > -((lead + FLIP_ARM_MARGIN_MIN) / 60.0)


def constraint_gate(target: "Target", site: dict[str, Any],
                    now: float) -> tuple[str, str, float] | None:
    """Evaluate the PRO-14 pro constraints (hour angle / moon sep / moon illum) at
    ``now``. Returns ``None`` when all are satisfied or off, else
    ``(state, reason, eta_s)`` where state is ``waiting`` or ``window_closed`` —
    the same enforce-and-skip vocabulary as the altitude gate.

    Moon-sep / illumination are IMAGE-QUALITY gates: they only bite while the Moon
    is UP (mirrors ``visibility._moon_factor``'s moon-up rule); a Moon below the
    horizon imposes no gate. This is the scheduler path only — unlike the Sun, the
    Moon is not a motion-boundary safety constraint."""
    sched = target.schedule
    lat, lon = _lat_lon(site)

    # --- hour angle (altitude-independent; predictable) ---
    lim = float(getattr(sched, "max_hour_angle_h", 0.0) or 0.0)
    if lim > 0.0:
        ha = hour_angle_h(target.ra_hours, lon, now)
        if ha > lim:
            return ("window_closed", f"past hour-angle limit (+{lim:g}h)", 0.0)
        if ha < -lim:
            return ("waiting", f"before hour-angle window (-{lim:g}h)",
                    (-lim - ha) * 3600.0)

    # --- moon (only while the Moon is up — moon-up gates the constraint) ---
    sep_min = float(getattr(sched, "min_moon_sep_deg", 0.0) or 0.0)
    illum_max = float(getattr(sched, "max_moon_illum_pct", 0.0) or 0.0)
    if sep_min > 0.0 or illum_max > 0.0:
        m_ra, m_dec = moon_radec(now)
        m_alt, _ = altaz(m_ra, m_dec, lat, lon, now)
        if m_alt > 0.0:
            if illum_max > 0.0:
                pct = moon_illumination(now) * 100.0
                if pct > illum_max:
                    return ("waiting",
                            f"moon too bright ({pct:.0f}% > {illum_max:g}%)", 0.0)
            if sep_min > 0.0:
                sep = angular_sep_deg(target.ra_hours, target.dec_deg, m_ra, m_dec)
                if sep < sep_min:
                    return ("waiting",
                            f"too close to the Moon ({sep:.0f} deg < {sep_min:g})",
                            0.0)
    return None


# --------------------------------------------------------------------- target alt

def target_altitude(ra_h: float, dec_deg: float, lat: float, lon: float,
                    t: float) -> float:
    """The target's altitude (degrees) at unix time ``t``."""
    alt, _ = altaz(ra_h, dec_deg, lat, lon, t)
    return alt


def target_max_altitude(ra_h: float, dec_deg: float, lat: float, lon: float,
                        start_ts: float | None, stop_ts: float | None) -> float:
    """Peak target altitude across ``[start_ts, stop_ts]`` (the pre-flight
    'never rises above floor' check — §1.6).

    When ``start_ts`` is ``None`` the scan defaults to *now* (``time.time()``);
    when ``stop_ts`` is ``None`` it scans one sidereal day forward so a transit is
    always captured. Coarse 10-min sampling — fine enough for a 'never rises'
    warning."""
    t0 = start_ts if start_ts is not None else time.time()
    t1 = stop_ts if stop_ts is not None else t0 + _SIDEREAL_DAY_S
    if t1 < t0:
        t0, t1 = t1, t0
    peak = -90.0
    steps = max(1, int((t1 - t0) / _PEAK_STEP_S))
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * (i / steps)
        peak = max(peak, target_altitude(ra_h, dec_deg, lat, lon, t))
    return peak


# --------------------------------------------------------------------- gating

def gating_status(target: "Target", site: dict[str, Any], twilight_deg: float,
                  now: float, target_alt: float | None = None,
                  window: tuple[float | None, float | None] | None = None
                  ) -> dict[str, Any]:
    """Decide whether ``target`` is runnable right now.

    Returns ``{state, reason, eta_s, start_ts, stop_ts}`` where ``state`` is one
    of:

    * ``ready`` — window is open and the target is at/above its start gate now.
    * ``waiting`` — window hasn't opened yet (``eta_s`` = seconds until start), or
      the window is open but the target is still below its start altitude
      (``eta_s`` = best-effort seconds to the gate, 0 if unknown).
    * ``window_closed`` — the stop boundary is already in the past.
    * ``never_rises`` — the target never clears its start gate across the whole
      window (the pre-flight warning case, C2-11).

    ``target_alt`` may be passed in (the engine already has a fresh altitude);
    otherwise it is computed at ``now``.

    ``window`` is the engine's FROZEN ``(start_ts, stop_ts)`` for this run — when
    supplied it is used verbatim instead of re-resolving, so a live ``now`` past a
    frozen dawn closes the window (instead of the boundary re-resolving into
    tomorrow every scheduler tick — the §1.6 window-freeze bug). When ``None``
    (pre-flight / one-shot callers) it resolves the window at ``now`` as before.
    """
    lat, lon = _lat_lon(site)
    sched = target.schedule
    if window is not None:
        start_ts, stop_ts = window
    else:
        start_ts, stop_ts = resolve_window(sched, site, twilight_deg, now)
    gate = float(sched.min_altitude_deg or 0.0)
    if target_alt is None:
        target_alt = target_altitude(target.ra_hours, target.dec_deg, lat, lon, now)

    def out(state: str, reason: str, eta_s: float) -> dict[str, Any]:
        return {"state": state, "reason": reason, "eta_s": max(0.0, eta_s),
                "start_ts": start_ts, "stop_ts": stop_ts}

    # 1. window already closed?
    if stop_ts is not None and now >= stop_ts:
        return out("window_closed", "observing window has closed", 0.0)

    # 2. never clears the start gate across the window? (pre-flight warning)
    if gate > 0.0:
        peak = target_max_altitude(target.ra_hours, target.dec_deg, lat, lon,
                                   start_ts, stop_ts)
        if peak < gate:
            return out("never_rises",
                       f"never rises above {gate:g} deg tonight", 0.0)

    # 3. window not open yet => waiting on the clock.
    if start_ts is not None and now < start_ts:
        return out("waiting", "waiting for start time", start_ts - now)

    # 4. window open but target still below its start gate => waiting on altitude.
    if gate > 0.0 and target_alt < gate:
        eta = _time_to_gate(target, lat, lon, gate, now, stop_ts)
        return out("waiting", f"below start altitude ({gate:g} deg)",
                   eta if eta is not None else 0.0)

    # 4b. pro constraints (moon sep / illumination / hour angle) — PRO-14, same
    #     enforce-and-skip model as the altitude gate above.
    cg = constraint_gate(target, site, now)
    if cg is not None:
        state, reason, eta = cg
        # a constraint-"waiting" target has an OPEN clock window (start_ts is in
        # the past); null the start anchor so the engine's earliest-waiter branch
        # skips it and it rides the bounded else-sleep instead of busy-spinning on
        # a past start_ts. window_closed keeps its window (it's skipped anyway).
        start = None if state == "waiting" else start_ts
        return {"state": state, "reason": reason, "eta_s": max(0.0, eta),
                "start_ts": start, "stop_ts": stop_ts}

    # 5. open and high enough.
    return out("ready", "", 0.0)


def _time_to_gate(target: "Target", lat: float, lon: float, gate: float,
                  now: float, stop_ts: float | None) -> float | None:
    """Best-effort seconds until the target first reaches ``gate`` after ``now``
    (within the window / one sidereal day). ``None`` if it never does."""
    horizon = stop_ts if stop_ts is not None else now + _SIDEREAL_DAY_S
    steps = max(1, int((horizon - now) / _PEAK_STEP_S))
    for i in range(1, steps + 1):
        t = now + i * _PEAK_STEP_S
        if target_altitude(target.ra_hours, target.dec_deg, lat, lon, t) >= gate:
            return t - now
    return None


def schedule_order(targets: list["Target"], site: dict[str, Any],
                   twilight_deg: float, now: float) -> list["Target"]:
    """Targets sorted by resolved window start (C1-23 skip-ahead helper).

    Targets whose start cannot be resolved (or have no start) sort last but keep
    their relative order (stable sort). This is the order the engine walks when it
    skips ahead to the first ready target instead of blocking on a waiting head.
    """
    def key(t: "Target") -> float:
        start, _ = resolve_window(t.schedule, site, twilight_deg, now)
        return start if start is not None else float("inf")

    return sorted(targets, key=key)


#: How close the MOUNT'S OWN limit has to be before it overrules the
#: "no flip needed over the pole" shortcut.
#:
#: The AM5 stopped tracking five minutes past the meridian on 2026-08-19, so the
#: window has to be wider than that to leave time to stop guiding, flip,
#: re-solve and re-centre. 20 minutes is comfortably inside one exposure of
#: warning and still narrow enough that a mount reporting a genuine half-hour
#: does not trigger a flip nobody needs.
MOUNT_LIMIT_FLIP_WINDOW_H = 20.0 / 60.0


def flip_forced_by_mount(device_hours: float | None) -> bool:
    """Does the MOUNT'S own meridian limit demand a flip, whatever the geometry?

    `flip_unnecessary_over_pole` answers a question about the TUBE: at high
    declination the lower culmination clears the horizon, so the tube never
    points down and there is nothing to hit. That is true, and skipping the flip
    saves a re-slew, a re-solve, a re-centre and 180 degrees of field rotation
    mid-stack.

    It is not the only question. A ZWO AM5 enforces its own limit regardless of
    where the tube is pointing: on 2026-08-19 the engine declined the flip for
    NGC 7129 at 00:49 on exactly that geometric reasoning, and the mount stopped
    tracking at 00:54 on its own authority. The run then shot 21 streaked frames
    before anything noticed.

    A device countdown is the mount stating a fact about itself, so it outranks
    our opinion about the tube. Absence is not a fact: most mounts report
    nothing useful, and a missing value must not manufacture a flip the geometry
    says is pure cost.
    """
    if device_hours is None:
        return False
    return 0.0 < float(device_hours) <= MOUNT_LIMIT_FLIP_WINDOW_H


def mount_is_gem(pier_side: "str | None") -> bool:
    """Is this a German equatorial, i.e. a mount that HAS a meridian limit?

    A mount that reports a real east/west pier side is a GEM. Fork and alt-az
    mounts report unknown/none — they have no pier to be on a side of, and no
    meridian flip. Mirrors `Hub._is_gem`, which has used the same test to drive
    the UI's flip indicator since it shipped.
    """
    return str(pier_side or "").strip().lower() in ("east", "west")


def flip_can_be_skipped(dec_deg: "float | None", lat_deg: "float | None",
                        pier_side: "str | None",
                        clearance_deg: float = MERIDIAN_POLE_CLEARANCE_DEG) -> bool:
    """May this target cross the meridian without a flip?

    THE TUBE CLEARING THE GROUND IS NOT THE SAME AS THE MOUNT AGREEING TO KEEP
    TRACKING, and conflating the two cost two whole nights.

    `flip_unnecessary_over_pole` proves something real: at high declination the
    tube's lowest point over a full rotation still clears the horizon, so it
    cannot strike the pier, the tripod or the ground. Its conclusion — "the
    meridian crossing is just the top of a circle the mount can follow the whole
    way round" — is a claim about the MOUNT, drawn from a fact about the TUBE,
    and a GEM does not follow it. A German equatorial enforces its own meridian
    limit in firmware, at an hour angle, with no opinion about where the tube is
    pointing.

    Measured on this rig, both times on NGC 7129 (dec +66.1, lower culmination
    13.5 deg, comfortably clear):

        2026-08-19  declined 00:49  ->  ZWO AM5 stopped tracking 00:54
        2026-08-20  declined 00:16  ->  ZWO AM5 stopped tracking 00:49

    The second night the run then set the target aside and parked, ending the
    session with 73 of 175 frames and four hours of clear sky left.

    So: a GEM flips. The cost of a flip that was not strictly needed is one
    re-slew, one re-centre and a field rotation — about four minutes, which is
    what the over-pole optimisation was written to save. The cost of missing one
    is the rest of the night. That asymmetry is the whole argument, and the
    original docstring named the risk itself: "this makes it flip less, which is
    the direction that can hurt".

    UNKNOWN PIER SIDE NOW MEANS FLIP, reversing what shipped on 2026-08-20.

    That version fell back to the tube geometry when the pier side could not be
    read, on the argument that a fork mount and a quiet GEM look identical from
    here. The ambiguity is real; the errors are not symmetric. Treating a GEM as
    a fork has now cost four nights — 2026-08-19, 08-20, 08-21, 08-22. Treating
    a fork as a GEM costs one unnecessary re-slew per meridian crossing, and a
    fork owner who minds already has `SequencePlan.meridian_flip = False`.

    This is "unreadable is not a verdict" (see `astrodeck-dead-link-recovery`):
    an absent pier side is a FAILURE TO MEASURE, and the old code was reading it
    as a measurement of "no pier".

    So the honest answer today is that nothing excuses a flip: this returns
    False for every mount. ``dec_deg``/``lat_deg``/``clearance_deg`` are kept —
    and `flip_unnecessary_over_pole` is still exported and still correct about
    the tube — because the geometry is not what was wrong, only its authority
    over a mount that has its own limit. If a driver ever reports a mount TYPE
    (rather than a side that a fork simply cannot have), the optimisation
    reconnects here and nowhere else.
    """
    if mount_is_gem(pier_side):
        return False
    # Not `flip_unnecessary_over_pole(...)`: see above. The tube clearing the
    # ground says nothing about whether the mount will keep tracking.
    return False
