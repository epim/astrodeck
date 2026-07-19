"""COM-T5: covercalibrator/dome/observingconditions host-side DEVICE_API
mapping. Portable fake-COM objects — these types have no AstroDeck ABC yet, so
this proves the HOST surface only (a future flats/dome/weather wave consumes it)."""
import astrodeck.comhost.handlers_aux  # noqa: F401  (register side effect)
from astrodeck.comhost.server import DEVICE_API


class _FakeCover:
    CoverState = 3        # Open
    CalibratorState = 1   # Off
    Brightness = 0
    MaxBrightness = 255
    def OpenCover(self): self.opened = True
    def CalibratorOn(self, brightness): self.on = brightness


def test_covercalibrator_mapping():
    c = DEVICE_API["covercalibrator"]
    o = _FakeCover()
    assert c["get"]["coverstate"](o, {}) == 3
    assert c["get"]["maxbrightness"](o, {}) == 255
    c["put"]["opencover"](o, {})
    assert o.opened is True
    c["put"]["calibratoron"](o, {"Brightness": ["128"]})
    assert o.on == 128


class _FakeDome:
    AtHome = False
    Slewing = False
    Azimuth = 90.0
    ShutterStatus = 0
    Slaved = False
    def SlewToAzimuth(self, az): self.az = az
    def OpenShutter(self): self.opened = True


def test_dome_mapping():
    d = DEVICE_API["dome"]
    o = _FakeDome()
    assert d["get"]["azimuth"](o, {}) == 90.0
    d["put"]["slewtoazimuth"](o, {"Azimuth": ["180.0"]})
    assert o.az == 180.0
    d["put"]["slaved"](o, {"Slaved": ["true"]})
    assert o.Slaved is True


class _FakeOC:
    Temperature = 12.0
    Humidity = 55.0
    CloudCover = 10.0
    def Refresh(self): self.refreshed = True


def test_observingconditions_mapping():
    oc = DEVICE_API["observingconditions"]
    o = _FakeOC()
    assert oc["get"]["temperature"](o, {}) == 12.0
    assert oc["get"]["humidity"](o, {}) == 55.0
    oc["put"]["refresh"](o, {})
    assert o.refreshed is True
