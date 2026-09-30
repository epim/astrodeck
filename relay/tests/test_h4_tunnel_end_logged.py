"""The relay logs a home tunnel's end (#521).

The rig's tunnel to the Fly relay drops in clusters, each time with "no close
frame received or sent" on the rig's side. The relay's log showed the new
``WebSocket /scope`` accepted and nothing about the old one ending: no close,
no error, no lifetime. So no drop could be classified from the relay: a
close the relay made, a close the home sent and a cut in between all looked
the same, which is to say they did not appear at all.

Now the end of every registered ``/scope`` tunnel is one line: the home id,
its generation, how long it lived, and the close code it ended with, "no
close frame" when there was none, or the code the relay itself sent and why.
No token, cookie or IP.

These cases run the REAL relay under the REAL uvicorn, configured by the same
``uvicorn_options`` that ``main`` hands ``uvicorn.run``, and read what the
production log config's handlers wrote, as ``test_h4_access_log_path_only``
does. That matters here more than there: uvicorn's stock config configures
only its own loggers, so an info line from ``relay.server`` fell through to
Python's last-resort handler, which prints warnings and above only. The line
would have been written by the code and read by nobody.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of relay/
(scratchpad/H4-H4-RELAY-mut in the session scratchpad), from a byte backup
restored with its sha256 checked. Output verbatim.

  M1 "no end log" - the ``log.info(tunnel_end_line(...))`` call in
  ``_scope_endpoint``'s finally removed. 3 failed, the first 3 cases (the
  fourth was added after this run and went red under M6), each the same
  but for its words:
      AssertionError: no 'home tunnel ended' line within 15 s of the home
      closing with 1011. The relay's log held:
        INFO:     Started server process [223072]
        ...
        INFO:     127.0.0.1:51404 - "WebSocket /scope" [accepted]
        INFO:     connection open
        INFO:     connection closed
        INFO:     Shutting down
        ...
      assert []
  (the others: "of the home dropping without a close frame", "of a home
  going deaf"). That log is the relay's log as it was before #521: the
  tunnel opened and closed, and nothing said who ended it or why.

  M2 "the relay logger is not wired" (#556) - the ``cfg["loggers"]["relay"]``
  entry dropped from ``uvicorn_log_config``. 3 failed, with M1's messages
  and M1's log, line for line: the line was built and logged, and went to
  the last-resort handler, which drops info. A test that captured
  ``relay.server`` with a handler of its own would have passed this.

  M3 "the code is not read" - ``tunnel_end_line``'s ``if received_code in
  (None, NO_CLOSE_FRAME_CODE):`` made ``if True:``, so every close reads as
  none. 1 failed, test_a_close_with_1011_logs_the_code:
      AssertionError: the line does not carry the home's close code:
        INFO:     home tunnel ended: home=home-end gen=3 lived=0.0s
        close=no close frame
      assert 'close=1011' in 'INFO:     home tunnel ended: home=home-end
      gen=3 lived=0.0s close=no close frame'

  M4 "1006 read as a code" - the same test made ``if received_code is
  None:``, so an abnormal closure prints its reserved number. 1 failed,
  test_an_abrupt_drop_logs_no_close_frame:
      AssertionError: an abrupt drop must read 'no close frame':
        INFO:     home tunnel ended: home=home-end gen=3 lived=0.0s
        close=1006
      assert 'close=no close frame' in 'INFO:     home tunnel ended:
      home=home-end gen=3 lived=0.0s close=1006'

  M5 "the relay's own close is not recorded" - ``close_socket`` stops
  setting ``relay_close_code``. 1 failed,
  test_a_close_the_relay_made_says_so:
      AssertionError: the relay's keepalive teardown must read as the
      relay's close, not the home's:
        INFO:     home tunnel ended: home=home-end gen=3 lived=0.6s
        close=1001
      assert 'close=1001 sent by the relay' in 'INFO:     home tunnel
      ended: home=home-end gen=3 lived=0.6s close=1001'
  The code alone is the echo of the relay's own close, and would have
  read as the home's.

  M6 "a protocol error is not said" - ``tunnel_end_line``'s ``if error:``
  branch removed. 1 failed, test_a_frame_the_relay_cannot_read_is_named:
      AssertionError: INFO:     home tunnel ended: home=home-end gen=3
      lived=0.0s close=no close frame
      assert False
  which is worse than no line: it reads as a network cut.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import socket

import pytest

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
pytest.importorskip("websockets")

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from relay import protocol  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.server import create_app, uvicorn_options  # noqa: E402

DEVICE_TOKEN = "end-log-device-token-0123456789abcdef"
HOME_ID = "home-end"
GENERATION = 3
_WAIT_S = 30.0
#: How long a case waits for the end line once the home has gone. The end is
#: handled in milliseconds; the bound only turns a missing line into a
#: failure that prints the log, and is kept short of _WAIT_S so the mutant
#: runs above finish in a reasonable time.
_LINE_WAIT_S = 15.0
END = "home tunnel ended"

#: Every logger the production config touches, the relay's own included,
#: put back afterwards so no later test inherits a handler writing into this
#: file's buffer.
_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "relay")


@pytest.fixture
def restore_logging():
    saved = {}
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.level, lg.propagate, lg.disabled)
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _end_lines(log: str) -> list[str]:
    return [ln for ln in log.splitlines() if END in ln]


async def _serve_and_capture(home, **cfg_overrides) -> str:
    """Run the relay as ``main`` would, let ``home(port)`` dial and go, wait
    for the end line, stop, and return everything the handlers wrote."""
    port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=port,
                      origin="relay.test", **cfg_overrides)
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    # uvicorn configures logging here, from the options production uses.
    config = uvicorn.Config(app, **uvicorn_options(cfg))
    buf = io.StringIO()
    handlers = {id(h): h for name in _LOGGERS
                for h in logging.getLogger(name).handlers}
    assert handlers, "the log config installed no handlers, so nothing is graded"
    for h in handlers.values():
        h.setStream(buf)
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_S
    while not server.started and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert server.started, "relay server did not start"
    try:
        await asyncio.wait_for(home(port), timeout=_WAIT_S)
        deadline = loop.time() + _LINE_WAIT_S
        while not _end_lines(buf.getvalue()) and loop.time() < deadline:
            await asyncio.sleep(0.02)
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        task.cancel()
    return buf.getvalue()


async def _registered(port: int):
    """Dial /scope, HELLO, and return the socket once the relay ACKed it."""
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/scope",
                                  max_size=2 * 1024 * 1024)
    await ws.send(protocol.hello(DEVICE_TOKEN, HOME_ID,
                                 generation=GENERATION).encode())
    ack = protocol.decode(await ws.recv())
    assert ack.header["ok"] is True, ack.header
    return ws


def _the_line(log: str, what: str) -> str:
    lines = _end_lines(log)
    assert lines, (
        f"no {END!r} line within {_LINE_WAIT_S:g} s of {what}. The relay's "
        "log held:\n" + log)
    assert len(lines) == 1, "one tunnel, one end line:\n" + log
    line = lines[0]
    # The fields every end line carries, whatever ended it.
    assert f"home={HOME_ID}" in line, line
    assert f"gen={GENERATION}" in line, line
    assert re.search(r"lived=\d+\.\ds", line), line
    # And what it must never carry. The IP is on the handshake lines uvicorn
    # writes itself; the end line is the relay's own, and has no need of it.
    assert DEVICE_TOKEN not in log, "the device token reached the relay's log"
    assert "127.0.0.1" not in line, line
    assert "cookie" not in line.lower(), line
    return line


async def test_a_close_with_1011_logs_the_code(restore_logging):
    async def home(port):
        ws = await _registered(port)
        await ws.close(code=1011, reason="home going down")

    line = _the_line(await _serve_and_capture(home),
                     "the home closing with 1011")
    assert "close=1011" in line, (
        "the line does not carry the home's close code:\n" + line)
    # CONTROL: a close the home sent is not reported as the relay's.
    assert "sent by the relay" not in line, line


async def test_an_abrupt_drop_logs_no_close_frame(restore_logging):
    async def home(port):
        ws = await _registered(port)
        # The TCP connection goes with no close frame: what a network cut
        # looks like from the relay, and what the rig reports as "no close
        # frame received or sent".
        ws.transport.abort()
        with contextlib.suppress(Exception):
            await ws.wait_closed()

    line = _the_line(await _serve_and_capture(home),
                     "the home dropping without a close frame")
    assert "close=no close frame" in line, (
        "an abrupt drop must read 'no close frame':\n" + line)
    assert "1006" not in line, line


async def test_a_close_the_relay_made_says_so(restore_logging):
    """Beyond the acceptance, and the half of #521 the other two cases leave
    open: when the RELAY ends a tunnel (here its keepalive, after a home
    stops answering PINGs), the line says so, so a relay-side close is never
    read as a network cut or a home's choice."""
    async def home(port):
        ws = await _registered(port)
        # Deaf: never answer a PING, and wait for the relay to hang up.
        with contextlib.suppress(websockets.ConnectionClosed):
            while True:
                await ws.recv()

    line = _the_line(await _serve_and_capture(home, ping_interval_s=0.2),
                     "a home going deaf")
    assert "close=1001 sent by the relay" in line, (
        "the relay's keepalive teardown must read as the relay's close, not "
        "the home's:\n" + line)
    assert "PONG" in line, line


async def test_a_frame_the_relay_cannot_read_is_named(restore_logging):
    """The fourth way out of the read loop: the home sends bytes that are
    not a frame. There is no close code to report, because the relay ends
    the tunnel itself afterwards, so the line says what went wrong instead."""
    async def home(port):
        ws = await _registered(port)
        await ws.send(b"this is not a tunnel frame")
        with contextlib.suppress(websockets.ConnectionClosed):
            while True:
                await ws.recv()

    line = _the_line(await _serve_and_capture(home),
                     "the home sending a frame that does not decode")
    assert line.endswith(
        "close=none: the home sent a frame the relay could not decode or "
        "route"), line
