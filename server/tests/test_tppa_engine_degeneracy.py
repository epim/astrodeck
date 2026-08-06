"""TPPA engine robustness against inputs that are not a clean three-point arc.

Everything here goes through the public PyO3 boundary —
``astrodeck_native.tppa_from_three`` / ``tppa_update`` — because that is the only
surface ``astrodeck/polar/native.py`` can see.

WHY THIS FILE EXISTS. The three-point fit is a plane through three points, so it
is EXACT by construction: there are no residuals, no chi-square, nothing inside
the engine that can notice a bad input. Its only degeneracy guard is
``AXIS_EPS = 1e-9`` on the norm of a cross product (``error_det.rs``), which is a
NUMERICAL zero, not a physical one. So the failure mode is never a crash — it is
a confident wrong number with a knob direction attached, which is exactly what
sent an operator out to turn a bolt by 121 degrees on 2026-08-06.

THE FORWARD MODEL IS INDEPENDENT OF THE ENGINE. The Rust integration tests in
``native/crates/astro-tppa/tests/forward_model.rs`` synthesise their solves with
the engine's OWN ``topocentric_to_equatorial``, so they largely prove
self-consistency. Everything below builds its solves from textbook spherical
astronomy written here in Python (``_altaz_to_radec``, ``_gmst_deg``) and never
calls back into the crate, so a sign error shared by the engine's forward and
inverse transforms cannot hide.

The model: place the mount's RA axis at topocentric ``(alt = lat + d_alt,
az = d_az)``, trace the small circle of half-angle ``cone`` that the tube sweeps
about it, and convert each point to J2000 RA/Dec at its own timestamp. The
engine's §6.2 decomposition against the true pole must then return exactly
``(d_alt, d_az)`` — see ``test_forward_model_anchor``, which is the assertion
that makes every other test in this file worth reading.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

astrodeck_native = pytest.importorskip(
    "astrodeck_native", reason="Rust wheel not installed")

from astrodeck.devices.base import DeviceError  # noqa: E402 (after importorskip)
from astrodeck.polar.native import (  # noqa: E402
    _DONE_THRESHOLD_ARCMIN,
    _refuse_if_it_did_not_arrive,
    _reject_implausible_fit,
)


# --------------------------------------------------------------------------
# Site + geometry. Invented coordinates: a northern mid-latitude site so the
# `latitude_deg > 0` hemisphere branch is exercised and the traced circle sits
# at usable altitudes.
# --------------------------------------------------------------------------
_LAT = 45.0
_LON = -122.0
_SITE = {"latitude_deg": _LAT, "longitude_deg": _LON, "elevation_m": 0.0}

#: Angular distance from the mount axis to the tube — i.e. how far from the pole
#: the operator pointed. 38 deg is the geometry ``MAX_PLAUSIBLE_ERROR_DEG`` in
#: ``polar/native.py`` was measured against, and is comfortably outside the
#: 20 deg ``MIN_POLE_DISTANCE_DEG`` refusal.
_CONE_DEG = 38.0

#: The injected axis error, degrees. 0.5 deg of altitude and -0.3 deg of azimuth
#: -> 30.0' and -18.0' at the engine boundary.
_D_ALT, _D_AZ = 0.5, -0.3
_TRUE_ALT_ARCMIN = _D_ALT * 60.0
_TRUE_AZ_ARCMIN = _D_AZ * 60.0
_TRUE_TOTAL_ARCMIN = math.hypot(_TRUE_ALT_ARCMIN, _TRUE_AZ_ARCMIN)

#: An arbitrary but fixed epoch (2025-08-05T13:20:00Z) so every run is
#: deterministic; nothing here depends on the wall clock.
_T0 = 1_754_400_000.0

#: Seconds between measurement frames. On the rig this is expose + download +
#: plate solve + a 12 deg slew, so 20 s is if anything optimistic.
_DT_S = 20.0

_ARCSEC = 1.0 / 3600.0

#: Refraction switched off (``pressure_hpa = 0``), which is what
#: ``polar/native.py::_options`` does for a sim rig, so the forward model inverts
#: exactly and a discrepancy is the fit's and not the atmosphere's.
_NO_REFRACTION = {"correct_for_refraction": True, "pressure_hpa": 0.0,
                  "temperature_c": 0.0001, "relative_humidity": 0.0,
                  "wavelength_um": 0.55}

#: What a REAL rig gets: engine defaults, standard atmosphere, refracted pole.
_REAL_RIG = {"correct_for_refraction": False}

_IMAGE_GEOM = {"arcsec_per_pixel": 1.55, "image_width_px": 4144.0,
               "image_height_px": 2822.0}


# --------------------------------------------------------------------------
# Independent forward model (textbook spherical astronomy; no engine calls).
# --------------------------------------------------------------------------

_UNIX_EPOCH_JD = 2440587.5
_SEC_PER_DAY = 86400.0


def _jd(unix_s: float) -> float:
    return unix_s / _SEC_PER_DAY + _UNIX_EPOCH_JD


def _gmst_deg(jd: float) -> float:
    """Greenwich mean sidereal time, degrees (IAU 1982 / Meeus 12.4)."""
    d = jd - 2451545.0
    t = d / 36525.0
    g = (280.46061837 + 360.98564736629 * d + 0.000387933 * t * t
         - (t ** 3) / 38710000.0)
    return g % 360.0


def _lst_deg(jd: float, lon_deg: float) -> float:
    return (_gmst_deg(jd) + lon_deg) % 360.0


def _unit(alt_deg: float, az_deg: float) -> tuple[float, float, float]:
    """Topocentric unit vector as (north, east, up). Azimuth N=0, E=90."""
    a, z = math.radians(alt_deg), math.radians(az_deg)
    return (math.cos(a) * math.cos(z), math.cos(a) * math.sin(z), math.sin(a))


def _to_altaz(v: tuple[float, float, float]) -> tuple[float, float]:
    n, e, u = v
    return (math.degrees(math.asin(max(-1.0, min(1.0, u)))),
            math.degrees(math.atan2(e, n)) % 360.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normed(a):
    n = math.sqrt(_dot(a, a))
    assert n > 0.0, "cannot normalise a zero vector"
    return (a[0] / n, a[1] / n, a[2] / n)


def _rodrigues(v, k, theta):
    """Rotate `v` about unit axis `k` by `theta` radians."""
    c, s = math.cos(theta), math.sin(theta)
    kv, kx = _dot(k, v), _cross(k, v)
    return tuple(v[i] * c + kx[i] * s + k[i] * kv * (1.0 - c) for i in range(3))


def _altaz_to_radec(alt_deg: float, az_deg: float, lat_deg: float,
                    lon_deg: float, jd: float) -> tuple[float, float]:
    """Horizontal -> equatorial. Written here rather than borrowed from the
    crate so the engine's inverse is checked against something else."""
    alt, az, phi = map(math.radians, (alt_deg, az_deg, lat_deg))
    north = math.cos(alt) * math.cos(az)
    east = math.cos(alt) * math.sin(az)
    up = math.sin(alt)
    x_e = -math.sin(phi) * north + math.cos(phi) * up   # cos(dec) cos(HA)
    y_e = -east                                         # cos(dec) sin(HA)
    z_e = math.cos(phi) * north + math.sin(phi) * up    # sin(dec)
    dec = math.degrees(math.asin(max(-1.0, min(1.0, z_e))))
    ha = math.degrees(math.atan2(y_e, x_e))
    return ((_lst_deg(jd, lon_deg) - ha) % 360.0, dec)


