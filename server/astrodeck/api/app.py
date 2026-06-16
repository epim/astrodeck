"""FastAPI application: REST command surface + WebSocket event stream.

Quick queries answer inline. Long operations (slews, autofocus, sequences,
centering) start a named background task and stream progress over the
WebSocket — the UI is event-driven.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..alerting import AlertDispatcher
from ..catalog import search_catalog
from ..catalog.survey import router as survey_router
from ..catalog.framing import router as framing_router
from ..catalog.visibility import router as visibility_router
from ..config import (AlertSink, ConfigVersionConflict, EscalationConfig,
                      Optics, SafetyConfig, Site, config_store, redacted)
from ..devices import alpaca as alpaca_backend
from ..devices.base import DeviceError
from ..devices.nina import discover_nina
from ..events import bus
from ..focus import run_autofocus
from ..hub import TOUCH_MAX_RATE_DEG_S, hub
from ..plans import PLAN_SCHEMA, plan_library
from ..profiles import Profile, profiles
from ..sequence import SequenceEngine, SequencePlan
from ..sequence import schedule as schedule_mod
from ..sequence.report import SessionReporter, _slug

engine = SequenceEngine(hub)

# Module-level outbound-alert dispatcher (Batch 4b §1.8). Reads the LIVE config
# through ``config_store.cfg`` so an alert-sink edit is picked up without a
# restart. Its long-running ``run()`` loop is launched in the app lifespan.
dispatcher = AlertDispatcher(bus, lambda: config_store.cfg())
# Inject the dispatcher into the engine so its per-frame loop can ping the
# external dead-man's-switch + emit progress heartbeats (§1.8/§1.9-F). Done by
# injection (not an import inside the engine) to avoid a circular import.
engine.dispatcher = dispatcher

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """App lifespan: start the AlertDispatcher subscriber on boot, cancel it on
    shutdown. The dispatcher never raises out of its loop, so a flaky alert
    endpoint can never take the server down (C1-16/C2-10)."""
    task = asyncio.create_task(dispatcher.run())
    try:
        yield
    finally:
        await dispatcher.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _spawn(name: str, coro) -> dict:
    """Run a long operation as a named background task (one per name)."""
    existing = hub._busy.get(name)
    if existing and not existing.done():
        raise HTTPException(409, f"'{name}' is already running")

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", f"{name} cancelled", name)
        except (DeviceError, Exception) as e:
            bus.log("error", f"{name} failed: {e}", name)

    hub._busy[name] = asyncio.create_task(wrapped())
    return {"started": name}


def _err(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


def _place_hint(lat: float, lon: float) -> str:
    """Coarse, offline hemisphere/longitude label — the novice sanity check that
    catches a flipped sign without any network tile fetch. Used only if the
    coords module doesn't ship its own (richer) place_hint."""
    ns = "N hemisphere" if lat >= 0 else "S hemisphere"
    ew = "E longitude" if lon >= 0 else "W longitude"
    return f"{ns} · {ew}"


def _profile_exists(profile_id: str) -> bool:
    """True only if a stored profile with this id already exists. Any lookup
    failure — missing file, or the FIX-A path-traversal guard raising — counts
    as "does not exist", so a malicious id can never be honored as an upsert."""
    try:
        profiles.get(profile_id)
        return True
    except Exception:
        return False


def _plan_exists(plan_id: str) -> bool:
    """True only if a stored plan with this id already exists (see
    ``_profile_exists`` for the traversal-safe rationale)."""
    try:
        plan_library.get(plan_id)
        return True
    except Exception:
        return False


# ------------------------------------------------------------ request models

class AlpacaConnectBody(BaseModel):
    role: Literal["camera", "telescope", "focuser", "filterwheel",
                  "switch", "safety"]
    host: str
    port: int
    dev_type: str
    dev_num: int
    name: str = ""


class CaptureBody(BaseModel):
    exposure_s: float = 1.0
    gain: int = 100
    offset: int = 30
    binning: int = 1
    save: bool = False
    target: str = ""
    frame_type: str = "Light"


class GotoBody(BaseModel):
    ra_hours: float
    dec_deg: float
    center: bool = True
    force: bool = False


class MoveAxisBody(BaseModel):
    # F-S6 (safety-of-motion): constrain to the two real axes. A mistyped axis
    # must 422 here — never silently command Alpaca DEC (axis!='ra' → 1) while
    # arming the deadman against a name the watchdog can't zero.
    axis: Literal["ra", "dec"]
    rate_deg_s: float


class FocuserMoveBody(BaseModel):
    position: int


class AutofocusBody(BaseModel):
    exposure_s: float = 2.0
    gain: int = 120
    step: int = 350
    steps_each_side: int = 4


class FilterBody(BaseModel):
    position: int


class SwitchBody(BaseModel):
    port_id: int
    value: float


class CoolerBody(BaseModel):
    on: bool
    target_c: float | None = None


class DewBody(BaseModel):
    power: int = 0


