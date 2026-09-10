"""What the TPPA adjusting phase computes, and what actually reaches the operator.

Surface: ``astrodeck_native.tppa_update`` plus the publish path in
``astrodeck/polar/native.py`` (``_capture_and_solve`` geometry, ``_publish_error``,
``_log_pa_spread``, ``_reject_implausible_fit``) and the session state that
``/api/polar/state`` serves verbatim.

Two things make this surface worth its own file.

The first is that the adjusting phase does NOT re-fit anything. The axis model is
frozen at the moment the three measurement points close, and every subsequent
reading is a pixel-space RE-SCALE of that frozen error (``update.rs``
``calculate_error_details``). A rescale has no residuals and no internal notion
of being wrong, so the only way to know it is right is to compare it against an
independently-computed physical truth — which is what the forward model below
does. It places a mount axis at a KNOWN offset from the pole, traces the small
circle the optics sweep, and then applies the SAME rigid rotation a physical
altitude/azimuth adjuster applies to both the axis and the tube. The residual
axis error is then computed here, in Python, from the rotated axis — never from
the engine — so "the engine converges" is a claim about physics and not about the
engine agreeing with itself.

The second is that this is the last hop before a human acts. A polar routine that
names the wrong bolt, or the wrong direction, or publishes a number from a fit
that collapsed, is worse than one that says nothing: on 2026-08-06 a collapsed
fit reached the operator as "7271 arcminutes — adjust the mount". So the knob
directions are checked for every sign combination in BOTH hemispheres against a
physically-placed axis, the units are pinned in arcminutes at every hop, and the
plausibility gate is checked as an integration (does the UI actually see a
terminal error?) rather than only as a unit.

The forward-model helpers deliberately reuse ``devices/sim.py``'s horizontal
<-> equatorial transforms (documented as the exact inverse of the Rust crate's
geometric transform, and proven so by ``test_polar_native.py``). Everything that
encodes the POLAR-ERROR convention — where the axis sits for a given (alt, az)
error, in either hemisphere, and what the residual is after an adjustment — is
written out here from the physical definition, because that convention is exactly
what these tests exist to check.
"""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pytest

from astrodeck.catalog.coords import lst_hours
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import (_altaz_from_neu, _cross, _horiz_to_equ,
                                   _jd_from_unix, _neu_from_altaz, _normalize,
                                   _rodrigues)
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.polar import native as nat
from astrodeck.polar.native import (MAX_PLAUSIBLE_ERROR_DEG,
                                    MIN_POLE_DISTANCE_DEG, _DONE_THRESHOLD_ARCMIN,
                                    _publish_error, _reject_implausible_fit)

try:
    import astrodeck_native as an
    _HAS_NATIVE = True
except ImportError:  # pragma: no cover - the wheel is built in this venv
    an = None
    _HAS_NATIVE = False

needs_engine = pytest.mark.skipif(not _HAS_NATIVE,
                                  reason="astrodeck_native wheel not installed")

# Two invented sites, one per hemisphere. Nothing here is anybody's back garden.
_NORTH = (45.0, -122.0)
_SOUTH = (-33.9, 151.2)

#: A fixed instant, so the sidereal time (and therefore every RA below) is the
#: same on every run. 2026-04-27T05:33:20Z.
_T0 = 1_775_000_000.0

#: Angular radius of the small circle the optics trace about the mount axis.
#: 38 degrees is the geometry the driver actually produces on a real target and
#: the one the conditioning note in ``native.py`` was measured against.
_RHO_DEG = 38.0

#: Three measurement points 12 degrees apart — ``_RA_STEP_HOURS`` expressed as
#: rotation about the mount axis. Deliberately the badly-conditioned real
#: geometry rather than a comfortable one.
_PHASES = (90.0, 102.0, 114.0)

#: Image geometry the driver hands the engine on a real rig.
_GEOM = {"arcsec_per_pixel": 1.55, "image_width_px": 4144.0,
         "image_height_px": 2822.0}
#: The simulator has no atmosphere; ``_options`` turns refraction off the same
#: way, which is what lets the geometric forward model below invert exactly.
_OPTS = dict(_GEOM, pressure_hpa=0.0)


# --------------------------------------------------------------- forward model

def _site(lat: float, lon: float) -> dict:
    return {"latitude_deg": lat, "longitude_deg": lon, "elevation_m": 0.0}


def _axis_vector(lat_deg: float, alt_err_deg: float, az_err_deg: float) -> tuple:
    """The topocentric [N,E,Up] direction of a mount RA axis carrying a KNOWN
    polar error, written from the definition of that error rather than from the
    engine's inverse.

    Northern: the axis points north at the pole's altitude, ``alt_err`` is how
    far ABOVE the pole it sits and ``az_err`` how far EAST of north.
    Southern: the axis points south, ``alt_err`` is how far BELOW the pole it
    sits (``pole - axis_alt``, the southern branch of
    ``calculate_mount_axis_error``) and ``az_err`` how far WEST of south.
    """
    if lat_deg > 0.0:
        return _neu_from_altaz(abs(lat_deg) + alt_err_deg, az_err_deg)
    return _neu_from_altaz(abs(lat_deg) - alt_err_deg, 180.0 + az_err_deg)


def _axis_error(axis: tuple, lat_deg: float) -> tuple[float, float]:
    """(alt_err, az_err) in DEGREES for an axis direction — the exact inverse of
    :func:`_axis_vector`, and the independent oracle every convergence assertion
    below is measured against."""
    alt, az = _altaz_from_neu(axis)
    if lat_deg > 0.0:
        return alt - abs(lat_deg), ((az + 180.0) % 360.0) - 180.0
    return abs(lat_deg) - alt, ((az % 360.0) % 360.0) - 180.0


def _pointings(axis: tuple, phases=_PHASES) -> list[tuple]:
    """The three directions the optics point at, ``_RHO_DEG`` from ``axis`` and
    separated by pure rotation ABOUT that axis — i.e. exactly what a mount slewing
    in RA does, and nothing else."""
    tilt = _normalize(_cross(axis, (0.0, 0.0, 1.0)))
    ref = _rodrigues(axis, tilt, math.radians(_RHO_DEG))
    return [_rodrigues(ref, axis, math.radians(p)) for p in phases]


def _solve_dict(v: tuple, lat: float, lon: float, t: float = _T0,
                pa_deg: float = 0.0) -> dict:
    """One solve payload in the shape ``polar/native.py`` builds."""
    alt, az = _altaz_from_neu(v)
    ra_deg, dec_deg = _horiz_to_equ(alt, az, lat, lon, _jd_from_unix(t))
    return {"ra_hours": ra_deg / 15.0, "dec_deg": dec_deg,
            "timestamp_unix_s": t, "position_angle_deg": pa_deg}


