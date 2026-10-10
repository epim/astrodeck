# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""`GET /api/cloudmap/at` answers `no_pointing` while the mount does not know
where it points (#912, the #144 / #791 class).

After a power cycle the AM5 reports its HOME position, the pole, wherever the
tube physically is, and the driver latches `position_known` False until a sync
from a solved frame or the operator's word (`POST /api/mount/trust-position`).
`CloudmapService._mount_pointing` read that home reading and turned it into an
alt/az, so the route answered a cloud-occlusion question for a direction the
tube was not looking in - and at the pole the altitude is the site latitude
(#140). The shipped panels stop asking while the position is unknown (WP-151),
but any other client, and any principal holding `view.weather`, still got a
confident answer. The route now reads the rig's own latch
(`rig_position_known`, which also carries a doubt across a replaced telescope
object) and answers the same `no_pointing` skeleton as no mount at all.

What this file holds, on the real service over the synthetic sky that
`test_cloudmap_routes` builds, with a SIMULATED mount in the hub:

  * the control: the same mount, at the same pointing, with a known position
    gets a real answer, so "no pointing" below is the flag and not a fixture
    that could never point;
  * position unknown: a 200 with basis `no_pointing`, a reason that says so and
    carries no coordinate, no probability and no pierce point, and the mount is
    NOT READ (its serial link costs nothing for an answer nobody may use);
  * Trust position / a sync clears it, and the next answer is the real one;
  * a doubt the hub carries across a REPLACED telescope object (a profile
    activate builds new device objects) still refuses, which is what reading
    `rig_position_known(hub)` and not the driver attribute buys;
  * a disconnected mount is still "no mount", and a switched-off map is still
    "switched off", ahead of the position;
  * the picked-point POST is a direction the caller chose and is unaffected.

MUTATIONS RUN, 2026-10-10, each from a byte backup restored with its md5
checked (scratchpad/w19/mut-WP-160). Output verbatim.

  M1 "the route does not pass the rig's latch" - `position_known=
  rig_position_known(hub)` in `get_cloudmap_at` replaced by `position_known=
  True`. 3 failed (test_position_unknown_is_no_pointing_and_the_mount_is_not_
  read, test_trust_position_brings_the_real_answer_back and test_a_doubt_the_
  hub_carries_to_a_replaced_telescope_still_refuses):
      AssertionError: position_known False: basis is 'crossing', not
      'no_pointing': [...]

  M2 "the service ignores the flag" - the `if not position_known:` return
  deleted from `_mount_pointing`. The same three, with the mount read once.

  M3 "the flag is read from the driver, not the rig" - `position_known=
  rig_position_known(hub)` replaced by `position_known=getattr(hub.devices.get(
  "telescope"), "position_known", True)`. 1 failed, test_a_doubt_the_hub_
  carries_to_a_replaced_telescope_still_refuses:
      AssertionError: the doubt did not survive a replaced telescope object:
      basis 'crossing'

  M4 "the position is checked before the mount is connected" - the `if not
  position_known:` block moved above the `connected` test. 1 failed,
  test_a_disconnected_mount_is_still_no_mount.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.catalog.coords import altaz, lst_hours
from astrodeck.devices.base import RIG_DOUBT_ATTR
from astrodeck.devices.sim import SimRig, SimTelescope

from test_cloudmap_routes import PICKED, _make
from test_cloudmap_service import BASE, SITE_LAT, SITE_LON

REAL_BASES = ("crossing", "mask_only")
UNKNOWN_REASON = "the mount does not know where it points"


class _CountingMount(SimTelescope):
    """A simulated mount that counts how often its position is asked for."""

    def __init__(self, rig: SimRig):
        super().__init__(rig)
        self.asked = 0

    async def get_position(self):
        self.asked += 1
        return await super().get_position()


def _mount(ha_hours: float = 0.0, dec_deg: float | None = None,
           *, known: bool = True) -> _CountingMount:
    """A connected simulated mount on the meridian about 60 degrees up, at the
    synthetic sky's instant. `known=False` latches the rig-level doubt the way
    a power-up at the home pole does."""
    rig = SimRig()
    rig.ra_hours = (lst_hours(SITE_LON, BASE) - ha_hours) % 24.0
    rig.dec_deg = SITE_LAT - 30.0 if dec_deg is None else dec_deg
    tel = _CountingMount(rig)
    asyncio.run(tel.connect())
    if not known:
        tel.mark_position_unknown("the mount reports its home position")
    return tel


def _pointing(tel: SimTelescope) -> tuple[float, float]:
    return altaz(tel.rig.ra_hours, tel.rig.dec_deg, SITE_LAT, SITE_LON, BASE)


@pytest.fixture
def clean_hub(monkeypatch):
    """The hub is a module singleton: no telescope, and no rig-level doubt left
    by a previous test (or left for the next one)."""
    monkeypatch.delitem(app_module.hub.devices, "telescope", raising=False)
    monkeypatch.setattr(app_module.hub, RIG_DOUBT_ATTR, None, raising=False)


def _install(monkeypatch, tel) -> None:
    monkeypatch.setitem(app_module.hub.devices, "telescope", tel)


def _get(client: TestClient) -> dict:
    r = client.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    return r.json()


# ======================================================= the control + the bug


def test_position_unknown_is_no_pointing_and_the_mount_is_not_read(
        tmp_path, monkeypatch, clean_hub):
    _store, app, _svc = _make(tmp_path, monkeypatch)
    known = _mount()
    alt, az = _pointing(known)
    assert 20.0 < alt < 89.0, "the fixture no longer points at the sky"
    unknown = _mount(known=False)
    with TestClient(app) as c:
        _install(monkeypatch, known)
        control = _get(c)
        assert control["basis"] in REAL_BASES, (
            "the control is not a real answer, so 'no pointing' below would "
            f"prove nothing: {control}")

        _install(monkeypatch, unknown)
        r = c.get("/api/cloudmap/at")
        body = _get(c)
    assert r.status_code == 200, r.text
    assert body["basis"] == "no_pointing", (
        f"position_known False: basis is {body['basis']!r}, not "
        f"'no_pointing': {body}")
    assert UNKNOWN_REASON in body["reason"], body["reason"]
    assert body["probability"] is None and body["pierce_lat_deg"] is None, body
    assert body["enabled"] is True and body["observed_at"] is not None, body
    assert unknown.asked == 0, (
        "the mount was read for an answer nobody may use "
        f"({unknown.asked} reads)")
    for value in (alt, az):
        for form in (repr(value), f"{value:.3f}", f"{value:.2f}"):
            assert form not in r.text, (
                "the no-pointing answer carries a rendering of a pointing")


def test_trust_position_brings_the_real_answer_back(
        tmp_path, monkeypatch, clean_hub):
    """The flag is read on every request, not cached: the operator's word that
    the tube is at home, or a verified sync, clears it, and the next poll is a
    real answer from the same mount."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    tel = _mount(known=False)
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        assert _get(c)["basis"] == "no_pointing"
        assert tel.asked == 0
        asyncio.run(tel.trust_position())
        body = _get(c)
    assert body["basis"] in REAL_BASES, body
    assert tel.asked == 1, tel.asked


def test_a_doubt_the_hub_carries_to_a_replaced_telescope_still_refuses(
        tmp_path, monkeypatch, clean_hub):
    """A profile activate (the operator's Reconnect) tears the rig down and
    builds NEW device objects. The hub's record of the doubt follows the rig,
    not the object (`rig_position_known`), so the cloud map reads it through
    that and not off the driver: a fresh telescope object answering `True` to
    `position_known` does not make the position known."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    old = _mount(known=False)
    fresh = _mount(known=True)
    with TestClient(app) as c:
        _install(monkeypatch, old)
        assert _get(c)["basis"] == "no_pointing"      # the hub records the doubt
        _install(monkeypatch, fresh)
        body = _get(c)
    assert body["basis"] == "no_pointing", (
        f"the doubt did not survive a replaced telescope object: basis "
        f"{body['basis']!r}")
    assert UNKNOWN_REASON in body["reason"], body["reason"]
    assert fresh.asked == 0, "the replaced mount was read while the rig is in doubt"


# ==================================== what the position does not outrank


def test_a_disconnected_mount_is_still_no_mount(tmp_path, monkeypatch,
                                                clean_hub):
    """A mount that is not there is reported as not there, whatever its latch
    says: the position question only exists for a connected one."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    tel = _mount(known=False)
    asyncio.run(tel.disconnect())
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        body = _get(c)
    assert body["basis"] == "no_pointing", body
    assert "no mount" in body["reason"], body["reason"]
    assert UNKNOWN_REASON not in body["reason"], body["reason"]


def test_a_switched_off_map_is_still_switched_off(tmp_path, monkeypatch,
                                                  clean_hub):
    _store, app, _svc = _make(tmp_path, monkeypatch, enabled=False,
                              populate=False)
    tel = _mount(known=False)
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        body = _get(c)
    assert body["basis"] == "no_data", body
    assert body["reason"] == "the cloud map is switched off", body["reason"]
    assert tel.asked == 0


def test_a_picked_direction_is_answered_whatever_the_mount_knows(
        tmp_path, monkeypatch, clean_hub):
    """The POST carries a direction the caller chose and never consults the
    mount, so a mount in doubt changes nothing about it."""
    _store, app, svc = _make(tmp_path, monkeypatch)
    tel = _mount(known=False)
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        r = c.post("/api/cloudmap/at", json=PICKED)
    assert r.status_code == 200, r.text
    want = svc.at_payload(alt_deg=PICKED["alt"], az_deg=PICKED["az"],
                          ahead_s=PICKED.get("ahead_s", 0), now=BASE)
    assert r.json() == want, r.json()
    assert tel.asked == 0
