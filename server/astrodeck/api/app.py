"""FastAPI application: REST command surface + WebSocket event stream.

Quick queries answer inline. Long operations (slews, autofocus, sequences,
centering) start a named background task and stream progress over the
WebSocket — the UI is event-driven.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import shutil
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import (Depends, FastAPI, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..alerting import AlertDispatcher
from ..auth import (ALL_CAPS, CAP_ADMIN_USERS, CAP_CONFIG_ALERTS,
                    CAP_VIEW_SITE_DERIVED,
                    CAP_CONFIG_BACKEND, CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                    CAP_CONFIG_SOLAR_OVERRIDE, CAP_CONTROL_CAPTURE,
                    CAP_CONTROL_GUIDE, CAP_CONTROL_MOUNT,
                    CAP_CONTROL_POWER, CAP_SYSTEM_UPDATE, CAP_VIEW_MEDIA,
                    CAP_VIEW_PREVIEW, CAP_VIEW_SITE_PRECISE, CAP_VIEW_STATUS,
                    CAP_VIEW_WEATHER,
                    Principal, _scope_is_remote,
                    configure_provider_from_auth, get_principal, require,
                    resolve_principal)
from ..auth.rbac import assert_route_capabilities, declare
# Site-precision redaction helpers + the WS re-auth cadence live in a neutral,
# import-light module so BOTH the LAN /ws handler (here) and the relay-tunneled
# /ws handler (remote.relay_client) share ONE implementation. They cannot live
# here as nested closures: app.py imports remote.relay_client, so relay_client
# importing them back out of app.py would be a circular import.
from .redact import (WS_AUTH_RECHECK_S, _redact_drivers_for,  # re-exported at module scope
                     _redact_report_for, _redact_session_for, _redact_site_for,
                     _redact_ws_event, report_csv_columns)
from ..persist import safe_id_path, safe_subpath
from ..catalog import search          # rows AND the reasons for what is missing
from ..catalog import survey_pack as survey_pack_mod
from ..catalog.survey import router as survey_router
from ..catalog.tiles import router as tiles_router
from ..catalog.framing import router as framing_router
from ..catalog.visibility import router as visibility_router
from ..config import (AlertSink, AuthConfig, CalibrationConfig,
                      ConfigVersionConflict, CoolingConfig,
                      EscalationConfig, GuideConfig, NamingConfig, Optics,
                      ProvidersConfig, RotatorConfig, SafetyConfig, Site,
                      SurveyConfig, UpdateConfig, WcsStampConfig, WeatherConfig,
                      config_store, redacted)
from ..locations import (LocationLibraryFull, LocationNameCollision,
                         location_store)
from .. import __version__
from ..update.state import update_state
from ..update.service import UpdateError, get_service as get_update_service
from ..devices import alpaca as alpaca_backend
from ..devices.base import DeviceError, TRACKING_RATES
from ..devices.nina import discover_nina
from ..events import LOG_READ_MAX, bus, night_key
from ..focus import run_autofocus
from ..focus.coarse import run_coarse_focus
from .. import hub as hub_module
# Imported as a MODULE (not `from ..config import CONFIG_DIR`) so the factory-
# reset routes read the live `CONFIG_DIR`, honouring a test monkeypatch exactly
# the way `hub_module.CAPTURE_DIR` already is.
from .. import config as config_module
from .. import factory_reset as factory_reset_module
from .. import gallery as gallery_module
from ..hub import CAPTURE_DIR, TOUCH_MAX_RATE_DEG_S, hub
from ..calibration import CalibrationLibrary, MatchTolerance
from ..imaging import build_caption, compose_share_jpeg, fmt_share_date, to_png
from ..naming import sanitize_component
from ..plans import PLAN_SCHEMA, plan_library
from ..profiles import Profile, profiles, redact_profile
from ..provenance import effective_config
from ..rotation import angle_equals, map_sky_target, mod360
from ..sequence import SequenceEngine, SequencePlan
from ..sequence import schedule as schedule_mod
from ..sequence.models import quota_unbounded
from ..sequence.report import SessionReporter, _slug
from ..sequence.bundle import (CalibrationLibraryAdapter, NullMasterLibrary,
                               build_bundle, bundle_materialize_plan,
                               bundle_summary, build_script,
                               manifest_json, readme_text, weights_csv)
from ..sequence.resume_arm import ResumeArm
from ..sequence.session import migrate_legacy_resume, session_store
from ..weather import NoNightError, weather_service

engine = SequenceEngine(hub)

# PRO-1 master calibration library (module singleton). Resolves CAPTURE_DIR LIVE
# through ``hub_module`` (never bound at import) so the test monkeypatch of
# ``hub.CAPTURE_DIR`` is honored, exactly like ``hub._counter_file``. Exposed on
# the hub singleton so PRO-10 (master export) can resolve the same store.
cal_library = CalibrationLibrary(lambda: hub_module.CAPTURE_DIR)
hub.master_library = cal_library

# Module-level outbound-alert dispatcher (Batch 4b §1.8). Reads the LIVE config
# through ``config_store.cfg`` so an alert-sink edit is picked up without a
# restart. Its long-running ``run()`` loop is launched in the app lifespan.
dispatcher = AlertDispatcher(bus, lambda: config_store.cfg())
# Inject the dispatcher into the engine so its per-frame loop can ping the
# external dead-man's-switch + emit progress heartbeats (§1.8/§1.9-F). Done by
# injection (not an import inside the engine) to avoid a circular import.
engine.dispatcher = dispatcher

# Auto-resume-at-dusk service (sessions spec §5). Started in the lifespan, like
# the AlertDispatcher; a disarm or any manual start stops its interest (it
# re-checks state every tick and holds no long-lived assumptions).
resume_arm = ResumeArm(engine, hub, weather=weather_service)

# Gallery trash auto-purge (gallery design §Trash). Same lifespan-owned-task
# shape as the three services above. Deliberately NOT hung off the
# AlertDispatcher's wall-clock loop: that loop drives the external dead-man's
# switch, and a directory walk that stalls it would fire a false "rig is down".
trash_keeper = gallery_module.TrashKeeper()

def _resolve_ui_dist() -> Path:
    """Where the built SPA lives, across every way AstroDeck is shipped.

    This used to be the repo-relative path alone, which is correct for a git
    checkout and for the release tarball (it preserves ``server/`` beside
    ``ui/dist/``) and wrong for every other form: in a container, a wheel, or a
    single-file binary there is no ``ui/`` sibling, so the SPA silently did not
    mount and the server answered API calls while serving no interface.

    Order, first hit wins:
      1. ``ASTRODECK_UI_DIR`` — an explicit override. What the container sets.
      2. ``<package>/webui`` — the SPA copied INSIDE the package, which is how a
         wheel and a PyInstaller bundle carry it (package data travels; a
         sibling directory does not).
      3. the repo / release-tarball layout.
    """
    env = os.environ.get("ASTRODECK_UI_DIR")
    if env:
        return Path(env).expanduser().resolve()
    bundled = Path(__file__).resolve().parents[1] / "webui"
    repo = Path(__file__).resolve().parents[3] / "ui" / "dist"
    has_bundled = (bundled / "index.html").is_file()
    has_repo = (repo / "index.html").is_file()
    if has_bundled and has_repo:
        # BOTH exist, so one of them is stale — take the newer.
        #
        # `webui` is a BUILD ARTIFACT (packaging/build_binary.py copies ui/dist
        # into it) and nothing tracks or refreshes it outside that script. A
        # release staged from a tree that once produced a binary therefore ships
        # an old SPA that silently shadows the freshly-built ui/dist beside it:
        # the server starts, serves a UI, and serves the WRONG one. That cost
        # four rounds of "this Atlas fix didn't work" on 2026-07-30 — the fixes
        # were real and had simply never reached a browser.
        #
        # mtime is the honest tiebreak: whichever build ran last is the one the
        # author meant. Compare index.html, not the directory, because copying
        # into a directory does not always bump its mtime.
        return bundled if (bundled / "index.html").stat().st_mtime >= (
            repo / "index.html").stat().st_mtime else repo
    if has_bundled:
        return bundled
    return repo


UI_DIST = _resolve_ui_dist()

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

# ``WS_AUTH_RECHECK_S`` (the WS re-auth cadence) is re-exported at module scope so
# the LAN /ws handler reads it as a module global and a test can shrink it via
# ``monkeypatch.setattr(app_module, "WS_AUTH_RECHECK_S", ...)``. Its definition
# and the site-precision redaction helpers now live in ``.redact`` (imported at
# the top of this module) -- see that import for why.


# Boot auto-connect opt-out (W1.6 test seam). When ``ASTRODECK_NO_AUTOCONNECT``
# is set to a truthy value, the lifespan does NOT auto-connect the active profile
# on startup. Default (unset) is ENABLED - production always auto-connects.
NO_AUTOCONNECT_ENV_VAR = "ASTRODECK_NO_AUTOCONNECT"


def _boot_autoconnect_disabled() -> bool:
    """True when boot auto-connect is turned off via env (test seam). Any of
    ``1/true/yes/on`` (case-insensitive) disables it; everything else enables."""
    val = (os.environ.get(NO_AUTOCONNECT_ENV_VAR) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """App lifespan: start the AlertDispatcher subscriber on boot, cancel it on
    shutdown. The dispatcher never raises out of its loop, so a flaky alert
    endpoint can never take the server down (C1-16/C2-10).

    Boot auto-connect (W1.6): ALWAYS connect the active profile on startup so a
    rebooted Pi comes back to its rig with no operator action. This is wrapped so
    it can NEVER raise out of the lifespan - an unreachable device degrades into
    the disconnected/retrying state (surfaced via ``backend_links`` /
    ``boot_connect_failed`` on the status poll) instead of bricking the UI. It
    NEVER initiates motion: ``connect_active`` only opens device connections (no
    unpark/slew/track). A persisted in-progress sequence is NOT auto-resumed here
    (W1.6 PAUSED-PENDING-ACK) - the boot path deliberately does not call
    engine.start/resume."""
    # Install the configured auth provider from persisted AuthConfig (W2.3). With
    # the default ``provider="none"`` and no ``admin_token`` this is the open
    # NoneAuthProvider, so behavior stays byte-for-byte today. Never raises out of
    # boot: a bad provider config degrades to open-default rather than bricking.
    try:
        configure_provider_from_auth(config_store.cfg().auth)
        _warn_insecure_session_secret()
    except Exception as e:  # noqa: BLE001 - degrade to open-default, never crash boot
        bus.log("error", f"auth provider init failed (open-default): {e}", "auth")
    # Multi-night sessions (spec §2/§4): migrate the retired single-slot resume
    # file ONCE, then sweep power-cut orphans (active -> dormant) so they are
    # manually resumable + ResumeArm-eligible. Never raises out of boot.
    try:
        migrate_legacy_resume()
        swept = session_store.boot_sweep()
        if swept:
            bus.log("info", f"boot sweep: {swept} orphaned session(s) -> dormant",
                    "sequence")
    except Exception as e:  # noqa: BLE001 - degrade, never crash boot
        bus.log("error", f"session boot sweep failed: {e}", "sequence")
    task = asyncio.create_task(dispatcher.run())
    # Auto-resume-at-dusk service (sessions spec §5) — its own 60s asyncio loop.
    resume_arm.start()
    # Weather forecast poller (weather spec §3) — its own 60 s asyncio loop.
    # Started UNCONDITIONALLY: each tick no-ops unless cfg.weather.enabled AND
    # the site is set, so runtime config toggles take effect within one tick.
    weather_service.start()
    # Gallery trash auto-purge — its own 6 h asyncio loop, first tick immediately
    # so a box that reboots daily still reaches the 30-day horizon. A no-op (one
    # `is_dir()`) until something has actually been deleted.
    trash_keeper.start()
    # W3 scope-side relay dial-out (OPT-IN). Launches ONLY when
    # ``RemoteConfig.enabled`` and a ``relay_url`` are set, so the default config
    # does NOTHING (LAN-only is byte-for-byte today). ISOLATED: the client's run
    # loop never raises, and we additionally swallow any launch error here -- a
    # relay problem must never brick boot; the home degrades to local-only.
    relay_client = None
    try:
        from ..remote.relay_client import run_relay_client
        relay_client = await run_relay_client(
            app, lambda: config_store.cfg().remote)
    except Exception as e:  # noqa: BLE001 - degrade to local-only, never crash boot
        bus.log("error", f"relay client launch failed (local-only): {e}", "remote")
    # Self-update (Phase 3): surface the last apply outcome on boot, and start the
    # OPT-IN poller ONLY when auto_check is enabled (default off => no task, so a
    # LAN-only install is unchanged). Never raises out of boot.
    try:
        _us = get_update_service()
        _us.load_boot_result()
        _ucfg = config_store.cfg().update
        if _ucfg.enabled and _ucfg.auto_check:
            _us.start_poller()
    except Exception as e:  # noqa: BLE001 - degrade, never crash boot
        bus.log("error", f"update service init failed: {e}", "update")
    # First-boot survey-pack seed (UX-07): copy the release's bundled baseline pack
    # into the persistent captures dir if absent, so a naive/self-updated box has
    # sky imagery immediately instead of a black Atlas. Cheap sync file copy; a
    # no-op in dev / unbundled builds. Never crashes boot.
    try:
        from ..catalog import survey_pack
        survey_pack.seed_bundled_pack(log=lambda m: bus.log("info", m, "survey"))
    except Exception as e:  # noqa: BLE001 - degrade, never crash boot
        bus.log("error", f"survey pack seed failed: {e}", "survey")
    # Boot auto-connect the active profile (no-op on first run / no active
    # profile), as a BACKGROUND task rather than awaited inline: a native profile
    # pointing at a powered-off host would otherwise serially burn a 30s httpx
    # timeout PER ROLE before the lifespan reaches `yield`, leaving the whole HTTP/
    # WS surface unreachable for minutes after a reboot. Spawning it lets the UI
    # serve immediately and degrade role-by-role (boot_connect_failed / backend_
    # links) as the connect progresses. MUST swallow every failure — a raise here
    # would only crash the background task, but we log it for parity with the old
    # inline path. The handle is retained so shutdown can cancel a slow connect.
    if not _boot_autoconnect_disabled():
        async def _boot_connect() -> None:
            try:
                await hub.connect_active()
            except Exception as e:  # noqa: BLE001 - degrade, never crash boot
                bus.log("error", f"boot auto-connect failed: {e}", "hub")
        hub._boot_connect_task = asyncio.create_task(_boot_connect())
    try:
        yield
    finally:
        await weather_service.stop()
        await resume_arm.stop()
        await trash_keeper.stop()
        await dispatcher.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        # Stop the relay dial-out (best-effort; never raises out of shutdown).
        if relay_client is not None:
            try:
                relay_client.stop()
            except Exception:
                pass
        # Stop the self-update poller (best-effort; never raises out of shutdown).
        try:
            get_update_service().stop_poller()
        except Exception:
            pass
        # Stop a still-running boot auto-connect before tearing the rig down, so a
        # slow connect can't race disconnect_all on shutdown (best-effort).
        bt = getattr(hub, "_boot_connect_task", None)
        if bt is not None and not bt.done():
            bt.cancel()
            try:
                await bt
            except (asyncio.CancelledError, Exception):
                pass
        # Clean teardown of an auto-connected rig (best-effort; never raises).
        try:
            await hub.disconnect_all()
        except Exception:
            pass
        # Stop the bundled COM host if this install ever started one (COM-T6):
        # the ascom-local backend owns a process-lifetime ComHostManager, and its
        # child is a no-orphan supervised process torn down here on app shutdown.
        # No-op when never spawned. Best-effort — must never raise out of shutdown.
        try:
            from ..comhost.manager import get_manager
            get_manager().stop()
        except Exception:
            pass


def _spawn(name: str, coro, *, replace: bool = False) -> dict:
    """Run a long operation as a named background task (one per name).

    ``replace=True`` cancels an existing same-named task instead of 409-ing — used
    by park, which is a motion-committing ABORT that must supersede an in-flight
    goto rather than be rejected by it. The cancelled goto unwinds (its slew abort
    + motion-fence bump already fenced it), releasing ``_motion_lock`` before the
    replacement acquires it, so the two never touch the mount at once."""
    existing = hub._busy.get(name)
    if existing and not existing.done():
        if not replace:
            raise HTTPException(409, f"'{name}' is already running")
        existing.cancel()

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", f"{name} cancelled", name)
        except (DeviceError, Exception) as e:
            bus.log("error", f"{name} failed: {e}", name)

    hub._busy[name] = asyncio.create_task(wrapped())
    return {"started": name}


# In-flight connect-by-profile/rig driver task (see _spawn_connect). Tracked here
# rather than in ``hub._busy`` so ``hub.disconnect_all()`` (called inside the
# connect to tear the old rig down) can't cancel the very task driving it.
_connect_task: "asyncio.Task | None" = None


def _spawn_connect(coro) -> dict:
    """Spawn a connect-by-profile/rig as a detached background task that streams
    over the WS but is NOT in ``hub._busy``.

    Rationale: a connect-by-profile tears the current rig down first via
    ``hub.disconnect_all()``, which cancels every task in ``hub._busy``. If this
    driver task lived in ``_busy`` it would cancel itself the instant
    ``disconnect_all`` ran (the same self-cancel the legacy ``apply`` path
    exhibits for a non-empty rig, where the connect happens AFTER the teardown).
    So we keep it out of ``_busy`` entirely and guard concurrency with a
    dedicated module handle. It still shares the ``profile`` lane intent: a
    pending connect blocks another connect/apply from stomping it."""
    global _connect_task
    if _connect_task is not None and not _connect_task.done():
        raise HTTPException(409, "'profile' is already running")
    if (t := hub._busy.get("profile")) and not t.done():
        raise HTTPException(409, "'profile' is already running")

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", "profile cancelled", "profile")
        except (DeviceError, Exception) as e:
            bus.log("error", f"profile failed: {e}", "profile")

    _connect_task = asyncio.create_task(wrapped())
    return {"started": "profile"}


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


def _get_master_library():
    """Resolve the master calibration library for the stacking bundle. PRO-1's
    real library (``hub.master_library``, a ``CalibrationLibrary``) is wrapped in
    :class:`CalibrationLibraryAdapter` to satisfy the bundle's ``MasterLibrary``
    Protocol (PRO-1 has no ``match(key)`` method); ``NullMasterLibrary`` stands in
    until PRO-1 attaches one to the hub."""
    lib = getattr(hub, "master_library", None)
    if lib:
        return CalibrationLibraryAdapter(lib)
    return NullMasterLibrary()


def _iso_utc(ts) -> str:
    """Epoch seconds -> ``YYYY-MM-DDThh:mm:ssZ`` (UTC), '' when unusable.

    Exports paired a raw float epoch with nothing human-readable (UX #49/#50);
    the explicit ``Z`` also states the zone, which is the piece missing when a
    local filename timestamp sits beside a UTC ``DATE-OBS``."""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _export_root(report_id: str) -> Path:
    """The materialize destination root: ``CAPTURE_DIR/exports/<sanitized id>``.

    CAPTURE_DIR is read LIVE off ``hub_module`` (never the import-time binding)
    so a test monkeypatch is honored, exactly like ``cal_library``. The leaf is
    ``_slug(report_id)`` — the SANITIZED id, never the raw path parameter — so no
    request can steer the export tree out of ``captures/exports``."""
    return hub_module.CAPTURE_DIR / "exports" / _slug(report_id)


def _materialize_bundle(b, root: Path) -> dict:
    """Lay a bundle's ACTUAL FITS out under ``root`` (blocking disk I/O — the
    route runs this in a worker thread).

    Placement strategy per file: ``os.link`` first (a hardlink costs zero extra
    bytes and is instant — the whole point of materializing on the capture box),
    falling back to ``shutil.copy2`` when the filesystem can't hardlink
    (EXDEV across devices, EMLINK, EPERM, a read-only FS, or a dest that already
    exists as a different file). Idempotent: a dest that is already the same
    inode counts as ``linked`` and is left alone, so re-materializing after more
    subs land just tops the tree up.

    Failures are collected, never fatal — a partial export is reported honestly
    rather than 500ing after having already placed half the files. Any plan item
    the containment guard refused is reported here and NEVER written."""
    items = bundle_materialize_plan(b, root)
    linked = copied = 0
    bytes_copied = 0
    failed: list[dict] = []
    per_group: dict[str, dict] = {}

    def _g(name: str) -> dict:
        return per_group.setdefault(name, {"dir": name, "linked": 0, "copied": 0})

    for it in items:
        g = _g(it.group_dir)
        if it.refused:
            failed.append({"src": it.src, "reason": it.refused})
            continue
        dest = Path(it.dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            failed.append({"src": it.src, "reason": f"could not create {dest.parent}: {e}"})
            continue
        try:
            os.link(it.src, dest)
            linked += 1
            g["linked"] += 1
            continue
        except OSError:
            pass  # cross-device / already exists / FS can't hardlink -> fall back
        try:
            if dest.exists() and os.path.samefile(it.src, dest):
                linked += 1          # already the same inode: idempotent no-op
                g["linked"] += 1
                continue
        except OSError:
            pass
        try:
            shutil.copy2(it.src, dest)
            copied += 1
            g["copied"] += 1
            bytes_copied += dest.stat().st_size
        except OSError as e:
            failed.append({"src": it.src, "reason": str(e)})

    return {
        "export_dir": str(root),
        "layout": b.layout,
        "linked": linked,
        "copied": copied,
        "bytes_copied": bytes_copied,
        "failed": failed,
        "groups": list(per_group.values()),
        "hardlink_note": ("Hardlinked files share an inode with the original — "
                          "editing an exported FITS in place also changes your "
                          "capture. Treat the export tree as read-only."),
    }


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


class LiveStackBody(CaptureBody):
    # NOV-1 Live View: drift-reject threshold as a fraction of the frame's short
    # edge (default 8%). An Advanced knob — the default suits any tracked rig.
    reject_frac: float = 0.08
    # Clip a pixel this many sigma above the running mean: the satellite-trail
    # guard. 0 disarms it (a deliberate "stack everything" mode), which is what
    # you want when the target itself is a genuine transient.
    clip_sigma: float = Field(4.0, ge=0, le=20)


class BahtinovBody(CaptureBody):
    # NOV-12 Bahtinov focus aid: |offset| <= tol_px reads "locked"; invert flips
    # the per-rig IN/OUT direction word (the geometric side is invariant).
    tol_px: float = 1.5
    invert: bool = False


class GotoBody(BaseModel):
    ra_hours: float
    dec_deg: float
    center: bool = True
    force: bool = False
    # allow_inf_nan=False: a NaN/inf rotation target would otherwise sail
    # through validation and blow up rotate_to_pa's mod-360 math (post-review
    # hardening). Default stays None (rotation is optional).
    rotation_deg: float | None = Field(default=None, allow_inf_nan=False)


class MoveAxisBody(BaseModel):
    # F-S6 (safety-of-motion): constrain to the two real axes. A mistyped axis
    # must 422 here — never silently command Alpaca DEC (axis!='ra' → 1) while
    # arming the deadman against a name the watchdog can't zero.
    axis: Literal["ra", "dec"]
    rate_deg_s: float


class FocuserMoveBody(BaseModel):
    position: int


class FocuserSetPositionBody(BaseModel):
    """Re-anchor: declare the current position. Moves nothing."""

    position: int


class RotatorMoveBody(BaseModel):
    position_deg: float


class RotatorReverseBody(BaseModel):
    reverse: bool


class RotateToPaBody(BaseModel):
    # allow_inf_nan=False: same NaN/inf hardening as GotoBody.rotation_deg.
    target_pa_deg: float = Field(..., allow_inf_nan=False)
    exposure_s: float = 3.0


class AutofocusBody(BaseModel):
    exposure_s: float = 2.0
    gain: int = 120
    step: int = 350
    steps_each_side: int = 4
    binning: int = 2
    filter: int | None = None  # UX-25: slot to move to before the sweep (per-filter AF)

class CoarseFocusBody(BaseModel):
    """Coarse focus: find a position with stars, then hand off to autofocus.

    Defaults are deliberately generous — this runs when the user cannot see
    anything, so a longer exposure and coarse binning buy signal that the
    search needs and the handoff does not care about."""
    exposure_s: float = 4.0
    gain: int = 200
    binning: int = 2
    #: Total travel to search, centred on the current position. None = the whole
    #: usable range, which is the case this exists for ("I have no idea where
    #: focus is"); a narrow default would fail exactly when it is needed.
    span: int | None = None
    stops: int = 9



class FilterBody(BaseModel):
    position: int


class FilterNamesBody(BaseModel):
    names: list[str]
    offsets: list[int] = []
    #: Per-slot blackout ("this slot is opaque") flags. None = leave whatever is
    #: stored alone, so a client that predates the flag never clears it; a list
    #: replaces the set wholesale, which is what makes un-marking a slot possible.
    opaque: list[bool] | None = None


class LearnOffsetsBody(BaseModel):
    """Per-filter AF-offset auto-learn. ``ref_slot`` None -> the hub picks an
    L/Lum/Clear slot when the wheel has one, else the current position."""
    ref_slot: int | None = None
    exposure_s: float = 2.0
    gain: int = 120
    step: int = 350
    steps_each_side: int = 4
    binning: int = 2


class EgainLearnBody(BaseModel):
    """Mean-variance EGAIN measurement: ``count`` bias + ``count`` flat frames."""
    gain: int
    count: int = 4
    exposure_s: float = 2.0
    offset: int = 10
    binning: int = 1


class SwitchBody(BaseModel):
    port_id: int
    value: float


class CoolerBody(BaseModel):
    on: bool
    target_c: float | None = None
    #: ``on=False`` now runs a background WARM RAMP (setpoint stepped toward
    #: ambient, TEC switched off only at the end) instead of cutting the cooler
    #: dead. ``ramp=False`` is the explicit escape hatch — "stop the ramp and
    #: switch it off NOW" from the Capture screen, or a scripted caller that
    #: knows what it is asking for. It defaults to True because the callers that
    #: most need the ramp (the unattended safety wind-down) are the ones nobody
    #: is going to go back and add a flag to. Ignored when ``on`` is True.
    ramp: bool = True


class DewBody(BaseModel):
    power: int = 0


class CalibratorBody(BaseModel):
    brightness: int = Field(ge=0)


class CoverBody(BaseModel):
    open: bool


class PHD2Body(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4400


class NinaConnectBody(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1888


class ConnSpecBody(BaseModel):
    """One per-role connection override in a /api/connect/rig request (mirrors
    ``devices.backend.ConnSpec``). Only ``backend`` is required; the rest carry
    the addressing a given backend needs (Alpaca/native: host/port/dev_*; nina:
    host/port; sim/phd2: nothing)."""
    backend: str
    host: str | None = None
    port: int | None = None
    dev_type: str | None = None
    dev_num: int | None = None
    role: str | None = None
    driver_id: str | None = None
    extra: dict = {}


class RigSpecBody(BaseModel):
    """A whole-rig connection plan posted to /api/connect/rig (mirrors
    ``devices.backend.RigSpec``): a ``primary`` backend that fills every ROLE it
    can, plus optional per-role ``roles`` overrides."""
    primary: str
    roles: dict[str, ConnSpecBody] = {}


class DitherBody(BaseModel):
    pixels: float = 3.0
    # UX-24: optional settle overrides (None = the guider's defaults).
    settle_pixels: float | None = None
    settle_time_s: float | None = None
    settle_timeout_s: float | None = None


class AssistantStartBody(BaseModel):
    """Guiding Assistant options (design §3.3). Both optional: skip Phase B, or
    pin the Phase-A watch duration."""
    include_backlash: bool = True
    duration_s: int | None = None


class FactoryResetBody(BaseModel):
    """Factory-reset options. BOTH extras default to False, at every layer.

    ``confirm`` is a typed-word interlock, not decoration: the panel makes the
    admin type RESET, and the server refuses anything else, so a stray curl or a
    replayed request can never wipe a rig by accident. The two destructive
    extras are SEPARATE fields (not one "everything" flag) because captured
    frames and sign-in accounts are different kinds of loss and each must be
    chosen on its own — see ``factory_reset.py`` for the tier contract."""
    confirm: str = ""
    delete_captures: bool = False
    reset_auth: bool = False


class SiteSaveBody(BaseModel):
    site: Site
    version: int | None = None
    # onboarding's per-site minimum-altitude horizon; persisted alongside site so
    # below-horizon GOTO guarding has a real number. Optional for back-compat.
    horizon_min_deg: float | None = None


class OpticsSaveBody(BaseModel):
    optics: Optics
    version: int | None = None


class LocationBody(BaseModel):
    # Range-constrained AT THE BOUNDARY (same ranges as config.Site /
    # locations.SavedLocation) so out-of-range input 422s in FastAPI's body
    # validation instead of raising an uncaught ValidationError (-> 500)
    # inside LocationStore's post-model_copy re-validate.
    name: str
    latitude: float = Field(..., ge=-90, le=90)      # +N (signed)
    longitude: float = Field(..., ge=-180, le=180)   # +E (East-positive)
    elevation_m: float = Field(..., ge=-430, le=9000)
    horizon_min_deg: float | None = Field(None, ge=0, le=90)

    @field_validator("name")
    @classmethod
    def _name_trimmed_non_empty(cls, v: str) -> str:
        """Same rule as SavedLocation (spec §4): trim, then reject empty —
        so '' and '   ' 422 at the boundary instead of 500ing in the store."""
        v = v.strip()
        if not v:
            raise ValueError("name must be non-empty")
        return v


class ProfileCaptureBody(BaseModel):
    name: str


class ProfileRenameBody(BaseModel):
    name: str


class ProfileClearOverridesBody(BaseModel):
    """#129: which of a profile's overrides to drop.

    ``providers`` names capability pins ("polar_align", …); ``optics`` clears the
    whole optics block, which is the only granularity that exists — the resolver
    swaps the block WHOLE, so there is no such thing as clearing one field.
    Both default to "change nothing" so a malformed body is inert rather than
    destructive."""
    providers: list[str] = []
    optics: bool = False


class ProfileSetProvidersBody(BaseModel):
    """#132: which of a profile's capability pins to WRITE, and to what.

    The mirror image of ``ProfileClearOverridesBody``. A dict rather than a whole
    ``ProvidersConfig`` because the caller edits ONE capability at a time and the
    other three must not be dragged along: a body carrying all four would let a
    client that read a stale config silently re-pin capabilities the user never
    touched. Defaults to "change nothing" for the same reason clear-overrides
    does — a malformed body is inert rather than destructive."""
    providers: dict[str, str] = {}


class ProfileApplyBody(BaseModel):
    force: bool = False


class PlanSaveBody(BaseModel):
    plan: SequencePlan
    id: str | None = None
    overwrite: bool = False


class SessionPatchBody(BaseModel):
    auto_resume: bool | None = None
    status: str | None = None            # only "abandoned" is accepted
    plan: SequencePlan | None = None     # dormant-only full replacement (spec §4)


class FramePatchBody(BaseModel):
    override: str | None = None          # "accept" | "reject" | null (clear)
    metrics: dict[str, float] | None = None


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
    still flips ``is_default`` off and re-reads onto the mount.

    RBAC fail-closed (W2.2 T-RBAC-8): ``extra="forbid"`` so a stray ``auth`` /
    ``remote`` / unknown block is REJECTED at binding (422, merge NOTHING) instead
    of being silently dropped. Auth/remote writes go through the dedicated
    ``admin.users``-gated routes (``/api/auth/config`` etc.), NEVER this merge."""
    model_config = ConfigDict(extra="forbid")
    site: Site | None = None
    safety: SafetyConfig | None = None
    escalation: EscalationConfig | None = None
    alerts: list[AlertSink] | None = None
    deadman_url: str | None = None
    # Cooler warm-down policy (2026-08-04). It rides this route, and is gated on
    # config.safety rather than a cap of its own, because the setting it governs
    # is a hardware-protection policy the SafetyLimitsPanel already claims in
    # words: "park, then warm the camera at a safe ramp". The knob belongs next
    # to the sentence that promises it.
    cooling: CoolingConfig | None = None


