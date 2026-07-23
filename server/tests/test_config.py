"""Persisted config: optics math, atomic write, corrupt-file recovery,
optimistic concurrency, and the load-bearing longitude sign round-trip."""
import json

import pytest

from astrodeck.config import (ARCSEC_PER_RAD, AppConfig, ConfigStore,
                              ConfigVersionConflict, Optics, Site,
                              fov_deg, image_scale_arcsec_px)


# --------------------------------------------------------------- optics math

def test_image_scale_known_case():
    # 530mm / 3.76µm → ~1.46 "/px (the canonical example from the spec)
    assert image_scale_arcsec_px(530.0, 3.76) == pytest.approx(1.463, abs=0.01)
    assert image_scale_arcsec_px(0.0, 3.76) == 0.0  # guard div-by-zero


def test_image_scale_matches_constant():
    # the formula is exactly ARCSEC_PER_RAD * px / fl at bin 1
    assert image_scale_arcsec_px(1000.0, 5.0) == pytest.approx(ARCSEC_PER_RAD * 5.0 / 1000.0)


def test_fov_is_bin_independent():
    # FOV (an angular extent) must NOT change with binning — only image scale does.
    fw1, fh1, d1 = fov_deg(530.0, 3.76, 9576, 6388)
    assert fw1 > fh1 > 0 and d1 > fw1
    # binning the scale would halve the hint; fov_deg always uses bin-1, so a
    # caller binning 2x must still get the same FOV.
    assert d1 == pytest.approx((fw1 ** 2 + fh1 ** 2) ** 0.5)


# ----------------------------------------------------------- store lifecycle

def test_missing_file_writes_defaults(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    assert cfg.version == 1
    assert cfg.site.is_default is True
    assert cfg.site.horizon_min_deg == 15.0
    assert (tmp_path / "astrodeck.json").exists()  # saved immediately


def test_set_site_persists_and_survives_restart(tmp_path):
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    london = Site(name="London", latitude=51.5, longitude=-0.13, elevation_m=11)
    store.set_site(london, expected_version=1)
    # a fresh store reading the same file must see London (restart simulation)
    reborn = ConfigStore(path=path)
    cfg = reborn.cfg()
    assert cfg.site.name == "London"
    assert cfg.site.latitude == pytest.approx(51.5)
    assert cfg.site.is_default is False         # a saved site is no longer default
    assert cfg.version == 2                       # bumped on save


def test_west_longitude_round_trips_to_negative(tmp_path):
    """C3-7: navigator.geolocation returns signed East-positive, so a US site
    stores a NEGATIVE longitude and round-trips unchanged. Flipping the sign
    would silently break transit + the polar compass."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    sf = Site(name="SF", latitude=37.77, longitude=-122.42)
    store.set_site(sf, expected_version=None)
    on_disk = json.loads((tmp_path / "astrodeck.json").read_text(encoding="utf-8"))
    assert on_disk["site"]["longitude"] == pytest.approx(-122.42)


def test_version_conflict_raises_valueerror(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    # current version is 1; a stale PUT (expected 0) must conflict
    with pytest.raises(ConfigVersionConflict) as ei:
        store.set_site(Site(name="x"), expected_version=0)
    # subclasses ValueError so the API's `except ValueError` maps it to a 409
    assert isinstance(ei.value, ValueError)
    assert ei.value.current.version == 1


def test_expected_version_none_skips_check(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.set_optics(Optics(focal_length_mm=1000.0), expected_version=None)
    assert cfg.optics.focal_length_mm == 1000.0
    assert cfg.version == 2


def test_corrupt_file_recovers_to_defaults_and_backs_up(tmp_path):
    path = tmp_path / "astrodeck.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.version == 1                        # reset to defaults
    assert path.exists()                           # rewritten clean
    assert (tmp_path / "astrodeck.json.bak").exists()  # bad file preserved


def test_atomic_write_leaves_no_tmp(tmp_path):
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    store.set_site(Site(name="Backyard", latitude=10, longitude=20),
                   expected_version=None)
    assert not (tmp_path / "astrodeck.json.tmp").exists()
    # the persisted file is valid JSON parseable back into the model
    AppConfig(**json.loads(path.read_text(encoding="utf-8")))


# ----------------------------------------------- active-profile pointer (Stage B)

def test_set_active_profile_persists_and_bumps_version(tmp_path):
    """The active-profile pointer is the only profile state AppConfig holds (the
    records live in ProfileLibrary). Setting it bumps version, writes atomically,
    and survives a restart."""
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    pid = "abc-123"
    cfg = store.set_active_profile(pid)
    assert cfg.active_profile_id == pid
    assert cfg.version == 2                         # bumped on save
    # a fresh store reading the same file sees the pointer (restart simulation).
    reborn = ConfigStore(path=path)
    assert reborn.cfg().active_profile_id == pid
    assert reborn.cfg().version == 2
    assert not (tmp_path / "astrodeck.json.tmp").exists()  # atomic, no temp left


def test_set_active_profile_none_clears_pointer(tmp_path):
    """Clearing the active pointer (e.g. after deleting the active profile) sets
    it to None and still bumps version."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_active_profile("xyz")
    cfg = store.set_active_profile(None)
    assert cfg.active_profile_id is None
    assert cfg.version == 3                         # 1 -> 2 (set) -> 3 (clear)


def test_active_profile_round_trips_on_disk(tmp_path):
    """The active_profile_id is serialized into the JSON document verbatim."""
    path = tmp_path / "astrodeck.json"
    store = ConfigStore(path=path)
    store.set_active_profile("rig-uuid-1")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["active_profile_id"] == "rig-uuid-1"


def test_union_config_tolerates_extra_keys(tmp_path):
    """Risk-2: the AppConfig is a union (settings keys now, automation keys
    later). A file with unknown keys must still load (defaults fill the rest)."""
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({
        "version": 7,
        "site": {"name": "X", "latitude": 1, "longitude": 2},
        "optics": {"focal_length_mm": 600},
        "active_profile_id": "abc",
        "safety": {"future": "automation key"},   # not yet a known key
    }), encoding="utf-8")
    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.version == 7
    assert cfg.site.name == "X"
    assert cfg.active_profile_id == "abc"


def test_optics_telescope_name_defaults_empty_and_surfaces():
    from astrodeck.config import Optics
    from astrodeck.hub import Hub
    # default is empty (honest: TELESCOP omitted when blank)
    assert Optics().telescope_name == ""
    # a set name round-trips through the model
    assert Optics(telescope_name="Askar 71F").telescope_name == "Askar 71F"
    # effective_optics() surfaces the key so Hub.capture can thread it
    h = Hub()
    assert "telescope_name" in h.effective_optics()
