"""Stage B: hub connect-by-profile / connect-by-rig (W1.6).

Drives the PINNED hub methods (``connect_rigspec`` / ``connect_profile_id`` /
``connect_active`` / ``backend_links`` / ``_apply_connect_result``) over the real
pluggable harness and over in-process FAKE backends (registered via the real
``register()``/``BACKENDS`` registry and removed in teardown -- NOT
``unittest.mock``, per the repo convention).

Pins:
  * a sim-primary RigSpec/Profile connects every role, derives ``self.mode``,
    populates ``backend_links`` ok, and (profile path) sets the active pointer;
  * a per-role open failure DEGRADES (not-ok ``RoleResult`` + missing device +
    red ``backend_links`` row + ``boot_connect_failed``) without crashing the
    connect, and the rest of the rig still comes up;
  * ``connect_active`` is a clean no-op when no profile is active;
  * a manual ``disconnect_all`` clears the boot-LED grid;
  * the legacy native ``alpaca`` -> ``native`` migration alias routes through.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import BACKENDS, ConnSpec, RigSpec, register
from astrodeck.hub import Hub
from astrodeck.profiles import Profile, ProfileDevice, ProfileLibrary


# --------------------------------------------------------------------- fakes

class FakeDevice:
    """A minimal role device: connect()/disconnect() idempotent, describe() shape."""

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


class FakeGuider:
    name = "Fake Guider"

    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def stats(self):  # pragma: no cover - only touched if poll_status reads it
        class _S:
            recent: list = []
        return _S()


class FakeSession:
    """A hostless in-process session: builds FakeDevices per role, can fail one
    role's get_device, optionally yields a native guider."""

    def __init__(self, name: str, *, roles, raise_roles=(), guider=None):
        self.name = name
        self._roles = roles
        self._raise_roles = set(raise_roles)
        self._guider = guider
        self._devices: dict[str, FakeDevice] = {}
        self.closed = False

    async def get_device(self, role: str, conn: ConnSpec):
        if role in self._raise_roles:
            raise RuntimeError(f"{role} offline")
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
        self.closed = True


class FakeBackend:
    """A registerable hostless backend that opens one FakeSession."""

    discoverable = False

    def __init__(self, name: str, roles, *, hostless=True, raise_roles=(),
                 guider=None):
        self.name = name
        self.label = name.title()
        self.roles = roles
        self.hostless = hostless
        self._raise_roles = raise_roles
        self._guider = guider
        self.opens = 0
        self.sessions: list[FakeSession] = []

    async def open(self, conn: ConnSpec):
        self.opens += 1
        s = FakeSession(self.name, roles=self.roles,
                        raise_roles=self._raise_roles, guider=self._guider)
        self.sessions.append(s)
        return s

    async def discover(self):
        return []


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def registry():
    """Snapshot/restore the global registry so fakes never leak into the real
    process registry (mirrors test_orchestrator.py).

    Import the backends package for its self-registration side-effect BEFORE the
    snapshot so the restore on teardown carries the real sim/native/etc backends;
    otherwise a snapshot taken while the registry is empty would (on teardown)
    clear the real backends and break later tests in the process."""
    import astrodeck.devices.backends as _backends  # noqa: F401
    saved = dict(BACKENDS)
    try:
        yield register
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


