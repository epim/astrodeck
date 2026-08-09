"""Effective config with provenance: WHICH LAYER won, not just what it says.

The failure these tests exist to catch is not "the console shows the wrong
number". It is "the console shows a number that is right in every case the user
would check and wrong in the one case they would not": the ACTIVE PROFILE beats
global config at read time, and the endpoints the UI binds to report global. A
profile-pinned simulator ran the polar aligner for twelve days that way.

So the assertions here are about LAYER IDENTITY, deliberately including the case
where both layers hold the SAME value. A test that only checked the winning
value would have passed, unbroken, throughout those twelve days.
"""
from __future__ import annotations

import pytest

import astrodeck.config as config_mod
from astrodeck.config import (PROVIDER_CAPABILITIES, Optics, ProvidersConfig)
from astrodeck.hub import Hub
from astrodeck.profiles import Profile, resolve_optics
from astrodeck.provenance import OPTICS_KEYS, effective_config
from astrodeck import providers as providers_mod


# --------------------------------------------------------------------- fakes

class FakeCam:
    """A connected camera: only the attributes effective_optics inspects."""

    def __init__(self, px: float = 3.76, w: int = 6248, h: int = 4176):
        self.connected = True
        self.pixel_size_um = px
        self.sensor_width = w
        self.sensor_height = h
        self.backend = "sim"
        self.hardware = False


