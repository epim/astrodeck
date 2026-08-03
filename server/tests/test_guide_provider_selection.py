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
        self.hardware = backend in ("nina", "alpaca")   # mirror old _REAL_BACKENDS
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

def test_eligible_providers_sim_rig_offers_auto_native_and_the_bridge(monkeypatch):
    """The bridge IS offered here, and this assertion changed on 2026-08-03.

    It used to read ``["auto", "astrodeck"]`` with the comment "no bridge -> no
    backend". That rule was wrong in the same way the native offer was wrong,
    only in the other direction: ``_resolve_guide`` honours an explicit
    ``backend`` override unconditionally — the legacy PHD2 socket is one the host
    can always attempt — so refusing to OFFER it made the offer disagree with the
    resolver, and once blocked reasons started being rendered that disagreement
    reached the screen as "no PHD2 or NINA bridge is connected" sitting directly
    above "no guide camera connected — using the PHD2 bridge".

    The invariant is not "these two lists happen to match today"; it is that the
    offer and the resolver are the SAME predicate. Both directions now hold.
    """
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    native = FakeGuider("native", "AstroDeck native")
    h = _hub(guider=native, devices=_sim_devices())
    elig = providers.guide_eligible_providers(h)
    assert elig == ["auto", "astrodeck", "backend"]
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


# ------------------------------- the offer must match the resolver (#132 pt.2)
#
# The offer predicate used to accept ``guide_camera OR camera`` while
# ``_resolve_guide`` computed ``native_ok`` from ``guide_camera`` ALONE. On a rig
# with only the imaging camera connected the dropdown therefore OFFERED
# "AstroDeck native", the write succeeded, and the resolver fell through to the
# PHD2 bridge: the user picked native and the badge said PHD2, with nothing on
# screen admitting the pick had been discarded.

def _imaging_camera_only():
    """A real rig with an imaging camera + mount and NO guide-camera role."""
    return {"camera": FakeDev(backend="alpaca"), "telescope": FakeDev(backend="alpaca")}


def test_imaging_camera_alone_does_not_make_native_selectable(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    h = _hub(devices=_imaging_camera_only())
    assert "astrodeck" not in providers.guide_eligible_providers(h)


def test_the_offer_never_promises_what_the_resolver_would_discard(
        isolated_config, monkeypatch):
    """The invariant, stated directly: if ``astrodeck`` is offered, an explicit
    ``astrodeck`` override must actually resolve to the native guider. This is
    the assertion that would have caught the original bug — the two predicates
    agreeing today is not the property; the property is that they agree."""
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    isolated_config.set_providers(ProvidersConfig(guide="astrodeck"))
    for devices in (_sim_devices(), _real_devices(), _imaging_camera_only(), {}):
        h = _hub(devices=devices)   # no guider wired -> the pure "what WOULD
        offered = "astrodeck" in providers.guide_eligible_providers(h)
        resolved_native = providers.resolve("guide", h).kind in ("astrodeck", "sim")
        assert offered == resolved_native, (sorted(devices), offered, resolved_native)


def test_a_blocked_option_carries_a_reason_naming_the_fix(monkeypatch):
    """A missing row tells the user nothing to act on, and on a rig that simply
    has not been wired yet it reads as "this product cannot guide" — which is the
    conclusion a real user reached. The blocked option is kept, with a sentence."""
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    h = _hub(devices=_imaging_camera_only())
    opts = {o["value"]: o for o in providers.guide_provider_options(h)}
    assert opts["astrodeck"]["eligible"] is False
    assert "guide camera" in opts["astrodeck"]["reason"]
    assert "auto" in opts and opts["auto"]["eligible"] is True


def test_option_reasons_distinguish_the_blockers(monkeypatch):
    """Four different situations produce four different sentences, because "not
    available" is the copy that sends a user to the forum."""
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    def native_reason(**kw):
        h = _hub(**kw)
        return next(o for o in providers.guide_provider_options(h)
                    if o["value"] == "astrodeck")["reason"]

    assert "NINA" in native_reason(devices=_real_devices(), nina_client=object())
    assert "guide camera" in native_reason(devices=_imaging_camera_only())
    assert "mount" in native_reason(devices={"guide_camera": FakeDev(backend="alpaca")})
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", False)
    assert "installed" in native_reason(devices=_real_devices())


def test_every_blocked_reason_names_its_own_provider(monkeypatch):
    """The UI renders these sentences WITHOUT prefixing the option's label,
    because prefixing produced the stutter "AstroDeck native — AstroDeck native
    needs a guide camera…". That only works while every sentence identifies its
    own provider, so the requirement is pinned here, next to the strings, rather
    than left as an assumption on the far side of the wire."""
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    rigs = (
        dict(devices=_real_devices(), nina_client=object()),
        dict(devices=_imaging_camera_only()),
        dict(devices={"guide_camera": FakeDev(backend="alpaca")}),
        dict(devices={}),
    )
    for kw in rigs:
        for opt in providers.guide_provider_options(_hub(**kw)):
            if opt["eligible"]:
                continue
            names = ("AstroDeck", "NINA", "PHD2")
            assert any(n in opt["reason"] for n in names), (opt, kw)


def test_eligible_is_derived_from_options_so_they_cannot_disagree(monkeypatch):
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    for devices in (_sim_devices(), _real_devices(), _imaging_camera_only(), {}):
        h = _hub(devices=devices)
        opts = providers.guide_provider_options(h)
        assert providers.guide_eligible_providers(h) == [
            o["value"] for o in opts if o["eligible"]]


# ---------------------------------------------- the screen must not argue with itself

def test_the_bridge_is_always_offered_because_the_resolver_always_honours_it(monkeypatch):
    """The offer must be the SAME predicate as the resolver, in both directions.

    `_resolve_guide` honours an explicit `backend` override unconditionally — its
    own comment says the legacy PHD2 socket is one the host can always attempt —
    but the offer used to refuse it unless a bridge was already reachable. On the
    default rig (imaging camera + mount, no guide camera, no bridge driver) that
    put three individually-true sentences on one screen: "no PHD2 or NINA bridge
    is connected", "no guide camera connected — using the PHD2 bridge", and a
    badge reading PHD2. They cannot all be about the same rig.

    It only became visible once blocked reasons were RENDERED; before that an
    ineligible value was simply absent from the select, so the sentence did not
    exist to contradict anything.
    """
    monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True)
    h = _hub(devices=_imaging_camera_only())          # the DEFAULT rig
    assert providers._guide_backend_blocker(h) is None
    rows = {o["value"]: o for o in providers.guide_provider_options(h)}
    assert rows["backend"]["eligible"] is True
    assert not rows["backend"].get("reason")
    # and the contradiction it produced is gone: no rendered sentence may claim
    # the bridge is absent while another says the bridge is guiding.
    blocked = " ".join(o.get("reason") or "" for o in rows.values())
    assert "no PHD2 or NINA bridge is connected" not in blocked
