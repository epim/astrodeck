# Weather Integration (Sub-project C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Site-gated weather integration: Open-Meteo cloud forecast + optional Astrospheric seeing/transparency + IEM radar/satellite tiles, driving one shared cloud threshold through a high-cloud night warning popup, a real `resume_veto()` for auto-resume, and a Monitor-view Sky Conditions panel + slippy radar map with a scope-pointing overlay.

**Architecture:** A new `server/astrodeck/weather.py` service (module singleton, ResumeArm-shaped 60 s poller) owns fetch/cache/latch/veto and publishes `weather` bus events; four new `@declare`-gated routes (`GET /api/weather`, `POST /api/weather/ignore-tonight`, `GET /api/weather/tile/...`, `POST /api/config/weather`) ride the existing RBAC + redaction seams, with WS `weather` events DROPPED for non-`view.site_precise` holders on both WS lanes. The UI adds a store `weather` slice fed by WS + cold GET, a Sky Conditions SVG chart panel, a Settings WeatherPanel, and a from-scratch Web-Mercator radar map (`lib/mercator.ts` + `RadarMap.tsx`) that only ever talks to the server-side tile proxy.

**Tech Stack:** FastAPI + pydantic + httpx (server, existing deps only) · React 18 + zustand + hand-rolled SVG (UI, NO new npm deps) · pytest (asyncio_mode=auto) · self-executing `npx tsx` UI logic tests.

## Global Constraints

Spec §16, verbatim:

- `weather.enabled` default FALSE; zero outbound weather httpx when disabled —
  test-enforced with the `_Boom` pattern for BOTH the poller and the tile proxy.
- ALL weather data/UI is `view.site_precise`-gated; WS weather events DROP (not strip)
  for non-holders on BOTH WS lanes; no new redaction lane — extend `_redact_ws_event`.
- `astrospheric_api_key` is a secret: scrubbed in `redacted()`, absent from config echoes
  and 409 bodies, never logged, write-only in the UI.
- No coordinates in ANY log line or alert text (times + percentages only).
- One shared threshold (`cloud_threshold_pct` + `sustain_minutes`) drives warning AND
  veto; veto fail-open on stale/missing data; safety monitor remains the hard guard.
- "Tonight" = `_night_dusk`/`_night_dawn` with `cfg.safety.twilight_deg` — the ResumeArm
  definition, everywhere in C.
- IEM/Open-Meteo/Astrospheric origins hardcoded server-side; browsers only ever hit
  `/api/weather/*`. Upstreams verified live 2026-07-16 (§3, §6) — the plan copies them
  verbatim. Astrospheric cadence is 6 h and MUST NOT be increased (5 credits/call,
  100/day Pro budget).
- No new npm dependencies; charts hand-rolled SVG; series differentiated by dash/width/
  label, never hue; imagery gets `var(--img-filter)` at night.
- Server tests via `cd server && ./.venv/Scripts/python.exe -m pytest -q`; UI tsx tests
  self-executing, never in CI; `npm run build` strict gate.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`; CI claims
  only via `gh run watch`/`gh run view`.

Operational constraints:

- Server tests: `cd server && ./.venv/Scripts/python.exe -m pytest -q` (full suite currently 1071 passing, ~13 min; targeted files during development, full suite before claiming task done).
- UI type/build gate: `cd ui && npm run build` (tsc -b && vite build, strict). UI logic tests are self-executing: `npx tsx ui/src/lib/__tests__/<name>.test.ts` — NEVER added to CI.
- Every commit message ends with BOTH trailer lines: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`.
- Work directly on main (repo convention), frequent small commits.
- TASK ORDERING (non-negotiable): ALL server tasks (1-5) before ALL UI tasks (6-8). External review agents are browsing the shared `ui/dist`, so `npm run build` may ONLY run inside Tasks 6-8, never earlier.
- Harness note: if a long command gets auto-backgrounded, re-run it immediately and stay foreground; there are no monitor notifications. Never park waiting.

Plan-level decisions (spec gaps resolved — do NOT re-litigate during implementation):

1. **Key write contract** (spec §2 "write-only" vs §9 "version-checked save"): `POST /api/config/weather` takes `{"weather": <WeatherConfig>, "version": int|null, "clear_astrospheric_key": bool}`. A `null`/empty `astrospheric_api_key` in the body means "keep the stored key" (the UI round-trips the masked config — same contract as `deadman_url`); `clear_astrospheric_key: true` clears it. Version checked via `ConfigStore._check_version` → 409 through the redaction seams.
2. **Tick site gate** (spec §3 "finite lat/lon"): pydantic bounds make lat/lon always finite, so the tick additionally requires `not site.is_default` — a default (0,0) site fetches nothing and `_tonight()` returns None (→ ignore-tonight 409 `no_night` on a default site).
3. **Astrospheric response tolerance**: the parser accepts fields at the JSON root OR under a `ReturnObject` wrapper, and `HourValue` as `{"Value": {"ActualValue": x}}` OR `{"Value": x}` (the docs describe "ReturnObject" fields; live shape may nest). Anything else is a parse failure → fail-soft.
4. **Dark-window band source** (spec §10): the Sky Conditions chart shades tonight using `GET /api/site/sky` `dark_window` (the existing display variant) — the weather payload carries no dusk/dawn.
5. **Veto vs sustain > 60 min**: spec §4's rule is literal — `max(1, sustain_minutes // 15)` consecutive samples inside [now, now+60 min]. With `sustain_minutes > 75` the next-hour window can never contain enough samples, so the veto cannot fire (the night warning still can, over the full dark window). Test-documented, not "fixed".

---

### Task 1: WeatherConfig + secret scrub + POST /api/config/weather

**Files**
- Modify: `server/astrodeck/config.py` (WeatherConfig class after `SurveyConfig` ~line 278; AppConfig slot after `survey:` ~line 337; `set_weather` after `set_survey` ~line 639; `redacted()` scrub before its `return data` ~line 860)
- Modify: `server/astrodeck/api/app.py` (import `WeatherConfig`; `WeatherSaveBody` + `POST /api/config/weather` route after the survey config route ~line 831)
- Test: `server/tests/test_weather.py` (create)

**Interfaces**
- Consumes (existing, verbatim in seams `.superpowers/sdd/seams/weather-server.md`): `SurveyConfig`/`set_survey` idiom (seam-01/03), `AppConfig` append slot (seam-02), `redacted()` (seam-05), `ConfigStore._check_version` + `bump_and_save` (config.py:477-487), `ConfigVersionConflict` (config.py:360, already imported in app.py:49), route idiom `POST /api/config/survey` (seam-09: `@declare(CAP_CONFIG_SITE_OPTICS)` + `Depends(require(CAP_CONFIG_SITE_OPTICS))` + `asyncio.to_thread` + `bus.publish("config", config=redacted(cfg))` + `return _config_payload(principal)`), the PUT /api/site 409 shape (app.py:1386-1400: `HTTPException(409, detail={"detail": str(e), "current": _redact_site_for(redacted(e.current), principal)})`).
- Produces (later tasks rely on these EXACT names):
  - `class WeatherConfig(BaseModel)` in `astrodeck.config` with fields `enabled: bool = False`, `cloud_threshold_pct: int` (default 50, ge=0 le=100), `sustain_minutes: int` (default 30, ge=15 le=240), `astrospheric_api_key: str | None = None`.
  - `AppConfig.weather: WeatherConfig` (default factory).
  - `ConfigStore.set_weather(self, weather: "WeatherConfig", expected_version: int | None = None) -> AppConfig`.
  - `redacted()` output: `data["weather"]["astrospheric_api_key"] is None` always; `data["weather"]["astrospheric_configured"]: bool`.
  - Route `POST /api/config/weather`, cap `CAP_CONFIG_SITE_OPTICS`, body `{"weather": WeatherConfig, "version": int|null, "clear_astrospheric_key": bool}`.

**Steps**

- [ ] **1.1 Write the failing tests.** Create `server/tests/test_weather.py` with EXACTLY this content:

```python
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
```

- [ ] **1.2 Run the new tests — expect FAIL** (ImportError: cannot import `WeatherConfig`):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py -q
```

Expected: collection error `ImportError ... cannot import name 'WeatherConfig'`.

- [ ] **1.3 Add `WeatherConfig` to `server/astrodeck/config.py`.** Edit — insert immediately AFTER the `SurveyConfig` class (seam-01 shows it verbatim; its last line is `    online_fetch: bool = False`). old_string:

```python
class SurveyConfig(BaseModel):
    """Sky-Atlas survey source (offline-pack spec §4). online_fetch gates ALL
    hips2fits upstream calls: False (the default) = offline-first, the local
    pack is the only source; True = upstream for fov < 4°, pack as fallback."""
    online_fetch: bool = False
```

new_string:

```python
class SurveyConfig(BaseModel):
    """Sky-Atlas survey source (offline-pack spec §4). online_fetch gates ALL
    hips2fits upstream calls: False (the default) = offline-first, the local
    pack is the only source; True = upstream for fov < 4°, pack as fallback."""
    online_fetch: bool = False


class WeatherConfig(BaseModel):
    """Weather forecast + radar integration (sub-project C). enabled gates ALL
    weather upstream calls (Open-Meteo, Astrospheric, IEM tile proxy): False
    (the default) = zero outbound weather traffic. One shared threshold drives
    both the night warning and the auto-resume veto. astrospheric_api_key is a
    SECRET (None = feature absent): scrubbed in redacted(), never logged."""
    enabled: bool = False
    cloud_threshold_pct: int = Field(50, ge=0, le=100)  # breach metric = TOTAL cloud_cover
    sustain_minutes: int = Field(30, ge=15, le=240)     # breach must persist this long
    astrospheric_api_key: str | None = None
```

- [ ] **1.4 Add the AppConfig slot.** Edit `server/astrodeck/config.py` — old_string (the last AppConfig field, seam-02):

```python
    # --- Sky-Atlas survey source (offline-pack spec §4; appended — old configs load fine) ---
    survey: SurveyConfig = Field(default_factory=SurveyConfig)
```

new_string:

```python
    # --- Sky-Atlas survey source (offline-pack spec §4; appended — old configs load fine) ---
    survey: SurveyConfig = Field(default_factory=SurveyConfig)
    # --- weather integration (sub-project C spec §2; appended — old configs load fine) ---
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
```

- [ ] **1.5 Add the setter.** Edit `server/astrodeck/config.py` — old_string (seam-03):

```python
    def set_survey(self, survey: "SurveyConfig") -> AppConfig:
        """Persist the survey-source config (offline-pack spec §4)."""
        cfg = self.cfg()
        cfg.survey = survey
        return self.bump_and_save()
```

new_string:

```python
    def set_survey(self, survey: "SurveyConfig") -> AppConfig:
        """Persist the survey-source config (offline-pack spec §4)."""
        cfg = self.cfg()
        cfg.survey = survey
        return self.bump_and_save()

    def set_weather(self, weather: "WeatherConfig",
                    expected_version: int | None = None) -> AppConfig:
        """Persist the weather config (weather spec §2). Version-checked like
        set_site so concurrent editors get a 409, not a silent clobber."""
        self._check_version(expected_version)
        cfg = self.cfg()
        cfg.weather = weather
        return self.bump_and_save()
```

- [ ] **1.6 Add the `redacted()` scrub.** Edit `server/astrodeck/config.py` — old_string (the tail of `redacted()`, seam-05):

```python
    remote = data.get("remote")
    if isinstance(remote, dict):
        dev_tok = remote.get("device_token") or ""
        remote["device_token"] = ""
        remote["remote_token_configured"] = bool(dev_tok)
        remote["remote_configured"] = bool(
            remote.get("enabled") and remote.get("relay_url"))
        data["remote"] = remote
    return data
```

new_string:

```python
    remote = data.get("remote")
    if isinstance(remote, dict):
        dev_tok = remote.get("device_token") or ""
        remote["device_token"] = ""
        remote["remote_token_configured"] = bool(dev_tok)
        remote["remote_configured"] = bool(
            remote.get("enabled") and remote.get("relay_url"))
        data["remote"] = remote
    # C weather block: the Astrospheric API key is a secret (weather spec §2/§8).
    # Blank it and surface an ``astrospheric_configured`` boolean so the UI can
    # show set/not-set without the value. Rebuilt defensively (auth/remote
    # idiom) — a malformed weather dict can't slip the key through.
    weather = data.get("weather")
    if isinstance(weather, dict):
        as_key = weather.get("astrospheric_api_key") or ""
        weather["astrospheric_api_key"] = None
        weather["astrospheric_configured"] = bool(as_key)
        data["weather"] = weather
    return data
```

- [ ] **1.7 Run the config-level tests — expect PASS** (route tests still fail with 404):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py -q
```

Expected: 4 passed (defaults/bounds, old-config load, set_weather, redacted scrub), 3 failed (the three route tests, `assert 404 == 200` style).

- [ ] **1.8 Add the route.** Edit `server/astrodeck/api/app.py`. First add `WeatherConfig` to the existing config import (lines 49-52): the import currently reads `from ..config import (AlertSink, AuthConfig, ConfigVersionConflict,` followed by more names — add `WeatherConfig` into that parenthesized list (keep alphabetical placement; the exact surrounding names vary, so open the file and extend the tuple). Then insert the route immediately AFTER the survey config route. old_string (seam-09 route body):

```python
    @app.post("/api/config/survey")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_survey_config(
            body: SurveyConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        cfg = await asyncio.to_thread(config_store.set_survey, body)
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)
```

new_string:

```python
    @app.post("/api/config/survey")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_survey_config(
            body: SurveyConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        cfg = await asyncio.to_thread(config_store.set_survey, body)
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ---------------------------------------------------- weather config (weather spec §2)
    # Same cap/broadcast shape as the survey route above, PLUS the optimistic-
    # concurrency version token (put_site idiom). Secret write contract
    # (deadman_url precedent): a null/empty astrospheric_api_key means "leave
    # the stored key unchanged" — the UI only ever sees the masked config, so
    # it round-trips a blank; clear_astrospheric_key=True clears explicitly.

    class WeatherSaveBody(BaseModel):
        weather: WeatherConfig
        version: int | None = None
        clear_astrospheric_key: bool = False

    @app.post("/api/config/weather")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_weather_config(
            body: WeatherSaveBody,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        weather = body.weather
        if body.clear_astrospheric_key:
            weather = weather.model_copy(update={"astrospheric_api_key": None})
        elif not weather.astrospheric_api_key:
            weather = weather.model_copy(update={
                "astrospheric_api_key":
                    config_store.cfg().weather.astrospheric_api_key})
        try:
            cfg = await asyncio.to_thread(
                config_store.set_weather, weather, body.version)
        except ConfigVersionConflict as e:
            # conflict body rides the SAME redaction seams as every config echo
            # (B rule): redacted() scrubs secrets (incl. the astrospheric key),
            # _redact_site_for strips the precise site for non-holders.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": _redact_site_for(redacted(e.current), principal)})
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)
```

- [ ] **1.9 Run all Task-1 tests — expect PASS:**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py -q
```

Expected: `7 passed`.

- [ ] **1.10 Full suite gate, then commit:**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: all passing (1071 + 7 new). Commit:

```
git add server/astrodeck/config.py server/astrodeck/api/app.py server/tests/test_weather.py
git commit -m "feat(weather): WeatherConfig + astrospheric key scrub + POST /api/config/weather (spec §2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 2: WeatherService — poller, both fetchers, cache/staleness, payload, lifespan wiring

**Files**
- Create: `server/astrodeck/weather.py`
- Modify: `server/astrodeck/api/app.py` (import + lifespan start/stop, seam-08 region app.py:112-224)
- Test: `server/tests/test_weather.py` (extend)

**Interfaces**
- Consumes: `WeatherConfig` on `config_store.cfg().weather` (Task 1); `Site` model (`cfg().site.latitude/.longitude/.is_default`, config.py:55-58 — floats bounded ±90/±180, default 0.0 with `is_default=True`); `bus.publish(type, **data)` / `bus.log(level, message, source)` (seam-29); ResumeArm service shape (seam-19 `start/stop/_run`, resume_arm.py:47-68); `_fetch_cutout` fetch shape (seam-16); `schedule._night_dusk(lat, lon, twilight_deg, now)` / `schedule._night_dawn(...)` -> `float | None` (seam-20); lifespan region (seam-08).
- Produces (Tasks 3-5 rely on these EXACT names in `astrodeck.weather`):
  - Constants: `CHECK_INTERVAL_S = 60.0`, `OPEN_METEO_INTERVAL_S = 900.0`, `ASTROSPHERIC_INTERVAL_S = 21600.0`, `OPEN_METEO_STALE_S = 2700.0`, `ASTROSPHERIC_STALE_S = 43200.0`, `OPEN_METEO_URL`, `ASTROSPHERIC_URL`.
  - `class NoNightError(Exception)`.
  - `def _iso_z(ts: float) -> str` (module helper, used by tests).
  - `class WeatherService` with `__init__(self, *, clock=time.time)`, `start() -> None`, `async stop() -> None`, `async tick() -> None`, `payload(self, now: float | None = None) -> dict` (spec §7 shape), `_tonight(self, now: float) -> tuple[float, float] | None`, `_ignore_active(self, now: float) -> bool`, `set_ignore_tonight(self, ignore: bool, now: float | None = None) -> None` (raises `NoNightError`), plus state fields `_om_times: list[float]`, `_om_series: dict[str, list[int]]`, `_om_fetched_ts: float | None`, `_om_attempt_at: float`, `_as_times/_as_seeing/_as_trans/_as_fetched_ts/_as_credits/_as_attempt_at`, `_alert: dict | None`, `_alert_dawn_ts: float | None`, `_alert_night_key: int | None`, `_ignore_night_key: int | None`, `_was_enabled: bool`.
  - Module singleton `weather_service = WeatherService()`.