class FakeHub:
    """A hub-shaped object carrying only what the provenance path reads.

    ``effective_optics`` is BORROWED from the real ``Hub`` rather than
    reimplemented: the provenance readout must report the layer behind the value
    the real hub computes, and a stubbed copy of that computation could agree
    about the number while disagreeing about where it came from — which is the
    entire bug. Borrowing it also pins that ``Hub.effective_optics`` depends on
    nothing but ``_active_profile()`` and ``devices``."""

    effective_optics = Hub.effective_optics

    def __init__(self, profile: Profile | None = None, devices=None):
        self._profile = profile
        self.devices = devices or {}
        self.nina_client = None
        self.sim_rig = None
        self.mode = "sim"

    def _active_profile(self):
        return self._profile


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the process-wide ``config_store`` singleton at a fresh file.

    Redirecting the PATH (rather than swapping the object) is what makes every
    module that did ``from .config import config_store`` at import time — hub,
    providers, profiles, provenance — see the SAME isolated store. Patching one
    module's binding leaves the others on the shared instance and silently tests
    a different config than the code under test reads."""
    s = config_mod.config_store
    monkeypatch.setattr(s, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(s, "_cfg", None)
    return s


# A profile whose optics block differs from global in every field, so a
# per-field "did the profile win" assertion can never pass by coincidence.
PROFILE_OPTICS = Optics(focal_length_mm=1000.0, pixel_size_um=2.4,
                        sensor_width_px=4144, sensor_height_px=2822,
                        auto_from_camera=True, guide_focal_length_mm=200.0,
                        telescope_name="profile scope")
# Every field DELIBERATELY differs from the model's own default, so "the global
# block won" is distinguishable from "nobody ever set this" on every key.
GLOBAL_OPTICS = Optics(focal_length_mm=700.0, pixel_size_um=3.76,
                       sensor_width_px=6248, sensor_height_px=4176,
                       auto_from_camera=False, guide_focal_length_mm=120.0,
                       telescope_name="global scope")

#: Every key a profile can override, with the profile value that overrides it.
#: This list IS the contract — if a new overridable key appears and is not here,
#: the coverage test below fails.
OVERRIDABLE_KEYS = ([f"providers.{c}" for c in PROVIDER_CAPABILITIES]
                    + [f"optics.{k}" for k in OPTICS_KEYS])


#: Two DISTINCT non-auto families each capability's resolver actually branches
#: on, derived from ``providers.HONOURED_FAMILIES`` rather than hardcoded.
#:
#: These tests only ever needed "some pinned value" and used a blanket
#: ``{c: "astrodeck"}`` / ``{c: "sim"}`` for all four capabilities. Since finding
#: O the write layer rejects a family whose resolver has no branch for it, and
#: neither blanket is honourable everywhere (there is no native plate solver, and
#: no simulated autofocus or guider). Deriving the pair keeps the fixture honest
#: as the table changes instead of pinning a value the product would refuse.
def _pins(cap: str) -> tuple[str, str]:
    """``(global_pin, profile_pin)`` for ``cap`` — distinct where the capability
    honours two or more families, so the layer under test is the only thing
    distinguishing them."""
    fams = sorted(providers_mod.honoured_families(cap) - {"auto"})
    assert fams, f"{cap} honours no concrete family — nothing to pin"
    return fams[0], fams[-1]


def _global_pins() -> dict[str, str]:
    return {c: _pins(c)[0] for c in PROVIDER_CAPABILITIES}


def _overriding_profile() -> Profile:
    """A profile that overrides EVERY overridable key at once."""
    return Profile(name="override rig", optics=PROFILE_OPTICS,
                   providers={c: _pins(c)[1] for c in PROVIDER_CAPABILITIES})


# ------------------------------------------------- the layer identity contract

def test_every_overridable_key_is_covered(store):
    """The provenance block explains exactly the keys a profile can override —
    no more (a dead key would invent provenance) and no fewer (a missing key is
    a key the console can still display from the losing layer unchallenged)."""
    eff = effective_config(FakeHub(profile=_overriding_profile()))
    assert sorted(eff) == sorted(OVERRIDABLE_KEYS)
    # site_name is stored on a profile and read by nothing (Hub.site is
    # unconditional), so it must NOT appear as an override.
    assert not [k for k in eff if "site" in k]


@pytest.mark.parametrize("key", OVERRIDABLE_KEYS)
def test_profile_named_as_winner_for_every_overridable_key(store, key):
    store.set_providers(ProvidersConfig(**_global_pins()))
    store.set_optics(GLOBAL_OPTICS)
    prof = _overriding_profile()
    entry = effective_config(FakeHub(profile=prof))[key]
    assert entry["layer"] == "profile", (
        f"{key}: the profile overrides this and the readout credits "
        f"{entry['layer']!r}")
    assert entry["profile_id"] == prof.id
    assert entry["profile_name"] == "override rig"
    # the losing layer is reported, not hidden: that is what lets the UI show
    # "you are looking at the profile's value, global config holds X".
    assert entry["config"] is not None
    assert "override" in (entry["reason"] or "")


@pytest.mark.parametrize("key", OVERRIDABLE_KEYS)
def test_global_named_as_winner_when_no_profile_override(store, key):
    """Same rig, same keys, profile carrying NEITHER override block."""
    store.set_providers(ProvidersConfig(**_global_pins()))
    store.set_optics(GLOBAL_OPTICS)
    entry = effective_config(
        FakeHub(profile=Profile(name="plain rig")))[key]
    assert entry["layer"] == "config", f"{key} credited {entry['layer']!r}"
    assert entry["profile"] is None
    assert "no optics override" in (entry["reason"] or "") \
        or "pins no provider" in (entry["reason"] or "")


@pytest.mark.parametrize("key", OVERRIDABLE_KEYS)
def test_winning_value_matches_what_the_rig_actually_runs(store, key):
    """The value column is not decorative: it must equal what the resolvers
    return, or the layer would be right about a number nobody uses."""
    store.set_providers(ProvidersConfig(**_global_pins()))
    store.set_optics(GLOBAL_OPTICS)
    prof = _overriding_profile()
    hub = FakeHub(profile=prof)
    entry = effective_config(hub)[key]
    family, _, name = key.partition(".")
    if family == "providers":
        assert entry["value"] == providers_mod._override(name, hub)
    else:
        assert entry["value"] == getattr(resolve_optics(prof), name)


def test_layer_identity_when_both_layers_hold_the_same_value(store):
    """THE regression this whole change exists for.

    A profile pinned to ``sim`` and a global config pinned to ``sim`` produce an
    identical value. Only the layer distinguishes "the rig is configured this
    way" from "this rig's profile is quietly overriding you"."""
    store.set_providers(ProvidersConfig(polar_align="sim"))
    pinned = effective_config(FakeHub(profile=Profile(
        name="rig", providers={"polar_align": "sim"})))["providers.polar_align"]
    unpinned = effective_config(
        FakeHub(profile=Profile(name="rig")))["providers.polar_align"]
    assert pinned["value"] == unpinned["value"] == "sim"
    assert (pinned["layer"], unpinned["layer"]) == ("profile", "config")


