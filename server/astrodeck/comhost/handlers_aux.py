"""CoverCalibrator/Dome/ObservingConditions COM<->Alpaca handlers (COM-T5).

Host-side only: AstroDeck has no ABC/client for these yet (they belong to the
later flats/dome/weather waves). Serving them here means those waves consume a
ready Alpaca surface with no comhost change. Excluded from ascom_registry
offers (no assignable role) so they never appear in role dropdowns."""
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
    cc = DEVICE_API.setdefault("covercalibrator", {"get": {}, "put": {}})
    cc["get"]["coverstate"] = lambda o, _: int(o.CoverState)
    cc["get"]["calibratorstate"] = lambda o, _: int(o.CalibratorState)
    cc["get"]["brightness"] = lambda o, _: int(o.Brightness)
    cc["get"]["maxbrightness"] = lambda o, _: int(o.MaxBrightness)
    cc["put"]["opencover"] = lambda o, _: o.OpenCover()
    cc["put"]["closecover"] = lambda o, _: o.CloseCover()
    cc["put"]["calibratoron"] = lambda o, prm: o.CalibratorOn(_i(prm, "Brightness"))
    cc["put"]["calibratoroff"] = lambda o, _: o.CalibratorOff()

    d = DEVICE_API.setdefault("dome", {"get": {}, "put": {}})
    d["get"]["athome"] = lambda o, _: bool(o.AtHome)
    d["get"]["atpark"] = lambda o, _: bool(o.AtPark)
    d["get"]["shutterstatus"] = lambda o, _: int(o.ShutterStatus)
    d["get"]["slewing"] = lambda o, _: bool(o.Slewing)
    d["get"]["azimuth"] = lambda o, _: float(o.Azimuth)
    d["get"]["slaved"] = lambda o, _: bool(o.Slaved)
    d["put"]["slewtoazimuth"] = lambda o, prm: o.SlewToAzimuth(_f(prm, "Azimuth"))
    d["put"]["openshutter"] = lambda o, _: o.OpenShutter()
    d["put"]["closeshutter"] = lambda o, _: o.CloseShutter()
    d["put"]["park"] = lambda o, _: o.Park()
    d["put"]["abortslew"] = lambda o, _: o.AbortSlew()
    d["put"]["slaved"] = lambda o, prm: setattr(o, "Slaved", _b(prm, "Slaved"))

    oc = DEVICE_API.setdefault("observingconditions", {"get": {}, "put": {}})
    for m, member in (("temperature", "Temperature"), ("humidity", "Humidity"),
                      ("cloudcover", "CloudCover"), ("dewpoint", "DewPoint"),
                      ("pressure", "Pressure"), ("windspeed", "WindSpeed"),
                      ("winddirection", "WindDirection"),
                      ("skytemperature", "SkyTemperature"),
                      ("rainrate", "RainRate")):
        oc["get"][m] = (lambda member: lambda o, _: float(getattr(o, member)))(member)
    oc["put"]["refresh"] = lambda o, _: o.Refresh()


register()