**Steps**

- [ ] **2.1 Write the failing tests.** Append to `server/tests/test_weather.py` (after the Task-1 route tests) EXACTLY:

```python
# ============================================================ WeatherService
# Poller cadence / fetchers / cache / staleness / payload (spec §3, §7, §14).
# Injected clock + monkeypatched httpx (test_survey _FakeClient/_Boom +
# test_resume_arm clock patterns). The bus is replaced by a recorder so
# publish/log assertions need no asyncio queue draining.

import httpx  # noqa: E402  (section import, mirrors test_survey.py style)

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
                      "threshold_pct", "sustain_minutes", "forecast",
                      "astrospheric", "alert"}
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
    # NO coordinates anywhere in the payload (spec §7)
    assert "34.2" not in str(p) and "118.1" not in str(p)
    # a successful refresh published the same shape on the bus
    published = [d for t, d in rec.published if t == "weather"]
    assert published and set(published[-1]) == set(p)
```

- [ ] **2.2 Run — expect FAIL** (ImportError: no module `astrodeck.weather`):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py -q
```

- [ ] **2.3 Create `server/astrodeck/weather.py`** with EXACTLY this content:

```python
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
```

- [ ] **2.4 Wire the lifespan.** Edit `server/astrodeck/api/app.py`. First the import — old_string:

```python
from ..sequence.session import migrate_legacy_resume, session_store
```

new_string:

```python
from ..sequence.session import migrate_legacy_resume, session_store
from ..weather import weather_service
```

Then the start — old_string (seam-08):

```python
    # Auto-resume-at-dusk service (sessions spec §5) — its own 60s asyncio loop.
    resume_arm.start()
```

new_string:

```python
    # Auto-resume-at-dusk service (sessions spec §5) — its own 60s asyncio loop.
    resume_arm.start()
    # Weather forecast poller (weather spec §3) — its own 60 s asyncio loop.
    # Started UNCONDITIONALLY: each tick no-ops unless cfg.weather.enabled AND
    # the site is set, so runtime config toggles take effect within one tick.
    weather_service.start()
```

Then the stop — old_string (seam-08 finally block head):

```python
    finally:
        await resume_arm.stop()
        await dispatcher.stop()
```

new_string:

```python
    finally:
        await weather_service.stop()
        await resume_arm.stop()
        await dispatcher.stop()
```

- [ ] **2.5 Run the Task-2 tests — expect PASS:**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py -q
```

Expected: `15 passed` (7 Task-1 + 8 new).

- [ ] **2.6 Full suite gate, then commit:**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: all passing. Commit:

```
git add server/astrodeck/weather.py server/astrodeck/api/app.py server/tests/test_weather.py
git commit -m "feat(weather): WeatherService poller — Open-Meteo 15-min + Astrospheric 6-h fetchers, in-memory cache, spec-§7 payload, lifespan wiring (spec §3, §7)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 3: Breach math, veto, night-warning latch, ResumeArm wiring, ignore-tonight route

**Files**
- Modify: `server/astrodeck/weather.py` (add `_needed_samples`, `_sustained_breach`, `veto_reason`, `_evaluate_night_warning`; call the evaluator from `_refresh_open_meteo`)
- Modify: `server/astrodeck/sequence/resume_arm.py` (constructor `weather=None` kwarg; `resume_veto()` delegates; docstring update)
- Modify: `server/astrodeck/api/app.py` (ResumeArm wiring at ~line 88; `POST /api/weather/ignore-tonight` route; import `NoNightError`)
- Test: `server/tests/test_weather.py` (extend), `server/tests/test_resume_arm.py` (extend)

**Interfaces**
- Consumes: everything Task 2 produced (`WeatherService` state fields `_om_times`, `_om_series`, `_om_fetched_ts`, `_alert`, `_alert_dawn_ts`, `_alert_night_key`; `_tonight(now) -> tuple[float, float] | None`; `set_ignore_tonight(ignore, now=None)` raising `NoNightError`; `_ignore_active(now) -> bool`; constants `OPEN_METEO_STALE_S = 2700.0`; module helper `_iso_z(ts)`; singleton `weather_service`). ResumeArm seam-19 verbatim (constructor at resume_arm.py:34-45, `resume_veto` at :70-73, the veto call site at :120-125 logs `f"auto-resume vetoed: {veto} — retrying in ..."` and arms `self._retry_at = now + RETRY_INTERVAL_S`). Route idiom seam-09; `bus` + `HTTPException` already imported in app.py.
- Produces:
  - `WeatherService._needed_samples(self, sustain_minutes: int) -> int` = `max(1, sustain_minutes // 15)`.
  - `WeatherService._sustained_breach(self, lo: float, hi: float, threshold: int, needed: int) -> tuple[int, int] | None` (inclusive index run into the Open-Meteo series).
  - `WeatherService.veto_reason(self, now: float) -> str | None` — the reason string is EXACTLY `f"cloud cover {peak}% forecast within the next hour (threshold {threshold}%)"`.
  - `WeatherService._evaluate_night_warning(self, now: float) -> None` (sets `_alert`/`_alert_dawn_ts`/`_alert_night_key`, emits ONE `bus.log("warning", ...)` per night).
  - `ResumeArm.__init__(self, engine, hub, *, clock=time.time, weather=None)`; `resume_veto()` returns `self._weather.veto_reason(self._clock())` when a service is injected, else `None`.
  - Route `POST /api/weather/ignore-tonight`, cap `CAP_CONTROL_CAPTURE`, body `{"ignore": bool}`, 409 `{"detail": {"detail": ..., "code": "no_night"}}`, returns the weather payload and publishes it on the bus.

**Steps**

- [ ] **3.1 Write the failing WeatherService tests.** Append to `server/tests/test_weather.py` EXACTLY:

```python
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
```

- [ ] **3.2 Write the failing ResumeArm veto-matrix tests.** Append to `server/tests/test_resume_arm.py` (the file's existing harness — `sim_hub` fixture, `wait_for`, `_plan`, `_dormant_armed`, `RETRY_INTERVAL_S` — is shown verbatim in seam-30 of `.superpowers/sdd/seams/weather-server.md`; reuse it, do not redefine):

```python
# =================================================== weather veto (spec §4/§14)
# ResumeArm stays thin: veto logic lives in WeatherService; these tests inject
# a fake with a fixed veto_reason. The stale/disabled/ignore-tonight variants
# all collapse to veto_reason() -> None inside the real service and are
# covered service-level in tests/test_weather.py.


class _FakeWeather:
    def __init__(self, reason):
        self._reason = reason

    def veto_reason(self, now):
        return self._reason


async def test_weather_veto_blocks_resume_and_arms_retry(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"],
                    weather=_FakeWeather(
                        "cloud cover 80% forecast within the next hour "
                        "(threshold 50%)"))
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running                      # vetoed BEFORE any device touch
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S   # 10-min retry latch
    from astrodeck.events import bus
    assert any("auto-resume vetoed: cloud cover 80%" in
               (e["data"].get("message") or "")
               for e in bus.log_history), "veto warning must be logged"


async def test_weather_veto_none_resumes(sim_hub, monkeypatch):
    """veto_reason None (the real service's stale/disabled/ignored outcomes)
    -> the run starts. Constructor default weather=None (no service injected,
    back-compat) is covered by the existing resume tests above."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"],
                    weather=_FakeWeather(None))
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert engine.running
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"
```

- [ ] **3.3 Run both files — expect FAIL** (`AttributeError: 'WeatherService' object has no attribute 'veto_reason'`; `TypeError: ResumeArm.__init__() got an unexpected keyword argument 'weather'`):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py tests/test_resume_arm.py -q
```

- [ ] **3.4 Add the breach/veto/warning methods to `server/astrodeck/weather.py`.** Edit — old_string (the section header from Task 2):

```python
    # -- payload (spec §7) -----------------------------------------------------
```

new_string:

```python
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
```

- [ ] **3.5 Call the evaluator on every successful Open-Meteo refresh.** Edit `server/astrodeck/weather.py` — old_string:

```python
        self._om_times, self._om_series = times, series
        self._om_fetched_ts = now
        bus.publish("weather", **self.payload(now))
```

new_string:

```python
        self._om_times, self._om_series = times, series
        self._om_fetched_ts = now
        # spec §5: evaluate BEFORE publishing so a fresh alert rides this
        # payload (the publish is emission #1; the bus.log inside is #2).
        self._evaluate_night_warning(now)
        bus.publish("weather", **self.payload(now))
```

- [ ] **3.6 Wire ResumeArm.** Edit `server/astrodeck/sequence/resume_arm.py` — old_string (seam-19):

```python
    def __init__(self, engine, hub, *, clock=time.time):
        self.engine = engine
        self.hub = hub
        self._clock = clock
```

new_string:

```python
    def __init__(self, engine, hub, *, clock=time.time, weather=None):
        self.engine = engine
        self.hub = hub
        self._clock = clock
        # sub-project C (weather spec §4): injected WeatherService (like clock,
        # so tests inject fakes). None = no weather gate (back-compat).
        self._weather = weather
```

Then — old_string (seam-19):

```python
    def resume_veto(self) -> str | None:
        """Veto hook (spec §5). v1: no veto — sub-project C plugs the
        cloud/precip forecast gate in here. Non-None = human-readable reason."""
        return None
```

new_string:

```python
    def resume_veto(self) -> str | None:
        """Veto hook (sessions spec §5 / weather spec §4): delegates to the
        injected WeatherService. Non-None = human-readable reason; the caller
        (tick, :120-125) logs it and arms the 10-min retry latch BEFORE any
        device is touched. No service injected -> no veto (back-compat)."""
        if self._weather is None:
            return None
        return self._weather.veto_reason(self._clock())
```

Also update the module docstring sentence — old_string:

```python
run_start alert on success comes free from the AlertDispatcher's sequence
state machine. ``resume_veto()`` is the sub-project-C weather-gate hook — v1
always returns None.
```

new_string:

```python
run_start alert on success comes free from the AlertDispatcher's sequence
state machine. ``resume_veto()`` delegates to the injected WeatherService
(sub-project C, weather spec §4); with no service injected it returns None.
```

- [ ] **3.7 Wire the singleton + ignore-tonight route in `server/astrodeck/api/app.py`.** Import — old_string (added in Task 2):

```python
from ..weather import weather_service
```

new_string:

```python
from ..weather import NoNightError, weather_service
```

Wiring — old_string (seam-07):

```python
resume_arm = ResumeArm(engine, hub)
```

new_string:

```python
resume_arm = ResumeArm(engine, hub, weather=weather_service)
```

Route — insert after the `set_weather_config` route (Task 1), anchored on that route's unambiguous tail. old_string:

```python
        except ConfigVersionConflict as e:
            # conflict body rides the SAME redaction seams as every config echo
            # (B rule): redacted() scrubs secrets (incl. the astrospheric key),
            # _redact_site_for strips the precise site for non-holders.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": _redact_site_for(redacted(e.current), principal)})
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)
```

new_string:

```python
        except ConfigVersionConflict as e:
            # conflict body rides the SAME redaction seams as every config echo
            # (B rule): redacted() scrubs secrets (incl. the astrospheric key),
            # _redact_site_for strips the precise site for non-holders.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": _redact_site_for(redacted(e.current), principal)})
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ------------------------------------------------- ignore-tonight (weather spec §4)
    # Runtime flag on the WeatherService, NOT persisted config. Gated
    # control.capture (operator — it affects sequencing, like other control
    # caps). Keyed to tonight's dusk; auto-expires when a new night begins.
    # The updated flag rides the weather payload so ALL clients see it.

    class IgnoreTonightBody(BaseModel):
        ignore: bool

    @app.post("/api/weather/ignore-tonight")
    @declare(CAP_CONTROL_CAPTURE)
    async def weather_ignore_tonight(
            body: IgnoreTonightBody,
            principal: Principal = Depends(require(CAP_CONTROL_CAPTURE))):
        try:
            weather_service.set_ignore_tonight(body.ignore)
        except NoNightError:
            raise HTTPException(409, detail={
                "detail": "no night resolves for the configured site",
                "code": "no_night"})
        payload = weather_service.payload()
        bus.publish("weather", **payload)
        return payload
```

- [ ] **3.8 Run both files — expect PASS:**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather.py tests/test_resume_arm.py -q
```

Expected: `22 passed` in test_weather.py (15 prior + 7 new) plus all of test_resume_arm.py (existing + 2 new) green.

- [ ] **3.9 Full suite gate, then commit:**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: all passing. Commit:

```
git add server/astrodeck/weather.py server/astrodeck/sequence/resume_arm.py server/astrodeck/api/app.py server/tests/test_weather.py server/tests/test_resume_arm.py
git commit -m "feat(weather): sustained-breach veto + once-per-night high-cloud warning latch + ResumeArm wiring + ignore-tonight route (spec §4-§5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 4: GET /api/weather + WS weather-event DROP on both lanes + RBAC tests

**Files**
- Modify: `server/astrodeck/api/redact.py` (`_redact_ws_event` gains the drop rule; return type `dict | None`)
- Modify: `server/astrodeck/api/app.py` (LAN /ws send-site None-check ~line 2950-3023; `GET /api/weather` route)
- Modify: `server/astrodeck/remote/relay_client.py` (relay `_run_ws` send-site None-check ~line 559-1581 region)
- Test: `server/tests/test_rbac_enforcement.py` (extend), `server/tests/test_remote_relay.py` (extend)

**Interfaces**
- Consumes: `weather_service.payload()` (Task 2); `_redact_ws_event(ev_json, principal)` (seam-27, redact.py:41-106 — currently always returns dict); LAN WS fan-out (seam-11, app.py:2950-3023: `await websocket.send_json(_redact_ws_event(ev.to_json(), principal))`); relay lane (seam-28, relay_client.py:514-606: `payload=_event_payload(redact._redact_ws_event(ev.to_json(), principal))`); RBAC harness `_make_client`/`FakeAuthProvider`/`_install` (seam-35), `principal_for_role` (test imports, seam-35), `_seed_precise_site` (test_rbac_enforcement.py:375); relay test helpers `FakeChannel` (test_remote_relay.py:72), `_make_relay_client(app, channel)` (:120), `_FixedPrincipalProvider` (:593), `_ws_data_payloads(channel)` (:608), the strip-test shape (seam-38).
- Produces:
  - `_redact_ws_event(ev_json: dict, principal: Principal | None) -> dict | None` — returns `None` for `type == "weather"` + non-holder; BOTH WS lanes skip the send on None.
  - Route `GET /api/weather`, cap `CAP_VIEW_SITE_PRECISE`, returns `weather_service.payload()` (spec §7 shape).

**Steps**

- [ ] **4.1 Write the failing RBAC tests.** Append to `server/tests/test_rbac_enforcement.py` EXACTLY:

```python
# ===================================================== weather (spec §7/§8/§14)
# All four weather routes are @declare-gated (the boot assertion covers them at
# create_app time); WS `weather` events are DROPPED entirely (not stripped) for
# non-holders of view.site_precise on the LAN lane; the astrospheric key is
# absent from redacted config, config echoes, and 409 bodies (T-RBAC-13b
# family). The relay-lane drop test lives in tests/test_remote_relay.py.


def _make_weather_client(tmp_path, monkeypatch):
    """_make_client + a FRESH WeatherService bound to the temp store (the
    module singleton would otherwise leak ignore/alert state across tests and
    read the global config store). Routes and the lifespan look the singleton
    up as an app-module global at call time, so monkeypatching it works."""
    store, app = _make_client(tmp_path, monkeypatch)
    import astrodeck.weather as weather_mod
    monkeypatch.setattr(weather_mod, "config_store", store)
    monkeypatch.setattr(app_module, "weather_service",
                        weather_mod.WeatherService())
    return store, app


def test_weather_routes_gated_for_viewer(tmp_path, monkeypatch):
    """Viewer holds view.status only: 403 on the weather GET (view.site_precise),
    ignore-tonight (control.capture), and config (config.site_optics)."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        assert c.get("/api/weather").status_code == 403
        r = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r.status_code == 403
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 403


def test_operator_ignore_tonight_allowed_config_and_get_denied(tmp_path, monkeypatch):
    """Operator holds control.capture (ignore-tonight passes the gate; a
    default site then yields the 409 no_night contract) but NOT
    config.site_optics nor view.site_precise."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("operator"))
    with TestClient(app) as c:
        r = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r.status_code == 409                 # gate passed; no night (default site)
        assert r.json()["detail"]["code"] == "no_night"
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": None})
        assert r.status_code == 403
        assert c.get("/api/weather").status_code == 403


def test_admin_weather_get_and_ignore_roundtrip(tmp_path, monkeypatch):
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _seed_precise_site(store)                       # real site -> night resolves
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.get("/api/weather")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"enabled", "fetched_ts", "stale", "ignore_tonight",
                             "threshold_pct", "sustain_minutes", "forecast",
                             "astrospheric", "alert"}
        assert body["enabled"] is False and body["forecast"] is None
        r2 = c.post("/api/weather/ignore-tonight", json={"ignore": True})
        assert r2.status_code == 200
        assert r2.json()["ignore_tonight"] is True
        assert c.get("/api/weather").json()["ignore_tonight"] is True