class SafetySimulateBody(BaseModel):
    unsafe: bool = True
    reason: str = "simulated unsafe condition"


class JtiBody(BaseModel):
    """A single session/link id to (un)revoke (POST /api/auth/revoke). The revoke
    registry is append-only and ``admin.users``-gated."""
    jti: str


class DriverCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    host: str = ""                # network transport (empty for serial)
    port: int | None = None
    transport: str = "network"    # "network" | "serial"
    port_path: str = ""           # serial transport, e.g. "COM3"
    label: str = ""
    extra: dict = {}


class DriverPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = None
    port: int | None = None
    enabled: bool | None = None
    label: str | None = None
    extra: dict | None = None
    # B follow-up C: a moved COM port can be fixed without delete+recreate.
    port_path: str | None = None
    transport: str | None = None


class PackFetchBody(BaseModel):
    """POST /api/survey/pack/fetch body (offline-pack spec §5). order is
    HiPS depth 1-6, clamped server-side; the route defaults it to 4 whether
    the body is omitted entirely or sent as ``{}``."""
    order: int = 4


class WeatherSaveBody(BaseModel):
    """POST /api/config/weather body (weather spec §2). Same optimistic-
    concurrency version token as SiteSaveBody. Secret write contract
    (deadman_url precedent): a null/empty astrospheric_api_key means "leave
    the stored key unchanged" — the UI only ever sees the masked config, so
    it round-trips a blank; clear_astrospheric_key=True clears explicitly."""
    weather: WeatherConfig
    version: int | None = None
    clear_astrospheric_key: bool = False


class WcsStampBody(BaseModel):
    """POST /api/config/wcs body (per-frame-wcs spec §3). The master enable and
    its advanced block travel together so one panel save is one atomic
    version bump — a half-applied state can never be broadcast. MUST be
    module-level (PEP 563 / FastAPI annotation resolution — see
    IgnoreTonightBody below)."""
    solve_saved_lights: bool = False
    wcs_stamp: WcsStampConfig = Field(default_factory=WcsStampConfig)


class GalleryPathsBody(BaseModel):
    """Body for the gallery's delete / restore routes: relative frame paths.

    A list rather than a path parameter because deleting a night is one action
    the user took, and N separate DELETE calls would give N chances to half-fail
    with no way to report which half. Module-level like every other ``*Body``
    here (see ``IgnoreTonightBody`` for why a nested class silently 422s).

    ``max_length`` is a denial-of-service bound, not a product limit: the whole
    293-frame reference library is three orders of magnitude below it."""
    paths: list[str] = Field(default_factory=list, max_length=50_000)


class GalleryPurgeBody(GalleryPathsBody):
    """Body for permanent deletion. ``all=True`` empties the bin; otherwise only
    the listed paths go. Two separate spellings on purpose — "empty the trash"
    must be something the client asked for in those words, never an empty
    ``paths`` list that got there by accident."""
    all: bool = False


class IgnoreTonightBody(BaseModel):
    """POST /api/weather/ignore-tonight body (weather spec §4). MUST be
    module-level (like every other ``*Body`` model here) rather than nested
    inside ``create_app`` -- with ``from __future__ import annotations`` in
    effect, FastAPI resolves parameter annotations via the function's module
    globals, so a class local to ``create_app`` cannot be found and the
    ``body`` param silently degrades to an (always-missing) query param,
    422-ing every call. Caught by the weather RBAC HTTP tests (Task 4)."""
    ignore: bool


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
# ``/auth/login`` + ``/auth/google/callback`` are the OIDC login dance (W2.4-C)
# and must be reachable pre-session; ``/auth/logout`` is NOT here (it needs a
# session). The RBAC boot assertion exempts these same auth-login paths.
_AUTH_OPEN_PREFIXES = ("/assets", "/auth/login", "/auth/google/callback")
_AUTH_OPEN_EXACT = {"/", "/index.html", "/favicon.ico", "/manifest.json", "/healthz"}


def auth_token() -> str:
    """The configured shared token, or '' when auth is disabled (the default).

    Read live from the environment so a token set before launch is honored and
    tests can monkeypatch ``os.environ`` per-app. Whitespace is stripped so a
    stray newline in a launcher script can't create a token nobody can type."""
    return (os.environ.get(AUTH_ENV_VAR) or "").strip()


def auth_enabled() -> bool:
    return bool(auth_token())


def _warn_insecure_session_secret() -> None:
    """LOUD, fail-closed-aware warning when a real auth method is enabled but the
    session-signing secret is still the public dev default (critical fix).

    ``configure_provider_from_auth`` already tries to generate+persist a random
    secret and arm the fail-closed interlock; this surfaces the state to the
    operator. ASCII only (the live console is cp1252). A no-op under the
    open/admin default (no method enabled), so default behavior is unchanged."""
    from ..auth import session as _session
    try:
        methods = config_store.cfg().auth.methods_effective()
    except Exception:  # noqa: BLE001 - never break boot on a banner
        methods = []
    if not methods:
        return  # open/admin default: nothing is signed, no secret needed
    if _session.secret_is_default():
        # A real secret could NOT be established (env unset AND persist failed);
        # the interlock is armed so sessions fail closed. Tell the operator LOUD.
        bus.log("error", "=" * 70, "auth")
        bus.log("error",
                "SECURITY: auth method enabled but no ASTRODECK_SECRET and the "
                "auto-generated secret could not be persisted.", "auth")
        bus.log("error",
                "Sessions are DISABLED (fail-closed) until you set "
                "ASTRODECK_SECRET. Logins will not work.", "auth")
        bus.log("error", "=" * 70, "auth")
    elif not (os.environ.get(_session.SECRET_ENV_VAR) or "").strip():
        # Running on the auto-generated persisted secret. Functional + safe, but
        # note it so the operator knows a key was minted on their behalf.
        bus.log("info",
                "auth: a random session secret was generated and persisted "
                "(set ASTRODECK_SECRET to manage it yourself).", "auth")


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
    # /api, /ws and /auth (the auth surface — only the login dance above is open;
    # /auth/logout + /api/auth/* are gated) are NEVER treated as an open SPA link.
    if path.startswith("/api") or path.startswith("/ws") or path.startswith("/auth"):
        return False
    # A non-API path with no extension is an SPA deep link → index.html shell.
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return True
    return False