class PHD2Body(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4400


class NinaConnectBody(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1888


class DitherBody(BaseModel):
    pixels: float = 3.0


class SiteSaveBody(BaseModel):
    site: Site
    version: int | None = None
    # onboarding's per-site minimum-altitude horizon; persisted alongside site so
    # below-horizon GOTO guarding has a real number. Optional for back-compat.
    horizon_min_deg: float | None = None


class OpticsSaveBody(BaseModel):
    optics: Optics
    version: int | None = None


class ProfileCaptureBody(BaseModel):
    name: str


class ProfileRenameBody(BaseModel):
    name: str


class ProfileApplyBody(BaseModel):
    force: bool = False


class PlanSaveBody(BaseModel):
    plan: SequencePlan
    id: str | None = None
    overwrite: bool = False


class StartSequenceBody(SequencePlan):
    # F-P1.6: the run body is the plan fields PLUS a force flag, FLAT (the UI
    # posts `{ ...plan, force }`, matching the flat GOTO body convention where
    # ra_hours/dec_deg/force all sit at the top level). Subclassing SequencePlan
    # keeps every plan field bindable while adding `force`; an old bare-plan body
    # (no force) still binds with force defaulting False.
    force: bool = False


# ------------------------------------------------- automation request models (4b)

class ConfigPatchBody(BaseModel):
    """Partial-merge config update (§1.10 POST /api/config). Every block is
    optional so the SettingsView can debounce-PUT only the panel that changed;
    an omitted block is left untouched. ``site`` rides through ``set_site`` so it
    still flips ``is_default`` off and re-reads onto the mount."""
    site: Site | None = None
    safety: SafetyConfig | None = None
    escalation: EscalationConfig | None = None
    alerts: list[AlertSink] | None = None
    deadman_url: str | None = None


class SafetySimulateBody(BaseModel):
    unsafe: bool = True
    reason: str = "simulated unsafe condition"


# ------------------------------------------------------------ optional auth (P0-4)
# OPTIONAL shared-token auth, OFF BY DEFAULT. The token is read from the
# ``ASTRODECK_TOKEN`` env var. When it is UNSET (or empty), the server behaves
# EXACTLY as before — fully open — so the live LAN tablet keeps working with no
# change. When a token IS set, every REST request and the WebSocket must present
# it (``X-Auth-Token`` header, ``Authorization: Bearer <token>``, or ``?token=``
# query) or get a 401. This is deliberately a single shared secret, not a user
# system: it is the minimum bar to keep a remote/untrusted-network deployment
# from being wide open. For a real remote observatory, ALSO put AstroDeck behind
# a TLS reverse proxy (see docs/SECURITY.md).
#
# Endpoints intentionally left open even when a token is set: none of the control
# surface. Only the SPA shell + static assets are served openly so a browser can
# load the login-less UI and then attach the token to its API/WS calls.

AUTH_ENV_VAR = "ASTRODECK_TOKEN"

# Path prefixes that stay open even when a token is configured, so the browser can
# fetch the UI bundle before it knows the token. The API + WS are NEVER in here.
_AUTH_OPEN_PREFIXES = ("/assets",)
_AUTH_OPEN_EXACT = {"/", "/index.html", "/favicon.ico", "/manifest.json"}


def auth_token() -> str:
    """The configured shared token, or '' when auth is disabled (the default).

    Read live from the environment so a token set before launch is honored and
    tests can monkeypatch ``os.environ`` per-app. Whitespace is stripped so a
    stray newline in a launcher script can't create a token nobody can type."""
    return (os.environ.get(AUTH_ENV_VAR) or "").strip()


def auth_enabled() -> bool:
    return bool(auth_token())


def _present_token(*, header: str | None, authorization: str | None,
                   query: str | None) -> str | None:
    """Pull the caller-supplied token from any of the accepted carriers."""
    if header:
        return header
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if query:
        return query
    return None


def _token_ok(supplied: str | None) -> bool:
    """Constant-time compare of a supplied token against the configured one.

    When auth is disabled this always returns True (open). When enabled, a
    missing/empty supplied token fails closed."""
    expected = auth_token()
    if not expected:
        return True
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _path_is_open(path: str) -> bool:
    """A REST/static path that is reachable WITHOUT a token even when auth is on
    (the UI shell + static assets only — never /api or /ws). SPA client-side
    routes (no file extension, not under /api) also fall through to index.html,
    so they are treated as open shell loads too."""
    if path in _AUTH_OPEN_EXACT:
        return True
    if any(path == p or path.startswith(p + "/") for p in _AUTH_OPEN_PREFIXES):
        return True
    if path.startswith("/api") or path.startswith("/ws"):
        return False
    # A non-API path with no extension is an SPA deep link → index.html shell.
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return True
    return False


def create_app() -> FastAPI:
    app = FastAPI(title="AstroDeck", version="0.1.0", lifespan=_lifespan)

    # Optional shared-token gate (P0-4). A pure pass-through when ASTRODECK_TOKEN
    # is unset, so default LAN behavior is byte-for-byte unchanged.
    @app.middleware("http")
    async def _auth_mw(request, call_next):
        if auth_enabled() and not _path_is_open(request.url.path):
            supplied = _present_token(
                header=request.headers.get("x-auth-token"),
                authorization=request.headers.get("authorization"),
                query=request.query_params.get("token"))
            if not _token_ok(supplied):
                return JSONResponse(
                    {"detail": "missing or invalid auth token"}, status_code=401)
        return await call_next(request)

    # --------------------------------------------------------- atlas routers
    # The Sky-Atlas feature lanes own these as separate APIRouter modules
    # (survey cutout proxy / mosaic compute / visibility ephemeris). Registered
    # here so no two owners edit the same function; /api/optics already exists
    # below (not duplicated here).
    app.include_router(survey_router)
    app.include_router(framing_router)
    app.include_router(visibility_router)

    # ------------------------------------------------------------ equipment

    @app.get("/api/discover")
    async def discover():
        return await alpaca_backend.discover()

    @app.get("/api/discover/nina")
    async def discover_nina_instances(host: str = "", port: int = 1888):
        extra = [host] if host else None
        return await discover_nina(port=port, extra_hosts=extra)

    @app.get("/api/discover/alpaca")
    async def discover_alpaca_one(host: str, port: int = 11111):
        """Server-side proxy for a manual Alpaca host/port scan (the browser
        can't do this directly — CORS). 502 with a differentiated cause so a
        beginner who typo'd the IP gets a useful message."""
        # SSRF guard (FIX-A): reject loopback/private/link-local/metadata hosts,
        # malformed host strings and bad ports before any outbound request. A
        # rejected host raises AlpacaScanError, handled identically below.
        try:
            validate = getattr(alpaca_backend, "validate_scan_host", None)
            if callable(validate):
                validate(host, port)
            return await alpaca_backend.query_server(host, port)
        except alpaca_backend.AlpacaScanError as e:
            # do NOT echo any upstream HTTP status here — that turned the 502
            # into a port/host scan oracle. The differentiated message already
            # lives in AlpacaScanError; surface only that.
            raise HTTPException(502, str(e))

    @app.get("/api/nina/health")
    async def nina_health():
        """Backend↔NINA link health (same shape as the ``nina_link`` block on
        the status event). For tests + a future Rig-page readout."""
        st = await hub.poll_status()
        return st.get("nina_link", {"active": False, "last_ok_age_s": None,
                                    "last_error": None, "healthy": False,
                                    "warming_up": False})

    @app.post("/api/connect/sim")
    async def connect_sim():
        return await hub.connect_sim()

    @app.post("/api/connect/alpaca")
    async def connect_alpaca(body: AlpacaConnectBody):
        # The 'safety' role gates the fail-closed SafetyMonitor poller, so a
        # non-safetymonitor device must not be allowed to occupy it (a wrong
        # device there would poll as a permanent cryptic UNSAFE/stale read).
        if body.role == "safety" and body.dev_type.lower() != "safetymonitor":
            raise HTTPException(
                422, "role 'safety' requires dev_type 'safetymonitor'")
        try:
            return await hub.connect_alpaca_device(
                body.role, body.host, body.port, body.dev_type, body.dev_num,
                body.name or f"{body.dev_type} #{body.dev_num}")
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/connect/phd2")
    async def connect_phd2(body: PHD2Body):
        try:
            await hub.connect_phd2(body.host, body.port)
            return {"connected": True}
        except Exception as e:
            raise _err(e)

    @app.post("/api/connect/nina")
    async def connect_nina(body: NinaConnectBody):
        try:
            return await hub.connect_nina(body.host, body.port)
        except DeviceError as e:
            raise _err(e)
        except Exception as e:
            raise HTTPException(502, f"NINA connection failed: {e}")

    @app.post("/api/disconnect")
    async def disconnect():
        if engine.running:
            await engine.abort()
        await hub.disconnect_all()
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return await hub.poll_status()

    @app.get("/api/summary")
    async def summary():
        return hub.summary()

    # ------------------------------------------------------ config / site / optics

    def _config_payload() -> dict:
        """REDACTED AppConfig dump + the server's computed optics readout (single
        source of truth for image-scale / FOV the UI never re-derives as logic).

        ``redacted`` blanks every alert sink's Telegram bot token — the only
        secret kept at rest — so the union sent over REST/WS never leaks it (4b
        §1.10). The union also carries the automation blocks (safety/escalation/
        alerts/deadman_url) appended to AppConfig."""
        cfg = config_store.cfg()
        return redacted(cfg) | {"optics_computed": hub.effective_optics()}

    def _preflight_alt(ra_hours: float, dec_deg: float) -> dict:
        """Live altitude verdict for a target from the current site. Returns
        ``unknown`` when the site is still the default (no trustworthy answer)."""
        from ..catalog import altaz
        site = hub.site
        is_default = bool(site.get("is_default", True))
        horizon_min = float(site.get("horizon_min_deg", 15.0))
        alt, az = altaz(ra_hours, dec_deg, site["latitude"], site["longitude"])
        if is_default:
            verdict = "unknown"
        elif alt < 0:
            verdict = "below"
        elif alt < horizon_min:
            verdict = "low"
        else:
            verdict = "ok"
        return {"alt": round(alt, 1), "az": round(az, 1), "verdict": verdict,
                "horizon_min_deg": horizon_min, "site_is_default": is_default}

    def _horizon_block(ra_hours: float, dec_deg: float) -> dict | None:
        """Return a 409 detail dict if a GOTO should be blocked (configured site
        AND the target is below the true horizon), else None. Default site never
        blocks — we don't trust an un-set location to refuse a slew."""
        # Prefer the hub's own check if it exists (keeps one source of truth).
        check = getattr(hub, "_check_horizon", None)
        if callable(check):
            try:
                verdict = check(ra_hours, dec_deg)
            except (DeviceError, ValueError) as e:
                return {"detail": str(e), "code": "below_horizon"}
            # a falsy/None return means "ok"; a dict/str means blocked
            if verdict:
                if isinstance(verdict, dict):
                    return verdict
                return {"detail": str(verdict), "code": "below_horizon"}
            return None
        pf = _preflight_alt(ra_hours, dec_deg)
        if pf["verdict"] == "below":
            return {"detail": f"target is below the horizon (alt {pf['alt']}°)",
                    "code": "below_horizon", "preflight": pf}
        return None

    def _merge_alert_verified(incoming: list[AlertSink]) -> list[AlertSink]:
        """Reset ``verified`` to False on any sink whose delivery identity
        (url/token/chat_id/kind) changed vs. the stored copy, so a re-pointed
        channel must be re-tested before its "verified" badge returns (C1-16).
        A brand-new id keeps whatever ``verified`` it arrived with (False by the
        model default)."""
        existing = {s.id: s for s in config_store.cfg().alerts}
        out: list[AlertSink] = []
        for sink in incoming:
            old = existing.get(sink.id)
            # an empty (redacted) token on update means "unchanged" — never blank
            # a stored secret just because the client echoed back the blanked
            # token. Restore it BEFORE the identity-change check so an empty token
            # also doesn't spuriously trip the verified reset.
            if old is not None and not sink.token and old.token:
                sink = sink.model_copy(update={"token": old.token})
            if old is not None and (
                    old.url != sink.url or old.token != sink.token
                    or old.chat_id != sink.chat_id or old.kind != sink.kind):
                sink = sink.model_copy(update={"verified": False})
            out.append(sink)
        return out

    def _persist_config_patch(body: ConfigPatchBody) -> None:
        """Apply a partial-merge config update through the typed ConfigStore
        setters (each bumps version + writes atomically). Runs on a worker thread
        — the disk writes must not block the event loop."""
        if body.site is not None:
            # set_site flips is_default off, persists elevation_m, and preserves
            # the stored per-site horizon (the TS Site omits it).
            site = body.site.model_copy(update={
                "horizon_min_deg": config_store.cfg().site.horizon_min_deg})
            config_store.set_site(site)
        if body.safety is not None:
            config_store.set_safety(body.safety)
        if body.escalation is not None:
            config_store.set_escalation(body.escalation)
        if body.alerts is not None:
            config_store.set_alerts(_merge_alert_verified(body.alerts))
        if body.deadman_url is not None:
            # The deadman url is now REDACTED outbound (P2-12) — it can carry a
            # per-ping secret in its path/query. So an empty string on update means
            # "unchanged" (the client echoed back the blanked value), exactly like
            # the alert-token guard above: never wipe a stored deadman just because
            # the redacted client round-tripped it. To truly clear it the UI POSTs
            # a dedicated clear (handled at the /api/config layer if needed).
            if body.deadman_url or not config_store.cfg().deadman_url:
                config_store.set_deadman(body.deadman_url)

    @app.get("/api/config")
    async def get_config():
        return _config_payload()

    @app.post("/api/config")
    async def post_config(body: ConfigPatchBody):
        """Partial-merge persist of any subset of the automation config (§1.10).
        Re-reads ``hub.site`` (config-backed property) implicitly, pushes the
        site to the mount when it changed, and broadcasts the redacted union."""
        await asyncio.to_thread(_persist_config_patch, body)
        if body.site is not None:
            push = getattr(hub, "push_site_to_mount", None)
            if callable(push):
                try:
                    await push()
                except Exception as e:
                    bus.log("warning", f"could not push site to mount: {e}",
                            "config")
        bus.publish("config", config=redacted(config_store.cfg()))
        return _config_payload()

    @app.put("/api/site")
    async def put_site(body: SiteSaveBody):
        site = body.site
        # onboarding: an optional per-site minimum-altitude horizon rides the
        # save. The Site model carries the field; merge it in before persisting.
        if body.horizon_min_deg is not None:
            site = site.model_copy(update={"horizon_min_deg": body.horizon_min_deg})
        else:
            # P2-1: the round-tripped TS Site omits horizon_min_deg, so an echoed
            # body would let pydantic default it back to 15.0 and silently wipe a
            # custom minimum altitude. Preserve the stored value when the body
            # carries no explicit override.
            site = site.model_copy(update={
                "horizon_min_deg": config_store.cfg().site.horizon_min_deg})
        try:
            # set_site flips is_default off (a user-saved site is, by definition,
            # no longer the default) and bumps the version atomically.
            cfg = await asyncio.to_thread(config_store.set_site, site, body.version)
        except ConfigVersionConflict as e:
            # optimistic-concurrency mismatch — hand back current so the UI can
            # reconcile rather than silently clobber a co-user's field.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": e.current.model_dump() | {
                    "optics_computed": hub.effective_optics()}})
        push = getattr(hub, "push_site_to_mount", None)
        if callable(push):
            try:
                await push()
            except Exception as e:
                bus.log("warning", f"could not push site to mount: {e}", "config")
        bus.publish("config", version=cfg.version)
        return _config_payload()

    # thin alias kept for backward compat (referenced nowhere in UI, cheap)
    @app.post("/api/site")
    async def post_site(body: SiteSaveBody):
        return await put_site(body)

    @app.put("/api/optics")
    async def put_optics(body: OpticsSaveBody):
        try:
            # P2-3: offload the blocking disk write off the event loop.
            cfg = await asyncio.to_thread(
                config_store.set_optics, body.optics, body.version)
        except ConfigVersionConflict as e:
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": e.current.model_dump() | {
                    "optics_computed": hub.effective_optics()}})
        bus.publish("config", version=cfg.version)
        return _config_payload()

    # atlas alias: also seeds hub.optics (same persisted object)
    @app.post("/api/optics")
    async def post_optics(body: OpticsSaveBody):
        return await put_optics(body)

    @app.get("/api/site/sky")
    async def site_sky(lat: float | None = None, lon: float | None = None):
        from ..catalog import coords
        s = config_store.cfg().site
        latitude = s.latitude if lat is None else lat
        longitude = s.longitude if lon is None else lon
        sun = coords.sun_altaz(latitude, longitude)
        sun_alt = sun[0] if isinstance(sun, (tuple, list)) else float(sun)
        window = coords.dark_window(latitude, longitude)
        place_fn = getattr(coords, "place_hint", None)
        hint = place_fn(latitude, longitude) if callable(place_fn) \
            else _place_hint(latitude, longitude)
        return {
            "sun_alt_deg": round(sun_alt, 1),
            "dark_window": window,
            "place_hint": hint,
            "lst_str": coords.format_ra(coords.lst_hours(longitude)),
        }

    # ------------------------------------------------------------------- safety

    @app.get("/api/safety/state")
    async def safety_state():
        """``{connected, reading|null, streak, stale}`` for the Monitor/Settings
        safety widget. ``reading`` is the hub's CACHED own-cadence read (never an
        inline ``is_safe()`` — C1-12); ``streak`` is the engine's consecutive
        same-verdict count (the gate's hysteresis), read defensively so this lane
        stays decoupled from the engine lane landing its counters."""
        reading = await hub.safety_reading()
        reading_dict = hub._safety_reading_dict(reading) if reading else None
        stale = bool(reading.stale) if reading else False
        # The engine accumulates _unsafe_streak/_safe_streak as the gate's
        # hysteresis; surface whichever matches the current verdict (0 if the
        # engine isn't running / hasn't landed its counters yet).
        if reading is not None and not reading.is_safe:
            streak = int(getattr(engine, "_unsafe_streak", 0) or 0)
        else:
            streak = int(getattr(engine, "_safe_streak", 0) or 0)
        return {
            "connected": hub.safety is not None
            and getattr(hub.safety, "connected", False),
            "reading": reading_dict,
            "streak": streak,
            "stale": stale,
        }

    @app.post("/api/safety/simulate")
    async def safety_simulate(body: SafetySimulateBody):
        """Sim-only safety injection (404 unless ``mode == 'sim'``). Flips the
        simulated SafetyMonitor so the unattended gate can be exercised end-to-end
        without real weather. The own-cadence poller picks the new verdict up on
        its next tick; we return the immediate reading for the test/UX."""
        if hub.mode != "sim":
            raise HTTPException(404, "safety simulation is sim-only")
        mon = hub.safety
        if mon is None:
            raise HTTPException(409, "no safety monitor connected")
        force_unsafe = getattr(mon, "force_unsafe", None)
        force_safe = getattr(mon, "force_safe", None)
        if not callable(force_unsafe) or not callable(force_safe):
            raise HTTPException(409, "connected monitor is not simulatable")
        if body.unsafe:
            force_unsafe(body.reason)
        else:
            force_safe()
        reading = await mon.reading()
        return {"ok": True, "reading": hub._safety_reading_dict(reading)}

    # ------------------------------------------------------------------- alerts

    @app.get("/api/alerts")
    async def list_alerts():
        """Configured alert sinks with the Telegram token blanked (never sent to
        the client — the only secret kept at rest)."""
        return [
            s.model_copy(update={"token": ""}).model_dump()
            for s in config_store.cfg().alerts
        ]

    @app.post("/api/alerts")
    async def upsert_alert(sink: AlertSink):
        """Upsert one alert sink by id. ``verified`` is reset to False whenever the
        delivery identity (url/token/chat_id/kind) changes vs. the stored copy, so
        a re-pointed channel must be re-tested (C1-16). Returns the full sink list
        (tokens blanked)."""
        alerts = list(config_store.cfg().alerts)
        idx = next((i for i, s in enumerate(alerts) if s.id == sink.id), None)
        if idx is not None:
            old = alerts[idx]
            if (old.url != sink.url or old.token != sink.token
                    or old.chat_id != sink.chat_id or old.kind != sink.kind):
                sink = sink.model_copy(update={"verified": False})
            # an empty token on update means "unchanged" — never blank a stored
            # secret just because the redacted client echoed it back.
            if not sink.token and old.token:
                sink = sink.model_copy(update={"token": old.token})
            alerts[idx] = sink
        else:
            alerts.append(sink)
        await asyncio.to_thread(config_store.set_alerts, alerts)
        bus.publish("config", config=redacted(config_store.cfg()))
        return [s.model_copy(update={"token": ""}).model_dump()
                for s in config_store.cfg().alerts]

    @app.delete("/api/alerts/{sink_id}")
    async def delete_alert(sink_id: str):
        alerts = [s for s in config_store.cfg().alerts if s.id != sink_id]
        await asyncio.to_thread(config_store.set_alerts, alerts)
        bus.publish("config", config=redacted(config_store.cfg()))
        return {"deleted": sink_id}

    @app.post("/api/alerts/{sink_id}/test")
    async def test_alert(sink_id: str):
        """Real round-trip test of one sink (§1.8). On a genuine 2xx the sink's
        ``verified`` flag flips True and is persisted; otherwise the error is
        returned for the UI to surface. 404 when the id is unknown."""
        if not any(s.id == sink_id for s in config_store.cfg().alerts):
            raise HTTPException(404, "no such alert sink")
        result = await dispatcher.test(sink_id)
        # dispatcher.test set sink.verified on the live cfg object in memory; the
        # caller persists it (and the redacted broadcast reflects the new badge).
        await asyncio.to_thread(config_store.set_alerts,
                                list(config_store.cfg().alerts))
        bus.publish("config", config=redacted(config_store.cfg()))
        return result

    # ------------------------------------------------------------------- reports

    @app.get("/api/reports")
    async def list_reports():
        """Newest-first session-report summaries (no frame detail)."""
        return await asyncio.to_thread(SessionReporter.list_reports)

    @app.get("/api/reports/{report_id}")
    async def get_report(report_id: str):
        """One report + read-time-derived trend sparklines (404 if missing). The
        trends are computed from the frame records on read, never stored as
        parallel arrays that could drift (C1-19)."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        trends = SessionReporter.trends(report)
        return report.model_dump() | {"trends": trends}

    @app.get("/api/reports/{report_id}/frames.csv")
    async def report_frames_csv(report_id: str):
        """Append-only frame list as CSV (power-user export). 404 if missing."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        buf = io.StringIO()
        cols = ["ts", "target", "filter", "frame_type", "exposure_s", "accepted",
                "hfr", "sensor_temp_c", "guide_rms_total", "saved_path"]
        import csv
        w = csv.writer(buf)
        w.writerow(cols)
        for fr in report.frames:
            d = fr.model_dump()
            w.writerow(["" if d.get(c) is None else d.get(c) for c in cols])
        # Use the sanitized slug (not the raw path param) so the response header
        # can never carry CR/LF/quotes from attacker-controlled input.
        fname = f"{_slug(report_id)}.frames.csv"
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})

    # ----------------------------------------------------------------- profiles

    @app.get("/api/profiles")
    async def list_profiles():
        return profiles.list(config_store.cfg().active_profile_id)

    @app.get("/api/profiles/{profile_id}")
    async def get_profile(profile_id: str):
        try:
            return profiles.get(profile_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")

    @app.post("/api/profiles")
    async def save_profile(profile: Profile):
        # P0: never trust a client-supplied id for a NEW record (path-traversal
        # / arbitrary-file-write vector). Only honor the id as an upsert when a
        # file for it already exists; otherwise mint a fresh server-side uuid.
        if not _profile_exists(profile.id):
            profile = profile.model_copy(update={"id": str(uuid4())})
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        row = await asyncio.to_thread(profiles.save, profile)
        if callable(invalidate):
            invalidate()
        return row

    @app.post("/api/profiles/capture")
    async def capture_profile(body: ProfileCaptureBody):
        if hub.mode == "none" or not hub.devices:
            raise HTTPException(409, "connect a rig before saving a profile")
        return await hub.capture_profile(body.name)

    @app.patch("/api/profiles/{profile_id}")
    async def rename_profile(profile_id: str, body: ProfileRenameBody):
        try:
            row = await asyncio.to_thread(profiles.rename, profile_id, body.name)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        return row

    @app.delete("/api/profiles/{profile_id}")
    async def delete_profile(profile_id: str):
        await asyncio.to_thread(profiles.delete, profile_id)
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        return {"deleted": profile_id}

    @app.post("/api/profiles/{profile_id}/apply")
    async def apply_profile(profile_id: str, body: ProfileApplyBody | None = None):
        force = bool(body and body.force)
        try:
            prof = profiles.get(profile_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        # Apply is destructive (disconnects the current rig). Refuse if anything
        # is actively running unless forced; the engine is aborted app-side.
        if (engine.running or hub.looping or hub.polar.running) and not force:
            raise HTTPException(409, detail={
                "detail": "a sequence, capture loop or polar alignment is running",
                "code": "running"})
        if force and engine.running:
            await engine.abort()
        return _spawn("profile", hub.apply_profile(prof))

    # -------------------------------------------------------------------- plans

    @app.get("/api/plans")
    async def list_plans():
        return plan_library.list()

    @app.get("/api/plans/{plan_id}")
    async def get_plan(plan_id: str):
        try:
            return plan_library.get(plan_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")

    @app.post("/api/plans")
    async def save_plan(body: PlanSaveBody):
        # P0: only honor a client id as an upsert when that plan already exists;
        # otherwise mint the uuid server-side (None → library mints) so a crafted
        # id can never write outside the plans dir or clobber an arbitrary file.
        plan_id = body.id if (body.id is not None and _plan_exists(body.id)) else None
        if plan_id is None and not body.overwrite and \
                plan_library.name_exists(body.plan.name, None):
            raise HTTPException(409, detail={
                "detail": f"a plan named '{body.plan.name}' already exists",
                "code": "name_collision"})
        return await asyncio.to_thread(plan_library.save, body.plan, plan_id)

    @app.delete("/api/plans/{plan_id}")
    async def delete_plan(plan_id: str):
        await asyncio.to_thread(plan_library.delete, plan_id)
        return {"deleted": plan_id}

    @app.get("/api/plans/{plan_id}/export")
    async def export_plan(plan_id: str):
        try:
            raw = plan_library.export_bytes(plan_id)
            name = plan_library.get(plan_id).name or plan_id
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "plan"
        return Response(raw, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{safe}.astroplan.json"'})

    @app.post("/api/plans/import")
    async def import_plan(raw: dict):
        # Distinguish "exported by a newer AstroDeck" from genuinely-invalid so
        # the UI maps the two codes to different copy (C1-E19).
        # P2-9: a malformed schema_version (string/list) must be a clean 422, not
        # an uncaught 500 from the int() coercion below.
        try:
            ver = int(raw.get("schema_version", 1) or 1)
        except (TypeError, ValueError):
            raise HTTPException(422, detail={
                "detail": "invalid schema_version", "code": "invalid"})
        if ver > PLAN_SCHEMA:
            raise HTTPException(422, detail={
                "detail": "This plan was exported by a newer AstroDeck version",
                "code": "version_too_new"})
        try:
            return await asyncio.to_thread(plan_library.import_plan, raw)
        except HTTPException:
            raise
        except ValueError as e:
            code = getattr(e, "code", "invalid")
            raise HTTPException(422, detail={"detail": str(e), "code": code})
        except Exception as e:
            raise HTTPException(422, detail={"detail": str(e), "code": "invalid"})

    # -------------------------------------------------------------- capture

    @app.post("/api/capture")
    async def capture(body: CaptureBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("capture", hub.capture(
            body.exposure_s, body.gain, body.offset, body.binning,
            save=body.save, target=body.target, frame_type=body.frame_type))

    @app.post("/api/capture/loop")
    async def capture_loop(body: CaptureBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning)
        return {"looping": True}

    @app.post("/api/capture/stop")
    async def capture_stop():
        hub.stop_loop()
        task = hub._busy.get("capture")
        if task and not task.done():
            task.cancel()
        cam = hub.devices.get("camera")
        if cam and cam.connected:
            try:
                await cam.abort_exposure()
            except Exception:
                pass
        return {"looping": False}

    # ---- live-preview routes (Pass 1, live-preview spec §4.4) --------------
    # Canonical URL: the client builds `/api/preview/{id}` and reads `mime` from
    # the event. `/lossless.png` / `/thumb.jpg` / `/fits` / `/png` are the
    # paused-zoom / filmstrip / FITS-download / PNG-download variants. `/crop`
    # and `/render.png` are declared now but stubbed 501 (Pass 2).

    _PREVIEW_CACHE = {"Cache-Control": "max-age=3600"}

    @app.get("/api/preview/{preview_id:int}")
    async def preview_display(preview_id: int):
        """Display bytes for the live loop, with the correct mime (JPEG for the
        linear path, NINA's JPEG verbatim otherwise).

        The ``:int`` path convertor matches digits ONLY, so ``/api/preview/5.png``
        falls through to the ``.png`` compat route below rather than 422-ing here."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        return Response(entry.display, media_type=entry.mime,
                        headers=_PREVIEW_CACHE)

    @app.get("/api/preview/{preview_id}.png")
    async def preview_png_compat(preview_id: int):
        """Back-compat `.png` URL. Returns a REAL PNG (the lossless base) when one
        is held; otherwise 404 so callers fall back to `/`. Never a JPEG-under-
        .png (live-preview spec §4.4)."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        if entry.lossless is not None:
            return Response(entry.lossless, media_type="image/png",
                            headers=_PREVIEW_CACHE)
        if entry.mime == "image/png":
            return Response(entry.display, media_type="image/png",
                            headers=_PREVIEW_CACHE)
        raise HTTPException(404, "no PNG for this frame — use /api/preview/{id}")

    @app.get("/api/preview/{preview_id}/lossless.png")
    async def preview_lossless(preview_id: int):
        """Lossless stretched PNG for the paused/zoomed frame (latest 1–2 only).
        422 when the lossless base is no longer held (Pi memory cap)."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        if entry.lossless is None:
            raise HTTPException(422, "lossless base not held for this frame")
        return Response(entry.lossless, media_type="image/png",
                        headers=_PREVIEW_CACHE)

    @app.get("/api/preview/{preview_id}/thumb.jpg")
    async def preview_thumb(preview_id: int):
        """~160px JPEG thumbnail for the filmstrip (kept for many frames)."""
        thumb = hub.preview_thumbs.get(preview_id)
        if thumb is None:
            entry = hub.previews.get(preview_id)
            thumb = entry.thumb if entry else None
        if not thumb:
            raise HTTPException(404, "thumbnail expired")
        return Response(thumb, media_type="image/jpeg", headers=_PREVIEW_CACHE)

    @app.get("/api/preview/{preview_id}/fits")
    async def preview_fits(preview_id: int):
        """The saved FITS for this frame, but only when it is a real file under
        CAPTURE_DIR (`saved_local`). The guard is the hub's own
        ``_is_local_save`` (resolves + ``is_relative_to(CAPTURE_DIR)``), so a
        crafted path can never escape (live-preview spec §4.4 security)."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        saved_path = entry.meta.get("saved_path")
        if not saved_path or not hub._is_local_save(saved_path):
            raise HTTPException(404, "saved FITS is not available locally")
        p = Path(saved_path).resolve()
        return FileResponse(p, media_type="application/fits", filename=p.name)

    @app.get("/api/preview/{preview_id}/png")
    async def preview_png_download(preview_id: int):
        """Full-res stretched PNG as a download (attachment)."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        png = entry.lossless or (entry.display if entry.mime == "image/png" else None)
        if png is None:
            raise HTTPException(404, "no PNG available for this frame")
        return Response(png, media_type="image/png", headers={
            **_PREVIEW_CACHE,
            "Content-Disposition": f'attachment; filename="preview_{preview_id}.png"'})

    @app.get("/api/preview/{preview_id}/crop")
    async def preview_crop(preview_id: int, x: int = 0, y: int = 0,
                           w: int = 0, h: int = 0):
        """Sensor-1:1 ROI from linear data. Pass 2 — stubbed."""
        raise HTTPException(501, "preview crop is not implemented yet (Pass 2)")

    @app.get("/api/preview/{preview_id}/render.png")
    async def preview_render(preview_id: int, black: float = 0.0,
                             mid: float = 0.5, white: float = 1.0):
        """Server-side baked stretch / export. Pass 2 — stubbed."""
        raise HTTPException(501, "server render is not implemented yet (Pass 2)")

    @app.post("/api/camera/cooler")
    async def cooler(body: CoolerBody):
        try:
            cam = hub.require("camera")
            await cam.set_cooler(body.on, body.target_c)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/camera/dew-heater")
    async def dew_heater(body: DewBody):
        try:
            cam = hub.require("camera")
            await cam.set_dew_heater(body.power)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # ---------------------------------------------------------------- mount

    @app.post("/api/mount/goto")
    async def goto(body: GotoBody):
        try:
            hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        # Below-horizon guard — only when the user actually configured a site
        # (is_default off) and the target is below the horizon, and only at the
        # GOTO entry (goto_and_center re-slews internally without re-checking).
        if not body.force:
            blocked = _horizon_block(body.ra_hours, body.dec_deg)
            if blocked is not None:
                raise HTTPException(409, detail=blocked)
        if body.center:
            return _spawn("goto", hub.goto_and_center(body.ra_hours, body.dec_deg))

        async def plain_goto():
            tel = hub.require("telescope")
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
            await tel.slew(body.ra_hours, body.dec_deg)
            bus.publish("mount", action="slew_complete")
        return _spawn("goto", plain_goto())

    @app.post("/api/mount/solve_sync")
    async def solve_sync():
        try:
            hub.require("telescope"), hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("solve", hub.solve_and_sync())

    @app.post("/api/mount/move")
    async def move_axis(body: MoveAxisBody):
        try:
            tel = hub.require("telescope")
            # Touch-safety rate clamp: manual slew is capped at ±TOUCH_MAX_RATE
            # (worst-case ≤0.72° uncommanded travel at the 1.2s deadman). Gross
            # repositioning is GOTO's job; no 2-4°/s manual band exists.
            rate = max(-TOUCH_MAX_RATE_DEG_S,
                       min(TOUCH_MAX_RATE_DEG_S, body.rate_deg_s))
            # F-A1 (safety-of-motion): arm the deadman with the actual (clamped)
            # rate BEFORE the move await, so the watchdog already covers the axis
            # if move_axis is cancelled/raises mid-flight (no uncovered moving
            # axis), and the keepalive cadence never inherits driver RTT. Arming
            # first is safe: if move_axis raises, the next tick issues a redundant
            # tel.stop() on a non-moving axis (harmless), and a rate-0 stop leaves
            # the deadman idle so there is no spurious halt.
            hub.note_move(body.axis, rate)
            await tel.move_axis(body.axis, rate)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/stop")
    async def mount_stop():
        try:
            tel = hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        for name in ("goto", "solve"):
            t = hub._busy.get(name)
            if t and not t.done():
                t.cancel()
        await tel.stop()
        # F-S5b (safety hygiene): an explicit/lock STOP just zeroed both axes, so
        # disarm the deadman cleanly. Without this the seen-rates stay stale and
        # the next watchdog tick fires a redundant tel.stop()+warning log against
        # an already-stopped mount.
        hub.note_move("ra", 0.0)
        hub.note_move("dec", 0.0)
        return {"ok": True}

    @app.post("/api/mount/tracking")
    async def tracking(on: bool):
        try:
            tel = hub.require("telescope")
            await tel.set_tracking(on)
            return {"tracking": on}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/park")
    async def park():
        try:
            hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        return _spawn("goto", hub.require("telescope").park())

    @app.post("/api/mount/unpark")
    async def unpark():
        try:
            tel = hub.require("telescope")
            await tel.unpark()
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # -------------------------------------------------------------- focuser

    @app.post("/api/focuser/move")
    async def focuser_move(body: FocuserMoveBody):
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        return _spawn("focuser", foc.move_to(body.position))

    @app.post("/api/focuser/autofocus")
    async def autofocus(body: AutofocusBody):
        try:
            cam = hub.require("camera")
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        return _spawn("autofocus", run_autofocus(
            cam, foc, exposure_s=body.exposure_s, gain=body.gain,
            step=body.step, steps_each_side=body.steps_each_side))

    @app.post("/api/focuser/halt")
    async def focuser_halt():
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        for name in ("focuser", "autofocus"):
            t = hub._busy.get(name)
            if t and not t.done():
                t.cancel()
        await foc.halt()
        return {"ok": True}

    # ---------------------------------------------------------- filterwheel

    @app.post("/api/filterwheel/position")
    async def set_filter(body: FilterBody):
        try:
            fw = hub.require("filterwheel")
        except DeviceError as e:
            raise _err(e)
        return _spawn("filterwheel", fw.set_position(body.position))

    # --------------------------------------------------------------- switch

    @app.get("/api/switch/ports")
    async def switch_ports():
        try:
            sw = hub.require("switch")
            return [p.__dict__ for p in await sw.get_ports()]
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/switch/set")
    async def switch_set(body: SwitchBody):
        try:
            sw = hub.require("switch")
            await sw.set_port(body.port_id, body.value)
            return [p.__dict__ for p in await sw.get_ports()]
        except (DeviceError, RuntimeError) as e:
            raise _err(e)

    # ---------------------------------------------------------------- guide

    @app.post("/api/guide/start")
    async def guide_start():
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        return _spawn("guide", hub.guider.start_guiding())

    @app.post("/api/guide/stop")
    async def guide_stop():
        if not hub.guider:
            raise HTTPException(409, "no guider connected")
        await hub.guider.stop_guiding()
        return {"ok": True}

    @app.post("/api/guide/dither")
    async def guide_dither(body: DitherBody):
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        return _spawn("dither", hub.guider.dither(body.pixels))

    @app.get("/api/guide/frame.png")
    async def guide_frame():
        """Auto-stretched PNG thumbnail of the current guide star (PHD2
        ``get_star_image`` in PHD2/NINA mode, a synthesized frame in sim). Cheap
        and safe: 404 (never 500) when there is no guider or no current star
        image, so the UI degrades to a 'guide camera unavailable' note."""
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(404, "no guider connected")
        try:
            png = await hub.guider.guide_frame()
        except Exception:
            png = None
        if not png:
            raise HTTPException(404, "no guide frame available")
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- sequence

    @app.post("/api/sequence/start")
    async def sequence_start(body: StartSequenceBody):
        # F-P1.6: read plan/force from the JSON BODY (matches the GOTO body
        # convention and the UI's api.post `{ ...plan, force }`, which only ever
        # sends a body). The body IS the plan (StartSequenceBody subclasses
        # SequencePlan) plus a force flag; rebuild a plain plan so engine.start
        # and total_frames never see the extra field. A forced run (operator
        # accepted a low/below-horizon target via the pre-flight gate) must
        # actually bypass the horizon 409 here.
        force = body.force
        plan = SequencePlan.model_validate(
            body.model_dump(exclude={"force"}))
        if not plan.targets or plan.total_frames() == 0:
            raise HTTPException(422, "plan has no frames")
        # Below-horizon pre-flight: refuse to start a run whose target can't be
        # observed from a *configured* site, unless explicitly forced.
        if not force:
            for t in plan.targets:
                # calibration targets (darks/bias/flats) carry mandatory dummy
                # coords and never slew/center — the horizon check is meaningless
                # for them, so a dark-library build at a configured site must not
                # be 409'd just because (0,0) happens to be below the horizon.
                if t.calibration:
                    continue
                ra = getattr(t, "ra_hours", None)
                dec = getattr(t, "dec_deg", None)
                if ra is None or dec is None:
                    continue
                blocked = _horizon_block(ra, dec)
                if blocked is not None:
                    blocked = dict(blocked)
                    blocked["target"] = getattr(t, "name", "")
                    raise HTTPException(409, detail=blocked)
        try:
            hub.require("camera")
            engine.start(plan)
        except DeviceError as e:
            raise _err(e)
        return {"started": True, "frames": plan.total_frames()}

    @app.post("/api/sequence/pause")
    async def sequence_pause():
        engine.pause()
        return {"paused": True}

    @app.post("/api/sequence/resume")
    async def sequence_resume():
        engine.resume()
        return {"paused": False}

    @app.post("/api/sequence/abort")
    async def sequence_abort():
        await engine.abort()
        return {"aborted": True}

    @app.get("/api/sequence/state")
    async def sequence_state():
        return engine.state | {"running": engine.running, "paused": engine.paused}

    # ----------------------------------------------------------------- monitor

    @app.get("/api/monitor/snapshot")
    async def monitor_snapshot():
        """One-shot cold-load hydration for the Monitor view (monitor spec §8).
        Non-fatal: the WS catches up within ~2s, so the view never blocks on it.
        Uses the live engine state (running/paused), not just the last snapshot."""
        snap = await hub.monitor_snapshot()
        snap["sequence"] = engine.state | {
            "running": engine.running, "paused": engine.paused}
        return snap

    @app.get("/api/sequence/preflight")
    async def sequence_preflight(ra_hours: float, dec_deg: float):
        """Live single-target altitude verdict from the current site. Returns
        ``unknown`` while the site is still the default (no trustworthy answer)."""
        return _preflight_alt(ra_hours, dec_deg)

    @app.post("/api/sequence/preflight")
    async def sequence_preflight_plan(plan: SequencePlan):
        """Plan-wide, NON-BLOCKING pre-flight (§1.10 / C2-11). A DIFFERENT route
        from the GET single-target verdict above (same path, different verb — no
        collision). Resolves each non-calibration target's autorun window and
        warns ONLY when a target never rises above the effective floor across its
        whole window (``never_rises``). The UI shows a confirm dialog defaulting to
        "Run anyway"; this endpoint never refuses a run on its own.

        Floor = ``max(per-target start gate, site horizon_min, safety floor)`` —
        the realistic altitude the target must clear to be worth slewing to. A
        default (un-configured) site yields no warnings: we don't trust an un-set
        location to call a target un-observable."""
        import time as _time
        site = hub.site
        cfg = config_store.cfg()
        twilight = float(cfg.safety.twilight_deg)
        site_floor = float(site.get("horizon_min_deg", 0.0))
        safety_floor = float(cfg.safety.min_alt_deg or 0.0)
        is_default = bool(site.get("is_default", True))
        lat = float(site["latitude"])
        lon = float(site["longitude"])
        now = _time.time()
        warnings: list[dict] = []
        if not is_default:
            for t in plan.targets:
                if t.calibration:
                    continue
                gate = float(getattr(t.schedule, "min_altitude_deg", 0.0) or 0.0)
                floor = max(gate, site_floor, safety_floor)
                if floor <= 0.0:
                    continue
                start_ts, stop_ts = schedule_mod.resolve_window(
                    t.schedule, site, twilight, now)
                peak = schedule_mod.target_max_altitude(
                    t.ra_hours, t.dec_deg, lat, lon, start_ts, stop_ts)
                if peak < floor:
                    warnings.append({
                        "target": t.name,
                        "kind": "never_rises",
                        "message": (f"{t.name or 'target'} never rises above "
                                    f"{floor:g} deg during its window "
                                    f"(peaks at {peak:.0f} deg)"),
                    })
        return {"ok": not warnings, "warnings": warnings}

    @app.get("/api/sequence/recoverable")
    async def sequence_recoverable():
        data = engine.load_resume()
        if not data:
            return {"recoverable": False}
        plan = SequencePlan(**data["plan"])
        done = sum(data.get("done", {}).values())
        return {"recoverable": True, "name": plan.name, "frames_done": done,
                "frames_total": plan.total_frames(), "ts": data.get("ts")}

    @app.post("/api/sequence/recover")
    async def sequence_recover():
        data = engine.load_resume()
        if not data:
            raise HTTPException(404, "no resumable sequence found")
        plan = SequencePlan(**data["plan"])
        try:
            hub.require("camera")
            # re-attach the persisted report so the resume keeps appending to the
            # SAME report instead of forking a new one (C2-5).
            engine.start(plan, resume_done=data.get("done", {}),
                         report_id=data.get("report_id"))
        except DeviceError as e:
            raise _err(e)
        done = sum(data.get("done", {}).values())
        return {"resumed": True, "frames_remaining": plan.total_frames() - done}

    # -------------------------------------------------------------- polar align

    @app.post("/api/polar/start")
    async def polar_start():
        try:
            await hub.polar.start()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"started": True, "source": hub.polar.state["source"]}

    @app.post("/api/polar/stop")
    async def polar_stop():
        await hub.polar.stop()
        return {"ok": True}

    @app.post("/api/polar/pause")
    async def polar_pause():
        await hub.polar.pause()
        return {"ok": True}

    @app.post("/api/polar/resume")
    async def polar_resume():
        await hub.polar.resume()
        return {"ok": True}

    @app.get("/api/polar/state")
    async def polar_state():
        return hub.polar.state | {"running": hub.polar.running}

    # -------------------------------------------------------------- catalog

    @app.get("/api/catalog")
    async def catalog(q: str = ""):
        from ..catalog import altaz
        results = search_catalog(q)
        for r in results:
            alt, az = altaz(r["ra_hours"], r["dec_deg"],
                            hub.site["latitude"], hub.site["longitude"])
            r["alt"] = round(alt, 1)
            r["az"] = round(az, 1)
        return results

    @app.get("/api/logs")
    async def logs():
        return bus.log_history

    # ------------------------------------------------------------ websocket

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        # Optional shared-token gate (P0-4). The middleware does not cover the WS
        # upgrade, so check here. When auth is disabled this is a no-op. The
        # browser can't set custom headers on a WebSocket, so the token is taken
        # from the ``?token=`` query (it may also arrive as X-Auth-Token for
        # non-browser clients). On failure close BEFORE accept with 1008
        # (policy violation) so an unauthenticated client never joins the bus.
        if auth_enabled():
            supplied = _present_token(
                header=websocket.headers.get("x-auth-token"),
                authorization=websocket.headers.get("authorization"),
                query=websocket.query_params.get("token"))
            if not _token_ok(supplied):
                await websocket.close(code=1008)
                return
        await websocket.accept()
        q = bus.subscribe()
        try:
            await websocket.send_json({"type": "hello", "data": hub.summary(), "ts": 0})
            while True:
                ev = await q.get()
                await websocket.send_json(ev.to_json())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    # ------------------------------------------------------------ static UI

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            target = UI_DIST / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