def _adjust(v: tuple, d_az_deg: float, d_alt_deg: float) -> tuple:
    """Apply the rigid rotation a mount's azimuth and altitude adjusters apply:
    about the vertical, then about the (co-rotated) horizontal axis. Applied to
    the axis it says where the axis ends up; applied to the tube it says where
    the field goes. That they are the SAME rotation is the physical fact the
    continuous-update math depends on."""
    z = (0.0, 0.0, 1.0)
    turned = _rodrigues(v, z, math.radians(d_az_deg))
    alt_axis = _rodrigues((0.0, 1.0, 0.0), z, math.radians(d_az_deg))
    return _rodrigues(turned, alt_axis, math.radians(d_alt_deg))


def _correction(alt_err_deg: float, az_err_deg: float, fraction: float) -> tuple:
    """The (d_az, d_alt) knob move that removes ``fraction`` of a northern-site
    error. Verified independently by :func:`test_the_forward_model_is_honest`."""
    return (-az_err_deg * fraction, alt_err_deg * fraction)


def _fit(solves: list[dict], lat: float, lon: float, opts: dict | None = None):
    return an.tppa_from_three(solves, _site(lat, lon), dict(opts or _OPTS))


# ------------------------------------------------------- the model's own honesty

@needs_engine
def test_the_forward_model_is_honest() -> None:
    """A forward model nobody checks makes every test built on it vacuous.

    Four independent claims, none of which involve the engine: the axis lands
    where the error says it does (both hemispheres), the three pointings really
    are ``_RHO_DEG`` from that axis and separated by pure rotation about it, the
    geometry is one the driver would ACCEPT (>= ``MIN_POLE_DISTANCE_DEG`` from
    the pole, above the horizon), and the knob move in :func:`_correction`
    genuinely nulls the error.
    """
    for lat, _lon in (_NORTH, _SOUTH):
        for alt_e, az_e in ((0.5, 0.4), (-0.5, 0.4), (0.5, -0.4), (-0.5, -0.4)):
            axis = _axis_vector(lat, alt_e, az_e)
            got_alt, got_az = _axis_error(axis, lat)
            assert got_alt == pytest.approx(alt_e, abs=1e-9), (lat, alt_e)
            assert got_az == pytest.approx(az_e, abs=1e-9), (lat, az_e)

    axis = _axis_vector(_NORTH[0], 0.5, 0.4)
    pts = _pointings(axis)
    assert len(pts) == 3
    for p in pts:
        sep = math.degrees(math.acos(sum(a * b for a, b in zip(p, axis))))
        assert sep == pytest.approx(_RHO_DEG, abs=1e-9), sep
        alt, _az = _altaz_from_neu(p)
        assert alt > 10.0, f"forward model put a measurement point at alt {alt}"
    for a, b in zip(pts, pts[1:]):
        solve_a = _solve_dict(a, *_NORTH)
        solve_b = _solve_dict(b, *_NORTH)
        assert abs(solve_a["dec_deg"] - solve_b["dec_deg"]) < 1.0
        assert 90.0 - abs(solve_a["dec_deg"]) >= MIN_POLE_DISTANCE_DEG

    # The correction really is a correction: applying it to the AXIS nulls the
    # error. If this were the wrong sign every convergence test below would be
    # measuring the engine against a diverging mount and still "passing".
    nulled = _adjust(axis, *_correction(0.5, 0.4, 1.0))
    residual = math.hypot(*_axis_error(nulled, _NORTH[0]))
    assert residual < 0.01, f"the correction does not null the axis: {residual} deg"
    # ...and half of it removes half the error.
    half = _adjust(axis, *_correction(0.5, 0.4, 0.5))
    assert math.hypot(*_axis_error(half, _NORTH[0])) == pytest.approx(
        math.hypot(0.25, 0.2), abs=0.01)


# ------------------------------------------- tppa_update: does it converge?

_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


@needs_engine
def test_the_live_error_falls_to_zero_as_the_mount_is_adjusted() -> None:
    """The headline claim of the adjusting phase: turn the knobs the right way
    and the number goes down, all the way to zero.

    The frozen model is never re-fit, so this is not self-evident — the reading
    is a pixel-space rescale of a number computed minutes earlier. Every step is
    checked against the residual error of the ROTATED AXIS, computed here from
    the axis direction, so the engine cannot pass by agreeing with itself.
    """
    lat, lon = _NORTH
    alt_e, az_e = 0.5, 0.4            # 30' and 24'
    axis = _axis_vector(lat, alt_e, az_e)
    pts = _pointings(axis)
    solves = [_solve_dict(p, lat, lon) for p in pts]
    out = _fit(solves, lat, lon)
    model, err0 = out["model"], out["error"]

    # The fit itself must first recover what was injected, or "converges to
    # zero" is a statement about the wrong starting point.
    assert err0["alt_arcmin"] == pytest.approx(alt_e * 60.0, abs=0.05)
    assert err0["az_arcmin"] == pytest.approx(az_e * 60.0, abs=0.05)
    assert err0["total_arcmin"] > 30.0, "nothing to converge from"

    assert len(_FRACTIONS) == 5, "the sweep lost its steps"
    reported: list[float] = []
    for f in _FRACTIONS:
        move = _correction(alt_e, az_e, f)
        truth = math.hypot(*_axis_error(_adjust(axis, *move), lat)) * 60.0
        live = an.tppa_update(model, _solve_dict(_adjust(pts[2], *move), lat, lon))
        assert live["total_arcmin"] == pytest.approx(truth, abs=0.3), (
            f"at {f:.0%} of the correction the mount is really {truth:.2f}' out "
            f"but the live reading says {live['total_arcmin']:.2f}'")
        reported.append(live["total_arcmin"])

    assert reported == sorted(reported, reverse=True), reported
    assert reported[-1] < 0.5, (
        f"a fully corrected mount still reads {reported[-1]:.2f}' — the live "
        "phase never lets the operator stop")
    assert reported[0] == pytest.approx(err0["total_arcmin"], abs=0.05)


@needs_engine
def test_the_first_update_reproduces_the_frozen_fit_exactly() -> None:
    """At the moment of freezing, the two entirely different computations — a
    plane through three vectors, and a pixel-space rescale — must agree. A drift
    here would show up on screen as the number jumping the instant the phase
    changes, with nothing having moved."""
    lat, lon = _NORTH
    axis = _axis_vector(lat, 0.5, 0.4)
    solves = [_solve_dict(p, lat, lon) for p in _pointings(axis)]
    out = _fit(solves, lat, lon)
    live = an.tppa_update(out["model"], solves[2])
    for key in ("alt_arcmin", "az_arcmin", "total_arcmin"):
        assert live[key] == pytest.approx(out["error"][key], abs=1e-6), key
    # The instruction must not change either.
    assert live["alt_direction"] == out["error"]["alt_direction"]
    assert live["az_direction"] == out["error"]["az_direction"]


