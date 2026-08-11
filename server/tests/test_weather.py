"""Sub-project C weather integration tests (weather spec 2026-07-16).

Task 1: WeatherConfig model + secret scrub + POST /api/config/weather.
Tasks 2-3 extend this file with WeatherService poller/veto/warning tests.

Repo convention (mirrors tests/test_survey.py / test_rbac_enforcement.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock.
"""
from __future__ import annotations

import time
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


# ============================================================ WeatherService
# Poller cadence / fetchers / cache / staleness / payload (spec §3, §7, §14).
# Injected clock + monkeypatched httpx (test_survey _FakeClient/_Boom +
# test_resume_arm clock patterns). The bus is replaced by a recorder so
# publish/log assertions need no asyncio queue draining.

import httpx  # noqa: E402  (section import, mirrors test_survey.py style)

import astrodeck.licensing as licensing_mod  # noqa: E402
import astrodeck.weather as weather_mod  # noqa: E402
from astrodeck.weather import (ASTROSPHERIC_INTERVAL_S,  # noqa: E402
                               OPEN_METEO_INTERVAL_S, OPEN_METEO_STALE_S,
                               WeatherService)

BASE = 1_700_000_000.0 - (1_700_000_000.0 % 900.0)   # 15-min-aligned anchor


def _iso_minute(ts: float) -> str:
    """Open-Meteo minutely_15 time format: ISO-8601 to the minute, no zone
    suffix (timezone=UTC is requested, so times are UTC)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _om_payload(cloud, *, start: float = BASE, low=None, mid=None, high=None):
    n = len(cloud)
    return {"minutely_15": {
        "time": [_iso_minute(start + i * 900) for i in range(n)],
        "cloud_cover": cloud,
        "cloud_cover_low": low if low is not None else [0] * n,
        "cloud_cover_mid": mid if mid is not None else [0] * n,
        "cloud_cover_high": high if high is not None else list(cloud),
    }}


def _astro_payload(*, start: float = BASE, seeing=(2.0, 3.0),
                   trans=(21.0, 22.0), credits=20):
    from datetime import datetime, timezone
    return {
        "UTCStartTime": datetime.fromtimestamp(start, tz=timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%S"),
        "ModelTime": "2026071600",
        "APICreditUsedToday": credits,
        "Astrospheric_Seeing": [{"Value": {"ActualValue": v}} for v in seeing],
        "Astrospheric_Transparency": [{"Value": {"ActualValue": v}} for v in trans],
    }


class _FakeJsonResp:
    def __init__(self, js, status: int = 200):
        self._js = js
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._js


class _FakeWxClient:
    """Serves Open-Meteo GETs and Astrospheric POSTs; counts calls."""
    om_payload: dict = {}
    astro_payload: dict = {}
    fail_om = False
    fail_astro = False
    om_calls = 0
    astro_calls = 0
    last_astro_body = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        type(self).om_calls += 1
        if type(self).fail_om:
            raise httpx.ConnectError("down")
        return _FakeJsonResp(type(self).om_payload)

    async def post(self, url, json=None):
        type(self).astro_calls += 1
        type(self).last_astro_body = json
        if type(self).fail_astro:
            raise httpx.ConnectError("down")
        return _FakeJsonResp(type(self).astro_payload)


class _BoomWx:
    """httpx.AsyncClient stand-in whose CONSTRUCTION fails the test (the
    survey _Boom zero-httpx-when-disabled invariant, spec §16)."""
    def __init__(self, *a, **kw):
        raise AssertionError("httpx client constructed while weather disabled")


class _BusRecorder:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.logs: list[tuple[str, str, str]] = []

    def publish(self, type: str, **data):
        self.published.append((type, data))

    def log(self, level: str, message: str, source: str = "hub"):
        self.logs.append((level, message, source))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """Isolated WeatherService: temp ConfigStore (enabled, real site), injected
    wall clock, recorder bus, _FakeWxClient httpx. Night resolution defaults to
    None (inert warning path) — latch tests re-patch _tonight themselves."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    site = store.cfg().site
    site.latitude, site.longitude, site.is_default = 34.2, -118.1, False
    store.cfg().weather.enabled = True
    monkeypatch.setattr(weather_mod, "config_store", store)
    rec = _BusRecorder()
    monkeypatch.setattr(weather_mod, "bus", rec)
    monkeypatch.setattr(weather_mod.httpx, "AsyncClient", _FakeWxClient)
    _FakeWxClient.om_payload = _om_payload([0] * 8)
    _FakeWxClient.astro_payload = _astro_payload()
    _FakeWxClient.fail_om = _FakeWxClient.fail_astro = False
    _FakeWxClient.om_calls = _FakeWxClient.astro_calls = 0
    _FakeWxClient.last_astro_body = None
    monkeypatch.setattr(WeatherService, "_tonight", lambda self, t: None)
    # #200: the Astrospheric client is inert until this instance has
    # acknowledged their terms. Isolated to tmp_path (a developer's real
    # acknowledgment must never decide a test, in either direction) and granted
    # here, so every test below keeps measuring what it was written to measure —
    # cadence, body shape, fail-soft. The GATE itself is tested separately, on a
    # fixture with no acknowledgment.
    monkeypatch.setattr(licensing_mod, "CONSENT_FILE",
                        tmp_path / "restricted_consent.json")
    licensing_mod.acknowledge("astrospheric", by="test")
    now = {"t": BASE}
    return WeatherService(clock=lambda: now["t"]), now, store, rec


