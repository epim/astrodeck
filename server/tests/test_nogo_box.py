"""No-go wedges: the hard-edged obstruction guard.

`SafetyConfig.nogo_box` shipped in the very first automation-safety spec and was
never wired. The field persisted, no UI showed it, and no code read it — so a
user who wrote a pier guard into their config file had, in fact, no pier guard.
The spec's own text (`_enforce_mount_floor` compares against
`max(min_alt_deg, interp(horizon, az), nogo_box)`) is what the implementation
now does.

Distinct from `horizon` on purpose: horizon control points INTERPOLATE, so a
pier declared at az 180 would slope a floor across the whole southern sky. A
wedge has hard edges.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.schedule import effective_floor, nogo_floor


# -------------------------------------------------------------- nogo_floor

def test_no_wedges_imposes_no_floor():
    assert nogo_floor(None, 180.0) == 0.0
    assert nogo_floor([], 180.0) == 0.0


def test_inside_the_wedge_imposes_its_floor_and_outside_does_not():
    box = [{"az_min": 170, "az_max": 190, "alt_max": 35}]
    assert nogo_floor(box, 180.0) == 35.0
    assert nogo_floor(box, 170.0) == 35.0     # inclusive at the near edge
    assert nogo_floor(box, 190.0) == 35.0     # inclusive at the far edge
    assert nogo_floor(box, 169.9) == 0.0
    assert nogo_floor(box, 190.1) == 0.0


def test_the_edges_are_hard_not_interpolated():
    """The whole reason this is not a horizon profile: one degree outside the
    wedge the floor is gone, not 34.9."""
    box = [{"az_min": 179, "az_max": 181, "alt_max": 40}]
    assert nogo_floor(box, 180.0) == 40.0
    assert nogo_floor(box, 178.0) == 0.0
    assert nogo_floor(box, 90.0) == 0.0


def test_a_wedge_wraps_through_due_north():
    """az_min=350, az_max=10 is the 20-degree wedge through north, not the
    340-degree complement — getting this backwards would blanket the whole sky
    with a floor the user meant for one chimney."""
    box = [{"az_min": 350, "az_max": 10, "alt_max": 25}]
    assert nogo_floor(box, 0.0) == 25.0
    assert nogo_floor(box, 355.0) == 25.0
    assert nogo_floor(box, 5.0) == 25.0
    assert nogo_floor(box, 180.0) == 0.0
    assert nogo_floor(box, 11.0) == 0.0


def test_overlapping_wedges_take_the_highest_floor():
    box = [{"az_min": 0, "az_max": 90, "alt_max": 20},
           {"az_min": 45, "az_max": 135, "alt_max": 30}]
    assert nogo_floor(box, 10.0) == 20.0
    assert nogo_floor(box, 60.0) == 30.0     # both apply; the taller wins
    assert nogo_floor(box, 120.0) == 30.0


def test_a_degenerate_wedge_covers_the_whole_circle():
    """az_min == az_max is ambiguous. It is read as "everywhere", because the
    other reading silently drops a guard the user believes is armed."""
    box = [{"az_min": 90, "az_max": 90, "alt_max": 15}]
    assert nogo_floor(box, 90.0) == 15.0
    assert nogo_floor(box, 270.0) == 15.0


@pytest.mark.parametrize("bad", [
    {"az_min": 0, "az_max": 90},                 # no alt_max
    {"az_min": "north", "az_max": 90, "alt_max": 10},
    {},
    None,
])
def test_a_malformed_wedge_is_ignored_not_fatal(bad):
    """This runs inside the slew gate mid-night. A KeyError there would abort a
    run over a typo — the guard degrades to "this wedge does nothing" instead."""
    box = [bad, {"az_min": 100, "az_max": 110, "alt_max": 22}]
    assert nogo_floor(box, 105.0) == 22.0
    assert nogo_floor(box, 5.0) == 0.0


def test_az_outside_0_360_is_normalised():
    box = [{"az_min": 170, "az_max": 190, "alt_max": 35}]
    assert nogo_floor(box, 540.0) == 35.0    # 540 % 360 == 180
    assert nogo_floor(box, -180.0) == 35.0


# ----------------------------------------------------------- effective_floor

def test_effective_floor_takes_the_max_of_all_three_terms():
    horizon = [(0.0, 5.0), (180.0, 5.0)]
    box = [{"az_min": 170, "az_max": 190, "alt_max": 35}]
    # inside the wedge the wedge wins
    assert effective_floor(10.0, horizon, 180.0, box) == 35.0
    # outside it, the global floor still wins over the horizon profile
    assert effective_floor(10.0, horizon, 90.0, box) == 10.0
    # and the horizon wins where it is the tallest
    assert effective_floor(1.0, [(90.0, 22.0)], 90.0, box) == 22.0


def test_omitting_nogo_box_reproduces_the_previous_answer_exactly():
    """Every existing caller passes three arguments. The default must not move
    a single floor."""
    horizon = [(0.0, 12.0), (180.0, 4.0)]
    for az in (0.0, 45.0, 90.0, 180.0, 275.0, 359.0):
        assert effective_floor(8.0, horizon, az) == effective_floor(
            8.0, horizon, az, None)
