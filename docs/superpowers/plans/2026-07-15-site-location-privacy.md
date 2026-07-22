# Site Location UI + Strip-Entirely Privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Settings → Site location UI and close the location-privacy surface: strip precise coordinates entirely (not coarsen) for any principal lacking `view.site_precise`, add mount-GPS read-back and a saved-locations library, so sub-project C can build weather/radar on a trustworthy `view.site_precise` gate.

**Architecture:** All redaction stays in the single `redact.py` seam shared by both WS lanes; `_coarsen_latlon` is replaced by `_strip_site` (deletes `{name, latitude, longitude, elevation_m}`, retains `{is_default, horizon_min_deg}`). Two ephemeris geolocators (`place_hint`, `lst_str`) are dropped from `/api/site/sky` for non-holders; `/api/visibility[/order]` gain a `view.status` gate. A new `hub.read_site_from_mount()` powers a `config.site_optics`-gated `GET /api/site/mount-gps`. A new `server/astrodeck/locations.py` `LocationStore` (own JSON file, never in AppConfig) backs four `config.site_optics`-gated routes. The UI adds `lib/site.ts` pure helpers, `api/site.ts` typed wrappers, a `useCanViewSitePrecise` hook, and a `SitePanel.tsx` mounted in the Connect tab.

**Tech Stack:** Python 3 / FastAPI / pydantic (server); React 18 + TypeScript (strict, `tsc -b && vite build`) + zustand (UI). Server tests: pytest + TestClient + in-process monkeypatch fakes. UI tests: self-executing `npx tsx` inline-assert harness.

## Global Constraints

Spec §8 (project-wide, binding on the plan) — verbatim:

