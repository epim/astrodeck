"""COM-T2: the comhost serves the EXACT Alpaca envelope the existing client
parses. Drive the real AlpacaConnection against a running comhost with a FAKE
COM factory (no comtypes) — this is the portable proof of the client<->host
contract; COM-T3+ add the real device method tables."""
import pytest

import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.alpaca import AlpacaConnection
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.base import DeviceError


def _drivers():
    return [AscomDriver("Camera", "camera", "Fake.Camera", "Fake Cam", 0),
            AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)]


class _FakeScope:
    def __init__(self):
        self.Connected = False
    RightAscension = 12.34


@pytest.fixture()
def running_host(monkeypatch):
    # Every ComDevice in this host is created from a fake factory (no COM).
    monkeypatch.setattr(server, "_make_com_device", lambda progid: ComDevice(
        progid, create=lambda pid: _FakeScope()))
    # Register a minimal telescope GET handler so the contract test can read one
    # property end-to-end through the real client (COM-T3 adds the full table).
    server.DEVICE_API.setdefault("telescope", {"get": {}, "put": {}})
    server.DEVICE_API["telescope"]["get"]["rightascension"] = \
        lambda obj, p: obj.RightAscension
    srv = server.serve(port=0, drivers=_drivers())
    yield srv.server_address[1]
    srv.shutdown()

@pytest.mark.asyncio
async def test_configureddevices_lists_enumeration(running_host):
    conn = AlpacaConnection("127.0.0.1", running_host)
    try:
        r = await conn.http.get(
            f"http://127.0.0.1:{running_host}/management/v1/configureddevices")
        value = r.json()["Value"]
        types = sorted(d["DeviceType"] for d in value)
        assert types == ["Camera", "Telescope"]
        assert value[0]["DeviceNumber"] == 0
        assert value[0]["UniqueID"]  # ProgID carried
    finally:
        await conn.close()

@pytest.mark.asyncio
async def test_connect_then_get_property_roundtrips(running_host):
    conn = AlpacaConnection("127.0.0.1", running_host)
    try:
        await conn.put("telescope", 0, "connected", Connected=True)
        ra = await conn.get("telescope", 0, "rightascension")
        assert ra == 12.34
    finally:
        await conn.close()

@pytest.mark.asyncio
async def test_unknown_method_raises_deviceerror(running_host):
    conn = AlpacaConnection("127.0.0.1", running_host)
    try:
        await conn.put("telescope", 0, "connected", Connected=True)
        with pytest.raises(DeviceError):
            await conn.get("telescope", 0, "nosuchmethod")
    finally:
        await conn.close()