def _arc(*, step_deg: float, lat: float = _LAT, lon: float = _LON,
         d_alt: float = _D_ALT, d_az: float = _D_AZ,
         cone_deg: float = _CONE_DEG, t0: float = _T0, dt_s: float = _DT_S,
         perturb_alt_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
         pa_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> list[dict]:
    """Three solves on the small circle a mount with axis error (d_alt, d_az)
    sweeps, stepping `step_deg` about that axis between frames.

    `perturb_alt_deg` nudges each point OFF the circle (an out-of-plane plate
    solve error), which is the only thing a three-point fit cannot absorb.
    """
    axis = _unit(lat + d_alt, d_az % 360.0)
    perp = _cross(axis, (0.0, 0.0, 1.0))
    if math.sqrt(_dot(perp, perp)) < 1e-9:           # axis at the zenith
        perp = _cross(axis, (1.0, 0.0, 0.0))
    p0 = _rodrigues(axis, _normed(perp), math.radians(cone_deg))
    out = []
    for i in range(3):
        t = t0 + dt_s * i
        alt, az = _to_altaz(_rodrigues(p0, axis, math.radians(step_deg * i)))
        alt += perturb_alt_deg[i]
        assert alt > 5.0, f"synthetic point {i} is below the horizon ({alt:.2f})"
        ra_deg, dec = _altaz_to_radec(alt, az, lat, lon, _jd(t))
        out.append({"ra_hours": ra_deg / 15.0, "dec_deg": dec,
                    "timestamp_unix_s": t, "position_angle_deg": pa_deg[i]})
    return out


def _fit(solves: list[dict], *, site: dict | None = None,
         options: dict | None = None) -> dict:
    opts = dict(_NO_REFRACTION)
    if options:
        opts.update(options)
    return astrodeck_native.tppa_from_three(solves, site or _SITE, opts)


def _total(solves, **kw) -> float:
    return _fit(solves, **kw)["error"]["total_arcmin"]


# --------------------------------------------------------------------------
# 0. The anchor. If this fails, nothing else in the file means anything.
# --------------------------------------------------------------------------

def test_forward_model_anchor() -> None:
    """An independently-built clean 12 deg arc must invert to EXACTLY the axis
    error that built it. This is the only test here that proves the engine
    right; the rest probe what it does when the input is not that."""
    err = _fit(_arc(step_deg=12.0))["error"]
    assert err["alt_arcmin"] == pytest.approx(_TRUE_ALT_ARCMIN, abs=1e-6)
    assert err["az_arcmin"] == pytest.approx(_TRUE_AZ_ARCMIN, abs=1e-6)
    assert err["total_arcmin"] == pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-6)
    # Sign conventions reach the knob hints: axis ABOVE the pole -> lower it;
    # azimuth error negative (west of north) -> turn right/east.
    assert err["alt_direction"] == "down"
    assert err["az_direction"] == "right_east"
    assert err["flags"] == []


def test_forward_model_anchor_holds_with_real_rig_refraction() -> None:
    """The same arc through the REAL-rig option set (standard atmosphere,
    refracted pole) — so no finding below can be dismissed as a pressure-0
    artefact. The refracted pole sits ~28" above the true one at this latitude,
    which is the whole of the discrepancy."""
    err = _fit(_arc(step_deg=12.0), options=_REAL_RIG)["error"]
    assert err["alt_arcmin"] == pytest.approx(_TRUE_ALT_ARCMIN, abs=0.5)
    assert err["az_arcmin"] == pytest.approx(_TRUE_AZ_ARCMIN, abs=0.05)


# --------------------------------------------------------------------------
# 1. The mount did not move.
# --------------------------------------------------------------------------

def test_three_identical_solves_are_refused() -> None:
    """The documented degeneracy: literally the same solve three times."""
    s = _arc(step_deg=12.0)[0]
    with pytest.raises(ValueError, match="mount did not move"):
        _fit([dict(s), dict(s), dict(s)])


