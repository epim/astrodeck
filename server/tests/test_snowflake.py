"""Wanderer Snowflake filter wheel: banner parser, stream-first wheel driver,
backend + framework integration. Hardware-free (FakeStreamLink)."""
from __future__ import annotations

import asyncio
import time

import pytest

from astrodeck.devices.base import DeviceError
import astrodeck.devices.backends.wanderer_snowflake as ws
from astrodeck.devices.backends.wanderer_snowflake import Banner, parse_banner

LIVE_LINE = "WSFW508A20260124A1.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A"


# ------------------------------------------------------------------- parser

def test_parse_banner_live_capture_line():
    b = parse_banner(LIVE_LINE)
    assert b is not None
    assert b.model == "WSFW508" and b.fw_date == 20260124
    assert b.slot == 1 and b.letters == "LXXXXXXX" and b.device_id == 0


def test_parse_banner_rejects_garbage():
    assert parse_banner("") is None
    assert parse_banner("XXAgarbageA") is None
    assert parse_banner("WandererRotatorLiteV2A20240226A0A0.5A0A") is None
    assert parse_banner("WSFW508A2026") is None            # truncated


def test_parse_banner_accepts_an_unseen_wsfw_model():
    """A model we have never met speaks the same protocol.

    The parser used to hold a two-entry allowlist, so any other WSFW made
    connect fail with "not a Snowflake" — refusing a wheel whose every field we
    read correctly, on the strength of a marketing string."""
    b = parse_banner("WSFW650A20260124A1.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A")
    assert b is not None and b.model == "WSFW650"


def test_parse_banner_still_rejects_a_different_wanderer_product():
    """Most of Wanderer's line sits behind the same CH340, so the prefix has to
    stay narrow enough to tell a filter wheel from a rotator on the next port."""
    assert parse_banner("WSRotatorA20260124A1.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A") is None


def test_banner_reports_the_wheels_own_slot_count():
    """The letters field carries one character per slot, so its length is the
    slot count. It used to be truncated to a hardcoded 8."""
    assert parse_banner(LIVE_LINE).slots == 8
    five = "WSFW368A20260124A1.00ALXXXXA0A0A0A0A0A0A0A0A0A"
    assert parse_banner(five).slots == 5


# ------------------------------------------------------------- fake stream

class FakeStreamLink:
    """SnowflakeLink test double: same surface; ``feed`` injects banners."""

    def __init__(self, port_path: str = "COM8"):
        self.port_path = port_path
        self.latest: Banner | None = None
        self.sent: list[str] = []
        self.opened = self.closed = False
        self.on_send = None            # optional callable(cmd)

    def feed(self, line: str) -> None:
        b = parse_banner(line)
        if b is not None:
            self.latest = b

    async def open(self) -> None:
        self.opened = True

    async def send(self, cmd: str) -> None:
        self.sent.append(cmd)
        if self.on_send:
            self.on_send(cmd)

    async def wait_banner(self, pred, timeout: float) -> Banner:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            b = self.latest
            if b is not None and pred(b):
                return b
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(f"timed out ({timeout:.0f}s) waiting for the wheel")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        self.closed = True


def _wheel(link=None):
    return ws.SnowflakeWheel(link or FakeStreamLink())


# ------------------------------------------------------------------- wheel

async def test_connect_seeds_names_no_calibrate():
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)
    w = _wheel(fl)
    await w.connect()
    assert w.connected and w.model == "WSFW508"
    assert w.filter_names == ["L"] + [f"Slot {i}" for i in range(2, 9)]
    assert fl.sent == []                       # NOTHING sent at connect
    assert await w.get_position() == 0         # slot 1 -> 0-based


async def test_connect_sizes_the_carousel_from_the_wire():
    """A five-slot wheel must not be described as an eight-slot one with three
    empties, and must refuse slot 5 rather than driving past its own carousel."""
    fl = FakeStreamLink()
    fl.feed("WSFW368A20260124A1.00ALXXXXA0A0A0A0A0A0A0A0A0A")
    w = _wheel(fl)
    await w.connect()
    assert w.slots == 5
    assert w.filter_names == ["L", "Slot 2", "Slot 3", "Slot 4", "Slot 5"]
    with pytest.raises(DeviceError, match="out of range 0..4"):
        await w.set_position(5)


