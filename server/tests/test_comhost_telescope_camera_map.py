"""COM-T3: the telescope+camera DEVICE_API handlers translate each Alpaca
method to the right ASCOM COM member. Portable (fake COM object; asserts the
member names and argument marshaling), no comtypes/hardware.

Two layers of proof:
  * the two verbatim smoke tests from the plan (a representative get/put pair
    per device), and
  * exhaustive per-method parametrized cases that pin EVERY Alpaca method the
    existing AlpacaTelescope/AlpacaCamera client calls to its COM member +
    type coercion + argument marshaling.

The handler tables are populated by importing the two handler modules (their
module-level register() runs). The autouse fixture re-runs register() so the
canonical handlers are guaranteed present regardless of any other test that
mutated the shared DEVICE_API global (COM-T2 contract-fixture leak note) — it
only ever restores the production handlers, so it cannot itself leak state.
"""
import numpy as np
import pytest

import astrodeck.comhost.handlers_camera as handlers_camera  # register side effect
import astrodeck.comhost.handlers_telescope as handlers_telescope
from astrodeck.comhost.server import DEVICE_API


@pytest.fixture(autouse=True)
def _canonical_handlers():
    # Guarantee the canonical (production) handlers are registered before each
    # test, undoing any DEVICE_API mutation a sibling test may have left behind.
    handlers_telescope.register()
    handlers_camera.register()


# --------------------------------------------------------------------------
# verbatim plan smoke tests
# --------------------------------------------------------------------------
class _FakeScope:
    def __init__(self):
        self.Connected = False
        self.Tracking = False
        self.RightAscension = 5.0
        self.Declination = 10.0
        self.Slewing = False
        self.AtPark = False
        self.CanPulseGuide = True
        self.slews = []
        self.pulses = []

    def SlewToCoordinatesAsync(self, ra, dec):
        self.slews.append((ra, dec))

    def PulseGuide(self, direction, duration):
        self.pulses.append((direction, duration))

    def MoveAxis(self, axis, rate):
        self.moved = (axis, rate)


def test_telescope_get_and_put_mapping():
    tel = DEVICE_API["telescope"]
    obj = _FakeScope()
    assert tel["get"]["rightascension"](obj, {}) == 5.0
    assert tel["get"]["canpulseguide"](obj, {}) is True
    # slew marshals RightAscension/Declination floats to SlewToCoordinatesAsync
    tel["put"]["slewtocoordinatesasync"](
        obj, {"RightAscension": ["1.5"], "Declination": ["-2.0"]})
    assert obj.slews == [(1.5, -2.0)]
    # tracking put sets the property
    tel["put"]["tracking"](obj, {"Tracking": ["true"]})
    assert obj.Tracking is True
    # pulseguide marshals ints
    tel["put"]["pulseguide"](obj, {"Direction": ["2"], "Duration": ["500"]})
    assert obj.pulses == [(2, 500)]


class _FakeCam:
    def __init__(self):
        self.Connected = False
        self.CameraXSize = 640
        self.CameraYSize = 480
        self.PixelSizeX = 3.8
        self.GainMax = 300
        self.CanSetCCDTemperature = True
        self.SensorType = 0
        self.MaxADU = 65535
        self.ImageReady = True
        self.Gain = 0
        self.NumX = 0
        # ASCOM ImageArray is [x][y]; 2x3 here
        self.ImageArray = ((1, 2, 3), (4, 5, 6))

    def StartExposure(self, dur, light):
        self.started = (dur, light)


def test_camera_mapping_and_imagearray_shape():
    cam = DEVICE_API["camera"]
    obj = _FakeCam()
    assert cam["get"]["cameraxsize"](obj, {}) == 640
    assert cam["get"]["imageready"](obj, {}) is True
    cam["put"]["gain"](obj, {"Gain": ["120"]})
    assert obj.Gain == 120
    cam["put"]["startexposure"](obj, {"Duration": ["1.5"], "Light": ["true"]})
    assert obj.started == (1.5, True)
    # imagearray returns the [x][y] nested lists the client reshapes+transposes
    arr = cam["get"]["imagearray"](obj, {})
    assert np.array(arr).tolist() == [[1, 2, 3], [4, 5, 6]]


