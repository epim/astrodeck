"""AlpacaDome + AlpacaCoverCalibrator verb/enum mapping against a recording fake
connection (PRO-4 / PRO-5 real-hardware clients).

Mirrors ``test_alpaca_rotator.py``: a ``RecConn`` records every Alpaca verb and
returns scripted responses per GET method name, so each test asserts the exact
PUT issued and the int->state mapping of each GET, with zero network I/O.
"""
import pytest

from astrodeck.devices import alpaca
from astrodeck.devices.alpaca import (
    AlpacaCoverCalibrator,
    AlpacaDome,
    DEVICE_CLASSES,
    make_device,
)
from astrodeck.devices.base import (
    CoverState,
    DeviceError,
    DomeShutterState,
)


class RecConn:
    """Records every Alpaca verb; scripted responses per GET method name."""

    def __init__(self):
        self.host, self.port = "10.0.0.9", 11111
        self.calls = []
        self.responses = {}

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        return self.responses.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        return None


# --------------------------------------------------------------------- fixtures

@pytest.fixture()
def dome():
    return AlpacaDome(RecConn(), 0, "Roll-Off Roof")


@pytest.fixture()
def cover():
    return AlpacaCoverCalibrator(RecConn(), 0, "Flat Panel")


# --------------------------------------------------------------------- registry

def test_registered_in_device_classes():
    assert DEVICE_CLASSES["dome"] is AlpacaDome
    assert DEVICE_CLASSES["covercalibrator"] is AlpacaCoverCalibrator
    assert AlpacaDome.dev_type == "dome"
    assert AlpacaCoverCalibrator.dev_type == "covercalibrator"


def test_make_device_builds_dome_and_cover():
    d = make_device("10.0.0.9", 11111, "dome", 0, "D")
    assert isinstance(d, AlpacaDome)
    c = make_device("10.0.0.9", 11111, "covercalibrator", 0, "C")
    assert isinstance(c, AlpacaCoverCalibrator)
    # dev_type is case-folded by make_device.
    assert isinstance(make_device("h", 1, "Dome", 0, "D"), AlpacaDome)


# ------------------------------------------------------------------------- dome

@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    (0, DomeShutterState.OPEN),
    (1, DomeShutterState.CLOSED),
    (2, DomeShutterState.OPENING),
    (3, DomeShutterState.CLOSING),
    (4, DomeShutterState.ERROR),
    (99, DomeShutterState.UNKNOWN),   # out-of-range -> UNKNOWN
])
async def test_shutter_state_maps_int(dome, raw, expected):
    dome.conn.responses["shutterstatus"] = raw
    assert await dome.shutter_state() is expected


@pytest.mark.asyncio
async def test_shutter_state_degrades_to_unknown_on_error(dome):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("unreachable")
    dome.conn.get = boom
    # A state read must NEVER raise -- it degrades to UNKNOWN.
    assert await dome.shutter_state() is DomeShutterState.UNKNOWN


@pytest.mark.asyncio
async def test_shutter_state_none_is_unknown(dome):
    # non-numeric / missing value (int(None) -> TypeError) also degrades.
    dome.conn.responses["shutterstatus"] = None
    assert await dome.shutter_state() is DomeShutterState.UNKNOWN


@pytest.mark.asyncio
async def test_open_shutter_puts(dome):
    await dome.open_shutter()
    assert ("put", "openshutter", {}) in dome.conn.calls


@pytest.mark.asyncio
async def test_close_shutter_puts_and_does_not_self_park(dome):
    await dome.close_shutter()
    puts = [c[1] for c in dome.conn.calls if c[0] == "put"]
    assert "closeshutter" in puts
    # close_shutter must NOT itself park -- the close-order guard owns that.
    assert "park" not in puts


@pytest.mark.asyncio
async def test_abort_puts_abortslew(dome):
    await dome.abort()
    assert ("put", "abortslew", {}) in dome.conn.calls


@pytest.mark.asyncio
async def test_get_slaved_reads_driver(dome):
    dome.conn.responses["slaved"] = True
    assert await dome.get_slaved() is True


@pytest.mark.asyncio
async def test_get_slaved_degrades_to_false_on_error(dome):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("no slaved")
    dome.conn.get = boom
    assert await dome.get_slaved() is False


@pytest.mark.asyncio
async def test_set_slaved_gated_on_canslave(dome):
    dome.can_slave = False
    with pytest.raises(DeviceError):
        await dome.set_slaved(True)
    dome.can_slave = True
    await dome.set_slaved(True)
    assert ("put", "slaved", {"Slaved": True}) in dome.conn.calls


