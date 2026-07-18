"""Native (direct Alpaca) backend: role->dev_type mapping, per-endpoint
connection POOL, and close-aclose-every-connection contract (Stage A, W1.2/W1.9).

These tests WRAP-check only -- they monkeypatch ``alpaca.AlpacaConnection`` and
``alpaca.DEVICE_CLASSES`` with in-process fakes (NOT unittest.mock, per the repo
convention) so no real network I/O happens, and assert the NativeBackend /
NativeSession honor the reshaped contract: roles EXCLUDE guider, two roles on one
endpoint reuse ONE connection while a different host opens a second, get_device
sets dev.role + awaits connect(), and close() aclose's EVERY owned connection.
"""
import pytest

from astrodeck.devices import alpaca
from astrodeck.devices.backend import (
    Backend,
    BackendSession,
    ConnSpec,
)
from astrodeck.devices.backends.native_backend import (
    NativeBackend,
    NativeSession,
)


# ----------------------------------------------------------------------- fakes

class FakeConnection:
    """Stand-in for ``alpaca.AlpacaConnection``: records its endpoint and whether
    it was aclose'd, with no httpx client. One instance per (host, port) the pool
    creates."""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.closed = False
        FakeConnection.instances.append(self)

    async def close(self):
        self.closed = True


class FakeAlpacaDevice:
    """Stand-in for an Alpaca device class instance: stores the SHARED connection
    it was built against, records connect(), and exposes role/dev_num/name like
    the real ``_AlpacaDevice``."""

    def __init__(self, conn, dev_num, name):
        self.conn = conn
        self.dev_num = dev_num
        self.name = name
        self.role = ""
        self.connected = False

    async def connect(self):
        self.connected = True


@pytest.fixture
def fake_alpaca(monkeypatch):
    """Replace AlpacaConnection with FakeConnection and every DEVICE_CLASSES entry
    with FakeAlpacaDevice, so the session builds against fakes with no network."""
    FakeConnection.instances = []
    monkeypatch.setattr(alpaca, "AlpacaConnection", FakeConnection)
    fake_classes = {k: FakeAlpacaDevice for k in alpaca.DEVICE_CLASSES}
    monkeypatch.setattr(alpaca, "DEVICE_CLASSES", fake_classes)
    return FakeConnection


# ----------------------------------------------------------------- contract

def test_backend_identity_and_protocols():
    b = NativeBackend()
    assert b.name == "native"
    assert b.label == "Native (direct)"
    # roles EXCLUDE guider (Alpaca has no guider device); safety stays;
    # guide_camera IS advertised (P2-T3 fix round, D6 — a second Alpaca camera
    # by dev_num, so the assignment UI can put a guide camera on a real rig).
    assert b.roles == ("camera", "telescope", "focuser", "filterwheel",
                       "switch", "safety", "rotator", "guide_camera")
    assert "guider" not in b.roles
    assert b.discoverable is True
    assert b.hostless is False
    assert isinstance(b, Backend)


def test_roles_do_not_advertise_guider():
    """The W1.C picker must never offer a native guider that can never connect."""
    assert "guider" not in NativeBackend().roles


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
        ("guide_camera", "camera"),   # D6: a dedicated guide cam IS an Alpaca camera
    ],
)
async def test_get_device_maps_role_to_dev_type(fake_alpaca, role, dev_type):
    s = NativeSession("host-from-open")
    conn = ConnSpec(backend="native", host="10.0.0.5", port=11111,
                    dev_type=None, dev_num=3, role=role)
    dev = await s.get_device(role, conn)
    # the fake device was built against a connection at the role's endpoint,
    # has its role set, and was connected.
    assert isinstance(dev, FakeAlpacaDevice)
    assert dev.dev_num == 3
    assert dev.name == role
    assert dev.role == role
    assert dev.connected is True
    assert dev.conn.host == "10.0.0.5"
    assert dev.conn.port == 11111


@pytest.mark.asyncio
async def test_guider_role_has_no_alpaca_device(fake_alpaca):
    s = NativeSession("h")
    with pytest.raises(KeyError):
        await s.get_device("guider", ConnSpec(backend="native", role="guider"))
    # never built a connection for the unmapped role.
    assert FakeConnection.instances == []


@pytest.mark.asyncio
async def test_get_device_is_lazy_and_cached(fake_alpaca):
    s = NativeSession("h")
    conn = ConnSpec(backend="native", host="h", port=11111, dev_num=0,
                    role="camera")
    first = await s.get_device("camera", conn)
    second = await s.get_device("camera", conn)
    assert first is second
    # built once -> one connection; the device is served from cache thereafter.
    assert len(FakeConnection.instances) == 1


