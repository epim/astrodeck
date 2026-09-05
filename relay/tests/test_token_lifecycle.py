"""OPEN-002: device-token rotation, revocation, and file reload.

A leaked home device token must be invalidatable WITHOUT a full relay restart,
and a planned rotation must swap the accepted token without dropping the live
tunnel. Tokens are never echoed in errors, logs, or registration state.
"""
from __future__ import annotations

import pytest

from relay import protocol
from relay.config import (
    new_device_token,
    reload_device_tokens,
    valid_device_token,
)
from relay.connection import ScopeConnection
from relay.registry import HomeRegistry, RegistrationError

from conftest import FakeScopeTunnel

GOOD = "tok-home1-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"   # >= 32 printable ASCII
GOOD2 = "tok-home2-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NEWTOK = "rotated-cccccccccccccccccccccccccccccccc"


def _registry():
    return HomeRegistry(token_to_home={GOOD: "home-1", GOOD2: "home-2"})


def _live(reg, token, home, generation=1):
    tunnel = FakeScopeTunnel(home)
    ScopeConnection(reg, tunnel).handle_hello(
        protocol.hello(token, home, generation=generation))
    return tunnel


# -------------------------------------------------------------- revoke

def test_revoke_removes_mapping_and_evicts_live_tunnel():
    reg = _registry()
    t1 = _live(reg, GOOD, "home-1")
    assert reg.get("home-1") is not None

    assert reg.revoke(GOOD) is True
    # the token no longer authenticates anything
    with pytest.raises(RegistrationError):
        reg.validate_token(GOOD, "home-1")
    # and the home is fenced at the REGISTRY layer (flags + dropped from the
    # table). These flags alone do not close the socket: that is
    # RelayState.evict_home, covered by the teardown test at the end of file.
    assert t1.evicted is True and t1.closed is True
    assert reg.get("home-1") is None


def test_revoke_unknown_token_is_a_noop_returning_false():
    reg = _registry()
    t1 = _live(reg, GOOD, "home-1")
    assert reg.revoke("never-provisioned-token-zzzzzzzzzzzz") is False
    # the live home is untouched
    assert reg.get("home-1") is not None and t1.evicted is False


# -------------------------------------------------------------- rotate

def test_rotate_swaps_token_and_keeps_live_tunnel():
    reg = _registry()
    t1 = _live(reg, GOOD, "home-1")

    assert reg.rotate(GOOD, NEWTOK) == "home-1"
    # old dies, new works
    with pytest.raises(RegistrationError):
        reg.validate_token(GOOD, "home-1")
    assert reg.validate_token(NEWTOK, "home-1") == "home-1"
    # a planned rotation does NOT disrupt the live tunnel
    assert reg.get("home-1").tunnel is t1
    assert t1.evicted is False and t1.closed is False


def test_rotate_rejects_bad_new_token_atomically():
    reg = _registry()
    with pytest.raises(RegistrationError):
        reg.rotate(GOOD, "too-short")
    # the old token is still valid (no partial mutation)
    assert reg.validate_token(GOOD, "home-1") == "home-1"


def test_rotate_unknown_old_token_raises_and_adds_nothing():
    reg = _registry()
    with pytest.raises(RegistrationError):
        reg.rotate("unknown-old-token-xxxxxxxxxxxxxxxxxx", NEWTOK)
    with pytest.raises(RegistrationError):
        reg.validate_token(NEWTOK, "home-1")


def test_rotate_cannot_steal_another_homes_token():
    reg = _registry()
    with pytest.raises(RegistrationError):
        reg.rotate(GOOD, GOOD2)   # GOOD2 already belongs to home-2
    # both original bindings intact
    assert reg.validate_token(GOOD, "home-1") == "home-1"
    assert reg.validate_token(GOOD2, "home-2") == "home-2"


# -------------------------------------------------------------- replace_tokens

def test_replace_tokens_evicts_homes_whose_token_vanished():
    reg = _registry()
    t1 = _live(reg, GOOD, "home-1")
    t2 = _live(reg, GOOD2, "home-2")

    evicted = reg.replace_tokens({GOOD: "home-1"})   # home-2 dropped
    assert evicted == ["home-2"]
    assert reg.get("home-2") is None
    assert t2.evicted is True and t2.closed is True
    # home-1 survives untouched
    assert reg.get("home-1").tunnel is t1
    assert t1.evicted is False


def test_replace_tokens_keeps_home_if_any_token_remains():
    reg = _registry()
    reg.provision(NEWTOK, "home-1")   # home-1 now has GOOD and NEWTOK
    t1 = _live(reg, GOOD, "home-1")
    evicted = reg.replace_tokens({NEWTOK: "home-1", GOOD2: "home-2"})
    assert "home-1" not in evicted
    assert reg.get("home-1").tunnel is t1 and t1.evicted is False


