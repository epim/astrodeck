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
#: How close counts as arrived. The EAF is an exact-step device, so this is a
#: guard against an off-by-one in the SDK's read-back, not a real tolerance.
ARRIVAL_TOLERANCE_STEPS = 2


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
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
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
        start = await self._call(self._sdk.get_position, what="read position")
        await self._call(self._sdk.move, position, what="EAFMove")
        deadline = asyncio.get_running_loop().time() + EAF_MOVE_TIMEOUT_S
        try:
            # ARRIVAL, not absence-of-motion.
            #
            # This loop used to return as soon as is_moving() read false TWICE,
            # which is instantly true when the motor never engages. On 2026-07-31
            # the EAF refused every move above position 360 — against a stop —
            # and this returned SUCCESS every time. The user typed 22000, pressed
            # Go, and nothing happened, with no error anywhere: not in the UI, not
            # in the log, not in the API response. A move that did not move must
            # never look like a move that did.
            #
            # So: succeed only when the position actually REACHES the target, and
            # fail loudly when the motor goes idle somewhere else.
            last = start
            idle_polls = 0
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    now = await self._call(self._sdk.get_position,
                                           what="read position")
                    raise DeviceError(
                        f"{self.name}: move to {position} timed out after "
                        f"{EAF_MOVE_TIMEOUT_S:.0f}s — stopped at {now} "
                        f"(started from {start})")
                await asyncio.sleep(POLL_S)
                pos = await self._call(self._sdk.get_position,
                                       what="read position")
                moving, _hand = await self._call(
                    self._sdk.is_moving, what="poll move")
                if abs(pos - position) <= ARRIVAL_TOLERANCE_STEPS:
                    return                                   # actually arrived
                if pos != last:
                    last, idle_polls = pos, 0                 # still making progress
                    continue
                # Not at the target, and the position is not changing. Two polls
                # of that with the motor idle is a stall or a limit, not a slow
                # start — the original two-poll guard against reading
                # not-moving before the motor engages is preserved here.
                idle_polls = idle_polls + 1 if not moving else 0
                if idle_polls >= 2:
                    raise DeviceError(
                        f"{self.name}: move to {position} did not happen — the "
                        f"focuser stopped at {pos} and is no longer moving. It "
                        "is probably at a mechanical limit or the drawtube is "
                        "jammed; try a smaller move in the other direction.")
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


class CaaRotator(Rotator):
    """ZWO CAA as an AstroDeck Rotator — MECHANICAL-space only.

    The SDK's own sync (``CAACurDegree``) is NEVER called: with the SDK's
    logical angle never offset, ``CAAGetDegree`` remains mechanical truth and
    the base class's client-side sync layer does the sky mapping (the same
    "never call a driver's own Sync" rule every rotator backend follows)."""

    backend = "zwo-usb"
    hardware = True
    can_reverse = True

    def __init__(self, sdk, dev_id: int, name: str = "ZWO CAA"):
        super().__init__(name)
        self._sdk = sdk
        self._id = dev_id
        self._lock = asyncio.Lock()
        self.firmware = ""
        self.model = ""

    async def _call(self, fn, *args, what: str):
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, self._id, *args)
            except ZwoSdkError as exc:
                raise _sdk_guard(exc, self.name, what) from exc

    async def connect(self) -> None:
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
        async with self._lock:
            try:
                await asyncio.to_thread(self._sdk.open, self._id)
                self.model = await asyncio.to_thread(self._sdk.get_type, self._id)
                self.firmware = await asyncio.to_thread(
                    self._sdk.firmware, self._id)
            except ZwoSdkError as exc:
                raise _sdk_guard(exc, self.name, "connect") from exc
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
        d["model"] = self.model
        return d

    async def get_mechanical_position(self) -> float:
        return float(await self._call(self._sdk.get_degree, what="get degree"))

    async def move_mechanical(self, mech_deg: float) -> None:
        await self._call(self._sdk.move_to_mechanical, float(mech_deg),
                         what="CAAMoveToMechanical")
        deadline = asyncio.get_running_loop().time() + self.MOVE_TIMEOUT_S
        try:
            settled = 0
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: rotation failed to settle within "
                        f"{self.MOVE_TIMEOUT_S:.0f}s — halted")
                await asyncio.sleep(POLL_S)
                moving, hand = await self._call(
                    self._sdk.is_moving, what="poll move")
                if hand:
                    # SDK contract: hand-controller motion cannot be aborted by
                    # CAAStop — surface it rather than spin until timeout.
                    raise DeviceError(
                        f"{self.name}: the hand controller is moving the "
                        "rotator — release it and retry")
                # two consecutive not-moving polls (review C-minor 4).
                settled = settled + 1 if not moving else 0
                if settled >= 2:
                    return
        except BaseException:
            try:
                await self._call(self._sdk.stop, what="halt")
            except Exception:  # noqa: BLE001 - halt is best-effort on teardown
                pass
            raise

    async def halt(self) -> None:
        await self._call(self._sdk.stop, what="halt")

    async def is_moving(self) -> bool:
        moving, _hand = await self._call(self._sdk.is_moving, what="poll move")
        return bool(moving)

    async def get_reverse(self) -> bool:
        return bool(await self._call(self._sdk.get_reverse, what="get reverse"))

    async def set_reverse(self, value: bool) -> None:
        await self._call(self._sdk.set_reverse, bool(value), what="set reverse")