def test_out_and_back_arc_is_refused_when_the_clock_also_stands_still() -> None:
    """Point 3 lands back on point 1 (a slew that reversed). The two chords are
    antiparallel, so the cross product vanishes and the guard fires — but only
    because the timestamps are identical too. See the next test."""
    a = _arc(step_deg=12.0)
    back = dict(a[0])
    with pytest.raises(ValueError, match="mount did not move"):
        _fit([dict(a[0]), dict(a[1]), back])


def test_mount_did_not_move_guard_is_defeated_by_elapsed_time_alone() -> None:
    """CHARACTERISATION of the guard's actual reach, and the reason the "did it
    arrive?" test has to live in the driver (see the next test).

    ``determine_plane_vector`` tests the refracted TOPOCENTRIC vectors, not the
    mount. Sidereal rotation moves those vectors even when the mount is bolted
    still, so whether three IDENTICAL sky positions are refused depends only on
    how long the exposures took: 5 s apart -> refused, 20 s apart -> accepted
    and answered with a confident number.
    """
    frozen = _arc(step_deg=12.0)[0]

    def run(dt_s: float):
        return [dict(frozen, timestamp_unix_s=_T0 + dt_s * i) for i in range(3)]

    with pytest.raises(ValueError, match="mount did not move"):
        _fit(run(5.0))
    # Same three sky positions, only the clock differs -> the engine answers.
    accepted = _fit(run(20.0))["error"]
    assert math.isfinite(accepted["total_arcmin"])
    # And the number it answers with is the WORST possible one.
    assert accepted["total_arcmin"] < 0.001, accepted


def test_identical_sky_positions_are_caught_by_the_driver_not_the_engine() -> None:
    """The mount that accepted a rotation and never made it — and WHICH LAYER
    owns catching it.

    Was: "three plate solves at the same sky position are answered perfectly
    aligned", filed as an engine defect that ``tppa_from_three`` should refuse.
    REFUTED at the engine level: three byte-identical plate solves are not a
    physical observation. Real solves of the same field differ in the last
    digits, so a refusal keyed on identical inputs would fire for nothing that
    happens and miss everything that does — and the engine cannot tell "the
    mount did not move" from "the operator pointed at the same place" anyway,
    because nothing in its input says a rotation was COMMANDED.

    The practical failure is real, though, and this is the layer that can see
    it: the driver knows what it asked for. So the assertion moved rather than
    disappeared — ``_refuse_if_it_did_not_arrive`` compares consecutive solved
    right ascensions against the commanded step. (This is the one place in this
    file that reaches past the PyO3 boundary; the point being made is precisely
    about which side of it the check belongs on.)
    """
    frozen = _arc(step_deg=12.0)[0]
    solves = [dict(frozen, timestamp_unix_s=_T0 + _DT_S * i) for i in range(3)]
    # Precondition: the three solves really are the same sky position.
    assert len({(s["ra_hours"], s["dec_deg"]) for s in solves}) == 1

    # The engine answers, and the answer is the worst possible one.
    assert _fit(solves)["error"]["total_arcmin"] < 0.001

    # ``_arc`` rotates the mount +12 deg about its own RA axis, which walks the
    # pointing WEST — RA down, hour angle up — so the equivalent commanded step
    # is negative. (Sign matters to the guard: it is signed on purpose.)
    step_hours = -12.0 / 15.0

    # The driver refuses, naming the point that did not arrive.
    with pytest.raises(DeviceError) as caught:
        _refuse_if_it_did_not_arrive(solves[0],
                                     SimpleNamespace(ra_hours=solves[1]["ra_hours"]),
                                     step_hours, 1)
    assert "point 2" in str(caught.value), caught.value

    # ...and it is not a blanket refusal: a mount that DID make the step passes.
    arrived = _arc(step_deg=12.0)
    _refuse_if_it_did_not_arrive(arrived[0],
                                 SimpleNamespace(ra_hours=arrived[1]["ra_hours"]),
                                 step_hours, 1)

    # ...while the same arc against the OPPOSITE commanded step is refused: a
    # mount that moved the right amount the wrong way is not a measurement.
    with pytest.raises(DeviceError):
        _refuse_if_it_did_not_arrive(arrived[0],
                                     SimpleNamespace(ra_hours=arrived[1]["ra_hours"]),
                                     -step_hours, 1)


def test_a_reversed_slew_reports_a_confident_wrong_error() -> None:
    """CHARACTERISATION. The out-and-back case again, with the realistic 40 s
    gap the refused version above lacked: the mount ended where it started, and
    the engine reports 96 arcminutes of altitude error rather than refusing."""
    a = _arc(step_deg=12.0)
    solves = [dict(a[0]), dict(a[1]),
              dict(a[0], timestamp_unix_s=a[0]["timestamp_unix_s"] + 2 * _DT_S)]
    err = _fit(solves)["error"]
    assert err["total_arcmin"] > 90.0, err
    # Nothing in the payload says the geometry was the problem.
    assert err["flags"] == [], err


# --------------------------------------------------------------------------
# 2. How far must the mount ACTUALLY move? (AXIS_EPS vs physics.)
# --------------------------------------------------------------------------

#: Steps to sweep, degrees. 12 is what ``_RA_STEP_HOURS`` commands.
_STEPS = (12.0, 5.0, 2.0, 1.0, 0.5, 0.1)


def test_exact_inputs_are_recovered_at_every_step_the_engine_accepts() -> None:
    """With NOISELESS solves the fit is exact all the way down to a tenth of a
    degree — the plane through three points does not care how close they are
    until floating point does. So the engine's refusal threshold is a statement
    about arithmetic, not about geometry."""
    assert _STEPS, "step sweep is empty"
    for step in _STEPS:
        err = _fit(_arc(step_deg=step))["error"]
        assert err["total_arcmin"] == pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-4), \
            f"exact arc at step {step} deg -> {err}"


