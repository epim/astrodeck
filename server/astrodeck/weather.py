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
        """Auto-resume weather gate (spec §4). Non-None = human-readable
        reason. FAIL-OPEN on stale/missing data: weather is advisory; the
        safety monitor remains the hard guard."""
        cfg = config_store.cfg().weather
        if not cfg.enabled:
            return None
        if self._ignore_active(now):
            return None
        if self._om_fetched_ts is None or \
                now - self._om_fetched_ts > OPEN_METEO_STALE_S:
            return None                            # stale/missing -> fail-open
        needed = self._needed_samples(cfg.sustain_minutes)
        hit = self._sustained_breach(now, now + 3600.0,
                                     cfg.cloud_threshold_pct, needed)
        if hit is None:
            return None
        cloud = self._om_series.get("cloud_cover") or []
        peak = max(cloud[hit[0]:hit[1] + 1])
        return (f"cloud cover {peak}% forecast within the next hour "
                f"(threshold {cfg.cloud_threshold_pct}%)")

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
        start_hhmm = datetime.fromtimestamp(
            start_ts, tz=timezone.utc).strftime("%H:%M")
        end_hhmm = datetime.fromtimestamp(
            end_ts, tz=timezone.utc).strftime("%H:%M")
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
        entirely)."""
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