async def test_open_meteo_cadence_due_not_due(svc):
    s, now, store, rec = svc
    await s.tick()
    assert _FakeWxClient.om_calls == 1
    now["t"] += 300
    await s.tick()                                # 5 min later: not due
    assert _FakeWxClient.om_calls == 1
    now["t"] += 601                               # past the 15-min mark
    await s.tick()
    assert _FakeWxClient.om_calls == 2


async def test_astrospheric_six_hourly_key_gated_exact_body(svc):
    s, now, store, rec = svc
    await s.tick()
    assert _FakeWxClient.astro_calls == 0          # no key -> NEVER called
    store.cfg().weather.astrospheric_api_key = "k-123"
    now["t"] += OPEN_METEO_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 1
    # exact verified body casing (spec §3) — the key rides ONLY here
    assert _FakeWxClient.last_astro_body == {
        "Latitude": 34.2, "Longitude": -118.1, "APIKey": "k-123"}
    now["t"] += 3600.0
    await s.tick()                                 # 1 h later: 6 h cadence holds
    assert _FakeWxClient.astro_calls == 1
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 2


async def test_astrospheric_never_calls_out_until_this_instance_agrees(
        svc, monkeypatch, tmp_path):
    """#200. Their Data API is scoped to "Astrospheric Professional members for
    use in personal projects" and AstroDeck is a public product.

    Nothing is being REDISTRIBUTED here, so unlike DSS2 there is no file to stop
    shipping — the REQUEST is the thing outside their scope. So a configured key
    is not enough on its own: the request does not go out until somebody with
    authority over this deployment has stated, on the record, that this is such
    a use. Open-Meteo is untouched, because the weather panel must still work.
    """
    s, now, store, rec = svc
    licensing_mod.withdraw("astrospheric")
    store.cfg().weather.astrospheric_api_key = "k-123"
    await s.tick()
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 0, (
        "a request went to Astrospheric with no acknowledgment on record")
    assert _FakeWxClient.om_calls > 0, (
        "Open-Meteo was collateral damage — the forecast must still work")

    # …and the moment it IS acknowledged, on the next due tick, it works.
    licensing_mod.acknowledge("astrospheric", by="test")
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 1


async def test_an_acknowledgment_can_be_taken_back(svc, monkeypatch):
    """A consent you cannot withdraw is not a consent. The integration has to go
    inert again, not merely stop being advertised."""
    s, now, store, rec = svc
    store.cfg().weather.astrospheric_api_key = "k-123"
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 1
    assert licensing_mod.withdraw("astrospheric") is True
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    assert _FakeWxClient.astro_calls == 1, "withdrawing did not stop the client"


