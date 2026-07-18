"""Guide-provider SELECTION + badge honesty (P5-T1 fix round C1).

The per-profile guide override must be a REAL selection input, not just a badge
relabel: at guiding start ``hub.select_guide_provider`` swaps ``hub.guider`` to a
guider of the requested FAMILY when one is constructible on the connected rig,
and DEGRADES to whatever is wired otherwise. Critically, the status badge
(``providers.resolve("guide")``) then reports the ACTUAL serving guider — never
the requested override — so the UI's same-night RMS ticks (tagged by that badge)
can't be mislabeled (review C1). ``guide_eligible_providers`` backs review I1:
the dropdown offers only what applies.

Fixture style mirrors ``test_resolve_guide.py`` (lightweight fakes, no
``unittest.mock``; isolated ConfigStore so ``_override`` reads a clean store).
Uses a REAL ``Hub`` for the selection method (its own ``_candidate_guiders`` is
exercised) with ``_active_profile`` pinned so overrides come from the config
path only.
"""
from __future__ import annotations

import pytest

from astrodeck import providers
from astrodeck.config import ConfigStore, ProvidersConfig
from astrodeck.hub import Hub


# --------------------------------------------------------------------- fakes

class FakeDev:
    """A connected role device: only the attributes resolve() inspects."""

    def __init__(self, *, backend: str = "", connected: bool = True):
        self.backend = backend
        self.connected = connected


class FakeGuider:
    """A guider stand-in carrying only what selection/honesty read: a
    ``provider_family`` marker, a name, connect/disconnect/is_active."""

    def __init__(self, family: str, name: str, *, active: bool = False):
        self.provider_family = family
        self.name = name
        self.connected = True
        self._active = active
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def is_active(self) -> bool:
        return self._active


class FakeSession:
    def __init__(self, guider):
        self._g = guider

    def native_guider(self):
        return self._g


class FakeConnectResult:
    def __init__(self, sessions: dict):
        self.sessions = sessions


def _sim_devices():
    return {"guide_camera": FakeDev(backend=""), "telescope": FakeDev(backend="")}