def test_ws_weather_event_dropped_for_viewer_kept_for_admin(tmp_path, monkeypatch):
    """LAN lane (spec §8): type=='weather' + non-holder -> the frame is NEVER
    sent (dropped, not stripped). A later marker event proves ordering."""
    from astrodeck.events import bus
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("viewer"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            bus.publish("weather", enabled=True, stale=False)
            bus.publish("safety", is_safe=True)     # ordered marker
            # drain until the marker: bus ordering guarantees the weather frame
            # (if it leaked) would arrive BEFORE the safety marker; unrelated
            # background events may interleave and are ignored.
            seen = []
            while True:
                ev = ws.receive_json()
                seen.append(ev["type"])
                if ev["type"] == "safety":
                    break
            assert "weather" not in seen, f"weather frame leaked: {seen}"
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()                        # hello
            bus.publish("weather", enabled=True, stale=False)
            while True:                              # holder receives verbatim
                ev = ws.receive_json()
                if ev["type"] == "weather":
                    break
            assert ev["data"]["enabled"] is True


def test_astrospheric_key_scrubbed_everywhere(tmp_path, monkeypatch):
    """T-RBAC-13b family (spec §8): key absent from the config-route echo, the
    generic GET /api/config echo, AND the 409 conflict body."""
    store, app = _make_weather_client(tmp_path, monkeypatch)
    _install(principal_for_role("admin"))
    with TestClient(app) as c:
        r = c.post("/api/config/weather", json={
            "weather": {"enabled": True, "cloud_threshold_pct": 60,
                        "sustain_minutes": 45,
                        "astrospheric_api_key": "SECRET-KEY-XYZ"},
            "version": None})
        assert r.status_code == 200
        assert "SECRET-KEY-XYZ" not in r.text
        w = r.json()["weather"]
        assert w["astrospheric_api_key"] is None
        assert w["astrospheric_configured"] is True
        assert store.cfg().weather.astrospheric_api_key == "SECRET-KEY-XYZ"
        assert "SECRET-KEY-XYZ" not in c.get("/api/config").text
        r2 = c.post("/api/config/weather", json={
            "weather": {"enabled": False, "cloud_threshold_pct": 50,
                        "sustain_minutes": 30, "astrospheric_api_key": None},
            "version": 1})                           # stale token -> 409
        assert r2.status_code == 409
        assert "SECRET-KEY-XYZ" not in r2.text
```

- [ ] **4.2 Write the failing relay-lane test.** Append to `server/tests/test_remote_relay.py` (reuse its existing helpers — `FakeChannel`, `_make_relay_client`, `_FixedPrincipalProvider`, `_ws_data_payloads`, `_make_client`, `principal_for_role`, `set_active_provider`, `FrameType`, `asyncio` — all already defined/imported in that file; seam-38 shows the harness shape verbatim):

```python
def test_tunneled_ws_drops_weather_for_viewer(tmp_path, monkeypatch):
    """WS `weather` events are DROPPED ENTIRELY (not stripped) for a principal
    lacking view.site_precise on the RELAY lane too (weather spec §8) — the
    relay handler must skip the send when _redact_ws_event returns None."""
    store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("viewer")))

    async def _scenario():
        channel = FakeChannel()
        client = _make_relay_client(app, channel)
        channel.push_frame(FrameType.WS_OPEN, 7,
                           {"path": "/ws", "query": "", "ws_id": "wsA"})
        task = asyncio.create_task(client._serve_once(client._config()))
        await asyncio.sleep(0.05)  # authorize + hello + enter loop
        from astrodeck.events import bus
        bus.publish("weather", enabled=True, stale=False)
        bus.publish("status", site={"is_default": True, "horizon_min_deg": 15.0})
        await asyncio.sleep(0.05)
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)

        payloads = await _ws_data_payloads(channel)
        types = [p["type"] for p in payloads]
        assert "status" in types            # the LATER event arrived...
        assert "weather" not in types       # ...but the weather frame was dropped

    asyncio.run(_scenario())


def test_tunneled_ws_delivers_weather_to_admin(tmp_path, monkeypatch):
    """A view.site_precise holder receives the weather event verbatim over the
    relay (the drop rule is non-holder-only)."""
    store, app = _make_client(tmp_path, monkeypatch)
    set_active_provider(_FixedPrincipalProvider(principal_for_role("admin")))

    async def _scenario():
        channel = FakeChannel()
        client = _make_relay_client(app, channel)
        channel.push_frame(FrameType.WS_OPEN, 7,
                           {"path": "/ws", "query": "", "ws_id": "wsA"})
        task = asyncio.create_task(client._serve_once(client._config()))
        await asyncio.sleep(0.05)
        from astrodeck.events import bus
        bus.publish("weather", enabled=True, stale=False)
        await asyncio.sleep(0.05)
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)

        payloads = await _ws_data_payloads(channel)
        weather = [p for p in payloads if p["type"] == "weather"]
        assert weather and weather[0]["data"]["enabled"] is True

    asyncio.run(_scenario())
```

- [ ] **4.3 Run — expect FAIL** (`404` on GET /api/weather in the admin test; the drop tests fail with `weather` present in types / `assert ev["type"] == "safety"` failing):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_enforcement.py tests/test_remote_relay.py -q
```

- [ ] **4.4 Extend `_redact_ws_event`.** Edit `server/astrodeck/api/redact.py` — old_string (seam-27):

```python
def _redact_ws_event(ev_json: dict, principal: Principal | None) -> dict:
    """Strip precise site keys in a broadcast WS event for a principal lacking
    ``view.site_precise``. The bus ``Event.data`` is SHARED across every
    subscriber, so we must NEVER mutate it in place -- we copy only the nodes we
    change (status carries ``data.site``; config carries ``data.config.site``).
    A holder sees the event verbatim (no copy).

    CONTRACT (spec §8): any FUTURE event or payload that embeds site
    coordinates MUST place them at ``data.site`` or ``data.config.site`` so this
    seam catches them. Site data reachable by no other path is the invariant
    that makes this the single enforcement point; do NOT add a second lane."""
    if principal is not None and principal.has(CAP_VIEW_SITE_PRECISE):
        return ev_json
    data = ev_json.get("data")
```

new_string:

```python
def _redact_ws_event(ev_json: dict, principal: Principal | None) -> dict | None:
    """Strip precise site keys in a broadcast WS event for a principal lacking
    ``view.site_precise``. The bus ``Event.data`` is SHARED across every
    subscriber, so we must NEVER mutate it in place -- we copy only the nodes we
    change (status carries ``data.site``; config carries ``data.config.site``).
    A holder sees the event verbatim (no copy).

    Weather events (sub-project C, weather spec §8) are DROPPED ENTIRELY (not
    stripped) for non-holders: this returns ``None`` and BOTH WS lanes (the
    LAN /ws handler in api/app.py and the relay ``_run_ws`` in
    remote/relay_client.py) skip the send on None. Rationale: max-privacy —
    even location-free forecast numbers describe conditions at the site.

    CONTRACT (spec §8): any FUTURE event or payload that embeds site
    coordinates MUST place them at ``data.site`` or ``data.config.site`` so this
    seam catches them. Site data reachable by no other path is the invariant
    that makes this the single enforcement point; do NOT add a second lane."""
    if principal is not None and principal.has(CAP_VIEW_SITE_PRECISE):
        return ev_json
    if ev_json.get("type") == "weather":
        return None
    data = ev_json.get("data")
```

- [ ] **4.5 None-check the LAN lane.** Edit `server/astrodeck/api/app.py` — old_string (seam-11 tail):

```python
                if ev is not None:
                    await websocket.send_json(
                        _redact_ws_event(ev.to_json(), principal))
```

new_string:

```python
                if ev is not None:
                    out = _redact_ws_event(ev.to_json(), principal)
                    if out is not None:  # None = dropped event (weather spec §8)
                        await websocket.send_json(out)
```

- [ ] **4.6 None-check the relay lane.** Edit `server/astrodeck/remote/relay_client.py` — old_string (seam-28 tail):

```python
                if ev is not None:
                    seq += 1
                    await self._send_frame(Frame(
                        type=FrameType.WS_DATA, stream_id=wire_stream_id,
                        header={"ws_id": ws_id, "seq": seq},
                        payload=_event_payload(
                            redact._redact_ws_event(ev.to_json(), principal))))
```

new_string:

```python
                if ev is not None:
                    out = redact._redact_ws_event(ev.to_json(), principal)
                    if out is not None:  # None = dropped event (weather spec §8)
                        seq += 1
                        await self._send_frame(Frame(
                            type=FrameType.WS_DATA, stream_id=wire_stream_id,
                            header={"ws_id": ws_id, "seq": seq},
                            payload=_event_payload(out)))
```

- [ ] **4.7 Add `GET /api/weather`.** Edit `server/astrodeck/api/app.py` — insert after the ignore-tonight route (Task 3). old_string:

```python
        payload = weather_service.payload()
        bus.publish("weather", **payload)
        return payload
```

new_string:

```python
        payload = weather_service.payload()
        bus.publish("weather", **payload)
        return payload

    # ---------------------------------------------------- weather read (weather spec §7)
    # Full payload, holders only (spec §8: REST requires the cap outright — no
    # partial payloads). The payload itself is coordinate-free by construction.

    @app.get("/api/weather")
    @declare(CAP_VIEW_SITE_PRECISE)
    async def get_weather(
            principal: Principal = Depends(require(CAP_VIEW_SITE_PRECISE))):
        return weather_service.payload()
```

- [ ] **4.8 Run — expect PASS:**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_enforcement.py tests/test_remote_relay.py tests/test_weather.py -q
```

Expected: all passing (5 new RBAC + 2 new relay tests green; the boot RBAC assertion also implicitly covers the new `@declare` routes on every `create_app()`).

- [ ] **4.9 Full suite gate, then commit:**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: all passing. Commit:

```
git add server/astrodeck/api/redact.py server/astrodeck/api/app.py server/astrodeck/remote/relay_client.py server/tests/test_rbac_enforcement.py server/tests/test_remote_relay.py
git commit -m "feat(weather): GET /api/weather + WS weather-event DROP for non-holders on both lanes + RBAC/secret tests (spec §7-§8)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 5: IEM radar/satellite tile proxy

**Files**
- Modify: `server/astrodeck/api/app.py` (stdlib imports `shutil`/`time` + third-party `httpx`; `CAPTURE_DIR` from hub; module-level tile constants/helpers after `UI_DIST`; `GET /api/weather/tile/{layer}/{z}/{x}/{y}.png` route after `get_weather`)
- Test: `server/tests/test_weather_tiles.py` (create)

