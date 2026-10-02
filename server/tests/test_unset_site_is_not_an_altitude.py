# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""An unset site must not produce a confident altitude (#121).

`_frame_altitude` returned `None` only when something raised. At a default site
nothing does: `Site.latitude` and `Site.longitude` default to 0.0 and
`is_default` to True, so `altaz` computed a perfectly good altitude for the Gulf
of Guinea and handed it back.

Both callers document the opposite, in so many words. The engine's floor gate:

    # None is "nobody can say" - an unset site or a bad coordinate - and
    # it must not read as "below the floor".

and the resume arm:

    # ``None`` is "nobody can say" - an unset site, a bad coordinate - and
    # it must not read as "below the floor". ... refusing on an unreadable
    # altitude would strand every rig whose site is not configured.

Two comments asserting a tri-state the function did not implement, which is
this project's "a claim nothing keeps" shape - and here the protection had been
consciously designed and then not built.

These drive `_frame_altitude` directly, because it is the seam both callers
share and the one place the tri-state either exists or does not.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.engine import _frame_altitude


class _Target:
    """Only what the function reads. A real target carries a plan behind it and
    none of that reaches this arithmetic."""

    def __init__(self, ra_hours: float = 5.5881, dec_deg: float = -5.3911):
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg


#: What `Hub.site` builds (hub.py:1930) - always all six keys, so a missing-key
#: KeyError is NOT the shape an unset site takes.
def _site(**over) -> dict:
    base = {"name": "", "latitude": 0.0, "longitude": 0.0, "elevation_m": 0,
            "is_default": True, "horizon_min_deg": 20}
    base.update(over)
    return base


WHEN = 1_757_000_000.0      # a fixed instant; the verdict must not depend on it


def test_a_default_site_is_nobody_can_say():
    """The defect. The old code returned a number here.

    MUTATION: delete the `is_default` early return. Observed: a float comes
    back - the target's altitude at 0N 0E - and this fails naming it.
    """
    alt = _frame_altitude(_Target(), _site(), WHEN)
    assert alt is None, (
        f"an unset site produced a confident altitude of {alt} degrees; both "
        f"callers read a number as 'measured' and act on it")


def test_a_real_site_still_gets_its_altitude():
    """The other half, and the one a too-eager guard breaks: with a saved site
    this must still answer, or the floor gate and the resume arm stop working
    for everybody.

    MUTATION: return None unconditionally. Observed: this fails with None, and
    the altitude gate it feeds can never fire again.
    """
    alt = _frame_altitude(_Target(), _site(latitude=40.0, longitude=-74.0,
                                           is_default=False), WHEN)
    assert isinstance(alt, float), (
        f"a configured site got {alt!r} instead of an altitude")
    assert -90.0 <= alt <= 90.0, f"{alt} is not an altitude"


def test_the_gap_shapes_the_docstring_already_promised_still_answer_none():
    """`None` on a missing key and on a bad coordinate, unchanged. The new
    branch is an ADDITIONAL gap, not a replacement for the `except`.

    THE NaN HALF WAS ALSO NOT TRUE, and this case is how it was found. `altaz`
    does not raise on a NaN coordinate - it clamps, and a NaN ra/dec came back
    as 90.0, the zenith. For an altitude FLOOR that is the fail-open direction:
    a target with unusable coordinates reads as comfortably above any floor, so
    the gate that exists to set it aside never fires. Fixed in the same pass as
    the unset site, by checking the inputs are finite; checking the OUTPUT is
    not enough, because 90.0 is.

    MUTATION: `if False and all(math.isfinite(...))`. Observed: "a NaN
    coordinate stopped answering None", 90.0 again.
    """
    assert _frame_altitude(_Target(), {"is_default": False}, WHEN) is None, (
        "a site dict with no latitude key stopped answering None")
    bad = _Target(ra_hours=float("nan"), dec_deg=float("nan"))
    assert _frame_altitude(bad, _site(latitude=40.0, longitude=-74.0,
                                      is_default=False), WHEN) is None, (
        "a NaN coordinate stopped answering None")


@pytest.mark.parametrize("missing", ["is_default"])
def test_a_site_dict_without_the_flag_is_treated_as_real(missing):
    """`.get` and not `[...]`: the flag is absent from hand-built site dicts in
    several tests, and a KeyError there would turn this guard into a crash in
    code paths that are best-effort by contract. Absent reads as configured,
    which matches every one of those call sites - they build a site precisely
    because they mean a real one.
    """
    site = _site(latitude=40.0, longitude=-74.0)
    site.pop(missing)
    assert isinstance(_frame_altitude(_Target(), site, WHEN), float)
