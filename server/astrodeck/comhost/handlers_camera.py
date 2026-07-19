"""Camera COM<->Alpaca handlers (COM-T3). Maps exactly the Alpaca methods
AlpacaCamera in devices/alpaca.py calls. ImageArray is returned as the [x][y]
nested structure the client reshapes+transposes (json path in _download_image);
a real comtypes SAFEARRAY is already a nested tuple, so json serialization is
the same shape a real Alpaca server's JSON ImageArray uses.

Deadline note: a long exposure never blocks a single COM call. ASCOM
StartExposure returns immediately and the camera exposes in the background; the
existing AlpacaCamera.expose polls imageready itself (its own
exposure_s + margin deadline). Every marshaled COM call here (StartExposure,
each ImageReady read, the ImageArray read) returns promptly, so the COM-T2 30s
per-call deadline is correct for all of them and is never applied to the
exposure duration.
"""
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


def _imagearray(o, _):
    # Return the driver's ImageArray as nested lists. comtypes yields a nested
    # tuple (SAFEARRAY [x][y]); list(...) makes it json-serializable. The client
    # does np.array(Value).T, matching a real Alpaca JSON ImageArray.
    raw = o.ImageArray
    return [list(col) for col in raw]


def register() -> None:
    c = DEVICE_API.setdefault("camera", {"get": {}, "put": {}})
    g, p = c["get"], c["put"]
    g["cameraxsize"] = lambda o, _: int(o.CameraXSize)
    g["cameraysize"] = lambda o, _: int(o.CameraYSize)
    g["pixelsizex"] = lambda o, _: float(o.PixelSizeX)
    g["gainmax"] = lambda o, _: int(o.GainMax)
    g["cansetccdtemperature"] = lambda o, _: bool(o.CanSetCCDTemperature)
    g["sensortype"] = lambda o, _: int(o.SensorType)
    g["maxadu"] = lambda o, _: int(o.MaxADU)
    g["coolerpower"] = lambda o, _: float(o.CoolerPower)
    g["cooleron"] = lambda o, _: bool(o.CoolerOn)
    g["ccdtemperature"] = lambda o, _: float(o.CCDTemperature)
    g["setccdtemperature"] = lambda o, _: float(o.SetCCDTemperature)
    g["imageready"] = lambda o, _: bool(o.ImageReady)
    g["gain"] = lambda o, _: int(o.Gain)
    g["offset"] = lambda o, _: int(o.Offset)
    g["imagearray"] = _imagearray
    p["gain"] = lambda o, prm: setattr(o, "Gain", _i(prm, "Gain"))
    p["offset"] = lambda o, prm: setattr(o, "Offset", _i(prm, "Offset"))
    p["binx"] = lambda o, prm: setattr(o, "BinX", _i(prm, "BinX"))
    p["biny"] = lambda o, prm: setattr(o, "BinY", _i(prm, "BinY"))
    p["numx"] = lambda o, prm: setattr(o, "NumX", _i(prm, "NumX"))
    p["numy"] = lambda o, prm: setattr(o, "NumY", _i(prm, "NumY"))
    p["startexposure"] = lambda o, prm: o.StartExposure(
        _f(prm, "Duration"), _b(prm, "Light"))
    p["cooleron"] = lambda o, prm: setattr(o, "CoolerOn", _b(prm, "CoolerOn"))
    p["setccdtemperature"] = lambda o, prm: setattr(
        o, "SetCCDTemperature", _f(prm, "SetCCDTemperature"))
    p["abortexposure"] = lambda o, _: o.AbortExposure()
    p["stopexposure"] = lambda o, _: o.StopExposure()


register()
