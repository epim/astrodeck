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
        ra = lx200.parse_ra(await self._get("GR"))
        dec = lx200.parse_dec(await self._get("GD"))
        self._last_pos = (ra, dec)
        return ra, dec

    async def is_parked(self) -> bool:
        return (await self._get("Gps")).startswith("2")

    async def unpark(self) -> None:
        await self._cmd_ack("Spu", "unpark")

    async def park(self) -> None:
        # NOTE: :hP# is the standard LX200 park; unverified on the AM5N while
        # unparked (the capture only saw it refused while already parked).
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

    async def guide_rates(self) -> tuple[float, float] | None:
        try:
            v = lx200.parse_dec(await self._get("GdG"))
        except (DeviceError, ValueError):
            return None
        return (v, v)

    # ------------------------------------------------------------- motion
    # Implemented in B-Task 4; present as honest stubs so the ABC is complete.

    async def set_tracking(self, on: bool) -> None:
        await self._cmd_ack("Te" if on else "Td",
                            "tracking on" if on else "tracking off")

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        raise DeviceError(f"{self.name}: slew not implemented yet (B-Task 4)")

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        raise DeviceError(f"{self.name}: sync not implemented yet (B-Task 4)")

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        raise DeviceError(f"{self.name}: move_axis not implemented yet (B-Task 4)")

    async def is_slewing(self) -> bool:
        return False

    async def stop(self) -> None:
        await self._link.request("Q", reply="none")
