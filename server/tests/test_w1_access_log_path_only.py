# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The rig server's uvicorn log carries the path of a request, never its query
string (#550): the fix WP-05a in backlog ruling D-nn (owner-approved
2026-09-30), which gave the relay a path-only formatter in #520 to the rig
server too. ``ASTRODECK_TOKEN`` is accepted as a `?token=` query parameter
(see `api/app.py:_present_token`), so an unfixed log writes the shared admin
credential to stdout for every request that used it that way.

These cases run a REAL uvicorn under the SAME options `_cmd_run` hands
`uvicorn.Config` (`astrodeck.__main__.uvicorn_config_kwargs` +
`astrodeck.logfmt.uvicorn_log_config`), so a change to that wiring is what
gets graded here, not a copy of it. The ASGI app underneath is a tiny
stand-in, not the rig's full `create_app()`: the formatters are what is under
test, not the rig's routing, auth or boot lifespan (weather/cloudmap/dew/etc.
pollers), which are exercised elsewhere.

MUTATIONS RUN, 2026-09-30, each in a byte backup of this worktree's
server/astrodeck/, restored and sha256-verified afterwards:

  M1 "formatter logs the full request line" - `logfmt.PathOnlyAccessFormatter
  .format` changed to `return super().format(record)`, skipping
  `_without_queries`. 1 failed,
  test_the_access_log_carries_the_path_and_not_the_query:
      AssertionError: a digit of the query string reached the access log:
      /probe?secret=w1-wp05a-marker-97531&n=24680

  M2 "the handshake line keeps its query" - the same change in
  `logfmt.PathOnlyDefaultFormatter.format`. 1 failed,
  test_the_websocket_handshake_line_carries_no_query:
      AssertionError: a value from a WebSocket query string reached the log

  M3 "_cmd_run's config drops the path-only wiring" -
  `uvicorn_config_kwargs`'s ``log_config=log_config`` line deleted (the
  dict no longer carries a ``log_config`` key at all, so uvicorn falls back
  to its stock formatters exactly as production would if `_cmd_run` stopped
  passing it). 2 failed, both cases above with the same two messages: the
  formatters were right and nothing wired them in, which is the failure a
  test of the formatter alone would have passed.
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
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route, WebSocketRoute  # noqa: E402

from astrodeck import logfmt  # noqa: E402
from astrodeck.__main__ import uvicorn_config_kwargs  # noqa: E402

#: A probe path with no digit in it, so any digit in a logged target came
#: from the query. The query carries a made-up, non-astronomical marker (no
#: alt/az/lat/lon-shaped values anywhere in this file, per this WP's rule).
PATH = "/probe"
MARKER = "97531"
QUERY = f"secret=w1-wp05a-marker-{MARKER}&n=24680"
_WAIT_S = 30.0

#: The loggers uvicorn's config touches, whose state is put back afterwards so
#: no later test inherits a handler writing into this file's buffer.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


async def _http_ep(request):  # noqa: ANN001
    return PlainTextResponse("ok")


async def _ws_ep(websocket) -> None:  # noqa: ANN001
    # Refused BEFORE accept, exactly the shape of the rig's own `/ws` refusing
    # a caller it will not serve (bad Host/origin/token) -- the pre-accept
    # path uvicorn's error logger writes the handshake line for.
    await websocket.close(code=1008)


def _probe_app() -> "Starlette":
    """A stand-in ASGI app carrying just enough routes to make uvicorn write
    an access line and a WebSocket handshake line. Not the rig's `create_app`
    -- see the module docstring for why."""
    return Starlette(routes=[
        Route(PATH, _http_ep, methods=["GET"]),
        WebSocketRoute("/probe-ws", _ws_ep),
    ])


def _config(port: int) -> "uvicorn.Config":
    """A real `uvicorn.Config` built from the production options (#550)."""
    return uvicorn.Config(
        _probe_app(),
        **uvicorn_config_kwargs(
            host="127.0.0.1", port=port, access_log_enabled=True,
            trusted_proxy_ips="", log_config=logfmt.uvicorn_log_config(),
        ),
    )


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


async def _serve_and_capture(config: "uvicorn.Config", ask) -> str:
    """Run uvicorn as `_cmd_run` would, call `ask()`, stop, and return
    everything uvicorn's handlers wrote."""
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
    assert server.started, "server did not start"
    try:
        await ask()
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        task.cancel()
    return buf.getvalue()


async def test_the_access_log_carries_the_path_and_not_the_query(
        restore_uvicorn_logging):
    port = _free_port()

    async def ask():
        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}{PATH}?{QUERY}")

    log = await _serve_and_capture(_config(port), ask)
    targets = re.findall(r'"GET (\S+) HTTP/', log)
    # The control: the request WAS logged, and by its path.
    assert targets, "no access line was written at all:\n" + log
    assert any(t.startswith(PATH) for t in targets), (
        "the access log no longer names the path:\n" + log)
    for target in targets:
        assert not re.search(r"\d", target), (
            "a digit of the query string reached the access log: " + target)
    assert MARKER not in log, (
        "a value from the query string reached the access log")


async def test_the_websocket_handshake_line_carries_no_query(
        restore_uvicorn_logging):
    """uvicorn logs a WebSocket handshake through its ERROR logger, with the
    same full target, so the access formatter alone would leave this open."""
    port = _free_port()

    async def ask():
        with contextlib.suppress(Exception):   # refused before accept, by design
            async with websockets.connect(
                    f"ws://127.0.0.1:{port}/probe-ws?{QUERY}",
                    open_timeout=5):
                pass

    log = await _serve_and_capture(_config(port), ask)
    assert "/probe-ws" in log, (
        "the handshake was not logged, so this case graded nothing:\n" + log)
    assert MARKER not in log, (
        "a value from a WebSocket query string reached the log")


def test_the_rule_withholds_a_query_and_leaves_everything_else():
    """The rule on its own, for the arguments it must leave alone: a path
    with no query, a client address, a method, a status code."""
    assert logfmt._path_only(PATH + "?" + QUERY) == PATH + logfmt.QUERY_WITHHELD
    assert logfmt._path_only(PATH) == PATH
    for untouched in ("127.0.0.1:5000", "GET", "1.1", 200, None):
        assert logfmt._path_only(untouched) == untouched