def test_the_engine_only_refuses_below_about_a_twentieth_of_a_degree() -> None:
    """Locate ``AXIS_EPS``'s physical meaning by bisection: the boundary between
    "mount did not move" and an answer sits near 0.079 deg (285 arcsec) of
    motion, with EXACT inputs. Pin it so a change to AXIS_EPS is visible."""
    lo, hi = 0.001, 0.2

    def accepted(step: float) -> bool:
        try:
            _fit(_arc(step_deg=step))
            return True
        except ValueError:
            return False

    assert not accepted(lo), "expected the engine to refuse a 0.001 deg step"
    assert accepted(hi), "expected the engine to accept a 0.2 deg step"
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if accepted(mid):
            hi = mid
        else:
            lo = mid
    assert 0.05 < lo < 0.12, f"refusal boundary moved to {lo:.4f} deg"


def test_one_arcsec_of_solve_error_is_amplified_as_the_step_shrinks() -> None:
    """THE conditioning measurement, and the answer to "how far must the mount
    move before the fit stops being garbage".

    One arcsecond of out-of-plane error at the MIDDLE point — well inside any
    plate solver's scatter — is amplified by the near-parallel chords. Measured
    against the independent forward model (true error 34.99'):

        step 12 deg ->    35.4'   (+1%)      usable
        step  5 deg ->    38.5'   (+10%)
        step  2 deg ->    59.7'   (+71%)     already unusable
        step  1 deg ->   136.1'   (x3.9)
        step  0.5 deg ->  405.1'  (x11.6)
        step  0.1 deg -> 1956.3'  (x55.9)    32.6 degrees of "polar error"

    The engine refuses NONE of these (it refuses only below ~0.08 deg), so the
    honest reading is: the fit stops being meaningful somewhere between 5 and 2
    degrees of motion, roughly two orders of magnitude above what the code
    declines to answer.
    """
    assert _STEPS, "step sweep is empty"
    perturb = (0.0, 1.0 * _ARCSEC, 0.0)
    totals: dict[float, float] = {}
    for step in _STEPS:
        totals[step] = _total(_arc(step_deg=step, perturb_alt_deg=perturb))

    assert len(totals) == len(_STEPS)
    # A 12 deg step absorbs an arcsecond; a 1 deg step multiplies it.
    assert totals[12.0] == pytest.approx(35.4, abs=0.5)
    assert totals[5.0] > _TRUE_TOTAL_ARCMIN * 1.05
    assert totals[2.0] > _TRUE_TOTAL_ARCMIN * 1.5
    assert totals[1.0] > _TRUE_TOTAL_ARCMIN * 3.0
    assert totals[0.5] > _TRUE_TOTAL_ARCMIN * 10.0
    assert totals[0.1] > _TRUE_TOTAL_ARCMIN * 50.0
    # Monotone: every halving of the arc makes the answer worse.
    ordered = [totals[s] for s in sorted(_STEPS, reverse=True)]
    assert ordered == sorted(ordered), ordered


def test_a_partial_slew_produces_an_actionable_looking_wrong_number() -> None:
    """The field version of the test above, and the shape of the next
    meridian-class bug: the mount was commanded 12 deg and only managed 1 (a
    slew that timed out, hit a limit, or was still settling when the shutter
    opened).

    The engine reports 2.3 degrees of polar error. It clears every gate the
    stack has — ``MAX_PLAUSIBLE_ERROR_DEG`` (30 deg), the fitted-axis-above-the-
    horizon test, and the position-angle spread flag — so
    ``polar/native.py::_reject_implausible_fit`` publishes it verbatim with an
    "adjust the mount" instruction, on a rig that is 35' out.
    """
    err = _fit(_arc(step_deg=1.0,
                    perturb_alt_deg=(0.0, 1.0 * _ARCSEC, 0.0)))["error"]
    total_deg = err["total_arcmin"] / 60.0
    assert total_deg > 2.0, err                       # a bolt-turning number
    assert total_deg < 30.0, err                      # under MAX_PLAUSIBLE_ERROR_DEG
    # The fitted axis is still above the horizon, so that test passes too.
    assert _LAT + err["alt_arcmin"] / 60.0 > 0.0, err
    # And the only signal is a severity flag about the MAGNITUDE, which says
    # nothing about the geometry that produced it.
    assert err["flags"] == ["initial_error_large"], err
    assert "position_angle_spread_large" not in err["flags"]


# --------------------------------------------------------------------------
# 3. Sky-coordinate edges: RA wrap, the poles, great circles.
# --------------------------------------------------------------------------

def test_an_arc_crossing_ra_zero_matches_an_equivalent_arc_that_does_not() -> None:
    """RA wrap 23h -> 0h. The same physical measurement, taken at two epochs
    chosen so one arc straddles the RA origin and the other does not, must give
    the same axis error."""
    wrapping = None
    for k in range(240):
        cand = _arc(step_deg=12.0, t0=_T0 + k * 600.0)
        ras = [s["ra_hours"] for s in cand]
        if max(ras) - min(ras) > 12.0:      # only possible across the wrap
            wrapping = cand
            break
    assert wrapping is not None, "no epoch in the scan produced a wrapping arc"
    assert min(s["ra_hours"] for s in wrapping) < 1.0
    assert max(s["ra_hours"] for s in wrapping) > 23.0

    err = _fit(wrapping)["error"]
    assert err["alt_arcmin"] == pytest.approx(_TRUE_ALT_ARCMIN, abs=1e-4)
    assert err["az_arcmin"] == pytest.approx(_TRUE_AZ_ARCMIN, abs=1e-4)


