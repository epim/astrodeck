"""Per-capability provider resolution matrix (native parity, spec §3).

Pins the routing policy that autofocus + TPPA consume:
  * a rig whose focuser advertises its own autofocus (NINA) -> backend/NINA;
  * a native (sim/Alpaca) rig with the Rust wheel -> astrodeck;
  * the wheel absent -> autofocus is UNAVAILABLE with a clear reason, while polar
    still degrades to the built-in Simulator (it always has a fallback);
  * a NINA bridge -> polar backend/NINA;
  * explicit config/profile overrides force the choice (but a forced choice whose
    prerequisites are absent falls back to auto rather than resolving to nothing);
  * ``poll_status`` carries a ``providers`` block and NEVER raises.

Uses lightweight fakes (repo convention: no ``unittest.mock``) — ``resolve`` only
reads ``hub.devices`` / ``nina_client`` / ``sim_rig`` / ``mode`` / ``_active_profile``.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck import providers
from astrodeck.config import ConfigStore, ProvidersConfig
from astrodeck.devices.base import DeviceError
from astrodeck.profiles import Profile, ProfileLibrary


# --------------------------------------------------------------------- fakes

class FakeDev:
    """A connected role device: only the attributes resolve() inspects."""

    def __init__(self, *, backend: str = "sim", connected: bool = True,
                 supports_native_autofocus: bool = False):
        self.backend = backend
        self.connected = connected
        self.supports_native_autofocus = supports_native_autofocus


class FakeHub:
    def __init__(self, *, devices=None, nina_client=None, sim_rig=None,
                 mode: str = "sim", profile: Profile | None = None):
        self.devices = devices or {}
        self.nina_client = nina_client
        self.sim_rig = sim_rig
        self.mode = mode
        self._profile = profile

    def _active_profile(self):
        return self._profile


def _sim_devices(*, focuser=True, camera=True, telescope=True):
    d = {}
    if camera:
        d["camera"] = FakeDev(backend="sim")
    if focuser:
        d["focuser"] = FakeDev(backend="sim", supports_native_autofocus=False)
    if telescope:
        d["telescope"] = FakeDev(backend="sim")
    return d


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the module-level ``config_store`` resolve() reads at a fresh temp
    store so ``_override`` never picks up the developer's real config file (its
    providers default to all-auto)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(providers, "config_store", store)
    return store


# ------------------------------------------------------------ autofocus auto

def test_nina_focuser_resolves_backend():
    hub = FakeHub(mode="nina", nina_client=object(),
                  devices={"camera": FakeDev(backend="nina"),
                           "focuser": FakeDev(backend="nina",
                                              supports_native_autofocus=True)})
    c = providers.resolve("autofocus", hub)
    assert c.kind == "backend"
    assert c.label == "NINA"
    assert "autofocus" in c.reason.lower()


def test_native_rig_with_wheel_resolves_astrodeck(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("autofocus", hub)
    assert c.kind == "astrodeck"
    assert c.label == "AstroDeck native"


def test_autofocus_unavailable_without_wheel(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    hub = FakeHub(mode="sim", devices=_sim_devices())
    with pytest.raises(DeviceError) as ei:
        providers.resolve("autofocus", hub)
    assert "not installed" in str(ei.value).lower()
    # and resolve_all surfaces it as an explicit unavailable row (never raises).
    row = providers.resolve_all(hub)["autofocus"]
    assert row["kind"] == "unavailable"
    assert "not installed" in row["reason"].lower()


def test_autofocus_native_needs_camera_and_focuser(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    # focuser present, camera missing -> native prerequisites absent.
    hub = FakeHub(mode="sim", devices=_sim_devices(camera=False))
    with pytest.raises(DeviceError) as ei:
        providers.resolve("autofocus", hub)
    assert "camera" in str(ei.value).lower()


# --------------------------------------------------------------- polar auto

def test_nina_client_resolves_polar_backend():
    hub = FakeHub(mode="nina", nina_client=object(), devices={})
    c = providers.resolve("polar_align", hub)
    assert c.kind == "backend"
    assert c.label == "NINA"


def test_sim_rig_resolves_polar_simulator_without_wheel(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("polar_align", hub)
    assert c.kind == "astrodeck"
    assert c.label == "Simulator"
    assert "simulator" in c.reason.lower()


def test_sim_rig_with_wheel_resolves_native_polar(monkeypatch):
    # sim mode => SimSolver does NOT refuse => a real solver is available.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(mode="sim", devices=_sim_devices())
    c = providers.resolve("polar_align", hub)
    assert c.kind == "astrodeck"
    assert c.label == "AstroDeck native"


def test_native_polar_falls_back_when_solver_refuses(monkeypatch):
    # a REAL (alpaca) rig with no ASTAP: SimSolver refuses -> no trusted solver ->
    # native TPPA can't run -> Simulator fallback (naming the missing solver).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(mode="alpaca",
                  devices={"camera": FakeDev(backend="alpaca"),
                           "telescope": FakeDev(backend="alpaca")})
    c = providers.resolve("polar_align", hub)
    assert c.label == "Simulator"
    assert "solver" in c.reason.lower()


# ----------------------------------------------------------------- overrides

def test_config_override_forces_astrodeck(monkeypatch, isolated_config):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(autofocus="astrodeck"))
    # a NINA focuser (which would auto-resolve to backend) is OVERRIDDEN to native.
    hub = FakeHub(mode="nina", nina_client=object(),
                  devices={"camera": FakeDev(backend="nina"),
                           "focuser": FakeDev(backend="nina",
                                              supports_native_autofocus=True)})
    c = providers.resolve("autofocus", hub)
    assert c.kind == "astrodeck"
    assert "override" in c.reason.lower()


def test_profile_override_beats_config(monkeypatch, isolated_config):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    # config says astrodeck, profile says backend -> profile wins.
    isolated_config.set_providers(ProvidersConfig(autofocus="astrodeck"))
    prof = Profile(name="nina rig", providers={"autofocus": "backend"})
    hub = FakeHub(mode="nina", nina_client=object(), profile=prof,
                  devices={"camera": FakeDev(backend="nina"),
                           "focuser": FakeDev(backend="nina",
                                              supports_native_autofocus=True)})
    c = providers.resolve("autofocus", hub)
    assert c.kind == "backend"
    assert c.label == "NINA"


def test_override_with_absent_prereqs_falls_back_to_auto(monkeypatch):
    # force astrodeck but the wheel is absent -> prerequisites absent -> auto ->
    # a NINA focuser still resolves to the backend (not a broken native choice).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    prof = Profile(name="x", providers={"autofocus": "astrodeck"})
    hub = FakeHub(mode="nina", nina_client=object(), profile=prof,
                  devices={"camera": FakeDev(backend="nina"),
                           "focuser": FakeDev(backend="nina",
                                              supports_native_autofocus=True)})
    c = providers.resolve("autofocus", hub)
    assert c.kind == "backend"


# ----------------------------------------------------------------- resolve_all

def test_resolve_all_shape_and_never_raises():
    # a bare hub with NOTHING connected: autofocus is unavailable, polar degrades
    # to the simulator, and resolve_all returns well-formed rows for both.
    hub = FakeHub(mode="none", devices={})
    out = providers.resolve_all(hub)
    assert set(out) == {"autofocus", "polar_align"}
    for row in out.values():
        assert set(row) == {"kind", "label", "reason"}
    assert out["autofocus"]["kind"] == "unavailable"


# ------------------------------------------------------------------ poll_status

async def test_poll_status_carries_providers(tmp_path, monkeypatch):
    """The 2s status poll surfaces the resolved providers and never 500s."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    lib = ProfileLibrary(directory=tmp_path / "profiles")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(hub_module, "profiles", lib)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    h = hub_module.Hub()
    status = await h.poll_status()
    assert "providers" in status
    assert set(status["providers"]) == {"autofocus", "polar_align"}
    # nothing connected -> autofocus unavailable, polar simulator, no exception.
    for row in status["providers"].values():
        assert "kind" in row and "label" in row and "reason" in row


def test_config_setter_rejects_unknown_kind(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    bad = ProvidersConfig()
    object.__setattr__(bad, "autofocus", "bogus")  # bypass pydantic to hit the guard
    with pytest.raises(ValueError):
        store.set_providers(bad)