async def test_disabled_zero_httpx_and_flip_within_one_tick(svc, monkeypatch):
    s, now, store, rec = svc
    store.cfg().weather.enabled = False
    monkeypatch.setattr(weather_mod.httpx, "AsyncClient", _BoomWx)
    await s.tick()                                 # disabled: ZERO construction
    now["t"] += OPEN_METEO_INTERVAL_S
    await s.tick()
    store.cfg().weather.enabled = True             # runtime flip...
    monkeypatch.setattr(weather_mod.httpx, "AsyncClient", _FakeWxClient)
    now["t"] += 60
    await s.tick()                                 # ...takes effect within one tick
    assert _FakeWxClient.om_calls == 1


async def test_default_site_never_fetches(svc, monkeypatch):
    s, now, store, rec = svc
    store.cfg().site.is_default = True             # plan decision 2
    monkeypatch.setattr(weather_mod.httpx, "AsyncClient", _BoomWx)
    await s.tick()                                 # no site: ZERO construction


async def test_disable_publishes_one_cleared_payload(svc):
    s, now, store, rec = svc
    await s.tick()                                 # enabled fetch + publish
    store.cfg().weather.enabled = False
    now["t"] += 60
    await s.tick()                                 # first disabled tick: cleared payload
    cleared = [d for t, d in rec.published if t == "weather" and not d["enabled"]]
    assert len(cleared) == 1
    assert cleared[0]["forecast"] is None
    assert cleared[0]["astrospheric"] is None
    assert cleared[0]["alert"] is None
    now["t"] += 60
    await s.tick()                                 # second disabled tick: silent
    assert len([d for t, d in rec.published
                if t == "weather" and not d["enabled"]]) == 1


async def test_failure_keeps_last_forecast_and_marks_stale(svc):
    s, now, store, rec = svc
    _FakeWxClient.om_payload = _om_payload([10] * 8)
    await s.tick()
    p = s.payload(now["t"])
    assert p["forecast"]["cloud"] == [10] * 8 and p["stale"] is False
    _FakeWxClient.fail_om = True
    for _ in range(4):                             # 4 failed cycles = 60 min
        now["t"] += OPEN_METEO_INTERVAL_S
        await s.tick()
    p = s.payload(now["t"])
    assert p["forecast"]["cloud"] == [10] * 8      # failure NEVER cached; last kept
    assert p["stale"] is True                      # age > 45 min
    fail_logs = [m for lvl, m, src in rec.logs if "open-meteo fetch failed" in m]
    assert fail_logs                               # outcome-only logging...
    assert "34.2" not in fail_logs[0] and "118.1" not in fail_logs[0]  # ...no coords


async def test_astrospheric_independent_fail_soft(svc):
    s, now, store, rec = svc
    store.cfg().weather.astrospheric_api_key = "k-123"
    _FakeWxClient.om_payload = _om_payload([5] * 8)
    _FakeWxClient.fail_astro = True
    await s.tick()
    p = s.payload(now["t"])
    assert p["forecast"] is not None               # Open-Meteo landed anyway
    assert p["astrospheric"] is None               # Astrospheric failed soft
    _FakeWxClient.fail_astro = False
    _FakeWxClient.fail_om = True
    now["t"] += ASTROSPHERIC_INTERVAL_S
    await s.tick()
    p = s.payload(now["t"])
    assert p["astrospheric"] is not None           # ...and vice versa
    assert p["astrospheric"]["credits_used_today"] == 20
    assert p["astrospheric"]["seeing"] == [2.0, 3.0]
    assert p["astrospheric"]["transparency"] == [21.0, 22.0]
    assert p["astrospheric"]["stale"] is False


async def test_payload_shape_matches_spec_7(svc):
    s, now, store, rec = svc
    store.cfg().weather.astrospheric_api_key = "k-123"
    _FakeWxClient.om_payload = _om_payload([1, 2, 3, 4])
    await s.tick()
    p = s.payload(now["t"])
    assert set(p) == {"enabled", "fetched_ts", "stale", "ignore_tonight",
                      "threshold_pct", "sustain_minutes", "site_lat",
                      "site_lon", "forecast", "astrospheric", "alert"}
    assert p["enabled"] is True and p["ignore_tonight"] is False
    assert p["threshold_pct"] == 50 and p["sustain_minutes"] == 30
    f = p["forecast"]
    assert set(f) == {"times", "cloud", "cloud_low", "cloud_mid", "cloud_high"}
    assert f["times"][0] == weather_mod._iso_z(BASE)       # ISO Z timestamps
    assert f["cloud"] == [1, 2, 3, 4]
    a = p["astrospheric"]
    assert set(a) == {"times", "seeing", "transparency", "fetched_ts", "stale",
                      "credits_used_today"}
    assert p["alert"] is None
    # site_lat/site_lon DO ride this payload (2026-07-17 decisions wave I2 --
    # the one deliberate exception; see weather.WeatherService.payload()). The
    # svc fixture seeds a real, non-default site (34.2, -118.1).
    assert p["site_lat"] == 34.2 and p["site_lon"] == -118.1
    # a successful refresh published the same shape on the bus
    published = [d for t, d in rec.published if t == "weather"]
    assert published and set(published[-1]) == set(p)


