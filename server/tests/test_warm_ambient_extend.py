"""An ASSUMED warm-ramp ambient must not cut the TEC below the real one.

Measured on the rig 2026-08-06: the fallback ambient is 20 °C and the air was
32 °C. The ramp climbed to 20, concluded it had arrived, switched the TEC off —
and handed the sensor the remaining 12 °C at the free-running rate. That plunge
is exactly what the ramp exists to prevent, performed by the ramp, because it
trusted a constant over the evidence in front of it.

The evidence was already being collected: the loop ends the ramp when the sensor
STOPS following the setpoint, which is what reaching real ambient looks like. So
an assumed ambient the sensor is still tracking is simply wrong, and may be
extended until the lead check fires.
"""
from __future__ import annotations

from astrodeck import cooling


def test_an_assumed_ambient_the_sensor_still_tracks_is_raised():
    # Setpoint has arrived at the assumed 20 and the sensor is right behind it.
    higher = cooling.warm_extend_ambient_c(20.0, "assumed", 19.5, 20.0)
    assert higher is not None and higher > 20.0


def test_a_configured_ambient_is_never_walked_past():
    """Someone stating a fact about their observatory outranks our inference."""
    assert cooling.warm_extend_ambient_c(20.0, "configured", 19.5, 20.0) is None


def test_a_measured_ambient_is_never_walked_past():
    assert cooling.warm_extend_ambient_c(20.0, "measured", 19.5, 20.0) is None


def test_a_sensor_that_has_stopped_following_ends_it_instead():
    """The other case, and the one the loop's lead check owns: the TEC is no
    longer what sets the temperature, so we ARE at ambient."""
    lagging = 20.0 - cooling.WARM_MAX_LEAD_C - 1.0
    assert cooling.warm_extend_ambient_c(20.0, "assumed", lagging, 20.0) is None


def test_nothing_happens_before_the_setpoint_gets_there():
    assert cooling.warm_extend_ambient_c(20.0, "assumed", 9.5, 10.0) is None


def test_a_sensor_with_no_reading_cannot_justify_extending():
    assert cooling.warm_extend_ambient_c(20.0, "assumed", None, 20.0) is None


def test_the_climb_is_bounded():
    """A driver whose reported temperature follows the setpoint forever must not
    walk the TEC up without end."""
    ceiling = cooling.WARM_AMBIENT_CEILING_C
    assert cooling.warm_extend_ambient_c(ceiling, "assumed", ceiling, ceiling) is None
    just_under = ceiling - 0.5
    got = cooling.warm_extend_ambient_c(just_under, "assumed", just_under, just_under)
    assert got is not None and got <= ceiling


def test_it_reaches_a_real_32_degree_night_in_bounded_steps():
    """The rig's actual case, walked end to end."""
    ambient, steps = cooling.WARM_FALLBACK_AMBIENT_C, 0
    while steps < 100:
        # The sensor tracks the setpoint until the real air temperature.
        sensor = min(ambient, 32.0)
        higher = cooling.warm_extend_ambient_c(ambient, "assumed", sensor, ambient)
        if higher is None:
            break
        ambient, steps = higher, steps + 1
    assert ambient >= 32.0, f"stopped at {ambient} °C, below the real 32 °C air"
    assert steps < 100, "the extension never terminated"