@pytest.mark.asyncio
async def test_get_device_falls_back_to_session_host(fake_alpaca):
    s = NativeSession("session-host")
    conn = ConnSpec(backend="native", host=None, port=11111, dev_num=0,
                    role="camera")
    dev = await s.get_device("camera", conn)
    assert dev.conn.host == "session-host"


# ----------------------------------------- per-endpoint connection pool (W1.2)

@pytest.mark.asyncio
async def test_two_roles_same_endpoint_reuse_one_connection(fake_alpaca):
    s = NativeSession("h")
    cam = await s.get_device(
        "camera", ConnSpec(backend="native", host="10.0.0.5", port=11111,
                           dev_num=0, role="camera"))
    foc = await s.get_device(
        "focuser", ConnSpec(backend="native", host="10.0.0.5", port=11111,
                            dev_num=1, role="focuser"))
    # ONE connection shared by both roles on the same host:port.
    assert len(FakeConnection.instances) == 1
    assert cam.conn is foc.conn


@pytest.mark.asyncio
async def test_different_host_opens_second_connection(fake_alpaca):
    s = NativeSession("h")
    cam = await s.get_device(
        "camera", ConnSpec(backend="native", host="10.0.0.1", port=11111,
                           dev_num=0, role="camera"))
    tel = await s.get_device(
        "telescope", ConnSpec(backend="native", host="10.0.0.2", port=11111,
                              dev_num=0, role="telescope"))
    # different host -> two distinct connections.
    assert len(FakeConnection.instances) == 2
    assert cam.conn is not tel.conn


# ------------------------------------------------------ native_*/health/close

@pytest.mark.asyncio
async def test_native_guider_solver_guide_camera_are_none():
    s = NativeSession("h")
    assert s.native_guider() is None
    assert s.native_solver() is None
    assert s.guide_camera() is None


@pytest.mark.asyncio
async def test_native_guider_built_over_guide_camera_and_mount(fake_alpaca):
    """P2-T3 fix round (D6): once the session has connected a guide_camera +
    telescope, native_guider() returns the Rust-engine NativeGuider over them
    (wheel present; None when absent) and guide_camera() returns the assigned
    device — the first-class real-rig guiding seam."""
    from astrodeck.providers import NATIVE_AVAILABLE

    s = NativeSession("h")
    gcam = await s.get_device(
        "guide_camera", ConnSpec(backend="native", host="h", port=11111,
                                 dev_num=1, role="guide_camera"))
    tel = await s.get_device(
        "telescope", ConnSpec(backend="native", host="h", port=11111,
                              dev_num=0, role="telescope"))
    assert s.guide_camera() is gcam
    g = s.native_guider()
    if not NATIVE_AVAILABLE:
        assert g is None
        return
    from astrodeck.guide import NativeGuider
    assert isinstance(g, NativeGuider)
    assert g.cam is gcam and g.tel is tel
    assert s.native_guider() is g               # cached / same instance


@pytest.mark.asyncio
async def test_health_reports_per_host(fake_alpaca):
    s = NativeSession("h")
    await s.get_device("camera", ConnSpec(backend="native", host="10.0.0.5",
                                          port=11111, dev_num=0, role="camera"))
    h = await s.health()
    assert h["backend"] == "native"
    assert h["ok"] is True
    assert h["hosts"] == ["10.0.0.5:11111"]


@pytest.mark.asyncio
async def test_close_acloses_every_owned_connection(fake_alpaca):
    """close() must aclose EVERY owned AlpacaConnection (one per endpoint) so a
    profile switch leaks no httpx client -- impossible against the committed code
    where close() only cleared a dict and make_device hid the connection."""
    s = NativeSession("h")
    await s.get_device("camera", ConnSpec(backend="native", host="10.0.0.1",
                                          port=11111, dev_num=0, role="camera"))
    await s.get_device("telescope", ConnSpec(backend="native", host="10.0.0.2",
                                             port=11111, dev_num=0, role="telescope"))
    conns = list(FakeConnection.instances)
    assert len(conns) == 2
    await s.close()
    assert all(c.closed for c in conns)         # EVERY connection aclose'd
    # caches dropped: a new get_device rebuilds a fresh connection.
    await s.get_device("camera", ConnSpec(backend="native", host="10.0.0.1",
                                          port=11111, dev_num=0, role="camera"))
    assert len(FakeConnection.instances) == 3


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
