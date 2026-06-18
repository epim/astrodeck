"""§T7(5) device registration + generation fencing (W3.3.4).

A ``HELLO{device_token}`` pins ``home_id``; a duplicate token with a HIGHER
``generation`` evicts the older socket, a LOWER one is rejected; a bad token is
refused. Also drives the ScopeConnection handshake + keepalive + teardown."""
from __future__ import annotations

import pytest

from relay import protocol
from relay.connection import ScopeConnection
from relay.registry import HomeRegistry, RegistrationError

from conftest import FakeScopeTunnel


def _registry():
    return HomeRegistry(token_to_home={"tok-home1": "home-1", "tok-home2": "home-2"})


def test_valid_hello_registers_and_acks():
    reg = _registry()
    tunnel = FakeScopeTunnel()
    conn = ScopeConnection(reg, tunnel)
    ack = conn.handle_hello(protocol.hello("tok-home1", "home-1", generation=1))
    assert ack.type == protocol.FrameType.HELLO_ACK
    assert ack.header["ok"] is True
    assert ack.header["endpoint"] == "/h/home-1/"
    assert reg.get("home-1") is not None
    assert reg.get("home-1").generation == 1


def test_bad_token_refused():
    reg = _registry()
    conn = ScopeConnection(reg, FakeScopeTunnel())
    ack = conn.handle_hello(protocol.hello("WRONG", "home-1", 1))
    assert ack.header["ok"] is False
    assert "unknown device_token" in ack.header["reason"]
    assert reg.get("home-1") is None


def test_token_authoritative_over_claimed_home_id():
    """The token decides the home_id; a HELLO claiming a different home_id can't
    hijack another home (the provisioned mapping wins)."""
    reg = _registry()
    conn = ScopeConnection(reg, FakeScopeTunnel())
    # tok-home1 maps to home-1; claim home-2 -> still pinned to home-1.
    conn.handle_hello(protocol.hello("tok-home1", "home-2", 1))
    assert reg.get("home-1") is not None
    assert reg.get("home-2") is None


def test_higher_generation_evicts_older():
    reg = _registry()
    t1 = FakeScopeTunnel("t1")
    t2 = FakeScopeTunnel("t2")
    c1 = ScopeConnection(reg, t1)
    c2 = ScopeConnection(reg, t2)
    c1.handle_hello(protocol.hello("tok-home1", "home-1", generation=1))
    ack2 = c2.handle_hello(protocol.hello("tok-home1", "home-1", generation=2))
    assert ack2.header["ok"] is True
    assert reg.get("home-1").tunnel is t2          # newer wins
    assert t1.evicted is True and t1.closed is True  # older fenced


def test_equal_or_lower_generation_rejected():
    reg = _registry()
    t1 = FakeScopeTunnel("t1")
    c1 = ScopeConnection(reg, t1)
    c1.handle_hello(protocol.hello("tok-home1", "home-1", generation=5))
    # equal
    ack_eq = ScopeConnection(reg, FakeScopeTunnel()).handle_hello(
        protocol.hello("tok-home1", "home-1", generation=5))
    assert ack_eq.header["ok"] is False
    # lower
    ack_lo = ScopeConnection(reg, FakeScopeTunnel()).handle_hello(
        protocol.hello("tok-home1", "home-1", generation=3))
    assert ack_lo.header["ok"] is False
    # the original is still the live tunnel.
    assert reg.get("home-1").tunnel is t1


def test_non_hello_first_frame_rejected():
    reg = _registry()
    conn = ScopeConnection(reg, FakeScopeTunnel())
    ack = conn.handle_hello(protocol.ping(1.0))
    assert ack.header["ok"] is False
    assert "HELLO" in ack.header["reason"]


def test_incompatible_proto_version_rejected():
    reg = _registry()
    conn = ScopeConnection(reg, FakeScopeTunnel())
    bad = protocol.hello("tok-home1", "home-1", 1,
                         proto_version=protocol.PROTO_VERSION + 99)
    ack = conn.handle_hello(bad)
    assert ack.header["ok"] is False
    assert "proto_version" in ack.header["reason"]


async def test_pong_updates_liveness_and_ping_built():
    reg = _registry()
    tunnel = FakeScopeTunnel()
    conn = ScopeConnection(reg, tunnel)
    conn.handle_hello(protocol.hello("tok-home1", "home-1", 1))
    # A PONG must update liveness (no reply).
    reply = await conn.on_frame(protocol.pong(1.0))
    assert reply is None
    # A PING from the home is answered with a PONG.
    reply = await conn.on_frame(protocol.ping(1.0))
    assert reply is not None and reply.type == protocol.FrameType.PONG
    # make_ping builds a control-stream PING.
    ping = conn.make_ping()
    assert ping.type == protocol.FrameType.PING
    assert ping.stream_id == protocol.CONTROL_STREAM_ID


async def test_frame_before_hello_errors():
    reg = _registry()
    conn = ScopeConnection(reg, FakeScopeTunnel())
    with pytest.raises(Exception):
        await conn.on_frame(protocol.resp_head(1, 200, []))


async def test_close_unregisters_only_if_still_live():
    """A connection evicted by a newer generation must NOT clobber the winner
    when it closes (the unregister is identity-guarded)."""
    reg = _registry()
    t1 = FakeScopeTunnel("t1")
    t2 = FakeScopeTunnel("t2")
    c1 = ScopeConnection(reg, t1)
    c2 = ScopeConnection(reg, t2)
    c1.handle_hello(protocol.hello("tok-home1", "home-1", 1))
    c2.handle_hello(protocol.hello("tok-home1", "home-1", 2))  # evicts c1
    await c1.close()                       # the evicted one closes
    assert reg.get("home-1").tunnel is t2  # winner survives


def test_unknown_token_validate_raises():
    reg = _registry()
    with pytest.raises(RegistrationError):
        reg.validate_token("nope", "home-1")
