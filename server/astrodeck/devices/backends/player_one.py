"""Player One camera backend — a native camera over the Player One SDK.

One hostless backend/session; fills the ``camera`` (or ``guide_camera``) role
with a NativeCamera wrapping a PlayerOneAdapter. On this rig the Poseidon-M Pro
is the imaging camera. Absent hardware/DLLs degrade to a clear DeviceError
(get_device) or an empty discover(), never a crash.
"""
from __future__ import annotations

import asyncio
import logging

from ..base import DeviceError
from ..cameras import player_one
from ..cameras.adapter import CAMERA_BUSY_HINT
from ..cameras.engine import NativeCamera
from ..cameras.player_one_sdk import PlayerOneSdkError, make_player_one

_CAMERA_ROLES = ("camera", "guide_camera")
_log = logging.getLogger("astrodeck.cameras")


class PlayerOneSession:
    """One session for the player-one backend (hostless). Builds cameras lazily
    via the ``player_one.make_player_one`` seam so tests can inject a fake SDK."""

    name = "player-one"

    def __init__(self):
        self._devices: dict[str, object] = {}

    async def get_device(self, role: str, conn):
        if role in self._devices:
            return self._devices[role]
        if role not in _CAMERA_ROLES:
            raise DeviceError(
                f"player-one fills only camera/guide_camera (asked {role!r})")
        extra = getattr(conn, "extra", None) or {}
        index = int(extra.get("index", 0))
        name = extra.get("name") or "Player One camera"
        try:
            dev = NativeCamera(player_one.PlayerOneAdapter(index=index),
                               index=index, name=name)
            dev.role = role
            await dev.connect()
        except (PlayerOneSdkError, DeviceError) as exc:
            # A held camera enumerates as 0 units -> get_properties INVALID_INDEX,
            # indistinguishable from "absent". Surface the actionable hint.
            _log.warning("player-one %s could not connect: %s. %s",
                         role, exc, CAMERA_BUSY_HINT)
            raise DeviceError(
                f"player-one {role}: camera unavailable ({exc}). {CAMERA_BUSY_HINT}"
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


class PlayerOneBackend:
    """Player One camera over the Player One SDK (bundling gated on licensing —
    docs/hardware/player-one-sdk-licensing.md)."""

    name = "player-one"
    label = "Player One camera"
    roles = _CAMERA_ROLES
    discoverable = True
    hostless = True
    version = "0"                   # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "local"
    hardware = True
    driver_type = "player-one"

    async def open(self, conn) -> PlayerOneSession:
        return PlayerOneSession()

    async def discover(self) -> list[dict]:
        """Enumerate attached Player One units (guarded; [] on any failure).
        Role hint 'camera' — the Poseidon is the imaging cam here."""
        found: list[dict] = []
        try:
            sdk = make_player_one()
            for _ in range(await asyncio.to_thread(sdk.count)):
                found.append({"role": "camera", "name": "Player One (USB)",
                              "verified": True})
        except Exception:  # noqa: BLE001
            pass
        return found


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = PlayerOneBackend()
    from ... import __version__
    b.version = __version__
    register(b)
