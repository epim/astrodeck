"""Config blocks that were API/config-file-only get a route the UI can call.

The gap this closes: an audit of AppConfig against ui/src found 41 leaf fields
no UI component touched. Most were auth secrets and server-owned crypto material
that correctly stay out of the UI, but safety limits, escalation policy,
calibration tolerances and the optics TELESCOP name were all real settings whose
only editor was a text file on the server.

The safety and escalation blocks already had a route (POST /api/config); what
they lacked was a caller. Calibration had neither, so it gets one here, with the
relational rule pydantic can't express checked at write time.
"""
from __future__ import annotations

import pytest

from astrodeck.config import CalibrationConfig, ConfigStore


@pytest.fixture
def store(tmp_path):
    # The store binds its path at construction, so pass it explicitly rather
    # than monkeypatching the module CONFIG_FILE (which a live singleton has
    # already read past).
    return ConfigStore(path=tmp_path / "astrodeck.json")


# ------------------------------------------------------- calibration tolerances

def test_set_calibration_persists_and_bumps_version(store):
    before = store.cfg().version
    cfg = store.set_calibration(CalibrationConfig(
        exposure_tol_pct=10, temp_tol_c=3, temp_bin_c=5,
        stack_sigma=2.5, max_stack_frames=50))
    assert cfg.calibration.exposure_tol_pct == 10
    assert cfg.calibration.max_stack_frames == 50
    assert cfg.version > before          # optimistic-concurrency token moved


def test_temp_bin_narrower_than_tolerance_is_rejected(store):
    """Two frames matching each other but landing in different stacking buckets
    silently halves the depth of every master — the failure looks like "my darks
    aren't helping", with nothing in any log to point at."""
    with pytest.raises(ValueError, match="at least the match tolerance"):
        store.set_calibration(CalibrationConfig(temp_tol_c=5, temp_bin_c=2))


def test_temp_bin_equal_to_tolerance_is_allowed(store):
    cfg = store.set_calibration(CalibrationConfig(temp_tol_c=4, temp_bin_c=4))
    assert cfg.calibration.temp_bin_c == 4


@pytest.mark.parametrize("field,value", [
    ("exposure_tol_pct", -1), ("exposure_tol_pct", 101),
    ("temp_tol_c", -1), ("temp_bin_c", 51),
    ("stack_sigma", 0), ("stack_sigma", 11),
    ("max_stack_frames", 0), ("max_stack_frames", 1001),
])
def test_out_of_range_values_are_rejected_by_the_model(field, value):
    """Bounds live on the model, so they reject at the route (422) rather than
    reaching the stacker."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CalibrationConfig(**{field: value})


# ---------------------------------------------------------------------- routes

def _client(monkeypatch, tmp_path):
    """Isolated app + store, following test_weather._make_app_client.

    The store must be patched into EVERY module that bound it at import time —
    `api.app` does `from ..config import config_store`, so reassigning
    `config.config_store` alone leaves the routes writing to the real singleton
    while the assertions read the temp one. (Which is exactly the false pass
    this helper produced on its first draft: green run-alone, red in-file.)"""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.auth import reset_active_provider
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    reset_active_provider()
    return TestClient(app_module.create_app()), store


def test_calibration_route_round_trips(monkeypatch, tmp_path):
    c, store = _client(monkeypatch, tmp_path)
    r = c.post("/api/config/calibration",
               json={"exposure_tol_pct": 12.5, "temp_tol_c": 1, "temp_bin_c": 5,
                     "stack_sigma": 3, "max_stack_frames": 200})
    assert r.status_code == 200, r.text
    assert r.json()["calibration"]["exposure_tol_pct"] == 12.5
    assert r.json()["calibration"]["max_stack_frames"] == 200


def test_calibration_route_422s_on_the_relational_rule(monkeypatch, tmp_path):
    c, store = _client(monkeypatch, tmp_path)
    r = c.post("/api/config/calibration",
               json={"temp_tol_c": 6, "temp_bin_c": 2})
    assert r.status_code == 422
    assert "match tolerance" in r.text


def test_safety_limits_route_accepts_the_fields_the_new_panel_sends(
        monkeypatch, tmp_path):
    """The panel echoes the WHOLE safety block (set_safety replaces it), so the
    fields it now edits have to survive that round trip unchanged."""
    c, store = _client(monkeypatch, tmp_path)
    body = store.cfg().safety.model_dump()
    body.update(min_alt_deg=17.5, twilight_deg=-18.0, on_unsafe="park",
                unsafe_consecutive=5, resume_when_safe=False,
                max_pause_min=45, poll_each_frame=False,
                enforce_pier_limits=True, preset="custom")
    r = c.post("/api/config", json={"safety": body})
    assert r.status_code == 200, r.text
    got = store.cfg().safety
    assert got.min_alt_deg == 17.5 and got.twilight_deg == -18.0
    assert got.on_unsafe == "park" and got.unsafe_consecutive == 5
    assert got.resume_when_safe is False and got.max_pause_min == 45
    assert got.poll_each_frame is False and got.enforce_pier_limits is True
    assert got.preset == "custom"
    # the solar fields ride along untouched in the echo
    assert got.solar_avoidance is True


def test_escalation_route_accepts_every_field_the_new_panel_edits(
        monkeypatch, tmp_path):
    c, store = _client(monkeypatch, tmp_path)
    r = c.post("/api/config", json={"escalation": {
        "require_cooling": True, "cooling_action": "abort",
        "require_guiding": True, "guiding_action": "skip",
        "af_failure_action": "abort", "hfr_reject_action": "retake",
        "hfr_retake_limit_per_target": 7, "no_progress_watchdog_s": 1800,
        "reconnect_resume": True, "reconnect_retries": 4}})
    assert r.status_code == 200, r.text
    got = store.cfg().escalation
    assert got.require_cooling and got.cooling_action == "abort"
    assert got.hfr_reject_action == "retake"
    assert got.hfr_retake_limit_per_target == 7
    assert got.no_progress_watchdog_s == 1800
    assert got.reconnect_resume and got.reconnect_retries == 4


def test_optics_route_carries_the_telescope_name(monkeypatch, tmp_path):
    """The FITS TELESCOP source. It was on the model and reachable by API, but
    no UI field wrote it, so every delivered frame shipped without the card."""
    c, store = _client(monkeypatch, tmp_path)
    version = store.cfg().version
    r = c.put("/api/optics", json={"optics": {
        "focal_length_mm": 400, "pixel_size_um": 3.76,
        "sensor_width_px": 6248, "sensor_height_px": 4176,
        "auto_from_camera": False, "telescope_name": "Askar FRA400"},
        "version": version})
    assert r.status_code == 200, r.text
    assert store.cfg().optics.telescope_name == "Askar FRA400"
