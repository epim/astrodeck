"""PHD2 guider backend adapter (Stage A) -- GUIDER ONLY.

These tests pin the WRAPPER contract: the backend connects a ``PHD2Guider`` and
exposes it as the "guider" role device + native guider, with a sane health dict.
We monkeypatch ``PHD2Guider`` with a fake so no real PHD2 socket is touched, and
assert the Stage-A shape (no behavior change to the guider itself).
"""
import pytest

from astrodeck.devices.backend import (
    BACKENDS,
    Backend,
    BackendSession,
    ConnSpec,
    get_backend,
)
from astrodeck.devices.backends import phd2_backend


# ----------------------------------------------------------------------- fakes

class FakeGuideStats:
    """Stand-in for guide.base.GuideStats: a dataclass-like object with
    ``__dict__`` so the session's health() can serialize it the same way."""

    def __init__(self) -> None:
        self.guiding = True
        self.rms_ra = 0.4
        self.rms_dec = 0.3
        self.snr = 12.5


class FakePHD2Guider:
    """A fake PHD2Guider recording its ctor args and connect/disconnect calls."""

    last_instance: "FakePHD2Guider | None" = None

    def __init__(self, host="127.0.0.1", port=4400, pixel_scale_arcsec=2.0):
        self.host = host
        self.port = port
        self.pixel_scale = pixel_scale_arcsec
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        FakePHD2Guider.last_instance = self

    async def connect(self):
        self.connected = True
        self.connect_calls += 1

    async def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1

    def stats(self):
        return FakeGuideStats()


@pytest.fixture
def fake_phd2(monkeypatch):
    """Patch the symbol the backend imports inside open()
    (``astrodeck.guide.phd2.PHD2Guider``) with the fake."""
    monkeypatch.setattr("astrodeck.guide.phd2.PHD2Guider", FakePHD2Guider)
    FakePHD2Guider.last_instance = None
    return FakePHD2Guider


# ----------------------------------------------------------------------- tests

def test_backend_is_registered_and_shape():
    b = get_backend("phd2")
    assert b is phd2_backend.PHD2_BACKEND
    assert isinstance(b, Backend)
    assert b.name == "phd2"
    assert b.label == "PHD2"
    assert b.roles == ("guider",)
    # Discoverable since 2026-07-30: PHD2's SOCKET is not UDP-discoverable,
    # but its BINARY is findable, and nothing else ever suggested PHD2 —
    # the only route was knowing to hand-add a driver with host and port.
    assert b.discoverable is True
    # endpoint-less in unmanaged-local mode: the orchestrator normalizes its
    # host/port -> None so a stray-addressed phd2-local guider override resolves.
    assert b.hostless is True
    # listed in the registry summary too
    assert "phd2" in BACKENDS


@pytest.mark.asyncio
async def test_guide_camera_is_none(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    assert session.guide_camera() is None


@pytest.mark.asyncio
async def test_open_connects_guider_with_default_address(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    assert isinstance(session, BackendSession)
    g = fake_phd2.last_instance
    assert g is not None
    # defaulted endpoint + connected exactly once
    assert (g.host, g.port) == ("127.0.0.1", 4400)
    assert g.connected is True
    assert g.connect_calls == 1


@pytest.mark.asyncio
async def test_open_honors_conn_host_port_and_extra_scale(fake_phd2):
    conn = ConnSpec(backend="phd2", host="10.0.0.9", port=4500,
                    extra={"pixel_scale_arcsec": 1.7})
    await get_backend("phd2").open(conn)
    g = fake_phd2.last_instance
    assert (g.host, g.port) == ("10.0.0.9", 4500)
    assert g.pixel_scale == 1.7


@pytest.mark.asyncio
async def test_native_guider_and_get_device_return_same_guider(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    g = fake_phd2.last_instance
    assert session.native_guider() is g
    got = await session.get_device("guider", ConnSpec(backend="phd2"))
    assert got is g
    # native_solver is None; the hub picks a solver itself
    assert session.native_solver() is None


@pytest.mark.asyncio
async def test_get_device_rejects_non_guider_role(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    with pytest.raises(KeyError):
        await session.get_device("camera", ConnSpec(backend="phd2"))


@pytest.mark.asyncio
async def test_health_reports_connected_and_stats(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    h = await session.health()
    assert h is not None
    assert h["connected"] is True
    assert h["stats"]["guiding"] is True
    assert h["stats"]["snr"] == 12.5


@pytest.mark.asyncio
async def test_close_disconnects_guider_without_killing_external_app(fake_phd2):
    session = await get_backend("phd2").open(ConnSpec(backend="phd2"))
    g = fake_phd2.last_instance
    await session.close()
    assert g.disconnect_calls == 1
    assert g.connected is False


@pytest.mark.asyncio
async def test_discover_is_empty():
    assert await get_backend("phd2").discover() == []


# ------------------------------------------------- offered when installed

def test_discover_offers_phd2_when_the_binary_is_installed(monkeypatch):
    """One tap instead of knowing to type 127.0.0.1:4400 by hand."""
    import asyncio

    from astrodeck import drivers
    from astrodeck.devices.backends.phd2_backend import PHD2_BACKEND

    monkeypatch.setattr(drivers, "phd2_installed_at", lambda: r"C:\PHD2\phd2.exe")
    found = asyncio.run(PHD2_BACKEND.discover())
    assert len(found) == 1
    assert found[0]["role"] == "guider"
    assert found[0]["port"] == 4400
    # NOT verified: finding the exe proves installed, not RUNNING. The socket
    # exists only while the app is open, so the probe still decides that.
    assert found[0]["verified"] is False


def test_discover_offers_nothing_when_phd2_is_absent(monkeypatch):
    import asyncio

    from astrodeck import drivers
    from astrodeck.devices.backends.phd2_backend import PHD2_BACKEND

    monkeypatch.setattr(drivers, "phd2_installed_at", lambda: None)
    assert asyncio.run(PHD2_BACKEND.discover()) == []


def test_an_installed_but_closed_phd2_says_so(monkeypatch):
    """"connection failed" reads as broken. PHD2 being closed is the NORMAL
    state of a perfectly good install, and the fix is to launch it."""
    import asyncio

    from astrodeck import drivers

    monkeypatch.setattr(drivers, "phd2_installed_at", lambda: r"C:\PHD2\phd2.exe")
    res = asyncio.run(drivers._probe_phd2("127.0.0.1", 59999))
    assert res["reachable"] is False
    assert "not running" in res["error"], res["error"]
    assert "start it" in res["error"].lower()


def test_a_remote_phd2_does_not_claim_a_local_install(monkeypatch):
    """The binary on THIS machine says nothing about a host across the LAN."""
    import asyncio

    from astrodeck import drivers

    monkeypatch.setattr(drivers, "phd2_installed_at", lambda: r"C:\PHD2\phd2.exe")
    res = asyncio.run(drivers._probe_phd2("192.0.2.1", 4400))
    assert res["reachable"] is False
    assert "not running" not in (res["error"] or "")