def create_app() -> FastAPI:
    app = FastAPI(title="AstroDeck", version=__version__, lifespan=_lifespan)

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
    app.include_router(tiles_router)
    app.include_router(framing_router)
    app.include_router(visibility_router)

    # ---------------------------------------------------- health + version
    # /healthz is OPEN (no token, no session): the supervisor health-checks it on
    # every restart and a relay / load balancer liveness-probes it. It discloses
    # only the running version -- no identity, no rig state.
    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": __version__}

    # /api/version surfaces the update-subsystem snapshot (current vs latest-known,
    # availability, channel, last check, last apply result). Non-secret; the poller
    # (update service) populates ``latest`` / ``update_available``.
    @app.get("/api/version", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    async def api_version():
        return update_state.snapshot()

    # ------------------------------------------------------------ self-update
    # status is a read (no cap); check/apply/config MUTATE and require the
    # admin-only system.update capability. apply additionally passes the rig-idle
    # safety gate (apply_preconditions -> hub.restart_blocker) at the home, so a
    # remote admin can never interrupt an exposure/slew/sequence.
    @app.get("/api/update/status",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    async def update_status():
        svc = get_update_service()
        ok, reason = svc.apply_preconditions()
        snap = update_state.snapshot()
        snap["supervised"] = svc.supervised
        snap["can_apply"] = ok
        snap["apply_blocked_reason"] = "" if ok else reason
        return snap

    @app.post("/api/update/check",
              dependencies=[Depends(require(CAP_SYSTEM_UPDATE))])
    @declare(CAP_SYSTEM_UPDATE)
    async def update_check():
        return await get_update_service().check()

    @app.post("/api/update/apply",
              dependencies=[Depends(require(CAP_SYSTEM_UPDATE))])
    @declare(CAP_SYSTEM_UPDATE)
    async def update_apply():
        svc = get_update_service()
        ok, reason = svc.apply_preconditions()
        if not ok:
            raise HTTPException(409, reason)
        # long pipeline runs in the background and streams phases over WS; on
        # success it asks the supervisor (via graceful exit 92) to swap + restart.
        return _spawn("system.update", svc.apply())

    @app.post("/api/update/config",
              dependencies=[Depends(require(CAP_SYSTEM_UPDATE))])
    @declare(CAP_SYSTEM_UPDATE)
    async def update_set_config(body: UpdateConfig):
        # A blank github_token means "unchanged" (the UI only ever sees the
        # redacted block, so it echoes back empty) -- restore the stored secret
        # rather than wiping it. Mirrors the admin_token / device_token pattern.
        if not (body.github_token or "").strip():
            body = body.model_copy(
                update={"github_token": config_store.cfg().update.github_token})
        try:
            cfg = config_store.set_update_config(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        bus.publish("config", config=redacted(cfg))
        # redacted() so the github_token (and any other secret) is never returned
        # in the HTTP response body either (only a *_configured boolean).
        return redacted(cfg)

    # ---------------------------------------------------------- factory reset
    # Return the controller to the state a FRESH INSTALL has, so the box can be
    # handed to the next QA tester with no trace of the last one. Sits with the
    # self-update routes above because it is the same family: a whole-system
    # lifecycle operation, not a settings write.
    #
    # Gated on ``admin.users`` — the strictest cap in the table (admin-only, and
    # in DESTRUCTIVE_CAPS), and the right one specifically: the reset can clear
    # the local sign-in accounts and the auth block, which is exactly what
    # admin.users governs on /api/auth/config. config.site_optics or
    # config.backend would each cover only a slice of what this wipes.
    #
    # Idle gate: reuses ``hub.restart_blocker`` — the same predicate that stops a
    # self-update from interrupting an exposure, slew or running sequence. A
    # factory reset mid-capture would strand a run against a config that no
    # longer describes the rig.

    @app.get("/api/system/factory-reset",
             dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def factory_reset_preview():
        """MEASURED scope for the confirm copy — how many profiles, plans,
        drivers, accounts and captured frames actually exist right now.

        The panel quotes these numbers on screen before the user commits, so the
        statement of what is about to be destroyed is counted rather than
        guessed. Pure read; ``blocked_reason`` mirrors the POST's idle gate so
        the button can explain itself instead of failing on press."""
        blocker = hub.restart_blocker
        snap = await asyncio.to_thread(
            factory_reset_module.preview, config_store,
            config_module.CONFIG_DIR, hub_module.CAPTURE_DIR)
        return snap | {"can_reset": not blocker,
                       "blocked_reason": blocker or ""}

    @app.post("/api/system/factory-reset",
              dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def factory_reset_apply(body: FactoryResetBody):
        """Restore defaults. IRREVERSIBLE.

        Three interlocks, deliberately: the admin.users capability, the typed
        ``confirm`` word, and the rig-idle gate. Captured frames and sign-in
        accounts are each behind their own explicit opt-in and are NOT touched
        otherwise — see ``factory_reset.py`` for the full tier contract."""
        if (body.confirm or "").strip().upper() != "RESET":
            raise HTTPException(400, detail={
                "detail": "type RESET to confirm a factory reset",
                "code": "confirm_required"})
        blocker = hub.restart_blocker
        if blocker:
            raise HTTPException(409, detail={"detail": blocker,
                                             "code": "rig_busy"})
        # Drop the rig FIRST. A fresh install has nothing connected, and leaving
        # a live rig up would leave the first-run wizard's "connect" step already
        # satisfied against equipment the reset config no longer knows about.
        try:
            await hub.disconnect_all()
        except Exception as e:  # never let a flaky teardown block the reset
            bus.log("warning", f"factory reset: disconnect failed: {e}", "config")
        report = await asyncio.to_thread(
            factory_reset_module.factory_reset, config_store,
            config_module.CONFIG_DIR, hub_module.CAPTURE_DIR,
            delete_captures=body.delete_captures,
            reset_auth=body.reset_auth)
        if body.reset_auth:
            # Rebuild the auth provider off the now-default block, so the change
            # takes effect without a restart (the /api/auth/config idiom).
            _reconfigure_provider()
        bus.publish("config", config=redacted(config_store.cfg()))
        return report

    # ---------------------------------------------------- capability providers
    # Global per-capability routing override (native parity — Settings → Connect
    # "Capabilities" card). ``config.backend`` gated, same cap as every other
    # backend/connect write: choosing which implementation runs autofocus/TPPA is
    # a backend-shape decision, not a safety one. Broadcasts the redacted union so
    # every open client's ProviderBadge / Capabilities card updates immediately.
    @app.post("/api/config/providers")
    @declare(CAP_CONFIG_BACKEND)
    async def set_providers_config(
            body: ProvidersConfig,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        try:
            cfg = await asyncio.to_thread(config_store.set_providers, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # -------------------------------------------------------------- rotator
    # Rotator mechanical ROM + rotate-loop tolerance (rotator/CAA spec §3.2).
    # Same cap/broadcast shape as the providers route above.
    @app.post("/api/config/rotator")
    @declare(CAP_CONFIG_BACKEND)
    async def set_rotator_config(
            body: RotatorConfig,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        try:
            cfg = await asyncio.to_thread(config_store.set_rotator, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ---------------------------------------------------- survey pack (offline-pack spec §4-5)
    # Sky-Atlas survey source selection (online_fetch gates upstream hips2fits
    # calls — an imaging/framing concern, so it's config.site_optics, not
    # config.backend) + the offline HiPS tile-pack lifecycle (status/fetch/
    # delete). Same cap/broadcast shape as the rotator/optics config routes
    # above: the config-store write is offloaded off the event loop
    # (bump_and_save is a blocking disk write), and the redacted union is
    # broadcast so every open client's Sky-Atlas panel updates immediately.
    # survey_pack_mod.* is always called as a module attribute (never
    # `from ... import start_fetch`) so tests can monkeypatch it.

    @app.post("/api/config/survey")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_survey_config(
            body: SurveyConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        cfg = await asyncio.to_thread(config_store.set_survey, body)
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ---------------------------------------------------- naming template (PRO-11)
    # Capture folder+filename token template. config.site_optics (imaging/output
    # concern, same rationale as the survey route). Write-time validated in
    # set_naming (ValueError -> 422); redacted union broadcast so every open
    # client's Naming panel updates.
    @app.post("/api/config/naming")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_naming_config(
            body: NamingConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            cfg = await asyncio.to_thread(config_store.set_naming, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ------------------------------------------------ calibration tolerances
    # How aggressively master darks/flats/bias are reused across nights, and how
    # the stacker bins them. config.site_optics for the same reason naming/wcs
    # are: this decides what lands in the delivered file, not which hardware is
    # driven. Relational validation lives in set_calibration (ValueError -> 422).
    @app.post("/api/config/calibration")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_calibration_config(
            body: CalibrationConfig,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            cfg = await asyncio.to_thread(config_store.set_calibration, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ------------------------------------------- per-frame WCS (per-frame-wcs §3)
    # Master enable + advanced knobs for stamping each saved light's plate-solved
    # WCS into its FITS header. config.site_optics — a capture-OUTPUT concern,
    # exactly like the naming/survey routes above (NOT config.backend: it changes
    # what lands in the file, not which hardware is driven). Write-time validated
    # in set_wcs_stamp (ValueError -> 422); redacted union broadcast so every open
    # client's panel updates.
    @app.post("/api/config/wcs")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_wcs_stamp_config(
            body: WcsStampBody,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            cfg = await asyncio.to_thread(
                config_store.set_wcs_stamp, body.solve_saved_lights, body.wcs_stamp)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    # ---------------------------------------------------- weather config (weather spec §2)
    # Same cap/broadcast shape as the survey route above, PLUS the optimistic-
    # concurrency version token (put_site idiom). Secret write contract
    # (deadman_url precedent): a null/empty astrospheric_api_key means "leave
    # the stored key unchanged" — the UI only ever sees the masked config, so
    # it round-trips a blank; clear_astrospheric_key=True clears explicitly.
    # WeatherSaveBody is defined at module scope alongside the other *Body
    # request models (SiteSaveBody idiom) — NOT locally here, because this
    # file uses ``from __future__ import annotations`` (PEP 563): FastAPI
    # resolves a route's string annotations via the function's module
    # globals, so a class defined inside create_app() cannot be resolved as
    # the request body and silently degrades to an unresolvable query param.

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

    # ------------------------------------------------- ignore-tonight (weather spec §4)
    # Runtime flag on the WeatherService, NOT persisted config. Gated on BOTH
    # control.capture (operator — it affects sequencing, like other control
    # caps) AND view.weather (defense-in-depth, I2 review): the response
    # echoes the full weather payload, which since I2 carries site_lat/
    # site_lon, so the route must also require the cap that gates reading
    # that payload. For the three fixed roles this changes nothing (every
    # control.capture holder also holds view.weather), but that implication
    # is NOT a stated invariant — if custom/split roles ever exist (queued
    # product decision), a control.capture-without-view.weather principal
    # must NOT be able to read coordinates off this write route's echo.
    # Keyed to tonight's dusk; auto-expires when a new night begins.
    # The updated flag rides the weather payload so ALL clients see it.

    @app.post("/api/weather/ignore-tonight",
              dependencies=[Depends(require(CAP_VIEW_WEATHER))])
    @declare(CAP_CONTROL_CAPTURE, CAP_VIEW_WEATHER)
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

    # ---------------------------------------------------- weather read (weather spec §7)
    # Full payload, holders only (spec §8: REST requires the cap outright — no
    # partial payloads). Gated on view.weather (2026-07-17 decisions wave I2:
    # split off view.site_precise so operators see weather too) rather than
    # view.site_precise -- view.site_precise stays the gate for every OTHER
    # precise-site surface (status/config/summary/site-sky), unchanged. The
    # payload carries site_lat/site_lon (the one deliberate exception to "site
    # coordinates are admin-only everywhere else" -- see weather.payload()),
    # so it is no longer coordinate-free, but it is still gated identically to
    # the rest of this payload and NEVER reaches a viewer.

    @app.get("/api/weather")
    @declare(CAP_VIEW_WEATHER)
    async def get_weather(
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))):
        return weather_service.payload()

    # ------------------------------------------------- weather tiles (weather spec §6)
    # Gated on view.weather, same split as the read route above (I2): radar/
    # satellite tiles are centred on the site, so the owner explicitly accepts
    # that operators reaching this route can infer the site's rough region --
    # the trade-off "full weather (radar map included) for operators" makes.

    @app.get("/api/weather/tile/{layer}/{z}/{x}/{y}.png")
    @declare(CAP_VIEW_WEATHER)
    async def weather_tile(
            layer: Literal["radar", "satellite"], z: int, x: int, y: int,
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))
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

    @app.get("/api/survey/pack",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_survey_pack():
        return survey_pack_mod.pack_status()

    @app.post("/api/survey/pack/fetch",
              dependencies=[Depends(require(CAP_CONFIG_SITE_OPTICS))])
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def start_survey_pack_fetch(body: PackFetchBody | None = None):
        order = max(1, min(6, body.order if body is not None else 4))
        try:
            survey_pack_mod.start_fetch(order=order)
        except survey_pack_mod.FetchAlreadyRunning:
            return JSONResponse({"started": False, "already": True}, status_code=200)
        except survey_pack_mod.InsufficientSpace as exc:
            raise HTTPException(status_code=507, detail={
                "detail": "insufficient disk space",
                "free_bytes": exc.free, "required_bytes": exc.required})
        return JSONResponse({"started": True}, status_code=202)

    @app.delete("/api/survey/pack",
                dependencies=[Depends(require(CAP_CONFIG_SITE_OPTICS))])
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def delete_survey_pack():
        try:
            removed = survey_pack_mod.remove_pack()
        except survey_pack_mod.FetchAlreadyRunning:
            raise HTTPException(status_code=409,
                                detail={"detail": "pack fetch in progress"})
        return {"deleted": removed}

    # ------------------------------------------------------------ equipment

    @app.get("/api/discover", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def discover():
        return await alpaca_backend.discover()

    @app.get("/api/discover/nina", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def discover_nina_instances(host: str = "", port: int = 1888):
        extra = [host] if host else None
        return await discover_nina(port=port, extra_hosts=extra)

    @app.get("/api/discover/alpaca", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
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

    @app.get("/api/discover/ascom-local",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def discover_ascom_local():
        """The native ASCOM scan (spec §3.2): registry-enumerated COM drivers
        per type, role-tagged, each carrying dev_type + dev_num addressing so
        the assignment UI can pick one with no host/port/ProgID typing. Empty
        off Windows (winreg absent) — the panel then shows no ascom-local
        devices and the UI degrades cleanly. No COM is instantiated here; this
        is a pure registry read."""
        from ..devices import ascom_registry
        return await asyncio.to_thread(ascom_registry.enumerate_offers)

    @app.get("/api/backends", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def backends():
        """Every registered backend, JSON-able: ``[{name, label, roles,
        discoverable}, ...]`` ordered by name (the connect UI's backend picker).
        Imports the backends package for its self-registration side effect so the
        registry is populated even on a cold first call."""
        from ..devices import backends as _b  # noqa: F401 - registration side-effect
        from ..devices.backend import list_backends
        return list_backends()

    @app.get("/api/backends/plugins",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def backend_plugins():
        """Discovery outcomes for third-party backend plugins: ``[{name, dist,
        version, status, detail}, ...]`` where status is loaded/failed/
        incompatible. Imports the backends package so discovery has run."""
        from ..devices import backends as _b  # noqa: F401 - discovery side-effect
        return _b.plugin_load_report()

    # -------------------------------------------- backend drivers (spec 2026-07-08)
    # GLOBAL driver config + the merged availability surface. Read = view.status
    # (same as discovery); write = config.backend (same as every connect write).
    # describe_all() never raises, so GET /api/drivers can't 500.

    @app.get("/api/drivers")
    @declare(CAP_VIEW_STATUS)
    async def list_drivers(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        from .. import drivers as drivers_mod
        # RBAC redaction (security review): a caller without config.backend
        # (viewer role) can SEE that a driver exists and probe its offers, but
        # not its host/port/extra — those are the same endpoint details
        # config.backend is required to WRITE.
        return _redact_drivers_for(await drivers_mod.describe_all(), principal)

    @app.post("/api/drivers/{driver_id}/probe",
              dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def probe_driver(driver_id: str):
        """Force ONE driver's re-probe (bypasses the 15s cache). Implicit ids
        (sim/astrodeck/astap) are accepted — they recompute on every describe."""
        from .. import drivers as drivers_mod
        known = ({d.id for d in config_store.cfg().drivers}
                 | {"sim", "astrodeck", "astap", "ascom-local"})
        if driver_id not in known:
            raise HTTPException(404, "unknown driver")
        drivers_mod.invalidate(driver_id)
        return await drivers_mod.describe_all()

    @app.post("/api/config/drivers")
    @declare(CAP_CONFIG_BACKEND)
    async def add_driver(
            body: DriverCreateBody,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        from .. import drivers as drivers_mod
        if body.type not in drivers_mod.configurable_driver_types():
            raise HTTPException(422, f"unknown driver type: {body.type!r}")
        try:
            entry = await asyncio.to_thread(
                lambda: config_store.add_driver(
                    body.type, body.host, body.port, body.label, body.extra,
                    transport=body.transport, port_path=body.port_path))
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(config_store.cfg()))
        return {"driver": entry.model_dump(), "config": _config_payload(principal)}

    @app.patch("/api/config/drivers/{driver_id}")
    @declare(CAP_CONFIG_BACKEND)
    async def patch_driver(
            driver_id: str, body: DriverPatchBody,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            entry = await asyncio.to_thread(
                config_store.update_driver, driver_id, patch)
        except KeyError:
            raise HTTPException(404, "unknown driver")
        except ValueError as e:
            raise HTTPException(422, str(e))
        from .. import drivers as drivers_mod
        drivers_mod.invalidate(driver_id)      # addressing may have changed
        bus.publish("config", config=redacted(config_store.cfg()))
        return {"driver": entry.model_dump(), "config": _config_payload(principal)}

    @app.delete("/api/config/drivers/{driver_id}")
    @declare(CAP_CONFIG_BACKEND)
    async def delete_driver(
            driver_id: str,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        try:
            await asyncio.to_thread(config_store.delete_driver, driver_id)
        except KeyError:
            raise HTTPException(404, "unknown driver")
        from .. import drivers as drivers_mod
        drivers_mod.invalidate(driver_id)
        bus.publish("config", config=redacted(config_store.cfg()))
        return {"deleted": driver_id, "config": _config_payload(principal)}

    @app.get("/api/discover/{backend}", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def discover_backend(backend: str):
        """Delegate discovery to a named backend's ``discover()`` (unknown backend
        -> 404). A more general sibling of the legacy ``/api/discover``,
        ``/api/discover/nina``, ``/api/discover/alpaca`` routes (distinct paths -
        no collision); those stay as-is for the live UI."""
        from ..devices import backends as _b  # noqa: F401 - registration side-effect
        from ..devices.backend import get_backend
        try:
            b = get_backend(backend)
        except KeyError:
            raise HTTPException(404, f"unknown backend {backend!r}")
        return await b.discover()

    @app.get("/api/nina/health", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def nina_health():
        """Backend↔NINA link health (same shape as the ``nina_link`` block on
        the status event). For tests + a future Rig-page readout."""
        st = await hub.poll_status()
        return st.get("nina_link", {"active": False, "last_ok_age_s": None,
                                    "last_error": None, "healthy": False,
                                    "warming_up": False})

    @app.post("/api/connect/sim", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def connect_sim():
        return await hub.connect_sim()

    @app.post("/api/connect/alpaca", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
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

    @app.post("/api/connect/phd2", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def connect_phd2(body: PHD2Body):
        try:
            await hub.connect_phd2(body.host, body.port)
            return {"connected": True}
        except Exception as e:
            raise _err(e)

    @app.post("/api/connect/nina", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def connect_nina(body: NinaConnectBody):
        try:
            return await hub.connect_nina(body.host, body.port)
        except DeviceError as e:
            raise _err(e)
        except Exception as e:
            raise HTTPException(502, f"NINA connection failed: {e}")

    @app.post("/api/connect/rig", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def connect_rig(body: RigSpecBody):
        """Connect a whole rig by RigSpec (the pluggable-backend connect path,
        W1.6). The ``primary`` backend fills every ROLE it can; ``roles`` carry
        explicit per-role overrides.

        Server-side reject rule (422): an EXPLICIT override whose role is not in
        the target backend's ``roles`` is refused (e.g. ``safety``->``nina``, the
        one role NINA can't fill). Primary-DERIVED resolution is NOT subject to
        this (switching the whole rig to a ``nina`` primary that resolves
        ``switch`` is fine). Connection itself degrades per-role gracefully via
        the orchestrator - a single unreachable device does not fail the request.
        """
        from ..devices import backends as _b  # noqa: F401 - registration side-effect
        from ..devices.backend import RigSpec, ConnSpec, get_backend
        # "none" is not a registry backend -- it's the Equipment surface's
        # explicit-only rig mode (spec §4.1): only `roles` overrides are
        # requested, so there is no primary to look up in the registry.
        if body.primary != "none":
            try:
                get_backend(body.primary)
            except KeyError:
                raise HTTPException(422, f"unknown primary backend {body.primary!r}")
        for role, cs in body.roles.items():
            try:
                allowed = get_backend(cs.backend).roles
            except KeyError:
                raise HTTPException(
                    422, f"unknown backend {cs.backend!r} for role {role!r}")
            if role not in allowed:
                raise HTTPException(
                    422, f"backend {cs.backend!r} cannot fill role {role!r}")
        spec = RigSpec(
            primary=body.primary,
            roles={r: ConnSpec(**cs.model_dump()) for r, cs in body.roles.items()})
        try:
            return await hub.connect_rigspec(spec)
        except DeviceError as e:
            raise _err(e)
        except Exception as e:
            raise HTTPException(502, f"rig connection failed: {e}")

    @app.post("/api/disconnect", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def disconnect():
        if engine.running:
            await engine.abort()
        await hub.disconnect_all()
        return {"ok": True}

    @app.get("/api/status")
    @declare(CAP_VIEW_STATUS)
    async def status(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        # ``require`` returns the resolved principal so we can strip the precise
        # site fix (name/lat/lon/elevation_m -- made ABSENT, not nulled) for
        # callers lacking view.site_precise (viewer/operator).
        return _redact_site_for(await hub.poll_status(), principal)

    @app.get("/api/summary")
    @declare(CAP_VIEW_STATUS)
    async def summary(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        return _redact_site_for(hub.summary(), principal)

    # ------------------------------------------------------ config / site / optics

    def _config_payload(principal: Principal | None) -> dict:
        """REDACTED AppConfig dump + the server's computed optics readout (single
        source of truth for image-scale / FOV the UI never re-derives as logic),
        run through the site-precision strip seam for ``principal``.

        ``redacted`` blanks every alert sink's Telegram bot token — the only
        secret kept at rest — so the union sent over REST/WS never leaks it (4b
        §1.10). The union also carries the automation blocks (safety/escalation/
        alerts/deadman_url) appended to AppConfig.

        SECURITY (whole-branch review finding): every caller of this function --
        GET /api/config AND every config-WRITE route that echoes the fresh config
        back (POST /api/config, PUT/POST /api/site, PUT/POST /api/optics, POST
        /api/config/{providers,rotator,survey,drivers...}) -- MUST pass the
        requesting principal so the echo is stripped identically to the read
        surfaces. Redacting only inside GET /api/config left every write route
        echoing precise site coords bare to any authenticated caller (even a
        floor-only viewer via an empty-body POST /api/config), defeating
        view.site_precise. Redaction now happens HERE, once, so no call site can
        forget it."""
        cfg = config_store.cfg()
        payload = redacted(cfg) | {
            "optics_computed": hub.effective_optics(),
            # #129: ``redacted(cfg)`` above is the GLOBAL AppConfig — the LOSING
            # layer for every key an active profile overrides. Bound to a form
            # field it will happily show a provider or a focal length that is not
            # what the rig is running, with no tell of any kind; that is how a
            # profile-pinned simulator drove the polar aligner for twelve days.
            # ``effective`` names, per key, the value in force and WHICH LAYER
            # supplied it, so the panels can show the winner and mark the loser
            # instead of silently displaying it. See astrodeck/provenance.py for
            # the entry shape.
            "effective": effective_config(hub),
            # UX #32: the capture root is an ASTRODECK_CAPTURE_DIR env var with no
            # readout anywhere in the product, so the naming preview showed a
            # relative path and "88 GB free" named no volume. Read-only for now
            # (the report store, the frame counters and any in-flight run all hang
            # off this path — retargeting it live is not a settings toggle).
            "capture_dir": str(hub_module.CAPTURE_DIR),
        }
        return _redact_site_for(payload, principal)

    # ---------------------------------------------------- site-precision redaction
    # ``view.site_precise`` (admin-only; EXCLUDED from viewer/operator) is the
    # access-control decision for the observatory's EXACT GPS fix. The serving
    # payloads (poll_status / summary / redacted config) are built without a
    # principal, so we STRIP at the seam: any principal LACKING the cap has the
    # four precise-site keys (name, latitude, longitude, elevation_m) REMOVED
    # (absent, not nulled) while is_default/horizon_min_deg are retained (the UI
    # needs both and neither reveals location), and a holder gets the full block.
    # This is the ONLY place the cap is enforced, so every precise-site surface
    # (REST status/summary/config + the WS hello frame and status pushes) must
    # route through here. The helpers (_redact_site_for / _redact_ws_event)
    # are imported from .redact at module scope -- shared with the relay-tunneled
    # /ws handler so both /ws lanes redact identically.

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

    def _solar_block(ra_hours: float, dec_deg: float) -> dict | None:
        """Return a 409 detail dict if a GOTO should be blocked by the sun-
        exclusion cone (W1.10), else None. Unlike the horizon check this is
        site-independent (Sun RA/Dec is date-based) and is NOT bypassed by
        ``force`` -- disarming requires a solar session (config.solar_override)."""
        check = getattr(hub, "_check_solar", None)
        if not callable(check):
            return None
        try:
            check(ra_hours, dec_deg)
        except (DeviceError, ValueError) as e:
            return {"detail": str(e), "code": "sun_exclusion"}
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
                    or old.chat_id != sink.chat_id or old.kind != sink.kind
                    or old.smtp_host != sink.smtp_host or old.smtp_port != sink.smtp_port
                    or old.smtp_user != sink.smtp_user or old.smtp_from != sink.smtp_from
                    or old.smtp_to != sink.smtp_to):
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
        if body.cooling is not None:
            config_store.set_cooling(body.cooling)
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

    def _require_config_field_caps(body: ConfigPatchBody,
                                   principal: Principal) -> None:
        """Field-level RBAC for ``POST /api/config`` (plan field-level map).

        Presence is determined by ``model_fields_set`` (NOT value-vs-default), so
        a block explicitly sent (even == its default) is gated; an omitted block
        is free. Atomic + fail-closed: if ANY present block's cap is not held, the
        WHOLE request 403s and NOTHING is merged. An unknown/forbidden block (incl.
        a stray ``auth``/``remote`` key -- which ``ConfigPatchBody`` doesn't even
        model, so it can't be merged, but we still reject the body) also 403s.

        Block -> required capability:
          site                 -> config.site_optics
          site.horizon_min_deg -> ALSO config.safety (a safety floor)
          safety               -> config.safety
          cooling              -> config.safety   (warm-down ramp = hardware protection)
          escalation           -> config.alerts   (notification/recovery policy)
          alerts               -> config.alerts
          deadman_url          -> config.alerts
        """
        present = body.model_fields_set
        # Known, mapped blocks only. Any field on the body outside this map is a
        # programming error (a new block added without a cap) -> fail closed.
        block_caps = {
            "site": CAP_CONFIG_SITE_OPTICS,
            "safety": CAP_CONFIG_SAFETY,
            "cooling": CAP_CONFIG_SAFETY,
            "escalation": CAP_CONFIG_ALERTS,
            "alerts": CAP_CONFIG_ALERTS,
            "deadman_url": CAP_CONFIG_ALERTS,
        }
        for field in present:
            cap = block_caps.get(field)
            if cap is None:
                # Unmapped/unknown block (or a forbidden auth/remote key that
                # slipped through model config) -> reject the whole body.
                raise HTTPException(403, detail={
                    "detail": f"config block {field!r} is not permitted here",
                    "code": "forbidden_block"})
            if not principal.has(cap):
                raise HTTPException(403, detail={
                    "detail": f"capability not held for config block {field!r}",
                    "code": "forbidden"})
        # Nested: a present site.horizon_min_deg ALSO requires config.safety.
        if "site" in present and body.site is not None \
                and "horizon_min_deg" in body.site.model_fields_set:
            if not principal.has(CAP_CONFIG_SAFETY):
                raise HTTPException(403, detail={
                    "detail": "config.safety required to set site.horizon_min_deg",
                    "code": "forbidden"})
        # Nested: toggling the sun-exclusion cone (solar_avoidance /
        # solar_exclusion_deg) is the single disarm path for W1.10. It ALSO
        # requires config.solar_override (admin-only; an operator never holds it),
        # so disarming sun avoidance needs BOTH config.safety + config.solar_override
        # and can only be a deliberate solar-astronomy toggle.
        if "safety" in present and body.safety is not None:
            solar_fields = {"solar_avoidance", "solar_exclusion_deg"}
            if solar_fields & body.safety.model_fields_set:
                if not principal.has(CAP_CONFIG_SOLAR_OVERRIDE):
                    raise HTTPException(403, detail={
                        "detail": "config.solar_override required to change "
                                  "sun avoidance (solar session)",
                        "code": "forbidden"})

    @app.get("/api/config")
    @declare(CAP_VIEW_STATUS)
    async def get_config(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        return _config_payload(principal)

    @app.post("/api/config")
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY,
             CAP_CONFIG_ALERTS)
    async def post_config(body: ConfigPatchBody,
                          principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Partial-merge persist of any subset of the automation config (§1.10).
        Re-reads ``hub.site`` (config-backed property) implicitly, pushes the
        site to the mount when it changed, and broadcasts the redacted union.

        FIELD-LEVEL RBAC: ``require(view.status)`` is the floor (any authenticated
        caller); ``_require_config_field_caps`` then atomically enforces the
        per-block capability before any write."""
        _require_config_field_caps(body, principal)
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
        return _config_payload(principal)

    def _require_site_field_caps(body: SiteSaveBody, principal: Principal) -> None:
        """Field-level RBAC for ``PUT/POST /api/site`` (plan): site coords need
        ``config.site_optics``; a present ``horizon_min_deg`` (a safety floor)
        ALSO needs ``config.safety``. Atomic: any missing cap 403s before any
        write (the persisted config is left untouched)."""
        if not principal.has(CAP_CONFIG_SITE_OPTICS):
            raise HTTPException(403, detail={
                "detail": "config.site_optics required to save site",
                "code": "forbidden"})
        if "horizon_min_deg" in body.model_fields_set \
                and body.horizon_min_deg is not None \
                and not principal.has(CAP_CONFIG_SAFETY):
            raise HTTPException(403, detail={
                "detail": "config.safety required to set horizon_min_deg",
                "code": "forbidden"})

    @app.put("/api/site")
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY)
    async def put_site(body: SiteSaveBody,
                       principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        _require_site_field_caps(body, principal)
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
            # reconcile rather than silently clobber a co-user's field. The
            # conflict body rides the SAME two redaction seams as every other
            # config echo (bridge-review follow-up): ``redacted`` scrubs the
            # at-rest secrets (telegram tokens, auth/remote secrets, deadman
            # url) the raw ``model_dump()`` used to leak, and
            # ``_redact_site_for`` strips the precise site for a caller
            # lacking view.site_precise.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": _redact_site_for(
                    redacted(e.current) | {
                        "optics_computed": hub.effective_optics()},
                    principal)})
        push = getattr(hub, "push_site_to_mount", None)
        if callable(push):
            try:
                await push()
            except Exception as e:
                bus.log("warning", f"could not push site to mount: {e}", "config")
        bus.publish("config", version=cfg.version)
        return _config_payload(principal)

    # thin alias kept for backward compat (referenced nowhere in UI, cheap)
    @app.post("/api/site")
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY)
    async def post_site(body: SiteSaveBody,
                        principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        return await put_site(body, principal)

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

    @app.put("/api/optics")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def put_optics(
            body: OpticsSaveBody,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            # P2-3: offload the blocking disk write off the event loop.
            cfg = await asyncio.to_thread(
                config_store.set_optics, body.optics, body.version)
        except ConfigVersionConflict as e:
            # same two-seam redaction as the put_site conflict body above.
            raise HTTPException(409, detail={
                "detail": str(e),
                "current": _redact_site_for(
                    redacted(e.current) | {
                        "optics_computed": hub.effective_optics()},
                    principal)})
        bus.publish("config", version=cfg.version)
        return _config_payload(principal)

    # atlas alias: also seeds hub.optics (same persisted object)
    @app.post("/api/optics")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def post_optics(
            body: OpticsSaveBody,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        return await put_optics(body, principal)

    @app.get("/api/site/sky")
    @declare(CAP_VIEW_STATUS)
    async def site_sky(lat: float | None = None, lon: float | None = None,
                       principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        from ..catalog import coords
        s = config_store.cfg().site
        # THE lat/lon QUERY OVERRIDES ARE A HOLDER-ONLY FEATURE. A route that
        # answers "what is the sun's altitude at the coordinates I name" is a
        # geolocation oracle no matter how carefully its DEFAULT path is
        # redacted: a caller sweeps candidate coordinates and keeps whichever
        # reproduces the readings it already has. They exist for the site picker,
        # which is admin-only anyway.
        if (lat is not None or lon is not None) \
                and not principal.has(CAP_VIEW_SITE_PRECISE):
            raise HTTPException(403, "naming coordinates requires view.site_precise")
        latitude = s.latitude if lat is None else lat
        longitude = s.longitude if lon is None else lon
        out: dict = {}
        # sun_alt_deg and dark_window are the SAME INFORMATION as the place_hint
        # and lst_str withheld below, arrived at by arithmetic: solar altitude
        # over a night gives latitude, and the dark-window boundaries give
        # longitude to within minutes. Withholding the two obvious geolocators
        # while serving the two computed ones was the shape of this whole class
        # of leak. Holders of view.site_derived (operator, admin) get them.
        if principal.has(CAP_VIEW_SITE_DERIVED):
            sun = coords.sun_altaz(latitude, longitude)
            sun_alt = sun[0] if isinstance(sun, (tuple, list)) else float(sun)
            out["sun_alt_deg"] = round(sun_alt, 1)
            out["dark_window"] = coords.dark_window(latitude, longitude)
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

    # ------------------------------------------------------------------- safety

    @app.get("/api/safety/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
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

    @app.post("/api/safety/simulate", dependencies=[Depends(require(CAP_CONFIG_SAFETY))])
    @declare(CAP_CONFIG_SAFETY)
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

    @app.get("/api/alerts", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_alerts():
        """Configured alert sinks with the token blanked (never sent to the
        client — the only secret kept at rest). `token_configured` is a derived
        marker so the UI can tell a configured secret from an empty one."""
        return [
            {**s.model_copy(update={"token": ""}).model_dump(),
             "token_configured": bool(s.token)}
            for s in config_store.cfg().alerts
        ]

    @app.post("/api/alerts", dependencies=[Depends(require(CAP_CONFIG_ALERTS))])
    @declare(CAP_CONFIG_ALERTS)
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
                    or old.chat_id != sink.chat_id or old.kind != sink.kind
                    or old.smtp_host != sink.smtp_host or old.smtp_port != sink.smtp_port
                    or old.smtp_user != sink.smtp_user or old.smtp_from != sink.smtp_from
                    or old.smtp_to != sink.smtp_to):
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
        return [{**s.model_copy(update={"token": ""}).model_dump(),
                 "token_configured": bool(s.token)}
                for s in config_store.cfg().alerts]

    @app.delete("/api/alerts/{sink_id}", dependencies=[Depends(require(CAP_CONFIG_ALERTS))])
    @declare(CAP_CONFIG_ALERTS)
    async def delete_alert(sink_id: str):
        alerts = [s for s in config_store.cfg().alerts if s.id != sink_id]
        await asyncio.to_thread(config_store.set_alerts, alerts)
        bus.publish("config", config=redacted(config_store.cfg()))
        return {"deleted": sink_id}

    @app.post("/api/alerts/{sink_id}/test", dependencies=[Depends(require(CAP_CONFIG_ALERTS))])
    @declare(CAP_CONFIG_ALERTS)
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

    @app.get("/api/alerts/health", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def alerts_health():
        """Dispatcher runtime health (queue depth + dead-man state) for the
        Settings → Alerts panel. Pure read; never does I/O."""
        return dispatcher.health()

    # ------------------------------------------------------------------- reports

    @app.get("/api/reports", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_reports():
        """Newest-first session-report summaries (no frame detail)."""
        return await asyncio.to_thread(SessionReporter.list_reports)

    @app.get("/api/reports/{report_id}")
    @declare(CAP_VIEW_STATUS)
    async def get_report(report_id: str,
                         principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """One report + read-time-derived trend sparklines (404 if missing). The
        trends are computed from the frame records on read, never stored as
        parallel arrays that could drift (C1-19).

        Frame paths are stripped for a caller without ``config.backend``, the
        same holder rule ``/api/sessions/{id}`` applies to the same frames."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        trends = SessionReporter.trends(report)
        return _redact_report_for(report.model_dump(), principal) | {"trends": trends}

    @app.get("/api/reports/{report_id}/frames.csv")
    @declare(CAP_VIEW_STATUS)
    async def report_frames_csv(report_id: str,
                                principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Append-only frame list as CSV (power-user export). 404 if missing.

        Carries EVERY field the JSON record carries (UX #49: gain / offset /
        binning / ecc / altitude were silently dropped, so the CSV could not be
        used to sort subs the report viewer could already rank), and pairs the
        raw epoch ``ts`` with a readable UTC stamp instead of shipping
        ``1785084747.5023835`` alone."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        buf = io.StringIO()
        # A CSV export is not a loophole around the JSON route's redaction —
        # same holder rule, same field, applied to the column list.
        cols = report_csv_columns(
            ["ts", "ts_utc", "target", "filter", "frame_type", "exposure_s",
             "gain", "offset", "binning", "accepted", "hfr", "ecc",
             "sensor_temp_c", "guide_rms_total", "altitude_deg", "saved_path"],
            principal)
        import csv
        w = csv.writer(buf)
        w.writerow(cols)
        for fr in report.frames:
            d = fr.model_dump()
            d["ts_utc"] = _iso_utc(d.get("ts"))
            w.writerow(["" if d.get(c) is None else d.get(c) for c in cols])
        # Use the sanitized slug (not the raw path param) so the response header
        # can never carry CR/LF/quotes from attacker-controlled input.
        fname = f"{_slug(report_id)}.frames.csv"
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/api/reports/{report_id}/bundle", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def report_bundle(report_id: str, weight_altitude: bool = False,
                            layout: str = "grouped",
                            keep_threshold: float | None = None):
        """Slim stacking-bundle preview (per-group counts + master-match status +
        warnings) for the report viewer panel (PRO-10 §1.5). 404 if missing.
        ``weight_altitude`` (opt-in) folds a sin(alt) term into the sub weights.
        ``layout`` picks the folder convention; ``keep_threshold`` (a normalized
        weight in [0,1]) makes each group report ``kept_count`` — one scalar
        instead of shipping a 2000-row weight vector to the client."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        try:
            b = build_bundle(report, _get_master_library(),
                             is_local=hub._is_local_save, layout=layout,
                             weight_altitude=weight_altitude,
                             keep_threshold=keep_threshold)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return bundle_summary(b)

    @app.get("/api/reports/{report_id}/bundle.zip", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def report_bundle_zip(report_id: str, weight_altitude: bool = False,
                                layout: str = "grouped",
                                keep_threshold: float | None = None):
        """The stacking bundle as an in-memory ``.zip`` (manifest + weights CSV +
        README + build.sh/.ps1 — NOT the FITS; §4 decision 1). Mirrors
        ``report_frames_csv``: the sanitized slug (never the raw path param) forms
        the download filename so the header can't carry CR/LF/quotes.
        ``weight_altitude`` (opt-in) folds a sin(alt) term into the sub weights;
        ``layout``/``keep_threshold`` are the PRO-10 enrichments (defaults keep the
        one-click download byte-for-byte what it was)."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        try:
            b = build_bundle(report, _get_master_library(),
                             is_local=hub._is_local_save, layout=layout,
                             weight_altitude=weight_altitude,
                             keep_threshold=keep_threshold)
        except ValueError as e:
            raise HTTPException(400, str(e))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest_json(b), indent=2))
            z.writestr("weights.csv", weights_csv(b))
            z.writestr("README.txt", readme_text(b))
            z.writestr("build.sh", build_script(b, "sh"))
            z.writestr("build.ps1", build_script(b, "ps1"))
        fname = f"{_slug(report_id)}.bundle.zip"
        return Response(buf.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})

    @app.post("/api/reports/{report_id}/bundle/materialize",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def report_bundle_materialize(report_id: str,
                                        weight_altitude: bool = False,
                                        layout: str = "grouped",
                                        keep_threshold: float | None = None):
        """Lay the ACTUAL FITS out under ``captures/exports/<id>/`` for someone
        running AstroDeck ON the capture box — hardlinks where possible, so a
        200 GB night materializes instantly and costs no extra disk (§2.4).

        POST + **CAP_CONTROL_CAPTURE**, unlike the other bundle routes: this one
        WRITES to the capture box's filesystem, so it needs the same authority as
        capturing, and a verb no browser will prefetch. Returns a summary only —
        no file body; the bytes are on disk where the user's stacker can see them.

        The sources are provably under CAPTURE_DIR (``build_bundle`` selects only
        ``is_local`` lights); masters may legitimately live in a shared library
        elsewhere, and they are library-chosen, not user-supplied. Every
        DESTINATION is re-validated for containment by
        ``bundle_materialize_plan``."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        try:
            b = build_bundle(report, _get_master_library(),
                             is_local=hub._is_local_save, layout=layout,
                             weight_altitude=weight_altitude,
                             keep_threshold=keep_threshold)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not b.groups:
            raise HTTPException(
                400, "no local light subs on this machine — nothing to materialize "
                     "(download bundle.zip and run build.sh on your imaging host)")
        root = _export_root(report_id)
        return await asyncio.to_thread(_materialize_bundle, b, root)

    # ----------------------------------------------------------------- profiles

    @app.get("/api/profiles", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_profiles():
        # Rows carry no device ``extra`` today, but redact defensively so no
        # secret-bearing ``extra`` value can ever cross the wire from this route.
        return [redact_profile(r)
                for r in profiles.list(config_store.cfg().active_profile_id)]

    @app.get("/api/profiles/{profile_id}", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_profile(profile_id: str):
        try:
            # Wire-redaction (W2): scrub secret-bearing device ``extra`` values
            # before this VIEWER-visible read leaves the server; the at-rest
            # profile keeps the real value so the rig can still connect.
            return redact_profile(profiles.get(profile_id))
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")

    @app.post("/api/profiles", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
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

    @app.post("/api/profiles/capture", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def capture_profile(body: ProfileCaptureBody):
        if hub.mode == "none" or not hub.devices:
            raise HTTPException(409, "connect a rig before saving a profile")
        return await hub.capture_profile(body.name)

    @app.patch("/api/profiles/{profile_id}", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def rename_profile(profile_id: str, body: ProfileRenameBody):
        try:
            row = await asyncio.to_thread(profiles.rename, profile_id, body.name)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        return row

    @app.post("/api/profiles/{profile_id}/clear-overrides",
              dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def clear_profile_overrides(profile_id: str,
                                      body: ProfileClearOverridesBody):
        """Remove a profile's provider pins and/or its optics block.

        The counterpart to the ``effective`` provenance readout: that block can
        now tell a user "profile X pins the simulator for polar alignment", and
        this is the route that lets them undo it. Without it the disclosure is a
        dead end — the Profiles tab has no editor and the only other write is a
        whole-profile POST, whose payload the client can only obtain from a
        wire-REDACTED GET (round-tripping it would persist blanked device
        credentials).

        The active-profile CACHE is invalidated the same way every other profile
        write does it. That is load-bearing here rather than hygienic: the cache
        is what ``providers.override_with_layer`` and ``profiles.resolve_optics``
        read, so a stale entry would leave the cleared pin still winning while
        the UI, having re-fetched /api/config, showed it as gone — the same
        console-disagrees-with-rig failure this whole change exists to end. The
        ``config`` event then pushes the corrected provenance to every other
        connected client, so a second tablet does not keep displaying the pin.
        """
        try:
            row = await asyncio.to_thread(
                profiles.clear_overrides, profile_id,
                providers=body.providers, optics=body.optics)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        bus.publish("config", config=redacted(config_store.cfg()))
        return row

    @app.post("/api/profiles/{profile_id}/set-providers",
              dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def set_profile_providers(profile_id: str,
                                    body: ProfileSetProvidersBody):
        """Write capability pins into a profile — the EDIT half of #129/#132.

        ``POST /api/config/providers`` writes the GLOBAL block, and the ACTIVE
        PROFILE beats it inside ``providers.override_with_layer``. So the Tasks
        dropdown, having been fixed to display the WINNING layer, could show
        "pinned by profile Rig1" and then accept a change that went to the layer
        the profile shadows: green toast, nothing different on the rig. This
        route is where a save lands when the profile is the layer in force, so
        the console edits the thing it is displaying.

        Deliberately shaped like ``clear-overrides`` rather than as a second
        convention: same cap, same at-rest mutation (never a client
        read-modify-write — ``GET /api/profiles/{id}`` is wire-redacted and
        round-tripping it would persist blanked device credentials), same cache
        invalidation, same ``config`` broadcast, same row response.

        The active-profile CACHE invalidation is load-bearing, not hygiene:
        ``providers.override_with_layer`` reads that cache, so a stale entry
        would leave the OLD pin resolving while the UI, having re-fetched
        /api/config, displayed the new one — recreating the console-disagrees-
        with-rig failure inside the fix for it. The ``config`` event then pushes
        the corrected provenance to every other connected client so a second
        tablet does not keep showing the previous pin.
        """
        try:
            row = await asyncio.to_thread(
                profiles.set_providers, profile_id, body.providers)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        except ValueError as e:
            # Same 422 contract the global providers route has, so one client-side
            # error path covers both layers.
            raise HTTPException(422, str(e))
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        bus.publish("config", config=redacted(config_store.cfg()))
        return row

    @app.delete("/api/profiles/{profile_id}", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def delete_profile(profile_id: str):
        # profiles.delete resolves through safe_id_path, which raises KeyError on
        # a refused id, and both stores' docstrings already promise "routes map
        # KeyError -> 404". This route never kept that promise: there is no
        # global exception handler, so `DELETE /api/profiles/..%5Cx` returned a
        # 500 with a full traceback (absolute paths included) while every sibling
        # -- calibration masters, sessions, plan export -- returned 404. Nothing
        # was ever deleted; the guard held. The 500 just confirmed the input
        # reached an unhandled path, which is itself an answer worth denying.
        try:
            await asyncio.to_thread(profiles.delete, profile_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "profile not found")
        invalidate = getattr(hub, "invalidate_profile_cache", None)
        if callable(invalidate):
            invalidate()
        return {"deleted": profile_id}

    @app.post("/api/profiles/{profile_id}/apply", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
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
        # Route through _spawn_connect (NOT _spawn): apply_profile's first step is
        # a full teardown, and _spawn would park this driver in hub._busy["profile"]
        # — which the teardown's busy-cancel loop then cancels, so apply always
        # cancelled itself mid-teardown (zombie half-connected rig). _spawn_connect
        # keeps the driver OUT of _busy (same single lane as activate).
        return _spawn_connect(hub.apply_profile(prof))

    @app.post("/api/profiles/{profile_id}/activate", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def activate_profile(profile_id: str,
                               body: ProfileApplyBody | None = None):
        """Set a profile active AND connect its rig (W1.6 / C2). Reuses the
        ``_spawn_connect`` convention so it can't run concurrently with ``apply``
        and streams progress over the WS. The active pointer is set INSIDE
        ``connect_profile_id`` only after a successful connect, so activating a
        rig that can't come up doesn't strand the pointer (boot will still
        degrade-connect it - intended).

        404 when the id is unknown; 409 when a sequence / capture loop / polar
        alignment is running and ``force`` is not set (the connect is destructive
        - it disconnects the current rig)."""
        force = bool(body and body.force)
        if not _profile_exists(profile_id):
            raise HTTPException(404, "profile not found")
        if (engine.running or hub.looping or hub.polar.running) and not force:
            raise HTTPException(409, detail={
                "detail": "a sequence, capture loop or polar alignment is running",
                "code": "running"})
        if force and engine.running:
            await engine.abort()
        return _spawn_connect(hub.connect_profile_id(profile_id))

    # -------------------------------------------------------------------- plans

    @app.get("/api/plans", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_plans():
        return plan_library.list()

    @app.get("/api/plans/{plan_id}", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_plan(plan_id: str):
        try:
            return plan_library.get(plan_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")

    @app.post("/api/plans", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
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

    @app.delete("/api/plans/{plan_id}", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def delete_plan(plan_id: str):
        # Same 500-instead-of-404 as delete_profile above; same fix.
        try:
            await asyncio.to_thread(plan_library.delete, plan_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")
        return {"deleted": plan_id}

    @app.get("/api/plans/{plan_id}/export", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def export_plan(plan_id: str):
        try:
            raw = plan_library.export_bytes(plan_id)
            name = plan_library.get(plan_id).name or plan_id
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "plan"
        return Response(raw, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{safe}.astroplan.json"'})

    @app.post("/api/plans/import", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
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

    # ------------------------------------------------ calibration library (PRO-1)

    @app.get("/api/calibration/masters", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_masters():
        # frame_type is stored upper-case internally (DARK/BIAS/FLAT) but the UI
        # FrameType union (reused from NOV-10 calibration.ts) is title-case, so
        # normalize at this one boundary: "DARK" -> "Dark".
        masters = await asyncio.to_thread(cal_library.list_masters)
        return [{**vars(m), "frame_type": m.frame_type.title()} for m in masters]

    @app.post("/api/calibration/build", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def build_masters():
        c = config_store.cfg().calibration
        rep = await asyncio.to_thread(
            cal_library.build, sigma=c.stack_sigma, temp_bin_width=c.temp_bin_c,
            max_frames=c.max_stack_frames)
        return vars(rep)

    @app.delete("/api/calibration/masters/{master_id}",
                dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def delete_master(master_id: str):
        try:
            await asyncio.to_thread(cal_library.delete, master_id)
        except KeyError:
            raise HTTPException(404, "master not found")
        return {"deleted": master_id}

    # ------------------------------------------------ multi-night sessions (§6)

    @app.get("/api/sessions", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_sessions():
        return {"sessions": await asyncio.to_thread(session_store.list)}

    @app.get("/api/sessions/{session_id}")
    @declare(CAP_VIEW_STATUS)
    async def get_session(session_id: str,
                          principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        # frame-path strip for a caller lacking config.backend (spec §8) — the
        # same endpoint==filesystem-identity rule as the driver-row redaction.
        return _redact_session_for(s.model_dump(), principal)

    @app.post("/api/sessions/{session_id}/resume",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def resume_session(session_id: str):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status != "dormant":
            raise HTTPException(409, f"session is {s.status}, not dormant")
        # Same unbounded accepted-quota guard as /api/sequence/start and
        # /api/sequence/recover (Task 4 review, IMPORTANT): resume starts the
        # engine on this same loop, so a session carrying the unbounded
        # combination (accepted mode, both reject guards off, no stop boundary)
        # must be refused here too — not just on the original start.
        if quota_unbounded(s.plan):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
        try:
            hub.require("camera")
            engine.start(s.plan, session=s)
        except DeviceError as e:
            raise _err(e)
        return {"resumed": True, "remaining": sum(s.remaining().values())}

    @app.patch("/api/sessions/{session_id}",
               dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def patch_session(session_id: str, body: SessionPatchBody):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        merge = None
        if body.plan is not None:
            # id-safe plan edit (spec §4): DORMANT only; running never editable.
            if s.status != "dormant":
                raise HTTPException(409, "plan edits require a dormant session")
            old_ids = {st.id for t in s.plan.targets for st in t.steps}
            new_ids = {st.id for t in body.plan.targets for st in t.steps}
            with_frames = {f.step_id for f in s.frames}
            merge = {"kept": sorted(old_ids & new_ids),
                     "new": sorted(new_ids - old_ids),
                     "dropped": sorted((old_ids - new_ids) & with_frames)}
            s.plan = body.plan
            s.name = body.plan.name or s.name
        if body.status is not None:
            if body.status != "abandoned":
                raise HTTPException(422, "status can only be set to 'abandoned'")
            if s.status == "active":
                raise HTTPException(409, "cannot abandon a running session")
            s.status = "abandoned"
            s.auto_resume = False
        if body.auto_resume is not None:
            # ARMING AN ACTIVE SESSION IS THE POINT, NOT AN EDGE CASE.
            #
            # This was dormant-only, written for the feature's original purpose
            # ("I have stopped for tonight, resume at dusk tomorrow"), where
            # dormant is true by definition. But a restart destroys an ACTIVE
            # session: boot_sweep then finds it dormant and UNARMED, and nobody
            # is awake at 2am to arm it. So the run that most needed to come
            # back was the exact run that could not be told to.
            #
            # Demonstrated on the rig 2026-08-02 by rebooting the observatory PC
            # mid-sequence: the box auto-logged in, the server returned, every
            # device reconnected and the boot sweep correctly rescued the run --
            # which then sat dormant and idle all night, because of this line.
            #
            # 'complete'/'abandoned' stay refused: there is nothing left to resume.
            if body.auto_resume and s.status not in ("dormant", "active"):
                raise HTTPException(
                    409, "auto-resume arms only dormant or active sessions")
            if body.auto_resume:
                # server-enforced singleton (spec §5): arming here disarms others.
                for other in await asyncio.to_thread(session_store.load_all):
                    if other.id != s.id and other.auto_resume:
                        other.auto_resume = False
                        await asyncio.to_thread(session_store.save, other)
            s.auto_resume = body.auto_resume
        await asyncio.to_thread(session_store.save, s)
        out = {"id": s.id, "status": s.status, "auto_resume": s.auto_resume,
               "remaining": s.remaining()}
        if merge is not None:
            out["merge"] = merge
        return out

    @app.patch("/api/sessions/{session_id}/frames/{frame_id}",
               dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def patch_session_frame(session_id: str, frame_id: str,
                                  body: FramePatchBody):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status == "active":
            # regrade is a between-nights operation (spec: manual regrade UI
            # between nights); also prevents store-vs-engine copy divergence.
            raise HTTPException(409, "session is running — regrade between nights")
        frame = next((f for f in s.frames if f.id == frame_id), None)
        if frame is None:
            raise HTTPException(404, "frame not found")
        if "override" in body.model_fields_set:      # omitted ≠ explicit null
            if body.override not in ("accept", "reject", None):
                raise HTTPException(422, "override must be 'accept', 'reject' or null")
            frame.override = body.override
        if body.metrics is not None:
            # float-merge: the external-grader write path (spec §10). pydantic
            # already coerced values to float (non-numeric -> 422).
            frame.metrics.update({k: float(v) for k, v in body.metrics.items()})
        await asyncio.to_thread(session_store.save, s)
        return {"frame": frame.model_dump(), "remaining": s.remaining()}

    @app.delete("/api/sessions/{session_id}",
                dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def delete_session(session_id: str):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        if s.status == "active":
            raise HTTPException(409, "cannot delete a running session")
        # Task 6 carry-in: natural completion does NOT drain thumb renders (only
        # abort() does), so a fire-and-forget render for THIS session may still
        # be mid-write — and its trailing session_store.save would RESURRECT the
        # JSON we are about to remove. Drain the matching renders before the
        # rmtree. (The actively-running session was refused above, never here.)
        await engine.drain_thumbs_for_session(session_id)
        # session file + thumbs only — NEVER the FITS frames (spec §6).
        await asyncio.to_thread(session_store.delete, session_id)
        return {"deleted": session_id}

    @app.get("/api/sessions/{session_id}/frames/{frame_id}/thumb",
             dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def session_frame_thumb(session_id: str, frame_id: str):
        try:
            tdir = session_store.thumbs_dir(session_id)
            path = safe_id_path(tdir, frame_id, suffix=".jpg")
        except KeyError:
            raise HTTPException(404, "not found")
        if not path.exists():
            raise HTTPException(404, "no thumbnail")
        return FileResponse(path, media_type="image/jpeg")

    # -------------------------------------------------------------- capture

    @app.post("/api/capture", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def capture(body: CaptureBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        # Camera mutual exclusion: a running sequence owns the camera all night, so
        # a stray single capture must not interleave its exposures (mis-stamped /
        # cross-downloaded frames). The hub exposure guard is the last line of
        # defense; reject up front for a clear error.
        if engine.running:
            raise HTTPException(409, "a sequence is running")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("capture", hub.capture(
            body.exposure_s, body.gain, body.offset, body.binning,
            save=body.save, target=body.target, frame_type=body.frame_type))

    @app.post("/api/capture/loop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def capture_loop(body: CaptureBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        if engine.running:
            raise HTTPException(409, "a sequence is running")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        # start_loop awaits the previous loop's teardown before spawning the
        # replacement, so a rapid restart can't leave the old loop's cancel-abort
        # racing the new loop's first frame.
        await hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning,
                             frame_type=body.frame_type)
        return {"looping": True}

    @app.post("/api/capture/stop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
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

    # ---- Live View (NOV-1): arm/reset/disarm, mirroring capture_loop -------
    @app.post("/api/capture/livestack/start", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def livestack_start(body: LiveStackBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        if engine.running:
            raise HTTPException(409, "a sequence is running")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        hub.start_live_stack(reject_frac=body.reject_frac,
                             clip_sigma=body.clip_sigma)
        await hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning,
                             frame_type="Light")
        return {"active": True}

    @app.post("/api/capture/livestack/reset", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def livestack_reset():
        return hub.reset_live_stack()

    @app.post("/api/capture/livestack/stop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def livestack_stop():
        hub.stop_loop()
        return hub.stop_live_stack()

    # ---- live-preview routes (live-preview spec §4.4) ---------------------
    # Canonical URL: the client builds `/api/preview/{id}` and reads `mime` from
    # the event. `/lossless.png` / `/thumb.jpg` / `/fits` / `/png` are the
    # paused-zoom / filmstrip / FITS-download / PNG-download variants. `/crop`
    # (sensor-1:1 ROI) and `/render.png` (baked-stretch export) read the retained
    # linear array.

    _PREVIEW_CACHE = {"Cache-Control": "max-age=3600"}

    @app.get("/api/preview/{preview_id:int}", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
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

    @app.get("/api/preview/{preview_id}.png", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
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

    @app.get("/api/preview/{preview_id}/lossless.png", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
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

    @app.get("/api/preview/{preview_id}/thumb.jpg", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def preview_thumb(preview_id: int):
        """~160px JPEG thumbnail for the filmstrip (kept for many frames)."""
        thumb = hub.preview_thumbs.get(preview_id)
        if thumb is None:
            entry = hub.previews.get(preview_id)
            thumb = entry.thumb if entry else None
        if not thumb:
            raise HTTPException(404, "thumbnail expired")
        return Response(thumb, media_type="image/jpeg", headers=_PREVIEW_CACHE)

    @app.get("/api/preview/{preview_id}/fits", dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
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

    @app.get("/api/preview/{preview_id}/png", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
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

    @app.get("/api/preview/{preview_id}/share.jpg", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def preview_share(preview_id: int, target: str = "", subs: int = 0):
        """Captioned, phone-sized shareable JPEG of this frame (NOV-11).

        Composites a caption band (target · exposure×count · date) onto the
        already-stretched display bytes. Caption carries NO location — target,
        exposure, count, gain and date only."""
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        m = entry.meta
        date_str = fmt_share_date(m.get("ts") or time.time())
        title, detail = build_caption(
            target or None, m.get("exposure_s", 0.0),
            subs or None, date_str, m.get("gain"))
        base = entry.lossless or entry.display
        jpeg, _w, _h = await asyncio.to_thread(compose_share_jpeg, base, title, detail)
        safe = sanitize_component(target, "loose") or f"preview_{preview_id}"
        return Response(jpeg, media_type="image/jpeg", headers={
            **_PREVIEW_CACHE,
            "Content-Disposition": f'attachment; filename="firstlight_{safe}.jpg"'})

    @app.get("/api/preview/{preview_id}/crop", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def preview_crop(preview_id: int, x: int = 0, y: int = 0,
                           w: int = 0, h: int = 0):
        """Sensor-1:1 (no downscale) auto-stretched PNG of an ROI cut from the
        LINEAR frame data — a pixel-peep zoom. The linear array is held only for
        the latest 1–2 frames, so an expired/old preview returns 404. ``w``/``h``
        <= 0 default to the rest of the frame from ``(x, y)``; the ROI is clamped
        inside the sensor so out-of-range params can never over-read."""
        entry = hub.previews.get(preview_id)
        if entry is None or entry.linear is None:
            raise HTTPException(404, "preview linear data unavailable")
        arr = entry.linear
        ny, nx = arr.shape[:2]
        x0 = max(0, min(int(x), nx - 1))
        y0 = max(0, min(int(y), ny - 1))
        w0 = (nx - x0) if int(w) <= 0 else min(int(w), nx - x0)
        h0 = (ny - y0) if int(h) <= 0 else min(int(h), ny - y0)
        crop = arr[y0:y0 + h0, x0:x0 + w0]
        # 1:1: max_width >= the crop width so `_encode` never downscales.
        png = await asyncio.to_thread(to_png, crop, True, max(1, int(crop.shape[1])))
        return Response(png, media_type="image/png", headers=_PREVIEW_CACHE)

    @app.get("/api/preview/{preview_id}/render.png", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def preview_render(preview_id: int, black: float = 0.0,
                             mid: float = 0.5, white: float = 1.0):
        """Full-resolution, server-side baked PNG of the LINEAR frame at the given
        stretch levels (``black``/``mid``/``white`` — the same LUT as the live
        preview, so a baked export matches what's on screen). The linear array is
        held only for the latest 1–2 frames, so an expired preview returns 404."""
        entry = hub.previews.get(preview_id)
        if entry is None or entry.linear is None:
            raise HTTPException(404, "preview linear data unavailable")
        arr = entry.linear
        # Full native resolution (an export, not the bandwidth-capped live view).
        png = await asyncio.to_thread(
            to_png, arr, True, max(1, int(arr.shape[1])), True,
            black=black, mid=mid, white=white)
        return Response(png, media_type="image/png", headers=_PREVIEW_CACHE)

    @app.post("/api/camera/cooler", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def cooler(body: CoolerBody):
        """Cool to a setpoint, or start the warm-down RAMP.

        Warming used to be ``set_cooler(False)`` straight through to the driver.
        Measured on the rig 2026-08-04, that took the sensor 8.3 → 11.8 °C in
        ~40 s: thermal shock plus in-chamber condensation, every time anyone
        pressed Warm. It now hands off to the hub, which walks the SETPOINT up to
        ambient in the background and only then switches the TEC off — so this
        route still answers in milliseconds and the caller gets the ramp state to
        render, not a ten-minute blocking request.

        Cooling routes through the hub too, because it has to CANCEL a ramp in
        flight: without that, the ramp's next setpoint step (≤15 s away) would
        silently overwrite the target the user just typed."""
        try:
            hub.require("camera")           # 400 when there is no camera at all
            if body.on:
                await hub.cool_camera(body.target_c)
                return {"ok": True}
            return {"ok": True, "warm": await hub.warm_camera(source="user",
                                                              ramp=body.ramp)}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/camera/dew-heater", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def dew_heater(body: DewBody):
        try:
            cam = hub.require("camera")
            await cam.set_dew_heater(body.power)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/camera/egain/learn",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def learn_egain(body: EgainLearnBody):
        """Measure the camera's conversion gain (e-/ADU) by mean-variance.

        Camera-exclusive (it takes its own bias/flat frames), so it 409s while a
        capture loop or sequence owns the camera. The measured value is stored
        per gain and used ONLY when the driver reports no egain."""
        if engine.running or hub.looping:
            raise HTTPException(409, "camera is busy (a capture loop or sequence "
                                     "is running)")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("egain", hub.learn_egain(
            gain=body.gain, count=body.count, exposure_s=body.exposure_s,
            offset=body.offset, binning=body.binning))

    # ------------------------------------------------------- flat calibrator

    @app.post("/api/calibrator/on", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_on(body: CalibratorBody):
        try:
            await hub.calibrator_on(body.brightness)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/calibrator/off", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_off():
        try:
            await hub.calibrator_off()
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/calibrator/cover", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def calibrator_cover(body: CoverBody):
        try:
            await (hub.open_cover() if body.open else hub.close_cover())
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # ---------------------------------------------------------------- mount

    @app.post("/api/mount/goto", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.slew"})
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
        # Sun-exclusion cone (W1.10). The centered path inherits this inside
        # goto_and_center; the plain path does NOT route through it, so gate it
        # here. force does NOT bypass the cone (only the horizon check above).
        solar = _solar_block(body.ra_hours, body.dec_deg)
        if solar is not None:
            raise HTTPException(409, detail=solar)
        if body.center:
            return _spawn("goto", hub.goto_and_center(body.ra_hours, body.dec_deg,
                                                       rotation_deg=body.rotation_deg))

        async def plain_goto():
            tel = hub.require("telescope")
            # Motion fence (W3.7): serialize the device-touching commit under the
            # hub motion lock and re-check the epoch immediately before dispatch,
            # so a STOP/abort that lands while this is awaiting (e.g. a stale
            # REMOTE goto racing a LOCAL abort) is fenced out at the mount.
            epoch = hub._motion_epoch
            async with hub._motion_lock:
                if not hub._motion_committed_clean(epoch):
                    bus.log("warning", "goto abandoned: aborted before motion", "mount")
                    return
                if await tel.is_parked():
                    await tel.unpark()
                await tel.set_tracking(True)
                if not hub._motion_committed_clean(epoch):
                    bus.log("warning", "goto abandoned: aborted before slew", "mount")
                    return
                await tel.slew(body.ra_hours, body.dec_deg)
            bus.publish("mount", action="slew_complete")
        return _spawn("goto", plain_goto())

    @app.post("/api/mount/solve_sync", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.sync"})
    async def solve_sync():
        try:
            hub.require("telescope"), hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("solve", hub.solve_and_sync())

    @app.post("/api/mount/move", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.move_axis"})
    async def move_axis(body: MoveAxisBody):
        try:
            tel = hub.require("telescope")
            # Touch-safety rate clamp: manual slew is capped at ±TOUCH_MAX_RATE
            # (worst-case ≤0.72° uncommanded travel at the 1.2s deadman). Gross
            # repositioning is GOTO's job; no 2-4°/s manual band exists.
            rate = max(-TOUCH_MAX_RATE_DEG_S,
                       min(TOUCH_MAX_RATE_DEG_S, body.rate_deg_s))
            # Sun-exclusion cone (W1.10) for manual jog: a non-zero move while the
            # mount is ALREADY pointed inside the cone would dwell/drive at the
            # Sun. Gate on the CURRENT pointing (best-effort: if we can't read the
            # position, fall through rather than block a stop). A rate-0 stop is
            # always allowed. force has no meaning here -- only a solar session
            # (solar_avoidance=False) makes _check_solar inert.
            check_solar = getattr(hub, "_check_solar", None)
            if rate != 0.0 and callable(check_solar):
                try:
                    cur_ra, cur_dec = await tel.get_position()
                except Exception:
                    cur_ra = cur_dec = None
                if cur_ra is not None:
                    try:
                        check_solar(cur_ra, cur_dec)
                    except DeviceError as e:
                        raise HTTPException(409, detail={
                            "detail": str(e), "code": "sun_exclusion"})
            # F-A1 (safety-of-motion): arm the deadman with the actual (clamped)
            # rate BEFORE the move await, so the watchdog already covers the axis
            # if move_axis is cancelled/raises mid-flight (no uncovered moving
            # axis), and the keepalive cadence never inherits driver RTT. Arming
            # first is safe: if move_axis raises, the next tick issues a redundant
            # tel.stop() on a non-moving axis (harmless), and a rate-0 stop leaves
            # the deadman idle so there is no spurious halt.
            hub.note_move(body.axis, rate)
            # Motion fence (W3.7): serialize the jog's device touch with every
            # other motion path under the hub lock. A rate-0 STOP is itself an
            # abort, so it BUMPS the fence first (so a concurrent slew is fenced)
            # and is always allowed; a non-zero jog re-checks the fence at the
            # pre-dispatch point so a STOP that landed mid-call wins.
            if rate == 0.0:
                hub.bump_motion_epoch()
                async with hub._motion_lock:
                    await tel.move_axis(body.axis, rate)
            else:
                epoch = hub._motion_epoch
                async with hub._motion_lock:
                    if not hub._motion_committed_clean(epoch):
                        bus.log("warning", "jog abandoned: aborted before motion",
                                "mount")
                        return {"ok": True, "aborted": True}
                    await tel.move_axis(body.axis, rate)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/stop", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.stop"})
    async def mount_stop():
        try:
            tel = hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        # Motion fence (W3.7): BUMP the epoch FIRST, then cancel + stop. The bump
        # fences any in-flight (or just-accepted-but-still-awaiting) slew so a
        # stale REMOTE goto cannot fire AFTER this LOCAL abort -- even if its task
        # was already past the cancel point and sitting in ``tel.slew``'s await,
        # its pre-dispatch re-check sees the advanced epoch and abandons.
        hub.bump_motion_epoch()
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

    @app.post("/api/mount/tracking", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.set_tracking"})
    async def tracking(on: bool):
        try:
            tel = hub.require("telescope")
            await tel.set_tracking(on)
            return {"tracking": on}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/tracking_rate", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.set_tracking_rate"})
    async def tracking_rate(rate: str):
        # Validate against the one vocabulary source BEFORE touching the device
        # (plan Task 4 / design doc): an unknown rate is a client error (422),
        # not a device failure.
        if rate not in TRACKING_RATES:
            raise HTTPException(422, f"unknown tracking rate {rate!r}")
        try:
            tel = hub.require("telescope")
            await tel.set_tracking_rate(rate)
            return {"tracking_rate": rate}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/park", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.park"})
    async def park():
        try:
            hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        # Park is a motion-committing abort: bump the fence FIRST so an in-flight
        # goto is abandoned, then run park under the motion lock (serialized with
        # every other device-touching motion path). replace=True CANCELS a prior
        # goto/center rather than 409-ing after the epoch bump already sabotaged it
        # (the old code bumped the fence and then _spawn 409'd, so the mount kept
        # slewing to the wrong target and never parked).
        hub.bump_motion_epoch()

        async def _park():
            tel = hub.require("telescope")
            async with hub._motion_lock:
                await tel.park()
            # PARK IS THE ONE EVENT AN UNATTENDED NIGHT MUST BE ABLE TO PROVE.
            #
            # Until 2026-08-02 this path wrote nothing anywhere. The morning
            # question "did the rig park itself, or did I leave it tracking into
            # the Sun?" was unanswerable from the night log: the mount sat at the
            # pole with tracking off and not one line said how it got there, so
            # the log could not distinguish a working failsafe from a lucky
            # coincidence. The roof path next door has always passed log=bus.log
            # into close_observatory for exactly this reason.
            #
            # Logged AFTER the await, so the line means "parked", not "asked to".
            bus.log("info", "mount parked", "mount")
        return _spawn("goto", _park(), replace=True)

    @app.post("/api/mount/home", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.find_home"})
    async def find_home():
        """Send the mount to its mechanical home and leave it usable there.

        Same motion discipline as park: bump the fence FIRST so an in-flight
        goto is abandoned rather than racing us, then run under the motion lock
        with ``replace=True`` so a prior goto is CANCELLED instead of 409-ing
        after the epoch bump already sabotaged it. Homing is a motion-committing
        abort for exactly the same reason parking is."""
        try:
            tel = hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        if not getattr(tel, "can_find_home", False):
            # Refuse in the API rather than letting the UI offer a control that
            # cannot work: the client gates on the same capability flag, so
            # reaching here means a stale client or a direct call.
            raise HTTPException(
                status_code=400,
                detail=f"{getattr(tel, 'name', 'this mount')} has no home position")
        hub.bump_motion_epoch()

        async def _home():
            t = hub.require("telescope")
            async with hub._motion_lock:
                await t.find_home()
            bus.log("info", "mount homed", "mount")   # see park, above
        return _spawn("goto", _home(), replace=True)

    @app.post("/api/mount/unpark", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.unpark"})
    async def unpark():
        try:
            tel = hub.require("telescope")
            await tel.unpark()
            # The counterpart to the park line: without it the log shows a rig
            # that parked and then, with no entry between, is somehow moving
            # again. Unpark is what makes the mount free to slew, so it belongs
            # in the same audit trail.
            bus.log("info", "mount unparked", "mount")
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # -------------------------------------------------------------- dome / roof

    @app.get("/api/dome/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def dome_state():
        """``{connected, shutter, requires_park_before_close, can_slave}`` for the
        Settings→Safety roof widget. Mirrors ``/api/safety/state``: honest defaults
        when no dome is connected (v1 is sim-only — a real Alpaca/COM Dome client is
        a follow-up)."""
        dome = hub.devices.get("dome")
        if dome is None:
            return {"connected": False, "shutter": "unknown",
                    "requires_park_before_close": True, "can_slave": False}
        return {"connected": bool(getattr(dome, "connected", False)),
                "shutter": (await dome.shutter_state()).value,
                "requires_park_before_close": bool(dome.requires_park_before_close),
                "can_slave": bool(dome.can_slave)}

    @app.post("/api/dome/close", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Dome.close_shutter", "Telescope.park"})
    async def dome_close():
        """Manual park-and-close: a motion-committing action (it moves the mount),
        so it reuses CAP_CONTROL_MOUNT (D4). Fence in-flight gotos, park under the
        motion lock, THEN close via the tested ordering guard (close_observatory),
        which re-confirms parked and REFUSES rather than crush the mount."""
        dome = hub.devices.get("dome")
        if dome is None or not getattr(dome, "connected", False):
            raise _err(DeviceError("no dome connected"))
        hub.bump_motion_epoch()

        async def _run():
            tel = hub.devices.get("telescope")
            async with hub._motion_lock:
                if (tel is not None and getattr(tel, "connected", False)
                        and getattr(dome, "requires_park_before_close", True)):
                    await tel.park()
                from ..sequence.roof import close_observatory
                return await close_observatory(dome, tel, log=bus.log)
        return _spawn("goto", _run(), replace=True)

    # -------------------------------------------------------------- focuser

    @app.post("/api/focuser/move", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def focuser_move(body: FocuserMoveBody):
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        return _spawn("focuser", foc.move_to(body.position))

    @app.post("/api/focuser/set-position",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def focuser_set_position(body: FocuserSetPositionBody):
        """Re-anchor the focuser's position count. MOVES NOTHING.

        A stepper focuser's position is a count with no physical meaning until
        something anchors it, and the EAF resets that count to 0 when it loses
        power. The safe repair is a human putting the drawtube somewhere known
        and saying so — NOT driving into a mechanical stop to find one, which
        hardware without limit switches cannot be relied on to notice.

        Synchronous (not _spawn): it is an instant write, and the caller wants
        the failure, not a task id.
        """
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        if not getattr(foc, "can_set_position_reference", False):
            raise HTTPException(
                409, f"{foc.name} cannot have its position reference set")
        if (t := hub._busy.get("focuser")) and not t.done():
            raise HTTPException(409, "the focuser is busy")
        try:
            await foc.set_position_reference(body.position)
        except DeviceError as e:
            raise _err(e)
        return {"position": await foc.get_position()}

    @app.post("/api/focuser/autofocus", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def autofocus(body: AutofocusBody):
        # Camera mutual exclusion: autofocus exposes the camera, so it must not run
        # while the live loop or a sequence is exposing (interleaved imageready
        # polls). Pass the hub exposure guard so each sweep frame is serialized
        # against the other capture paths too.
        if engine.running or hub.looping:
            raise HTTPException(409, "camera is busy (a capture loop or sequence "
                                     "is running)")
        try:
            cam = hub.require("camera")
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)

        async def _af():
            # UX-25: per-filter autofocus — move to the requested slot before the
            # sweep so each filter can be focused (and its offset measured). No
            # camera exposure here, so no capture guard is needed for the move.
            if body.filter is not None:
                fw = hub.devices.get("filterwheel")
                if fw is not None and fw.connected:
                    await fw.set_position(body.filter)
            return await run_autofocus(
                cam, foc, exposure_s=body.exposure_s, gain=body.gain,
                step=body.step, steps_each_side=body.steps_each_side,
                binning=body.binning, expose_guard=hub.exposure_guard, hub=hub)

        return _spawn("autofocus", _af())

    @app.post("/api/focuser/coarse", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def coarse_focus(body: CoarseFocusBody):
        """Walk the focuser travel until a position has enough stars to autofocus.

        Same camera mutual exclusion as autofocus — it exposes on every stop —
        and the same spawn lane, so Halt stops it and the UI's focus state
        machine needs no new vocabulary."""
        if engine.running or hub.looping:
            raise HTTPException(409, "camera is busy (a capture loop or sequence "
                                     "is running)")
        try:
            cam = hub.require("camera")
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)

        async def _cf():
            return await run_coarse_focus(
                cam, foc, exposure_s=body.exposure_s, gain=body.gain,
                binning=body.binning, span=body.span, stops=body.stops,
                expose_guard=hub.exposure_guard)

        return _spawn("autofocus", _cf())

    @app.post("/api/focuser/halt", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
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

    # ---- Bahtinov focus aid (NOV-12) — mirrors the livestack start/stop routes.
    # Arming attaches an additive `bahtinov` verdict to every raw/linear preview
    # event (hub._publish_preview); stop disarms only, leaving the live loop as
    # the user left it (design §4.3 — focusing overlaps ordinary live preview).

    @app.post("/api/focuser/bahtinov/start", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def bahtinov_start(body: BahtinovBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        if engine.running:
            raise HTTPException(409, "a sequence is running")
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        hub.arm_bahtinov(tol_px=body.tol_px, invert=body.invert)
        if not hub.looping:
            await hub.start_loop(body.exposure_s, body.gain, body.offset,
                                 body.binning, frame_type="Light")
        return {"active": True}

    @app.post("/api/focuser/bahtinov/stop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def bahtinov_stop():
        return hub.disarm_bahtinov()   # leaves the live loop as the user left it (§4.3)

    # ---------------------------------------------------------- rotator

    @app.post("/api/rotator/move",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_move(body: RotatorMoveBody):
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        # spec §3.5.2: a manual rotation mid-exposure ruins the frame — refuse.
        if hub._capture_lock.locked():
            raise HTTPException(
                409, f"camera is busy ({hub._capture_busy or 'exposing'}); "
                     f"rotator move refused")
        rcfg = config_store.cfg().rotator
        mech = await rot.get_mechanical_position()
        target = map_sky_target(body.position_deg, mech, rot.sync_offset_deg,
                                rcfg.range_type, rcfg.range_start_deg)
        adjusted = not angle_equals(target, mod360(body.position_deg), 0.1)
        return _spawn("rotator", rot.move_to(target)) | {
            "target_deg": round(target, 2), "adjusted": adjusted}

    @app.post("/api/rotator/halt",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_halt():
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        for name in ("rotator", "rotate_to_pa"):
            task = hub._busy.get(name)
            if task and not task.done():
                task.cancel()
        await rot.halt()
        return {"ok": True}

    @app.post("/api/rotator/reverse",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_reverse(body: RotatorReverseBody):
        try:
            rot = hub.require("rotator")
        except DeviceError as e:
            raise _err(e)
        if not rot.can_reverse:
            raise HTTPException(400, "this rotator does not support reverse")
        await rot.set_reverse(body.reverse)
        return {"reverse": body.reverse}

    @app.post("/api/rotator/rotate-to-pa",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_rotate_to_pa(body: RotateToPaBody):
        try:
            hub.require("rotator")
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("rotate_to_pa",
                      hub.rotate_to_pa(body.target_pa_deg, body.exposure_s))

    # ---------------------------------------------------------- filterwheel

    @app.post("/api/filterwheel/position", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def set_filter(body: FilterBody):
        try:
            fw = hub.require("filterwheel")
        except DeviceError as e:
            raise _err(e)
        return _spawn("filterwheel", fw.set_position(body.position))

    @app.post("/api/filterwheel/names", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def set_filter_names(body: FilterNamesBody):
        """Assign user filter slot names (+ optional per-filter focuser offsets),
        persisted per profile so they outlive a reconnect (UX-05)."""
        try:
            hub.require("filterwheel")
        except DeviceError as e:
            raise _err(e)
        try:
            return await hub.set_filter_names(body.names, body.offsets,
                                              body.opaque)
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/filterwheel/learn-offsets",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def learn_filter_offsets(body: LearnOffsetsBody):
        """Autofocus each filter slot and fill in the per-filter offsets.

        Camera-exclusive (a full AF sweep per slot), so it 409s while a capture
        loop or sequence owns the camera — the same guard autofocus uses."""
        if engine.running or hub.looping:
            raise HTTPException(409, "camera is busy (a capture loop or sequence "
                                     "is running)")
        try:
            hub.require("filterwheel")
            hub.require("focuser")
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("filter_offsets", hub.learn_filter_offsets(
            ref_slot=body.ref_slot, exposure_s=body.exposure_s, gain=body.gain,
            step=body.step, steps_each_side=body.steps_each_side,
            binning=body.binning))

    # --------------------------------------------------------------- switch

    @app.get("/api/switch/ports", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def switch_ports():
        try:
            sw = hub.require("switch")
            return [p.__dict__ for p in await sw.get_ports()]
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/switch/set", dependencies=[Depends(require(CAP_CONTROL_POWER))])
    @declare(CAP_CONTROL_POWER)
    async def switch_set(body: SwitchBody):
        try:
            sw = hub.require("switch")
            await sw.set_port(body.port_id, body.value)
            return [p.__dict__ for p in await sw.get_ports()]
        except (DeviceError, RuntimeError) as e:
            raise _err(e)

    # ---------------------------------------------------------------- guide

    @app.post("/api/guide/start", dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_start():
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        # honor the per-profile guide-provider override at THIS start (never
        # hot-swaps a running guider; degrades to what's wired) — fix round C1.
        await hub.select_guide_provider()
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        return _spawn("guide", hub.guider.start_guiding())

    @app.post("/api/guide/stop", dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_stop():
        if not hub.guider:
            raise HTTPException(409, "no guider connected")
        await hub.guider.stop_guiding()
        return {"ok": True}

    @app.post("/api/guide/dither", dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_dither(body: DitherBody):
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        # UX-24: assemble a settle override from any provided fields (omit the
        # rest so each guider keeps its own default for those).
        settle = {k: v for k, v in (
            ("pixels", body.settle_pixels),
            ("time", body.settle_time_s),
            ("timeout", body.settle_timeout_s),
        ) if v is not None} or None
        return _spawn("dither", hub.guider.dither(body.pixels, settle))

    @app.get("/api/guide/frame.png", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def guide_frame():
        """Auto-stretched PNG of the guide field: the guider's own star image
        (PHD2 ``get_star_image``, sim's synthesized frame) when a guider is
        running, else one exposure from the connected guide CAMERA.

        That second source is the 2026-07-31 fix: this route gated on
        ``hub.guider``, which is None on a native rig unless a profile overrides
        the ``guider`` role, so a connected-and-idle ZWO ASI guide camera 404'd
        every time and the Capture panel's toggle looked dead. ``hub`` decides
        which source applies and NAMES why when neither does; still 404 (never
        500), and the reason rides the detail."""
        png, reason = await hub.guide_preview_png()
        if not png:
            raise HTTPException(404, reason or "no guide frame available")
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/guide/calibrate",
              dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_calibrate():
        """Force a FRESH calibration: stop any live guiding, clear the active
        profile's stored calibration, then (re)start guiding so the engine
        recalibrates from scratch instead of reusing a persisted calibration.
        409 when no guider is connected. A guider that owns its own calibration
        lifecycle (PHD2/NINA/sim) has no ``clear_calibration`` and simply
        re-starts (its backend re-runs calibration as needed)."""
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        await hub.guider.stop_guiding()
        # honor the guide-provider override for the fresh calibration (guiding is
        # stopped above, so this may swap the guider) — fix round C1.
        await hub.select_guide_provider()
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        clear = getattr(hub.guider, "clear_calibration", None)
        if callable(clear):
            clear()
        return _spawn("guide", hub.guider.start_guiding(), replace=True)

    @app.delete("/api/guide/calibration",
                dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_clear_calibration():
        """Clear the active profile's PERSISTED calibration so the next start
        drives a fresh calibration walk (dossier §8.4). 409 when no guider is
        connected; 400 when the connected guider manages no clearable persisted
        calibration (PHD2/NINA/sim own their own calibration lifecycle)."""
        if not hub.guider:
            raise HTTPException(409, "no guider connected")
        clear = getattr(hub.guider, "clear_calibration", None)
        if not callable(clear):
            raise HTTPException(
                400, "this guider does not manage a clearable calibration")
        return {"cleared": bool(clear())}

    @app.get("/api/guide/calibration",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def guide_calibration_report():
        """The active guider's calibration report (UX-23): pass/fail + geometry
        + advisories, or ``{"report": null}`` when none is available (no guider,
        no calibration yet, or a guider that exposes none)."""
        g = hub.guider
        report = g.calibration_report() if g is not None else None
        return {"report": report}

    @app.get("/api/guide/settings",
             dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_settings_get():
        """The persisted native-guider per-axis algorithm selection
        (``AppConfig.guide``)."""
        return config_store.cfg().guide.model_dump()

    @app.put("/api/guide/settings",
             dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_settings_put(body: GuideConfig):
        """Persist the native-guider per-axis algorithm selection (write-time
        validated against the PHD2-parity pick lists; PPEC is RA-only —
        ValueError→422). Takes effect on the next ``start_guiding`` (the guider
        reads it at construction)."""
        try:
            cfg = await asyncio.to_thread(config_store.set_guide, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return config_store.cfg().guide.model_dump()

    # ---------------------------------------------------- guiding assistant
    # A guided ~1-2 min measurement session (drift / periodic error / seeing +
    # Dec backlash) that RECOMMENDS guide params (design 2026-07-24). Apply is
    # zero new backend — the client reuses PUT /api/guide/settings + DELETE
    # /api/guide/calibration. Native-guider-only: it needs the raw pulse+centroid
    # access the PHD2/NINA bridges don't expose (capability-probe idiom, mirrors
    # clear_calibration above).

    @app.post("/api/guide/assistant/start",
              dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_assistant_start(body: AssistantStartBody):
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        run = getattr(hub.guider, "run_guiding_assistant", None)
        if not callable(run):
            raise HTTPException(
                400, "the Guiding Assistant works with the AstroDeck native "
                "guider only")
        if await hub.guider.is_active():
            raise HTTPException(409, "stop guiding before running the Guiding "
                                "Assistant")
        return _spawn("guide_assistant",
                      run({"include_backlash": body.include_backlash,
                           "duration_s": body.duration_s}))

    @app.get("/api/guide/assistant/report",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def guide_assistant_report():
        """The last Guiding Assistant report (measurements + recommendations), or
        ``{"report": null}`` when it has not run (or a non-native guider)."""
        g = hub.guider
        getter = getattr(g, "run_assistant_report", None) if g is not None else None
        report = getter() if callable(getter) else None
        return {"report": report}

    @app.post("/api/guide/assistant/stop",
              dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_assistant_stop():
        if not hub.guider:
            raise HTTPException(409, "no guider connected")
        stop = getattr(hub.guider, "stop_guiding_assistant", None)
        if not callable(stop):
            raise HTTPException(
                400, "the Guiding Assistant works with the AstroDeck native "
                "guider only")
        stop()
        return {"ok": True}

    # ------------------------------------------------------------- sequence

    @app.post("/api/sequence/start", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
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
        # Unbounded accepted-quota guard (Task 4 review, IMPORTANT): the
        # accepted-mode capture loop (_run_step, spec §3) only terminates via an
        # accepted frame, a reject-guard trip, or a frozen stop boundary — the
        # no-progress watchdog only WARNs, never raises. When both reject
        # guards are disabled AND no target carries a stop boundary, a step
        # whose quota can never be satisfied (e.g. persistent clouds) would run
        # forever. Never bypassed by `force` (that flag only overrides the
        # horizon pre-flight below, not a structural configuration hazard).
        if quota_unbounded(plan):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
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
        # Sun-exclusion pre-flight (W1.10) runs REGARDLESS of ``force`` -- a
        # forced run bypasses only the visible-horizon 409, never sun avoidance.
        # Disarming requires a solar session (config.solar_override), which makes
        # _check_solar inert. Calibration targets never slew, so skip them.
        for t in plan.targets:
            if t.calibration:
                continue
            ra = getattr(t, "ra_hours", None)
            dec = getattr(t, "dec_deg", None)
            if ra is None or dec is None:
                continue
            solar = _solar_block(ra, dec)
            if solar is not None:
                solar = dict(solar)
                solar["target"] = getattr(t, "name", "")
                raise HTTPException(409, detail=solar)
        # Auto-stop the live preview loop before the run owns the camera (the
        # natural ASIAIR-style flow: frame with the loop, then hit Start Plan).
        # Awaited so the loop's in-flight expose fully releases the camera +
        # capture lock before the engine's first exposure.
        if hub.looping:
            await hub.stop_loop_and_wait()
        try:
            hub.require("camera")
            engine.start(plan)
        except DeviceError as e:
            raise _err(e)
        return {"started": True, "frames": plan.total_frames()}

    @app.post("/api/sequence/pause", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def sequence_pause():
        engine.pause()
        return {"paused": True}

    @app.post("/api/sequence/resume", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def sequence_resume():
        engine.resume()
        return {"paused": False}

    @app.post("/api/sequence/abort", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def sequence_abort():
        await engine.abort()
        return {"aborted": True}

    @app.get("/api/sequence/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def sequence_state():
        return engine.state | {"running": engine.running, "paused": engine.paused}

    # ----------------------------------------------------------------- monitor

    @app.get("/api/monitor/snapshot", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def monitor_snapshot():
        """One-shot cold-load hydration for the Monitor view (monitor spec §8).
        Non-fatal: the WS catches up within ~2s, so the view never blocks on it.
        Uses the live engine state (running/paused), not just the last snapshot.

        POLAR RIDES HERE for the same reason ``sequence`` does: its state reaches
        the client only as a bus event, and its most important states are TERMINAL
        — a refusal ("too close to the pole to measure"), an error, a finished
        run. Those publish exactly once and the bus keeps no history, so a page
        reload or a dropped socket left the client on the cold default, showing
        an idle aligner and no reason. The user then re-runs the thing that just
        refused, and gets the same silence."""
        snap = await hub.monitor_snapshot()
        snap["sequence"] = engine.state | {
            "running": engine.running, "paused": engine.paused}
        snap["polar"] = hub.polar.state | {"running": hub.polar.running}
        return snap

    @app.get("/api/sequence/preflight",
             dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def sequence_preflight(ra_hours: float, dec_deg: float):
        """Live single-target altitude verdict from the current site. Returns
        ``unknown`` while the site is still the default (no trustworthy answer).

        GATED, NOT REDACTED. Everything this route returns is a function of the
        site: strip the altitude and the azimuth and the remaining ``verdict``
        is still a three-level channel a caller can binary-search on dec until
        it has the observer's latitude. There is nothing left of the route once
        the site-derived part is removed, so the honest move is to require the
        capability rather than serve a hollowed-out answer. Operators hold it —
        they plan sequences here."""
        return _preflight_alt(ra_hours, dec_deg)

    @app.post("/api/sequence/preflight", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
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
        location to call a target un-observable.

        Two BLOCKING checks were added (``"blocking": true`` on the warning, and
        they are the only ones that can set it):

        * ``unsafe`` (UX #2) — the safety monitor reads unsafe/stale. A go/no-go
          screen that omits the go/no-go input is worse than no screen: preflight
          reported a green READY while ``/api/safety/state`` said "rain detected".
        * ``no_filter`` (UX #1) — a Light step with no filter on a rig whose wheel
          IS connected. Those frames are recorded under whatever slot the wheel
          happens to sit on, which is how a night of SII landed in the stacking
          bundle beside L flats."""
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
        # --- safety (UX #2) — the go/no-go input, first, because it is the one
        # that ends a night. Fail-CLOSED exactly like the engine gate: a stale or
        # unreadable monitor is unsafe, not "probably fine". Only checked when the
        # run would actually honor it (cfg.safety.enabled and plan.safety_check),
        # so a user who deliberately turned safety off is not nagged.
        if cfg.safety.enabled and plan.safety_check:
            mon = hub.devices.get("safety")
            if mon is not None and getattr(mon, "connected", False):
                reading = await hub.safety_reading()
                if reading is None or reading.stale:
                    warnings.append({
                        "target": "", "kind": "unsafe", "blocking": True,
                        "message": ("safety monitor is not reporting — treated as "
                                    "UNSAFE (fail-closed)")})
                elif not reading.is_safe:
                    warnings.append({
                        "target": "", "kind": "unsafe", "blocking": True,
                        "message": (f"conditions are UNSAFE: "
                                    f"{reading.reason or reading.source or 'unsafe'}")})
        # --- filter identity (UX #1) — a Light step with no filter while a wheel
        # is connected. The frames are NOT unfiltered: they come out through
        # whatever slot the wheel is parked on, get that name in FILTER/report/
        # bundle, and are then calibrated with that slot's flats.
        wheel_names: list[str] = []
        fw = hub.devices.get("filterwheel")
        if fw is not None and getattr(fw, "connected", False):
            wheel_names = [n for n in (getattr(fw, "filter_names", []) or []) if n]
        if wheel_names:
            current = await hub._active_filter_name()
            unset = sorted({t.name for t in plan.targets if not t.calibration
                            for s in t.steps
                            if (s.frame_type or "Light").strip().lower() == "light"
                            and not s.filter})
            if unset:
                one = len(unset) == 1
                subject = "a Light step" if one else "Light steps"
                verb = "has" if one else "have"
                pronoun = "it" if one else "they"
                where = (f"the wheel is on {current}, so {pronoun} would be "
                         f"recorded as {current}" if current
                         else f"{pronoun} would be recorded under whatever slot "
                              "the wheel is parked on")
                warnings.append({
                    "target": unset[0] if one else "",
                    "kind": "no_filter", "blocking": True,
                    "message": (f"{subject} in {', '.join(unset)} {verb} no "
                                f"filter set — {where}")})
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
        # --- calibration coverage (PRO-1) — folded into this same non-blocking
        # surface (NOT a separate preflight route). ``coverage`` returns [] when
        # no masters exist, so a user who never built a library is never nagged;
        # a gap warns only once ≥1 master exists. Not gated on ``is_default`` —
        # calibration coverage is independent of the observing site.
        cal = cfg.calibration
        tol = MatchTolerance(cal.exposure_tol_pct, cal.temp_tol_c)
        gaps = await asyncio.to_thread(cal_library.coverage, plan, tol, cal.temp_bin_c)
        for g in gaps:
            warnings.append({
                "target": "",
                "kind": "no_calibration",
                "message": (f"no {' or '.join(g.missing)} master for "
                            f"{g.exposure_s:g}s · gain {g.gain} · bin {g.binning}"
                            + (f" · {g.filter}" if g.filter else "")),
            })
        # ``blocked`` is additive: ``ok`` keeps its old meaning (nothing at all to
        # say), so no existing client changes behaviour, while a client that
        # understands the flag can hard-fail instead of offering "Run anyway".
        return {"ok": not warnings, "warnings": warnings,
                "blocked": any(w.get("blocking") for w in warnings)}

    @app.get("/api/sequence/recoverable", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def sequence_recoverable():
        # Re-backed on the session store (spec §2): a dormant session WITH
        # frames is recoverable. Route path unchanged for UI compatibility.
        s = session_store.recoverable()
        if s is None:
            return {"recoverable": False}
        return {"recoverable": True, "session_id": s.id, "name": s.name,
                "frames_done": sum(s.done_map().values()),
                "frames_total": s.plan.total_frames(), "ts": s.updated_ts}

    @app.post("/api/sequence/recover", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def sequence_recover():
        s = session_store.recoverable()
        if s is None:
            raise HTTPException(404, "no resumable sequence found")
        # Same unbounded accepted-quota guard as /api/sequence/start (Task 4
        # review, IMPORTANT) — resume starts the engine on this same loop, so a
        # dormant session carrying the unbounded combination must be refused
        # here too, not just on the original start.
        if quota_unbounded(s.plan):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
        try:
            hub.require("camera")
            engine.start(s.plan, session=s)
        except DeviceError as e:
            raise _err(e)
        return {"resumed": True,
                "frames_remaining": sum(s.remaining().values())}

    # -------------------------------------------------------------- polar align

    @app.post("/api/polar/start", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"PolarSession.start"})
    async def polar_start():
        try:
            await hub.polar.start()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"started": True, "source": hub.polar.state["source"]}

    @app.post("/api/polar/stop", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_stop():
        await hub.polar.stop()
        return {"ok": True}

    @app.post("/api/polar/pause", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_pause():
        await hub.polar.pause()
        return {"ok": True}

    @app.post("/api/polar/resume", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_resume():
        await hub.polar.resume()
        return {"ok": True}

    @app.get("/api/polar/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def polar_state():
        return hub.polar.state | {"running": hub.polar.running}

    # -------------------------------------------------------------- catalog

    @app.get("/api/catalog")
    @declare(CAP_VIEW_STATUS)
    async def catalog(q: str = "", explain: bool = False,
                      principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Target search. Returns a bare LIST of rows, as it always has.

        `explain=1` returns {"results": [...], "notes": [...]} instead. The
        notes are the things this server knows and a target row cannot say: the
        Sun withheld by the sun-avoidance gate (a query for "sun" still returns
        M63, the Sunflower Galaxy, so an explanation that waited for an empty
        result set never appeared at all), a body whose ephemeris failed this
        second, a body deliberately not carried. Anything not sent here the
        browser has to guess, and its guess — "Planets aren't supported yet" —
        is the failure this whole search change exists to end.
        """
        from ..catalog import altaz
        # OFF the event loop. search() now evaluates astropy ephemerides inline:
        # ~6ms per planet, ~27ms for the Moon, and ~512ms on the first
        # solar-system query of the process (astropy import + IERS init). This
        # route is hit on a 250ms debounce from two search boxes, so run on the
        # loop it would stall the 2s status poll and the relay behind it — half a
        # second of frozen telemetry for one keystroke. Same offload the hub
        # already uses for detect_stars and measure_blob.
        found = await asyncio.to_thread(search, q)
        # alt/az ONLY for a holder of view.site_derived. Each row is
        # f(site, target), and the caller chooses the target — so a search box
        # is a coordinate oracle with as many samples as the caller cares to
        # type. The rows themselves (name, type, magnitude, RA/Dec) are catalog
        # facts and stay: a viewer can still see what is in the sky, just not
        # where the sky is being observed from.
        if principal.has(CAP_VIEW_SITE_DERIVED):
            for r in found.rows:
                alt, az = altaz(r["ra_hours"], r["dec_deg"],
                                hub.site["latitude"], hub.site["longitude"])
                r["alt"] = round(alt, 1)
                r["az"] = round(az, 1)
        if explain:
            return {"results": found.rows, "notes": found.notes}
        return found.rows

    @app.get("/api/catalog/tonight",
             dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def catalog_tonight(date: str | None = None, alt_limit: float = 30.0):
        """Rank the whole catalog by tonight's best-window visibility and tag each
        object with a beginner difficulty rating (NOV-3). Mirrors post_order's
        throttled fan-out of compute_night (visibility.py)."""
        from ..catalog.objects import CATALOG, _TYPE_NAMES
        from ..catalog.visibility import check_night_date, compute_night
        from ..catalog.tonight import tonight_score, rank_picks
        from ..catalog.difficulty import difficulty_for

        # A bad date otherwise falls back to TONIGHT inside the anchor parser,
        # answering for the wrong night with no sign anything went wrong.
        date = check_night_date(date)
        sem = asyncio.Semaphore(8)

        async def _night(o):
            async with sem:
                return await asyncio.to_thread(
                    compute_night, o.ra_hours, o.dec_deg,
                    date=date, step_min=20, alt_limit=alt_limit)

        nights = await asyncio.gather(*[_night(o) for o in CATALOG])
        picks: list[dict] = []
        for o, night in zip(CATALOG, nights):
            d = difficulty_for(o.id, o.mag, o.size_arcmin)
            bw = night["best_window"]
            picks.append({
                "id": o.id, "name": o.name, "type": _TYPE_NAMES[o.type],
                "ra_hours": o.ra_hours, "dec_deg": o.dec_deg,
                "mag": o.mag, "size_arcmin": o.size_arcmin,
                "difficulty": d["tier"],
                "surface_brightness": d["surface_brightness"],
                "difficulty_source": d["source"],
                "max_alt": night["transit_alt"],
                "transit_unix": night["transit_unix"],
                "best_window": ({"start_unix": bw["start_unix"],
                                 "end_unix": bw["end_unix"]} if bw else None),
                "moon_sep_deg": night["moon"]["separation_deg"],
                "never_rises_above_limit": night["never_rises_above_limit"],
                "score": tonight_score(night),
            })
        out_date = nights[0]["date"] if nights else (date or "")
        return {"date": out_date, "picks": rank_picks(picks),
                "site_is_default": bool(hub.site.get("is_default", False))}

    @app.get("/api/logs", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def logs(level: str | None = None, night: str | None = None,
                   limit: int = 0):
        """Event log rows, oldest first.

        Default (no params) = the in-memory ring, byte-identical to before.
        ``night=YYYY-MM-DD`` reads that night's PERSISTED file instead (UX #9 —
        the ring is only the last ~40 minutes of a ten-hour run), and ``level``
        filters either source. ``limit`` keeps the newest N rows."""
        if night:
            store = bus.night_log
            rows = (await asyncio.to_thread(
                store.read, _slug(night), level=level,
                limit=(limit or LOG_READ_MAX))) if store is not None else []
            return rows
        rows = bus.log_history
        if level:
            rows = [r for r in rows if (r.get("data") or {}).get("level") == level]
        if limit and limit > 0:
            rows = rows[-limit:]
        return rows

    @app.get("/api/logs/nights", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def log_nights():
        """``{current, nights:[{night,bytes}]}`` — which nights are on disk, so
        the log drawer can offer more than the live tail."""
        store = bus.night_log
        nights = await asyncio.to_thread(store.nights) if store is not None else []
        return {"current": night_key(), "persisted": store is not None,
                "nights": nights}

    @app.get("/api/logs/export", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def log_export(night: str | None = None, format: str = "txt"):
        """Download one night's log. ``format=txt`` (default) is the readable
        transcript; ``jsonl`` is the raw rows. The filename is built from the
        SANITIZED night (never the raw param), like every other export route."""
        store = bus.night_log
        n = _slug(night or night_key())
        if store is None:
            raise HTTPException(404, "log persistence is disabled")
        if format == "jsonl":
            rows = await asyncio.to_thread(store.read, n)
            body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
            media, ext = "application/x-ndjson", "jsonl"
        else:
            body = await asyncio.to_thread(store.export_text, n)
            media, ext = "text/plain; charset=utf-8", "txt"
        if not body:
            raise HTTPException(404, f"no persisted log for {n}")
        return Response(body, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="astrodeck-{n}.log.{ext}"'})

    # ----------------------------------------------------------------- gallery
    # Browse / search / download / trash the capture library (gallery design,
    # 2026-08-03). The store logic lives in ``astrodeck.gallery``; these routes
    # are argument validation, capability gating and response shaping only.
    #
    # EVERY route here is authenticated, and none is anonymous, because every
    # frame this server writes embeds SITELAT/SITELONG/SITEELEV (and OBJCTALT/
    # AIRMASS, which disclose the site indirectly because they are computed from
    # it). A gallery download hands over the observatory's location. The split
    # follows the meanings ``capabilities.py`` already documents:
    #
    #   list / nights / summary / thumbnails  view.preview   ("downsized preview
    #                                                          frames (NOT raw FITS)")
    #   download FITS, single or bulk         view.media     ("raw FITS / full-res
    #                                                          science (bulk)")
    #   trash / restore / purge               control.capture (mutates the capture
    #                                                          volume)
    #
    # A viewer therefore browses thumbnails and can neither download a frame nor
    # learn where the rig is. An operator CAN delete, deliberately: control.capture
    # is already the authority to fill this volume with frames, and an operator
    # who can run the sequence that writes 200 GB but cannot remove a cloudy hour
    # is a rig that fills its own disk. The 30-day trash is what makes that safe.

    #: Bounds on one page. 500 is what an intersection-observer grid can hold
    #: without the JSON itself becoming the slow part.
    _GALLERY_PAGE_MAX = 500

    #: Bound on an explicit ``path`` selection.
    #:
    #: The POST bodies are already capped by their pydantic model; the GET
    #: ``path`` list on ``summary`` and ``download.zip`` was bounded only by
    #: whatever URL length the proxy in front happened to allow. Each path costs
    #: a ``fits.getheader`` (measured ~4.7 ms) inside ``resolve_selection``, so a
    #: long authenticated URL bought seconds of worker thread at ``view.preview``
    #: — a limit that lives in someone else's config is not a limit. 1000 is far
    #: above anything the UI can produce (its own URL budget is 6000 characters,
    #: ~140 paths) and far below anything that costs real time.
    _GALLERY_SELECTION_MAX = 1000

    def _gallery_nights_ok(*values: str) -> None:
        """422 on a malformed night bound. Refusing beats coercing: a silently
        ignored ``night_from=last week`` returns the WHOLE library and looks like
        a filter that worked."""
        for v in values:
            if v and not gallery_module.valid_night(v):
                raise HTTPException(
                    422, f"night must be YYYY-MM-DD (got {v!r})")

    async def _gallery_rows(q: str, night_from: str, night_to: str,
                            paths: list[str] | None = None):
        """The frame set a request refers to, plus per-path refusals.

        Two ways to name a set, one resolver: an explicit ``path`` list (the user
        ticked frames) wins over the filter (the user took what the filter
        returned), so the summary and the download can be given IDENTICAL
        parameters and are guaranteed to describe the same bytes. That identity
        is the whole point of showing "1,284 frames, 38.2 GB" beforehand."""
        # Validated even when a selection overrides them: a bad bound that is
        # accepted because some other parameter happened to win is a 422 the
        # caller will not get next time either.
        _gallery_nights_ok(night_from, night_to)
        if paths:
            if len(paths) > _GALLERY_SELECTION_MAX:
                raise HTTPException(
                    422, f"too many paths in one request "
                         f"({len(paths)} > {_GALLERY_SELECTION_MAX}) — name the "
                         f"set with the search and night filter instead, which "
                         f"has no size limit")
            rows, failed = await asyncio.to_thread(
                gallery_module.resolve_selection, paths)
            return rows, failed, False
        all_rows, truncated = await asyncio.to_thread(gallery_module.scan)
        return (gallery_module.filter_rows(all_rows, q=q, night_from=night_from,
                                           night_to=night_to),
                [], truncated)

    @app.get("/api/gallery/frames", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_frames(q: str = "", night_from: str = "", night_to: str = "",
                             offset: int = 0, limit: int = 200):
        """One page of the capture library, newest capture first.

        ``night_from``/``night_to`` are INCLUSIVE noon-to-noon night keys, not
        calendar dates, and they are matched against a night derived from each
        frame's DATE-OBS — never from its filename. The default naming template
        writes the calendar date, so a filename-based filter would file the 23:50
        and 00:10 halves of one session under different days and silently return
        half a night. See ``gallery.py`` GROUNDED 1.

        ``total``/``bytes`` describe the WHOLE filtered set, not this page, so
        the UI can show the download size without a second round trip."""
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), _GALLERY_PAGE_MAX))
        t0 = time.monotonic()
        rows, _failed, truncated = await _gallery_rows(q, night_from, night_to)
        totals = gallery_module.summarize(rows)
        return {
            "frames": rows[offset:offset + limit],
            "total": totals["count"],
            "bytes": totals["bytes"],
            "offset": offset,
            "limit": limit,
            # True only when the walk hit its ceiling: the library is bigger than
            # a walk should serve and the UI must say so rather than present a
            # prefix as the whole thing.
            "truncated": truncated,
            "scan_ms": round((time.monotonic() - t0) * 1000, 1),
        }

    @app.get("/api/gallery/nights", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_nights():
        """``{current, nights:[{night, frames, bytes}]}`` — which nights actually
        exist, so the date filter offers real nights instead of a blank calendar
        where most dates return nothing. ``current`` is tonight's key by the same
        noon rollover, so "tonight" can be highlighted before dawn."""
        rows, truncated = await asyncio.to_thread(gallery_module.scan)
        return {"current": night_key(),
                "nights": gallery_module.nights_index(rows),
                "truncated": truncated}

    @app.get("/api/gallery/summary", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_summary(q: str = "", night_from: str = "",
                              night_to: str = "",
                              path: list[str] = Query(default=[])):
        """What a download of this exact selection would be: ``{count, bytes}``.

        Takes the SAME parameters as ``/api/gallery/download.zip`` and goes
        through the same resolver, so the two cannot disagree about what a
        selection means (``test_gallery.py`` pins that pair).

        NOT called by the shipped web UI, deliberately: ``/api/gallery/frames``
        already returns ``total``/``bytes`` for the WHOLE filtered set from this
        same resolver, so the grid prices "Download 1,284 frames, 38.2 GB" off a
        listing it has already fetched rather than paying a second library walk
        for the same two numbers. This route is for the callers that have no
        listing — a script or a CLI that wants the size before committing to a
        multi-GB stream."""
        rows, failed, _ = await _gallery_rows(q, night_from, night_to, path)
        return {**gallery_module.summarize(rows), "failed": failed}

    @app.get("/api/gallery/thumb", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_thumb(path: str, w: int = 256):
        """Lazily-rendered, disk-cached JPEG thumbnail for one frame.

        422 (not 404) when the file exists but cannot be decoded, so the grid can
        draw a "no preview" tile that still lets the user download the frame —
        a frame we cannot render is not a frame that is missing."""
        try:
            jpeg = await asyncio.to_thread(gallery_module.thumbnail, path, width=w)
        except KeyError:
            raise HTTPException(404, "frame not found")
        except FileNotFoundError:
            raise HTTPException(404, "frame not found")
        except ValueError as e:
            raise HTTPException(422, str(e))
        except OSError as e:
            raise HTTPException(404, f"frame not readable: {e}")
        # Immutable by construction: the cache key includes mtime, so a re-capture
        # at the same path produces a different URL rather than a stale image.
        return Response(jpeg, media_type="image/jpeg", headers=_PREVIEW_CACHE)

    @app.get("/api/gallery/file", dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
    async def gallery_file(path: str):
        """Download ONE frame's raw FITS. ``view.media``, because the file embeds
        the observatory's coordinates."""
        try:
            target = safe_subpath(hub_module.CAPTURE_DIR, path)
        except KeyError:
            raise HTTPException(404, "frame not found")
        if (path.replace("\\", "/").split("/")[0] in gallery_module.SKIP_TOP_DIRS
                or target.suffix.lower() not in gallery_module.FRAME_SUFFIXES
                or not target.is_file()):
            raise HTTPException(404, "frame not found")
        # ``filename`` cannot forge a Content-Disposition — but NOT because
        # ``safe_subpath`` sanitized it. That guard refuses separators, NUL,
        # ``:``, ``.``/``..``, trailing dot-or-space and reserved device names,
        # and it ACCEPTS CR, LF, ``"`` and ``;`` (verified against it directly).
        # The header is safe because Starlette's FileResponse runs the name
        # through ``quote()`` and, whenever quoting changed anything — which any
        # of those four characters does — emits the fully percent-encoded RFC
        # 5987 ``filename*`` form instead of a quoted string. Credited to
        # Starlette rather than claimed for safe_subpath, so the next caller does
        # not inherit a guarantee that does not exist; tightening the guard
        # itself belongs to the file-safety sweep, with its own corpus.
        return FileResponse(target, media_type="application/fits",
                            filename=target.name)

    @app.get("/api/gallery/download.zip",
             dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
    async def gallery_download(q: str = "", night_from: str = "",
                               night_to: str = "",
                               path: list[str] = Query(default=[])):
        """Bulk download as a STREAMED zip. Same parameters as the summary.

        This is the first StreamingResponse in this server, and it has to be: a
        filter result is routinely tens of GB, while the nearest precedent (the
        report bundle) buffers a whole zip in memory and is safe only because it
        deliberately contains no FITS. Nothing here is buffered — one 64 KiB
        chunk and one open file handle, whatever the total size.

        GET rather than POST so the browser can download it natively (the session
        is a cookie, so a plain navigation authenticates) — a fetch-into-a-Blob
        would put the whole 38 GB back in memory and undo the streaming.

        ``X-Gallery-Frames``/``X-Gallery-Bytes`` carry the payload size the
        summary route reported: there is no Content-Length on a streamed zip, so
        without them a client has no way to draw a progress bar."""
        rows, failed, _ = await _gallery_rows(q, night_from, night_to, path)
        if not rows:
            # 404, not an empty zip: an archive with nothing in it is a download
            # that looks like it worked.
            detail = "no frames matched"
            if failed:
                detail += f" ({failed[0]['reason']})"
            raise HTTPException(404, detail)
        totals = gallery_module.summarize(rows)
        members = gallery_module.zip_members(rows)
        if night_from and night_from == night_to:
            stem = f"astrodeck-{_slug(night_from)}"
        else:
            stem = f"astrodeck-frames-{totals['count']}"
        return StreamingResponse(
            gallery_module.iter_zip(members),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{stem}.zip"',
                "X-Gallery-Frames": str(totals["count"]),
                "X-Gallery-Bytes": str(totals["bytes"]),
                # The archive is generated per request; caching it would pin GBs
                # in a proxy for a URL nobody re-fetches.
                "Cache-Control": "no-store",
            })

    @app.post("/api/gallery/trash",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_trash_frames(body: GalleryPathsBody):
        """Move frames to the trash (a RENAME inside CAPTURE_DIR, so it is atomic
        and instant even for a 200 GB night).

        Partial success is reported, never hidden: ``failed`` names each path and
        why. Note the deliberate, documented consequence — deleting a FITS orphans
        the session ledger and report rows that reference it. Nothing repairs
        that today; the report renders such a frame as missing rather than as a
        broken link, and fixing the ledger is a separate item."""
        if not body.paths:
            raise HTTPException(422, "no paths given")
        return await asyncio.to_thread(gallery_module.trash_frames, body.paths)

    @app.get("/api/gallery/trash",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_trash_list():
        """What is in the bin, when each item auto-purges, and whether it can
        still go back. Gated at ``control.capture`` with the rest of the trash
        surface: only someone who can delete needs to read the bin."""
        return await asyncio.to_thread(gallery_module.list_trash)

    @app.post("/api/gallery/trash/restore",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_trash_restore(body: GalleryPathsBody):
        """Put trashed frames back where they came from. ``paths`` are relative
        to the TRASH root (what the trash listing returns), not to the library.

        In scope even though the request never named it: a bin without restore is
        a delayed delete, and "Trash" is a word that promises undo."""
        if not body.paths:
            raise HTTPException(422, "no paths given")
        return await asyncio.to_thread(gallery_module.restore_frames, body.paths)

    @app.post("/api/gallery/trash/purge",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_trash_purge(body: GalleryPurgeBody):
        """Permanently delete trashed frames. Irreversible.

        ``all=true`` empties the bin; otherwise only the listed trash-relative
        paths go. Every path is resolved and re-checked against the trash root
        before the unlink — a path that does not land inside the trash is
        REFUSED and reported, never followed (``tests/test_path_traversal.py``
        binds the shared attack corpus to this route)."""
        if body.all:
            return await asyncio.to_thread(gallery_module.purge_all)
        if not body.paths:
            raise HTTPException(422, "no paths given (send all=true to empty the trash)")
        return await asyncio.to_thread(gallery_module.purge_paths, body.paths)

    # ----------------------------------------------------- identity / auth admin
    # W2.5 client seam + the admin.users-gated auth/remote/revoke surface. These
    # are the ONLY way to write AuthConfig (NEVER via POST /api/config, whose
    # ConfigPatchBody forbids auth/remote blocks).

    @app.get("/api/me", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def whoami(principal: Principal = Depends(get_principal)):
        """The genuinely-resolved caller identity (W2.5). FAIL-CLOSED: a None
        resolution is a 401 (``get_principal``), never a default-admin. Under the
        open ``none`` provider this returns admin/ALL_CAPS as today; under a real
        provider it returns the caller's actual ``{role, email, caps}``."""
        return principal.to_public()

    def _reconfigure_provider() -> None:
        """Re-install the active provider from the freshly-persisted AuthConfig so
        a role-allowlist / revoke / provider change takes effect immediately (no
        restart). Never raises out of the route (degrade, log)."""
        try:
            configure_provider_from_auth(config_store.cfg().auth)
        except Exception as e:  # noqa: BLE001
            bus.log("error", f"auth provider re-init failed: {e}", "auth")

    def _preserve_auth_secrets(new: AuthConfig) -> AuthConfig:
        """Re-apply the stored secrets when the incoming value is BLANK.

        The redacted ``auth`` block the UI reads has every secret scrubbed
        (``admin_token`` / ``google_client_secret`` / ``session_private_key`` ->
        ""). When the admin-method config panel echoes that block back, a blank
        secret means "UNCHANGED" -- never "wipe it" -- exactly mirroring the
        alerts route's "empty token means unchanged" contract. A non-empty value
        is an explicit rotation and is kept. ``revoked_jti`` is append-only, so a
        blank/short list from the UI is unioned with the stored registry (the UI
        never needs to carry it)."""
        old = config_store.cfg().auth
        updates: dict = {}
        if not (new.admin_token or "").strip():
            updates["admin_token"] = old.admin_token
        if not (new.google_client_secret or "").strip():
            updates["google_client_secret"] = old.google_client_secret
        if not (new.session_private_key or "").strip():
            updates["session_private_key"] = old.session_private_key
        # Never let a UI round-trip drop a revoked jti (append-only registry).
        merged_jti = list(dict.fromkeys([*old.revoked_jti, *new.revoked_jti]))
        if merged_jti != list(new.revoked_jti):
            updates["revoked_jti"] = merged_jti
        return new.model_copy(update=updates) if updates else new

    @app.post("/api/auth/config", dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def set_auth_config(auth: AuthConfig):
        """Persist a new ``AuthConfig`` (admin.users-gated). Validates the pinned
        rules (provider/role/default_role ceiling/append-only revoke registry) via
        ``ConfigStore.set_auth``; a violation is a 400. Re-installs the provider
        and broadcasts the REDACTED config so the UI updates without a restart.

        Blank secrets in the body mean "unchanged" (the UI only ever sees the
        redacted block), so a method/TTL toggle never wipes a stored credential."""
        auth = _preserve_auth_secrets(auth)
        try:
            cfg = await asyncio.to_thread(config_store.set_auth, auth)
        except ValueError as e:
            raise HTTPException(400, detail={"detail": str(e), "code": "invalid_auth"})
        _reconfigure_provider()
        # Enabling a method without ASTRODECK_SECRET auto-generates+persists a
        # secret and (if that fails) arms the fail-closed session interlock --
        # ``_reconfigure_provider`` did that; surface the state LOUD here too.
        _warn_insecure_session_secret()
        bus.publish("config", config=redacted(cfg))
        return redacted(cfg)["auth"]

    @app.post("/api/remote/config", dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def set_remote_config(auth: AuthConfig):
        """W3 relay-config seam (admin.users-gated). The relay/remote knobs live on
        the same ``AuthConfig`` (relay_pubkey / viewer_link_pubkey); this dedicated
        admin route exists now so the relay lane never has to touch ``app.py``.
        Today it persists AuthConfig exactly like ``/api/auth/config``."""
        try:
            cfg = await asyncio.to_thread(config_store.set_auth, auth)
        except ValueError as e:
            raise HTTPException(400, detail={"detail": str(e), "code": "invalid_auth"})
        _reconfigure_provider()
        bus.publish("config", config=redacted(cfg))
        return redacted(cfg)["auth"]

    @app.post("/api/auth/revoke", dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def revoke_jti(body: JtiBody):
        """APPEND a session/link id to the revoke registry (admin.users-gated).
        Append-only: the registry can only grow, so a revoked session can never be
        un-revoked by a config merge (only the explicit unrevoke route below)."""
        auth = config_store.cfg().auth
        if body.jti in auth.revoked_jti:
            return {"revoked": list(auth.revoked_jti)}
        new = auth.model_copy(update={"revoked_jti": [*auth.revoked_jti, body.jti]})
        try:
            cfg = await asyncio.to_thread(config_store.set_auth, new)
        except ValueError as e:
            raise HTTPException(400, detail={"detail": str(e), "code": "invalid_auth"})
        _reconfigure_provider()
        return {"revoked": list(cfg.auth.revoked_jti)}

    @app.post("/api/auth/unrevoke", dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def unrevoke_jti(body: JtiBody):
        """Remove a jti from the revoke registry (admin.users-gated). This is the
        ONLY path that may shrink the registry (``set_auth``'s append-only guard is
        bypassed here by passing the shrunk list as the new baseline)."""
        auth = config_store.cfg().auth
        remaining = [j for j in auth.revoked_jti if j != body.jti]
        # set_auth refuses to shrink vs. current; build the new cfg so current==new
        # for the registry by writing the model directly through a fresh validate.
        new = auth.model_copy(update={"revoked_jti": remaining})
        cfg = config_store.cfg()
        cfg.auth = new
        await asyncio.to_thread(config_store.bump_and_save)
        _reconfigure_provider()
        return {"revoked": list(new.revoked_jti)}

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
        # RBAC accept-time subscribe gate (W2.2). The WS is SEND-ONLY (it never
        # calls receive()), so there is no control channel to gate -- the only
        # gate is "may this principal subscribe to the status stream?", i.e.
        # ``view.status``. Resolve the principal via the active provider (the
        # WebSocket is request-like enough for ``resolve_principal``). Under the
        # open ``none`` provider every caller is admin -> holds view.status, so
        # the default LAN path is byte-for-byte today. A principal lacking
        # ``view.status`` (or an unauthenticated caller under a real provider) is
        # closed 1008 BEFORE accept, never joining the bus.
        # W3 remote flag: a relay-tunneled /ws scope is marked remote, so an open
        # ``none`` provider hard-denies the subscribe over a relay (the send-only
        # status stream is never served to an unauthenticated remote viewer).
        principal = await resolve_principal(
            websocket, remote=_scope_is_remote(websocket))
        if principal is None or not principal.has(CAP_VIEW_STATUS):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        q = bus.subscribe()
        # Periodic re-authentication (revocation + session-exp enforcement). Auth
        # is otherwise only checked at accept, so once the socket is up neither
        # POST /api/auth/revoke (revoked jti) nor a lapsed session TTL would ever
        # terminate it -- it would keep streaming status/preview/precise-site for
        # the whole unattended run. We re-resolve at least every WS_AUTH_RECHECK_S
        # (SessionCookieProvider.resolve re-reads the live provider's deny set +
        # the exp claim) and close 4401 the instant it no longer resolves or loses
        # view.status. Re-resolving also refreshes ``principal`` so a still-valid
        # but role-downgraded caller's site-precision redaction tracks its caps.
        import time as _t
        next_check = _t.monotonic() + WS_AUTH_RECHECK_S
        try:
            await websocket.send_json(
                {"type": "hello",
                 "data": _redact_site_for(hub.summary(), principal), "ts": 0})
            while True:
                # Wake for either the next event or the recheck deadline, so a
                # quiet socket is still re-validated on schedule (not only when
                # traffic happens to arrive).
                timeout = max(0.0, next_check - _t.monotonic())
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    ev = None
                if _t.monotonic() >= next_check:
                    principal = await resolve_principal(
                        websocket, remote=_scope_is_remote(websocket))
                    if principal is None or not principal.has(CAP_VIEW_STATUS):
                        # 4401 = application "unauthorized"; the SPA re-opens login.
                        await websocket.close(code=4401)
                        return
                    next_check = _t.monotonic() + WS_AUTH_RECHECK_S
                if ev is not None:
                    out = _redact_ws_event(ev.to_json(), principal)
                    if out is not None:  # None = dropped event (weather spec §8)
                        await websocket.send_json(out)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    # --------------------------------------------------- auth router (OIDC, W2.4-C)
    # The OIDC login/callback/logout APIRouter is owned by the provider lane
    # (auth/routes.py). Include it if present so the apply-lane and the
    # provider-lane never fight over app.py. Absent today -> a no-op import guard
    # (the seam is reserved; the login paths are already in _AUTH_OPEN_PREFIXES).
    try:
        from ..auth.routes import router as auth_router  # type: ignore
        app.include_router(auth_router)
    except Exception:  # noqa: BLE001 - router not present yet; seam reserved
        pass

    # ------------------------------------------------------------ static UI

    # index.html, not just the directory: a packaging mistake that leaves an
    # empty or half-copied ui dir would otherwise crash the mount at boot
    # (StaticFiles raises on a missing directory) instead of degrading to the
    # API-only server the `else` branch already describes.
    if (UI_DIST / "index.html").is_file() and (UI_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            # API fence (H1): an UNROUTED path under /api, /ws or /auth must NEVER
            # fall through to the SPA HTML shell. It used to -- an unknown /api/*
            # (e.g. GET /api/auth, which has no route) returned 200 text/html, and
            # the client's JSON fetch then blew up with `Unexpected token '<',
            # "<!doctype "...`. Return a JSON 404 (FastAPI's default detail body)
            # so an unknown API/auth/ws path reads as a proper API error, not the
            # index document. Real /api, /auth and /ws routes are registered ABOVE
            # this catch-all, so they still match first; only genuinely-unrouted
            # paths reach here. /auth is fenced too (it is an auth surface, never a
            # client-side SPA route -- the login dance is server-driven and the
            # in-app sign-in lives at "/"), so a stray GET there also 404s JSON
            # rather than leaking the shell.
            if (path == "api" or path.startswith("api/")
                    or path == "auth" or path.startswith("auth/")
                    or path == "ws" or path.startswith("ws/")):
                raise HTTPException(status_code=404, detail="Not Found")
            # CONTAINMENT: `UI_DIST / path` alone was an UNAUTHENTICATED
            # arbitrary file read. `{path:path}` captures separators, starlette
            # percent-decodes before we see it (so `..%2f` arrives as `../`, past
            # any client-side normalizer), and pathlib joins `..` literally —
            # `GET /..%2f..%2f..%2fUsers%2f<u>%2f.ssh%2fknown_hosts` returned the
            # file. This route is anonymous by necessity (it serves the sign-in
            # shell), so the read needed no credential, and the relay exposes it
            # off-LAN. A rejected path falls through to index.html rather than
            # 403ing: an unknown path IS a client-side SPA route, and a distinct
            # refusal would confirm which targets exist.
            try:
                target = safe_subpath(UI_DIST, path) if path else None
            except KeyError:
                target = None
            if target is not None and target.is_file():
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    # ----------------------------------------- BOOT RBAC route assertion (W2.2)
    # Fail create_app() LOUDLY if any mutating route is un-gated, tagged with a
    # retired cap, or reaches a motion sink without control.mount. The SPA
    # catch-all + the open auth-login dance are exempt. This runs LAST so every
    # route (incl. the included routers) is present when it enumerates.
    # NOTHING under /api is exempt. /api/framing/mosaic used to be, as "pure
    # stateless compute -- mutates no state, commands no device, so it is
    # read-equivalent". Stateless is not the same as secret-free: the route's
    # transit_alt answers are a function of the observing site, and the
    # 2026-08-01 cross-cut review recovered the latitude from it with no
    # principal at all. It now gates on view.status like its sibling
    # /api/visibility/order and declares the capability normally.
    #
    # The whole ``/auth`` prefix is owned by the provider lane's auth/routes.py
    # (the OIDC login dance + self-revoke logout + fail-closed /auth/me). Those
    # routes self-gate (login is open pre-session; logout revokes your OWN session
    # without admin.users; /auth/me is fail-closed) rather than via require(), so
    # the prefix is exempt from this app-side mutating-cap assertion.
    assert_route_capabilities(
        app,
        exempt_paths={"/{path:path}"},
        exempt_prefixes=("/assets", "/auth"))

    return app
