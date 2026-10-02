# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The relay's logs carry the path of a request, never its query string (#520).

On 2026-09-28 the Fly relay's access log held the mount's live pointing,
several times a minute, as `GET /h/home-1/api/cloudmap/at?alt=..&az=..`. A
pointing at a known time is a function of the site, and this log is a third
party's pipeline. The UI no longer sends one and the rig refuses one; this is
the wall that covers every route, including the ones not written yet.

These cases run the REAL relay under the REAL uvicorn, configured by the same
`uvicorn_options` that `main` hands `uvicorn.run`, and read what uvicorn's own
handlers wrote. The handler streams are pointed at a buffer after uvicorn has
configured them, so the formatter under test is the one the production log
config installs rather than one this file built for itself.

No home is registered, so the relay answers the request itself. That is
enough: uvicorn logs the request line for every response, whoever wrote it.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of relay/
(scratchpad/H4-PRIV-mut in the session scratchpad) from a byte backup restored
with its sha256 checked. Output verbatim.

  M1 "formatter logs the full request line" - `PathOnlyAccessFormatter.format`
  returns `super().format(record)`, skipping `_without_queries`. 1 failed,
  test_the_access_log_carries_the_path_and_not_the_query:
      AssertionError: a digit of the query string reached the access log:
      /h/home/api/cloudmap/at?alt=12.345&az=67.891

  M2 "the handshake line keeps its query" - the same change in
  `PathOnlyDefaultFormatter.format`. 1 failed,
  test_the_websocket_handshake_line_carries_no_query:
      AssertionError: a value from a WebSocket query string reached the
      relay's log
      '12.345' is contained here:
        me/ws?alt=12.345&az=67.891" 403

  M3 "main is not wired to the formatter" - `log_config=uvicorn_log_config()`
  dropped from `uvicorn_options`, so uvicorn's stock config applies. 2 failed,
  both cases above with the same two messages: the formatters were right and
  nothing ran them, which is the failure a test of the formatter alone would
  have passed.

MUTATION run 2026-09-30, in a byte backup of this worktree (sha256-verified
restore), for the fixture fix below (#556 residual):

  M4 "the fixture does not restore the relay logger" - `"relay"` dropped
  from `_UVICORN_LOGGERS`. 1 failed,
  test_the_fixture_restores_the_relay_logger_after_this_files_server_ran:
      AssertionError: the 'relay' logger was not restored after this file's
      server ran: its handler still writes into test_h4_access_log_path_
      only.py's own (now closed) buffer, so a later test's relay.server
      line would be silently dropped (before=([], 0, True, False)
      after=([<StreamHandler (NOTSET)>], 20, False, False))

MUTATION run 2026-09-30, in a byte backup of this worktree (sha256-verified
restore), for the websocket-ping pin below (#597):

  M5 "the ping keywords are not pinned" - `ws_ping_interval=20.0,
  ws_ping_timeout=20.0` dropped from `uvicorn_options`. 1 failed,
  test_uvicorn_options_pins_the_websocket_ping_keywords:
      AssertionError: {'access_log': True, 'backlog': 128, ...}
      assert None == 20.0
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
pytest.importorskip("httpx")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402

from relay.config import RelayConfig  # noqa: E402
from relay.server import (QUERY_WITHHELD, _path_only, create_app,  # noqa: E402
                          uvicorn_options)

#: A home id with no digit in it, so any digit in a logged target came from
#: the query.
PATH = "/h/home/api/cloudmap/at"
QUERY = "alt=12.345&az=67.891"
_WAIT_S = 30.0

#: The loggers uvicorn's config touches, whose state is put back afterwards so
#: no later test inherits a handler writing into this file's buffer.
#:
#: "relay" is included alongside uvicorn's own three (#556 residual):
#: `uvicorn_log_config` also wires `relay.server`'s own info lines (the #521
#: tunnel-end line, the SIGHUP reload line) to uvicorn's "default" handler, so
#: `uvicorn.Config` in `_serve_and_capture` points that handler's stream at
#: THIS file's buffer same as the other three. Leaving "relay" out of this
#: tuple restored only the three uvicorn loggers, so after this file ran, the
#: relay logger's handler kept writing into this file's closed buffer and any
#: later test reading `relay.server` output (test_h4_tunnel_end_logged.py, if
#: it ran after this file in the same process) would see nothing.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "relay")


@pytest.fixture
def restore_uvicorn_logging():
    saved = {}
    for name in _UVICORN_LOGGERS:
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


async def _serve_and_capture(ask) -> str:
    """Run the relay as ``main`` would, call ``ask(port)``, stop, and return
    everything uvicorn's handlers wrote."""
    port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=port,
                      origin="relay.test")
    app = create_app(cfg)
    # uvicorn configures logging here, from the options production uses.
    config = uvicorn.Config(app, **uvicorn_options(cfg))
    buf = io.StringIO()
    handlers = [h for name in _UVICORN_LOGGERS
                for h in logging.getLogger(name).handlers]
    assert handlers, "uvicorn installed no log handlers, so nothing is graded"
    for h in handlers:
        h.setStream(buf)
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_S
    while not server.started and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert server.started, "relay server did not start"
    try:
        await ask(port)
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        task.cancel()
    return buf.getvalue()


async def test_the_access_log_carries_the_path_and_not_the_query(
        restore_uvicorn_logging):
    async def ask(port):
        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}{PATH}?{QUERY}")

    log = await _serve_and_capture(ask)
    targets = re.findall(r'"GET (\S+) HTTP/', log)
    # The control: the request WAS logged, and by its path.
    assert targets, "no access line was written at all:\n" + log
    assert any(t.startswith(PATH) for t in targets), (
        "the access log no longer names the path:\n" + log)
    for target in targets:
        assert not re.search(r"\d", target), (
            "a digit of the query string reached the access log: " + target)
        assert "alt" not in target and "az=" not in target, target
    assert "12.345" not in log and "67.891" not in log, (
        "a value from the query string reached the relay's log")