def test_profile_pinned_auto_still_beats_a_global_pin(store):
    """``auto`` reads as "no override" and is not one: the profile's ``auto``
    wins over a global ``astap``, so the solver the rig picks is chosen
    automatically while the Settings screen shows ASTAP."""
    store.set_providers(ProvidersConfig(solve="astap"))
    entry = effective_config(FakeHub(profile=Profile(
        name="rig", providers={"solve": "auto"})))["providers.solve"]
    assert entry["value"] == "auto"
    assert entry["layer"] == "profile"
    assert entry["config"] == "astap"


def test_global_auto_is_reported_as_default_not_as_a_pin(store):
    """A stored ``auto`` is indistinguishable from the field's own default, so
    claiming global config "pins" it would overstate what the user did."""
    entry = effective_config(FakeHub())["providers.guide"]
    assert (entry["value"], entry["layer"]) == ("auto", "default")


def test_discarded_profile_pin_is_visible(store):
    """A pin naming a driver id that no longer exists degrades to the global
    layer silently. The user sees their override ignored with no explanation
    anywhere in the product — the same invisible-wrong class, one step over."""
    store.set_providers(ProvidersConfig(guide="astrodeck"))
    entry = effective_config(FakeHub(profile=Profile(
        name="rig", providers={"guide": "deleted-driver-id"})))["providers.guide"]
    assert entry["layer"] == "config"
    assert entry["value"] == "astrodeck"
    assert entry["profile"] == "deleted-driver-id"      # the discarded string
    assert "deleted-driver-id" in (entry["reason"] or "")


def test_profile_optics_block_wins_whole_not_field_by_field(store):
    """A profile that only meant to change the focal length also imposes its own
    defaults on every other optics field. The readout has to say so, or the user
    reverts a pixel size without ever seeing it happen."""
    store.set_optics(GLOBAL_OPTICS)
    prof = Profile(name="rig", optics=Optics(focal_length_mm=250.0))
    eff = effective_config(FakeHub(profile=prof))
    assert eff["optics.focal_length_mm"]["value"] == 250.0
    # telescope_name was never touched by this profile, yet the profile's empty
    # default is what runs — global's "global scope" is shadowed.
    assert eff["optics.telescope_name"]["layer"] == "profile"
    assert eff["optics.telescope_name"]["value"] == ""
    assert eff["optics.telescope_name"]["config"] == "global scope"


def test_camera_is_named_as_the_winning_layer(store):
    """A zeroed optics field is filled from the connected camera. Reporting
    ``pixel_size_um: 0`` while the rig runs 3.76 would be the original lie in a
    different place."""
    store.set_optics(Optics(focal_length_mm=530.0))       # pixel/sensor unset
    eff = effective_config(FakeHub(devices={"camera": FakeCam()}))
    entry = eff["optics.pixel_size_um"]
    assert entry["layer"] == "camera"
    assert entry["value"] == 3.76
    assert entry["config"] == 0.0
    assert eff["optics.sensor_width_px"]["layer"] == "camera"
    # the focal length has no camera source and must not be miscredited
    assert eff["optics.focal_length_mm"]["layer"] != "camera"


