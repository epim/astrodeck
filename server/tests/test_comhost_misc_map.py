"""COM-T4: focuser/filterwheel/rotator/switch/safetymonitor DEVICE_API mapping
to ASCOM COM members. Portable fake-COM objects; no comtypes/hardware.

Mirrors the COM-T3 test pattern (test_comhost_telescope_camera_map.py):
  * the verbatim smoke tests from the plan (a representative get/put pair per
    device), and
  * exhaustive per-method parametrized cases that pin EVERY Alpaca method the
    existing AlpacaFocuser/AlpacaFilterWheel/AlpacaRotator/AlpacaSwitch/
    AlpacaSafetyMonitor client calls to its COM member + type coercion +
    argument marshaling.

The handler table is populated by importing handlers_misc (its module-level
register() runs). The autouse fixture re-runs register() so the canonical
handlers are guaranteed present regardless of any other test that mutated the
shared DEVICE_API global (COM-T2 contract-fixture leak note, same rationale
as the T3 mapping file) — it only ever restores the production handlers, so
it cannot itself leak state.
"""
import pytest

import astrodeck.comhost.handlers_misc as handlers_misc  # noqa: F401 (register side effect)
from astrodeck.comhost.server import DEVICE_API


@pytest.fixture(autouse=True)
def _canonical_handlers():
    handlers_misc.register()


# --------------------------------------------------------------------------
# verbatim plan smoke tests
# --------------------------------------------------------------------------
class _FakeFoc:
    MaxStep = 50000
    StepSize = 5.0
    Position = 1234
    IsMoving = False
    Temperature = -3.5

    def Move(self, position):
        self.moved_to = position

    def Halt(self):
        self.halted = True


def test_focuser_mapping():
    f = DEVICE_API["focuser"]
    o = _FakeFoc()
    assert f["get"]["maxstep"](o, {}) == 50000
    assert f["get"]["position"](o, {}) == 1234
    f["put"]["move"](o, {"Position": ["4321"]})
    assert o.moved_to == 4321
    f["put"]["halt"](o, {})
    assert o.halted is True


class _FakeWheel:
    Names = ("L", "R", "G", "B")
    Position = 2


def test_filterwheel_mapping():
    w = DEVICE_API["filterwheel"]
    o = _FakeWheel()
    assert list(w["get"]["names"](o, {})) == ["L", "R", "G", "B"]
    assert w["get"]["position"](o, {}) == 2
    w["put"]["position"](o, {"Position": ["3"]})
    assert o.Position == 3


class _FakeSwitch:
    MaxSwitch = 2

    def GetSwitchName(self, i):
        return f"port{i}"

    def CanWrite(self, i):
        return True

    def MinSwitchValue(self, i):
        return 0.0

    def MaxSwitchValue(self, i):
        return 1.0

    def GetSwitchValue(self, i):
        return 1.0

    def SetSwitchValue(self, i, v):
        self.set = (i, v)


def test_switch_and_safety_mapping():
    s = DEVICE_API["switch"]
    o = _FakeSwitch()
    assert s["get"]["maxswitch"](o, {}) == 2
    assert s["get"]["getswitchname"](o, {"Id": ["1"]}) == "port1"
    s["put"]["setswitchvalue"](o, {"Id": ["0"], "Value": ["0.5"]})
    assert o.set == (0, 0.5)

    class _FakeSafety:
        IsSafe = True

    assert DEVICE_API["safetymonitor"]["get"]["issafe"](_FakeSafety(), {}) is True


class _FakeRot:
    CanReverse = True
    MechanicalPosition = 42.0
    IsMoving = False
    Reverse = False

    def MoveMechanical(self, position):
        self.moved = position

    def Halt(self):
        self.halted = True


def test_rotator_mapping():
    r = DEVICE_API["rotator"]
    o = _FakeRot()
    assert r["get"]["canreverse"](o, {}) is True
    assert r["get"]["mechanicalposition"](o, {}) == 42.0
    r["put"]["movemechanical"](o, {"Position": ["100.5"]})
    assert o.moved == 100.5
    r["put"]["reverse"](o, {"Reverse": ["true"]})
    assert o.Reverse is True


