"""FilterWheel.is_moving() — the wheel's REAL in-motion state.

Why this exists: on 2026-07-31 picking L on the Capture screen looked like
nothing happened. The pick reaches the server fine; the problem is that the only
thing on screen which could have changed — the slot name — cannot change until
the carousel lands, because every backend's ``get_position`` reports the OLD
slot for the whole move (the Snowflake's status stream goes SILENT while it
turns; ASCOM returns the -1 sentinel, which we clamp to 0).

So the UI needs a separate signal, and it has to be the wheel's own. A
fixed-duration animation would have hidden precisely the failure this run is
about: a jammed wheel must not look like a working one.

The discipline mirrors ``Focuser.is_moving`` (added the same day for the same
class of defect): default False, never a hopeful True, and published by the hub
in its own try so a backend that cannot answer never costs the readouts the user
is looking at.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as configmod
import astrodeck.devices.backends.wanderer_snowflake as ws
from astrodeck.devices.base import DeviceError, FilterWheel
from astrodeck.devices.sim import SimFilterWheel, SimRig
from astrodeck.hub import Hub

from test_snowflake import LIVE_LINE, FakeStreamLink   # noqa: I001 (rootdir-relative)


@pytest.fixture
def filter_store(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    return tmp_path


# ------------------------------------------------------------------ contract

async def test_a_wheel_that_cannot_tell_says_not_moving():
    """The base default is False, and it must stay False.

    A hopeful True would make the Capture screen pulse forever on any backend
    without a motion flag — which is worse than no pulse at all, because it
    turns "your command was ignored" into "your command is under way"."""

    class MuteWheel(FilterWheel):
        async def connect(self) -> None: self.connected = True
        async def disconnect(self) -> None: self.connected = False
        async def get_position(self) -> int: return 0
        async def set_position(self, slot: int) -> None: pass

    assert await MuteWheel("mute").is_moving() is False


# ----------------------------------------------------------------------- sim

async def test_the_sim_wheel_is_moving_for_the_whole_turn():
    """And the slot only flips at the END, exactly like real hardware — if the
    sim updated the position instantly there would be nothing to pulse against
    and this behaviour could not be exercised off-telescope."""
    fw = SimFilterWheel(SimRig())
    await fw.connect()
    assert not await fw.is_moving()

    task = asyncio.create_task(fw.set_position(4))
    await asyncio.sleep(0.05)
    assert await fw.is_moving(), "must report motion while the carousel turns"
    assert await fw.get_position() == 0, "the slot must not land early"

    await task
    assert not await fw.is_moving()
    assert await fw.get_position() == 4


async def test_a_cancelled_filter_change_does_not_leave_the_sim_claiming_motion():
    """The flag is cleared in a `finally`. A cancelled change that left it set
    would pulse the Capture screen for the rest of the session."""
    fw = SimFilterWheel(SimRig())
    await fw.connect()
    task = asyncio.create_task(fw.set_position(6))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await fw.is_moving()


# -------------------------------------------------------------------- alpaca

async def test_the_ascom_minus_one_sentinel_survives_the_position_clamp():
    """ASCOM says "between slots" by returning ``Position == -1``.

    ``get_position`` clamps that to 0 so no caller ever sees a negative slot —
    and that clamp is exactly what hid the turn: mid-move the status poll read 0
    and the Capture screen calmly named slot 1's filter. ``is_moving`` reads the
    RAW value so the sentinel is not thrown away twice."""
    from astrodeck.devices.alpaca import AlpacaConnection, AlpacaFilterWheel

    fw = AlpacaFilterWheel(AlpacaConnection("localhost", 11111), 0, "ASCOM FW")
    raw = {"value": -1}

    async def fake_get(method, **params):
        assert method == "position"
        return raw["value"]

    fw._get = fake_get
    assert await fw.is_moving() is True
    assert await fw.get_position() == 0, "the clamp itself must not change"

    raw["value"] = 3
    assert await fw.is_moving() is False
    assert await fw.get_position() == 3


# ---------------------------------------------------------------------- nina

async def test_a_nina_bridge_without_an_ismoving_field_reports_not_moving():
    """NINA's filterwheel info carries ``IsMoving`` (same field NinaFocuser
    already reads). A bridge build that omits it genuinely cannot tell us, so it
    must answer False and let the position be the witness — not raise, and not
    invent motion."""
    from astrodeck.devices.nina import NinaFilterWheel

    class _Client:
        host, port = "localhost", 1888

        def __init__(self, payload): self.payload = payload
        async def get(self, path, **kw): return self.payload

    turning = NinaFilterWheel(_Client({"IsMoving": True}), "NINA FW")
    assert await turning.is_moving() is True

    silent = NinaFilterWheel(_Client({"Connected": True}), "NINA FW")
    assert await silent.is_moving() is False


# ----------------------------------------------------------------- snowflake

async def test_the_snowflake_reports_motion_the_instant_the_goto_goes_out():
    """The wheel has no busy field and cannot have one: the banner stream that
    would carry it is silent for the duration of the move. That silence IS the
    signal — while a goto is outstanding and nothing new arrives on the wire, the
    carousel is turning — and it has to read live from the first moment, because
    the first moment is when the user is looking."""
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)                       # slot 1 (0-based 0)
    w = ws.SnowflakeWheel(fl)
    await w.connect()
    assert not await w.is_moving()

    # The wheel goes quiet: no banner arrives until the carousel lands, so the
    # position stays on the old slot for the whole move.
    task = asyncio.create_task(w.set_position(3))
    await asyncio.sleep(0.05)
    assert await w.is_moving()
    assert await w.get_position() == 0, "the stale banner still names the old slot"

    fl.feed("WSFW508A20260124A4.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A")
    await task
    assert not await w.is_moving()
    assert await w.get_position() == 3


async def test_a_snowflake_that_keeps_talking_never_started_turning(monkeypatch):
    """The forty-second lie, killed.

    Answering "a goto is outstanding" keeps ``is_moving`` True for the whole
    MOVE_TIMEOUT_S (40 s), so a wheel that ignored the command was pixel-identical
    to a working one for forty seconds — a fixed-duration reassurance spelled with
    a timeout, which is the one thing this flag exists to abolish.

    The stream pauses for the duration of a physical move, so a banner that
    arrives AFTER the goto still naming the OLD slot is positive evidence the
    carousel never started: the wheel is sitting there talking. Sub-second, not
    forty."""
    # Sleeps are generous next to the grace on purpose: time.monotonic() is
    # quantized to ~15 ms on Windows, which is the platform this rig runs on.
    monkeypatch.setattr(ws, "MOVE_START_GRACE_S", 0.05)
    monkeypatch.setattr(ws, "MOVE_TIMEOUT_S", 0.8)
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)                       # slot 1 (0-based 0)
    w = ws.SnowflakeWheel(fl)
    await w.connect()

    task = asyncio.create_task(w.set_position(3))
    await asyncio.sleep(0.01)
    fl.feed(LIVE_LINE)                       # inside the grace: it is spinning up
    assert await w.is_moving(), "a wheel still being commanded is not a dead one"

    await asyncio.sleep(0.2)
    fl.feed(LIVE_LINE)                       # past the grace, STILL on slot 1
    assert not await w.is_moving(), "a talking wheel is not a turning wheel"
    assert not task.done(), "the goto keeps waiting — the wheel may yet act"

    with pytest.raises(DeviceError):
        await task


async def test_a_jammed_snowflake_stops_claiming_motion(monkeypatch):
    """The entire point of reading the device instead of animating a timer.

    The carousel never reaches the target, ``wait_banner`` times out, the goto
    raises — and ``is_moving`` must go False so the UI can say "the wheel did
    not turn" instead of pulsing a reassuring box at a wheel that is stuck."""
    monkeypatch.setattr(ws, "MOVE_TIMEOUT_S", 0.2)
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)
    w = ws.SnowflakeWheel(fl)
    await w.connect()

    with pytest.raises(DeviceError):
        await w.set_position(5)              # no banner ever shows slot 6
    assert not await w.is_moving()
    assert await w.get_position() == 0       # still where it started