def test_every_entry_has_the_full_shape(store):
    """Uniform keys, nulls instead of absences: the UI renders this block
    generically and a missing key reads as ``undefined``, i.e. as "no
    override"."""
    expected = {"value", "layer", "profile", "config", "default",
                "profile_id", "profile_name", "reason"}
    for key, entry in effective_config(
            FakeHub(profile=_overriding_profile())).items():
        assert set(entry) == expected, key
        assert entry["layer"] in ("profile", "config", "camera", "default"), key


# ------------------------------------------------ effective_optics completeness

def test_effective_optics_returns_the_guide_focal_length(store):
    """It did not, which is why the native guider had to read GLOBAL config for
    it while every sibling field came from the profile."""
    store.set_optics(GLOBAL_OPTICS)
    plain = FakeHub().effective_optics()
    assert plain["guide_focal_length_mm"] == 120.0
    assert plain["auto_from_camera"] is False
    overridden = FakeHub(profile=Profile(name="r", optics=PROFILE_OPTICS)
                         ).effective_optics()
    assert overridden["guide_focal_length_mm"] == 200.0
    assert overridden["auto_from_camera"] is True


def test_resolve_optics_swaps_the_whole_block(store):
    store.set_optics(GLOBAL_OPTICS)
    assert resolve_optics(None) is config_mod.config_store.cfg().optics
    assert resolve_optics(Profile(name="r")) == GLOBAL_OPTICS
    assert resolve_optics(Profile(name="r", optics=PROFILE_OPTICS)) is PROFILE_OPTICS


# ------------------------------------------------------------- Profile.row()

def test_row_carries_both_override_blocks():
    """Dropping these made it structurally impossible for the Profiles tab to
    show that a profile carries overrides at all."""
    prof = Profile(name="rig", optics=PROFILE_OPTICS,
                   providers={"polar_align": "sim"})
    row = prof.row()
    assert row["providers"] == {"polar_align": "sim"}
    assert row["optics"]["focal_length_mm"] == 1000.0
    assert row["optics"]["guide_focal_length_mm"] == 200.0


def test_row_omits_blocks_a_profile_does_not_carry():
    row = Profile(name="plain").row()
    assert row["providers"] is None and row["optics"] is None


