# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The relay keeps its tunnel evidence: no access line per health probe (#617).

Fly's HTTP check (relay/fly.toml, ``path = "/healthz"``, ``interval =
"15s"``) polls the relay every 15 s. Before this fix uvicorn's access logger
wrote one line per probe, the same as for any other request, and `flyctl
logs --no-tail` serves only its own 100-line buffer: four probe lines a
minute filled it in about 25 minutes and could push the #521 tunnel-end line
-- the evidence a tunnel problem needs -- out of the only window `--no-tail`
can read before anyone happened to look.

A ``logging.Filter`` on uvicorn's "access" handler now drops that one line
(``relay.server._NoHealthzAccessFilter``), beside the path-only formatters
from #520. Tunnel connect (the /scope WebSocket handshake, uvicorn's error
logger) and tunnel end (``tunnel_end_line``, #521) go through a different
handler entirely and are untouched.

These cases run the REAL relay under REAL uvicorn, configured by the same
``uvicorn_options`` that ``main`` hands ``uvicorn.run``, and read what
uvicorn's own handlers wrote -- the same approach test_h4_access_log_path_
only.py and test_h4_tunnel_end_logged.py use, for the same reason: a test of
the filter alone would pass even if nothing wired it on.

MUTATION RUN, 2026-10-01, in a byte backup of this worktree (sha256
91173fc45b19a7ebd95028e21318a0d05622e8af2472c4cb7e75925fba9b7e20, restore
verified against the same hash):

  M1 "filter removed" -- the two wiring lines (``cfg["filters"] = ...`` and
  ``cfg["handlers"]["access"]["filters"] = ["no_healthz"]``) dropped from
  ``uvicorn_log_config``, the filter class itself left in place. 1 failed,
  test_healthz_probes_write_no_access_line_but_tunnel_lines_still_print:
      AssertionError: a /healthz probe reached the access log:
      INFO:     Started server process [221764]
      INFO:     Waiting for application startup.
      INFO:     Application startup complete.
      INFO:     Uvicorn running on http://127.0.0.1:57536 (Press CTRL+C to quit)
      INFO:     127.0.0.1:57537 - "WebSocket /scope" [accepted]
      INFO:     connection open
      INFO:     127.0.0.1:57539 - "GET /healthz HTTP/1.1" 200 OK
      INFO:     127.0.0.1:57539 - "GET /healthz HTTP/1.1" 200 OK
      INFO:     127.0.0.1:57539 - "GET /healthz HTTP/1.1" 200 OK
      INFO:     home tunnel ended: home=home-w4-70 gen=1 lived=0.2s close=1000
      INFO:     Shutting down
      ...
      assert not <re.Match object; span=(312, 331), match='"GET /healthz HTTP/'>
  The other two cases in this file (the scope control and the pure-function
  rule test) still passed under this mutant: ``_is_healthz_access`` itself
  was never touched, only its wiring onto the handler, which is exactly the
  gap an integration test closes and a unit test of the rule cannot see.
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

from relay import protocol  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.server import (HEALTHZ_PATH, _is_healthz_access, create_app,  # noqa: E402
                          uvicorn_options)

DEVICE_TOKEN = "w4-wp-70-device-token-0123456789ab"
#: Made-up home id (CLAUDE.md: no real site name anywhere), distinct from the
#: H4 files' so this file's tests can run in the same process as those.
HOME_ID = "home-w4-70"
GENERATION = 1
_WAIT_S = 30.0
#: How long a case waits for the tunnel-end line once the home has closed.
#: Handled in milliseconds locally; the bound only turns a missing line into
#: a failure that prints the log.
_LINE_WAIT_S = 15.0

#: Every logger the production log config touches, the relay's own included
#: (#556 residual -- see test_h4_access_log_path_only.py), put back
#: afterwards so no later test inherits a handler writing into this file's
#: closed buffer.
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


async def _registered(port: int):
    """Dial /scope, HELLO, and return the socket once the relay ACKed it."""
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/scope",
                                  max_size=2 * 1024 * 1024)
    await ws.send(protocol.hello(DEVICE_TOKEN, HOME_ID,
                                 generation=GENERATION).encode())
    ack = protocol.decode(await ws.recv())
    assert ack.header["ok"] is True, ack.header
    return ws


