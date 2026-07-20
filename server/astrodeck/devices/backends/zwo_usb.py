"""ZWO USB accessories — native CAA (rotator) + EAF (focuser) over the SDK.

One hostless backend, one session owning both SDK handles (sub-project C spec
§2). All SDK calls are blocking C calls → run in ``asyncio.to_thread`` under a
per-device ``asyncio.Lock`` (the SDKs are not documented thread-safe).

Discipline carried from the AM5N driver: connect flows only READ; every waiting
move halts (SDK stop) on ANY abnormal exit; absent hardware/DLLs degrade to
clear DeviceErrors, never crashes. The CAA driver NEVER calls the SDK's own
sync (``CAACurDegree``) — the sky↔mechanical offset lives client-side in the
``Rotator`` base class (devices/base.py:259 contract), so ``CAAGetDegree``
stays mechanical truth.
"""
from __future__ import annotations

import asyncio

from .. import zwo_sdk
from ..base import DeviceError, Focuser, Rotator
from ..zwo_sdk import ZwoSdkError

#: Poll cadence while a move is in flight.
POLL_S = 0.5
#: Wall-clock cap on a focuser move (Rotator uses its base MOVE_TIMEOUT_S=180).
EAF_MOVE_TIMEOUT_S = 120.0


def _sdk_guard(exc: ZwoSdkError, name: str, what: str) -> DeviceError:
    return DeviceError(f"{name}: {what} failed — {exc}")


class EafFocuser(Focuser):
    """ZWO EAF as an AstroDeck Focuser (absolute steps over the SDK)."""

    backend = "zwo-usb"
    hardware = True

    def __init__(self, sdk, dev_id: int, name: str = "ZWO EAF"):
        super().__init__(name)
        self._sdk = sdk
        self._id = dev_id
        self._lock = asyncio.Lock()
        self.firmware = ""

    async def _call(self, fn, *args, what: str):
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, self._id, *args)
            except ZwoSdkError as exc:
                raise _sdk_guard(exc, self.name, what) from exc

    async def connect(self) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._sdk.open, self._id)
                prop_name, max_step = await asyncio.to_thread(
                    self._sdk.get_property, self._id)
                self.firmware = await asyncio.to_thread(
                    self._sdk.firmware, self._id)
            except ZwoSdkError as exc:
                raise _sdk_guard(exc, self.name, "connect") from exc
        if prop_name:
            self.name = f"{self.name} ({prop_name})" if prop_name not in self.name else self.name
        self.max_position = int(max_step)
        self.connected = True

    async def disconnect(self) -> None:
        try:
            await self._call(self._sdk.stop, what="halt-on-disconnect")
        except DeviceError:
            pass
        try:
            async with self._lock:
                await asyncio.to_thread(self._sdk.close, self._id)
        except ZwoSdkError:
            pass
        self.connected = False

    def describe(self) -> dict:
        d = super().describe()
        d["firmware"] = self.firmware
        return d

    async def get_position(self) -> int:
        return int(await self._call(self._sdk.get_position, what="get position"))

    async def move_to(self, position: int) -> None:
        position = int(position)
        if not (0 <= position <= self.max_position):
            raise DeviceError(
                f"{self.name}: target {position} out of range 0..{self.max_position}")
        await self._call(self._sdk.move, position, what="EAFMove")
        deadline = asyncio.get_running_loop().time() + EAF_MOVE_TIMEOUT_S
        try:
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: move failed to settle within "
                        f"{EAF_MOVE_TIMEOUT_S:.0f}s — halted")
                await asyncio.sleep(POLL_S)
                moving, _hand = await self._call(
                    self._sdk.is_moving, what="poll move")
                if not moving:
                    return
        except BaseException:
            # halt on ANY abnormal exit (cancel/timeout/SDK failure)
            try:
                await self._call(self._sdk.stop, what="halt")
            except Exception:  # noqa: BLE001 - halt is best-effort on teardown
                pass
            raise

    async def halt(self) -> None:
        await self._call(self._sdk.stop, what="halt")

    async def get_temperature(self) -> float | None:
        try:
            return float(await self._call(self._sdk.get_temp, what="get temp"))
        except DeviceError:
            return None
