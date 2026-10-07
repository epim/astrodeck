# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""FastAPI application: REST command surface + WebSocket event stream.

Quick queries answer inline. Long operations (slews, autofocus, sequences,
centering) start a named background task and stream progress over the
WebSocket — the UI is event-driven.
"""
from __future__ import annotations

import asyncio
import functools
import hmac
import ipaddress
import io
import json
import math
import os
import re
import shutil
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import (Depends, FastAPI, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator)

from ..aio import reap
from ..alerting import AlertDispatcher
from ..auth import (ALL_CAPS, CAP_ADMIN_USERS, CAP_CONFIG_ALERTS,
                    CAP_VIEW_SITE_DERIVED,
                    CAP_CONFIG_BACKEND, CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                    CAP_CONFIG_SOLAR_OVERRIDE, CAP_CONTROL_CAPTURE,
                    CAP_CONTROL_GUIDE, CAP_CONTROL_MOUNT,
                    CAP_CONTROL_POWER, CAP_SYSTEM_UPDATE, CAP_VIEW_MEDIA,
                    CAP_VIEW_PREVIEW, CAP_VIEW_SITE_PRECISE, CAP_VIEW_STATUS,
                    CAP_VIEW_WEATHER,
                    Principal, TokenAdminProvider, _scope_is_remote,
                    configure_provider_from_auth, get_active_provider,
                    get_principal, require, resolve_principal,
                    set_active_provider)
from ..auth.rbac import assert_route_capabilities, declare
# Site-precision redaction helpers + the WS re-auth cadence live in a neutral,
# import-light module so BOTH the LAN /ws handler (here) and the relay-tunneled
# /ws handler (remote.relay_client) share ONE implementation. They cannot live
# here as nested closures: app.py imports remote.relay_client, so relay_client
# importing them back out of app.py would be a circular import.
from .redact import (WS_AUTH_RECHECK_S, _redact_drivers_for,  # re-exported at module scope
                     _redact_log_rows_for,
                     _redact_profile_for, _redact_report_for,
                     _redact_resume_arm_for, _redact_sequence_for,
                     _redact_session_for, _redact_site_for,
                     _redact_switch_ports_for, _redact_ws_event,
                     redact_bundle_csv_for, redact_bundle_manifest_for,
                     report_csv_columns)
from ..persist import safe_id_path, safe_subpath, secure_private_tree
from ..catalog import search          # rows AND the reasons for what is missing
from ..catalog import survey_pack as survey_pack_mod
from ..catalog.survey import router as survey_router
from ..catalog.tiles import router as tiles_router
from ..catalog.framing import router as framing_router
from ..catalog.visibility import router as visibility_router
from ..catalog.region import router as region_router
from ..catalog.ephemeris.routes import router as ephemeris_router
# The SER video lane (#D-RIG-1) is its own router for the reason the atlas
# routers are: one owner per file. The recorder singleton is imported beside
# it because the three single-camera routes below have to be able to ask
# whether a recording already owns the camera before they spawn a lane.
from ..imaging.video_routes import recorder as video_recorder
from ..imaging.video_routes import router as video_router
from ..config import (FRAME_SCOPES, REDACTED_SINK_FIELDS, AlertSink, AuthConfig,
                      CalibrationConfig, CloudmapConfig,
                      ConfigVersionConflict, CoolingConfig, DewConfig, DuskConfig,
                      EscalationConfig, FocusConfig, GuideConfig,
                      NamingConfig, Optics,
                      ProvidersConfig, RotatorConfig, SafetyConfig, Site,
                      StandardsConfig, SurveyConfig, SyncPushConfig,
                      UpdateConfig, WcsStampConfig,
                      WeatherConfig,
                      config_store, frames_payload, publish_frames, redacted,
                      set_frame_settings)
from ..locations import (UNCHANGED, LocationLibraryFull, LocationNameCollision,
                         location_store, normalize_horizon_points)
# Rig-level planning preferences (#D-FU-1). Its own router because the
# models live in config.py (to avoid an import cycle) and the merge helpers
# live beside them, not here.
from ..planning import router as planning_router
from .. import __version__
from ..update.state import update_state
from ..update.service import UpdateError, get_service as get_update_service
from ..dawn_park import DawnPark
from ..dusk_arm import DuskArm
from ..sun_watch import SunWatch
# A MODULE SINGLETON rather than a constructor: the orbital-element cache is
# one set of files on this box, so a second store would be a second writer
# to them.
from ..catalog.ephemeris.elements import ephemeris_store
from ..dew import DewController
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
from .. import capture_geometry
from .. import gallery_listing
from ..sync import manifest as sync_manifest_mod
from ..sync.runner import runner as sync_push_runner
from ..hub import CAPTURE_DIR, TOUCH_MAX_RATE_DEG_S, PromoteRefused, hub
from ..site_gate import site_is_set
from ..calibration import CalibrationLibrary, MatchTolerance
from ..calibration.matcher import LightNeed
from ..imaging import build_caption, compose_share_jpeg, fmt_share_date, to_png
from ..mount_offset import nudge as nudge_offset
from ..mount_offset import parse_nudge
from ..mount_offset import POSITION_UNKNOWN_CODE, POSITION_UNKNOWN_DETAIL
from ..naming import sanitize_component
from ..plans import PLAN_SCHEMA, PlanUnreadable, plan_library
from .. import power_guard
from ..profiles import Profile, profiles, redact_profile
from ..provenance import effective_config
# The flows package is imported by SUBMODULE. flows/__init__ re-exports only
# doctor/models/nodes — it predates five of the nine modules — so
# `from ..flows import compile_plan` is an ImportError, not a style choice.
from ..flows.calibration_health import (CalNeed, DEFAULT_QUOTA, KIND_ORDER,
                                        frame_from_header, health_matrix)
from ..flows.compile import compile_plan
from ..flows.continuation import (AdoptEvidence, AdoptMatches, adopt_detail,
                                  adopt_evidence, adopt_matches,
                                  apply_adoption, dropped_detail,
                                  plan_replace_report, recount,
                                  recount_detail, saved_before_s1)
# Two private helpers, and on purpose: which instants ADOPT samples a body
# step at, and the shape of a listed step, are continuation's to define, and
# the evidence check below must ask the same ones or it would judge a
# different set of instants from the set the match reads (#249).
from ..flows.continuation import _capture_times, _describe
# And the words for a count mode, for the same reason: the line a quiet
# recount logs (S4 orchestrator ruling 2) names the two modes in the words the
# recount question uses, so the operator reads one vocabulary for one change.
from ..flows.continuation import _MODE_WORDS
from ..flows.doctor import UNGUIDED_SUB_LINE_S, check as flow_doctor
from ..flows.models import (MY_FLOWS_FOLDER, FlowGraph, FlowRecord,
                            MigrationNote)
from ..flows.progress import continue_night, flow_progress, replay_facts
from ..flows.readouts import readouts as flow_readouts, rig_readout
from ..flows.rig import RigFacts
from ..flows.store import FlowLibraryFull, ReadOnlyFlow, flow_store
from ..flows import wizard as flow_wizard
from ..flows.to_plan import (GRID_MAX, OVERLAP_MAX_PCT, GraphNotRunnable,
                             blocking_reasons, losses, to_sequence_plan)
from ..flows.tonight import (banked_hours_by_target_from_reports,
                             banked_hours_from_reports,
                             flow_target_names,
                             frames_by_target_from_reports,
                             resolve_tonight)
from ..rotation import angle_equals, map_sky_target, mod360, sky_to_mechanical
from ..sequence import SequenceEngine, SequencePlan
from ..sequence import schedule as schedule_mod
from ..sequence.models import (FrameType, TargetGroup,
                               duplicate_name_warning, plan_identity_errors,
                               quota_unbounded, replan_cooling)
from ..sequence.policy import resolve_policy
from ..sequence.report import SessionReport, SessionReporter, _slug
from ..sequence.bundle import (CalibrationLibraryAdapter, NullMasterLibrary,
                               build_bundle, bundle_materialize_plan,
                               bundle_summary, build_script, externalize_bundle,
                               manifest_json, readme_text, weights_csv)
from ..sequence.resume_arm import ResumeArm
from ..plans import migrate_plan_policy_fields
from ..sequence.session import (Session, SessionUnreadable,
                                migrate_legacy_resume, session_store)
from ..sequence.session_files import active_session, files_index
from ..weather import NoNightError, weather_service
# Cloud-occlusion model (stage 6a). Imported HERE and nowhere near the sequence
# engine, the safety gate or the auto-resume arm: the model informs, it does not
# vote, and a named test greps those files to keep it that way.
from ..cloudmap.service import cloudmap_service

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

#: Content hashes for /api/sync/manifest, keyed (path, size, mtime_ns). THE SAME
#: OBJECT the push runner uses (``manifest.SHARED_HASH_CACHE``) — a pull agent
#: polling this route and a push sweeping to a NAS hash the same files with the
#: same function, so giving them one cache halves the disk reads instead of
#: giving each of them a cold one. Aliased rather than re-exported so the name
#: existing tests clear still works.
_SYNC_HASH_CACHE: dict = sync_manifest_mod.SHARED_HASH_CACHE

# Dawn park (backlog K / task #139). EVERY other park lives inside the sequence
# engine's run lifecycle, so a night that ended without a run — which is what a
# failed session looks like — left the mount tracking through sunrise with
# nothing scheduled to stop it. Needs the engine as well as the hub: its first
# question is whether a run is in progress, because the engine owns wind-down
# then and racing it is worse than not acting.
dawn_park = DawnPark(hub, engine)
dusk_arm = DuskArm(hub, engine, weather=weather_service,
                   connection_busy=lambda: (_connect_task is not None and not _connect_task.done())
                   or video_recorder.active)
hub.dusk_arm = dusk_arm

# Sun watch (task #150). The complement to ``Hub._check_solar``, which is a
# PRE-SLEW gate and can only ever refuse a destination: this one samples where
# the tube IS against where the Sun is going to be, so the Sun arriving at a
# stationary tube is noticed rather than discovered in the morning. Takes the
# engine for the same reason dawn park does — a live run owns its own aborts.
sun_watch = SunWatch(hub, engine)

# Dew heaters (#D-RIG-3). Module level beside the other two loops, and the hub
# is all it takes: it reads the weather surface the hub already publishes and
# drives whichever camera and switch ports say they follow the dew margin. The
# controller attaches ITSELF to ``hub.dew_controller`` in its constructor, so
# the status node finds it without hub.py importing a service it does not own.
dew_controller = DewController(hub)

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
    # This must remain outside every broad startup exception handler.  A DACL,
    # owner, reparse-point, filesystem, or POSIX mode failure is fatal before
    # any provider reads config or any background service is launched.
    secure_private_tree(config_module.CONFIG_DIR)
    # Install the configured auth provider from persisted AuthConfig (W2.3). With
    # the default ``provider="none"`` and no ``admin_token`` this is the open
    # NoneAuthProvider. A broken auth config MUST fail closed: continuing with
    # open-admin after an initialization error silently turns a protected rig
    # into an unauthenticated one.
    try:
        configure_provider_from_auth(config_store.cfg().auth)
        _warn_insecure_session_secret()
    except Exception as e:  # noqa: BLE001 - keep health/UI up, but deny authority
        set_active_provider(TokenAdminProvider(""))
        bus.log("error", f"auth provider init failed (FAIL-CLOSED): {e}", "auth")
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
    # #239 stage A: stored plans hold a concrete value for twelve settings that
    # are now the rig's standards unless a plan overrides them. One that equals
    # the old built-in default was never a choice, so it becomes "inherit" -
    # otherwise the rig's standards could not reach a single existing plan.
    # Idempotent, and only rewrites the plans it actually changes.
    try:
        moved = migrate_plan_policy_fields()
        if moved:
            bus.log("info", f"{moved} stored plan(s) now follow the rig's "
                            f"imaging standards", "plans")
    except Exception as e:  # noqa: BLE001 - degrade, never crash boot
        bus.log("error", f"plan policy migration failed: {e}", "plans")
    task = asyncio.create_task(dispatcher.run())
    # #125: what the PC's previous session ended as - clean, a planned restart,
    # or an unexpected end (power, crash, thermal reset) - into the night log
    # while the System log still says so. Off the loop and never raises; a
    # background task so a slow event log cannot delay boot.
    from ..bootcause import log_boot_cause
    boot_cause_task = asyncio.create_task(log_boot_cause())
    # Auto-resume-at-dusk service (sessions spec §5) — its own 60s asyncio loop.
    resume_arm.start()
    # Weather forecast poller (weather spec §3) — its own 60 s asyncio loop.
    # Started UNCONDITIONALLY: each tick no-ops unless cfg.weather.enabled AND
    # the site is set, so runtime config toggles take effect within one tick.
    weather_service.start()
    # GOES cloud-occlusion poller (cloud-occlusion stage 6a §3) — its own 60 s
    # asyncio loop, polling NOAA every 10 min. Started UNCONDITIONALLY for the
    # same reason as the weather poller: each tick no-ops unless
    # cfg.cloudmap.enabled AND the site is set, and the disabled path
    # constructs no HTTP client at all, so a runtime toggle takes effect within
    # one tick with no restart and a rig that never enables it pays nothing.
    cloudmap_service.start()
    # Gallery trash auto-purge — its own 6 h asyncio loop, first tick immediately
    # so a box that reboots daily still reaches the 30-day horizon. A no-op (one
    # `is_dir()`) until something has actually been deleted.
    trash_keeper.start()
    # Dawn park — its own 60 s asyncio loop, the safety net under a night that
    # ends with no sequence to wind it down. Started UNCONDITIONALLY: every tick
    # re-reads the site and the Sun, so it costs one trig evaluation on a rig
    # that never needs it and is armed the moment one does.
    dawn_park.start()
    dusk_arm.start()
    # Orbital elements (#D-SKY-1) - its own asyncio loop that keeps the
    # satellite and comet element files fresh. Started UNCONDITIONALLY for
    # the same reason as the parks above: a tick with nothing stale is one
    # `stat` per file and opens no socket at all, and the alternative -
    # starting it the first time somebody searches - means the first search
    # of the night is the one that waits on CelesTrak.
    ephemeris_store.start()
    # Sun watch — its own 60 s asyncio loop, the net under a tube the Sun is
    # coming TO rather than one being slewed at it. Started UNCONDITIONALLY for
    # the same reason: a tick with the mount disconnected or the sky elsewhere
    # costs one device read and a handful of trig, and it is armed the moment a
    # mount is connected and left somewhere.
    sun_watch.start()
    # Dew heaters (#D-RIG-3) - its own asyncio loop. Started UNCONDITIONALLY
    # too: a tick with the policy disabled reads one config field and
    # returns, and the loop being armed already is what lets the operator
    # turn dew control on mid-night without restarting the server.
    dew_controller.start()
    # File-sync push (Phase 2) — its own 5 s asyncio loop. Started
    # UNCONDITIONALLY for the same reason as the two above: a tick with no
    # destination configured is one attribute read, and it is armed the moment
    # someone saves one, with no restart.
    sync_push_runner.start()
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
        await cloudmap_service.stop()
        await resume_arm.stop()
        await trash_keeper.stop()
        await dusk_arm.stop()
        await dawn_park.stop()
        await ephemeris_store.stop()
        await sun_watch.stop()
        await dew_controller.stop()
        await sync_push_runner.stop()
        await dispatcher.stop()
        task.cancel()
        await reap(task)
        boot_cause_task.cancel()
        await reap(boot_cause_task)
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
            await reap(bt)
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


#: Lanes that must not overlap even though they carry DIFFERENT names.
#:
#: ``_spawn``'s one-task-per-name rule serializes a lane against itself, which is
#: all most operations need. The roof is the exception: a close PARKS THE MOUNT
#: first, so it is mount motion wearing another name. That is why the close was
#: spawned as ``goto`` — one lane, one mount, correct by construction — and why
#: the Settings "Close roof now" button then went dead during every unrelated
#: slew and explained itself with somebody else's operation.
#:
#: Splitting the lane keeps the exclusion rather than inheriting it. The mapping
#: reads ``key supersedes values``, and BOTH directions come from it (see
#: ``_lane_conflict``) so the halves of one interlock cannot drift apart:
#:   * closing the roof CANCELS an in-flight slew. A close is a safety action,
#:     and the route has already bumped the motion fence — leaving the fenced
#:     goto's task alive is the exact shape of the bug park was fixed for.
#:   * a slew is REFUSED while the roof lane is live. This is the half that
#:     would have been lost by simply renaming the lane: the close parks the
#:     mount and then travels a shutter over it, and an unpark+slew accepted in
#:     that window is how a tube meets a roof. ``close_observatory`` re-confirms
#:     parked before it moves the shutter, but nothing downstream can stop a
#:     slew that starts AFTER the roof is shut.
#:
#: WHAT THIS REACHES, exactly, so the sentence above is not read as wider than
#: the guard: every route that spawns into the ``goto`` lane (goto, park, home)
#: plus ``/api/mount/unpark``, which calls ``_refuse_if_lane_blocked`` directly
#: because it never spawns — and unpark is the choke point, since a parked mount
#: refuses axis motion at the driver. NOT reached: ``/api/mount/stop`` (never
#: refused, on purpose — an emergency stop must work during a close) and the
#: sequence engine's own slews, which do not go through ``_spawn`` at all. The
#: engine case is pre-existing and unchanged by the split: a manual close during
#: a run has always been able to race the run's slews, which is why the close
#: bumps the motion fence.
_LANE_SUPERSEDES: dict[str, tuple[str, ...]] = {
    "dome": ("goto",),
    # POLAR AND GOTO ARE MUTUALLY EXCLUSIVE, both directions.
    #
    # Three-point alignment's whole premise is that the ONLY thing moving this
    # mount for the next two minutes is the alignment: it solves three fields
    # separated by a PURE RA ROTATION and fits a circle through them. Anything
    # else that slews — or syncs — between those points does not degrade the
    # answer, it invalidates it, and the fit reports a confident number computed
    # from three unrelated positions.
    #
    # Measured on the rig 2026-08-06: a goto_and_center was still running when
    # an alignment started. It solved, synced and re-centred TWICE between the
    # measurement points; the declination moved 1.1 deg between point 1 and
    # point 2, which a pure RA rotation cannot do. The fit returned 4747' of
    # total error (79 deg) for a mount whose north leg was on geographic north.
    # Nothing refused, nothing warned, and the number looked like every other
    # number this screen prints.
    #
    # Both directions, because either order produces the same corruption:
    "polar": ("goto",),        # a slew is refused while an alignment measures
    "goto": ("polar",),        # an alignment is refused while a slew is live
}

#: What to tell the operator when the reverse direction refuses, per superseding
#: lane. A 409 reading "'goto' is already running" about a ROOF would send
#: somebody hunting for a slew nobody started.
_LANE_BLOCK_REASON: dict[str, str] = {
    "dome": "the roof is closing — the mount was parked so the shutter could "
            "travel over it, and moving it now is how a tube meets a roof",
    "polar": "polar alignment is measuring — it solves three fields separated "
             "by a pure rotation in RA, and a slew between them does not blur "
             "the answer, it invalidates it. Stop the alignment first",
    "goto": "the mount is still slewing — an alignment started now would "
            "measure three positions the slew moved between, and report a "
            "confident number computed from them. Wait for it to settle",
}


#: How long a cancel route waits for the lane it just cancelled to unwind
#: before halting the device and answering anyway.
#:
#: The wait is not politeness: both autofocus paths restore the focuser inside
#: a SHIELDED move on their way out, and that move is the difference between a
#: cancelled sweep leaving the drawtube at focus and leaving it wherever the
#: sweep abandoned it. 30 s covers the widest restore a sweep can owe (a full
#: half-span at the crawl an EAF manages under load) and is far short of the
#: 180 s the sequencer allows a filter-offset move, which is a bound on a
#: background step rather than on somebody holding a phone.
_LANE_UNWIND_TIMEOUT_S = 30.0


def _lane_conflict(name: str) -> str | None:
    """The live lane that forbids starting ``name`` right now, or None.

    DERIVED from ``_LANE_SUPERSEDES`` rather than written out a second time, so
    the refuse direction can never disagree with the supersede direction."""
    if dusk_arm.connecting and name not in ("park", "abort", "dome"):
        return "dusk preparation"
    for winner, losers in _LANE_SUPERSEDES.items():
        if name in losers:
            t = hub._busy.get(winner)
            if t is not None and not t.done():
                return winner
    return None


def _lane_409(detail: str, *, code: str, lane: str, **extra) -> HTTPException:
    """A 409 a CLIENT can act on, not just print.

    These refusals were bare-string details, so ``ApiError.code`` came back
    undefined and the UI had to match a naked 409 — indistinguishable from every
    other conflict a route can raise. The human sentence is UNCHANGED and still
    lands at ``detail.detail`` (the nested shape ``lib/apiError.ts`` already
    parses, and which this file uses for name_collision / version_too_new /
    sun_exclusion), so nothing that read the message before reads anything
    different now; the code and lane are additive."""
    return HTTPException(409, detail={"detail": detail, "code": code,
                                      "lane": lane, **extra})


def _discard(coro) -> None:
    """Close a coroutine we are refusing to run, so a 409 does not ALSO emit
    "coroutine ... was never awaited" into the log of a rig that did nothing
    wrong."""
    close = getattr(coro, "close", None)
    if callable(close):
        close()


def _sequence_envelope(engine) -> dict:
    """The sequence payload, with `state` and `running` made to agree (#117).

    Both seams that serve a sequence to a client (GET /api/sequence/state and
    the monitor snapshot) bolt liveness onto `engine.state`, which the engine
    owns and publishes on its own schedule. Between `SequenceEngine.start()`
    returning and `_run` reaching its first `_set_state`, that dict still says
    `state: "idle"` while `engine.running` is already True - and that window is
    not instants: the safety gates, the cooling wait, the slew, the autofocus
    and the plate solve all happen inside it, minutes of it on this rig.

    A client then gets a different answer depending on which field it trusts.
    My own night supervisor keys on `state` against a working set, so a
    starting run read as not-working and the guard that exists to notice a
    stalled run was watching a sequence it believed was not running at all.

    It is the recurring shape: a verdict field contradicting the evidence field
    beside it, with the verdict wearing the obvious name (#111 `is_valid` over
    an advisory saying the axes are questionable, #112 `cloudy: false` over a
    reason string saying cloudy).

    Fixed here, at the seam, rather than in `start()`: it corrects every
    consumer at once and leaves the engine's own publishing contract alone.
    The comment at that line ("_run publishes running once execution actually
    begins") is a deliberate choice about when the ENGINE says running, and an
    automated pass is not the place to relitigate it.

    Only `idle` is rewritten, and only while the task is live. A terminal state
    is left exactly as it is: `running` stays True through the teardown that
    follows an abort, and "aborting" or "aborted" is the true thing to say
    there - rewriting it to "running" would reinstate, on the abort path, the
    same lie this removes from the start path.

    `live` IS RECOMPUTED HERE TOO, FRESH, ON EVERY CALL (#665). `_set_state`
    only calls `SequenceEngine._live_block` AT a published transition (a
    frame boundary, a hold starting or ending, ...), so `engine.state["live"]`
    is whatever that one instant cached - and an open-ended wait with no
    transition in the middle (a meridian wait, a cloud hold, an idle
    park-hold) can span the whole rest of the hold with nothing re-publishing
    it. A GET made minutes into such a wait served the meridian ETA and the
    sensor temperature exactly as they stood at the START of the wait, not
    as of the GET (confirmed on the harness's clocked meridian-straddle
    night: `hub.last_meridian` already read "not tracking" while the state
    read minutes later still carried the stale pre-wait countdown).
    `_live_block` is already documented "best-effort, sync, no device I/O",
    which is exactly what makes it safe to call again here, at the one seam
    both polling consumers (this route and the monitor snapshot) share,
    rather than teaching every open-ended wait to re-publish on a timer or
    editing the engine's own publish schedule (`engine.py` is WP-56's this
    wave). The WS stream is UNCHANGED: it only ever carries what a publish
    sends, and a long hold still sends none until something happens - this
    fixes every caller that POLLS, which is what #665 was about.

    A fake engine with no `_live_block` (as the lightweight doubles in
    test_sequence_envelope_agrees.py use) is left exactly as it serves
    `state`'s own `live`, if any: this only overrides it for an engine
    that actually offers a fresh answer.
    """
    payload = engine.state | {"running": engine.running, "paused": engine.paused}
    if engine.running and payload.get("state") == "idle":
        payload["state"] = "running"
    compute_live = getattr(engine, "_live_block", None)
    if callable(compute_live):
        live = compute_live()
        if live:
            payload["live"] = live
        else:
            payload.pop("live", None)
    return payload


def _refuse_if_lane_blocked(name: str) -> None:
    """Raise the cross-lane 409 for ``name``, or return.

    Split out of ``_spawn`` so a route that has SIDE EFFECTS TO PERFORM FIRST
    can take the refusal before it performs them. ``/api/mount/park`` and
    ``/api/mount/home`` bump the motion fence before spawning, and park's own
    docstring names bump-then-409 as a past bug: the fence bump abandons an
    in-flight motion, so a refusal after it leaves the rig with the sabotage and
    without the park. Calling this first means a park refused because the roof
    is closing changes nothing at all."""
    blocker = _lane_conflict(name)
    if blocker is not None:
        raise _lane_409(_LANE_BLOCK_REASON.get(
            blocker, f"'{blocker}' is running and '{name}' cannot run with it"),
            code="lane_blocked", lane=name, blocked_by=blocker)


#: The one sentence a route refuses with when a .ser recording holds the camera.
#: A constant because it is asserted by name in tests and shown verbatim by the
#: UI, and because eight routes saying it eight ways is eight strings to drift.
_VIDEO_OWNS_CAMERA = "a video recording owns the camera"


def _refuse_if_camera_owned() -> None:
    """Raise 409 ``video_owns_camera`` while a .ser recording is running.

    ONE HELPER, CALLED FROM EVERY ROUTE THAT EXPOSES. A recording holds the
    hub's exposure guard for the whole file, so any other route that wants the
    camera is going to be refused - the only question is WHERE. Refused here,
    the caller gets a 409 and the rig does nothing. Refused inside the spawned
    lane, the route has already answered 202, the UI has already drawn a
    running sweep, and the failure arrives as a log line nobody is reading.

    THE WORSE HALF IS THE MOUNT. ``/api/sequence/start`` and ``/api/polar/start``
    do not merely want the camera - they SLEW. Starting a plan while a
    planetary recording is running took the mount out from under the file and
    left an hour of frames of empty sky, and the recording kept writing them.
    Same for the rotator's two solving routes, which turn the camera AND the
    rotator, and for the guide-offset measurement, which is two plate solves.

    The three capture routes said this inline and the other eight said nothing;
    it is one function now so the next route that exposes gets the guard by
    calling it rather than by remembering the sentence.
    """
    if dusk_arm.connecting:
        raise HTTPException(409, detail={"detail": "Dusk preparation is connecting equipment. Try again when it finishes.",
                                         "code": "dusk_connecting"})
    if video_recorder.active:
        raise HTTPException(409, detail={"detail": _VIDEO_OWNS_CAMERA,
                                         "code": "video_owns_camera"})


def _refuse_start_while_rig_is_held() -> None:
    """The guards EVERY start path runs before anything else (#323).

    ONE HELPER FOR THE FOUR HTTP START PATHS: ``/api/sequence/start``,
    ``/api/flows/{id}/run`` (fresh and CONTINUE), ``/api/sessions/{id}/resume``
    and ``/api/sequence/recover``. Only the first called
    ``_refuse_if_camera_owned`` until #323, so a flow run, a resume from the
    session list or the Recover button pressed during a planetary .ser
    recording slewed the mount off the planet, and the recording filled with
    empty sky; the dusk-connecting refusal was skipped by the same three.
    That is #291's class, in ``run_flow``'s words: "a second start path that
    quietly omits one is how a guard stops being a guard". A guard added
    HERE reaches all four, which is the point of it being one function and
    not four calls.

    FIRST, BEFORE ANY PRE-FLIGHT, because every start is a SLEW: the horizon,
    Sun, identity and quota checks are about the plan, and this is about
    whether the rig is someone else's right now. NOT WAIVED BY ``force``,
    which overrides the horizon pre-flight and nothing that belongs to
    another lane's hardware, so it takes no arguments.

    What it holds today: the camera-ownership refusals
    (``_refuse_if_camera_owned``: 409 ``dusk_connecting`` while dusk
    preparation connects equipment, 409 ``video_owns_camera`` while a
    recording runs). The resume ladder's refusal is not here, because it
    must be read with no await before ``engine.start``
    (``_refuse_while_resume_recovers``); ResumeArm, the fifth start path,
    starts from its own loop and does not come through these routes."""
    _refuse_if_camera_owned()


def _refuse_plan_identity(plan: SequencePlan, status: int) -> None:
    """Refuse a plan whose ids repeat, or whose rules name a repeated target
    (#156), and log the duplicate-name warning when the plan may start anyway.

    ONE HELPER FOR THE FOUR HTTP START PATHS. ``engine.start`` is deliberately
    unguarded, so a start path that forgets this check is a start path without
    it; ResumeArm, the fifth, calls the same pure functions itself.

    ``status`` is the caller's: 422 where the request carries the plan
    (``/api/sequence/start``, ``/api/flows/{id}/run``), 409 where a stored
    session does (resume, recover). The session is left exactly as it was, so
    it stays listed, and can be edited and resumed."""
    errors = plan_identity_errors(plan)
    if errors:
        raise HTTPException(status, detail={"detail": "; ".join(errors),
                                            "code": "plan_identity"})
    warning = duplicate_name_warning(plan)
    if warning:
        bus.log("warning", warning, "sequence")


_RESUME_RECOVERING = (
    "Auto-resume is re-centring the mount after a restart and will start its "
    "armed session when that is done; the Monitor shows the re-centring and "
    "the step it is on. Wait for it, or turn that session's auto-resume off, "
    "which stops the re-centring before its next step (an abort does the "
    "same); start again once it has stopped.")


def _refuse_while_resume_recovers() -> None:
    """Raise 409 ``resume_recovering`` while ResumeArm's recovery ladder runs
    (#189 A7, spec 5.9 "One starter per session").

    ONE MOTION SOURCE AT A TIME. After a restart the ladder reads the safety
    monitor, may autofocus, blind-solves and re-centres the mount, and it is
    minutes long. None of it is a run, so ``engine.running`` is False the
    whole time and ``engine.start``'s "already running" refusal does not
    see it. A start that got in put a run's first slew on a mount the ladder
    was still slewing; the ladder only notices at its next step
    (``ResumeArm._a_run_took_over``), never in the middle of one.

    ONE HELPER FOR THE FOUR HTTP START PATHS, called IMMEDIATELY before
    ``engine.start`` with no ``await`` between. That is what makes a flag
    enough: ResumeArm raises ``recovering`` in the same synchronous stretch as
    its own ``engine.running`` check, so a route's check-and-start runs either
    wholly before that check (and the tick then finds the engine running) or
    wholly after the flag went up. An ``await`` added between this call and
    the start reopens the gap. ResumeArm, the fifth start path, is the one
    this guards against, so it does not call this.

    Not a refusal of the session: nothing is written, so it stays dormant
    and armed, and the ladder's own start follows.

    THE SENTENCE NAMES WHAT THE OPERATOR CAN REACH (#220, #246). ``GET
    /api/sequence/resume-arm`` reports ``recovering`` and the step the ladder
    is on (``ResumeArm.recovery``), and the Monitor draws both (the UI half
    of #246), so the sentence sends the operator to the Monitor. It used to
    name the route, which is no place a person pressing RUN on a phone can
    go and read. The action it names is the DISARM: a PATCH
    that turns the session's auto-resume off stops the ladder before its next
    step and cancels the step it is awaiting (``ResumeArm.stop_recovery``),
    and it is the one control both UIs show for that session while the
    ladder runs, the session list's auto-resume switch, because the session
    is dormant and armed. Abort stops the ladder too and disarms the session,
    as Abort disarms a running session (spec 6.15), but the sentence does not
    send the operator to press it: while the ladder runs the engine is idle,
    and both UIs draw their Abort and STOP only for a live run (classic
    Monitor's ``runActive``, #/next's ``LIVE_STATES``; the one exception is
    the locked screen's EMERGENCY STOP), so "press Abort" named a control the
    operator pressing RUN could not find. Either way the start is refused
    until the ladder has actually returned, which a cancelled step makes a
    matter of a turn of the loop, so "once it has stopped" is not a long
    wait. Before #220 nothing stopped the ladder and no route said it was
    running, and the sentence could only say to wait it out."""
    if resume_arm.recovering:
        raise HTTPException(409, detail={"detail": _RESUME_RECOVERING,
                                         "code": "resume_recovering"})


#: The 409 detail a teardown route has always given for a run, a capture loop
#: or a polar alignment, unchanged when no ladder runs
#: (test_connect_rig_guard.py pins it as written).
_TEARDOWN_BUSY = "a sequence, capture loop or polar alignment is running"

#: Names the Monitor, as ``_RESUME_RECOVERING`` does, and not the route
#: (WP-67, #272). H3 wrote this naming ``GET /api/sequence/resume-arm``,
#: the only place to read the ladder's step at the time, but neither UI
#: drew a 409's own detail then (#256), so the wording was latent. WP-39
#: made both UIs draw it, so what this says now reaches the operator, and
#: it is reworded to send them to the same place #246 sends
#: ``_RESUME_RECOVERING``'s reader.
_TEARDOWN_WHILE_RECOVERING = (
    "auto-resume is re-centring the mount after a restart "
    "(the Monitor shows the step it is on); force stops "
    "the re-centring before its next step and turns that session's "
    "auto-resume off, as Abort does, then goes ahead")


def _teardown_busy_detail() -> str | None:
    """The 409 detail for an unforced profile apply, profile activate or
    ``/api/connect/rig``, or None when nothing they would tear down under is
    running (#238, spec 6.15).

    THE LADDER COUNTS AS RUNNING. Each of the three disconnects the rig
    before it builds another, and they refused while a run, a capture loop
    or a polar alignment ran, but not while ResumeArm's recovery ladder did,
    and the ladder is the thing using the camera and the mount after a
    restart, with the engine idle all the while. So they tore the rig down
    under a solve or a slew without asking for confirmation. Now the ladder
    refuses them too, under the same code ``running`` (the clients that
    offer "force" on it need nothing new), and the detail says what is
    running, because "a sequence ... is running" over an idle engine would
    send the operator looking for a run that is not there.

    Reads ``resume_arm.recovering`` and the three busy flags with no await,
    so the answer is one moment's."""
    busy = engine.running or hub.looping or hub.polar.running
    if not resume_arm.recovering:
        return _TEARDOWN_BUSY if busy else None
    return (f"{_TEARDOWN_BUSY}, and " if busy else "") + \
        _TEARDOWN_WHILE_RECOVERING


async def _wait_for_the_ladder() -> None:
    """After ``resume_arm.stop_recovery``: return once the recovery ladder
    has returned, or raise 409 ``running`` when it has not within
    ``LADDER_STOP_WAIT_S`` (#238).

    ASKING IS NOT STOPPING. ``stop_recovery`` raises the ladder's flag and
    cancels the step it is awaiting, and a step can take its time to end on
    a cancel (a driver sending its halt) or swallow it and run to its end.
    Until the ladder has returned it is still using the camera or the mount,
    and a teardown then pulls the devices out from under it. So every route
    that tears the rig down calls this between the stop and the teardown,
    and touches nothing when it raises. The stop and the disarm stand: they
    are what the operator asked for, and the next press finds the ladder
    gone or still going."""
    if await resume_arm.wait_stopped():
        return
    from ..sequence import resume_arm as _resume_arm_mod
    # Names the Monitor, not the route, for the same reason
    # ``_TEARDOWN_WHILE_RECOVERING`` does (WP-67, #272).
    raise HTTPException(409, detail={
        "detail": ("auto-resume's re-centring was asked to stop and has not "
                   f"stopped within {_resume_arm_mod.LADDER_STOP_WAIT_S:g} s, "
                   "so nothing was torn down; its session's auto-resume is "
                   "off. The Monitor reports it until it "
                   "has stopped; try again then."),
        "code": "running"})


#: The statuses ``DELETE /api/sessions/{id}`` removes. Named rather than "not
#: active" because ``Session.status`` is a plain string: a hand-edited or
#: half-written file can carry anything, and a delete should not guess (#212).
_DELETABLE_STATUSES = ("dormant", "complete", "abandoned")


def _asks_adopt(session: Session, report) -> bool:
    """Whether CONTINUE asks ADOPT's question about ``session``, given the
    ``plan_replace_report`` of it against tonight's compile (spec 5.9 (a)):
    no step id shared, frames banked, and saved before S1.

    ONE GATE, ASKED TWICE. ``run_flow`` asks it of its first read, to decide
    whether to spend the catalogue's time on ``adopt_evidence`` before the
    lock; ``_continue_flow_session`` asks it of the re-read, and that answer
    decides."""
    return (not report.kept and bool(session.frames)
            and saved_before_s1(session))


#: How a step is listed when its frames outran ADOPT's catalogue lookup (#249).
_ADOPT_AGAIN = ("frames were captured on this step after ADOPT looked it up in "
                "the catalogue, so it was not matched; press ADOPT again to "
                "include them")


def _unasked(session: Session, plan: SequencePlan,
             evidence: AdoptEvidence) -> set[str]:
    """The steps of ``session`` that hold frames and whose match ``evidence``
    holds no answer for (#249): every step of a target whose name it was
    never asked, and every body step with a capture instant it was never
    asked at (``_capture_times``, the instants the match reads). Every step
    that holds frames, when a name of ``plan`` is missing: a new key built
    without its answer could pair anything.

    THE EVIDENCE IS OLDER THAN THE SESSION IT JUDGES. ``run_flow`` asks the
    catalogue about its FIRST read, off the loop and outside the lock, and
    the match is made on the RE-READ inside the lock. Frames a run banked in
    between (a ResumeArm run that started and ended in the gap) are on the
    re-read alone. ``adopt_matches`` maps no step on an answer it does not
    hold, so nothing is mis-credited, but it lists such a step as a body
    "the catalogue could not place", which is false and sends the operator
    nowhere. The next press asks the catalogue about a read that holds those
    frames, so that is what these steps are told to do.

    A deep-sky step needs its name and nothing else: its bound is against
    tonight's target, which no frame moves."""
    by_step: dict[str, list] = {}
    for f in session.frames:
        by_step.setdefault(f.step_id, []).append(f)
    held = {st.id for t in session.plan.targets for st in t.steps
            if by_step.get(st.id)}
    if any(t.name not in evidence.names for t in plan.targets):
        return held
    out: set[str] = set()
    for t in session.plan.targets:
        if t.name not in evidence.names:
            out.update(st.id for st in t.steps if st.id in held)
            continue
        hit = evidence.names[t.name]
        if hit is None or not hit.moves:
            continue
        for st in t.steps:
            if st.id in held and any(
                    (t.name, when) not in evidence.positions
                    for when in _capture_times(by_step[st.id],
                                               session.created_ts)):
                out.add(st.id)
    return out


def _relisted(session: Session, matches: AdoptMatches,
              unasked: set[str]) -> AdoptMatches:
    """``matches`` with every step in ``unasked`` taken out of the mapping
    and the other lists, and listed first, with ``_ADOPT_AGAIN``."""
    counts: dict[str, int] = {}
    for f in session.frames:
        counts[f.step_id] = counts.get(f.step_id, 0) + 1
    again = [_describe(t, st, counts[st.id], _ADOPT_AGAIN)
             for t in session.plan.targets for st in t.steps
             if st.id in unasked]
    return AdoptMatches(
        mapping={k: v for k, v in matches.mapping.items()
                 if k not in unasked},
        unmatched=[*again, *(u for u in matches.unmatched
                             if u["step_id"] not in unasked)],
        ambiguous=[a for a in matches.ambiguous
                   if a["step_id"] not in unasked],
        frames_matched=matches.frames_matched - sum(
            counts.get(k, 0) for k in matches.mapping if k in unasked))


def _adopt_again_detail(steps: int) -> str:
    return (f". {steps} step{'' if steps == 1 else 's'} took frames after "
            f"ADOPT looked this session up in the catalogue; press ADOPT "
            f"again to include them")


def _continue_flow_session(first_read: Session, plan: SequencePlan,
                           body: FlowRunBody,
                           evidence: AdoptEvidence | None = None, *,
                           plan_saved_ts: float | None = None) -> dict:
    """CONTINUE a flow's dormant session on tonight's compile, or refuse with
    a 409 that says what continuing would do (#189 S1, spec 5.9, D6).

    ONE CRITICAL SECTION, SYNCHRONOUS, UNDER THE STORE'S WRITE LOCK: re-read
    the session, require it dormant, decide, replace the plan, start. There is
    no ``await`` in here, so nothing else on the event loop - ResumeArm, the
    engine's ledger writes, a finalize - runs between the read and the start,
    and the lock holds off store writes made from worker threads, deletes
    included (#212).

    That is the whole defence against the one-starter race (2026-09-18).
    ``patch_session`` loads, checks and saves in separate ``to_thread`` calls,
    and ResumeArm can start the same session in between: the save then puts a
    stale frame list over the file of a session that is now running. So this:

    * NEVER SAVES THE SESSION ITSELF. ``engine.start(session=)`` persists it,
      as a resume does. A start that is refused ("already running") has
      written nothing.
    * RE-READS inside the lock. ``first_read`` is the route's lookup, made
      before an await; a ResumeArm run that started - or started and ENDED -
      since then has banked frames that only the file knows about, and
      continuing the route's copy would start a run from a ledger without
      them. It is used for its id and nothing else.

    The refusals come in the order the UI asks them, each one lifted only by
    its own flag on the next request:

    (a) ``adopt`` - no step id is shared, the ledger holds frames, and the
        session was saved before S1 (``saved_before_s1``: none of its ids is
        one the compile mints), so its uuid4 ids no compile produces again.
        Continuing it as it is would count none of them; starting fresh
        without a word would disarm it (``engine.start``'s singleton). With
        ``adopt`` the unique matches are re-keyed and a ``.bak`` is taken
        before ``engine.start`` writes the result. What does not match still
        meets (c). A session compiled since S1 that shares no id was
        re-framed (a single TARGET is keyed on its geometry): it goes straight
        to (c), and an ``adopt`` flag re-keys nothing, because re-keying it
        by name would credit the old field's frames to the new one (D5).
        The match asks the catalogue nothing: ``evidence`` is
        ``adopt_evidence``'s answer, asked by ``run_flow`` off the loop and
        before the lock (#249), and a step it holds no answer for is told
        to press ADOPT again (``_unasked``).
    (b) ``recount`` - the ledger is counted by the frozen plan's
        ``count_mode``, so a different mode recounts every banked frame.
        Asked ONLY WHEN THE TWO TOTALS DIFFER (S4 orchestrator ruling 2,
        #348). With both totals equal - no rejected frame banked, or no
        frame at all - nothing banked recounts differently, and the question
        would carry nothing the operator can act on: the save already said
        "now counts accepted subs only". So the continue goes ahead in
        tonight's mode, and one info line after the start names both modes
        and says so.
    (c) ``dropped_steps`` - steps that hold frames are gone from the flow. The
        frames stay in the ledger and on disk; they stop counting.

    A refusal leaves the file exactly as it was: every change above is made
    to the in-memory copy, and only ``engine.start`` writes it.

    What carries over is tonight's compile, with one exception: a session
    that has a sensor temperature keeps it, and a different setpoint tonight
    is said in the log, not obeyed (the comment at the replace says why).

    ``plan_saved_ts`` is the saved time of the flow version ``plan`` was
    compiled from (``run_flow`` reads it off the record it compiled), and it
    becomes the session's with the plan: the version an armed auto-resume
    replays from here on (#473). It is set on the copy ``engine.start``
    writes, so it is written by that start or not at all.

    The answer's ``night`` is the observing night this run falls on
    (``Session.night_at``, #430): the nights the session has run, plus one
    only when tonight is not already one of them.
    """
    with session_store.write_locked():
        try:
            s = session_store.load(first_read.id)
        except (KeyError, SessionUnreadable):
            s = None
        if s is None or s.status != "dormant":
            now = "no longer on disk" if s is None else f"now {s.status}"
            raise HTTPException(409, detail={
                "code": "session_changed", "session_id": first_read.id,
                "status": None if s is None else s.status,
                "detail": f"this flow's session changed while the run was "
                          f"being prepared: it is {now}, so it was not "
                          f"continued and nothing was written. Press Run "
                          f"again."})
        report = plan_replace_report(s, plan)
        adopted = None
        if _asks_adopt(s, report):
            # NO CATALOGUE CALL IN HERE (#249). The match reads ``evidence``,
            # which ``run_flow`` asked for on a worker thread before the
            # lock, and nothing else. When the first read did not ask this
            # question and the re-read does (frames banked on a frameless
            # pre-S1 session in the gap), there is no evidence, and every
            # step that holds frames is told to press ADOPT again rather
            # than looked up here.
            held = (evidence if evidence is not None
                    else AdoptEvidence(names={}, positions={}))
            matches = adopt_matches(s, plan, evidence=held)
            unasked = _unasked(s, plan, held)
            if unasked:
                matches = _relisted(s, matches, unasked)
            # Refused even with ``adopt`` while any step is unasked: adopted
            # now, its frames would stay on a step id the new plan does not
            # have, and once the plan is replaced there is no ADOPT left to
            # press. Nothing is written, so the next press starts clean.
            if not body.adopt or unasked:
                detail = adopt_detail(len(s.frames), matches.frames_matched)
                if unasked:
                    detail += _adopt_again_detail(len(unasked))
                raise HTTPException(409, detail={
                    "code": "adopt",
                    "detail": detail,
                    "adopt": {"session_id": s.id, "frames": len(s.frames),
                              "matched": matches.frames_matched,
                              "unmatched": matches.rest()}})
            apply_adoption(s, matches)
            adopted = matches
            report = plan_replace_report(s, plan)
        # THE RECOUNT QUESTION ASKS ONLY ABOUT A DIFFERENCE (S4 orchestrator
        # ruling 2, #348). Since S3 every save writes "Accepted subs"
        # (Revision 2 ruling 2), so the first CONTINUE of every flow whose
        # dormant session predates S3 meets a mode change; asked whatever the
        # totals, it read "counted every sub taken (5); counting accepted
        # subs makes it 5", a warning-shaped dialog about nothing. Equal
        # totals mean no banked frame counts differently, only future ones
        # do, so the continue runs in tonight's mode unasked and the log
        # says why (``quiet_recount``, after the start).
        quiet_recount: tuple[str, str, int] | None = None
        if s.plan.count_mode != plan.count_mode:
            before, after = recount(s, plan)
            if before != after and not body.accept_recount:
                raise HTTPException(409, detail={
                    "code": "recount",
                    "detail": recount_detail(s.plan.count_mode,
                                             plan.count_mode, before, after),
                    "before": before, "after": after, "session_id": s.id})
            if before == after:
                quiet_recount = (s.plan.count_mode, plan.count_mode, before)
        if report.dropped and not body.accept_dropped:
            raise HTTPException(409, detail={
                "code": "dropped_steps",
                "detail": dropped_detail(report.dropped_frames),
                "dropped_frames": report.dropped_frames, "session_id": s.id})
        if adopted is not None:
            # Before the first write of the re-keyed ledger, which is
            # engine.start's. Raises rather than rewrite without a copy.
            session_store.backup(s.id)
        # THE OBSERVING NIGHT, NOT THE RUN COUNT (#430, S7 orchestrator
        # ruling 7). ``nights`` holds a report id per start, so a second
        # CONTINUE in one evening, or one after a crash-resume at 01:40, was
        # called the next night. Tonight is counted once, as the night log's
        # file is, by ``events.night_key``. Read before ``engine.start``
        # appends tonight's id, so it is the night this run starts.
        night = s.night_at(time.time())
        # THE SESSION KEEPS ITS SENSOR TEMPERATURE (#189 hardening A1). A
        # flow has no cooling node, so tonight's compile carries TONIGHT'S
        # standing setpoint, and replacing the plan with it would move a
        # session shot at -10 °C to -15 °C because the setpoint changed in
        # between. Subs that span two sensor temperatures cannot share one
        # dark library, which is why ``replan_cooling`` never re-resolves a
        # plan that has a temperature; the plan replace below went round that
        # rule. So a temperature the session has is carried onto the plan,
        # and ``replan_cooling`` stays a no-op for it. A session with none
        # takes tonight's, as a resume does: no continuity to break.
        kept_c = s.plan.cool_to
        moved: tuple[float, float] | None = None
        if kept_c is not None:
            # Said only when tonight asks for a DIFFERENT temperature. A
            # cleared setpoint asks for none, so there is no second
            # temperature to name, and the session's own is kept quietly,
            # as ``replan_cooling`` keeps it on a resume.
            if plan.cool_to is not None and plan.cool_to != kept_c:
                moved = (kept_c, plan.cool_to)
            plan = plan.model_copy(update={"cool_to": kept_c})
        s.plan = plan
        s.name = plan.name or s.name
        # THE FROZEN VERSION MOVES WITH THE PLAN (#473): the plan the session
        # now holds, and an armed auto-resume will replay, is this version's.
        # On the copy engine.start saves, never saved here.
        s.plan_saved_ts = plan_saved_ts
        # The call /api/sessions/{id}/resume makes: a continue is a NEW run
        # and re-reads the standing setpoint, which a plan with a temperature
        # ignores (replan_cooling).
        disarmed = engine.start(replan_cooling(
            plan, config_store.cfg().cooling.setpoint_c), session=s)
    if moved is not None:
        # After the start, not before it: a start the engine refused
        # ("already running") continued nothing, and must not say it did.
        bus.log("warning",
                f"'{s.name}' continues at {moved[0]:g}°C, the temperature "
                f"its frames were shot at, not at tonight's setpoint of "
                f"{moved[1]:g}°C: subs at two sensor temperatures cannot "
                f"share one dark library. START OVER begins a new session "
                f"at {moved[1]:g}°C.", "sequence")
    if quiet_recount is not None:
        # After the start, like the temperature line: a refused start
        # continued nothing and changed no mode. Info, not warning: it is
        # not a problem, it is the reason nothing was asked.
        was, now, banked = quiet_recount
        bus.log("info",
                f"'{s.name}' continues counting "
                f"{_MODE_WORDS.get(now, now)} where its session counted "
                f"{_MODE_WORDS.get(was, was)}: the captured frame count is unchanged "
                f"({banked} sub{'' if banked == 1 else 's'} "
                f"either way), so nothing was asked.", "sequence")
    out = {"id": s.id, "night": night, "continued": True,
           "kept": len(report.kept), "new": len(report.new),
           "dropped": len(report.dropped)}
    if adopted is not None:
        out["adopted"] = {"matched": adopted.frames_matched,
                          "unmatched": adopted.rest()}
    if disarmed:
        # #595, D-04: CONTINUE arms this session exactly as a fresh start
        # does, so it rides the same singleton and can disarm another
        # session just as silently. ``run_flow`` lifts this to the top of
        # its own response, alongside the fresh-start branch's.
        out["disarmed"] = disarmed
    return out


def _freeze_saved_version(plan_saved_ts: float | None) -> None:
    """Record, on the session a FRESH flow run just made, the saved time of
    the flow version its plan was compiled from (#473, S7 orchestrator ruling
    1). Called by ``run_flow`` straight after ``engine.start``, with no await
    between, so the engine's run task has not yet taken a turn.

    ON THE ENGINE'S OWN SESSION OBJECT, NOT ON A COPY LOADED FROM DISK.
    ``engine.start`` makes the session (it owns the fresh branch: the
    origin, the arming, the singleton disarm) and keeps it as ``_session``,
    writing that whole object back at every ledger write and at finalize. A
    field written to the file alone would be put back to None by the run's
    first frame. Set on the object, every later write of the run carries it,
    and ``save_run_state`` writes it now, so a run that dies before its
    first frame keeps it too.

    Bookkeeping after a start that has succeeded: a write that fails is said
    and never turns the started run into a failed request. The value stays
    on the object, so the run's next ledger write persists it anyway."""
    ours = getattr(engine, "_session", None)
    if ours is None:
        return
    ours.plan_saved_ts = plan_saved_ts
    try:
        session_store.save_run_state(ours)
    except Exception as e:      # noqa: BLE001 - never fail a live run
        bus.log("warning", f"could not record which version of the flow "
                           f"'{ours.name}' froze: {e}", "flow")


#: The ``end_reason`` ``GET /api/sequence/recoverable`` answers for a run
#: the process stopped under (#487): the one ending no report can record,
#: because the process that would have written it is gone. The recoverable
#: card's restart sentence reads this word and no other
#: (``Interrupted.tsx``'s ``END_REASON_RESTART``, held to it by
#: tests/test_h4_recoverable_says_why.py).
RESTART_END_REASON = "restart"


def _why_dormant(session: Session,
                 last: SessionReport | None) -> str | None:
    """Why ``session`` went dormant, for the recoverable card, from its last
    report (``last``, the ``SessionReport`` of ``session.nights[-1]``, or
    None when there is none or it could not be read) (#487).

    THE REPORT'S OWN WORD, VERBATIM, WHEN IT RECORDED ONE. Every ending the
    engine reaches in-process stamps the report through
    ``_finalize_report``: "aborted" for a STOP, "shutdown" for a polite
    server stop, "incomplete", "dawn_cutoff", "error", "unsafe" and the
    rest. Until #487 the route carried none of them, and the card said "The
    server restarted" after every one, including an operator's STOP.

    ``RESTART_END_REASON`` FOR THE EVIDENCE A RESTART LEAVES, and only for
    it. A process that stops under a run (a power cut, a crash, a kill by
    PID) never reaches ``_finalize_report``, so two traces are left and
    nothing else leaves both: the last report is on disk but records no
    ending (``engine.start`` writes it at the start since #517, and only the
    finalize stamps ``end_reason``), and the session was still ``active`` at
    boot, which ``SessionStore.boot_sweep`` turns dormant and counts as a
    death in ``crash_resumes``. Both are asked. A report with no ending on a
    session nobody swept (a final write that failed, a file edited by hand)
    is no evidence of a restart, and neither is a report that is missing or
    unreadable: those answer None, and the card then states no cause.

    A POLITE STOP OF THE SERVER IS TOLD FROM A STOP (#565, fixed). A
    teardown that cancels the run task (Ctrl+C, a service stop) lands on
    the engine's ``except CancelledError`` arm too, but that arm now
    finalizes "shutdown" there and "aborted" only when the cancel came
    through ``abort()`` (keyed on ``_aborting``, same as the disarm in
    ``_finalize_report``). This function reads whichever word the report
    recorded, verbatim, so the card can tell the two apart without asking
    this function to re-derive anything: a hard kill (no ending recorded
    at all) still falls through to ``RESTART_END_REASON`` below."""
    if last is None:
        return None
    if isinstance(last.end_reason, str) and last.end_reason:
        return last.end_reason
    if session.crash_resumes >= 1:
        return RESTART_END_REASON
    return None


def _spawn(name: str, coro, *, replace: bool = False) -> dict:
    """Run a long operation as a named background task (one per name).

    ``replace=True`` cancels an existing same-named task instead of 409-ing — used
    by park, which is a motion-committing ABORT that must supersede an in-flight
    goto rather than be rejected by it. The cancelled goto unwinds (its slew abort
    + motion-fence bump already fenced it), releasing ``_motion_lock`` before the
    replacement acquires it, so the two never touch the mount at once.

    Cross-lane exclusion (``_LANE_SUPERSEDES``) is applied FIRST, in both
    directions: a lane that supersedes another cancels it here, and a lane held
    off by a live superseding lane is refused here — before anything is spawned,
    so the refusal costs the rig nothing. Routes that act before they spawn call
    ``_refuse_if_lane_blocked`` themselves, earlier; this is the backstop for
    every route that does not."""
    try:
        _refuse_if_lane_blocked(name)
    except HTTPException:
        _discard(coro)
        raise
    for loser in _LANE_SUPERSEDES.get(name, ()):
        t = hub._busy.get(loser)
        if t is not None and not t.done():
            t.cancel()
    existing = hub._busy.get(name)
    if existing and not existing.done():
        if not replace:
            _discard(coro)
            raise _lane_409(f"'{name}' is already running",
                            code="lane_busy", lane=name)
        existing.cancel()

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", f"{name} cancelled", name)
            # RE-RAISE (#252): this coroutine runs as the task's own top
            # level, so swallowing its cancellation here would leave the task
            # reporting a normal, uncancelled completion -- a claim nothing
            # behind it keeps, the same broken promise #235/#252 names
            # elsewhere. Nothing today awaits this task directly, but the
            # guard that scans for the shape cannot know that, and a future
            # caller that DOES await it deserves an honest `cancelled()`.
            raise
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
    pending connect blocks another connect/apply from stomping it.

    THE MISSING LANE IS DELIBERATE, and it must stay missing. ``busy_lanes()``
    is built from ``hub._busy`` alone, so publishing a "profile" lane means
    putting a task in ``_busy`` — and ``_busy`` is not a display list, it is the
    CANCEL LIST ``hub._teardown`` walks. Two things follow, and only the first
    of them has been defused:
      1. the self-cancel this function exists for. ``_teardown`` now skips
         ``asyncio.current_task()`` (hub.py:1001), so the driver would survive
         its own teardown — but that is defence-in-depth around a hazard, not a
         licence to re-enter it;
      2. ``_teardown`` ends with ``self._busy.clear()``. The lane would
         therefore VANISH the moment the teardown finished — i.e. at the start
         of the slow part, the reconnect — and a control gated on it would
         unlock itself mid-connect. That is worse than no lane: it is a lane
         that lies. Nothing app.py can do fixes it, because the clear happens
         inside the hub.
    So the connect stays out, and the client reads the RESULT instead (the
    active-profile pointer, which ``connect_rigspec`` sets only on success —
    see ProfileList's poll). ``test_busy_lanes_routes.py`` pins both halves.
    """
    global _connect_task
    busy = dusk_arm.connecting or (_connect_task is not None and not _connect_task.done())
    if not busy and (t := hub._busy.get("profile")) and not t.done():
        busy = True
    if busy:
        _discard(coro)
        raise _lane_409("'profile' is already running",
                        code="lane_busy", lane="profile")

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", "profile cancelled", "profile")
            raise  # #252: see _spawn.wrapped -- the task must report cancelled.
        except (DeviceError, Exception) as e:
            bus.log("error", f"profile failed: {e}", "profile")

    _connect_task = asyncio.create_task(wrapped())
    return {"started": "profile"}


def _err(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


#: Query parameter names that carry a place or a pointing, and so may never
#: ride a URL (#520). A URL is written down by every hop it crosses - the Fly
#: relay's access log, a reverse proxy, the browser's history - and a pointing
#: at a known time is a function of the site. The two routes that used to take
#: them refuse them outright (`_refuse_site_query`); nothing else declares them,
#: which test_h4_no_route_takes_site_query_params holds.
_SITE_QUERY_NAMES = frozenset({"alt", "az", "lat", "lon", "site"})


def _refuse_site_query(request: Request, instead: str) -> None:
    """422 for a request whose query string names a place or a pointing.

    REFUSED RATHER THAN IGNORED. FastAPI drops an undeclared query parameter
    without a word, so a tab loaded before #520 would go on sending the mount's
    alt/az - and every log between it and here would go on recording them -
    while getting a perfectly good answer back. A 422 makes it fail where
    somebody will see it.

    The refusal names the PARAMETERS, never their values: the body goes back
    through the same relay, and FastAPI's own 422 would echo the input.
    """
    named = sorted(k for k in request.query_params.keys()
                   if k in _SITE_QUERY_NAMES)
    if named:
        raise HTTPException(422, detail={
            "detail": ("this route no longer takes " + ", ".join(named)
                       + " in its query string, where every log on the way "
                       "writes them down: " + instead),
            "code": "site_query_refused"})


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
    request_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    exposure_s: float = 1.0
    gain: int = 100
    offset: int = 30
    binning: int = 1
    save: bool = False
    target: str = ""
    # One of the four, in any case, blank for Light, and always handed on as
    # the spelling IMAGETYP carries (``sequence.models.FrameType``, #334). A
    # plain ``str`` passed 'L' + i-diaeresis + 'ght' through to ``save_fits``,
    # which failed the save after the exposure had been taken.
    frame_type: FrameType = "Light"


class PromoteBody(BaseModel):
    """Body for ``POST /api/capture/last/save`` (D-SES-4).

    ``target`` is the ONE value the operator may change after the fact. Every
    other header value was a MEASUREMENT, frozen when the shutter closed; the
    target is what they meant to call the field, and naming it correctly is
    often the reason they are saving at all. Empty or absent keeps whatever
    they typed at capture time.

    ``frame_id`` is the id from ``GET /api/capture/last``. Optional, and it is
    a SAFETY interlock rather than a selector: the buffer holds exactly one
    frame, so a client that names the one it is looking at gets a refusal
    instead of silently saving a newer exposure it never saw. Absent = save
    whatever is held."""
    target: str = ""
    frame_id: int | None = None


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
    #: allow_inf_nan=False, and this one is not hardening-in-general. The clamp
    #: downstream is ``max(-ceiling, min(ceiling, rate))``, and EVERY comparison
    #: with a NaN is False - so ``min`` returns its first argument and ``max``
    #: returns its first argument, and a posted ``NaN`` came out of the clamp as
    #: the FULL driver ceiling (1.44 deg/s on the AM5). The deadman was then
    #: armed with 1.44 and the mount driven at it, from a body that named no
    #: rate at all. A literal ``NaN`` survives ``json.loads``, so the only place
    #: to stop it is validation.
    rate_deg_s: float = Field(..., allow_inf_nan=False)


class NudgeBody(BaseModel):
    """A RELATIVE offset for ``POST /api/mount/nudge`` (#D-RIG-4)."""
    axis: Literal["ra", "dec"]
    #: signed; + is east / north. allow_inf_nan=False for the same reason
    #: GotoBody.rotation_deg has it: a NaN must never reach the geometry.
    arcmin: float = Field(..., allow_inf_nan=False)


class FocuserMoveBody(BaseModel):
    position: int


class FocuserSetPositionBody(BaseModel):
    """Re-anchor: declare the current position. Moves nothing."""

    position: int


class RotatorMoveBody(BaseModel):
    position_deg: float
    #: Send the single mechanical move to the target, with none of the
    #: one-sided approach's overshoot (#526), and log it as a calibration move
    #: (#594, WP-88). For a measurement that asks whether the camera follows
    #: the rotator (``scripts/rig_rotator_follow.py``): a move against the
    #: approach direction otherwise goes ``ROTATOR_BACKLASH_DEG`` past its
    #: target and comes back, which adds 10 degrees of travel to the thing
    #: under test. False by default, so Go and the nudge buttons, which post
    #: here too, keep the approach.
    direct: bool = False


class RotatorReverseBody(BaseModel):
    reverse: bool


class RotateToPaBody(BaseModel):
    # allow_inf_nan=False: same NaN/inf hardening as GotoBody.rotation_deg.
    target_pa_deg: float = Field(..., allow_inf_nan=False)
    exposure_s: float = 3.0


class RotatorSyncBody(BaseModel):
    """Sky-sync the rotator (no motion). Only the solve frame's exposure."""
    exposure_s: float = Field(3.0, gt=0, le=60)


class AutofocusBody(BaseModel):
    exposure_s: float = 2.0
    gain: int = 120
    #: None — the default — sizes the sweep from this focuser's MEASURED
    #: defocus slope (focus/span.py), falling back to the shipped 350 until a
    #: sweep has measured one. A number is honoured verbatim: the Focus screen's
    #: Advanced panel exists so an operator can overrule us.
    step: int | None = None
    steps_each_side: int = 4
    binning: int = 2
    filter: int | None = None  # UX-25: slot to move to before the sweep (per-filter AF)


class GuidedCheckpointBody(BaseModel):
    context: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    fact: Literal["location", "horizon", "focus", "alignment"]
    action: Literal["complete", "invalidate"]

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
    #: Per-slot "this slot is narrowband" flags. Same contract as ``opaque``:
    #: None leaves the stored set alone, a list replaces it wholesale. What it
    #: changes is the exposure and gain an offset-learning sweep uses on that
    #: slot — a 3-7 nm passband delivers a star 40-100x fainter than luminance.
    narrowband: list[bool] | None = None
    #: Per-slot capture settings. Same whole-list contract as ``opaque`` — None
    #: leaves the stored set alone, a list replaces it — but the ELEMENTS are
    #: tri-state: ``null`` in a slot means "not pinned", which 0 cannot mean
    #: because 0 is a real gain. These are DEFAULTS the camera dial and the plan
    #: editor seed from; the sequence engine never reads them (a plan that gets
    #: rewritten underneath the operator stops describing the night). The one
    #: authoritative reader is a focus sweep, which has no plan to consult.
    exposures: list[float | None] | None = None
    gains: list[int | None] | None = None


class CloudmapAtBody(BaseModel):
    """``POST /api/cloudmap/at``: a direction somebody PICKED (#520).

    Plain floats, deliberately without ``allow_inf_nan=False``: a NaN azimuth
    reaches ``at_payload`` and is refused there with stage 4's sentence as a
    400, the same answer the old query form gave, so the two forms cannot come
    to disagree about what a domain error is."""
    alt: float
    az: float
    ahead_s: float = 0.0


class SiteSkyPreviewBody(BaseModel):
    """``POST /api/site/sky``: the site picker's typed coordinates (#520).

    In a body, where no access log writes them. Finite, because a NaN is not a
    place and the arithmetic does not say so: measured without the check, a NaN
    latitude came back 200 with the Sun at 90 degrees in the southern
    hemisphere, a confident read-back for nowhere."""
    lat: float = Field(allow_inf_nan=False)
    lon: float = Field(allow_inf_nan=False)


class AcknowledgeBody(BaseModel):
    """Body for the restricted-asset acknowledgment (#216).

    ``note`` is free text the person recording it can leave for whoever reads
    the record later — which is the point of recording it rather than flipping
    a flag. ``withdraw`` takes it back."""
    note: str = ""
    withdraw: bool = False


class GuideCameraSettingsBody(BaseModel):
    """The guide frame's own imaging settings.

    MODULE SCOPE, not ``create_app``'s, and that is the whole point. ``app.py``
    runs under ``from __future__ import annotations``, so every route signature
    reaches FastAPI as a STRING that it resolves against this module's globals.
    A body model declared inside ``create_app`` is not in those globals, so
    ``body: GuideCameraSettingsBody`` did not resolve to a BaseModel, FastAPI
    fell back to treating ``body`` as a QUERY parameter, and every PUT answered
    ``422 {"loc": ["query", "body"], "msg": "Field required"}`` — measured on the
    rig 2026-08-08, mid-calibration, reaching for the longer exposure this
    endpoint exists to provide. It was the only body model in the file not
    declared out here; keep new ones out here too."""
    exposure_s: float | None = Field(None, gt=0, le=15)
    gain: int | None = Field(None, ge=0, le=1000)
    #: #187: applied to every guide exposure since the loop was written, and
    #: settable by nothing until 2026-08-08 — the route answered a literal 30.
    offset: int | None = Field(None, ge=0, le=255)
    binning: int | None = Field(None, ge=1, le=4)


class FrameSettingsBody(BaseModel):
    """A partial update to ONE frame-settings scope (#176).

    All optional, ``exclude_unset`` semantics: only the fields the client SENDS
    change, and an explicit null clears that field back to its default — the
    contract ``/api/polar/solve-settings`` has always had, kept because that
    route is now a delegate onto this one.

    MODULE SCOPE, like ``GuideCameraSettingsBody`` above and for the same
    reason: ``app.py`` runs under ``from __future__ import annotations``, so a
    body model declared inside ``create_app`` is not in the globals FastAPI
    resolves signatures against and the parameter silently degrades to a query
    field. Keep new body models out here.
    """
    exposure_s: float | None = Field(None, gt=0, le=3600)
    gain: int | None = Field(None, ge=0, le=1000)
    offset: int | None = Field(None, ge=0, le=255)
    binning: int | None = Field(None, ge=1, le=4)
    #: A filter NAME, or null for "leave the wheel where it is". Never a slot
    #: index: the index is a property of how the wheel was wired this session.
    filter: str | None = None


class LearnOffsetsBody(BaseModel):
    """Per-filter AF-offset auto-learn. ``ref_slot`` None -> the hub picks an
    L/Lum/Clear slot when the wheel has one, else the current position."""
    ref_slot: int | None = None
    exposure_s: float = 2.0
    gain: int = 120
    #: None sizes each slot's sweep from the measured defocus slope — see
    #: AutofocusBody.step.
    step: int | None = None
    steps_each_side: int = 4
    binning: int = 2
    #: Which slots are narrowband. None leaves the wheel's stored marking alone
    #: (it persists per profile); a list replaces it AND is saved before the run
    #: starts, so ticking three boxes and pressing Start does not have to be
    #: repeated next time — or after a cancel.
    narrowband: list[bool] | None = None
    #: The exposure/gain those slots sweep at. None derives them from the
    #: broadband pair above — see focus.filter_offsets.narrowband_sweep_settings
    #: for where the multiple comes from.
    nb_exposure_s: float | None = None
    nb_gain: int | None = None


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


class SwitchPortSettingsBody(BaseModel):
    """One port's protection/dew settings (#D-RIG-5). Partial: absent means
    UNCHANGED, read off ``model_fields_set``, never value-vs-default. That
    distinction is load-bearing for ``protect_during_run``, which is TRI-state:
    ``null`` is one of its three real values ("follow the name"), so a caller
    editing only ``follow_dew`` cannot signal "leave it alone" with null.
    MODULE SCOPE, like ``GuideCameraSettingsBody`` above and for the same reason."""
    protect_during_run: bool | None = None
    follow_dew: bool = False


class PolarSolveSettingsBody(BaseModel):
    """Imaging settings for the native TPPA's solve frames (PUT
    /api/polar/solve-settings). All optional: only the fields the client SENDS
    change, and sending null clears a field back to its default. Bounds match
    the capture surface's. The exposure ceiling was 30 s ("past that the
    pointing is the problem") — wrong for a real rig class: an OSC camera
    behind a narrowband filter legitimately needs minutes per solve frame
    (operator feedback 2026-08-07 20:09), so the ceiling now matches the
    longest preset the Align dial offers."""
    exposure_s: float | None = Field(None, gt=0, le=300)
    gain: int | None = Field(None, ge=0, le=1000)
    offset: int | None = Field(None, ge=0, le=255)
    binning: int | None = Field(None, ge=1, le=4)
    filter: str | None = None


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


class FanBody(BaseModel):
    """Issue #22. Bounded at the boundary so a bad value is a 422, never a
    write the camera has to refuse."""
    power: int = Field(..., ge=0, le=100)


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
    can, plus optional per-role ``roles`` overrides.

    ``force`` is the same escape hatch the two profile routes carry, and it is
    here for the same reason: connecting a rig DISCONNECTS the current one
    (issue #20)."""
    primary: str
    roles: dict[str, ConnSpecBody] = {}
    force: bool = False


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
    replayed request can never wipe a rig by accident. The destructive extras
    are SEPARATE fields (not one "everything" flag) because captured frames,
    sign-in accounts and relay pairing are different kinds of loss and each must
    be chosen on its own. ``reset_remote`` is the ownership-transfer opt-in
    (OPEN-004): it clears relay pairing + the stored update credential so a
    resold box carries no path back to the previous operator — see
    ``factory_reset.py`` for the tier contract."""
    confirm: str = ""
    delete_captures: bool = False
    reset_auth: bool = False
    reset_remote: bool = False


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
    #: The site's drawn horizon PROFILE: ``[[az_deg, alt_deg], ...]``, az 0..360,
    #: alt -10..90. Validated by the SAME rule the store applies (sorted, one
    #: point per azimuth) so an out-of-range point 422s at the boundary instead
    #: of raising inside LocationStore's post-model_copy re-validate. Absent/None
    #: means "no drawn horizon"; ``[]`` means "no obstructions" (an explicit
    #: clear) — the two are NOT the same on apply.
    horizon_points: list[list[float]] | None = None

    @field_validator("horizon_points", mode="before")
    @classmethod
    def _horizon_points_valid(cls, v):
        return normalize_horizon_points(v)

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


def _accepted_count_mode_if_omitted(plan: SequencePlan) -> SequencePlan:
    """``plan``, with ``count_mode`` stamped "accepted" when the CLIENT'S
    OWN body never named it (#141, backlog WP-19(c), owner-approved
    2026-09-30).

    ``SequencePlan.count_mode``'s bare pydantic default stays "attempts"
    (NOT flipped to "accepted"): the model is constructed at 377 sites
    across the tree, and the coder who tried flipping the default found 2
    regressions in a 17-file sample -- an unbounded blast radius for a fix
    this narrow. The classic Plan tab is the ONE caller whose plans should
    default to "accepted" (a prior UX review, #30, already made the UI's
    OWN new-plan default send it explicitly), so this stamps it at the
    ROUTE, only for a body that left the field out entirely.

    ``plan.model_fields_set`` is what makes "omitted" legible at all: by
    the time a route holds a validated ``SequencePlan``, a field the client
    never sent and one the client sent as the SAME value as the bare
    default are otherwise indistinguishable (`attempts == attempts`), so
    checking `plan.count_mode` itself cannot tell "defaulted" from
    "explicitly chosen". pydantic tracks, per model instance, exactly which
    fields the input actually named -- including a NESTED model's own
    fields, validated from its own slice of the body -- so this reads that
    set rather than the value."""
    if "count_mode" in plan.model_fields_set:
        return plan
    return plan.model_copy(update={"count_mode": "accepted"})


class FlowWizardBody(BaseModel):
    """The sheet's three answers, the Mosaic kind's and the door's.

    VALIDATED AGAINST THE GENERATOR'S OWN CONSTANTS rather than re-typed here.
    wizard.py opens by explaining why: a caller matching on "EAA quick look" and
    a generator matching on "EAA Quick Look" silently builds a guided deep-sky
    night instead. A second copy of these strings in this file is exactly that
    bug with a longer fuse.

    An unknown kind or chip is REFUSED (422) rather than dropped. Dropping it
    generates a night the operator did not ask for and gives them no way to tell
    -- the wizard exists so somebody's first flow works, and quietly building a
    different one fails that harder than an error does.
    """
    kind: str = flow_wizard.KIND_DEEP_SKY
    options: list[str] = Field(default_factory=list)
    target: str = ""
    #: Only consulted for an unguided lane; the generator picks a safe default.
    #: BOUNDED STRICTLY BELOW THE DOCTOR'S RULE 2 LINE (#432), read from the
    #: doctor's one constant: the generator writes this answer onto an
    #: unguided lane's CAPTURE LOOP and holds the door's filter rows to it,
    #: and from the line up the doctor warns "stars will trail" on the
    #: wizard's own output, which every generated graph must never do (spec
    #: 1.8, and Revision 2, ruling 4). It was ``le=3600`` until S7, so an
    #: answer of 150 came back 200 already warning. Refused whatever the
    #: lane, since a field bound cannot see the chips; the generator refuses
    #: the same answer in its own words (``wizard._unguided_seconds``) for a
    #: caller that does not come through this door.
    unguided_exposure_s: float | None = Field(
        None, gt=0, lt=UNGUIDED_SUB_LINE_S)
    #: THE MOSAIC KIND'S ANSWERS (#189 spec 1.8, #196). The generator refuses
    #: them with any other kind, and refuses a grid of one panel, a missing
    #: angle, and "Rotate to PA" on a rig with no rotator; the route answers
    #: those 422. What is checked HERE is each answer on its own, against
    #: the wizard's constants, because with no optics the generator answers
    #: one target and never reads the grid: without the door a malformed
    #: grid would come back 200 as a single target. The camera field and the
    #: measured angle are NOT answers: the route injects them from the rig,
    #: and a client cannot send either.
    rows: int | None = None
    cols: int | None = None
    #: Percent, as a TARGET holds it; None takes the wizard's default.
    overlap_pct: float | None = None
    angle_mode: str | None = None
    #: The position angle the grid is laid out at, degrees.
    pa_deg: float | None = None
    #: USE MEASURED: lay the grid out at the angle the last solve measured.
    use_measured: bool = False
    #: THE DOOR'S ANSWERS (#196, spec Revision 2 ruling 4, S6): what Send to
    #: Flow Wizard pre-fills from the Sky FRAME or the Atlas, beside the
    #: three answers. Every one defaults to None, which the generator reads
    #: as not given and changes nothing for: a body with only the three
    #: original answers generates exactly what it did (ruling 4), and a
    #: default here would change every flow the sheet has ever made. The
    #: generator checks each (``wizard.generate_answer``); what is checked
    #: HERE is what pydantic's coercion would hide from it (a ``true``
    #: counted as 1 pass, a "true" read as guiding) and the skip, which
    #: with no optics the generator never reads. The wheel the rows are
    #: checked against is a rig fact the route injects, like the field.
    #:
    #: The coordinates as typed: the framing's centre, not the catalogue's.
    ra: str | None = Field(None, max_length=64)
    dec: str | None = Field(None, max_length=64)
    #: The panels to leave out, as a TARGET's `skip` holds them ("2-3, 1-1").
    #: Declared after ``rows`` and ``cols``, which its validator reads.
    skip: str | None = Field(None, max_length=1000)
    #: The filter rows, a FILTER CYCLE's slot table ("L 60, R 60"), and how
    #: many subs of each (its passes).
    cycle_plan: str | None = Field(None, max_length=1000)
    cycles: int | None = None
    #: On lights the Guiding chip; off says it stays dark.
    guiding: bool | None = None
    #: THE NIGHT AND RESUME ANSWERS (backlog WP-100, #196): what the sheet's
    #: NIGHT and RESUME steps ask, written to the lane's one DUSK WINDOW.
    #: Every one defaults to None and writes NOTHING then, for the reason the
    #: door's answers above do: a default here would be written into every
    #: flow the sheet has ever made, and "silent" is not "answered Dawn".
    #: Each is checked by the wizard's own reader, before pydantic's coercion
    #: (a ``true`` is not a stop, and read as 1.0 it would be a floor of one
    #: degree nobody chose), and the generator checks them again, with the
    #: pairs that only make sense together (a "Clock time" and its clock).
    stop: str | None = None
    stop_clock: str | None = None
    start: str | None = None
    start_clock: str | None = None
    min_alt: float | None = None
    auto_resume: str | None = None

    @field_validator("stop", mode="before")
    @classmethod
    def _stop(cls, v):
        return flow_wizard.checked_stop(v)

    @field_validator("start", mode="before")
    @classmethod
    def _start(cls, v):
        return flow_wizard.checked_start(v)

    @field_validator("stop_clock", mode="before")
    @classmethod
    def _stop_clock(cls, v):
        return flow_wizard.checked_clock("stop_clock", v)

    @field_validator("start_clock", mode="before")
    @classmethod
    def _start_clock(cls, v):
        return flow_wizard.checked_clock("start_clock", v)

    @field_validator("min_alt", mode="before")
    @classmethod
    def _min_alt(cls, v):
        return flow_wizard.checked_min_alt(v)

    @field_validator("auto_resume", mode="before")
    @classmethod
    def _auto_resume(cls, v):
        return flow_wizard.checked_auto_resume(v)

    @field_validator("cycles", mode="before")
    @classmethod
    def _cycles(cls, v):
        # Before pydantic's int coercion, which would read True as 1 pass
        # and "10" as 10, through the wizard's own reading of a count.
        return None if v is None else flow_wizard.checked_cycles(v)

    @field_validator("guiding", mode="before")
    @classmethod
    def _guiding(cls, v):
        # Before pydantic's bool coercion, which reads 1, "true" and "yes"
        # as True: a client that sent one of those meant something the
        # answer does not say.
        return flow_wizard.checked_guiding(v)

    @field_validator("skip")
    @classmethod
    def _skip(cls, v, info):
        # Read against the grid it names, whether or not the rig has
        # optics: with none the generator answers one target and never
        # reads the grid, so a skip naming no panel would come back 200 as
        # a single target, the gap the grid's own door closes.
        if v is None or not v.strip():
            return v
        if "rows" not in info.data or "cols" not in info.data:
            return v            # a side was refused; its error says why
        rows, cols = info.data["rows"], info.data["cols"]
        if rows is None or cols is None:
            raise ValueError("skip names panels of a grid, and this answer "
                             "has no rows and cols")
        return flow_wizard.checked_skip(v, rows, cols)

    @field_validator("rows", "cols", mode="before")
    @classmethod
    def _grid_side(cls, v):
        # Before pydantic's int coercion, which would take True as 1 and "2"
        # as 2: the generator refuses both spellings, and so does the door.
        # A whole float (2.0) is a whole number to both, and is taken as 2.
        if v is None:
            return v
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or not float(v).is_integer()
                or not flow_wizard.GRID_MIN <= v <= GRID_MAX):
            raise ValueError(f"a side of a mosaic's grid is a whole number "
                             f"from {flow_wizard.GRID_MIN} to {GRID_MAX}, "
                             f"not {v!r}")
        return int(v)

    # The overlap and the PA are checked BEFORE pydantic's float coercion,
    # as the grid's sides are: coerced, ``true`` arrives as 1.0 and "30" as
    # 30.0, and the generator, which refuses a bool or a string for either,
    # never sees what was sent. A PA of 1.0 that nobody typed is a default
    # angle nobody chose, the I-04 defect (spec 1.8, #344).
    @field_validator("overlap_pct", mode="before")
    @classmethod
    def _overlap_pct(cls, v):
        if v is not None and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v)
                              or not flow_wizard.OVERLAP_MIN_PCT <= v
                              <= OVERLAP_MAX_PCT):
            raise ValueError(f"a mosaic's overlap is a percentage from "
                             f"{flow_wizard.OVERLAP_MIN_PCT:g} to "
                             f"{OVERLAP_MAX_PCT:g}, not {v!r}")
        return v

    @field_validator("angle_mode")
    @classmethod
    def _mosaic_angle(cls, v):
        if v is not None and v not in flow_wizard.MOSAIC_ANGLES:
            raise ValueError(
                f"a mosaic is laid out at one camera angle, so its angle is "
                + " or ".join(repr(a) for a in flow_wizard.MOSAIC_ANGLES)
                + f", not {v!r}")
        return v

    @field_validator("pa_deg", mode="before")
    @classmethod
    def _finite_pa(cls, v):
        if v is not None and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v)):
            raise ValueError(f"a PA is a finite number of degrees, not {v!r}")
        return v

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in flow_wizard.KINDS:
            raise ValueError(
                f"unknown kind {v!r}; expected one of "
                + ", ".join(repr(k) for k in flow_wizard.KINDS))
        return v

    @field_validator("options")
    @classmethod
    def _known_options(cls, v: list[str]) -> list[str]:
        bad = [o for o in v if o not in flow_wizard.AUTOMATION_OPTIONS]
        if bad:
            raise ValueError(
                "unknown automation " + ", ".join(repr(b) for b in bad)
                + "; expected from "
                + ", ".join(repr(o) for o in flow_wizard.AUTOMATION_OPTIONS))
        return v


class FlowQuickTarget(BaseModel):
    """What the picker hands over: the three strings the TARGET node stores.

    RA AND DEC ARE REQUIRED, and that is the whole point of taking a target
    object instead of a name. A flow whose TARGET carries a name and the node's
    shipped M31 coordinates slews to Andromeda and files the frames under the
    name that was typed -- ``to_plan`` reads ra/dec and never the name.
    """
    name: str = Field(min_length=1, max_length=120)
    ra: str = Field(min_length=1, max_length=64)
    dec: str = Field(min_length=1, max_length=64)


class FlowQuickBody(BaseModel):
    """The quick sheet's four answers, plus what to do with the result.

    ``filters`` EMPTY MEANS ONE CHANNEL (a colour camera, or a rig with no
    wheel) -- see ``wizard.quick``. It is not a missing answer: the sheet shows
    a single "OSC" row and no checkboxes when the rig has no wheel, and there is
    nothing there to tick.

    ``exposures`` is optional and per filter; anything omitted takes the
    generator's default (broadband 60 s, narrowband 180 s). The sheet sends what
    it displayed, so the operator gets the numbers they were looking at.
    """
    target: FlowQuickTarget
    subs: int = Field(10, ge=1, le=10_000)
    filters: list[str] = Field(default_factory=list)
    exposures: dict[str, float] | None = None
    guided: bool = True
    #: Start it on the engine as well. Refused exactly as POST
    #: /api/flows/{id}/run refuses -- it IS that route's handler.
    run: bool = False
    name: str | None = Field(None, max_length=120)


class FlowSaveBody(BaseModel):
    """The record to persist.

    Server-owned fields on it are IGNORED rather than trusted — see
    ``_persist_flow``. ``readonly`` is cleared by the route, and the store
    takes ``created_ts``, ``last_run`` and ``last_result`` from the file it
    replaces (``store.BOOKKEEPING``, #364), so a client cannot forge
    ``last_result: "ok"`` onto a flow that has never run, the field the
    library card draws.
    """
    flow: FlowRecord


#: What a SAVE'S answer says in ``migrated`` when the save switched a TARGET
#: or POOL to counting accepted subs (#189 Revision 2, ruling 2). The ruling
#: fixes the UI's words, "now counts accepted subs only", and both editors
#: print their own line for the ``counts`` key; this is the sentence for a
#: caller with no line of its own (the API, a script). A MigrationNote, not
#: the bare "counts" the ruling writes, so the answer stays a record a client
#: may send straight back: ``FlowRecord.migrated`` refuses a bare string, and
#: a save drops whatever ``migrated`` it is sent.
COUNTS_SWITCHED_NOTE = (
    "now counts accepted subs only: this save switched every TARGET and POOL "
    "that counted every sub taken, rejected ones included")

#: Ruling 2's second sentence, verbatim, added to the read's counts note when
#: the session Run would continue is dormant. The ledger is counted by its
#: frozen plan's ``count_mode`` (spec 5.9), so a save does not recount it:
#: the first CONTINUE asks, with both totals (``accept_recount``).
COUNTS_DORMANT_ADDENDUM = "Its armed session keeps its count until you CONTINUE."

#: ``prepare_save``'s ``migrated`` keys, and what the answer says for each.
_SAVE_NOTES = {"counts": COUNTS_SWITCHED_NOTE}

#: THE NATIVE GUIDE ENGINE'S OWN SETTLE RULE (#506), ``(pixels, seconds)``:
#: after a dither the guide star must stay within 1.5 guide-camera pixels for
#: 10 s. The run hands the guider no settle of its own (the engine dithers
#: with a distance alone), so this is what every dither of a native-guided
#: night waits on, and the Tonight brief says so (``_guider_and_settle``).
#: These are the Rust engine's ``DEFAULT_SETTLE_TOL_PX`` and
#: ``DEFAULT_SETTLE_TIME_S`` (native/crates/astro-guide/src/engine.rs). The
#: wheel does not export them, so they are written again here, and
#: tests/test_h4_brief_guides_from_rig.py holds this pair to that source.
NATIVE_GUIDE_SETTLE = (1.5, 10.0)


def _save_answer(record: FlowRecord, migrated: list[str],
                 reanchored: list[dict]) -> dict:
    """What every save route answers: the record as stored, with what THIS
    save did to it (``FlowStore.save_and_report``, rulings 2 and 3).

    * ``migrated``: one note per rule the save applied, ``counts`` when it
      switched a TARGET or POOL to accepted subs. Empty when it changed
      nothing, so the answer never repeats what an earlier save did.
    * ``reanchored``: every block whose counts the save restarted,
      ``{node_id, max_move_deg, threshold_deg}``, so a raw field edit that
      restarts a campaign is said when it is made, not nights later as
      CONTINUE's dropped-steps question.

    BUILT BY HAND, SO BY ALIAS. The routes used to return the record and let
    FastAPI dump it by alias; ``FlowEdge``'s source is ``from_`` with alias
    ``from``, and a dump without ``by_alias`` would answer every wire as
    ``from_``, which the editor stores and would send back wireless."""
    body = record.model_dump(mode="json", by_alias=True)
    body["migrated"] = [MigrationNote(key=k, note=_SAVE_NOTES.get(k, k))
                        .model_dump() for k in migrated]
    body["reanchored"] = [dict(r) for r in reanchored]
    return body


class FlowFolderRenameBody(BaseModel):
    """Re-parent every flow in ``name`` to ``new_name``. Not a directory
    rename — a folder is a field on the record, so this is the only thing
    "renaming a folder" can mean."""
    name: str = Field(min_length=1, max_length=200)
    new_name: str = Field(min_length=1, max_length=200)


class FlowCompileBody(BaseModel):
    """An unsaved graph to compile — the editor's live doctor.

    ``graph`` is optional so the empty canvas has an answer too: a new flow
    should get the doctor's "nothing to run yet", not a 422."""
    graph: FlowGraph | None = None
    name: str = ""


class FlowRunBody(BaseModel):
    """``accept_unmapped`` is the operator saying "yes, I know some of this
    graph will not be honoured — run the rest anyway".

    It deliberately does NOT clear the dome refusal. Everything else on the
    unmapped list costs frames; a roof that will not close costs equipment, and
    a checkbox that can wave that through is a checkbox that will be ticked
    once and never read again.

    The last four answer CONTINUE's questions (#189 S1, spec 5.9). Run
    continues the flow's own dormant session by default, and each flag is the
    operator saying yes to one thing that continuing would otherwise refuse
    to do without asking:

    * ``fresh`` - START OVER: a new session, leaving the dormant one on disk.
    * ``adopt`` - re-key a pre-S1 session's frames onto this compile's step
      ids (409 ``adopt`` asks).
    * ``accept_dropped`` - continue although steps holding frames are gone
      from the flow (409 ``dropped_steps`` asks).
    * ``accept_recount`` - continue under a different ``count_mode``, which
      recounts every banked frame (409 ``recount`` asks).

    None of them lifts any guard above: identity, the unbounded quota, the
    horizon and the Sun all apply to a continue exactly as to a fresh run."""
    accept_unmapped: bool = False
    force: bool = False
    fresh: bool = False
    adopt: bool = False
    accept_dropped: bool = False
    accept_recount: bool = False


class ResumeBody(BaseModel):
    """Optional body of ``POST /api/sessions/{id}/resume`` and ``POST
    /api/sequence/recover`` (#291). ``force`` is a start's ``force``: it
    waives the horizon pre-flight, never the Sun. The body is optional, so a
    caller that sends none resumes exactly as it did before the two routes
    ran the pre-flight at all."""
    force: bool = False


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
    cloudmap: CloudmapConfig | None = None
    safety: SafetyConfig | None = None
    escalation: EscalationConfig | None = None
    alerts: list[AlertSink] | None = None
    deadman_url: str | None = None
    #: Explicitly clear the deadman url. Needed because the url is redacted
    #: outbound, so "" from a client means "unchanged" and the field would
    #: otherwise be write-once. Same contract as WeatherSaveBody's
    #: clear_astrospheric_key. Gated on config.alerts like deadman_url itself.
    clear_deadman_url: bool = False
    # Cooler warm-down policy (2026-08-04). It rides this route, and is gated on
    # config.safety rather than a cap of its own, because the setting it governs
    # is a hardware-protection policy the SafetyLimitsPanel already claims in
    # words: "park, then warm the camera at a safe ramp". The knob belongs next
    # to the sentence that promises it.
    cooling: CoolingConfig | None = None
    dusk: DuskConfig | None = None
    # The rig's imaging standards (#239 stage A). Gated on config.safety rather
    # than site_optics: these are the thresholds that decide whether a frame is
    # kept and when the night gives up, which is the same family of
    # hardware-and-run protection SafetyLimitsPanel already owns.
    standards: StandardsConfig | None = None
    # How the focuser is DRIVEN (#D-RIG-2): the approach overshoot and the
    # temperature-compensation model. Gated on config.safety with the
    # standards block above, and for the same kind of reason:
    # ``temp_comp.steps_per_c`` with the sign backwards does not fail to
    # correct the drift, it doubles it.
    focus: FocusConfig | None = None
    # The dew-heater policy (#D-RIG-3). Same cap: it decides how much power
    # reaches a resistor strapped to the optics all night.
    dew: DewConfig | None = None
    # ``planning`` is DELIBERATELY ABSENT. It has its own route
    # (PUT /api/planning, control.capture) because deciding what tonight
    # shoots is not a config.* decision - the shipped operator holds no
    # config capability at all and must still be able to set it.


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


class SyncPushBody(BaseModel):
    """POST /api/config/sync body (file-sync Phase 2). One block, one save.

    MUST be module-level like every other ``*Body`` here — see
    ``IgnoreTonightBody`` for why a class defined inside ``create_app()``
    silently degrades to an unresolvable query param under PEP 563."""
    sync_push: SyncPushConfig = Field(default_factory=SyncPushConfig)


class GalleryPathsBody(BaseModel):
    """Body for the gallery's delete / restore routes: relative frame paths.

    A list rather than a path parameter because deleting a night is one action
    the user took, and N separate DELETE calls would give N chances to half-fail
    with no way to report which half. Module-level like every other ``*Body``
    here (see ``IgnoreTonightBody`` for why a nested class silently 422s).

    ``max_length`` is a denial-of-service bound, not a product limit: the whole
    293-frame reference library is three orders of magnitude below it."""
    paths: list[str] = Field(default_factory=list, max_length=50_000)
    snapshot: str = ""
    q: str = ""
    night_from: str = ""
    night_to: str = ""


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
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024

# Path prefixes that stay open even when a token is configured, so the browser can
# fetch the UI bundle before it knows the token. The API + WS are NEVER in here.
# ``/auth/login`` + ``/auth/google/callback`` are the OIDC login dance (W2.4-C)
# and must be reachable pre-session; ``/auth/logout`` is NOT here (it needs a
# session). The RBAC boot assertion exempts these same auth-login paths.
_AUTH_OPEN_PREFIXES = ("/assets", "/auth/login", "/auth/google/callback")
# ``/sw.js`` joins ``/manifest.json`` here for the SAME reason the shell is open:
# a service worker is fetched by the BROWSER, from the SW registration, before
# any session exists and with no way to attach a token — a gated /sw.js simply
# 401s and the PWA never installs. It is inert static JS with no rig state, and
# it is an EXACT path: ``/api/sw.js`` is still gated, because the openness is
# about that one file at the root, never about a suffix.
# ``/icon-192.png`` + ``/icon-512.png`` join them for the same reason: the
# install prompt and the browser tab read the manifest's icon URLs (and the SW
# precaches them) before any session exists, so a gated icon just 401s and the
# install prompt/tab icon silently fail. Inert static images, no rig state,
# exact paths only — ``/api/icon-192.png`` stays gated.
_AUTH_OPEN_EXACT = {"/", "/index.html", "/favicon.ico", "/manifest.json",
                    "/healthz", "/sw.js", "/icon-192.png", "/icon-512.png"}

# These endpoints define identities/trust roots or perform whole-system
# lifecycle operations. A relay-terminated session cookie is a replayable bearer
# credential, so even a correctly signed admin cookie is insufficient over the
# tunnel. Keep these operations on the directly connected LAN UI.
_REMOTE_LOCAL_ONLY_EXACT = frozenset({
    "/auth/token",
    "/api/auth/config",
    "/api/auth/revoke",
    "/api/auth/unrevoke",
    "/api/config/sync",
    "/api/remote/config",
    "/api/system/factory-reset",
    "/api/update/check",
    "/api/update/apply",
    "/api/update/config",
    "/api/sync/push/now",
})
_REMOTE_LOCAL_ONLY_PREFIXES = ("/api/users", "/api/discover")
# These route families either choose host filesystem/network destinations or
# cause the server to probe/connect to caller-selected local resources. Reads
# remain available where useful, but no tunnelled bearer session may mutate or
# trigger them: a compromised TLS-terminating relay must not become an SSRF,
# serial-device, or arbitrary-destination foothold into the base OS/LAN.
_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES = (
    "/api/config",
    "/api/alerts",
    "/api/drivers",
    "/api/profiles",
    "/api/connect",
    "/api/survey/pack",
    # ``/api/ephemeris`` because ``POST /api/ephemeris/refresh`` makes THIS
    # BOX DIAL OUT to CelesTrak/the MPC. That is an outbound fetch a
    # tunnelled cookie must not be able to trigger: it is the SSRF shape the
    # rest of this list exists for, and the refresh is a maintenance action
    # somebody standing at the rig performs, never a remote one. The GET
    # status and the passes read stay open (this list catches unsafe methods
    # only), so a remote client can still see how old the elements are.
    "/api/ephemeris",
    # ``/api/locations`` is here because it is a SECOND DOOR into ``/api/config``
    # and nothing else. ``PUT /api/locations/{id}`` writes ``config.safety.horizon``
    # through the active-site write-through, and ``POST /api/locations/{id}/apply``
    # writes both ``config.site`` and that same safety floor. Fencing POST /api/config
    # while leaving those open meant a tunnelled cookie could set an 89-degree
    # horizon (every slew of the night denied) or erase the tree line, which is
    # the safety floor the engine's obstruction rule interpolates. Reads stay
    # open: this list only catches unsafe methods, so GET /api/locations (the
    # library the remote UI lists) still answers over the relay.
    "/api/locations",
    # ``/api/switch/ports`` for the same reason as ``/api/locations``: it
    # decides which ports the ENGINE refuses during a run, which is
    # protection policy rather than power control. ``startswith`` catches
    # ``PUT /api/switch/ports/{id}`` and does NOT catch
    # ``POST /api/switch/set`` - operating a power box from the relay IS the
    # product. A prefix of ``/api/switch`` would read as the same intent and
    # would silently kill remote power control.
    "/api/switch/ports",
    # NOT ON THIS LIST, and each is a decision rather than an omission:
    #
    # * ``/api/capture*`` (including ``/api/capture/last/save`` and
    #   ``/api/capture/video``) - taking and keeping frames is the science.
    #   The fence exists for policy that decides what the rig does while
    #   nobody is beside it; a frame already in this box's memory going to
    #   this box's own disk is not that. Fencing it would mean "keep that
    #   one" answers yes at the scope and no from the sofa.
    # * ``/api/mount`` (including the new ``/api/mount/nudge``) - pointing
    #   the telescope remotely is the whole point of a relay, and the nudge
    #   is bounded (1..600 arcmin) and passes the same horizon and
    #   sun-exclusion gates as every other slew.
    # * ``/api/planning`` - it writes exposure times and catalogue ids, and
    #   deliberately does not live under ``/api/config``. Deciding what
    #   tonight shoots is exactly what a remote operator is doing.
)
_UPDATE_MUTATION_PATHS = frozenset({
    "/api/update/check", "/api/update/apply", "/api/update/config",
})

ALLOWED_HOSTS_ENV = "ASTRODECK_ALLOWED_HOSTS"
_DNS_HOST_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def _normalise_allowed_host(raw: str) -> str:
    """Canonical host-only allowlist entry, rejecting wildcard/URL syntax."""
    value = (raw or "").strip()
    if not value or any(ch.isspace() for ch in value) or any(
            ch in value for ch in "/\\@,?\0"):
        raise ValueError(f"invalid host in {ALLOWED_HOSTS_ENV}")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value).compressed.casefold()
    except ValueError:
        pass
    value = value.rstrip(".").casefold()
    if len(value) > 253 or not _DNS_HOST_RE.fullmatch(value):
        raise ValueError(f"invalid host in {ALLOWED_HOSTS_ENV}: {raw!r}")
    return value


def _request_host(raw: str) -> str:
    """Return the canonical hostname from an HTTP Host authority, or ``''``."""
    value = (raw or "").strip()
    if (not value or any(ch.isspace() for ch in value)
            or any(ch in value for ch in "/\\@,?\0")):
        return ""
    # urllib requires brackets around an IPv6 authority and otherwise gives us
    # strict, well-tested hostname/port splitting.
    if value.count(":") > 1 and not value.startswith("["):
        return ""
    try:
        parsed = urlsplit(f"http://{value}")
        host = parsed.hostname
        _ = parsed.port  # force rejection of malformed/out-of-range ports
        if not host:
            return ""
        return _normalise_allowed_host(host)
    except (TypeError, ValueError):
        return ""


def _trusted_hosts(bind_host: str | None, configured: str | None) -> frozenset[str] | None:
    """Build the exact Host allowlist for a real listener.

    ``None`` means create_app was used as an in-process/application factory and
    no bind context was supplied.  The supported CLI always supplies it.
    """
    if bind_host is None:
        return None
    allowed = {"localhost", "127.0.0.1", "::1"}
    bind = _normalise_allowed_host(bind_host)
    if bind not in {"0.0.0.0", "::"}:
        allowed.add(bind)
    for entry in (configured or "").split(","):
        if entry.strip():
            allowed.add(_normalise_allowed_host(entry))
    return frozenset(allowed)


def _host_allowed(headers, allowed: frozenset[str] | None) -> bool:
    return allowed is None or _request_host(headers.get("host") or "") in allowed


def _canonical_origin(raw: str) -> str:
    """Canonical HTTP(S) origin, or ``""`` for malformed/non-origin input."""
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return ""
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        if port is not None and not (
                (scheme == "http" and port == 80)
                or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        return f"{scheme}://{host}"
    except (TypeError, ValueError):
        return ""


def _browser_origin_allowed(scope, headers) -> bool:
    """Reject cross-origin browser requests while preserving CLI/API clients.

    Browsers send ``Origin`` on unsafe forms/fetches and WebSocket handshakes.
    Non-browser clients commonly omit it, so absence remains valid; when
    present it must exactly match the effective scheme + Host. Fetch Metadata
    also closes same-site/different-origin forms (cookies ignore ports).
    """
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False
    supplied_raw = (headers.get("origin") or "").strip()
    if not supplied_raw:
        return True
    supplied = _canonical_origin(supplied_raw)
    scheme = str(scope.get("scheme") or "http").lower()
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    host = (headers.get("host") or "").strip()
    expected = _canonical_origin(f"{scheme}://{host}")
    return bool(supplied and expected) and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8"))


def auth_token() -> str:
    """The configured shared token, or '' when auth is disabled (the default).

    Read live from the environment so a token set before launch is honored and
    tests can monkeypatch ``os.environ`` per-app. Whitespace is stripped so a
    stray newline in a launcher script can't create a token nobody can type."""
    token = (os.environ.get(AUTH_ENV_VAR) or "").strip()
    if token and len(token.encode("utf-8")) < config_module.MIN_BEARER_TOKEN_BYTES:
        raise RuntimeError(
            f"{AUTH_ENV_VAR} must contain at least "
            f"{config_module.MIN_BEARER_TOKEN_BYTES} bytes of independently "
            "generated secret material"
        )
    return token


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
        # Running on the auto-generated persisted secret. Functional and safe.
        #
        # SAY WHICH OF THE TWO THINGS HAPPENED. This used to read "a random
        # session secret was generated and persisted" unconditionally — past
        # tense, on every boot and on every POST /api/auth/config, for a secret
        # that ``ensure_real_secret`` had declined to rewrite because one was
        # already on disk. It is the shape of log line that manufactures
        # incidents: four of them in one night log on 2026-08-09, two mid-run,
        # read as "the signing key keeps changing, that's why we get logged
        # out". The key had not changed since June.
        if _session.secret_was_minted():
            bus.log("info",
                    "auth: no ASTRODECK_SECRET was set, so a random session "
                    "secret has been generated and persisted just now (set "
                    "ASTRODECK_SECRET to manage it yourself).", "auth")
        else:
            bus.log("info",
                    "auth: sessions are signed with the stored auto-generated "
                    "secret (set ASTRODECK_SECRET to manage it yourself). "
                    "Nothing was regenerated.", "auth")


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


def create_app(*, bind_host: str | None = None,
               allowed_hosts: str | None = None) -> FastAPI:
    host_allowlist = _trusted_hosts(
        bind_host,
        os.environ.get(ALLOWED_HOSTS_ENV) if allowed_hosts is None else allowed_hosts,
    )
    app = FastAPI(title="AstroDeck", version=__version__, lifespan=_lifespan)

    # Optional shared-token gate (P0-4). A pure pass-through when ASTRODECK_TOKEN
    # is unset, so default LAN behavior is byte-for-byte unchanged.
    @app.middleware("http")
    async def _auth_mw(request, call_next):
        remote = _scope_is_remote(request)
        # The Host allowlist guards the LISTENER against DNS rebinding and Host
        # injection. A relay-tunneled request never touched the listener: the
        # home dialed OUT to its configured relay over TLS and the relay client
        # stamps the scope (ASGI state, not a header). The Host it carries is
        # the relay's public name, which is never a listener name and may not
        # even be the dial address (a custom domain in front of the relay).
        # 0.3.23 checked Host first and answered 421 to every tunneled request.
        if not remote and not _host_allowed(request.headers, host_allowlist):
            return JSONResponse(
                {"detail": "unrecognized Host authority",
                 "code": "invalid_host"},
                status_code=421)
        unsafe_method = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        if unsafe_method and not _browser_origin_allowed(
                request.scope, request.headers):
            return JSONResponse(
                {"detail": "cross-origin browser request denied",
                 "code": "invalid_origin"},
                status_code=403)
        # ASTRODECK_TOKEN is a direct-transport credential and is deliberately
        # stripped by the relay client. Remote scopes authenticate through the
        # home-signed session/provider path instead; the open ``none`` provider
        # independently hard-denies them in ``resolve_principal``.
        if (auth_enabled() and not remote
                and not _path_is_open(request.url.path)):
            supplied = _present_token(
                header=request.headers.get("x-auth-token"),
                authorization=request.headers.get("authorization"),
                query=request.query_params.get("token"))
            if not _token_ok(supplied):
                return JSONResponse(
                    {"detail": "missing or invalid auth token"}, status_code=401)
        path = request.url.path
        if (remote and (
                path in _REMOTE_LOCAL_ONLY_EXACT
                or any(path == prefix or path.startswith(prefix + "/")
                       for prefix in _REMOTE_LOCAL_ONLY_PREFIXES)
                or (unsafe_method and any(
                    path == prefix or path.startswith(prefix + "/")
                    for prefix in _REMOTE_LOCAL_ONLY_MUTATION_PREFIXES)))):
            return JSONResponse(
                {"detail": "this security-sensitive operation is LAN-only",
                 "code": "local_only"},
                status_code=403)
        if path in _UPDATE_MUTATION_PATHS:
            provider_name = getattr(get_active_provider(), "name", "none")
            if provider_name == "none" and not auth_enabled():
                return JSONResponse(
                    {"detail": "configure authentication before managing updates",
                     "code": "authentication_required"},
                    status_code=403)
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"detail": "invalid Content-Length"}, status_code=400)
            if declared_length < 0:
                return JSONResponse(
                    {"detail": "invalid Content-Length"}, status_code=400)
            if declared_length > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    {"detail": "request body too large"}, status_code=413,
                    headers={"Connection": "close"})

        # Content-Length is advisory: a chunked sender can omit it or lie. Wrap
        # the ASGI receive channel so every parsed body is bounded before
        # pydantic/json can allocate without limit.
        original_receive = request._receive
        received = 0
        body_overflow = False

        async def limited_receive():
            nonlocal received, body_overflow
            if body_overflow:
                return {"type": "http.disconnect"}
            message = await original_receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_REQUEST_BODY_BYTES:
                    body_overflow = True
                    # Give the parser a terminal empty chunk; after it unwinds,
                    # replace its response with the authoritative 413 below.
                    return {"type": "http.request", "body": b"",
                            "more_body": False}
            return message

        request._receive = limited_receive
        response = await call_next(request)
        if body_overflow:
            return JSONResponse(
                {"detail": "request body too large"}, status_code=413,
                headers={"Connection": "close"})
        return response

    @app.middleware("http")
    async def _security_headers_mw(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # `()` is not "off for third parties", it is OFF FOR EVERYONE INCLUDING
        # US: an empty allowlist denies the feature to this origin's own
        # documents. That silently broke three shipped features against this
        # server - the finder's AR camera overlay, the photosphere capture, and
        # the Sites sheet's "fill from the phone" - each of which asks a
        # permission the browser had already been told to refuse.
        #
        # `(self)` re-admits exactly this origin and nobody else, and the CSP
        # below already refuses embedding outright (`frame-ancestors 'none'`),
        # so there is no frame to inherit either one. Microphone, payment and
        # usb stay fully denied: nothing here asks for them.
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; "
            # OPEN-012: no 'unsafe-inline' for scripts. The one pre-paint inline
            # script was moved to public/bootstrap.js (served 'self'), so an
            # injected inline <script> is now refused by the browser.
            "script-src 'self'; "
            # style-src keeps 'unsafe-inline': React/Vite set element style
            # attributes and inject <style> at runtime, which nonces/hashes can't
            # cover without a styling-layer rewrite. Tracked as a documented
            # exception; script injection is the higher-value class and is closed.
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; connect-src 'self' ws: wss:")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(request, exc: RequestValidationError):
        """A 422 that can actually be SERIALISED, with a machine code on it.

        FastAPI's own handler answers ``{"detail": jsonable_encoder(
        exc.errors())}``, and each error carries the offending ``input`` -- so
        a body containing a value ``json.dumps`` cannot write took the RESPONSE
        down instead of the request. ``NaN`` is exactly that value: it survives
        ``json.loads`` on the way in, pydantic refuses it (every ``Field(...,
        allow_inf_nan=False)`` on this server exists to make it), and then the
        refusal itself raised ``ValueError: Out of range float values are not
        JSON compliant`` out of the encoder. The caller saw a 500 with no body,
        which reads as "the server is broken" rather than "that is not a
        number" -- and a client retrying a 500 is a client retrying a NaN.

        So the errors are walked and every non-finite float is replaced by its
        name. ``detail`` keeps the shape and position FastAPI gives it (the
        list of per-field errors) so nothing that already reads it changes;
        ``code`` is added because every 4xx on this server carries one."""
        def _finite(value):
            if isinstance(value, float) and not math.isfinite(value):
                return repr(value)          # "nan" / "inf" / "-inf"
            if isinstance(value, dict):
                return {k: _finite(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_finite(v) for v in value]
            return value

        return JSONResponse(
            {"detail": _finite(jsonable_encoder(exc.errors())),
             "code": "invalid_request"},
            status_code=422)

    @app.exception_handler(SessionUnreadable)
    async def _session_unreadable(request, exc: SessionUnreadable):
        """A damaged session file is an answer, not a crash.

        Six routes load a session and every one of them caught only
        ``KeyError``, so a file that parsed as JSON and failed
        ``Session.model_validate`` 500'd with a traceback and no sentence. It
        is registered once, here, rather than repeated six times: the next
        route to load a session gets the behaviour for free instead of
        inheriting the omission. Still a 500 — the server IS broken in a way
        the caller cannot fix — but a named one, and never a 404, which would
        say a session the user can see in the list does not exist."""
        return JSONResponse(
            {"detail": str(exc), "code": "session_unreadable"},
            status_code=500)

    # --------------------------------------------------------- atlas routers
    # The Sky-Atlas feature lanes own these as separate APIRouter modules
    # (survey cutout proxy / mosaic compute / visibility ephemeris). Registered
    # here so no two owners edit the same function; /api/optics already exists
    # below (not duplicated here).
    app.include_router(survey_router)
    app.include_router(tiles_router)
    app.include_router(framing_router)
    app.include_router(visibility_router)
    # Before create_app returns, NOT after: the trailing SPA catch-all
    # GET /{path:path} shadows anything registered later (measured: 404).
    app.include_router(region_router)
    # The three wave-S7 routers, registered HERE for that same measured
    # reason: a router included after the catch-all answers 404 on every one
    # of its paths, and nothing else in the app would say so.
    app.include_router(ephemeris_router)
    app.include_router(video_router)
    app.include_router(planning_router)

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
        stored_update = config_store.cfg().update
        if ((body.repo or "").strip() != (stored_update.repo or "").strip()
                or (body.signing_pubkey or "").strip()
                != (stored_update.signing_pubkey or "").strip()):
            # ``repo`` + ``signing_pubkey`` together are a code-execution trust
            # root. Letting a web session replace both turns admin-panel access
            # into arbitrary OS code execution during the next update. Provision
            # or rotate them through the local config file/installer instead.
            raise HTTPException(
                403,
                detail={
                    "detail": "update repository and signing key are offline-only",
                    "code": "update_trust_root_offline_only",
                })
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
        if body.reset_auth:
            # Check the post-reset posture before disconnecting equipment or
            # touching any state.  Managed deployments may reset accounts only
            # when a separate strong ASTRODECK_TOKEN will keep the listener
            # authenticated after the auth block is cleared.
            try:
                config_module.enforce_required_auth(AuthConfig())
            except ValueError as exc:
                raise HTTPException(409, detail={
                    "detail": str(exc),
                    "code": "authentication_required",
                }) from exc
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
            reset_auth=body.reset_auth,
            reset_remote=body.reset_remote)
        # The relay client reads remote config at startup (like every other
        # remote change, it applies on the next restart); a transferred box is
        # powered down and handed off, so no live tunnel outlives the handover.
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

    # ------------------------------------------- file-sync push config (Phase 2)
    # CAP_CONFIG_SITE_OPTICS matches the settings-panel neighbourhood (survey /
    # naming / wcs), and in the shipped role map that means ADMIN ONLY — no
    # operator and no viewer can point this anywhere. That is the property that
    # matters: the block names a filesystem path every science frame gets copied
    # to, so it belongs with the config caps rather than the control ones.

    @app.post("/api/config/sync")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def set_sync_push_config(
            body: SyncPushBody,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        """Point file-sync at a destination (or turn it off).

        Takes effect within one runner tick — there is no restart and no
        re-arm, because the runner re-reads this block every time rather than
        capturing it at start."""
        try:
            cfg = await asyncio.to_thread(config_store.set_sync_push, body.sync_push)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(cfg))
        return _config_payload(principal)

    @app.get("/api/sync/push", dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
    async def sync_push_status():
        """What the push runner has been doing.

        Gated at ``view.media`` for the same reason ``/api/sync/manifest`` is:
        this reports how much science data has left the rig and where it went,
        which is a fact about the media, not about the rig's health."""
        return sync_push_runner.status()

    @app.post("/api/sync/push/now", dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
    async def sync_push_now():
        """Run one pass right now instead of waiting for the cadence.

        This is what makes the settings panel honest: an operator who has just
        typed a UNC path finds out whether the rig can write to it while they
        are still looking at the screen, rather than at 02:00 with nobody
        watching. Gated at ``view.media`` — the same "plan and perform behind
        ONE capability" rule the manifest route explains — because this MOVES
        science frames off the rig."""
        return await sync_push_runner.push_now()

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

    # ------------------------------------ cloud map (cloud-occlusion 6a §6)
    #
    # Three read-only routes over the GOES cloud-occlusion model, the look route
    # answering both a GET and a POST (#520, below). All are gated on
    # view.weather, the same cap and the same trade-off as the radar map above
    # (2026-07-17 decisions wave I2), and test_h4_cloudmap_at_telescope holds
    # it for both forms of the look route: the dome is centred on the site
    # and a pierce point sits within 30 km of it, so reaching these routes
    # discloses the rig's region to an operator, and a VIEWER never reaches
    # them at all.
    #
    # ALL THREE ANSWER 200 FOR EVERY STATE OF THE SKY, including "switched off"
    # and "nothing fetched yet". 6b has to draw off, stale and no-data as three
    # different things, and a 404 collapses them into one status code that also
    # means "the route moved" and "the proxy is misconfigured". A 400 is
    # reserved for the caller asking for something that does not exist -- a
    # zero-degree grid step -- which is not a state of the sky.
    #
    # NONE OF THIS GATES ANYTHING. No sequence decision, no safety gate and no
    # auto-resume consults these routes or the service behind them.

    def _cloudmap_400(exc: ValueError):
        # Stage 4 owns the domains -- 0 < step <= 45, 0 < alt <= 90, finite
        # azimuth -- and says why in a full sentence. Re-checking them here
        # would be a second copy of a boundary rule, which is how two copies
        # come to disagree; the route only decides what a domain error IS,
        # which is a 400.
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/cloudmap")
    @declare(CAP_VIEW_WEATHER)
    async def get_cloudmap(
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))):
        return cloudmap_service.payload()

    @app.get("/api/cloudmap/dome")
    @declare(CAP_VIEW_WEATHER)
    async def get_cloudmap_dome(
            alt_step: float = 2.0, az_step: float = 4.0,
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))):
        try:
            return await cloudmap_service.dome_payload(
                alt_step_deg=alt_step, az_step_deg=az_step)
        except ValueError as exc:
            raise _cloudmap_400(exc) from exc

    # THE LOOK ROUTE IS TWO ROUTES (#520). The panels used to send the mount's
    # live alt/az as `GET /api/cloudmap/at?alt=&az=`, so every proxy and the
    # Fly relay's access log recorded the pointing several times a minute - a
    # function of the site, and at park the latitude itself (#140). Now:
    #
    #   * GET takes nothing but the lead time and reads the mount HERE. It is
    #     the one the panels poll, and its URL carries nothing site-derived.
    #   * POST carries a PICKED direction in its body, where no access log
    #     writes it. The same gate, the same domain 400s.
    #
    # A GET still carrying alt or az is refused rather than ignored, so an old
    # tab fails loudly instead of quietly going on writing the pointing into
    # every log between it and here.

    @app.get("/api/cloudmap/at")
    @declare(CAP_VIEW_WEATHER)
    async def get_cloudmap_at(
            request: Request, ahead_s: float = 0.0,
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))):
        _refuse_site_query(request, "/api/cloudmap/at reads the mount itself; "
                                    "POST {alt, az, ahead_s} asks about a "
                                    "picked point")
        try:
            return await cloudmap_service.telescope_payload(
                hub.devices.get("telescope"), ahead_s=ahead_s)
        except ValueError as exc:
            raise _cloudmap_400(exc) from exc

    @app.post("/api/cloudmap/at")
    @declare(CAP_VIEW_WEATHER)
    async def post_cloudmap_at(
            body: CloudmapAtBody,
            principal: Principal = Depends(require(CAP_VIEW_WEATHER))):
        try:
            return cloudmap_service.at_payload(
                alt_deg=body.alt, az_deg=body.az, ahead_s=body.ahead_s)
        except ValueError as exc:
            raise _cloudmap_400(exc) from exc

    # -------------------------------------------------- restricted assets (#216)
    #
    # Three things we are not licensed to redistribute, one mechanism. Reading
    # is CAP_VIEW_STATUS because it is a disclosure, not a secret — the whole
    # value of the screen is that anyone using the rig can see what we ship and
    # on whose authority. Writing is CAP_CONFIG_BACKEND, the same gate as any
    # other instance-wide setting: the acknowledgment is a statement ABOUT THE
    # DEPLOYMENT, so it must come from someone who administers the deployment.

    @app.get("/api/licensing/restricted",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_restricted_assets():
        from ..licensing import status as _restricted_status
        return {"assets": _restricted_status()}

    @app.post("/api/licensing/restricted/{asset_id}/acknowledge",
              dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def acknowledge_restricted_asset(
            asset_id: str, body: AcknowledgeBody | None = None,
            principal: Principal = Depends(require(CAP_CONFIG_BACKEND))):
        """Record — or withdraw — this INSTANCE's acknowledgment.

        Instance-scoped on purpose: one statement covers every user of this rig,
        and viewers and operators are never asked. What is being asserted is a
        fact about the deployment, not a promise by whoever is signed in.

        Withdrawable, because a consent that cannot be taken back is not a
        consent — the integration goes inert again immediately."""
        from .. import licensing as _lic
        if body is not None and body.withdraw:
            return {"withdrawn": _lic.withdraw(asset_id),
                    "assets": _lic.status()}
        try:
            _lic.acknowledge(asset_id,
                             by=getattr(principal, "name", None)
                             or getattr(principal, "subject", "") or "unknown",
                             note=(body.note if body else ""))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"assets": _lic.status()}

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

    # DISCOVERY IS AN ACTION, NOT A READ -- config.backend, not view.status.
    #
    # These five routes used to sit on view.status, alongside GET /api/drivers,
    # on the reasoning that both answer "what equipment is there". That reading
    # conflates two different things. GET /api/drivers describes what is already
    # CONFIGURED: a passive read of our own state. Every route below makes the
    # SERVER GO LOOK -- a UDP broadcast, a sweep of the local /24
    # (devices/nina.py::_local_subnets, no host argument required), an outbound
    # HTTP probe of a host the CALLER names, an enumeration of the COM drivers
    # installed on this machine. A viewer link handed to someone on the internet
    # could map the observatory's LAN through it.
    #
    # Nothing real loses access. Both callers are buttons on Settings > Backend
    # Drivers and Equipment, and both panels already tell a non-holder
    # "Read-only -- changing drivers needs config.backend": the scan results are
    # only actionable by someone who can write a driver or a profile.
    #
    # The SSRF guard on /api/discover/alpaca stays exactly as it is. It is
    # defence in depth, not a substitute: an authenticated operator must still
    # not be able to make this server dial 169.254.169.254.

    @app.get("/api/discover", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def discover():
        return await alpaca_backend.discover()

    @app.get("/api/discover/nina",
             dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def discover_nina_instances(host: str = "", port: int = 1888):
        extra = [host] if host else None
        return await discover_nina(port=port, extra_hosts=extra)

    @app.get("/api/discover/alpaca",
             dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def discover_alpaca_one(host: str, port: int = 11111):
        """Server-side proxy for a manual Alpaca host/port scan (the browser
        can't do this directly — CORS). 502 with a differentiated cause so a
        beginner who typo'd the IP gets a useful message."""
        # SSRF guard: query_server is the single chokepoint — it validates the
        # host AND dials the address its own check approved. This route used to
        # pre-validate here as well and throw the result away, which cost a
        # second DNS lookup and, worse, made it look as though the guard lived
        # at the route rather than at the connection.
        try:
            return await alpaca_backend.query_server(host, port)
        except alpaca_backend.AlpacaScanError as e:
            # do NOT echo any upstream HTTP status here — that turned the 502
            # into a port/host scan oracle. The differentiated message already
            # lives in AlpacaScanError; surface only that.
            raise HTTPException(502, str(e))

    @app.get("/api/discover/ascom-local",
             dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
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
    # (a passive read of drivers we already have -- unlike /api/discover/*, which
    # makes the server go LOOK and is therefore config.backend); write =
    # config.backend (same as every connect write).
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

    @app.get("/api/discover/{backend}",
             dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
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
        # THE WHOLE BODY IS VALIDATED FIRST (#257, spec 6.15), before the busy
        # refusal and before anything a forced connect ends: the ladder stop,
        # the disarm, the wait and ``engine.abort``. These checks used to sit
        # after all four, so a forced request with a typo in one role override
        # ended the night and switched the armed session's auto-resume off,
        # then answered 422 and connected nothing: a 422 that read as "nothing
        # happened" over a rig left idle and untracked with nothing to restart
        # it. A 422 from this route now means nothing was touched. Ahead of
        # the unforced 409 as well, so the operator is never offered "force"
        # for a body that would fail whatever the rig was doing.
        #
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
        # A WHOLE-RIG CONNECT IS DESTRUCTIVE: it disconnects the current rig
        # before it builds the new one. The two routes that are strictly LESS
        # destructive - /api/profiles/{id}/apply and /api/profiles/{id}/activate
        # - have refused mid-night since they were written; this one, with the
        # widest blast radius on the box, had no guard at all (issue #20). A
        # night was lost to it on 2026-09-12 and recovered only by a profile
        # activate.
        #
        # Same 409 contract as those two, deliberately: one client-side error
        # path covers all three, and a caller that already handles "running"
        # from a profile apply needs no new code for this.
        #
        # The issue blamed a missing `primary`, and that is ruled out: the field
        # is required, has been since it was introduced, and FastAPI rejects a
        # body without it with a 422 before this function runs. What was missing
        # is the guard, not the validation.
        #
        # AUTO-RESUME'S RECOVERY LADDER COUNTS AS RUNNING (#238, spec 6.15):
        # after a restart it solves and re-centres with the engine idle, and
        # this route tore the rig down under it. Unforced, the guard refuses
        # it as it refuses a run (``_teardown_busy_detail``). Forced, the
        # ladder is stopped and its session disarmed, as Abort does, and the
        # route waits until it has returned before anything is torn down.
        busy = _teardown_busy_detail()
        if busy is not None and not body.force:
            raise HTTPException(409, detail={"detail": busy, "code": "running"})
        if body.force:
            resume_arm.stop_recovery(
                "the operator force-connected a rig while it was re-centring "
                "the mount", disarm=True)
            await _wait_for_the_ladder()
        if body.force and engine.running:
            await engine.abort()
        try:
            return await hub.connect_rigspec(spec)
        except DeviceError as e:
            raise _err(e)
        except Exception as e:
            raise HTTPException(502, f"rig connection failed: {e}")

    @app.post("/api/disconnect", dependencies=[Depends(require(CAP_CONFIG_BACKEND))])
    @declare(CAP_CONFIG_BACKEND)
    async def disconnect():
        # DISCONNECT STOPS THE RECOVERY LADDER, THEN WAITS FOR IT (#238, spec
        # 6.15). After a restart ResumeArm's ladder solves and re-centres the
        # mount with no run behind it, so ``engine.abort`` had nothing to
        # stop and ``disconnect_all`` pulled the camera and the mount out
        # from under a ladder still awaiting them. It is stopped as Abort
        # stops it: here, before the first await, and the session it was
        # recovering is disarmed, so the ladder does not run it again the
        # moment the rig is connected again. Then the route waits until the
        # ladder has actually returned (``_wait_for_the_ladder``), and
        # refuses with 409, tearing nothing down, if it has not within the
        # bound. Both are no-ops when no ladder is running. There is no
        # ``force`` here and no unforced refusal: a disconnect has always
        # ended whatever it found.
        resume_arm.stop_recovery(
            "the operator disconnected the rig while it was re-centring the "
            "mount", disarm=True)
        await _wait_for_the_ladder()
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
        ``unknown`` when the site is still the default (no trustworthy answer).

        THE FLOOR IS THE EFFECTIVE ONE (#132 a): ``schedule.effective_floor``
        raises ``cfg.safety.min_alt_deg`` by the drawn obstruction-horizon
        mask and any no-go wedge at the target's azimuth -- the same formula
        ``hub._check_horizon`` now enforces and the engine's
        ``_altitude_limit_verdict`` / ``_mount_floor_verdict`` already
        enforced mid-run. Before this, "low" vs. "ok" turned on the flat
        ``site.horizon_min_deg`` alone, so the UI's Horizon light could read
        "ok" for a target a start or a slew would actually refuse. ``below``
        is still the true, geometric horizon (``alt < 0``): no configured
        floor can make a target that is not there read as merely "low"."""
        from ..catalog import altaz, round_az_deg
        from ..sequence.schedule import effective_floor
        site = hub.site
        is_default = bool(site.get("is_default", True))
        safety = config_store.cfg().safety
        alt, az = altaz(ra_hours, dec_deg, site["latitude"], site["longitude"])
        floor = effective_floor(safety.min_alt_deg, safety.horizon, az,
                                safety.nogo_box)
        if is_default:
            verdict = "unknown"
        elif alt < 0:
            verdict = "below"
        elif alt < floor:
            verdict = "low"
        else:
            verdict = "ok"
        return {"alt": round(alt, 1), "az": round_az_deg(az), "verdict": verdict,
                "horizon_min_deg": round(floor, 1), "site_is_default": is_default}

    def _horizon_block(ra_hours: float, dec_deg: float) -> dict | None:
        """Return a 409 detail dict if a GOTO should be blocked (configured site
        AND the target is below the true horizon OR the configured obstruction
        floor -- #132 a, via ``hub._check_horizon``), else None. Default site
        never blocks — we don't trust an un-set location to refuse a slew."""
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

    def _ceiling_block(t: Target) -> dict | None:
        """Return a 409 detail dict if ``t`` is in the mount's zenith keep-out
        (#619 a, first leg). Asks the SAME verdict function the engine's slew
        gate asks mid-run (``SequenceEngine._altitude_limit_verdict``) rather
        than a second copy of the ceiling maths, so pre-flight and the first
        slew cannot disagree about where the ceiling is -- the gap #619
        reported: a target that cleared the floor but sat above
        ``cfg.safety.max_alt_deg`` passed this gate and was only caught by
        the engine's own gate on the first slew.

        Skipped on the default site exactly like ``_horizon_block`` (no
        un-configured location is trusted to call a slew unsafe) and touches
        no device, like the verdict function itself.

        NOT BYPASSED BY ``force``. Unlike the horizon/floor leg above, this
        is the same kind of hazard the Sun check below is: a mount that
        reaches its own tripod is a hardware cost, not the lost-a-night
        tradeoff ``force`` exists to let an operator accept."""
        if hub.site.get("is_default", True):
            return None
        verdict = engine._altitude_limit_verdict(t, projected=True,
                                                  cfg=config_store.cfg())
        if verdict is not None and verdict.kind == "ceiling":
            return {"detail": verdict.sentence, "code": "ceiling",
                    "site_detail": verdict.site_detail}
        return None

    async def _pier_block(t: Target, plan: SequencePlan) -> dict | None:
        """Return a 409 detail dict if a slew to ``t`` would need a pier flip
        while ``plan.meridian_flip`` is off (#619 a, second leg) -- the SAME
        pier-collision guard, over the SAME bounded mount read, the engine's
        slew gate asks mid-run (``SequenceEngine._mount_floor_verdict``), so
        a plan the first slew would refuse cannot pass pre-flight first.

        A disconnected mount, or one that does not report a destination pier
        side, answers None -- nothing to compare the destination side
        against, exactly as the engine's own guard reads an unreadable side.

        NOT BYPASSED BY ``force``, for the same reason as the ceiling above:
        a pier-side change with flips disabled is a collision risk, not a
        lost-frames tradeoff."""
        verdict = await engine._mount_floor_verdict(t, projected=True,
                                                     cfg=config_store.cfg(),
                                                     plan=plan)
        if verdict is not None and verdict.kind == "pier":
            return {"detail": verdict.sentence, "code": "pier_flip",
                    "site_detail": verdict.site_detail}
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

    async def _start_preflight(plan: SequencePlan, *, force: bool) -> list[dict]:
        """The horizon, ceiling, pier and Sun pre-flight of the HTTP start
        paths, ``/api/sequence/start`` and ``/api/flows/{id}/run``, and of the
        two that resume a stored session, ``/api/sessions/{id}/resume`` and
        ``/api/sequence/recover``, which hand it only the targets the session
        still owes (``_owed_plan``; #291): one helper, so the four cannot
        drift (spec 6.3; #132, #619). Raises 409 for a refusal. Returns
        the panels of a mosaic group that are below the horizon now and did
        not refuse the start, for the caller to name in its response and its
        log line (``_name_panels_below``) once the run has started.

        ASYNC SINCE #619: the pier leg needs a bounded live telescope read
        (``_pier_block`` -> ``SequenceEngine._mount_floor_verdict``), the
        same read the engine's own slew gate makes. Every caller already
        awaits inside an ``async def`` route.

        A TARGET OUTSIDE A GROUP gets exactly the engine's own floor
        verdict (#132 a): it refuses through ``_horizon_block`` ->
        ``hub._check_horizon``, which now enforces the same effective floor
        (``cfg.safety.min_alt_deg`` raised by the horizon mask and any no-go
        wedge at the target's azimuth) the engine's ``_mount_floor_verdict``
        enforces mid-run -- so a single-target start can no longer pass this
        gate and then be refused by the engine's slew gate on the first slew.

        A MOSAIC GROUP IS REFUSED ONLY WHEN EVERY PANEL IS BLOCKED. A group's
        panels span the sky between them, so some can be below the horizon
        while the rest are up. The loop this replaces refused the whole start
        for the first panel behind the horizon, so such a mosaic could only
        be started with ``force``, which waives the check for every other
        target in the plan too. Now a member counts toward its group: the
        group refuses (unless forced) only when every one of its panels is
        below the horizon now, and the 409 lists them. Otherwise the start
        goes ahead and the blocked panels are returned. What the run does
        with a panel that is not up is decided in the scheduler's selection
        (spec D9, 5.1), not here.

        A member is what the engine counts as one (``SequenceEngine.
        _group_of``): a non-calibration target whose ``mosaic_group`` names a
        group the plan CARRIES. Every other target, including each panel of
        a classic Plan mosaic (a ``mosaic_group`` with no ``groups`` entry),
        refuses alone, with the same 409 as before, and ``force`` still
        waives it without a look. A forced group's members are still looked
        at, so the panels a forced start leaves below the horizon are named.

        THE CEILING AND THE PIER (#619 a) ARE ALSO UNCHANGED BY GROUPING OR
        ``force``, exactly like the Sun: every sky target is asked, panel or
        not, forced or not. Both are hardware hazards (the mount reaching its
        own tripod; a pier-side change with flips disabled) rather than the
        lost-a-night tradeoff the horizon/floor leg lets ``force`` accept, so
        neither gets the mosaic's "some panels are up" leniency or the
        force bypass -- a mosaic with one panel in the zenith keep-out
        refuses the whole start, the same as one target would.

        THE SUN IS ALSO UNCHANGED: any target in the cone refuses, a panel
        included, forced or not. Everything else here costs you a night;
        the ceiling, the pier and the Sun cost you a sensor or a collision.

        Refusals come in plan order: every horizon refusal (group-aware),
        then every ceiling refusal, then every pier refusal, then every Sun
        refusal, as the loops run. Calibration targets (darks, bias, flats)
        carry mandatory dummy coordinates and never slew, so every check
        skips them: a dark-library build at a configured site must not be
        refused because (0, 0) is below the horizon or in the keep-out."""
        groups = {g.id: g for g in plan.groups}
        sky = [t for t in plan.targets if not t.calibration
               and getattr(t, "ra_hours", None) is not None
               and getattr(t, "dec_deg", None) is not None]

        def group_of(t) -> TargetGroup | None:
            gid = getattr(t, "mosaic_group", None)
            return groups.get(gid) if gid is not None else None

        def mosaic(g: TargetGroup) -> str:
            return g.name or g.id               # as the engine's lines name it

        def entry(t, g: TargetGroup) -> dict:
            return {"group": g.id, "mosaic": mosaic(g), "target": t.name,
                    "panel": SequenceEngine._panel_name(t)}

        below: dict[int, dict] = {}
        for t in sky:
            if force and group_of(t) is None:
                continue            # forced, as before: not looked at
            blocked = _horizon_block(t.ra_hours, t.dec_deg)
            if blocked is not None:
                below[id(t)] = blocked
        if not force:
            for t in sky:
                if id(t) not in below:
                    continue
                g = group_of(t)
                if g is None:
                    raise HTTPException(409, detail={**below[id(t)],
                                                     "target": t.name})
                members = [m for m in sky if group_of(m) is g]
                if all(id(m) in below for m in members):
                    labels = ", ".join(SequenceEngine._panel_name(m)
                                       for m in members)
                    raise HTTPException(409, detail={
                        "detail": f"every panel of mosaic '{mosaic(g)}' is "
                                  f"below the horizon now: {labels}",
                        "code": "below_horizon", "group": g.id,
                        "mosaic": mosaic(g),
                        "panels": [{"panel": e["panel"], "target": e["target"]}
                                   for e in (entry(m, g) for m in members)]})
        # Ceiling and pier (#619 a): per-target hazards, never waived by
        # ``force`` and never given the group's "some panels are up"
        # leniency (see the docstring) -- asked over every sky target, in
        # plan order, ceiling fully before pier starts.
        for t in sky:
            ceiling = _ceiling_block(t)
            if ceiling is not None:
                raise HTTPException(409, detail={**ceiling, "target": t.name})
        for t in sky:
            pier = await _pier_block(t, plan)
            if pier is not None:
                raise HTTPException(409, detail={**pier, "target": t.name})
        for t in sky:
            solar = _solar_block(t.ra_hours, t.dec_deg)
            if solar is not None:
                raise HTTPException(409, detail={**solar, "target": t.name})
        return [entry(t, group_of(t)) for t in sky
                if id(t) in below and group_of(t) is not None]

    def _name_panels_below(plan: SequencePlan, blocked: list[dict]) -> None:
        """One log line per group for the panels ``_start_preflight`` found
        below the horizon on a start that went ahead (spec 6.3). A group with
        every panel below can only have started forced: unforced, it refused.

        WORDS ONLY (6.9; H3 orchestrator ruling 1): the mosaic's name and the
        panels' labels, never the altitude the 409 detail of a refusal
        carries. ``/api/logs`` is view.status, and a named target's altitude
        at a logged time is a circle of latitudes (#140). When it is logged
        is the operator's press, not a site computation, so the line needs
        no ``site_derived`` flag. Called only once the engine is going: a
        refused start says nothing."""
        by_group: dict[str, list[dict]] = {}
        for e in blocked:
            by_group.setdefault(e["group"], []).append(e)
        for gid, rows in by_group.items():
            n = sum(1 for t in plan.targets if not t.calibration
                    and getattr(t, "mosaic_group", None) == gid)
            labels = [e["panel"] for e in rows]
            name = rows[0]["mosaic"]
            if len(labels) == n:
                bus.log("info",
                        f"mosaic '{name}': all {n} panels are below the "
                        f"horizon now ({', '.join(labels)}); started because "
                        f"the start was forced", "sequence")
                continue
            if len(labels) == 1:
                which = f"panel {labels[0]} is"
            else:
                which = (f"panels {', '.join(labels[:-1])} and {labels[-1]} "
                         f"are")
            up = n - len(labels)
            bus.log("info",
                    f"mosaic '{name}': {which} below the horizon now; "
                    f"started, since {up} of its {n} panels "
                    f"{'is' if up == 1 else 'are'} not", "sequence")

    def _owed_plan(s: Session) -> SequencePlan:
        """``s``'s plan cut to the targets that still owe frames, for the
        pre-flight of the two routes that resume a stored session,
        ``/api/sessions/{id}/resume`` and ``/api/sequence/recover`` (#291).

        OWED, NOT EVERY TARGET. A dormant session is usually part done, and
        a target that owes nothing is not one the run will slew to. Checked
        with the rest, a finished target that has since set would refuse
        the resume of everything the session still owes, and a finished
        panel that is up would carry a group whose every owed panel is below
        the horizon past the every-panel rule. So both ``_start_preflight``
        and ``_name_panels_below`` are handed this plan, and a group's rule
        and its logged count are over the panels still owed.

        "Owes" is ``Session.remaining``, counted as the frozen plan's
        ``count_mode`` counts, the same count ``done_map`` seeds the engine
        with. The groups ride along whole, so a member keeps its group. A
        copy: the session's own plan is what ``engine.start`` is handed."""
        left = s.remaining()
        return s.plan.model_copy(update={"targets": [
            t for t in s.plan.targets
            if any(left.get(st.id, 0) > 0 for st in t.steps)]})

    def _merge_alert_verified(incoming: list[AlertSink]) -> list[AlertSink]:
        """Reset ``verified`` to False on any sink whose delivery identity
        (url/token/chat_id/kind) changed vs. the stored copy, so a re-pointed
        channel must be re-tested before its "verified" badge returns (C1-16).
        A brand-new id keeps whatever ``verified`` it arrived with (False by the
        model default).

        AN OMITTED KEY IS NOT A FALSE. ``AlertSink.verified`` is ``bool = False``,
        so pydantic fills the default and a client that never mentioned the field
        is indistinguishable, downstream, from one that asked to clear it — the
        badge went out on a PATCH that only meant to rename the sink. The real
        client echoes the flag (AlertsPanel spreads the stored sink), which is
        why this was never the reported bug and is still wrong: ``verified`` is
        SERVER-OWNED state, earned by a delivery test, and the only two things
        entitled to change it are that test and the identity check below.
        ``model_fields_set`` is what tells the two apart, and it is the same
        "empty means unchanged" rule the token already gets."""
        existing = {s.id: s for s in config_store.cfg().alerts}
        out: list[AlertSink] = []
        for sink in incoming:
            old = existing.get(sink.id)
            if old is not None and "verified" not in sink.model_fields_set:
                sink = sink.model_copy(update={"verified": old.verified})
            # An empty (redacted) value on update means "unchanged" — never blank
            # a stored secret just because the client echoed back the blanked
            # field. Restored BEFORE the identity-change check so an empty value
            # also doesn't spuriously trip the verified reset.
            #
            # EVERY FIELD redacted() BLANKS HAS TO BE ON THIS LIST. It blanks
            # `token` AND `chat_id`; only `token` was restored, so a plain
            # GET -> POST echo of the alerts array — what a settings panel does
            # when you edit any unrelated sink field — wrote the blank chat_id
            # through and then tripped the identity check below, resetting
            # `verified` to False. The sink survived, could no longer deliver,
            # and nothing said so. Derived from REDACTED_SINK_FIELDS rather than
            # spelled out here so a NEW redaction cannot silently become a new
            # eraser.
            for field in REDACTED_SINK_FIELDS:
                if old is not None and not getattr(sink, field, None)                         and getattr(old, field, None):
                    sink = sink.model_copy(
                        update={field: getattr(old, field)})
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
            # set_site flips is_default off and persists elevation_m. The stored
            # per-site horizon is preserved ONLY when the caller did not mention
            # it: the TS Site type omits the field, so a settings save must not
            # reset a safety floor by omission.
            #
            # THE CARRY-OVER USED TO BE UNCONDITIONAL, which made
            # horizon_min_deg unwritable through this route entirely — 200 OK,
            # value discarded, caller unable to tell "saved" from "thrown away".
            # Exactly the shape that cost 19 warm frames via cooling.setpoint_c
            # (see ConfigStore.set_cooling), and worse here: the route 403s a
            # caller who lacks config.safety for sending this very field
            # (_require_config_field_caps), so it gated a write it then dropped.
            # PUT /api/site honours it, so the two routes silently disagreed.
            #
            # model_fields_set is what tells an omitted field from an explicitly
            # sent one. Absent means unchanged; sent means write it.
            site = body.site
            if "horizon_min_deg" not in site.model_fields_set:
                site = site.model_copy(update={
                    "horizon_min_deg": config_store.cfg().site.horizon_min_deg})
            config_store.set_site(site)
        if body.cloudmap is not None:
            config_store.set_cloudmap(body.cloudmap)
        if body.safety is not None:
            config_store.set_safety(body.safety)
        if body.escalation is not None:
            config_store.set_escalation(body.escalation)
        if body.cooling is not None:
            config_store.set_cooling(body.cooling)
        if body.dusk is not None:
            config_store.set_dusk(body.dusk)
        if body.standards is not None:
            config_store.set_standards(body.standards)
        if body.focus is not None:
            config_store.set_focus(body.focus)
        if body.dew is not None:
            # THE OVERRIDE WINDOW IS A SETTING, SO RE-WRITING IT IS AN
            # INSTRUCTION. An override taken while ``manual_override_s`` was 0
            # never expires on its own, and before ``POST /api/dew/resume``
            # existed the only way out was a restart. Changing the window is the
            # operator saying what they want the pause to be, and applying the
            # new number only to the NEXT override would leave the current one
            # running under the old rule - which is the setting that looks like
            # it fixed the problem and did not.
            prior_override_s = getattr(config_store.cfg().dew,
                                       "manual_override_s", None)
            config_store.set_dew(body.dew)
            if getattr(body.dew, "manual_override_s", None) != prior_override_s:
                dew_controller.resume("the manual-override window was changed")
        if body.alerts is not None:
            config_store.set_alerts(_merge_alert_verified(body.alerts))
        if body.clear_deadman_url:
            # The explicit clear. Wins over any deadman_url in the same body:
            # asking to clear it and supplying one is a contradiction, and the
            # destructive reading is the one the operator typed on purpose.
            config_store.set_deadman("")
        elif body.deadman_url is not None:
            # The deadman url is REDACTED outbound (P2-12) — it can carry a
            # per-ping secret in its path/query. So an empty string on update
            # means "unchanged" (the client echoed back the blanked value),
            # exactly like the alert-secret guard above: never wipe a stored
            # deadman just because the redacted client round-tripped it.
            #
            # That made it WRITE-ONCE. The comment here used to promise "to
            # truly clear it the UI POSTs a dedicated clear (handled at the
            # /api/config layer if needed)" — and it never was, so the only
            # documented escape hatch did not exist and POST "" answered 200
            # while keeping the old url. clear_deadman_url above is that
            # dedicated clear, built to the shape /api/config/weather already
            # uses for astrospheric_api_key.
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
          dusk                 -> config.backend AND config.safety
          standards            -> config.safety   (frame-quality + give-up thresholds)
          focus                -> config.safety   (how the focuser is DRIVEN)
          dew                  -> config.safety   (heater policy on the optics)
          escalation           -> config.alerts   (notification/recovery policy)
          alerts               -> config.alerts
          deadman_url          -> config.alerts
          clear_deadman_url    -> config.alerts   (same field, destructive half)
        """
        present = body.model_fields_set
        if "dusk" in present and not principal.has(CAP_CONFIG_SAFETY):
            raise HTTPException(403, detail={"detail": "config.safety required to change dusk cooling",
                                            "code": "forbidden"})
        # Known, mapped blocks only. Any field on the body outside this map is a
        # programming error (a new block added without a cap) -> fail closed.
        block_caps = {
            "site": CAP_CONFIG_SITE_OPTICS,
            # The GOES cloud model is a weather DATA SOURCE, so it carries the
            # same cap as /api/config/weather rather than a safety cap: it
            # gates nothing (a named test pins that the engine never consults
            # it), it only decides whether we fetch.
            "cloudmap": CAP_CONFIG_SITE_OPTICS,
            "safety": CAP_CONFIG_SAFETY,
            "cooling": CAP_CONFIG_SAFETY,
            "dusk": CAP_CONFIG_BACKEND,
            "standards": CAP_CONFIG_SAFETY,
            # Both wave-2 blocks ride config.safety rather than site_optics:
            # a temp-comp coefficient with the wrong sign drives the focuser
            # away from focus all night, and a dew policy decides how much
            # power sits on the glass. Neither is a description of the site.
            "focus": CAP_CONFIG_SAFETY,
            "dew": CAP_CONFIG_SAFETY,
            "escalation": CAP_CONFIG_ALERTS,
            "alerts": CAP_CONFIG_ALERTS,
            "deadman_url": CAP_CONFIG_ALERTS,
            # The destructive half of deadman_url, so it carries the identical
            # cap. Unmapped it would 403 as an unknown block (fail-closed), which
            # is the map doing its job -- but it would make the clear
            # unreachable for everyone rather than gated for the right people.
            "clear_deadman_url": CAP_CONFIG_ALERTS,
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

    @app.get("/api/dusk/state")
    @declare(CAP_VIEW_STATUS)
    async def get_dusk_state(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        return dusk_arm.snapshot()

    @app.post("/api/config")
    @declare(CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SAFETY,
             CAP_CONFIG_ALERTS, CAP_CONFIG_BACKEND)
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

    def _active_horizon_points(cfg) -> list[list[float]] | None:
        """``config.safety.horizon`` as ``[[az, alt], ...]`` (JSON pairs, not
        tuples), or None when no profile is configured. ONE converter, used by
        the read route and by the two write paths' round-trip."""
        pts = cfg.safety.horizon
        if pts is None:
            return None
        return [[float(a), float(h)] for a, h in pts]

    def _location_is_active_site(loc, site) -> bool:
        """Is this saved location the site the rig is CURRENTLY configured for?

        Name (trimmed) plus coordinates within 1e-6 deg (~0.1 m — far below any
        GPS fix, so it matches a round-trip through JSON and never two genuinely
        different sites). A DEFAULT site never matches: it carries placeholder
        coordinates (0,0) that a location could otherwise collide with."""
        if getattr(site, "is_default", False):
            return False
        return (loc.name.strip() == (site.name or "").strip()
                and abs(float(loc.latitude) - float(site.latitude)) <= 1e-6
                and abs(float(loc.longitude) - float(site.longitude)) <= 1e-6)

    def _write_active_horizon(points: list[list[float]] | None):
        """Copy a location's control points into ``config.safety.horizon`` (the
        list of ``(az, alt)`` tuples the engine's obstruction rule reads).
        Blocking: call under ``asyncio.to_thread``."""
        cur = config_store.cfg().safety
        horizon = None if points is None else [
            (float(a), float(h)) for a, h in points]
        return config_store.set_safety(cur.model_copy(update={"horizon": horizon}))

    @app.get("/api/site")
    @declare(CAP_VIEW_STATUS)
    async def get_site(
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """The active site, plus the horizon profile the engine is actually
        gating slews with (``config.safety.horizon`` as ``[[az, alt], ...]``).

        REDACTION: the site node rides the ONE precise-site seam
        (``_redact_site_for``), so a caller lacking ``view.site_precise`` gets
        the block without name/lat/lon/elevation. ``horizon_points`` is NOT
        stripped, matching ``horizon_min_deg``, which ``redact.py`` retains by
        name (``_SITE_STRIP_KEYS`` excludes it) — and the same control points
        already ride the redacted config union on every WS ``config`` event and
        ``GET /api/config``, so gating them only here would be a lock on a door
        that stands open beside it."""
        cfg = config_store.cfg()
        site = cfg.site.model_dump()
        site["horizon_points"] = _active_horizon_points(cfg)
        return _redact_site_for({"site": site, "version": cfg.version},
                                principal)

    @app.get("/api/site/mount-gps")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def site_mount_gps(
            detected_only: bool = False,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        """Best-effort read-back of the connected mount's GPS fix. config.site_optics
        (the cap that may WRITE the site). Always 200; the body's ``available``
        flag + ``detail`` carry unavailability. ASSIST ONLY — the UI fills the
        draft; the user saves explicitly via PUT /api/site."""
        read = getattr(hub, "read_site_from_mount", None)
        # Alpaca site coordinates can be entered by hand. They are not proof
        # of a GPS receiver; Guided only offers a verified receiver source.
        telescope = getattr(hub, "devices", {}).get("telescope")
        mount_gps_known = getattr(telescope, "gps_available", False) is True
        if callable(read) and (not detected_only or mount_gps_known):
            result = await read()
            if result.get("available"):
                return {**result, "source": "mount", "detected": mount_gps_known}
            if not detected_only:
                return result
        if not detected_only:
            return {"available": False, "detail": "Mount GPS read-back is unavailable"}
        from ..site_gps import read_usb_gps
        return await asyncio.to_thread(read_usb_gps)

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
                body.elevation_m, body.horizon_min_deg, body.horizon_points)
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
        # Match against the row AS STORED, before the edit: "the location that
        # is CURRENTLY the site" is a fact about the old name/coordinates, and
        # this PUT may be changing them.
        before = next((row for row in location_store.list()
                       if row.id == loc_id), None)
        cfg = config_store.cfg()
        # ABSENT MEANS UNCHANGED, and only ``model_fields_set`` knows the
        # difference. ``horizon_points`` defaults to None, so a body that never
        # mentions it was indistinguishable from one that cleared it — and the
        # legacy Site panel (ui/src/components/settings/SitePanel.tsx) sends
        # exactly that body on every "save this location", which erased a drawn
        # polyline through a route the user thought was renaming a site. ``[]``
        # is still the explicit clear; ``UNCHANGED`` is the third state.
        # ``horizon_min_deg`` carries the identical shape and the identical
        # cost (it is a safety floor), so it takes the same rule.
        sent = body.model_fields_set
        points = body.horizon_points if "horizon_points" in sent else UNCHANGED
        floor = (body.horizon_min_deg if "horizon_min_deg" in sent
                 else UNCHANGED)
        # What the row's polyline will BE after this write — computed before it,
        # because the safety cap below has to be enforced before anything is
        # written. ``body.horizon_points`` is already normalized by the boundary
        # model's validator, so this compares like with like.
        after_points = (body.horizon_points if points is not UNCHANGED
                        else (before.horizon_points if before else None))
        # THE POINTS CHANGED, not "the body carried points". Keyed on the
        # latter, a pure rename echoed the library's stale polyline back into
        # ``config.safety.horizon`` and clobbered a horizon somebody had edited
        # by hand — irreversibly, through the API. The sites sheet echoes the
        # stored points on every coordinate or name edit, so that fired
        # routinely.
        write_through = (before is not None
                         and _location_is_active_site(before, cfg.site)
                         and after_points != before.horizon_points)
        # Field-level RBAC, the same shape as ``_require_site_field_caps``: the
        # library itself is site description (config.site_optics), but the
        # moment an edit reaches ``config.safety.horizon`` it is writing a
        # safety floor. Checked BEFORE any write, so a refusal leaves both the
        # library and the config untouched.
        if write_through and not principal.has(CAP_CONFIG_SAFETY):
            raise HTTPException(403, detail={
                "detail": "config.safety required to change the active site's "
                          "horizon",
                "code": "forbidden"})
        try:
            loc = await asyncio.to_thread(
                location_store.update, loc_id, body.name, body.latitude,
                body.longitude, body.elevation_m, floor, points)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        except LocationNameCollision as e:
            raise HTTPException(409, detail={"code": "name_collision",
                                             "id": e.existing_id})
        if write_through:
            # Editing the horizon of the site the rig is standing at takes effect
            # NOW. The engine reads config.safety.horizon, never the library, so
            # an edit that stopped at locations.json would be a drawn line that
            # gates nothing until someone re-applied the location — a claim the
            # UI would make and nothing would keep.
            await asyncio.to_thread(_write_active_horizon, loc.horizon_points)
            bus.publish("config", version=config_store.cfg().version)
        # The row the site was applied FROM just moved, and a library edit does
        # not move the mount - so the pointer now names coordinates the rig is
        # not using. Same claim ``set_site`` clears when they are typed in.
        moved = (before is not None
                 and (abs(float(loc.latitude) - float(before.latitude)) > 1e-6
                      or abs(float(loc.longitude) - float(before.longitude))
                      > 1e-6))
        if moved and await asyncio.to_thread(
                config_store.clear_active_location, loc_id):
            bus.publish("config", version=config_store.cfg().version)
        return loc.model_dump()

    @app.post("/api/locations/{loc_id}/apply")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def apply_location(
            loc_id: str,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        """Make a saved location the ACTIVE site: coordinates into
        ``config.site`` (exactly what ``PUT /api/site`` writes, including the
        ``horizon_min_deg`` floor), and the drawn polyline into
        ``config.safety.horizon`` so the engine's obstruction rule gates slews
        with the line the user traced at THIS site.

        ``horizon_points is None`` (a location with no drawn horizon) leaves the
        configured profile ALONE rather than clearing it: the field is new, so
        every location predating it would otherwise silently erase a horizon
        somebody configured through ``POST /api/config``. ``[]`` is the explicit
        clear.

        ATOMICITY + CONCURRENCY. Both fields land in ONE ``AppConfig`` mutation
        and one save (see ``_apply``), so there is no window in which the new
        coordinates are live against the old site's horizon. There is
        deliberately no ``expected_version`` here, unlike ``PUT /api/site``:
        this route does not carry field values a second editor could be
        clobbering, it names a stored location and asks for it whole, so
        last-apply-wins is the meaning of the request rather than a lost
        update. The version still bumps, so an open client's stale token loses
        its next field-level write."""
        loc = next((row for row in location_store.list()
                    if row.id == loc_id), None)
        if loc is None:
            raise HTTPException(404, detail={"code": "not_found"})
        # Same field-level rule as PUT /api/site: a horizon is a safety floor.
        writes_safety = (loc.horizon_min_deg is not None
                         or loc.horizon_points is not None)
        if writes_safety and not principal.has(CAP_CONFIG_SAFETY):
            raise HTTPException(403, detail={
                "detail": "config.safety required to apply a location's horizon",
                "code": "forbidden"})
        cur_site = config_store.cfg().site
        site = cur_site.model_copy(update={
            "name": loc.name,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "elevation_m": loc.elevation_m,
            # Same "absent means unchanged" rule PUT /api/site keeps: a location
            # with no floor of its own must not reset the stored one to the
            # model default.
            "horizon_min_deg": (loc.horizon_min_deg
                                if loc.horizon_min_deg is not None
                                else cur_site.horizon_min_deg)})

        def _apply():
            # ONE mutation, ONE save, ONE version bump. This was
            # ``set_site(...)`` followed by ``set_safety(...)`` — two
            # ``bump_and_save`` calls with a window between them, and the
            # partial state that window can leave on disk is the worst one
            # available: the NEW site's coordinates active against the PREVIOUS
            # site's horizon, i.e. a safety floor traced somewhere else gating
            # tonight's slews, with a version number that says the config is
            # consistent. ``set_site_and_safety`` cannot half-happen.
            cur = config_store.cfg()
            safety = cur.safety
            if loc.horizon_points is not None:
                safety = safety.model_copy(update={
                    "horizon": [(float(a), float(h))
                                for a, h in loc.horizon_points]})
            # WHICH saved location is live (#D-FU-1). Set INSIDE the same
            # mutation as the site and the horizon, not beside it: the
            # pointer and the coordinates it points at have to land in one
            # save, or a crash between two writes leaves the config naming a
            # location whose values it is not using. Server-owned - no route
            # writes it directly, which is why it is not on ConfigPatchBody.
            cur.active_location_id = loc.id
            return config_store.set_site_and_safety(site, safety)

        cfg = await asyncio.to_thread(_apply)
        push = getattr(hub, "push_site_to_mount", None)
        if callable(push):
            try:
                await push()
            except Exception as e:
                bus.log("warning", f"could not push site to mount: {e}", "config")
        bus.publish("config", version=cfg.version)
        return _config_payload(principal)

    @app.delete("/api/locations/{loc_id}")
    @declare(CAP_CONFIG_SITE_OPTICS)
    async def delete_location(
            loc_id: str,
            principal: Principal = Depends(require(CAP_CONFIG_SITE_OPTICS))):
        try:
            await asyncio.to_thread(location_store.delete, loc_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        # A pointer to a location that no longer exists is worse than no
        # pointer: the Sky hub would show a site name with nothing behind it
        # and no way to clear it. The COORDINATES stay - deleting the saved
        # entry is not a request to forget where the rig is standing, and
        # blanking config.site here would take the horizon with it.
        if config_store.cfg().active_location_id == loc_id:
            def _clear():
                cur = config_store.cfg()
                cur.active_location_id = None
                return config_store.bump_and_save()
            cfg = await asyncio.to_thread(_clear)
            bus.publish("config", version=cfg.version)
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

    def _site_sky_answer(latitude: float, longitude: float,
                         principal: Principal) -> dict:
        """What `/api/site/sky` says about a place, cut to what the caller may
        hold. Takes the coordinates as numbers so the stored site and the
        picker's typed point are answered by one body of arithmetic."""
        from ..catalog import coords
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

    @app.get("/api/site/sky")
    @declare(CAP_VIEW_STATUS)
    async def site_sky(request: Request,
                       principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        # THE STORED SITE ONLY. The picker's lat/lon used to ride this URL as
        # query overrides, and a URL is written down by every hop it crosses
        # (#520); they moved to `POST /api/site/sky` below, and a GET still
        # naming them is refused so an old tab fails where it can be seen.
        _refuse_site_query(request, "POST {lat, lon} to /api/site/sky asks "
                                    "about a typed place")
        s = config_store.cfg().site
        # NO SITE, NO SKY (#24). Every field is computed from the stored site,
        # and at the 0,0 default that is the Gulf of Guinea's sun and dark
        # window. Withheld rather than invented: an absent `dark_window` is
        # already what a viewer receives, so every consumer handles it.
        if not site_is_set(s):
            return {}
        return _site_sky_answer(s.latitude, s.longitude, principal)

    @app.post("/api/site/sky")
    @declare(CAP_VIEW_SITE_PRECISE)
    async def site_sky_preview(
            body: SiteSkyPreviewBody,
            principal: Principal = Depends(require(CAP_VIEW_SITE_PRECISE))):
        """The site picker's read-back: the same answer, for a TYPED place.

        A HOLDER-ONLY QUESTION, gated at the dependency. A route that answers
        "what is the sun's altitude at the coordinates I name" is a
        geolocation oracle no matter how carefully the stored-site path is
        redacted: a caller sweeps candidate coordinates and keeps whichever
        reproduces the readings it already has. It exists for the site
        picker, which only a holder of view.site_precise is shown.

        A POST although it changes nothing, because its argument is a place
        and a body is the one part of a request no access log writes down
        (#520). It answers whether or not a site is saved: the picker is how
        an unsited rig GETS one, so refusing it here would be circular.
        """
        return _site_sky_answer(body.lat, body.lon, principal)

    # ------------------------------------------------------------------- safety

    @app.get("/api/safety/state", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def safety_state():
        """``{connected, reading|null, streak, stale, sun_watch}`` for the
        Monitor/Settings safety widget. ``reading`` is the hub's CACHED
        own-cadence read (never an inline ``is_safe()`` — C1-12); ``streak`` is
        the engine's consecutive same-verdict count (the gate's hysteresis), read
        defensively so this lane stays decoupled from the engine lane landing its
        counters.

        ``sun_watch`` is ``{blind, blind_since, last_position_at, armed}`` (#137):
        whether the sun-exclusion net can currently see the mount, since when,
        when it last read a position, and whether its task is alive. TIMES AND
        BOOLEANS ONLY, which is what lets this stay readable at ``view.status``
        for a viewer: the position the net last read, and anything derived from
        it, is a latitude oracle (#140) and is never part of this."""
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
            "sun_watch": sun_watch.state(),
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
        # ONE implementation of the merge rule. This route used to carry a second
        # copy that compared the delivery identity BEFORE restoring the blanked
        # token — and since the client is only ever served ``token: ""``, every
        # save of a credential-bearing sink echoed a blank back, read as a
        # re-point, and silently cleared ``verified``. ``_merge_alert_verified``
        # restores first and resets second. It looks the old sink up in the
        # STORE (not this local list) and is a no-op for a brand-new id, so it
        # must run BEFORE the assignment and is correct for the append branch.
        sink = _merge_alert_verified([sink])[0]
        if idx is not None:
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

    def _refuse_weight_altitude_for(principal: Principal,
                                    weight_altitude: bool) -> None:
        """400 when ``weight_altitude`` is requested by a principal lacking
        ``view.site_derived`` (#567), for every bundle route that takes the
        option: it folds each frame's altitude into the sub weights (and so
        into a bundle group's ``kept_count``), which is the #19 class carried
        into a bundle. REFUSED rather than silently coerced to False, so a
        caller who asked for it learns why, instead of reading an unweighted
        bundle as "this report has no useful altitude data".

        Independent of role: a viewer-LINK's ``caps`` are an explicit per-link
        set (``Principal``'s own contract), not necessarily a whole role's, so
        this checks the capability rather than assuming which roles hold
        ``control.capture`` today (see ``report_bundle_materialize``)."""
        if weight_altitude and not principal.has(CAP_VIEW_SITE_DERIVED):
            raise HTTPException(
                400, "weight_altitude requires view.site_derived: it folds "
                     "each frame's altitude into the sub weights")

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

        Frame paths become capture-root-relative for every caller (see
        ``_redact_report_for``'s own docstring -- this is no longer a holder
        rule; ``saved_path`` externalizes the same way ``/api/sessions/{id}``
        externalizes ``path``). A principal without ``view.site_derived``
        also loses each frame's ``altitude_deg`` and every ``sky_angles``
        row's ``exposed_at`` (#567)."""
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
        ``1785084747.5023835`` alone -- EXCEPT ``altitude_deg``, which
        ``report_csv_columns`` drops for a principal lacking
        ``view.site_derived`` (#567), the same per-frame value the JSON route
        withholds from the same caller."""
        report = await asyncio.to_thread(SessionReporter.load, report_id)
        if report is None:
            raise HTTPException(404, "report not found")
        buf = io.StringIO()
        # A CSV export is not a loophole around the JSON route — SAME FUNCTION,
        # not a parallel rule. Each row goes through the externalizer the JSON
        # route uses, so `saved_path` here is the same capture-root-relative
        # value it is there. Two hand-written implementations of one disclosure
        # rule is exactly how the report route ended up shipping absolute paths
        # while the session route stripped them.
        cols = report_csv_columns(
            ["ts", "ts_utc", "target", "filter", "frame_type", "exposure_s",
             "gain", "offset", "binning", "accepted", "hfr", "ecc",
             "sensor_temp_c", "guide_rms_total", "altitude_deg", "saved_path"],
            principal)
        import csv
        w = csv.writer(buf)
        w.writerow(cols)
        rows = _redact_report_for(
            {"frames": [fr.model_dump() for fr in report.frames]}, principal
        )["frames"]
        for d in rows:
            d = dict(d)
            d["ts_utc"] = _iso_utc(d.get("ts"))
            w.writerow(["" if d.get(c) is None else d.get(c) for c in cols])
        # Use the sanitized slug (not the raw path param) so the response header
        # can never carry CR/LF/quotes from attacker-controlled input.
        fname = f"{_slug(report_id)}.frames.csv"
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/api/reports/{report_id}/bundle")
    @declare(CAP_VIEW_STATUS)
    async def report_bundle(report_id: str, weight_altitude: bool = False,
                            layout: str = "grouped",
                            keep_threshold: float | None = None,
                            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Slim stacking-bundle preview (per-group counts + master-match status +
        warnings) for the report viewer panel (PRO-10 §1.5). 404 if missing.
        ``weight_altitude`` (opt-in) folds a sin(alt) term into the sub weights,
        and is REFUSED for a principal lacking ``view.site_derived`` (#567) —
        see ``_refuse_weight_altitude_for``. ``bundle_summary`` carries no
        per-light rows, so nothing else here needs redacting once that option
        is refused. ``layout`` picks the folder convention; ``keep_threshold``
        (a normalized weight in [0,1]) makes each group report ``kept_count``
        — one scalar instead of shipping a 2000-row weight vector to the
        client."""
        _refuse_weight_altitude_for(principal, weight_altitude)
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

    @app.get("/api/reports/{report_id}/bundle.zip")
    @declare(CAP_VIEW_STATUS)
    async def report_bundle_zip(report_id: str, weight_altitude: bool = False,
                                layout: str = "grouped",
                                keep_threshold: float | None = None,
                                principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """The stacking bundle as an in-memory ``.zip`` (manifest + weights CSV +
        README + build.sh/.ps1 — NOT the FITS; §4 decision 1). Mirrors
        ``report_frames_csv``: the sanitized slug (never the raw path param) forms
        the download filename so the header can't carry CR/LF/quotes.
        ``weight_altitude`` (opt-in) folds a sin(alt) term into the sub weights,
        and is REFUSED for a principal lacking ``view.site_derived`` (#567);
        ``layout``/``keep_threshold`` are the PRO-10 enrichments (defaults keep the
        one-click download byte-for-byte what it was).

        NO ABSOLUTE PATH LEAVES HERE. Every member is built from an
        EXTERNALIZED bundle, so ``src`` is capture-root-relative exactly as
        ``saved_path`` is on ``GET /api/reports/{id}`` and on the frames CSV.
        This route is ``view.status`` — every role — and it was the last one
        shipping ``fr.saved_path`` verbatim, in three members at once. The
        generated scripts read the user's own capture folder from
        ``CAPTURE_ROOT`` so they still resolve.

        NOR DOES ANY MEMBER CARRY ALTITUDE, for a principal lacking
        ``view.site_derived`` (#567): ``build_bundle`` copies every frame's
        ``altitude_deg`` onto its light row UNCONDITIONALLY (not only when
        ``weight_altitude`` is set), so ``manifest.json`` and ``weights.csv``
        are redacted the same way regardless of that option -- the #19 class,
        carried into a bundle. ``README.txt``/``build.sh``/``build.ps1`` name
        no per-sub metric at all, so they need no redaction here."""
        _refuse_weight_altitude_for(principal, weight_altitude)
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
        b = externalize_bundle(b, gallery_module.relpath_under_capture)
        manifest = redact_bundle_manifest_for(manifest_json(b), principal)
        wcsv = redact_bundle_csv_for(weights_csv(b), principal)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.writestr("weights.csv", wcsv)
            z.writestr("README.txt", readme_text(b))
            z.writestr("build.sh", build_script(b, "sh"))
            z.writestr("build.ps1", build_script(b, "ps1"))
        fname = f"{_slug(report_id)}.bundle.zip"
        return Response(buf.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})

    @app.post("/api/reports/{report_id}/bundle/materialize")
    @declare(CAP_CONTROL_CAPTURE)
    async def report_bundle_materialize(report_id: str,
                                        weight_altitude: bool = False,
                                        layout: str = "grouped",
                                        keep_threshold: float | None = None,
                                        principal: Principal = Depends(require(CAP_CONTROL_CAPTURE))):
        """Lay the ACTUAL FITS out under ``captures/exports/<id>/`` for someone
        running AstroDeck ON the capture box — hardlinks where possible, so a
        200 GB night materializes instantly and costs no extra disk (§2.4).

        POST + **CAP_CONTROL_CAPTURE**, unlike the other bundle routes: this one
        WRITES to the capture box's filesystem, so it needs the same authority as
        capturing, and a verb no browser will prefetch. Returns a summary only —
        no file body; the bytes are on disk where the user's stacker can see them.
        The summary (linked/copied/failed counts, per group) carries no per-light
        metric, so it needs no redaction of its own; ``weight_altitude`` is still
        REFUSED for a principal lacking ``view.site_derived`` (#567), for the
        same reason the other two bundle routes refuse it -- a viewer-LINK's
        capabilities are an explicit per-link set, not necessarily a whole
        role's, so this does not assume every ``control.capture`` holder also
        holds ``view.site_derived``.

        The sources are provably under CAPTURE_DIR (``build_bundle`` selects only
        ``is_local`` lights); masters may legitimately live in a shared library
        elsewhere, and they are library-chosen, not user-supplied. Every
        DESTINATION is re-validated for containment by
        ``bundle_materialize_plan``."""
        _refuse_weight_altitude_for(principal, weight_altitude)
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

    @app.get("/api/profiles")
    @declare(CAP_VIEW_STATUS)
    async def list_profiles(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        # Rows carry no device ``extra`` today, but redact defensively so no
        # secret-bearing ``extra`` value can ever cross the wire from this route.
        # A picker row also carries ``site_name``, which is PRECISE-tier site
        # data (a pad name geolocates as well as the coordinates do), so it goes
        # for anyone without view.site_precise.
        return [_redact_profile_for(redact_profile(r), principal)
                for r in profiles.list(config_store.cfg().active_profile_id)]

    @app.get("/api/profiles/{profile_id}")
    @declare(CAP_VIEW_STATUS)
    async def get_profile(profile_id: str,
                          principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        try:
            # Wire-redaction (W2): scrub secret-bearing device ``extra`` values
            # before this VIEWER-visible read leaves the server; the at-rest
            # profile keeps the real value so the rig can still connect.
            # RBAC redaction: that is a key-NAME filter and a profile is a saved
            # connection intent, so every device's host/port/port_path and the
            # top-level NINA/PHD2 endpoints are the SAME addressing /api/drivers
            # strips for a caller without config.backend.
            return _redact_profile_for(
                redact_profile(profiles.get(profile_id)), principal)
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
        # Auto-resume's recovery ladder is running too (#238): refused
        # unforced, and forced it is stopped and waited for as Abort stops it
        # (see ``connect_rig``).
        busy = _teardown_busy_detail()
        if busy is not None and not force:
            raise HTTPException(409, detail={"detail": busy, "code": "running"})
        if force:
            resume_arm.stop_recovery(
                "the operator force-applied a profile while it was "
                "re-centring the mount", disarm=True)
            await _wait_for_the_ladder()
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
        - it disconnects the current rig). Auto-resume's recovery ladder counts
        as running (#238); forced, it is stopped with its session disarmed and
        waited for before the connect (see ``connect_rig``)."""
        force = bool(body and body.force)
        if not _profile_exists(profile_id):
            raise HTTPException(404, "profile not found")
        busy = _teardown_busy_detail()
        if busy is not None and not force:
            raise HTTPException(409, detail={"detail": busy, "code": "running"})
        if force:
            resume_arm.stop_recovery(
                "the operator force-activated a profile while it was "
                "re-centring the mount", disarm=True)
            await _wait_for_the_ladder()
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
        """The stored plan; 404 when there is none, and 422 ``unreadable``
        with the list row's own sentence for a file that is there and is not
        a plan (``PlanUnreadable``, #378). That was pydantic's
        ``ValidationError`` escaping as a 500."""
        try:
            return plan_library.get(plan_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")
        except PlanUnreadable as e:
            raise HTTPException(422, detail={"detail": e.reason,
                                             "code": e.code})

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
        plan = _accepted_count_mode_if_omitted(body.plan)
        return await asyncio.to_thread(plan_library.save, plan, plan_id)

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
        # THE PLAN FIRST, THEN ITS BYTES (#378): ``get`` is the one judgment
        # of which files are plans, so a damaged file answers 422 with the
        # list row's sentence here too, whether it fails validation or does
        # not parse, instead of a 500 from the name read or a 404.
        try:
            name = plan_library.get(plan_id).name or plan_id
            raw = plan_library.export_bytes(plan_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "plan not found")
        except PlanUnreadable as e:
            raise HTTPException(422, detail={"detail": e.reason,
                                             "code": e.code})
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

    # -------------------------------------------------------------------- flows
    #
    # The graph is the source of truth and the plan is derived — so nothing here
    # persists a compiled plan, and every read compiles fresh. ORDERING MATTERS
    # twice below; both places say so where they sit.

    async def _persist_flow(record: FlowRecord) -> dict:
        """One writer for POST, PUT, the wizard and the quick flow, because
        the field-ownership policy is the thing that must not drift between
        them.

        FlowRecord carries four fields a client must not set: ``created_ts``,
        ``last_run`` and ``last_result`` (``store.BOOKKEEPING``), and
        ``readonly``. The library cards RENDER last_run/last_result — so a
        client that PUTs ``last_result: "ok"`` onto a flow that has never run
        would get a green card for free. ``readonly`` is cleared here (the
        store refuses it, but only by exception); the other three are the
        STORE'S, taken by ``save_and_report`` from the record its own read of
        the file it replaces returns (``_stored``, ``_bookkeeping``).

        NO READ OF ITS OWN (#364). This used to take the three from
        ``flow_store.get``, a walk of the library that turns any failure to
        read a file into an unreadable row, so a transient read error there
        answered "no such flow" and the save wrote the flow as created now
        and never run, whenever the store's own read a moment later
        succeeded. Now the one read decides: it refuses the save (409
        ``stored_unreadable``, below) or carries the history.

        THE SAVE RULES RIDE THE STORE'S ONE WRITER (#189 Revision 2 rulings 2
        and 3, spec 3.3): ``save_and_report`` runs ``save_rules.prepare_save``
        against the file it replaces, so every TARGET and POOL is written
        counting accepted subs and every TARGET's ``frameAnchor`` is the
        server's. This answers with what that did (``_save_answer``):
        ``migrated`` when the counts were switched, ``reanchored`` for every
        block whose counts restarted. ``save`` would apply the same rules and
        throw the report away, and the report is the only place a restarted
        campaign is said at the moment it is caused.
        """
        record = record.model_copy(update={"readonly": False})
        try:
            stored, migrated, reanchored = await asyncio.to_thread(
                flow_store.save_and_report, record)
        except ReadOnlyFlow as e:
            raise HTTPException(403, detail={"detail": str(e), "code": e.code})
        except FlowLibraryFull as e:
            raise HTTPException(409, detail={"detail": str(e), "code": e.code})
        except KeyError:
            # safe_id_path's refusal for '..', separators, NUL, a drive prefix,
            # a reserved device name or a trailing dot. A traversal id is a
            # miss, not a server error.
            raise HTTPException(404, detail={"code": "not_found"})
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": getattr(e, "code", "invalid")})
        return _save_answer(stored, migrated, reanchored)

    def _camera_can_cool() -> bool:
        """Does this rig have a TEC? Live off the connected camera, and from the
        last one that connected when nothing is plugged in.

        THE FALLBACK IS THE POINT. ``can_cool`` is populated on connect and
        ``_teardown`` clears ``hub.devices``, so reading it live answered False
        for a disconnected rig — and the cooling advisory this feeds exists to
        put a line on the canvas at 19:00, which is exactly when a rig is not
        connected. Measured: the same flow compiled silent, then produced the
        note after POST /api/connect/sim, then went silent again on disconnect.
        "Not plugged in right now" is not "has no cooler".

        ``camera_can_cool_seen`` is False until a camera has actually connected,
        so a rig that has genuinely never had a cooler still says nothing rather
        than nagging — the same rule the engine's run-start warning uses, and it
        has to be the same rule or the editor and the log disagree about tonight.
        """
        cam = hub.devices.get("camera")
        if cam is not None:
            return bool(getattr(cam, "can_cool", False))
        return bool(config_store.cfg().camera_can_cool_seen)

    def _profile_has_rotator() -> bool | None:
        """Whether the rig has a rotator, as M8 and the wizard ask it: True,
        False, or None for "nobody knows".

        TRUE ON EVIDENCE: a rotator connected now, or a rotator row in the
        active profile. FALSE only when the answer is written down: a profile
        that lists its devices one by one on the native backend, with no
        rotator among them, because a native role with no row has no address
        and cannot connect. Everything else is None: no active profile, or a
        primary (the simulator, NINA) that may fill the role itself. A None
        is not a "no": M8 says nothing and the wizard offers "Rotate to PA",
        where a False read off a rig nobody described would have both refuse
        a rotator that is there."""
        rot = hub.devices.get("rotator")
        if rot is not None and getattr(rot, "connected", False):
            return True
        profile = hub._active_profile()
        if profile is None:
            return None
        if any(d.role == "rotator" for d in profile.devices):
            return True
        primary = profile.primary_backend or profile._derived_primary()
        if primary == "native" and profile.devices and not profile.nina_host:
            return False
        return None

    def _rig_facts() -> RigFacts:
        """What the flow compile knows about the live rig, as ONE value
        (``flows.rig``; #189 spec 3.3, 1.8). The compile, the doctor and the
        wizard are pure, so the route reads the rig for them, as it reads
        ``cool_to``:

        * ``fov_deg``: the imaging camera's field at BIN 1, off
          ``hub.effective_optics`` (the profile's optics, else the rig's,
          else the connected camera's sensor), because a block's
          ``fovX``/``fovY`` snapshot is bin 1 and the two are compared (M5).
          None while the optics are not known, never ``(0, 0)``: a zero field
          is a camera that images nothing, and ``RigFacts`` refuses it.
        * ``hop_cost_s``/``hop_samples``: ``engine.measured_cost("hop")``,
          None and 0 until a hop has been measured. Never the engine's 150 s
          seed: the doctor's M10 and the brief speak only of a measured cost.
        * ``has_rotator``: ``_profile_has_rotator``.
        * ``reject_guards_off``: both reject guards resolved off by
          ``resolve_policy``, the resolution ``quota_unbounded`` reads. A
          flow plan never sets either guard, so the rig's standards decide,
          and a bare plan asks for them without compiling anything.
        * ``guide_provider``, ``guide_settle``, ``guide_dither_px`` and
          ``guide_dither_every``: Rig > Guider's half (#506), for the Tonight
          brief's guide sentence (``_guider_and_settle``, and the comment at
          the call).

        On the event loop, like ``_camera_can_cool``: it reads the device
        map, and a caller on a worker thread is handed the value."""
        optics = hub.effective_optics()
        fov = None
        x, y = optics.get("fov_w_deg"), optics.get("fov_h_deg")
        if (optics.get("have_optics") and isinstance(x, (int, float))
                and isinstance(y, (int, float)) and math.isfinite(x)
                and math.isfinite(y) and x > 0 and y > 0):
            fov = (float(x), float(y))
        profile = hub._active_profile()
        where = (f"profile {profile.name}"
                 if profile is not None and profile.optics is not None
                 else "the rig's optics")
        measured = getattr(engine, "measured_cost", None)
        hop = measured("hop") if callable(measured) else None
        bare = SequencePlan()
        policy = resolve_policy(bare, config_store.cfg())
        guider, settle = _guider_and_settle()
        return RigFacts(
            fov_deg=fov,
            fov_from=(f"{where}, matched {time.strftime('%Y-%m-%d')}"
                      if fov is not None else ""),
            hop_cost_s=hop[0] if hop else None,
            hop_samples=hop[1] if hop else 0,
            has_rotator=_profile_has_rotator(),
            reject_guards_off=not (policy.max_consecutive_rejects
                                   or policy.max_consecutive_rejects_night),
            # Rig > Guider's half (#506): the dither distance
            # ``resolve_policy`` gives a plan that sets none, which a flow's
            # plan is (the rig's ``guide.dither_pixels``), and the cadence a
            # flow's run dithers at: ``to_sequence_plan`` never sets
            # ``dither_every``, so the plan keeps the model's own default,
            # whatever the GUIDE card's ``dither`` says.
            guide_provider=guider, guide_settle=settle,
            guide_dither_px=policy.dither_pixels,
            guide_dither_every=bare.dither_every)

    def _settle_field_override(override: float | None, default: float) -> float:
        """One settle field for the brief: the persisted override (#560 WP-58,
        backlog wave 12 second half) when it is SET and a reading the brief
        can print, else the provider's own default.

        A 0 override is a real choice at the engine
        (``SequenceEngine._dither_settle_override``'s own docstring: "the
        engine's fast-recenter pulses alone, no extra settle dwell"), but
        ``RigFacts.guide_settle`` refuses a non-positive or non-finite pixel
        or second as not a reading at all
        (``test_h4_brief_guides_from_rig.py::TestTheFacts::
        test_a_reading_that_is_no_reading_is_refused``). Printing it verbatim
        would crash ``_rig_facts`` instead of merely misstating it, so a 0 or
        invalid override falls back to the provider default here exactly as
        "not set" does."""
        if override is None:
            return default
        try:
            v = float(override)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) and v > 0 else default

    def _guider_and_settle() -> tuple[str | None, tuple | None]:
        """The guider a flow's run will guide with and the settle its dithers
        wait on (#506), from Rig > Guider, the source
        ``to_plan.NODE_SETTINGS["guide"]`` tells the operator the GUIDE
        card's ignored provider and settle come from; the brief names these
        and never the card's.

        * The guider is the label ``providers.resolve("guide")`` gives, the
          resolver the run's guide start honours and the sheet's provider row
          shows; None when it cannot say.
        * The settle is the resolved guider's OWN, ``(pixels, seconds)``,
          UNLESS the operator has persisted a settle override that the guider
          this run picks actually honours (#560 WP-58, second half). The
          engine dithers with a distance and the ``GuideConfig.dither_settle_
          pixels``/``_time_s`` override
          (``SequenceEngine._dither_settle_override``), so each guider waits
          on its own rule only where that override is unset: the native
          engine's (``NATIVE_GUIDE_SETTLE``; a ``NativeGuider`` over real
          hardware or over the simulator alike) and the PHD2 bridge's
          (``guide.phd2.SETTLE``, which it sends with every dither). NINA
          settles by a rule it does not publish: None.

          THE NATIVE ENGINE NEVER SEES THE OVERRIDE'S PIXELS/TIME. Unlike
          PHD2, the Rust engine self-manages its own settle pixel/time
          criteria and does not export them to this wheel
          (``guide/native.py::dither``'s own docstring; restated at
          ``GuideConfig.dither_settle_pixels``): a caller-supplied settle
          only ever widens its WAIT TIMEOUT there, never the criteria. So a
          persisted override that would change a PHD2 night's settle changes
          nothing about what a NATIVE night's dither waits on, and applying
          it here anyway would print a number the night will never use --
          the exact defect this item exists to fix, aimed at the wrong
          guider. ``NATIVE_GUIDE_SETTLE`` is therefore unconditional.

        NOT DECIDED YET IS NONE, NOT PHD2. With no guider wired (the rig not
        connected, as when tonight is planned in the afternoon) and no pin to
        the bridge, the resolver can only answer its last resort, the PHD2
        bridge, because the guide camera and mount it would weigh are absent.
        That is not what the run will use: once the rig connects, the guide
        start (``hub.select_guide_provider``) picks the native engine for a
        rig pinned to it or offering it. Named, it put #506's own sentence
        back, "guides with PHD2" and PHD2's 8 s settle, on a rig pinned to
        the native guider. So that answer is None, and the brief says where
        the guider comes from instead. #506 asks for the guider the run will
        use "or leaves them out"; this leaves it out until it is known."""
        from ..providers import guide_override_family
        from ..providers import resolve as resolve_provider
        try:
            choice = resolve_provider("guide", hub)
            pinned = guide_override_family(hub)
        except Exception:
            return None, None
        if (choice.kind == "backend" and choice.label == "PHD2"
                and getattr(hub, "guider", None) is None
                and pinned != "backend"):
            return None, None
        if choice.kind in ("astrodeck", "sim"):
            return choice.label or None, NATIVE_GUIDE_SETTLE
        if choice.kind == "backend" and choice.label == "PHD2":
            from ..guide.phd2 import SETTLE as phd2_settle
            gcfg = config_store.cfg().guide
            settle = (
                _settle_field_override(gcfg.dither_settle_pixels,
                                       phd2_settle["pixels"]),
                _settle_field_override(gcfg.dither_settle_time_s,
                                       phd2_settle["time"]))
            return choice.label, settle
        return choice.label or None, None

    async def _compile_payload(graph: FlowGraph, name: str, *,
                               flow_id: str = "") -> dict:
        """``{plan, structural, issues, unmapped, readouts, rig}``.

        ``flow_id`` is the stored flow's id, "" for an unsaved draft. It goes
        to ``to_sequence_plan`` exactly as the run passes it (spec 3.3), so a
        saved flow's preview is compiled with the ids its run will carry.

        SIX KEYS. The compile itself, THREE LISTS of what is wrong, not one,
        because three different things can be wrong with a graph and
        collapsing them takes away the operator's ability to act, and two
        objects the Target modal's RUN section prints:

        * ``plan`` — ``compile_plan``'s output, the dict the PLAN tab shows
          verbatim. Not the SequencePlan: that is what ``to_sequence_plan``
          makes of it, and ``readouts`` are read off it.
        * ``structural`` — edges to nodes that do not exist, an input wired
          twice, a flow output feeding an event input. NOT run by model
          construction; only ``FlowStore.save`` calls it. ``compile_plan``
          validates NOTHING and will happily emit ``action: "?"`` for an edge
          whose destination is missing, so a compile route that does not call
          this itself compiles nonsense without complaint.
        * ``issues`` — the doctor's 28 rules (``flows.doctor.check``): 12
          from the prototype (1 to 10, 13 and 14; 11 and 12 were removed on
          2026-08-16), the 15 mosaic rules M1 to M15 and L1, each an advisory
          ``{text, level}``; then the capture-geometry warnings
          (``capture_geometry.plan_warnings``) and that inventory's note.
        * ``unmapped`` — what the compile emits that ``SequencePlan`` cannot
          carry. This is the list that stops a graph feature being silently
          inert, and it is the reason this endpoint is worth calling before a
          run rather than after one.
        * ``readouts`` — ``{node_id: block}`` for every TARGET block the plan
          shoots: subs, hours, the visit bound and the visits, the hop, the
          pre-flip idle, the angle tolerance and the focus line
          (``flows.readouts``, spec 2.4 RUN, S4 item 1). "Every number comes
          from the server compile, never computed in the client", so both
          UIs print these and neither does the arithmetic. Empty when the
          compile refused. No site data, so every role that may compile
          reads them (spec 6.9).
        * ``rig`` — the rig facts the modal prints (``flows.readouts.
          rig_readout``): the live camera field, whether there is a rotator,
          and the MEASURED hop, null until one has been timed.

        ONE READING OF THE RIG (``_rig_facts``, spec 3.3), handed to the plan,
        to the doctor and to the readouts as the same object: M5's loss in
        ``unmapped`` and its warning in ``issues`` are then two sentences about
        one field, and the RUN section's hop the one M10 weighed, never two
        reads taken a moment apart.
        """
        structural = graph.validation_errors()
        compiled = compile_plan(graph, name)
        rig = _rig_facts()
        unmapped: list[dict] = []
        geometry_issues: list[dict] = []
        _plan: SequencePlan | None = None
        try:
            # SAME ARGUMENTS AS THE RUN. A preview compiled differently from the
            # run is a preview of a different night — the defect the park/warm
            # binding was written for, one layer up. Whatever the run would cool
            # to, the PLAN tab has to show. The flow's id included: without it
            # the ids fall back to uuid4 and the preview names different steps
            # from the run's.
            _plan, unmapped = to_sequence_plan(
                compiled, graph, flow_id=flow_id,
                cool_to=getattr(config_store.cfg().cooling, "setpoint_c", None),
                camera_can_cool=_camera_can_cool(),
                closes_on_unsafe=bool(
                    config_store.cfg().safety.close_dome_on_unsafe),
                rig=rig)
            groups, note = await capture_geometry.inventory()
            geometry_issues = [{"text": text, "level": "warn"} for text in
                               capture_geometry.plan_warnings(_plan, groups)]
            if note:
                geometry_issues.append({"text": note, "level": "warn"})
        except GraphNotRunnable as e:
            # Not an error response: a half-built graph is the NORMAL state of
            # an editor. The canvas's compile (``flowsCompile``) runs when a
            # flow opens, after each save and on the Target modal's DONE or
            # LOOP PANELS, which write through ``flowsApplyFraming``; an edit
            # between them compiles nothing (#356), and Tonight asks for one
            # more: when Tonight is read over a graph the compile in hand was
            # not made from (#688), so its PLAN is never a stale graph's.
            # The modal also posts its own draft here once a framing edit
            # settles, for its RUN numbers.
            # The refusal is reported in the same list as every other loss.
            unmapped = [{"key": "plan", "detail": str(e), "level": "danger"}]
        run_readouts: dict = {}
        if _plan is not None:
            # OFF THE LOOP: finding which plan targets are which block asks
            # ``to_plan``'s own drop test, and for a TARGET known only by its
            # name that is a catalogue search (10 to 35 ms, #249), made for
            # every compile a client asks, the editor's on open, save and
            # DONE (#356) and the modal's settled draft among them. The focus
            # settings are the run's: ``autofocus_every`` off the plan, and
            # the temperature delta as ``resolve_policy`` resolves it for
            # this plan, which is what ``_refocus_due`` reads.
            run_readouts = await asyncio.to_thread(
                flow_readouts, compiled, _plan, rig,
                autofocus_every=_plan.autofocus_every,
                refocus_on_temp_delta_c=resolve_policy(
                    _plan, config_store.cfg()).refocus_on_temp_delta_c)
        return {"plan": compiled, "structural": structural,
                # WITH the rig's standards: rule 14 asks whether frame grading
                # is armed, which no graph can say. Same store the run reads.
                # WITH the connected telescope: GN-09's needs-guiding rule asks
                # whether THIS mount's unguided tracking can hold the sub the
                # graph asks for, which is a driver capability, not a config
                # value. None on a disconnected rig -- the rule simply does
                # not run, same as `standards=None`. WITH the rig facts the
                # plan was compiled with (M5, M8, M9, M10), the same object.
                "issues": [i.to_json() for i in
                           flow_doctor(graph,
                                       standards=config_store.cfg().standards,
                                       mount=hub.devices.get("telescope"),
                                       rig=rig)] + geometry_issues,
                "unmapped": unmapped,
                "readouts": run_readouts,
                "rig": rig_readout(rig)}

    @app.get("/api/flows", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_flows():
        """The card projection, never the graphs. A library of 30 flows at up
        to 400 nodes each is megabytes of wires to draw a card wall.

        FILES THIS BUILD CANNOT OPEN ARE LISTED TOO (#153), after the cards:
        one saved by a newer AstroDeck, one that does not parse, one that fails
        validation. Each is a read-only card carrying ``unreadable`` (a reason
        with no filesystem path in it). Skipping them made a damaged flow look
        deleted; every other route still answers 404 for them.

        ONE WALK OF THE DIRECTORY (``listing``). This used to call
        ``load_all`` and then ``unreadable``: every file parsed and validated
        twice per request, and the two halves of one response free to disagree
        about a file written between them."""
        records, rows = await asyncio.to_thread(flow_store.listing)
        return [r.card() for r in records] + rows

    @app.post("/api/flows", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def save_flow(body: FlowSaveBody):
        """Upsert. CAP_CONTROL_CAPTURE, not admin, matching POST /api/plans —
        an operator who may compose a sequence may compose the graph that
        compiles into one."""
        return await _persist_flow(body.flow)

    # ORDERING: every static /api/flows/<segment> route MUST be declared before
    # /api/flows/{flow_id}, or Starlette matches the parameterised route first
    # and "folders" arrives as a flow id — a 404 on a route that exists.
    @app.post("/api/flows/wizard",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def generate_flow_from_wizard(body: FlowWizardBody):
        """Three answers in, a saved flow out.

        The generator lives in ``flows/wizard.py`` and stays there. This route
        is the missing wire, not a second implementation: it validates the
        answers against the generator's own constants, calls
        ``generate_answer`` (the record and its notes), and persists through
        ``_persist_flow`` -- the same writer POST /api/flows and PUT use, so
        the four server-owned fields are re-derived here exactly as they are
        everywhere else, and the save rules are applied and reported.

        SAVED, not returned unsaved. The sheet's next act is to open the flow in
        the editor, and a generated graph the operator has to save by hand is a
        graph they can lose by closing a tab.

        CAP_CONTROL_CAPTURE matches POST /api/flows for the same reason it does
        there: somebody who may compose a graph may compose this one.

        THE MOSAIC KIND (#189 spec 1.8, #196) takes the grid and the angle as
        answers, and two RIG FACTS the route injects, never the client: the
        live camera field and the rotator (``_rig_facts``, the value the
        compile reads), and the angle the last solve measured
        (``hub.last_sky_angle``, ``status.sky_angle``'s PA) for USE MEASURED.
        With no optics the generator answers one target; the answer's
        ``notes`` say why (``wizard.NO_OPTICS_REASON``), as do any other
        notes it has (a name the catalogue does not know). A refusal of the
        generator's (a grid with another kind, one panel, no angle, "Rotate
        to PA" with no rotator) is a 422 naming it, not a 500.

        THE DOOR'S ANSWERS (#196, spec Revision 2 ruling 4, S6): the typed
        coordinates, the skipped panels, the filter rows and guiding, which
        Send to Flow Wizard pre-fills, are handed to the generator as they
        came, each None when the body does not carry it. A third rig fact
        goes with them: the connected wheel's usable slots (``_rig_wheel``,
        the reading POST /api/flows/quick checks its filters against, None
        for no wheel), read here on the event loop where the device map is,
        so a filter row the wheel does not have is refused as the quick
        flow refuses it. The camera field and the measured angle stay rig
        facts too; a client sends none of the three. A refusal of a door
        answer is the same 422, naming the answer.

        THE NIGHT AND RESUME ANSWERS (backlog WP-100, #196): ``stop``,
        ``stop_clock``, ``start``, ``start_clock``, ``min_alt`` and
        ``auto_resume``, which the sheet's NIGHT and RESUME steps ask, go to
        the generator as they came, each None when the body does not carry
        it, and write nothing then. A "Clock time" without its clock, a clock
        with no "Clock time" to belong to, and a stop of "None" are refused
        in the generator's words, the same 422.
        """
        sky = getattr(hub, "last_sky_angle", None)
        measured = sky.get("pa_deg") if isinstance(sky, dict) else None
        if not (isinstance(measured, (int, float))
                and not isinstance(measured, bool)
                and math.isfinite(measured)):
            measured = None
        try:
            answer = await asyncio.to_thread(
                flow_wizard.generate_answer,
                body.kind, body.options, body.target,
                body.unguided_exposure_s,
                rows=body.rows, cols=body.cols, overlap_pct=body.overlap_pct,
                angle_mode=body.angle_mode, pa_deg=body.pa_deg,
                use_measured=body.use_measured, skip=body.skip, ra=body.ra,
                dec=body.dec, cycle_plan=body.cycle_plan, cycles=body.cycles,
                guiding=body.guiding, stop=body.stop,
                stop_clock=body.stop_clock, start=body.start,
                start_clock=body.start_clock, min_alt=body.min_alt,
                auto_resume=body.auto_resume, rig=_rig_facts(),
                measured_pa_deg=measured, wheel=_rig_wheel())
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "invalid_wizard_answer"})
        out = await _persist_flow(answer.record)
        out["notes"] = list(answer.notes)
        return out

    def _rig_wheel() -> list[str] | None:
        """The connected wheel's usable slot names, or None when there is none.

        NONE IS NOT AN EMPTY LIST. None means "no wheel connected", and
        ``wizard.quick`` then validates against the assumed seven so a flow can
        still be built on a laptop with the rig switched off -- building a flow
        is a planning activity, and every shipped example is required to run on
        the simulator with no hardware. An empty list would mean "this wheel has
        no filters", which would refuse every name.

        BLACKOUT AND UNNAMED SLOTS ARE EXCLUDED, matching ``resolveWheel`` in
        ui/src/components/flows/cyclePlanRows.ts. An opaque slot passes no light,
        so a Light frame through it is a black frame with IMAGETYP=Light on it;
        a slot called "Slot 6" names nothing and makes a frame unfilable.
        """
        fw = hub.devices.get("filterwheel")
        if fw is None or not getattr(fw, "connected", False):
            return None
        names = list(getattr(fw, "filter_names", []) or [])
        usable = [n for i, n in enumerate(names)
                  if (n or "").strip()
                  and (n or "").strip().lower() != f"slot {i + 1}"
                  and not fw.is_opaque(i)]
        return usable or None

    @app.post("/api/flows/quick",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE)),
                            Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_CAPTURE, CAP_CONTROL_MOUNT,
             reaches={"SequenceEngine.start"})
    async def quick_flow(body: FlowQuickBody):
        """Pick a target, say how many subs, tick the filters, go.

        THE GRAPH IS THE PROVEN ONE. ``wizard.quick`` wraps the same
        ``generate()`` the guided sheet uses -- dusk window, target, slew +
        center, autofocus, guide, the capture stage, session report, with the
        relative HFR watchdog on it and a safety monitor that aborts and parks.
        There is no second generator, for the reason wizard.py's header gives.

        BOTH CAPS ARE ENFORCED, ``run`` or not. Invariant (3) in auth/rbac.py is
        that a route reaching ``SequenceEngine.start`` must be GATED on
        control.mount as a dependency, not merely labelled with it -- and this
        route reaches it, on the ``run`` path, through the very handler
        /api/flows/{id}/run uses. Making the gate conditional on a field of the
        body is exactly the shape that invariant exists to refuse. Nothing is
        lost by it in practice: the operator role holds control.mount alongside
        control.capture (auth/capabilities.py), and a viewer holds neither.

        THE RUN GOES THROUGH ``run_flow``, not through a copy of it. That
        handler applies its guards in a fixed order -- structural errors, the
        dome refusal, the unmapped list, repeated ids (#156), an unbounded
        quota, the horizon and the sun -- and its own docstring says a second
        start path that quietly omits one is how a guard stops being a guard.
        So this calls it.

        ``accept_unmapped`` IS TRUE, and only that. Every wizard-shaped graph
        carries the same list of node settings the compiler does not carry into
        the plan (slew tolerances, guide settle, report format), so a quick flow
        would 409 on every single run otherwise -- and the sheet has no canvas
        on which to show the operator what they would be accepting. The dome
        refusal is NOT waived by it, which is the point of that flag: a roof
        that will not close is never clickable-past.

        A REFUSED RUN STILL REPORTS THE SAVED FLOW. The save already happened
        and undoing it would throw away work the operator asked for; the id
        travels in the refusal so the sheet can offer to open it.
        """
        try:
            record = await asyncio.to_thread(
                flow_wizard.quick, body.target.model_dump(), body.subs,
                body.filters, body.exposures, body.guided, body.name,
                wheel=_rig_wheel())
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "invalid_quick_flow"})
        saved = await _persist_flow(record)
        if not body.run:
            return {"flow": saved, "started": False}
        try:
            started = await run_flow(saved["id"],
                                     FlowRunBody(accept_unmapped=True))
        except HTTPException as e:
            detail = e.detail
            if isinstance(detail, dict):
                detail = {**detail, "flow_id": saved["id"], "saved": True}
            else:
                detail = {"detail": str(detail), "flow_id": saved["id"],
                          "saved": True}
            raise HTTPException(e.status_code, detail=detail) from None
        return {"flow": saved, "started": True, "run": started}

    @app.get("/api/flows/folders", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def list_flow_folders():
        """``{name, count, readonly}`` rows. "My flows" and "Examples" are
        seeded even at count 0, and the sort rank lives in the store."""
        return await asyncio.to_thread(flow_store.folders)

    @app.post("/api/flows/folders",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rename_flow_folder(body: FlowFolderRenameBody):
        """Re-parent, not rename — a folder is a field on the record.

        THE STORE DOES NOT VALIDATE THE TARGET NAME. ``rename_folder`` writes
        it straight into each moved file's raw JSON, which runs no validators,
        so ``FlowRecord``'s own folder rule never sees the new name and a
        path-shaped string would be persisted into every moved record.
        Validated here THROUGH THE MODEL rather than against a second copy of
        the rule, so the two cannot drift.
        """
        try:
            FlowRecord(name="_", folder=body.new_name)
        except ValidationError:
            raise HTTPException(422, detail={
                "detail": "a folder name is 1-4 segments of letters, numbers, "
                          "spaces or dashes",
                "code": "invalid_folder"})
        try:
            moved = await asyncio.to_thread(
                flow_store.rename_folder, body.name, body.new_name)
        except ReadOnlyFlow as e:
            raise HTTPException(403, detail={"detail": str(e), "code": e.code})
        return {"moved": moved, "folder": body.new_name}

    @app.delete("/api/flows/folders/{name}",
                dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def delete_flow_folder(name: str):
        """Never deletes a flow — it re-parents them into My flows.

        Losing a night's automation because a folder was tidied away is not a
        trade anybody would choose. The destination is FIXED rather than a query
        param: it would reach ``rename_folder`` — and therefore the raw files —
        unvalidated, and one unvalidated path into that is enough. A caller who
        wants somewhere else can rename first.
        """
        try:
            moved = await asyncio.to_thread(flow_store.delete_folder, name)
        except ReadOnlyFlow as e:
            raise HTTPException(403, detail={"detail": str(e), "code": e.code})
        return {"moved": moved, "reparented_to": MY_FLOWS_FOLDER}

    @app.post("/api/flows/compile",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def compile_draft(body: FlowCompileBody):
        """The editor's live doctor: compile work in progress without saving it.

        Also static-before-parameterised, though only for symmetry — there is no
        POST /api/flows/{flow_id} for it to collide with today, and relying on
        that absence is how the next route added here breaks this one."""
        return await _compile_payload(body.graph or FlowGraph(), body.name or "")

    def _flow_progress_payload(rec: FlowRecord, flow_id: str,
                               camera_can_cool: bool, rig: RigFacts,
                               now: float) -> dict:
        """The progress answer for one stored flow. Synchronous, so the route
        can run all of it on a worker thread.

        THE STORED GRAPH, COMPILED THE WAY ``run_flow`` COMPILES IT:
        ``compile_plan(rec.graph, rec.name)`` and then ``to_sequence_plan``
        with the flow's own id and the run's own arguments. The ids are uuid5s
        of that id (spec 3.3), and the ledger counts frames by step id alone,
        so a compile on any other path names steps the ledger never heard of:
        every block reads "nothing banked" and every frame "orphaned".
        ``flow_progress`` refuses a plan it cannot account for, but a compile
        keyed on the wrong id is self-consistent and it cannot see that. The
        cooling and dome arguments shape plan-level fields this answer never
        reads, and they are passed anyway: an identical call cannot drift from
        the run's, and every ``to_sequence_plan`` call in this file is held to
        the run's arguments (test_flows_cooling.py and
        test_a_run_without_a_temperature_says_so.py parse them). They are all
        config reads except ``camera_can_cool``, which asks the connected
        camera and so is asked on the event loop, where the run and the
        preview ask it, and handed in. ``rig`` likewise (``_rig_facts``, the
        device map): it moves only M5's loss, which this answer never reads,
        and it is passed so the call is the run's call.

        NOT THE SESSION'S FROZEN PLAN. The card shows what the flow owes as it
        stands now; a step the operator has since changed or removed is a new
        step with nothing banked, and its old frames are orphaned, which are
        the numbers the next CONTINUE's dropped-steps question will quote.

        THE SESSION ``run_flow`` WOULD PICK: ``current_for_flow``, the
        flow's newest session by ``created_ts`` whatever became of it, and
        none when that newest one was abandoned (an abandoned session's files
        stay on disk, but the operator closed its ledger). The same call Run
        makes, so the chip can never name a session Run would not continue,
        nor count toward one it would leave: the two used to differ once a
        START OVER left an old dormant session behind (#189 hardening A2).

        THE SESSION ALSO SAYS WHAT AN AUTO-RESUME WOULD REPLAY (#473, S7
        orchestrator ruling 1): ``armed`` and ``plan_saved_ts``
        (``progress.replay_facts``), added here, beside the four keys
        ``flow_progress`` answers, so the editor can say "the armed session
        will replay the version from ...; press CONTINUE to apply your
        edits" (spec 5.9). ``plan_saved_ts`` is the SESSION'S, written when
        its plan was frozen, never this record's ``updated_ts``: that one
        moves with every save, and read here it would say the session holds
        the version on screen, which is the opposite of the notice's point.

        AND THE NIGHT A CONTINUE PRESSED NOW WOULD START (#511, H4):
        ``continue_night``, from ``now``, the clock the route read for this
        request and hands in. ``progress.continue_night`` asks
        ``Session.night_at``, the rule ``_continue_flow_session`` answers
        ``night`` by, so the button prints the run route's own number. Until
        H4 this answer carried no clock and the button added one to
        ``nights``, which on a night the session had already run read one
        night more than the run route answered. Only on a dormant session:
        only a dormant session is continued, and any other answers without
        the key.
        """
        compiled = compile_plan(rec.graph, rec.name)
        plan, _unmapped = to_sequence_plan(
            compiled, rec.graph, flow_id=flow_id,
            cool_to=getattr(config_store.cfg().cooling, "setpoint_c", None),
            camera_can_cool=camera_can_cool,
            closes_on_unsafe=bool(
                config_store.cfg().safety.close_dome_on_unsafe),
            rig=rig)
        session = session_store.current_for_flow(flow_id)
        out = flow_progress(compiled, plan, session, flow_id=flow_id)
        if session is not None:
            out["session"].update(replay_facts(session))
            # Present only on a dormant session, as ``locked_angle`` is only
            # where there is a lock: any other session answers as S7's did.
            night = continue_night(session, now)
            if night is not None:
                out["session"]["continue_night"] = night
        return out

    # ORDERING: declared with the static /api/flows/<segment> routes, before
    # GET /api/flows/{flow_id}. Starlette tries routes in declaration order,
    # and ``{flow_id}`` matches one path segment only, so today it cannot
    # swallow ``/<id>/progress`` the way it would swallow "folders". It stays
    # up here for the day that parameter widens to ``{flow_id:path}`` (an id
    # carrying a folder): declared below it, "<id>/progress" would then be read
    # as a flow id and 404'd, on a route that exists.
    @app.get("/api/flows/{flow_id}/progress",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_flow_progress(flow_id: str):
        """Per block, per panel and per step: subs banked and owed from the
        flow's newest session, plus the frames sitting on steps the flow no
        longer has (#189 S1 item 9; the shape is ``flow_progress``'s). The
        card's state chip, the modal's panel bars and the CONTINUE button's
        copy all read it.

        CAP_VIEW_STATUS, so a viewer can read it, and therefore NOTHING HERE
        MAY BE DERIVED FROM THE SITE (spec 6.9). No altitude, no transit, no
        setting time, not one value computed from a clock and the site: a
        key-name filter cannot withhold a value a route computes and names
        itself (#19), so the only safe answer is never to compute one.
        ``flow_progress`` takes no site, clock or config, and the keys it
        emits are held to an allow-list at the wire by
        tests/test_flows_progress_route.py, with the session's ``armed`` and
        ``plan_saved_ts`` (S7, #473): a status and a flag, and the moment an
        operator pressed Save, none of them from the site. Its ``nights``
        counts observing nights since S7 (#430), from the runs' own start
        stamps, never from the site. Since H4 the session also carries
        ``continue_night`` (#511): the night CONTINUE would start now, from
        the clock read here and the server's local noon-to-noon night key,
        a count and never a time, and none of it from the site; the allow-
        list and the site-move test walk it valued. Anything site-derived
        belongs on GET /api/flows/{flow_id}/tonight, which is
        CAP_VIEW_SITE_DERIVED.

        OFF THE EVENT LOOP: a compile, a scan of every session file on disk
        and a count, for each card of a library that asks for its chips.

        404 for an id ``flow_store.get`` does not answer, which includes a
        file the library lists as unreadable (#153): there is no graph to
        compile. 422 ``invalid_graph`` for a graph that cannot become a plan
        (``GraphNotRunnable``), the refusal and the words ``/run`` gives it.
        ``flow_progress``'s own ValueError (a plan it cannot account for) is
        deliberately NOT caught: this route compiles with the flow's id, so
        that refusal would be a defect here, never the operator's, and a 500
        says so where a mapped answer would pass for a verdict. A broader
        ``except ValueError`` would also swallow ``GraphNotRunnable`` and
        pydantic's ``ValidationError``, both subclasses of it.
        """
        try:
            rec = await asyncio.to_thread(flow_store.get, flow_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        try:
            # The request's clock, read here and handed in: the one clock
            # ``continue_night`` counts by (#511), read as the run route
            # reads its own, from this module's ``time``.
            return await asyncio.to_thread(_flow_progress_payload, rec, flow_id,
                                           _camera_can_cool(), _rig_facts(),
                                           time.time())
        except GraphNotRunnable as e:
            raise HTTPException(422, detail={"detail": str(e), "code": e.code})

    @app.get("/api/flows/{flow_id}", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_flow(flow_id: str):
        """Returns the RECORD OBJECT, deliberately.

        FlowEdge's source field is ``from_`` with ``alias="from"``, and FastAPI
        serialises response models by alias. Hand-building this with
        ``model_dump()`` instead would emit ``from_``, and every wire in the
        canvas would vanish with no error anywhere.

        WHAT A DORMANT SESSION KEEPS (#189 Revision 2, ruling 2). While a
        TARGET or POOL counts every sub taken, the read carries the store's
        ``counts`` note, and saving switches the flow. The session Run would
        continue does not switch with it: its ledger is counted by its frozen
        plan's ``count_mode`` until CONTINUE recounts it, which asks first
        (spec 5.9). So when that session (``current_for_flow``, the one Run
        and the progress chip both read) is dormant, the note says so too,
        in the ruling's words. The session store is asked only when the note
        is there, so a current flow's read costs what it did."""
        try:
            rec = await asyncio.to_thread(flow_store.get, flow_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        if any(n.key == "counts" for n in rec.migrated):
            latest = await asyncio.to_thread(session_store.current_for_flow,
                                             flow_id)
            if latest is not None and latest.status == "dormant":
                rec = rec.model_copy(update={"migrated": [
                    n.model_copy(update={
                        "note": f"{n.note} {COUNTS_DORMANT_ADDENDUM}"})
                    if n.key == "counts" else n for n in rec.migrated]})
        return rec

    @app.put("/api/flows/{flow_id}",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def put_flow(flow_id: str, body: FlowSaveBody):
        """The PATH id wins over the body id. A body that disagrees is either a
        stale client or an attempt to write elsewhere under cover of a legal
        path; either way the URL is what the caller asked for."""
        return await _persist_flow(body.flow.model_copy(update={"id": flow_id}))

    @app.delete("/api/flows/{flow_id}",
                dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def delete_flow(flow_id: str):
        try:
            removed = await asyncio.to_thread(flow_store.delete, flow_id)
        except ReadOnlyFlow as e:
            raise HTTPException(403, detail={"detail": str(e), "code": e.code})
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        if not removed:
            # delete() returns False for an absent file rather than raising, so
            # without this the route answers "deleted" for something it did not.
            raise HTTPException(404, detail={"code": "not_found"})
        return {"deleted": flow_id}

    @app.post("/api/flows/{flow_id}/compile",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def compile_flow(flow_id: str):
        try:
            rec = await asyncio.to_thread(flow_store.get, flow_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        return await _compile_payload(rec.graph, rec.name, flow_id=flow_id)

    @app.get("/api/flows/{flow_id}/tonight",
             dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def flow_tonight(flow_id: str):
        """CAP_VIEW_SITE_DERIVED, NOT CAP_VIEW_STATUS.

        Every value here — dark-window boundaries, altitude curves, transit
        times, the meridian-flip instant — is f(latitude, longitude). A key-name
        filter cannot withhold that, because the function carries the coordinate
        without carrying the key; an audit of this codebase recovered the
        observatory to 2.9 km from three viewer-legal requests. Same call as
        /api/framing/mosaic.

        THE GRAPH IS PASSED, NOT THE COMPILED DICT. Given a dict, resolve_tonight
        ignores ``name`` and the dawn story cannot mention the session report,
        because a report sink compiles to nothing and a plan alone cannot know
        one existed.

        Off the event loop: this runs one astropy ephemeris pass PER RESOLVED
        TARGET, so a four-member pool is four passes and the first call also pays
        the lazy astropy import.

        A MOSAIC READS TWO MORE THINGS (#189 S3 item 5). Its CAMPAIGN rows
        are per panel, from the flow's progress answer: the one
        ``GET /api/flows/{id}/progress`` gives, handed in as a callable that
        ``resolve_tonight`` calls only for a flow with a mosaic, on its
        worker thread. And its budget and brief add the hops, at the cost the
        engine MEASURED (``RigFacts.hop_cost_s``, None until a hop is timed,
        never the engine's seed). The device reads are made here, on the
        loop, and the values handed over, as the progress route does.

        THE BRIEF'S GUIDE SENTENCE READS THE SAME RIG FACTS (#506): the
        guider the run will use and its settle and dither, Rig > Guider's
        (``_guider_and_settle``), never the GUIDE card's.
        """
        try:
            rec = await asyncio.to_thread(flow_store.get, flow_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        can_cool, rig = _camera_can_cool(), _rig_facts()

        # THE LEDGER'S SUMMARIES, NOT THE REPORTS (#536, H4 orchestrator
        # ruling 6). Both folds read each report's per-filter accepted frames
        # and integration, per target and over the report, which is all a
        # summary holds (``report_summary``, written by ``finalize``). Loading
        # every report for them (#419's fix, after ``list_reports``' eight
        # scalars had folded to {}) made each open of the sheet read every
        # report in full twice, once to list it and once to load it. The
        # summaries reader (``SessionReporter.summaries``) opens no report
        # whose summary is current, and reads a report with none once,
        # writing its summary for the next request, so a report older than
        # the summaries still counts. A report that cannot be read is left out
        # and logged, as the list leaves it out. Read ONCE for both folds, and
        # only when ``resolve_tonight`` asks, on ITS worker thread: this runs
        # inside the to_thread below and never on the loop.
        @functools.cache
        def summaries() -> tuple:
            return tuple(SessionReporter.summaries())

        return await asyncio.to_thread(
            resolve_tonight, rec.graph, hub.site, name=rec.name,
            # BUDGET: this flow's own targets' hours (#536), the names its run
            # records frames under, a mosaic's by panel. M16's Ha is not
            # M31's progress, and the row says "for these targets". Kept
            # alongside banked_by_target below for a caller that only reads
            # ``banked`` (and for has_ledger when neither read succeeds).
            banked=lambda: banked_hours_from_reports(
                summaries(), targets=flow_target_names(rec.graph, rec.name)),
            # BUDGET, PER BLOCK (#562). The fold above answers one mapping for
            # the WHOLE flow, so a flow of several blocks sharing a filter had
            # every row read the flow's total rather than its own block's: an
            # M16 Ha row and an M31 Ha row in the same flow both filled with
            # M16's-plus-M31's Ha. This keeps the ledger's per-target
            # breakdown, so `_budget` can give each row only the hours of the
            # entries it stands for. Same cached `summaries()`, so this costs
            # no extra read: `SessionReporter.summaries` is read once either
            # way (`functools.cache` above), and this is a second, cheap fold
            # over the same in-memory tuple, not a second disk read.
            banked_by_target=lambda: banked_hours_by_target_from_reports(
                summaries()),
            # The CAMPAIGN tab's per-member progress. Same ledger, different
            # fold: BUDGET wants hours per filter over the flow's targets, a
            # campaign wants accepted frames per filter PER TARGET, because a
            # pool member is retired by its own quota and nobody else's.
            frames_by_target=lambda: frames_by_target_from_reports(
                summaries()),
            hop_cost_s=rig.hop_cost_s,
            rig=rig,
            # Tonight reads the progress answer's blocks and never its
            # session, so the clock handed in moves nothing it reads.
            progress=lambda: _flow_progress_payload(rec, flow_id, can_cool,
                                                    rig, time.time()))

    @app.post("/api/flows/{flow_id}/run",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def run_flow(flow_id: str, body: FlowRunBody):
        """Compile the stored graph and hand it to the engine.

        The guards below are the SAME ones /api/sequence/start applies, in the
        same order, because a second start path that quietly omits one is how a
        guard stops being a guard.

        THEN IT CONTINUES THE FLOW'S OWN SESSION (#189 S1, spec 5.9, D6), when
        the newest session this flow started is dormant: one ledger per flow,
        so night two banks on night one's step ids and ``Session.owed()``
        stays the only definition of finished. ``_continue_flow_session`` is
        the write-locked section that does it, and its docstring lists the
        three 409s (``adopt``, ``recount``, ``dropped_steps``) that ask before
        continuing changes what the ledger counts. ``fresh`` starts over.
        """
        try:
            rec = await asyncio.to_thread(flow_store.get, flow_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found"})
        # BEFORE ANY PRE-FLIGHT, fresh and CONTINUE alike (#323): a flow run
        # is a slew, and one started under a .ser recording took the mount
        # off the planet while the file kept writing. Not waived by force.
        _refuse_start_while_rig_is_held()

        structural = rec.graph.validation_errors()
        if structural:
            raise HTTPException(422, detail={
                "detail": "; ".join(structural), "code": "invalid_graph"})

        compiled = compile_plan(rec.graph, rec.name)
        try:
            # THE RIG'S STANDING SETPOINT BECOMES THE RUN'S. `cooling.setpoint_c`
            # is the operator's expressed intent (the same field the hub restores
            # on every camera connect, #153/#204); a flow has no cooling node, so
            # without this the night shoots at whatever the sensor drifted to and
            # no dark in the library matches it. None means no intent, and the
            # run then behaves exactly as it always has.
            #
            # `flow_id` makes the target and step ids deterministic (spec 3.3):
            # the same flow compiles to the same ids on every night, which is
            # what lets the continue below find last night's frames by step id.
            #
            # `rig`: the rig facts the preview compiled with (spec 3.3). A
            # mosaic framed for a field the camera now fitted cannot cover is
            # M5's LOSS, so it lands in `unmapped` and the gate below refuses
            # the run until the operator accepts it (spec 1.8).
            plan, unmapped = to_sequence_plan(
                compiled, rec.graph, flow_id=flow_id,
                cool_to=getattr(config_store.cfg().cooling, "setpoint_c", None),
                camera_can_cool=_camera_can_cool(),
                closes_on_unsafe=bool(
                    config_store.cfg().safety.close_dome_on_unsafe),
                rig=_rig_facts())
        except GraphNotRunnable as e:
            raise HTTPException(422, detail={"detail": str(e), "code": e.code})

        # (0) FAIL CLOSED ON THE DOME, and only on the dome. A DOME CONTROL node
        # compiles a real DomePolicy that SequencePlan has nowhere to put, so it
        # is dropped — and unlike a lost flat panel, that means a shutter which
        # was promised to close on unsafe and will not. NOT clearable by
        # accept_unmapped: nobody should be able to click past a roof.
        #
        # Gated on a dome being CONNECTED. With no dome attached there is no
        # roof to leave open, and refusing anyway would stop the shipped M16
        # example running on the simulator — which the handoff requires.
        dome_dev = hub.devices.get("dome")
        _safety = config_store.cfg().safety
        blocking = blocking_reasons(
            unmapped, dome_connected=bool(dome_dev is not None
                                          and dome_dev.connected),
            closes_on_unsafe=bool(_safety.close_dome_on_unsafe))
        if blocking:
            raise HTTPException(409, detail={
                "detail": "this flow's DOME CONTROL node cannot be honoured "
                          "yet — the plan carries no dome policy, and this "
                          "rig's safety settings do not close the roof on an "
                          "unsafe reading either, so nothing would shut the "
                          "shutter in the rain. Turn on 'close dome on unsafe' "
                          "in Settings > Safety (the Remote preset sets it), "
                          "or remove the dome node to run without a roof.",
                "code": "dome_unmapped", "unmapped": blocking})
        # `losses`, not `unmapped`: a note-level entry says the drawn thing IS
        # honoured elsewhere in the engine, so gating on it would make the
        # operator accept "parts of this flow do not survive the compile" about
        # parts that do. They stay in the 409 body when a real loss holds the
        # start, and in the /compile response either way.
        real = losses(unmapped)
        if real and not body.accept_unmapped:
            raise HTTPException(409, detail={
                "detail": "parts of this flow do not survive the compile",
                "code": "unmapped", "unmapped": unmapped})

        if not plan.targets or plan.total_frames() == 0:
            raise HTTPException(422, "plan has no frames")
        # The compile's ids are deterministic now (`flow_id` above, S1), so an
        # id collision in the identity scheme would arrive by this door, and
        # a continue must never start one: the ledger counts by step id alone.
        _refuse_plan_identity(plan, 422)
        if quota_unbounded(plan, resolve_policy(plan, config_store.cfg())):
            raise HTTPException(400, "count_mode=accepted with both reject "
                                     "guards disabled and no stop boundary can "
                                     "run unbounded — set a frame count, a stop "
                                     "time, or a reject guard")
        # The horizon (waived by force) and the Sun (never waived), the same
        # helper /api/sequence/start calls (spec 6.3): a mosaic group refuses
        # only when every panel is below the horizon, and the ones that are
        # below on a start that goes ahead are named once it has started.
        below_horizon = await _start_preflight(plan, force=body.force)
        if hub.looping:
            # Awaited, not fired: the preview loop must have released the camera
            # before the engine's first exposure.
            await hub.stop_loop_and_wait()
        # WHICH LEDGER, decided after every guard above so a refused run never
        # touches a session. ``current_for_flow``: the NEWEST session this
        # flow started, whatever became of it (none if it was abandoned), and
        # it is continued only if it is dormant. Not "the newest dormant one":
        # after a START OVER the old session stays dormant (unarmed) forever,
        # and once the new one completes or is abandoned that rule would
        # reopen the ledger the operator chose to leave. A complete newest
        # session starts fresh (spec 5.9; reopening one whose flow now owes
        # more is I-30). The progress chip calls the same method, so it names
        # the session this continues (#189 hardening A2).
        #
        # This read is before an await, so it is only a hint: the continue
        # re-reads the session under the write lock before it decides.
        latest = None
        if not body.fresh:
            latest = await asyncio.to_thread(
                session_store.current_for_flow, flow_id)
        # ADOPT'S CATALOGUE WORK, HERE AND NEVER IN THE LOCK (#249). The
        # match needs every target name resolved, and a body's position at
        # its capture instants: a full catalogue search each, 10 to 35 ms,
        # and some 700 ms for the first body of the process. Made inside the
        # write-locked section, that ran on the event loop holding the
        # store's lock, stalling the safety poller, the relay and every
        # route, and every worker-thread session write behind the lock. So
        # it is asked here, of the first read, on a worker thread, and only
        # when that read asks the ADOPT question at all. The locked section
        # re-reads and matches on this answer alone (``_unasked`` says what
        # it does about frames banked in between). Before the recovering
        # check, never after it: nothing may await between that check and
        # the start.
        evidence = None
        if (latest is not None and latest.status == "dormant"
                and _asks_adopt(latest, plan_replace_report(latest, plan))):
            evidence = await asyncio.to_thread(adopt_evidence, latest, plan)
        # THE VERSION THIS RUN FREEZES (#473, S7 orchestrator ruling 1): the
        # saved time of the record compiled above, read off that record and
        # never re-read: ``flow_store.get`` parsed it from disk for this
        # request alone, so a save that lands during the awaits above leaves
        # it the version the plan came from. None for a shipped Example: it
        # is never saved, and its ``updated_ts`` is only the moment it was
        # built for this read (FlowRecord's default), a time no one pressed
        # Save at, which the editor would then report as edits the session
        # lacks.
        plan_saved_ts = None if rec.readonly else rec.updated_ts
        continued: dict | None = None
        disarmed: list[dict] = []
        try:
            hub.require("camera")
            # Both branches, and nothing awaits between here and either
            # start: CONTINUE's locked section is synchronous too (#189 A7).
            _refuse_while_resume_recovers()
            if latest is not None and latest.status == "dormant":
                continued = _continue_flow_session(
                    latest, plan, body, evidence,
                    plan_saved_ts=plan_saved_ts)
            else:
                # Synchronous, and it owns its own task — do not await it, and
                # do not wrap it in a busy lane. "Already running" is raised in
                # here.
                disarmed = engine.start(plan, origin="flow", origin_id=flow_id)
                _freeze_saved_version(plan_saved_ts)
        except DeviceError as e:
            raise _err(e)
        if continued is None:
            # The session the engine just made, read back rather than reached
            # for inside the engine. "active" only: an older session of this
            # flow must never be reported as tonight's, so no match is None.
            made = await asyncio.to_thread(
                session_store.newest_for_flow, flow_id, ("active",))
            session_out = {"id": made.id if made is not None else None,
                           "night": 1, "continued": False, "kept": 0,
                           "new": sum(len(t.steps) for t in plan.targets),
                           "dropped": 0}
        else:
            session_out = continued
            # Lifted to the top of THIS route's own response (below), rather
            # than left nested under "session": #595/D-04 names one field,
            # not two different paths to the same answer depending on which
            # branch started the run.
            disarmed = session_out.pop("disarmed", [])

        # WHAT THIS READ REWROTE, SAID ON EVERY RUN UNTIL THE FLOW IS SAVED
        # (#150, spec 3.6). Only save() stamps FLOW_SCHEMA: `touch_run` below
        # edits last_run/last_result in the raw file and leaves its version
        # alone (carry-over 1), so an unsaved v2 flow reads v2 on every run and
        # every run says what that means. A run started from a list - SESSION
        # / NOW, the library's RUN verb, the wizard - never opens the editor
        # that otherwise says it, so without this line a v2 flow's 23.4 would
        # shoot at "any angle" night after night and the sentence saying so
        # would reach nobody. Only once the engine is going: a refused start
        # says nothing, and the next read (the editor, or the next run) will.
        for note in rec.migrated:
            bus.log("warning", f"flow '{rec.name}': {note.note}", "flow")

        # THE CARD SAID "NEVER RUN" FOREVER. `last_run` has been on FlowRecord
        # since the library shipped and the cards render it; nothing wrote it.
        # Stamped after the engine is already going, so a bookkeeping failure
        # can never turn a started run into a failed request.
        try:
            await asyncio.to_thread(flow_store.touch_run, flow_id,
                                    ts=time.time(), result="")
        except Exception as e:      # noqa: BLE001 - never fail a live run
            bus.log("warning", f"could not record the run on flow "
                               f"'{rec.name}': {e}", "flow")

        bus.log("info",
                f"flow '{rec.name}' started: {plan.total_frames()} frames"
                + (f", continuing its session on night "
                   f"{session_out['night']} ({session_out['kept']} step(s) "
                   f"carried over, {session_out['new']} new)"
                   if continued is not None else "")
                + (f" — {len(real)} graph feature(s) are not honoured by "
                   f"this run" if real else ""), "flow")
        _name_panels_below(plan, below_horizon)
        out = {"started": True, "flow_id": flow_id,
               "frames": plan.total_frames(), "unmapped": unmapped,
               "session": session_out}
        if below_horizon:
            # Absent when none is: every answer without a blocked panel is
            # byte-identical to before (spec 6.3).
            out["below_horizon"] = below_horizon
        if disarmed:
            # #595, D-04: same field, same place, whichever branch started
            # the run (fresh or CONTINUE).
            out["disarmed"] = disarmed
        return out

    # ------------------------------------------------ calibration library (PRO-1)

    @app.get("/api/calibration/health",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def calibration_health(flow_id: str | None = Query(None)):
        """The calibration matrix: what tonight needs against what we have.

        CAP_VIEW_STATUS matches GET /api/calibration/masters — a row carries
        exposure/gain/temp/filter/rotation and counts, nothing site-derived.

        THE ROWS ARE THE DEMAND, so with no flow named there is no demand and
        the answer is an empty list. ``planned: false`` is what says so:
        an empty matrix MUST NOT be drawn as healthy, it must say there are no
        lights planned yet.

        BOTH HOLES ARE NOW CLOSED, and the flags stay so a client can tell.

        ``counts_masters_only`` is False: ``CalibrationLibrary.iter_cal_headers``
        walks the capture root and ``have`` counts RAW SUBS, not only stacked
        masters. Before this the matrix read MISSING beside a folder holding
        hundreds of usable darks — a library browser that reported the opposite
        of the truth, which is worse than reporting nothing.

        ``assumed.temp_c`` is the rig's standing cooling setpoint instead of
        None, because the run now carries one (``to_sequence_plan(cool_to=…)``).
        It stays under ``assumed`` rather than moving out: the setpoint is what
        the night INTENDS to reach, and a sensor that never got there would make
        every temperature-matched row optimistic. Offset is still the shipped
        default — the node vocabulary has no offset param — and still says so.
        """
        needs: list[CalNeed] = []
        quota: int = DEFAULT_QUOTA
        # The night's intended sensor temperature. Darks are indexed by it, so a
        # matrix that matched on None would count a +20 °C dark as cover for a
        # -5 °C light.
        setpoint = getattr(config_store.cfg().cooling, "setpoint_c", None)
        if flow_id is not None:
            try:
                rec = await asyncio.to_thread(flow_store.get, flow_id)
            except KeyError:
                raise HTTPException(404, detail={"code": "not_found"})
            compiled = compile_plan(rec.graph, rec.name)
            seen: set[tuple] = set()
            for target in compiled.get("targets") or []:
                for step in target.get("steps") or []:
                    key = (step.get("exposure_s"), step.get("gain"),
                           step.get("binning"), step.get("filter"))
                    if key in seen or not step.get("exposure_s"):
                        continue
                    seen.add(key)
                    needs.append(CalNeed(LightNeed(
                        exposure_s=float(step.get("exposure_s") or 0),
                        gain=int(step.get("gain") or 0), offset=30,
                        temp_c=setpoint, binning=int(step.get("binning") or 1),
                        filter=str(step.get("filter") or ""))))
            cq = (compiled.get("automation") or {}).get("calibration_queue") or {}
            quota = int(cq.get("quota") or DEFAULT_QUOTA)
        c = config_store.cfg().calibration

        def scan_frames() -> list:
            # Passed as a CALLABLE so the capture-root walk happens inside
            # health_matrix, on the worker thread below — its own docstring asks
            # for exactly this. Frames that will not parse are already dropped by
            # the walk rather than sinking the whole matrix.
            out = []
            for path, header, ts in cal_library.iter_cal_headers():
                frame = frame_from_header(header, ts=ts, path=str(path))
                if frame is not None:
                    out.append(frame)
            return out

        rows = await asyncio.to_thread(
            health_matrix, needs, scan_frames, masters=cal_library.list_masters(),
            quota=quota, kinds=KIND_ORDER,
            tol=MatchTolerance(c.exposure_tol_pct, c.temp_tol_c))
        return {"rows": [r.to_json() for r in rows], "planned": bool(needs),
                "counts_masters_only": False,
                "assumed": {"offset": 30, "temp_c": setpoint}}

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
        # Every session, and a row with ``status: "unreadable"`` and the
        # store's reason for every file it cannot read (#242), which DELETE
        # removes. No ledger field on those: see ``_unreadable_row``. Such a
        # row says ``backup: true`` when a ``.bak`` sits beside the file,
        # which DELETE keeps (#266).
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
    async def resume_session(session_id: str,
                             body: ResumeBody | None = None):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        # First, as every start path does it (#323): a resume slews to the
        # session's next target, whatever the camera is doing.
        _refuse_start_while_rig_is_held()
        if s.status != "dormant":
            raise HTTPException(409, f"session is {s.status}, not dormant")
        # 409, not 422: the stored session is what conflicts, and it stays
        # listed and dormant for the operator to fix.
        _refuse_plan_identity(s.plan, 409)
        # Same unbounded accepted-quota guard as /api/sequence/start and
        # /api/sequence/recover (Task 4 review, IMPORTANT): resume starts the
        # engine on this same loop, so a session carrying the unbounded
        # combination (accepted mode, both reject guards off, no stop boundary)
        # must be refused here too — not just on the original start.
        if quota_unbounded(s.plan, resolve_policy(s.plan, config_store.cfg())):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
        # THE START'S SKY CHECKS, which a resume never ran (#291; spec 6.3,
        # 5.9 "every existing guard still applies"). The stored plan was
        # checked when it first started, which can be nights ago, so the sky
        # it was checked against is gone; and without this, resuming a
        # session was the way around the Sun check, the one that costs a
        # sensor rather than a night. The same helper and 409s as a start:
        # the Sun is never waived, the horizon is waived by ``force``, and a
        # group refuses only when every panel is below. Over the targets
        # the session still owes, not the whole plan (``_owed_plan``).
        owed = _owed_plan(s)
        below_horizon = await _start_preflight(owed,
                                               force=bool(body and body.force))
        try:
            hub.require("camera")
            _refuse_while_resume_recovers()          # no await until the start
            # A resume is a NEW run, so it re-reads the rig's standing setpoint
            # the same way a fresh start does -- but only if the stored plan has
            # no temperature at all. See replan_cooling for why the "only".
            disarmed = engine.start(replan_cooling(
                s.plan, config_store.cfg().cooling.setpoint_c), session=s)
        except DeviceError as e:
            raise _err(e)
        # Named once the engine is going, as a start names them: a refused
        # resume says nothing.
        _name_panels_below(owed, below_horizon)
        out = {"resumed": True, "remaining": sum(s.remaining().values())}
        if below_horizon:
            # Absent when none is, so every other answer is unchanged.
            out["below_horizon"] = below_horizon
        if disarmed:
            # #595, D-04: a resume arms this session, same singleton as a
            # fresh start, so it can disarm another just as silently.
            out["disarmed"] = disarmed
        return out

    @app.patch("/api/sessions/{session_id}",
               dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def patch_session(session_id: str, body: SessionPatchBody):
        """Load, check, mutate and save, ALL IN ONE LOCKED SECTION ON A
        WORKER THREAD (#167).

        It used to load, check and save through separate ``asyncio.to_thread``
        calls, each its own await, with nothing holding the gap between them.
        ResumeArm starts a dormant session on its own tick, inside its own
        ``session_store.write_locked()`` section (``ResumeArm.tick``), and a
        start landing in the gap between this route's load and its save left
        the save overwriting a run that had JUST begun: the frames it had
        already banked, and its ``active`` status, both went back to whatever
        this route's stale copy said. The request still answered 200, so
        nothing said the write had been lost.

        ONE SECTION, UNDER THE STORE'S LOCK, AND ON A WORKER THREAD rather
        than the event loop. A session can hold thousands of frames
        (``session_text``, #514), and reading and writing one is real file
        I/O; Run CONTINUE's own locked section (``run_flow``) can afford to
        run straight on the loop because it never touches a ledger that
        size, but a PATCH does. ``write_locked()``'s ``RLock`` is what closes
        the race either way: whichever side — this call, or ResumeArm's own
        section — asks for the lock first runs to completion, load through
        save, before the other is let in, so neither can read a copy the
        other is mid-way through changing. See ``SessionStore.write_locked``.

        Raising ``HTTPException`` from ``_locked`` crosses ``asyncio.
        to_thread`` back to this await exactly as it would from here
        directly, so every refusal below reads the same as it always did.
        """
        def _locked() -> dict:
            with session_store.write_locked():
                try:
                    s = session_store.load(session_id)
                except KeyError:
                    raise HTTPException(404, "session not found")
                merge = None
                if body.plan is not None:
                    # id-safe plan edit (spec §4): DORMANT only; running never
                    # editable. Read fresh, under the lock, so a start that
                    # landed since the last time anybody looked is the status
                    # this refusal sees, not one read before the lock.
                    if s.status != "dormant":
                        raise HTTPException(
                            409, "plan edits require a dormant session")
                    # The same kept/new/dropped rule Run CONTINUE applies,
                    # from one function so the two cannot drift. A PATCH
                    # reports and never refuses; the dropped-steps 409 is
                    # CONTINUE's alone.
                    merge = plan_replace_report(s, body.plan).merge()
                    s.plan = body.plan
                    s.name = body.plan.name or s.name
                    # A PLAN NO FLOW SAVE PRODUCED (#473): the session no
                    # longer holds the version ``plan_saved_ts`` names, and
                    # nothing here knows when this one was saved, so it says
                    # none rather than let the editor date a replay by the
                    # version it replaced.
                    s.plan_saved_ts = None
                if body.status is not None:
                    if body.status != "abandoned":
                        raise HTTPException(
                            422, "status can only be set to 'abandoned'")
                    if s.status == "active":
                        raise HTTPException(
                            409, "cannot abandon a running session")
                    s.status = "abandoned"
                    s.auto_resume = False
                disarmed: list[dict] = []
                if body.auto_resume is not None:
                    # ARMING AN ACTIVE SESSION IS THE POINT, NOT AN EDGE CASE.
                    #
                    # This was dormant-only, written for the feature's
                    # original purpose ("I have stopped for tonight, resume
                    # at dusk tomorrow"), where dormant is true by
                    # definition. But a restart destroys an ACTIVE session:
                    # boot_sweep then finds it dormant and UNARMED, and
                    # nobody is awake at 2am to arm it. So the run that most
                    # needed to come back was the exact run that could not be
                    # told to.
                    #
                    # Demonstrated on the rig 2026-08-02 by rebooting the
                    # observatory PC mid-sequence: the box auto-logged in,
                    # the server returned, every device reconnected and the
                    # boot sweep correctly rescued the run -- which then sat
                    # dormant and idle all night, because of this line.
                    #
                    # 'complete'/'abandoned' stay refused: there is nothing
                    # left to resume.
                    if body.auto_resume and s.status not in ("dormant",
                                                             "active"):
                        raise HTTPException(
                            409,
                            "auto-resume arms only dormant or active "
                            "sessions")
                    if body.auto_resume:
                        # server-enforced singleton (spec §5): arming here
                        # disarms others, read fresh under the same lock this
                        # whole section holds, so a session armed by another
                        # request in the gap cannot survive this one's write.
                        for other in session_store.load_all():
                            if other.id != s.id and other.auto_resume:
                                other.auto_resume = False
                                # A disarm like any other, so it stops a
                                # ladder that is recovering ``other`` (#220,
                                # below); the next tick then recovers the
                                # session armed here.
                                resume_arm.stop_recovery(
                                    "another session was armed in its place "
                                    "while the recovery ladder was working, "
                                    "so the ladder stopped before its next "
                                    "step",
                                    session_id=other.id)
                                session_store.save(other)
                                disarmed.append(
                                    {"id": other.id,
                                     "name": other.name or other.plan.name})
                    s.auto_resume = body.auto_resume
                if disarmed:
                    # NAMED, NOT SILENT (#595, backlog ruling D-04,
                    # owner-approved 2026-09-30). #595's own text: "the same
                    # applies to PATCH auto_resume" -- this route runs its
                    # own copy of the singleton `engine.start` disarms with
                    # (above), so it owes the same warning and the same
                    # `disarmed` field in its response, not left for a
                    # caller to notice only by re-reading /api/sessions.
                    names = ", ".join(d["name"] or d["id"] for d in disarmed)
                    bus.log("warning",
                            f"arming '{s.name or s.plan.name or s.id}' "
                            f"disarmed auto-resume for: {names}",
                            "sequence")
                # A DISARM STOPS THE LADDER RECOVERING THIS SESSION (#220).
                # It used to be read only after the ladder, by ResumeArm's
                # re-check, so the mount was solved and re-centred, minutes
                # of motion, for a session the operator had just withdrawn.
                # ``stop_recovery`` names this session, so disarming any
                # OTHER session leaves a running ladder alone, and it is
                # called after every refusal above (a refused request
                # changes nothing, the ladder included) and before the save,
                # inside the same locked section, so the ladder cannot take
                # a step between the request and the stop.
                if body.status == "abandoned" or body.auto_resume is False:
                    resume_arm.stop_recovery(
                        ("it was abandoned" if body.status == "abandoned"
                         else "it was disarmed")
                        + " while the recovery ladder was working, so the "
                          "ladder stopped before its next step",
                        session_id=s.id)
                session_store.save(s)
                out = {"id": s.id, "status": s.status,
                       "auto_resume": s.auto_resume,
                       "remaining": s.remaining()}
                if merge is not None:
                    out["merge"] = merge
                if disarmed:
                    # #595, D-04: present only when this PATCH actually
                    # disarmed another session, exactly as engine.start's own
                    # callers carry it (above), so a caller that arms a
                    # session here is told the same way a fresh run or
                    # CONTINUE would tell it.
                    out["disarmed"] = disarmed
                return out
        return await asyncio.to_thread(_locked)

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
        # A FILE THE STORE CANNOT READ IS DELETABLE (#242). GET /api/sessions
        # lists it as unreadable (not JSON, failing validation, or stating no
        # status), and this is the one way to remove it short of a shell on
        # the rig. ``load`` raises ``SessionUnreadable`` for it, which the
        # app-wide handler answers 500: loaded as this route's first step
        # with only ``KeyError`` caught, the delete never ran. It has no
        # status to refuse on, so ``s`` is None and the file's word is
        # silent; the engine's word below still decides whether it runs.
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        except SessionUnreadable:
            s = None
        if s is not None and s.status == "active":
            raise HTTPException(409, "cannot delete a running session")
        # Task 6 carry-in: natural completion does NOT drain thumb renders (only
        # abort() does), so a fire-and-forget render for THIS session may still
        # be mid-write — and its trailing session_store.save would RESURRECT the
        # JSON we are about to remove. Drain the matching renders before the
        # rmtree.
        await engine.drain_thumbs_for_session(session_id)
        # THE CHECK ABOVE IS A HINT; THIS ONE DECIDES (#212). The drain is an
        # await, and any starter - ResumeArm, /resume, Run CONTINUE - can take
        # the session inside it. The unlink used to follow in a worker thread
        # on the strength of the status read before the drain, so it removed
        # the file and thumbnails of a session that was now running, answered
        # 200, and the engine's next ledger write put the JSON back from
        # memory. So the session is read again, judged and unlinked in one
        # synchronous section under the store's write lock: nothing on the
        # loop can start it in between, and no worker-thread write can land
        # in the middle. The unlink runs on the loop for the same reason:
        # handed to a worker, its engine check would read state that a start
        # on the loop is halfway through changing. Measured on the dev box
        # (2026-09-24), removing a thumbs directory costs 17 ms at 170
        # frames and 53 ms at 600, once, on a delete the operator asked for.
        with session_store.write_locked():
            try:
                s = session_store.load(session_id)
            except KeyError:
                # Another delete landed during the drain.
                raise HTTPException(404, "session not found")
            except SessionUnreadable:
                s = None                # unreadable (#242): see the top
            # THE ENGINE'S WORD AS WELL AS THE FILE'S. They agree unless a
            # stale copy was saved over a running session's file, which is
            # exactly what the PATCH race does (#167: a dormant copy loaded
            # before an await, saved after a start). The file then says
            # dormant while the engine writes the ledger every frame. The
            # engine holds ``_session`` from ``start`` until the night is
            # finalized, so this asks for the run of THIS session only; a
            # dormant session is deletable while another one runs. For an
            # unreadable file it is the only word there is: the engine's
            # session came from a readable load, but a file damaged by hand
            # while it runs is still the file its next ledger write puts back.
            ours = getattr(engine, "_session", None)
            running_it = bool(engine.running and ours is not None
                              and ours.id == session_id)
            if running_it or (s is not None and s.status == "active"):
                raise HTTPException(409, "cannot delete a running session")
            if s is not None and s.status not in _DELETABLE_STATUSES:
                raise HTTPException(
                    409, f"cannot delete a session that is {s.status}")
            # DELETING THE SESSION THE LADDER IS RECOVERING STOPS THE LADDER
            # (#220): the same class as a disarm, decided so. A deleted
            # session can never be started - ResumeArm's re-check finds it
            # gone and stands down - so every step the ladder took after the
            # delete was a solve or a slew for nothing. Stopped only when the
            # delete goes through, and inside this section, so no step lands
            # between the unlink and the stop.
            resume_arm.stop_recovery(
                "it was deleted while the recovery ladder was working, so "
                "the ladder stopped before its next step",
                session_id=session_id)
            # session file + thumbs only — NEVER the FITS frames (spec §6).
            # AN UNREADABLE FILE'S BACKUP STAYS (#266). Its ``.bak`` is the
            # copy taken before an ADOPT, which for a ledger damaged since
            # can be the last good one, and the operator deleted the damaged
            # file, not that. ``s is None`` is exactly "unreadable" here: a
            # missing file answered 404 above. A readable session's backup
            # still goes with it, as ``delete`` documents.
            kept = session_store.delete(session_id, keep_backup=s is None)
        if kept is None:
            return {"deleted": session_id}
        # In words as well as a key, and naming the file, because nothing
        # lists a ``.bak``: once the row is gone, this answer is the last
        # thing that says the backup is there and what it is called. File
        # names only, never the path: the captures directory is the rig's
        # filesystem. "If ... intact", because nothing read the backup: the
        # delete keeps whatever ``.bak`` it finds.
        return {"deleted": session_id, "backup_kept": kept.name,
                "detail": (
                    f"Removed {kept.stem}, which could not be read. Its "
                    f"backup {kept.name} remains in the sessions folder, "
                    f"and so do the session's thumbnails: renaming the "
                    f"backup to {kept.stem} brings the session log back as it "
                    f"was when the backup was taken, if the backup itself "
                    f"is intact.")}

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

    # ---- per-session files index with grades (Session hub S5) --------------
    #
    # CAP_VIEW_PREVIEW, not CAP_CONTROL_MOUNT and not CAP_CONFIG_BACKEND. The
    # person who needs to know which subs were kept is the one watching the run,
    # and an operator holds neither config.backend nor anything else that would
    # let a stricter gate through -- so gating this like the ledger routes would
    # have put frame grades behind a capability the grader does not have.
    # view.preview is the right floor because that is exactly the disclosure:
    # per-frame quality plus a thumbnail URL, no pixels of the raw science
    # frame and NO PATH of any kind (see sequence/session_files.py).
    #
    # /current is declared FIRST and deliberately: FastAPI matches in
    # declaration order, so with the parameterised route ahead of it "current"
    # would bind as a session id and 404 as a missing session -- the failure
    # would look like a data problem rather than a routing one.

    @app.get("/api/sessions/current/files",
             dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def current_session_files():
        s = await asyncio.to_thread(active_session)
        if s is None:
            raise HTTPException(404, "no active session")
        return await asyncio.to_thread(files_index, s)

    @app.get("/api/sessions/{session_id}/files",
             dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def session_files(session_id: str):
        try:
            s = await asyncio.to_thread(session_store.load, session_id)
        except KeyError:
            raise HTTPException(404, "session not found")
        # One thread hop for the whole fold: it stats every frame on disk.
        return await asyncio.to_thread(files_index, s)

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
        if engine.owns_camera:
            # `owns_camera` and not `running`: a PAUSED run still has a live
            # task but no exposure in flight, and taking a frame is what an
            # operator pauses in order to do. See SequenceEngine.owns_camera.
            raise HTTPException(409, "a sequence is running")
        # A recording HOLDS the exposure guard for the whole file, so without
        # this the refusal still happens - inside the spawned task, after the
        # route has already answered 200. The operator sees a capture start
        # and no frame arrive.
        _refuse_if_camera_owned()
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("capture", hub.capture(
            body.exposure_s, body.gain, body.offset, body.binning,
            save=body.save, target=body.target, frame_type=body.frame_type,
            **({"request_id": body.request_id} if body.request_id else {})))

    @app.get("/api/capture/last",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def capture_last():
        """The unsaved frame the hub is holding, or an explicit "nothing held"
        (D-SES-4).

        ``view.status``, not ``view.preview``: this returns the frame's
        IDENTITY and its settings, never its pixels."""
        return hub.promotable_summary()

    @app.post("/api/capture/last/save",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def capture_last_save(body: PromoteBody | None = None):
        """Write the held unsaved frame to disk (D-SES-4).

        NOT RELAY-FENCED, and that is a decision rather than an omission. The
        fence exists for policy that decides what the rig does while nobody is
        standing next to it - the site, port protection, credentials. This
        writes one frame already in memory on this box to this box's own disk:
        it is science, exactly like ``POST /api/capture``, which is not fenced
        either. Fencing it would mean the answer to "keep that one" is yes at
        the scope and no from the sofa.

        Awaited rather than ``_spawn``-ed: the caller needs the path back, and
        the work is one already-decoded frame going to disk on a worker
        thread, not a lane that can run for minutes."""
        try:
            return await hub.promote_last_frame(
                target=(body.target if body is not None else ""),
                frame_id=(body.frame_id if body is not None else None))
        except PromoteRefused as e:
            # 404 nothing_to_promote / 409 already_saved / 409
            # frame_id_mismatch - the hub decides which, because only it can
            # tell "there was never a frame" from "you already saved it".
            raise HTTPException(e.status,
                                detail={"detail": e.detail, "code": e.code})

    @app.post("/api/capture/loop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def capture_loop(body: CaptureBody):
        if hub.polar.running:
            raise HTTPException(409, "polar alignment in progress")
        if engine.owns_camera:
            # `owns_camera` and not `running`: a PAUSED run still has a live
            # task but no exposure in flight, and taking a frame is what an
            # operator pauses in order to do. See SequenceEngine.owns_camera.
            raise HTTPException(409, "a sequence is running")
        # A recording HOLDS the exposure guard for the whole file, so without
        # this the refusal still happens - inside the spawned task, after the
        # route has already answered 200. The operator sees a capture start
        # and no frame arrive.
        _refuse_if_camera_owned()
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
        if engine.owns_camera:
            # `owns_camera` and not `running`: a PAUSED run still has a live
            # task but no exposure in flight, and taking a frame is what an
            # operator pauses in order to do. See SequenceEngine.owns_camera.
            raise HTTPException(409, "a sequence is running")
        # A recording HOLDS the exposure guard for the whole file, so without
        # this the refusal still happens - inside the spawned task, after the
        # route has already answered 200. The operator sees a capture start
        # and no frame arrive.
        _refuse_if_camera_owned()
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

    # ---- Session stack: the run's colour composite -------------------------
    # Live View above owns the CAMERA (it starts a loop); this owns nothing. It
    # is a switch on the sequence's own frames, so it never conflicts with a
    # running plan -- it needs one -- and there is deliberately no 409 here.
    @app.post("/api/sequence/stack/start",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def session_stack_start(backfill: bool = False):
        # `backfill=true` also folds in the subs this run has ALREADY accepted,
        # on a worker thread; the reply's `backfill` block is the progress
        # counter for it. Default false: it is minutes of disk and CPU on a full
        # night, so it is opt-in per press rather than per switch (Hub.
        # start_session_stack says why at length).
        return hub.start_session_stack(backfill=bool(backfill))

    @app.post("/api/sequence/stack/backfill",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def session_stack_backfill():
        # Separate from /start so an already-running stack can catch up without
        # being switched off and on again -- which would throw away the frames
        # it has stacked since, and is the obvious wrong way to reach this.
        return hub.session_stack_backfill()

    @app.post("/api/sequence/stack/stop",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def session_stack_stop():
        return hub.stop_session_stack()

    @app.post("/api/sequence/stack/reset",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def session_stack_reset():
        return hub.reset_session_stack()

    @app.get("/api/sequence/stack",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def session_stack_state():
        return hub.session_stack_status()

    # CAP_VIEW_PREVIEW, not CAP_VIEW_STATUS: this route returns PIXELS OF THE
    # SKY, which is the thing every other preview route is gated on. Counts and
    # integration time are status; an image is an image.
    @app.get("/api/sequence/stack/preview.jpg",
             dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def session_stack_preview(size: int = 1600, channel: str = ""):
        """The session stack as a JPEG: the composite, or ONE channel.

        ``?channel=Ha`` renders that channel's own running mean instead of
        the colour composite (#D-SES-1), so "show me just Ha" stops being a
        colour filter over the composite and becomes the Ha stack. The name
        may be the channel (``Ha``) or the operator's own filter name
        (``H-alpha``): it is resolved through the same fold the stacker used
        to decide which accumulator the frame went into, so the two cannot
        disagree, and the channel actually rendered comes back in
        ``X-Stack-Channel``.

        TWO DIFFERENT 404s, and the difference is the whole point. An empty
        stack is "nothing stacked yet"; a channel with no frames is "nothing
        stacked in that channel yet". One sentence for both would tell an
        operator whose night is going fine that their run had produced
        nothing."""
        capped = max(256, min(4096, int(size)))
        name = (channel or "").strip()
        if name:
            # ``hub.session_stack`` is public and the stacker owns the
            # resolution, so there is no hub wrapper to add here.
            got = await asyncio.to_thread(
                hub.session_stack.channel_preview, name, capped)
            if got is None:
                raise HTTPException(404, "nothing stacked in that channel yet")
        else:
            got = await asyncio.to_thread(hub.session_stack_preview, capped)
            if got is None:
                raise HTTPException(404, "nothing stacked yet")
        jpeg, meta = got
        # NOT cacheable: the same URL returns a different picture every time a
        # frame lands. `seq` is the client's change signal (from the status
        # route), and it is echoed here so a stale render is recognisable.
        #
        # ``X-Stack-Frames`` is THIS channel's own count on a channel
        # request (the stacker substitutes it), because a caption under one
        # filter's pixels reporting the night's total is the caption lying
        # about the picture it sits under. ``X-Stack-Channel`` is empty for
        # the composite, which is how a client tells the two renders apart
        # without re-reading its own request.
        return Response(jpeg, media_type="image/jpeg", headers={
            "Cache-Control": "no-store",
            "X-Stack-Seq": str(meta.get("seq", 0)),
            "X-Stack-Frames": str(meta.get("frames", 0)),
            "X-Stack-Channel": str(meta.get("channel", "") or ""),
        })

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

    # -------------------------------------------- frame settings, by PURPOSE
    # ONE home per (camera, purpose) pair — capture / focus / solve on the
    # imaging camera, guide on the guide camera — instead of one per SCREEN.
    #
    # The night this closes: the operator set FILT=R on the Align screen and
    # the Capture screen said Oiii. Both were internally honest — Align was
    # showing a server-side pin the polar session only acts on inside a solve
    # frame, Capture was showing the wheel — and neither said which question it
    # was answering. Every setting behind those screens had the same shape: a
    # private copy per surface, seeded from a hardcoded constant, that no other
    # surface could see and no reload could recover.
    #
    # The scopes stay SEPARATE on purpose. A guide camera's exposure is
    # legitimately not the imaging camera's, and a 0.3 s plate-solve frame is
    # not a light frame. Folding them would be as wrong as splitting them per
    # screen. What was missing was a name for which is which.

    def _require_scope_cap(scope: str, principal: Principal) -> None:
        """The cap a scope's WRITE needs, kept at the scope the operator is
        actually reaching for: solve settings drive an alignment (mount), guide
        settings drive the guider, the rest are imaging. Enforced here rather
        than by widening one route dependency, so unifying the transport did
        not quietly widen who can move the mount."""
        need = {"solve": CAP_CONTROL_MOUNT,
                "guide": CAP_CONTROL_GUIDE}.get(scope, CAP_CONTROL_CAPTURE)
        if not principal.has(need):
            raise HTTPException(403, f"{scope} frame settings need {need}")
        return

    @app.get("/api/camera/frame-settings",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def frame_settings_get():
        """Every scope's effective settings.

        The COLD-START read the old model never had. ``GET
        /api/polar/solve-settings`` existed and no client had ever called it,
        so after a reload the Align screen rendered mirrored defaults over a
        live server-side pin that would drive the wheel on the next run. The
        WS ``hello`` carries the same block (``hub.summary()``), so a client
        that connects normally does not need this at all — it is here for the
        cold GET and for scripted callers."""
        return {"frames": frames_payload(hub.guider)}

    @app.put("/api/camera/frame-settings",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS, CAP_CONTROL_CAPTURE, CAP_CONTROL_MOUNT,
             CAP_CONTROL_GUIDE)
    async def frame_settings_put(
            body: FrameSettingsBody,
            scope: str = Query(..., description="capture | focus | solve | guide"),
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Patch one scope. Accepted at ANY time, including mid-run: every
        consumer reads its scope per frame, so when solves start failing behind
        thin cloud the fix is a longer exposure NOW."""
        if scope not in FRAME_SCOPES:
            raise HTTPException(
                422, f"unknown scope {scope!r} — valid scopes are "
                     f"{', '.join(FRAME_SCOPES)}")
        _require_scope_cap(scope, principal)
        patch = body.model_dump(exclude_unset=True)
        if not patch:
            raise HTTPException(422, "nothing to set")
        # A pin naming a filter this wheel does not have is a pin that will
        # silently do nothing on the next frame — rejected at the door, where
        # the operator is still looking at the screen that sent it.
        if patch.get("filter"):
            fw = hub.devices.get("filterwheel")
            names = list(getattr(fw, "filter_names", []) or []) if fw else []
            if patch["filter"] not in names:
                have = ", ".join(names) if names else "no wheel connected"
                raise HTTPException(
                    422, f"no filter named {patch['filter']!r} ({have})")
        # LIVE FIRST, persist second — for the guide scope the running guider
        # can REFUSE (binning mid-session), and persisting before asking would
        # leave the file promising what the loop refused.
        if scope == "guide":
            live = {k: v for k, v in patch.items()
                    if k != "filter" and v is not None}
            g = hub.guider
            if live and g is not None and hasattr(g, "set_camera_settings"):
                try:
                    g.set_camera_settings(**live)
                except DeviceError as e:
                    raise HTTPException(409, str(e))
        try:
            eff = await asyncio.to_thread(set_frame_settings, scope, patch)
        except ValueError as e:
            raise HTTPException(422, str(e))
        frames = publish_frames(hub.guider)
        return {"scope": scope, "settings": eff, "frames": frames}

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
            # A human just moved this heater: the dew loop backs off for the
            # override window rather than overwriting it on the next tick.
            dew_controller.note_manual("camera")
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/camera/fan", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def camera_fan(body: FanBody):
        """Issue #22: set the hot-side fan (0-100%). Hot-side heat rejection is
        the dominant TEC failure mode, and ruling the fan out during the
        2026-09-12 cooler failure took poking config ids from a script."""
        try:
            cam = hub.require("camera")
            await cam.set_fan_power(body.power)
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

    async def _plain_goto(ra_hours: float, dec_deg: float) -> None:
        """Slew to an absolute J2000 target with NO centring pass.

        HOISTED out of the goto handler so a second route can spawn the same
        lane. It was a closure over ``body``; a nudge computes its own
        destination and has no body to close over, and copying the motion fence
        into a second handler is how two paths that must agree stop agreeing.
        """
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
            # Drop the field identification BEFORE the tube moves. The
            # pointing-delta catch-all in hub._current_field_solve would
            # normally notice, but it compares the mount's REPORTED
            # position -- and a mount that loses steps keeps reporting the
            # old one, which is this rig's actual AM5 failure mode. So the
            # explicit call is the primary and the delta is the backstop.
            hub.invalidate_field_solve("the mount is slewing to a new target")
            # A commanded move is what retires a plate-solved centre
            # (GN-07); the mount's own drifting report is not.
            hub.note_pointing_moved()
            await tel.slew(ra_hours, dec_deg)
        bus.publish("mount", action="slew_complete")

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

        return _spawn("goto", _plain_goto(body.ra_hours, body.dec_deg))

    @app.post("/api/mount/nudge",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.slew"})
    async def nudge(body: NudgeBody):
        """Move the tube by a RELATIVE offset on one axis (#D-RIG-4).

        A goto needs a destination; nudging needs an amount. "A bit further
        east" was only expressible as a jog - hold a direction for as long as
        you think - which is a stopwatch and a guess, and the deadman makes it
        a short one. This is the same intent as a number.

        NOT ``center=True``: a nudge is a small deliberate offset, and
        re-centring on a plate solve would undo the very thing that was asked
        for."""
        try:
            tel = hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        # #144: a nudge computes its destination by reading the CURRENT
        # position and adding an offset, so a driver that cannot vouch for
        # that position turns a small requested correction into a goto to
        # wherever the mount GUESSES it last was -- exactly the state right
        # after a reset. ``position_known`` defaults True (``getattr``, not a
        # required attribute): a driver, or a test double, that predates this
        # flag nudges exactly as it always has.
        if not getattr(tel, "position_known", True):
            raise HTTPException(409, detail={"detail": POSITION_UNKNOWN_DETAIL,
                                             "code": POSITION_UNKNOWN_CODE})
        try:
            arcmin = parse_nudge(body.axis, body.arcmin)
        except ValueError as e:
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "out_of_range"})
        try:
            cur_ra, cur_dec = await tel.get_position()
        except DeviceError as e:
            raise _err(e)
        # The mount reports its OWN frame; a real Alpaca mount reports JNOW.
        # Convert FIRST and slew J2000 - the frame every other target on this
        # server is in. Nudging in the mount's frame and slewing the answer as
        # J2000 would add a precession-sized error to EVERY tap.
        from_ra, from_dec = await hub.from_mount_frame(tel, cur_ra, cur_dec)
        moved = nudge_offset(from_ra, from_dec, body.axis, arcmin)
        to_ra, to_dec = moved.ra_hours, moved.dec_deg
        # The DESTINATION passes the same two gates a goto does. A nudge is
        # small, but "small" is exactly how a tube walks below a tree line or
        # into the solar cone one tap at a time.
        blocked = _horizon_block(to_ra, to_dec)
        if blocked is not None:
            raise HTTPException(409, detail=blocked)
        solar = _solar_block(to_ra, to_dec)
        if solar is not None:
            raise HTTPException(409, detail=solar)
        started = _spawn("goto", _plain_goto(to_ra, to_dec))
        return {**started,
                "from": {"ra_hours": from_ra, "dec_deg": from_dec},
                "to": {"ra_hours": to_ra, "dec_deg": to_dec},
                # The REQUESTED size, unchanged, and beside it what the geometry
                # could actually deliver. Both clamps in ``mount_offset`` are
                # silent and both bite near the pole - the exact place a nudge
                # is most used - so echoing only the request meant the pad said
                # "moved 600' east" for a move of nineteen, and the operator
                # waited for a field that was never going to arrive.
                "arcmin": arcmin,
                "clamped": moved.clamped,
                "achieved_arcmin": moved.achieved_arcmin}

    @app.get("/api/align/guide-offset",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def get_guide_offset():
        """The last measurement, or nulls before one has been taken.

        SEPARATE FROM THE POST because the measurement is two plate solves and
        takes about forty seconds -- past the point where a browser fetch is
        still waiting. The POST starts it in a lane; this collects it, and the
        same payload rides an `align` bus event for anything already listening.
        """
        return {"last": getattr(hub, "_last_guide_offset", None)}

    @app.post("/api/align/guide-offset/measure",
              dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Camera.expose"})
    async def measure_guide_offset(exposure_s: float = 4.0,
                                   guide_exposure_s: float = 4.0):
        """Solve both cameras where the mount is now and return the offset.

        CAP_CONTROL_MOUNT rather than a capture capability: this does not move
        the mount, but it takes the camera off whatever it was doing and it is
        the pointing model it feeds. The capability that governs pointing is
        the honest gate.
        """
        _refuse_if_camera_owned()
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("solve", hub.measure_guide_offset(
            exposure_s=exposure_s, guide_exposure_s=guide_exposure_s))

    @app.post("/api/mount/solve_sync", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.sync"})
    async def solve_sync():
        _refuse_if_camera_owned()
        try:
            hub.require("telescope"), hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("solve", hub.solve_and_sync())

    # Monotonic stamp of the last "position unknown" warning /api/mount/move
    # wrote (None before the first). A one-slot list, not a global: the rate
    # limit is about this app's log, and the route below is a closure.
    move_unknown_warned_at: list[float | None] = [None]

    @app.post("/api/mount/move", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.move_axis"})
    async def move_axis(body: MoveAxisBody):
        try:
            tel = hub.require("telescope")
            # Touch-safety rate clamp, now the DRIVER'S ceiling rather than a
            # constant. TOUCH_MAX_RATE_DEG_S (0.6) is what a mount that cannot
            # say gets; a driver that reports ``max_rate_deg_s`` gets its own
            # measured figure. On the AM5 that is 1.44 deg/s, which raises the
            # worst-case uncommanded travel at the 1.2 s deadman from 0.72 to
            # 1.73 degrees - a deliberate trade the owner made, and the number
            # the slew-pad copy states out loud. Gross repositioning is still
            # GOTO's job.
            ceiling = getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S
            rate = max(-ceiling, min(ceiling, body.rate_deg_s))
            # Sun-exclusion cone (W1.10) for manual jog: a non-zero move while the
            # mount is ALREADY pointed inside the cone would dwell/drive at the
            # Sun. Gate on the CURRENT pointing (best-effort: if we can't read the
            # position, fall through rather than block a stop). A rate-0 stop is
            # always allowed. force has no meaning here -- only a solar session
            # (solar_avoidance=False) makes _check_solar inert.
            check_solar = getattr(hub, "_check_solar", None)
            # #144: the cone is measured from where the mount SAYS it points,
            # and a driver that cannot vouch for that (an AM5 after a reset
            # reports its home position, the pole, wherever the tube is) turns
            # the check into a precise answer about nothing - a pass the
            # operator would trust, or a refusal with no cause. So it is not
            # run, and the response and one log line say so instead; the jog
            # itself still goes ahead, because it computes no destination (a
            # wrong position cannot send it to the wrong point in the sky) and
            # refusing it would take away the only way to drive a reset mount
            # home by eye.
            position_unknown = (rate != 0.0
                                and not getattr(tel, "position_known", True))
            if position_unknown:
                # ONCE A MINUTE: the hold-to-move pad re-asserts its rate about
                # every 600 ms (``KEEPALIVE_MS``) to feed the deadman, so a line
                # per post is a hundred identical lines a minute. No coordinates
                # in it (the home position is the pole, #140).
                now = time.monotonic()
                if (move_unknown_warned_at[0] is None
                        or now - move_unknown_warned_at[0] >= 60.0):
                    move_unknown_warned_at[0] = now
                    bus.log("warning",
                            "manual move: the mount's position is unknown, so "
                            "the solar-cone check was not run; watch the tube",
                            "mount")
            elif rate != 0.0 and callable(check_solar):
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
            # The pad reads this to know the move ran WITHOUT the cone check.
            # Absent on every other answer, so a client that predates it sees
            # exactly what it always saw.
            if position_unknown:
                return {"ok": True, "position_known": False}
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
        # slewing to the wrong target and never parked). The cross-lane refusal
        # is taken BEFORE the bump for that same reason — see
        # _refuse_if_lane_blocked.
        _refuse_if_lane_blocked("goto")
        hub.bump_motion_epoch()

        async def _park():
            tel = hub.require("telescope")
            async with hub._motion_lock:
                hub.invalidate_field_solve("the mount is parking")
                hub.note_pointing_moved()
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
        _refuse_if_lane_blocked("goto")     # before the bump; see park
        hub.bump_motion_epoch()

        async def _home():
            t = hub.require("telescope")
            async with hub._motion_lock:
                hub.invalidate_field_solve("the mount is homing")
                hub.note_pointing_moved()
                await t.find_home()
            bus.log("info", "mount homed", "mount")   # see park, above
        return _spawn("goto", _home(), replace=True)

    @app.post("/api/mount/unpark", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.unpark"})
    async def unpark():
        # Held off by a closing roof, on the SAME table as goto/park/home.
        # ``/api/dome/close``'s docstring justifies its interlock with "an
        # unpark+slew accepted in that window is how a tube meets a roof", and
        # the unpark half was not actually covered: this route never goes
        # through ``_spawn``, so nothing consulted the lane. Unpark is the choke
        # point — a parked mount refuses axis motion at the driver — so guarding
        # it is what makes that sentence true rather than aspirational.
        _refuse_if_lane_blocked("goto")
        try:
            tel = hub.require("telescope")
            hub.invalidate_field_solve("the mount was unparked")
            hub.note_pointing_moved()
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
        """``{connected, shutter, requires_park_before_close, can_bind}`` for the
        Settings→Safety roof widget. Mirrors ``/api/safety/state``: honest defaults
        when no dome is connected (v1 is sim-only — a real Alpaca/COM Dome client is
        a follow-up)."""
        dome = hub.devices.get("dome")
        if dome is None:
            return {"connected": False, "shutter": "unknown",
                    "requires_park_before_close": True, "can_bind": False}
        return {"connected": bool(getattr(dome, "connected", False)),
                "shutter": (await dome.shutter_state()).value,
                "requires_park_before_close": bool(dome.requires_park_before_close),
                "can_bind": bool(dome.can_bind)}

    @app.post("/api/dome/close", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"Dome.close_shutter", "Telescope.park"})
    async def dome_close():
        """Manual park-and-close: a motion-committing action (it moves the mount),
        so it reuses CAP_CONTROL_MOUNT (D4). Fence in-flight gotos, park under the
        motion lock, THEN close via the tested ordering guard (close_observatory),
        which re-confirms parked and REFUSES rather than crush the mount.

        Runs in its OWN "dome" lane. It used to run in "goto" — which was not
        laziness, it is genuinely mount motion — but that made the roof invisible
        as a roof: `busy_lanes` reported a slew, so "Close roof now" was dead
        during every unrelated goto and its disabled reason described somebody
        else's operation. The exclusion that made "goto" correct is preserved by
        `_LANE_SUPERSEDES` (see it): the close still cancels an in-flight slew,
        and a slew POSTed while the roof is travelling is now refused with a
        reason about the roof."""
        dome = hub.devices.get("dome")
        if dome is None or not getattr(dome, "connected", False):
            raise _err(DeviceError("no dome connected"))
        hub.bump_motion_epoch()

        async def _run():
            tel = hub.devices.get("telescope")
            async with hub._motion_lock:
                if (tel is not None and getattr(tel, "connected", False)
                        and getattr(dome, "requires_park_before_close", True)):
                    hub.invalidate_field_solve("the mount is parking for the roof")
                    hub.note_pointing_moved()
                    await tel.park()
                from ..sequence.roof import close_observatory
                return await close_observatory(dome, tel, log=bus.log)
        # replace=True is UNCHANGED behaviour for a second close arriving while
        # one is in flight (it supersedes it), just now against the dome lane
        # rather than the mount's.
        return _spawn("dome", _run(), replace=True)

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
        _refuse_if_camera_owned()
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
        _refuse_if_camera_owned()
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
        # `filter_offsets` is in this list because it IS a focus sweep — one per
        # slot — and Halt is the operator's panic button for the focuser. It
        # was missing, so the one focus operation that can run for a quarter of
        # an hour was the one Halt could not stop (2026-08-08). The dedicated
        # `/api/filterwheel/learn-offsets/cancel` route is the considered exit,
        # with the unwind wait; this is the two-taps-from-anywhere one.
        for name in ("focuser", "autofocus", "filter_offsets"):
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
        if engine.owns_camera:
            # `owns_camera` and not `running`: a PAUSED run still has a live
            # task but no exposure in flight, and taking a frame is what an
            # operator pauses in order to do. See SequenceEngine.owns_camera.
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
        # THE MOTION FENCE (#574, #589): read before the first await below,
        # the same discipline `Hub._approach_rotator`'s docstring asks of
        # every caller — a STOP landing in that read must still be caught by
        # the per-leg check inside `_approach_rotator_mechanical`.
        epoch = hub._motion_epoch
        mech = await rot.get_mechanical_position()
        # R-4 (#145, #626): the LEARNED sky/mechanical sign, anchored on the
        # rotator's last TRUSTED calibration rather than this instant's bare
        # reading (`_rotator_sync_anchor`) — a manual move has no solve of
        # its own to anchor on, unlike the rotate loop's calls into the same
        # pure math. Unmeasured defaults to +1, what every rotator before
        # R-4 assumed unconditionally; this route never refuses a manual
        # move over it the way the unattended `rotate_to_pa` loop does.
        sign = hub._effective_rotator_sign()
        anchor_mech, anchor_offset = hub._rotator_sync_anchor(rot, mech)
        target = map_sky_target(body.position_deg, anchor_mech, anchor_offset,
                                rcfg.range_type, rcfg.range_start_deg,
                                sky_sign=sign)
        adjusted = not angle_equals(target, mod360(body.position_deg), 0.1)
        mech_target = sky_to_mechanical(target, anchor_mech, anchor_offset,
                                        sign)
        # ONE-SIDED APPROACH (#526, H4 orchestrator ruling 3; #589 is this
        # route's own gap): every caller that moves the rotator now arrives
        # from the one side, exactly as the rotate loop does. Go and the ±1
        # degree nudges both post here (RotatorCard.tsx, RotatorPanel.tsx),
        # so both inherit it. ``direct`` (#594, WP-88) is the one caller that
        # must not: a measurement of whether the camera follows the rotator
        # sends the single mechanical move, through the same epoch-checked leg
        # loop. Passed only when set, so the default call is the one it was.
        moves = (hub._approach_rotator_mechanical(
                     rot, mech_target, mech, rcfg, epoch, direct=True)
                 if body.direct else
                 hub._approach_rotator_mechanical(
                     rot, mech_target, mech, rcfg, epoch))
        return _spawn("rotator", moves) | {
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

    @app.post("/api/rotator/sync-to-sky",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_sync_to_sky(body: RotatorSyncBody):
        """Measure the sky position angle and tell the rotator where it is.
        MOVES NOTHING. Before this the only way to establish the sky↔mechanical
        offset was to command a rotation (2026-08-07)."""
        _refuse_if_camera_owned()
        try:
            hub.require("rotator")
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("rotate_to_pa", hub.sync_rotator_to_sky(body.exposure_s))

    @app.post("/api/rotator/preflight",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_preflight():
        """TEST ROTATOR (WP-88; #145, #594): learn the sky/mechanical sign and
        check the camera follows the rotator, measuring only what this connect
        has not measured (``Hub.ensure_rotator_ready``).

        TURNS THE ROTATOR about 22 degrees and takes four plate solves, so it
        is refused up front, with nothing done, while a sequence or an
        exposure holds the camera: a run's frames would be ruined and its
        field mis-registered. A live loop is not refused, as for the other
        two solving routes: the calibrations yield the camera themselves.
        ``goto_and_center`` runs the same preflight by
        itself on the first rotating hop of a connect; this is the operator's
        way to run it ahead of time, at dusk, with the answer on the status
        block (``rotator.sky_sign``, ``rotator.trusted``). Runs on the
        ``rotate_to_pa`` lane, like the other two solving routes, so a second
        press while one runs is the lane's own 409. A failure is a log line;
        the result is the status block.

        A RECORDING IS NAMED FIRST. It holds the exposure guard for the whole
        file, so the busy test below would refuse it too, but with a
        sentence about a sequence or an exposure that is not what is
        running. ``_refuse_if_camera_owned`` answers with the recording's own
        code, as it does on every other route that takes the camera."""
        _refuse_if_camera_owned()
        if engine.running or hub._capture_lock.locked():
            raise HTTPException(
                409, "camera is busy (a sequence or an exposure is running); "
                     "rotator preflight refused")
        try:
            hub.require("rotator")
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("rotate_to_pa", hub.ensure_rotator_ready())

    @app.post("/api/rotator/rotate-to-pa",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def rotator_rotate_to_pa(body: RotateToPaBody):
        _refuse_if_camera_owned()
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
                                              body.opaque, body.narrowband,
                                              body.exposures, body.gains)
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
            binning=body.binning,
            narrowband=body.narrowband,
            nb_exposure_s=body.nb_exposure_s, nb_gain=body.nb_gain))

    @app.post("/api/filterwheel/learn-offsets/cancel",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def cancel_learn_filter_offsets():
        """Stop a per-filter offset run, and leave the focuser somewhere sane.

        THE LANE HAD NO WAY OUT. A learn-offsets run drives one full autofocus
        sweep per slot and could only be cleared by restarting the server —
        found on 2026-08-08 when a sweep re-exposed one unmeasurable position
        fourteen times, focuser stationary, the run unable to finish or fail.
        The retry bound (MAX_DROPS_PER_POSITION) stops that particular spin;
        this is the operator's answer to every other one.

        Same idiom as ``/api/focuser/halt`` and ``/api/rotator/halt`` — cancel
        the named lane, then halt the device — with one addition that matters
        here: it WAITS for the cancelled sweep to unwind before halting. Both
        autofocus paths restore the focuser to the position their sweep began
        at inside a shielded move (focus/autofocus.py, focus/native.py), and
        halting the focuser before that move has run would strand the drawtube
        at whatever mid-sweep position the cancel happened to land on, which is
        the thing this route exists to prevent. The wait is bounded, so a
        driver that will not answer cannot make the route hang; when the bound
        is hit the halt still fires and the answer says so.
        """
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        task = hub._busy.get("filter_offsets")
        if task is None or task.done():
            # Not an error: the run may have finished between the operator
            # deciding and pressing. Say what is true rather than 409-ing.
            return {"cancelled": False, "reason": "no filter-offsets run is in "
                                                  "flight",
                    "position": await foc.get_position()}
        task.cancel()
        # ``asyncio.wait`` and not ``wait_for(shield(task))``: since WP-59
        # (#252) a ``_spawn`` lane honestly ends ``cancelled()`` instead of
        # swallowing its own cancel, and awaiting it re-raised THAT
        # CancelledError here, past the old ``except Exception``, so the
        # route ended with no response at all. ``wait`` never raises the
        # task's outcome, never cancels the task when the bound passes (the
        # timeout abandons the WAIT and not the unwind: the sweep keeps
        # putting the focuser back after this answer), and a cancel of THIS
        # request still propagates out of it.
        done, _pending = await asyncio.wait({task},
                                            timeout=_LANE_UNWIND_TIMEOUT_S)
        settled = task in done
        await foc.halt()
        return {"cancelled": True, "settled": settled,
                "position": await foc.get_position()}

    # --------------------------------------------------------------- switch

    def _switch_profile_id() -> str | None:
        """Which profile's port settings apply. Ports are named per rig."""
        return config_store.cfg().active_profile_id

    def _dew_snapshot() -> dict | None:
        """The dew loop's last answer, or None. Never raises, never blocks.

        Read for ONE thing: whether the loop is currently driving the heaters,
        which is what decides if a ``follow_dew`` port's level is a weather
        reading (``redact._redact_switch_ports_for``). Through ``getattr`` and
        a bare except so a build without the controller, or one whose first
        tick has not happened, answers "not following" rather than failing a
        power-box readout."""
        try:
            ctrl = getattr(hub, "dew_controller", None)
            return ctrl.snapshot() if ctrl is not None else None
        except Exception:       # noqa: BLE001 - a readout never 500s on this
            return None

    def _switch_rows(ports, principal: Principal | None) -> list:
        """Annotated port rows, redacted for ``principal``. ONE builder for all
        three switch routes, so the GET and the two echoes cannot drift."""
        rows = [p.__dict__ for p in power_guard.annotate(
            ports, run_active=bool(getattr(hub.engine, "running", False)),
            profile_id=_switch_profile_id())]
        return _redact_switch_ports_for(rows, principal, _dew_snapshot())

    @app.get("/api/switch/ports")
    @declare(CAP_VIEW_STATUS)
    async def switch_ports(principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """The power box's ports, annotated with the protection policy (#D-RIG-5).

        ``protect_during_run`` is the STORED tri-state (null = nobody has
        decided, follow the name); ``protected_now`` is the effective answer
        already ANDed with a live run. The annotation is on COPIES - the driver
        reports a port's value, not who may change it.

        REDACTED, which is why it takes a principal. While the dew loop is
        following, the level on a ``follow_dew`` port IS the duty cycle the
        ramp computed from the dew margin - the same number ``_strip_dew``
        nulls on ``/api/status`` - so this route was the way to read it
        without ``view.weather``. The row stays, the level goes."""
        try:
            sw = hub.require("switch")
            ports = await sw.get_ports()
        except DeviceError as e:
            raise _err(e)
        return _switch_rows(ports, principal)

    @app.post("/api/switch/set")
    @declare(CAP_CONTROL_POWER)
    async def switch_set(body: SwitchBody,
                         principal: Principal = Depends(require(CAP_CONTROL_POWER))):
        """Set one port, unless a live run protects it (#D-RIG-5).

        THE REFUSAL COMES BEFORE THE WRITE. The lock used to be a regex in a
        React sheet, which meant curl could cut power to the mount mid-sequence
        and the server would do it without comment. A guard that refuses after
        the write has already cut the power is not a guard - and from the
        status code alone the two are indistinguishable.

        AND AN UNKNOWN PORT ID IS A 404, NOT A WRITE. ``target is None`` used to
        mean "no row to check", so the whole protection guard was SKIPPED and
        the write went to the driver anyway. That is the guard failing open on
        the one input it cannot reason about: a port id that is not in
        ``get_ports()`` is either a client built against a different power box
        or an off-by-one, and on a Pegasus UPB the neighbouring id is the mount.
        Refusing by NAME is the only safe reading, and the 404 says which id
        was not found."""
        try:
            sw = hub.require("switch")
            profile_id = _switch_profile_id()
            run_active = bool(getattr(hub.engine, "running", False))
            ports = await sw.get_ports()
            target = next((p for p in ports if p.id == body.port_id), None)
            if target is None:
                raise HTTPException(404, detail={
                    "detail": f"this power box has no port {body.port_id}",
                    "code": "unknown_port", "port_id": body.port_id})
            why = power_guard.refusal(target, run_active=run_active,
                                      profile_id=profile_id)
            if why is not None:
                raise HTTPException(409, detail={
                    "detail": why, "code": "port_protected",
                    "port_id": target.id, "port_name": target.name})
            await sw.set_port(body.port_id, body.value)
            # The dew loop has to know a human just touched a heater, so it
            # backs off for the override window instead of overwriting the
            # change on its next tick. Called UNCONDITIONALLY: the controller
            # filters out ports that do not follow the dew margin, and a second
            # copy of that decision here is a second copy to drift.
            dew_controller.note_manual("switch", body.port_id)
            return _switch_rows(await sw.get_ports(), principal)
        except (DeviceError, RuntimeError) as e:
            # HTTPException is NOT caught here on purpose: the 404/409 above
            # must reach the client with its structured body, not be re-wrapped.
            raise _err(e)

    @app.put("/api/switch/ports/{port_id}")
    @declare(CAP_CONFIG_SAFETY)
    async def switch_port_settings(
            port_id: int, body: SwitchPortSettingsBody,
            principal: Principal = Depends(require(CAP_CONFIG_SAFETY))):
        """Write one port's protection/dew settings (#D-RIG-5).

        ``config.safety``, not ``control.power``: this does not operate a port,
        it decides which ports the ENGINE refuses to let anyone operate during
        a run. The shipped operator holds neither, and that is the point - they
        can switch the ports, they cannot re-point what is protected.

        ABSENT MEANS UNCHANGED, read off ``model_fields_set``. The switch is
        required FIRST so a disconnected power box fails before anything
        persists.

        AND THE PORT HAS TO EXIST. Requiring the switch is not the same as
        requiring the PORT: an id no box reports used to persist a policy under
        itself and answer 200, so a client posting a stale or off-by-one id got
        a success for a setting that would never be read - and, because the
        store is keyed per profile per port id, the orphan row would come back
        to life the day a box with that many ports was plugged in. 404 with
        the id, before anything is written."""
        try:
            sw = hub.require("switch")
            ports = await sw.get_ports()
        except DeviceError as e:
            raise _err(e)
        if not any(getattr(p, "id", None) == port_id for p in ports):
            raise HTTPException(404, detail={
                "detail": f"this power box has no port {port_id}",
                "code": "unknown_port", "port_id": port_id})
        present = body.model_fields_set
        try:
            power_guard.set_port_settings(
                port_id,
                protect_during_run=(body.protect_during_run
                                    if "protect_during_run" in present
                                    else UNCHANGED),
                follow_dew=(body.follow_dew if "follow_dew" in present
                            else UNCHANGED),
                profile_id=_switch_profile_id())
        except ValueError as e:
            # A MACHINE CODE, like every other 4xx on this server. A bare string
            # detail is readable by a person and opaque to a client, which then
            # has nothing to branch on but the status - and 422 is also what a
            # schema rejection looks like.
            raise HTTPException(422, detail={"detail": str(e),
                                             "code": "invalid_port_setting",
                                             "port_id": port_id})
        return _switch_rows(ports, principal)

    # ------------------------------------------------------------------ dew

    @app.post("/api/dew/resume")
    @declare(CAP_CONTROL_POWER)
    async def dew_resume(
            principal: Principal = Depends(require(CAP_CONTROL_POWER))):
        """Clear the dew loop's manual override and start following again.

        THE WAY BACK, and there was not one. Any hand write to a heater - the
        camera dew slider, a switch port - pauses following for
        ``dew.manual_override_s``, and 0 means "until I say otherwise", which is
        infinity. There was no "I say otherwise": the only way back to following
        was a server restart, which on this rig is the next night, so the
        heaters held whatever the last hand write left them at through every
        change in the weather.

        ``control.power`` and not ``config.safety``: this does not change a
        policy, it hands a running heater back to the loop - the same authority
        as the write that took it. It is also the same cap as
        ``POST /api/switch/set``, which is where most overrides come from.

        IDEMPOTENT. Resuming a loop that is already following answers
        ``resumed: false`` with an empty ``cleared`` and the sentence saying so,
        not an error: a button whose job is "put it back" should be pressable
        whenever the operator is unsure, and a 409 there would be a refusal with
        nothing to fix.
        """
        cleared = dew_controller.resume("an operator asked for it")
        snap = _dew_snapshot()
        reason = ("dew following resumed on " + " and ".join(cleared)
                  + " - the loop re-commands the heaters on its next tick"
                  if cleared else "dew following was not paused")
        return {
            "resumed": bool(cleared),
            # The surfaces that WERE held, as phrases ("the camera window",
            # "switch port 3", "every switch port"). Equipment names, never
            # readings, so this list is safe for a principal without
            # view.weather.
            "cleared": cleared,
            "following": bool(snap.get("following")) if snap else True,
            "reason": reason,
            # The fresh snapshot, redacted like every other carrier of it, so a
            # caller can repaint without a second round trip. None before the
            # loop's first tick.
            "dew": (_redact_site_for({"dew": snap}, principal).get("dew")
                    if snap else None),
        }

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

    # ---------------------------------------------- guide-camera speed dials
    # The guide frame's own imaging settings (2026-08-07). Before this the
    # loop's exposure was a constructor-frozen 2.0 s no UI could reach — when
    # the guide star fades behind haze the fix is a longer exposure NOW, and
    # there was no dial to reach for (the same trap the polar solve settings
    # closed). The native guider reads these per exposure, so a PUT applies
    # from the next guide frame, mid-calibration or mid-guiding.

    @app.get("/api/guide/camera-settings",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def guide_camera_settings_get():
        # A delegate onto the ``guide`` scope (#176): the LIVE guider's values
        # when one is running, the persisted config otherwise. The literal
        # ``"offset": 30`` this used to return was #187 — a value applied to
        # every guide exposure, reported as a constant that was not necessarily
        # it, and settable by nothing.
        return frames_payload(hub.guider)["guide"]

    @app.put("/api/guide/camera-settings",
             dependencies=[Depends(require(CAP_CONTROL_GUIDE))])
    @declare(CAP_CONTROL_GUIDE)
    async def guide_camera_settings_put(body: GuideCameraSettingsBody):
        """A delegate onto the ``guide`` scope (#176) — same store, same
        announcement. Kept because clients and tests name this path."""
        settings = {k: v for k, v in body.model_dump().items() if v is not None}
        if not settings:
            raise HTTPException(422, "nothing to set")
        # LIVE FIRST, persist second. A binning change under an active session
        # is refused by the guider (the calibration was measured in the current
        # binning's pixels) — persisting before asking would leave the file
        # promising a binning the running guider refused, and the next
        # construction would apply it silently.
        g = hub.guider
        if g is not None and hasattr(g, "set_camera_settings"):
            try:
                g.set_camera_settings(**settings)
            except DeviceError as e:
                raise HTTPException(409, str(e))
        try:
            await asyncio.to_thread(set_frame_settings, "guide", settings)
        except ValueError as e:
            raise HTTPException(422, str(e))
        bus.publish("config", config=redacted(config_store.cfg()))
        return publish_frames(hub.guider)["guide"]

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
        # #141 (backlog WP-19(c)): ``model_dump`` fills in EVERY field,
        # ``count_mode`` included, so checking the rebuilt plan's own
        # ``model_fields_set`` below would always find it "set" -- the
        # client's own OMISSION only survives on ``body`` itself, read
        # before the dump (`_accepted_count_mode_if_omitted`'s own
        # docstring explains why the value alone cannot say this).
        dumped = body.model_dump(exclude={"force"})
        if "count_mode" not in body.model_fields_set:
            dumped["count_mode"] = "accepted"
        plan = SequencePlan.model_validate(dumped)
        if not plan.targets or plan.total_frames() == 0:
            raise HTTPException(422, "plan has no frames")
        # BEFORE ANY OF THE PRE-FLIGHT, because a plan start is a SLEW. A
        # planetary .ser is minutes of frames of one small ROI on one object,
        # and a run starting under it takes the mount away and leaves the
        # recorder writing empty sky for the rest of the file - with nothing
        # in either UI saying the two had met. ``force`` does not reach this:
        # it overrides the horizon pre-flight, not another lane's hardware.
        # The helper every start path calls (#323).
        _refuse_start_while_rig_is_held()
        # Repeated ids, or a rule naming a repeated target (#156). Not bypassed
        # by `force` either: it is the plan's shape, not tonight's sky.
        _refuse_plan_identity(plan, 422)
        # Unbounded accepted-quota guard (Task 4 review, IMPORTANT): the
        # accepted-mode capture loop (_run_step, spec §3) only terminates via an
        # accepted frame, a reject-guard trip, or a frozen stop boundary — the
        # no-progress watchdog only WARNs, never raises. When both reject
        # guards are disabled AND no target carries a stop boundary, a step
        # whose quota can never be satisfied (e.g. persistent clouds) would run
        # forever. Never bypassed by `force` (that flag only overrides the
        # horizon pre-flight below, not a structural configuration hazard).
        if quota_unbounded(plan, resolve_policy(plan, config_store.cfg())):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
        # Below-horizon pre-flight: refuse to start a run whose target can't be
        # observed from a *configured* site, unless explicitly forced; a mosaic
        # group refuses only when every panel is below it (spec 6.3). The
        # sun-exclusion pre-flight (W1.10) runs REGARDLESS of ``force`` -- a
        # forced run bypasses only the visible-horizon 409, never sun avoidance.
        # Disarming requires a solar session (config.solar_override), which makes
        # _check_solar inert. One helper, shared with /api/flows/{id}/run.
        below_horizon = await _start_preflight(plan, force=force)
        # Auto-stop the live preview loop before the run owns the camera (the
        # natural ASIAIR-style flow: frame with the loop, then hit Start Plan).
        # Awaited so the loop's in-flight expose fully releases the camera +
        # capture lock before the engine's first exposure.
        if hub.looping:
            await hub.stop_loop_and_wait()
        try:
            hub.require("camera")
            _refuse_while_resume_recovers()          # no await until the start
            # The OTHER start path. Stamped so a session can say which of the
            # two screens built it - the question "is the flow running?" had no
            # answer because both paths produced identical plans.
            disarmed = engine.start(plan, origin="plan")
        except DeviceError as e:
            raise _err(e)
        _name_panels_below(plan, below_horizon)
        out = {"started": True, "frames": plan.total_frames()}
        if below_horizon:
            # Absent when none is, so every other answer is unchanged.
            out["below_horizon"] = below_horizon
        if disarmed:
            # #595, D-04: named here instead of silent, same as every other
            # start route. Absent when nothing was armed.
            out["disarmed"] = disarmed
        return out

    @app.get("/api/sequence/resume-arm")
    @declare(CAP_VIEW_STATUS)
    async def sequence_resume_arm(
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Is a run armed and waiting, and what is holding it.

        The engine's own state cannot answer this: `_set_state` clears the
        session sub-block on every terminal transition, so a night that ended
        owing 58 frames leaves `sequence` as literally {"state": "idle"} and
        Monitor rendered "No run active - plan a session" over an armed
        session. Following that instruction is how you strand it, because a
        fresh start disarms every other session.

        `armed` is derived from the store (the same predicate ResumeArm uses);
        `hold` is the service's own current refusal, which existed only as a
        log line before this. view.status - it says nothing a status frame
        does not already carry.

        `recovering` and `recovery` say whether the recovery ladder is
        running, which step it is on and for which session (#220), and are
        false and null otherwise. The 409 `resume_recovering` sends the
        operator here, so a refused start can see what it is waiting for.
        The step is a WORD (`LADDER_STEPS`): this route is view.status, and
        an altitude or a mount position would hand a viewer the site (#140).
        Both are read after the store read's await, in one synchronous
        stretch, so they cannot disagree with each other.

        `hold.site_detail` is the numbers behind a start-floor or slew-limit
        hold (the target's altitude, its floor, the wait until it rises, the
        gate's azimuth), and a principal without view.site_derived does not
        get the key at all (#233; H3 orchestrator ruling 1 (spec, Still
        waiting on the owner, item 10)). The hold's `reason` is words, and a
        viewer reads it. `_redact_resume_arm_for` makes that one decision,
        and nothing else in the payload changes with the role.
        """
        armed = await asyncio.to_thread(session_store.armed)
        return _redact_resume_arm_for({
            "armed": ({"id": armed.id, "name": armed.name,
                       "owed": armed.owed(),
                       "accepted": armed.total_accepted(),
                       "total": armed.plan.total_frames(),
                       "origin": armed.origin, "origin_id": armed.origin_id}
                      if armed is not None else None),
            "hold": resume_arm.hold,
            "recovering": resume_arm.recovering,
            "recovery": resume_arm.recovery,
        }, principal)

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
        # ABORT STOPS THE RECOVERY LADDER TOO (#220, spec 6.15). After a
        # restart, ResumeArm's ladder solves and re-centres the mount with no
        # run behind it, so ``engine.abort`` had nothing to stop: the ladder
        # slewed on and started the session a moment later. It is stopped
        # here, before the first await, so it cannot take a step after the
        # press, and the session it was recovering is disarmed as Abort
        # disarms a running one, so the next tick does not restart it 60 s
        # later. A no-op when no ladder is running. ``engine.abort`` still
        # runs: the start routes refuse while the ladder runs, but a run can
        # still get in (the ladder's own ``_a_run_took_over`` exists for it),
        # and an abort must stop whichever of the two it finds.
        resume_arm.stop_recovery(
            "the operator pressed Abort while it was re-centring the mount",
            disarm=True)
        await engine.abort()
        return {"aborted": True}

    @app.get("/api/sequence/state")
    @declare(CAP_VIEW_STATUS)
    async def sequence_state(
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """The engine's state with liveness made to agree (`_sequence_envelope`).

        While a mosaic group waits on the meridian rule, `group.panel` and
        `group.pass` are withheld from a principal without view.site_derived
        (spec 5.10): the panel held for the crossing, and the hop that ends
        the wait, change at the moment a known RA transits, which is the
        longitude. `_redact_sequence_for` decides it, the same helper the WS
        `sequence` event and the monitor snapshot use."""
        return _redact_sequence_for(_sequence_envelope(engine), principal)

    # ----------------------------------------------------------------- monitor

    @app.get("/api/monitor/snapshot")
    @declare(CAP_VIEW_STATUS)
    async def monitor_snapshot(
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
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
        # The same sequence state GET /api/sequence/state serves, so the same
        # 5.10 withholding: without it a viewer's cold load would read the
        # panel a mosaic holds for the meridian that the route withholds.
        snap["sequence"] = _redact_sequence_for(_sequence_envelope(engine),
                                                principal)
        snap["polar"] = hub.polar.state | {"running": hub.polar.running}
        # SAME SEAM AS /api/status, and it was missing here. This route carries
        # a whole ``poll_status()`` under ``snap["status"]`` — site block,
        # mount alt/az and the meridian countdown included — one level deeper
        # than ``_redact_site_for`` looks, so a viewer's cold-load hydration
        # handed out the precise coordinates that every other surface strips.
        # Redact the nested payload, then the envelope, so the rule holds
        # wherever a future key puts a site block.
        if isinstance(snap.get("status"), dict):
            snap["status"] = _redact_site_for(snap["status"], principal)
        return _redact_site_for(snap, principal)

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

    def _never_rises_scan(ra_h: float, dec_deg: float, lat: float, lon: float,
                          start_ts: float | None, stop_ts: float | None,
                          floor_base: float,
                          horizon: list[tuple[float, float]] | None,
                          nogo_box: list[dict] | None) -> tuple[float, bool]:
        """Sample ``[start_ts, stop_ts]`` for the plan-wide 'never rises'
        warning (#619 b), asking ``schedule.effective_floor`` at EACH
        sample's OWN azimuth rather than one flat number.

        Before this, the warning compared the window's peak altitude
        against a single scalar floor -- exactly ``effective_floor`` with an
        empty horizon mask and no no-go wedges -- so a target hidden behind
        a drawn obstruction, or inside a wedge, for its whole window never
        triggered the warning unless the flat floor alone already caught it
        (#132 sibling: the mask is azimuth-dependent by design, and the
        target's track moves through azimuth as it moves through the sky,
        so only a per-sample check can see what a single peak-vs-floor
        comparison cannot).

        Same coarse 10-minute cadence as ``schedule.target_max_altitude``
        (the scalar scan this augments) -- fine enough for a non-blocking
        warning, not a slew gate; an open-ended window (``stop_ts`` None)
        is capped the same way, at one day ahead, so a transit is always
        captured. Returns ``(peak_alt, clears)``: ``peak_alt`` for the
        message (unchanged meaning), ``clears`` True from the first sample
        at or above ITS OWN azimuth's effective floor.
        """
        from ..catalog import altaz as _target_altaz
        t0 = start_ts if start_ts is not None else time.time()
        t1 = stop_ts if stop_ts is not None else t0 + 86400.0
        if t1 < t0:
            t0, t1 = t1, t0
        peak = -90.0
        clears = False
        steps = max(1, int((t1 - t0) / 600.0))
        for i in range(steps + 1):
            t = t0 + (t1 - t0) * (i / steps)
            alt, az = _target_altaz(ra_h, dec_deg, lat, lon, t)
            peak = max(peak, alt)
            if alt >= schedule_mod.effective_floor(floor_base, horizon, az,
                                                   nogo_box):
                clears = True
        return peak, clears

    @app.post("/api/sequence/preflight", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def sequence_preflight_plan(plan: SequencePlan):
        """Plan-wide, NON-BLOCKING pre-flight (§1.10 / C2-11). A DIFFERENT route
        from the GET single-target verdict above (same path, different verb — no
        collision). Resolves each non-calibration target's autorun window and
        warns ONLY when a target never rises above the effective floor across its
        whole window (``never_rises``). The UI shows a confirm dialog defaulting to
        "Run anyway"; this endpoint never refuses a run on its own.

        THE FLOOR IS THE EFFECTIVE ONE, AZIMUTH BY AZIMUTH (#619 b):
        ``max(per-target start gate, site horizon_min, safety floor)`` is the
        FLAT base, and ``_never_rises_scan`` asks ``schedule.effective_floor``
        -- the same formula the engine's slew gate enforces mid-run -- at
        every sampled point of the target's track, raising that base by the
        drawn obstruction-horizon mask (``cfg.safety.horizon``) and any
        no-go wedge (``cfg.safety.nogo_box``) at THAT point's azimuth.
        Before this, the comparison was peak altitude vs. the flat base
        alone, so a target hidden behind a horizon-mask obstruction (or
        inside a wedge) for its whole window was never reported as never
        rising unless the flat base alone already caught it. A default
        (un-configured) site yields no warnings: we don't trust an un-set
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
            if mon is None or not getattr(mon, "connected", False):
                # An ARMED safety config with nothing behind it used to produce a
                # clean, unqualified green here — the one omission this route's
                # own docstring calls worse than no screen. Blocking only when
                # the operator asked for enforcement; otherwise it is said, not
                # imposed, because the shipped default arms safety on a rig that
                # has no monitor.
                require = bool(cfg.escalation.require_safety_monitor)
                what = ("no safety monitor is assigned" if mon is None
                        else "the safety monitor is disconnected")
                warnings.append({
                    "target": "", "kind": "no_safety_source",
                    "blocking": require,
                    "message": (f"safety is armed but {what} — nothing will watch "
                                f"the weather for this run")})
            else:
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
            # #619 b: the floor used to be ONE scalar
            # (max(gate, site_floor, safety_floor)) compared against the
            # window's peak altitude -- exactly `schedule.effective_floor`
            # with an empty horizon mask and no no-go wedges, so a target
            # hidden behind a drawn obstruction (or inside a wedge) for its
            # WHOLE window never warned unless the flat floor alone already
            # caught it. The floor is azimuth-dependent by design
            # (`effective_floor`'s whole point) and a target's track moves
            # through azimuth as it moves through the sky, so only a
            # per-sample check -- `_never_rises_scan`, below -- can see a
            # mask or wedge that a single peak-vs-floor comparison cannot.
            horizon = cfg.safety.horizon
            nogo = cfg.safety.nogo_box
            for t in plan.targets:
                if t.calibration:
                    continue
                gate = float(getattr(t.schedule, "min_altitude_deg", 0.0) or 0.0)
                floor_base = max(gate, site_floor, safety_floor)
                if floor_base <= 0.0 and not horizon and not nogo:
                    continue    # no limit of any kind is configured
                start_ts, stop_ts = schedule_mod.resolve_window(
                    t.schedule, site, twilight, now)
                peak, clears = _never_rises_scan(
                    t.ra_hours, t.dec_deg, lat, lon, start_ts, stop_ts,
                    floor_base, horizon, nogo)
                if clears:
                    continue
                if peak >= floor_base:
                    # the FLAT floor alone would have let it in -- what
                    # actually keeps it down for the whole window is the
                    # obstruction mask or a no-go wedge at the azimuths it
                    # occupies (the exact gap #619 b reported: the old
                    # comparison could not see this at all).
                    message = (f"{t.name or 'target'} never clears the "
                               f"horizon mask or a no-go wedge during its "
                               f"window (peaks at {peak:.0f} deg; the "
                               f"open-sky floor there is {floor_base:g} deg)")
                else:
                    message = (f"{t.name or 'target'} never rises above "
                               f"{floor_base:g} deg during its window "
                               f"(peaks at {peak:.0f} deg)")
                warnings.append({
                    "target": t.name,
                    "kind": "never_rises",
                    "message": message,
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
        """The session RECOVER would resume, and why it went dormant.

        Re-backed on the session store (spec §2): a dormant session WITH
        frames is recoverable, whatever made it dormant, an operator's STOP
        included. Route path unchanged for UI compatibility.

        ``end_reason`` (#487) is why, from the session's last report
        (``_why_dormant``): the report's own word, ``RESTART_END_REASON`` for
        the traces only a process that stopped under the run leaves, or null
        when there is nothing to read. The recoverable card words its cause
        from this and nothing else; before #487 it said "The server
        restarted" after every ending, a STOP too.

        The report is read OFF THE LOOP: ``SessionReporter.read`` retries a
        refused read with sleeps (#370, #477). A read that raises (the ACL
        layer's refusal) is no report here, and the card still appears: a
        cause that cannot be read is a cause not stated, never a failed
        card."""
        s = session_store.recoverable()
        if s is None:
            return {"recoverable": False}
        last = None
        if s.nights:
            try:
                last = (await asyncio.to_thread(SessionReporter.read,
                                                s.nights[-1])).report
            except Exception:      # noqa: BLE001 - no cause, never no card
                last = None
        return {"recoverable": True, "session_id": s.id, "name": s.name,
                "frames_done": sum(s.done_map().values()),
                "frames_total": s.plan.total_frames(), "ts": s.updated_ts,
                "end_reason": _why_dormant(s, last)}

    @app.post("/api/sequence/recover", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"SequenceEngine.start"})
    async def sequence_recover(body: ResumeBody | None = None):
        s = session_store.recoverable()
        if s is None:
            raise HTTPException(404, "no resumable sequence found")
        # First, as every start path does it (#323): Recover is a resume by
        # another door, and a slew.
        _refuse_start_while_rig_is_held()
        _refuse_plan_identity(s.plan, 409)
        # Same unbounded accepted-quota guard as /api/sequence/start (Task 4
        # review, IMPORTANT) — resume starts the engine on this same loop, so a
        # dormant session carrying the unbounded combination must be refused
        # here too, not just on the original start.
        if quota_unbounded(s.plan, resolve_policy(s.plan, config_store.cfg())):
            raise HTTPException(
                400,
                "count_mode=accepted with both reject guards disabled and no "
                "stop boundary can run unbounded — set max_consecutive_rejects, "
                "max_consecutive_rejects_night, a stop time, or max_run_min")
        # The start's horizon and Sun pre-flight over what the session still
        # owes, exactly as /api/sessions/{id}/resume runs it (#291): this is
        # the same resume by another door, and a door without the Sun check
        # is the one a stale plan walks through.
        owed = _owed_plan(s)
        below_horizon = await _start_preflight(owed,
                                               force=bool(body and body.force))
        try:
            hub.require("camera")
            _refuse_while_resume_recovers()          # no await until the start
            # Same re-resolve as /api/sessions/{id}/resume -- the three entries
            # into a dormant session must not disagree about its temperature.
            disarmed = engine.start(replan_cooling(
                s.plan, config_store.cfg().cooling.setpoint_c), session=s)
        except DeviceError as e:
            raise _err(e)
        _name_panels_below(owed, below_horizon)      # once it has started
        out = {"resumed": True,
               "frames_remaining": sum(s.remaining().values())}
        if below_horizon:
            out["below_horizon"] = below_horizon     # absent when none is
        if disarmed:
            out["disarmed"] = disarmed               # #595, D-04
        return out

    # -------------------------------------------------------------- polar align

    from ..guided_recovery import GuidedCheckpoint
    guided_checkpoint = GuidedCheckpoint()

    async def _guided_checkpoint_state():
        from ..events import night_key
        cfg = config_store.cfg()
        status = await hub.poll_status()
        focus = bus.operation_snapshots.get("focus")
        polar = dict(hub.polar.state)
        busy = hub.busy_lanes()
        if focus and focus.get("state") == "running" and not any(lane in busy for lane in ("autofocus", "filter_offsets")):
            focus = {**focus, "state": "failed", "best": None,
                     "message": "The focus operation has stopped. Check the stars before trying again."}
        identity = {"night": night_key(), "site": cfg.site.model_dump(),
                    "horizon": cfg.safety.horizon, "providers": cfg.providers.model_dump(),
                    "profile": cfg.active_profile_id, "optics": hub.effective_optics(),
                    "devices": {role: {"instance": id(device), "description": device.describe()}
                                for role, device in hub.devices.items()},
                    "links": {link.get("role"): bool(link.get("connected")) for link in status.get("backend_links", [])},
                    "mode": hub.mode}
        position = (status.get("focuser") or {}).get("position")
        guided_checkpoint.observe(identity, focus, polar, focuser_position=position)
        return status, focus, polar, busy, position

    @app.get("/api/guided/checkpoint", dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def guided_checkpoint_get():
        _, focus, polar, busy, _ = await _guided_checkpoint_state()
        return {**guided_checkpoint.snapshot(), "focus": focus, "polar": polar, "busy": busy,
                "filter_offsets": bus.operation_snapshots.get("filter_offsets")}

    @app.post("/api/guided/checkpoint", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def guided_checkpoint_update(body: GuidedCheckpointBody):
        status, focus, polar, busy, position = await _guided_checkpoint_state()
        if body.context != guided_checkpoint.context or body.revision != guided_checkpoint.revision:
            raise HTTPException(409, "The setup changed. Review this step again before saving its check.")
        if body.action == "invalidate":
            guided_checkpoint.invalidate(body.fact)
        else:
            if body.fact in ("focus", "alignment") and any(lane in busy for lane in ("autofocus", "filter_offsets", "polar", "focuser")):
                raise HTTPException(409, "Wait for the current operation to finish before confirming this check.")
            from ..guided import simulated_equipment
            try:
                guided_checkpoint.complete(body.fact, focus, polar, focuser_position=position,
                    mount_slewing=bool((status.get("mount") or {}).get("slewing")),
                    simulated=simulated_equipment(hub))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return guided_checkpoint.snapshot()

    @app.get("/api/guided/polar-field", dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def guided_polar_field():
        from ..guided import polar_field, simulated_equipment
        from ..providers import resolve, _rig_has_real_motion
        cfg = config_store.cfg()
        provider = resolve("polar_align", hub)
        if provider.kind == "sim" and _rig_has_real_motion(hub):
            return {"field": None, "reason": "A simulated alignment cannot check this mount. Configure a real polar-alignment provider before continuing.", "config_version": cfg.version}
        if provider.kind not in ("astrodeck", "sim"):
            return {"field": None, "reason": "Guided field selection currently supports AstroDeck's polar alignment. Use Pro for this provider's setup.", "config_version": cfg.version}
        simulation = simulated_equipment(hub)
        result = await asyncio.to_thread(polar_field, dict(hub.site), cfg.safety, simulation=simulation)
        return {**result, "config_version": cfg.version, "simulation": simulation}

    @app.get("/api/guided/first-targets", dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
    @declare(CAP_VIEW_SITE_DERIVED)
    async def guided_first_targets():
        from ..guided import first_targets, simulated_equipment
        simulation = simulated_equipment(hub)
        result = await asyncio.to_thread(first_targets, dict(hub.site), config_store.cfg().safety, simulation=simulation)
        return {**result, "simulation": simulation}

    def _publish_polar_lane() -> None:
        """Put the polar session's OWN task in ``hub._busy`` under "polar".

        ``busy_lanes()`` — the list every control reads to answer "is MY
        operation still running" — is built from ``_busy``, and ``_busy`` is
        populated only by ``_spawn``. A polar session does not go through
        ``_spawn``: it owns its task so ``pause``/``resume``/``stop`` can drive
        it. So ``useBusy("polar")`` was false for the whole of a five-minute
        alignment, and PolarView had to infer liveness from the event stream
        (see its comment) — which cannot distinguish "still running" from "the
        socket dropped forty minutes ago".

        The SESSION'S REAL TASK is registered, not a wrapper around it, because
        that is what makes the lane mean something: it ends exactly when the
        driver ends, including when the driver is cancelled out from under us.

        Safe against ``_busy``'s other reader, ``hub._teardown``, which cancels
        everything in the map: it already awaits ``polar.stop()`` (hub.py:980)
        BEFORE that sweep, so the task is finished by the time the sweep reaches
        it and the cancel is a no-op. This is NOT the ``_spawn_connect`` hazard —
        a polar session never tears the rig down, so it can never be the task
        cancelling itself."""
        task = getattr(hub.polar, "_task", None)
        if task is not None:
            hub._busy["polar"] = task

    @app.put("/api/polar/solve-settings",
             dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_solve_settings(body: PolarSolveSettingsBody):
        """The native TPPA solve frame's imaging settings — exposure, gain,
        offset, binning, filter. Accepted at ANY time, including mid-run: the
        driver reads them per frame, so when solves start failing behind thin
        cloud the fix is a longer exposure NOW, not a restarted session. A null
        field clears back to its default. NINA/sim runs ignore these.

        A THIN DELEGATE onto the ``solve`` scope of
        ``PUT /api/camera/frame-settings`` since #176 (2026-08-08) — same store,
        same validation, same ``frames`` announcement. The path stays because
        clients and tests name it, and because "the solve frame's settings" is
        a true description of what it edits; what it no longer is, is a second
        place where those settings LIVE."""
        settings = body.model_dump(exclude_unset=True)
        if settings.get("filter"):
            fw = hub.devices.get("filterwheel")
            names = list(getattr(fw, "filter_names", []) or []) if fw else []
            if settings["filter"] not in names:
                have = ", ".join(names) if names else "no wheel connected"
                raise HTTPException(422, f"no filter named "
                                         f"{settings['filter']!r} ({have})")
        return {"solve_settings": hub.polar.set_solve_settings(**settings)}

    @app.get("/api/polar/solve-settings",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def polar_solve_settings_get():
        return {"solve_settings": hub.polar.solve_settings}

    @app.post("/api/polar/start", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT, reaches={"PolarSession.start"})
    async def polar_start():
        # BEFORE the session exists, not after: an alignment that begins while a
        # slew is still settling measures three positions the slew moved
        # between, and the circle fit reports a confident number computed from
        # them (measured 2026-08-06 — 4747' for a mount that was very nearly
        # aligned). The reverse direction, refusing a slew while this runs, is
        # the same table read the other way.
        _refuse_if_lane_blocked("polar")
        # ...and the camera, which polar alignment also takes: three plate
        # solves with a slew between each.
        _refuse_if_camera_owned()
        try:
            await hub.polar.start()
        except RuntimeError as e:
            raise _lane_409(str(e), code="lane_busy", lane="polar")
        _publish_polar_lane()
        return {"started": True, "source": hub.polar.state["source"]}

    @app.post("/api/polar/stop", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_stop():
        await hub.polar.stop()
        # Drop the finished task rather than leave it as a done entry: nothing
        # reads it (``_live_lanes`` filters done tasks) but ``_teardown`` would
        # cancel it, and a map that only ever grows is how a stale lane
        # eventually gets published by a future reader that forgets to filter.
        hub._busy.pop("polar", None)
        return {"ok": True}

    #: Pause/Resume with no session behind them. ``PolarSession.pause`` returns
    #: silently in that case, and it must keep doing so — publishing a pause
    #: state with no driver is what stranded the UI (#19). But a route that
    #: swallows the same call and answers 200 {"ok": true} is claiming an action
    #: happened, and every REST client (the UI's own included) reads that as
    #: "the mount is stopping". The refusal belongs at the door.
    #:
    #: SAFE FOR THE UI AS IT STANDS: both buttons are already
    #: `disabled={!canMount || !running || busy}` in PolarView.tsx, so this can
    #: only fire when the client's view of `running` is staler than the server's
    #: — and PolarView routes every failure through `run()`, which turns an
    #: ApiError into an error toast. Nothing throws uncaught, and nothing else
    #: in ui/src posts these two paths. STOP is deliberately NOT given the same
    #: treatment: it is a never-disabled HonestButton whose whole job is to be
    #: pressable when the user is unsure, and it already says "No alignment is
    #: running — nothing to stop" for itself.
    def _polar_live_or_409(action: str) -> None:
        if not hub.polar.running:
            raise _lane_409(
                f"No polar alignment is running — nothing to {action}.",
                code="not_running", lane="polar")

    @app.post("/api/polar/pause", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_pause():
        _polar_live_or_409("pause")
        await hub.polar.pause()
        return {"ok": True}

    @app.post("/api/polar/resume", dependencies=[Depends(require(CAP_CONTROL_MOUNT))])
    @declare(CAP_CONTROL_MOUNT)
    async def polar_resume():
        _polar_live_or_409("resume")
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
        second, a body deliberately not carried, the MOON withheld from a caller
        without ``view.site_derived``. Anything not sent here the browser has to
        guess, and its guess — "Planets aren't supported yet" — is the failure
        this whole search change exists to end.
        """
        from ..catalog import altaz, order_by_observability, round_az_deg
        # OFF the event loop. search() now evaluates astropy ephemerides inline:
        # ~6ms per planet, ~27ms for the Moon, and ~512ms on the first
        # solar-system query of the process (astropy import + IERS init). This
        # route is hit on a 250ms debounce from two search boxes, so run on the
        # loop it would stall the 2s status poll and the relay behind it — half a
        # second of frozen telemetry for one keystroke. Same offload the hub
        # already uses for detect_stars and measure_blob.
        #
        # THE MOON IS WITHHELD without view.site_derived, and it is withheld
        # INSIDE search() rather than filtered out of `found.rows` here. Its
        # topocentric RA/Dec moves ~1 degree with the observer (and 0.55" between
        # two sites 1.1 km apart), so the row IS the site — as are its
        # distance_km and size_arcmin, which is why no field-name filter can do
        # this job. The Atlas marker layer has gated the same body on the same
        # capability since it shipped (catalog/region.py _SITE_DERIVED_BODIES);
        # this is that gate, on the higher-precision half of the same oracle.
        # NO SITE IS TREATED EXACTLY LIKE NO PERMISSION TO KNOW IT (#24).
        # `hub.site` defaults to 0,0 with `is_default` True, so every alt/az
        # below would be computed for the Gulf of Guinea and the observability
        # ordering would sink whatever is genuinely up and float whatever is
        # not - silently, in well-formed rows.
        #
        # The catalogue still WORKS: name, type, magnitude and RA/Dec are
        # catalogue facts and do not depend on where the rig is, which is the
        # same line already drawn for a caller without `view.site_derived`
        # (owner's ruling, 2026-09-22: the catalogue should keep working
        # without exposing the location). Withholding the derived fields is
        # therefore a path this route already has, and an unset site takes it
        # rather than needing one of its own.
        sited = site_is_set(hub.site)
        derived_ok = principal.has(CAP_VIEW_SITE_DERIVED) and sited
        found = await asyncio.to_thread(search, q, 25, None, derived_ok)
        # alt/az ONLY for a holder of view.site_derived. Each row is
        # f(site, target), and the caller chooses the target — so a search box
        # is a coordinate oracle with as many samples as the caller cares to
        # type. The rows themselves (name, type, magnitude, RA/Dec) are catalog
        # facts and stay: a viewer can still see what is in the sky, just not
        # where the sky is being observed from.
        rows = found.rows
        if derived_ok:
            for r in rows:
                # A SATELLITE ARRIVES WITH ITS OWN alt/az and must keep it.
                # Its ra_hours/dec_deg are GEOCENTRIC - the direction from
                # the centre of the Earth - while its alt/az came from the
                # TOPOCENTRIC vector at this site. For a body 400 km up those
                # are not the same direction, so recomputing here would
                # overwrite the right answer with one tens of degrees out,
                # and the row would still look perfectly well-formed. Every
                # other kind is far enough away that the two coincide.
                if r.get("kind") == "satellite":
                    continue
                alt, az = altaz(r["ra_hours"], r["dec_deg"],
                                hub.site["latitude"], hub.site["longitude"])
                r["alt"] = round(alt, 1)
                r["az"] = round_az_deg(az)
            # WHAT IS UP, FIRST. Ordering purely by magnitude put the Large
            # Magellanic Cloud (-17 deg from this site) and the Carina Nebula
            # (-29) at the top of a GOTO list. A stable partition keeps the
            # search's relevance within each group and only sinks the ones that
            # cannot be pointed at. Only possible here, because alt is computed
            # above — `search` never sees it.
            #
            # A LOCAL, because SearchResult is a frozen dataclass. Assigning to
            # `found.rows` raised FrozenInstanceError on every site-derived
            # search — i.e. the catalog was 500ing for exactly the callers this
            # change was written for.
            rows = order_by_observability(rows)
        notes = list(found.notes)
        if not sited and principal.has(CAP_VIEW_SITE_DERIVED):
            # A holder whose numbers are missing is owed the reason, and
            # the reason is fixable in one screen. Without this the rows
            # look identical to a viewer's and the UI would have to guess
            # which of the two it was looking at.
            notes.append(
                "no observing site is saved, so altitude, azimuth and the "
                "what-is-up-first ordering are withheld rather than computed "
                "for latitude 0, longitude 0 - save the site in Settings")
        if explain:
            return {"results": rows, "notes": notes}
        return rows

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

        # AND NEITHER DOES A BAD SITE (#24). Unlike the search route, there is
        # no useful degraded form here: this route's entire output is
        # tonight's windows, transit altitudes and a ranking built from them,
        # all of which are f(site). At the 0,0 default it would rank the whole
        # catalogue for the Gulf of Guinea and every row would look
        # well-formed - and this is the list an operator picks targets off, so
        # a wrong answer is worse than no answer by the width of a night.
        if not site_is_set(hub.site):
            raise HTTPException(409, detail={
                "detail": "no observing site is saved, so tonight's windows "
                          "cannot be computed - save the site in Settings",
                "code": "no_site"})
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

    @app.get("/api/logs")
    @declare(CAP_VIEW_STATUS)
    async def logs(level: str | None = None, night: str | None = None,
                   limit: int = 0,
                   principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Event log rows, oldest first.

        Default (no params) = the in-memory ring, byte-identical to before.
        ``night=YYYY-MM-DD`` reads that night's PERSISTED file instead (UX #9 —
        the ring is only the last ~40 minutes of a ten-hour run), and ``level``
        filters either source. ``limit`` keeps the newest N rows.

        A line flagged ``site_derived`` (its moment was set by a site
        computation, spec 6.9) is left out of BOTH sources for a principal
        without view.site_derived, and left out before ``level`` and ``limit``
        are applied, so ``limit=N`` is the newest N lines this reader may see
        and cannot go short at the moment a flagged line lands. This route is
        view.status, which a viewer holds (#166)."""
        sees_timed = principal.has(CAP_VIEW_SITE_DERIVED)
        if night:
            store = bus.night_log
            rows = (await asyncio.to_thread(
                store.read, _slug(night), level=level,
                limit=(limit or LOG_READ_MAX),
                include_site_derived=sees_timed)) if store is not None else []
            return rows
        # A reader without view.site_derived reads the ring that no flagged
        # line enters (``EventBus._history_unflagged``), not the full ring
        # filtered: in the full ring a flagged line still evicts the oldest
        # row, and a viewer polling a full ring would see that row go at the
        # flagged line's moment. Filtered again all the same, so a flagged row
        # that ever reached that ring would still not be served.
        rows = (bus.log_history if sees_timed else
                _redact_log_rows_for(bus.log_history_unflagged, principal))
        if level:
            rows = [r for r in rows if (r.get("data") or {}).get("level") == level]
        if limit and limit > 0:
            rows = rows[-limit:]
        return rows

    @app.get("/api/logs/nights")
    @declare(CAP_VIEW_STATUS)
    async def log_nights(
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """``{current, nights:[{night,bytes}]}`` — which nights are on disk, so
        the log drawer can offer more than the live tail.

        For a principal without view.site_derived, the CURRENT night's
        ``bytes`` counts only its lines not flagged ``site_derived`` (spec
        6.9, #166). The file keeps every line, so its size grows at the moment
        a flagged line is written, and a viewer polling this view.status route
        would read that moment off the size while every row stayed withheld.
        The route's own ``current`` is the night asked about, so the two
        answers cannot name different nights."""
        store = bus.night_log
        current = night_key()
        hide = None if principal.has(CAP_VIEW_SITE_DERIVED) else current
        nights = (await asyncio.to_thread(store.nights,
                                          unflagged_bytes_for=hide)
                  if store is not None else [])
        return {"current": current, "persisted": store is not None,
                "nights": nights}

    @app.get("/api/logs/export")
    @declare(CAP_VIEW_STATUS)
    async def log_export(night: str | None = None, format: str = "txt",
                         principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Download one night's log. ``format=txt`` (default) is the readable
        transcript; ``jsonl`` is the raw rows. The filename is built from the
        SANITIZED night (never the raw param), like every other export route.

        Both formats leave out a ``site_derived`` line for a principal without
        view.site_derived, as ``GET /api/logs`` does (spec 6.9): the transcript
        prints each line's time, and that time is what the flag withholds.
        The file on disk is not changed; it keeps every line."""
        store = bus.night_log
        n = _slug(night or night_key())
        if store is None:
            raise HTTPException(404, "log persistence is disabled")
        sees_timed = principal.has(CAP_VIEW_SITE_DERIVED)
        if format == "jsonl":
            rows = await asyncio.to_thread(
                store.read, n, include_site_derived=sees_timed)
            body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
            media, ext = "application/x-ndjson", "jsonl"
        else:
            body = await asyncio.to_thread(
                store.export_text, n, include_site_derived=sees_timed)
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
                            paths: list[str] | None = None, snapshot: str = "", *,
                            path_limit: int = _GALLERY_SELECTION_MAX):
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
        if paths and len(paths) > path_limit:
            raise HTTPException(422, f"too many paths in one request ({len(paths)} > {path_limit}) — "
                                "name the set with the search and night filter instead")
        if snapshot:
            try:
                rows, truncated = await asyncio.to_thread(
                    gallery_listing.selected, snapshot, q, night_from, night_to, paths)
                return rows, [], truncated
            except gallery_listing.ListingExpired as e:
                raise HTTPException(409, str(e))
            except ValueError as e:
                raise HTTPException(422, str(e))
        if paths:
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
                             offset: int = 0, limit: int = 200, cursor: str = ""):
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
        _gallery_nights_ok(night_from, night_to)
        try:
            result = await asyncio.to_thread(gallery_listing.page, q=q, night_from=night_from,
                                             night_to=night_to, offset=offset, limit=limit, cursor=cursor)
        except gallery_listing.ListingExpired as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        except OSError:
            raise HTTPException(503, "Could not prepare the gallery listing. Check available temporary storage and try again.")
        return {**result, "scan_ms": round((time.monotonic() - t0) * 1000, 1)}

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
                              night_to: str = "", snapshot: str = "",
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
        rows, failed, _ = await _gallery_rows(q, night_from, night_to, path, snapshot)
        return {**gallery_module.summarize(rows), "failed": failed}

    @app.get("/api/gallery/thumb", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_thumb(path: str, w: int = 256):
        """Lazily-rendered, disk-cached JPEG thumbnail for one frame.

        422 (not 404) when the file exists but cannot be decoded, so the grid can
        draw a "no preview" tile that still lets the user download the frame —
        a frame we cannot render is not a frame that is missing."""
        try:
            jpeg = await gallery_module.thumbnail_async(path, width=w)
        except gallery_module.RenderBusy as e:
            raise HTTPException(503, str(e), headers={"Retry-After": "1"})
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

    @app.get("/api/gallery/view", dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
    @declare(CAP_VIEW_PREVIEW)
    async def gallery_view(path: str, w: int = 1280):
        """A saved frame rendered for LOOKING AT, sized to the caller's screen.

        Deliberately not `/api/gallery/thumb`: that route is the scanning grid
        and is clamped to 256px, and a viewer that inherited the tile's ceiling
        is exactly the confusion `test_preview_fidelity_is_not_the_gallery`
        exists to prevent. Same render and same disk cache, different ceiling.

        `w` is rounded UP to a `VIEW_WIDTH_STEPS` rung so a phone rotating, or a
        desktop window being dragged, lands on a handful of cache keys instead of
        re-rendering 26 megapixels per pixel of resize."""
        try:
            jpeg = await gallery_module.thumbnail_async(
                path, width=gallery_module.view_width_for(w), ceiling=gallery_module.VIEW_MAX_WIDTH)
        except gallery_module.RenderBusy as e:
            raise HTTPException(503, str(e), headers={"Retry-After": "1"})
        except KeyError:
            raise HTTPException(404, "frame not found")
        except FileNotFoundError:
            raise HTTPException(404, "frame not found")
        except ValueError as e:
            raise HTTPException(422, str(e))
        except OSError as e:
            raise HTTPException(404, f"frame not readable: {e}")
        return Response(jpeg, media_type="image/jpeg", headers=_PREVIEW_CACHE)

    @app.post("/api/gallery/thumbs/backfill",
              dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_thumbs_backfill(limit: int = 0):
        """Warm every listable frame's thumbnails, and report what it did.

        Gated at ``control.capture`` rather than ``view.preview``, which is what
        merely READING one of these needs. It discloses nothing new — the caller
        could already fetch every one of them one at a time — but it can occupy
        a core for twenty minutes on a large library, and spending the imaging
        machine's CPU is an operational act, not a browse.

        Runs in a worker thread and is idempotent: an already-warm library costs
        one stat per frame per width.

        BOUNDED BY TIME, so it can be called from anywhere. This awaited inline
        while promising "twenty minutes on a large library", and the relay gives
        up on an upstream after 30s — so from a phone, which is exactly where an
        operator reaches for it, it could only ever 504 (measured 2026-08-19).
        It now returns what it managed with `truncated` set, which the response
        contract already meant as "run again to continue"."""
        result = await asyncio.to_thread(
            gallery_module.backfill, limit=limit,
            budget_s=gallery_module.DEFAULT_BACKFILL_BUDGET_S)
        bus.log("info",
                f"gallery thumbnails: warmed {result['rendered']} across "
                f"{result['frames']} frames"
                + (f", {result['unrenderable']} could not be rendered"
                   if result["unrenderable"] else "")
                + (" (list truncated — run again to continue)"
                   if result.get("truncated") else ""),
                "gallery")
        return result

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
                               night_to: str = "", snapshot: str = "",
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
        rows, failed, _ = await _gallery_rows(q, night_from, night_to, path, snapshot)
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
        if body.snapshot:
            await _gallery_rows(body.q, body.night_from, body.night_to, body.paths, body.snapshot,
                                path_limit=50_000)
        return await asyncio.to_thread(gallery_module.trash_frames, body.paths)

    @app.get("/api/gallery/trash",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
    @declare(CAP_CONTROL_CAPTURE)
    async def gallery_trash_list(count_only: bool = False):
        """What is in the bin, when each item auto-purges, and whether it can
        still go back. Gated at ``control.capture`` with the rest of the trash
        surface: only someone who can delete needs to read the bin.

        ``count_only`` drops the rows. The gallery fetches this on EVERY open to
        put a number on the Trash tab, and on this rig that number cost 40 KB of
        listing over a WebSocket relay - the second-largest payload of a gallery
        page load, entirely to render "41". The panel itself still asks for the
        rows when it is actually opened.
        """
        data = await asyncio.to_thread(gallery_module.list_trash)
        if count_only and isinstance(data, dict):
            return {k: v for k, v in data.items() if k != "items"}
        return data

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

    # ------------------------------------------------------------------ sync

    @app.get("/api/sync/manifest", dependencies=[Depends(require(CAP_VIEW_MEDIA))])
    @declare(CAP_VIEW_MEDIA)
    async def sync_manifest(night: str = "", night_from: str = "",
                            night_to: str = ""):
        """What this rig currently holds, with a content hash per file.

        The other half of a sync is ``/api/gallery/file``, and this route is
        gated at ``view.media`` to MATCH it rather than at the looser
        ``view.preview`` the listing routes use. Two reasons, and the second is
        the real one. A manifest exists for exactly one purpose — to drive a
        bulk copy of the files that route gates — so a principal who cannot
        fetch the bytes has no use for their hashes. And keeping plan and
        perform behind ONE capability means there is a single decision to get
        right; two gates on halves of the same operation are two things that can
        drift, and only one of them gets the next review.

        ``night``/``night_from``/``night_to`` are the gallery's own vocabulary
        and reach the gallery's own filter, so "that one night" means the same
        set of frames here as it does on screen — including the noon rollover
        that keeps a 23:50 and a 00:10 frame together.

        FIRST CALL FOR A NIGHT IS SLOW ON PURPOSE. Nothing is hashed until it is
        asked for, so the first manifest over ~9 GB spends ~25 s reading the
        library; afterwards the (path, size, mtime) cache answers instantly and
        only genuinely-changed files are re-read. The work is on a worker
        thread, so a guiding loop and an exposure in flight never wait for it.
        The cache is in memory and is deliberately NOT persisted yet: a restart
        pays the read again, which is a known 25 s cost rather than a new file
        format to keep correct.
        """
        for value in (night, night_from, night_to):
            if value and not gallery_module.valid_night(value):
                raise HTTPException(422, "night must be YYYY-MM-DD")
        lo = night or night_from
        hi = night or night_to
        rows, truncated = await asyncio.to_thread(gallery_module.scan)
        rows = gallery_module.filter_rows(rows, night_from=lo, night_to=hi)
        # THE SAME builder the push runner uses (sync/manifest.py). Both
        # directions have to agree about what the library contains, and one
        # function is how that stays true.
        facts = sync_manifest_mod.rig_facts(rows)
        man = await asyncio.to_thread(
            sync_manifest_mod.build, facts,
            root=gallery_module.capture_root(), now=time.time(),
            cache=_SYNC_HASH_CACHE)
        out = man.to_json()
        out.update({
            "truncated": truncated,
            "settle_s": sync_manifest_mod.DEFAULT_SETTLE_S,
            "count": len(man.entries),
            "bytes": man.bytes,
        })
        return out

    # ----------------------------------------------------- identity / auth admin
    # W2.5 client seam + the admin.users-gated auth/remote/revoke surface. These
    # are the ONLY way to write AuthConfig (NEVER via POST /api/config, whose
    # ConfigPatchBody forbids auth/remote blocks).

    @app.get("/api/me", dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS, identity=True)
    async def whoami(principal: Principal = Depends(get_principal)):
        """The genuinely-resolved caller identity (W2.5). FAIL-CLOSED: a None
        resolution is a 401 (``get_principal``), never a default-admin. Under the
        open ``none`` provider this returns admin/ALL_CAPS as today; under a real
        provider it returns the caller's actual ``{role, email, caps}``.

        ``identity=True`` is belt and braces. RBAC invariant (4) — an
        identity-disclosing GET must carry an auth dependency — already covered
        this route through ``IDENTITY_PATHS``, a frozenset of literal paths. That
        is a coupling nothing enforces: rename the path here and the route keeps
        working, keeps disclosing who you are, and quietly stops being graded.
        The flag travels WITH the declaration, so the invariant survives the
        rename; the path membership stays as the second belt."""
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
        redacted block), so a method/TTL toggle never wipes a stored credential.

        THAT SENTENCE WAS A LIE FOR WEEKS. It described `_preserve_auth_secrets`
        — and this route never called it. The call was added to the SIBLING
        route (`/api/remote/config`) when the same bug was found there, and its
        docstring even says "'Exactly like /api/auth/config' was true of the
        sentence and not of the code" — while the route it was quoting stayed
        broken. The fixer patched the route that had visibly failed and left the
        one whose docstring already claimed the fix, which is the hardest place
        to look, because reading it tells you it is already handled.

        The cost was a full lockout of the rig, twice. Saving anything in the
        auth panel echoed the redacted block back, wiping `google_client_secret`
        (so Google resolved unconfigured) and `session_private_key` (so every
        live session died). With local not enabled and no break-glass token,
        that left no way to sign in at all."""
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

    @app.get("/api/remote/status")
    @declare(CAP_VIEW_STATUS)
    async def get_remote_status(
            request: Request,
            principal: Principal = Depends(require(CAP_VIEW_STATUS))):
        """Is the relay tunnel up, and did THIS request come through it?

        The read half of the W3 relay seam: ``POST /api/remote/config`` writes
        the knobs and nothing could ever read back whether the dial-out was
        actually connected, so a "relay: connected" badge had nothing to poll.

        ``via`` answers a different question from ``connected``: it is how the
        request in your hand arrived (the ASGI scope flag the relay client
        stamps, never a header), so a LAN browser sees ``direct`` while the
        tunnel is up and a remote browser sees ``relay``.

        CARRIES NO SECRET. ``relay_host`` is the HOSTNAME parsed out of
        ``relay_url`` -- never the url (which can carry userinfo credentials)
        and never ``device_token``, which is the credential that registers this
        home with the relay and is scrubbed everywhere else it appears
        (``config.redacted``). view.status, because a viewer who is looking at
        the rig through the relay is exactly the caller who needs to know the
        link is up."""
        remote_cfg = config_store.cfg().remote
        try:
            from ..remote.relay_client import relay_status
            st = relay_status()
        except Exception:  # noqa: BLE001 - a missing/failed module reads as "not running"
            st = {"connected": False, "last_error": None, "since_unix": None,
                  "gen": None}
        host = None
        try:
            raw = (remote_cfg.relay_url or "").strip()
            if raw:
                host = urlsplit(raw).hostname or None
        except Exception:  # noqa: BLE001 - an unparsable url is simply not shown
            host = None
        return {
            "enabled": bool(remote_cfg.enabled),
            "home_id": remote_cfg.home_id or None,
            "relay_host": host,
            "connected": bool(st.get("connected")),
            "last_error": st.get("last_error"),
            "since_unix": st.get("since_unix"),
            "gen": st.get("gen"),
            "via": "relay" if _scope_is_remote(request) else "direct",
        }

    @app.post("/api/remote/config", dependencies=[Depends(require(CAP_ADMIN_USERS))])
    @declare(CAP_ADMIN_USERS)
    async def set_remote_config(auth: AuthConfig):
        """W3 relay-config seam (admin.users-gated). The relay/remote knobs live on
        the same ``AuthConfig`` (relay_pubkey / viewer_link_pubkey); this dedicated
        admin route exists now so the relay lane never has to touch ``app.py``.
        Today it persists AuthConfig exactly like ``/api/auth/config``.

        AND THAT INCLUDES THE SECRET-PRESERVING STEP, which it was missing. The
        two routes take the same whole ``AuthConfig``, and the block the UI
        holds has every secret scrubbed to "" — so saving the relay panel wrote
        those blanks straight over the stored values, silently wiping the Google
        client secret and the session signing key. Wiping the signing key
        invalidates every live session, which is a strange thing to have happen
        because you pressed Save on a relay setting. "Exactly like
        /api/auth/config" was true of the sentence and not of the code."""
        auth = _preserve_auth_secrets(auth)
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

    @app.post("/api/auth/ws-ticket",
              dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def mint_ws_ticket():
        """Exchange the caller's ALREADY-authenticated request (session cookie or
        X-Auth-Token header -- never a query string) for a single-use, short-TTL
        ticket to authenticate the /ws upgrade (OPEN-011).

        A browser cannot set an Authorization header on a WebSocket, so without
        this a shared token would have to ride ``?token=`` and leak into history,
        telemetry, and proxy logs. The ticket is one-time and expires in seconds,
        so a leaked one is inert; RBAC is still re-resolved and re-checked on the
        socket itself."""
        from ..auth import ws_ticket
        return {"ticket": ws_ticket.issue(), "expires_in": ws_ticket.ttl_s()}

    # ------------------------------------------------------------ websocket

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        # Listener-only, exactly as in the HTTP middleware: a tunneled socket
        # carries the relay's Host, not ours.
        if (not _scope_is_remote(websocket)
                and not _host_allowed(websocket.headers, host_allowlist)):
            await websocket.close(code=1008)
            return
        if not _browser_origin_allowed(websocket.scope, websocket.headers):
            await websocket.close(code=1008)
            return
        # Optional shared-token gate (P0-4). The middleware does not cover the WS
        # upgrade, so check here. When auth is disabled this is a no-op. The
        # browser can't set custom headers on a WebSocket, so the token is taken
        # from the ``?token=`` query (it may also arrive as X-Auth-Token for
        # non-browser clients). On failure close BEFORE accept with 1008
        # (policy violation) so an unauthenticated client never joins the bus.
        if auth_enabled():
            # Prefer a single-use ticket (OPEN-011): a browser cannot set a WS
            # auth header, and a one-time ticket in the query is inert if it
            # lands in a log, unlike the long-lived shared token. Fall back to
            # the header / bearer / ?token= carriers for non-browser clients.
            from ..auth import ws_ticket
            ticket = websocket.query_params.get("ticket")
            if not (ticket and ws_ticket.consume(ticket)):
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
    #
    # The guard is NARROW on purpose. It used to be a bare `except Exception:
    # pass`, which meant any error anywhere inside auth/routes.py silently shipped
    # an app with NO LOGIN SURFACE AT ALL -- no /auth/me, no OIDC callback, no
    # local login -- and nothing anywhere said so. Under a real provider that is
    # not a degraded app, it is an unusable one, and the only symptom is a 404 on
    # a route the docs say exists. Absent module = the reserved seam, still
    # silent. Anything else is a broken build and now says so and stops.
    try:
        from ..auth.routes import router as auth_router  # type: ignore
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith("astrodeck.auth.routes"):
            auth_router = None          # seam reserved: no auth lane in this build
        else:
            raise                       # a DEPENDENCY of the auth lane is missing
    if auth_router is not None:
        app.include_router(auth_router)

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
