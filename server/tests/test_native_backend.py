"""Native (direct Alpaca) backend: role->dev_type mapping + contract shape.

These tests WRAP-check only -- they monkeypatch ``alpaca.make_device`` so no
real network I/O happens, and assert the NativeBackend/NativeSession honor the
pluggable contract (roles, lazy+cached device creation, native_guider/solver
None, health ok, discover delegates to alpaca.discover).
"""
import pytest

from astrodeck.devices import alpaca
from astrodeck.devices.backend import (
    ROLES,
    Backend,
    BackendSession,
    ConnSpec,
)
from astrodeck.devices.backends.native_backend import (
    NativeBackend,
    NativeSession,
)


@pytest.fixture
def fake_make_device(monkeypatch):
    """Replace alpaca.make_device with a recorder that returns a marker dict and
    captures every (host, port, dev_type, dev_num, name) it is called with."""
    calls = []

    def _fake(host, port, dev_type, dev_num, name):
        calls.append(
            {"host": host, "port": port, "dev_type": dev_type,
             "dev_num": dev_num, "name": name}
        )
        return {"marker": dev_type, "host": host}

    monkeypatch.setattr(alpaca, "make_device", _fake)
    return calls


# ----------------------------------------------------------------- contract

def test_backend_identity_and_protocols():
    b = NativeBackend()
    assert b.name == "native"
    assert b.label == "Native (direct)"
    assert b.roles == ROLES
    assert b.discoverable is True
    assert isinstance(b, Backend)


@pytest.mark.asyncio
async def test_open_returns_session_bound_to_host():
    b = NativeBackend()
    s = await b.open(ConnSpec(backend="native", host="10.0.0.5"))
    assert isinstance(s, NativeSession)
    assert isinstance(s, BackendSession)
    assert s.name == "native"
    assert s.host == "10.0.0.5"


# ---------------------------------------------------- role -> dev_type map

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, dev_type",
    [
        ("camera", "camera"),
        ("telescope", "telescope"),
        ("focuser", "focuser"),
        ("filterwheel", "filterwheel"),
        ("switch", "switch"),
        ("safety", "safetymonitor"),
    ],
)
async def test_get_device_maps_role_to_dev_type(fake_make_device, role, dev_type):
    s = NativeSession("host-from-open")
    conn = ConnSpec(backend="native", host="10.0.0.5", port=11111,
                    dev_type=None, dev_num=3, role=role)
    dev = await s.get_device(role, conn)
    assert dev == {"marker": dev_type, "host": "10.0.0.5"}
    assert len(fake_make_device) == 1
    call = fake_make_device[0]
    assert call["host"] == "10.0.0.5"
    assert call["port"] == 11111
    assert call["dev_type"] == dev_type
    assert call["dev_num"] == 3
    assert call["name"] == role


@pytest.mark.asyncio
async def test_guider_role_has_no_alpaca_device(fake_make_device):
    s = NativeSession("h")
    with pytest.raises(KeyError):
        await s.get_device("guider", ConnSpec(backend="native", role="guider"))
    assert fake_make_device == []  # never reached make_device


@pytest.mark.asyncio
async def test_get_device_is_lazy_and_cached(fake_make_device):
    s = NativeSession("h")
    conn = ConnSpec(backend="native", host="h", port=11111, dev_num=0,
                    role="camera")
    first = await s.get_device("camera", conn)
    second = await s.get_device("camera", conn)
    assert first is second
    assert len(fake_make_device) == 1  # created once, then served from cache


@pytest.mark.asyncio
async def test_get_device_falls_back_to_session_host(fake_make_device):
    s = NativeSession("session-host")
    conn = ConnSpec(backend="native", host=None, port=11111, dev_num=0,
                    role="camera")
    await s.get_device("camera", conn)
    assert fake_make_device[0]["host"] == "session-host"


# ------------------------------------------------------ native_*/health/close

@pytest.mark.asyncio
async def test_native_guider_and_solver_are_none():
    s = NativeSession("h")
    assert s.native_guider() is None
    assert s.native_solver() is None


@pytest.mark.asyncio
async def test_health_reports_ok():
    s = NativeSession("h")
    assert await s.health() == {"backend": "native", "ok": True}


@pytest.mark.asyncio
async def test_close_clears_cache(fake_make_device):
    s = NativeSession("h")
    conn = ConnSpec(backend="native", host="h", port=1, dev_num=0, role="camera")
    await s.get_device("camera", conn)
    await s.close()
    # after close, a new get_device rebuilds (cache was dropped)
    await s.get_device("camera", conn)
    assert len(fake_make_device) == 2


# ------------------------------------------------------------------ discover

@pytest.mark.asyncio
async def test_discover_delegates_to_alpaca(monkeypatch):
    sentinel = [{"address": "1.2.3.4", "port": 11111, "devices": []}]

    async def _fake_discover(*a, **k):
        return sentinel

    monkeypatch.setattr(alpaca, "discover", _fake_discover)
    out = await NativeBackend().discover()
    assert out is sentinel


# ------------------------------------------------------- self-registration

def test_self_registers_in_registry():
    from astrodeck.devices.backend import get_backend

    b = get_backend("native")
    assert b.name == "native"
    assert b.label == "Native (direct)"
