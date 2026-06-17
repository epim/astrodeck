"""Lifespan boot auto-connect tests (Stage B, api/app.py:_lifespan, W1.6).

On startup the app ALWAYS connects the active profile (so a rebooted Pi comes
back to its rig with no operator action), wrapped so a flaky/unreachable device
degrades into the disconnected state and NEVER crashes startup. First run / no
active profile -> the app starts cleanly disconnected.

These tests drive the REAL lifespan by entering ``TestClient(app)`` as a context
manager (which runs ``_lifespan``). Fake backends are registered via the real
``register()``/``BACKENDS`` registry and removed in teardown (NO
``unittest.mock``). ``ASTRODECK_NO_AUTOCONNECT`` is left UNSET (default enabled)
so the boot path actually runs; per-test isolation comes from a temp ConfigStore
+ temp profiles dir.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.devices.backend import BACKENDS, ConnSpec, register
from astrodeck.profiles import Profile, ProfileDevice


# --------------------------------------------------------------------- fakes

class _FakeDevice:
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


class _FakeSession:
    def __init__(self, name: str, *, roles, raise_roles=()):
        self.name = name
        self._roles = roles
        self._raise_roles = set(raise_roles)
        self._devices: dict[str, _FakeDevice] = {}
        self.closed = False

    async def get_device(self, role: str, conn: ConnSpec):
        if role in self._raise_roles:
            raise RuntimeError(f"{role} offline")
        dev = self._devices.get(role)
        if dev is None:
            dev = _FakeDevice(role, backend=self.name)
            self._devices[role] = dev
        return dev

    def native_guider(self):
        return None

    def guide_camera(self):
        return None

    def native_solver(self):
        return None

    async def health(self):
        return None

    async def close(self):
        self.closed = True


class _FakeBackend:
    discoverable = False

    def __init__(self, name: str, roles, *, hostless=True, raise_roles=()):
        self.name = name
        self.label = name.title()
        self.roles = roles
        self.hostless = hostless
        self._raise_roles = raise_roles
        self.opens = 0

    async def open(self, conn: ConnSpec):
        self.opens += 1
        return _FakeSession(self.name, roles=self.roles,
                            raise_roles=self._raise_roles)

    async def discover(self):
        return []


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config + profiles, with the global backend registry snapshotted so
    any fake registered by a test cannot leak. Boot auto-connect is left ENABLED
    (env unset). Yields a helper that builds the app AFTER the test has seeded the
    active profile, so the lifespan boot sees it."""
    monkeypatch.delenv(app_module.NO_AUTOCONNECT_ENV_VAR, raising=False)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    # Import the backends package for its self-registration side-effect BEFORE
    # snapshotting the registry, so the snapshot we restore on teardown contains
    # the real sim/nina/native/phd2 backends. Otherwise, if this fixture runs
    # before anything else has triggered that import, ``saved`` is empty and the
    # teardown ``clear()``+restore wipes the registry for every later test in the
    # process (an order-dependent KeyError("unknown backend 'sim'") elsewhere).
    import astrodeck.devices.backends as _backends  # noqa: F401
    saved = dict(BACKENDS)
    try:
        yield temp_store, profile_lib
    finally:
        BACKENDS.clear()
        BACKENDS.update(saved)


def _client(env):
    """Build + enter the app's lifespan (runs boot auto-connect). The caller has
    already seeded the active profile / registered fakes."""
    app = app_module.create_app()
    return TestClient(app)


# ---------------------------------------------------------- first-run no-op

def test_boot_first_run_no_active_profile_starts_disconnected(env):
    _store, _lib = env
    with _client(env) as c:
        r = c.get("/api/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("mode") == "none"
        # no rig connected, no boot failure flagged.
        assert not body.get("boot_connect_failed", False)


# --------------------------------------------------- boot connects the active rig

def test_boot_connects_active_sim_profile(env):
    store, lib = env
    prof = Profile(name="active sim", primary_backend="sim")
    lib.save(prof)
    store.set_active_profile(prof.id)

    with _client(env) as c:
        r = c.get("/api/status")
        assert r.status_code == 200, r.text
        body = r.json()
        # the sim rig auto-connected on boot.
        assert body.get("mode") == "sim"
        assert not body.get("boot_connect_failed", False)
        # backend_links surface the per-role tri-state, all ok.
        links = {row["role"]: row for row in body.get("backend_links", [])}
        assert links, "expected backend_links on the status poll"
        assert links["camera"]["ok"] and links["camera"]["connected"]
        c.post("/api/disconnect")


# ----------------------------------------------- boot SWALLOWS a connect failure

def test_boot_swallows_connect_failure_and_degrades(env):
    """A backend whose ``focuser`` open raises must NOT crash startup: the app
    starts, /api/status is 200, the rig comes up with the roles that DID connect,
    and the failed role is a red (not-ok) backend_links row with
    ``boot_connect_failed`` True."""
    store, lib = env
    register(_FakeBackend("flakyboot",
                          ("camera", "telescope", "focuser"),
                          raise_roles=("focuser",)))
    prof = Profile(name="flaky", primary_backend="flakyboot")
    lib.save(prof)
    store.set_active_profile(prof.id)

    with _client(env) as c:
        # the app started despite the failing open.
        r = c.get("/api/status")
        assert r.status_code == 200, r.text
        body = r.json()
        links = {row["role"]: row for row in body.get("backend_links", [])}
        assert links, "expected backend_links on the status poll"
        # the working roles came up; the failed one is not-ok.
        assert links["camera"]["ok"] and links["camera"]["connected"]
        assert links["focuser"]["ok"] is False
        assert links["focuser"]["connected"] is False
        assert links["focuser"]["attempted"] is True
        assert body.get("boot_connect_failed") is True
        c.post("/api/disconnect")


# -------------------------------------------- env opt-out skips boot auto-connect

def test_boot_autoconnect_disabled_env_skips_connect(env, monkeypatch):
    store, lib = env
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    prof = Profile(name="active sim", primary_backend="sim")
    lib.save(prof)
    store.set_active_profile(prof.id)

    with _client(env) as c:
        body = c.get("/api/status").json()
        # boot auto-connect was opted out -> nothing connected even with an active
        # profile set.
        assert body.get("mode") == "none"
