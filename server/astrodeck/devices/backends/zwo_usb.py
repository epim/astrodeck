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
#: A travel limit below this many steps, on a focuser whose hardware ceiling is
#: an order of magnitude larger, is almost certainly a reset artefact rather
#: than a deliberate setting — 360 steps out of 600000 is 0.06% of the travel,
#: which no one configures on purpose. Worth saying out loud.
SUSPICIOUS_LIMIT_STEPS = 1000


#: EAF_focuser.h documents the motor codes E0/E5 and battery codes E6/E7/E8.
#: E0 IS THE HEALTHY ONE — reporting it as a fault would attach "the device
#: reports motor error E0" to every failure that had nothing wrong with it.
NO_FAULT_CODE = "E0"
#: E5 is the one that matters here: the device CAN report a stall, on firmware
#: that implements EAFGetErrorCode at all. The rig's 3.3.8 does not (see
#: describe()["reports_diagnostics"]) — but a firmware that does would make a
#: sweep-to-the-stops travel calibration terminable, which it otherwise is not.
MOTOR_CODE_MEANINGS = {"E5": " (motor stall)"}


def _is_fault(code: str | None) -> bool:
    return bool(code) and code != NO_FAULT_CODE


def _sdk_guard(exc: ZwoSdkError, name: str, what: str) -> DeviceError:
    return DeviceError(f"{name}: {what} failed — {exc}")