async def test_connect_refuses_silence(monkeypatch):
    monkeypatch.setattr(ws, "FIRST_BANNER_TIMEOUT_S", 0.05)
    fl = FakeStreamLink()                      # never feeds
    with pytest.raises(DeviceError, match="no Snowflake banner"):
        await _wheel(fl).connect()
    assert fl.closed


async def test_connect_refuses_old_firmware():
    fl = FakeStreamLink()
    fl.feed("WSFW508A20250101A1.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A")
    with pytest.raises(DeviceError, match="older than required"):
        await _wheel(fl).connect()


async def test_goto_waits_for_stream_resume():
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)
    w = _wheel(fl)
    await w.connect()

    def deliver_after_pause(cmd):
        # the physical move: the stream pauses, then resumes with the target
        async def _later():
            await asyncio.sleep(0.05)
            fl.feed("WSFW508A20260124A3.00ALXXXXXXXA0A0A0A0A0A0A0A0A0A")
        asyncio.get_running_loop().create_task(_later())
    fl.on_send = deliver_after_pause

    await w.set_position(2)                    # 0-based 2 -> wire slot 3
    assert fl.sent == ["2003"]
    assert await w.get_position() == 2


async def test_goto_timeout(monkeypatch):
    monkeypatch.setattr(ws, "MOVE_TIMEOUT_S", 0.05)
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)
    w = _wheel(fl)
    await w.connect()
    with pytest.raises(DeviceError, match="timed out"):
        await w.set_position(4)                # no resume ever arrives


async def test_goto_bounds():
    fl = FakeStreamLink()
    fl.feed(LIVE_LINE)
    w = _wheel(fl)
    await w.connect()
    with pytest.raises(DeviceError, match="out of range"):
        await w.set_position(8)


# ------------------------------------------- backend + framework integration

from astrodeck.devices import backends as _backends  # noqa: E402,F401
from astrodeck.devices.backend import BACKENDS  # noqa: E402


@pytest.fixture
def registered():
    prior = BACKENDS.get("wanderer-snowflake")
    ws.register_all()
    try:
        yield BACKENDS["wanderer-snowflake"]
    finally:
        if prior is not None:
            BACKENDS["wanderer-snowflake"] = prior
        else:
            BACKENDS.pop("wanderer-snowflake", None)


def test_snowflake_manifest(registered):
    from astrodeck.devices.backend import list_backends
    row = next(r for r in list_backends() if r["name"] == "wanderer-snowflake")
    assert row["transport"] == "serial" and row["hardware"] is True
    assert row["roles"] == ("filterwheel",)
    assert row["driver_type"] == "wanderer-snowflake"


async def test_connect_profile_e2e_filterwheel(registered, monkeypatch):
    from astrodeck.devices.orchestrator import connect_profile
    from astrodeck.profiles import Profile, ProfileDevice

    def make_fed_link(port):
        fl = FakeStreamLink(port)
        fl.feed(LIVE_LINE)
        return fl
    monkeypatch.setattr(ws, "_make_link", make_fed_link)
    p = Profile(name="fw rig", primary_backend="none", devices=[
        ProfileDevice(role="filterwheel", backend="wanderer-snowflake",
                      transport="serial", port_path="COM8")])
    res = await connect_profile(p.to_rigspec())
    wheel = res.rig.get("filterwheel")
    assert wheel is not None and wheel.connected
    assert wheel.hardware is True
    for s in res.sessions.values():
        await s.close()


async def test_discover_lists_ch340_unverified(registered, monkeypatch):
    class _Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid
    from serial.tools import list_ports
    monkeypatch.setattr(list_ports, "comports",
                        lambda: [_Port("COM8", 0x1A86, 0x7523),
                                 _Port("COM3", 0x03C3, 0x4001)])
    found = await registered.discover()
    assert found == [{"role": "filterwheel", "name": "CH340 serial (Wanderer?)",
                      "port_path": "COM8", "verified": False}]