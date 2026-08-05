"""ASIAIR backend — drive a ZWO ASIAIR box over its own network protocol.

AstroDeck FITS AROUND an ASIAIR rig: point this backend at the box's IP and the
ASIAIR keeps owning the hardware while AstroDeck adds its touch UI, the
multi-night session ledger, the Sky Atlas planner, remote access and the phone
dashboard. Nothing here replaces the ASIAIR app, and nothing here reconfigures
the box's equipment: ``close()`` drops OUR sockets and leaves every ASIAIR-owned
device exactly as it found it (the same bridge semantics ``nina_backend`` keeps).

Transport is ``libasi`` (MIT, https://github.com/jewzaam/libasi) — JSON-RPC over
TCP on ports 4700 (main) / 4400 (guider) / 4801 (binary image download),
reverse-engineered from the ASIAIR Android app and validated against live
hardware. libasi is an OPTIONAL dependency (``pip install -e .[asiair]``; it is
not on PyPI, so that extra points at a git checkout). Absent it this module
still imports, ``register_all()`` registers NOTHING, and a user without an
ASIAIR sees no change anywhere: no backend row, no ``asiair`` driver type, no
Equipment offers.

WHAT THIS FILLS -- four roles, every one of them mapped onto a wire-CONFIRMED
command (libasi's ``CONFIRMED_FORMATS.md``):

  * ``camera``    expose / abort / cooler / temperature / anti-dew, frames
                  downloaded as real FITS off port 4801 (linear sensor data).
  * ``telescope`` position, blind slew + settle, sync, tracking (incl. drive
                  rate), park/unpark, timed manual jog, pier side, guide rates.
  * ``focuser``   position, absolute move + settle, halt, temperature.
  * ``switch``    the four DC output ports (dew heaters), read-back verified.

WHAT THIS DELIBERATELY DOES NOT FILL, and why -- each is a thing that could
only be guessed at without an ASIAIR on the bench, and a guessed mapping is
exactly the silent-wrong-data defect this project ranks highest:

  * ``rotator`` (CAA). ``get_caa_info`` reports ``solve_angle.current_angle``,
    which is a PLATE-SOLVE-DERIVED SKY angle carrying its own
    ``update_timestamp`` -- not the mechanical position ``devices.base.Rotator``
    requires. Feeding a sky angle into ``get_mechanical_position`` would
    double-apply AstroDeck's client-side sync offset and silently rotate to the
    wrong PA. Needs a hardware session to confirm a mechanical readout.
  * ``filterwheel``. ``get_wheel_position`` / ``set_wheel_position`` are
    APK-derived, not wire-captured, and the slot base (is wire slot 0 the user's
    filter 1?) is unconfirmed. An off-by-one there images through the wrong
    filter and reports success -- unacceptable to ship on a guess.
  * ``guider``. The ASIAIR's port-4400 guider is its own JSON-RPC dialect, not
    the PHD2 event protocol ``guide.phd2.PHD2Guider`` speaks, and AstroDeck has
    no read path into the ASIAIR's guide star. Guiding stays where it already
    works: in the ASIAIR. AstroDeck REFUSES to move the mount while the box
    reports guiding rather than fighting it (see the contention rules below).
  * ``safety`` / ``dome`` / ``covercalibrator``. The box has no such devices.

THREE DESIGN RULES, because the ASIAIR is one stateful box and not a set of
independent Alpaca endpoints:

1. libasi is SYNCHRONOUS; every AstroDeck device method is ``async``. EVERY
   blocking call goes through ``_Link.call`` -> ``asyncio.to_thread``. The
   server drives a live UI over a WebSocket, so a blocking socket read on the
   event loop would freeze the whole app.

2. ONE client, ONE session, ONE lock. Every role shares the single
   ``ASIAIRClient`` held by ``AsiairSession``. libasi's transport serializes
   only its request-id counter, not the socket write or the response demux, and
   its own ``images.list_dir`` is documented as not thread-safe (the directory
   context lives on the BOX). So ``_Link`` funnels every RPC through one
   ``asyncio.Lock``. The lock is held PER RPC and never across a wait -- a
   five-minute exposure polls with the lock released, so mount and focuser
   reads stay live throughout.

3. The box can be busy, and the ASIAIR app may be the one driving it. Before
   any state-changing command ``_Link.require_idle`` calls libasi's
   ``check_idle``, and a ``BusyError`` becomes a ``DeviceError`` NAMING what
   the box is doing and where to stop it. Never a silent no-op, never a
   success return. Reads are never gated, and ABORT paths (``stop``,
   ``abort_exposure``, ``halt``) are never gated either -- an emergency stop
   that refused because the box was busy would be the worst possible failure.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any

import numpy as np

from ..base import (
    Camera,
    CameraFrame,
    DeviceError,
    Focuser,
    PierSide,
    Switch,
    SwitchPort,
    Telescope,
    TRACKING_RATES,
)

_log = logging.getLogger("astrodeck.asiair")

#: The roles this backend ACTUALLY fills (see the module docstring for the
#: roles it deliberately refuses). Advertised verbatim in ``AsiairBackend.roles``
#: so AstroDeck never offers a device it cannot drive.
ASIAIR_ROLES: tuple[str, ...] = ("camera", "telescope", "focuser", "switch")

#: Main JSON-RPC port. Carried as the driver's config ``port`` purely so the
#: existing host+port driver row has something honest to show; the client always
#: opens 4700/4400/4801 itself from the host.
DEFAULT_PORT = 4700
#: Socket timeout for one RPC. The box answers ``scope_get_info`` in ms; 10s is
#: libasi's own default and leaves room for a busy Wi-Fi link.
DEFAULT_TIMEOUT_S = 10.0

#: Poll cadence while waiting for a slew / park / focus move to settle.
POLL_S = 0.5
#: Wall-clock caps. Each raises a stated-reason DeviceError rather than hanging.
SLEW_TIMEOUT_S = 300.0
PARK_TIMEOUT_S = 180.0
FOCUS_MOVE_TIMEOUT_S = 180.0
#: Grace ADDED to the requested exposure before the download is declared lost.
EXPOSURE_OVERHEAD_S = 180.0

#: Sidereal rate in deg/s — the unit the ASIAIR's slew-rate presets ("1x".."60x")
#: and its guide-rate fraction are expressed in.
SIDEREAL_DEG_S = 0.00417807

#: Duration handed to the ASIAIR's TIMED ``scope_move`` for one manual jog.
#: The ASIAIR has no continuous-rate move (no ASCOM ``MoveAxis`` equivalent), so
#: a jog is a short timed move that the hub's jog keepalive re-issues. The hub's
#: move deadman is 1.2 s (``hub.MOVE_DEADMAN_MS``), so 2 s covers the gap
#: between keepalives while bounding uncommanded travel if comms drop.
JOG_DURATION_S = 2

#: Position-stability epsilon for "the mount has stopped", in degrees of sky.
#: RA/Dec are a FIXED frame, so a tracking mount reads a constant RA/Dec; a
#: non-tracking one drifts ~0.002 deg per POLL_S. 0.01 deg clears that drift
#: without masking real motion.
SETTLE_EPS_DEG = 0.01

#: How close to the COMMANDED coordinates counts as arrived, in degrees of sky.
#: Deliberately loose: this is an arrival check ("did the mount go where it was
#: told?"), not a centring check. Centring is the caller's job (see ``slew``),
#: and a tight value here would turn every ordinary pointing error into a failed
#: slew.
ARRIVE_EPS_DEG = 1.0

#: Consecutive stopped samples before a wait calls the mount settled. TWO while
#: ``move_status`` is being reported, because then two independent signals agree.
#: When the box omits the field entirely there is only ONE signal (position
#: stability), so the run has to be longer — a missing field is not a reading.
STOPPED_SAMPLES = 2
STOPPED_SAMPLES_NO_STATUS = 4

#: How close the focuser must land to count as arrived. The EAF is an
#: exact-step device, so this is a guard against an off-by-one in the box's
#: read-back, not a real tolerance. Same 2 steps as zwo_usb.py and
#: ui/src/lib/focusMove.ts, which drive the same hardware.
ARRIVAL_TOLERANCE_STEPS = 2

_LIBASI_HINT = (
    "libasi is not installed. AstroDeck talks to an ASIAIR through libasi "
    "(MIT, https://github.com/jewzaam/libasi); it is not on PyPI, so install "
    "it with `pip install -e .[asiair]` after cloning it, or "
    "`pip install git+https://github.com/jewzaam/libasi`."
)


# --------------------------------------------------------------- libasi seam

def libasi_available() -> bool:
    """True when ``libasi`` can be imported. Cheap and side-effect free."""
    try:
        import asiair  # noqa: F401
    except Exception:  # noqa: BLE001 — any import problem means "not available"
        return False
    return True


def make_client(host: str, timeout: float = DEFAULT_TIMEOUT_S) -> Any:
    """Build an ``asiair.ASIAIRClient``. THE test seam: tests monkeypatch this
    module attribute with a fake-client factory, exactly as the ZWO drivers
    monkeypatch ``zwo_sdk.make_eaf`` / ``make_caa``.

    The import is DEFERRED to here so importing this module (and running
    ``register_all``) never requires libasi."""
    try:
        from asiair import ASIAIRClient
    except Exception as exc:  # noqa: BLE001
        raise DeviceError(_LIBASI_HINT) from exc
    return ASIAIRClient(host, timeout=timeout)


#: Cached result of the libasi BusyError lookup: unset / the class / False when
#: libasi is absent. Cached so a machine WITHOUT libasi does not pay a failed
#: import on every exception that passes through ``_is_busy``.
_BUSY_ERROR: Any = None


def _real_busy_error() -> type[BaseException] | None:
    """libasi's own ``BusyError`` class when importable, else None."""
    global _BUSY_ERROR
    if _BUSY_ERROR is None:
        try:
            from asiair.transport import BusyError
        except Exception:  # noqa: BLE001
            _BUSY_ERROR = False
        else:
            _BUSY_ERROR = BusyError
    return _BUSY_ERROR or None


