"""backend_links must report ENGINE-served roles honestly (UX review #52).

The boot-LED grid joins each retained ``RoleResult`` with the role's LIVE
``connected`` state. That join used to read ``hub.devices[role]`` ONLY — but the
guider is never a device: the orchestrator sources it from the guider-role
session's ``native_guider()`` and the hub keeps it on ``hub.guider`` (AstroDeck
native on every sim/native rig, NinaGuider under NINA, PHD2 otherwise). So a
perfectly healthy DEFAULT guider read as ``connected: false`` with ``ok: true``
and ``error: null`` — the UI's "degraded" cell — an orange alarm word with no
cause, on every fresh sim connect.

Pins:
  * a sim rig's guider row is ok AND connected (the exact repro);
  * an engine that reports ``connected=False`` STILL degrades (the fix must not
    paper over a genuinely dropped guider link);
  * a role with no device and no engine stays not-connected.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import BACKENDS, ConnSpec, RigSpec, register
from astrodeck.hub import Hub
from astrodeck.profiles import ProfileLibrary


# --------------------------------------------------------------------- fakes

class FakeDevice:
    def __init__(self, role: str, *, backend: str = "fake"):
        self.role = role
        self.name = f"fake-{role}"
        self.backend = backend
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def describe(self) -> dict:
        return {"name": self.name, "role": self.role, "connected": self.connected}


class DeadGuider:
    """A guider ENGINE whose link is genuinely down: ``connect()`` does not make
    it connected (a PHD2 socket that answered at connect and then dropped)."""

    name = "Dead Guider"
    connected = False

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def stats(self):  # pragma: no cover - only if poll_status reads it
        class _S:
            recent: list = []
        return _S()


class FakeSession:
    def __init__(self, name: str, *, guider=None):
        self.name = name
        self._guider = guider
        self._devices: dict[str, FakeDevice] = {}

    async def get_device(self, role: str, conn: ConnSpec):
        dev = self._devices.get(role)
        if dev is None:
            dev = FakeDevice(role, backend=self.name)
            self._devices[role] = dev
        return dev

    def native_guider(self):
        return self._guider

    def guide_camera(self):
        return None

    def native_solver(self):
        return None

    async def health(self):
        return None

    async def close(self):
        pass


class FakeBackend:
    discoverable = False
    hostless = True

    def __init__(self, name: str, roles, *, guider=None):
        self.name = name
        self.label = name.title()
        self.roles = roles
        self._guider = guider

    async def open(self, conn: ConnSpec):
        return FakeSession(self.name, guider=self._guider)

    async def discover(self):
        return []


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def registry():
    """Snapshot/restore the global backend registry so fakes never leak."""
    import astrodeck.devices.backends as _backends  # noqa: F401
    saved = dict(BACKENDS)
    try:
        yield register
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    lib = ProfileLibrary(directory=tmp_path / "profiles")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(hub_module, "profiles", lib)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    h = Hub()
    try:
        yield h
    finally:
        await h.disconnect_all()


def _row(links: list[dict], role: str) -> dict:
    return next(r for r in links if r["role"] == role)


# --------------------------------------------------------------------- tests

async def test_sim_guider_is_engine_served_and_reads_connected(hub):
    """The #52 repro: connect the sim rig, the guider row must be CONNECTED."""
    out = await hub.connect_rigspec(RigSpec(primary="sim"))
    # the guider really is engine-served: it is on hub.guider, NOT in hub.devices.
    assert "guider" not in hub.devices
    assert hub.guider is not None and hub.guider.connected
    row = _row(out["backend_links"], "guider")
    assert row["ok"] is True and row["error"] is None
    assert row["connected"] is True, "engine-served guider must not read as a dropped link"
    # and it stays true on the polled surface the UI actually renders.
    assert _row(hub.backend_links(), "guider")["connected"] is True


async def test_engine_that_is_down_still_degrades(hub, registry):
    """The fix must not paper over a genuinely dead guider engine: a guider that
    reports connected=False keeps ok=True + connected=False (the DEGRADED cell)."""
    registry(FakeBackend("deadguide", ("camera", "guider"), guider=DeadGuider()))
    out = await hub.connect_rigspec(RigSpec(primary="deadguide"))
    row = _row(out["backend_links"], "guider")
    assert row["ok"] is True          # a guider WAS sourced at connect
    assert row["connected"] is False  # ...but its live link is down: degraded
    assert _row(out["backend_links"], "camera")["connected"] is True


async def test_role_with_no_device_and_no_engine_is_not_connected(hub, registry):
    """Sanity on the device lane: a role whose device vanished from hub.devices
    reads not-connected (there is no engine fallback for a device role)."""
    registry(FakeBackend("plain", ("camera", "focuser")))
    await hub.connect_rigspec(RigSpec(primary="plain"))
    assert _row(hub.backend_links(), "focuser")["connected"] is True
    hub.devices.pop("focuser")
    assert _row(hub.backend_links(), "focuser")["connected"] is False