async def test_payload_site_coords_null_on_default_site(svc):
    """The default (0, 0) site is not a real fix -- site_lat/site_lon stay
    null (same "no real site = no data" convention as the rest of the
    service, e.g. test_default_site_never_fetches)."""
    s, now, store, rec = svc
    store.cfg().site.is_default = True
    p = s.payload(now["t"])
    assert p["site_lat"] is None and p["site_lon"] is None


# ==================================================== breach / veto / warning
# Spec §4 (consecutive-sample sustained veto, fail-open), §5 (once-per-night
# latch), §14 test matrix rows.


async def test_veto_consecutive_sample_rule_and_reason_string(svc):
    s, now, store, rec = svc
    store.cfg().weather.cloud_threshold_pct = 50
    store.cfg().weather.sustain_minutes = 30       # -> 2 consecutive samples
    # a single >=50 sample inside [now, now+60min] is NOT sustained
    _FakeWxClient.om_payload = _om_payload([40, 60, 40, 40, 40, 40, 40, 40])
    await s.tick()
    assert s.veto_reason(now["t"]) is None
    # two CONSECUTIVE samples >= 50 inside the hour ARE (peak = 70)
    _FakeWxClient.om_payload = _om_payload([40, 60, 70, 40, 40, 40, 40, 40])
    now["t"] += OPEN_METEO_INTERVAL_S
    await s.tick()
    assert s.veto_reason(now["t"]) == \
        "cloud cover 70% forecast within the next hour (threshold 50%)"


async def test_veto_sustain_longer_than_window_never_fires(svc):
    """Plan decision 5 (spec-literal edge): sustain 240 -> 16 consecutive
    samples needed, but [now, now+60min] holds at most 5 -> no veto even at
    100% cloud. The NIGHT warning still covers this (full dark window)."""
    s, now, store, rec = svc
    store.cfg().weather.sustain_minutes = 240
    _FakeWxClient.om_payload = _om_payload([100] * 8)
    await s.tick()
    assert s.veto_reason(now["t"]) is None


async def test_veto_fail_open_when_stale(svc):
    s, now, store, rec = svc
    _FakeWxClient.om_payload = _om_payload([90] * 8)
    await s.tick()
    assert s.veto_reason(now["t"]) is not None     # baseline: breach vetoes
    _FakeWxClient.fail_om = True
    now["t"] += OPEN_METEO_STALE_S + 900           # age past 45 min, fetch dead
    await s.tick()
    assert s.veto_reason(now["t"]) is None         # stale -> FAIL-OPEN (spec §4)


async def test_veto_none_when_disabled(svc):
    s, now, store, rec = svc
    _FakeWxClient.om_payload = _om_payload([90] * 8)
    await s.tick()
    store.cfg().weather.enabled = False
    assert s.veto_reason(now["t"]) is None


async def test_ignore_tonight_set_expire_and_no_night(svc, monkeypatch):
    from astrodeck.weather import NoNightError
    s, now, store, rec = svc
    _FakeWxClient.om_payload = _om_payload([90] * 8)
    await s.tick()
    dusk, dawn = BASE - 3600.0, BASE + 8 * 3600.0
    monkeypatch.setattr(
        WeatherService, "_tonight",
        lambda self, t: (dusk, dawn) if t < dawn
        else (dusk + 86400.0, dawn + 86400.0))
    assert s.veto_reason(now["t"]) is not None     # breach vetoes...
    s.set_ignore_tonight(True, now["t"])
    assert s.veto_reason(now["t"]) is None         # ...until overridden
    assert s.payload(now["t"])["ignore_tonight"] is True
    # a NEW night has a new dusk key -> the flag auto-expires
    assert s._ignore_active(dawn + 3600.0) is False
    # clearing works
    s.set_ignore_tonight(False, now["t"])
    assert s.veto_reason(now["t"]) is not None
    # no resolvable night -> NoNightError (route maps to 409 no_night)
    monkeypatch.setattr(WeatherService, "_tonight", lambda self, t: None)
    with pytest.raises(NoNightError):
        s.set_ignore_tonight(True, now["t"])