# ------------------------------------------------------------- status payload

async def test_status_publishes_the_wheels_moving_flag(filter_store):
    h = Hub()
    await h.connect_sim()
    try:
        assert (await h.poll_status())["filterwheel"]["moving"] is False

        fw = h.devices["filterwheel"]
        task = asyncio.create_task(fw.set_position(5))
        await asyncio.sleep(0.05)
        st = await h.poll_status()
        assert st["filterwheel"]["moving"] is True
        # the name the UI would otherwise show is still the OLD slot — which is
        # exactly why the flag has to exist.
        assert st["filterwheel"]["current"] == "L"
        await task

        assert (await h.poll_status())["filterwheel"]["moving"] is False
    finally:
        await h.disconnect_all()


async def test_a_wheel_that_raises_on_is_moving_keeps_its_names_and_position(filter_store):
    """`moving` gets its OWN try for the same reason `focuser.moving` does: it is
    the newest and least universally supported reading in the block, and it must
    never be able to blank the slot names and position the user came for.

    An ABSENT key is the contract for "this backend cannot say" — the client
    reads undefined as "watch the position instead", never as "not moving"."""
    h = Hub()
    await h.connect_sim()
    try:
        async def boom() -> bool:
            raise DeviceError("wheel dropped off the bus")

        h.devices["filterwheel"].is_moving = boom
        fwst = (await h.poll_status())["filterwheel"]
        assert "moving" not in fwst
        assert fwst["names"][0] == "L" and fwst["position"] == 0
        assert fwst["dark_slot"] == 7
    finally:
        await h.disconnect_all()
