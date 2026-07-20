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
from ..base import DeviceError, PierSide, Telescope
from ..serial_link import LinkError, SerialLink

#: Seam for tests: the link factory used by ZwoAm5Session.
_make_link = SerialLink

#: Wall-clock cap on a slew settle (spec: no motion path may hang).
SLEW_TIMEOUT_S = 120.0
#: Settle criterion: coord delta below this across two consecutive polls.
SETTLE_DEG = 0.05
#: Poll cadence during a slew.
SETTLE_POLL_S = 0.5

#: |rate deg/s| upper bound -> LX200 rate index command.
_RATE_TABLE = ((0.25, "R1"), (1.0, "R3"), (4.0, "R5"), (16.0, "R7"),
               (float("inf"), "R9"))
#: (axis, positive?) -> move command; stop is Q + same letter.
_MOVE_CMD = {("ra", True): "Me", ("ra", False): "Mw",
             ("dec", True): "Mn", ("dec", False): "Ms"}


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
    can_pulse_guide = False   # implemented below; flipped only after at-scope validation

    def __init__(self, link, name: str = "ZWO AM5"):
        super().__init__(name)
        self._link = link
        self.firmware = ""
        self._last_pos: tuple[float, float] | None = None

    # ------------------------------------------------------------ helpers

    async def _cmd_ack(self, cmd: str, what: str) -> None:
        """Send an ack-class command; map e14 to the honest parked error."""
        try:
            reply = await self._link.request(cmd, reply="ack")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: {what} failed: {exc}") from exc
        if reply == lx200.REFUSED:
            raise DeviceError(
                f"{self.name}: {what} refused — mount is parked; unpark first "
                "(AM5 e14)")
        if reply != lx200.ACK_OK:
            raise DeviceError(f"{self.name}: {what} rejected (reply {reply!r})")

    async def _get(self, cmd: str) -> str:
        try:
            return await self._link.request(cmd, reply="hash")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: {cmd} read failed: {exc}") from exc

    # ---------------------------------------------------------- lifecycle

    async def connect(self) -> None:
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
        # Idempotent, mirroring unpark. :hP# is the standard LX200 park;
        # at-scope verification pending for the unparked->parked transition.
        if await self.is_parked():
            return
        await self._cmd_ack("hP", "park")

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
        """AM5 :GdG# encodes the guide RATE as a sidereal fraction x100 in the
        degrees field (at-scope finding 2026-07-20: '+90*00:00' = 0.90x
        sidereal, NOT 90 degrees). Convert to deg/s; None when the reply
        doesn't fit that encoding."""
        try:
            raw = lx200.parse_dec(await self._get("GdG"))
        except (DeviceError, ValueError):
            return None
        frac = raw / 100.0
        if not (0.0 < frac <= 1.0):
            return None
        v = frac * self._SIDEREAL_DEG_S
        return (v, v)

    # ------------------------------------------------------------- motion

    async def set_tracking(self, on: bool) -> None:
        await self._cmd_ack("Te" if on else "Td",
                            "tracking on" if on else "tracking off")

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
            raise DeviceError(
                f"{self.name}: goto refused — mount is parked; unpark first "
                "(AM5 e14)")
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
            raise DeviceError(
                f"{self.name}: sync refused — mount is parked; unpark first "
                "(AM5 e14)")

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
        d = direction.lower()[0]
        if d not in "nsew":
            raise DeviceError(f"{self.name}: bad guide direction {direction!r}")
        await self._link.request(f"Mg{d}{int(ms):04d}", reply="none")

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
