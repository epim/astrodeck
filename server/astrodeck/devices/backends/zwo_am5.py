"""ZWO AM5/AM5N native serial mount driver (sub-project B).

Speaks Meade LX200 ASCII over the mount's USB CDC serial port — no ZWO software,
no ASCOM. Wire truth: docs/hardware/zwo-am5-lx200-protocol.md. The mount powers
up PARKED and refuses all motion with ``e14#`` until the ZWO-specific ``:Spu#``
unpark; connect() deliberately does NOT auto-unpark (honest parked UX).

Registered through the driver framework's entry-point path
(``[project.entry-points."astrodeck.backends"] zwo_am5 = ...:register_all``) —
the first real citizen of sub-project A's discovery mechanism.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .. import lx200
from ..base import DeviceError, PierSide, Telescope, TRACKING_RATES
from ..serial_link import LinkError, SerialLink

#: Seam for tests: the link factory used by ZwoAm5Session.
_make_link = SerialLink

#: Wall-clock cap on a slew settle (spec: no motion path may hang).
SLEW_TIMEOUT_S = 120.0
#: Settle criterion: coord delta below this across two consecutive polls.
SETTLE_DEG = 0.05
#: Poll cadence during a slew.
SETTLE_POLL_S = 0.5
#: Poll cadence while waiting for a park to complete.
PARK_POLL_S = 1.0

#: |rate deg/s| upper bound -> LX200 rate index command.
#: CALIBRATED ON HARDWARE 2026-07-20 (dec-axis nudges): the AM5 R-indices are
#: sidereal-multiple presets, roughly R1=0.3x, R3=1.9x, R5=7.8x, R7=60x,
#: R8~344x (1.44 deg/s; R8/R9 measurements were acceleration-ramp-limited).
#: Bounds sit between adjacent measured rates so a request maps to the nearest
#: preset at or above it.
_RATE_TABLE = ((0.004, "R1"), (0.02, "R3"), (0.1, "R5"), (0.7, "R7"),
               (float("inf"), "R8"))
#: (axis, positive?) -> move command; stop is Q + same letter.
_MOVE_CMD = {("ra", True): "Me", ("ra", False): "Mw",
             ("dec", True): "Mn", ("dec", False): "Ms"}

#: rate name (TRACKING_RATES) -> classic LX200 drive-rate select command.
_TRACKING_RATE_CMD = {"sidereal": "TQ", "lunar": "TL", "solar": "TS"}

#: Pulse-guide emulation (fw 1.8.8, all verified at scope 2026-07-20):
#: - The LX200 :Mg*# pulse commands PARSE but are INERT over serial.
#: - :M<dir># during tracking REPLACES the tracking drive (does not
#:   superimpose): Me at R1(~0.5x) reads +1.5x sid on GR; Mw reads +0.5x —
#:   both eastward! So each direction gets its own strategy:
#:   east  = suspend tracking (:Td# ... :Te#): drifts east at EXACTLY 1x sid;
#:   west  = R2 + Mw: measured EXACTLY -1x sid during tracking;
#:   north/south = R1 + Mn/Ms: +/-0.5x sid (dec has no tracking to fight).
#: Sky sign of N/S depends on pier side — guider calibration owns that, as
#: with every ASCOM mount.
_PULSE_DEC_RATE_CMD = "R1"
_PULSE_WEST_RATE_CMD = "R2"
#: measured pulse rates, deg/s (10s GR/GD deltas, 2026-07-20): ra = 1.0x
#: sidereal BOTH directions (+150/-150 arcsec per 10s), dec = 0.5x.
_PULSE_RA_RATE_DEG_S = 0.004178
_PULSE_DEC_RATE_DEG_S = 0.002089
_PULSE_MOVE = {"n": "Mn", "s": "Ms", "e": "Me", "w": "Mw"}
_PULSE_STOP = {"n": "Qn", "s": "Qs", "e": "Qe", "w": "Qw"}


def _utcnow() -> datetime:
    """Injectable clock (tests monkeypatch this)."""
    return datetime.now(timezone.utc)


def _site_latlon() -> tuple[float, float]:
    """Site (lat, lon_east) from config; injectable for tests."""
    from ...config import config_store
    site = config_store.cfg().site
    return float(site.latitude), float(site.longitude)


class ZwoAm5Telescope(Telescope):
    """The AM5 as an AstroDeck Telescope: LX200 over one SerialLink."""

    backend = "zwo-am5"
    hardware = True
    can_pulse_guide = True    # EMULATED: timed R1 moves (native :Mg*# is inert)
    can_set_tracking_rate = True

    def __init__(self, link, name: str = "ZWO AM5"):
        super().__init__(name)
        self._link = link
        self.firmware = ""
        self._last_pos: tuple[float, float] | None = None
        #: cache of the last-set drive rate -- the AM5's rate read-back is
        #: unreliable (no verified LX200 query for it), so get_tracking_rate
        #: returns this rather than round-tripping the mount.
        self._tracking_rate = "sidereal"

    # ------------------------------------------------------------ helpers

    async def _refused_error(self, what: str) -> DeviceError:
        """Compose an HONEST error for the mount's ``e14#`` refusal.

        ``e14`` means "refused in current state" — parked is only ONE cause
        (below-horizon limit, a motion already running, an un-set target all
        produce it too), so we probe the actual park flag before blaming park.
        The probe runs only on this already-exceptional path and is best-effort:
        a failed ``:Gps#`` must never mask the refusal we came here to report."""
        parked = False
        try:
            parked = await self.is_parked()
        except Exception:  # noqa: BLE001 - probe is advisory only
            pass
        if parked:
            return DeviceError(
                f"{self.name}: {what} refused — mount is parked; unpark first "
                "(AM5 e14)")
        return DeviceError(
            f"{self.name}: {what} refused in current state — check limits / "
            "that a slew isn't already running (AM5 e14)")

    async def _cmd_ack(self, cmd: str, what: str) -> None:
        """Send an ack-class command; map e14 to the honest refusal error."""
        try:
            reply = await self._link.request(cmd, reply="ack")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: {what} failed: {exc}") from exc
        if reply == lx200.REFUSED:
            raise await self._refused_error(what)
        if reply != lx200.ACK_OK:
            raise DeviceError(f"{self.name}: {what} rejected (reply {reply!r})")

    async def _get(self, cmd: str) -> str:
        try:
            return await self._link.request(cmd, reply="hash")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: {cmd} read failed: {exc}") from exc

    # ---------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
        await self._link.open()
        try:
            ident = await self._get("GVP")
            if "AM5" not in ident:
                raise DeviceError(
                    f"{self.name}: device on port is not an AM5 (GVP={ident!r})")
            self.firmware = await self._get("GV")
            for cmd in lx200.utc_init_cmds(_utcnow()):
                await self._cmd_ack(cmd, f"clock init {cmd}")
            lat, lon = _site_latlon()
            await self._cmd_ack(lx200.smge(lat, lon), "site init")
            await self._get("Gps")   # prime state (parked flag)
            await self._get("GU")
        except Exception:
            await self._link.close()
            self.connected = False
            raise
        self.connected = True

    async def disconnect(self) -> None:
        try:
            await self._link.request("Q", reply="none")   # never leave motion running
        except Exception:  # noqa: BLE001 - best-effort on teardown
            pass
        await self._link.close()
        self.connected = False

    def describe(self) -> dict:
        d = super().describe()
        d["firmware"] = self.firmware
        return d

    # -------------------------------------------------------------- state

    async def get_position(self) -> tuple[float, float]:
        raw_ra = await self._get("GR")
        raw_dec = await self._get("GD")
        try:
            ra = lx200.parse_ra(raw_ra)
            dec = lx200.parse_dec(raw_dec)
        except ValueError as exc:   # mount garbage -> the driver's error type
            raise DeviceError(
                f"{self.name}: unparseable position reply "
                f"(RA={raw_ra!r} Dec={raw_dec!r})") from exc
        self._last_pos = (ra, dec)
        return ra, dec

    async def is_parked(self) -> bool:
        return (await self._get("Gps")).startswith("2")

    async def unpark(self) -> None:
        # Idempotent (at-scope finding 2026-07-20): :Spu# on an ALREADY-unparked
        # mount replies '0', which is not a failure — there is simply no park to
        # cancel. Check state first; only a parked mount gets the command.
        if not await self.is_parked():
            return
        await self._cmd_ack("Spu", "unpark")

    async def park(self) -> None:
        # Idempotent, mirroring unpark. VERIFIED ON HARDWARE 2026-07-20: :hP#
        # is in the AM5's fire-and-forget motion class (NO ack; an ack-read
        # times out) and the mount reports parked (:Gps# '2') ~1s later.
        # Send-and-poll, with a wall-clock bound.
        if await self.is_parked():
            return
        # STOP TRACKING FIRST. Verified on hardware 2026-07-30: with tracking
        # ON, :hP# is accepted and silently does nothing — the mount never
        # moves and never reports parked, so every park times out at 60s.
        # Tracking off first, and the identical park lands in ~15s.
        #
        # This is the single most important ordering in the driver. A night
        # ALWAYS ends with the mount tracking, so "park at dawn" — the thing
        # standing between the sun and the optics — hit exactly this case and
        # failed. It was found by test-firing a dawn failsafe rather than
        # trusting that it would work.
        try:
            if await self.get_tracking():
                await self.set_tracking(False)
                # The mount needs a moment to actually stop before it will
                # honour a park; polling Gps immediately reads the old state.
                await asyncio.sleep(1.0)
        except Exception:  # noqa: BLE001
            # A mount that cannot report or stop tracking still gets the park
            # attempt — refusing to try would be worse than trying and timing
            # out, and this path is the last thing protecting the optics.
            pass
        await self._link.request("hP", reply="none")
        deadline = asyncio.get_running_loop().time() + 60.0
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(
                    f"{self.name}: park did not complete within 60s "
                    "(mount still reports unparked)")
            await asyncio.sleep(PARK_POLL_S)
            if await self.is_parked():
                return

    async def get_tracking(self) -> bool:
        return (await self._get("GAT")).startswith("1")

    async def pier_side(self) -> PierSide:
        side = await self._get("Gm")
        if side.startswith("E"):
            return PierSide.EAST
        if side.startswith("W"):
            return PierSide.WEST
        return PierSide.UNKNOWN

    #: sidereal rate in deg/s (15.041"/s) — the unit :GdG# is a fraction of.
    _SIDEREAL_DEG_S = 0.004178074

    async def guide_rates(self) -> tuple[float, float] | None:
        """The rates our emulated pulses ACTUALLY deliver (hardware-measured):
        ra ~1x sidereal (tracking-suspend east / R3-west), dec ~0.5x (R1).
        (The mount's :GdG# setting governs only the inert :Mg*# path.)"""
        return (_PULSE_RA_RATE_DEG_S, _PULSE_DEC_RATE_DEG_S)

    # ------------------------------------------------------------- motion

    async def set_tracking(self, on: bool) -> None:
        await self._cmd_ack("Te" if on else "Td",
                            "tracking on" if on else "tracking off")

    async def set_tracking_rate(self, rate: str) -> None:
        """Select the drive rate via the classic LX200 ``:TQ#``/``:TL#``/
        ``:TS#`` commands. Unlike ``:Te#``/``:Td#`` (verified ACK ``1`` at
        scope 2026-07-20, see docs/hardware/zwo-am5-lx200-protocol.md), these
        rate-select commands are NOT in the captured wire truth -- classic
        LX200 firmwares reply to them with nothing at all, and the AM5's other
        rate-index commands (``:R0#``..``:R9#``) are confirmed fire-and-forget.
        So this sends fire-and-forget (``reply="none"``) rather than assuming
        an ack; whether the AM5N actually ACKs ``:TL#``/``:TS#`` is an
        at-scope validation item (plan Task 7)."""
        if rate not in TRACKING_RATES:
            raise DeviceError(f"{self.name}: unknown tracking rate {rate!r}")
        await self._link.request(_TRACKING_RATE_CMD[rate], reply="none")
        self._tracking_rate = rate

    async def get_tracking_rate(self) -> str:
        return self._tracking_rate

    async def _set_target(self, ra_hours: float, dec_deg: float) -> None:
        await self._cmd_ack(f"Sr{lx200.format_ra(ra_hours)}", "set target RA")
        await self._cmd_ack(f"Sd{lx200.format_dec(dec_deg)}", "set target Dec")

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        """Goto and wait until settled. AM5 moves are fire-and-forget, so the
        ONLY completion signal is polling: settled when the coordinate delta
        stays under SETTLE_DEG across two consecutive polls. Cancel-safe: a
        CancelledError (or timeout) halts the mount with :Q# first."""
        await self._set_target(ra_hours, dec_deg)
        reply = await self._link.request("MS", reply="ack")
        if reply == lx200.REFUSED:
            raise await self._refused_error("goto")
        # LX200 :MS# convention: '0' = slew accepted; anything else = refused.
        if reply != "0":
            raise DeviceError(f"{self.name}: goto rejected (reply {reply!r})")
        self._slewing = True
        try:
            deadline = asyncio.get_running_loop().time() + SLEW_TIMEOUT_S
            prev: tuple[float, float] | None = None
            stable = 0
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: slew failed to settle within "
                        f"{SLEW_TIMEOUT_S:.0f}s — halted (:Q#)")
                await asyncio.sleep(SETTLE_POLL_S)
                ra, dec = await self.get_position()
                if prev is not None:
                    d_deg = max(abs(ra - prev[0]) * 15.0, abs(dec - prev[1]))
                    stable = stable + 1 if d_deg < SETTLE_DEG else 0
                    if stable >= 2:
                        return
                prev = (ra, dec)
        except BaseException:
            # ANY abnormal settle exit — cancel, timeout, link/parse failure —
            # halts the mount before propagating (B review M3). :Q# is
            # write-only (cannot hang) and harmless if the goto already ended.
            try:
                await self._link.request("Q", reply="none")
            except Exception:  # noqa: BLE001 - halt is best-effort on teardown
                pass
            raise
        finally:
            self._slewing = False

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        await self._set_target(ra_hours, dec_deg)
        try:
            reply = await self._link.request("CM", reply="hash")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: sync failed: {exc}") from exc
        if reply == lx200.REFUSED:
            raise await self._refused_error("sync")

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        if axis not in ("ra", "dec"):
            raise DeviceError(f"{self.name}: unknown axis {axis!r}")
        if rate_deg_s == 0.0:
            # stop both directions of this axis (fire-and-forget)
            for d in ("e", "w") if axis == "ra" else ("n", "s"):
                await self._link.request(f"Q{d}", reply="none")
            return
        for bound, rate_cmd in _RATE_TABLE:
            if abs(rate_deg_s) <= bound:
                break
        await self._link.request(rate_cmd, reply="none")
        await self._link.request(_MOVE_CMD[(axis, rate_deg_s > 0)], reply="none")

    async def pulse_guide(self, direction: str, ms: int) -> None:
        """EMULATED pulse guide (native :Mg*# is inert; :M<dir># during
        tracking REPLACES the drive — see the module notes). Strategies:
        east = tracking-suspend (exact 1x sidereal drift); west = R3+Mw
        (~0.9x net west); n/s = R1 moves. Every path restores state in a
        ``finally`` so cancellation can't leave the mount drifting."""
        d = direction.lower()[0]
        if d not in "nsew":
            raise DeviceError(f"{self.name}: bad guide direction {direction!r}")
        secs = int(ms) / 1000.0
        if d == "e" and await self.get_tracking():
            await self._cmd_ack("Td", "pulse east (suspend tracking)")
            try:
                await asyncio.sleep(secs)
            finally:
                await self._cmd_ack("Te", "pulse east (resume tracking)")
            return
        rate_cmd = _PULSE_WEST_RATE_CMD if d == "w" else _PULSE_DEC_RATE_CMD
        await self._link.request(rate_cmd, reply="none")
        await self._link.request(_PULSE_MOVE[d], reply="none")
        try:
            await asyncio.sleep(secs)
        finally:
            await self._link.request(_PULSE_STOP[d], reply="none")

    async def is_slewing(self) -> bool:
        return bool(getattr(self, "_slewing", False))

    async def stop(self) -> None:
        """Emergency halt: :Q# goes out FIRST, no preamble. Whether :Q# also
        disturbs tracking on this firmware is an at-scope runbook item."""
        await self._link.request("Q", reply="none")