**Interfaces**
- Consumes: `catalog/tiles.py` pattern verbatim (seam-18: single-flight, atomic `.tmp`→replace write, disk-free guard, zero-httpx-when-disabled, PNG-magic analog of the JPEG-SOI check — COPY the pattern, do NOT import its private helpers); `config_store.cfg().weather.enabled` (Task 1); `CAP_VIEW_SITE_PRECISE`/`declare`/`require` (already imported in app.py); `CAPTURE_DIR` (hub module; survey.py:39 precedent `from ..hub import CAPTURE_DIR`).
- Produces (Task 8's RadarMap relies on this contract):
  - `GET /api/weather/tile/{layer}/{z}/{x}/{y}.png`, `layer: Literal["radar", "satellite"]`, cap `CAP_VIEW_SITE_PRECISE`, zoom clamp `3 <= z <= 11`, `0 <= x,y < 2**z` else 422; disabled → 404 `Cache-Control: no-store` with ZERO httpx; success → PNG with `Cache-Control: private, max-age=240` (radar) / `max-age=600` (satellite); upstream failure → 502 `no-store`, never cached.
  - app.py module globals (tests monkeypatch these): `_WEATHER_TILE_CACHE_DIR: Path`, `IEM_TILE_BASE`, `IEM_SLUGS = {"radar": "nexrad-n0q-900913", "satellite": "goes_east_fulldisk_ch13"}`, `WEATHER_TILE_TTL_S = {"radar": 240, "satellite": 600}`.

**Steps**

- [ ] **5.1 Write the failing tests.** Create `server/tests/test_weather_tiles.py` with EXACTLY this content:

```python
"""Weather tile proxy tests (weather spec §6/§14): Literal/range 422, disabled
= 404 no-store + zero-httpx (_Boom), verbatim IEM slugs, TTL disk cache hit
skips the network, PNG-magic validation, failure never cached, RBAC gate."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore

_PNG = b"\x89PNG\r\n\x1a\n" + b"tilebytes"


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]


class _FakeTileClient:
    body = _PNG
    fail = False
    calls = 0
    last_url = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        type(self).calls += 1
        type(self).last_url = url
        if type(self).fail:
            raise httpx.ConnectError("down")
        return _FakeResp(type(self).body)


class _BoomTile:
    def __init__(self, *a, **kw):
        raise AssertionError("httpx client constructed while weather disabled")


class _FixedProvider:
    name = "fixed"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def client(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.weather as weather_mod
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg().weather.enabled = True             # site stays default: the
    monkeypatch.setattr(config_mod, "config_store", store)   # poller no-ops,
    monkeypatch.setattr(hub_mod, "config_store", store)      # only the proxy
    monkeypatch.setattr(app_module, "config_store", store)   # fetches
    monkeypatch.setattr(weather_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "_WEATHER_TILE_CACHE_DIR",
                        tmp_path / "_weather_tiles")
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _FakeTileClient)
    _FakeTileClient.body = _PNG
    _FakeTileClient.fail = False
    _FakeTileClient.calls = 0
    _FakeTileClient.last_url = None
    reset_active_provider()                        # open default => admin
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, store
    reset_active_provider()


def test_layer_and_range_validation_422(client):
    c, store = client
    assert c.get("/api/weather/tile/wind/5/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/2/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/12/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/5/32/1.png").status_code == 422  # x >= 2**5
    assert c.get("/api/weather/tile/radar/5/1/-1.png").status_code == 422


def test_disabled_404_no_store_zero_httpx(client, monkeypatch):
    c, store = client
    store.cfg().weather.enabled = False
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _BoomTile)
    r = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r.status_code == 404
    assert r.headers.get("cache-control") == "no-store"


def test_tile_served_verbatim_slugs_and_ttl_cache(client):
    c, store = client
    r1 = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r1.status_code == 200
    assert r1.headers["cache-control"] == "private, max-age=240"
    assert r1.content == _PNG
    # verbatim live-verified slug (spec §6) in the hardcoded upstream URL
    assert _FakeTileClient.last_url == (
        "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "nexrad-n0q-900913/5/8/12.png")
    # TTL cache hit skips the network entirely
    _FakeTileClient.fail = True
    r2 = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r2.status_code == 200 and _FakeTileClient.calls == 1
    _FakeTileClient.fail = False
    r3 = c.get("/api/weather/tile/satellite/5/8/12.png")
    assert r3.status_code == 200
    assert r3.headers["cache-control"] == "private, max-age=600"
    assert _FakeTileClient.last_url == (
        "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "goes_east_fulldisk_ch13/5/8/12.png")


def test_png_validation_and_failure_not_cached(client):
    c, store = client
    _FakeTileClient.body = b"\xff\xd8not-a-png"    # JPEG SOI, not PNG magic
    r = c.get("/api/weather/tile/radar/6/10/20.png")
    assert r.status_code == 502
    assert r.headers.get("cache-control") == "no-store"
    _FakeTileClient.body = _PNG
    r2 = c.get("/api/weather/tile/radar/6/10/20.png")   # failure was NOT cached
    assert r2.status_code == 200 and r2.content == _PNG


def test_upstream_down_502_no_store(client):
    c, store = client
    _FakeTileClient.fail = True
    r = c.get("/api/weather/tile/satellite/7/40/50.png")
    assert r.status_code == 502
    assert r.headers.get("cache-control") == "no-store"


def test_tile_route_view_site_precise_gated(client):
    """Radar tiles centered on the site reveal the site area — holders only
    (spec §6/§8)."""
    c, store = client
    set_active_provider(_FixedProvider(principal_for_role("viewer")))
    assert c.get("/api/weather/tile/radar/5/8/12.png").status_code == 403
    set_active_provider(_FixedProvider(principal_for_role("operator")))
    assert c.get("/api/weather/tile/radar/5/8/12.png").status_code == 403
```

- [ ] **5.2 Run — expect FAIL** (404 route-not-found on every tile URL / AttributeError `_WEATHER_TILE_CACHE_DIR`):

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather_tiles.py -q
```

- [ ] **5.3 Add the imports.** Edit `server/astrodeck/api/app.py` — old_string:

```python
import asyncio
import hmac
import io
import os
```

new_string:

```python
import asyncio
import hmac
import io
import os
import shutil
import time
```

Then — old_string:

```python
from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
```

new_string:

```python
import httpx
from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
```

Then — old_string:

```python
from ..hub import TOUCH_MAX_RATE_DEG_S, hub
```

new_string:

```python
from ..hub import CAPTURE_DIR, TOUCH_MAX_RATE_DEG_S, hub
```

- [ ] **5.4 Add the module-level tile constants + helpers.** Edit `server/astrodeck/api/app.py` — old_string (seam-07):

```python
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
```

new_string:

```python
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"

# ------------------------------------------------ weather tile proxy (weather spec §6)
# IEM tile cache proxy. Upstream HARDCODED server-side (never caller-supplied);
# browsers — possibly on foreign networks, over the relay — only ever hit
# /api/weather/tile/..., so they NEVER contact IEM. The HOME SERVER's IP
# fetching site-area tiles is the same exposure class as the Open-Meteo fetch
# itself (accepted, spec §6). Slugs VERIFIED LIVE 2026-07-16 — copied verbatim.
# Pattern copied from catalog/tiles.py (spec: copy, do NOT import its private
# helpers). Module-level names so tests can monkeypatch the cache dir.
IEM_TILE_BASE = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0"
IEM_SLUGS = {"radar": "nexrad-n0q-900913", "satellite": "goes_east_fulldisk_ch13"}
WEATHER_TILE_TTL_S = {"radar": 240, "satellite": 600}   # radar updates ~5 min
_WEATHER_TILE_TIMEOUT_S = 6.0
_WEATHER_TILE_MIN_FREE_BYTES = 200 * 1024 * 1024        # Pi-card disk guard
_WEATHER_TILE_CACHE_DIR = CAPTURE_DIR / "_weather_tiles"
_WEATHER_NO_STORE = {"Cache-Control": "no-store"}

# Local copy of the refcounted per-key single-flight (tiles.py idiom):
# concurrent requests for the same missing tile coalesce; waiters serve the
# file the leader wrote. Event-loop-only state, no guard lock needed.
_weather_tile_inflight: dict[str, list] = {}


@asynccontextmanager
async def _weather_tile_single_flight(key: str):
    entry = _weather_tile_inflight.get(key)
    if entry is None:
        entry = [asyncio.Lock(), 0]
        _weather_tile_inflight[key] = entry
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0:
            _weather_tile_inflight.pop(key, None)


def _weather_tile_free_bytes() -> int:
    probe = _WEATHER_TILE_CACHE_DIR
    while not probe.exists():                # disk_usage needs an existing path
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _write_weather_tile(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)                        # atomic publish (survey_pack idiom)


async def _fetch_weather_tile(layer: str, z: int, x: int, y: int) -> bytes | None:
    """One request + one retry against the IEM tile cache; PNG-magic-validated
    (the tiles.py JPEG-SOI check, PNG flavor). None on exhaustion."""
    url = f"{IEM_TILE_BASE}/{IEM_SLUGS[layer]}/{z}/{x}/{y}.png"
    headers = {"User-Agent": "AstroDeck/0.1"}
    async with httpx.AsyncClient(timeout=_WEATHER_TILE_TIMEOUT_S,
                                 headers=headers) as client:
        for attempt in range(2):             # initial + one retry
            try:
                r = await client.get(url)
                r.raise_for_status()
                body = r.content
                if not body.startswith(b"\x89PNG"):
                    raise RuntimeError("not a PNG")
                return body
            except Exception:  # noqa: BLE001 — uniform per-attempt failure
                if attempt == 0:
                    await asyncio.sleep(0.3)
    return None
```

- [ ] **5.5 Add the route.** Edit `server/astrodeck/api/app.py` — old_string (the `get_weather` route from Task 4):

```python
    @app.get("/api/weather")
    @declare(CAP_VIEW_SITE_PRECISE)
    async def get_weather(
            principal: Principal = Depends(require(CAP_VIEW_SITE_PRECISE))):
        return weather_service.payload()
```

new_string:

```python
    @app.get("/api/weather")
    @declare(CAP_VIEW_SITE_PRECISE)
    async def get_weather(
            principal: Principal = Depends(require(CAP_VIEW_SITE_PRECISE))):
        return weather_service.payload()

    # ------------------------------------------------- weather tiles (weather spec §6)

    @app.get("/api/weather/tile/{layer}/{z}/{x}/{y}.png")
    @declare(CAP_VIEW_SITE_PRECISE)
    async def weather_tile(
            layer: Literal["radar", "satellite"], z: int, x: int, y: int,
            principal: Principal = Depends(require(CAP_VIEW_SITE_PRECISE))
    ) -> Response:
        if not (3 <= z <= 11):
            raise HTTPException(status_code=422, detail="z out of range [3,11]")
        if not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise HTTPException(status_code=422, detail="x/y out of range for z")
        if not config_store.cfg().weather.enabled:
            # ZERO httpx construction on the disabled path (tiles.py:110-113
            # invariant, _Boom-tested).
            raise HTTPException(status_code=404, detail="weather disabled",
                                headers=dict(_WEATHER_NO_STORE))
        ttl = WEATHER_TILE_TTL_S[layer]
        cache_headers = {"Cache-Control": f"private, max-age={ttl}"}
        path = _WEATHER_TILE_CACHE_DIR / layer / str(z) / str(x) / f"{y}.png"

        def _fresh() -> bool:
            # NOT immutable — these tiles change; freshness = mtime within TTL.
            try:
                return path.exists() and \
                    time.time() - path.stat().st_mtime < ttl
            except OSError:
                return False

        if _fresh():
            return FileResponse(path, media_type="image/png",
                                headers=cache_headers)
        key = f"{layer}/{z}/{x}/{y}"
        async with _weather_tile_single_flight(key):
            if _fresh():                     # a coalesced leader just wrote it
                return FileResponse(path, media_type="image/png",
                                    headers=cache_headers)
            body = await _fetch_weather_tile(layer, z, x, y)
            if body is None:
                # failures return 502 no-store and are NEVER cached (spec §6)
                raise HTTPException(status_code=502,
                                    detail="tile upstream failed",
                                    headers=dict(_WEATHER_NO_STORE))
            if _weather_tile_free_bytes() < _WEATHER_TILE_MIN_FREE_BYTES:
                return Response(body, media_type="image/png",
                                headers=cache_headers)
            await asyncio.to_thread(_write_weather_tile, path, body)
            return Response(body, media_type="image/png",
                            headers=cache_headers)
```

- [ ] **5.6 Run — expect PASS:**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_weather_tiles.py -q
```

Expected: `6 passed`.

- [ ] **5.7 Full suite gate, then commit:**

```
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: all passing. Commit:

```
git add server/astrodeck/api/app.py server/tests/test_weather_tiles.py
git commit -m "feat(weather): IEM radar/satellite tile proxy — verbatim slugs, TTL disk cache, PNG validation, zero-httpx when disabled, view.site_precise-gated (spec §6)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 6: UI foundations — types, api/weather.ts, lib/weather.ts, store slice + handleEvent + selectors

(First UI task — `npm run build` is now permitted.)

**Files**
- Modify: `ui/src/types.ts` (AppConfig weather block after `deadman_configured?: boolean;` ~line 508; weather payload types appended at end of file, currently 1219 lines)
- Create: `ui/src/api/weather.ts`
- Create: `ui/src/lib/weather.ts`
- Modify: `ui/src/store.ts` (type import ~line 4-34; normalizer import ~line 36; AppState slice after `lastReportId` ~line 441; initial state after `lastReportId: null,` ~line 607; `case "weather"` in handleEvent after the `report` case ~line 1130; selectors after `useLastReportId` ~line 1285)
- Test: `ui/src/lib/__tests__/weather.test.ts` (create)

**Interfaces**
- Consumes: server payload shape (Task 2/Task 4 — spec §7); `api` fetch wrapper (`api.get<T>(path)`, `api.post<T>(path, body?)` — ui/src/api.ts:90-96); api-module template (seam-31, api/site.ts); pure-lib template (seam-29 UI-seams, lib/safety.ts); store idioms (seam-02 slice block, seam-05 handleEvent switch incl. the `report` case verbatim, seam-06 selector block, `let alertKey = 0` module counter at store.ts:548).
- Produces (Tasks 7-8 rely on these EXACT names):
  - `ui/src/types.ts`: `WeatherForecast`, `WeatherAstrospheric`, `WeatherAlert`, `WeatherState` (fields exactly as coded below); `AppConfig.weather?: {...}` optional block.
  - `ui/src/api/weather.ts`: `getWeather(): Promise<WeatherState>`, `setIgnoreTonight(ignore: boolean): Promise<WeatherState>`, `saveWeatherConfig(weather: WeatherConfigInput, version: number | null, clearKey?: boolean): Promise<AppConfig>`, `interface WeatherConfigInput`.
  - `ui/src/lib/weather.ts`: `OPEN_METEO_STALE_S = 2700`, `normalizeWeather(raw, nowTs): WeatherState | null`, `breachSpans(cloud, thresholdPct, sustainMinutes): {start,end}[]`, `fmtHm(iso): string`, `agoLabel(fetchedTs, nowTs): string`.
  - `ui/src/store.ts`: `weather: WeatherState | null` slice, `weatherAlertKey: number` (bumps on alert null→non-null edge), `case "weather"` in handleEvent, selectors `useWeather`, `useWeatherAlertKey`.

**Steps**

- [ ] **6.1 Add the types.** Edit `ui/src/types.ts`. Inside `export interface AppConfig` — old_string:

```ts
  deadman_url: string;
  deadman_configured?: boolean;
```

new_string:

```ts
  deadman_url: string;
  deadman_configured?: boolean;
  // --- weather (sub-project C; additive). The Astrospheric key is blanked
  //     server-side via redacted(); astrospheric_configured is the marker the
  //     write-only Settings field reads ("set" / "not set"). ---
  weather?: {
    enabled: boolean;
    cloud_threshold_pct: number;
    sustain_minutes: number;
    astrospheric_api_key: string | null;
    astrospheric_configured?: boolean;
  };
```

Then APPEND at the very end of `ui/src/types.ts`:

```ts

// ===================================================================== weather
// Sub-project C (weather spec §7): the GET /api/weather + WS `weather` event
// payload. ALL weather data is view.site_precise-gated server-side (WS events
// are DROPPED for non-holders), so these only ever populate for holders. NO
// coordinates ride this payload.
export interface WeatherForecast {
  times: string[];        // ISO-8601 Z, 15-min grid, <= 192 samples (48 h)
  cloud: number[];        // TOTAL cloud cover % — the breach metric
  cloud_low: number[];
  cloud_mid: number[];
  cloud_high: number[];
}

export interface WeatherAstrospheric {
  times: string[];        // ISO-8601 Z, hourly (81 h horizon)
  seeing: (number | null)[];
  transparency: (number | null)[];
  fetched_ts: number | null;
  stale: boolean;         // > 12 h old (two 6-hourly model cycles)
  credits_used_today: number | null;  // 5 credits/call on a 100/day Pro budget
}

export interface WeatherAlert {
  kind: "high_cloud";
  start_iso: string;
  end_iso: string;
  peak_pct: number;
  dominant_layer: "low" | "mid" | "high";
}

export interface WeatherState {
  enabled: boolean;
  fetched_ts: number | null;   // Open-Meteo fetch time (unix s)
  stale: boolean;              // client re-derives FAIL-CLOSED (> 45 min)
  ignore_tonight: boolean;
  threshold_pct: number;
  sustain_minutes: number;
  forecast: WeatherForecast | null;
  astrospheric: WeatherAstrospheric | null;
  alert: WeatherAlert | null;
}
```

- [ ] **6.2 Create `ui/src/api/weather.ts`** with EXACTLY this content:

```ts
// api/weather.ts — typed client for the weather surfaces (weather spec §7/§9).
// Thin over the shared `api` fetch wrapper (api.ts): same ApiError throwing.
// Every weather route is view.site_precise-gated server-side — callers gate on
// useCanViewSitePrecise() so a non-holder client never issues a request (§8).
import { api } from "../api";
import type { AppConfig, WeatherState } from "../types";

/** GET /api/weather. view.site_precise. Full spec-§7 payload. */
export const getWeather = (): Promise<WeatherState> =>
  api.get<WeatherState>("/api/weather");

/** POST /api/weather/ignore-tonight {ignore}. control.capture.
 *  409 {code:"no_night"} when no site/night resolves. Returns the updated
 *  weather payload (the server also broadcasts it on the bus). */
export const setIgnoreTonight = (ignore: boolean): Promise<WeatherState> =>
  api.post<WeatherState>("/api/weather/ignore-tonight", { ignore });

/** Save body for POST /api/config/weather (server WeatherSaveBody.weather).
 *  Key contract (deadman_url precedent): null/empty key = KEEP the stored key
 *  (the UI round-trips the masked config); clearKey=true clears it. */
export interface WeatherConfigInput {
  enabled: boolean;
  cloud_threshold_pct: number;
  sustain_minutes: number;
  astrospheric_api_key: string | null;
}

/** POST /api/config/weather {weather, version, clear_astrospheric_key}.
 *  config.site_optics. 409 on a version conflict (reload-and-retoast). */
export const saveWeatherConfig = (
  weather: WeatherConfigInput,
  version: number | null,
  clearKey = false,
): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/weather", {
    weather,
    version,
    clear_astrospheric_key: clearKey,
  });
```

- [ ] **6.3 Create `ui/src/lib/weather.ts`** with EXACTLY this content:

```ts
// lib/weather.ts — pure weather-payload helpers (weather spec §9). No React,
// no DOM: npx-tsx testable (lib/safety.ts precedent).
//
// normalizeWeather derives `stale` FAIL-CLOSED client-side from fetched_ts age
// (> 45 min, the server's Open-Meteo staleness threshold), clamps every
// percentage series to 0-100, and pads missing/short series to the times
// length so the panel never renders NaN. breachSpans is the DISPLAY-ONLY twin
// of the server's consecutive-sample sustained-breach rule (spec §4).

import type { WeatherAstrospheric, WeatherForecast, WeatherState } from "../types";

export const OPEN_METEO_STALE_S = 45 * 60;

const clampPct = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v)
    ? Math.max(0, Math.min(100, v))
    : 0;

function normalizeSeries(a: unknown, n: number): number[] {
  const src = Array.isArray(a) ? (a as unknown[]) : [];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(clampPct(src[i]));
  return out;
}

export function normalizeWeather(
  raw: WeatherState | null | undefined,
  nowTs: number,
): WeatherState | null {
  if (!raw) return null;
  const fetched = typeof raw.fetched_ts === "number" ? raw.fetched_ts : null;
  const stale = fetched === null || nowTs - fetched > OPEN_METEO_STALE_S;
  let forecast: WeatherForecast | null = null;
  const f = raw.forecast;
  if (f && Array.isArray(f.times) && f.times.length > 0) {
    const n = f.times.length;
    forecast = {
      times: f.times.slice(0, n).map(String),
      cloud: normalizeSeries(f.cloud, n),
      cloud_low: normalizeSeries(f.cloud_low, n),
      cloud_mid: normalizeSeries(f.cloud_mid, n),
      cloud_high: normalizeSeries(f.cloud_high, n),
    };
  }
  let astro: WeatherAstrospheric | null = null;
  const a = raw.astrospheric;
  if (a && Array.isArray(a.times) && a.times.length > 0) {
    const fetchedA = typeof a.fetched_ts === "number" ? a.fetched_ts : null;
    astro = {
      times: a.times.map(String),
      seeing: Array.isArray(a.seeing) ? a.seeing : [],
      transparency: Array.isArray(a.transparency) ? a.transparency : [],
      fetched_ts: fetchedA,
      stale: fetchedA === null || nowTs - fetchedA > 12 * 3600,
      credits_used_today:
        typeof a.credits_used_today === "number" ? a.credits_used_today : null,
    };
  }
  return {
    enabled: !!raw.enabled,
    fetched_ts: fetched,
    stale,
    ignore_tonight: !!raw.ignore_tonight,
    threshold_pct: clampPct(raw.threshold_pct),
    sustain_minutes:
      typeof raw.sustain_minutes === "number" && Number.isFinite(raw.sustain_minutes)
        ? raw.sustain_minutes
        : 30,
    forecast,
    astrospheric: astro,
    alert: raw.alert ?? null,
  };
}

/** Display-only sustained-breach spans (the server rule, spec §4): runs of
 *  >= max(1, floor(sustain/15)) CONSECUTIVE samples with cloud >= threshold.
 *  Returns inclusive index ranges into the given series. */
export function breachSpans(
  cloud: number[],
  thresholdPct: number,
  sustainMinutes: number,
): { start: number; end: number }[] {
  const needed = Math.max(1, Math.floor(sustainMinutes / 15));
  const spans: { start: number; end: number }[] = [];
  let runStart = -1;
  for (let i = 0; i <= cloud.length; i++) {
    const breach = i < cloud.length && cloud[i] >= thresholdPct;
    if (breach) {
      if (runStart < 0) runStart = i;
    } else {
      if (runStart >= 0 && i - runStart >= needed) {
        spans.push({ start: runStart, end: i - 1 });
      }
      runStart = -1;
    }
  }
  return spans;
}

/** "HH:MM" (viewer-local time) from an ISO-Z stamp — chart ticks, popup and
 *  chip copy. Garbage-safe: unparseable input renders as "--:--". */
export function fmtHm(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Staleness chip copy: "updated 12 min ago" / "STALE — last fetch 2.0 h ago". */
export function agoLabel(fetchedTs: number | null, nowTs: number): string {
  if (fetchedTs === null) return "STALE — never fetched";
  const ageS = Math.max(0, nowTs - fetchedTs);
  const mins = Math.round(ageS / 60);
  if (ageS > OPEN_METEO_STALE_S) {
    const h = ageS / 3600;
    return `STALE — last fetch ${h >= 1 ? `${h.toFixed(1)} h` : `${mins} min`} ago`;
  }
  return `updated ${mins} min ago`;
}
```

- [ ] **6.4 Write the self-executing tests.** Create `ui/src/lib/__tests__/weather.test.ts` with EXACTLY this content (safety.test.ts harness pattern, seam-42):

```ts
// weather.test.ts — normalizeWeather (stale fail-closed, clamping, missing
// series) + breachSpans golden cases + fmtHm/agoLabel (weather spec §9/§14).
// Run with:  npx tsx src/lib/__tests__/weather.test.ts   (from ui/)

import type { WeatherState } from "../../types";
import {
  agoLabel, breachSpans, fmtHm, normalizeWeather, OPEN_METEO_STALE_S,
} from "../weather";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const NOW = 1_700_000_000;

const base: WeatherState = {
  enabled: true,
  fetched_ts: NOW - 60,
  stale: false,
  ignore_tonight: false,
  threshold_pct: 50,
  sustain_minutes: 30,
  forecast: {
    times: ["2026-07-16T01:00:00Z", "2026-07-16T01:15:00Z"],
    cloud: [10, 120],
    cloud_low: [0, -5],
    cloud_mid: [0, 0],
    cloud_high: [10, 100],
  },
  astrospheric: null,
  alert: null,
};

test("fresh payload stays fresh; percentages clamp 0-100", () => {
  const w = normalizeWeather(base, NOW)!;
  assert(w.stale === false, `stale=${w.stale}`);
  assert(w.forecast!.cloud[1] === 100, `clamp high ${w.forecast!.cloud[1]}`);
  assert(w.forecast!.cloud_low[1] === 0, `clamp low ${w.forecast!.cloud_low[1]}`);
});

test("stale derived FAIL-CLOSED from fetched_ts age (> 45 min)", () => {
  const w = normalizeWeather(
    { ...base, stale: false, fetched_ts: NOW - OPEN_METEO_STALE_S - 1 }, NOW)!;
  assert(w.stale === true, "old fetched_ts must derive stale");
  const w2 = normalizeWeather({ ...base, fetched_ts: null, stale: false }, NOW)!;
  assert(w2.stale === true, "missing fetched_ts must derive stale");
});

test("missing series tolerated (padded to times length with 0)", () => {
  const raw = {
    ...base,
    forecast: { ...base.forecast!, cloud_mid: undefined },
  } as unknown as WeatherState;
  const w = normalizeWeather(raw, NOW)!;
  assert(w.forecast!.cloud_mid.length === 2, "padded to n");
  assert(w.forecast!.cloud_mid[0] === 0, "pad value 0");
});

test("null payload -> null; astrospheric staleness derived (> 12 h)", () => {
  assert(normalizeWeather(null, NOW) === null, "null passthrough");
  const w = normalizeWeather({
    ...base,
    astrospheric: {
      times: ["2026-07-16T00:00:00Z"], seeing: [2], transparency: [21],
      fetched_ts: NOW - 13 * 3600, stale: false, credits_used_today: 20,
    },
  }, NOW)!;
  assert(w.astrospheric!.stale === true, "13 h old astrospheric is stale");
});

test("breachSpans: consecutive-sample rule (sustain 30 -> 2 samples)", () => {
  assert(breachSpans([40, 60, 40, 40], 50, 30).length === 0,
    "single sample is not sustained");
  const spans = breachSpans([40, 60, 70, 40, 80, 90, 95, 10], 50, 30);
  assert(spans.length === 2, `spans=${JSON.stringify(spans)}`);
  assert(spans[0].start === 1 && spans[0].end === 2, "first span 1-2");
  assert(spans[1].start === 4 && spans[1].end === 6, "second span 4-6");
});

test("breachSpans: sustain 15 -> single sample; run reaching array end", () => {
  const spans = breachSpans([0, 0, 99], 50, 15);
  assert(spans.length === 1 && spans[0].start === 2 && spans[0].end === 2,
    "tail run counted");
});

test("fmtHm shape + garbage guard; agoLabel fresh vs stale copy", () => {
  assert(/^\d{2}:\d{2}$/.test(fmtHm("2026-07-16T04:05:00Z")), "hh:mm shape");
  assert(fmtHm("garbage") === "--:--", "garbage guarded");
  assert(agoLabel(NOW - 720, NOW) === "updated 12 min ago", "fresh copy");
  assert(agoLabel(NOW - 2 * 3600, NOW).startsWith("STALE — last fetch 2.0 h"),
    "stale copy");
  assert(agoLabel(null, NOW) === "STALE — never fetched", "never fetched");
});

console.log(`weather.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  process.exit(1);
}
```

- [ ] **6.5 Run the tsx tests — expect PASS** (the lib exists already; this validates it):

```
cd ui && npx tsx src/lib/__tests__/weather.test.ts
```

Expected output: `weather.test: 7 passed, 0 failed`, exit code 0. (If any case fails, fix `lib/weather.ts` — not the test — unless the test contradicts the spec values above.)

- [ ] **6.6 Wire the store.** Edit `ui/src/store.ts`, four edits:

(a) type import — old_string:

```ts
  UpdateStatus,
  ViewName,
  Viewport,
  WsPhase,
} from "./types";
```

new_string:

```ts
  UpdateStatus,
  ViewName,
  Viewport,
  WeatherState,
  WsPhase,
} from "./types";
```

(b) normalizer import — old_string:

```ts
import { normalizeSafety } from "./lib/safety";
```

new_string:

```ts
import { normalizeSafety } from "./lib/safety";
import { normalizeWeather } from "./lib/weather";
```

(c) AppState slice declaration — old_string (seam-02 tail):

```ts
  // lastReportId: id of the most recently finalized SessionReport (a `report`
  // event). The run-complete panel + overflow deep-link to it in the next workflow.
  lastReportId: string | null;