- Longitude is stored **signed East-positive**; latitude signed +N. UI collects magnitude + hemisphere and converts at the boundary (`lib/site.ts`). Exact convention per config.py:11-16.
- Strip set is exactly `{name, latitude, longitude, elevation_m}`; retain set is exactly `{is_default, horizon_min_deg}`.
- `view.site_precise` remains admin-only in the role map; `config.site_optics` remains the write cap; no new capabilities are introduced.
- All redaction changes live in `redact.py` (single seam shared by both WS lanes); no redaction logic in route handlers beyond calling the seam. Any FUTURE event or payload that embeds site data must place it at `site` / `config.site` so the seam catches it — a code comment at `_redact_ws_event` states this contract.
- **Precise coordinates must never enter `bus.log`** — `GET /api/logs` (app.py:2687-2690) returns `bus.log_history` to any `view.status` holder, which would bypass redaction entirely. `push_site_to_mount` already logs outcome-only (hub.py:880-882); the new `read_site_from_mount()` and the `/api/site` save path must do the same (log presence/success/failure, never values).
- Night-mode: the panel uses semantic tokens only (`text-warn`/`text-bad`/`.field`/`btn*`); status never encoded by hue alone.
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`.
- Do not touch: the catalog HiPS/tile engine and survey-pack code, `native/`, sessions/quota code shipped in A, weather (C). Exception: adding the auth gate to the `/api/visibility[/order]` routes in `catalog/visibility.py` IS in scope (§2).

Operational constraints (binding):

- Server tests run as `cd server && ./.venv/Scripts/python.exe -m pytest -q` — all 1037+ must pass (currently 1037; no regressions, no skips).
- UI `lib/__tests__/*.test.ts` are **self-executing** and run locally only: `cd ui && npx tsx src/lib/__tests__/<name>.test.ts`. They NEVER run in CI. They must pass before the commit that adds them.
- `cd ui && npm run build` (`tsc -b && vite build`, strict `noUnusedLocals`/`noUnusedParameters`) must be green before ANY UI commit.
- Every commit message ends with the two trailer lines from spec §8 (shown in each task's commit step).

---

### Task 1: redact.py coarsen→strip + rework the coarsen tests

**Files:**
- Modify `server/astrodeck/api/redact.py:41-102` (replace `_coarsen_latlon`/`_SITE_LATLON_KEYS` with `_strip_site`/`_SITE_STRIP_KEYS`; rewrite `_redact_site_for`, `_redact_ws_event`; add future-events contract comment) and `:161-169` (`__all__`), plus the module docstring `:6-17`.
- Modify `server/tests/test_rbac_enforcement.py:361-415` (T-RBAC-13 → strip-asserts).
- Modify `server/tests/test_remote_relay.py:580-619` (seed constants) and `:622-807` (three site tests → strip/full/downgrade).

**Interfaces:**
- Produces `_strip_site(site: dict) -> None` (deletes the four keys in place); `_SITE_STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")`.
- `_redact_site_for(payload, principal) -> dict` and `_redact_ws_event(ev_json, principal) -> dict` keep their exact signatures and call sites; copy-on-write in `_redact_ws_event` preserved.

Steps:

- [ ] Rewrite the site-precision region of `server/astrodeck/api/redact.py`. Replace the block from the module docstring's coarsen paragraph and everything from `_SITE_LATLON_KEYS` through the end of `_redact_ws_event` (lines 41-102), and update `__all__`. The full replacement for lines 6-17 (docstring paragraph) and 41-102:

  Docstring paragraph replacement (lines 6-17, the paragraph beginning "``view.site_precise``"):
  ```python
  ``view.site_precise`` (admin-only; EXCLUDED from viewer/operator) is the
  access-control decision for the observatory's EXACT GPS fix. The serving
  payloads (poll_status / summary / redacted config) are built without a
  principal, so we STRIP at the seam: any principal LACKING the cap has the
  four precise-site keys (name, latitude, longitude, elevation_m) REMOVED
  (absent, not nulled) while is_default/horizon_min_deg are retained (the UI
  needs both and neither reveals location), and a holder gets the full block.
  This is the ONLY place the cap is enforced, so every precise-site surface
  (REST status/summary/config + the WS hello frame and status pushes) must
  route through here.
  ```

  Body replacement (lines 41-102):
  ```python
  # ---------------------------------------------------- site-precision redaction
  # The exact set of precise-site keys removed for a principal lacking
  # view.site_precise (spec §2/§8). is_default + horizon_min_deg are NOT here —
  # they are retained (default-site nudge + alt-limit display; neither is a
  # geolocator).
  _SITE_STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")


  def _strip_site(site: dict) -> None:
      """Delete the four precise-site keys from a site dict IN PLACE (safe: every
      caller hands us a freshly-built/copied dict, never shared/persisted state).
      Keys are made ABSENT, not nulled (spec §2). is_default/horizon_min_deg are
      left untouched."""
      for k in _SITE_STRIP_KEYS:
          site.pop(k, None)


  def _redact_site_for(payload: dict, principal: Principal | None) -> dict:
      """Strip precise site keys in ``payload`` unless ``principal`` holds
      ``view.site_precise``. Handles the top-level ``site`` block AND the
      duplicate copy inside an embedded ``config`` block (summary/hello frame).
      Mutates + returns ``payload`` (which is always a fresh per-call dict)."""
      if principal is not None and principal.has(CAP_VIEW_SITE_PRECISE):
          return payload  # holder: full precision, untouched
      if isinstance(payload, dict):
          site = payload.get("site")
          if isinstance(site, dict):
              _strip_site(site)
          cfg = payload.get("config")
          if isinstance(cfg, dict):
              cfg_site = cfg.get("site")
              if isinstance(cfg_site, dict):
                  _strip_site(cfg_site)
      return payload


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
      if not isinstance(data, dict):
          return ev_json
      new_data: dict | None = None
      site = data.get("site")
      if isinstance(site, dict) and any(k in site for k in _SITE_STRIP_KEYS):
          new_data = dict(data)
          new_site = dict(site)
          _strip_site(new_site)
          new_data["site"] = new_site
      cfg = data.get("config")
      if isinstance(cfg, dict) and isinstance(cfg.get("site"), dict):
          base = new_data if new_data is not None else dict(data)
          new_cfg = dict(cfg)
          new_cfg_site = dict(cfg["site"])
          _strip_site(new_cfg_site)
          new_cfg["site"] = new_cfg_site
          base["config"] = new_cfg
          new_data = base
      if new_data is None:
          return ev_json  # nothing site-bearing in this event
      return {**ev_json, "data": new_data}
  ```

- [ ] Update `__all__` (lines 161-169) — swap the two retired symbols for the two new ones:
  ```python
  __all__ = [
      "WS_AUTH_RECHECK_S",
      "_redact_site_for",
      "_redact_ws_event",
      "_redact_drivers_for",
      "_redact_session_for",
      "_strip_site",
      "_SITE_STRIP_KEYS",
  ]
  ```

- [ ] Rewrite T-RBAC-13 in `server/tests/test_rbac_enforcement.py` (lines 361-415). Replace the entire block (the section header comment, `_PRECISE_LAT`/`_PRECISE_LON`, `_seed_precise_site`, and both `test_site_precise_*` functions) with:
  ```python
  # ==================================== T-RBAC-13 view.site_precise strip-entirely
  # view.site_precise (admin-only; EXCLUDED from viewer/operator) gates the
  # observatory's EXACT GPS fix. A principal lacking it must see the four precise
  # keys (name/latitude/longitude/elevation_m) REMOVED (absent, not nulled) on
  # EVERY precise-site surface (REST status/summary/config + the WS hello frame),
  # while is_default + horizon_min_deg are retained; a holder sees the full block.

  _PRECISE_LAT = 40.123456
  _PRECISE_LON = -74.654321
  _PRECISE_ELEV = 123.4
  _PRECISE_NAME = "Secret Barn"
  _STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")


  def _seed_precise_site(store):
      from astrodeck.config import Site
      store.set_site(Site(name=_PRECISE_NAME, latitude=_PRECISE_LAT,
                          longitude=_PRECISE_LON, elevation_m=_PRECISE_ELEV))


  def _assert_site_stripped(site):
      for k in _STRIP_KEYS:
          assert k not in site, f"{k} must be ABSENT for a non-holder"
      assert "is_default" in site, "is_default must be retained"
      assert "horizon_min_deg" in site, "horizon_min_deg must be retained"


  def test_site_stripped_for_viewer(tmp_path, monkeypatch):
      """A viewer (no view.site_precise) gets the four precise keys REMOVED on
      status, config, summary (both the top-level site block and the duplicate
      copy inside the embedded config) AND the WS hello frame; is_default and
      horizon_min_deg remain."""
      store, app = _make_client(tmp_path, monkeypatch)
      _seed_precise_site(store)
      _install(principal_for_role("viewer"))
      with TestClient(app) as c:
          _assert_site_stripped(c.get("/api/status").json()["site"])
          _assert_site_stripped(c.get("/api/config").json()["site"])
          summ = c.get("/api/summary").json()
          _assert_site_stripped(summ["site"])
          _assert_site_stripped(summ["config"]["site"])
          with c.websocket_connect("/ws") as ws:
              hello = ws.receive_json()
              assert hello["type"] == "hello"
              _assert_site_stripped(hello["data"]["site"])
              _assert_site_stripped(hello["data"]["config"]["site"])


  def test_site_full_for_admin(tmp_path, monkeypatch):
      """An admin holds view.site_precise -> the exact name/lat/lon/elevation
      everywhere, untouched."""
      store, app = _make_client(tmp_path, monkeypatch)
      _seed_precise_site(store)
      _install(principal_for_role("admin"))
      with TestClient(app) as c:
          st = c.get("/api/status").json()["site"]
          assert st["latitude"] == _PRECISE_LAT and st["longitude"] == _PRECISE_LON
          assert st["elevation_m"] == _PRECISE_ELEV and st["name"] == _PRECISE_NAME
          cfg = c.get("/api/config").json()["site"]
          assert cfg["latitude"] == _PRECISE_LAT and cfg["name"] == _PRECISE_NAME
          with c.websocket_connect("/ws") as ws:
              hello = ws.receive_json()
              assert hello["data"]["site"]["latitude"] == _PRECISE_LAT
              assert hello["data"]["config"]["site"]["latitude"] == _PRECISE_LAT
  ```

- [ ] Rework the seed constants in `server/tests/test_remote_relay.py` (lines 580-586). Replace `_PRECISE_LAT`/`_PRECISE_LON`/`_seed_precise_site` with the strip-aware seed (adds elevation + name so the strip is observable):
  ```python
  _PRECISE_LAT = 40.123456
  _PRECISE_LON = -74.654321
  _PRECISE_ELEV = 123.4
  _PRECISE_NAME = "Secret Barn"
  _STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")


  def _seed_precise_site(store):
      from astrodeck.config import Site
      store.set_site(Site(name=_PRECISE_NAME, latitude=_PRECISE_LAT,
                          longitude=_PRECISE_LON, elevation_m=_PRECISE_ELEV))
  ```

- [ ] Rewrite the three relay site tests in `server/tests/test_remote_relay.py`. Replace `test_tunneled_ws_coarsens_site_for_viewer` (622-659), `test_tunneled_ws_precise_site_for_holder` (662-694), and `test_tunneled_ws_downgrade_midstream_coarsens` (765-807) with:
  ```python
  def test_tunneled_ws_strips_site_for_viewer(tmp_path, monkeypatch):
      """A viewer LACKING view.site_precise: the hello AND every streamed status
      event have the four precise site keys REMOVED (the precise fix never leaks
      over the relay to a viewer that lost/never had the cap)."""
      store, app = _make_client(tmp_path, monkeypatch)
      _seed_precise_site(store)
      set_active_provider(_FixedPrincipalProvider(principal_for_role("viewer")))

      async def _scenario():
          channel = FakeChannel()
          client = _make_relay_client(app, channel)
          channel.push_frame(FrameType.WS_OPEN, 7,
                             {"path": "/ws", "query": "", "ws_id": "wsA"})
          task = asyncio.create_task(client._serve_once(client._config()))
          await asyncio.sleep(0.05)  # authorize + hello + enter loop
          from astrodeck.events import bus
          bus.publish("status",
                      site={"name": _PRECISE_NAME, "latitude": _PRECISE_LAT,
                            "longitude": _PRECISE_LON, "elevation_m": _PRECISE_ELEV,
                            "is_default": False, "horizon_min_deg": 15.0})
          await asyncio.sleep(0.05)
          channel.finish()
          await asyncio.wait_for(task, timeout=5.0)

          payloads = await _ws_data_payloads(channel)
          hello = payloads[0]
          assert hello["type"] == "hello"
          for k in _STRIP_KEYS:
              assert k not in hello["data"]["site"]
              assert k not in hello["data"]["config"]["site"]
          assert "horizon_min_deg" in hello["data"]["site"]
          status = [p for p in payloads if p["type"] == "status"]
          assert status, "expected a status event"
          for k in _STRIP_KEYS:
              assert k not in status[0]["data"]["site"]
          assert "horizon_min_deg" in status[0]["data"]["site"]

      asyncio.run(_scenario())


  def test_tunneled_ws_precise_site_for_holder(tmp_path, monkeypatch):
      """A viewer HOLDING view.site_precise: the hello AND every streamed event
      carry FULL-precision site coords, byte-for-byte (no strip)."""
      store, app = _make_client(tmp_path, monkeypatch)
      _seed_precise_site(store)
      holder = Principal(role="viewer", email=None,
                         caps=frozenset({CAP_VIEW_STATUS, CAP_VIEW_SITE_PRECISE}),
                         jti=None)
      set_active_provider(_FixedPrincipalProvider(holder))

      async def _scenario():
          channel = FakeChannel()
          client = _make_relay_client(app, channel)
          channel.push_frame(FrameType.WS_OPEN, 7,
                             {"path": "/ws", "query": "", "ws_id": "wsB"})
          task = asyncio.create_task(client._serve_once(client._config()))
          await asyncio.sleep(0.05)
          from astrodeck.events import bus
          bus.publish("status",
                      site={"name": _PRECISE_NAME, "latitude": _PRECISE_LAT,
                            "longitude": _PRECISE_LON, "elevation_m": _PRECISE_ELEV,
                            "is_default": False, "horizon_min_deg": 15.0})
          await asyncio.sleep(0.05)
          channel.finish()
          await asyncio.wait_for(task, timeout=5.0)

          payloads = await _ws_data_payloads(channel)
          hello = payloads[0]
          assert hello["data"]["site"]["latitude"] == _PRECISE_LAT
          assert hello["data"]["site"]["name"] == _PRECISE_NAME
          assert hello["data"]["config"]["site"]["latitude"] == _PRECISE_LAT
          status = [p for p in payloads if p["type"] == "status"]
          assert status and status[0]["data"]["site"]["latitude"] == _PRECISE_LAT
          assert status[0]["data"]["site"]["longitude"] == _PRECISE_LON

      asyncio.run(_scenario())


  def test_tunneled_ws_downgrade_midstream_strips(tmp_path, monkeypatch):
      """A viewer that stays valid (keeps view.status) but LOSES view.site_precise
      mid-stream: events AFTER the downgrade recheck are stripped, even though
      earlier events were full-precision -- redaction tracks the refreshed caps."""
      monkeypatch.setattr(redact_module, "WS_AUTH_RECHECK_S", 0.05)
      store, app = _make_client(tmp_path, monkeypatch)
      _seed_precise_site(store)
      holder = Principal(role="viewer", email=None,
                         caps=frozenset({CAP_VIEW_STATUS, CAP_VIEW_SITE_PRECISE}),
                         jti=None)
      prov = _FixedPrincipalProvider(holder)
      set_active_provider(prov)

      async def _scenario():
          from astrodeck.events import bus
          channel = FakeChannel()
          client = _make_relay_client(app, channel)
          channel.push_frame(FrameType.WS_OPEN, 7,
                             {"path": "/ws", "query": "", "ws_id": "wsE"})
          task = asyncio.create_task(client._serve_once(client._config()))
          await asyncio.sleep(0.03)  # authorize + hello + enter loop
          # event BEFORE downgrade -> full precision
          bus.publish("status",
                      site={"name": _PRECISE_NAME, "latitude": _PRECISE_LAT,
                            "longitude": _PRECISE_LON, "elevation_m": _PRECISE_ELEV,
                            "is_default": False, "horizon_min_deg": 15.0})
          await asyncio.sleep(0.03)
          # downgrade: drop view.site_precise but keep view.status (still allowed)
          prov.principal = principal_for_role("viewer")
          await asyncio.sleep(0.15)  # let >=1 recheck refresh the cached principal
          # event AFTER downgrade -> stripped
          bus.publish("status",
                      site={"name": _PRECISE_NAME, "latitude": _PRECISE_LAT,
                            "longitude": _PRECISE_LON, "elevation_m": _PRECISE_ELEV,
                            "is_default": False, "horizon_min_deg": 15.0})
          await asyncio.sleep(0.05)
          channel.finish()
          await asyncio.wait_for(task, timeout=5.0)

          payloads = await _ws_data_payloads(channel)
          status = [p for p in payloads if p["type"] == "status"]
          assert len(status) >= 2, "expected a pre- and post-downgrade status event"
          assert status[0]["data"]["site"]["latitude"] == _PRECISE_LAT   # before
          assert "latitude" not in status[-1]["data"]["site"]            # after

      asyncio.run(_scenario())
  ```

- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q test_rbac_enforcement.py test_remote_relay.py`. Expected: all pass (the two reworked T-RBAC-13 tests + the three reworked relay tests, plus the untouched tests in both files). If the harness auto-backgrounds the command, re-run it immediately in the foreground.

- [ ] Run the full suite `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Expected: 1037+ passed, 0 failed (net test count unchanged — the two site-precision tests and three relay tests were renamed/rewritten 1:1).

- [ ] Commit:
  ```
  git add server/astrodeck/api/redact.py server/tests/test_rbac_enforcement.py server/tests/test_remote_relay.py
  ```
  ```
  feat(api/redact): strip precise site entirely instead of coarsening

  Replace _coarsen_latlon (round lat/lon to 0.1 deg) with _strip_site,
  which DELETES {name, latitude, longitude, elevation_m} (keys absent, not
  nulled) for any principal lacking view.site_precise, retaining only
  {is_default, horizon_min_deg}. _redact_site_for / _redact_ws_event keep
  their signatures, call sites, and copy-on-write behavior; a future-events
  contract comment pins site data to data.site / data.config.site so the
  seam stays the single enforcement point. Elevation and site name (which
  routinely embeds an address) were leaking unredacted; they are now
  stripped. Reworks T-RBAC-13 and the three relay site tests to strip-asserts.

  BREAKING: third-party scripts polling /api/status or /api/summary with a
  NON-admin token no longer receive latitude/longitude (previously coarsened
  to ~0.1 deg); they also lose name/elevation_m. This is the intended
  privacy outcome — grant view.site_precise to a custom role to restore
  precise coordinates for a trusted automation.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 2: `/api/site/sky` geolocator strip + `/api/visibility[/order]` auth gates

**Files:**
- Modify `server/astrodeck/api/app.py:1386-1404` (`GET /api/site/sky`: capture principal, omit `place_hint`/`lst_str` for non-holders) and `:2912-2934` (remove `/api/visibility/order` from the boot-assertion exemption).
- Modify `server/astrodeck/catalog/visibility.py:32-50` (imports) and `:508-569` (add `require`+`@declare` to both routes).
- Modify `server/tests/test_rbac_enforcement.py` (append site/sky + visibility tests).

**Interfaces:**
- Consumes `require(CAP_VIEW_STATUS)`, `declare`, `CAP_VIEW_STATUS`, `Depends`.
- `GET /api/site/sky` response for holders: `{sun_alt_deg, dark_window, place_hint, lst_str}`; for non-holders: `{sun_alt_deg, dark_window}`.
- `/api/visibility` (GET) and `/api/visibility/order` (POST) now require `view.status`.

Steps:

- [ ] Replace `GET /api/site/sky` in `server/astrodeck/api/app.py` (lines 1386-1404) with a principal-aware version that omits the two geolocators for non-holders. Import `CAP_VIEW_SITE_PRECISE` — it is NOT currently in app.py's `from ..auth import (...)` block (lines 717-725); add it to that import list first (append `CAP_VIEW_SITE_PRECISE` next to `CAP_VIEW_STATUS`). Then:
  ```python
      @app.get("/api/site/sky")
      @declare(CAP_VIEW_STATUS)
      async def site_sky(lat: float | None = None, lon: float | None = None,
                         principal: Principal = Depends(require(CAP_VIEW_STATUS))):
          from ..catalog import coords
          s = config_store.cfg().site
          latitude = s.latitude if lat is None else lat
          longitude = s.longitude if lon is None else lon
          sun = coords.sun_altaz(latitude, longitude)
          sun_alt = sun[0] if isinstance(sun, (tuple, list)) else float(sun)
          window = coords.dark_window(latitude, longitude)
          out = {
              "sun_alt_deg": round(sun_alt, 1),
              "dark_window": window,
          }
          # place_hint (names the region) and lst_str (LST == longitude) are direct
          # geolocators; a non-holder keeps the ephemeris (sun alt + dark window)
          # but not these two (spec §2). Holder gets everything.
          if principal.has(CAP_VIEW_SITE_PRECISE):
              place_fn = getattr(coords, "place_hint", None)
              hint = place_fn(latitude, longitude) if callable(place_fn) \
                  else _place_hint(latitude, longitude)
              out["place_hint"] = hint
              out["lst_str"] = coords.format_ra(coords.lst_hours(longitude))
          return out
  ```
  (Note: the original used `dependencies=[Depends(require(CAP_VIEW_STATUS))]` in the decorator; this version captures the principal as a parameter instead, so the `dependencies=[...]` list is dropped from `@app.get`.)

- [ ] In `server/astrodeck/catalog/visibility.py`, add the gate imports after the existing `from ..hub import hub` (near line 49). Add:
  ```python
  from fastapi import Depends
  from ..auth import CAP_VIEW_STATUS, require
  from ..auth.rbac import declare
  ```
  (The file already imports `from fastapi import APIRouter, Query` — add `Depends` there or as the separate line above; either compiles. Prefer extending the existing line to `from fastapi import APIRouter, Depends, Query`.)

- [ ] Gate the two routes in `server/astrodeck/catalog/visibility.py`. Change the `@router.get("/api/visibility")` decorator (line 510) to carry the dependency, and add `@declare` under it:
  ```python
  @router.get("/api/visibility", dependencies=[Depends(require(CAP_VIEW_STATUS))])
  @declare(CAP_VIEW_STATUS)
  async def get_visibility(
      ra: float = Query(..., ge=0, lt=24),
      dec: float = Query(..., ge=-90, le=90),
      date: str | None = None,
      step_min: int = Query(DEFAULT_STEP_MIN, ge=1, le=240),
      alt_limit: float = Query(DEFAULT_ALT_LIMIT, ge=-90, le=90),
  ):
  ```
  And the `@router.post("/api/visibility/order")` decorator (line 532):
  ```python
  @router.post("/api/visibility/order",
               dependencies=[Depends(require(CAP_VIEW_STATUS))])
  @declare(CAP_VIEW_STATUS)
  async def post_order(body: OrderBody):
  ```
  (Response bodies are unchanged — ephemeris is kept for all `view.status` holders by decision. The handler signatures and bodies stay exactly as-is otherwise.)

- [ ] Remove `/api/visibility/order` from the boot-assertion exemption in `server/astrodeck/api/app.py` (lines 2985-2989 in the seam extract — the `assert_route_capabilities(...)` call). Change the `exempt_paths` set so only the SPA catch-all and the still-exempt `/api/framing/mosaic` remain:
  ```python
      assert_route_capabilities(
          app,
          exempt_paths={"/{path:path}", "/api/framing/mosaic"},
          exempt_prefixes=("/assets", "/auth"))
  ```
  (`/api/visibility` GET was never in this set — the boot assertion only requires *mutating* methods to declare a cap, so the GET always passed; only the POST `/api/visibility/order` was exempt, and it is now removed because it declares `view.status`.)

- [ ] Append the site/sky + visibility tests to `server/tests/test_rbac_enforcement.py` (after the T-RBAC-13 block from Task 1):
  ```python
  # ============================== site/sky geolocator strip + visibility gating

  def test_site_sky_strips_geolocators_for_viewer(tmp_path, monkeypatch):
      """A viewer lacks view.site_precise -> /api/site/sky omits place_hint and
      lst_str but keeps sun_alt_deg + dark_window (ephemeris kept by decision)."""
      store, app = _make_client(tmp_path, monkeypatch)
      _install(principal_for_role("viewer"))
      with TestClient(app) as c:
          r = c.get("/api/site/sky").json()
          assert "place_hint" not in r and "lst_str" not in r
          assert "sun_alt_deg" in r and "dark_window" in r


  def test_site_sky_full_for_admin(tmp_path, monkeypatch):
      """An admin holds view.site_precise -> all four fields present."""
      store, app = _make_client(tmp_path, monkeypatch)
      _install(principal_for_role("admin"))
      with TestClient(app) as c:
          r = c.get("/api/site/sky").json()
          assert "place_hint" in r and "lst_str" in r
          assert "sun_alt_deg" in r and "dark_window" in r


  def test_visibility_fail_closed_for_unauthenticated(tmp_path, monkeypatch):
      """Both visibility routes now require view.status; an unauthenticated caller
      (provider resolves None) is refused fail-closed (401/403), not served."""
      store, app = _make_client(tmp_path, monkeypatch)
      _install(None)
      with TestClient(app) as c:
          assert c.get("/api/visibility?ra=5&dec=10").status_code in (401, 403)
          assert c.post("/api/visibility/order",
                        json={"targets": []}).status_code in (401, 403)


  def test_visibility_allows_viewer(tmp_path, monkeypatch):
      """A viewer (view.status) gets 200 from the visibility ephemeris."""
      store, app = _make_client(tmp_path, monkeypatch)
      _install(principal_for_role("viewer"))
      with TestClient(app) as c:
          assert c.get("/api/visibility?ra=5&dec=10").status_code == 200
          assert c.post("/api/visibility/order",
                        json={"targets": []}).status_code == 200


  def test_boot_assertion_passes_with_visibility_gated(tmp_path, monkeypatch):
      """create_app() runs assert_route_capabilities LAST; with /api/visibility/order
      removed from the exemption it must still build because the route now declares
      view.status (a real capability)."""
      store, app = _make_client(tmp_path, monkeypatch)
      assert app is not None
  ```

- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Expected: 1037+ prior tests still pass, plus 5 new tests pass (1042+ total). If auto-backgrounded, re-run in the foreground.

- [ ] Commit:
  ```
  git add server/astrodeck/api/app.py server/astrodeck/catalog/visibility.py server/tests/test_rbac_enforcement.py
  ```
  ```
  feat(api): strip site/sky geolocators + gate /api/visibility on view.status

  GET /api/site/sky now omits place_hint (region name) and lst_str (LST ==
  longitude) for principals lacking view.site_precise, keeping the ephemeris
  (sun_alt_deg + dark_window) per the keep-ephemeris decision. GET
  /api/visibility and POST /api/visibility/order gain the standard
  require(view.status) + @declare gate (previously ungated); /api/visibility/order
  is removed from the boot-RBAC exemption now that it declares a capability.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 3: mount GPS read-back — `hub.read_site_from_mount()` + `GET /api/site/mount-gps`

**Files:**
- Modify `server/astrodeck/hub.py` (add `read_site_from_mount` next to `push_site_to_mount`, after line 882).
- Modify `server/astrodeck/api/app.py` (add `GET /api/site/mount-gps` after the `post_site` alias, ~line 1363).
- Modify `server/tests/test_rbac_enforcement.py` (append mount-gps tests).

**Interfaces:**
- Produces `hub.read_site_from_mount() -> dict` — always returns `{available: bool, latitude?, longitude?, elevation_m?, detail?}`; never raises; never logs coordinate values.
- `GET /api/site/mount-gps` gated `require(CAP_CONFIG_SITE_OPTICS)` + `@declare(CAP_CONFIG_SITE_OPTICS)`; always 200.

Steps:

- [ ] Add `read_site_from_mount` to `server/astrodeck/hub.py` immediately after `push_site_to_mount` (after line 882). Full method:
  ```python
      async def read_site_from_mount(self) -> dict:
          """Best-effort READ-BACK of the mount's GPS fix (site lat/lon/elevation)
          from a connected Alpaca telescope — the first read on the site<->mount
          channel (push_site_to_mount is push-only). ASSIST ONLY: the result fills
          the Settings form draft; nothing is persisted here.

          Always returns a dict; NEVER raises. ``available: false`` with a human
          ``detail`` when: no mount connected, the mount is not Alpaca-backed (no
          ``_get``), any property read fails, the mount reports exactly (0.0, 0.0)
          (unset-GPS sentinel), or any value is non-finite (NaN/inf) or out of the
          Site model's ranges (lat +-90, lon +-180, elevation -430..9000 — some
          mounts return junk like 99.0/181.0 when unset). Logs OUTCOME ONLY, never
          coordinate values (spec §8)."""
          import math
          tel = self.devices.get("telescope")
          if not (tel and tel.connected):
              return {"available": False, "detail": "No mount connected"}
          get = getattr(tel, "_get", None)
          if get is None:
              return {"available": False,
                      "detail": "Mount does not support GPS read-back"}
          try:
              lat = float(await get("sitelatitude"))
              lon = float(await get("sitelongitude"))
              elev = float(await get("siteelevation"))
          except Exception:
              bus.log("warning", "could not read site from mount", "config")
              return {"available": False,
                      "detail": "Could not read GPS from mount"}
          if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(elev)):
              return {"available": False,
                      "detail": "Mount returned invalid (non-finite) coordinates"}
          if lat == 0.0 and lon == 0.0:
              return {"available": False,
                      "detail": "Mount reports 0,0 — GPS likely unset"}
          if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
                  and -430.0 <= elev <= 9000.0):
              return {"available": False,
                      "detail": "Mount returned out-of-range coordinates"}
          bus.log("info", "read observing site from mount", "config")
          return {"available": True, "latitude": lat, "longitude": lon,
                  "elevation_m": elev}
  ```

- [ ] Add `GET /api/site/mount-gps` to `server/astrodeck/api/app.py` right after the `post_site` alias (after line 1363). `CAP_CONFIG_SITE_OPTICS`, `require`, `declare`, `Principal`, `Depends` are already imported. Full route:
  ```python
      @app.get("/api/site/mount-gps")
      @declare(CAP_CONFIG_SITE_OPTICS)
      async def site_mount_gps(
              principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
          """Best-effort read-back of the connected mount's GPS fix. config.site_optics
          (the cap that may WRITE the site). Always 200; the body's ``available``
          flag + ``detail`` carry unavailability. ASSIST ONLY — the UI fills the
          draft; the user saves explicitly via PUT /api/site."""
          read = getattr(hub, "read_site_from_mount", None)
          if not callable(read):
              return {"available": False,
                      "detail": "Mount GPS read-back unavailable"}
          return await read()
  ```

- [ ] Append the mount-gps tests to `server/tests/test_rbac_enforcement.py`:
  ```python
  # ================================================ /api/site/mount-gps read-back

  class _FakeTel:
      """A minimal connected Alpaca-like telescope exposing async _get for the
      three site properties (mirrors _AlpacaDevice._get)."""

      def __init__(self, lat, lon, elev):
          self.connected = True
          self._vals = {"sitelatitude": lat, "sitelongitude": lon,
                        "siteelevation": elev}

      async def _get(self, method):
          return self._vals[method]


  def test_mount_gps_viewer_forbidden(tmp_path, monkeypatch):
      """A viewer lacks config.site_optics -> 403 (the read-back exposes precise
      coordinates)."""
      store, app = _make_client(tmp_path, monkeypatch)
      _install(principal_for_role("viewer"))
      with TestClient(app) as c:
          assert c.get("/api/site/mount-gps").status_code == 403


  def test_mount_gps_no_mount(tmp_path, monkeypatch):
      """config.site_optics holder, no mount connected -> 200 {available: false}."""
      import astrodeck.hub as hub_mod
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      store, app = _make_client(tmp_path, monkeypatch)
      monkeypatch.setattr(hub_mod.hub, "devices", {})
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          r = c.get("/api/site/mount-gps")
          assert r.status_code == 200 and r.json()["available"] is False


  def test_mount_gps_reports_coords(tmp_path, monkeypatch):
      """A fake mount reporting valid coords -> the values are echoed."""
      import astrodeck.hub as hub_mod
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      store, app = _make_client(tmp_path, monkeypatch)
      monkeypatch.setattr(hub_mod.hub, "devices",
                          {"telescope": _FakeTel(40.5, -74.5, 30.0)})
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          r = c.get("/api/site/mount-gps").json()
          assert r["available"] is True
          assert r["latitude"] == 40.5 and r["longitude"] == -74.5
          assert r["elevation_m"] == 30.0


  def test_mount_gps_zero_zero_is_unset(tmp_path, monkeypatch):
      """Exactly (0.0, 0.0) is the GPS-unset sentinel -> available: false."""
      import astrodeck.hub as hub_mod
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      store, app = _make_client(tmp_path, monkeypatch)
      monkeypatch.setattr(hub_mod.hub, "devices",
                          {"telescope": _FakeTel(0.0, 0.0, 0.0)})
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          r = c.get("/api/site/mount-gps").json()
          assert r["available"] is False and "GPS" in r["detail"]


  def test_mount_gps_out_of_range_rejected(tmp_path, monkeypatch):
      """Junk sentinels (99.0/181.0) are out of the Site ranges -> available: false."""
      import astrodeck.hub as hub_mod
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      store, app = _make_client(tmp_path, monkeypatch)
      monkeypatch.setattr(hub_mod.hub, "devices",
                          {"telescope": _FakeTel(99.0, 181.0, 0.0)})
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          r = c.get("/api/site/mount-gps").json()
          assert r["available"] is False
  ```

- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Expected: prior tests still pass plus 5 new mount-gps tests pass. If auto-backgrounded, re-run in the foreground.

- [ ] Commit:
  ```
  git add server/astrodeck/hub.py server/astrodeck/api/app.py server/tests/test_rbac_enforcement.py
  ```
  ```
  feat(api/hub): mount GPS read-back — GET /api/site/mount-gps

  Add hub.read_site_from_mount() (best-effort _get of sitelatitude/
  sitelongitude/siteelevation on a connected Alpaca mount — the first
  read-back on the previously push-only site<->mount channel) and a
  config.site_optics-gated GET /api/site/mount-gps that always 200s with
  {available, latitude?, longitude?, elevation_m?, detail?}. available:false
  for no mount, no _get, read failure, exact (0,0), non-finite, or
  out-of-Site-range values. Logs outcome-only, never coordinate values (§8).
  Assist only — never persisted; the UI fills the draft and the user saves
  via PUT /api/site.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 4: saved locations library — `LocationStore` + four `/api/locations` routes

**Files:**
- Create `server/astrodeck/locations.py` (`SavedLocation` model, `LocationStore`, exceptions, `location_store` singleton).
- Modify `server/astrodeck/api/app.py` (import `location_store` + exceptions; add `LocationBody` and the four routes after the mount-gps route).
- Create `server/tests/test_locations.py` (pure store tests).
- Modify `server/tests/test_rbac_enforcement.py` (route RBAC + payload-absence + log-hygiene).

**Interfaces:**
- Produces `SavedLocation` (pydantic): `id: str` (uuid4 hex), `name: str`, `latitude: float`, `longitude: float`, `elevation_m: float`, `horizon_min_deg: float | None`, `created_ts: float`, `updated_ts: float`.
- `LocationStore.list() -> list[SavedLocation]`; `.create(name, latitude, longitude, elevation_m, horizon_min_deg=None) -> SavedLocation`; `.update(loc_id, name, latitude, longitude, elevation_m, horizon_min_deg=None) -> SavedLocation`; `.delete(loc_id) -> None`.
- Raises `LocationNameCollision(existing_id)` / `LocationLibraryFull` / `KeyError` (unknown id).
- Routes: `GET/POST /api/locations`, `PUT/DELETE /api/locations/{loc_id}` — all `require(CAP_CONFIG_SITE_OPTICS)` + `@declare(CAP_CONFIG_SITE_OPTICS)`.

Steps:

- [ ] Create `server/astrodeck/locations.py`:
  ```python
  """Saved-locations library — named observing sites, INDEPENDENT of rig profiles
  and of AppConfig (spec §4). Precise coordinates live ONLY in this module's own
  JSON file and are served ONLY by the four /api/locations routes, so they never
  ride config/status/WS payloads and the §2 strip seam needs no changes for them.

  Single JSON file CONFIG_DIR/locations.json (survives self-update), atomic write
  + .bak recovery mirroring ConfigStore. MAX_LOCATIONS caps the store. Location
  names/coords must never enter bus.log (same §8 constraint) — this module logs
  nothing coordinate-bearing.
  """
  from __future__ import annotations

  import time
  from pathlib import Path
  from uuid import uuid4

  from pydantic import BaseModel, Field

  from .config import CONFIG_DIR
  from .events import bus
  from .persist import ensure_dir, read_json, write_json_atomic

  LOCATIONS_FILE = CONFIG_DIR / "locations.json"
  MAX_LOCATIONS = 50


  class SavedLocation(BaseModel):
      id: str = Field(default_factory=lambda: uuid4().hex)
      name: str
      latitude: float = Field(..., ge=-90, le=90)      # +N (signed)
      longitude: float = Field(..., ge=-180, le=180)   # +E (East-positive)
      elevation_m: float = Field(..., ge=-430, le=9000)
      # A horizon profile is a property of the SITE (trees/ridgelines), not the
      # rig; optional so a location may omit it.
      horizon_min_deg: float | None = None
      created_ts: float = 0.0
      updated_ts: float = 0.0


  class LocationNameCollision(Exception):
      """Raised on a case-insensitive name clash. Carries the EXISTING id so the
      route can 409 {code: "name_collision", id}."""

      def __init__(self, existing_id: str):
          self.existing_id = existing_id
          super().__init__(f"name collides with {existing_id}")


  class LocationLibraryFull(Exception):
      """Raised when a create would exceed MAX_LOCATIONS."""


  def _norm(name: str) -> str:
      """Trim + casefold for the uniqueness compare."""
      return name.strip().casefold()


  class LocationStore:
      """Module singleton (like ConfigStore) owning the persisted location list.

      One JSON envelope ``{"locations": [...]}``; every mutation writes atomically
      (write_json_atomic keeps a .bak by copy) and a corrupt/absent primary
      recovers from .bak, mirroring ConfigStore._load/_restore_from_bak."""

      def __init__(self, path: Path = LOCATIONS_FILE):
          self._path = path
          self._items: list[SavedLocation] | None = None

      def _bak_path(self) -> Path:
          return self._path.with_suffix(self._path.suffix + ".bak")

      def _parse(self, raw) -> list[SavedLocation] | None:
          if not isinstance(raw, dict):
              return None
          rows = raw.get("locations")
          if not isinstance(rows, list):
              return None
          try:
              return [SavedLocation(**r) for r in rows]
          except Exception:
              return None

      def _restore_from_bak(self) -> list[SavedLocation] | None:
          try:
              raw = read_json(self._bak_path())
          except (FileNotFoundError, ValueError, OSError):
              return None
          items = self._parse(raw)
          if items is None:
              return None
          bus.log("warning", "locations restored from backup (.bak)", "config")
          self._items = items
          ensure_dir(self._path.parent)
          write_json_atomic(self._path, self._dump(items), backup=False)
          return items

      def _load(self) -> list[SavedLocation]:
          try:
              raw = read_json(self._path)
          except FileNotFoundError:
              recovered = self._restore_from_bak()
              if recovered is not None:
                  return recovered
              self._items = []
              return []
          except (ValueError, OSError):
              recovered = self._restore_from_bak()
              if recovered is not None:
                  return recovered
              bus.log("warning", "locations reset to empty (corrupt file)", "config")
              self._items = []
              return []
          items = self._parse(raw)
          if items is None:
              recovered = self._restore_from_bak()
              if recovered is not None:
                  return recovered
              bus.log("warning", "locations reset to empty (invalid shape)", "config")
              items = []
          self._items = items
          return items

      def _dump(self, items: list[SavedLocation]) -> dict:
          return {"locations": [it.model_dump() for it in items]}

      def _items_now(self) -> list[SavedLocation]:
          if self._items is None:
              self._items = self._load()
          return self._items

      def _save(self) -> None:
          ensure_dir(self._path.parent)
          write_json_atomic(self._path, self._dump(self._items_now()))

      def reload(self) -> list[SavedLocation]:
          self._items = self._load()
          return self._items

      # -- read ------------------------------------------------------------------

      def list(self) -> list[SavedLocation]:
          return list(self._items_now())

      # -- mutation --------------------------------------------------------------

      def _collision(self, name: str, exclude_id: str | None) -> str | None:
          key = _norm(name)
          for it in self._items_now():
              if it.id != exclude_id and _norm(it.name) == key:
                  return it.id
          return None

      def create(self, name: str, latitude: float, longitude: float,
                 elevation_m: float,
                 horizon_min_deg: float | None = None) -> SavedLocation:
          items = self._items_now()
          collide = self._collision(name, None)
          if collide is not None:
              raise LocationNameCollision(collide)
          if len(items) >= MAX_LOCATIONS:
              raise LocationLibraryFull()
          now = time.time()
          loc = SavedLocation(name=name.strip(), latitude=latitude,
                              longitude=longitude, elevation_m=elevation_m,
                              horizon_min_deg=horizon_min_deg,
                              created_ts=now, updated_ts=now)
          items.append(loc)
          self._save()
          return loc

      def update(self, loc_id: str, name: str, latitude: float, longitude: float,
                 elevation_m: float,
                 horizon_min_deg: float | None = None) -> SavedLocation:
          items = self._items_now()
          idx = next((i for i, it in enumerate(items) if it.id == loc_id), None)
          if idx is None:
              raise KeyError(loc_id)
          collide = self._collision(name, exclude_id=loc_id)
          if collide is not None:
              raise LocationNameCollision(collide)
          existing = items[idx]
          updated = existing.model_copy(update={
              "name": name.strip(), "latitude": latitude, "longitude": longitude,
              "elevation_m": elevation_m, "horizon_min_deg": horizon_min_deg,
              "updated_ts": time.time()})
          # Re-validate ranges (model_copy skips validation).
          updated = SavedLocation(**updated.model_dump())
          items[idx] = updated
          self._save()
          return updated

      def delete(self, loc_id: str) -> None:
          items = self._items_now()
          idx = next((i for i, it in enumerate(items) if it.id == loc_id), None)
          if idx is None:
              raise KeyError(loc_id)
          items.pop(idx)
          self._save()


  location_store = LocationStore()
  ```

- [ ] Add the locations import and routes to `server/astrodeck/api/app.py`. First add the import (near the other package imports, after the `..config` import block, ~line 744):
  ```python
  from ..locations import (LocationLibraryFull, LocationNameCollision,
                           location_store)
  ```
  Then add the request body model near `SiteSaveBody` (after line 446):
  ```python
  class LocationBody(BaseModel):
      name: str
      latitude: float
      longitude: float
      elevation_m: float
      horizon_min_deg: float | None = None
  ```
  Then add the four routes after the `site_mount_gps` route (from Task 3):
  ```python
      # ------------------------------------------------------- saved locations
      # A named-location library (spec §4), INDEPENDENT of rig profiles and NOT
      # part of AppConfig — precise coords live only in locations.json and are
      # served ONLY here, so they never ride config/status/WS payloads. All four
      # routes are config.site_optics: the library contains precise coordinates
      # and exists to WRITE the site, so the write cap gates the whole surface.

      @app.get("/api/locations")
      @declare(CAP_CONFIG_SITE_OPTICS)
      async def list_locations(
              principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
          return [loc.model_dump() for loc in location_store.list()]

      @app.post("/api/locations")
      @declare(CAP_CONFIG_SITE_OPTICS)
      async def create_location(
              body: LocationBody,
              principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
          try:
              loc = await asyncio.to_thread(
                  location_store.create, body.name, body.latitude, body.longitude,
                  body.elevation_m, body.horizon_min_deg)
          except LocationNameCollision as e:
              raise HTTPException(409, detail={"code": "name_collision",
                                               "id": e.existing_id})
          except LocationLibraryFull:
              raise HTTPException(409, detail={"code": "library_full"})
          return loc.model_dump()

      @app.put("/api/locations/{loc_id}")
      @declare(CAP_CONFIG_SITE_OPTICS)
      async def update_location(
              loc_id: str, body: LocationBody,
              principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
          try:
              loc = await asyncio.to_thread(
                  location_store.update, loc_id, body.name, body.latitude,
                  body.longitude, body.elevation_m, body.horizon_min_deg)
          except KeyError:
              raise HTTPException(404, detail={"code": "not_found"})
          except LocationNameCollision as e:
              raise HTTPException(409, detail={"code": "name_collision",
                                               "id": e.existing_id})
          return loc.model_dump()

      @app.delete("/api/locations/{loc_id}")
      @declare(CAP_CONFIG_SITE_OPTICS)
      async def delete_location(
              loc_id: str,
              principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
          try:
              await asyncio.to_thread(location_store.delete, loc_id)
          except KeyError:
              raise HTTPException(404, detail={"code": "not_found"})
          return {"ok": True}
  ```

- [ ] Create `server/tests/test_locations.py` (pure store tests — no app needed):
  ```python
  """Saved-locations LocationStore unit tests (spec §4/§6): round-trip, atomic
  write + .bak recovery, case-insensitive name-collision, library-full, rename
  collision vs OTHER ids, unknown-id delete. Pure store — no TestClient."""
  from __future__ import annotations

  import pytest

  from astrodeck.locations import (LocationLibraryFull, LocationNameCollision,
                                   LocationStore, MAX_LOCATIONS)


  def _store(tmp_path):
      return LocationStore(path=tmp_path / "locations.json")


  def test_roundtrip_persists_to_disk(tmp_path):
      s = _store(tmp_path)
      loc = s.create("Backyard", 40.0, -74.0, 12.0, 15.0)
      assert loc.id and loc.name == "Backyard" and loc.horizon_min_deg == 15.0
      # a fresh store over the same file reads it back
      rows = _store(tmp_path).list()
      assert len(rows) == 1
      assert rows[0].id == loc.id and rows[0].latitude == 40.0
      assert rows[0].horizon_min_deg == 15.0


  def test_horizon_optional(tmp_path):
      s = _store(tmp_path)
      loc = s.create("NoHorizon", 10.0, 20.0, 0.0)
      assert loc.horizon_min_deg is None


  def test_atomic_write_keeps_bak_and_recovers(tmp_path):
      p = tmp_path / "locations.json"
      s = LocationStore(path=p)
      s.create("A", 1.0, 2.0, 0.0)
      s.create("B", 3.0, 4.0, 0.0)   # second write copies the old primary -> .bak
      assert p.with_suffix(".json.bak").exists()
      # corrupt the primary; a fresh store recovers from .bak (the post-A state)
      p.write_text("{ not valid json", encoding="utf-8")
      rows = LocationStore(path=p).list()
      assert any(r.name == "A" for r in rows)


  def test_name_collision_case_insensitive_trimmed(tmp_path):
      s = _store(tmp_path)
      a = s.create("Home", 1.0, 2.0, 0.0)
      with pytest.raises(LocationNameCollision) as ei:
          s.create("  home  ", 3.0, 4.0, 0.0)
      assert ei.value.existing_id == a.id


  def test_library_full(tmp_path):
      s = _store(tmp_path)
      for i in range(MAX_LOCATIONS):
          s.create(f"L{i}", 1.0, 2.0, 0.0)
      with pytest.raises(LocationLibraryFull):
          s.create("overflow", 1.0, 2.0, 0.0)


  def test_update_rename_collision_vs_other_id(tmp_path):
      s = _store(tmp_path)
      a = s.create("A", 1.0, 2.0, 0.0)
      b = s.create("B", 3.0, 4.0, 0.0)
      with pytest.raises(LocationNameCollision) as ei:
          s.update(b.id, "a", 3.0, 4.0, 0.0)   # rename B->A collides with A
      assert ei.value.existing_id == a.id
      # renaming to its OWN (unchanged) name is allowed
      out = s.update(b.id, "B", 9.0, 9.0, 0.0)
      assert out.latitude == 9.0


  def test_update_unknown_id_raises_keyerror(tmp_path):
      s = _store(tmp_path)
      with pytest.raises(KeyError):
          s.update("nope", "X", 1.0, 2.0, 0.0)


  def test_delete_unknown_id_raises_keyerror(tmp_path):
      s = _store(tmp_path)
      with pytest.raises(KeyError):
          s.delete("nope")
  ```

- [ ] Append the locations route tests (RBAC + payload-absence + log-hygiene) to `server/tests/test_rbac_enforcement.py`. These reuse `_make_client` / `_install` / `_principal_with` / `principal_for_role`:
  ```python
  # ================================================= /api/locations routes + RBAC

  def _wire_locations(tmp_path, monkeypatch, app):
      """Point the location_store singleton at a temp file for this test."""
      import astrodeck.locations as loc_mod
      from astrodeck.locations import LocationStore
      temp = LocationStore(path=tmp_path / "locations.json")
      monkeypatch.setattr(loc_mod, "location_store", temp)
      monkeypatch.setattr(app_module, "location_store", temp)
      return temp


  def test_locations_rbac_viewer_and_operator_forbidden(tmp_path, monkeypatch):
      """All four routes require config.site_optics: viewer AND operator -> 403."""
      store, app = _make_client(tmp_path, monkeypatch)
      _wire_locations(tmp_path, monkeypatch, app)
      body = {"name": "Home", "latitude": 40.0, "longitude": -74.0,
              "elevation_m": 12.0}
      for role in ("viewer", "operator"):
          _install(principal_for_role(role))
          with TestClient(app) as c:
              assert c.get("/api/locations").status_code == 403
              assert c.post("/api/locations", json=body).status_code == 403
              assert c.put("/api/locations/x", json=body).status_code == 403
              assert c.delete("/api/locations/x").status_code == 403


  def test_locations_holder_full_access_and_error_codes(tmp_path, monkeypatch):
      """A config.site_optics holder gets full CRUD; collision -> 409 name_collision
      + existing id; unknown id -> 404."""
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      store, app = _make_client(tmp_path, monkeypatch)
      _wire_locations(tmp_path, monkeypatch, app)
      body = {"name": "Home", "latitude": 40.0, "longitude": -74.0,
              "elevation_m": 12.0}
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          r = c.post("/api/locations", json=body)
          assert r.status_code == 200
          lid = r.json()["id"]
          assert len(c.get("/api/locations").json()) == 1
          # case-insensitive collision -> 409 {code, id}
          rc = c.post("/api/locations", json={**body, "name": "home"})
          assert rc.status_code == 409
          assert rc.json()["detail"]["code"] == "name_collision"
          assert rc.json()["detail"]["id"] == lid
          # rename onto a fresh name -> 200
          assert c.put(f"/api/locations/{lid}",
                       json={**body, "name": "Renamed"}).status_code == 200
          # unknown id -> 404 on PUT and DELETE
          assert c.put("/api/locations/nope", json=body).status_code == 404
          assert c.delete("/api/locations/nope").status_code == 404
          # delete real -> 200, list empty
          assert c.delete(f"/api/locations/{lid}").status_code == 200
          assert c.get("/api/locations").json() == []


  def test_locations_library_full_409(tmp_path, monkeypatch):
      """A create beyond MAX_LOCATIONS -> 409 {code: library_full}."""
      from astrodeck.auth import CAP_CONFIG_SITE_OPTICS
      from astrodeck.locations import MAX_LOCATIONS
      store, app = _make_client(tmp_path, monkeypatch)
      _wire_locations(tmp_path, monkeypatch, app)
      _install(_principal_with(CAP_CONFIG_SITE_OPTICS))
      with TestClient(app) as c:
          for i in range(MAX_LOCATIONS):
              assert c.post("/api/locations",
                            json={"name": f"L{i}", "latitude": 1.0,
                                  "longitude": 2.0, "elevation_m": 0.0}
                            ).status_code == 200
          r = c.post("/api/locations",
                     json={"name": "over", "latitude": 1.0, "longitude": 2.0,
                           "elevation_m": 0.0})
          assert r.status_code == 409
          assert r.json()["detail"]["code"] == "library_full"


  def test_locations_absent_from_all_payloads(tmp_path, monkeypatch):
      """The library is served ONLY by /api/locations — never in status/summary/
      config or the WS hello (the §2 strip seam needs no change for it)."""
      store, app = _make_client(tmp_path, monkeypatch)
      _wire_locations(tmp_path, monkeypatch, app)
      _install(principal_for_role("admin"))
      with TestClient(app) as c:
          assert "locations" not in c.get("/api/status").json()
          assert "locations" not in c.get("/api/config").json()
          summ = c.get("/api/summary").json()
          assert "locations" not in summ
          assert "locations" not in summ.get("config", {})
          with c.websocket_connect("/ws") as ws:
              hello = ws.receive_json()
              assert "locations" not in hello["data"]
              assert "locations" not in hello["data"].get("config", {})


  def test_no_precise_coords_in_logs(tmp_path, monkeypatch):
      """After a /api/site save, a mount-gps read, and a full locations save/
      update/delete cycle against the seeded precise site, the NEW log entries
      contain no precise coordinate strings (spec §8; /api/logs is viewer-visible)."""
      import json as _json
      from astrodeck.events import bus
      from astrodeck.auth import (CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                                  CAP_CONFIG_SAFETY)
      store, app = _make_client(tmp_path, monkeypatch)
      _wire_locations(tmp_path, monkeypatch, app)
      _install(_principal_with(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                               CAP_CONFIG_SAFETY))
      lat_s, lon_s = "40.123456", "-74.654321"
      with TestClient(app) as c:
          before = len(bus.log_history)
          c.put("/api/site", json={"site": {
              "name": "Secret Barn", "latitude": 40.123456,
              "longitude": -74.654321, "elevation_m": 123.4}})
          c.get("/api/site/mount-gps")
          r = c.post("/api/locations", json={
              "name": "Barn", "latitude": 40.123456, "longitude": -74.654321,
              "elevation_m": 123.4})
          lid = r.json()["id"]
          c.put(f"/api/locations/{lid}", json={
              "name": "Barn2", "latitude": 40.123456, "longitude": -74.654321,
              "elevation_m": 123.4})
          c.delete(f"/api/locations/{lid}")
          new_logs = bus.log_history[before:]
      blob = _json.dumps(new_logs)
      assert lat_s not in blob, "precise latitude leaked into bus.log"
      assert lon_s not in blob, "precise longitude leaked into bus.log"
  ```
  (Note: `bus.log_history` is a property returning a fresh list each access; snapshot `before = len(...)` then slice `bus.log_history[before:]` — never `.clear()`.)

- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Expected: prior tests pass plus the new `test_locations.py` (8 tests) and 6 route/hygiene tests. If auto-backgrounded, re-run in the foreground.

- [ ] Commit:
  ```
  git add server/astrodeck/locations.py server/astrodeck/api/app.py server/tests/test_locations.py server/tests/test_rbac_enforcement.py
  ```
  ```
  feat(locations): saved-locations library + four config.site_optics routes

  New server/astrodeck/locations.py: SavedLocation model (uuid4-hex id,
  case-insensitive/trimmed unique name, lat/lon/elevation ranges, optional
  horizon_min_deg, created/updated ts) and a LocationStore singleton backed
  by CONFIG_DIR/locations.json (atomic write + .bak recovery, MAX_LOCATIONS=50)
  — deliberately outside AppConfig and rig profiles, so precise coords never
  ride config/status/WS payloads. GET/POST /api/locations + PUT/DELETE
  /api/locations/{id}, all config.site_optics; 409 name_collision (with the
  existing id) / 409 library_full / 404 unknown id. Names/coords never enter
  bus.log (§8).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 5: UI foundations — types, `lib/site.ts`, `api/site.ts`, `useCanViewSitePrecise`

**Files:**
- Modify `ui/src/types.ts:454-459` (`Site`), `:890-895` (`SiteInfo`), `:86-98` (`RigStatus.site`); append `SavedLocation`.
- Create `ui/src/lib/site.ts` (pure helpers).
- Create `ui/src/lib/__tests__/site.test.ts` (self-executing).
- Create `ui/src/api/site.ts` (typed wrappers).
- Modify `ui/src/lib/caps.ts` (add `useCanViewSitePrecise`).
- Modify `ui/src/components/PreflightStrip.tsx:123` (site signature `?? "hidden"`), and any other consumer the compiler flags.

**Interfaces:**
- Produces `toSigned(magnitude, hemisphere) -> number`; `fromSigned(signed, axis) -> {magnitude, hemisphere}`; `validateLat/validateLon/validateElevation(v) -> string | null`; `formatCoord(v) -> string`; `locationEquals(draft: SiteDraft, loc: SavedLocation) -> boolean`; types `Hemisphere`, `SiteDraft`.
- `saveSite(site, version, horizonMinDeg?)`, `getMountGps()`, `listLocations()`, `saveLocation(body)`, `updateLocation(id, body)`, `deleteLocation(id)`; type `MountGps`, `LocationInput`.
- `useCanViewSitePrecise() -> boolean`.

Steps:

- [ ] Make the wire site shapes' strippable fields optional in `ui/src/types.ts`. Replace `Site` (454-459):
  ```ts
  export interface Site {
    // Strippable over the wire for principals lacking view.site_precise (spec §2):
    // absent, not nulled. is_default + horizon_min_deg are always present.
    name?: string;
    latitude?: number;   // +N (stored signed)
    longitude?: number;  // +E (East-positive; matches coords.lst_hours)
    elevation_m?: number;
    is_default: boolean;
    horizon_min_deg: number;
  }
  ```
  Replace `SiteInfo` (890-895):
  ```ts
  export interface SiteInfo {
    name?: string;
    latitude?: number;
    longitude?: number;
    elevation_m?: number;
    is_default: boolean;
    horizon_min_deg: number;
  }
  ```
  Replace the `RigStatus.site` block (86-98):
  ```ts
    // --- settings (additive; survives the 2s wholesale status replace) ---
    // Full poll_status site shape — superset of SiteInfo. name/lat/lon/elevation
    // are stripped over the wire for non-holders of view.site_precise (spec §2),
    // so they are optional; is_default/horizon_min_deg are always present.
    site?: {
      name?: string;
      latitude?: number;
      longitude?: number;
      elevation_m?: number;
      is_default: boolean;
      horizon_min_deg: number;
    };
    optics?: OpticsComputed;
  }
  ```

- [ ] Append the `SavedLocation` type to `ui/src/types.ts` (near the other config/site types):
  ```ts
  // Saved observing location (server astrodeck/locations.py SavedLocation). Served
  // ONLY by /api/locations — never embedded in config/status/WS payloads.
  export interface SavedLocation {
    id: string;
    name: string;
    latitude: number;   // +N (signed)
    longitude: number;  // +E (East-positive)
    elevation_m: number;
    horizon_min_deg: number | null;
    created_ts: number;
    updated_ts: number;
  }
  ```

- [ ] Create `ui/src/lib/site.ts`:
  ```ts
  // lib/site.ts — pure geo helpers for the Settings → Site panel. No React, no
  // DOM: npx-tsx testable (caps.ts/safety.ts precedent). Longitude is stored
  // SIGNED East-positive, latitude signed +N (config.py:11-16 convention); the UI
  // collects magnitude + hemisphere and converts here at the boundary.
  import type { SavedLocation } from "../types";

  export type Hemisphere = "N" | "S" | "E" | "W";

  // The panel's converted (signed) draft — the comparison basis for saved
  // locations and the payload basis for PUT /api/site.
  export interface SiteDraft {
    name: string;
    latitude: number;   // signed +N
    longitude: number;  // signed +E
    elevation_m: number;
  }

  /** magnitude + hemisphere -> signed value. S/W negate; N/E keep. */
  export function toSigned(magnitude: number, hemisphere: Hemisphere): number {
    return hemisphere === "S" || hemisphere === "W" ? -magnitude : magnitude;
  }

  /** signed value -> {magnitude, hemisphere}. axis picks the N/S vs E/W pair;
   *  0 maps to the positive hemisphere (N / E). */
  export function fromSigned(
    signed: number,
    axis: "lat" | "lon",
  ): { magnitude: number; hemisphere: Hemisphere } {
    const magnitude = Math.abs(signed);
    if (axis === "lat") return { magnitude, hemisphere: signed < 0 ? "S" : "N" };
    return { magnitude, hemisphere: signed < 0 ? "W" : "E" };
  }

  /** Validate a latitude MAGNITUDE (0..90). Returns an error string or null. */
  export function validateLat(mag: number): string | null {
    if (!Number.isFinite(mag)) return "Latitude must be a number";
    if (mag < 0 || mag > 90) return "Latitude must be between 0 and 90°";
    return null;
  }

  /** Validate a longitude MAGNITUDE (0..180). */
  export function validateLon(mag: number): string | null {
    if (!Number.isFinite(mag)) return "Longitude must be a number";
    if (mag < 0 || mag > 180) return "Longitude must be between 0 and 180°";
    return null;
  }

  /** Validate an elevation in metres (−430..9000). */
  export function validateElevation(v: number): string | null {
    if (!Number.isFinite(v)) return "Elevation must be a number";
    if (v < -430 || v > 9000) return "Elevation must be between −430 and 9000 m";
    return null;
  }

  /** Format a signed coordinate to 6 decimal places (mirrors the server store). */
  export function formatCoord(v: number): string {
    return v.toFixed(6);
  }

  /** True when a converted (signed) draft equals a saved location — the dirty-
   *  state basis for the saved-locations row. Coordinates compare at 6-dp; name
   *  is trimmed. */
  export function locationEquals(draft: SiteDraft, loc: SavedLocation): boolean {
    return (
      draft.name.trim() === loc.name.trim() &&
      formatCoord(draft.latitude) === formatCoord(loc.latitude) &&
      formatCoord(draft.longitude) === formatCoord(loc.longitude) &&
      formatCoord(draft.elevation_m) === formatCoord(loc.elevation_m)
    );
  }
  ```

- [ ] Create `ui/src/lib/__tests__/site.test.ts` (self-executing, inline assert harness — `site.ts` is pure so no browser stubs needed):
  ```ts
  // site.test.ts — pure geo helpers (spec §6). Inline assert harness like
  // caps.test.ts. Run: npx tsx src/lib/__tests__/site.test.ts
  import {
    toSigned,
    fromSigned,
    validateLat,
    validateLon,
    validateElevation,
    formatCoord,
    locationEquals,
    type Hemisphere,
    type SiteDraft,
  } from "../site";
  import type { SavedLocation } from "../../types";

  let passed = 0;
  let failed = 0;
  const failures: string[] = [];
  function test(name: string, fn: () => void): void {
    try { fn(); passed++; } catch (e) {
      failed++; failures.push(`✗ ${name}: ${(e as Error).message}`);
    }
  }
  function assert(cond: boolean, msg: string): void {
    if (!cond) throw new Error(msg);
  }
  function eq<T>(a: T, b: T, msg = ""): void {
    if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
  }

  // ---------------------------------------------------------- toSigned
  test("toSigned negates S/W, keeps N/E", () => {
    eq(toSigned(40.5, "N"), 40.5, "N");
    eq(toSigned(40.5, "S"), -40.5, "S");
    eq(toSigned(74.25, "E"), 74.25, "E");
    eq(toSigned(74.25, "W"), -74.25, "W");
    eq(toSigned(0, "S"), -0, "0 magnitude");   // -0 === 0 in JS
    assert(toSigned(0, "S") === 0, "toSigned(0,S) is 0");
  });

  // ---------------------------------------------------------- fromSigned
  test("fromSigned splits magnitude + hemisphere by axis", () => {
    eq(fromSigned(40.5, "lat").magnitude, 40.5, "lat mag");
    eq(fromSigned(40.5, "lat").hemisphere, "N", "lat +");
    eq(fromSigned(-40.5, "lat").hemisphere, "S", "lat -");
    eq(fromSigned(74.25, "lon").hemisphere, "E", "lon +");
    eq(fromSigned(-74.25, "lon").hemisphere, "W", "lon -");
    eq(fromSigned(0, "lat").hemisphere, "N", "0 lat -> N");
    eq(fromSigned(0, "lon").hemisphere, "E", "0 lon -> E");
  });

  // ---------------------------------------------------------- round-trips
  test("signed <-> magnitude round-trips (incl 0 and boundaries)", () => {
    const cases: Array<{ v: number; axis: "lat" | "lon" }> = [
      { v: 0, axis: "lat" }, { v: 0, axis: "lon" },
      { v: 40.123456, axis: "lat" }, { v: -74.654321, axis: "lon" },
      { v: 90, axis: "lat" }, { v: -90, axis: "lat" },
      { v: 180, axis: "lon" }, { v: -180, axis: "lon" },
    ];
    for (const { v, axis } of cases) {
      const { magnitude, hemisphere } = fromSigned(v, axis);
      eq(toSigned(magnitude, hemisphere as Hemisphere), v, `roundtrip ${v}/${axis}`);
    }
  });

  // ---------------------------------------------------------- validation
  test("validateLat range 0..90 + NaN rejected", () => {
    assert(validateLat(0) === null, "0 ok");
    assert(validateLat(90) === null, "90 ok");
    assert(validateLat(-1) !== null, "-1 rejected");
    assert(validateLat(90.1) !== null, "90.1 rejected");
    assert(validateLat(NaN) !== null, "NaN rejected");
  });

  test("validateLon range 0..180 + NaN rejected", () => {
    assert(validateLon(0) === null, "0 ok");
    assert(validateLon(180) === null, "180 ok");
    assert(validateLon(180.1) !== null, "180.1 rejected");
    assert(validateLon(NaN) !== null, "NaN rejected");
  });

  test("validateElevation range -430..9000 + NaN rejected", () => {
    assert(validateElevation(0) === null, "0 ok");
    assert(validateElevation(-430) === null, "-430 ok");
    assert(validateElevation(9000) === null, "9000 ok");
    assert(validateElevation(-431) !== null, "-431 rejected");
    assert(validateElevation(9001) !== null, "9001 rejected");
    assert(validateElevation(NaN) !== null, "NaN rejected");
  });

  // empty-string inputs parse to NaN via Number("") === 0? No: Number("") === 0,
  // but the panel passes Number(str); an EMPTY field yields "" -> Number("") = 0
  // which is a valid coord. The reject-empty guard belongs to the panel (it
  // treats "" as unset); here we prove NaN (non-numeric text) is rejected.
  test("non-numeric text -> NaN is rejected by every validator", () => {
    assert(validateLat(Number("abc")) !== null, "lat NaN");
    assert(validateLon(Number("abc")) !== null, "lon NaN");
    assert(validateElevation(Number("abc")) !== null, "elev NaN");
  });

  // ---------------------------------------------------------- formatCoord
  test("formatCoord is 6 dp", () => {
    eq(formatCoord(40.123456789), "40.123457", "rounds to 6dp");
    eq(formatCoord(-74.65), "-74.650000", "pads to 6dp");
    eq(formatCoord(0), "0.000000", "zero");
  });

  // ---------------------------------------------------------- locationEquals
  const loc: SavedLocation = {
    id: "abc", name: "Backyard", latitude: 40.123456, longitude: -74.654321,
    elevation_m: 12, horizon_min_deg: 15, created_ts: 0, updated_ts: 0,
  };
  test("locationEquals true for a matching signed draft", () => {
    const draft: SiteDraft = {
      name: " Backyard ", latitude: 40.123456, longitude: -74.654321,
      elevation_m: 12,
    };
    assert(locationEquals(draft, loc), "trimmed name + 6dp coords match");
  });
  test("locationEquals false when any field differs", () => {
    const base: SiteDraft = {
      name: "Backyard", latitude: 40.123456, longitude: -74.654321,
      elevation_m: 12,
    };
    assert(!locationEquals({ ...base, name: "Other" }, loc), "name differs");
    assert(!locationEquals({ ...base, latitude: 40.2 }, loc), "lat differs");
    assert(!locationEquals({ ...base, longitude: -74.6 }, loc), "lon differs");
    assert(!locationEquals({ ...base, elevation_m: 13 }, loc), "elev differs");
  });

  // ---------------------------------------------------------- report
  const total = passed + failed;
  // eslint-disable-next-line no-console
  console.log(`\nsite.test: ${passed}/${total} passed`);
  if (failures.length) {
    // eslint-disable-next-line no-console
    console.error(failures.join("\n"));
  }
  export const result = { passed, failed, total };
  ```

- [ ] Create `ui/src/api/site.ts` (mirrors `api/backends.ts` one-function-per-route style):
  ```ts
  // api/site.ts — typed client for the site / mount-GPS / saved-locations surfaces
  // (spec §5). Thin over the shared `api` fetch wrapper (api.ts): same ApiError
  // throwing + per-path timeouts. Each function maps 1:1 to a verified route.
  import { api } from "../api";
  import type { AppConfig, SavedLocation, Site } from "../types";

  /** Best-effort mount GPS read-back (GET /api/site/mount-gps). config.site_optics.
   *  Always resolves 200; `available:false` carries a human `detail`. */
  export interface MountGps {
    available: boolean;
    latitude?: number;
    longitude?: number;
    elevation_m?: number;
    detail?: string;
  }

  /** Create/update body for a saved location (server LocationBody). */
  export interface LocationInput {
    name: string;
    latitude: number;
    longitude: number;
    elevation_m: number;
    horizon_min_deg?: number | null;
  }

  /** PUT /api/site {site, version, horizon_min_deg?} -> redacted config payload.
   *  config.site_optics (+ config.safety when horizon_min_deg is present). Pass
   *  `horizonMinDeg` ONLY when applying a saved location that carries one and the
   *  principal holds config.safety (§5); manual edits omit it (server preserves
   *  the stored value). 409 on a version conflict. */
  export const saveSite = (
    site: Site,
    version: number | null,
    horizonMinDeg?: number,
  ): Promise<AppConfig> => {
    const body: { site: Site; version: number | null; horizon_min_deg?: number } = {
      site,
      version,
    };
    if (horizonMinDeg !== undefined) body.horizon_min_deg = horizonMinDeg;
    return api.put<AppConfig>("/api/site", body);
  };

  /** GET /api/site/mount-gps. config.site_optics. */
  export const getMountGps = (): Promise<MountGps> =>
    api.get<MountGps>("/api/site/mount-gps");

  /** GET /api/locations -> full list. config.site_optics. */
  export const listLocations = (): Promise<SavedLocation[]> =>
    api.get<SavedLocation[]>("/api/locations");

  /** POST /api/locations. 409 {code:"name_collision"|"library_full"}. */
  export const saveLocation = (body: LocationInput): Promise<SavedLocation> =>
    api.post<SavedLocation>("/api/locations", body);

  /** PUT /api/locations/{id}. 409 name_collision / 404 not_found. */
  export const updateLocation = (
    id: string,
    body: LocationInput,
  ): Promise<SavedLocation> =>
    api.put<SavedLocation>(`/api/locations/${id}`, body);

  /** DELETE /api/locations/{id}. 404 not_found. */
  export const deleteLocation = (id: string): Promise<void> =>
    api.del<void>(`/api/locations/${id}`);
  ```

- [ ] Add `useCanViewSitePrecise` to `ui/src/lib/caps.ts` (after the other convenience hooks, ~line 131). Insert:
  ```ts
  /** view.site_precise — gates precise-coordinate display (Site panel "Hidden"
   *  placeholder) and, in sub-project C, the weather/radar panel. Admin-only. */
  export const useCanViewSitePrecise = () => useCapability("view.site_precise");
  ```

- [ ] Update the site signature in `ui/src/components/PreflightStrip.tsx`. In `useSharedAltById` (line 123) `site.latitude`/`site.longitude` are now `number | undefined`; use a `"hidden"` fallback so a stripped viewer produces a stable cache key (behavior unchanged — the effect already skips fetching when `site.is_default`). Change line 123:
  ```tsx
      const siteSig = `${site.latitude ?? "hidden"}|${site.longitude ?? "hidden"}|${site.horizon_min_deg}`;
  ```

- [ ] Run the new UI test: `cd ui && npx tsx src/lib/__tests__/site.test.ts`. Expected: `site.test: N/N passed` with no failures. If auto-backgrounded, re-run in the foreground.

- [ ] Run `cd ui && npm run build`. Expected: green. The optional-field change is a strict-`tsc` sweep: fix EVERY consumer the compiler flags for a now-possibly-undefined `name`/`latitude`/`longitude`/`elevation_m` on `Site`/`SiteInfo`/`RigStatus.site`, using the kept fields (`is_default`/`horizon_min_deg`) or `?? ` fallbacks — do not add non-null assertions to hide a real strip. Known consumers per §5: `PreflightStrip.tsx` (handled above) and `AtlasView.tsx` (uses only `is_default`/`horizon_min_deg` — expected to need no change). Add any other flagged file to this task's `git add`.

- [ ] Commit:
  ```
  git add ui/src/types.ts ui/src/lib/site.ts ui/src/lib/__tests__/site.test.ts ui/src/api/site.ts ui/src/lib/caps.ts ui/src/components/PreflightStrip.tsx
  ```
  (Append any additional consumer files the build sweep required.)
  ```
  feat(ui): site foundations — optional strippable fields, lib/site, api/site

  types.ts: name/latitude/longitude/elevation_m become optional on Site,
  SiteInfo, and RigStatus.site (stripped over the wire for non-holders of
  view.site_precise); is_default + horizon_min_deg stay required; add
  SavedLocation. New lib/site.ts pure helpers (toSigned/fromSigned/validate*/
  formatCoord/locationEquals) with a self-executing site.test.ts. New
  api/site.ts typed wrappers (saveSite/getMountGps/list/save/update/
  deleteLocation). New useCanViewSitePrecise hook. PreflightStrip site
  signature uses `?? "hidden"` (behavior unchanged).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 6: SitePanel core — form, geolocation, mount GPS, mount in Connect tab

