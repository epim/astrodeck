"""PRO-11 file-naming templates — pure engine + per-target counter."""
from __future__ import annotations
import re
import pytest
from astrodeck.naming import (
    DEFAULT_TEMPLATE, capture_tokens, format_exposure_token,
    render_relative_path, sanitize_component, validate_template)


def test_default_template_byte_for_byte():
    f = dict(TARGET="M42", FRAMETYPE="Light", FILTER="Ha",
             DATE="2026-07-23", TIME="213045", FRAMENR="0001")
    assert render_relative_path(DEFAULT_TEMPLATE, f).as_posix() == \
        "M42/Light_M42_Ha_2026-07-23_213045_0001.fits"
    # no filter -> the empty piece drops, no double underscore (legacy parity)
    assert render_relative_path(DEFAULT_TEMPLATE, dict(f, FILTER="")).as_posix() == \
        "M42/Light_M42_2026-07-23_213045_0001.fits"


@pytest.mark.parametrize("gain,exposure,binning,expect", [
    (100, 300.0, 1, "M42_100_300_1.fits"),          # whole seconds -> plain int
    (100, 1.5, 2, "M42_100_1p5_2.fits"),            # fractional -> '.' becomes 'p'
    (0, 0.001, 1, "M42_0_0p001_1.fits"),            # sub-second bias/flat exposure
    (None, None, None, "M42.fits"),                 # absent values drop the tokens
])
def test_capture_setting_tokens_render(gain, exposure, binning, expect):
    """PRO-10 (e): the three new tokens render through the (unchanged) engine.
    A '.' inside a filename segment invites extension confusion, so a fractional
    exposure renders 1p5 — never 1.5."""
    tmpl = "$$TARGET$$_$$GAIN$$_$$EXPOSURE$$_$$BINNING$$"
    fields = dict(TARGET="M42", **capture_tokens(gain, exposure, binning))
    assert render_relative_path(tmpl, fields).as_posix() == expect
    validate_template(tmpl)                          # dry render must not raise


def test_exposure_token_edge_values():
    assert format_exposure_token(None) == ""
    assert format_exposure_token(float("nan")) == ""
    assert format_exposure_token(float("inf")) == ""
    assert format_exposure_token(300) == "300"       # int in, no ".0"
    # the formatted values are path-safe: no separators survive sanitization
    assert "/" not in format_exposure_token(2.25) and "." not in format_exposure_token(2.25)


def test_sanitizers_match_legacy():
    assert sanitize_component("NGC 7000", "loose") == "NGC 7000"   # target keeps space
    assert sanitize_component("@M42/x", "loose") == "_M42_x"
    assert sanitize_component("L Pro", "strict") == "L_Pro"         # filter -> underscore
    assert sanitize_component("_Ha_", "strict") == "Ha"


def test_folder_tokens_and_empty_folder_drop():
    f = dict(TARGET="M42", NIGHT="2026-07-23", FILTER="", FRAMETYPE="Light",
             DATE="2026-07-23", TIME="213045", FRAMENR="0007")
    tmpl = "$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$"
    # empty $$FILTER$$ folder level drops out entirely
    assert render_relative_path(tmpl, f).as_posix() == \
        "M42/2026-07-23/Light_0007.fits"


def test_no_path_traversal_from_values_or_literals():
    f = dict(TARGET="../../etc", FRAMETYPE="Light", FILTER="", DATE="d",
             TIME="t", FRAMENR="0001")
    rel = render_relative_path("$$TARGET$$/$$FRAMETYPE$$_$$FRAMENR$$", f)
    assert ".." not in rel.parts and not rel.is_absolute()


def test_validate_template():
    validate_template(DEFAULT_TEMPLATE)                 # ok
    for bad in ("", "$$NOPE$$", r"$$TARGET$$\x", "$$TARGET$$/"):
        with pytest.raises(ValueError):
            validate_template(bad)


# ------------------------------------------------------------- config.py (T2)

import astrodeck.config as configmod


def test_appconfig_naming_default_and_roundtrip(tmp_path):
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    assert store.cfg().naming.template == configmod.DEFAULT_TEMPLATE
    store.set_naming(configmod.NamingConfig(template="$$TARGET$$/$$FRAMENR$$"))
    assert store.reload().naming.template == "$$TARGET$$/$$FRAMENR$$"


def test_set_naming_rejects_bad_template(tmp_path):
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    with pytest.raises(ValueError):
        store.set_naming(configmod.NamingConfig(template="$$NOPE$$"))


def test_old_config_without_naming_key_loads(tmp_path):
    from astrodeck.persist import write_json_atomic
    p = tmp_path / "astrodeck.json"
    write_json_atomic(p, {"version": 1})          # legacy file, no naming key
    assert configmod.ConfigStore(path=p).cfg().naming.template == configmod.DEFAULT_TEMPLATE