async def test_the_websocket_handshake_line_carries_no_query(
        restore_uvicorn_logging):
    """uvicorn logs a WebSocket handshake through its ERROR logger, with the
    same full target, so the access formatter alone would leave this open."""
    async def ask(port):
        with contextlib.suppress(Exception):     # refused: no home is here
            async with websockets.connect(
                    f"ws://127.0.0.1:{port}/h/home/ws?{QUERY}",
                    open_timeout=5):
                pass

    log = await _serve_and_capture(ask)
    assert "/h/home/ws" in log, (
        "the handshake was not logged, so this case graded nothing:\n" + log)
    assert "12.345" not in log and "67.891" not in log, (
        "a value from a WebSocket query string reached the relay's log")


def test_the_rule_withholds_a_query_and_leaves_everything_else():
    """The rule on its own, for the arguments it must leave alone: a path with
    no query, a client address, a method, a status code."""
    assert _path_only(PATH + "?" + QUERY) == PATH + QUERY_WITHHELD
    assert _path_only(PATH) == PATH
    for untouched in ("127.0.0.1:5000", "GET", "1.1", 200, None):
        assert _path_only(untouched) == untouched


def test_the_fixture_restores_the_relay_logger_after_this_files_server_ran():
    """#556 residual: ``uvicorn_log_config`` wires ``relay.server``'s own info
    lines onto uvicorn's "default" handler (#521, #556), and
    ``_serve_and_capture`` points every handler of every ``_UVICORN_LOGGERS``
    logger at THIS file's buffer. If "relay" were left out of that tuple (as
    it was before this fix), the "relay" logger's handler would keep pointing
    at this file's buffer after the test ended, so any later test reading
    ``relay.server`` output in the same process would see nothing.

    Drives ``restore_uvicorn_logging`` by hand (its undecorated function,
    since pytest refuses a fixture called directly) instead of taking it as a
    test parameter, so this test can see its OWN teardown run and check what
    it leaves behind -- something a test that merely depends on the fixture,
    like the two above, cannot observe about its own invocation."""
    relay_logger = logging.getLogger("relay")
    before = (list(relay_logger.handlers), relay_logger.level,
              relay_logger.propagate, relay_logger.disabled)

    gen = restore_uvicorn_logging.__wrapped__()
    next(gen)  # run the fixture's setup half (it only records state)
    try:
        async def ask(port):
            async with httpx.AsyncClient() as client:
                await client.get(f"http://127.0.0.1:{port}{PATH}")

        asyncio.run(_serve_and_capture(ask))
        # While the fixture is still "live" (not yet torn down), uvicorn's
        # config wired a handler onto "relay" that writes into the buffer
        # _serve_and_capture just read back and discarded -- the state #556's
        # residual left in place afterwards.
        assert relay_logger.handlers, (
            "uvicorn never gave the 'relay' logger a handler, so this case "
            "is grading nothing")
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)  # run the fixture's teardown half

    after = (list(relay_logger.handlers), relay_logger.level,
             relay_logger.propagate, relay_logger.disabled)
    assert after == before, (
        "the 'relay' logger was not restored after this file's server ran: "
        f"its handler still writes into test_h4_access_log_path_only.py's "
        f"own (now closed) buffer, so a later test's relay.server line "
        f"would be silently dropped (before={before!r} after={after!r})")


def test_uvicorn_options_pins_the_websocket_ping_keywords():
    """#597: ``ws_ping_interval``/``ws_ping_timeout`` are given explicitly in
    ``uvicorn_options`` rather than left to whatever ``uvicorn.Config``
    defaults to, so an unpinned dependency upgrade (the relay's requirements
    had none until this same change) cannot also silently change this wire
    timing. Pinned at uvicorn's own current default (20.0/20.0): this closes
    the gap, it does not change today's behaviour.

    The Dockerfile's CMD is ``python -m relay`` with no arguments, so these
    cannot be set on a command line; ``uvicorn_options`` is the one place
    ``main`` (and this test) builds the keywords ``uvicorn.run`` receives."""
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=_free_port(),
                      origin="relay.test")
    options = uvicorn_options(cfg)
    assert options.get("ws_ping_interval") == 20.0, options
    assert options.get("ws_ping_timeout") == 20.0, options