```

new_string:

```ts
  // lastReportId: id of the most recently finalized SessionReport (a `report`
  // event). The run-complete panel + overflow deep-link to it in the next workflow.
  lastReportId: string | null;

  // --- weather (sub-project C §9) ---
  // Latest normalized weather payload (null until a `weather` event or the
  // panel's cold GET lands — non-holders never receive either, spec §8).
  weather: WeatherState | null;
  // Bumped ONLY when `alert` transitions null -> non-null (the `alert` slice
  // key idiom) so the popup effect fires exactly once per server-side latch.
  weatherAlertKey: number;
```

(d) initial state — old_string:

```ts
  safety: null,
  alert: null,
  lastReportId: null,
```

new_string:

```ts
  safety: null,
  alert: null,
  lastReportId: null,
  weather: null,
  weatherAlertKey: 0,
```

(e) handleEvent case — old_string (seam-05, the `report` case verbatim):

```ts
      case "report": {
        // A SessionReport finalized — stash its id so the run-complete panel +
        // overflow can deep-link to ReportView (lands in the next workflow).
        const d = ev.data as unknown as { id: string | null };
        if (d.id) set({ lastReportId: d.id });
        break;
      }
```

new_string:

```ts
      case "report": {
        // A SessionReport finalized — stash its id so the run-complete panel +
        // overflow can deep-link to ReportView (lands in the next workflow).
        const d = ev.data as unknown as { id: string | null };
        if (d.id) set({ lastReportId: d.id });
        break;
      }
      case "weather": {
        // Weather payload (spec §7) — WS push, the panel's cold GET, and the
        // ignore-tonight POST all route through here (single application
        // path). Normalize (stale fail-closed, clamped) and replace; bump
        // weatherAlertKey ONLY on the alert null -> non-null edge so the
        // popup fires once per server-side once-per-night latch (spec §12).
        const raw = ev.data as unknown as WeatherState;
        const nw = normalizeWeather(raw, Date.now() / 1000);
        set((s) => ({
          weather: nw,
          weatherAlertKey:
            nw?.alert && !s.weather?.alert
              ? s.weatherAlertKey + 1
              : s.weatherAlertKey,
        }));
        break;
      }
```

(f) selectors — old_string (seam-06 tail):

```ts
export const useLastReportId = () => useStore((s) => s.lastReportId);
```

new_string:

```ts
export const useLastReportId = () => useStore((s) => s.lastReportId);
export const useWeather = () => useStore((s) => s.weather);
export const useWeatherAlertKey = () => useStore((s) => s.weatherAlertKey);
```

- [ ] **6.7 Type/build gate — expect PASS:**

```
cd ui && npm run build
```

Expected: `tsc -b` clean, vite build succeeds (exit 0).

- [ ] **6.8 Commit:**

```
git add ui/src/types.ts ui/src/api/weather.ts ui/src/lib/weather.ts ui/src/store.ts ui/src/lib/__tests__/weather.test.ts
git commit -m "feat(ui/weather): WeatherState types, api client, pure normalize/breach helpers, store slice + weather event + alert-edge key (spec §9)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 7: SkyConditionsPanel + health strip + warning popup + SessionsPanel chips + Settings WeatherPanel

**Files**
- Create: `ui/src/components/weather/SkyConditionsPanel.tsx`
- Create: `ui/src/components/settings/WeatherPanel.tsx`
- Modify: `ui/src/lib/health.ts` (deriveHealthIssues input + Tier-1 weather rule, lib/health.ts:76-172)
- Modify: `ui/src/views/MonitorView.tsx` (imports; `weather`/`canSeePrecise` hooks; healthIssues input + deps; mosaic mount before the THERMAL cell ~line 524)
- Modify: `ui/src/App.tsx` (popup effect before the auth-splash return ~line 194; imports)
- Modify: `ui/src/components/sequence/SessionsPanel.tsx` (weather chips + ignore toggle beside the no-monitor chip ~line 171-184; imports)
- Modify: `ui/src/components/settings/SettingsView.tsx` (mount WeatherPanel below SitePanel ~line 150; imports)

**Interfaces**
- Consumes: Task 6 (`useWeather`, `useWeatherAlertKey`, `getWeather`, `setIgnoreTonight`, `saveWeatherConfig`, `WeatherConfigInput`, `normalizeWeather` — applied via `handleEvent`, `breachSpans`, `fmtHm`, `agoLabel`, `WeatherState`); UI seams (`.superpowers/sdd/seams/weather-ui.md`): `Panel({title, right, children, className})` + `Toggle({checked, onChange, disabled, label, showState})` + `Field` (components/ui.tsx:14/121; Field per SitePanel usage seam-27), `Icon` names limited to the existing set — use `"alert"`, `"lock"`, `"info"`, `"check"`, `"refresh"` only (icons.tsx:7-12 union has NO "cloud"), `confirmDialog(opts): Promise<boolean>` + `mode:"ok"` acknowledge-only (seam-08), `deriveHealthIssues` (seam-14) and its MonitorView call site (MonitorView.tsx:260-276), HealthStrip Tier-1 amber chips (seam-13), SessionsPanel harness (seam-09/10: `showToast`, `canControl`, dormant-card chip block), SettingsView Connect mount (seam-26), SitePanel idiom (seam-27: `toNum`, sig-keyed reseed, `run`/409 handling, `ApiError` from `"../../api"`), `useCanViewSitePrecise`/`useCanControlCapture`/`useCan` (seam-24), `useConfig` (store.ts:1237), `wsConnected` store field (store.ts:473/636), `api.get` wrapper, GET `/api/site/sky` → `{dark_window: {start_iso, end_iso} | null}` (server seam-10, holder-agnostic ephemeris under view.status).
- Produces:
  - `SkyConditionsPanel` (default export) — self-gating on `weather.enabled`, renders its own `<Panel className="col-span-full lg:col-span-6" title="Sky Conditions">`; the CALLER gates on `useCanViewSitePrecise()`.
  - `WeatherPanel` (default export) — Settings panel; caller gates on `useCanViewSitePrecise()`.
  - `deriveHealthIssues` accepts `weather?: WeatherState | null` and emits the Tier-1 chip `high cloud forecast tonight (peak N%)` when `weather.alert` is non-null.

**Steps**

- [ ] **7.1 Health fold first (pure, tsx-verifiable).** Edit `ui/src/lib/health.ts`: add `WeatherState` to its existing `import type { ... } from "../types"` list. Then — old_string (seam-14):

```ts
export function deriveHealthIssues(input: {
  safety: SafetyState | null;
```

new_string:

```ts
export function deriveHealthIssues(input: {
  safety: SafetyState | null;
  weather?: WeatherState | null;
```

Then — old_string:

```ts
  const {
    safety,
    disk,
```

new_string:

```ts
  const {
    safety,
    weather,
    disk,
```

Then — old_string (seam-14 tail):

```ts
  for (const [cap, label] of [
    ["autofocus", "Autofocus"],
    ["polar_align", "Polar alignment"],
    ["solve", "Plate solving"],
  ] as const) {
    const choice = providers?.[cap];
    if (choice?.kind === "unavailable") {
      issues.push({ tier: 1, icon: "info", text: `${label} unavailable — ${choice.reason}` });
    }
  }

  return issues;
}
```

new_string:

```ts
  for (const [cap, label] of [
    ["autofocus", "Autofocus"],
    ["polar_align", "Polar alignment"],
    ["solve", "Plate solving"],
  ] as const) {
    const choice = providers?.[cap];
    if (choice?.kind === "unavailable") {
      issues.push({ tier: 1, icon: "info", text: `${label} unavailable — ${choice.reason}` });
    }
  }
  // weather (sub-project C §10): ADVISORY — always Tier-1 amber, never Tier-2
  // red (red is for safety/disk/link). "Night looks OK" therefore requires no
  // active cloud alert. Non-holders never carry a weather slice at all.
  if (weather?.alert) {
    issues.push({
      tier: 1,
      icon: "alert",
      text: `high cloud forecast tonight (peak ${weather.alert.peak_pct}%)`,
    });
  }

  return issues;
}
```