# ------------------------------------------------------------------ hub.py (T3)

from pathlib import Path

import astrodeck.hub as hub_module
from astrodeck.hub import Hub


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(configmod, "config_store", store)
    return store


def test_hub_default_layout_byte_for_byte(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    p = Hub()._capture_path("M42", "Light", "Ha")
    assert p.parent == tmp_path / "M42"
    assert re.fullmatch(r"Light_M42_Ha_\d{4}-\d{2}-\d{2}_\d{6}_0001\.fits", p.name)


def test_counter_is_per_target_and_persists(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    h = Hub()
    assert h._capture_path("M42", "Light", "").name.endswith("_0001.fits")
    assert h._capture_path("M42", "Light", "").name.endswith("_0002.fits")
    assert h._capture_path("M31", "Light", "").name.endswith("_0001.fits")  # per-target
    # persists across a fresh Hub sharing CAPTURE_DIR
    assert Hub()._capture_path("M42", "Light", "").name.endswith("_0003.fits")


def test_custom_template_folders_by_night_and_filter(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    store.set_naming(configmod.NamingConfig(
        template="$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$"))
    p = Hub()._capture_path("M42", "Light", "Ha")
    assert p.relative_to(tmp_path).parts[:3] == ("M42",) + tuple(p.parent.parts[-2:])
    assert p.parent.name == "Ha" and p.name == "Light_0001.fits"


@pytest.mark.parametrize("gain,exposure_s,binning,expect", [
    (100, 300.0, 1, "g100_300s_bin1"),
    (0, 1.5, 2, "g0_1p5s_bin2"),        # sub-second uses the 'p' idiom
    (None, None, None, ""),             # nothing passed => the tokens drop out
])
def test_capture_settings_tokens_render_from_the_call_site(
        tmp_path, monkeypatch, gain, exposure_s, binning, expect):
    """$$GAIN$$/$$EXPOSURE$$/$$BINNING$$ must be THREADED, not just validated:
    they used to preview as M42/300s_g100/ and then render M42/s_g/ at capture
    time, collapsing every exposure and gain into one directory."""
    store = _isolate(tmp_path, monkeypatch)
    store.set_naming(configmod.NamingConfig(
        template="$$TARGET$$/g$$GAIN$$_$$EXPOSURE$$s_bin$$BINNING$$/$$FRAMENR$$"))
    p = Hub()._capture_path("M42", "Light", "", gain=gain, exposure_s=exposure_s,
                            binning=binning)
    assert p.name == "0001.fits"
    parts = p.relative_to(tmp_path).parts
    if expect:
        assert parts == ("M42", expect, "0001.fits")
    else:
        assert parts == ("M42", "g_s_bin", "0001.fits")   # literals only


async def test_capture_threads_the_settings_tokens(tmp_path, monkeypatch):
    """End-to-end through hub.capture(): the saved file really lands under the
    per-setting directory the settings preview promises."""
    store = _isolate(tmp_path, monkeypatch)
    store.set_naming(configmod.NamingConfig(
        template="$$TARGET$$/$$EXPOSURE$$s_g$$GAIN$$_bin$$BINNING$$/"
                 "$$FRAMETYPE$$_$$FRAMENR$$"))
    h = Hub()
    await h.connect_sim()
    try:
        await h.capture(0.5, 120, 30, 2, save=True, target="M42")
    finally:
        await h.disconnect_all()
    saved = Path(h.last_frame.saved_path)
    assert saved.parent == tmp_path / "M42" / "0p5s_g120_bin2"
    assert saved.name == "Light_0001.fits" and saved.exists()


# --------------------------------------------------------------- route (T4)

import astrodeck.api.app as app_module
from fastapi.testclient import TestClient


def _app_client(tmp_path, monkeypatch):
    """Isolated app: config + captures redirected to tmp (test_automation_api's
    client fixture idiom)."""
    temp_store = configmod.ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(configmod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    app = app_module.create_app()
    return TestClient(app), temp_store


def test_naming_route_saves_and_422s(tmp_path, monkeypatch):
    c, store = _app_client(tmp_path, monkeypatch)
    r = c.post("/api/config/naming", json={"template": "$$TARGET$$/$$FRAMENR$$"})
    assert r.status_code == 200, r.text
    assert store.cfg().naming.template == "$$TARGET$$/$$FRAMENR$$"
    assert r.json()["naming"]["template"] == "$$TARGET$$/$$FRAMENR$$"
    r = c.post("/api/config/naming", json={"template": "$$NOPE$$"})
    assert r.status_code == 422
    # rejected write did not clobber the previously-saved good template
    assert store.cfg().naming.template == "$$TARGET$$/$$FRAMENR$$"