@needs_engine
def test_a_tracking_mount_nobody_touches_reports_a_steady_error() -> None:
    """The adjusting phase runs for minutes while the sky turns. A mount that is
    tracking holds its RA/Dec, so the reading must not drift on the clock alone —
    an operator who walks away for five minutes must come back to the same
    number, not a growing one."""
    lat, lon = _NORTH
    axis = _axis_vector(lat, 0.5, 0.4)
    solves = [_solve_dict(p, lat, lon) for p in _pointings(axis)]
    out = _fit(solves, lat, lon)
    base = an.tppa_update(out["model"], solves[2])["total_arcmin"]
    for dt in (1.0, 60.0, 300.0, 1800.0):
        later = an.tppa_update(out["model"],
                               dict(solves[2], timestamp_unix_s=_T0 + dt))
        assert later["total_arcmin"] == pytest.approx(base, abs=1e-6), dt


@needs_engine
def test_overshooting_reverses_the_instruction() -> None:
    """Turn the bolt too far and the routine has to say so. Past the destination
    the signed errors flip and BOTH knob directions must reverse — otherwise the
    display keeps pointing the same way while the mount walks off the pole."""
    lat, lon = _NORTH
    alt_e, az_e = 0.5, 0.4
    axis = _axis_vector(lat, alt_e, az_e)
    pts = _pointings(axis)
    out = _fit([_solve_dict(p, lat, lon) for p in pts], lat, lon)
    before = out["error"]
    assert (before["alt_direction"], before["az_direction"]) == ("down", "left_west")

    move = _correction(alt_e, az_e, 1.5)
    truth_alt, truth_az = _axis_error(_adjust(axis, *move), lat)
    after = an.tppa_update(out["model"], _solve_dict(_adjust(pts[2], *move), lat, lon))
    assert truth_alt < 0.0 and truth_az < 0.0, "the overshoot did not overshoot"
    assert after["alt_arcmin"] == pytest.approx(truth_alt * 60.0, abs=0.3)
    assert after["az_arcmin"] == pytest.approx(truth_az * 60.0, abs=0.5)
    assert after["alt_direction"] == "up", after
    assert after["az_direction"] == "right_east", after


# ------------------------------------------------------------ knob directions

def _expected_instruction(lat: float, alt_err: float, az_err: float) -> tuple:
    """What the operator must be told, derived from where the axis IS.

    Altitude: the axis has to move toward the pole, so an axis above the pole
    must come down. Azimuth: name the compass direction the axis must move, then
    the hand — facing the pole you are facing north up here and south down there,
    so left is west in the north and east in the south.
    """
    northern = lat > 0.0
    axis_above_pole = alt_err > 0.0 if northern else alt_err < 0.0
    alt_word = "down" if axis_above_pole else "up"
    # Northern: az_err is degrees EAST of north. Southern: degrees WEST of south.
    axis_must_move = ("west" if az_err >= 0.0 else "east") if northern else \
                     ("east" if az_err >= 0.0 else "west")
    left_is = "west" if northern else "east"
    hand = "left" if axis_must_move == left_is else "right"
    return alt_word, f"{hand}_{axis_must_move}"


_SIGN_CASES = [(lat, alt_e, az_e)
               for lat in (_NORTH[0], _SOUTH[0])
               for alt_e in (0.5, -0.5)
               for az_e in (0.4, -0.4)]


@needs_engine
@pytest.mark.parametrize("lat,alt_err,az_err", _SIGN_CASES)
def test_the_knob_directions_are_right_in_both_hemispheres(lat, alt_err, az_err) -> None:
    """A polar routine that names the wrong bolt direction is worse than one that
    says nothing: the operator turns it, the number gets worse, and the natural
    reading of that is "I need to turn it further".

    The axis is PLACED at a known offset and the instruction is derived from
    where it sits (see :func:`_expected_instruction`) — not copied from the
    engine's own sign table, which is what the Rust unit test already does.
    """
    lon = _NORTH[1] if lat > 0 else _SOUTH[1]
    axis = _axis_vector(lat, alt_err, az_err)
    solves = [_solve_dict(p, lat, lon) for p in _pointings(axis)]
    err = _fit(solves, lat, lon)["error"]

    assert err["alt_arcmin"] == pytest.approx(alt_err * 60.0, abs=0.1)
    assert err["az_arcmin"] == pytest.approx(az_err * 60.0, abs=0.1)
    want_alt, want_az = _expected_instruction(lat, alt_err, az_err)
    assert err["alt_direction"] == want_alt, (
        f"axis {'above' if alt_err > 0 else 'below'} the pole at lat {lat}: "
        f"told the operator to move {err['alt_direction']}")
    assert err["az_direction"] == want_az, (
        f"lat {lat}, az error {az_err * 60:+.0f}': told the operator "
        f"{err['az_direction']}, should be {want_az}")


@needs_engine
def test_the_sign_cases_cover_every_combination() -> None:
    """A parametrisation that silently loses a case passes forever."""
    assert len(_SIGN_CASES) == 8
    # Eight distinct instructions: nothing collapses two sign combinations onto
    # the same advice, which is what a hemisphere mix-up looks like.
    assert len({_expected_instruction(*c) for c in _SIGN_CASES}) == 8
    assert {w for w, _ in map(lambda c: _expected_instruction(*c), _SIGN_CASES)} \
        == {"up", "down"}


# --------------------------------------------------------------- plate scale

@needs_engine
def test_the_live_error_does_not_depend_on_the_assumed_plate_scale() -> None:
    """``_capture_and_solve`` falls back to a hard-coded 1.55 arcsec/pixel when
    the solver reports no scale and optics are unset, which looks like a silent
    guess that would scale everything the operator is told.

    It is not, and this test is the guard on that: the continuous-update
    construction (``update.rs``) projects every point through the same 1/scale
    about the same image centre, so the corrected/original leg RATIO — the only
    thing the reported error depends on — is exactly scale-invariant. Measured
    here: a scale 2x, 0.5x and 6.5x wrong changes the answer by nothing at all.
    The fallback's only job is to be non-zero.

    If the construction ever stops being scale-invariant, this fails — and at
    that moment the hard-coded 1.55 stops being harmless and becomes a silent
    multiplier on the number a human turns a bolt by.
    """
    lat, lon = _NORTH
    axis = _axis_vector(lat, 0.5, 0.4)
    pts = _pointings(axis)
    solves = [_solve_dict(p, lat, lon) for p in pts]
    moved = _solve_dict(_adjust(pts[2], *_correction(0.5, 0.4, 0.5)), lat, lon)

    scales = (1.55, 3.10, 0.775, 10.0)
    assert len(scales) == 4
    readings = []
    for scale in scales:
        model = _fit(solves, lat, lon, dict(_OPTS, arcsec_per_pixel=scale))["model"]
        readings.append(an.tppa_update(model, moved)["total_arcmin"])
    for scale, got in zip(scales[1:], readings[1:]):
        assert got == pytest.approx(readings[0], abs=1e-9), (
            f"assuming {scale}\"/px instead of {scales[0]}\"/px moved the "
            f"reported error from {readings[0]:.4f}' to {got:.4f}'")

    # Sensor size is likewise inert (it only supplies the image centre).
    for w, h in ((1024.0, 768.0), (9576.0, 6388.0)):
        model = _fit(solves, lat, lon,
                     dict(_OPTS, image_width_px=w, image_height_px=h))["model"]
        assert an.tppa_update(model, moved)["total_arcmin"] == pytest.approx(
            readings[0], abs=1e-9)

    # And the INITIAL fit never looks at image geometry at all.
    bare = _fit(solves, lat, lon, {"pressure_hpa": 0.0})["error"]
    assert bare["total_arcmin"] == pytest.approx(
        _fit(solves, lat, lon)["error"]["total_arcmin"], abs=1e-9)