async def test_night_warning_latch_once_per_night_and_reset(svc, monkeypatch):
    s, now, store, rec = svc
    store.cfg().weather.cloud_threshold_pct = 50
    store.cfg().weather.sustain_minutes = 30
    dusk, dawn = BASE + 3 * 3600.0, BASE + 12 * 3600.0
    monkeypatch.setattr(WeatherService, "_tonight", lambda self, t: (dusk, dawn))
    n = 96
    cloud = [0] * n
    hi = [0] * n
    for i in range(16, 20):        # 1 h sustained breach starting BASE + 4 h
        cloud[i] = 80
        hi[i] = 75
    _FakeWxClient.om_payload = _om_payload(cloud, high=hi)
    await s.tick()
    p = s.payload(now["t"])
    assert p["alert"] == {
        "kind": "high_cloud",
        "start_iso": weather_mod._iso_z(BASE + 16 * 900),
        "end_iso": weather_mod._iso_z(BASE + 19 * 900),
        "peak_pct": 80,
        "dominant_layer": "high",
    }
    warn_logs = [m for lvl, m, src in rec.logs
                 if lvl == "warning" and "high cloud forecast tonight" in m]
    assert len(warn_logs) == 1
    assert "peak 80%" in warn_logs[0] and "high layer" in warn_logs[0]
    # B log rule: times + percentages ONLY — never coordinates
    assert "34.2" not in warn_logs[0] and "118.1" not in warn_logs[0]
    # #228: the window is quoted in LOCAL time. It used to be formatted in UTC
    # and labelled "tonight", so on 2026-08-11 an operator read "09:15–12:15"
    # — mid-morning, hours after dawn — for the window the modal correctly
    # showed as 02:15–05:15. Same window, seven hours apart, printed beside a
    # log stamp that events.py renders in local time.
    expect = (f"{time.strftime('%H:%M', time.localtime(BASE + 16 * 900))}–"
              f"{time.strftime('%H:%M', time.localtime(BASE + 19 * 900))}")
    assert expect in warn_logs[0], (
        f"banner says {warn_logs[0]!r}; local window is {expect}")
    # same night, next refresh: latched — no second log, alert still rides
    now["t"] += OPEN_METEO_INTERVAL_S
    await s.tick()
    assert len([m for lvl, m, src in rec.logs
                if "high cloud forecast tonight" in m]) == 1
    assert s.payload(now["t"])["alert"] is not None
    # past dawn the alert leaves the payload (spec §5)
    assert s.payload(dawn + 60.0)["alert"] is None
    # a NEW night key resets the latch: a breach the next night alerts again
    dusk2, dawn2 = dusk + 86400.0, dawn + 86400.0
    monkeypatch.setattr(WeatherService, "_tonight",
                        lambda self, t: (dusk2, dawn2))
    cloud2 = [0] * n
    for i in range(16, 20):
        cloud2[i] = 90
    _FakeWxClient.om_payload = _om_payload(cloud2, start=BASE + 86400.0,
                                           high=list(cloud2))
    now["t"] = BASE + 86400.0
    await s.tick()
    assert len([m for lvl, m, src in rec.logs
                if "high cloud forecast tonight" in m]) == 2


async def test_no_night_means_no_warning_evaluation(svc):
    """Polar day / default site: _tonight() -> None -> no evaluation, no alert
    (spec §5). The svc fixture already pins _tonight to None."""
    s, now, store, rec = svc
    _FakeWxClient.om_payload = _om_payload([100] * 8)
    await s.tick()
    assert s.payload(now["t"])["alert"] is None
    assert not [m for lvl, m, src in rec.logs
                if "high cloud forecast tonight" in m]
