# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The AM5 driver does not write 0,0 into the mount, or predict from it (#24).

`ZwoAm5Telescope.connect` sends `:SMGE<lat>&<lon>#` on EVERY open - on purpose,
because a mount that restarted is back at its power-up site and a bare reopen
would point against it. At the default site that line was `+00*00:00&+000*00:00`:
every connect told the mount it stood in the Gulf of Guinea, and the mount then
takes its own hour-angle decisions from that - its meridian limit, the side a
GoTo lands on - where nothing in this tree can see them.

#24's allowlist said this was "reachable only by a caller that goes around"
`hub.push_site_to_mount`, which refuses at a default site. That was wrong:
`connect` IS the caller that goes around it, and it runs on every reconnect.

What the mount already holds - set from its handset, or pushed by a session
that did have a site - is a better answer than a made-up one, so an unsited
connect now leaves it alone and says so.

`destination_pier_side` answers UNKNOWN without a site. Its self-check compares
the rule against `:Gm#` at the current position; a constant hour-angle error can
pass that check and still flip the answer for a destination across the meridian.

MUTATIONS RUN, and what each printed:

  M1, `_site_latlon` returns `(float(site.latitude), float(site.longitude))`
  unconditionally - the pre-fix body. 2 failed: the no-site `_site_latlon`
  case, and the connect case, whose FakeLink then saw `SMGE+00*00:00&+000*00:00`.

  M2, in `connect`, `lx200.smge(*(latlon or (0.0, 0.0)))` with no None branch.
  1 failed: the connect case, same wire line.

  M3, delete the `latlon is None: return PierSide.UNKNOWN` in
  `destination_pier_side`. 1 failed: the unsited prediction returned EAST.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.devices.backends.zwo_am5 as am5
from astrodeck.config import AppConfig, config_store
from astrodeck.devices.base import PierSide
from astrodeck.catalog import coords

from test_zwo_am5 import FakeLink, FIXED_UTC, _connect_script  # rootdir-relative

pytestmark = pytest.mark.asyncio


def _cfg(monkeypatch, *, sited: bool, lat=40.0, lon=-110.0):
    cfg = AppConfig()
    cfg.site.latitude = lat if sited else 0.0
    cfg.site.longitude = lon if sited else 0.0
    cfg.site.is_default = not sited
    monkeypatch.setattr(config_store, "cfg", lambda: cfg)
    return cfg


async def test_no_site_gives_no_coordinates(monkeypatch):
    _cfg(monkeypatch, sited=False)
    assert am5._site_latlon() is None


async def test_a_saved_site_gives_its_coordinates(monkeypatch):
    """The control: the accessor still answers for a real rig."""
    _cfg(monkeypatch, sited=True, lat=40.0, lon=-110.0)
    assert am5._site_latlon() == (40.0, -110.0)


async def test_an_unsited_connect_does_not_write_a_site_into_the_mount(
        monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    _cfg(monkeypatch, sited=False)
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert tel.connected is True, "an unsited rig must still be able to connect"
    written = [c for c in fl.sent if c.startswith("SMGE")]
    assert written == [], f"the mount was told a made-up site: {written}"


async def test_a_sited_connect_still_writes_it(monkeypatch):
    """The control, and the reason the re-assert exists: a mount that restarted
    is back at its power-up site."""
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    _cfg(monkeypatch, sited=True, lat=40.0,
         lon=-(100 + 30 / 60 + 30 / 3600))
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert "SMGE+40*00:00&+100*30:30" in fl.sent, fl.sent


def _am5_pointing(monkeypatch, *, sited: bool, lon=-110.0):
    tel = am5.ZwoAm5Telescope(FakeLink({}))
    now = time.time()
    # Pointing two hours east of the meridian AT THE LONGITUDE THE CONFIG
    # HOLDS - 0.0 when unsited - so the driver's self-check against `:Gm#`
    # PASSES in both cases. Without that, the unsited case could read UNKNOWN
    # because the self-check failed, and would pass with the guard deleted.
    lon = lon if sited else 0.0
    ra_now = (coords.lst_hours(lon, now) + 2.0) % 24.0

    async def _pier():
        return PierSide.WEST

    async def _pos():
        return (ra_now, 20.0)
    tel.pier_side = _pier
    tel.get_position = _pos
    _cfg(monkeypatch, sited=sited, lon=lon)
    return tel, (coords.lst_hours(lon, now) - 2.0) % 24.0


async def test_an_unsited_mount_does_not_predict_its_destination(monkeypatch):
    tel, ra_dest = _am5_pointing(monkeypatch, sited=False)
    assert await tel.destination_pier_side(ra_dest, 20.0) is PierSide.UNKNOWN


async def test_a_sited_mount_still_predicts(monkeypatch):
    """The control for the case above: same mount, same pointing, same
    destination across the meridian, with the site saved."""
    tel, ra_dest = _am5_pointing(monkeypatch, sited=True)
    assert await tel.destination_pier_side(ra_dest, 20.0) is PierSide.EAST
