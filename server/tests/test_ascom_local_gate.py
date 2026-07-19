"""COM-T7 ACCEPTANCE GATE (spec §5): a fresh install drives its OWN bundled COM
host end-to-end against the ASCOM SIMULATOR drivers — no ASCOM Remote, no NINA,
no hardware. The ascom-local backend opens (spawning the managed comhost),
connects Telescope+Camera+Focuser+FilterWheel through the EXISTING Alpaca
client, and slews/exposes/moves/changes-filter with a frame round-trip.
Windows + simulators only; skips cleanly on the Linux CI baseline."""
import sys

import pytest

from astrodeck.comhost import manager as mgr_mod
from astrodeck.devices import ascom_registry
from astrodeck.devices.backend import ConnSpec


def _sim(dev_type, progid):
    if sys.platform != "win32":
        return False
    return any(d.dev_type == dev_type and d.progid == progid
               for d in ascom_registry.enumerate())


_NEED = {
    "telescope": "ASCOM.Simulator.Telescope",
    "camera": "ASCOM.Simulator.Camera",
    "focuser": "ASCOM.Simulator.Focuser",
    "filterwheel": "ASCOM.Simulator.FilterWheel",
}
pytestmark = pytest.mark.skipif(
    not all(_sim(t, p) for t, p in _NEED.items()),
    reason="ASCOM Simulators (Telescope/Camera/Focuser/FilterWheel) not registered")


def _num(dev_type, progid):
    for d in ascom_registry.enumerate():
        if d.dev_type == dev_type and d.progid == progid:
            return d.dev_num
    raise AssertionError("simulator vanished")


@pytest.fixture()
def clean_manager():
    mgr_mod.reset_manager_for_tests()
    yield
    mgr_mod.get_manager().stop()
    mgr_mod.reset_manager_for_tests()


@pytest.mark.asyncio
async def test_single_install_four_roles_end_to_end(clean_manager):
    from astrodeck.devices.backends.ascom_local import AscomLocalBackend
    backend = AscomLocalBackend()
    sess = await backend.open(ConnSpec(backend="ascom-local"))  # spawns comhost
    try:
        tel = await sess.get_device("telescope", ConnSpec(
            backend="ascom-local", dev_type="telescope",
            dev_num=_num("telescope", _NEED["telescope"]), role="telescope"))
        cam = await sess.get_device("camera", ConnSpec(
            backend="ascom-local", dev_type="camera",
            dev_num=_num("camera", _NEED["camera"]), role="camera"))
        foc = await sess.get_device("focuser", ConnSpec(
            backend="ascom-local", dev_type="focuser",
            dev_num=_num("focuser", _NEED["focuser"]), role="focuser"))
        fw = await sess.get_device("filterwheel", ConnSpec(
            backend="ascom-local", dev_type="filterwheel",
            dev_num=_num("filterwheel", _NEED["filterwheel"]), role="filterwheel"))

        await tel.unpark()
        await tel.set_tracking(True)
        await tel.slew(4.0, 30.0)
        ra, dec = await tel.get_position()
        assert abs(ra - 4.0) < 0.2 and abs(dec - 30.0) < 1.0

        frame = await cam.expose(0.05, 0, 0, binning=1)
        assert frame.data.ndim == 2 and frame.data.size > 0

        start = await foc.get_position()
        await foc.move_to(min(start + 250, foc.max_position))
        assert await foc.get_position() != start

        await fw.set_position(1)
        assert await fw.get_position() == 1
    finally:
        await sess.close()