@needs_engine
def test_no_image_geometry_refuses_instead_of_guessing_inside_the_engine() -> None:
    """The engine will not invent a scale of its own: with none supplied the
    update raises rather than dividing by zero. That refusal is what the driver's
    1.55 fallback exists to keep out of the live loop."""
    lat, lon = _NORTH
    axis = _axis_vector(lat, 0.5, 0.4)
    solves = [_solve_dict(p, lat, lon) for p in _pointings(axis)]
    model = _fit(solves, lat, lon, {"pressure_hpa": 0.0})["model"]
    assert model["arcsec_per_pixel"] == 0.0
    with pytest.raises(ValueError, match="image geometry"):
        an.tppa_update(model, solves[2])


# ------------------------------------------------------- units: ARCMINUTES

@needs_engine
def test_the_engine_reports_arcminutes_not_degrees() -> None:
    """Hop 1. One degree of altitude error must come back as 60, not 1. The whole
    chain below is arcminutes by convention and nothing carries a unit label, so
    this is the only place a refactor to degrees would be caught."""
    lat, lon = _NORTH
    axis = _axis_vector(lat, 1.0, 0.0)
    solves = [_solve_dict(p, lat, lon) for p in _pointings(axis)]
    err = _fit(solves, lat, lon)["error"]
    assert err["alt_arcmin"] == pytest.approx(60.0, abs=0.1), err
    assert err["total_arcmin"] == pytest.approx(60.0, abs=0.1), err
    live = an.tppa_update(_fit(solves, lat, lon)["model"], solves[2])
    assert live["alt_arcmin"] == pytest.approx(60.0, abs=0.1), live


class _RecordingSession:
    """Just the ``_publish`` seam ``_publish_error`` writes through."""

    def __init__(self) -> None:
        self.published: list[dict] = []

    def _publish(self, **kw) -> None:
        self.published.append(kw)


def _engine_error(az: float, alt: float, **extra) -> dict:
    """An engine error dict in the shape the PyO3 layer returns."""
    return {"az_arcmin": az, "alt_arcmin": alt,
            "total_arcmin": math.hypot(az, alt),
            "az_direction": "left_west", "alt_direction": "down",
            "flags": [], "position_angle_spread_deg": 0.0, **extra}


def test_publish_error_passes_arcminutes_straight_through() -> None:
    """Hop 2. ``_publish_error`` must not convert: the canonical ``polar`` event
    is already arcminutes (the NINA driver multiplies its degrees by 60 to get
    here). A unit change on either side of this function would double-convert."""
    sess = _RecordingSession()
    _publish_error(sess, _engine_error(24.0, 30.0), phase="adjusting",
                   point_index=2, progress=0.85, message="adjust the mount")
    assert len(sess.published) == 1
    got = sess.published[0]
    assert got["az_error"] == 24.0 and got["alt_error"] == 30.0
    assert got["state"] == "running" and got["source"] == "native"
    assert got["phase"] == "adjusting" and got["point_index"] == 2


def test_the_session_state_the_api_serves_is_arcminutes() -> None:
    """Hop 3. ``/api/polar/state`` returns ``hub.polar.state`` verbatim, so the
    session's derived total is the number on the screen. 24' and 30' is 38.42',
    not 0.64 of anything."""
    sess = Hub().polar
    _publish_error(sess, _engine_error(24.0, 30.0), phase="adjusting",
                   point_index=2, progress=0.85, message="adjust the mount")
    assert sess.state["az_error"] == 24.0
    assert sess.state["alt_error"] == 30.0
    assert sess.state["total_error"] == pytest.approx(38.42, abs=0.01)


def test_publish_error_forwards_the_untrustworthy_fit_caveat() -> None:
    """The flags and the spread NUMBER drive the "this fit is not trustworthy"
    banner. Both must survive the hop, and a payload missing them must publish
    nulls rather than raise inside the driver's publish path."""
    sess = _RecordingSession()
    _publish_error(sess, _engine_error(24.0, 30.0, flags=["position_angle_spread_large"],
                                       position_angle_spread_deg=9.4),
                   phase="adjusting", point_index=2, progress=0.85, message="m")
    got = sess.published[0]
    assert got["flags"] == ["position_angle_spread_large"]
    assert got["position_angle_spread_deg"] == pytest.approx(9.4)

    bare = _RecordingSession()
    _publish_error(bare, {"az_arcmin": 1.0, "alt_arcmin": 2.0},
                   phase="adjusting", point_index=2, progress=0.85, message="m")
    assert bare.published[0]["flags"] == []
    assert bare.published[0]["position_angle_spread_deg"] is None


@needs_engine
def test_the_spread_caveat_is_frozen_into_every_live_update() -> None:
    """The spread is a property of the three MEASUREMENT frames, so it cannot be
    recomputed later — and it must not quietly vanish when the phase changes.
    A caveat that only appears on the first published reading is a caveat nobody
    sees: the operator is looking at the live number."""
    lat, lon = _NORTH
    axis = _axis_vector(lat, 0.5, 0.4)
    pts = _pointings(axis)
    spun = [_solve_dict(p, lat, lon, pa_deg=pa)
            for p, pa in zip(pts, (0.0, 9.4, 3.0))]
    out = _fit(spun, lat, lon)
    assert "position_angle_spread_large" in out["error"]["flags"]
    assert out["error"]["position_angle_spread_deg"] == pytest.approx(9.4)
    live = an.tppa_update(out["model"], spun[2])
    assert "position_angle_spread_large" in live["flags"], live
    assert live["position_angle_spread_deg"] == pytest.approx(9.4), live


# ------------------------------------------------- the driver: harness

