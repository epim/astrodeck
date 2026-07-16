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

async def _fetch_open_meteo(lat: float, lon: float) -> dict:
    """One request + one retry against Open-Meteo (the _fetch_cutout shape,
    survey.py:170-191); parsed JSON or RuntimeError on exhaustion."""
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "minutely_15": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
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
    series: dict[str, list[int]] = {}
    for key in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid",
                "cloud_cover_high"):
        vals = block.get(key) or []
        series[key] = [
            max(0, min(100, int(v))) if isinstance(v, (int, float)) else 0
            for v in vals]
    n = min([len(times)] + [len(series[k]) for k in series])
    n = min(n, _MAX_SAMPLES)
    times = times[:n]
    for k in series:
        series[k] = series[k][:n]
    return times, series


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

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self._task: asyncio.Task | None = None
        # Open-Meteo cache (in-memory only — spec §3; failures never cached)
        self._om_times: list[float] = []
        self._om_series: dict[str, list[int]] = {}
        self._om_fetched_ts: float | None = None
        self._om_attempt_at: float = 0.0   # due-when-older-than attempt latch
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
        self._om_fetched_ts = now
        bus.publish("weather", **self.payload(now))

    async def _refresh_astrospheric(self, lat: float, lon: float,
                                    api_key: str, now: float) -> None:
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
        """Tonight's [dusk, dawn] via the ResumeArm definition (spec §5):
        schedule._night_dusk/_night_dawn with cfg.safety.twilight_deg. None
        when no night resolves (polar day) or the site is default."""
        cfg = config_store.cfg()
        site = cfg.site
        if site.is_default:
            return None
        twilight = cfg.safety.twilight_deg
        dusk = schedule._night_dusk(site.latitude, site.longitude, twilight, now)
        dawn = schedule._night_dawn(site.latitude, site.longitude, twilight, now)
        if dusk is None or dawn is None:
            return None
        return dusk, dawn

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

    # -- payload (spec §7) -----------------------------------------------------

    def payload(self, now: float | None = None) -> dict:
        """The GET /api/weather + WS ``weather`` event payload. NO coordinates
        anywhere — the series are site-derived but location-free; access
        control is the event-drop rule (spec §8), not key-stripping."""
        now = self._clock() if now is None else now
        cfg = config_store.cfg().weather
        out: dict = {
            "enabled": bool(cfg.enabled),
            "fetched_ts": self._om_fetched_ts,
            "stale": (self._om_fetched_ts is None
                      or now - self._om_fetched_ts > OPEN_METEO_STALE_S),
            "ignore_tonight": self._ignore_active(now),
            "threshold_pct": cfg.cloud_threshold_pct,
            "sustain_minutes": cfg.sustain_minutes,
            "forecast": None,
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