def test_a_parked_mount_at_the_pole_is_refused() -> None:
    """Dec exactly +/-90 for all three frames: RA is meaningless there, so every
    frame is the same point on the sky and the guard fires. This is the state a
    park leaves the mount in, which is why ``MIN_POLE_DISTANCE_DEG`` exists
    upstream of the engine."""
    for dec in (90.0, -90.0):
        solves = [{"ra_hours": h, "dec_deg": dec,
                   "timestamp_unix_s": _T0 + _DT_S * i,
                   "position_angle_deg": 0.0}
                  for i, h in enumerate((1.0, 1.8, 2.6))]
        with pytest.raises(ValueError, match="mount did not move"):
            _fit(solves)


def test_declinations_past_the_pole_are_silently_aliased() -> None:
    """CHARACTERISATION. A declination beyond +/-90 cannot come from a real sky
    and means the caller is broken, but the engine takes it: dec 95 aliases to
    dec 85 on the far side and the fit lands on the celestial pole, so the
    answer is 'perfectly aligned' rather than a refusal."""
    for dec in (95.0, 100.0, -95.0):
        solves = [{"ra_hours": h, "dec_deg": dec,
                   "timestamp_unix_s": _T0 + _DT_S * i,
                   "position_angle_deg": 0.0}
                  for i, h in enumerate((1.0, 1.8, 2.6))]
        err = _fit(solves)["error"]
        assert err["total_arcmin"] == pytest.approx(0.0, abs=1e-6), (dec, err)


def test_a_great_circle_arc_is_the_best_conditioned_case_not_a_degenerate_one()\
        -> None:
    """"Three points collinear on a great circle" is what a mount pointed 90 deg
    from its own RA axis traces — the celestial equator for a well-aligned
    mount. The chords are as long as they can be, so this is the best geometry
    the fit ever sees, and it must recover the injected error exactly."""
    axis = _unit(_LAT + _D_ALT, _D_AZ % 360.0)
    p0 = _rodrigues(axis, _normed(_cross(axis, (0.0, 0.0, 1.0))),
                    math.radians(90.0))
    solves = []
    for i in range(3):
        t = _T0 + _DT_S * i
        alt, az = _to_altaz(_rodrigues(p0, axis, math.radians(12.0 * i)))
        assert alt > 5.0, f"great-circle point {i} below the horizon ({alt:.2f})"
        ra_deg, dec = _altaz_to_radec(alt, az, _LAT, _LON, _jd(t))
        solves.append({"ra_hours": ra_deg / 15.0, "dec_deg": dec,
                       "timestamp_unix_s": t, "position_angle_deg": 0.0})
    assert len(solves) == 3
    err = _fit(solves)["error"]
    assert err["alt_arcmin"] == pytest.approx(_TRUE_ALT_ARCMIN, abs=1e-6)
    assert err["az_arcmin"] == pytest.approx(_TRUE_AZ_ARCMIN, abs=1e-6)


# --------------------------------------------------------------------------
# 4. Site latitude branches.
# --------------------------------------------------------------------------

def test_latitude_exactly_zero_takes_the_southern_branch_and_still_works() -> None:
    """``northern = latitude_deg > 0.0``, so a site on the equator takes the
    SOUTHERN branch: the axis is forced to point south and the error is
    decomposed as ``pole - axis_alt`` / ``axis_az + 180``. Both sign flips have
    to cancel, or an equatorial site gets its knob directions reversed."""
    for lat in (0.0, 1e-12, -1e-12):
        site = {"latitude_deg": lat, "longitude_deg": _LON, "elevation_m": 0.0}
        err = _fit(_arc(step_deg=12.0, lat=lat, d_alt=0.2, d_az=-0.1,
                        cone_deg=30.0), site=site)["error"]
        assert err["alt_arcmin"] == pytest.approx(12.0, abs=1e-4), (lat, err)
        assert err["az_arcmin"] == pytest.approx(-6.0, abs=1e-4), (lat, err)


def test_azimuth_error_is_a_knob_angle_not_an_angular_distance() -> None:
    """The reported azimuth error is the angle to turn the AZIMUTH BOLT, which
    moves the axis by ``az_err * cos(alt)``. At 45 deg that overstates the true
    angular miss by ~1.4x; at 75 deg by ~3.9x, and ``total_arcmin`` is the
    hypotenuse of two knob angles rather than a distance from the pole.

    Worth pinning because ``MAX_PLAUSIBLE_ERROR_DEG`` in ``polar/native.py``
    gates on ``total_arcmin`` as if it were a distance from the pole.
    """
    for lat in (45.0, 60.0, 75.0):
        site = {"latitude_deg": lat, "longitude_deg": _LON, "elevation_m": 0.0}
        err = _fit(_arc(step_deg=12.0, lat=lat, d_alt=0.0, d_az=-1.0,
                        cone_deg=30.0), site=site)["error"]
        assert err["az_arcmin"] == pytest.approx(-60.0, abs=1e-3), (lat, err)
        # True angular offset of the axis from the pole, computed independently.
        pole = _unit(lat, 0.0)
        axis = _unit(lat, -1.0 % 360.0)
        true_arcmin = math.degrees(math.acos(_dot(pole, axis))) * 60.0
        assert true_arcmin < 60.0 * math.cos(math.radians(lat)) * 1.01
        assert abs(err["az_arcmin"]) > true_arcmin * 1.3, (lat, true_arcmin, err)


# --------------------------------------------------------------------------
# 5. Position angle — the pier-flip detector.
# --------------------------------------------------------------------------

def test_position_angle_never_moves_the_fitted_axis() -> None:
    """PA is advisory in ``tppa_from_three``: it feeds the spread flag and
    nothing else. Pin it, so a future change that starts using PA in the fit
    cannot land unnoticed."""
    base = _total(_arc(step_deg=12.0, pa_deg=(0.0, 0.0, 0.0)))
    for pas in ((30.0, 30.0, 30.0), (0.0, 180.0, 0.0), (-10.0, 350.0, 190.0)):
        assert _total(_arc(step_deg=12.0, pa_deg=pas)) == pytest.approx(base, abs=1e-9)