class _Frame:
    def __init__(self, timestamp: float = _T0, shape=(4, 6)) -> None:
        self.timestamp = timestamp
        # Only ``.shape`` is ever read, so the driver's frames stay tiny; the
        # image-geometry test below is the one that needs real sensor dimensions.
        self.data = np.zeros(shape, dtype=np.uint16)


#: Declination of the fake target: 70 degrees from the pole, so the pole guard
#: lets the run start, and high enough at this latitude that the whole 24-degree
#: arc stays well above :data:`nat.LOW_MEASUREMENT_ALT_DEG`.
_TARGET_DEC_DEG = 20.0

#: Where the fake tube starts, as an HOUR ANGLE rather than an RA. The driver
#: now projects the arc's altitude from the first SOLVED position and refuses
#: below ``MIN_MEASUREMENT_ALT_DEG``, so a fixed RA is a fake whose meaning
#: changes every hour of the day — the same run is legal at midnight and refused
#: at noon. Half an hour EAST of the meridian is what an operator actually
#: points at for this routine: the arc then steps further east (``_ra_step_hours``
#: always steps away from the meridian) to HA -1.3h and -2.1h, which at Dec +20
#: from 45N runs 64 -> 54 degrees altitude.
_START_HA_H = -0.5


class _Solve:
    """A solve 70 degrees from the pole, so the pole guard lets the run start."""
    rotation_deg = 0.0
    success = True
    message = "ok"

    def __init__(self, ra_hours: float = 5.0, dec_deg: float = _TARGET_DEC_DEG,
                 pixel_scale_arcsec: float = 1.55):
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg
        self.pixel_scale_arcsec = pixel_scale_arcsec


class _Tel:
    """A mount that does the two things the driver's guards now ask about: it
    TRACKS, and it ARRIVES where it was sent.

    Both are load-bearing rather than decorative. ``_ensure_tracking`` reads the
    tracking state before the first frame, and ``_refuse_if_it_did_not_arrive``
    compares consecutive SOLVED right ascensions against the step it commanded —
    so a fake that accepted a slew and stayed put would be refused by the driver
    exactly as a stuck mount is, which is the whole point of that guard. The
    solved position below is read back off this object, so "the mount arrives"
    is modelled once, here, instead of being assumed by the capture stub."""

    def __init__(self, ra_hours: float = 5.0,
                 dec_deg: float = _TARGET_DEC_DEG) -> None:
        self.ra = ra_hours % 24.0
        self.dec = dec_deg
        self.tracking = False
        self.slews: list[tuple[float, float]] = []

    async def get_position(self):
        return self.ra, self.dec

    async def slew(self, ra, dec):
        self.slews.append((ra % 24.0, dec))
        self.ra = ra % 24.0
        self.dec = dec

    async def get_tracking(self):
        return self.tracking

    async def set_tracking(self, on):
        self.tracking = bool(on)


class _Cam:
    name = "cam"


class _DriverHub:
    mode = "sim"
    _motion_epoch = 0

    def __init__(self, start_ha_h: float = _START_HA_H) -> None:
        self.site = {"latitude": _NORTH[0], "longitude": _NORTH[1],
                     "elevation_m": 0.0}
        # HA = LST - RA, so an RA of LST - HA puts the tube at the hour angle we
        # asked for whenever this test happens to run.
        self._tel = _Tel((lst_hours(_NORTH[1]) - start_ha_h) % 24.0)

    def require(self, role):
        return self._tel if role == "telescope" else _Cam()

    async def yield_camera_for(self, why):
        pass

    def _check_solar(self, ra, dec):
        # Deliberately inert. The sun cone is a function of the wall-clock DATE,
        # and these tests point at the meridian NOW, so a live solar check would
        # refuse every run made in daylight. Sun avoidance has its own tests;
        # nothing here is about it.
        pass


class _StubEngine:
    """A Rust engine whose two entry points are scripted by the test."""

    def __init__(self, fit_error: dict, update_errors) -> None:
        self.fit_error = fit_error
        self._updates = update_errors
        self.update_calls = 0

    def tppa_from_three(self, solves, site, opts):
        return {"model": {"stub": True}, "error": dict(self.fit_error)}

    def tppa_update(self, model, solve):
        self.update_calls += 1
        err = self._updates(self.update_calls) if callable(self._updates) \
            else self._updates
        if isinstance(err, Exception):
            raise err
        return dict(err)


@pytest.fixture
async def driver(monkeypatch):
    """Drive the REAL ``run_native`` over a stubbed engine and a stubbed camera,
    with a real ``PolarAlignSession`` so ``_publish`` is production code."""
    monkeypatch.setattr(nat, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(nat, "_ADJUST_INTERVAL_S", 0.01)
    # The post-rotation settle is real time on a real rig and pure cost here;
    # its own behaviour is graded directly (see the settle tests), so every
    # run that only wants the LOOP pays nothing for it.
    monkeypatch.setattr(nat, "_SETTLE_AFTER_SLEW_S", 0.0)
    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: object())

    session = Hub().polar
    hub = _DriverHub()
    state: dict = {"captures": 0, "task": None}

    async def _fake_capture(_hub, _solver, _session=None):
        # The solve reports where the MOUNT actually is. That is what makes this
        # a fake telescope rather than a fake sky: the arc the driver verifies
        # against (_refuse_if_it_did_not_arrive) is produced by the slews it
        # itself commanded, so a driver that stopped rotating, rotated the wrong
        # way, or double-stepped would be caught here instead of being handed a
        # scripted RA that agrees with it no matter what it did.
        state["captures"] += 1
        ra, dec = await hub._tel.get_position()
        return _Frame(), _Solve(ra, dec), (1.55, 4144.0, 2822.0)

    monkeypatch.setattr(nat, "_capture_and_solve", _fake_capture)

    def launch(engine):
        monkeypatch.setattr(nat, "_native", engine)
        task = asyncio.create_task(nat.run_native(session, hub))
        session._task = task
        state["task"] = task
        return task

    state["launch"] = launch
    state["session"] = session
    state["hub"] = hub
    yield state
    task = state["task"]
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _wait(predicate, timeout=10.0, poll=0.01) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return False


def _polar_events(q) -> list[dict]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "polar":
            out.append(ev.data)
    return out


async def _stop(task) -> None:
    """Freeze the run where it is.

    Necessary rather than tidy: ``asyncio.sleep`` of less than the platform clock
    resolution returns on the next loop pass whenever the loop has other work
    ready, so with the cadence shortened for tests the adjusting loop reaches its
    240-update safety cap in a few milliseconds. A test that reads the session
    state "while the run is live" is racing that cap — and the cap ALSO ends in
    ``state:"done"``. So: stop the driver, then read what it published."""
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ------------------------------------------- the plausibility gate, integrated

