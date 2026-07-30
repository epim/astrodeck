"""Wanderer Snowflake filter wheel — native stream-first serial driver.

Wire truth (hardware-verified 2026-07-20, docs/hardware/
rotator-focuser-filterwheel-native.md): CH340 USB serial at 19200 8N1; the
wheel STREAMS an 'A'-separated status banner ~2 Hz unprompted; commands are
offset-encoded ASCII decimals terminated by CR (goto slot N = ``200N\\r``) with
NO acks; the stream PAUSES for the duration of a physical move and resumes
with the new slot — that resume IS the completion signal.

Two hard-won operational rules:
  - Open the port with DTR/RTS held LOW (deferred-open) or the CH340 resets
    the MCU (Arduino-style auto-reset).
  - Do NOT auto-calibrate at connect (INDI sends 1500002 every connect, which
    sweeps the carousel unprompted — we never move hardware on connect).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ..base import DeviceError, FilterWheel

#: The wheel's protocol minimum firmware (the INDI driver's floor; our unit's).
MIN_FW_DATE = 20260124
#: Model-string PREFIX, not an allowlist. Known units are WSFW508 and WSFW368
#: (both 8-slot; the number is the filter diameter — 50.8 mm and 36 mm), but the
#: model is a label on a protocol we parse in full, so a WSFW we have not seen
#: is accepted on its own terms rather than rejected as "not a Snowflake". The
#: banner carries everything that actually varies: firmware date and slot count.
MODEL_PREFIX = "WSFW"
#: Fallback slot count for a banner whose per-slot letters are unreadable. The
#: REAL count comes from the wire — see ``Banner.slots``.
SLOT_COUNT = 8
#: A goto can take several seconds per hop; INDI bounds the wait at 40 s.
MOVE_TIMEOUT_S = 40.0
#: How long connect waits for the first banner before declaring "not a Snowflake".
FIRST_BANNER_TIMEOUT_S = 5.0


@dataclass
class Banner:
    model: str
    fw_date: int
    slot: int            # 1-based, as on the wire
    letters: str         # ONE per-slot filter letter, per slot ('X' = unset)
    device_id: int
    at: float            # monotonic receive time

    @property
    def slots(self) -> int:
        """How many slots this wheel has, straight from the device.

        The letters field carries exactly one character per slot, so its length
        IS the slot count. Reading it here instead of assuming ``SLOT_COUNT``
        means a wheel with a different carousel reports its own size rather than
        being described as an 8-slot unit that happens to be missing filters."""
        return len(self.letters) or SLOT_COUNT


def parse_banner(line: str) -> Banner | None:
    """Parse one stream line; None on anything malformed (resync-tolerant)."""
    parts = line.strip().split("A")
    if len(parts) < 13 or not parts[0].startswith(MODEL_PREFIX):
        return None
    try:
        return Banner(
            model=parts[0],
            fw_date=int(parts[1]),
            slot=int(float(parts[2])),
            letters=parts[3],
            device_id=int(parts[12]),
            at=time.monotonic(),
        )
    except (ValueError, IndexError):
        return None


class SnowflakeLink:
    """Reader-first serial link: a background task consumes the banner stream;
    writes are lock-guarded and CR-terminated. DTR/RTS low via deferred open."""

    def __init__(self, port_path: str):
        self.port_path = port_path
        self._ser = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.latest: Banner | None = None
        self.last_line_at: float = 0.0

    async def open(self) -> None:
        import serial
        def _open():
            s = serial.Serial()          # deferred open: lines low BEFORE open
            s.port = self.port_path
            s.baudrate = 19200
            s.timeout = 0.5
            s.dtr = False
            s.rts = False
            s.open()
            return s
        try:
            self._ser = await asyncio.to_thread(_open)
        except Exception as exc:  # noqa: BLE001
            raise DeviceError(f"cannot open {self.port_path}: {exc}") from exc
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        while self._ser is not None:
            try:
                raw = await asyncio.to_thread(self._ser.readline)
            except Exception:  # noqa: BLE001 - port gone; reader ends quietly
                return
            if not raw:
                continue
            line = raw.decode("ascii", "replace")
            self.last_line_at = time.monotonic()
            b = parse_banner(line)
            if b is not None:
                self.latest = b

    async def send(self, cmd: str) -> None:
        if self._ser is None:
            raise DeviceError("link not open")
        async with self._lock:
            data = (cmd + "\r").encode("ascii")
            await asyncio.to_thread(self._ser.write, data)

    async def wait_banner(self, pred, timeout: float) -> Banner:
        """The newest banner satisfying ``pred``, else DeviceError on timeout."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            b = self.latest
            if b is not None and pred(b):
                return b
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(
                    f"timed out ({timeout:.0f}s) waiting for the wheel")
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        ser, self._ser = self._ser, None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if ser is not None:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:  # noqa: BLE001
                pass


