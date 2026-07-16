"""Sub-project C weather integration tests (weather spec 2026-07-16).

Task 1: WeatherConfig model + secret scrub + POST /api/config/weather.
Tasks 2-3 extend this file with WeatherService poller/veto/warning tests.

Repo convention (mirrors tests/test_survey.py / test_rbac_enforcement.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock.
"""
from __future__ import annotations

import pydantic
import pytest
from fastapi.testclient import TestClient

from astrodeck.config import (ConfigStore, ConfigVersionConflict, WeatherConfig,
                              redacted)


# --------------------------------------------------------------------- harness

def _make_app_client(tmp_path, monkeypatch):
    """Isolated full app + ConfigStore (test_rbac_enforcement._make_client
    shape). Default open provider => caller is admin (all caps). Also patches
    the weather module's config_store once weather.py exists (Task 2) so the
    lifespan-started poller reads the temp store, never the real one."""
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.auth import reset_active_provider
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    try:
        import astrodeck.weather as weather_mod
        monkeypatch.setattr(weather_mod, "config_store", store)
    except ImportError:
        pass  # weather.py lands in Task 2
    reset_active_provider()   # provider hygiene: never inherit a leaked fake
    return store, app_module.create_app()


# ----------------------------------------------------------- config (spec §2)

def test_weather_config_defaults_and_bounds(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    w = store.cfg().weather
    assert (w.enabled, w.cloud_threshold_pct, w.sustain_minutes,
            w.astrospheric_api_key) == (False, 50, 30, None)
    with pytest.raises(pydantic.ValidationError):
        WeatherConfig(cloud_threshold_pct=101)
    with pytest.raises(pydantic.ValidationError):
        WeatherConfig(cloud_threshold_pct=-1)
    with pytest.raises(pydantic.ValidationError):
        WeatherConfig(sustain_minutes=10)
    with pytest.raises(pydantic.ValidationError):
        WeatherConfig(sustain_minutes=241)


def test_old_config_without_weather_key_loads_with_defaults(tmp_path):
    """Append-tolerant load (spec §2): a persisted config predating the weather
    block deserializes fine with the protective defaults."""
    path = tmp_path / "astrodeck.json"
    path.write_text('{"version": 3}')
    store = ConfigStore(path=path)
    assert store.cfg().weather.enabled is False
    assert store.cfg().version == 3


def test_set_weather_persists_and_version_checks(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    v = store.cfg().version
    cfg = store.set_weather(
        WeatherConfig(enabled=True, cloud_threshold_pct=70, sustain_minutes=60,
                      astrospheric_api_key="k"), v)
    assert cfg.weather.enabled is True and cfg.version == v + 1
    # survives a reload from disk
    store2 = ConfigStore(path=tmp_path / "astrodeck.json")
    assert store2.cfg().weather.cloud_threshold_pct == 70
    assert store2.cfg().weather.astrospheric_api_key == "k"
    # stale optimistic-concurrency token -> conflict
    with pytest.raises(ConfigVersionConflict):
        store.set_weather(WeatherConfig(), v)


def test_redacted_scrubs_astrospheric_key(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_weather(WeatherConfig(astrospheric_api_key="SEKRIT"), None)
    data = redacted(store.cfg())
    assert data["weather"]["astrospheric_api_key"] is None
    assert data["weather"]["astrospheric_configured"] is True
    assert "SEKRIT" not in str(data)
    # unconfigured marker
    store.set_weather(WeatherConfig(), None)
    data = redacted(store.cfg())
    assert data["weather"]["astrospheric_configured"] is False


# ---------------------------------------------- POST /api/config/weather route

def test_config_weather_route_saves_and_bounds_422(tmp_path, monkeypatch):
    store, app = _make_app_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 60,
                        "sustain_minutes": 45, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 200, r.text
        assert store.cfg().weather.cloud_threshold_pct == 60
        # bounded fields 422 AT THE BOUNDARY (spec §13)
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 101,
                        "sustain_minutes": 45, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 422
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 5, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 422


def test_config_weather_key_write_contract(tmp_path, monkeypatch):
    """Plan decision 1: null/empty key = keep stored (UI round-trips the masked
    config); clear_astrospheric_key = explicit clear."""
    store, app = _make_app_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        base = {"enabled": True, "cloud_threshold_pct": 50, "sustain_minutes": 30}

        def post(weather, clear=False):
            return c.post("/api/config/weather", json={
                "weather": weather, "version": None,
                "clear_astrospheric_key": clear})

        assert post({**base, "astrospheric_api_key": "K1"}).status_code == 200
        assert store.cfg().weather.astrospheric_api_key == "K1"
        assert post({**base, "astrospheric_api_key": None}).status_code == 200
        assert store.cfg().weather.astrospheric_api_key == "K1"   # kept
        assert post({**base, "astrospheric_api_key": ""}).status_code == 200
        assert store.cfg().weather.astrospheric_api_key == "K1"   # kept
        assert post({**base, "astrospheric_api_key": None},
                    clear=True).status_code == 200
        assert store.cfg().weather.astrospheric_api_key is None   # cleared


def test_config_weather_409_body_never_leaks_key(tmp_path, monkeypatch):
    """Version conflict rides the SAME redaction seams as every config echo
    (spec §8): the 409 body carries the masked current config, never the key."""
    store, app = _make_app_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30,
                        "astrospheric_api_key": "SECRET-KEY-XYZ"},
            "version": None})
        assert r.status_code == 200
        assert "SECRET-KEY-XYZ" not in r.text          # config echo masked
        stale = 1  # store version has been bumped past 1 by the save above
        r2 = c.post("/api/config/weather", json={
            "weather": {"enabled": False, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": stale})
        assert r2.status_code == 409
        assert "SECRET-KEY-XYZ" not in r2.text         # 409 body masked
        assert store.cfg().weather.astrospheric_api_key == "SECRET-KEY-XYZ"