def test_a_pier_flip_raises_the_spread_flag() -> None:
    """The detector that was missing on 2026-08-06: a 180 deg jump in camera
    angle between frames means the three frames were not a pure RA rotation."""
    err = _fit(_arc(step_deg=12.0, pa_deg=(0.0, 180.0, 0.0)))["error"]
    assert err["position_angle_spread_deg"] == pytest.approx(180.0, abs=1e-9)
    assert "position_angle_spread_large" in err["flags"], err

    # A clean run must NOT raise it, or the flag means nothing.
    clean = _fit(_arc(step_deg=12.0, pa_deg=(30.0, 30.2, 29.8)))["error"]
    assert clean["position_angle_spread_deg"] == pytest.approx(0.4, abs=1e-9)
    assert "position_angle_spread_large" not in clean["flags"], clean


def test_the_spread_is_wrap_aware_across_zero() -> None:
    """350 deg and 10 deg are 20 deg apart, not 340."""
    err = _fit(_arc(step_deg=12.0, pa_deg=(350.0, 10.0, 355.0)))["error"]
    assert err["position_angle_spread_deg"] == pytest.approx(20.0, abs=1e-9)
    assert "position_angle_spread_large" in err["flags"]


def test_the_flip_detector_fires_for_every_convention_a_solver_reports_in() -> None:
    """CHARACTERISATION + the property that matters, replacing a claim that
    ``pa_gap_deg`` "returns a negative gap for position angles outside [0,360)".

    The arithmetic is true: the gap is ``d.min(360 - d)`` on the RAW difference,
    so a PAIR spanning more than a full turn (e.g. -175 and +350, 525 apart)
    makes the second term negative and the reported gap wrong. REFUTED as a
    defect because reaching it needs the two angles to be in DIFFERENT
    conventions within one run: -175 is a signed-convention reading and +350 is
    an unsigned one. A solver's convention is fixed — ASTAP returns FITS
    ``CROTA2``, the sim solver returns ``% 360.0`` — so the three frames of a run
    are always inside one 360-wide window, where the formula is exact.

    So the test now grades the thing the flag exists for: a 180 degree pier flip
    is detected whatever window the solver reports in, and a clean run is not
    flagged in any of them. The mixed-convention case is pinned last, as a
    characterisation, so that a future solver that starts mixing conventions
    trips this test rather than silently disarming the detector.
    """
    # 1. A pier flip is caught in the signed (-180, 180] convention, the
    #    unsigned [0, 360) one, and any other single 360-wide window.
    for base in (-170.0, -90.0, 0.0, 12.0, 175.0, 300.0, 359.9):
        flipped = (base, base + 180.0, base)
        err = _fit(_arc(step_deg=12.0, pa_deg=flipped))["error"]
        assert err["position_angle_spread_deg"] == pytest.approx(180.0, abs=1e-9), \
            (flipped, err)
        assert "position_angle_spread_large" in err["flags"], (flipped, err)

        # ...and the same window, un-flipped, is clean.
        steady = (base, base + 0.4, base - 0.3)
        clean = _fit(_arc(step_deg=12.0, pa_deg=steady))["error"]
        assert clean["position_angle_spread_deg"] == pytest.approx(0.7, abs=1e-9), \
            (steady, clean)
        assert "position_angle_spread_large" not in clean["flags"], (steady, clean)

    # 2. CHARACTERISATION of the unreachable case: the same three physical
    #    orientations, one set written in mixed conventions, do NOT agree.
    assert [(p % 360.0) for p in (-175.0, 350.0, -170.0)] == [185.0, 350.0, 190.0]
    mixed = _fit(_arc(step_deg=12.0, pa_deg=(-175.0, 350.0, -170.0)))["error"]
    single = _fit(_arc(step_deg=12.0, pa_deg=(185.0, 350.0, 190.0)))["error"]
    assert single["position_angle_spread_deg"] == pytest.approx(165.0, abs=1e-9)
    assert "position_angle_spread_large" in single["flags"]
    assert mixed["position_angle_spread_deg"] < single["position_angle_spread_deg"]
    assert "position_angle_spread_large" not in mixed["flags"], mixed


def test_a_missing_position_angle_defaults_to_zero_and_the_pa_deg_alias_works()\
        -> None:
    """``result.rotation_deg or 0.0`` upstream means the key can be absent; the
    binding also accepts ``pa_deg``. Neither may change the fit."""
    base = _arc(step_deg=12.0)
    stripped = [{k: v for k, v in s.items() if k != "position_angle_deg"}
                for s in base]
    err = _fit(stripped)["error"]
    assert err["position_angle_spread_deg"] == 0.0
    assert err["total_arcmin"] == pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-6)

    aliased = [dict({k: v for k, v in s.items() if k != "position_angle_deg"},
                    pa_deg=40.0 + 3.0 * i) for i, s in enumerate(base)]
    assert _fit(aliased)["error"]["position_angle_spread_deg"] == \
        pytest.approx(6.0, abs=1e-9)


# --------------------------------------------------------------------------
# 6. Timestamps.
# --------------------------------------------------------------------------

def test_out_of_order_timestamps_are_accepted_without_comment() -> None:
    """CHARACTERISATION. Reversing the frame order changes the sky the engine
    thinks it is looking at, and it answers anyway. Small here (the arc is only
    40 s long) but nothing flags it."""
    base = _arc(step_deg=12.0)
    reversed_clock = [dict(s, timestamp_unix_s=_T0 + _DT_S * (2 - i))
                      for i, s in enumerate(base)]
    err = _fit(reversed_clock)["error"]
    assert math.isfinite(err["total_arcmin"])
    assert err["flags"] == []
    assert err["total_arcmin"] != pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-6)