async def _serve_and_capture(scenario) -> str:
    """Run the relay as ``main`` would, provision one home, let
    ``scenario(port, buf)`` drive it, stop, and return everything uvicorn's
    production handlers wrote. ``buf`` is handed to the scenario so it can
    wait on what has actually landed (e.g. the tunnel-end line) rather than
    on a fixed sleep."""
    port = _free_port()
    cfg = RelayConfig(bind_host="127.0.0.1", bind_port=port,
                      origin="relay.test")
    app = create_app(cfg)
    app.state.relay.registry.provision(DEVICE_TOKEN, HOME_ID)
    # uvicorn configures logging here, from the options production uses.
    config = uvicorn.Config(app, **uvicorn_options(cfg))
    buf = io.StringIO()
    handlers = {id(h): h for name in _UVICORN_LOGGERS
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
        await asyncio.wait_for(scenario(port, buf), timeout=_WAIT_S)
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        task.cancel()
    return buf.getvalue()


async def test_healthz_probes_write_no_access_line_but_tunnel_lines_still_print(
        restore_uvicorn_logging):
    """Both halves of #617, pinned together so a fix that silences the whole
    access log (not just /healthz) cannot pass by accident: a tunnel opens
    and closes while several health probes land on it."""
    statuses = []

    async def scenario(port, buf):
        ws = await _registered(port)
        async with httpx.AsyncClient() as client:
            # Fly's check runs every 15 s; three probes stand in for a run
            # of them landing while the tunnel is open.
            for _ in range(3):
                resp = await client.get(f"http://127.0.0.1:{port}{HEALTHZ_PATH}")
                statuses.append(resp.status_code)
        await ws.close(code=1000, reason="scenario done")
        # The tunnel-end line is written in /scope's finally block after the
        # close completes; wait for it rather than read the buffer mid-write.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _LINE_WAIT_S
        while "home tunnel ended" not in buf.getvalue() and loop.time() < deadline:
            await asyncio.sleep(0.02)

    log = await _serve_and_capture(scenario)

    # CONTROL: the probes were answered, not dropped before they could log.
    assert statuses == [200, 200, 200], statuses

    # Half 1: no access line for any of the three probes.
    assert not re.search(r'"GET ' + re.escape(HEALTHZ_PATH) + r' HTTP/', log), (
        "a /healthz probe reached the access log:\n" + log)
    assert "healthz" not in log.lower(), (
        "a /healthz probe reached the relay's log in some other form:\n" + log)

    # Half 2: tunnel connect and tunnel end still print.
    assert '"WebSocket /scope" [accepted]' in log, (
        "the tunnel connect line did not print:\n" + log)
    assert "home tunnel ended" in log, (
        "the tunnel end line did not print:\n" + log)
    assert f"home={HOME_ID}" in log and "close=1000" in log, log
    # Neither half of this test is an excuse to print the device token.
    assert DEVICE_TOKEN not in log, "the device token reached the relay's log"


async def test_an_unrelated_request_still_writes_an_access_line(
        restore_uvicorn_logging):
    """CONTROL for the filter's scope: it names /healthz, not "every
    request", so a request to any other path keeps writing its line exactly
    as #520's path-only formatter already covers. No home is registered, so
    the relay answers the request itself (404) -- same setup as
    test_h4_access_log_path_only.py's "no home is registered" cases."""
    async def scenario(port, buf):
        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}/h/nobody/api/status")

    log = await _serve_and_capture(scenario)
    assert re.search(r'"GET /h/nobody/api/status HTTP/', log), (
        "an unrelated request's access line went missing too -- the filter "
        "is not scoped to /healthz:\n" + log)


def test_the_rule_matches_only_a_bare_healthz_target():
    """``_is_healthz_access`` on its own: the exact 5-tuple shape uvicorn's
    access logger uses, with the target in each place a probe or something
    else could put it."""
    def access_args(target, method="GET", status=200):
        return ("127.0.0.1:1234", method, target, "1.1", status)

    assert _is_healthz_access(access_args(HEALTHZ_PATH)) is True
    # A query string would be unusual for a Fly health check, but the rule
    # does not depend on there being none.
    assert _is_healthz_access(access_args(HEALTHZ_PATH + "?x=1")) is True
    # A verb other than GET is not one the route accepts, but the rule still
    # answers by path alone (see _is_healthz_access's docstring).
    assert _is_healthz_access(access_args(HEALTHZ_PATH, method="POST")) is True
    # Untouched: a different path, and an exact-match rule rather than a
    # prefix match.
    assert _is_healthz_access(access_args("/h/home/healthz")) is False
    assert _is_healthz_access(access_args(HEALTHZ_PATH + "z")) is False
    # Untouched: anything that is not the 5-tuple shape the access logger
    # actually uses, answered False rather than guessed at.
    for untouched in (None, (), ("too", "few"), "not a tuple",
                      (1, 2, 3, 4, 5)):
        assert _is_healthz_access(untouched) is False