async def test_a_rejected_fit_reaches_the_ui_as_a_terminal_error(driver, bus_lines) -> None:
    """``_reject_implausible_fit`` raising is only half the contract: the UI
    reticle derives "a run is live" from the published state, so a refusal that
    unwound without publishing would leave the screen blinking ``running``
    forever with Start disabled.

    Numbers verbatim from the 2026-08-06 run.
    """
    engine = _StubEngine(_engine_error(1590.0, -7091.0, total_arcmin=7271.1),
                         _engine_error(1.0, 1.0))
    task = driver["launch"](engine)
    session = driver["session"]

    # Wait on the DRIVER finishing, not on a state string — "idle" is the state
    # a fresh session already has, so waiting for a terminal-looking state would
    # match before the run started and the test could never fail.
    assert await _wait(task.done), session.state
    assert session.state["state"] == "error", session.state
    assert session.state["source"] == "native"
    msg = session.state["message"].lower()
    assert "failed fit" in msg, msg
    assert "below the horizon" in msg, msg
    # It never became a reading: the reticle must not have drawn a 121-degree
    # correction vector on the way past.
    assert session.state["az_error"] == 0.0, session.state
    assert session.state["alt_error"] == 0.0, session.state
    assert session.state["total_error"] == 0.0, session.state
    # And the adjusting loop never started.
    assert engine.update_calls == 0


async def test_the_refused_numbers_are_still_in_the_log(driver, bus_lines) -> None:
    """A refusal that throws away its own inputs is what made the 2026-08-06 run
    take a second night to diagnose. The raw fit must be logged BEFORE the gate,
    and the position-angle spread that explains it must be named."""
    engine = _StubEngine(
        _engine_error(1590.0, -7091.0, total_arcmin=7271.1,
                      flags=["position_angle_spread_large"],
                      position_angle_spread_deg=179.7),
        _engine_error(1.0, 1.0))
    task = driver["launch"](engine)
    assert await _wait(task.done), driver["session"].state
    assert driver["session"].state["state"] == "error"

    solved = [m for lvl, m, _s in bus_lines if "native TPPA solved" in m]
    assert solved, [m for _l, m, _s in bus_lines]
    assert "7271.1" in solved[0], solved[0]
    spread = [m for lvl, m, _s in bus_lines
              if lvl == "warning" and "179.7" in m]
    assert spread, [m for _l, m, _s in bus_lines]
    # The three measurement points are the other half of the evidence. (The
    # refusal message quotes the same phrase back at the operator, so anchor.)
    points = [m for _l, m, _s in bus_lines if m.startswith("native TPPA point")]
    assert len(points) == 3, points
    assert "Dec +20.000" in points[0], points[0]


async def test_a_plausible_fit_is_not_rejected(driver) -> None:
    """The gate must not become a second pole guard: the operator needs the
    number most when the alignment is genuinely bad."""
    engine = _StubEngine(_engine_error(300.0, 400.0), _engine_error(300.0, 400.0))
    task = driver["launch"](engine)
    session = driver["session"]
    assert await _wait(lambda: session.state.get("phase") == "adjusting"), session.state
    await _stop(task)
    assert session.state["state"] != "error", session.state
    assert session.state["total_error"] == pytest.approx(500.0, abs=0.1)
    assert session.state["message"] in ("adjust the mount", "alignment session ended")


# ---------------------------------------------------- the "aligned" threshold

async def test_crossing_the_threshold_ends_the_session_aligned(driver) -> None:
    """Below 1.0' the run is over: a terminal ``done``, the message the UI reads
    as success, and — the part that matters at the mount — no further exposures.

    Asserted on the message and the exposure count, never on ``state == "done"``
    alone: the 240-update safety cap ALSO ends in ``done``, so a state-only
    assertion would be satisfied by the run giving up."""
    engine = _StubEngine(_engine_error(9.0, 12.0), _engine_error(0.6, 0.7))
    q = bus.subscribe()
    try:
        task = driver["launch"](engine)
        assert await _wait(task.done), driver["session"].state
        events = _polar_events(q)
    finally:
        bus.unsubscribe(q)

    session = driver["session"]
    assert session.state["state"] == "done", session.state
    assert session.state["message"] == "polar aligned", session.state
    assert session.state["progress"] == 1.0
    assert not session.running
    assert [e.get("message") for e in events].count("polar aligned") == 1, events
    # It stopped at the FIRST reading inside the threshold: one update, and four
    # exposures in total (three measuring, one adjusting) — nothing kept firing
    # the shutter after the routine said the mount was aligned.
    assert engine.update_calls == 1, engine.update_calls
    assert driver["captures"] == 4, driver["captures"]


async def test_not_crossing_it_keeps_the_session_running(driver) -> None:
    """The mirror image, and the one that would catch an inverted comparison:
    1.5' is not aligned, so the run must stay live and keep saying so."""
    engine = _StubEngine(_engine_error(9.0, 12.0), _engine_error(0.9, 1.2))
    q = bus.subscribe()
    try:
        task = driver["launch"](engine)
        assert await _wait(lambda: engine.update_calls >= 3), driver["session"].state
        await _stop(task)
        events = _polar_events(q)
    finally:
        bus.unsubscribe(q)

    live = [e for e in events if e.get("message") == "adjust the mount"]
    assert len(live) >= 3, events
    assert all(e["state"] == "running" for e in live), live
    assert all(e["progress"] == 0.85 for e in live[1:]), live
    assert not [e for e in events if e.get("message") == "polar aligned"], events


@pytest.mark.parametrize("total,done", [(0.999, True), (1.0, True), (1.001, False)])
async def test_the_threshold_boundary_is_the_documented_constant(driver, total, done) -> None:
    """``_DONE_THRESHOLD_ARCMIN`` is 1.0 and the comparison is ``<=``. Pinned on
    both sides so a change to the constant or the operator is deliberate."""
    assert nat._DONE_THRESHOLD_ARCMIN == 1.0
    err = _engine_error(total * 0.6, total * 0.8)
    assert err["total_arcmin"] == pytest.approx(total, abs=1e-9)
    engine = _StubEngine(_engine_error(9.0, 12.0), err)
    q = bus.subscribe()
    try:
        task = driver["launch"](engine)
        assert await _wait(lambda: task.done() or engine.update_calls >= 3), \
            driver["session"].state
        await _stop(task)
        events = _polar_events(q)
    finally:
        bus.unsubscribe(q)

    aligned = [e for e in events if e.get("message") == "polar aligned"]
    if done:
        assert aligned, driver["session"].state
        assert engine.update_calls == 1
    else:
        assert not aligned, aligned
        assert engine.update_calls >= 3