@pytest.mark.asyncio
async def test_connect_probes_canslave(dome):
    dome.conn.responses["canslave"] = True
    await dome.connect()
    assert dome.connected is True
    assert dome.can_slave is True
    assert ("get", "canslave", {}) in dome.conn.calls
    # connected=True was PUT by the base connect().
    assert ("put", "connected", {"Connected": True}) in dome.conn.calls


@pytest.mark.asyncio
async def test_connect_survives_missing_canslave(dome):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("no canslave")
    dome.conn.get = boom
    await dome.connect()
    assert dome.can_slave is False


def test_requires_park_before_close_stays_true(dome):
    # FAIL-SAFE: ASCOM exposes no roof-through-mount geometry, so the real client
    # must keep the collision-possible default. Never flip this to False.
    assert dome.requires_park_before_close is True


# --------------------------------------------------------------- covercalibrator

@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    (0, CoverState.NOT_PRESENT),
    (1, CoverState.CLOSED),
    (2, CoverState.MOVING),
    (3, CoverState.OPEN),
    (4, CoverState.UNKNOWN),
    (5, CoverState.ERROR),
    (99, CoverState.UNKNOWN),
])
async def test_cover_state_maps_int(cover, raw, expected):
    cover.conn.responses["coverstate"] = raw
    assert await cover.get_cover_state() is expected


@pytest.mark.asyncio
async def test_cover_state_degrades_to_unknown_on_error(cover):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("unreachable")
    cover.conn.get = boom
    assert await cover.get_cover_state() is CoverState.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    (0, "not_present"),
    (1, "off"),
    (2, "unknown"),   # NotReady
    (3, "ready"),
    (4, "unknown"),   # Unknown
    (5, "error"),
    (99, "unknown"),
])
async def test_calibrator_state_maps_int(cover, raw, expected):
    cover.conn.responses["calibratorstate"] = raw
    assert await cover.get_calibrator_state() == expected


@pytest.mark.asyncio
async def test_calibrator_state_degrades_on_error(cover):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("unreachable")
    cover.conn.get = boom
    assert await cover.get_calibrator_state() == "unknown"


@pytest.mark.asyncio
async def test_get_brightness_reads_driver(cover):
    cover.conn.responses["brightness"] = 128
    assert await cover.get_brightness() == 128


@pytest.mark.asyncio
async def test_calibrator_on_clamps_and_puts(cover):
    cover.max_brightness = 255
    await cover.calibrator_on(1000)          # over the ceiling -> clamp to 255
    await cover.calibrator_on(-5)            # below floor -> clamp to 0
    await cover.calibrator_on(100)
    puts = [c for c in cover.conn.calls if c[0] == "put"]
    assert ("put", "calibratoron", {"Brightness": 255}) in puts
    assert ("put", "calibratoron", {"Brightness": 0}) in puts
    assert ("put", "calibratoron", {"Brightness": 100}) in puts


@pytest.mark.asyncio
async def test_calibrator_off_puts(cover):
    await cover.calibrator_off()
    assert ("put", "calibratoroff", {}) in cover.conn.calls


@pytest.mark.asyncio
async def test_open_cover_puts(cover):
    await cover.open_cover()
    assert ("put", "opencover", {}) in cover.conn.calls


@pytest.mark.asyncio
async def test_close_cover_gated_on_has_cover(cover):
    cover.has_cover = False
    with pytest.raises(DeviceError):
        await cover.close_cover()
    # nothing was PUT while gated.
    assert not [c for c in cover.conn.calls if c[0] == "put"]
    cover.has_cover = True
    await cover.close_cover()
    assert ("put", "closecover", {}) in cover.conn.calls


@pytest.mark.asyncio
async def test_connect_reads_maxbrightness_and_derives_has_cover(cover):
    cover.conn.responses["maxbrightness"] = 255
    cover.conn.responses["coverstate"] = 1        # Closed -> has a cover
    await cover.connect()
    assert cover.connected is True
    assert cover.max_brightness == 255
    assert cover.has_cover is True
    assert ("get", "maxbrightness", {}) in cover.conn.calls


@pytest.mark.asyncio
async def test_connect_no_cover_when_coverstate_not_present(cover):
    cover.conn.responses["maxbrightness"] = 100
    cover.conn.responses["coverstate"] = 0        # NotPresent -> no cover
    await cover.connect()
    assert cover.has_cover is False


@pytest.mark.asyncio
async def test_connect_survives_missing_capabilities(cover):
    async def boom(dev_type, dev_num, method, **params):
        raise DeviceError("not implemented")
    cover.conn.get = boom
    await cover.connect()
    # base default (on/off-only panel) preserved; no cover assumed.
    assert cover.max_brightness == 1
    assert cover.has_cover is False
