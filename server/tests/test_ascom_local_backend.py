"""COM-T6: the ascom-local backend resolves roles through the EXISTING Alpaca
client pointed at the comhost loopback port (no new client), and self-registers
only on Windows. Driven against an in-process comhost with a fake COM factory."""
import sys

import pytest

import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.ascom_registry import AscomDriver


class _FakeScope:
    Connected = False
    RightAscension = 7.0
    Declination = 41.0
    Slewing = False
    AtPark = False
    CanPulseGuide = True


@pytest.fixture()
def comhost_port(monkeypatch):
    import astrodeck.comhost.handlers_telescope  # noqa: F401 (register)
    monkeypatch.setattr(server, "_make_com_device", lambda progid: ComDevice(
        progid, create=lambda pid: _FakeScope()))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "ASCOM.Simulator.Telescope", "S", 0)])
    yield srv.server_address[1]
    srv.com_host.close()
    srv.shutdown()


@pytest.mark.asyncio
async def test_session_resolves_via_alpaca_client(comhost_port):
    from astrodeck.devices.backends.ascom_local import AscomLocalSession
    from astrodeck.devices.backend import ConnSpec
    sess = AscomLocalSession(port=comhost_port)
    dev = await sess.get_device("telescope", ConnSpec(
        backend="ascom-local", dev_type="telescope", dev_num=0, role="telescope"))
    ra, dec = await dev.get_position()
    assert (ra, dec) == (7.0, 41.0)
    assert dev.backend == "alpaca"  # reuses the real Alpaca client class
    await sess.close()


def test_backend_registered_only_on_windows():
    from astrodeck.devices import backends as _b  # noqa: F401 (registration)
    from astrodeck.devices.backend import BACKENDS
    if sys.platform == "win32":
        assert "ascom-local" in BACKENDS
        assert BACKENDS["ascom-local"].label == "ASCOM (local)"
    else:
        assert "ascom-local" not in BACKENDS  # clean cross-platform degradation


def test_implicit_ascom_local_row_present_only_on_windows():
    """drivers._implicit_rows() carries the built-in ascom-local row on Windows
    (offers from the ASCOM registry), and omits it entirely off Windows — the UI
    feature-detects purely by the row's presence."""
    from astrodeck import drivers as drivers_mod
    rows = drivers_mod._implicit_rows()
    by_id = {r["id"]: r for r in rows}
    if sys.platform == "win32":
        assert "ascom-local" in by_id
        row = by_id["ascom-local"]
        assert row["type"] == "ascom-local"
        assert row["label"] == "ASCOM (local)"
        assert row["implicit"] is True
        assert row["offers"]["tasks"] == []
        assert isinstance(row["offers"]["devices"], list)
    else:
        assert "ascom-local" not in by_id  # clean cross-platform degradation