async def test_an_adjust_phase_that_never_updated_ends_in_error_not_done(
        driver, monkeypatch, bus_lines) -> None:
    """The panel shows ONE number and the operator turns bolts until it moves.
    If every live solve fails, the number on screen is the initial fit and has
    not tracked a single thing they did — so ending that session in ``done``
    tells them their adjustment was measured when the display was frozen the
    whole time. The last published reading is genuinely stale, which is exactly
    the state that has to be named rather than blessed.

    The mirror case is asserted too: one successful update is enough for the cap
    to be an ordinary end-of-session, because then the number did track."""
    monkeypatch.setattr(nat, "_MAX_ADJUST_UPDATES", 3)

    frozen = _StubEngine(_engine_error(9.0, 12.0), RuntimeError("no stars"))
    task = driver["launch"](frozen)
    session = driver["session"]
    assert await _wait(task.done), session.state
    assert frozen.update_calls == 3, frozen.update_calls
    assert session.state["state"] == "error", session.state
    # The reading it is stuck on is still the initial fit, unchanged.
    assert session.state["total_error"] == pytest.approx(15.0, abs=0.01)
    msg = session.state["message"]
    assert len(msg) > 40 and "again" in msg, msg
    assert [m for lvl, m, _s in bus_lines
            if lvl == "warning" and "every live update failed" in m], \
        [m for _l, m, _s in bus_lines]


async def test_one_good_update_makes_the_cap_an_ordinary_ending(
        driver, monkeypatch) -> None:
    """The other side of the line above: the cap is not a failure by itself.

    Two failures trail the successful one here, well inside
    ``_STALE_UPDATE_LIMIT``, so the reading is still current — a brief gap while
    a cloud crosses must not be dressed up as a broken session."""
    monkeypatch.setattr(nat, "_MAX_ADJUST_UPDATES", 3)
    ok = _engine_error(9.0, 12.0)
    engine = _StubEngine(ok, lambda n: ok if n == 1 else RuntimeError("no stars"))
    task = driver["launch"](engine)
    session = driver["session"]
    assert await _wait(task.done), session.state
    assert session.state["state"] == "done", session.state
    assert session.state["message"] == "alignment session ended", session.state


async def test_a_reading_that_went_stale_mid_session_is_not_blessed_as_done(
        driver, monkeypatch, bus_lines) -> None:
    """The gap between the two tests above.

    Keying the terminal state on "did an update EVER succeed" is one notch too
    weak: an update that landed in the first second, followed by every
    subsequent one failing, is indistinguishable on screen from a session that
    followed the knobs all the way to the cap. The operator turned bolts against
    a number that stopped moving minutes ago. Recency is the property, not
    existence — so this drives the SAME shape as the test above (success, then
    only failures) and differs only in how long the failure runs.
    """
    monkeypatch.setattr(nat, "_MAX_ADJUST_UPDATES", 8)
    monkeypatch.setattr(nat, "_STALE_UPDATE_LIMIT", 3)
    ok = _engine_error(9.0, 12.0)
    engine = _StubEngine(ok, lambda n: ok if n == 1 else RuntimeError("no stars"))
    task = driver["launch"](engine)
    session = driver["session"]
    assert await _wait(task.done), session.state
    # Since review 2026-08-07 [0] the stale limit ENDS the session rather than
    # letting it grind on to the cap: once the number is unmistakably stale,
    # every further exposure is spent photographing a fact already known. One
    # success plus the three failures the limit allows = 4 calls, not 8.
    assert engine.update_calls == 4, engine.update_calls

    assert session.state["state"] == "error", session.state
    msg = session.state["message"]
    assert "stale" in msg or "older" in msg, msg
    assert "3" in msg, f"the message must say HOW MANY updates failed: {msg}"
    assert [m for lvl, m, _s in bus_lines
            if lvl == "warning" and "stale" in m], [m for _l, m, _s in bus_lines]


async def test_the_stale_limit_is_the_documented_constant() -> None:
    """A silent change to 1 would turn every passing cloud into a failed
    session; a silent change to 10000 would retire the check."""
    assert nat._STALE_UPDATE_LIMIT == 30


# ---------------------------------------- what the mount must be doing already

async def test_a_mount_found_with_tracking_off_is_tracking_before_the_first_frame(
        driver) -> None:
    """Park and find_home both LEAVE tracking off, which is exactly the state a
    mount is in when someone reaches for Align — and ``tppa_update`` attributes
    every bit of field motion to the operator's knobs, so a stationary mount
    reports the sky's own 15'/minute drift as polar error that grows without
    limit. The frozen fit's quality flags cannot fire on it, because they were
    computed minutes earlier from three frames that were fine.

    So the fix has to land BEFORE the first exposure, not as a check on the
    number afterwards: by the time the first frame is taken the mount is
    tracking, or the run is measuring the earth."""
    tel = driver["hub"]._tel
    assert tel.tracking is False, "the fake mount started in the wrong state"
    engine = _StubEngine(_engine_error(9.0, 12.0), _engine_error(9.0, 12.0))
    task = driver["launch"](engine)
    assert await _wait(lambda: driver["captures"] >= 1), driver["session"].state
    assert tel.tracking is True, (
        "the first solve frame was exposed on a mount that was not tracking")
    await _stop(task)


# ------------------------------------------------------- characterisation only

async def test_a_large_live_update_is_published_as_the_engine_returned_it(
        driver) -> None:
    """CHARACTERISATION of correct behaviour. Not a defect — do not re-file it.

    The claim was: "the adjusting phase publishes any number the engine returns;
    the plausibility gate covers only the fit, so an update past
    ``MAX_PLAUSIBLE_ERROR_DEG`` becomes an instruction to turn a bolt." REFUTED
    on review. The large live readings that prompted it were the engine being
    RIGHT — a real ~20 degree rotation of the knob moves the field that far, and
    the rescale reports it correctly — so gating the live number would refuse
    correct readings and end live sessions mid-adjustment. The one residual that
    was genuinely wrong, tracking left off so the sky's drift was read as
    growing polar error, is fixed at its source by ``_ensure_tracking`` (see the
    test above), which is where it belongs: the cure is to make the mount track,
    not to hide the number that says it isn't.

    Note also what a stub engine can and cannot show. It hard-codes a reading,
    so the only thing it can ever demonstrate is what the DRIVER does with what
    it is handed — which is precisely what is pinned here, and all the original
    "defect" was evidence of. Pinned: forwarded unchanged, same phase, same
    ``running`` state, same cadence, no truncation and no silent skip.
    """
    live = _engine_error(3864.3, 1589.1, total_arcmin=4178.3)
    assert live["total_arcmin"] > MAX_PLAUSIBLE_ERROR_DEG * 60.0, live
    engine = _StubEngine(_engine_error(9.0, 12.0), live)
    q = bus.subscribe()
    try:
        task = driver["launch"](engine)
        session = driver["session"]
        assert await _wait(lambda: engine.update_calls >= 2), session.state
        await _stop(task)
        events = _polar_events(q)
    finally:
        bus.unsubscribe(q)

    # The session derives total_error itself (hypot of the two axes, rounded),
    # so match on that rather than on the engine's own total_arcmin.
    updates = [e for e in events
               if e.get("total_error", 0.0) == pytest.approx(4178.3, abs=0.05)]
    assert len(updates) >= 2, events
    for e in updates:
        assert e["az_error"] == pytest.approx(3864.3)
        assert e["alt_error"] == pytest.approx(1589.1)
        assert e["message"] == "adjust the mount", e
        assert e["state"] == "running", e
        assert e["phase"] == "adjusting" and e["progress"] == 0.85, e
    # And the session is still live on it — the reading is a measurement, not a
    # terminal condition.
    assert session.state["state"] != "error", session.state


