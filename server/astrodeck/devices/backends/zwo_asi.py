"""ZWO ASI camera backend — a native camera over the ASICamera2 SDK.

One hostless backend/session; fills the ``camera`` or ``guide_camera`` role with
a NativeCamera wrapping an AsiCameraAdapter. On this rig the ASI220MM is the
guide camera, but the profile may assign it to either role. Absent hardware/DLLs
degrade to a clear DeviceError (get_device) or an empty discover(), never a crash.
"""
from __future__ import annotations

import asyncio
import logging

from ..base import DeviceError
from ..cameras import zwo_asi
from ..cameras.adapter import CAMERA_BUSY_HINT
from ..cameras.engine import NativeCamera
from ..cameras.zwo_asi_sdk import AsiSdkError, make_asi

_CAMERA_ROLES = ("camera", "guide_camera")
_log = logging.getLogger("astrodeck.cameras")


class ZwoAsiSession:
    """One session for the zwo-asi backend (hostless). Builds cameras lazily via
    the ``zwo_asi.make_asi`` seam so tests can inject a fake SDK."""

    name = "zwo-asi"

    def __init__(self):
        self._devices: dict[str, object] = {}

    async def get_device(self, role: str, conn):
        if role in self._devices:
            return self._devices[role]
        if role not in _CAMERA_ROLES:
            raise DeviceError(f"zwo-asi fills only camera/guide_camera (asked {role!r})")
        extra = getattr(conn, "extra", None) or {}
        index = int(extra.get("index", 0))
        name = extra.get("name") or "ZWO ASI"
        try:
            dev = NativeCamera(zwo_asi.AsiCameraAdapter(index=index), index=index,
                               name=name)
            dev.role = role
            await dev.connect()
        except (AsiSdkError, DeviceError) as exc:
            # A held camera enumerates as 0 units -> get_property INVALID_INDEX,
            # indistinguishable from "absent". Surface the actionable hint.
            _log.warning("zwo-asi %s could not connect: %s. %s",
                         role, exc, CAMERA_BUSY_HINT)
            raise DeviceError(
                f"zwo-asi {role}: camera unavailable ({exc}). {CAMERA_BUSY_HINT}"
            ) from exc
        self._devices[role] = dev
        return dev

    def native_guider(self):
        return None

    def guide_camera(self):
        return self._devices.get("guide_camera")

    def native_solver(self):
        return None

    async def health(self) -> dict | None:
        if not self._devices:
            return None
        return {"devices": sorted(self._devices)}

    async def close(self) -> None:
        devices, self._devices = dict(self._devices), {}
        for dev in devices.values():
            try:
                await dev.disconnect()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass


class ZwoAsiBackend:
    """ZWO ASI camera over the bundled ASICamera2 SDK."""

    name = "zwo-asi"
    label = "ZWO ASI camera"
    roles = _CAMERA_ROLES
    discoverable = True
    hostless = True
    version = "0"                   # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "local"
    hardware = True
    driver_type = "zwo-asi"

    async def open(self, conn) -> ZwoAsiSession:
        return ZwoAsiSession()

    async def discover(self) -> list[dict]:
        """Enumerate attached ASI units (guarded; [] on any failure so absent
        DLLs never break discovery).

        One entry PER ROLE the unit can fill, because the probe offers exactly
        what discovery reports (drivers.py ``_offers_from``). Unlike the ZWO
        accessory bus — where rotator and focuser are two separate devices — one
        camera genuinely fills either role, so it reports both and the profile
        decides which it takes. Emitting only the guide_camera "hint" here would
        make the probe drop 'camera' and the unit would vanish from the imaging
        picker."""
        found: list[dict] = []
        try:
            sdk = make_asi()
            for i in range(await asyncio.to_thread(sdk.count)):
                for role in _CAMERA_ROLES:
                    found.append({"role": role, "name": "ZWO ASI (USB)",
                                  "verified": True, "index": i})
        except Exception:  # noqa: BLE001
            pass
        return found


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = ZwoAsiBackend()
    from ... import __version__
    b.version = __version__
    register(b)
