"""Focuser/FilterWheel/Rotator/Switch/SafetyMonitor COM<->Alpaca handlers
(COM-T4). Maps exactly the Alpaca methods the corresponding Alpaca* client
classes in devices/alpaca.py call. Handlers run on the device STA thread."""
from __future__ import annotations

from .server import DEVICE_API


def _f(params, key):
    v = params.get(key)
    return float(v[0] if isinstance(v, list) else v)


def _i(params, key):
    v = params.get(key)
    return int(v[0] if isinstance(v, list) else v)


def _b(params, key):
    v = params.get(key)
    return str(v[0] if isinstance(v, list) else v).strip().lower() in (
        "true", "1", "yes")


def register() -> None:
    f = DEVICE_API.setdefault("focuser", {"get": {}, "put": {}})
    f["get"]["maxstep"] = lambda o, _: int(o.MaxStep)
    f["get"]["stepsize"] = lambda o, _: float(o.StepSize)
    f["get"]["position"] = lambda o, _: int(o.Position)
    f["get"]["ismoving"] = lambda o, _: bool(o.IsMoving)
    f["get"]["temperature"] = lambda o, _: float(o.Temperature)
    f["put"]["move"] = lambda o, prm: o.Move(_i(prm, "Position"))
    f["put"]["halt"] = lambda o, _: o.Halt()

    w = DEVICE_API.setdefault("filterwheel", {"get": {}, "put": {}})
    w["get"]["names"] = lambda o, _: list(o.Names)
    w["get"]["position"] = lambda o, _: int(o.Position)
    w["put"]["position"] = lambda o, prm: setattr(o, "Position", _i(prm, "Position"))

    r = DEVICE_API.setdefault("rotator", {"get": {}, "put": {}})
    r["get"]["canreverse"] = lambda o, _: bool(o.CanReverse)
    r["get"]["mechanicalposition"] = lambda o, _: float(o.MechanicalPosition)
    r["get"]["ismoving"] = lambda o, _: bool(o.IsMoving)
    r["get"]["reverse"] = lambda o, _: bool(o.Reverse)
    r["put"]["movemechanical"] = lambda o, prm: o.MoveMechanical(_f(prm, "Position"))
    r["put"]["halt"] = lambda o, _: o.Halt()
    r["put"]["reverse"] = lambda o, prm: setattr(o, "Reverse", _b(prm, "Reverse"))

    s = DEVICE_API.setdefault("switch", {"get": {}, "put": {}})
    s["get"]["maxswitch"] = lambda o, _: int(o.MaxSwitch)
    s["get"]["getswitchname"] = lambda o, prm: str(o.GetSwitchName(_i(prm, "Id")))
    s["get"]["canwrite"] = lambda o, prm: bool(o.CanWrite(_i(prm, "Id")))
    s["get"]["minswitchvalue"] = lambda o, prm: float(o.MinSwitchValue(_i(prm, "Id")))
    s["get"]["maxswitchvalue"] = lambda o, prm: float(o.MaxSwitchValue(_i(prm, "Id")))
    s["get"]["getswitchvalue"] = lambda o, prm: float(o.GetSwitchValue(_i(prm, "Id")))
    s["put"]["setswitchvalue"] = lambda o, prm: o.SetSwitchValue(
        _i(prm, "Id"), _f(prm, "Value"))

    sm = DEVICE_API.setdefault("safetymonitor", {"get": {}, "put": {}})
    sm["get"]["issafe"] = lambda o, _: bool(o.IsSafe)


register()