# ------------------------------------------------------------------ session

class ZwoAm5Session:
    """One live serial connection to one AM5. Fills only the telescope role."""

    name = "zwo-am5"

    def __init__(self, port_path: str):
        self._port_path = port_path
        self._tel: ZwoAm5Telescope | None = None

    async def get_device(self, role: str, conn) -> ZwoAm5Telescope:
        if role != "telescope":
            raise DeviceError(f"zwo-am5 backend fills only 'telescope' (asked {role!r})")
        if self._tel is None:
            name = (getattr(conn, "extra", None) or {}).get("name") or "ZWO AM5"
            tel = ZwoAm5Telescope(_make_link(self._port_path), name=name)
            tel.role = role
            await tel.connect()
            self._tel = tel
        return self._tel

    def native_guider(self):
        return None

    def guide_camera(self):
        return None

    def native_solver(self):
        return None

    async def health(self) -> dict | None:
        if self._tel is None or not self._tel.connected:
            return None
        return {"port": self._port_path, "firmware": self._tel.firmware}

    async def close(self) -> None:
        tel, self._tel = self._tel, None
        if tel is not None:
            await tel.disconnect()      # sends :Q# then closes the link


# ------------------------------------------------------------------ backend

def _app_version() -> str:
    from ... import __version__
    return __version__


