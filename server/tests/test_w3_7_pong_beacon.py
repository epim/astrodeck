# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#606): the home's answer to a relay PING carries the status beacon.

The relay keeps the last ten beacons per home so an outage can be read after the
rig has gone dark. That needs the PONG to carry one, and the beacon leaves the
box, so it goes only if it is exactly the closed vocabulary: a source that
raises, or returns text outside the grammar, sends a plain PONG, never a
guess.

NAMED MUTANT: the client always omits the beacon (``_pong_header`` returns the
PING's header as it was). test_a_pong_carries_the_beacon turns red.
"""
from __future__ import annotations

import pytest

from astrodeck.remote.protocol import Frame, FrameType, decode_frame
from astrodeck.remote.relay_client import RelayClient

GOOD = "v=1 state=idle frames=0 level=none up=5 log=0 pending=no boot=normal"


def _client(source) -> RelayClient:
    return RelayClient(lambda *a: None, lambda: None, beacon_source=source)


async def _answer(client: RelayClient, header: dict) -> dict:
    sent: list[bytes] = []

    async def capture(data: bytes) -> None:
        sent.append(data)

    client._raw_send = capture
    await client._dispatch(Frame(FrameType.PING, 0, header))
    (raw,) = sent
    frame = decode_frame(raw)
    assert frame.type == FrameType.PONG
    return frame.header


async def test_a_pong_carries_the_beacon():
    header = await _answer(_client(lambda: GOOD), {"ts": 12.5})
    assert header == {"ts": 12.5, "beacon": GOOD}


async def test_a_client_with_no_source_sends_the_plain_pong():
    assert await _answer(_client(None), {"ts": 12.5}) == {"ts": 12.5}


async def test_a_source_that_raises_sends_a_plain_pong():
    def boom() -> str:
        raise RuntimeError("lat 11.1111 lon 22.2222")

    assert await _answer(_client(boom), {"ts": 1}) == {"ts": 1}


@pytest.mark.parametrize("text", ["lat 11.1111 lon 22.2222", "", None, 7,
                                  GOOD + " extra",
                                  "v=1 state=idle frames=0 level=none up=5 boot=Fictional Hilltop"])
async def test_text_outside_the_grammar_is_not_sent(text):
    assert await _answer(_client(lambda: text), {"ts": 1}) == {"ts": 1}


async def test_a_beacon_in_the_relays_own_ping_is_not_echoed_back():
    header = await _answer(_client(None), {"ts": 1, "beacon": "echo me"})
    assert "beacon" not in header


def test_the_app_hands_the_dispatchers_beacon_source_to_the_relay_client(
        tmp_path, monkeypatch):
    """A beacon the client could send and nothing hands it is a claim nothing
    keeps: the lifespan must pass the source the dead-man ping already uses."""
    from fastapi.testclient import TestClient

    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.remote.relay_client as rc
    from astrodeck.auth import reset_active_provider
    from astrodeck.config import ConfigStore

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    for module in (config_mod, hub_mod, app_module):
        monkeypatch.setattr(module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    seen: dict = {}

    async def fake_run(app, provider, beacon_source=None):
        seen["source"] = beacon_source

    monkeypatch.setattr(rc, "run_relay_client", fake_run)
    try:
        with TestClient(app_module.create_app()):
            pass
    finally:
        reset_active_provider()
    assert seen["source"] is not None
    assert seen["source"] is app_module.dispatcher.beacon_source