def test_a_wrong_clock_silently_rescales_the_azimuth_correction() -> None:
    """CHARACTERISATION. The transform is absolute in time, so a wall clock that
    is wrong by hours (an NTP-less rig that booted to the epoch, a timezone
    slip) rotates every solve about the pole before the fit sees it.

    Two hours of clock error takes the azimuth knob correction from -18' to
    -37' — the operator turns the bolt twice as far as they should — with no
    flag and no way to tell from the payload.
    """
    base = _arc(step_deg=12.0)
    shifted = [dict(s, timestamp_unix_s=s["timestamp_unix_s"] + 7200.0)
               for s in base]
    err = _fit(shifted)["error"]
    assert err["az_arcmin"] < _TRUE_AZ_ARCMIN * 1.9, err   # more negative
    assert err["flags"] == [], err
    # A one-second slip, by contrast, is harmless — so this is about magnitude.
    one_sec = _fit([dict(s, timestamp_unix_s=s["timestamp_unix_s"] + 1.0)
                    for s in base])["error"]
    assert one_sec["az_arcmin"] == pytest.approx(_TRUE_AZ_ARCMIN, abs=0.01)


def test_epoch_zero_timestamps_still_produce_a_confident_answer() -> None:
    """CHARACTERISATION. Timestamp 0 (a camera that never set ``frame.timestamp``)
    is 1970, so the sky is rotated by decades of precession-free sidereal drift.
    The engine returns a plausible-looking 36' with the AZIMUTH SIGN REVERSED —
    the operator turns the azimuth bolt the wrong way."""
    base = _arc(step_deg=12.0)
    zeroed = [dict(s, timestamp_unix_s=float(_DT_S * i))
              for i, s in enumerate(base)]
    err = _fit(zeroed)["error"]
    assert math.isfinite(err["total_arcmin"])
    assert err["az_arcmin"] > 0.0, err          # true value is -18'
    assert err["az_direction"] == "left_west"   # true direction is right_east
    assert err["flags"] == [], err


# --------------------------------------------------------------------------
# 7. Non-finite inputs.
# --------------------------------------------------------------------------

_NON_FINITE = (float("nan"), float("inf"), float("-inf"))
_SOLVE_FIELDS = ("ra_hours", "dec_deg", "timestamp_unix_s")


def test_a_non_finite_solve_field_propagates_as_nan_and_the_caller_refuses_it()\
        -> None:
    """CHARACTERISATION. Was: "non-finite inputs return a NaN polar error with a
    confident knob direction", filed as a defect the engine should raise on.
    REFUTED on severity: NaN never reaches an operator, because the one thing a
    NaN cannot do is pass a comparison.

    The mechanism is real. ``determine_plane_vector`` guards with
    ``n.norm() < AXIS_EPS`` and ``NaN < 1e-9`` is False, so a non-finite solve
    field sails past the engine's only degeneracy check and every arcminute in
    the payload comes back NaN — while ``alt_direction`` / ``az_direction``,
    which are chosen by sign tests that NaN also fails, stay populated. That
    pairing is what made it look actionable.

    But ``_reject_implausible_fit`` is where the number would become a bolt
    instruction, and its two tests are ``axis_alt > 0.0`` and
    ``total_deg <= MAX_PLAUSIBLE_ERROR_DEG``. Both are False for NaN, so it
    RAISES, and ``run_native`` turns that into a terminal error. Pinned here
    against the real function, because it is an accident of comparison order
    that a rewrite could undo: swap either test to a negated form and NaN starts
    passing.

    (Reaching any of this needs a plate solver that reports SUCCESS with a
    non-finite field centre; no shipped solver does.)
    """
    assert _SOLVE_FIELDS and _NON_FINITE, "parametrisation is empty"
    checked = 0
    for field in _SOLVE_FIELDS:
        for bad in _NON_FINITE:
            solves = _arc(step_deg=12.0)
            solves[1][field] = bad
            err = _fit(solves)["error"]
            checked += 1
            # Every magnitude is NaN...
            assert math.isnan(err["total_arcmin"]), (field, bad, err)
            assert math.isnan(err["alt_arcmin"]) and math.isnan(err["az_arcmin"])
            # ...while the knob hints still read as confident text.
            assert err["alt_direction"] in ("up", "down"), (field, bad, err)
            assert err["az_direction"], (field, bad, err)
            # ...and the caller refuses it rather than publishing it.
            with pytest.raises(DeviceError):
                _reject_implausible_fit(err, SimpleNamespace(site={"latitude": _LAT}))
    assert checked == len(_SOLVE_FIELDS) * len(_NON_FINITE), checked

    # Control: the same gate PASSES a real fit, so the refusals above are not a
    # function that raises for everything.
    _reject_implausible_fit(_fit(_arc(step_deg=12.0))["error"],
                            SimpleNamespace(site={"latitude": _LAT}))

    # A non-finite POSITION ANGLE is different in kind: it never reaches the
    # axis fit at all, so the answer stays exact and only the spread is lost.
    for bad in _NON_FINITE:
        solves = _arc(step_deg=12.0)
        solves[1]["position_angle_deg"] = bad
        err = _fit(solves)["error"]
        assert err["total_arcmin"] == pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-6)
        assert err["position_angle_spread_deg"] == 0.0, (bad, err)


