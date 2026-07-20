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