# ------------------------------------------------------------------- defects

async def test_a_fit_already_inside_the_threshold_is_not_told_to_adjust(driver) -> None:
    """FIXED (was: ``_DONE_THRESHOLD_ARCMIN`` is only consulted in the update
    loop, so a three-point fit landing at 0.4' — better than the threshold the
    routine itself calls aligned — was published as ``adjust the mount``).

    The operator is crouched at the mount reading one number; being told to turn
    a bolt on a mount that already meets the target is the one instruction that
    can only make it worse.

    Graded as a PROPERTY, not as phrasing: no published reading may carry an
    adjust-the-mount instruction while sitting inside the routine's own aligned
    threshold, and the already-aligned fit must say so positively rather than by
    the absence of something.
    """
    engine = _StubEngine(_engine_error(0.24, 0.32), _engine_error(0.24, 0.32))
    q = bus.subscribe()
    try:
        task = driver["launch"](engine)
        assert await _wait(lambda: task.done() or engine.update_calls >= 1), \
            driver["session"].state
        await _stop(task)
        events = _polar_events(q)
    finally:
        bus.unsubscribe(q)

    inside = [e for e in events
              if 0.0 < e.get("total_error", 0.0) <= _DONE_THRESHOLD_ARCMIN]
    assert inside, events
    told_to_adjust = [e for e in inside if e.get("message") == "adjust the mount"]
    assert not told_to_adjust, (
        f"measured {told_to_adjust[0]['total_error']}' — inside the routine's own "
        f"{_DONE_THRESHOLD_ARCMIN}' aligned threshold — and told the operator to "
        "adjust the mount")
    # The FIRST reading is the fit itself, published before any update ran: it
    # is the one that used to carry the wrong instruction, so pin it directly
    # rather than only asserting the absence of a phrase.
    assert inside[0]["phase"] == "adjusting" and inside[0]["point_index"] == 2
    assert inside[0]["message"] == "polar aligned", inside[0]


# ------------------------------------------- image geometry at the source

class _GeomHub:
    """The slice of Hub ``_capture_and_solve`` touches."""

    def __init__(self, optics: dict) -> None:
        self._optics = optics
        self.previewed: list = []

    def require(self, role):
        return _Tel() if role == "telescope" else _GeomCam()

    def effective_optics(self):
        return dict(self._optics)

    async def from_mount_frame(self, tel, ra, dec):
        return ra, dec

    async def _publish_preview(self, frame):
        self.previewed.append(frame)

    def exposure_guard(self, label):
        class _G:
            async def __aenter__(_s):
                return None

            async def __aexit__(_s, *a):
                return False
        return _G()


class _GeomCam:
    name = "cam"

    async def expose(self, *a, **kw):
        # 4144 x 2822: deliberately non-square so a width/height swap shows.
        return _Frame(shape=(2822, 4144))


class _GeomSolver:
    def __init__(self, result) -> None:
        self.result = result

    async def solve(self, path, **kw):
        return self.result


@pytest.mark.parametrize("solver_scale,optics,expected", [
    (2.31, {"image_scale_arcsec_px": 0.9}, 2.31),   # the solver measured it: use it
    (0.0, {"image_scale_arcsec_px": 0.9}, 0.9),     # else the configured optics
    (0.0, {}, 1.55),                                # else the hard-coded fallback
])
async def test_the_image_scale_precedence_is_measured_then_configured_then_guessed(
        monkeypatch, solver_scale, optics, expected) -> None:
    """What the driver hands the engine as image geometry, and in what order.

    The solver's own measurement is the only one derived from the frame that was
    just taken, so it has to win; a configured scale is the operator's claim; the
    1.55 is a guess with no provenance at all. Getting this order wrong is
    invisible — every layer returns a plausible float.
    """
    monkeypatch.setattr("astrodeck.imaging.save_fits",
                        lambda *a, **kw: None)
    hub = _GeomHub(optics)
    solver = _GeomSolver(_Solve(5.0, pixel_scale_arcsec=solver_scale))
    _frame, result, geom = await nat._capture_and_solve(hub, solver)
    scale, width, height = geom
    assert scale == pytest.approx(expected), geom
    # Width and height come from the frame, not from each other: numpy shape is
    # (rows, cols) = (height, width) and the engine's dict is (width, height).
    assert (width, height) == (4144.0, 2822.0), geom
    assert result.success
    assert hub.previewed, "the solve frame never reached the preview"


async def test_a_failed_solve_stops_the_run_instead_of_returning_a_guess(
        monkeypatch) -> None:
    """The other half of the geometry contract: no solve, no numbers. A returned
    default here would feed the fit a fabricated position."""
    monkeypatch.setattr("astrodeck.imaging.save_fits", lambda *a, **kw: None)

    class _Bad:
        success = False
        message = "no stars"

    hub = _GeomHub({})
    with pytest.raises(DeviceError, match="plate solve failed"):
        await nat._capture_and_solve(hub, _GeomSolver(_Bad()))


# ------------------------------------------------ the gate's own arithmetic

def test_the_gate_reads_the_live_error_the_same_way_it_reads_the_fit() -> None:
    """``_reject_implausible_fit`` inverts ``alt_arcmin`` back into an axis
    altitude, so it is usable on ANY error dict of this shape — including the
    ones the adjusting phase produces.

    Kept after the "gate the live updates too" claim was refuted, because it
    grades the gate's own arithmetic and not where the gate is called from: the
    inversion has to use the right hemisphere branch and the right sign, and the
    last two lines are the half that matters most — a genuinely awful but
    physically possible misalignment is REPORTED, never hidden."""
    hub = _DriverHub()
    # The 90-degree-displaced live reading measured against the real engine:
    # 69.6 degrees from the pole, past any adjuster's travel.
    live = _engine_error(3864.3, 1589.1, total_arcmin=4178.3)
    with pytest.raises(DeviceError, match="failed fit"):
        _reject_implausible_fit(live, hub)
    # A live reading inside the physical bound still passes untouched — including
    # a genuinely awful 20-degree misalignment, which the gate deliberately
    # reports rather than hides.
    _reject_implausible_fit(_engine_error(30.0, 24.0), hub)
    _reject_implausible_fit(_engine_error(1223.8, 123.7, total_arcmin=1230.0), hub)