(If the seam-14 `for` block's inline formatting differs slightly in the file, anchor the edit on `return issues;\n}` — the weather rule goes immediately before it.)

- [ ] **7.2 Create `ui/src/components/weather/SkyConditionsPanel.tsx`** with EXACTLY this content:

```tsx
// SkyConditionsPanel.tsx — Monitor-mosaic cloud forecast cell (weather spec
// §10). The CALLER mounts this ONLY for view.site_precise holders (conditional
// -mount idiom, SettingsView.tsx:140) — nothing weather-related renders for
// non-holders (spec §8). Holders with weather disabled get an empty state.
// Hand-rolled SVG time-series (GuideGraph/Sparkline precedent): series
// differentiated by dash pattern + stroke width + inline labels, NEVER hue
// alone (night rule, monitor.tsx:266-267).
import { useEffect, useMemo, useState } from "react";
import { useStore, useWeather } from "../../store";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { api } from "../../api";
import { getWeather, setIgnoreTonight } from "../../api/weather";
import { useCanControlCapture } from "../../lib/caps";
import { agoLabel, breachSpans, fmtHm } from "../../lib/weather";
import type { WeatherState } from "../../types";

const W = 480;
const H = 150;
const PAD_L = 30;
const PAD_R = 40;
const PAD_T = 8;
const PAD_B = 16;
const PLOT_W = W - PAD_L - PAD_R;
const PLOT_H = H - PAD_T - PAD_B;
const HORIZON_S = 24 * 3600; // x-domain: now -> now + 24 h

interface Windowed {
  ts: number[]; // epoch seconds, ascending, within [now-450s, now+24h]
  cloud: number[];
  low: number[];
  mid: number[];
  high: number[];
}

function windowSamples(w: WeatherState, nowTs: number): Windowed {
  const out: Windowed = { ts: [], cloud: [], low: [], mid: [], high: [] };
  const f = w.forecast;
  if (!f) return out;
  for (let i = 0; i < f.times.length; i++) {
    const t = Date.parse(f.times[i]) / 1000;
    if (!Number.isFinite(t) || t < nowTs - 450 || t > nowTs + HORIZON_S) continue;
    out.ts.push(t);
    out.cloud.push(f.cloud[i]);
    out.low.push(f.cloud_low[i]);
    out.mid.push(f.cloud_mid[i]);
    out.high.push(f.cloud_high[i]);
  }
  return out;
}

function nearestAstro(
  w: WeatherState,
  nowTs: number,
  key: "seeing" | "transparency",
): number | null {
  const a = w.astrospheric;
  if (!a || a.times.length === 0) return null;
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < a.times.length; i++) {
    const t = Date.parse(a.times[i]) / 1000;
    const d = Math.abs(t - nowTs);
    if (Number.isFinite(t) && d < bestD) {
      bestD = d;
      best = i;
    }
  }
  if (best < 0 || bestD > 2 * 3600) return null;
  const v = a[key][best];
  return typeof v === "number" ? v : null;
}

export default function SkyConditionsPanel() {
  const weather = useWeather();
  const wsConnected = useStore((s) => s.wsConnected);
  const showToast = useStore((s) => s.showToast);
  const canOperate = useCanControlCapture();
  const [busy, setBusy] = useState(false);
  const [darkWindow, setDarkWindow] = useState<
    { start_iso: string; end_iso: string } | null
  >(null);

  // Cold load on mount + on every WS reconnect (spec §9; holders only — the
  // caller already cap-gated this mount). Routed through handleEvent so the
  // WS path stays the single source of truth for how weather state applies.
  useEffect(() => {
    if (!wsConnected) return;
    let gone = false;
    void (async () => {
      try {
        const raw = await getWeather();
        if (!gone) {
          useStore.getState().handleEvent({
            type: "weather",
            data: raw as unknown as Record<string, unknown>,
            ts: Date.now() / 1000,
          });
        }
      } catch {
        /* non-fatal — WS events keep it fresh */
      }
    })();
    return () => {
      gone = true;
    };
  }, [wsConnected]);

  // Tonight's dark band (plan decision 4: /api/site/sky dark_window, the
  // existing display variant under view.status). Non-fatal: no band if absent.
  useEffect(() => {
    let gone = false;
    void (async () => {
      try {
        const sky = await api.get<{
          dark_window: { start_iso: string; end_iso: string } | null;
        }>("/api/site/sky");
        if (!gone) setDarkWindow(sky.dark_window ?? null);
      } catch {
        /* band simply not drawn */
      }
    })();
    return () => {
      gone = true;
    };
  }, []);

  const nowTs = Date.now() / 1000;
  const win = useMemo(
    () => (weather ? windowSamples(weather, nowTs) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [weather],
  );

  const onIgnore = async (v: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const raw = await setIgnoreTonight(v);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      // 409 no_night etc. surface via the parsed ApiError message (B plumbing)
      showToast("error", e instanceof Error ? e.message : "ignore-tonight failed");
    } finally {
      setBusy(false);
    }
  };

  // ---- chart geometry (pure, from the windowed samples) ----
  const chart = useMemo(() => {
    if (!win || win.ts.length < 2 || !weather) return null;
    const t0 = nowTs;
    const sx = (t: number) => PAD_L + ((t - t0) / HORIZON_S) * PLOT_W;
    const sy = (pct: number) => PAD_T + (1 - pct / 100) * PLOT_H;
    const path = (vals: number[]) =>
      win.ts
        .map((t, i) => `${i === 0 ? "M" : "L"}${sx(t).toFixed(1)},${sy(vals[i]).toFixed(1)}`)
        .join(" ");
    // breach spans: the lib/weather.ts helper — SAME sustained rule as the
    // server, display-only (spec §10).
    const spans = breachSpans(win.cloud, weather.threshold_pct, weather.sustain_minutes);
    let dark: { x0: number; x1: number } | null = null;
    if (darkWindow) {
      const ds = Date.parse(darkWindow.start_iso) / 1000;
      const de = Date.parse(darkWindow.end_iso) / 1000;
      if (Number.isFinite(ds) && Number.isFinite(de) && de > t0 && ds < t0 + HORIZON_S) {
        dark = { x0: sx(Math.max(ds, t0)), x1: sx(Math.min(de, t0 + HORIZON_S)) };
      }
    }
    const ticks: { x: number; label: string }[] = [];
    for (let h = 0; h <= 24; h += 6) {
      const t = t0 + h * 3600;
      ticks.push({ x: sx(t), label: fmtHm(new Date(t * 1000).toISOString()) });
    }
    return { sx, sy, path, spans, dark, ticks };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win, weather, darkWindow]);

  if (!weather || !weather.enabled) {
    return (
      <Panel className="col-span-full lg:col-span-6" title="Sky Conditions">
        <p className="text-dim text-xs py-6 text-center">
          Weather is off — enable it in Settings → Connect.
        </p>
      </Panel>
    );
  }

  const alert = weather.alert;
  const seeingNow = nearestAstro(weather, nowTs, "seeing");
  const transNow = nearestAstro(weather, nowTs, "transparency");

  return (
    <Panel className="col-span-full lg:col-span-6" title="Sky Conditions">
      <div className="data-dim flex flex-col gap-2">
        {chart && win ? (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="w-full block"
            role="img"
            aria-label="Cloud cover forecast, next 24 hours"
          >
            {/* tonight's dark window (shaded band, spec §10) */}
            {chart.dark && (
              <rect
                x={chart.dark.x0}
                y={PAD_T}
                width={Math.max(0, chart.dark.x1 - chart.dark.x0)}
                height={PLOT_H}
                fill="var(--text-faint)"
                opacity={0.18}
              />
            )}
            {/* sustained-breach spans */}
            {chart.spans.map((s, i) => (
              <rect
                key={i}
                x={chart.sx(win.ts[s.start])}
                y={PAD_T}
                width={Math.max(1, chart.sx(win.ts[s.end]) - chart.sx(win.ts[s.start]))}
                height={PLOT_H}
                fill="var(--warn)"
                opacity={0.12}
              />
            ))}
            {/* threshold rule */}
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={chart.sy(weather.threshold_pct)}
              y2={chart.sy(weather.threshold_pct)}
              stroke="var(--text-faint)"
              strokeDasharray="2 5"
            />
            {/* "now" cursor */}
            <line x1={PAD_L} x2={PAD_L} y1={PAD_T} y2={PAD_T + PLOT_H} stroke="var(--line-bright)" />
            {/* series: total solid/thick; low dotted; mid dashed; high
                long-dash + thicker. Dash + width + inline label — never hue
                alone (night rule). */}
            <path d={chart.path(win.cloud)} fill="none" stroke="var(--accent)" strokeWidth={1.8} />
            <path d={chart.path(win.low)} fill="none" stroke="var(--text-dim)" strokeWidth={1} strokeDasharray="1 3" />
            <path d={chart.path(win.mid)} fill="none" stroke="var(--text-dim)" strokeWidth={1} strokeDasharray="4 3" />
            <path d={chart.path(win.high)} fill="none" stroke="var(--text-dim)" strokeWidth={1.4} strokeDasharray="8 3" />
            <text x={W - PAD_R + 3} y={chart.sy(win.cloud[win.cloud.length - 1]) + 3} fill="var(--accent)" fontSize={9} fontFamily="IBM Plex Mono">total</text>
            <text x={W - PAD_R + 3} y={chart.sy(win.low[win.low.length - 1]) + 3} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">low</text>
            <text x={W - PAD_R + 3} y={chart.sy(win.mid[win.mid.length - 1]) + 3} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">mid</text>
            <text x={W - PAD_R + 3} y={chart.sy(win.high[win.high.length - 1]) + 3} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">high</text>
            <text x={2} y={PAD_T + 8} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">100%</text>
            <text x={2} y={PAD_T + PLOT_H} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">0%</text>
            {chart.ticks.map((t, i) => (
              <text key={i} x={t.x} y={H - 4} textAnchor="middle" fill="var(--text-faint)" fontSize={8} fontFamily="IBM Plex Mono">
                {t.label}
              </text>
            ))}
          </svg>
        ) : (
          <p className="text-dim text-xs py-6 text-center">
            no forecast yet — first fetch lands within a minute
          </p>
        )}

        {/* chips row (spec §10) */}
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          <span className={weather.stale ? "text-warn" : "text-dim"}>
            {weather.stale && <Icon name="alert" size={11} className="inline mr-1" />}
            {agoLabel(weather.fetched_ts, nowTs)}
          </span>
          {seeingNow !== null && (
            <span className="text-dim border border-line px-1.5 py-0.5">seeing {seeingNow}</span>
          )}
          {transNow !== null && (
            <span className="text-dim border border-line px-1.5 py-0.5">transparency {transNow}</span>
          )}
          {weather.astrospheric?.credits_used_today != null && (
            <span className="text-dim">
              astrospheric {weather.astrospheric.credits_used_today}/100 credits
            </span>
          )}
          {alert && !weather.ignore_tonight && (
            <span className="text-warn inline-flex items-center gap-1">
              <Icon name="alert" size={11} />
              high cloud tonight — auto-resume will hold unless overridden
            </span>
          )}
          {weather.ignore_tonight && (
            <span className="text-warn inline-flex items-center gap-1">
              <Icon name="alert" size={11} />
              weather override active — resume will ignore clouds tonight
            </span>
          )}
        </div>

        {/* ignore-tonight toggle (operator cap; B lock-note idiom otherwise) */}
        <label className="flex items-center justify-between gap-2 text-xs">
          <span className="text-dim">ignore weather tonight</span>
          <Toggle
            checked={weather.ignore_tonight}
            disabled={!canOperate || busy}
            onChange={(v) => void onIgnore(v)}
          />
        </label>
        {!canOperate && (
          <span className="text-[11px] text-dim inline-flex items-center gap-1">
            <Icon name="lock" size={11} />
            operator or admin access needed to override weather
          </span>
        )}
      </div>
    </Panel>
  );
}
```

- [ ] **7.3 Mount in MonitorView + feed the health fold.** Edit `ui/src/views/MonitorView.tsx`, four edits:

(a) caps import — old_string:

```tsx
import { useCanControlCapture } from "../lib/caps";
```

new_string:

```tsx
import { useCanControlCapture, useCanViewSitePrecise } from "../lib/caps";
```

(b) component import — old_string:

```tsx
import { Icon } from "../components/icons";
```

new_string:

```tsx
import { Icon } from "../components/icons";
import SkyConditionsPanel from "../components/weather/SkyConditionsPanel";
```

(c) add `useWeather` to the store import list (`useBackendLinks, useBootConnectFailed, useProviders, useStore` block at MonitorView.tsx:31-35 — insert `useWeather,` before `useStore,`), then hooks + fold input — old_string (MonitorView.tsx:260-276):

```tsx
  const healthIssues = useMemo(
    () =>
      deriveHealthIssues({
        safety,
        disk: status?.disk,
        meridian: status?.meridian,
        ninaLink: status?.nina_link,
        backendLinks,
        bootConnectFailed,
        providers,
        seqState: state,
        endReason: seq.end_reason,
        wsConnected,
      }),
    [safety, status, backendLinks, bootConnectFailed, providers, state, seq.end_reason, wsConnected],
  );
```

new_string:

```tsx
  const weather = useWeather();
  const canSeePrecise = useCanViewSitePrecise();
  const healthIssues = useMemo(
    () =>
      deriveHealthIssues({
        safety,
        weather,
        disk: status?.disk,
        meridian: status?.meridian,
        ninaLink: status?.nina_link,
        backendLinks,
        bootConnectFailed,
        providers,
        seqState: state,
        endReason: seq.end_reason,
        wsConnected,
      }),
    [safety, weather, status, backendLinks, bootConnectFailed, providers, state, seq.end_reason, wsConnected],
  );
```

(d) mosaic mount — old_string (the unique THERMAL cell header, seam-17):

```tsx
        {/* ================================================== THERMAL */}
```

new_string:

```tsx
        {/* ==================================== SKY CONDITIONS (weather spec §10) */}
        {canSeePrecise && <SkyConditionsPanel />}

        {/* ================================================== THERMAL */}
```

- [ ] **7.4 Warning popup in the always-mounted shell.** Edit `ui/src/App.tsx`, three edits:

(a) — old_string:

```tsx
import { ConfirmHost } from "./components/ConfirmDialog";
```

new_string:

```tsx
import { confirmDialog, ConfirmHost } from "./components/ConfirmDialog";
```

(b) — old_string:

```tsx
import { useStore, useBrightness, useAuthMethods, type ViewName } from "./store";
```

new_string:

```tsx
import { useStore, useBrightness, useAuthMethods, useWeatherAlertKey, type ViewName } from "./store";
import { fmtHm } from "./lib/weather";
```

(c) — insert the effect immediately BEFORE the auth-splash return. old_string:

```tsx
  if (authMethods === null && !authGraceElapsed) {
```

new_string:

```tsx
  // High-cloud night warning popup (weather spec §12): fires once per server-
  // side once-per-night latch (weatherAlertKey bumps only on the alert
  // null -> non-null edge, store §9). Acknowledge-only (mode "ok",
  // ConfirmDialog.tsx:140-144) — the user-required hard notice; the
  // ignore-tonight override lives on the Sky Conditions / Sessions cards, not
  // in this dialog. Non-holders never receive weather events (spec §8), so
  // this can never fire for them.
  const weatherAlertKey = useWeatherAlertKey();
  useEffect(() => {
    if (weatherAlertKey === 0) return;
    const w = useStore.getState().weather;
    const a = w?.alert;
    if (!w || !a) return;
    void confirmDialog({
      title: "High cloud forecast tonight",
      body:
        `Forecast peak ${a.peak_pct}% total cloud (${a.dominant_layer} layer ` +
        `dominant) between ${fmtHm(a.start_iso)} and ${fmtHm(a.end_iso)} — ` +
        `at/above your ${w.threshold_pct}% threshold. Auto-resume will hold ` +
        `unless "ignore weather tonight" is set.`,
      tone: "warn",
      mode: "ok",
    });
  }, [weatherAlertKey]);

  if (authMethods === null && !authGraceElapsed) {
```

(Hook-order note: this `useEffect` must sit with App's other hooks, i.e. before ANY conditional `return` — the auth-splash `if` above is the first one; do not move it below.)

- [ ] **7.5 SessionsPanel chips + adjacent override toggle.** Edit `ui/src/components/sequence/SessionsPanel.tsx`, three edits:

(a) imports — old_string:

```tsx
import { useStore } from "../../store";
```

new_string:

```tsx
import { useStore, useWeather } from "../../store";
```

and — old_string:

```tsx
import { useCanControlMount } from "../../lib/caps";
```

new_string:

```tsx
import { useCanControlCapture, useCanControlMount } from "../../lib/caps";
import { setIgnoreTonight } from "../../api/weather";
```

(b) hooks + handler — old_string (SessionsPanel.tsx:33):

```tsx
  const canControl = useCanControlMount();
```

new_string:

```tsx
  const canControl = useCanControlMount();
  const canCapture = useCanControlCapture();
  const weather = useWeather();

  const onIgnoreWeather = async (v: boolean) => {
    try {
      const raw = await setIgnoreTonight(v);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      showToast("error", `Weather override failed: ${(e as Error).message}`);
    }
  };
```

(Note: `showToast` is already destructured above in this component — seam-09.)

(c) chips — old_string (seam-10):

```tsx
              {r.auto_resume && noMonitor && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  auto-resume armed without a safety monitor — rig may start in bad weather
                </span>
              )}
```

new_string:

```tsx
              {r.auto_resume && noMonitor && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  auto-resume armed without a safety monitor — rig may start in bad weather
                </span>
              )}
              {r.auto_resume && weather?.alert && !weather.ignore_tonight && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  high cloud tonight — auto-resume will hold unless overridden
                </span>
              )}
              {r.auto_resume && weather?.ignore_tonight && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  weather override active — resume will ignore clouds tonight
                </span>
              )}
              {r.auto_resume && weather?.alert && (
                <label className="flex items-center justify-between gap-2">
                  <span className="text-dim">ignore weather tonight</span>
                  <Toggle
                    checked={!!weather.ignore_tonight}
                    disabled={!canCapture}
                    onChange={(v) => void onIgnoreWeather(v)}
                  />
                </label>
              )}
```

- [ ] **7.6 Create `ui/src/components/settings/WeatherPanel.tsx`** with EXACTLY this content:

```tsx
// WeatherPanel.tsx — Settings → Connect weather config (weather spec §2/§10).
// SitePanel idiom: drafts seeded from a signature of exactly the fields this
// form edits (never config.version); bounded inputs validated client-side
// (422-safe); the Astrospheric key is WRITE-ONLY — the masked config only ever
// carries astrospheric_configured, never the value. Save = POST
// /api/config/weather with the 409 reload-and-retoast idiom.
import { useEffect, useState, type JSX } from "react";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { saveWeatherConfig } from "../../api/weather";
import { Field, Panel, Toggle } from "../ui";
import { Icon } from "../icons";

// Blank input must read as "not entered", never as 0 (SitePanel toNum rule).
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

export default function WeatherPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  const loadConfig = useStore((s) => s.loadConfig);
  const canEdit = useCan("config.site_optics");

  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("50");
  const [sustain, setSustain] = useState("30");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  const w = config?.weather;
  const keyConfigured = !!w?.astrospheric_configured;
  const sig = w
    ? JSON.stringify([
        w.enabled,
        w.cloud_threshold_pct,
        w.sustain_minutes,
        w.astrospheric_configured ?? false,
      ])
    : null;
  useEffect(() => {
    if (!w) return;
    setEnabled(!!w.enabled);
    setThreshold(String(w.cloud_threshold_pct));
    setSustain(String(w.sustain_minutes));
    setKey(""); // write-only: never seeded from the masked config
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  const validate = (): string | null => {
    const t = toNum(threshold);
    if (!Number.isFinite(t) || t < 0 || t > 100) return "Cloud threshold must be 0-100 %";
    const s = toNum(sustain);
    if (!Number.isFinite(s) || s < 15 || s > 240) return "Sustain must be 15-240 minutes";
    return null;
  };

  const save = async (clearKey: boolean) => {
    if (busy) return;
    const err = validate();
    if (err) {
      showToast("error", err);
      return;
    }
    setBusy(true);
    try {
      await saveWeatherConfig(
        {
          enabled,
          cloud_threshold_pct: Math.round(toNum(threshold)),
          sustain_minutes: Math.round(toNum(sustain)),
          astrospheric_api_key: key.trim() === "" ? null : key.trim(),
        },
        config?.version ?? null,
        clearKey,
      );
      await loadConfig();
      showToast("success", clearKey ? "Astrospheric key cleared" : "Weather settings saved");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await loadConfig();
        showToast("error", "Config changed elsewhere — reloaded, re-apply your edit");
      } else if (e instanceof ApiError && e.status === 403) {
        showToast("error", "config.site_optics required to change weather settings");
      } else {
        showToast("error", e instanceof Error ? e.message : "Could not save weather settings");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Weather">
      <div className="flex flex-col gap-3">
        <p className="text-[12px] text-dim">
          Cloud forecast, radar and the high-cloud night warning. One shared
          threshold drives both the warning and the auto-resume hold. Enabling
          this makes the home server fetch forecasts for the configured site.
        </p>

        <label className="flex items-center justify-between gap-2 text-sm">
          <span className="text-dim">weather enabled</span>
          <Toggle checked={enabled} disabled={!canEdit || busy} onChange={setEnabled} />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Cloud threshold (%)">
            <input
              className="field"
              inputMode="numeric"
              value={threshold}
              disabled={!canEdit}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder="50"
              aria-label="Cloud threshold percent (0-100)"
            />
          </Field>
          <Field label="Sustained for (min)">
            <input
              className="field"
              inputMode="numeric"
              value={sustain}
              disabled={!canEdit}
              onChange={(e) => setSustain(e.target.value)}
              placeholder="30"
              aria-label="Sustain minutes (15-240)"
            />
          </Field>
        </div>

        <Field label={`Astrospheric API key — ${keyConfigured ? "set" : "not set"}`}>
          <div className="flex items-center gap-2">
            <input
              className="field"
              type="password"
              value={key}
              disabled={!canEdit}
              onChange={(e) => setKey(e.target.value)}
              placeholder={keyConfigured ? "(unchanged)" : "optional — Pro membership"}
              aria-label="Astrospheric API key (write-only)"
            />
            {keyConfigured && (
              <button
                type="button"
                className="btn !py-1 text-xs"
                disabled={!canEdit || busy}
                onClick={() => void save(true)}
              >
                Clear key
              </button>
            )}
          </div>
        </Field>
        <p className="text-[11px] text-dim">
          Optional, North-America-only seeing/transparency. Polled every 6 h
          (5 credits per call on a 100/day Pro budget) — never faster.
        </p>

        {!canEdit && (
          <span className="text-[11px] text-dim inline-flex items-center gap-1">
            <Icon name="lock" size={11} />
            config.site_optics needed to change weather settings
          </span>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            className="btn btn-accent"
            disabled={!canEdit || busy}
            onClick={() => void save(false)}
          >
            Save weather settings
          </button>
        </div>
      </div>
    </Panel>
  );
}
```

- [ ] **7.7 Mount WeatherPanel in Settings → Connect.** Edit `ui/src/components/settings/SettingsView.tsx`, three edits:

(a) — old_string:

```tsx
import {
  useCanConfigBackend,
  useCanAdminUsers,
  useCanSystemUpdate,
  useIsViewer,
} from "../../lib/caps";
```

new_string:

```tsx
import {
  useCanConfigBackend,
  useCanAdminUsers,
  useCanSystemUpdate,
  useCanViewSitePrecise,
  useIsViewer,
} from "../../lib/caps";
```

(b) — old_string:

```tsx
import SkyAtlasPanel from "./SkyAtlasPanel";
```

new_string:

```tsx
import SkyAtlasPanel from "./SkyAtlasPanel";
import WeatherPanel from "./WeatherPanel";
```

(c) hook + mount — old_string:

```tsx
  const isViewer = useIsViewer();
```

new_string:

```tsx
  const isViewer = useIsViewer();
  const canSeePrecise = useCanViewSitePrecise();
```

and — old_string (seam-26):

```tsx
            <DriversPanel />
            <SitePanel />
            <SkyAtlasPanel />
```

new_string:

```tsx
            <DriversPanel />
            <SitePanel />
            {canSeePrecise && <WeatherPanel />}
            <SkyAtlasPanel />
```

- [ ] **7.8 Type/build gate — expect PASS:**

```
cd ui && npm run build
```

Expected: `tsc -b` clean, vite build succeeds (exit 0). Also re-run the pure-lib tests (health fold has no tsx test of its own; the build gate types it):

```
cd ui && npx tsx src/lib/__tests__/weather.test.ts
```

Expected: `weather.test: 7 passed, 0 failed`.

- [ ] **7.9 Commit:**

```
git add ui/src/components/weather/SkyConditionsPanel.tsx ui/src/components/settings/WeatherPanel.tsx ui/src/lib/health.ts ui/src/views/MonitorView.tsx ui/src/App.tsx ui/src/components/sequence/SessionsPanel.tsx ui/src/components/settings/SettingsView.tsx
git commit -m "feat(ui/weather): Sky Conditions panel + health-strip amber chip + once-per-latch warning popup + Sessions chips/override + Settings WeatherPanel (spec §10, §12)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 8: lib/mercator.ts + RadarMap + scope-pointing overlay + final gates

**Files**
- Create: `ui/src/lib/mercator.ts`
- Create: `ui/src/lib/__tests__/mercator.test.ts`
- Create: `ui/src/components/weather/RadarMap.tsx`
- Modify: `ui/src/views/MonitorView.tsx` (import + mount beside SkyConditionsPanel)
- Final gates: full UI build + full server suite

**Interfaces**
- Consumes: Task 5 tile route contract (`/api/weather/tile/{layer}/{z}/{x}/{y}.png`, z 3-11, radar TTL 240 s); Task 6 (`useWeather`); mount pointing = `status.mount.alt` / `status.mount.az` (types.ts MountStatus:25-35, seam-33 — numbers, degrees); `useSite()` (store selector, seam-06 — holder carries `latitude`/`longitude`); `u(path)` URL helper (`ui/src/lib/base.ts:13`, TileEngine idiom seam-22); SkyCanvas gesture layer (seam-21: pointer capture drag, native `{passive:false}` wheel trap via refs, keyboard nudge/zoom); night rule: tiles get `filter: var(--img-filter)` (index.css:115, seam-37 — TileEngine has NO night filter of its own; the redshift lives in the CSS var applied at the element level, exactly what RadarMap does directly per spec §11); `Panel` (components/ui.tsx:14); B broken-thumb fallback idiom (hide `<img>` onError).
- Produces:
  - `ui/src/lib/mercator.ts`: `TILE_SIZE = 256`, `MIN_Z = 3`, `MAX_Z = 11`, `EARTH_RADIUS_KM = 6371`, `MERCATOR_MAX_LAT = 85.0511`, `MIN_PIERCE_ALT_DEG = 3`, `MAX_PIERCE_KM = 150`, `CLOUD_DECKS_KM = { low: 2, mid: 5, high: 9 }`, `clampLat(lat)`, `clampZoom(z)`, `lonToTileX(lon, z)`, `latToTileY(lat, z)`, `tileXToLon(x, z)`, `tileYToLat(y, z)`, `destPoint(lat, lon, bearingDeg, distKm): {lat, lon}`, `pierceDistanceKm(altDeg, deckKm): number | null`.
  - `RadarMap` (default export) — self-contained Monitor cell; caller gates on cap + `weather.enabled`.

**Steps**

- [ ] **8.1 Write the failing math tests.** Create `ui/src/lib/__tests__/mercator.test.ts` with EXACTLY this content:

```ts
// mercator.test.ts — tile math round-trips, destPoint golden vectors, pierce
// distances (zenith / 45° / 20° / clamps) — weather spec §11/§14.
// Run with:  npx tsx src/lib/__tests__/mercator.test.ts   (from ui/)

import {
  CLOUD_DECKS_KM, clampLat, clampZoom, destPoint, latToTileY, lonToTileX,
  pierceDistanceKm, tileXToLon, tileYToLat,
} from "../mercator";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg: string): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg}: ${a} !~ ${b} (tol ${tol})`);
}

test("tile x/y round-trips at z=7", () => {
  near(tileXToLon(lonToTileX(-118.1, 7), 7), -118.1, 1e-9, "lon round-trip");
  near(tileYToLat(latToTileY(34.2, 7), 7), 34.2, 1e-9, "lat round-trip");
});

test("known anchor: lon 0 / lat 0 land mid-grid at z=1", () => {
  near(lonToTileX(0, 1), 1, 1e-12, "lon 0 @ z1");
  near(latToTileY(0, 1), 1, 1e-12, "lat 0 @ z1");
});

test("clampZoom (server range 3-11) + mercator clampLat", () => {
  assert(clampZoom(1) === 3 && clampZoom(15) === 11 && clampZoom(7.4) === 7,
    "zoom clamp/round");
  near(clampLat(89), 85.0511, 1e-9, "lat clamp");
  near(clampLat(-89), -85.0511, 1e-9, "lat clamp south");
});

test("destPoint bearing 0/90/180/270 sanity at mid-latitude", () => {
  const p0 = destPoint(34.2, -118.1, 0, 100); // due north
  near(p0.lat, 34.2 + 100 / 111.195, 0.01, "north dlat ~0.9deg/100km");
  near(p0.lon, -118.1, 1e-6, "north lon unchanged");
  const p90 = destPoint(34.2, -118.1, 90, 100); // due east
  near(
    p90.lon,
    -118.1 + 100 / (111.195 * Math.cos((34.2 * Math.PI) / 180)),
    0.02,
    "east dlon scaled by cos(lat)",
  );
  assert(p90.lat < 34.21 && p90.lat > 34.1, "east lat ~unchanged");
  const p180 = destPoint(34.2, -118.1, 180, 100);
  assert(p180.lat < 34.2, "south decreases lat");
  const p270 = destPoint(34.2, -118.1, 270, 100);
  assert(p270.lon < -118.1, "west decreases lon");
});

test("pierce distances: zenith -> ~0; 45deg/9km -> 9; 20deg/9km -> ~24.7", () => {
  const z = pierceDistanceKm(90, CLOUD_DECKS_KM.high);
  assert(z !== null && Math.abs(z) < 1e-9, `zenith ${z}`);
  near(pierceDistanceKm(45, 9)!, 9, 1e-9, "45deg high deck = 9 km");
  near(pierceDistanceKm(20, 9)!, 24.727, 0.01, "20deg high deck ~24.7 km");
});

test("pierce clamps: below 3deg altitude and beyond 150 km hidden", () => {
  assert(pierceDistanceKm(2.9, 2) === null, "below 3deg hidden");
  assert(pierceDistanceKm(3, 9) === null, "high deck at 3deg (~171 km) hidden");
  const low = pierceDistanceKm(3, 2);
  assert(low !== null && low < 150, "low deck at 3deg still visible");
  assert(pierceDistanceKm(Number.NaN, 9) === null, "NaN altitude hidden");
});

console.log(`mercator.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  process.exit(1);
}
```

- [ ] **8.2 Run — expect FAIL** (Cannot find module '../mercator'):

```
cd ui && npx tsx src/lib/__tests__/mercator.test.ts
```

- [ ] **8.3 Create `ui/src/lib/mercator.ts`** with EXACTLY this content:

```ts
// lib/mercator.ts — pure Web-Mercator slippy-tile math + spherical geometry
// for the radar map's scope overlay (weather spec §11). No React, no DOM:
// npx-tsx testable (lib/site.ts precedent).

