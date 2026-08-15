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

from ...events import bus
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
#: Grace between the goto going out and the stream's silence being treated as
#: evidence. The command still has to cross the wire, be parsed, and spin a motor
#: up, and the ~2 Hz banner keeps talking through all of that — without this
#: window every healthy move would report "not turning" for its first fraction of
#: a second. Three banner periods: long enough that a working wheel is never
#: called dead, short enough that an IGNORED command is visible in about a second
#: instead of at the 40 s timeout. Do not shrink it towards one banner period:
#: this runs on Windows, where ``time.monotonic()`` is quantized to ~15 ms, and
#: the reader thread's scheduling adds more on top.
MOVE_START_GRACE_S = 1.5
#: How long connect waits for the first banner before declaring "not a Snowflake".
FIRST_BANNER_TIMEOUT_S = 5.0
#: Floor between attempts to reopen a wheel whose reader died. Same
#: reasoning as the AM5's RELINK_MIN_INTERVAL_S: a refused reopen is
#: normal for a moment after the port drops, and must not turn every
#: filter change into an open() attempt on a port somebody still holds.
RELINK_MIN_INTERVAL_S = 5.0

#: How old the newest status banner may be and still answer "where is the
#: wheel". The Snowflake streams continuously and goes quiet only for the
#: duration of a physical move, which ``get_position`` handles separately, so
#: silence past this budget is the reader having stopped rather than the
#: carousel turning. Generous next to the stream's own cadence and far below
#: MOVE_TIMEOUT_S, so a slow move can never be mistaken for a dead link.
POSITION_MAX_AGE_S = 10.0


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
        #: Set when the reader dies on a dead port; cleared by open/close. The
        #: wheel's equivalent of SerialLink.needs_reopen — see _reader.
        self._dropped = False

    @property
    def is_open(self) -> bool:
        """Is there a usable handle AND a reader still pumping it?

        Both halves matter. The handle can outlive the reader: ``_reader``
        returns on any read failure, after which ``_ser`` is still a live object
        and ``send`` still writes into it, but nothing will ever parse a reply
        again — so every ``wait_banner`` times out and the wheel is dead while
        looking connected."""
        return self._ser is not None and not self._dropped

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
        self._dropped = False       # only on success; a failed reopen stays dropped
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        while self._ser is not None:
            try:
                raw = await asyncio.to_thread(self._ser.readline)
            except Exception as exc:  # noqa: BLE001 - the port died under us
                # NOT QUIETLY. This used to be a bare `return`: the reader
                # vanished, `_ser` stayed non-None so the wheel still reported
                # connected, `send` still wrote into a port nobody was reading,
                # and every wait_banner timed out with no clue why. The mount's
                # driver had the identical shape and it cost five and a half
                # hours of dead rig on 2026-08-09 (#207).
                self._dropped = True
                bus.log("error",
                        f"filter wheel {self.port_path}: the serial reader "
                        f"stopped ({exc}) — the link is marked unusable and "
                        f"will be reopened on the next command", "filterwheel")
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
        # A teardown somebody asked for is not a fault to recover from.
        self._dropped = False
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
        #: 1-based slot a goto is currently driving to, or None when idle, plus
        #: the monotonic instant that goto went out. Position alone cannot show a
        #: move: the banner stream PAUSES for the whole physical move (module
        #: docstring), so ``latest`` keeps reporting the OLD slot until the
        #: carousel lands, and a move looks like nothing happening — exactly what
        #: the Capture screen showed. The SILENCE is the signal; ``_move_sent_at``
        #: is what makes it readable (see ``is_moving``).
        self._move_target: int | None = None
        self._move_sent_at: float = 0.0
        #: Monotonic deadline before which no further reopen is attempted.
        self._relink_after = 0.0

    @property
    def connected(self) -> bool:
        """True only when the handshake completed AND the link is still alive.

        DERIVED, NOT REMEMBERED — the same fix as the AM5's (#208), for the same
        reason. A plain flag meaning "connect() once returned" survives the
        reader dying, so the wheel keeps reporting healthy while every command
        times out, and ``connect()``'s ``if self.connected: return`` guard turns
        that stale True into the thing that prevents recovery."""
        # getattr: Device.__init__ assigns through this setter before _link is
        # bound, so the getter has to survive that window.
        link = getattr(self, "_link", None)
        if link is not None and not link.is_open:
            return False
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = bool(value)

    async def connect(self) -> None:
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
        if self._link.is_open:
            # A half-dead link (handle alive, reader gone) must be torn down
            # before reopening, or open() leaks the old handle and the port.
            await self._link.close()
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
        """Where the carousel IS, or a refusal. Never a remembered slot.

        THE SAME DEFECT AS #208 AND #213, arrived at from a third direction: a
        value that was a measurement when it was taken and is a memory by the
        time it is read. ``latest`` is the newest parsed banner and it never
        expires, so a wheel whose reader has stopped keeps answering with
        wherever it was when the stream died, forever, with no error anywhere.

        WHAT THAT COSTS, precisely: ``SequenceEngine._apply_filter`` reads this
        and returns early on ``new_slot == old_slot``. A frozen banner that
        happens to name the slot being asked for therefore CANCELS the move --
        silently, since skipping is the normal fast path -- and every frame
        after it is written with a FILTER header naming a filter that is not in
        the light path. On 2026-08-13 eighteen 180 s subs of NGC 6946 were
        written FILTER='Dark' while the beam was demonstrably open: their
        brightest pixels share 73% with a genuine Ha sub of the same target and
        4% with a real dark. Something told the run the wheel was on slot 7
        when it was not.

        A refusal is the right answer rather than a stale number, and it is
        cheap: the Snowflake streams a banner continuously and only goes quiet
        for the duration of a physical move, so an old banner with no move
        outstanding is positive evidence the link has stopped talking.
        ``_apply_filter`` puts this behind ``_bounded``, so the refusal
        escalates through the normal wind-down instead of quietly shooting the
        rest of the night through the wrong glass.
        """
        b = self._link.latest
        if b is None:
            raise DeviceError(f"{self.name}: no status banner yet")
        if self._move_target is not None:
            # Mid-goto the stream pauses, so `latest` is the slot we are LEAVING.
            # `set_position` waits for the landing banner before it returns, so
            # anyone reaching here during a move is asking a question that does
            # not have an answer yet.
            raise DeviceError(
                f"{self.name}: the carousel is moving to slot "
                f"{self._move_target} - its position is not known until it lands")
        age = time.monotonic() - b.at
        if age > POSITION_MAX_AGE_S:
            raise DeviceError(
                f"{self.name}: the newest status banner is {age:.0f}s old "
                f"(budget {POSITION_MAX_AGE_S:.0f}s) - the wheel has stopped "
                f"reporting, so slot {b.slot} is a memory, not a measurement")
        return b.slot - 1

    async def is_moving(self) -> bool:
        """Is the carousel turning — read off the wire, not off the command.

        "A goto is outstanding" is NOT the same claim, and answering with it was
        wrong: a wheel that ignored the command keeps that True for the whole
        MOVE_TIMEOUT_S, so for forty seconds a dead wheel and a working one are
        pixel-identical on the Capture screen. That is the fixed-duration
        reassurance this flag exists to abolish, just spelled with a timeout.

        The wheel has no busy field, but its SILENCE is one. The banner stream
        pauses for the duration of a physical move and resumes with the new slot
        (module docstring), so a banner that arrives AFTER our goto went out
        still showing the old slot is positive evidence the carousel never
        started: the wheel is sitting there talking. That is a sub-second
        observation instead of a forty-second wait.

        Two deliberate asymmetries:
          * MOVE_START_GRACE_S covers command latency — the wheel is allowed to
            still be talking while it spins up.
          * When we cannot tell (no banner yet, or the stream has gone quiet) we
            report motion, because silence with a goto outstanding is what a real
            move looks like. The cost of that direction is bounded: the UI keeps
            pulsing, and ``set_position`` still times out and raises.
        """
        target = self._move_target
        if target is None:
            return False
        b = self._link.latest
        if b is None:
            return True                     # no banner to reason from
        # `at` is the receive time of a PARSED banner — the same clock and the
        # same predicate set_position waits on, so the two can never disagree
        # about whether the stream has resumed.
        if b.at > self._move_sent_at + MOVE_START_GRACE_S and b.slot != target:
            return False
        return True

    async def _relink(self) -> None:
        """Reopen a wheel whose reader died. Raises DeviceError if it cannot.

        Rate-limited for the same reason the mount's reopen is (see
        RELINK_MIN_INTERVAL_S there): the port may still be held briefly, and a
        refused reopen must not become one open() per command."""
        now = time.monotonic()
        if now < self._relink_after:
            raise DeviceError(
                f"{self.name}: the link is down; the last reopen failed and "
                f"the next attempt is in {self._relink_after - now:.0f}s")
        self._relink_after = now + RELINK_MIN_INTERVAL_S
        # NOTHING to reset before connect(): ``connected`` is derived and the
        # link is not open, so the idempotence guard is already False.
        #
        # This used to clear ``self.connected`` here, which looked harmless and
        # was not: it wrote through to ``_connected``, and set_position's guard
        # was ``self._connected and not is_open`` — so after ONE failed reopen
        # the guard went quiet and every later filter change sailed past the
        # dead link straight into ``send``, writing gotos into a port nobody was
        # reading. The test asserting "no goto reaches a dead port" caught it.
        await self.connect()            # full reopen + banner handshake
        bus.log("warning", f"{self.name}: serial link reopened after the reader "
                           f"stopped — the wheel is answering again",
                "filterwheel")

    async def set_position(self, slot: int) -> None:
        if not (0 <= slot < self.slots):
            raise DeviceError(
                f"{self.name}: slot {slot} out of range 0..{self.slots - 1}")
        target = slot + 1                     # wire is 1-based
        if not self._link.is_open:
            # "We believe we are connected, but the reader is gone." Reopening
            # is the only thing that fixes that, and a filter change is
            # precisely where it matters: without this the wheel sits at
            # whatever slot it was on and every subsequent frame is written with
            # a FILTER header naming a filter that is not in the light path —
            # the same silent-mislabelling failure as #175's one-slot offset,
            # arrived at from a different direction.
            await self._relink()
        sent_at = time.monotonic()
        self._move_target = target
        self._move_sent_at = sent_at
        try:
            await self._link.send(f"200{target}")
            # Completion = a banner NEWER than the command showing the target
            # slot (the stream pauses during the physical move and resumes with
            # it).
            await self._link.wait_banner(
                lambda b: b.at > sent_at and b.slot == target, MOVE_TIMEOUT_S)
        finally:
            # finally, not a trailing assignment: a timed-out or cancelled goto
            # must not leave the wheel reporting motion for the rest of the
            # session — the whole point of the flag is that "turning" and
            # "jammed" look different.
            self._move_target = None


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