def _is_busy(exc: BaseException) -> bool:
    """Is this libasi's ``BusyError`` (the box refusing a conflicting command)?

    ``isinstance`` against the real class FIRST, so the answer is exact wherever
    libasi is installed. Falling back to a structural match (class named
    ``BusyError`` carrying libasi's ``activity``/``requested`` attributes) keeps
    the contention path testable on a machine with no libasi -- which is every
    machine that does not own an ASIAIR, including CI."""
    real = _real_busy_error()
    if real is not None and isinstance(exc, real):
        return True
    return (type(exc).__name__ == "BusyError"
            and hasattr(exc, "activity") and hasattr(exc, "requested"))


# ------------------------------------------------------------------- the link

class _Link:
    """The single shared ASIAIR client, plus the async/contention discipline.

    Owns ONE ``asyncio.Lock``. Every RPC in this module goes through ``call``
    (or ``require_idle``), so no two coroutines are ever inside libasi's
    transport at once -- which matters because libasi guards only its request-id
    counter, and because ``images.list_dir`` sets a directory context ON THE BOX
    that a concurrent listing would clobber.

    The lock is per-RPC. Long waits (exposure, slew settle, focus settle) sleep
    with it RELEASED so a capture never blocks the mount status poll."""

    def __init__(self, client: Any, host: str) -> None:
        self.client = client
        self.host = host
        self._lock = asyncio.Lock()
        self.connected = False
        self.last_ok: float | None = None
        self.last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open both command sockets (heartbeat on, so the box keeps them)."""
        try:
            await asyncio.to_thread(self.client.connect, True)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:200]
            raise DeviceError(
                f"ASIAIR {self.host}: could not connect — {exc}") from exc
        self.connected = True
        self.last_ok = time.time()

    async def close(self) -> None:
        """Drop OUR sockets. Bridge semantics: this never closes the ASIAIR's
        own camera/focuser/mount, and never changes anything on the box."""
        self.connected = False
        try:
            await asyncio.to_thread(self.client.disconnect)
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass

    # -- RPC ---------------------------------------------------------------

    async def call(self, fn, *args, what: str, **kwargs):
        """Run ONE blocking libasi call off the event loop, under the lock.

        Maps libasi's exceptions onto ``DeviceError`` with a message that says
        what failed and (for BusyError) what the box is doing instead."""
        async with self._lock:
            try:
                value = await asyncio.to_thread(fn, *args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one honest DeviceError out
                if _is_busy(exc):
                    raise DeviceError(self._busy_message(exc, what)) from exc
                self.last_error = f"{what}: {exc}"[:200]
                raise DeviceError(
                    f"ASIAIR {self.host}: {what} failed — {exc}") from exc
        self.last_ok = time.time()
        return value

    def _busy_message(self, exc: BaseException, what: str) -> str:
        activity = str(getattr(exc, "activity", "") or "another operation")
        return (f"ASIAIR {self.host} is busy ({activity}) — refusing {what}. "
                "The ASIAIR app or another client is driving the box; stop that "
                "operation there (or wait for it to finish) and retry.")

    async def activity(self) -> dict:
        """What the box is doing right now (``capturing``/``auto_goto``/
        ``guiding``/``auto_focusing``/...). Raises DeviceError on a link
        failure — callers that must not raise wrap it."""
        return await self.call(self.client.get_activity, what="read app state")

    async def require_idle(self, requested: str, *, allow_guiding: bool = True,
                           allow_capturing: bool = False) -> None:
        """Refuse ``requested`` when a conflicting ASIAIR activity is running.

        Delegates to libasi's own ``check_idle`` (the box's real definition of
        a conflict) and converts its ``BusyError`` into a stated-reason
        ``DeviceError``. Defaults mirror libasi's: guiding is compatible with
        most commands, an in-flight capture/autorun is not.

        NEVER call this from an abort path."""
        await self.call(self.client.check_idle, requested,
                        allow_guiding=allow_guiding,
                        allow_capturing=allow_capturing,
                        what=f"idle check for {requested}")


class _AsiairDevice:
    """Shared plumbing for every ASIAIR-backed device: the link + identity."""

    backend = "asiair"
    hardware = True

    def __init__(self, link: _Link) -> None:
        self._link = link
        self.host = link.host
        self.port = DEFAULT_PORT

    @property
    def link(self) -> _Link:
        return self._link


# ------------------------------------------------------------------ telescope

#: (axis, positive rate?) -> ASIAIR ``scope_move`` direction. Matches ASCOM
#: MoveAxis semantics (primary axis positive = increasing RA = east; secondary
#: positive = increasing Dec = north) and the native AM5 driver's ``_MOVE_CMD``.
_JOG_DIR = {("ra", True): "east", ("ra", False): "west",
            ("dec", True): "north", ("dec", False): "south"}

#: ASIAIR ``track_mode_list`` names <-> AstroDeck's ``TRACKING_RATES``.
_TRACK_NAME = {"sidereal": "Sidereal", "lunar": "Lunar", "solar": "Solar"}


def _slew_rate_deg_s(label: str) -> float | None:
    """Parse one ``slew_rate_list`` entry ("1x", "60x") into deg/s.

    ``"MAX"`` / ``"MAX/2"`` return None ON PURPOSE: their actual rate is
    mount-specific and unreported (on an AM5 MAX is degrees per second, orders
    of magnitude above the touch-safety clamp), so selecting one to satisfy a
    0.3 deg/s jog request would move the mount far faster than asked. An
    unparseable label is treated the same way."""
    label = str(label).strip().lower()
    if not label.endswith("x"):
        return None
    try:
        return float(label[:-1]) * SIDEREAL_DEG_S
    except ValueError:
        return None


def pick_slew_rate_index(rate_list: list[str], rate_deg_s: float) -> int:
    """The largest numeric ASIAIR slew preset that does NOT exceed
    ``rate_deg_s`` (falling back to the slowest preset when even that is
    faster than asked). Never returns a MAX/unparseable index — see
    ``_slew_rate_deg_s``. Pure; unit-tested directly."""
    best_i, best_v = 0, -1.0
    for i, label in enumerate(rate_list or []):
        v = _slew_rate_deg_s(label)
        if v is None or v > abs(rate_deg_s):
            continue
        if v > best_v:
            best_i, best_v = i, v
    return best_i


class AsiairTelescope(_AsiairDevice, Telescope):
    """The mount the ASIAIR is driving, as an AstroDeck ``Telescope``.

    Capability flags come from the box's OWN ``caps`` list (``scope_get_info``),
    so a mount that cannot park/sync/goto says so instead of failing mid-slew.
    ``pulse_guide`` is NOT implemented: the ASIAIR exposes no pulse-guide
    command, so ``can_pulse_guide`` stays False and the base class's refusal
    stands (AstroDeck's native guider correctly declines to start rather than
    issuing pulses that go nowhere)."""

    kind = "telescope"

    def __init__(self, link: _Link, name: str = "ASIAIR mount") -> None:
        _AsiairDevice.__init__(self, link)
        Telescope.__init__(self, name)
        self.caps: tuple[str, ...] = ()
        self.model = ""
        self.firmware = ""
        self._slew_rates: list[str] = []

    async def _info(self) -> dict:
        info = await self._link.call(self._link.client.mount.info,
                                     what="read mount info")
        raw = getattr(info, "raw", None)
        return raw if isinstance(raw, dict) else {}

    async def connect(self) -> None:
        raw = await self._info()
        if not raw:
            raise DeviceError(
                f"ASIAIR {self.host}: no mount is connected on the box — "
                "connect it in the ASIAIR app first.")
        self.caps = tuple(str(c) for c in (raw.get("caps") or []))
        self.model = str(raw.get("model") or "")
        self.firmware = str(raw.get("fw_ver") or "")
        self._slew_rates = [str(s) for s in (raw.get("slew_rate_list") or [])]
        if self.model:
            self.name = f"{self.model} (ASIAIR)"
        # Capability truth from the box, never assumed.
        self.can_set_tracking_rate = "track_mode" in self.caps
        self.can_pulse_guide = False        # no ASIAIR pulse-guide command
        self.reports_destination_pier_side = False
        self.connected = True

    async def disconnect(self) -> None:
        # Bridge semantics: AstroDeck's view closes, the ASIAIR keeps its mount.
        self.connected = False

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d["model"] = self.model
        d["firmware"] = self.firmware
        d["caps"] = list(self.caps)
        return d

    def _require_cap(self, cap: str, what: str) -> None:
        if self.caps and cap not in self.caps:
            raise DeviceError(
                f"{self.name}: this mount does not support {what} "
                f"(ASIAIR reports caps {sorted(self.caps)})")

    # -- reads -------------------------------------------------------------

    async def get_position(self) -> tuple[float, float]:
        raw = await self._info()
        return float(raw.get("RA", 0.0)), float(raw.get("Dec", 0.0))

    async def get_tracking(self) -> bool:
        raw = await self._info()
        return bool(raw.get("is_enable_track", False))

    async def is_parked(self) -> bool:
        raw = await self._info()
        return str(raw.get("park_status", "")).lower() == "parked"

    async def is_slewing(self) -> bool:
        # The status readout needs a yes/no, and a box that never reports
        # move_status would otherwise read "slewing" forever. UNKNOWN therefore
        # shows as not-slewing here; the waits are where the missing signal is
        # made up for (see _wait_stopped).
        raw = await self._info()
        return bool(_is_moving(raw))

    async def pier_side(self) -> PierSide:
        raw = await self._info()
        side = str(raw.get("pier_side", "")).lower()
        return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
            side, PierSide.UNKNOWN)

    async def get_tracking_rate(self) -> str:
        raw = await self._info()
        names = raw.get("track_mode_list") or []
        idx = int(raw.get("track_mode_index", 0) or 0)
        if 0 <= idx < len(names):
            name = str(names[idx]).lower()
            if name in TRACKING_RATES:
                return name
        return "sidereal"

    async def guide_rates(self) -> tuple[float, float] | None:
        """The ASIAIR reports ONE guide rate as a fraction of sidereal and
        applies it to both axes, so both entries carry the same deg/s."""
        raw = await self._info()
        g = raw.get("guide_rate")
        if g is None:
            return None
        try:
            rate = float(g) * SIDEREAL_DEG_S
        except (TypeError, ValueError):
            return None
        return rate, rate

    # -- motion ------------------------------------------------------------

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        """Blind slew (``scope_goto``) then WAIT for the mount to stop AT the
        commanded coordinates -- roughly, to ``ARRIVE_EPS_DEG``.

        Deliberately NOT the ASIAIR's plate-solve ``start_auto_goto``: AstroDeck
        owns centring (its own solve/centre loop, its own framing rotation), and
        borrowing the box's goto would run a second, invisible solve loop with
        different tolerances. This is the ``Telescope.slew`` contract — get
        there and settle; centring is the caller's job.

        REFUSED while the ASIAIR is guiding, and that is deliberate: AstroDeck
        does not drive the box's guider, so it cannot pause guiding for a slew
        the way it can with PHD2 or its own engine. Slewing under an active
        guider would have the guider chase the mount. The refusal names guiding
        as the reason, so the fix ("stop guiding in the ASIAIR app") is obvious
        rather than mysterious."""
        self._require_cap("goto", "GoTo")
        await self._link.require_idle("a slew", allow_guiding=False)
        await self._link.call(self._link.client.mount.scope_goto,
                              float(ra_hours), float(dec_deg), force=True,
                              what="slew")
        # The commanded destination goes into the wait: "settled" has to mean
        # settled THERE, or a mount that never moved satisfies it instantly.
        await self._wait_stopped(SLEW_TIMEOUT_S, "slew",
                                 (float(ra_hours), float(dec_deg)))

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        self._require_cap("sync", "sync")
        await self._link.require_idle("a sync", allow_guiding=True)
        await self._link.call(self._link.client.mount.sync,
                              float(ra_hours), float(dec_deg), what="sync")

    async def set_tracking(self, on: bool) -> None:
        self._require_cap("ctrl_track", "tracking control")
        await self._link.call(self._link.client.mount.set_tracking, bool(on),
                              what="set tracking")

    async def set_tracking_rate(self, rate: str) -> None:
        rate = str(rate).lower()
        if rate not in TRACKING_RATES:
            raise DeviceError(
                f"{self.name}: unknown tracking rate {rate!r} "
                f"(expected one of {list(TRACKING_RATES)})")
        if not self.can_set_tracking_rate:
            raise DeviceError(
                f"{self.name}: this mount does not support selecting a drive "
                "rate (ASIAIR reports no 'track_mode' capability)")
        raw = await self._info()
        names = [str(n) for n in (raw.get("track_mode_list") or [])]
        want = _TRACK_NAME[rate]
        try:
            idx = next(i for i, n in enumerate(names) if n.lower() == want.lower())
        except StopIteration:
            raise DeviceError(
                f"{self.name}: the mount offers no {want} rate "
                f"(ASIAIR track modes: {names})") from None
        await self._link.call(self._link.client.mount.set_track_mode, idx,
                              what="set tracking rate")

    async def park(self) -> None:
        self._require_cap("park", "park")
        await self._link.require_idle("a park", allow_guiding=False)
        await self._link.call(self._link.client.mount.park, force=True,
                              what="park")
        deadline = asyncio.get_running_loop().time() + PARK_TIMEOUT_S
        while True:
            await asyncio.sleep(POLL_S)
            raw = await self._info()
            status = str(raw.get("park_status", "")).lower()
            if status == "parked":
                return
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(
                    f"{self.name}: park did not complete within "
                    f"{PARK_TIMEOUT_S:.0f}s (ASIAIR still reports "
                    f"park_status={status!r})")

    async def unpark(self) -> None:
        # libasi's unpark is scope_abort_slew — the ASIAIR's own release path.
        await self._link.call(self._link.client.mount.unpark, what="unpark")

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        """Manual jog as a short TIMED ASIAIR move.

        The ASIAIR has no continuous-rate axis command; ``scope_move`` takes a
        direction plus a duration and the box picks the speed from its slew-rate
        preset. So a jog = pick the fastest preset that does NOT exceed the
        requested rate (``pick_slew_rate_index`` -- never MAX), then issue a
        ``JOG_DURATION_S`` move. The hub's jog keepalive re-issues faster than
        that expires, and a dropped link stops the mount on its own within
        ``JOG_DURATION_S`` instead of running away.

        rate 0 = STOP, and a stop is NEVER gated on the box being idle."""
        if axis not in ("ra", "dec"):
            raise DeviceError(f"{self.name}: unknown axis {axis!r}")
        if rate_deg_s == 0.0:
            await self._link.call(self._link.client.mount.stop, what="stop")
            return
        self._require_cap("move", "manual slewing")
        await self._link.require_idle("a manual slew", allow_guiding=True)
        if self._slew_rates:
            idx = pick_slew_rate_index(self._slew_rates, rate_deg_s)
            await self._link.call(self._link.client.mount.set_slew_rate, idx,
                                  what="set slew rate")
        await self._link.call(self._link.client.mount.move,
                              _JOG_DIR[(axis, rate_deg_s > 0)], JOG_DURATION_S,
                              force=True, what="manual slew")

    async def stop(self) -> None:
        """Emergency stop — ``scope_abort_slew``. NEVER gated on idleness."""
        await self._link.call(self._link.client.mount.stop, what="stop")

    # -- settle ------------------------------------------------------------

    async def _wait_stopped(self, timeout_s: float, what: str,
                            target: tuple[float, float] | None = None) -> None:
        """Wait until the mount has STOPPED AT ``target`` (ra_hours, dec_deg).

        ARRIVAL, not absence-of-motion. A mount that never left the origin is
        stationary from the first poll, so no number of "it is not moving"
        samples can distinguish it from one that finished the slew — that is
        what the old two-sample rule claimed and could not deliver. What closes
        the window is the destination: with a ``target`` this returns only once
        the reported RA/Dec is within ``ARRIVE_EPS_DEG`` of it, so a slew that
        never started keeps polling to the honest timeout below. The tolerance
        is loose on purpose (``ARRIVE_EPS_DEG``); centring is the caller's job.

        Stillness is then confirmed from two signals, because only one of them
        is wire-confirmed: ``move_status`` (confirmed present, reading ``"none"``
        at rest -- its in-motion vocabulary was NOT observed, so anything other
        than none/empty counts as moving, the fail-safe direction) AND position
        stability (RA/Dec is a fixed frame, so a settled mount reads a constant
        RA/Dec whether or not it is tracking). When the box does not report
        ``move_status`` at all only the second signal exists, so a longer stable
        run is required (``STOPPED_SAMPLES_NO_STATUS``) rather than treating the
        missing field as a stopped reading.

        Without a ``target`` (no commanded destination to check against) this
        can only report stillness, and says so here rather than in a promise.

        On timeout or cancellation the mount is STOPPED before the error
        propagates -- never left driving."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        prev: tuple[float, float] | None = None
        stopped = 0
        try:
            while True:
                await asyncio.sleep(POLL_S)
                raw = await self._info()
                pos = (float(raw.get("RA", 0.0)), float(raw.get("Dec", 0.0)))
                reported = _is_moving(raw)          # None = the box did not say
                moving = bool(reported)
                if prev is not None and not moving:
                    moving = _sky_delta_deg(prev, pos) > SETTLE_EPS_DEG
                prev = pos
                stopped = stopped + 1 if not moving else 0
                need = (STOPPED_SAMPLES if reported is not None
                        else STOPPED_SAMPLES_NO_STATUS)
                if stopped >= need and (
                        target is None
                        or _sky_delta_deg(pos, target) <= ARRIVE_EPS_DEG):
                    return
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: {what} did not settle within "
                        f"{timeout_s:.0f}s — stopped the mount. Last read "
                        f"RA {pos[0]:.4f}h Dec {pos[1]:+.4f}deg"
                        + ("" if target is None else
                           f", {_sky_delta_deg(pos, target):.2f}deg from the "
                           f"requested RA {target[0]:.4f}h "
                           f"Dec {target[1]:+.4f}deg"))
        except BaseException:
            try:
                await self._link.call(self._link.client.mount.stop,
                                      what="stop after failed " + what)
            except Exception:  # noqa: BLE001 — halt is best-effort
                pass
            raise