def _real_devices():
    return {"guide_camera": FakeDev(backend="alpaca"),
            "telescope": FakeDev(backend="alpaca")}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """A fresh temp store for the module-level ``config_store`` resolve() reads,
    so ``_override`` never picks up the developer's real config."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(providers, "config_store", store)
    return store


def _hub(*, guider=None, devices=None, nina_client=None, result=None):
    h = Hub()
    h.guider = guider
    h.devices = devices or {}
    h.nina_client = nina_client
    h.last_connect_result = result
    # overrides come from the config path only (no active profile) in these unit
    # tests; providers._override reads hub._active_profile() first.
    h._active_profile = lambda: None
    return h


# ----------------------------------------------------- selection: default/auto

async def test_sim_rig_default_keeps_native_guider(isolated_config, monkeypatch):
    # a sim rig's connect-time guider is the NativeGuider; auto (no override)
    # must keep it and badge it ``sim`` (native engine, sim devices).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    native = FakeGuider("native", "AstroDeck native")
    h = _hub(guider=native, devices=_sim_devices())
    await h.select_guide_provider()
    assert h.guider is native                       # unchanged
    assert providers.resolve("guide", h).kind == "sim"


# ---------------------------------------- selection: degrade + honest badge

async def test_backend_override_no_bridge_guider_degrades_and_badge_is_honest(
        isolated_config, monkeypatch):
    # a sim rig has NO backend guider constructible; forcing ``backend`` must
    # DEGRADE to the wired native guider (never a crash), and — the C1 root
    # fix — the badge must report the ACTUAL guider (``sim``), NOT ``backend``,
    # so the RMS comparison can't be built from mislabeled ticks.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="backend"))
    native = FakeGuider("native", "AstroDeck native")
    h = _hub(guider=native, devices=_sim_devices())
    await h.select_guide_provider()
    assert h.guider is native                       # degraded — nothing to swap to
    choice = providers.resolve("guide", h)
    assert choice.kind == "sim"                     # ACTUAL guider, not the override
    assert choice.kind != "backend"


# ------------------------------------------- selection: honored where available

async def test_astrodeck_override_honored_when_native_constructible(
        isolated_config, monkeypatch):
    # a rig whose guider ROLE is the PHD2 bridge (connect-time guider = backend)
    # but which ALSO has a native session (guide camera + mount): forcing
    # ``astrodeck`` must swap the active guider to the native one and badge it
    # ``astrodeck`` (real rig).
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    phd2 = FakeGuider("backend", "PHD2")
    native = FakeGuider("native", "AstroDeck native")
    result = FakeConnectResult({
        ("phd2", None, None): FakeSession(phd2),
        ("native", "host", 11111): FakeSession(native),
    })
    h = _hub(guider=phd2, devices=_real_devices(), result=result)
    await h.select_guide_provider()
    assert h.guider is native                       # honored: swapped to native
    assert native.connect_calls == 1
    assert phd2.disconnect_calls == 1               # previous guider released
    assert providers.resolve("guide", h).kind == "astrodeck"


async def test_auto_reconciles_to_native_first_on_mixed_rig(
        isolated_config, monkeypatch):
    # auto is native-first (the resolver's own preference): on the same mixed
    # rig, auto swaps the backend connect-time guider to native.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    phd2 = FakeGuider("backend", "PHD2")
    native = FakeGuider("native", "AstroDeck native")
    result = FakeConnectResult({
        ("phd2", None, None): FakeSession(phd2),
        ("native", "host", 11111): FakeSession(native),
    })
    h = _hub(guider=phd2, devices=_real_devices(), result=result)
    await h.select_guide_provider()                 # override defaults to auto
    assert h.guider is native


async def test_backend_override_honored_on_mixed_rig(
        isolated_config, monkeypatch):
    # forcing ``backend`` on the mixed rig keeps the PHD2 bridge (no swap) and
    # badges ``backend``.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="backend"))
    phd2 = FakeGuider("backend", "PHD2")
    native = FakeGuider("native", "AstroDeck native")
    result = FakeConnectResult({
        ("phd2", None, None): FakeSession(phd2),
        ("native", "host", 11111): FakeSession(native),
    })
    h = _hub(guider=phd2, devices=_real_devices(), result=result)
    await h.select_guide_provider()
    assert h.guider is phd2                          # already the target family
    assert native.connect_calls == 0
    assert providers.resolve("guide", h).kind == "backend"


# --------------------------------------------------------- no hot-swap guarantee

async def test_running_guider_is_not_hot_swapped(isolated_config, monkeypatch):
    # a switch takes effect only at the NEXT start: an override must not yank a
    # guider that is CURRENTLY guiding.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    phd2 = FakeGuider("backend", "PHD2", active=True)   # actively guiding
    native = FakeGuider("native", "AstroDeck native")
    result = FakeConnectResult({
        ("phd2", None, None): FakeSession(phd2),
        ("native", "host", 11111): FakeSession(native),
    })
    h = _hub(guider=phd2, devices=_real_devices(), result=result)
    await h.select_guide_provider()
    assert h.guider is phd2                          # left running, no swap
    assert native.connect_calls == 0


# ------------------------------------------------------------ D5: NINA untouched

async def test_nina_rig_guider_left_untouched(isolated_config, monkeypatch):
    # D5: a NINA rig owns its guiding — the override must not disturb it.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    nina = FakeGuider("backend", "NINA Guider")
    h = _hub(guider=nina, devices=_real_devices(), nina_client=object())
    await h.select_guide_provider()
    assert h.guider is nina
    assert providers.resolve("guide", h).kind == "backend"


# ------------------------------------------------------ eligibility (review I1)

def test_eligible_providers_sim_rig_offers_auto_and_native_only(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    native = FakeGuider("native", "AstroDeck native")
    h = _hub(guider=native, devices=_sim_devices())
    elig = providers.guide_eligible_providers(h)
    assert elig == ["auto", "astrodeck"]            # no bridge -> no "backend"
    assert "sim" not in elig                        # no-op value never offered


def test_eligible_providers_mixed_rig_offers_native_and_backend(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    phd2 = FakeGuider("backend", "PHD2")
    native = FakeGuider("native", "AstroDeck native")
    result = FakeConnectResult({
        ("phd2", None, None): FakeSession(phd2),
        ("native", "host", 11111): FakeSession(native),
    })
    h = _hub(guider=phd2, devices=_real_devices(), result=result)
    elig = providers.guide_eligible_providers(h)
    assert "astrodeck" in elig and "backend" in elig


def test_eligible_providers_nina_rig_offers_backend_not_native(monkeypatch):
    # D5: native guiding is not offered on a NINA rig.
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    nina = FakeGuider("backend", "NINA Guider")
    h = _hub(guider=nina, devices=_real_devices(), nina_client=object())
    elig = providers.guide_eligible_providers(h)
    assert "backend" in elig
    assert "astrodeck" not in elig
