"""A CLOUD FORECAST MAY NOT VETO. RAIN MAY. They are different hazards.

The auto-resume gate refused to start a night whenever the forecast predicted
sustained cloud in the next hour. That is the wrong instrument aimed at the
wrong hazard, and it cost two nights running:

    2026-08-19  forecast 100% from 22:00  ->  sky stayed clear past 02:00
    2026-08-20  forecast 100% from 21:30  ->  plate solve succeeded and the
                                              autofocus frame held 1386 stars

Both times the veto was a PREDICTION over a ~10 km grid cell, and both times the
telescope could see the actual sky above itself and disagreed.

The two hazards are not alike:

                    rain                        cloud
  at risk           the equipment               some frames
  acting late       irreversible                free - discard the frames
  acting early      lose a night                lose a night
  observable here   no (too late once wet)      YES - stars in the frame
  right instrument  forecast / radar            the camera

So the policy splits. Rain is the one hazard where a forecast is the RIGHT
instrument, because by the time water is on the corrector it is too late to
observe your way out of it. Cloud is the one where a forecast is the WRONG
instrument, because the rig holds a better one and the cost of testing it is a
few frames.

The engine already reasons this way once a run is going - see
`_safety_gate`: "THE SKY IS STILL EVIDENCE ... a cloudy verdict engages the
SELF-RELEASING hold, not the park path, because the detector has known false
modes and parking on one costs a re-slew and a re-solve for weather that may
pass." The resume gate was the one place that did not.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.config as config_mod
from astrodeck.config import ConfigStore
from astrodeck.weather import RAIN_VETO_MM, WeatherService


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", s)
    import astrodeck.weather as weather_mod
    monkeypatch.setattr(weather_mod, "config_store", s)
    # weather.enabled defaults to False, and a site is needed before a night
    # (and therefore ignore-tonight) resolves at all. The rig runs with both.
    cfg = s.cfg()
    cfg.weather.enabled = True
    # invented coordinates - the real site never goes in the tree
    cfg.site.latitude, cfg.site.longitude = 40.0, -105.0
    cfg.site.is_default = False
    s.bump_and_save()
    return s


def _svc(now: float, *, cloud: list[int] | None = None,
         precip: list[float] | None = None) -> WeatherService:
    """A service holding a 15-minute forecast series starting at `now`."""
    svc = WeatherService()
    n = max(len(cloud or []), len(precip or []))
    svc._om_times = [now + i * 900.0 for i in range(n)]
    svc._om_series = {
        "cloud_cover": list(cloud or [0] * n),
        "precipitation": list(precip or [0.0] * n),
    }
    svc._om_fetched_ts = now
    return svc


# --------------------------------------------------------------- cloud

def test_a_solid_cloud_forecast_does_NOT_veto(store):
    """THE REGRESSION. 100% cloud for the whole hour used to refuse the night.

    Both rig nights looked exactly like this and both skies were clear.
    """
    now = time.time()
    svc = _svc(now, cloud=[100] * 8)
    assert svc.veto_reason(now) is None, (
        "a cloud FORECAST vetoed the night - the sky above the telescope is "
        "measured every frame and is the better instrument")


def test_cloud_is_still_reported_it_just_does_not_gate(store):
    """Removing the veto must not remove the information: the operator still
    needs to know what tonight is forecast to do."""
    now = time.time()
    svc = _svc(now, cloud=[100] * 8)
    assert svc.cloud_outlook(now) is not None
    assert "100" in svc.cloud_outlook(now)


# ---------------------------------------------------------------- rain

def test_forecast_rain_DOES_veto(store):
    """The hazard a forecast is genuinely right for. No sustain requirement:
    one bucket of rain is enough, because the damage is instantaneous and you
    cannot un-wet a corrector."""
    now = time.time()
    svc = _svc(now, cloud=[10] * 8, precip=[0.0, 0.0, 0.8, 0.0, 0.0, 0.0, 0.0, 0.0])
    r = svc.veto_reason(now)
    assert r is not None and "rain" in r.lower(), r


def test_rain_beyond_the_hour_does_not_veto_yet(store):
    """A gate that fires on rain eight hours out never opens at all. The window
    is the same one the cloud gate used: the next hour."""
    now = time.time()
    far = [0.0] * 6 + [5.0] * 4          # rain starts 90 min out
    svc = _svc(now, cloud=[10] * 10, precip=far)
    assert svc.veto_reason(now) is None


def test_model_drizzle_noise_does_not_veto(store):
    """Forecast models emit trace amounts constantly. A gate that trips on 0.01
    mm would refuse every night and be turned off within a week."""
    now = time.time()
    svc = _svc(now, cloud=[10] * 8, precip=[0.02] * 8)
    assert svc.veto_reason(now) is None
    assert RAIN_VETO_MM > 0.02


def test_rain_vetoes_even_under_a_clear_sky_forecast(store):
    """The two signals are independent: rain out of a forecast-clear sky is
    exactly the case where the imaging camera would notice too late."""
    now = time.time()
    svc = _svc(now, cloud=[0] * 8, precip=[0.0, 1.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert svc.veto_reason(now) is not None


# ------------------------------------------------------- absence and escapes

def test_missing_precipitation_data_fails_OPEN(store):
    """Consistent with the module's existing stance: weather is advisory and a
    network blip must not end a night. We act on positive evidence of rain, not
    on the absence of evidence of no rain."""
    now = time.time()
    svc = WeatherService()
    svc._om_times = [now + i * 900.0 for i in range(8)]
    svc._om_series = {"cloud_cover": [100] * 8}      # no precipitation key at all
    svc._om_fetched_ts = now
    assert svc.veto_reason(now) is None


def test_a_stale_forecast_fails_open(store):
    now = time.time()
    svc = _svc(now, precip=[9.0] * 8)
    svc._om_fetched_ts = now - 86400                  # a day old
    assert svc.veto_reason(now) is None


def test_disabling_weather_disables_the_rain_gate_too(store):
    now = time.time()
    cfg = store.cfg()
    cfg.weather.enabled = False
    store.bump_and_save()
    svc = _svc(now, precip=[9.0] * 8)
    assert svc.veto_reason(now) is None


def test_ignore_tonight_still_works(store):
    """The operator's existing override has to cover the rain gate as well, or
    there is no way to say 'I have a roll-off roof, let me run'."""
    now = time.time()
    svc = _svc(now, precip=[9.0] * 8)
    svc.set_ignore_tonight(True, now=now)
    assert svc.veto_reason(now) is None


# ------------------------------------------------------- the parser, for real

def test_the_parser_keeps_millimetres_as_millimetres():
    """PRECIPITATION IS FRACTIONAL. The cloud series goes through a
    `max(0, min(100, int(v)))` clamp, which is right for a percentage and fatal
    for millimetres: 0.8 mm of rain becomes 0 and the gate never fires again,
    silently.

    Every other test in this file sets `_om_series` directly and would pass with
    the clamp applied to precipitation - a sabotage that did exactly that went
    undetected until this test existed.
    """
    from astrodeck.weather import _parse_open_meteo
    js = {"minutely_15": {
        "time": ["2026-08-21T04:00", "2026-08-21T04:15", "2026-08-21T04:30"],
        "cloud_cover": [10, 100, 55],
        "cloud_cover_low": [0, 0, 0],
        "cloud_cover_mid": [0, 0, 0],
        "cloud_cover_high": [10, 100, 55],
        "precipitation": [0.0, 0.8, 2.4],
    }}
    _times, series = _parse_open_meteo(js)
    assert series["precipitation"] == [0.0, 0.8, 2.4], series["precipitation"]
    assert series["cloud_cover"] == [10, 100, 55]


def test_the_fetch_actually_asks_for_precipitation():
    """A parser that handles a field nobody requested is dead code. The gate is
    only as real as the request that feeds it."""
    import inspect
    from astrodeck import weather as weather_mod
    src = inspect.getsource(weather_mod._fetch_open_meteo)
    assert "precipitation" in src, (
        "the Open-Meteo request does not ask for precipitation, so the rain "
        "gate has no data and can never fire")


def test_end_to_end_a_parsed_payload_vetoes(store):
    """Parse a realistic payload and put it straight through the gate, so the
    fetch/parse/veto chain is tested as one thing rather than three."""
    from astrodeck.weather import _parse_open_meteo
    import datetime as _dt
    base = _dt.datetime.now(_dt.timezone.utc).replace(second=0, microsecond=0)
    stamps = [(base + _dt.timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M")
              for i in range(4)]
    js = {"minutely_15": {
        "time": stamps,
        "cloud_cover": [0, 0, 0, 0],
        "cloud_cover_low": [0, 0, 0, 0],
        "cloud_cover_mid": [0, 0, 0, 0],
        "cloud_cover_high": [0, 0, 0, 0],
        "precipitation": [0.0, 0.0, 1.5, 0.0],
    }}
    times, series = _parse_open_meteo(js)
    svc = WeatherService()
    svc._om_times, svc._om_series = times, series
    svc._om_fetched_ts = time.time()
    assert svc.veto_reason(base.timestamp()) is not None
