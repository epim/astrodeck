"""ZWO AM5N native serial driver: link double, telescope behavior, backend +
framework integration. All coordinates fictional (site privacy)."""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.devices.serial_link import LinkError, SerialLink


class FakeLink:
    """Test double for SerialLink: same ``request``/``close`` surface.

    ``script`` maps an unframed command string to either a reply string, a list
    of replies (consumed in order; last repeats), or a callable(cmd)->reply.
    Unscripted "hash"/"ack" requests raise LinkError (like a silent mount);
    unscripted "none" requests are simply logged.
    """

    def __init__(self, script: dict | None = None):
        self.script = dict(script or {})
        self.sent: list[str] = []
        self.closed = False

    async def open(self) -> None:  # parity with SerialLink
        pass

    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5):
        self.sent.append(cmd)
        entry = self.script.get(cmd)
        if callable(entry):
            entry = entry(cmd)
        elif isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if reply == "none":
            return None
        if entry is None:
            raise LinkError(f"unscripted command {cmd!r}")
        return entry

    async def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------- link double

async def test_fakelink_sequences_and_logging():
    fl = FakeLink({"GR": ["10:00:00", "11:00:00"], "Spu": "1"})
    assert await fl.request("GR") == "10:00:00"
    assert await fl.request("GR") == "11:00:00"
    assert await fl.request("GR") == "11:00:00"          # last repeats
    assert await fl.request("Spu", reply="ack") == "1"
    assert await fl.request("Me", reply="none") is None  # unscripted fire-forget ok
    assert fl.sent == ["GR", "GR", "GR", "Spu", "Me"]
    with pytest.raises(LinkError):
        await fl.request("GD")                            # unscripted read raises


async def test_seriallink_requires_open():
    link = SerialLink("COM99")
    with pytest.raises(LinkError):
        await link.request("GR")


# ------------------------------------------------------- telescope: connect/state

from datetime import datetime, timezone  # noqa: E402

from astrodeck.devices.base import DeviceError, PierSide  # noqa: E402
import astrodeck.devices.backends.zwo_am5 as am5  # noqa: E402

FIXED_UTC = datetime(2026, 7, 19, 22, 14, 58, tzinfo=timezone.utc)


def _connect_script(**over):
    """A FakeLink script for a successful connect (parked mount)."""
    s = {
        "GVP": "AM5N", "GV": "1.8.8",
        "SG+00:00": "1", "SH0": "1", "SC07/19/26": "1", "SL22:14:58": "1",
        "SMGE+40*00:00&+100*30:30": "1",
        "Gps": "2", "GU": "nGM000000005",
        "GR": "10:13:56", "GD": "+90*00:00", "GAT": "0",
    }
    s.update(over)
    return s


@pytest.fixture
def fixed_env(monkeypatch):
    monkeypatch.setattr(am5, "_utcnow", lambda: FIXED_UTC)
    monkeypatch.setattr(am5, "_site_latlon",
                        lambda: (40.0, -(100 + 30 / 60 + 30 / 3600)))


async def test_connect_flow_sequence_no_autounpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert tel.connected is True
    assert tel.hardware is True and tel.backend == "zwo-am5"
    # exact init order, and NO :Spu# (connect must not auto-unpark)
    assert fl.sent[:7] == ["GVP", "GV", "SG+00:00", "SH0", "SC07/19/26",
                           "SL22:14:58", "SMGE+40*00:00&+100*30:30"]
    assert "Spu" not in fl.sent


async def test_connect_identity_mismatch_closes(fixed_env):
    fl = FakeLink(_connect_script(GVP="Prototype"))
    tel = am5.ZwoAm5Telescope(fl)
    with pytest.raises(DeviceError):
        await tel.connect()
    assert fl.closed is True and tel.connected is False


async def test_parked_state_and_unpark(fixed_env):
    fl = FakeLink(_connect_script())
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    assert await tel.is_parked() is True          # Gps -> "2"
    fl.script["Gps"] = "0"
    fl.script["Spu"] = "1"
    await tel.unpark()
    assert "Spu" in fl.sent
    assert await tel.is_parked() is False


async def test_position_and_tracking_reads(fixed_env):
    fl = FakeLink(_connect_script(GAT="1"))
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    ra, dec = await tel.get_position()
    assert abs(ra - (10 + 13 / 60 + 56 / 3600)) < 1e-9 and dec == 90.0
    assert await tel.get_tracking() is True
    fl.script["Gm"] = "E"
    assert await tel.pier_side() == PierSide.EAST


async def test_parked_refusal_maps_to_honest_error(fixed_env):
    fl = FakeLink(_connect_script())
    fl.script["Te"] = "e14"
    tel = am5.ZwoAm5Telescope(fl)
    await tel.connect()
    with pytest.raises(DeviceError, match="parked"):
        await tel.set_tracking(True)