# --------------------------------------------------------------------------
# exhaustive per-method mapping (one case per Alpaca method the client calls)
# --------------------------------------------------------------------------
class _RichScope:
    """A fake with every ASCOM Telescope member the handlers touch, recording
    method calls so put-marshaling is observable."""

    def __init__(self):
        self.Connected = False
        self.RightAscension = 5.0
        self.Declination = 10.0
        self.Slewing = 0            # int -> handler must bool() it
        self.Tracking = 0
        self.AtPark = 1
        self.CanPulseGuide = True
        self.SideOfPier = 1
        self.GuideRateRightAscension = 0.5
        self.GuideRateDeclination = 0.25
        self.calls = []

    def SlewToCoordinatesAsync(self, ra, dec):
        self.calls.append(("SlewToCoordinatesAsync", ra, dec))

    def AbortSlew(self):
        self.calls.append(("AbortSlew",))

    def SyncToCoordinates(self, ra, dec):
        self.calls.append(("SyncToCoordinates", ra, dec))

    def Park(self):
        self.calls.append(("Park",))

    def Unpark(self):
        self.calls.append(("Unpark",))

    def MoveAxis(self, axis, rate):
        self.calls.append(("MoveAxis", axis, rate))

    def PulseGuide(self, direction, duration):
        self.calls.append(("PulseGuide", direction, duration))

    def DestinationSideOfPier(self, ra, dec):
        self.calls.append(("DestinationSideOfPier", ra, dec))
        return 0


# (method, attr, set_value, expected_value, expected_type)
_TEL_GETS = [
    ("rightascension", "RightAscension", 5.0, 5.0, float),
    ("declination", "Declination", 10.0, 10.0, float),
    ("slewing", "Slewing", 0, False, bool),
    ("tracking", "Tracking", 1, True, bool),
    ("atpark", "AtPark", 1, True, bool),
    ("canpulseguide", "CanPulseGuide", True, True, bool),
    ("sideofpier", "SideOfPier", 1, 1, int),
    ("guideraterightascension", "GuideRateRightAscension", 0.5, 0.5, float),
    ("guideratedeclination", "GuideRateDeclination", 0.25, 0.25, float),
]


@pytest.mark.parametrize("method,attr,val,expected,typ", _TEL_GETS)
def test_telescope_get_each_method(method, attr, val, expected, typ):
    obj = _RichScope()
    setattr(obj, attr, val)
    result = DEVICE_API["telescope"]["get"][method](obj, {})
    assert result == expected
    assert type(result) is typ


def test_telescope_get_destinationsideofpier_marshals_and_returns_int():
    obj = _RichScope()
    result = DEVICE_API["telescope"]["get"]["destinationsideofpier"](
        obj, {"RightAscension": ["3.5"], "Declination": ["44.0"]})
    assert result == 0
    assert type(result) is int
    assert obj.calls == [("DestinationSideOfPier", 3.5, 44.0)]


def test_telescope_put_slewtocoordinatesasync():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["slewtocoordinatesasync"](
        obj, {"RightAscension": ["1.5"], "Declination": ["-2.0"]})
    assert obj.calls == [("SlewToCoordinatesAsync", 1.5, -2.0)]


def test_telescope_put_synctocoordinates():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["synctocoordinates"](
        obj, {"RightAscension": ["6.0"], "Declination": ["12.5"]})
    assert obj.calls == [("SyncToCoordinates", 6.0, 12.5)]


def test_telescope_put_abortslew():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["abortslew"](obj, {})
    assert obj.calls == [("AbortSlew",)]


def test_telescope_put_park_unpark():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["park"](obj, {})
    DEVICE_API["telescope"]["put"]["unpark"](obj, {})
    assert obj.calls == [("Park",), ("Unpark",)]


@pytest.mark.parametrize("value,expected", [
    (["true"], True), (["false"], False), (["1"], True), (["0"], False),
])
def test_telescope_put_tracking_sets_bool(value, expected):
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["tracking"](obj, {"Tracking": value})
    assert obj.Tracking is expected


def test_telescope_put_moveaxis_marshals_int_axis_float_rate():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["moveaxis"](
        obj, {"Axis": ["1"], "Rate": ["0.25"]})
    assert obj.calls == [("MoveAxis", 1, 0.25)]
    (_, axis, rate) = obj.calls[0]
    assert type(axis) is int and type(rate) is float


def test_telescope_put_pulseguide_marshals_ints():
    obj = _RichScope()
    DEVICE_API["telescope"]["put"]["pulseguide"](
        obj, {"Direction": ["2"], "Duration": ["500"]})
    assert obj.calls == [("PulseGuide", 2, 500)]
    (_, direction, duration) = obj.calls[0]
    assert type(direction) is int and type(duration) is int


class _RichCam:
    """A fake with every ASCOM Camera member the handlers touch."""

    def __init__(self):
        self.Connected = False
        self.CameraXSize = 640
        self.CameraYSize = 480
        self.PixelSizeX = 3.8
        self.GainMax = 300
        self.CanSetCCDTemperature = True
        self.SensorType = 2
        self.MaxADU = 65535
        self.CoolerPower = 42.5
        self.CoolerOn = True
        self.CCDTemperature = -10.0
        self.SetCCDTemperature = -5.0
        self.ImageReady = True
        self.Gain = 120
        self.Offset = 30
        self.BinX = 1
        self.BinY = 1
        self.NumX = 0
        self.NumY = 0
        self.ImageArray = ((1, 2, 3), (4, 5, 6))
        self.calls = []

    def StartExposure(self, dur, light):
        self.calls.append(("StartExposure", dur, light))

    def AbortExposure(self):
        self.calls.append(("AbortExposure",))

    def StopExposure(self):
        self.calls.append(("StopExposure",))


