"""Weather forecast service (sub-project C, weather spec §3-§5, §7).

One service class owning fetch, cache, alert latch, and veto logic; a
module-level singleton (``weather_service``) wired into the app lifespan like
``resume_arm``. The poller task ticks every 60 s and no-ops unless
``cfg.weather.enabled`` AND the config site is set (non-default; pydantic
bounds keep lat/lon finite) — so runtime config toggles take effect within one
tick, no service restart.

Invariants (spec §16):
- ZERO outbound httpx when disabled (survey ``_Boom`` pattern, test-enforced).
- Failures are NEVER cached: on a fetch failure the previous forecast is kept
  and staleness is derived from its age.
- Failure logging is outcome-only (exception type) — never coordinates, never
  a URL with a lat/lon query (B log rule).
- Astrospheric cadence is 6 h and MUST NOT be increased: each call costs 5 API
  credits on a 100/day Pro budget, and their model only updates every 6 h.
- The Astrospheric key appears ONLY in the request body — never in logs, never
  in any payload.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from .config import config_store
from .events import bus
from .sequence import schedule

CHECK_INTERVAL_S = 60.0
#: Millimetres of forecast precipitation in one 15-minute bucket that count as
#: RAIN for the auto-resume veto.
#:
#: Not zero. Forecast models emit trace amounts more or less constantly, and a
#: gate that tripped on 0.01 mm would refuse every night and be switched off
#: within a week - which is how a safety feature becomes decorative. 0.1 mm is
#: below anything that would wet an OTA and above the models' noise floor.
RAIN_VETO_MM = 0.1

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
ASTROSPHERIC_URL = ("https://astrosphericpublicaccess.azurewebsites.net"
                    "/api/GetForecastData_V1")
_USER_AGENT = "AstroDeck/0.1"
_TIMEOUT_S = 6.0

OPEN_METEO_INTERVAL_S = 15 * 60.0      # spec §3: every 15 min (~96 calls/day)
ASTROSPHERIC_INTERVAL_S = 6 * 3600.0   # spec §3: every 6 h — NEVER faster
OPEN_METEO_STALE_S = 45 * 60.0         # spec §3: stale > 45 min
ASTROSPHERIC_STALE_S = 12 * 3600.0     # spec §3: stale > 12 h (two model cycles)
_MAX_SAMPLES = 192                     # keep <= 48 h of 15-min samples


class NoNightError(Exception):
    """No night resolves for the configured site (polar day / default site)."""


def _iso_z(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ fetchers

#: The pressure levels the wind column is read at, hPa.
#:
#: THEY RIDE THE FORECAST REQUEST rather than a second one. The cloud-occlusion
#: model corroborates its measured cloud motion against the wind aloft, and
#: this module already holds the only connection to Open-Meteo there is. A
#: second client for the same host would double the traffic, double the failure
#: modes, and double the number of places a site coordinate leaves the rig --
#: for data that costs nothing to append here.
#:
#: 850 to 200 hPa is roughly 1.5 km to 12 km, which brackets every layer cloud
#: is advected in. Which level a measurement matches is NOT chosen by the
#: cloud's height: tracking by height band is precisely the thing that does not
#: work (design 2 §2.5 got 126 km/h for cloud below 2 km against a 17 km/h wind
#: there), so the whole column is offered and the consumer asks whether the
#: measurement resembles ANY of it.
WIND_LEVELS_HPA: tuple[int, ...] = (850, 700, 500, 400, 300, 250, 200)
#: How far from ``now`` an hourly sample may sit and still describe it. The
#: series is hourly, so a series covering now is never more than 30 min away;
#: past an hour there is no sample for this moment and the honest answer is an
#: empty column rather than the nearest thing on file.
WIND_COLUMN_MAX_AGE_S = 3600.0

#: The surface observations, and the response key each one is read back under.
#:
#: ``(payload_key, (requested_name, *accepted_aliases))``. The FIRST name is
#: what the request asks for and what Open-Meteo therefore echoes back; the
#: rest are the current-generation spellings of the same variable, accepted on
#: the way in so a future rename upstream (or a body recorded from the other
#: spelling) reads as data rather than as a missing series.
#:
#: THEY RIDE THE SAME REQUEST as the pressure-level winds above, for the same
#: reasons: one connection to Open-Meteo, one place a site coordinate leaves
#: the rig. See WIND_LEVELS_HPA.
#:
#: Units are Open-Meteo's defaults, which this request does not override:
#: degrees Celsius, percent, km/h, degrees. The payload key names say so.
SURFACE_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("temp_c", ("temperature_2m",)),
    ("dewpoint_c", ("dewpoint_2m", "dew_point_2m")),
    ("humidity_pct", ("relativehumidity_2m", "relative_humidity_2m")),
    ("wind_kmh", ("windspeed_10m", "wind_speed_10m")),
    ("wind_dir_deg", ("winddirection_10m", "wind_direction_10m")),
    ("gust_kmh", ("windgusts_10m", "wind_gusts_10m")),
)
#: How far from ``now`` a surface sample may sit and still describe it. The
#: same rule, and the same reasoning, as ``WIND_COLUMN_MAX_AGE_S``: the series
#: is hourly, so one that covers this moment is never more than 30 min away,
#: and past an hour the honest answer is no reading rather than the nearest
#: thing on file presented as the weather outside.
SURFACE_MAX_AGE_S = WIND_COLUMN_MAX_AGE_S
#: Metres of cloud base per degree Celsius of temperature/dew-point spread.
#:
#: The lifting condensation level, the standard field approximation (Espy's
#: rule): ~125 m of height above the SURFACE for every degree the air is above
#: its dew point. It is an estimate of the base of convective cloud, not a
#: measurement and not a ceiling report -- a layer advected in from elsewhere
#: sits where it sits, and this number says nothing about it.
LCL_M_PER_DEG_C = 125.0


def _wind_fields() -> list[str]:
    """The ``hourly=`` field list for the pressure-level winds."""
    fields: list[str] = []
    for level in WIND_LEVELS_HPA:
        fields.append("wind_speed_%dhPa" % level)
        fields.append("wind_direction_%dhPa" % level)
        fields.append("geopotential_height_%dhPa" % level)
    return fields


def _surface_request_fields() -> list[str]:
    """The ``hourly=`` field list for the surface observations."""
    return [names[0] for _key, names in SURFACE_FIELDS]


def cloud_base_m(temp_c: float | None,
                 dewpoint_c: float | None) -> float | None:
    """Estimated cloud base above the site, metres, or None.

    ``LCL_M_PER_DEG_C * (T - Td)``, clamped at zero (saturated air condenses at
    the ground; a negative height is not a thing). None when either input is
    missing: a spread nobody measured is not a clear sky.
    """
    if temp_c is None or dewpoint_c is None:
        return None
    return max(0.0, LCL_M_PER_DEG_C * (float(temp_c) - float(dewpoint_c)))


async def _fetch_open_meteo(lat: float, lon: float) -> dict:
    """One request + one retry against Open-Meteo (the _fetch_cutout shape,
    survey.py:170-191); parsed JSON or RuntimeError on exhaustion."""
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "minutely_15": ("cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation"),
        # Appended, not substituted: the minutely_15 block above is what every
        # other consumer of this response reads, and the hourly block is a
        # separate key in the same payload. A response that carries no `hourly`
        # -- an older cached body, a partial reply -- still parses, and the
        # wind column simply comes back empty, which is not a refutation of
        # anything (stage 5 §5.2). The surface fields are appended to the same
        # list for the same reason the winds are there at all: this module owns
        # the only connection to Open-Meteo there is.
        "hourly": ",".join(_wind_fields() + _surface_request_fields()),
        "forecast_days": "2",
        "timezone": "UTC",
    }
    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers) as client:
        for attempt in range(2):                   # initial + one retry
            try:
                resp = await client.get(OPEN_METEO_URL, params=params)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — uniform failure
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.4)
    raise RuntimeError(f"open-meteo upstream failed: {type(last_exc).__name__}")


def _parse_open_meteo(js: dict) -> tuple[list[float], dict[str, list[int]]]:
    """minutely_15 block -> (unix sample times, clamped 0-100 int series).
    Truncated to the shortest series and to _MAX_SAMPLES (48 h)."""
    block = js.get("minutely_15") or {}
    times: list[float] = []
    for t in block.get("time") or []:
        # timezone=UTC requested -> "YYYY-MM-DDTHH:MM" in UTC
        dt = datetime.strptime(str(t), "%Y-%m-%dT%H:%M").replace(
            tzinfo=timezone.utc)
        times.append(dt.timestamp())
    series: dict[str, list] = {}
    for key in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid",
                "cloud_cover_high"):
        vals = block.get(key) or []
        series[key] = [
            max(0, min(100, int(v))) if isinstance(v, (int, float)) else 0
            for v in vals]
    # The CLOUD series decide the usable length; precipitation is folded in
    # afterwards so a payload without it cannot shorten anything (see below).
    n = min([len(times)] + [len(series[k]) for k in series])
    n = min(n, _MAX_SAMPLES)
    times = times[:n]
    # PRECIPITATION IS MILLIMETRES, NOT A PERCENTAGE, and it joins here rather
    # than above for two separate reasons.
    #
    # It must not go through the 0-100 int clamp the cloud series uses: that
    # turns 0.8 mm of rain into 0 and silently disables the only gate that
    # protects the equipment.
    #
    # And it must not participate in the shortest-series truncation. Adding it
    # to that `min` meant a response WITHOUT precipitation - an older cached
    # payload, a partial upstream reply, or any of the existing fixtures -
    # collapsed n to 0 and wiped the entire cloud forecast. The suite caught
    # that immediately, which is the argument for running all of it.
    #
    # Missing values pad with 0.0, which reads as "no rain forecast" and so
    # fails OPEN, matching how the rest of this module treats absent data.
    precip = block.get("precipitation") or []
    vals = [max(0.0, float(v)) if isinstance(v, (int, float)) else 0.0
            for v in precip][:n]
    series["precipitation"] = vals + [0.0] * (n - len(vals))
    for k in series:
        series[k] = series[k][:n]
    return times, series


def _hourly_times(block: object) -> list[float]:
    """The hourly block's time base as unix seconds, or an empty list.

    ONE definition, because the pressure-level winds and the surface readings
    are two views of the same hourly grid and the index that means 03:00 has to
    mean 03:00 in both. Total by construction: anything that is not the shape
    expected -- a missing block, a `time` that is not a list, one stamp that
    does not parse -- answers with an empty base, and both consumers read that
    as "no hourly data", never as an error worth losing the cloud forecast for.
    """
    if not isinstance(block, dict):
        return []
    raw = block.get("time")
    if not isinstance(raw, list):
        return []
    times: list[float] = []
    for t in raw:
        try:
            dt = datetime.strptime(str(t), "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return []
        times.append(dt.timestamp())
    return times


def _num_at(seq: object, i: int) -> float | None:
    """``seq[i]`` as a float, or None for anything that is not a real number.

    Booleans are not numbers here: ``True`` is not a 1 km/h wind.
    """
    if not isinstance(seq, list) or i >= len(seq):
        return None
    v = seq[i]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _parse_surface(js: dict) -> tuple[list[float], dict[str, list]]:
    """hourly block -> (unix sample times, one series per surface field).

    Series are parallel to the returned times and to each other, ``None`` where
    the upstream had no value. A field the response does not carry at all comes
    back as an all-None series of the right length rather than as a missing key,
    so a consumer indexing by hour never has to ask which fields arrived.

    ``wind_dir_deg`` IS THE METEOROLOGICAL CONVENTION -- where the surface wind
    comes FROM -- matching ``_parse_wind_column``'s ``from_deg`` and every
    weather report anyone has ever read. It is not turned around here.

    ``cloud_base_m`` is DERIVED, not fetched: the lifting-condensation estimate
    from the temperature/dew-point spread (see ``cloud_base_m``).

    Total by construction, for the reason given on ``_hourly_times``.
    """
    block = js.get("hourly")
    times = _hourly_times(block)
    # The isinstance re-test is redundant (an empty base is the only thing a
    # non-dict block can produce) and is here anyway, because the alternative
    # is an `assert` that `python -O` deletes.
    if not times or not isinstance(block, dict):
        return [], {}
    series: dict[str, list] = {}
    for key, names in SURFACE_FIELDS:
        raw: object = None
        for name in names:
            candidate = block.get(name)
            if isinstance(candidate, list):
                raw = candidate
                break
        vals = [_num_at(raw, i) for i in range(len(times))]
        if key == "wind_dir_deg":
            vals = [None if v is None else v % 360.0 for v in vals]
        series[key] = vals
    series["cloud_base_m"] = [
        cloud_base_m(t, d)
        for t, d in zip(series["temp_c"], series["dewpoint_c"])]
    return times, series


def _parse_wind_column(js: dict) -> tuple[list[float], list[list[dict]]]:
    """hourly block -> (unix sample times, one level list per sample).

    Each level is ``{"label", "height_km", "speed_kmh", "from_deg"}``.

    ``from_deg`` IS THE METEOROLOGICAL CONVENTION and the key says so. Open-Meteo
    reports the direction wind comes FROM; a consumer comparing it against a
    measured drift wants the direction air is GOING, and the two are 180 degrees
    apart. Naming the field for the convention it is in -- rather than handing
    over a bare ``direction`` -- is what stops the turn being forgotten: a
    column entered backwards corroborates nothing and looks exactly like a sky
    the wind disagrees with, which is a silent wrong answer rather than a loud
    one.

    A level missing any of its three values is dropped from that sample rather
    than defaulted. A wind speed nobody measured is not calm air.
    """
    # Total by construction: every shape that is not the one expected answers
    # with an empty column. This runs on the success path of the forecast
    # refresh, and an exception here would cost the cloud forecast that had
    # already parsed cleanly out of the same body.
    block = js.get("hourly")
    times = _hourly_times(block)
    if not times or not isinstance(block, dict):   # see _parse_surface
        return [], []
    samples: list[list[dict]] = []
    for i in range(len(times)):
        levels: list[dict] = []
        for level in WIND_LEVELS_HPA:
            speed = _num_at(block.get("wind_speed_%dhPa" % level), i)
            direction = _num_at(
                block.get("wind_direction_%dhPa" % level), i)
            height_m = _num_at(
                block.get("geopotential_height_%dhPa" % level), i)
            if speed is None or direction is None or height_m is None:
                continue
            levels.append({
                "label": "%d hPa" % level,
                "height_km": height_m / 1000.0,
                "speed_kmh": speed,
                "from_deg": direction % 360.0,
            })
        samples.append(levels)
    return times, samples


async def _fetch_astrospheric(lat: float, lon: float, api_key: str) -> dict:
    """HTTP POST, body casing EXACTLY as verified live 2026-07-16 (spec §3):
    {"Latitude": float, "Longitude": float, "APIKey": string}. The key rides
    ONLY here. 400/403/500 (ErrorInfo JSON; non-NA locations are a 400) all
    collapse into the uniform RuntimeError -> fail-soft."""
    body = {"Latitude": lat, "Longitude": lon, "APIKey": api_key}
    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers) as client:
        for attempt in range(2):
            try:
                resp = await client.post(ASTROSPHERIC_URL, json=body)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — uniform failure
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.4)
    raise RuntimeError(
        f"astrospheric upstream failed: {type(last_exc).__name__}")


def _hour_value(entry: object) -> float | None:
    """Extract the raw hourly value from an Astrospheric HourValue. Tolerates
    both {"Value": {"ActualValue": x}, ...} and {"Value": x, ...}; the bundled
    map-color is ignored (spec §3)."""
    if not isinstance(entry, dict):
        return None
    v = entry.get("Value")
    if isinstance(v, dict):
        v = v.get("ActualValue")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _parse_astrospheric(
        js: dict) -> tuple[list[float], list[float | None],
                           list[float | None], int | None]:
    """ReturnObject -> (hourly unix times, seeing, transparency, credits).
    Sample times = UTCStartTime + index hours (81 h horizon). Fields accepted
    at the root or under a ReturnObject wrapper (plan decision 3)."""
    root = js.get("ReturnObject") if isinstance(js.get("ReturnObject"),
                                                dict) else js
    raw_start = root.get("UTCStartTime")
    try:
        dt = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start_ts = dt.timestamp()
    except (ValueError, TypeError) as exc:
        raise RuntimeError("astrospheric parse failed: UTCStartTime") from exc
    seeing_raw = root.get("Astrospheric_Seeing") or []
    trans_raw = root.get("Astrospheric_Transparency") or []
    n = min(len(seeing_raw), len(trans_raw))
    times = [start_ts + i * 3600.0 for i in range(n)]
    seeing = [_hour_value(e) for e in seeing_raw[:n]]
    trans = [_hour_value(e) for e in trans_raw[:n]]
    credits = root.get("APICreditUsedToday")
    credits = int(credits) if isinstance(credits, (int, float)) else None
    return times, seeing, trans, credits


# ------------------------------------------------------------------- service

class WeatherService:
    """Owns fetch/cache/latch/veto (spec §3-§5). ResumeArm service shape
    (resume_arm.py:47-68): one asyncio task, 60 s tick, broad-except never-die
    loop, injected wall clock."""

    def __init__(self, *, clock=None):
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
        self._task: asyncio.Task | None = None
        # Open-Meteo cache (in-memory only — spec §3; failures never cached)
        self._om_times: list[float] = []
        self._om_series: dict[str, list[int]] = {}
        self._om_fetched_ts: float | None = None
        self._om_attempt_at: float = 0.0   # due-when-older-than attempt latch
        # Pressure-level winds, riding the same Open-Meteo response (see
        # WIND_LEVELS_HPA). Hourly, so kept as its own time base rather than
        # folded into the 15-minute series.
        self._wind_times: list[float] = []
        self._wind_samples: list[list[dict]] = []
        # Surface observations (2 m / 10 m), riding the same hourly block as
        # the pressure-level winds and sharing its time base exactly.
        self._sfc_times: list[float] = []
        self._sfc_series: dict[str, list] = {}
        # Astrospheric cache
        self._as_times: list[float] = []
        self._as_seeing: list[float | None] = []
        self._as_trans: list[float | None] = []
        self._as_fetched_ts: float | None = None
        self._as_credits: int | None = None
        self._as_attempt_at: float = 0.0
        # High-cloud night warning latch (spec §5), keyed by int(dusk_ts)
        # (the _gave_up_for idiom, resume_arm.py:45). Populated in Task 3.
        self._alert: dict | None = None
        self._alert_dawn_ts: float | None = None
        self._alert_night_key: int | None = None
        # Ignore-tonight override (spec §4): runtime flag keyed to tonight's
        # dusk, NOT persisted config; auto-expires when a new night begins.
        self._ignore_night_key: int | None = None
        # enabled-flip edge: publish ONE cleared payload when enabled goes false
        self._was_enabled = False

    # -- lifecycle (ResumeArm shape) ----------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — service must never die
                bus.log("warning", f"weather tick failed: {e}", "weather")
            await asyncio.sleep(CHECK_INTERVAL_S)

    # -- tick ----------------------------------------------------------------

    async def tick(self) -> None:
        now = self._clock()
        cfg = config_store.cfg()
        wcfg = cfg.weather
        site = cfg.site
        # Non-default site required (plan decision 2): a default (0,0) site
        # fetches nothing. pydantic bounds keep lat/lon finite when set.
        site_ok = not site.is_default
        if not (wcfg.enabled and site_ok):
            if self._was_enabled:
                # ONE cleared payload on the first tick after enabled flips
                # false (spec §3) — then silence until re-enabled.
                self._clear()
                bus.publish("weather", **self.payload(now))
            self._was_enabled = False
            return
        self._was_enabled = True
        if now - self._om_attempt_at >= OPEN_METEO_INTERVAL_S:
            self._om_attempt_at = now
            await self._refresh_open_meteo(site.latitude, site.longitude, now)
        if wcfg.astrospheric_api_key and \
                now - self._as_attempt_at >= ASTROSPHERIC_INTERVAL_S:
            self._as_attempt_at = now
            await self._refresh_astrospheric(
                site.latitude, site.longitude, wcfg.astrospheric_api_key, now)

    def _clear(self) -> None:
        self._om_times, self._om_series, self._om_fetched_ts = [], {}, None
        self._om_attempt_at = 0.0
        self._wind_times, self._wind_samples = [], []
        self._sfc_times, self._sfc_series = [], {}
        self._as_times, self._as_seeing, self._as_trans = [], [], []
        self._as_fetched_ts, self._as_credits = None, None
        self._as_attempt_at = 0.0
        self._alert = None
        self._alert_dawn_ts = None
        self._alert_night_key = None

    async def _refresh_open_meteo(self, lat: float, lon: float,
                                  now: float) -> None:
        try:
            js = await _fetch_open_meteo(lat, lon)
            times, series = _parse_open_meteo(js)
            if not times:
                raise RuntimeError("open-meteo parse failed: empty minutely_15")
        except Exception as exc:  # noqa: BLE001 — keep previous forecast (§3)
            bus.log("warning",
                    f"open-meteo fetch failed: {type(exc).__name__}", "weather")
            return
        self._om_times, self._om_series = times, series
        # Parsed off the SAME body, and outside the try above on purpose: a
        # malformed hourly block must not throw away a good cloud forecast.
        # _parse_wind_column answers an unusable one with an empty column --
        # and IN ITS OWN try, because that promise was the only thing keeping
        # this line from being the opposite of what the sentence above claims.
        # A raise here lands outside every handler in the refresh: `_om_times`
        # and `_om_series` would already be assigned, `_om_fetched_ts` would
        # stay None, `_evaluate_night_warning` would never run and no
        # `weather` event would be published -- so the RAIN VETO would go
        # stale with 96 good samples in hand and nothing in any log to say
        # why. The function is total today (fuzzed with 23 adversarial hourly
        # blocks, none raised); nothing enforced that, and now the call site
        # does not need it to be true.
        try:
            self._wind_times, self._wind_samples = _parse_wind_column(js)
        except Exception as exc:  # noqa: BLE001 — an unusable block is empty
            self._wind_times, self._wind_samples = [], []
            bus.log("warning",
                    f"wind column unusable: {type(exc).__name__}", "weather")
        # Its OWN try, not the one above: the surface readings and the wind
        # column are read out of the same block but neither is evidence about
        # the other, and one of them being unusable is no reason to discard the
        # one that parsed.
        try:
            self._sfc_times, self._sfc_series = _parse_surface(js)
        except Exception as exc:  # noqa: BLE001 — an unusable block is empty
            self._sfc_times, self._sfc_series = [], {}
            bus.log("warning",
                    f"surface readings unusable: {type(exc).__name__}",
                    "weather")
        self._om_fetched_ts = now
        # spec §5: evaluate BEFORE publishing so a fresh alert rides this
        # payload (the publish is emission #1; the bus.log inside is #2).
        self._evaluate_night_warning(now)
        bus.publish("weather", **self.payload(now))

    async def _refresh_astrospheric(self, lat: float, lon: float,
                                    api_key: str, now: float) -> None:
        # ---- #200: THE CLIENT IS THE THING THEIR TERMS DO NOT COVER --------
        #
        # Astrospheric's Data API is scoped to "Astrospheric Professional
        # members for use in personal projects", and AstroDeck is a public
        # product. Nothing is being redistributed, so there is no file to stop
        # shipping — the request itself is what needs to be inside their scope.
        # So it does not go out until somebody with authority over this instance
        # has stated on the record that this deployment is such a use.
        #
        # ONE acknowledgment covers the whole instance; viewers and operators
        # inherit it and are never asked. The thing being asserted is a fact
        # about the deployment, not a promise by whoever is logged in.
        #
        # Silent about it on purpose: the operator is told once, on the Credits
        # screen, where the terms are quoted and the button is. A key that was
        # entered and is not being used would otherwise warn every six hours
        # forever, which is how a real signal becomes noise.
        from .licensing import is_acknowledged
        if not is_acknowledged("astrospheric"):
            return
        try:
            js = await _fetch_astrospheric(lat, lon, api_key)
            times, seeing, trans, credits = _parse_astrospheric(js)
        except Exception as exc:  # noqa: BLE001 — independent fail-soft (§3)
            bus.log("warning",
                    f"astrospheric fetch failed: {type(exc).__name__}",
                    "weather")
            return
        self._as_times, self._as_seeing, self._as_trans = times, seeing, trans
        self._as_fetched_ts, self._as_credits = now, credits
        bus.publish("weather", **self.payload(now))

    # -- tonight / ignore-tonight (spec §4) -----------------------------------

    def _tonight(self, now: float) -> tuple[float, float] | None:
        """Tonight's [dusk, dawn]. Delegates to ``schedule.observing_night`` —
        THE definition, shared with the auto-resume bound.

        This used to inline the same four lines (``_night_dusk``/``_night_dawn``
        + the is_default check). Two copies of "when is tonight?" is how three
        surfaces came to describe one forecast three different ways; one of them
        is now a call, so there is nothing left to drift."""
        cfg = config_store.cfg()
        return schedule.observing_night(cfg.site, cfg.safety.twilight_deg, now)

    def _ignore_active(self, now: float) -> bool:
        if self._ignore_night_key is None:
            return False
        night = self._tonight(now)
        if night is None:
            return False
        # keyed to tonight's dusk: a new night has a new key -> auto-expired
        return int(night[0]) == self._ignore_night_key

    def set_ignore_tonight(self, ignore: bool,
                           now: float | None = None) -> None:
        """Set/clear the ignore-weather-tonight override (spec §4). Raises
        NoNightError when no site/night resolves (route maps it to a 409
        {detail:{code:"no_night"}}). Does NOT retract an already-shown warning
        — the flag records "proceed anyway"."""
        now = self._clock() if now is None else now
        night = self._tonight(now)
        if night is None:
            raise NoNightError()
        self._ignore_night_key = int(night[0]) if ignore else None

    # -- breach math + veto (spec §4) -----------------------------------------

    def _needed_samples(self, sustain_minutes: int) -> int:
        """Consecutive 15-min samples a breach must span (spec §4)."""
        return max(1, sustain_minutes // 15)

    def _sustained_breach(self, lo: float, hi: float, threshold: int,
                          needed: int) -> tuple[int, int] | None:
        """First run of >= ``needed`` CONSECUTIVE 15-min samples with TOTAL
        cloud >= ``threshold`` among samples timestamped in [lo, hi]; returns
        the run's (start_idx, end_idx) inclusive indices into the Open-Meteo
        series, or None. Samples are consecutive by construction (one series
        on a 15-min grid)."""
        cloud = self._om_series.get("cloud_cover") or []
        run_start: int | None = None
        run_end: int | None = None
        for i, t in enumerate(self._om_times):
            in_window = i < len(cloud) and lo <= t <= hi
            if in_window and cloud[i] >= threshold:
                if run_start is None:
                    run_start = i
                run_end = i
            else:
                if (run_start is not None and run_end is not None
                        and run_end - run_start + 1 >= needed):
                    return run_start, run_end
                run_start = run_end = None
        if (run_start is not None and run_end is not None
                and run_end - run_start + 1 >= needed):
            return run_start, run_end
        return None

    def veto_reason(self, now: float) -> str | None:
        """Auto-resume weather gate. Non-None = human-readable reason.

        RAIN VETOES. CLOUD DOES NOT. They are different hazards and they want
        different instruments.

        Rain risks the equipment, is irreversible, and cannot be observed from
        here in time - by the time water is on the corrector the decision is
        long past. A forecast is the RIGHT instrument for it, and being early is
        the correct kind of wrong.

        Cloud risks some frames. The frames are free to discard, the rig
        measures the sky above itself on every exposure, and the engine already
        treats that measurement as the authority once a run is going (see
        `SequenceEngine._safety_gate`: "THE SKY IS STILL EVIDENCE"). A forecast
        over a ~10 km grid cell is the WRONG instrument, and using it to refuse
        to start is self-fulfilling: decline to open and you never learn the sky
        was clear.

        It was not a hypothetical. This gate refused two consecutive nights:

            2026-08-19  forecast 100% from 22:00  ->  clear past 02:00
            2026-08-20  forecast 100% from 21:30  ->  plate solve succeeded and
                                                      the focus frame held 1386
                                                      stars

        The cloud outlook is still computed, still alerted, and still shown in
        the Tonight brief. It informs; it no longer gates.

        FAIL-OPEN on stale/missing data, unchanged: we act on positive evidence
        of rain, never on the absence of evidence of no rain, because a network
        blip must not end a night.
        """
        cfg = config_store.cfg().weather
        if not cfg.enabled:
            return None
        if self._ignore_active(now):
            return None
        if self._om_fetched_ts is None or \
                now - self._om_fetched_ts > OPEN_METEO_STALE_S:
            return None                            # stale/missing -> fail-open
        return self._rain_veto(now)

    def _rain_veto(self, now: float) -> str | None:
        """Forecast rain inside the next hour, or None.

        NO SUSTAIN REQUIREMENT, unlike cloud. One 15-minute bucket of rain is
        enough: the hazard is instantaneous and the damage does not average out
        over half an hour the way a thin cloud layer does.
        """
        precip = self._om_series.get("precipitation") or []
        worst = 0.0
        when: float | None = None
        for i, t in enumerate(self._om_times):
            if i >= len(precip) or not (now <= t <= now + 3600.0):
                continue
            if float(precip[i]) > worst:
                worst, when = float(precip[i]), t
        if worst < RAIN_VETO_MM or when is None:
            return None
        mins = max(0, int((when - now) / 60.0))
        return (f"rain forecast within the next hour ({worst:.1f} mm in "
                f"about {mins} min) - the sky can be argued with, water cannot")

    def cloud_outlook(self, now: float) -> str | None:
        """What the cloud forecast says for the next hour, as ADVICE.

        This is what `veto_reason` used to return before cloud stopped gating.
        Kept so the information survives the policy change - the operator still
        needs to know what tonight is forecast to do, they just are not stopped
        by it.
        """
        cfg = config_store.cfg().weather
        if self._om_fetched_ts is None:
            return None
        needed = self._needed_samples(cfg.sustain_minutes)
        hit = self._sustained_breach(now, now + 3600.0,
                                     cfg.cloud_threshold_pct, needed)
        if hit is None:
            return None
        cloud = self._om_series.get("cloud_cover") or []
        peak = max(cloud[hit[0]:hit[1] + 1])
        return (f"cloud cover {peak}% forecast within the next hour "
                f"(threshold {cfg.cloud_threshold_pct}%) - the frames decide, "
                f"not this")

    def wind_column(self, now: float) -> list[dict]:
        """The pressure-level winds over the site at ``now``, or an empty list.

        One row per level: ``{"label", "height_km", "speed_kmh", "from_deg"}``,
        ordered 850 hPa upward. ``from_deg`` is where the wind comes FROM (see
        ``_parse_wind_column``); a caller comparing it against a measured drift
        has to turn it around.

        PLAIN ROWS, NOT A TYPE FROM THE CONSUMER. The one caller is the
        cloud-occlusion service, whose corroboration takes a small dataclass --
        and importing that dataclass here would put an import of the cloud
        model into the module that owns ``veto_reason``, which is the auto-
        resume safety gate. The rule that the cloud model gates nothing is kept
        by a test that greps this file for exactly that import, and a rule kept
        by a detector must not be worked around by the file it is watching. The
        translation costs the consumer four lines and buys a dependency edge
        that only ever points one way.

        EMPTY IS NOT A REFUTATION and never an error: no forecast yet, weather
        disabled, an upstream that dropped the hourly block, or a moment the
        series does not cover all answer the same way, and the consumer treats
        the absence of a column as "unconfirmed" rather than "contradicted".
        """
        if not self._wind_times:
            return []
        best = min(range(len(self._wind_times)),
                   key=lambda i: abs(self._wind_times[i] - now))
        if abs(self._wind_times[best] - now) > WIND_COLUMN_MAX_AGE_S:
            return []
        return [dict(level) for level in self._wind_samples[best]]

    def surface_now(self, now: float) -> dict | None:
        """The surface reading nearest ``now``, or None.

        ``{"ts", "temp_c", "dewpoint_c", "humidity_pct", "wind_kmh",
        "wind_dir_deg", "gust_kmh", "cloud_base_m"}`` -- ``ts`` is the ISO-Z
        stamp of the hour this actually came from, because "nearest" is up to
        half an hour away and a reader deserves to know which hour they are
        being told about.

        Individual values stay None when the upstream had none; the whole
        reading is None when there is no hourly series, or when the nearest
        sample is further from ``now`` than ``SURFACE_MAX_AGE_S``. An old
        sample presented as the current conditions is worse than no sample:
        nobody can tell the difference by looking at it.
        """
        if not self._sfc_times:
            return None
        best = min(range(len(self._sfc_times)),
                   key=lambda i: abs(self._sfc_times[i] - now))
        if abs(self._sfc_times[best] - now) > SURFACE_MAX_AGE_S:
            return None
        out: dict = {"ts": _iso_z(self._sfc_times[best])}
        for key in [k for k, _names in SURFACE_FIELDS] + ["cloud_base_m"]:
            series = self._sfc_series.get(key) or []
            out[key] = series[best] if best < len(series) else None
        return out

    # -- high-cloud night warning (spec §5, REQUIRED) --------------------------

    def _evaluate_night_warning(self, now: float) -> None:
        """Sustained-breach scan over tonight's [dusk, dawn] on each successful
        Open-Meteo refresh; ONCE-PER-NIGHT latch keyed by int(dusk_ts). On the
        first breach of a night: populate the alert (rides the weather payload
        until dawn) and bus.log ONE warning that reaches the existing ntfy/
        webhook/telegram sinks — times + percentages only, NEVER coordinates."""
        night = self._tonight(now)
        if night is None:
            return                                 # polar day / no site
        dusk, dawn = night
        key = int(dusk)
        if self._alert_night_key is not None and self._alert_night_key != key:
            # night rolled over: drop the stale alert, allow a fresh latch
            self._alert = None
            self._alert_dawn_ts = None
            self._alert_night_key = None
        if self._alert_night_key == key:
            return                                 # already latched tonight
        cfg = config_store.cfg().weather
        needed = self._needed_samples(cfg.sustain_minutes)
        hit = self._sustained_breach(dusk, dawn, cfg.cloud_threshold_pct,
                                     needed)
        if hit is None:
            return
        i0, i1 = hit
        cloud = self._om_series.get("cloud_cover") or []
        peak = int(max(cloud[i0:i1 + 1]))
        layers = {
            "low": self._om_series.get("cloud_cover_low") or [],
            "mid": self._om_series.get("cloud_cover_mid") or [],
            "high": self._om_series.get("cloud_cover_high") or [],
        }

        def _mean(vals: list[int]) -> float:
            window = vals[i0:i1 + 1]
            return sum(window) / len(window) if window else 0.0

        dominant = max(layers, key=lambda k: _mean(layers[k]))
        start_ts, end_ts = self._om_times[i0], self._om_times[i1]
        self._alert = {
            "kind": "high_cloud",
            "start_iso": _iso_z(start_ts),
            "end_iso": _iso_z(end_ts),
            "peak_pct": peak,
            "dominant_layer": dominant,
        }
        self._alert_dawn_ts = dawn
        self._alert_night_key = key
        # LOCAL time, not UTC. This line said "09:15–12:15" for a window the
        # modal (which renders start_iso/end_iso in the browser) correctly
        # showed as 02:15–05:15 — the same window, seven hours apart, because
        # this one formatted UTC and labelled it "tonight". At a glance it read
        # as a mid-MORNING peak, which is not a thing an observer can act on,
        # and it sat beside a log stamp that events.py renders in LOCAL time.
        # Two clocks in one line is worse than either clock.
        #
        # `time.localtime` (not `datetime.utcnow`), matching events.py: the rig
        # runs in the observatory's own timezone, so its local time IS the
        # operator's wall clock.
        start_hhmm = time.strftime("%H:%M", time.localtime(start_ts))
        end_hhmm = time.strftime("%H:%M", time.localtime(end_ts))
        bus.log("warning",
                f"high cloud forecast tonight: peak {peak}% ({dominant} layer) "
                f"{start_hhmm}–{end_hhmm}", "weather")

    # -- payload (spec §7) -----------------------------------------------------

    def payload(self, now: float | None = None) -> dict:
        """The GET /api/weather + WS ``weather`` event payload.

        The forecast series are site-derived but location-free, as originally
        designed. ``site_lat``/``site_lon`` are the ONE deliberate exception
        (2026-07-17 decisions wave I2): the radar map (RadarMap.tsx) needs the
        site fix client-side to center its tiles and project the scope's
        pierce-point overlay, and the product owner explicitly accepted that
        the radar map's tile coordinates disclose the site region to an
        operator (consistent with the future telescope-rental interface).
        Both fields are null on the default (0, 0) site, same convention as
        everywhere else. This does NOT reopen the general "site coordinates
        are admin-only everywhere else" rule: every other coordinate-bearing
        surface (status/summary/config, the WS hello/status frames) is still
        gated on the separate, unchanged ``view.site_precise``. This payload
        is safe to carry them because access control here is the event-drop /
        route-gate rule on ``view.weather`` (spec §8; api/redact.py,
        api/app.py get_weather/weather_tile), not key-stripping -- a viewer
        never reaches this payload at all, over REST (403) or WS (dropped
        entirely).

        ``surface`` (hourly series) and ``now`` (the sample nearest this
        moment) carry temperature, dew point, humidity, the 10 m wind and the
        estimated cloud base. They are readings AT the site and carry no
        coordinate of it: the only two coordinate-bearing keys in this payload
        are still ``site_lat``/``site_lon`` above, and nothing was added to
        that list."""
        now = self._clock() if now is None else now
        cfg = config_store.cfg().weather
        site = config_store.cfg().site
        out: dict = {
            "enabled": bool(cfg.enabled),
            "fetched_ts": self._om_fetched_ts,
            "stale": (self._om_fetched_ts is None
                      or now - self._om_fetched_ts > OPEN_METEO_STALE_S),
            "ignore_tonight": self._ignore_active(now),
            "threshold_pct": cfg.cloud_threshold_pct,
            "sustain_minutes": cfg.sustain_minutes,
            "site_lat": None if site.is_default else site.latitude,
            "site_lon": None if site.is_default else site.longitude,
            "forecast": None,
            # The surface observations are HOURLY and ``forecast`` is a
            # 15-minute grid, so they get their own block with their own
            # ``times`` rather than being indexed alongside series they do not
            # line up with. Padding one grid onto the other would have meant
            # inventing three values out of every four and no reader could tell
            # which was measured.
            "surface": None,
            "now": None,
            "astrospheric": None,
            "alert": None,
        }
        if not cfg.enabled:
            return out
        if self._om_times:
            out["forecast"] = {
                "times": [_iso_z(t) for t in self._om_times],
                "cloud": list(self._om_series.get("cloud_cover") or []),
                "cloud_low": list(self._om_series.get("cloud_cover_low") or []),
                "cloud_mid": list(self._om_series.get("cloud_cover_mid") or []),
                "cloud_high": list(self._om_series.get("cloud_cover_high") or []),
            }
        if self._sfc_times:
            surface: dict = {"times": [_iso_z(t) for t in self._sfc_times]}
            for key in [k for k, _names in SURFACE_FIELDS] + ["cloud_base_m"]:
                surface[key] = list(self._sfc_series.get(key) or [])
            out["surface"] = surface
        out["now"] = self.surface_now(now)
        if self._as_fetched_ts is not None:
            out["astrospheric"] = {
                "times": [_iso_z(t) for t in self._as_times],
                "seeing": list(self._as_seeing),
                "transparency": list(self._as_trans),
                "fetched_ts": self._as_fetched_ts,
                "stale": now - self._as_fetched_ts > ASTROSPHERIC_STALE_S,
                "credits_used_today": self._as_credits,
            }
        # the alert stays in the payload until dawn (spec §5); the latch itself
        # resets when the night key changes (Task 3 sets these fields).
        if self._alert is not None and self._alert_dawn_ts is not None \
                and now < self._alert_dawn_ts:
            out["alert"] = dict(self._alert)
        return out


# Module-level singleton, wired in api/app.py like ``resume_arm``.
weather_service = WeatherService()