class ZwoAm5Backend:
    """The AM5 native serial backend — first real citizen of the entry-point
    driver framework (spec 2026-07-20, sub-project B)."""

    name = "zwo-am5"
    label = "ZWO AM5 (native serial)"
    roles = ("telescope",)
    discoverable = True
    hostless = False
    version = "0"                # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "serial"
    hardware = True
    driver_type = "zwo-am5"

    async def open(self, conn) -> ZwoAm5Session:
        port = getattr(conn, "port_path", None)
        if not port:
            raise DeviceError(
                "zwo-am5 backend needs a serial port_path (e.g. COM3)")
        return ZwoAm5Session(port)

    async def discover(self) -> list[dict]:
        """Enumerate serial ports; AM5s match VID:PID 03C3:4001."""
        try:
            from serial.tools import list_ports
            ports = await asyncio.to_thread(list_ports.comports)
        except Exception:  # noqa: BLE001 - discovery must never raise
            return []
        found = []
        for p in ports:
            if getattr(p, "vid", None) == 0x03C3 and getattr(p, "pid", None) == 0x4001:
                found.append({"role": "telescope", "name": "ZWO AM5 (USB)",
                              "port_path": p.device, "verified": True})
        return found


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = ZwoAm5Backend()
    b.version = _app_version()
    register(b)