export const TILE_SIZE = 256;
export const MIN_Z = 3; // server zoom clamp (spec §6)
export const MAX_Z = 11;
export const EARTH_RADIUS_KM = 6371;
/** Web-Mercator latitude limit — beyond this latToTileY diverges. */
export const MERCATOR_MAX_LAT = 85.0511;
/** Pierce markers hide below this altitude (spec §11 clamp). */
export const MIN_PIERCE_ALT_DEG = 3;
/** ...and beyond this downrange distance (spec §11 clamp). */
export const MAX_PIERCE_KM = 150;
/** Cloud-deck heights (km): low=2, mid=5, high=9 (spec §11). */
export const CLOUD_DECKS_KM = { low: 2, mid: 5, high: 9 } as const;

const rad = (d: number): number => (d * Math.PI) / 180;
const deg = (r: number): number => (r * 180) / Math.PI;

export function clampLat(lat: number): number {
  return Math.max(-MERCATOR_MAX_LAT, Math.min(MERCATOR_MAX_LAT, lat));
}

export function clampZoom(z: number): number {
  return Math.max(MIN_Z, Math.min(MAX_Z, Math.round(z)));
}

/** Longitude -> fractional tile-x at zoom z (slippy convention). */
export function lonToTileX(lon: number, z: number): number {
  return ((lon + 180) / 360) * Math.pow(2, z);
}

/** Latitude -> fractional tile-y at zoom z (slippy convention). */
export function latToTileY(lat: number, z: number): number {
  const r = rad(clampLat(lat));
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * Math.pow(2, z);
}

export function tileXToLon(x: number, z: number): number {
  return (x / Math.pow(2, z)) * 360 - 180;
}

export function tileYToLat(y: number, z: number): number {
  const n = Math.PI - (2 * Math.PI * y) / Math.pow(2, z);
  return deg(Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))));
}

/** Spherical destination point: from (lat, lon) travel distKm along the great
 *  circle at compass bearingDeg (0 = north, 90 = east). */
export function destPoint(
  lat: number,
  lon: number,
  bearingDeg: number,
  distKm: number,
): { lat: number; lon: number } {
  const delta = distKm / EARTH_RADIUS_KM;
  const theta = rad(bearingDeg);
  const phi1 = rad(lat);
  const lam1 = rad(lon);
  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(delta) +
      Math.cos(phi1) * Math.sin(delta) * Math.cos(theta),
  );
  const lam2 =
    lam1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2),
    );
  return { lat: deg(phi2), lon: ((deg(lam2) + 540) % 360) - 180 };
}

/** Downrange distance (km) where a sight line at altDeg pierces a cloud deck
 *  deckKm above the site: deck_km / tan(alt) (spec §11). Returns null when the
 *  marker should be hidden: below 3° altitude or beyond 150 km. */
export function pierceDistanceKm(altDeg: number, deckKm: number): number | null {
  if (!Number.isFinite(altDeg) || altDeg < MIN_PIERCE_ALT_DEG || altDeg > 90) {
    return null;
  }
  const d = deckKm / Math.tan(rad(altDeg));
  if (!Number.isFinite(d) || d < 0) return null;
  return d > MAX_PIERCE_KM ? null : d;
}
```

- [ ] **8.4 Run — expect PASS:**

```
cd ui && npx tsx src/lib/__tests__/mercator.test.ts
```

Expected: `mercator.test: 6 passed, 0 failed`, exit 0.

- [ ] **8.5 Create `ui/src/components/weather/RadarMap.tsx`** with EXACTLY this content:

```tsx
// RadarMap.tsx — mini slippy radar/satellite map + scope-pointing overlay
// (weather spec §11). From scratch (no map lib): absolutely-positioned <img>
// grid over the SERVER-SIDE IEM proxy (/api/weather/tile/... — the browser
// NEVER contacts IEM), pure Web-Mercator math from lib/mercator.ts, gestures
// ported from SkyCanvas (drag-pan, native non-passive wheel trap, keyboard).
// Night mode: the tile layer gets filter var(--img-filter) directly, exactly
// as survey imagery is dimmed (index.css:115). Broken tiles hide themselves
// (B thumb-fallback idiom). Controls are word-labeled — never hue alone.
import { useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as RKeyboardEvent,
  PointerEvent as RPointerEvent,
} from "react";
import { useSite, useStore } from "../../store";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { u } from "../../lib/base";
import {
  CLOUD_DECKS_KM,
  clampLat,
  clampZoom,
  destPoint,
  latToTileY,
  lonToTileX,
  pierceDistanceKm,
  TILE_SIZE,
  tileXToLon,
  tileYToLat,
} from "../../lib/mercator";

const MAP_H = 320; // px cell height; width tracks the panel
const RADAR_TTL_S = 240; // matches the server radar TTL (spec §6/§11)

type Layer = "radar" | "satellite";

/** Narrow wedge from (x,y) along a compass bearing (deg; 0 = north = up on a
 *  north-up map), fixed 42 px screen length, ±7° half-angle. */