class EafFocuser(Focuser):
    """ZWO EAF as an AstroDeck Focuser (absolute steps over the SDK)."""

    backend = "zwo-usb"
    hardware = True
    can_set_position_reference = True

    def __init__(self, sdk, dev_id: int, name: str = "ZWO EAF",
                 max_step: int | None = None, state_key: str | None = None):
        super().__init__(name)
        self._sdk = sdk
        self._id = dev_id
        self._lock = asyncio.Lock()
        self.firmware = ""
        #: The rig's configured travel limit, re-applied on every connect
        #: (driver `extra.max_step`). None = leave whatever the device holds.
        self._max_step = int(max_step) if max_step else None
        #: Key for the remembered position (driver id), so two focusers on one
        #: box do not overwrite each other's reference.
        self._state_key = state_key
        #: Diagnostics read once at connect. `reports_diagnostics` is the one
        #: that matters: it says whether this firmware answers EAFGetErrorCode
        #: at all, which decides whether a refused move can ever explain itself.
        self._motor_error: str | None = None
        self._battery_error: str | None = None
        self._power_off_reason: int | None = None
        self._reports_diagnostics = False

    async def _call(self, fn, *args, what: str):
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, self._id, *args)
            except ZwoSdkError as exc:
                raise _sdk_guard(exc, self.name, what) from exc

    async def _complaint(self) -> str:
        """What the device says is wrong, as a trailing clause for an error
        message — or "" when it has nothing to report or will not say (several
        firmwares answer NOT_SUPPORTED, and silence is not a fault).

        Best-effort by construction: this only ever runs while building the
        text of an error that has ALREADY happened, so it must not be able to
        replace that error with one of its own.
        """
        try:
            codes = await asyncio.to_thread(self._sdk.error_codes, self._id)
        except Exception:  # noqa: BLE001
            return ""
        if not codes:
            return ""
        motor, battery = codes
        parts = []
        if _is_fault(motor):
            parts.append(f"motor error {motor}{MOTOR_CODE_MEANINGS.get(motor, '')}")
        if _is_fault(battery):
            parts.append(f"battery error {battery}")
        return f" The device reports {', '.join(parts)}." if parts else ""

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
        # EAF_INFO.MaxStep is the HARDWARE ceiling; EAFGetMaxStep is the limit
        # the firmware actually enforces, and they are not the same number. On
        # 2026-07-31 they read 600000 and 360: every move above 360 was silently
        # clamped by the device while this driver believed it had the full range,
        # so the user typed 22000, pressed Go, and nothing happened. Use the
        # enforced limit, and SAY BOTH at connect — one log line would have
        # ended that in minutes instead of hours.
        self.hardware_max_position = int(max_step)
        from ...events import bus

        # RESTORE the configured limit before reading it back.
        #
        # The EAF does not keep its travel limit across a reconnect: on
        # 2026-07-31 a device deliberately set to 40000 came back reporting 360
        # after a server restart, with its position counter also reset to 0.
        # That is the entire "type 22000, press Go, nothing happens" bug, and it
        # re-arms itself on every restart — so setting it once by hand fixes
        # nothing. Push it back every time, then verify the device took it.
        wanted: int | None = None
        if self._max_step:
            wanted = max(1, min(self._max_step, int(max_step)))
            try:
                await asyncio.to_thread(self._sdk.set_max_step, self._id, wanted)
            except Exception as exc:  # noqa: BLE001 - older SDKs lack the export
                bus.log("warning",
                        f"{self.name}: could not apply the configured travel "
                        f"limit {wanted} ({type(exc).__name__}: {exc}) — the "
                        "device keeps whatever limit it woke up with", "focuser")

        enforced: int | None = None
        why: str | None = None
        try:
            enforced = int(await asyncio.to_thread(self._sdk.get_max_step, self._id))
        except Exception as exc:     # noqa: BLE001 - older SDKs lack the export
            enforced, why = None, f"{type(exc).__name__}: {exc}"
        self.max_position = enforced if enforced else int(max_step)

        # ALWAYS say the numbers, even when they agree. The version of this that
        # only spoke up on a mismatch is how a falling-back read looks identical
        # to a healthy one: the rig reported max 600000 with the enforced limit
        # never read, and nothing in the log said which of those had happened.
        if wanted is not None and enforced == wanted:
            bus.log("info", f"{self.name}: travel limit {enforced} restored from "
                            f"the rig's configuration (hardware max {max_step})",
                    "focuser")
        elif wanted is not None:
            bus.log("warning",
                    f"{self.name}: asked for a travel limit of {wanted} but the "
                    f"device reports {enforced} — moves past {self.max_position} "
                    "will be refused", "focuser")
        elif enforced and enforced != max_step:
            bus.log("info",
                    f"{self.name}: hardware max {max_step}, device travel limit "
                    f"{enforced}, using {self.max_position}", "focuser")
        elif enforced:
            bus.log("info", f"{self.name}: travel limit {enforced} (= hardware max)",
                    "focuser")
        else:
            # Not a detail. `max_position` is what the UI clamps Go-to targets
            # against, so an unread limit means the panel will happily offer
            # positions the firmware will refuse without a word.
            bus.log("warning",
                    f"{self.name}: could not read the enforced travel limit "
                    f"({why or f'EAFGetMaxStep returned {enforced!r}'}) — falling "
                    f"back to the hardware max {max_step}, which the firmware may "
                    "not honour", "focuser")

        # An unconfigured, implausibly small limit is the reset artefact above.
        # Say what it means and what to do, rather than leaving the user to
        # discover it as "Go does nothing".
        if (self._max_step is None and enforced
                and enforced < SUSPICIOUS_LIMIT_STEPS
                and int(max_step) > 10 * enforced):
            bus.log("warning",
                    f"{self.name}: the focuser will only accept positions up to "
                    f"{enforced} of its {max_step} hardware steps. That is almost "
                    "certainly a limit the EAF reset itself to, not one you set — "
                    "moves past it are refused silently by the device. Set this "
                    "driver's travel limit (max_step) so it is restored on every "
                    "connect.", "focuser")
        self.connected = True
        await self._read_diagnostics()
        await self._check_position_reference()

    async def _read_diagnostics(self) -> None:
        """Ask the device what it will admit about itself. Read-only, once.

        Whether EAFGetErrorCode answers at all is firmware-dependent (several
        return NOT_SUPPORTED), and it decides whether a refused move can ever
        explain itself. Recording it here means the answer is visible on the
        Equipment page instead of only discoverable during a failure.
        """
        try:
            codes = await asyncio.to_thread(self._sdk.error_codes, self._id)
        except Exception:  # noqa: BLE001
            codes = None
        if codes is not None:
            self._reports_diagnostics = True
            self._motor_error, self._battery_error = codes or (None, None)
        try:
            self._power_off_reason = await asyncio.to_thread(
                self._sdk.power_off_reason, self._id)
        except Exception:  # noqa: BLE001
            self._power_off_reason = None

    async def _check_position_reference(self) -> None:
        """Compare where the device says it is against where we last saw it.

        This cannot REPAIR the reference — only a human putting the drawtube
        somewhere known can do that (set_position_reference). Its whole job is
        to make the loss visible: on 2026-07-31 an EAF came back reading 0 after
        sitting at 30000, and the only symptom was that stored focus positions
        quietly stopped meaning what they used to.
        """
        from ...config import load_focuser_position
        from ...events import bus
        try:
            last = load_focuser_position(self._state_key)
            now = int(await asyncio.to_thread(self._sdk.get_position, self._id))
        except Exception:  # noqa: BLE001 - a bookkeeping read must never cost
            return         # the connect itself
        if last is None:
            self._remember(now)
            return
        if abs(now - last) <= ARRIVAL_TOLERANCE_STEPS:
            return

        reason = None
        try:
            reason = await asyncio.to_thread(self._sdk.power_off_reason, self._id)
        except Exception:  # noqa: BLE001
            reason = None
        power = ""
        if reason == 1:
            power = " The device reports it was last powered off in shipping mode."
        elif reason == 0:
            power = " The device reports a normal last power-off."

        bus.log("warning",
                f"{self.name}: position reads {now} but it was {last} when we "
                f"last saw it, and nothing moved it in between — the focuser "
                f"lost its count (this happens when it loses power). The "
                f"drawtube has NOT moved, but every saved focus position is now "
                f"off by about {last - now} steps. Re-anchor it: put the tube "
                f"somewhere you know and set the position.{power}", "focuser")
        self._remember(now)

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
        d["hardware_max_position"] = getattr(self, "hardware_max_position", None)
        # What the device will admit about itself. Recorded at connect rather
        # than polled: these are diagnostics, and the question they answer —
        # "can this focuser tell us anything when a move is refused?" — is a
        # property of the firmware, not of the moment.
        d["motor_error_code"] = self._motor_error
        d["battery_error_code"] = self._battery_error
        d["power_off_reason"] = self._power_off_reason
        d["reports_diagnostics"] = self._reports_diagnostics
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
                        f"(started from {start}).{await self._complaint()}")
                await asyncio.sleep(POLL_S)
                pos = await self._call(self._sdk.get_position,
                                       what="read position")
                moving, _hand = await self._call(
                    self._sdk.is_moving, what="poll move")
                if abs(pos - position) <= ARRIVAL_TOLERANCE_STEPS:
                    self._remember(pos)
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
                    self._remember(pos)
                    raise DeviceError(
                        f"{self.name}: move to {position} did not happen — the "
                        f"focuser stopped at {pos} and is no longer moving. It "
                        "is probably at a mechanical limit or the drawtube is "
                        "jammed; try a smaller move in the other direction."
                        + await self._complaint())
        except BaseException:
            # halt on ANY abnormal exit (cancel/timeout/SDK failure)
            try:
                await self._call(self._sdk.stop, what="halt")
            except Exception:  # noqa: BLE001 - halt is best-effort on teardown
                pass
            raise

    async def halt(self) -> None:
        await self._call(self._sdk.stop, what="halt")

    def _remember(self, position: int) -> None:
        """Record where the drawtube ended up, so the next connect can notice
        if the device came back somewhere else. Never raises."""
        from ...config import save_focuser_position
        save_focuser_position(self._state_key, int(position))

    async def set_position_reference(self, position: int) -> None:
        """Tell the EAF it is at ``position``. Moves nothing.

        Refused mid-move on purpose: re-anchoring while the tube is travelling
        writes a number that is already stale by the time it lands."""
        if await self.is_moving():
            raise DeviceError(
                f"{self.name}: the focuser is moving — wait for it to stop "
                "before setting its position")
        position = int(position)
        if not (0 <= position <= self.max_position):
            raise DeviceError(
                f"{self.name}: position {position} is outside "
                f"0..{self.max_position}")
        await self._call(self._sdk.reset_position, position,
                         what="EAFResetPostion")
        self._remember(position)
        from ...events import bus
        bus.log("info", f"{self.name}: position reference set to {position} "
                        "— the drawtube did not move", "focuser")

    async def is_moving(self) -> bool:
        moving, _hand = await self._call(self._sdk.is_moving, what="poll move")
        return bool(moving)

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
        extra = getattr(conn, "extra", None) or {}
        name = extra.get("name") or None
        try:
            if role == "focuser":
                sdk = zwo_sdk.make_eaf()
                dev = EafFocuser(sdk, await asyncio.to_thread(
                    self._first_unit, sdk, "EAF"), name=name or "ZWO EAF",
                    # The EAF forgets its travel limit on reconnect; this is the
                    # rig's remembered value, re-applied on every connect.
                    max_step=extra.get("max_step"),
                    # Per-driver key so two focusers cannot overwrite each
                    # other's remembered position. ConnSpec calls it driver_id
                    # (not `id`); None for a raw spec, which shares one slot.
                    state_key=getattr(conn, "driver_id", None))
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