def _is_moving(raw: dict) -> bool | None:
    """Mount-in-motion from ``scope_get_info``. TRI-STATE: True / False / None,
    where None means the box did not report on it at all.

    ``move_status`` is wire-confirmed present and reads ``"none"`` at rest; its
    IN-MOTION vocabulary was never captured, so any other value reads as moving
    — the fail-safe direction. A field missing entirely (older firmware) is
    UNKNOWN, not stopped: defaulting it to ``"none"`` manufactured a stopped
    reading out of a measurement nobody took, and callers that count stopped
    samples were counting that phantom. ``is_parking`` is a separate flag and
    still answers True on its own."""
    if raw.get("is_parking", False):
        return True
    if "move_status" not in raw:
        return None
    status = str(raw.get("move_status") or "none").strip().lower()
    return status not in ("none", "")


def _sky_delta_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle-ish separation between two (ra_hours, dec_deg) samples, in
    degrees. Small-angle approximation is plenty for a stop detector."""
    d_dec = b[1] - a[1]
    d_ra = (b[0] - a[0]) * 15.0 * np.cos(np.radians((a[1] + b[1]) / 2.0))
    return float(np.hypot(d_ra, d_dec))


# -------------------------------------------------------------------- focuser

class AsiairFocuser(_AsiairDevice, Focuser):
    """The ZWO EAF the ASIAIR is driving, as an AstroDeck ``Focuser``.

    ``supports_native_autofocus`` stays FALSE on purpose. The box does have an
    autofocus routine, but its V-curve payload
    (``get_app_state()['auto_focus']['result']``) is elided in the protocol
    capture, so parsing it would be a guess -- and a native-autofocus adapter
    that returned an empty curve would hand the UI a blank graph and call it
    success. AstroDeck instead runs its OWN V-curve sweep through ``move_to``
    plus the camera, which is fully implemented here and produces a real curve."""

    kind = "focuser"
    supports_native_autofocus = False

    def __init__(self, link: _Link, name: str = "ASIAIR focuser") -> None:
        _AsiairDevice.__init__(self, link)
        Focuser.__init__(self, name)
        self.model = ""
        self.firmware = ""

    async def _info(self) -> Any:
        return await self._link.call(self._link.client.focuser.info,
                                     what="read focuser info")

    async def connect(self) -> None:
        try:
            info = await self._info()
        except DeviceError as exc:
            raise DeviceError(
                f"ASIAIR {self.host}: no focuser is open on the box — connect "
                f"the EAF in the ASIAIR app first ({exc})") from exc
        self.max_position = int(getattr(info, "max_step", 0) or 0) or 100_000
        self.model = str(getattr(info, "model", "") or "")
        self.firmware = str(getattr(info, "firmware", "") or "")
        if self.model:
            self.name = f"{self.model} (ASIAIR)"
        self.connected = True

    async def disconnect(self) -> None:
        # Bridge semantics: never close the ASIAIR's own focuser.
        self.connected = False

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d["model"] = self.model
        d["firmware"] = self.firmware
        return d

    async def get_position(self) -> int:
        return int(getattr(await self._info(), "position", 0) or 0)

    async def get_temperature(self) -> float | None:
        try:
            t = getattr(await self._info(), "temperature", None)
            return None if t is None else float(t)
        except DeviceError:
            return None

    async def move_to(self, position: int) -> None:
        """Absolute move, then wait for the drawtube to REACH ``position``.

        Halts on ANY abnormal exit (timeout, cancellation, link failure) so a
        cancelled autofocus step never leaves the motor driving."""
        position = int(position)
        if not (0 <= position <= self.max_position):
            raise DeviceError(
                f"{self.name}: target {position} out of range "
                f"0..{self.max_position}")
        await self._link.require_idle("a focuser move", allow_guiding=True)
        start = await self.get_position()      # so a failure can say where it began
        await self._link.call(self._link.client.focuser.move_to, position,
                              what="move focuser")
        deadline = asyncio.get_running_loop().time() + FOCUS_MOVE_TIMEOUT_S
        last = start
        idle_polls = 0
        try:
            # ARRIVAL, not absence-of-motion — the same rewrite zwo_usb.py's
            # EafFocuser got after 2026-07-31, when the EAF refused every move
            # above position 360 and this shape of loop returned SUCCESS every
            # time: an idle focuser is idle from the first poll, so "idle twice
            # running" is satisfied before the motor has been asked to do
            # anything. A move that did not move must never look like a move
            # that did.
            while True:
                await asyncio.sleep(POLL_S)
                info = await self._info()
                pos = int(getattr(info, "position", 0) or 0)
                # The same idle test is_moving() uses — one source of truth.
                moving = str(getattr(info, "state", "") or "").lower() != "idle"
                if abs(pos - position) <= ARRIVAL_TOLERANCE_STEPS:
                    return                                   # actually arrived
                if pos != last:
                    last, idle_polls = pos, 0                # making progress
                else:
                    # Not at the target and not advancing. TWO polls of that
                    # with the box reporting idle before it counts, so a slow
                    # motor start cannot cry wolf.
                    idle_polls = idle_polls + 1 if not moving else 0
                    if idle_polls >= 2:
                        raise DeviceError(
                            f"{self.name}: move to {position} did not happen — "
                            f"the focuser stopped at {pos} (started from "
                            f"{start}) and is no longer moving. It is probably "
                            "at a mechanical limit or the drawtube is jammed; "
                            "try a smaller move in the other direction.")
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: move to {position} did not settle within "
                        f"{FOCUS_MOVE_TIMEOUT_S:.0f}s — halted, stopped at "
                        f"{pos} (started from {start})")
        except BaseException:
            try:
                await self.halt()
            except Exception:  # noqa: BLE001 — halt is best-effort
                pass
            raise

    async def halt(self) -> None:
        """Stop the focuser. NEVER gated on idleness."""
        await self._link.call(self._link.client.focuser.stop,
                              what="halt focuser")

    async def is_moving(self) -> bool:
        """Same idle test ``move_to`` waits on — one source of truth for
        "is it turning", so the status readout and the move loop can never
        disagree about it."""
        info = await self._info()
        return str(getattr(info, "state", "") or "").lower() != "idle"


# --------------------------------------------------------------------- switch

class AsiairSwitch(_AsiairDevice, Switch):
    """The ASIAIR's four DC output ports (dew heaters, camera power...).

    Writes are READ-BACK VERIFIED: after ``pi_output_set2`` the ports are
    re-read and the target port must actually report the commanded state, or a
    ``DeviceError`` is raised. Without that check a mis-indexed write would
    return success while the heater stayed cold -- the exact silent lie this
    backend must not produce.

    The port TYPE label is never touched (libasi preserves it): changing a
    port's type on an ASIAIR disconnects all attached equipment."""

    kind = "switch"

    def __init__(self, link: _Link, name: str = "ASIAIR power") -> None:
        _AsiairDevice.__init__(self, link)
        Switch.__init__(self, name)
        self._pwm: dict[int, bool] = {}

    async def _read_ports(self) -> list[Any]:
        return await self._link.call(self._link.client.power.get_ports,
                                     what="read power ports")

    async def connect(self) -> None:
        ports = await self._read_ports()
        if not ports:
            raise DeviceError(
                f"ASIAIR {self.host}: the box reported no DC output ports")
        self._pwm = {int(p.index): bool(p.is_pwm) for p in ports}
        self.connected = True

    async def disconnect(self) -> None:
        # Never turns anything off — the user's dew heaters keep running.
        self.connected = False

    async def get_ports(self) -> list[SwitchPort]:
        out: list[SwitchPort] = []
        for p in await self._read_ports():
            idx = int(p.index)
            is_pwm = bool(p.is_pwm)
            self._pwm[idx] = is_pwm
            on = bool(p.on)
            label = str(getattr(p, "port_type", "") or "other")
            if is_pwm:
                value = float(p.value) if on else 0.0
                out.append(SwitchPort(id=idx, name=f"DC {idx + 1} ({label})",
                                      can_write=True, is_boolean=False,
                                      value=value, min=0.0, max=100.0,
                                      unit="%"))
            else:
                out.append(SwitchPort(id=idx, name=f"DC {idx + 1} ({label})",
                                      can_write=True, is_boolean=True,
                                      value=1.0 if on else 0.0,
                                      min=0.0, max=1.0, unit=""))
        return out

    async def set_port(self, port_id: int, value: float) -> None:
        port_id = int(port_id)
        if port_id not in self._pwm:
            raise DeviceError(
                f"{self.name}: no DC output port {port_id} "
                f"(ASIAIR reports ports {sorted(self._pwm)})")
        is_pwm = self._pwm[port_id]
        if is_pwm:
            level = max(0.0, min(100.0, float(value)))
            on = level > 0.0
            send_level = level
        else:
            on = float(value) >= 0.5
            level = 100.0 if on else 0.0
            send_level = 100.0
        await self._link.call(self._link.client.power.set_port, port_id,
                              on=on, value=send_level, what="set power port")
        # READ-BACK: never report success on an unverified write.
        for p in await self._read_ports():
            if int(p.index) != port_id:
                continue
            if bool(p.on) != on:
                raise DeviceError(
                    f"{self.name}: DC {port_id + 1} did not switch "
                    f"{'on' if on else 'off'} — the ASIAIR still reports it "
                    f"{'on' if p.on else 'off'}")
            if is_pwm and on and abs(float(p.value) - level) > 1.0:
                raise DeviceError(
                    f"{self.name}: DC {port_id + 1} did not take level "
                    f"{level:.0f}% — the ASIAIR reports {float(p.value):.0f}%")
            return
        raise DeviceError(
            f"{self.name}: DC {port_id + 1} vanished from the ASIAIR's port "
            "list after the write")