_CAM_GETS = [
    ("cameraxsize", "CameraXSize", 640, 640, int),
    ("cameraysize", "CameraYSize", 480, 480, int),
    ("pixelsizex", "PixelSizeX", 3.8, 3.8, float),
    ("gainmax", "GainMax", 300, 300, int),
    ("cansetccdtemperature", "CanSetCCDTemperature", True, True, bool),
    ("sensortype", "SensorType", 2, 2, int),
    ("maxadu", "MaxADU", 65535, 65535, int),
    ("coolerpower", "CoolerPower", 42.5, 42.5, float),
    ("cooleron", "CoolerOn", True, True, bool),
    ("ccdtemperature", "CCDTemperature", -10.0, -10.0, float),
    ("setccdtemperature", "SetCCDTemperature", -5.0, -5.0, float),
    ("imageready", "ImageReady", True, True, bool),
    ("gain", "Gain", 120, 120, int),
    ("offset", "Offset", 30, 30, int),
]


@pytest.mark.parametrize("method,attr,val,expected,typ", _CAM_GETS)
def test_camera_get_each_method(method, attr, val, expected, typ):
    obj = _RichCam()
    setattr(obj, attr, val)
    result = DEVICE_API["camera"]["get"][method](obj, {})
    assert result == expected
    assert type(result) is typ


@pytest.mark.parametrize("method,attr,param,value,expected", [
    ("gain", "Gain", "Gain", ["120"], 120),
    ("offset", "Offset", "Offset", ["30"], 30),
    ("binx", "BinX", "BinX", ["2"], 2),
    ("biny", "BinY", "BinY", ["2"], 2),
    ("numx", "NumX", "NumX", ["640"], 640),
    ("numy", "NumY", "NumY", ["480"], 480),
])
def test_camera_put_int_property(method, attr, param, value, expected):
    obj = _RichCam()
    DEVICE_API["camera"]["put"][method](obj, {param: value})
    assert getattr(obj, attr) == expected
    assert type(getattr(obj, attr)) is int


def test_camera_put_setccdtemperature_sets_float():
    obj = _RichCam()
    DEVICE_API["camera"]["put"]["setccdtemperature"](
        obj, {"SetCCDTemperature": ["-12.5"]})
    assert obj.SetCCDTemperature == -12.5
    assert type(obj.SetCCDTemperature) is float


@pytest.mark.parametrize("value,expected", [
    (["true"], True), (["false"], False), (["1"], True), (["0"], False),
])
def test_camera_put_cooleron_sets_bool(value, expected):
    obj = _RichCam()
    DEVICE_API["camera"]["put"]["cooleron"](obj, {"CoolerOn": value})
    assert obj.CoolerOn is expected


def test_camera_put_startexposure_marshals_float_and_bool():
    obj = _RichCam()
    DEVICE_API["camera"]["put"]["startexposure"](
        obj, {"Duration": ["1.5"], "Light": ["true"]})
    assert obj.calls == [("StartExposure", 1.5, True)]
    (_, dur, light) = obj.calls[0]
    assert type(dur) is float and type(light) is bool


def test_camera_put_abort_and_stop_exposure():
    obj = _RichCam()
    DEVICE_API["camera"]["put"]["abortexposure"](obj, {})
    DEVICE_API["camera"]["put"]["stopexposure"](obj, {})
    assert obj.calls == [("AbortExposure",), ("StopExposure",)]


def test_camera_imagearray_returns_nested_lists_xy():
    obj = _RichCam()
    obj.ImageArray = ((10, 20, 30), (40, 50, 60))
    arr = DEVICE_API["camera"]["get"]["imagearray"](obj, {})
    # nested lists (json-serializable), preserving the [x][y] ASCOM convention
    assert arr == [[10, 20, 30], [40, 50, 60]]
    assert np.array(arr).tolist() == [[10, 20, 30], [40, 50, 60]]


def test_handlers_register_is_idempotent():
    # setdefault at dict level; re-running register() must not duplicate/break.
    handlers_telescope.register()
    handlers_camera.register()
    assert "slewtocoordinatesasync" in DEVICE_API["telescope"]["put"]
    assert "imagearray" in DEVICE_API["camera"]["get"]
    assert callable(DEVICE_API["telescope"]["get"]["rightascension"])
    assert callable(DEVICE_API["camera"]["put"]["startexposure"])