function wedgePath(x: number, y: number, bearingDeg: number): string {
  const len = 42;
  const tip = (angDeg: number) => {
    const a = ((angDeg - 90) * Math.PI) / 180; // bearing 0 -> screen-up
    return { x: x + len * Math.cos(a), y: y + len * Math.sin(a) };
  };
  const l = tip(bearingDeg - 7);
  const r = tip(bearingDeg + 7);
  return `M${x},${y} L${l.x.toFixed(1)},${l.y.toFixed(1)} L${r.x.toFixed(1)},${r.y.toFixed(1)} Z`;
}

export default function RadarMap() {
  const site = useSite();
  const mount = useStore((s) => s.status?.mount);
  const siteLat = typeof site?.latitude === "number" ? site.latitude : null;
  const siteLon = typeof site?.longitude === "number" ? site.longitude : null;

  const [layer, setLayer] = useState<Layer>("radar");
  const [zoom, setZoom] = useState(7);
  const [center, setCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [bust, setBust] = useState(() => Math.floor(Date.now() / (RADAR_TTL_S * 1000)));
  const [width, setWidth] = useState(0);
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  const boxRef = useRef<HTMLDivElement>(null);

  // Center defaults to the site; the recenter button returns to it (spec §11).
  useEffect(() => {
    if (center === null && siteLat !== null && siteLon !== null) {
      setCenter({ lat: siteLat, lon: siteLon });
    }
  }, [center, siteLat, siteLon]);

  // Track the rendered width (mosaic cells are fluid).
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // ---- pointer drag-pan (SkyCanvas idiom, in the mercator tile plane) ----
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startCenter: { lat: number; lon: number };
  } | null>(null);

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    if (!center) return;
    try {
      boxRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current = { startX: e.clientX, startY: e.clientY, startCenter: center };
  };
  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const n = Math.pow(2, zoom);
    let cx = lonToTileX(d.startCenter.lon, zoom) - (e.clientX - d.startX) / TILE_SIZE;
    let cy = latToTileY(d.startCenter.lat, zoom) - (e.clientY - d.startY) / TILE_SIZE;
    cx = ((cx % n) + n) % n; // wrap the antimeridian
    cy = Math.max(0, Math.min(n, cy)); // clamp at the mercator poles
    setCenter({ lat: clampLat(tileYToLat(cy, zoom)), lon: tileXToLon(cx, zoom) });
  };
  const onPointerUp = (e: RPointerEvent<HTMLDivElement>) => {
    try {
      boxRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current = null;
  };

  // ---- wheel zoom (native, non-passive — the SkyCanvas page-scroll trap:
  // React registers synthetic onWheel PASSIVE, so preventDefault would be
  // ignored and the page would scroll under the map). Refs keep the handler
  // current without re-attaching per zoom change.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault(); // honored: registered with passive: false
      setZoom(clampZoom(zoomRef.current + (e.deltaY > 0 ? -1 : 1)));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  // ---- keyboard nudge/zoom ----
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    if (!center) return;
    const stepPx = 64;
    let dx = 0;
    let dy = 0;
    switch (e.key) {
      case "ArrowLeft": dx = -stepPx; break;
      case "ArrowRight": dx = stepPx; break;
      case "ArrowUp": dy = -stepPx; break;
      case "ArrowDown": dy = stepPx; break;
      case "+":
      case "=":
        setZoom(clampZoom(zoom + 1));
        e.preventDefault();
        return;
      case "-":
        setZoom(clampZoom(zoom - 1));
        e.preventDefault();
        return;
      default:
        return;
    }
    e.preventDefault();
    const n = Math.pow(2, zoom);
    let cx = lonToTileX(center.lon, zoom) + dx / TILE_SIZE;
    let cy = latToTileY(center.lat, zoom) + dy / TILE_SIZE;
    cx = ((cx % n) + n) % n;
    cy = Math.max(0, Math.min(n, cy));
    setCenter({ lat: clampLat(tileYToLat(cy, zoom)), lon: tileXToLon(cx, zoom) });
  };

  // ---- tile grid (ceil(viewport/256)+1 overscan; broken tiles hidden) ----
  const tiles: { key: string; id: string; src: string; left: number; top: number }[] = [];
  if (center && width > 0) {
    const n = Math.pow(2, zoom);
    const cx = lonToTileX(center.lon, zoom);
    const cy = latToTileY(center.lat, zoom);
    const x0 = Math.floor(cx - width / (2 * TILE_SIZE)) - 1;
    const x1 = Math.floor(cx + width / (2 * TILE_SIZE)) + 1;
    const y0 = Math.floor(cy - MAP_H / (2 * TILE_SIZE)) - 1;
    const y1 = Math.floor(cy + MAP_H / (2 * TILE_SIZE)) + 1;
    for (let tx = x0; tx <= x1; tx++) {
      for (let ty = y0; ty <= y1; ty++) {
        if (ty < 0 || ty >= n) continue;
        const wx = ((tx % n) + n) % n;
        const id = `${layer}/${zoom}/${wx}/${ty}?${bust}`;
        if (broken[id]) continue;
        tiles.push({
          key: `${tx}:${ty}:${layer}:${bust}`,
          id,
          // proxy only — cache-busting query rolls with the server TTL
          src: u(`/api/weather/tile/${layer}/${zoom}/${wx}/${ty}.png?t=${bust}`),
          left: Math.round((tx - cx) * TILE_SIZE + width / 2),
          top: Math.round((ty - cy) * TILE_SIZE + MAP_H / 2),
        });
      }
    }
  }

  // ---- overlay projection (px within the box) ----
  const toPx = (lat: number, lon: number): { x: number; y: number } | null => {
    if (!center || width === 0) return null;
    const n = Math.pow(2, zoom);
    let dx = lonToTileX(lon, zoom) - lonToTileX(center.lon, zoom);
    if (dx > n / 2) dx -= n; // shortest wrap
    if (dx < -n / 2) dx += n;
    const dy = latToTileY(clampLat(lat), zoom) - latToTileY(center.lat, zoom);
    return { x: dx * TILE_SIZE + width / 2, y: dy * TILE_SIZE + MAP_H / 2 };
  };

  const hasPointing =
    !!mount && Number.isFinite(mount.alt) && Number.isFinite(mount.az);
  const sitePx = siteLat !== null && siteLon !== null ? toPx(siteLat, siteLon) : null;

  // Sight-line pierce points (spec §11): where the line of sight crosses each
  // cloud deck, at destPoint(site, az, deck_km / tan(alt)). HIGH is the
  // primary marker — the exact radar/satellite pixel that decides whether
  // subs survive; low/mid are dots on the same ray. Ray length IS the
  // inclination readout, physically.
  const pierce: { deck: "low" | "mid" | "high"; x: number; y: number }[] = [];
  if (hasPointing && siteLat !== null && siteLon !== null && mount) {
    for (const deck of ["low", "mid", "high"] as const) {
      const dist = pierceDistanceKm(mount.alt, CLOUD_DECKS_KM[deck]);
      if (dist === null) continue; // clamp: <3° alt or >150 km hidden
      const p = destPoint(siteLat, siteLon, mount.az, dist);
      const px = toPx(p.lat, p.lon);
      if (px) pierce.push({ deck, ...px });
    }
  }
  const farthest = pierce.length > 0 ? pierce[pierce.length - 1] : null;

  return (
    <Panel className="col-span-full lg:col-span-6" title="Radar">
      <div className="data-dim flex flex-col gap-2">
        {/* controls: word-labeled buttons + readout chip (hue-free) */}
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          {(["radar", "satellite"] as const).map((l) => (
            <button
              key={l}
              type="button"
              className={`btn !py-0.5 text-[11px] ${layer === l ? "btn-accent" : ""}`}
              aria-pressed={layer === l}
              onClick={() => {
                setLayer(l);
                setBroken({});
              }}
            >
              {l === "radar" ? "Radar" : "IR satellite"}
            </button>
          ))}
          <button
            type="button"
            className="btn !py-0.5 text-[11px]"
            onClick={() => {
              setBust(Math.floor(Date.now() / (RADAR_TTL_S * 1000)));
              setBroken({});
            }}
          >
            <Icon name="refresh" size={11} className="inline mr-1" />
            refresh
          </button>
          <button
            type="button"
            className="btn !py-0.5 text-[11px]"
            disabled={siteLat === null}
            onClick={() => {
              if (siteLat !== null && siteLon !== null) {
                setCenter({ lat: siteLat, lon: siteLon });
              }
            }}
          >
            recenter
          </button>
          <span className="mono text-dim border border-line px-1.5 py-0.5">
            {hasPointing && mount
              ? `Az ${Math.round(mount.az)}° · Alt ${Math.round(mount.alt)}°`
              : "no mount"}
          </span>
        </div>

        <div
          ref={boxRef}
          className="relative overflow-hidden outline-none border border-line select-none touch-none"
          style={{ height: MAP_H, cursor: "grab" }}
          tabIndex={0}
          role="application"
          aria-label="Radar map around the observing site (drag to pan, wheel or +/- to zoom, arrows to nudge)"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
        >
          {/* tile layer — night-dimmed exactly like survey imagery (spec §11) */}
          <div className="absolute inset-0" style={{ filter: "var(--img-filter)" }} aria-hidden>
            {tiles.map((t) => (
              <img
                key={t.key}
                src={t.src}
                alt=""
                draggable={false}
                width={TILE_SIZE}
                height={TILE_SIZE}
                style={{ position: "absolute", left: t.left, top: t.top, maxWidth: "none" }}
                onError={() => setBroken((b) => ({ ...b, [t.id]: true }))}
              />
            ))}
          </div>

          {/* scope location + orientation overlay (spec §11) */}
          <svg
            className="absolute inset-0 pointer-events-none"
            width={width}
            height={MAP_H}
            aria-hidden
          >
            {sitePx && (
              <g>
                {/* site marker: scope glyph (circle + tripod stem) */}
                <circle cx={sitePx.x} cy={sitePx.y} r={5} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
                <line x1={sitePx.x} y1={sitePx.y + 5} x2={sitePx.x} y2={sitePx.y + 11} stroke="var(--accent)" strokeWidth={1.6} />
                {/* azimuth wedge (map is north-up) */}
                {hasPointing && mount && (
                  <path d={wedgePath(sitePx.x, sitePx.y, mount.az)} fill="var(--accent)" opacity={0.35} />
                )}
                {/* dashed sight-line ray to the farthest visible pierce point */}
                {farthest && (
                  <line
                    x1={sitePx.x}
                    y1={sitePx.y}
                    x2={farthest.x}
                    y2={farthest.y}
                    stroke="var(--warn)"
                    strokeWidth={1}
                    strokeDasharray="4 3"
                  />
                )}
                {pierce.map((p) =>
                  p.deck === "high" ? (
                    <g key={p.deck}>
                      {/* PRIMARY: crosshair on the high deck */}
                      <line x1={p.x - 7} y1={p.y} x2={p.x + 7} y2={p.y} stroke="var(--warn)" strokeWidth={1.4} />
                      <line x1={p.x} y1={p.y - 7} x2={p.x} y2={p.y + 7} stroke="var(--warn)" strokeWidth={1.4} />
                      <circle cx={p.x} cy={p.y} r={4} fill="none" stroke="var(--warn)" strokeWidth={1.4} />
                      <text x={p.x + 9} y={p.y - 6} fill="var(--warn)" fontSize={10} fontFamily="IBM Plex Mono">
                        high cloud
                      </text>
                    </g>
                  ) : (
                    <circle key={p.deck} cx={p.x} cy={p.y} r={2.5} fill="var(--warn)" opacity={0.8} />
                  ),
                )}
              </g>
            )}
          </svg>
        </div>

        <p className="text-[10px] text-dim">
          Radar/satellite: Iowa Environmental Mesonet · radar is ~5 min delayed
        </p>
      </div>
    </Panel>
  );
}
```

- [ ] **8.6 Mount in MonitorView.** Edit `ui/src/views/MonitorView.tsx`, two edits:

(a) — old_string:

```tsx
import SkyConditionsPanel from "../components/weather/SkyConditionsPanel";
```

new_string:

```tsx
import SkyConditionsPanel from "../components/weather/SkyConditionsPanel";
import RadarMap from "../components/weather/RadarMap";
```

(b) — old_string (from Task 7):

```tsx
        {/* ==================================== SKY CONDITIONS (weather spec §10) */}
        {canSeePrecise && <SkyConditionsPanel />}
```

new_string:

```tsx
        {/* ==================================== SKY CONDITIONS (weather spec §10) */}
        {canSeePrecise && <SkyConditionsPanel />}

        {/* ========================================= RADAR MAP (weather spec §11) */}
        {canSeePrecise && weather?.enabled && <RadarMap />}
```

- [ ] **8.7 Final gates — UI build, both tsx test files, FULL server suite:**

```
cd ui && npm run build
cd ui && npx tsx src/lib/__tests__/weather.test.ts
cd ui && npx tsx src/lib/__tests__/mercator.test.ts
cd server && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: build exit 0; `weather.test: 7 passed, 0 failed`; `mercator.test: 6 passed, 0 failed`; full server suite all passing (1071 pre-existing + ~30 new weather tests).

- [ ] **8.8 Commit:**

```
git add ui/src/lib/mercator.ts ui/src/lib/__tests__/mercator.test.ts ui/src/components/weather/RadarMap.tsx ui/src/views/MonitorView.tsx
git commit -m "feat(ui/weather): slippy radar map — mercator math + IEM-proxy img grid + scope pierce-point overlay (spec §11)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Spec coverage map (§2-§12 → tasks; self-review record)

| Spec section | Requirement | Task(s) |
|---|---|---|
| §2 | WeatherConfig fields/defaults/bounds, AppConfig slot, set_weather, POST /api/config/weather, key scrub | T1 |
| §3 | 60 s poller, enabled+site gate within one tick, Open-Meteo 15 min / Astrospheric 6 h cadences, exact endpoints/params/body casing, in-memory cache, failures never cached, 45 min / 12 h staleness, outcome-only logging, publish on refresh + one cleared publish on disable, credits surfaced | T2 |
| §4 | veto_reason (fail-open on stale/missing; consecutive-sample next-hour rule; exact reason string), ResumeArm thin delegation + wiring, ignore-tonight route (control.capture, dusk-keyed, auto-expire, 409 no_night, flag rides payload) | T3 |
| §5 | night warning: _night_dusk/_night_dawn + twilight_deg, sustained rule over [dusk,dawn], once-per-night latch keyed int(dusk), dual emission (payload alert + bus.log warning, no coordinates), alert until dawn, latch reset on night change, dominant layer | T3 |
| §6 | tile proxy: verbatim slugs, cap gate, zoom/x/y 422 clamps, disabled 404 no-store zero-httpx, 240/600 s TTL + private cache headers, atomic writes, disk guard, single-flight, PNG magic, 502 no-store never cached | T5 |
| §7 | route table (all four @declare'd → boot assertion), exact payload shape, nulls when absent, no coordinates | T2 (payload), T3 (ignore route), T4 (GET), T1/T5 (config/tiles) |
| §8 | WS weather DROP on BOTH lanes via _redact_ws_event None, 60 s re-auth downgrade applies unchanged (recheck loop re-resolves principal — no code change needed), REST caps outright, key scrub + T-RBAC-13b tests, log hygiene, non-holder UI never mounts/fetches | T4 (+T1 scrub, T7 UI gates) |
| §9 | WeatherState types, api/weather.ts (getWeather/setIgnoreTonight/saveWeatherConfig w/ 409 idiom), lib/weather.ts normalize (fail-closed stale, clamp, missing series) + breach helper, store slice + weather case + alert-edge key + selectors, cold load on mount/reconnect | T6 (+T7 panel cold-load) |
| §10 | SkyConditionsPanel (cap-gated mount, disabled empty state, SVG chart w/ dash/width/label series + dark band + threshold rule + now cursor + breach shading + Date/Intl ticks), chips row (staleness, seeing/transparency, alert, ignore toggle w/ lock note), health strip Tier-1 amber, Settings WeatherPanel (bounded inputs, write-only key, 409 handling) | T7 |
| §11 | lib/mercator.ts (tile math, destPoint, pierceDistanceKm = deck/tan(alt), decks 2/5/9 km, clamps 3°/150 km), RadarMap img-grid via proxy only, SkyCanvas gestures, zoom clamp 3-11, recenter, layer toggle, refresh w/ TTL-locked cache-bust, attribution + delay note, var(--img-filter) night dim, site marker + az wedge + pierce markers (high primary + label, dashed ray) + Az/Alt chip + no-mount degrade | T8 |
| §12 | popup effect on weatherAlertKey (mode "ok", tone warn, peak/layer/window/threshold copy), persistent chips on Sky Conditions + SessionsPanel with adjacent operator-gated override toggle, override-active chip text flip | T7 |
| §13 | error handling: fail-soft fetch (T2), veto fail-open (T3), tile 502/404/422 (T5), 409 no_night toast via ApiError (T6/T7), 422-bounded config + 409 conflict (T1/T7), STALE chip (T7) | as listed |
| §14 | test matrix: test_weather.py (T1-T3), test_resume_arm.py veto matrix (T3), test_rbac_enforcement.py + test_remote_relay.py (T4), tile tests (T5), weather.test.ts (T6), mercator.test.ts (T8), npm build in every UI task | as listed |
