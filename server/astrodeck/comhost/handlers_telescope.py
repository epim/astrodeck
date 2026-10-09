# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Telescope COM<->Alpaca handlers (COM-T3). Each entry maps an Alpaca method
(exactly what AlpacaTelescope in devices/alpaca.py calls) to its ASCOM COM
member. Handlers run on the device's STA thread: obj is the live COM object."""
from __future__ import annotations

from .server import DEVICE_API


def _f(params, key):  # first form/query value as float
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
    t = DEVICE_API.setdefault("telescope", {"get": {}, "put": {}})
    g, p = t["get"], t["put"]
    g["rightascension"] = lambda o, _: float(o.RightAscension)
    g["declination"] = lambda o, _: float(o.Declination)
    # #861 N5: the hub's frame probe (``Hub._mount_expects_jnow``) asks for
    # this; unserved, every ASCOM-local mount was treated as JNOW, so a COM
    # driver working in J2000 was precessed on every slew and sync.
    g["equatorialsystem"] = lambda o, _: int(o.EquatorialSystem)
    g["slewing"] = lambda o, _: bool(o.Slewing)
    g["tracking"] = lambda o, _: bool(o.Tracking)
    g["atpark"] = lambda o, _: bool(o.AtPark)
    g["canpulseguide"] = lambda o, _: bool(o.CanPulseGuide)
    g["sideofpier"] = lambda o, _: int(o.SideOfPier)
    g["guideraterightascension"] = lambda o, _: float(o.GuideRateRightAscension)
    g["guideratedeclination"] = lambda o, _: float(o.GuideRateDeclination)
    g["destinationsideofpier"] = lambda o, prm: int(
        o.DestinationSideOfPier(_f(prm, "RightAscension"), _f(prm, "Declination")))
    p["slewtocoordinatesasync"] = lambda o, prm: o.SlewToCoordinatesAsync(
        _f(prm, "RightAscension"), _f(prm, "Declination"))
    p["abortslew"] = lambda o, _: o.AbortSlew()
    p["synctocoordinates"] = lambda o, prm: o.SyncToCoordinates(
        _f(prm, "RightAscension"), _f(prm, "Declination"))
    p["tracking"] = lambda o, prm: setattr(o, "Tracking", _b(prm, "Tracking"))
    p["park"] = lambda o, _: o.Park()
    p["unpark"] = lambda o, _: o.Unpark()
    p["moveaxis"] = lambda o, prm: o.MoveAxis(_i(prm, "Axis"), _f(prm, "Rate"))
    p["pulseguide"] = lambda o, prm: o.PulseGuide(
        _i(prm, "Direction"), _i(prm, "Duration"))


register()
