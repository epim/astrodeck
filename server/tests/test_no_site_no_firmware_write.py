"""A default site is not written into the mount's firmware (#24).

`push_site_to_mount` sends latitude, longitude and elevation to a connected
Alpaca telescope so that the app-side and mount-side LST agree. At a default
site those are 0, 0, 0, and the consequence is worse than the server computing
for the Gulf of Guinea: the MOUNT then does it too, independently, for its own
LST, its pier-side decision and its internal horizon limit. The user sees GOTOs
refused or slews to the wrong sky with nothing wrong on this side to explain
it, and the wrong numbers outlive the session because they are persisted in the
hardware.

One of the 36 unguarded site consumers the #24 audit enumerated, and the one
with a physical consequence.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub


class _Telescope:
    """An Alpaca-shaped mount: the push path is gated on a private `_put`."""

    connected = True

    def __init__(self):
        self.puts: list[tuple[str, dict]] = []

    async def _put(self, name, **kw):
        self.puts.append((name, kw))


@pytest.fixture
def hub_and_mount(monkeypatch, tmp_path):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    tel = _Telescope()
    h.devices["telescope"] = tel
    return h, tel


def _site(h, monkeypatch, **over):
    base = {"name": "", "latitude": 0.0, "longitude": 0.0, "elevation_m": 0,
            "is_default": True, "horizon_min_deg": 20}
    base.update(over)
    monkeypatch.setattr(type(h), "site", property(lambda self: base))


async def test_an_unsaved_site_is_never_pushed(hub_and_mount, monkeypatch):
    """The defect. Before the guard this sent 0,0,0 and logged success.

    MUTATION: delete the `site_is_set` early return. Observed: three puts land,
    carrying zeros, and this fails listing them.
    """
    h, tel = hub_and_mount
    _site(h, monkeypatch)
    await h.push_site_to_mount()
    assert tel.puts == [], (
        f"0,0 was written into the mount's firmware: {tel.puts}")


async def test_a_saved_site_still_reaches_the_mount(hub_and_mount, monkeypatch):
    """The other half, and the one a too-eager guard breaks: this function
    exists so the two LSTs agree, and a rig that HAS a site must still get it.

    MUTATION: return unconditionally. Observed: nothing is pushed, the mount
    keeps whatever it had, and the two clocks disagree for the whole night.
    """
    h, tel = hub_and_mount
    _site(h, monkeypatch, latitude=40.0, longitude=-74.0, elevation_m=17,
          is_default=False, name="Test")
    await h.push_site_to_mount()
    names = [n for n, _ in tel.puts]
    assert names == ["sitelatitude", "sitelongitude", "siteelevation"], names
    assert tel.puts[0][1]["SiteLatitude"] == 40.0
    assert tel.puts[1][1]["SiteLongitude"] == -74.0


async def test_the_refusal_says_why(hub_and_mount, monkeypatch, bus_lines):
    """A silent no-op here is indistinguishable from a mount that ignored the
    write, and the next person debugging a pier-side fault needs to know which.

    MUTATION: drop the `bus.log` from the early return. Observed: nothing is
    said and this fails.
    """
    h, _tel = hub_and_mount
    _site(h, monkeypatch)
    await h.push_site_to_mount()
    said = " ".join(m for _l, m, _s in bus_lines)
    assert "no site has been saved" in said, (
        f"the refusal was silent: {bus_lines}")