**Files:**
- Create `ui/src/components/settings/SitePanel.tsx` (core panel; the saved-locations row is added in Task 7).
- Modify `ui/src/components/settings/SettingsView.tsx:150-157` (mount `<SitePanel />` between `DriversPanel` and `SkyAtlasPanel`).

**Interfaces:**
- Consumes `useConfig`, `useStore` (`showToast`, `loadConfig`), `useCan("config.site_optics")`, `useCanViewSitePrecise`, `saveSite`, `getMountGps`, `lib/site.ts` helpers, `ApiError`, `Panel`/`Field`/`Icon`.
- Produces the `SitePanel` default export.

Steps:

- [ ] Create `ui/src/components/settings/SitePanel.tsx` (follows the DriversPanel pattern: `Panel`/`Field`/`.field`/`btn btn-accent`/`run` busy+toast/403 message/lock note). Full file:
  ```tsx
  // SitePanel.tsx — Settings → "Observing Site" (site-location-privacy spec §5).
  // The ONE place the observing site is edited: name + latitude (magnitude 0-90 +
  // N/S) + longitude (magnitude 0-180 + E/W) + elevation, converted to the signed
  // storage convention at the boundary via lib/site.ts. Reads config.site
  // (useConfig); coordinate fields show "Hidden" for principals lacking
  // view.site_precise. Writes are config.site_optics-gated (read-only otherwise,
  // same Gated idiom as DriversPanel). horizon_min_deg is NEVER sent from a manual
  // edit — the Safety panel owns it, and omission preserves the stored value.
  import { useEffect, useState, type JSX } from "react";
  import type { Site } from "../../types";
  import { getMountGps, saveSite } from "../../api/site";
  import { ApiError } from "../../api";
  import { useConfig, useStore } from "../../store";
  import { useCan, useCanViewSitePrecise } from "../../lib/caps";
  import {
    formatCoord,
    fromSigned,
    toSigned,
    validateElevation,
    validateLat,
    validateLon,
  } from "../../lib/site";
  import { Field, Panel } from "../ui";
  import { Icon } from "../icons";

  export default function SitePanel(): JSX.Element {
    const config = useConfig();
    const showToast = useStore((s) => s.showToast);
    const loadConfig = useStore((s) => s.loadConfig);
    const canEdit = useCan("config.site_optics");
    const canSeePrecise = useCanViewSitePrecise();

    // Draft fields (strings for text inputs; hemispheres as selects).
    const [name, setName] = useState("");
    const [latMag, setLatMag] = useState("");
    const [latHemi, setLatHemi] = useState<"N" | "S">("N");
    const [lonMag, setLonMag] = useState("");
    const [lonHemi, setLonHemi] = useState<"E" | "W">("E");
    const [elev, setElev] = useState("");
    const [busy, setBusy] = useState(false);

    // Seed drafts from config.site whenever the version changes (initial load +
    // after a save that bumps version) — not on every render, so in-flight edits
    // survive an unrelated config event.
    useEffect(() => {
      const s = config?.site;
      if (!s) return;
      setName(s.name ?? "");
      if (typeof s.latitude === "number") {
        const { magnitude, hemisphere } = fromSigned(s.latitude, "lat");
        setLatMag(formatCoord(magnitude));
        setLatHemi(hemisphere as "N" | "S");
      }
      if (typeof s.longitude === "number") {
        const { magnitude, hemisphere } = fromSigned(s.longitude, "lon");
        setLonMag(formatCoord(magnitude));
        setLonHemi(hemisphere as "E" | "W");
      }
      if (typeof s.elevation_m === "number") setElev(String(s.elevation_m));
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [config?.version]);

    // A generic action runner (DriversPanel idiom): busy + optional success toast,
    // 403 -> capability message. Save has its own handler (409 is special).
    const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
      if (busy) return;
      setBusy(true);
      try {
        await fn();
        if (okMsg) showToast("success", okMsg);
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? e.status === 403
              ? "config.site_optics required to change the site"
              : e.message
            : e instanceof Error
              ? e.message
              : "operation failed";
        showToast("error", msg);
      } finally {
        setBusy(false);
      }
    };

    const validate = (): string | null =>
      validateLat(Number(latMag)) ||
      validateLon(Number(lonMag)) ||
      validateElevation(Number(elev));

    const buildSite = (): Site => ({
      // carry is_default + horizon_min_deg through from config (server flips
      // is_default off and preserves the stored horizon when the body omits it).
      ...(config?.site ?? { is_default: true, horizon_min_deg: 15 }),
      name: name.trim() || "My Observatory",
      latitude: toSigned(Number(latMag), latHemi),
      longitude: toSigned(Number(lonMag), lonHemi),
      elevation_m: Number(elev),
    });

    const onSave = async () => {
      if (busy) return;
      const err = validate();
      if (err) {
        showToast("error", err);
        return;
      }
      setBusy(true);
      try {
        await saveSite(buildSite(), config?.version ?? null);
        await loadConfig();
        showToast("success", "Site saved");
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          await loadConfig();
          showToast(
            "error",
            "Config changed elsewhere — reloaded, re-apply your edit",
          );
        } else if (e instanceof ApiError && e.status === 403) {
          showToast("error", "config.site_optics required to save the site");
        } else {
          showToast("error", e instanceof Error ? e.message : "Could not save site");
        }
      } finally {
        setBusy(false);
      }
    };

    // Browser geolocation is only reachable over HTTPS or localhost; the LAN UI is
    // plain http, so gate the button on a secure context (spec §5).
    const geoAvailable =
      typeof window !== "undefined" &&
      window.isSecureContext &&
      "geolocation" in navigator;

    const useMyLocation = () => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const la = fromSigned(pos.coords.latitude, "lat");
          const lo = fromSigned(pos.coords.longitude, "lon");
          setLatMag(formatCoord(la.magnitude));
          setLatHemi(la.hemisphere as "N" | "S");
          setLonMag(formatCoord(lo.magnitude));
          setLonHemi(lo.hemisphere as "E" | "W");
          if (typeof pos.coords.altitude === "number")
            setElev(String(Math.round(pos.coords.altitude)));
          showToast("success", "Filled from browser location — review and save");
        },
        (e) => showToast("error", e.message || "Couldn't get browser location"),
        { enableHighAccuracy: true, timeout: 10000 },
      );
    };

    const useMountGps = () =>
      run(async () => {
        const g = await getMountGps();
        if (!g.available) {
          showToast("info", g.detail ?? "Mount GPS unavailable");
          return;
        }
        const la = fromSigned(g.latitude as number, "lat");
        const lo = fromSigned(g.longitude as number, "lon");
        setLatMag(formatCoord(la.magnitude));
        setLatHemi(la.hemisphere as "N" | "S");
        setLonMag(formatCoord(lo.magnitude));
        setLonHemi(lo.hemisphere as "E" | "W");
        if (typeof g.elevation_m === "number")
          setElev(String(Math.round(g.elevation_m)));
        showToast("success", "Filled from mount GPS — review and save");
      });

    const coordPlaceholder = canSeePrecise ? "0.000000" : "Hidden";

    return (
      <Panel title="Observing Site">
        <div className="flex flex-col gap-3">
          {config?.site?.is_default && (
            <p className="text-[12px] text-warn inline-flex items-start gap-1.5">
              <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
              <span>
                Using default location (0, 0) — sequencing windows and Atlas
                visibility are wrong until set.
              </span>
            </p>
          )}

          <Field label="Site name">
            <input
              className="field"
              value={name}
              disabled={!canEdit}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Observatory"
            />
          </Field>

          <Field label="Latitude">
            <div className="flex items-center gap-2">
              <input
                className="field"
                inputMode="decimal"
                value={latMag}
                disabled={!canEdit}
                onChange={(e) => setLatMag(e.target.value)}
                placeholder={coordPlaceholder}
                aria-label="Latitude magnitude (0–90)"
              />
              <select
                className="field !w-auto"
                value={latHemi}
                disabled={!canEdit}
                onChange={(e) => setLatHemi(e.target.value as "N" | "S")}
                aria-label="Latitude hemisphere"
              >
                <option value="N">N</option>
                <option value="S">S</option>
              </select>
            </div>
          </Field>

          <Field label="Longitude">
            <div className="flex items-center gap-2">
              <input
                className="field"
                inputMode="decimal"
                value={lonMag}
                disabled={!canEdit}
                onChange={(e) => setLonMag(e.target.value)}
                placeholder={coordPlaceholder}
                aria-label="Longitude magnitude (0–180)"
              />
              <select
                className="field !w-auto"
                value={lonHemi}
                disabled={!canEdit}
                onChange={(e) => setLonHemi(e.target.value as "E" | "W")}
                aria-label="Longitude hemisphere"
              >
                <option value="E">E</option>
                <option value="W">W</option>
              </select>
            </div>
          </Field>

          <Field label="Elevation (m)">
            <input
              className="field"
              inputMode="numeric"
              value={elev}
              disabled={!canEdit}
              onChange={(e) => setElev(e.target.value)}
              placeholder="0"
              aria-label="Elevation in metres"
            />
          </Field>

          {canEdit && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void onSave()}
              >
                Save site
              </button>
              {geoAvailable ? (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={useMyLocation}
                >
                  Use my location
                </button>
              ) : (
                <p className="text-[11px] text-dim self-center">
                  Browser location needs HTTPS or localhost — enter manually or use
                  mount GPS.
                </p>
              )}
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void useMountGps()}
              >
                Use mount GPS
              </button>
            </div>
          )}

          {!canEdit && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — changing the site needs config.site_optics access.
            </p>
          )}
        </div>
      </Panel>
    );
  }
  ```
  (Hemisphere casts use the narrower literal unions `"N" | "S"` / `"E" | "W"` directly, so `lib/site.ts`'s `Hemisphere` is deliberately NOT imported here — an unused import would fail strict `noUnusedLocals`.)

- [ ] Mount `<SitePanel />` in the Connect tab of `ui/src/components/settings/SettingsView.tsx`. Add the import next to the other panel imports (after line 33 `import SkyAtlasPanel ...`):
  ```tsx
  import SitePanel from "./SitePanel";
  ```
  And place it between `DriversPanel` and `SkyAtlasPanel` in the Connect column (lines 153-156):
  ```tsx
            <div className="order-2 lg:order-1 min-w-0 flex flex-col gap-4">
              <DriversPanel />
              <SitePanel />
              <SkyAtlasPanel />
            </div>
  ```

- [ ] Run `cd ui && npm run build`. Expected: green (strict `tsc -b && vite build`; no unused locals).

- [ ] Commit:
  ```
  git add ui/src/components/settings/SitePanel.tsx ui/src/components/settings/SettingsView.tsx
  ```
  ```
  feat(ui/settings): Observing Site panel (core) in the Connect tab

  New SitePanel following the DriversPanel idiom: name + latitude (0-90 +
  N/S) + longitude (0-180 + E/W) + elevation, converted to the signed
  storage convention via lib/site.ts. Reads config.site; coordinate fields
  show "Hidden" for principals lacking view.site_precise. Save = PUT /api/site
  {site, version} then loadConfig(); 409 -> reload + "re-apply your edit"
  toast. is_default warn chip. "Use my location" only when isSecureContext +
  geolocation (else a manual-entry hint); "Use mount GPS" fills the draft.
  horizon_min_deg never sent from manual edits. Mounted between DriversPanel
  and SkyAtlasPanel. Semantic tokens only (night-safe).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

### Task 7: saved locations row in SitePanel + final dist rebuild

**Files:**
- Modify `ui/src/components/settings/SitePanel.tsx` (add the saved-locations row + apply/save-current/delete + dirty-state; the full final file is shown below).
- Rebuild `ui/dist` (`npm run build`) so `:8801` serves the updated bundle.

**Interfaces:**
- Consumes `listLocations`, `saveLocation`, `updateLocation`, `deleteLocation`, `locationEquals`, `type SiteDraft`, `confirmDialog`, `SavedLocation`.
- No new exports.

Steps:

- [ ] Replace `ui/src/components/settings/SitePanel.tsx` with the full version that adds the saved-locations row (superset of Task 6). Full file:
  ```tsx
  // SitePanel.tsx — Settings → "Observing Site" (site-location-privacy spec §5).
  // The ONE place the observing site is edited: name + latitude (magnitude 0-90 +
  // N/S) + longitude (magnitude 0-180 + E/W) + elevation, converted to the signed
  // storage convention at the boundary via lib/site.ts. Reads config.site
  // (useConfig); coordinate fields show "Hidden" for principals lacking
  // view.site_precise. Writes are config.site_optics-gated. horizon_min_deg is
  // NEVER sent from a manual edit; a saved location may CARRY one, applied through
  // PUT /api/site only when the principal holds config.safety (§4). The saved-
  // locations row renders only for config.site_optics holders (the routes 403
  // otherwise).
  import { useEffect, useState, type JSX } from "react";
  import type { SavedLocation, Site } from "../../types";
  import {
    deleteLocation,
    getMountGps,
    listLocations,
    saveLocation,
    saveSite,
    updateLocation,
  } from "../../api/site";
  import { ApiError } from "../../api";
  import { useConfig, useStore } from "../../store";
  import { useCan, useCanViewSitePrecise } from "../../lib/caps";
  import {
    formatCoord,
    fromSigned,
    locationEquals,
    toSigned,
    validateElevation,
    validateLat,
    validateLon,
    type SiteDraft,
  } from "../../lib/site";
  import { confirmDialog } from "../ConfirmDialog";
  import { Field, Panel } from "../ui";
  import { Icon } from "../icons";

  export default function SitePanel(): JSX.Element {
    const config = useConfig();
    const showToast = useStore((s) => s.showToast);
    const loadConfig = useStore((s) => s.loadConfig);
    const canEdit = useCan("config.site_optics");
    const canSafety = useCan("config.safety");
    const canSeePrecise = useCanViewSitePrecise();

    const [name, setName] = useState("");
    const [latMag, setLatMag] = useState("");
    const [latHemi, setLatHemi] = useState<"N" | "S">("N");
    const [lonMag, setLonMag] = useState("");
    const [lonHemi, setLonHemi] = useState<"E" | "W">("E");
    const [elev, setElev] = useState("");
    const [busy, setBusy] = useState(false);

    // Saved-locations row state.
    const [locations, setLocations] = useState<SavedLocation[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [baseline, setBaseline] = useState<SavedLocation | null>(null);
    // horizon carried by the applied location, sent on the next save when the
    // principal holds config.safety (§4/§5). Null = nothing to carry.
    const [appliedHorizon, setAppliedHorizon] = useState<number | null>(null);
    // inline "Save current…" name prompt (ConfirmDialog-pattern, but a text field
    // — the modal has no text input).
    const [savingName, setSavingName] = useState<string | null>(null);

    useEffect(() => {
      const s = config?.site;
      if (!s) return;
      setName(s.name ?? "");
      if (typeof s.latitude === "number") {
        const { magnitude, hemisphere } = fromSigned(s.latitude, "lat");
        setLatMag(formatCoord(magnitude));
        setLatHemi(hemisphere as "N" | "S");
      }
      if (typeof s.longitude === "number") {
        const { magnitude, hemisphere } = fromSigned(s.longitude, "lon");
        setLonMag(formatCoord(magnitude));
        setLonHemi(hemisphere as "E" | "W");
      }
      if (typeof s.elevation_m === "number") setElev(String(s.elevation_m));
      // a fresh config re-seed invalidates the applied-location baseline.
      setAppliedHorizon(null);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [config?.version]);

    const refreshLocations = async () => {
      setLocations(await listLocations());
    };

    // Only holders can read the library (the route 403s otherwise).
    useEffect(() => {
      if (!canEdit) return;
      void refreshLocations().catch(() => {
        /* non-fatal; the row just shows an empty list */
      });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [canEdit]);

    const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
      if (busy) return;
      setBusy(true);
      try {
        await fn();
        if (okMsg) showToast("success", okMsg);
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? e.status === 403
              ? "config.site_optics required to change the site"
              : e.message
            : e instanceof Error
              ? e.message
              : "operation failed";
        showToast("error", msg);
      } finally {
        setBusy(false);
      }
    };

    const validate = (): string | null =>
      validateLat(Number(latMag)) ||
      validateLon(Number(lonMag)) ||
      validateElevation(Number(elev));

    const draft = (): SiteDraft => ({
      name: name.trim(),
      latitude: toSigned(Number(latMag) || 0, latHemi),
      longitude: toSigned(Number(lonMag) || 0, lonHemi),
      elevation_m: Number(elev) || 0,
    });

    const buildSite = (): Site => ({
      ...(config?.site ?? { is_default: true, horizon_min_deg: 15 }),
      name: name.trim() || "My Observatory",
      latitude: toSigned(Number(latMag), latHemi),
      longitude: toSigned(Number(lonMag), lonHemi),
      elevation_m: Number(elev),
    });

    const onSave = async () => {
      if (busy) return;
      const err = validate();
      if (err) {
        showToast("error", err);
        return;
      }
      // §4/§5: include the applied location's horizon ONLY when it carries one AND
      // the principal holds config.safety; otherwise omit (server preserves stored).
      const horizon =
        canSafety && appliedHorizon !== null ? appliedHorizon : undefined;
      setBusy(true);
      try {
        await saveSite(buildSite(), config?.version ?? null, horizon);
        await loadConfig();
        showToast("success", "Site saved");
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          await loadConfig();
          showToast(
            "error",
            "Config changed elsewhere — reloaded, re-apply your edit",
          );
        } else if (e instanceof ApiError && e.status === 403) {
          showToast("error", "config.site_optics required to save the site");
        } else {
          showToast("error", e instanceof Error ? e.message : "Could not save site");
        }
      } finally {
        setBusy(false);
      }
    };

    const geoAvailable =
      typeof window !== "undefined" &&
      window.isSecureContext &&
      "geolocation" in navigator;

    const useMyLocation = () => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const la = fromSigned(pos.coords.latitude, "lat");
          const lo = fromSigned(pos.coords.longitude, "lon");
          setLatMag(formatCoord(la.magnitude));
          setLatHemi(la.hemisphere as "N" | "S");
          setLonMag(formatCoord(lo.magnitude));
          setLonHemi(lo.hemisphere as "E" | "W");
          if (typeof pos.coords.altitude === "number")
            setElev(String(Math.round(pos.coords.altitude)));
          showToast("success", "Filled from browser location — review and save");
        },
        (e) => showToast("error", e.message || "Couldn't get browser location"),
        { enableHighAccuracy: true, timeout: 10000 },
      );
    };

    const useMountGps = () =>
      run(async () => {
        const g = await getMountGps();
        if (!g.available) {
          showToast("info", g.detail ?? "Mount GPS unavailable");
          return;
        }
        const la = fromSigned(g.latitude as number, "lat");
        const lo = fromSigned(g.longitude as number, "lon");
        setLatMag(formatCoord(la.magnitude));
        setLatHemi(la.hemisphere as "N" | "S");
        setLonMag(formatCoord(lo.magnitude));
        setLonHemi(lo.hemisphere as "E" | "W");
        if (typeof g.elevation_m === "number")
          setElev(String(Math.round(g.elevation_m)));
        showToast("success", "Filled from mount GPS — review and save");
      });

    // ---- saved-locations actions --------------------------------------------

    const applyLocation = (loc: SavedLocation) => {
      setName(loc.name);
      const la = fromSigned(loc.latitude, "lat");
      const lo = fromSigned(loc.longitude, "lon");
      setLatMag(formatCoord(la.magnitude));
      setLatHemi(la.hemisphere as "N" | "S");
      setLonMag(formatCoord(lo.magnitude));
      setLonHemi(lo.hemisphere as "E" | "W");
      setElev(String(loc.elevation_m));
      setSelectedId(loc.id);
      setBaseline(loc);
      setAppliedHorizon(loc.horizon_min_deg);
    };

    const onPickLocation = (id: string) => {
      const loc = locations.find((l) => l.id === id);
      if (loc) applyLocation(loc);
    };

    const submitSaveCurrent = async (locName: string) => {
      const err = validate();
      if (err) {
        showToast("error", err);
        return;
      }
      const d = draft();
      const input = {
        name: locName.trim(),
        latitude: d.latitude,
        longitude: d.longitude,
        elevation_m: d.elevation_m,
        // Save the CURRENT stored horizon (§5), not a panel-edited one.
        horizon_min_deg: config?.site?.horizon_min_deg ?? null,
      };
      try {
        const loc = await saveLocation(input);
        await refreshLocations();
        setSelectedId(loc.id);
        setBaseline(loc);
        setSavingName(null);
        showToast("success", `Saved location "${loc.name}"`);
      } catch (e) {
        if (e instanceof ApiError && e.code === "name_collision") {
          const existing = locations.find(
            (l) => l.name.trim().toLowerCase() === locName.trim().toLowerCase(),
          );
          const ok = await confirmDialog({
            title: `A location named "${locName}" already exists`,
            body: "Overwrite it with the current coordinates?",
            tone: "warn",
            confirmLabel: "Overwrite",
          });
          if (ok && existing) {
            try {
              const loc = await updateLocation(existing.id, input);
              await refreshLocations();
              setSelectedId(loc.id);
              setBaseline(loc);
              setSavingName(null);
              showToast("success", "Location updated");
            } catch (e2) {
              showToast("error", e2 instanceof Error ? e2.message : "Update failed");
            }
          }
        } else if (e instanceof ApiError && e.code === "library_full") {
          showToast("error", "Location library is full (50) — delete one first.");
        } else {
          showToast("error", e instanceof Error ? e.message : "Save failed");
        }
      }
    };

    const deleteSelected = () =>
      void (async () => {
        const loc = locations.find((l) => l.id === selectedId);
        if (!loc) return;
        const ok = await confirmDialog({
          title: `Delete saved location "${loc.name}"?`,
          tone: "danger",
          confirmLabel: "Delete",
        });
        if (!ok) return;
        try {
          await deleteLocation(loc.id);
          await refreshLocations();
          setSelectedId(null);
          setBaseline(null);
          showToast("success", "Location deleted");
        } catch (e) {
          showToast("error", e instanceof Error ? e.message : "Delete failed");
        }
      })();

    const coordPlaceholder = canSeePrecise ? "0.000000" : "Hidden";
    const dirty = baseline ? !locationEquals(draft(), baseline) : false;
    const sortedLocations = [...locations].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );

    return (
      <Panel title="Observing Site">
        <div className="flex flex-col gap-3">
          {config?.site?.is_default && (
            <p className="text-[12px] text-warn inline-flex items-start gap-1.5">
              <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
              <span>
                Using default location (0, 0) — sequencing windows and Atlas
                visibility are wrong until set.
              </span>
            </p>
          )}

          <Field label="Site name">
            <input
              className="field"
              value={name}
              disabled={!canEdit}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Observatory"
            />
          </Field>

          <Field label="Latitude">
            <div className="flex items-center gap-2">
              <input
                className="field"
                inputMode="decimal"
                value={latMag}
                disabled={!canEdit}
                onChange={(e) => setLatMag(e.target.value)}
                placeholder={coordPlaceholder}
                aria-label="Latitude magnitude (0–90)"
              />
              <select
                className="field !w-auto"
                value={latHemi}
                disabled={!canEdit}
                onChange={(e) => setLatHemi(e.target.value as "N" | "S")}
                aria-label="Latitude hemisphere"
              >
                <option value="N">N</option>
                <option value="S">S</option>
              </select>
            </div>
          </Field>

          <Field label="Longitude">
            <div className="flex items-center gap-2">
              <input
                className="field"
                inputMode="decimal"
                value={lonMag}
                disabled={!canEdit}
                onChange={(e) => setLonMag(e.target.value)}
                placeholder={coordPlaceholder}
                aria-label="Longitude magnitude (0–180)"
              />
              <select
                className="field !w-auto"
                value={lonHemi}
                disabled={!canEdit}
                onChange={(e) => setLonHemi(e.target.value as "E" | "W")}
                aria-label="Longitude hemisphere"
              >
                <option value="E">E</option>
                <option value="W">W</option>
              </select>
            </div>
          </Field>

          <Field label="Elevation (m)">
            <input
              className="field"
              inputMode="numeric"
              value={elev}
              disabled={!canEdit}
              onChange={(e) => setElev(e.target.value)}
              placeholder="0"
              aria-label="Elevation in metres"
            />
          </Field>

          {canEdit && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void onSave()}
              >
                Save site
              </button>
              {geoAvailable ? (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={useMyLocation}
                >
                  Use my location
                </button>
              ) : (
                <p className="text-[11px] text-dim self-center">
                  Browser location needs HTTPS or localhost — enter manually or use
                  mount GPS.
                </p>
              )}
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void useMountGps()}
              >
                Use mount GPS
              </button>
            </div>
          )}

          {/* --------------------------------------------- saved locations row */}
          {canEdit && (
            <div className="border-t border-line pt-3 flex flex-col gap-2">
              <Field label="Saved locations">
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    className="field !w-auto"
                    value={dirty ? "" : (selectedId ?? "")}
                    disabled={busy}
                    onChange={(e) => onPickLocation(e.target.value)}
                    aria-label="Saved locations"
                  >
                    <option value="">—</option>
                    {sortedLocations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || !selectedId || dirty}
                    onClick={() => {
                      const loc = locations.find((l) => l.id === selectedId);
                      if (loc) applyLocation(loc);
                    }}
                  >
                    Apply
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => setSavingName(name.trim() || "New location")}
                  >
                    Save current…
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={busy || !selectedId}
                    onClick={deleteSelected}
                  >
                    Delete
                  </button>
                </div>
              </Field>

              {dirty && baseline && (
                <p className="text-[11px] text-dim">
                  Modified — differs from &quot;{baseline.name}&quot;
                </p>
              )}

              {savingName !== null && (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    className="field !w-auto"
                    value={savingName}
                    disabled={busy}
                    autoFocus
                    onChange={(e) => setSavingName(e.target.value)}
                    aria-label="New saved-location name"
                    placeholder="Location name"
                  />
                  <button
                    type="button"
                    className="btn btn-accent"
                    disabled={busy || savingName.trim() === ""}
                    onClick={() => void submitSaveCurrent(savingName)}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => setSavingName(null)}
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          )}

          {!canEdit && (
            <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
              <Icon name="lock" size={11} />
              Read-only — changing the site needs config.site_optics access.
            </p>
          )}
        </div>
      </Panel>
    );
  }
  ```

- [ ] Run `cd ui && npm run build`. Expected: green (`tsc -b && vite build`, strict). This regenerates `ui/dist` so `:8801` serves the updated bundle.

- [ ] Run the full server suite once more to confirm nothing regressed: `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Expected: all green (1037 baseline + the new server tests from Tasks 1-4). If auto-backgrounded, re-run in the foreground.