# --------------------------------------------------------------------- camera

#: Where a manually-triggered AstroDeck exposure lands on the ASIAIR. libasi's
#: own validated single-shot example takes a preview exposure, calls
#: ``save_image``, and finds the file under ``Preview/`` — that is the flow this
#: backend follows. Overridable via ``ConnSpec.extra['save_dir']`` for a box
#: whose firmware files previews elsewhere.
DEFAULT_SAVE_DIR = "Preview/"


class AsiairCamera(_AsiairDevice, Camera):
    """The ASIAIR's main imaging camera, as an AstroDeck ``Camera``.

    EXPOSURE FLOW (the one libasi validates against hardware in
    ``examples/capture_and_download.py``):

      1. refuse if the box is mid-capture / goto / autofocus (guiding is fine);
      2. push Exposure/Gain/Offset/bin through ``set_control_value``;
      3. snapshot the save directory's filenames;
      4. ``start_exposure`` as a PREVIEW, then wait out the exposure with the
         RPC lock released, polling until the box reports capture idle;
      5. ``save_image`` to put the frame on the ASIAIR's storage;
      6. diff the directory listing to identify OUR file, and download it as
         real FITS over port 4801.

    Step 6 is where honesty matters: if zero new files appear, or if MORE than
    one does (another client saving into the same folder), this raises a
    stated-reason ``DeviceError`` instead of guessing which frame was ours.

    Consequence worth knowing, documented rather than hidden: saving on the box
    is the ONLY way to get pixel data out of an ASIAIR -- the live-preview
    stream on ports 4800/4500 is not implemented by libasi. So every AstroDeck
    exposure briefly writes a file to the box's ``Preview/`` folder.

    THROWAWAY frames (``save=False``: the live-preview loop, focus frames,
    solve frames) would otherwise fill the user's SD card at one file every few
    seconds, so after a SUCCESSFUL download this backend deletes the one file it
    just created -- never anything else, never a frame it did not write, and
    never a ``save=True`` light. Set ``ConnSpec.extra['cleanup_previews'] =
    False`` to keep every frame on the box instead.

    Frames are genuine linear sensor FITS, so ``data_is_linear`` is True and
    AstroDeck's linear histogram / HFR / clip readouts are all valid -- unlike
    the NINA bridge, which can only serve a decoded render."""

    kind = "camera"

    def __init__(self, link: _Link, name: str = "ASIAIR camera") -> None:
        _AsiairDevice.__init__(self, link)
        Camera.__init__(self, name)
        self._save_dir = DEFAULT_SAVE_DIR
        self._cleanup_previews = True
        self._controls: set[str] = set()
        self._target_c: float | None = None
        self.can_report_cooler_power = False

    def configure(self, extra: dict | None) -> None:
        """Apply ``ConnSpec.extra`` options (``save_dir``, ``cleanup_previews``)."""
        extra = extra or {}
        save_dir = str(extra.get("save_dir") or "").strip()
        if save_dir:
            self._save_dir = save_dir if save_dir.endswith("/") else save_dir + "/"
        if "cleanup_previews" in extra:
            self._cleanup_previews = bool(extra["cleanup_previews"])

    async def connect(self) -> None:
        cam = self._link.client.camera
        try:
            info = await self._link.call(cam.info, what="read camera info")
        except DeviceError as exc:
            raise DeviceError(
                f"ASIAIR {self.host}: no main camera is open on the box — open "
                f"it in the ASIAIR app first ({exc})") from exc
        chip = tuple(getattr(info, "chip_size", ()) or ())
        if len(chip) == 2:
            self.sensor_width, self.sensor_height = int(chip[0]), int(chip[1])
        self.pixel_size_um = float(getattr(info, "pixel_size_um", 0.0) or 0.0)
        bins = [int(b) for b in (getattr(info, "bins", None) or [1])]
        self.max_bin = max(bins) if bins else 1
        self.can_cool = bool(getattr(info, "has_cooler", False))
        # The ASIAIR reports a 2-letter pattern ("RG"); AstroDeck/FITS use the
        # 4-letter form. Only the four known 2x2 layouts are expanded -- an
        # unknown string is dropped rather than guessed into a wrong debayer
        # (which would swap the colour channels of every frame). A mono camera
        # (``is_color`` false) is always None, whatever the field says.
        self.bayer_pattern = (
            _expand_bayer(getattr(info, "debayer_pattern", ""))
            if bool(getattr(info, "is_color", False)) else None)
        name = str(getattr(info, "name", "") or "")
        if name:
            self.name = f"{name} (ASIAIR)"

        controls = await self._link.call(cam.controls, what="read camera controls")
        self._controls = {str(getattr(c, "name", "")) for c in (controls or [])}
        for c in controls or []:
            if str(getattr(c, "name", "")) == "Gain":
                self.max_gain = int(getattr(c, "max_val", 0) or 0) or self.max_gain
        self.has_dew_heater = "AntiDewHeater" in self._controls
        self.can_report_cooler_power = "CoolPowerPerc" in self._controls
        self.connected = True

    async def disconnect(self) -> None:
        # Bridge semantics: never close the ASIAIR's camera or stop its cooler.
        self.connected = False

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d["save_dir"] = self._save_dir
        d["cleanup_previews"] = self._cleanup_previews
        return d

    # -- controls ----------------------------------------------------------

    async def _set_control(self, name: str, value: int, *,
                           required: bool) -> bool:
        """Push one camera control. Returns whether it was actually applied — a
        control this camera does not have is skipped (``required=False``) and
        the caller reports what it really set, never what it asked for."""
        if self._controls and name not in self._controls:
            if required:
                raise DeviceError(
                    f"{self.name}: the ASIAIR camera has no {name!r} control "
                    f"(it offers {sorted(self._controls)})")
            return False
        await self._link.call(self._link.client.camera.set_control, name,
                              int(value), what=f"set {name}")
        return True

    async def get_temperature(self) -> float | None:
        try:
            v = await self._link.call(self._link.client.camera.get_control,
                                      "Temperature", what="read sensor temperature")
        except DeviceError:
            return None
        return float(v) / 10.0

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if not self.can_cool:
            raise DeviceError(f"{self.name} has no cooler")
        await self._set_control("CoolerOn", 1 if on else 0, required=True)
        if target_c is not None:
            await self._set_control("TargetTemp", int(round(target_c)),
                                    required=True)
            self._target_c = float(target_c)

    async def get_cooler(self) -> dict | None:
        """``{on, power, target_c, can_report_power}`` for the Monitor, or None
        when the camera has no cooler."""
        if not self.can_cool:
            return None
        get = self._link.client.camera.get_control

        async def _read(name: str) -> float | None:
            try:
                return float(await self._link.call(get, name, what=f"read {name}"))
            except DeviceError:
                return None

        on = await _read("CoolerOn")
        power = await _read("CoolPowerPerc") if self.can_report_cooler_power else None
        target = await _read("TargetTemp")
        return {"on": bool(on), "power": power,
                "target_c": self._target_c if target is None else target,
                "can_report_power": self.can_report_cooler_power}

    async def set_dew_heater(self, power: int) -> None:
        """The ASIAIR camera's anti-dew heater is ON/OFF only (``AntiDewHeater``
        is a 0/1 control), so any power > 0 turns it fully on. Stated, not
        silently rescaled."""
        if not self.has_dew_heater:
            raise DeviceError(f"{self.name} has no dew heater")
        await self._set_control("AntiDewHeater", 1 if int(power) > 0 else 0,
                                required=True)

    # -- exposure ----------------------------------------------------------

    async def expose(self, seconds: float, gain: int, offset: int,
                     binning: int = 1, light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        cam = self._link.client.camera
        images = self._link.client.images
        frame_type = "light" if light else "dark"
        await self._link.require_idle("an exposure", allow_guiding=True,
                                      allow_capturing=False)

        await self._set_control("Exposure", int(round(seconds * 1_000_000)),
                                required=True)
        await self._set_control("Gain", int(gain), required=True)
        # A camera with no Offset control reports offset 0 rather than echoing a
        # value that was never applied.
        applied_offset = (int(offset)
                          if await self._set_control("Offset", int(offset),
                                                     required=False) else 0)
        if binning:
            await self._link.call(_set_bin, cam, int(binning), what="set binning")

        before = await self._link.call(images.list_filenames, self._save_dir,
                                       what="list save directory")
        started = time.time()
        await self._link.call(cam.start_exposure, frame_type, False, force=True,
                              what="start exposure")
        try:
            try:
                await self._wait_capture_done(seconds)
            except DeviceError:
                # The box wedged mid-frame. Abort the exposure WE started rather
                # than leaving the camera busy, then report the real reason.
                await self.abort_exposure()
                raise
            await self._link.call(images.save_current, what="save frame")
            path = await self._find_new_file(before)
            raw = await self._link.call(images.fetch_fits, path,
                                        what="download FITS")
        except asyncio.CancelledError:
            await self.abort_exposure()
            raise
        data, hdr = await asyncio.to_thread(_decode_fits, raw)
        # Only AFTER a successful decode: a throwaway frame's file on the box is
        # the one we just wrote, and keeping it would fill the user's card at
        # one file per preview tick. Best-effort — a failed delete is logged,
        # never an exposure failure (we already hold the pixels).
        if not save and self._cleanup_previews:
            try:
                await self._link.call(images.delete, path,
                                      what="remove downloaded preview")
                path = None
            except DeviceError as exc:
                _log.warning("asiair: could not remove %s from the box: %s",
                             path, exc)
        return CameraFrame(
            data=data,
            exposure_s=float(seconds), gain=int(gain), offset=applied_offset,
            binning=int(binning or 1),
            bayer_pattern=str(hdr.get("BAYERPAT") or "").strip() or self.bayer_pattern,
            temperature_c=_maybe_float(hdr.get("CCD-TEMP")),
            timestamp=started,
            # The ASIAIR-side path of a frame we LEFT on the box. The hub
            # overwrites this with the local FITS path when it saves one, so a
            # kept light reports where AstroDeck actually wrote it.
            saved_path=path,
            data_is_linear=True,
            egain_e_per_adu=_maybe_float(hdr.get("EGAIN")),
        )

    async def abort_exposure(self) -> None:
        """Stop the exposure and drop the frame. NEVER gated on idleness."""
        try:
            await self._link.call(self._link.client.camera.stop_exposure, False,
                                  what="abort exposure")
        except DeviceError:
            pass

    async def _wait_capture_done(self, seconds: float) -> None:
        """Sleep out the exposure with the RPC lock RELEASED, then poll the
        box's ``capturing`` flag until it clears. Every ``await`` here is
        outside ``_Link``'s lock, so mount/focuser status stays live throughout
        a long sub."""
        await asyncio.sleep(max(0.0, float(seconds)))
        deadline = asyncio.get_running_loop().time() + EXPOSURE_OVERHEAD_S
        while True:
            act = await self._link.activity()
            if not act.get("capturing", False):
                return
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(
                    f"{self.name}: the ASIAIR was still capturing "
                    f"{EXPOSURE_OVERHEAD_S:.0f}s after the {seconds:.1f}s "
                    "exposure should have finished")
            await asyncio.sleep(POLL_S)

    async def _find_new_file(self, before: set[str]) -> str:
        """Identify OUR frame by diffing the save directory, or raise.

        Never guesses. Zero new files and more-than-one new file are both
        errors with a stated reason, because picking one would risk handing the
        caller somebody else's frame and calling it ours."""
        images = self._link.client.images
        new: set[str] = set()
        for _ in range(10):
            after = await self._link.call(images.list_filenames, self._save_dir,
                                          what="list save directory")
            new = set(after) - set(before)
            if new:
                break
            await asyncio.sleep(POLL_S)
        if not new:
            raise DeviceError(
                f"{self.name}: the exposure finished but no new file appeared "
                f"in the ASIAIR's {self._save_dir} folder — the frame was not "
                "saved, so there is nothing to download.")
        if len(new) > 1:
            raise DeviceError(
                f"{self.name}: {len(new)} new files appeared in the ASIAIR's "
                f"{self._save_dir} folder ({sorted(new)}) — another client is "
                "saving there too, so AstroDeck cannot tell which frame is "
                "its own. Refusing to guess.")
        return f"{self._save_dir}{new.pop()}"


def _set_bin(cam: Any, value: int) -> None:
    """``camera.bin`` is a property setter in libasi, so it cannot be handed to
    ``asyncio.to_thread`` directly."""
    cam.bin = int(value)


_BAYER_2 = {"RG": "RGGB", "BG": "BGGR", "GR": "GRBG", "GB": "GBRG"}


def _expand_bayer(pattern: str) -> str | None:
    """ASIAIR 2-letter debayer pattern -> the 4-letter FITS form. Unknown or
    empty (mono) -> None; never a guessed pattern (a wrong one would swap the
    colour channels of every frame)."""
    p = str(pattern or "").strip().upper()
    if len(p) == 4 and set(p) <= {"R", "G", "B"}:
        return p
    return _BAYER_2.get(p)


def _maybe_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _decode_fits(raw: bytes) -> tuple[np.ndarray, dict]:
    """Parse ASIAIR FITS bytes into (uint16 2-D array, header dict). Runs in a
    worker thread (astropy + a multi-megabyte buffer)."""
    from astropy.io import fits

    with fits.open(io.BytesIO(raw), memmap=False) as hdul:
        hdu = next((h for h in hdul if getattr(h, "data", None) is not None), None)
        if hdu is None:
            raise DeviceError("ASIAIR returned a FITS file with no image data")
        data = np.asarray(hdu.data)
        header = {k: hdu.header[k] for k in hdu.header}
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    if data.ndim != 2:
        raise DeviceError(
            f"ASIAIR returned a {data.ndim}-D FITS image (shape {data.shape}); "
            "AstroDeck expects a 2-D sensor frame")
    if data.dtype != np.uint16:
        data = np.clip(data, 0, 65535).astype(np.uint16)
    return data, header


# -------------------------------------------------------------------- session

_ROLE_FACTORIES = {
    "camera": AsiairCamera,
    "telescope": AsiairTelescope,
    "focuser": AsiairFocuser,
    "switch": AsiairSwitch,
}


class AsiairSession:
    """One live connection to ONE ASIAIR box, serving every requested role.

    Built ONCE per ``open()``. Every role device shares the single ``_Link``
    (and therefore the single ``ASIAIRClient`` and its one RPC lock), which is
    what makes concurrent access from different AstroDeck subsystems safe on a
    box that is fundamentally one stateful device.

    Devices are cached per role, so re-resolving a role hands back the same
    object rather than re-probing the box."""

    name = "asiair"

    def __init__(self, link: _Link) -> None:
        self._link = link
        self._devices: dict[str, object] = {}

    @property
    def link(self) -> _Link:
        return self._link

    async def get_device(self, role: str, conn) -> object:
        if role in self._devices:
            return self._devices[role]
        factory = _ROLE_FACTORIES.get(role)
        if factory is None:
            raise DeviceError(
                f"asiair fills only {', '.join(ASIAIR_ROLES)} (asked {role!r}). "
                "See the module docstring for why rotator/filterwheel/guider "
                "are deliberately not offered.")
        extra = (getattr(conn, "extra", None) or {})
        name = extra.get("name")
        dev = factory(self._link, name) if name else factory(self._link)
        configure = getattr(dev, "configure", None)
        if callable(configure):
            configure(extra)
        dev.role = role
        await dev.connect()
        self._devices[role] = dev
        return dev

    def native_guider(self) -> object | None:
        """The ASIAIR's guider is not exposed to AstroDeck (module docstring):
        guiding stays in the box. SYNC by contract."""
        return None

    def guide_camera(self) -> object | None:
        """The ASIAIR's guide camera belongs to its own guider; AstroDeck never
        assigns it. SYNC by contract."""
        return None

    def native_solver(self) -> object | None:
        """The box HAS a plate solver, but AstroDeck downloads real FITS from it
        and solves locally (ASTAP / the native engine) against the frame it
        actually holds. Returning the box's solver would solve a different
        image than the one on screen. SYNC by contract."""
        return None

    async def health(self) -> dict | None:
        """Link health plus WHAT THE BOX IS DOING — the readout that makes a
        refusal understandable ("busy: guiding") instead of mysterious. Never
        raises: a failed probe is a reported ``ok: False``."""
        out: dict = {"backend": "asiair", "host": self._link.host,
                     "ok": bool(self._link.connected),
                     "last_ok": self._link.last_ok,
                     "last_error": self._link.last_error,
                     "devices": sorted(self._devices)}
        try:
            out["activity"] = await self._link.activity()
        except Exception as exc:  # noqa: BLE001 — health must never raise
            out["ok"] = False
            out["activity"] = None
            out["last_error"] = str(exc)[:200]
        return out

    async def close(self) -> None:
        """Release AstroDeck's sockets ONLY.

        Bridge semantics, hard rule: the ASIAIR keeps its camera open, its
        cooler running, its mount tracking and its dew heaters powered. We
        disconnect; the box notices nothing."""
        devices, self._devices = dict(self._devices), {}
        for dev in devices.values():
            try:
                await dev.disconnect()      # flips OUR connected flag only
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
        await self._link.close()


# -------------------------------------------------------------------- backend

class AsiairBackend:
    """The ASIAIR vendor adapter.

    ``discoverable = False``: the ASIAIR announces itself on no protocol libasi
    implements, so there is nothing honest to return from a scan — the user
    types the box's IP (it is on the ASIAIR app's connection screen, or the
    router's DHCP table; on the box's own AP it is 10.0.0.1).

    ``hostless = False``: this is a network endpoint, and two ASIAIRs on one
    network are two separate sessions."""

    name = "asiair"
    label = "ZWO ASIAIR"
    roles = ASIAIR_ROLES
    discoverable = False
    hostless = False
    version = "0"                   # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "network"
    hardware = True
    driver_type = "asiair"

    async def open(self, conn) -> AsiairSession:
        host = (getattr(conn, "host", None) or "").strip()
        if not host:
            raise DeviceError(
                "asiair backend needs the ASIAIR's IP address — add it as a "
                "driver in Settings (its address is on the ASIAIR app's "
                "connection screen).")
        timeout = float((getattr(conn, "extra", None) or {}).get(
            "timeout_s", DEFAULT_TIMEOUT_S))
        link = _Link(make_client(host, timeout), host)
        await link.connect()
        return AsiairSession(link)

    async def discover(self) -> list[dict]:
        """No honest discovery exists for the ASIAIR — see the class docstring."""
        return []


async def probe_asiair(host: str, port: int = DEFAULT_PORT) -> dict:
    """Driver-availability probe for ``drivers.describe_all``.

    Connects, reads the box's identity, and then offers ONLY the roles whose
    device is actually present on the box right now — so the Equipment UI never
    lists a camera row for an ASIAIR with no camera open. Always disconnects.
    NEVER raises (a probe must not 500 the drivers API)."""
    offers: list[dict] = []
    detail: str | None = None
    link: _Link | None = None
    try:
        link = _Link(make_client(host, 5.0), host)
        await link.connect()
        try:
            info = await link.call(link.client.system.info, what="read system info")
            detail = str(getattr(info, "model", "") or "ASIAIR") or None
        except DeviceError:
            detail = None
        for role, probe, label in (
            ("camera", lambda: link.client.camera.info(), "camera"),
            ("telescope", lambda: link.client.mount.info(), "mount"),
            ("focuser", lambda: link.client.focuser.info(), "focuser"),
            ("switch", lambda: link.client.power.get_ports(), "power ports"),
        ):
            try:
                got = await link.call(probe, what=f"probe {label}")
            except DeviceError:
                continue
            if got:
                offers.append({"role": role,
                               "name": _offer_name(role, got, detail)})
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return {"reachable": False, "error": str(exc)[:200] or "connect failed",
                "detail": None, "offers": {"devices": [], "tasks": []}}
    finally:
        if link is not None:
            try:
                await link.close()
            except Exception:  # noqa: BLE001
                pass
    return {"reachable": True, "error": None, "detail": detail,
            "offers": {"devices": offers, "tasks": []}}


def _offer_name(role: str, got: Any, model: str | None) -> str:
    """A human name for one probe offer, from whatever the box reported."""
    for attr in ("name", "model"):
        v = str(getattr(got, attr, "") or "").strip()
        if v:
            return f"{v} (ASIAIR)"
    return f"{model or 'ASIAIR'} {role}"


def register_all() -> None:
    """Entry-point target: ``[project.entry-points."astrodeck.backends"]``.

    Registers NOTHING when libasi is absent, so a user with no ASIAIR sees no
    ``asiair`` backend, no ``asiair`` driver type (``configurable_driver_types``
    is registry-derived, so adding one 422s) and no change anywhere in the UI.
    This mirrors ``ascom_local``'s Windows-only registration."""
    if not libasi_available():
        _log.debug("asiair backend not registered: %s", _LIBASI_HINT)
        return
    from ..backend import register
    b = AsiairBackend()
    from ... import __version__
    b.version = __version__
    register(b)
