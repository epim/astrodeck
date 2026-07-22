"""UX-05: user filter slot names + focuser offsets.

Covers the per-profile config store, hub.set_filter_names (apply + persist,
blank-keeps-hardware), connect-time seeding overlay, and the NINA-style filter
token in the saved filename.
"""
from __future__ import annotations

import astrodeck.config as configmod
import astrodeck.hub as hub_module
from astrodeck.hub import Hub


def test_filter_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE", tmp_path / "filter_names.json")
    configmod.save_filter_config("p1", ["Lum", "Red"], [0, 12])
    assert configmod.load_filter_config("p1") == {"names": ["Lum", "Red"], "offsets": [0, 12]}
    # another profile is isolated
    assert configmod.load_filter_config("other") == {}
    # a second profile doesn't clobber the first (merge into shared store)
    configmod.save_filter_config(None, ["Ha"], [5])
    assert configmod.load_filter_config(None)["names"] == ["Ha"]
    assert configmod.load_filter_config("p1")["names"] == ["Lum", "Red"]


def test_capture_path_includes_filter_token(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    assert "_Ha_" in h._capture_path("M42", "Light", "Ha").name
    # no active filter -> no extra token
    assert "_Ha_" not in h._capture_path("M42", "Light", "").name


async def test_set_filter_names_applies_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE", tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        n = len(fw.filter_names)
        hardware_slot1 = fw.filter_names[1]
        # blank slots keep the hardware fallback; slot 0 is renamed
        res = await h.set_filter_names(["Lumi"] + [""] * (n - 1), [7] + [0] * (n - 1))
        assert res["names"][0] == "Lumi"
        assert res["names"][1] == hardware_slot1
        assert fw.filter_offsets[0] == 7
        # persisted under the active profile (None -> default key)
        saved = configmod.load_filter_config(configmod.config_store.cfg().active_profile_id)
        assert saved["names"][0] == "Lumi"
    finally:
        await h.disconnect_all()


async def test_seed_filter_config_on_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE", tmp_path / "filter_names.json")
    # a saved name for the default profile is overlaid onto the wheel at connect
    configmod.save_filter_config(None, ["MyLum"], [3])
    h = Hub()
    await h.connect_sim()
    try:
        fw = h.devices["filterwheel"]
        assert fw.filter_names[0] == "MyLum"
        assert fw.filter_offsets[0] == 3
    finally:
        await h.disconnect_all()
