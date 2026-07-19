"""COM-T3 gate: the EXISTING Alpaca client drives Telescope+Camera through the
running comhost against the ASCOM SIMULATOR drivers — no NINA, no ASCOM Remote,
no hardware (spec §5). Windows + simulator only; skips cleanly elsewhere."""
import sys

import pytest

import astrodeck.comhost.server as server
from astrodeck.devices import ascom_registry
from astrodeck.devices.alpaca import AlpacaConnection, AlpacaTelescope, AlpacaCamera


def _sim_present(dev_type: str, progid: str) -> bool:
    if sys.platform != "win32":
        return False
    return any(d.dev_type == dev_type and d.progid == progid
               for d in ascom_registry.enumerate())


pytestmark = pytest.mark.skipif(
    not (_sim_present("telescope", "ASCOM.Simulator.Telescope")
         and _sim_present("camera", "ASCOM.Simulator.Camera")),
    reason="ASCOM Simulator Telescope+Camera not registered (non-Windows / no sim)")


def _dev_num(dev_type: str, progid: str) -> int:
    for d in ascom_registry.enumerate():
        if d.dev_type == dev_type and d.progid == progid:
            return d.dev_num
    raise AssertionError("simulator vanished between skip-check and use")


@pytest.fixture()
def host_port():
    srv = server.serve(port=0)
    yield srv.server_address[1]
    srv.com_host.close()
    srv.shutdown()


@pytest.mark.asyncio
async def test_telescope_slew_through_comhost(host_port):
    conn = AlpacaConnection("127.0.0.1", host_port)
    tel = AlpacaTelescope(conn, _dev_num("telescope", "ASCOM.Simulator.Telescope"),
                          "Sim Scope")
    try:
        await tel.connect()
        await tel.unpark()
        await tel.set_tracking(True)
        await tel.slew(3.0, 20.0)  # returns when Slewing clears
        ra, dec = await tel.get_position()
        assert abs(ra - 3.0) < 0.2 and abs(dec - 20.0) < 1.0
    finally:
        await tel.disconnect()
        await conn.close()


@pytest.mark.asyncio
async def test_camera_expose_frame_roundtrips(host_port):
    conn = AlpacaConnection("127.0.0.1", host_port)
    cam = AlpacaCamera(conn, _dev_num("camera", "ASCOM.Simulator.Camera"), "Sim Cam")
    try:
        await cam.connect()
        frame = await cam.expose(0.05, 0, 0, binning=1)
        assert frame.data.ndim == 2
        assert frame.data.shape[0] > 0 and frame.data.shape[1] > 0
    finally:
        await cam.disconnect()
        await conn.close()