# --------------------------------------------------------------------------
# exhaustive per-method mapping (one case per Alpaca method the client calls)
# --------------------------------------------------------------------------
class _RichFoc:
    """A fake with every ASCOM IFocuserV3 member the handlers touch, recording
    method calls so put-marshaling is observable."""

    def __init__(self):
        self.Connected = False
        self.MaxStep = 50000
        self.StepSize = 5.0
        self.Position = 1234
        self.IsMoving = 0     # int -> handler must bool() it
        self.Temperature = -3.5
        self.calls = []

    def Move(self, position):
        self.calls.append(("Move", position))

    def Halt(self):
        self.calls.append(("Halt",))


_FOC_GETS = [
    ("maxstep", "MaxStep", 50000, 50000, int),
    ("stepsize", "StepSize", 5.0, 5.0, float),
    ("position", "Position", 1234, 1234, int),
    ("ismoving", "IsMoving", 1, True, bool),
    ("temperature", "Temperature", -3.5, -3.5, float),
]


@pytest.mark.parametrize("method,attr,val,expected,typ", _FOC_GETS)
def test_focuser_get_each_method(method, attr, val, expected, typ):
    obj = _RichFoc()
    setattr(obj, attr, val)
    result = DEVICE_API["focuser"]["get"][method](obj, {})
    assert result == expected
    assert type(result) is typ


def test_focuser_put_move_marshals_int():
    obj = _RichFoc()
    DEVICE_API["focuser"]["put"]["move"](obj, {"Position": ["777"]})
    assert obj.calls == [("Move", 777)]
    assert type(obj.calls[0][1]) is int


def test_focuser_put_halt():
    obj = _RichFoc()
    DEVICE_API["focuser"]["put"]["halt"](obj, {})
    assert obj.calls == [("Halt",)]


class _RichWheel:
    """A fake with every ASCOM IFilterWheelV2 member the handlers touch."""

    def __init__(self):
        self.Connected = False
        self.Names = ("Luminance", "Red", "Green", "Blue")
        self.Position = 2


def test_filterwheel_get_names_returns_list():
    obj = _RichWheel()
    result = DEVICE_API["filterwheel"]["get"]["names"](obj, {})
    assert result == ["Luminance", "Red", "Green", "Blue"]
    assert type(result) is list


def test_filterwheel_get_position():
    obj = _RichWheel()
    obj.Position = 3
    result = DEVICE_API["filterwheel"]["get"]["position"](obj, {})
    assert result == 3
    assert type(result) is int


def test_filterwheel_put_position_marshals_int():
    obj = _RichWheel()
    DEVICE_API["filterwheel"]["put"]["position"](obj, {"Position": ["1"]})
    assert obj.Position == 1
    assert type(obj.Position) is int


class _RichRot:
    """A fake with every ASCOM IRotatorV3 member the handlers touch."""

    def __init__(self):
        self.Connected = False
        self.CanReverse = True
        self.MechanicalPosition = 42.0
        self.IsMoving = 0     # int -> handler must bool() it
        self.Reverse = False
        self.calls = []

    def MoveMechanical(self, position):
        self.calls.append(("MoveMechanical", position))

    def Halt(self):
        self.calls.append(("Halt",))


_ROT_GETS = [
    ("canreverse", "CanReverse", True, True, bool),
    ("mechanicalposition", "MechanicalPosition", 42.0, 42.0, float),
    ("ismoving", "IsMoving", 1, True, bool),
    ("reverse", "Reverse", 1, True, bool),
]


@pytest.mark.parametrize("method,attr,val,expected,typ", _ROT_GETS)
def test_rotator_get_each_method(method, attr, val, expected, typ):
    obj = _RichRot()
    setattr(obj, attr, val)
    result = DEVICE_API["rotator"]["get"][method](obj, {})
    assert result == expected
    assert type(result) is typ


def test_rotator_put_movemechanical_marshals_float():
    obj = _RichRot()
    DEVICE_API["rotator"]["put"]["movemechanical"](obj, {"Position": ["100.5"]})
    assert obj.calls == [("MoveMechanical", 100.5)]
    assert type(obj.calls[0][1]) is float


