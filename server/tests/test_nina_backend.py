"""NINA backend adapter (Stage A): the wrapper around ``build_nina_rig``.

These tests pin the WRAPPER contract, not NINA itself -- they monkeypatch
``build_nina_rig`` to a fake rig (no HTTP, no real NINA) and assert that
``open`` -> ``NinaSession`` surfaces the right device/guider/health, and that
the backend metadata + registration match the spec. The real NINA device paths
are covered by ``test_nina.py``.
"""
import pytest

import astrodeck.devices.nina as nina_module
from astrodeck.devices.backend import (
    BACKENDS,
    Backend,
    BackendSession,
    ConnSpec,
    get_backend,
)
from astrodeck.devices.backends.nina_backend import (
    NINA_BACKEND,
    NinaBackend,
    NinaSession,
)


# ------------------------------------------------------------------- fake rig

class _FakeNinaClient:
    """Stand-in for ``NinaClient`` -- records ``/version`` pings, exposes the
    link-health stamps the session reads, and a close flag. ``fail`` makes the
    ping raise so we can exercise the health-degrade path."""

    def __init__(self, host="nina.test", port=1888, fail=False):
        self.host = host
        self.port = port
        self.fail = fail
        self.last_ok = 123.0
        self.last_error = None
        self.pings = 0
        self.closed = False

    async def get(self, path):
        self.pings += 1
        if self.fail:
            self.last_error = "boom"
            raise RuntimeError("boom")
        return "2.2.0"

    async def close(self):
        self.closed = True


class _FakeGuider:
    name = "NINA Guider"


def _fake_rig(*, client=None, with_guider=True, roles=("camera", "telescope")):
    devices = {role: object() for role in roles}
    return {
        "client": client if client is not None else _FakeNinaClient(),
        "devices": devices,
        "guider": _FakeGuider() if with_guider else None,
    }


@pytest.fixture
def patched_build(monkeypatch):
    """Monkeypatch ``build_nina_rig`` to return a captured fake rig. The deferred
    ``from ..nina import build_nina_rig`` inside ``open()`` resolves to the
    module attribute, so patching the module attribute is enough."""
    captured = {}
    rig = _fake_rig()

    async def fake_build_nina_rig(host, port=1888, http=None):
        captured["host"] = host
        captured["port"] = port
        return rig

    monkeypatch.setattr(nina_module, "build_nina_rig", fake_build_nina_rig)
    return captured, rig


# ----------------------------------------------------------------- metadata

def test_backend_metadata_and_protocol():
    b = NinaBackend()
    assert isinstance(b, Backend)
    assert b.name == "nina"
    assert b.label == "NINA"
    # ``switch`` is fillable (NINA's switch hub); only ``safety`` stays unfillable.
    assert b.roles == ("camera", "telescope", "focuser", "filterwheel",
                       "switch", "guider")
    assert "safety" not in b.roles
    assert b.discoverable is True
    assert b.hostless is False


def test_self_registered_under_nina():
    # Importing the module self-registers the instance.
    assert get_backend("nina") is NINA_BACKEND
    assert BACKENDS["nina"] is NINA_BACKEND
    assert isinstance(NINA_BACKEND, NinaBackend)


# -------------------------------------------------------------------- open

async def test_open_awaits_build_and_passes_host_port(patched_build):
    captured, _rig = patched_build
    session = await NinaBackend().open(ConnSpec(backend="nina", host="10.0.0.9", port=9999))
    assert isinstance(session, NinaSession)
    assert isinstance(session, BackendSession)
    assert captured == {"host": "10.0.0.9", "port": 9999}


async def test_open_defaults_port_when_unset(patched_build):
    captured, _rig = patched_build
    await NinaBackend().open(ConnSpec(backend="nina", host="h"))
    assert captured["port"] == nina_module.DEFAULT_PORT


# ------------------------------------------------------------- get_device

async def test_get_device_returns_rig_devices(patched_build):
    _captured, rig = patched_build
    session = await NinaBackend().open(ConnSpec(backend="nina", host="h"))
    cam = await session.get_device("camera", ConnSpec(backend="nina"))
    assert cam is rig["devices"]["camera"]
    tel = await session.get_device("telescope", ConnSpec(backend="nina"))
    assert tel is rig["devices"]["telescope"]


async def test_get_device_unknown_role_raises_keyerror(patched_build):
    session = await NinaBackend().open(ConnSpec(backend="nina", host="h"))
    with pytest.raises(KeyError):
        await session.get_device("focuser", ConnSpec(backend="nina"))


# ---------------------------------------------------------- native_guider

async def test_native_guider_returns_rig_guider(patched_build):
    _captured, rig = patched_build
    session = await NinaBackend().open(ConnSpec(backend="nina", host="h"))
    # SYNC by contract.
    assert session.native_guider() is rig["guider"]


def test_native_guider_none_when_absent():
    session = NinaSession(_fake_rig(with_guider=False))
    assert session.native_guider() is None


def test_native_solver_is_none():
    session = NinaSession(_fake_rig())
    assert session.native_solver() is None


def test_guide_camera_is_none():
    # NINA exposes no dedicated guide-camera pseudo-device to AstroDeck.
    session = NinaSession(_fake_rig())
    assert session.guide_camera() is None


# ----------------------------------------------------------------- health

async def test_health_ok_pings_version():
    client = _FakeNinaClient(host="nina.test", port=1888)
    session = NinaSession(_fake_rig(client=client))
    h = await session.health()
    assert h["backend"] == "nina"
    assert h["ok"] is True
    assert h["host"] == "nina.test"
    assert h["port"] == 1888
    assert h["last_ok"] == 123.0
    assert client.pings == 1


async def test_health_degrades_without_raising():
    client = _FakeNinaClient(fail=True)
    session = NinaSession(_fake_rig(client=client))
    h = await session.health()  # must not raise
    assert h["ok"] is False
    assert h["last_error"] == "boom"


async def test_health_no_client():
    session = NinaSession({"devices": {}, "guider": None})
    h = await session.health()
    assert h == {"backend": "nina", "ok": False, "error": "no client"}


# ------------------------------------------------------------------ close

async def test_close_releases_client_only():
    client = _FakeNinaClient()
    session = NinaSession(_fake_rig(client=client))
    await session.close()
    assert client.closed is True


async def test_close_noop_without_client():
    session = NinaSession({"devices": {}, "guider": None})
    # must not raise
    await session.close()


# --------------------------------------------------------------- discover

async def test_discover_delegates_to_discover_nina(monkeypatch):
    async def fake_discover_nina():
        return [{"host": "10.0.0.1", "port": 1888}]

    monkeypatch.setattr(nina_module, "discover_nina", fake_discover_nina)
    out = await NinaBackend().discover()
    assert out == [{"host": "10.0.0.1", "port": 1888}]