def test_replace_tokens_rejects_invalid_map_without_mutating():
    reg = _registry()
    with pytest.raises(RegistrationError):
        reg.replace_tokens({"short": "home-1"})
    # the pre-existing map is untouched
    assert reg.validate_token(GOOD, "home-1") == "home-1"


# -------------------------------------------------------------- file reload

def test_reload_device_tokens_applies_file_and_reports_counts(tmp_path):
    reg = _registry()
    t1 = _live(reg, GOOD, "home-1")
    _live(reg, GOOD2, "home-2")

    f = tmp_path / "tokens.json"
    f.write_text('{"%s": "home-1"}' % GOOD, encoding="utf-8")  # drops home-2

    summary = reload_device_tokens(reg, str(f))
    assert summary["homes"] == 1
    assert summary["evicted"] == ["home-2"]
    assert reg.get("home-2") is None
    assert reg.get("home-1").tunnel is t1
    # the summary must NOT leak any token material
    assert GOOD not in repr(summary) and GOOD2 not in repr(summary)


# -------------------------------------------------------------- redaction

def test_registration_state_never_contains_the_token():
    reg = _registry()
    _live(reg, GOOD, "home-1")
    assert GOOD not in repr(reg.get("home-1"))


def test_lifecycle_errors_never_echo_the_token():
    reg = _registry()
    # validate: unknown token is not echoed back
    with pytest.raises(RegistrationError) as ei:
        reg.validate_token("leaky-secret-token-abcdefghijklmnop", "home-1")
    assert "leaky-secret" not in str(ei.value)
    # rotate: a bad new token is not echoed back
    with pytest.raises(RegistrationError) as ej:
        reg.rotate(GOOD, "leaky-bad-new-token")
    assert "leaky-bad-new" not in str(ej.value)


# -------------------------------------------------------------- helpers

def test_new_device_token_is_conforming_and_unique():
    a, b = new_device_token(), new_device_token()
    assert a != b
    assert valid_device_token(a) and valid_device_token(b)


def test_valid_device_token_bounds():
    assert not valid_device_token("short")
    assert not valid_device_token("x" * 300)
    assert not valid_device_token("has space " + "y" * 30)
    assert valid_device_token("y" * 40)


# ------------------------------------------ re-review 2026-09-05: real teardown

import asyncio

from relay.config import RelayConfig
from relay.server import RelayState


class ClosingTunnel(FakeScopeTunnel):
    """A fake tunnel that records a PHYSICAL close, like the real WSS wrapper
    does. The plain FakeScopeTunnel only carries flags, which is exactly how
    the incomplete eviction slipped past the first tests."""

    def __init__(self, conn_id: str = "fake"):
        super().__init__(conn_id)
        self.closed_with: int | None = None

    async def close_socket(self, code: int = 1012) -> None:
        self.closed_with = code
        self.closed = True


async def test_reload_tears_down_the_live_socket_and_routing(tmp_path, monkeypatch):
    """The registry alone only refuses a re-HELLO. Browser traffic resolves the
    home through RelayState.connections and the WSS stays open, so a revoked
    token would keep the session it already holds. The reload must close the
    socket and drop the affinity."""
    f = tmp_path / "tokens.json"
    f.write_text('{"%s": "home-1"}' % GOOD, encoding="utf-8")
    monkeypatch.setenv("RELAY_DEVICE_TOKENS_FILE", str(f))
    state = RelayState(RelayConfig(bind_host="127.0.0.1", origin="relay.test"))

    tunnel = ClosingTunnel("t1")
    conn = ScopeConnection(state.registry, tunnel)
    ack = conn.handle_hello(protocol.hello(GOOD, "home-1", generation=1))
    assert ack.header["ok"] is True
    state.connections["home-1"] = conn  # what _scope_endpoint does on HELLO_ACK

    f.write_text("{}", encoding="utf-8")  # the operator removes the token
    summary = state.reload_tokens()
    await asyncio.gather(*state._evict_tasks)

    assert summary["evicted"] == ["home-1"]
    assert "home-1" not in state.connections      # no longer routable
    assert tunnel.closed_with == 1008             # socket physically closed
    assert state.registry.get("home-1") is None   # and fenced at the registry


async def test_evict_home_is_a_noop_for_an_unknown_home():
    state = RelayState(RelayConfig(bind_host="127.0.0.1", origin="relay.test"))
    assert await state.evict_home("never-connected") is False
