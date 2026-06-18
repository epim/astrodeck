"""Shared test fakes: an in-memory tunnel-frame channel (NO WSS on the wire).

The relay's transport-free core (registry/proxy/connection) is driven over a
``FakeScopeTunnel`` -- a ``ScopeTunnel`` whose ``send_frame`` appends to a list
(the home would have received these). A ``FakeBrowserWS`` records what the relay
sent toward one browser ``/ws``. This mirrors the repo's in-process-fakes
discipline (no ``unittest.mock``)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the relay package importable without an install (tests run in-tree).
_RELAY_ROOT = Path(__file__).resolve().parents[1]
if str(_RELAY_ROOT) not in sys.path:
    sys.path.insert(0, str(_RELAY_ROOT))

from relay.protocol import Frame  # noqa: E402
from relay.proxy import BrowserWS  # noqa: E402
from relay.registry import ScopeTunnel  # noqa: E402


class FakeScopeTunnel(ScopeTunnel):
    """A ScopeTunnel that captures frames the relay sends toward the home."""

    def __init__(self, conn_id: str = "fake"):
        self.sent: list[Frame] = []
        super().__init__(self._capture, conn_id=conn_id)

    async def _capture(self, frame: Frame) -> None:
        if self.closed:
            raise RuntimeError("send on closed fake tunnel")
        self.sent.append(frame)

    def last(self) -> Frame:
        return self.sent[-1]

    def of_type(self, t: int) -> list[Frame]:
        return [f for f in self.sent if f.type == t]


class FakeBrowserWS(BrowserWS):
    """Records text frames + close codes the relay pushed toward one browser."""

    def __init__(self, *, fail_send: bool = False, slow: bool = False):
        self.received: list[str] = []
        self.closed_code = None
        self._fail_send = fail_send
        # ``slow`` lets a test hold the send so a buffer backs up; the test sets
        # ``release`` to drain.
        self.slow = slow
        import asyncio
        self.release = asyncio.Event()
        if not slow:
            self.release.set()

    async def send_text(self, text: str) -> None:
        if self._fail_send:
            raise ConnectionResetError("browser gone")
        if self.slow:
            await self.release.wait()
        self.received.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


@pytest.fixture
def fake_tunnel() -> FakeScopeTunnel:
    return FakeScopeTunnel()
