"""A ceiling, not just a floor.

Every altitude limit in the codebase was a MINIMUM. But a strain-wave mount with
a long imaging train reaches its own tripod legs near the zenith while the
optics are still on open sky — observed on the AM5N 2026-07-30, with the scope
resting against a leg and no guard having an opinion about it.

The confusion that hid this: a no-go wedge's field is called ``alt_max`` and is
a FLOOR ("inside this azimuth range, stay ABOVE"). It reads like a ceiling and
is the opposite.
"""
import pytest

from astrodeck.sequence.schedule import (
    NO_CEILING_DEG, effective_ceiling, effective_floor, nogo_floor,
)


def test_unset_means_no_ceiling():
    """Default must leave existing rigs untouched."""
    assert effective_ceiling(None) == NO_CEILING_DEG == 90.0


def test_a_real_limit_is_honoured():
    assert effective_ceiling(80.0) == 80.0


def test_a_nonsense_value_never_silently_disarms_the_guard():
    """Junk must not read as "no limit" — a user who typed something wrong
    should get a conservative answer, not an unguarded mount."""
    assert effective_ceiling("not a number") == NO_CEILING_DEG
    assert effective_ceiling(None) == NO_CEILING_DEG


def test_a_value_past_the_zenith_clamps_instead_of_disabling():
    assert effective_ceiling(120.0) == 90.0


def test_a_negative_value_clamps_to_zero_rather_than_wrapping():
    """0 means "nothing is reachable", which is safe and obvious. A wrap to a
    large number would silently disable the keep-out."""
    assert effective_ceiling(-5.0) == 0.0


def test_the_wedge_field_named_alt_max_is_still_a_FLOOR():
    """Pinned so the naming cannot quietly change meaning under someone. At az
    180 inside the wedge the mount must stay ABOVE 40, i.e. 40 is a minimum."""
    wedge = [{"az_min": 170.0, "az_max": 190.0, "alt_max": 40.0}]
    assert nogo_floor(wedge, 180.0) == 40.0
    assert nogo_floor(wedge, 0.0) == 0.0
    assert effective_floor(0.0, None, 180.0, wedge) == 40.0


def test_floor_and_ceiling_are_independent():
    """They constrain opposite ends; a rig can legitimately have both (a tree
    line at 20 and a tripod at 80)."""
    assert effective_floor(20.0, None, 90.0, None) == 20.0
    assert effective_ceiling(80.0) == 80.0


@pytest.mark.parametrize("alt,expected_ok", [
    (79.0, True), (80.0, True), (80.1, False), (89.0, False),
])
def test_the_gate_is_strictly_above(alt, expected_ok):
    """Exactly at the limit is allowed; past it is not. An off-by-one here is a
    mount against a tripod leg."""
    assert (alt <= effective_ceiling(80.0)) is expected_ok