def test_rotator_put_halt():
    obj = _RichRot()
    DEVICE_API["rotator"]["put"]["halt"](obj, {})
    assert obj.calls == [("Halt",)]


@pytest.mark.parametrize("value,expected", [
    (["true"], True), (["false"], False), (["1"], True), (["0"], False),
])
def test_rotator_put_reverse_sets_bool(value, expected):
    obj = _RichRot()
    DEVICE_API["rotator"]["put"]["reverse"](obj, {"Reverse": value})
    assert obj.Reverse is expected


class _RichSwitch:
    """A fake with every ASCOM ISwitchV2 member the handlers touch, keyed by
    switch Id so per-port argument marshaling is observable."""

    def __init__(self):
        self.Connected = False
        self.MaxSwitch = 3
        self._names = {0: "Power", 1: "Dew Heater", 2: "USB Hub"}
        self._writable = {0: True, 1: True, 2: False}
        self._min = {0: 0.0, 1: 0.0, 2: 0.0}
        self._max = {0: 1.0, 1: 1.0, 2: 1.0}
        self._value = {0: 0.0, 1: 1.0, 2: 1.0}
        self.calls = []

    def GetSwitchName(self, i):
        return self._names[i]

    def CanWrite(self, i):
        return self._writable[i]

    def MinSwitchValue(self, i):
        return self._min[i]

    def MaxSwitchValue(self, i):
        return self._max[i]

    def GetSwitchValue(self, i):
        return self._value[i]

    def SetSwitchValue(self, i, v):
        self.calls.append(("SetSwitchValue", i, v))


def test_switch_get_maxswitch():
    obj = _RichSwitch()
    result = DEVICE_API["switch"]["get"]["maxswitch"](obj, {})
    assert result == 3
    assert type(result) is int


_SWITCH_ID_GETS = [
    ("getswitchname", 1, "Dew Heater", str),
    ("canwrite", 2, False, bool),
    ("minswitchvalue", 0, 0.0, float),
    ("maxswitchvalue", 0, 1.0, float),
    ("getswitchvalue", 1, 1.0, float),
]


@pytest.mark.parametrize("method,switch_id,expected,typ", _SWITCH_ID_GETS)
def test_switch_get_each_method_marshals_id(method, switch_id, expected, typ):
    obj = _RichSwitch()
    result = DEVICE_API["switch"]["get"][method](obj, {"Id": [str(switch_id)]})
    assert result == expected
    assert type(result) is typ


def test_switch_put_setswitchvalue_marshals_id_and_value():
    obj = _RichSwitch()
    DEVICE_API["switch"]["put"]["setswitchvalue"](obj, {"Id": ["2"], "Value": ["0.75"]})
    assert obj.calls == [("SetSwitchValue", 2, 0.75)]
    (_, switch_id, value) = obj.calls[0]
    assert type(switch_id) is int and type(value) is float


class _RichSafety:
    """A fake with the single ASCOM ISafetyMonitorV3 member the handler touches."""

    def __init__(self):
        self.Connected = False
        self.IsSafe = 0


@pytest.mark.parametrize("val,expected", [
    (1, True), (0, False), (True, True), (False, False),
])
def test_safetymonitor_get_issafe(val, expected):
    obj = _RichSafety()
    obj.IsSafe = val
    result = DEVICE_API["safetymonitor"]["get"]["issafe"](obj, {})
    assert result is expected


def test_handlers_register_is_idempotent():
    # setdefault at dict level; re-running register() must not duplicate/break.
    handlers_misc.register()
    assert "move" in DEVICE_API["focuser"]["put"]
    assert "names" in DEVICE_API["filterwheel"]["get"]
    assert "movemechanical" in DEVICE_API["rotator"]["put"]
    assert "setswitchvalue" in DEVICE_API["switch"]["put"]
    assert "issafe" in DEVICE_API["safetymonitor"]["get"]
    assert callable(DEVICE_API["focuser"]["get"]["maxstep"])