def test_non_finite_site_coordinates_reach_the_same_place() -> None:
    """CHARACTERISATION of the same hole reached through the site dict rather
    than the solves. Recorded separately because the site is operator-entered
    configuration, so it is the field most likely to be blank/garbage — and
    because the three site keys do NOT behave alike."""
    seen = []
    for key in ("latitude_deg", "longitude_deg"):
        for bad in (float("nan"), float("inf")):
            site = dict(_SITE)
            site[key] = bad
            try:
                err = _fit(_arc(step_deg=12.0), site=site)["error"]
            except ValueError:
                continue
            seen.append((key, bad, err["total_arcmin"]))
    # Today every one of these comes back NaN rather than raising.
    assert len(seen) == 4, seen
    assert all(math.isnan(t) for _, _, t in seen), seen

    # Elevation, by contrast, is accepted and ignored (the engine takes its
    # pressure from `pressure_hpa`, never from the site) — so a garbage
    # elevation cannot poison a fit.
    for bad in _NON_FINITE:
        err = _fit(_arc(step_deg=12.0), site=dict(_SITE, elevation_m=bad))["error"]
        assert err["total_arcmin"] == pytest.approx(_TRUE_TOTAL_ARCMIN, abs=1e-6)


# --------------------------------------------------------------------------
# 8. The continuous-update phase.
# --------------------------------------------------------------------------

def _model_and_error() -> tuple[dict, dict]:
    out = _fit(_arc(step_deg=12.0), options=_IMAGE_GEOM)
    return out["model"], out["error"]


def test_update_with_the_reference_solve_reproduces_the_initial_error() -> None:
    """Anchor for the update path: solve 3 IS the reference frame, so re-feeding
    it must return the frozen error unchanged."""
    model, err0 = _model_and_error()
    err = astrodeck_native.tppa_update(model, _arc(step_deg=12.0)[2])
    assert err["alt_arcmin"] == pytest.approx(err0["alt_arcmin"], abs=1e-6)
    assert err["az_arcmin"] == pytest.approx(err0["az_arcmin"], abs=1e-6)


def test_update_amplifies_pointing_drift_into_enormous_polar_errors() -> None:
    """CHARACTERISATION, and the reason the adjusting phase needs the same gate
    the initial fit has.

    ``tppa_update`` re-scales the frozen error by the ratio of two pixel-space
    leg lengths and puts no bound on how far the new solve may sit from the
    reference frame. A field centre that has drifted in RA — sidereal tracking
    that is not actually running drifts 0.25 deg per minute while the operator
    turns bolts — is read as alignment error:

        drifted 0.1 deg ->    38'
        drifted 1 deg   ->   235'   (x6.7)
        drifted 5 deg   ->  1200'   (20 degrees)
        drifted 20 deg  ->  4873'   (81 degrees)

    ``polar/native.py`` applies ``_reject_implausible_fit`` to the INITIAL fit
    only (line 225); the adjusting loop publishes ``tppa_update``'s output
    straight to the UI with "adjust the mount".
    """
    model, _ = _model_and_error()
    ref = _arc(step_deg=12.0)[2]
    drifts = (0.1, 1.0, 5.0, 20.0)
    assert drifts, "drift sweep is empty"

    totals = []
    for off_deg in drifts:
        solve = dict(ref, ra_hours=(ref["ra_hours"] + off_deg / 15.0) % 24.0)
        totals.append(astrodeck_native.tppa_update(model, solve)["total_arcmin"])

    assert totals == sorted(totals), totals          # monotone in the drift
    assert totals[1] > _TRUE_TOTAL_ARCMIN * 5.0, totals
    assert totals[2] / 60.0 > 15.0, totals           # >15 deg of "polar error"
    assert totals[3] / 60.0 > 60.0, totals           # >60 deg
    # And none of it raised, so the adjusting loop's try/except never fires.


def test_update_propagates_a_non_finite_input_as_nan_and_never_reports_done()\
        -> None:
    """CHARACTERISATION, and the OTHER half of the refuted NaN claim.

    The adjusting loop has no plausibility gate: it catches exceptions and skips
    the update, but a NaN is not an exception, so ``_publish_error`` would ship
    ``az_error: NaN`` to the UI. That is a display bug, not the "confident wrong
    instruction" the claim described — and the one comparison that MATTERS still
    behaves: ``nan <= _DONE_THRESHOLD_ARCMIN`` is False, so a NaN update can
    never publish ``state:"done", message:"polar aligned"``. The session stays
    running and keeps measuring.

    Note the asymmetry with the initial fit, which is why this is a separate
    test: ``tppa_from_three`` IGNORES a non-finite position angle, while
    ``tppa_update`` propagates it — the update's re-scale runs in pixel space
    and the position angle is part of that transform.
    """
    model, _ = _model_and_error()
    ref = _arc(step_deg=12.0)[2]
    assert _SOLVE_FIELDS and _NON_FINITE, "parametrisation is empty"

    checked = 0
    for field in _SOLVE_FIELDS + ("position_angle_deg",):
        for bad in _NON_FINITE:
            err = astrodeck_native.tppa_update(model, dict(ref, **{field: bad}))
            checked += 1
            assert math.isnan(err["total_arcmin"]), (field, bad, err)
            assert not (err["total_arcmin"] <= _DONE_THRESHOLD_ARCMIN), (
                f"a NaN update compared as aligned for {field}={bad}: {err}")
    assert checked == (len(_SOLVE_FIELDS) + 1) * len(_NON_FINITE), checked

    # Control: a real update is finite and does reach the comparison meaningfully.
    good = astrodeck_native.tppa_update(model, ref)
    assert math.isfinite(good["total_arcmin"])


def test_update_without_image_geometry_refuses_clearly() -> None:
    """The one degeneracy the update path DOES refuse, kept so the contrast with
    the tests above is on the record."""
    out = _fit(_arc(step_deg=12.0))              # no arcsec_per_pixel
    assert out["model"]["arcsec_per_pixel"] == 0.0
    with pytest.raises(ValueError, match="image geometry"):
        astrodeck_native.tppa_update(out["model"], _arc(step_deg=12.0)[2])