class SnowflakeWheel(FilterWheel):
    """The Snowflake as an AstroDeck FilterWheel (0-based slots)."""

    backend = "wanderer-snowflake"
    hardware = True

    def __init__(self, link, name: str = "Snowflake FW"):
        super().__init__(name)
        self._link = link
        self.fw_date = 0
        self.model = ""
        self.slots = SLOT_COUNT      # replaced at connect by the wheel's own count

    async def connect(self) -> None:
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
        await self._link.open()
        try:
            first = await self._link.wait_banner(
                lambda b: True, FIRST_BANNER_TIMEOUT_S)
        except DeviceError:
            await self._link.close()
            raise DeviceError(
                f"{self.name}: no Snowflake banner on {self._link.port_path} — "
                "wrong device or wheel unpowered")
        if first.fw_date < MIN_FW_DATE:
            await self._link.close()
            raise DeviceError(
                f"{self.name}: firmware {first.fw_date} older than required "
                f"{MIN_FW_DATE}")
        self.model = first.model
        self.fw_date = first.fw_date
        self.slots = first.slots
        self.filter_names = [
            (c if c not in ("X", "") else f"Slot {i + 1}")
            for i, c in enumerate(first.letters.ljust(self.slots, "X"))]
        self.connected = True
        # NO auto-calibrate: connect never moves the carousel.

    async def disconnect(self) -> None:
        await self._link.close()
        self.connected = False

    def describe(self) -> dict:
        d = super().describe()
        d["model"] = self.model
        d["fw_date"] = self.fw_date
        d["slots"] = self.slots
        return d

    async def get_position(self) -> int:
        b = self._link.latest
        if b is None:
            raise DeviceError(f"{self.name}: no status banner yet")
        return b.slot - 1

    async def set_position(self, slot: int) -> None:
        if not (0 <= slot < self.slots):
            raise DeviceError(
                f"{self.name}: slot {slot} out of range 0..{self.slots - 1}")
        target = slot + 1                     # wire is 1-based
        sent_at = time.monotonic()
        await self._link.send(f"200{target}")
        # Completion = a banner NEWER than the command showing the target slot
        # (the stream pauses during the physical move and resumes with it).
        await self._link.wait_banner(
            lambda b: b.at > sent_at and b.slot == target, MOVE_TIMEOUT_S)


# ------------------------------------------------------------------ session

#: Test seam: the link factory.
_make_link = SnowflakeLink


class SnowflakeSession:
    name = "wanderer-snowflake"

    def __init__(self, port_path: str):
        self._port_path = port_path
        self._wheel: SnowflakeWheel | None = None

    async def get_device(self, role: str, conn) -> SnowflakeWheel:
        if role != "filterwheel":
            raise DeviceError(
                f"wanderer-snowflake fills only 'filterwheel' (asked {role!r})")
        if self._wheel is None:
            name = (getattr(conn, "extra", None) or {}).get("name") or "Snowflake FW"
            wheel = SnowflakeWheel(_make_link(self._port_path), name=name)
            wheel.role = role
            await wheel.connect()
            self._wheel = wheel
        return self._wheel

    def native_guider(self):
        return None

    def guide_camera(self):
        return None

    def native_solver(self):
        return None

    async def health(self) -> dict | None:
        if self._wheel is None or not self._wheel.connected:
            return None
        return {"port": self._port_path, "model": self._wheel.model,
                "slots": self._wheel.slots}

    async def close(self) -> None:
        wheel, self._wheel = self._wheel, None
        if wheel is not None:
            await wheel.disconnect()


# ------------------------------------------------------------------ backend

class SnowflakeBackend:
    name = "wanderer-snowflake"
    label = "Wanderer Snowflake FW"
    roles = ("filterwheel",)
    discoverable = True
    hostless = False
    version = "0"                   # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "serial"
    hardware = True
    driver_type = "wanderer-snowflake"

    async def open(self, conn) -> SnowflakeSession:
        port = getattr(conn, "port_path", None)
        if not port:
            raise DeviceError(
                "wanderer-snowflake needs a serial port_path (e.g. COM8)")
        return SnowflakeSession(port)

    async def discover(self) -> list[dict]:
        """CH340 ports (1A86:7523). The chip is generic, so entries are
        ``verified=False`` — identity is proven by the banner at connect."""
        try:
            from serial.tools import list_ports
            ports = await asyncio.to_thread(list_ports.comports)
        except Exception:  # noqa: BLE001
            return []
        return [{"role": "filterwheel", "name": "CH340 serial (Wanderer?)",
                 "port_path": p.device, "verified": False}
                for p in ports
                if getattr(p, "vid", None) == 0x1A86
                and getattr(p, "pid", None) == 0x7523]


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = SnowflakeBackend()
    from ... import __version__
    b.version = __version__
    register(b)
