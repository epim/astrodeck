"""Native polar alignment refuses at the 0,0 default, before any slew (#24).

Polar alignment is the worst place in this tree for the default site, because
its whole output is an instruction to a human standing at the mount - turn the
azimuth knob this way, this far - and nothing in that instruction tells the
operator it was computed for the Gulf of Guinea. Everything else on this path
reads the coordinates AFTER the first twenty-four degree rotation, so refusing
late would still cost the rotation and the frames.

The single gate is `_site_dict`, called by `_drive` before the first slew.
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.polar import native


class _Hub:
    def __init__(self, site):
        self.site = site
        self.required: list[str] = []

    def require(self, role):
        self.required.append(role)
        raise AssertionError(
            f"_drive asked for the {role} before it had refused for want of a "
            "site, so a rig with no location still takes the camera and slews")


DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "elevation_m": 0.0,
                "is_default": True}
REAL_SITE = {"latitude": 40.0, "longitude": -74.0, "elevation_m": 10.0}


def test_the_default_site_refuses_with_something_the_operator_can_act_on():
    """The defect, at the gate.

    MUTATION: delete the `if not site_is_set(hub.site)` block from
    `_site_dict`. Observed: no refusal, and the returned dict carries
    latitude_deg 0.0.
    """
    with pytest.raises(DeviceError) as e:
        native._site_dict(_Hub(DEFAULT_SITE))
    message = str(e.value)
    assert "site" in message.lower(), message
    assert "settings" in message.lower(), (
        f"the refusal does not say what to do about it: {message!r}")


def test_a_real_site_still_computes():
    """The guard against the fix. A configured rig must be unaffected, and the
    elevation default must survive.

    MUTATION: `raise DeviceError` unconditionally in `_site_dict`. Observed:
    this fails, so the case above cannot be satisfied by refusing everything.
    """
    got = native._site_dict(_Hub(REAL_SITE))
    assert got == {"latitude_deg": 40.0, "longitude_deg": -74.0,
                   "elevation_m": 10.0}
    no_elev = native._site_dict(_Hub({"latitude": 40.0, "longitude": -74.0}))
    assert no_elev["elevation_m"] == 0.0


def test_a_half_saved_site_is_not_a_site():
    """`is_default` cleared but no coordinates yet. This used to pass the gate
    and then raise KeyError from the dict read - a refusal, but one that told
    the operator nothing. The earlier version of this case recorded that as a
    known limitation and named the fix: route `_site_dict` through
    `site_lat_lon`, which answers None for exactly this shape. That is now how
    every polar site read works (`_site_coords`), so the half-saved site gets
    the same sentence as the unsaved one.

    MUTATION: in `_site_coords`, read `hub.site["latitude"]` and
    `hub.site["longitude"]` raw instead of `site_lat_lon`. Observed: KeyError,
    and this fails on the missing DeviceError.
    """
    with pytest.raises(DeviceError, match="no observing site is set"):
        native._site_dict(_Hub({"is_default": False}))


async def test_the_refusal_happens_before_the_camera_is_taken():
    """`_drive` reads the site at the top for this reason: TPPA is a
    camera-owning mount-motion path, and refusing after `hub.require` would
    abandon the tube part-way through a rotation with the session dead.

    The hub here raises if anything asks it for a device, so the only way this
    passes is by refusing first.

    MUTATION: move the `site = _site_dict(hub)` line below the two
    `hub.require` calls in `_drive`. Observed: this fails with "_drive asked
    for the telescope before it had refused for want of a site".
    """
    hub = _Hub(DEFAULT_SITE)
    with pytest.raises(DeviceError):
        await native._drive(object(), hub)
    assert hub.required == [], (
        f"devices were taken before the refusal: {hub.required}")


def _helpers():
    from types import SimpleNamespace
    solved = SimpleNamespace(ra_hours=3.0, dec_deg=80.0)
    err = {"alt_arcmin": 10.0, "total_arcmin": 12.0, "az_arcmin": 6.0}
    return {
        "_refuse_low_arc": lambda hub: native._refuse_low_arc(hub, solved, 1.6),
        "_reject_implausible_fit":
            lambda hub: native._reject_implausible_fit(err, hub),
        "_ra_step_hours": lambda hub: native._ra_step_hours(hub, 3.0),
    }


@pytest.mark.parametrize("name", sorted(_helpers()))
def test_each_helper_refuses_on_its_own(name):
    """Below `_drive`, not only at it. These were exempt from #24's gate as
    "reachable only through `_drive`" - true, and a claim nothing checked. A
    caller that goes around `_drive` now meets the same refusal.

    MUTATION: in `_site_coords`, `latlon = site_lat_lon(hub.site) or (0.0,
    0.0)`. Observed: all three fail, each having computed for 0,0.
    """
    with pytest.raises(DeviceError, match="no observing site is set"):
        _helpers()[name](_Hub(DEFAULT_SITE))


@pytest.mark.parametrize("name", sorted(_helpers()))
def test_each_helper_still_computes_at_a_saved_site(name):
    """The control: a real site gets past `_site_coords`. Whatever a helper
    then decides about the arc or the fit is not graded here - only that no
    refusal FOR WANT OF A SITE came out of it."""
    try:
        _helpers()[name](_Hub(REAL_SITE))
    except DeviceError as e:
        assert "no observing site" not in str(e), e
