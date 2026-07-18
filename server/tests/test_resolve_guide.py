"""Guide-provider resolution matrix (native guider wiring, spec §3.3/§3.5).

Pins ``providers.resolve("guide", hub)`` — the badge/routing answer for "who
autoguides this rig":
  * a NINA rig -> ``backend`` (NINA owns guiding, Global Constraint D5);
  * an Alpaca/native rig with a connected guide camera + mount and the wheel ->
    ``astrodeck`` (the native Rust engine);
  * a sim rig -> ``sim`` (the badge; the guider OBJECT is a NativeGuider over the
    sim rig after the P2-T3 default flip);
  * no guide camera / wheel absent -> ``backend`` (the PHD2 bridge fallback —
    never a crash, spec §4);
  * an explicit ``backend`` override is honored; an explicit ``astrodeck``
    override whose prerequisites are absent degrades to auto.

Mirrors the ``_resolve_autofocus`` test style in ``test_providers.py`` —
lightweight fakes (repo convention: no ``unittest.mock``); ``resolve`` only reads
``hub.devices`` / ``nina_client`` / ``_active_profile``.
"""
from __future__ import annotations

import pytest

from astrodeck import providers
from astrodeck.config import ConfigStore, ProvidersConfig
from astrodeck.profiles import Profile


# --------------------------------------------------------------------- fakes

class FakeDev:
    """A connected role device: only the attributes resolve() inspects."""

    def __init__(self, *, backend: str = "sim", connected: bool = True):
        self.backend = backend
        self.connected = connected


class FakeHub:
    def __init__(self, *, devices=None, nina_client=None,
                 profile: Profile | None = None):
        self.devices = devices or {}
        self.nina_client = nina_client
        self._profile = profile

    def _active_profile(self):
        return self._profile


def _sim_guide_devices(*, guide_camera=True, telescope=True):
    """A sim rig's guide devices (backend '' — the Device default, NOT real)."""
    d = {}
    if guide_camera:
        d["guide_camera"] = FakeDev(backend="")
    if telescope:
        d["telescope"] = FakeDev(backend="")
    return d


def _native_guide_devices():
    """A native/Alpaca rig's guide devices (backend 'alpaca' — real hardware)."""
    return {"guide_camera": FakeDev(backend="alpaca"),
            "telescope": FakeDev(backend="alpaca")}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the module-level ``config_store`` resolve() reads at a fresh temp
    store so ``_override`` never picks up the developer's real config file (its
    providers default to all-auto)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(providers, "config_store", store)
    return store


# --------------------------------------------------------------- guide auto

def test_sim_rig_resolves_sim(monkeypatch):
    # a sim rig with the wheel present: the native engine runs, but the badge is
    # ``sim`` (the guider object is a NativeGuider over sim devices).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(devices=_sim_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind == "sim"
    assert c.label == "Simulator"


def test_native_rig_with_wheel_resolves_astrodeck(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(devices=_native_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind == "astrodeck"
    assert c.label == "AstroDeck native"


def test_nina_rig_resolves_backend():
    hub = FakeHub(nina_client=object(),
                  devices={"guide_camera": FakeDev(backend="nina"),
                           "telescope": FakeDev(backend="nina")})
    c = providers.resolve("guide", hub)
    assert c.kind == "backend"
    assert c.label == "NINA"


def test_wheel_absent_falls_back_to_backend(monkeypatch):
    # a sim rig on a box with no wheel: native can't run -> PHD2 bridge fallback.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    hub = FakeHub(devices=_sim_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind == "backend"
    assert "PHD2" in c.label or c.label == "NINA"


def test_no_guide_camera_falls_back_to_backend(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    hub = FakeHub(devices=_sim_guide_devices(guide_camera=False))
    c = providers.resolve("guide", hub)
    assert c.kind == "backend"


# ----------------------------------------------------------------- overrides

def test_backend_override_is_honored(monkeypatch, isolated_config):
    # a sim rig would auto-resolve to ``sim``; an explicit ``backend`` override
    # forces the PHD2/NINA bridge.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="backend"))
    hub = FakeHub(devices=_sim_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind == "backend"
    assert "override" in c.reason.lower()


def test_astrodeck_override_with_absent_prereqs_degrades(monkeypatch,
                                                         isolated_config):
    # force astrodeck but the wheel is absent -> prerequisites absent -> auto ->
    # PHD2 bridge fallback (never a broken native choice, never a crash).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    hub = FakeHub(devices=_sim_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind != "astrodeck"          # degraded, not resolved to native
    assert c.kind == "backend"


def test_profile_override_beats_config(monkeypatch, isolated_config):
    # config says astrodeck, profile says backend -> profile wins (mirrors AF).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    prof = Profile(name="sim rig", providers={"guide": "backend"})
    hub = FakeHub(profile=prof, devices=_sim_guide_devices())
    c = providers.resolve("guide", hub)
    assert c.kind == "backend"


# ----------------------------------------------------------------- resolve_all

def test_resolve_all_includes_guide_and_never_raises():
    hub = FakeHub(devices={})
    out = providers.resolve_all(hub)
    assert "guide" in out
    assert set(out) == {"autofocus", "polar_align", "solve", "guide"}
    for row in out.values():
        assert set(row) == {"kind", "label", "reason"}