def test_native_guider_uses_the_profile_guide_focal_length(store, monkeypatch):
    """The split-layer bug: the guide scale was read from GLOBAL config while
    every sibling optics field came from the profile, so a profile override was
    honoured for imaging and silently ignored for guiding. The symptom is not an
    error — it is guiding RMS reported in pixels at an assumed 1"/px, next to an
    Optics panel showing a guide focal length that looks set."""
    import astrodeck.devices.backends.native_backend as nb
    import astrodeck.profiles as profiles_mod

    monkeypatch.setattr(nb, "NATIVE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(providers_mod, "NATIVE_AVAILABLE", True, raising=False)
    store.set_optics(GLOBAL_OPTICS)                    # guide scope: 120 mm
    prof = Profile(name="rig", optics=PROFILE_OPTICS)  # guide scope: 200 mm
    store.set_active_profile(prof.id)
    monkeypatch.setattr(profiles_mod.profiles, "active", lambda pid: prof)

    sess = nb.NativeSession("127.0.0.1")
    sess._devices["guide_camera"] = FakeCam(px=4.0)
    sess._devices["telescope"] = object()
    guider = sess.native_guider()
    assert guider is not None
    assert guider._image_scale == pytest.approx(206.265 * 4.0 / 200.0)
    assert guider._image_scale != pytest.approx(206.265 * 4.0 / 120.0)


async def test_auto_from_camera_seeds_the_layer_that_wins(store, monkeypatch,
                                                          tmp_path):
    """Both the gate and the write target used to be GLOBAL, under a profile
    whose optics block shadows global on every read. So on a rig with an optics
    override the user watched the panel fill in from the camera and not one of
    those numbers reached the rig."""
    import astrodeck.hub as hub_module
    from astrodeck.profiles import ProfileLibrary

    lib = ProfileLibrary(tmp_path / "profiles")
    monkeypatch.setattr(hub_module, "profiles", lib)

    async def _noop(self, *args, **kwargs):
        return None
    monkeypatch.setattr(Hub, "_teardown", _noop)
    monkeypatch.setattr(Hub, "poll_status", _noop)

    # global says auto-seeding is OFF; the profile that is being connected says ON.
    store.set_optics(Optics(focal_length_mm=530.0, auto_from_camera=False))
    prof = Profile(name="rig",
                   optics=Optics(focal_length_mm=1000.0, auto_from_camera=True))
    lib.save(prof)

    h = Hub()
    h.devices["camera"] = FakeCam()
    await h._apply_profile_unlocked(prof)

    saved = lib.get(prof.id)
    assert saved.optics.pixel_size_um == 3.76, "the profile's flag never ran"
    assert saved.optics.sensor_width_px == 6248
    assert saved.optics.focal_length_mm == 1000.0, "the seed clobbered the override"
    # and nothing was written into the block the override shadows.
    assert config_mod.config_store.cfg().optics.pixel_size_um == 0.0


async def test_auto_from_camera_still_writes_global_without_a_profile_override(
        store, monkeypatch, tmp_path):
    """The un-overridden rig keeps its old behaviour: no profile optics block
    means global config IS the winning layer, so that is where the seed goes."""
    import astrodeck.hub as hub_module
    from astrodeck.profiles import ProfileLibrary

    lib = ProfileLibrary(tmp_path / "profiles")
    monkeypatch.setattr(hub_module, "profiles", lib)

    async def _noop(self, *args, **kwargs):
        return None
    monkeypatch.setattr(Hub, "_teardown", _noop)
    monkeypatch.setattr(Hub, "poll_status", _noop)

    store.set_optics(Optics(focal_length_mm=530.0, auto_from_camera=True))
    prof = Profile(name="plain rig")
    lib.save(prof)

    h = Hub()
    h.devices["camera"] = FakeCam()
    await h._apply_profile_unlocked(prof)

    assert config_mod.config_store.cfg().optics.pixel_size_um == 3.76
    assert lib.get(prof.id).optics is None


# --------------------------------------------------------- the payload contract

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated app; the UI-facing contract is tested through the real route."""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module
    import astrodeck.hub as hub_mod
    from astrodeck.config import ConfigStore

    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        yield c, temp_store


def test_config_payload_carries_the_effective_block(client):
    """``_config_payload`` is the single choke point every config READ and every
    config-WRITE echo passes through, so the provenance has to hang there — a
    write echo that dropped it would leave the panel showing the raw global
    block again the moment the user saved anything."""
    c, _ = client
    for response in (c.get("/api/config"), c.post("/api/config", json={})):
        assert response.status_code == 200, response.text
        body = response.json()
        assert "effective" in body
        assert sorted(body["effective"]) == sorted(OVERRIDABLE_KEYS)
        entry = body["effective"]["providers.polar_align"]
        assert entry["layer"] in ("profile", "config", "camera", "default")


def test_effective_block_survives_json_serialization(client):
    """Every value in the block has to be JSON-native — a pydantic object or a
    numpy scalar leaking in here would 500 the console's boot read."""
    c, store_ = client
    store_.set_optics(GLOBAL_OPTICS)
    body = c.get("/api/config").json()["effective"]
    assert body["optics.focal_length_mm"]["value"] == 700.0
    assert body["optics.telescope_name"]["value"] == "global scope"


def test_row_filters_inert_provider_keys_but_keeps_auto():
    """A profile's ``providers`` dict accepts anything (an imported profile file
    posts it verbatim) and only four keys are ever read. Showing an inert key as
    an override would be a lie; hiding ``auto`` would hide a real one."""
    row = Profile(name="rig", providers={
        "polar_align": "auto", "not_a_capability": "astrodeck", "solve": 7,
    }).row()
    assert row["providers"] == {"polar_align": "auto"}