@pytest.fixture
async def hub_env(tmp_path, monkeypatch):
    """An isolated Hub: temp ConfigStore + temp ProfileLibrary wired into the hub
    module singletons, so connect-by-profile / set_active never touch the real
    server tree. Yields (hub, store, library)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    lib = ProfileLibrary(directory=tmp_path / "profiles")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(hub_module, "profiles", lib)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    h = Hub()
    try:
        yield h, store, lib
    finally:
        await h.disconnect_all()


# --------------------------------------------------- sim-primary happy path

async def test_connect_rigspec_sim_connects_all_roles(hub_env):
    h, store, _lib = hub_env
    out = await h.connect_rigspec(RigSpec(primary="sim"))
    # every sim role came up and is connected.
    for role in ("camera", "telescope", "focuser", "filterwheel", "switch", "safety"):
        assert role in h.devices
        assert h.devices[role].connected
    assert h.mode == "sim"
    assert h.sim_rig is not None
    assert h.guider is not None and h.guider.connected
    assert "guide_camera" in h.devices and h.devices["guide_camera"].connected
    # the return shape: summary + per-role results + backend_links.
    assert set(out) == {"summary", "results", "backend_links"}
    assert all(rr["ok"] for rr in out["results"])
    # every backend_links row is ok + connected (no red LEDs on a full sim rig).
    links = {row["role"]: row for row in out["backend_links"]}
    assert links["camera"]["ok"] and links["camera"]["connected"]
    # last_connect_result retained for the boot surface.
    assert h.last_connect_result is not None


async def test_connect_profile_id_sets_active_and_connects(hub_env):
    h, store, lib = hub_env
    prof = Profile(name="sim rig", primary_backend="sim")
    lib.save(prof)
    assert store.cfg().active_profile_id is None

    out = await h.connect_profile_id(prof.id)

    assert h.mode == "sim"
    assert "camera" in h.devices and h.devices["camera"].connected
    # active pointer set ONLY after a successful connect.
    assert store.cfg().active_profile_id == prof.id
    assert out["summary"]["mode"] == "sim"


async def test_connect_active_connects_active_profile(hub_env):
    h, store, lib = hub_env
    prof = Profile(name="active sim", primary_backend="sim")
    lib.save(prof)
    store.set_active_profile(prof.id)

    out = await h.connect_active()

    assert out is not None
    assert h.mode == "sim"
    assert "camera" in h.devices


async def test_connect_active_noop_when_no_active_profile(hub_env):
    h, store, _lib = hub_env
    assert store.cfg().active_profile_id is None
    out = await h.connect_active()
    assert out is None
    assert h.devices == {}
    assert h.mode == "none"


async def test_connect_profile_id_unknown_raises_keyerror(hub_env):
    h, _store, _lib = hub_env
    with pytest.raises(KeyError):
        await h.connect_profile_id("does-not-exist")


# ------------------------------------------------------ graceful degrade

async def test_per_role_failure_degrades_without_crashing(hub_env, registry):
    h, _store, _lib = hub_env
    # a hostless fake primary whose focuser open fails; the rest still come up.
    registry(FakeBackend("flakyrig",
                         ("camera", "telescope", "focuser"),
                         raise_roles=("focuser",)))
    out = await h.connect_rigspec(RigSpec(primary="flakyrig"))

    # camera + telescope connected; focuser degraded (absent), no crash.
    assert "camera" in h.devices and h.devices["camera"].connected
    assert "telescope" in h.devices
    assert "focuser" not in h.devices
    results = {rr["role"]: rr for rr in out["results"]}
    assert results["focuser"]["ok"] is False
    assert results["focuser"]["attempted"] is True
    assert "focuser offline" in results["focuser"]["error"]
    # backend_links: the failed role is a red LED (attempted, not ok, not connected).
    links = {row["role"]: row for row in out["backend_links"]}
    assert links["focuser"]["ok"] is False and links["focuser"]["connected"] is False
    assert links["camera"]["ok"] is True and links["camera"]["connected"] is True


async def test_boot_connect_failed_flag_reflects_degraded_role(hub_env, registry):
    h, _store, _lib = hub_env
    registry(FakeBackend("partial", ("camera", "focuser"),
                         raise_roles=("focuser",)))
    await h.connect_rigspec(RigSpec(primary="partial"))
    status = await h.poll_status()
    assert status["boot_connect_failed"] is True
    # a clean full sim rig has no boot failure.
    await h.connect_rigspec(RigSpec(primary="sim"))
    status2 = await h.poll_status()
    assert status2["boot_connect_failed"] is False


async def test_disconnect_clears_backend_links(hub_env):
    h, _store, _lib = hub_env
    await h.connect_rigspec(RigSpec(primary="sim"))
    assert h.backend_links()           # populated
    await h.disconnect_all()
    assert h.backend_links() == []
    assert h.last_connect_result is None
    status = await h.poll_status()
    assert status["backend_links"] == []
    assert status["boot_connect_failed"] is False


# ------------------------------------------------------ mixed / migration

async def test_legacy_alpaca_profile_routes_to_native(hub_env, registry):
    """A persisted profile in the OLD schema (backend='alpaca') must connect via
    the 'native' backend through the to_rigspec migration alias, NOT KeyError."""
    h, store, lib = hub_env
    # register a fake standing in for the native backend so we need no real Alpaca
    # server; it fills the one camera role the legacy device declares.
    fake_native = FakeBackend("native", ("camera",), hostless=True)
    registry(fake_native)
    prof = Profile(
        name="legacy",
        primary_backend="native",
        devices=[ProfileDevice(role="camera", backend="alpaca",
                               host="10.0.0.5", port=11111,
                               dev_type="camera", dev_num=0, name="ASI")],
    )
    lib.save(prof)
    out = await h.connect_profile_id(prof.id)
    # connected through 'native' (alpaca alias), mode mapped to the legacy label.
    assert "camera" in h.devices and h.devices["camera"].connected
    assert h.mode == "alpaca"           # native -> "alpaca" legacy UI label
    assert fake_native.opens == 1
    assert store.cfg().active_profile_id == prof.id
    assert out["summary"]["mode"] == "alpaca"


async def test_native_primary_maps_mode_to_alpaca(hub_env, registry):
    h, _store, _lib = hub_env
    registry(FakeBackend("native", ("camera", "telescope"), hostless=True))
    await h.connect_rigspec(RigSpec(primary="native"))
    # the PIN: self.mode = "alpaca" if primary == "native" (legacy UI compat).
    assert h.mode == "alpaca"
    assert h.sim_rig is None and h.nina_client is None


async def test_reconnect_same_shape_is_idempotent(hub_env):
    """Connect a sim rig, disconnect, connect again -> identical backend_links and
    device-role shape (active-profile reload posture)."""
    h, _store, lib = hub_env
    prof = Profile(name="repeat", primary_backend="sim")
    lib.save(prof)

    await h.connect_profile_id(prof.id)
    shape1 = sorted(h.devices)
    links1 = sorted((r["role"], r["ok"]) for r in h.backend_links())

    await h.disconnect_all()
    await h.connect_profile_id(prof.id)
    shape2 = sorted(h.devices)
    links2 = sorted((r["role"], r["ok"]) for r in h.backend_links())

    assert shape1 == shape2
    assert links1 == links2