# ------------------------------------------------------------------ session

class ZwoUsbSession:
    """One session for ALL zwo-usb roles (the backend is hostless, so the
    orchestrator coalesces rotator+focuser here). SDK handles built lazily via
    the ``zwo_sdk.make_*`` seams so tests can inject fakes."""

    name = "zwo-usb"

    def __init__(self):
        self._devices: dict[str, object] = {}

    def _first_unit(self, sdk, kind: str) -> int:
        n = sdk.count()
        if n < 1:
            raise DeviceError(f"no {kind} attached (SDK enumerated 0 units)")
        return sdk.get_id(0)

    async def get_device(self, role: str, conn):
        if role in self._devices:
            return self._devices[role]
        name = (getattr(conn, "extra", None) or {}).get("name") or None
        try:
            if role == "focuser":
                sdk = zwo_sdk.make_eaf()
                dev = EafFocuser(sdk, await asyncio.to_thread(
                    self._first_unit, sdk, "EAF"), name=name or "ZWO EAF")
            elif role == "rotator":
                sdk = zwo_sdk.make_caa()
                dev = CaaRotator(sdk, await asyncio.to_thread(
                    self._first_unit, sdk, "CAA"), name=name or "ZWO CAA")
            else:
                raise DeviceError(
                    f"zwo-usb fills only rotator/focuser (asked {role!r})")
        except ZwoSdkError as exc:
            raise DeviceError(f"zwo-usb {role}: SDK unavailable — {exc}") from exc
        dev.role = role
        await dev.connect()
        self._devices[role] = dev
        return dev

    def native_guider(self):
        return None

    def guide_camera(self):
        return None

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
                await dev.disconnect()      # halts, then closes the SDK handle
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass


# ------------------------------------------------------------------ backend

class ZwoUsbBackend:
    """ZWO USB accessories (CAA rotator + EAF focuser) over the bundled SDK."""

    name = "zwo-usb"
    label = "ZWO USB accessories"
    roles = ("rotator", "focuser")
    discoverable = True
    hostless = True                 # one session for every role (one SDK context)
    version = "0"                   # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "local"
    hardware = True
    driver_type = "zwo-usb"

    async def open(self, conn) -> ZwoUsbSession:
        return ZwoUsbSession()

    async def discover(self) -> list[dict]:
        """Enumerate attached CAA/EAF units via the SDK (guarded; [] on any
        failure — absent DLLs must not break discovery)."""
        found: list[dict] = []
        try:
            eaf = zwo_sdk.make_eaf()
            for i in range(await asyncio.to_thread(eaf.count)):
                found.append({"role": "focuser", "name": "ZWO EAF (USB)",
                              "verified": True})
        except Exception:  # noqa: BLE001
            pass
        try:
            caa = zwo_sdk.make_caa()
            for i in range(await asyncio.to_thread(caa.count)):
                found.append({"role": "rotator", "name": "ZWO CAA (USB)",
                              "verified": True})
        except Exception:  # noqa: BLE001
            pass
        return found


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = ZwoUsbBackend()
    from ... import __version__
    b.version = __version__
    register(b)
