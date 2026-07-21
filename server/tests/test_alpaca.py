"""AlpacaTelescope tracking-rate mapping against a recording fake connection
(multi-rate mount tracking, 2026-07-21). Mirrors test_alpaca_rotator.py's
RecConn pattern -- no real network, no unittest.mock."""
from __future__ import annotations

import pytest

from astrodeck.devices.alpaca import AlpacaTelescope
from astrodeck.devices.base import DeviceError


class RecConn:
    """Records every Alpaca verb; scripted responses per GET method name."""

    def __init__(self, **responses):
        self.host, self.port = "10.0.0.9", 11111
        self.calls = []
        self.responses = {
            "rightascension": 5.0, "declination": 10.0, "slewing": False,
            "tracking": True, "atpark": False, "canpulseguide": False,
            "trackingrates": [0, 1, 2], "trackingrate": 0,
        }
        self.responses.update(responses)

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        return self.responses.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        return None


@pytest.fixture()
def tel():
    return AlpacaTelescope(RecConn(), 0, "Fictional GEM Mount")


async def test_connect_probes_trackingrates_and_sets_flag(tel):
    await tel.connect()
    assert tel.connected is True
    assert tel.can_set_tracking_rate is True
    assert ("get", "trackingrates", {}) in tel.conn.calls


async def test_connect_leaves_flag_false_when_only_sidereal_offered():
    tel = AlpacaTelescope(RecConn(trackingrates=[0]), 0, "Fictional Alt-Az Mount")
    await tel.connect()
    assert tel.can_set_tracking_rate is False


async def test_connect_leaves_flag_false_when_only_lunar_offered():
    """Capability requires BOTH lunar(1) AND solar(2) -- one alone is not enough."""
    tel = AlpacaTelescope(RecConn(trackingrates=[0, 1]), 0, "Partial Mount")
    await tel.connect()
    assert tel.can_set_tracking_rate is False


async def test_connect_survives_missing_trackingrates():
    async def boom(dev_type, dev_num, method, **params):
        if method == "trackingrates":
            raise DeviceError("not implemented")
        return RecConn().responses.get(method)
    conn = RecConn()
    conn.get = boom
    tel = AlpacaTelescope(conn, 0, "No-Rates Mount")
    await tel.connect()
    assert tel.can_set_tracking_rate is False


async def test_set_tracking_rate_puts_the_ascom_enum(tel):
    await tel.connect()
    await tel.set_tracking_rate("lunar")
    assert ("put", "trackingrate", {"TrackingRate": 1}) in tel.conn.calls
    await tel.set_tracking_rate("solar")
    assert ("put", "trackingrate", {"TrackingRate": 2}) in tel.conn.calls
    await tel.set_tracking_rate("sidereal")
    assert ("put", "trackingrate", {"TrackingRate": 0}) in tel.conn.calls


async def test_set_tracking_rate_rejects_unknown(tel):
    await tel.connect()
    tel.conn.calls.clear()
    with pytest.raises(DeviceError):
        await tel.set_tracking_rate("king")
    assert tel.conn.calls == []                # nothing sent to the driver


async def test_get_tracking_rate_maps_enum_to_name(tel):
    await tel.connect()
    tel.conn.responses["trackingrate"] = 2
    assert await tel.get_tracking_rate() == "solar"
    tel.conn.responses["trackingrate"] = 1
    assert await tel.get_tracking_rate() == "lunar"
    tel.conn.responses["trackingrate"] = 0
    assert await tel.get_tracking_rate() == "sidereal"