- [ ] Commit:
  ```
  git add ui/src/components/settings/SitePanel.tsx ui/dist
  ```
  ```
  feat(ui/settings): saved-locations row in SitePanel + rebuild dist

  Adds the saved-locations row (config.site_optics holders only): a
  case-insensitively-sorted dropdown, Apply (fills draft + records the
  baseline), dirty-state via locationEquals with a muted "Modified — differs
  from <name>" note, "Save current…" (inline name prompt; 409 name_collision
  -> confirm-overwrite -> updateLocation; 409 library_full -> "full (50)"
  toast), and Delete (ConfirmDialog). Applying a location that carries a
  horizon_min_deg includes it in the PUT only when the principal holds
  config.safety (§4/§5). Rebuilds ui/dist so :8801 serves the new bundle.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

---

## Spec coverage table

| Spec § requirement | Task(s) |
| --- | --- |
| §1 Ground truth (existing code the spec builds on) — leak surfaces 1-3 identified | Closed by Tasks 1 (elevation+name), 2 (site/sky + visibility) |
| §2 Strip entirely: replace `_coarsen_latlon` with `_strip_site` deleting `{name,latitude,longitude,elevation_m}`, keys ABSENT | Task 1 |
| §2 `_redact_site_for`/`_redact_ws_event` keep signatures/call sites + copy-on-write | Task 1 |
| §2 Mid-stream downgrade re-tightens to stripped | Task 1 (relay downgrade test) |
| §2 `/api/site/sky` omits `place_hint`+`lst_str` for non-holders; keeps `sun_alt_deg`+`dark_window` | Task 2 |
| §2 `/api/visibility`+`/api/visibility/order` gain `require(view.status)`+`@declare`; removed from boot exemption | Task 2 |
| §2 `PUT /api/site` echo already redacted per-principal (documented, not special-cased) | Task 1 (docstring) / existing behavior |
| §2 Residual leak accepted/documented | §-decision; no code (kept ephemeris) — Task 2 |
| §2 BREAKING change note for non-admin `/api/status`+`/api/summary` consumers | Task 1 (commit body) |
| §3 `hub.read_site_from_mount()` best-effort `_get`; always-200 response shape; false for no-mount/no-`_get`/read-fail/(0,0)/non-finite/out-of-range | Task 3 |
| §3 `GET /api/site/mount-gps` gated `config.site_optics`; assist-only, never persisted | Task 3 |
| §3 Never log coordinate values | Task 3 (outcome-only log) + Task 4 (log-hygiene test) |
| §4 `SavedLocation` model (uuid4 hex id, unique case-insensitive trimmed non-empty name, ranges, `horizon_min_deg` optional, ts) | Task 4 |
| §4 `LocationStore` single JSON `CONFIG_DIR/locations.json`, atomic+`.bak`, `MAX_LOCATIONS=50` | Task 4 |
| §4 Four routes gated `config.site_optics`; 409 name_collision{id}/library_full, 404 unknown, PUT rename collision vs OTHER ids | Task 4 |
| §4 Apply is client-side; horizon included in PUT only when location carries one AND principal holds `config.safety` | Task 5 (`saveSite` param) + Task 7 (apply logic) |
| §4 Library never in config/status/summary/hello/WS; names/coords never in `bus.log` | Task 4 (payload-absence + log-hygiene tests) |
| §4 No profile↔location wiring | Out of scope — untouched |
| §5 `SitePanel.tsx` in Connect tab between DriversPanel and SkyAtlasPanel, DriversPanel idiom | Task 6 |
| §5 Fields name + lat mag+N/S + lon mag+E/W + elevation; boundary conversion via `lib/site.ts` | Tasks 5, 6 |
| §5 `config.site` authoritative; "Hidden" placeholder via `useCanViewSitePrecise`; is_default warn chip | Tasks 5, 6 |
| §5 Save = PUT `{site, version}` + loadConfig(); 409 → reload + toast; horizon never sent from manual edits | Task 6 |
| §5 "Use my location" gated on `isSecureContext`+geolocation else hint; `getCurrentPosition` high-accuracy 10s → 6dp drafts | Task 6 |
| §5 "Use mount GPS" → `getMountGps`; unavailable → info toast; else fill drafts | Task 6 |
| §5 Saved-locations row: dropdown sorted case-insensitively, Apply+baseline, dirty via `locationEquals`, muted note, Save current… (409 overwrite/full), Delete (ConfirmDialog), holders only | Task 7 |
| §5 `SavedLocation` type + `listLocations/saveLocation/updateLocation/deleteLocation` wrappers | Task 5 |
| §5 Types: strippable fields optional on Site/SiteInfo/RigStatus.site; PreflightStrip `?? "hidden"`; AtlasView kept fields; compiler sweep | Task 5 |
| §6 Server tests (T-RBAC-13 strip, site/sky, visibility, mount-gps, relay strip/full/downgrade, locations store+RBAC+absence, log hygiene) | Tasks 1-4 |
| §6 UI `site.test.ts` self-executing + `npm run build` green | Tasks 5, 6, 7 |
| §7 Out of scope (weather/cloud/radar, D, horizon editing, profile wiring, relay server, role→cap changes) | Not implemented — respected throughout |
| §8 Signed East-positive convention; strip/retain sets exact; caps unchanged; redaction only in `redact.py`; no coords in `bus.log`; future-events contract comment; night-safe tokens; commit trailers; do-not-touch list | Tasks 1 (seam+comment), 3-4 (log hygiene), 5-7 (tokens/convention), all (trailers) |
