"""Persisted server configuration — the single source of truth for the observing
site and the imaging optics.

This module fixes a correctness-class P0: the observing site used to be hardcoded
to San Francisco in ``hub.py``, poisoning every alt/az, transit and meridian
calculation for everyone else, and it never persisted across a restart. The site
(and the optics that seed the plate-solver's FOV hint) now live in one atomic
JSON file under ``server/config/astrodeck.json``, loaded once and mutated through
typed helpers with optimistic-concurrency versioning.

Longitude sign convention — load-bearing:
    ``coords.lst_hours(lon)`` is East-positive (``navigator.geolocation`` /
    ISO 6709 also returns signed East-positive). The stored ``longitude`` is
    always signed East-positive. The UI collects a magnitude + E/W toggle and
    converts at the boundary. Do NOT flip this (it would silently break transit
    and the polar compass).
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, model_validator

from .events import bus
from .naming import DEFAULT_TEMPLATE, validate_template
from .persist import ensure_dir, read_json, read_json_or, write_json_atomic

# --------------------------------------------------------------------- locations

# Persistent state (config + profiles + plans) lives OUTSIDE the versioned package
# dir when ``ASTRODECK_CONFIG_DIR`` is set -- the supervisor points it at
# ``<install-root>/config`` so site/optics/profiles/auth/remote SURVIVE a
# self-update that swaps the release dir. Unset (dev) => the in-tree server/config/.
_CONFIG_ENV = (os.environ.get("ASTRODECK_CONFIG_DIR") or "").strip()
CONFIG_DIR = Path(_CONFIG_ENV) if _CONFIG_ENV else (Path(__file__).resolve().parents[1] / "config")
CONFIG_FILE = CONFIG_DIR / "astrodeck.json"
PLANS_DIR = CONFIG_DIR / "plans"
PROFILES_DIR = CONFIG_DIR / "profiles"

# One true constant: 206265 arcsec/rad / 1000 (µm↔mm). Mirrored verbatim in
# ui/src/lib/optics.ts — never duplicate the *logic*, only the constant.
ARCSEC_PER_RAD = 206.265

# Onboarding/safety: a site is "default" (never user-set) until /api/site lands.
# Below-horizon GOTO guarding is inert on a default site, and the readiness
# checklist disables the horizon row until a real location is saved.
DEFAULT_HORIZON_MIN_DEG = 15.0


# ------------------------------------------------------------------------ models

class Site(BaseModel):
    name: str = "My Observatory"
    latitude: float = Field(0.0, ge=-90, le=90)      # +N (stored signed)
    longitude: float = Field(0.0, ge=-180, le=180)   # +E (East-positive)
    elevation_m: float = Field(0.0, ge=-430, le=9000)  # forwarded to mount; ignored by altaz
    # timezone is NOT stored: sky math is longitude-only; display tz comes from
    # the browser. is_default/horizon_min_deg are server-owned onboarding fields.
    is_default: bool = True
    horizon_min_deg: float = Field(DEFAULT_HORIZON_MIN_DEG, ge=0, le=90)


class Optics(BaseModel):
    focal_length_mm: float = Field(530.0, gt=0, le=20000)
    pixel_size_um: float = Field(0.0, ge=0, le=50)   # 0 = use camera
    sensor_width_px: int = Field(0, ge=0)            # 0 = use camera
    sensor_height_px: int = Field(0, ge=0)           # 0 = use camera
    auto_from_camera: bool = True
    # A4 (P2-T3 review F2): the GUIDE scope's focal length (mm), optional and
    # independent of the main imaging train's focal_length_mm above. When set
    # (with the guide camera's pixel_size_um) it gives the native guider a real
    # image_scale_arcsec so on-sky RMS is reported in true arcsec instead of the
    # 1.0 "/px badge default. Validated > 0 when present.
    guide_focal_length_mm: float | None = Field(default=None, gt=0, le=20000)
    # TELESCOP source (PRO-2 F-B, supervisor ruling 1): the optical tube's name.
    # Written to the FITS TELESCOP card when set, omitted when blank. NOT the
    # mount device name (a wrong string pollutes stacker grouping).
    telescope_name: str = ""


# ------------------------------------------------------- automation (Batch 4b)
#
# Unattended-safety + alerting config. APPENDED to the existing AppConfig — the
# in-tree ``ConfigStore`` is reused verbatim (its ``_load`` already tolerates
# missing keys, so old config files deserialize unchanged). Named presets are
# the primary UX; the per-knob numerics are Advanced-only. The only secret kept
# at rest is an optional Telegram bot token, which ``redacted()`` blanks before
# the config is sent over WS/REST.

# Named safety presets. ``preset == "custom"`` => user-edited numerics, no patch.
SAFETY_PRESETS: dict[str, dict] = {
    "backyard": dict(on_unsafe="pause", unsafe_consecutive=3,
                     resume_when_safe=True, resume_safe_consecutive=3,
                     max_pause_min=120),
    "remote":   dict(on_unsafe="abort_park_warm", unsafe_consecutive=2,
                     resume_when_safe=False, max_pause_min=0,
                     close_dome_on_unsafe=True),
}


class SafetyConfig(BaseModel):
    enabled: bool = True
    preset: str = "backyard"               # backyard | remote | custom
    poll_each_frame: bool = True
    # floor is OFF until a SafetyMonitor or a custom horizon is configured (C1-4);
    # 0 = disabled. The UI sets 10 when the user enables the floor.
    min_alt_deg: float = 0.0
    horizon: list[tuple[float, float]] | None = None  # sorted (az,alt) control pts
    nogo_box: list[dict] | None = None     # optional [{az_min,az_max,alt_max}] pier guard
    enforce_pier_limits: bool = False      # only settable if mount reports pier side
    twilight_deg: float = -12.0            # nautical default (C1-26)
    # Sun-exclusion cone (W1.10). ON by default to protect deep-sky gear; a
    # deliberate solar-astronomy session disarms it via solar_avoidance=False
    # (route-gated behind config.solar_override). RA/Dec-based => site-independent
    # (works on a default site with no lat/lon). Additive: legacy configs without
    # these keys deserialize with the protective defaults (avoidance ON).
    solar_avoidance: bool = True           # master enable; True = cone armed (deep-sky default)
    solar_exclusion_deg: float = Field(30.0, ge=0, le=90)  # cone half-angle; <=0 = inert
    # advanced (driven by the active preset unless preset == "custom")
    on_unsafe: str = "pause"               # abort_park_warm | park | pause | warn
    unsafe_consecutive: int = 3
    resume_when_safe: bool = True
    resume_safe_consecutive: int = 3
    max_pause_min: int = 120               # 0 = no cap; escalates to park on timeout
    # Roll-off roof / dome auto-close (PRO-4). All default False → every existing
    # rig/test byte-identical (the rotator_pa_offset/polar_misalignment opt-in
    # precedent). close_dome_on_unsafe: a rain/cloud trip ESCALATES to the
    # park-and-close teardown (a closeable roof closes over the parked gear rather
    # than pause-holding under open sky). close_dome_when_done: close the roof at a
    # normal end-of-night. Two flags (not one) so protective close-on-rain and
    # end-of-night close are independently choosable. Enacting the close still
    # requires a connected dome.
    close_dome_on_unsafe: bool = False
    close_dome_when_done: bool = False
    # reopen_dome_when_safe (PRO-4 D3): OPT-IN advanced flag that ONLY matters when
    # close_dome_on_unsafe is also set. OFF (default) ⇒ close_dome_on_unsafe is
    # byte-identical to before: an unsafe trip closes the roof and ENDS the run. ON
    # ⇒ instead of ending, the run closes the roof over the parked gear, waits for
    # safe-again (debounced by resume_safe_consecutive), REOPENS the roof, re-acquires
    # the target, and RESUMES. If the never-crush close refuses (mount won't park),
    # it falls back to the open-sky park-hold pause (never crushes); if still unsafe
    # after max_pause_min it SafetyAborts with the roof left CLOSED (fail-safe). Left
    # out of SAFETY_PRESETS on purpose (advanced opt-in, no surprise roof cycling).
    reopen_dome_when_safe: bool = False


class EscalationConfig(BaseModel):
    # all default to the gentle "warn" — never silently downgrade, never abort by default
    require_cooling: bool = False
    cooling_action: str = "warn"           # warn | abort | skip
    require_guiding: bool = False
    guiding_action: str = "warn"           # warn | abort | skip
    af_failure_action: str = "warn"        # warn | abort | skip
    hfr_reject_action: str = "warn"        # warn | discard | retake (retake = Advanced)
    hfr_retake_limit_per_target: int = 4   # cap per target (C1-7)
    no_progress_watchdog_s: int = 0        # 0 = off
    reconnect_resume: bool = False         # Alpaca-only; off by default (C2-15)
    reconnect_retries: int = 1


class AlertSink(BaseModel):
    id: str
    kind: str                              # ntfy | webhook | telegram
    enabled: bool = True
    url: str = ""                          # ntfy topic url / webhook url
    token: str = ""                        # telegram bot token (the only secret we store)
    chat_id: str = ""                      # telegram chat id
    min_level: str = "warning"             # warning | error
    events: list[str] = Field(default_factory=lambda: ["run_start", "run_end", "safety", "error"])
    verified: bool = False                 # set True only by a successful round-trip test
    heartbeat_min: int = 0                 # 0 = off; periodic progress ping
    # --- email (SMTP) channel (PRO-9). The SMTP password rides in `token`
    #     (the single per-sink secret). These are NON-secret and stay visible.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""          # login user (usually the From address)
    smtp_from: str = ""          # From / envelope address
    smtp_to: str = ""            # comma-separated recipients
    smtp_starttls: bool = True   # STARTTLS after connect (587); False = plain


# ------------------------------------------------------- auth / RBAC (W2.3)
#
# Pluggable-auth + role config. APPENDED to AppConfig (additive — old config
# files without an ``auth`` block load fine; pydantic fills the default). The
# secrets here (``admin_token``, ``google_client_secret``, ``session_private_key``)
# are scrubbed by ``redacted()`` before the config goes over WS/REST, exactly
# like the Telegram token. With ``provider == "none"`` and no ``admin_token``
# the server is fully open (today's behavior); enforcement only turns on when an
# admin token or a Google provider is explicitly configured.

class AuthConfig(BaseModel):
    # Multi-method auth (W2.3-bis). ``methods`` is the source of truth: a subset
    # of {"local","google"}. EMPTY => OPEN/admin (today's byte-for-byte default).
    # Both methods can be enabled at once. ``provider`` below is LEGACY, honored
    # only for read-time migration of old config files (never written back).
    methods: list[str] = Field(default_factory=list)  # subset of {"local","google"}; [] => open/admin
    provider: str = "none"               # LEGACY (migrate-only): "none" | "google"
    admin_token: str = ""                # OPTIONAL break-glass: generalizes ASTRODECK_TOKEN; bearer => admin (secret)
    google_client_id: str = ""
    google_client_secret: str = ""       # secret
    google_redirect_uri: str = ""
    google_hd: str = ""                  # optional Workspace hosted-domain pin
    role_allowlist: dict[str, str] = Field(default_factory=dict)  # email -> role; re-evaluated EVERY request
    default_role: str | None = None      # role for any authenticated user, or None=deny
    session_signing_alg: str = "EdDSA"   # asymmetric (W3 seam); the home session today is HMAC (see auth/session.py)
    session_private_key: str = ""        # secret (home is the JWT issuer)
    session_public_key: str = ""         # home verifies its own sessions
    session_ttl_s: int = 28800           # session lifetime (8h); used by BOTH local + google logins
    local_enabled_first_run: bool = True  # allow the first-admin setup path while the user store is empty
    trust_loopback: bool = True          # G4: False => a loopback (127.0.0.1/::1) caller no longer
                                          # auto-resolves to admin under the open/"none" provider -- it
                                          # is hard-denied (401) exactly like a W3 remote-tunneled caller,
                                          # so it must authenticate via a configured method (local/google)
                                          # or admin_token like any other client. Only affects the "none"
                                          # provider's open-admin short-circuit; a no-op once a real method
                                          # is configured (loopback already gets no special treatment
                                          # there). Default True = today's behavior, unchanged. FOOTGUN:
                                          # flipping this to False over loopback with no session cookie and
                                          # no other auth configured locks that browser out on its very next
                                          # request -- recover by editing config/astrodeck.json
                                          # (auth.trust_loopback -> true) and restarting the server, or seed
                                          # a local admin with ``python -m astrodeck create-admin``.
    relay_pubkey: str = ""               # verify relay-forwarded principal (W3 seam)
    viewer_link_pubkey: str = ""         # SEPARATE key for viewer links (W3 seam)
    revoked_jti: list[str] = Field(default_factory=list)  # append-only deny registry; admin.users-gated ONLY
    session_epoch: int = 0               # monotonic session-invalidation epoch (R4B-AUTH-01).
                                          # Bumped by ``ConfigStore.set_auth`` whenever authentication
                                          # transitions from OFF (no method) to ON, so every session
                                          # token minted under an earlier epoch stops verifying the
                                          # moment auth is (re)enabled. Tokens carry an ``epoch`` claim
                                          # (auth/session.py); providers reject claims below this value
                                          # (auth/providers.py). Non-secret; server-owned (a client echo
                                          # can never LOWER it -- set_auth clamps to max(old, new)).

    @model_validator(mode="after")
    def _migrate_legacy_provider(self) -> "AuthConfig":
        """READ-TIME migration ONLY (never written back to ``provider``).

        When ``methods`` is empty we honor the legacy single ``provider`` so old
        config files keep their behavior:
          - empty methods + ``provider == "google"`` => behave as ["google"];
          - empty methods + ``provider in {"none",""}`` => stay open/admin ([]).
        A non-empty ``methods`` always wins and ``provider`` is ignored. The
        resolution chain reads ``methods``; ``provider`` is never persisted from
        here (we only populate the in-memory ``methods`` list)."""
        if not self.methods and self.provider == "google":
            object.__setattr__(self, "methods", ["google"])
        return self

    def methods_effective(self) -> list[str]:
        """The enabled methods after migration, deduped to {"local","google"}
        order. Empty => OPEN/admin."""
        return [m for m in ("local", "google") if m in self.methods]


# ------------------------------------------------------- remote relay (W3.6)
#
# Scope-side dial-out config. APPENDED to AppConfig (additive — old config files
# without a ``remote`` block load fine; pydantic fills the default). When
# ``enabled`` is False (the default) the scope NEVER dials a relay, so a LAN-only
# install is byte-for-byte today. ``device_token`` is the ONLY secret here — the
# relay validates it to register this home; it is scrubbed by ``redacted()``
# before the config goes over WS/REST (exactly like the auth/telegram secrets).
#
# This is HALF of the W3 remote pairing: the home verifies a relay-forwarded
# principal against ``AuthConfig.relay_pubkey`` / ``viewer_link_pubkey`` (already
# present), while THIS block is the outbound transport (where to dial + the token
# proving which home is dialing). They are split because the pubkeys are
# auth-resolution material and the relay coordinates are pure transport.

class RemoteConfig(BaseModel):
    enabled: bool = False        # master kill switch; False => never dial (today's default)
    relay_url: str = ""          # outbound WSS endpoint, e.g. wss://relay.example/scope
    device_token: str = ""       # SECRET: registers this home with the relay (scrubbed in redacted())
    home_id: str = ""            # stable home identifier the relay pins a path/subdomain to


# ------------------------------------------------------- self-update (Phase 3)
#
# Config for the in-app self-update subsystem (spec 2026-06-19). APPENDED to
# AppConfig (additive — old config files load fine). Holds NO secret: the
# ``signing_pubkey`` is a PUBLIC Ed25519 key the scope verifies releases against
# (the matching private key lives only in CI). ``auto_check`` is opt-in and only
# governs background polling — applying an update ALWAYS requires explicit admin
# consent (system.update) and passes the rig-idle safety gate.

class UpdateConfig(BaseModel):
    enabled: bool = True                  # master switch for the update subsystem
    auto_check: bool = False              # poll GitHub on a timer (opt-in); apply stays manual
    check_interval_hours: int = Field(24, ge=1, le=720)
    channel: str = "stable"               # stable | prerelease
    repo: str = "epim/astrodeck"          # GitHub releases source (owner/repo)
    signing_pubkey: str = ""              # PUBLIC Ed25519 key (base64); REQUIRED to apply
    # SECRET: a GitHub read token (contents:read) so a PRIVATE ``repo``'s releases
    # can be listed + the artifact/.sha256/.sig assets downloaded (public repos
    # need none). Scrubbed by ``redacted()`` -> the UI only ever sees a
    # ``github_token_configured`` boolean. Empty on a public repo (today's default).
    github_token: str = ""
    health_timeout_s: int = Field(60, ge=5, le=600)
    last_check_ts: float | None = None    # bookkeeping (set by the poller)


# ------------------------------------------------- capability providers (native)
#
# Per-capability routing override. APPENDED to AppConfig (additive — old config
# files without a ``providers`` block load fine; pydantic fills the default).
# Vocabulary (equipment-drivers spec §3.4, registry-driven): ``auto`` (let
# ``providers.resolve()`` pick), the LEGACY alias ``backend`` (the connected
# backend's own implementation — kept working forever, no migration), an
# implicit driver id (``astrodeck`` / ``astap`` / ``sim``), or a
# currently-configured driver id (``AppConfig.drivers[].id``). Because the
# vocabulary is dynamic (driver ids), the fields are plain ``str`` validated in
# ``ConfigStore.set_providers`` — pydantic ``Literal`` can't express it.

ProviderKind = str


class ProvidersConfig(BaseModel):
    autofocus: str = "auto"
    polar_align: str = "auto"
    solve: str = "auto"
    guide: str = "auto"


# --------------------------------------------------- native guider settings (§3.5)
#
# Per-axis guide-ALGORITHM selection for the native autoguider (distinct from
# ``ProvidersConfig.guide``, which routes WHO guides). The vocabulary is the
# PHD2-parity pick list (dossier §15; upstream ``mount.cpp:227-234``, mirrored in
# ``ui/src/lib/guideSettings.ts``): PPEC is RA-ONLY. Only the algorithm KIND flows
# to the engine today (``guide/native.py::_build_engine_config``); the pick is
# validated at write time in ``ConfigStore.set_guide``. APPENDED to AppConfig
# (additive — old config files without a ``guide`` block load fine).

#: RA algorithm vocabulary (PHD2 ``RA_ALGORITHMS`` — includes PPEC + ResistSwitch).
RA_GUIDE_ALGORITHMS: tuple[str, ...] = (
    "hysteresis", "lowpass", "lowpass2", "resist_switch", "ppec", "z_filter")
#: Dec algorithm vocabulary (PHD2 ``DEC_ALGORITHMS`` — PPEC is RA-only, so absent).
DEC_GUIDE_ALGORITHMS: tuple[str, ...] = (
    "hysteresis", "lowpass", "lowpass2", "resist_switch", "z_filter")
#: Dec guide-DIRECTION vocabulary (PRO-12 Tier 1; engine `parse_dec_mode`,
#: `native/crates/astrodeck-native/src/lib.rs:875-885` — accepts these four
#: literals verbatim). Distinct from the algorithm KIND above: this picks
#: which direction(s) the engine is willing to correct on the Dec axis.
DEC_GUIDE_MODES: tuple[str, ...] = ("auto", "north", "south", "off")


class GuideAxisParams(BaseModel):
    """Per-axis algorithm-tunable overrides (PRO-12 Tier 2 / T6). Every field is
    optional-``None``: ``None`` means "not pinned", so the Rust engine's own
    per-algorithm default constant applies (dossier §15) — mirrors the
    ``AxisAlgoParams`` carrier on the Rust side (open decision #2). Forwarded
    verbatim (as a sub-dict, ``exclude_none``) to the ``astrodeck_native``
    wheel by ``guide/native.py::guide_algo_config``; the wheel's PyO3 parser
    ignores keys it doesn't recognize, so this is safe to forward even before
    a wheel rebuild picks up the new engine-side fields."""
    min_move: float | None = None
    aggression: float | None = None
    hysteresis: float | None = None
    slope_weight: float | None = None
    aggressiveness: float | None = None
    exp_factor: float | None = None


class GuideConfig(BaseModel):
    ra_algorithm: str = "hysteresis"       # DefaultRaGuideAlgorithm (dossier §6/§17)
    dec_algorithm: str = "resist_switch"   # DefaultDecGuideAlgorithm (dossier §6/§17)
    # PRO-12 Tier 1: both fields are ALREADY forwarded by
    # ``guide/native.py::_build_engine_config`` (dec_guide_mode, blc_pulse_ms) —
    # only the persisted-config + validation layer was missing. No Rust change.
    dec_guide_mode: str = "auto"           # DecMode default (engine.rs:168)
    blc_pulse_ms: int = 0                  # EngineConfig.blc_pulse_ms default (engine.rs:171)
    # PRO-12 Tier 2 (T6): per-axis algorithm-tunable overrides, all-None by
    # default so an unset config round-trips byte-identical (no params sent).
    ra_params: GuideAxisParams = GuideAxisParams()
    dec_params: GuideAxisParams = GuideAxisParams()


class RotatorConfig(BaseModel):
    """Rotator mechanical range-of-motion + rotate-loop tolerance (spec §3.2).
    range_type: "full" | "half" | "quarter" — cable-wrap limiting (parity §11.2).
    range_start_deg is MECHANICAL degrees (set from the UI's "Set to current
    position"). tolerance_deg is the rotate loop's mod-180 convergence bound."""
    range_type: str = "full"
    range_start_deg: float = 0.0
    tolerance_deg: float = 1.0


class SurveyConfig(BaseModel):
    """Sky-Atlas survey source (offline-pack spec §4). online_fetch gates ALL
    hips2fits upstream calls: False (the default) = offline-first, the local
    pack is the only source; True = upstream for fov < 4°, pack as fallback."""
    online_fetch: bool = False


class NamingConfig(BaseModel):
    """PRO-11: NINA-style $$TOKEN$$ path template for capture folder+filename.
    Default reproduces the legacy fixed layout byte-for-byte."""
    template: str = DEFAULT_TEMPLATE


#: Downsample factors the WCS-stamp solve may use. 0 = ASTAP's own "auto"
#: (today's hardcoded ``-z 0``), so the default is byte-identical to the
#: shipped behaviour. Higher = faster + less precise.
WCS_DOWNSAMPLE_CHOICES = (0, 1, 2, 4)


class WcsStampConfig(BaseModel):
    """Advanced knobs for per-frame WCS stamping (per-frame-wcs spec §3).

    The MASTER ENABLE is the sibling ``AppConfig.solve_saved_lights`` bool —
    already persisted in live configs and wired at the capture seam — so this
    block is purely additive with all-default fields (decision D1: zero
    migration, the bool alone fully drives the feature).

    ``solver`` is deliberately Auto/ASTAP only (decision D4): "auto" defers to
    ``providers.pick_solver`` — the single owner of ASTAP-vs-sim precedence —
    and there is no "force sim", because faking a solve on a real rig is the
    exact hazard ``SimSolver`` refuses."""
    solver: Literal["auto", "astap"] = "auto"
    downsample: int = Field(0, ge=0, le=4)     # 0=auto; else ASTAP -z
    min_stars: int = Field(0, ge=0, le=100000)  # 0=off; else skip below N stars
    queue_max: int = Field(4, ge=1, le=64)      # bounded backlog, drop-oldest


class CalibrationConfig(BaseModel):
    """PRO-1 master-library matching + stacking tolerances (appended — old
    configs load fine). ``exposure_tol_pct``/``temp_tol_c`` control how
    aggressively masters are reused across nights; ``temp_bin_c`` quantizes the
    stacking bucket; ``stack_sigma``/``max_stack_frames`` bound the reduction."""
    exposure_tol_pct: float = Field(5.0, ge=0, le=100)
    temp_tol_c: float = Field(2.0, ge=0, le=50)
    temp_bin_c: float = Field(5.0, ge=0, le=50)
    stack_sigma: float = Field(3.0, gt=0, le=10)
    max_stack_frames: int = Field(100, ge=1, le=1000)


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


# ------------------------------------------------------- backend drivers (2026-07-08)
#
# GLOBAL configured drivers (equipment-drivers spec §3.1): a driver is "how to
# reach a backend" (NINA instance, Alpaca server, PHD2), declared ONCE here and
# referenced by id from profiles/assignments. APPENDED to AppConfig (additive —
# old config files without a ``drivers`` block load fine; pydantic fills []).
# Implicit drivers (sim / astrodeck native / astap) are DETECTED, never stored.
# A DriverEntry holds NO secret (host/port/label only), so ``redacted()`` needs
# no change for it.

#: The DETECTED (non-configured) driver ids drivers._implicit_rows() serves —
#: also the implicit half of the provider-override vocabulary (spec §3.4).
#: ``ascom-local`` (COM-T6) is Windows-ONLY: the bundled COM host is a built-in
#: only where COM exists, so it joins the vocabulary only on win32 (keeping it in
#: lockstep with the Windows-gated _implicit_rows() row). Off Windows a rig or
#: provider-override referencing it is correctly rejected — there is no such
#: driver — and the UI never surfaces it.
IMPLICIT_DRIVER_IDS: tuple[str, ...] = (
    ("sim", "astrodeck", "astap", "ascom-local")
    if sys.platform == "win32"
    else ("sim", "astrodeck", "astap"))

# Open string: the configurable-driver vocabulary is registry-derived
# (drivers.configurable_driver_types()) and validated at the API layer. Kept as a
# named alias so existing imports/annotations keep working. config.py stays
# import-light (it must NOT import the registry), so validation lives one layer up.
DriverType = str

#: Default port per configurable driver type (NINA Advanced API / Alpaca / PHD2).
DRIVER_DEFAULT_PORTS: dict[str, int] = {"nina": 1888, "alpaca": 11111, "phd2": 4400}


class DriverEntry(BaseModel):
    id: str                                  # server-minted "<type>-<4hex>", immutable
    type: DriverType
    transport: str = "network"               # "network" | "serial"
    host: str = ""                           # network transport
    port: int = Field(default=0, ge=0, le=65535)
    port_path: str = ""                      # serial transport, e.g. "COM3"
    enabled: bool = True
    label: str = ""
    extra: dict = Field(default_factory=dict)  # driver-typed options (e.g. phd2 managed)

    @model_validator(mode="after")
    def _check_transport(self) -> "DriverEntry":
        """Per-transport addressing: serial needs a port_path; local (SDK-
        enumerated USB, e.g. ZWO CAA/EAF) needs NO addressing; network needs a
        host + a real port. Defaults keep every existing (network) driver valid."""
        if self.transport == "serial":
            if not self.port_path:
                raise ValueError("serial driver requires port_path")
        elif self.transport == "local":
            pass  # identity comes from SDK enumeration — nothing to address
        else:  # network
            if not self.host:
                raise ValueError(f"{self.transport} driver requires host")
            if not (1 <= self.port <= 65535):
                raise ValueError(f"{self.transport} driver requires port 1..65535")
        return self


class AppConfig(BaseModel):
    version: int = 1                   # bumped on every save (optimistic-concurrency token)
    site: Site = Field(default_factory=Site)
    optics: Optics = Field(default_factory=Optics)
    active_profile_id: str | None = None
    # --- automation (Batch 4b; appended — old configs without these load fine) ---
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    alerts: list[AlertSink] = Field(default_factory=list)
    deadman_url: str = ""              # external healthcheck ping URL (C2-9)
    # --- auth / RBAC (W2; appended — old configs load fine) ---
    auth: AuthConfig = Field(default_factory=AuthConfig)
    # --- remote relay (W3; appended — old configs load fine) ---
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    # --- self-update (Phase 3; appended — old configs load fine) ---
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    # --- capability providers (native parity; appended — old configs load fine) ---
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    # --- native guider algorithm selection (spec §3.5; appended — old configs load fine) ---
    guide: GuideConfig = Field(default_factory=GuideConfig)
    # --- backend drivers (equipment-drivers spec; appended — old configs load fine) ---
    drivers: list[DriverEntry] = Field(default_factory=list)
    # --- rotator ROM/tolerance (rotator/CAA spec §3.2; appended — old configs load fine) ---
    rotator: RotatorConfig = Field(default_factory=RotatorConfig)
    # --- Sky-Atlas survey source (offline-pack spec §4; appended — old configs load fine) ---
    survey: SurveyConfig = Field(default_factory=SurveyConfig)
    # --- weather integration (sub-project C spec §2; appended — old configs load fine) ---
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    # --- file-naming template (PRO-11; appended — old configs load fine) ---
    naming: NamingConfig = Field(default_factory=NamingConfig)
    # --- calibration master library (PRO-1; appended — old configs load fine) ---
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    # --- opt-in re-solve-free astrometry (PRO-2 F-B, supervisor ruling 4;
    #     appended — old configs load fine) ---
    solve_saved_lights: bool = False   # ON => solve each saved light in place, stamp WCS
    # --- per-frame-WCS advanced knobs (per-frame-wcs spec §3; appended — old
    #     configs load fine, and every field defaults to today's behaviour) ---
    wcs_stamp: WcsStampConfig = Field(default_factory=WcsStampConfig)


# ------------------------------------------------------- filter slot-name store
# User-assigned filter-wheel slot names + per-filter focuser offsets, persisted
# per profile so they survive reconnect (hardware wheels re-derive their letters
# every connect). Keyed by active profile id; a single small JSON file (UX-05).
FILTER_CONFIG_FILE = CONFIG_DIR / "filter_names.json"
_FILTER_DEFAULT_KEY = "__default__"


def load_filter_config(profile_id: str | None) -> dict:
    """The saved ``{"names": [...], "offsets": [...]}`` for a profile, or ``{}``
    when nothing has been saved (or the store is missing/corrupt)."""
    data = read_json_or(FILTER_CONFIG_FILE, {})
    if not isinstance(data, dict):
        return {}
    entry = data.get(profile_id or _FILTER_DEFAULT_KEY)
    return entry if isinstance(entry, dict) else {}


def save_filter_config(profile_id: str | None, names: list[str],
                       offsets: list[int]) -> None:
    """Persist filter slot names + offsets for a profile (best-effort merge into
    the shared store; other profiles' entries are preserved)."""
    data = read_json_or(FILTER_CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data[profile_id or _FILTER_DEFAULT_KEY] = {
        "names": [str(n) for n in names],
        "offsets": [int(o) for o in offsets],
    }
    write_json_atomic(FILTER_CONFIG_FILE, data)


# --------------------------------------------------- learned camera EGAIN store
# Measured conversion gain (e-/ADU) per camera GAIN setting, persisted per
# profile. Conversion gain varies with the gain setting (notably across an HCG
# transition), so this is a MAP keyed by the exact gain integer and applied only
# on an exact match — no interpolation. A driver-reported EGAIN always wins; a
# learned value is used ONLY when the driver reports 0.0 (never override real
# hardware with an estimate).
EGAIN_CONFIG_FILE = CONFIG_DIR / "egain.json"
_EGAIN_DEFAULT_KEY = "__default__"


def load_egain_config(profile_id: str | None) -> dict:
    """``{gain:int -> egain:float}`` learned for a profile (``{}`` when nothing
    is saved or the store is missing/corrupt)."""
    data = read_json_or(EGAIN_CONFIG_FILE, {})
    if not isinstance(data, dict):
        return {}
    entry = data.get(profile_id or _EGAIN_DEFAULT_KEY)
    if not isinstance(entry, dict):
        return {}
    out: dict[int, float] = {}
    for k, v in entry.items():
        try:
            out[int(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def save_egain_config(profile_id: str | None, by_gain: dict) -> None:
    """Persist the learned per-gain EGAIN map for a profile (best-effort merge
    into the shared store; other profiles' entries are preserved)."""
    data = read_json_or(EGAIN_CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data[profile_id or _EGAIN_DEFAULT_KEY] = {
        str(int(g)): float(v) for g, v in by_gain.items()}
    write_json_atomic(EGAIN_CONFIG_FILE, data)


# --------------------------------------------------------------------- pure math

def image_scale_arcsec_px(focal_mm: float, pixel_um: float, binning: int = 1) -> float:
    """Image scale in arcsec/pixel. ``ARCSEC_PER_RAD * px * bin / fl``."""
    if focal_mm <= 0:
        return 0.0
    return ARCSEC_PER_RAD * (pixel_um * binning) / focal_mm


def fov_deg(focal_mm: float, pixel_um: float, w_px: int, h_px: int) -> tuple[float, float, float]:
    """Field of view (width, height, diagonal) in degrees.

    FOV is bin-INDEPENDENT — fewer, bigger binned pixels cover the same sky — so
    it is always computed at bin-1. Sending a binned scale to a plate solver
    would halve the hint.
    """
    s = image_scale_arcsec_px(focal_mm, pixel_um, binning=1)
    fw = s * w_px / 3600.0
    fh = s * h_px / 3600.0
    return fw, fh, (fw * fw + fh * fh) ** 0.5


# -------------------------------------------------------------------- exceptions

class ConfigVersionConflict(ValueError):
    """Raised when a PUT carries a stale version. Subclasses ``ValueError`` so the
    API's ``except ValueError`` maps it to a 409; carries the current config so
    the UI can reconcile rather than silently clobber a co-user's field."""

    def __init__(self, current: AppConfig):
        super().__init__("config changed on another client")
        self.current = current


# ------------------------------------------------------------------------- store

class ConfigStore:
    """Module singleton (like ``hub``) owning the persisted ``AppConfig``.

    Loaded once from disk; missing file → defaults + immediate save; a corrupt
    file → defaults + a logged warning + the bad file backed up. Every mutating
    helper bumps ``version`` and writes atomically.
    """

    def __init__(self, path: Path = CONFIG_FILE):
        self._path = path
        self._cfg: AppConfig | None = None

    # -- loading ---------------------------------------------------------------

    def _bak_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".bak")

    def _restore_from_bak(self) -> AppConfig | None:
        """Try to load a valid ``AppConfig`` from the ``.bak`` copy. Returns the
        recovered config (already re-persisted as the primary) or ``None`` if the
        backup is absent/unparseable/invalid.

        Called *before* ``_recover_from_corrupt`` stashes the corrupt primary at
        ``.bak`` — otherwise the recoverable previous version would be clobbered.
        """
        bak = self._bak_path()
        try:
            raw = read_json(bak)
        except (FileNotFoundError, ValueError, OSError):
            return None
        try:
            cfg = AppConfig(**raw)
        except Exception:
            return None
        bus.log("warning", "config restored from backup (.bak)", "config")
        self._cfg = cfg
        # Re-establish the primary from the good backup WITHOUT taking a fresh
        # backup: the (possibly corrupt) primary still on disk must not be copied
        # over the known-good ``.bak`` we just recovered from.
        ensure_dir(self._path.parent)
        write_json_atomic(self._path, cfg.model_dump(), backup=False)
        return cfg

    def _load(self) -> AppConfig:
        try:
            raw = read_json(self._path)
        except FileNotFoundError:
            recovered = self._restore_from_bak()
            if recovered is not None:
                return recovered
            cfg = AppConfig()
            self._cfg = cfg
            self._save()
            return cfg
        except (ValueError, OSError) as e:
            recovered = self._restore_from_bak()
            if recovered is not None:
                return recovered
            cfg = self._recover_from_corrupt(e)
            self._cfg = cfg
            self._save()
            return cfg
        try:
            # tolerate missing keys (forward/back-compat with the automation
            # surface, which appends keys later) — pydantic fills defaults.
            cfg = AppConfig(**raw)
        except Exception as e:  # invalid shape
            recovered = self._restore_from_bak()
            if recovered is not None:
                return recovered
            cfg = self._recover_from_corrupt(e)
            self._cfg = cfg
            self._save()
            return cfg
        return cfg

    def _recover_from_corrupt(self, err: Exception) -> AppConfig:
        bus.log("warning", f"config reset to defaults: {err}", "config")
        try:
            if self._path.exists():
                import os
                os.replace(self._path, self._bak_path())
        except OSError:
            pass
        return AppConfig()

    def cfg(self) -> AppConfig:
        if self._cfg is None:
            self._cfg = self._load()
        return self._cfg

    def _save(self) -> None:
        cfg = self._cfg
        if cfg is None:
            return
        ensure_dir(self._path.parent)
        write_json_atomic(self._path, cfg.model_dump())

    def reload(self) -> AppConfig:
        """Force a re-read from disk (used by tests)."""
        self._cfg = self._load()
        return self._cfg

    def replace(self, cfg: AppConfig) -> AppConfig:
        """Swap the WHOLE in-memory config for ``cfg``, then bump + persist.

        The wholesale counterpart to the typed ``set_*`` helpers, and deliberately
        the only one: it exists for factory reset (``factory_reset.py``), where
        the point is that every block returns to its default at once. It is NOT a
        general write path — a partial update must still go through the typed
        setter that validates it, because this one validates nothing beyond the
        model itself. Version handling stays with ``bump_and_save``, so a caller
        that carries the old ``version`` forward keeps the counter monotonic and
        an open client's stale token still loses its concurrency race."""
        self._cfg = cfg
        return self.bump_and_save()

    # -- mutation --------------------------------------------------------------

    def _check_version(self, expected_version: int | None) -> None:
        if expected_version is None:
            return
        if expected_version != self.cfg().version:
            raise ConfigVersionConflict(self.cfg())

    def bump_and_save(self) -> AppConfig:
        cfg = self.cfg()
        cfg.version += 1
        self._save()
        return cfg

    def set_site(self, site: Site, expected_version: int | None = None) -> AppConfig:
        self._check_version(expected_version)
        cfg = self.cfg()
        # a user-saved site is, by definition, no longer the default.
        site = site.model_copy(update={"is_default": False})
        cfg.site = site
        return self.bump_and_save()

    def set_optics(self, optics: Optics, expected_version: int | None = None) -> AppConfig:
        self._check_version(expected_version)
        cfg = self.cfg()
        cfg.optics = optics
        return self.bump_and_save()

    def set_active_profile(self, profile_id: str | None) -> AppConfig:
        cfg = self.cfg()
        cfg.active_profile_id = profile_id
        return self.bump_and_save()

    # -- automation mutation (Batch 4b) ----------------------------------------

    def set_safety(self, safety: SafetyConfig) -> AppConfig:
        cfg = self.cfg()
        cfg.safety = safety
        return self.bump_and_save()

    def set_escalation(self, escalation: EscalationConfig) -> AppConfig:
        cfg = self.cfg()
        cfg.escalation = escalation
        return self.bump_and_save()

    def set_alerts(self, alerts: list[AlertSink]) -> AppConfig:
        cfg = self.cfg()
        cfg.alerts = list(alerts)
        return self.bump_and_save()

    def set_deadman(self, url: str) -> AppConfig:
        cfg = self.cfg()
        cfg.deadman_url = url
        return self.bump_and_save()

    # -- auth / RBAC mutation (W2) ---------------------------------------------

    def set_auth(self, auth: AuthConfig) -> AppConfig:
        """Persist a new ``AuthConfig`` (admin.users-gated at the API layer).

        Enforces the pinned ``default_role`` ceiling: a non-null ``default_role``
        must be at most ``viewer`` UNLESS a Workspace hosted-domain (``google_hd``)
        is pinned — otherwise the entire Google population could be auto-elevated
        to a control/config-bearing role over the WAN. Also rejects an unknown
        provider, an unknown default_role/allowlist role, and any attempt to
        SHRINK the append-only ``revoked_jti`` registry.
        """
        validate_auth_config(auth, current=self.cfg().auth)
        cfg = self.cfg()
        old = cfg.auth
        # Session-epoch invariant (R4B-AUTH-01). The epoch is SERVER-owned: a
        # client echoing a stale redacted block can never lower it (clamp to
        # max), and the OFF -> ON transition (no enabled method -> any enabled
        # method) advances it so every session minted under an earlier epoch --
        # including one that survived an auth-off interval in some browser --
        # stops verifying the moment authentication is (re)enabled.
        epoch = max(int(auth.session_epoch or 0), int(old.session_epoch or 0))
        if not old.methods_effective() and auth.methods_effective():
            epoch += 1
        if epoch != auth.session_epoch:
            auth = auth.model_copy(update={"session_epoch": epoch})
        cfg.auth = auth
        return self.bump_and_save()

    # -- remote relay mutation (W3) --------------------------------------------

    def set_remote(self, remote: RemoteConfig) -> AppConfig:
        """Persist a new ``RemoteConfig`` (admin.users-gated at the API layer).

        Mirrors ``set_auth``: a typed setter so the relay/remote knobs are written
        through one place (NOT the ``extra="forbid"`` ``POST /api/config`` merge).
        A blank ``device_token`` means 'unchanged' is resolved at the API layer
        (the UI only ever sees the redacted block), not here -- this setter writes
        exactly what it is given."""
        cfg = self.cfg()
        cfg.remote = remote
        return self.bump_and_save()

    # -- self-update mutation (Phase 3) ----------------------------------------

    def set_update_config(self, update: "UpdateConfig") -> AppConfig:
        """Persist a new ``UpdateConfig`` (system.update-gated at the API layer).

        Validates the channel + signing_pubkey. The one secret is ``github_token``
        (private-repo read); the API layer resolves a blank token to 'unchanged'
        (like admin_token) and ``redacted()`` scrubs it from every broadcast."""
        if update.channel not in ("stable", "prerelease"):
            raise ValueError(f"unknown update channel: {update.channel!r}")
        # A non-empty signing key must be a valid 32-byte base64 Ed25519 public
        # key (reject garbage that would later silently fail every verification).
        pk = (update.signing_pubkey or "").strip()
        if pk:
            import base64
            try:
                raw = base64.b64decode(pk, validate=True)
            except Exception:
                raise ValueError("signing_pubkey must be valid base64")
            if len(raw) != 32:
                raise ValueError("signing_pubkey must decode to 32 bytes (Ed25519)")
        cfg = self.cfg()
        cfg.update = update
        return self.bump_and_save()

    # -- capability providers mutation (native parity + spec §3.4 vocabulary) --

    def valid_override_values(self) -> set[str]:
        """Every value ``set_providers`` accepts RIGHT NOW: ``auto``, the legacy
        ``backend`` alias, the implicit driver ids, and each currently-configured
        driver id. Registry-driven (spec review finding 2) — deleting a driver
        removes its id from this set for FUTURE writes; already-stored values
        degrade to auto at resolve time instead."""
        return ({"auto", "backend", *IMPLICIT_DRIVER_IDS}
                | {d.id for d in self.cfg().drivers})

    def set_providers(self, providers: "ProvidersConfig") -> AppConfig:
        """Persist a new ``ProvidersConfig`` (per-capability routing override).

        Values are validated against the CURRENT vocabulary (see
        ``valid_override_values``) so an unknown/typo'd driver id is rejected at
        write time (the route maps this ValueError to 422) rather than silently
        resolving to auto forever."""
        valid = self.valid_override_values()
        for cap in ("autofocus", "polar_align", "solve", "guide"):
            v = getattr(providers, cap, "auto")
            if v not in valid:
                raise ValueError(
                    f"unknown provider for {cap}: {v!r} — valid values are "
                    f"auto, backend (legacy), an implicit driver id "
                    f"({', '.join(IMPLICIT_DRIVER_IDS)}), or a configured "
                    f"driver id")
        cfg = self.cfg()
        cfg.providers = providers
        return self.bump_and_save()

    # -- native guider algorithm mutation (spec §3.5) --------------------------

    def set_guide(self, guide: "GuideConfig") -> AppConfig:
        """Persist the native guider's per-axis algorithm selection. Write-time
        validated against the PHD2-parity pick lists (PPEC is RA-only) so an
        unknown/mis-axised algorithm is rejected here (route maps ValueError→422)
        rather than silently reaching the engine."""
        if guide.ra_algorithm not in RA_GUIDE_ALGORITHMS:
            raise ValueError(
                f"unknown RA guide algorithm: {guide.ra_algorithm!r} — valid "
                f"values are {', '.join(RA_GUIDE_ALGORITHMS)}")
        if guide.dec_algorithm not in DEC_GUIDE_ALGORITHMS:
            raise ValueError(
                f"unknown Dec guide algorithm: {guide.dec_algorithm!r} — valid "
                f"values are {', '.join(DEC_GUIDE_ALGORITHMS)}")
        if guide.dec_guide_mode not in DEC_GUIDE_MODES:
            raise ValueError(
                f"unknown Dec guide mode: {guide.dec_guide_mode!r} — valid "
                f"values are {', '.join(DEC_GUIDE_MODES)}")
        # Defense in depth (open decision #4): clamp to the same [0, 10000] ms
        # ceiling the client's clampBlcPulse enforces, so a value that skips the
        # UI still can't reach the engine out of range.
        guide.blc_pulse_ms = max(0, min(10000, int(guide.blc_pulse_ms)))
        # PRO-12 Tier 2 (T6, open decision #3): clamp each PRESENT per-axis
        # tunable to the engine's own bounds — defense in depth, the Rust
        # ``new()`` constructors clamp again authoritatively. A param left
        # ``None`` (not pinned) stays ``None`` so the engine's per-algorithm
        # default constant applies.
        for axis_params in (guide.ra_params, guide.dec_params):
            if axis_params.aggression is not None:
                axis_params.aggression = max(0.0, min(2.0, axis_params.aggression))
            if axis_params.hysteresis is not None:
                axis_params.hysteresis = max(0.0, min(0.99, axis_params.hysteresis))
            for field in ("min_move", "slope_weight", "aggressiveness", "exp_factor"):
                value = getattr(axis_params, field)
                if value is not None:
                    setattr(axis_params, field, max(0.0, value))
        cfg = self.cfg()
        cfg.guide = guide
        return self.bump_and_save()

    # -- rotator ROM/tolerance mutation (rotator/CAA spec §3.2) -----------------

    def set_rotator(self, rotator: "RotatorConfig") -> AppConfig:
        """Persist the rotator ROM/tolerance config; write-time validated so a
        junk range never reaches the rotate loop (route maps ValueError→422)."""
        if rotator.range_type not in ("full", "half", "quarter"):
            raise ValueError(
                f"unknown range_type: {rotator.range_type!r} — "
                f"valid values are full, half, quarter")
        if not (0.0 <= rotator.range_start_deg < 360.0):
            raise ValueError("range_start_deg must be in [0, 360)")
        if not (0.0 < rotator.tolerance_deg <= 45.0):
            raise ValueError("tolerance_deg must be in (0, 45]")
        cfg = self.cfg()
        cfg.rotator = rotator
        return self.bump_and_save()

    # -- survey-source mutation (offline-pack spec §4) --------------------------

    def set_survey(self, survey: "SurveyConfig") -> AppConfig:
        """Persist the survey-source config (offline-pack spec §4)."""
        cfg = self.cfg()
        cfg.survey = survey
        return self.bump_and_save()

    # -- file-naming template mutation (PRO-11) ---------------------------------

    def set_naming(self, naming: "NamingConfig") -> AppConfig:
        """Persist the capture-naming template. Write-time validated so an
        unusable template (empty/unknown-token/traversal) is rejected here
        (route maps ValueError -> 422) rather than reaching _capture_path."""
        validate_template(naming.template)
        cfg = self.cfg()
        cfg.naming = naming
        return self.bump_and_save()

    # -- per-frame WCS stamping mutation (per-frame-wcs spec §3) ----------------

    def set_wcs_stamp(self, solve_saved_lights: bool,
                      wcs_stamp: "WcsStampConfig") -> AppConfig:
        """Persist the per-frame-WCS master enable + its advanced block in ONE
        atomic bump (they are edited by a single panel, so splitting them across
        two routes would let a half-applied state be broadcast).

        Write-time validated so an unusable downsample never reaches ASTAP's
        ``-z`` flag (route maps ValueError -> 422). ``solver`` is already
        Literal-validated by pydantic."""
        if wcs_stamp.downsample not in WCS_DOWNSAMPLE_CHOICES:
            raise ValueError(
                f"downsample must be one of "
                f"{', '.join(str(d) for d in WCS_DOWNSAMPLE_CHOICES)} "
                f"(0 = automatic)")
        cfg = self.cfg()
        cfg.solve_saved_lights = bool(solve_saved_lights)
        cfg.wcs_stamp = wcs_stamp
        return self.bump_and_save()

    def set_weather(self, weather: "WeatherConfig",
                    expected_version: int | None = None) -> AppConfig:
        """Persist the weather config (weather spec §2). Version-checked like
        set_site so concurrent editors get a 409, not a silent clobber."""
        self._check_version(expected_version)
        cfg = self.cfg()
        cfg.weather = weather
        return self.bump_and_save()

    # -- backend drivers mutation (equipment-drivers spec §3.1) -----------------

    def add_driver(self, driver_type: str, host: str = "", port: int | None = None,
                   label: str = "", extra: dict | None = None, *,
                   transport: str = "network", port_path: str = "") -> DriverEntry:
        """Create a configured driver with a server-minted, never-reused id.

        The id is "<type>-<4 hex>" (collision-checked against existing entries)
        so profiles can reference drivers stably. Network drivers require a
        host; port defaults per known type (``DRIVER_DEFAULT_PORTS``), and an
        unknown-to-the-map type (a plugin's network driver_type — vocabulary is
        registry-validated at the API layer) requires an EXPLICIT port. Serial
        drivers require ``port_path`` instead; no host/port. Raises
        ``ValueError`` (→ 422 at the API) on a bad combination."""
        cfg = self.cfg()
        existing = {d.id for d in cfg.drivers}
        while True:
            new_id = f"{driver_type}-{secrets.token_hex(2)}"
            if new_id not in existing:
                break
        if transport == "serial":
            port_path = (port_path or "").strip()
            if not port_path:
                raise ValueError("serial driver requires port_path")
            entry = DriverEntry(
                id=new_id, type=driver_type, transport="serial",
                port_path=port_path,
                label=(label or "").strip() or f"{driver_type.upper()} @ {port_path}",
                extra=dict(extra or {}))
        elif transport == "local":
            entry = DriverEntry(
                id=new_id, type=driver_type, transport="local",
                label=(label or "").strip() or f"{driver_type.upper()} (USB)",
                extra=dict(extra or {}))
        else:
            host = (host or "").strip()
            if not host:
                raise ValueError("driver host must not be empty")
            if port is None:
                port = DRIVER_DEFAULT_PORTS.get(driver_type)
                if port is None:
                    raise ValueError(
                        f"driver type {driver_type!r} has no default port — "
                        "specify one explicitly")
            entry = DriverEntry(
                id=new_id, type=driver_type, host=host, port=port,
                label=(label or "").strip() or f"{driver_type.upper()} @ {host}",
                extra=dict(extra or {}))
        cfg.drivers.append(entry)
        self.bump_and_save()
        return entry

    def update_driver(self, driver_id: str, patch: dict) -> DriverEntry:
        """Patch host/port/enabled/label/extra/port_path/transport on one driver
        (id/type immutable).

        ``model_copy(update=...)`` does NOT re-validate in pydantic v2, so the
        patched entry is re-constructed through ``DriverEntry(**...)`` to run
        the field validators (port range, per-transport addressing — serial
        requires port_path, network requires host+port). Raises ``KeyError``
        for an unknown id (→ 404) and ``ValueError`` for a bad field/value
        (→ 422).

        ``port_path``/``transport`` (B follow-up C) let a moved COM port be
        fixed without delete+recreate — e.g. patching just ``port_path`` on a
        serial driver."""
        allowed = {"host", "port", "enabled", "label", "extra", "port_path",
                   "transport"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"unknown driver fields: {sorted(unknown)}")
        # Normalize a COPY of the patch so create/update stay symmetric with
        # add_driver: host/port_path are stored stripped, and a patched extra
        # dict is copied so a caller-retained reference can't alias into the
        # stored config. isinstance guards let a wrong-typed value fall through
        # to the DriverEntry re-construction below (→ ValueError, → 422)
        # instead of raising AttributeError here.
        patch = dict(patch)
        if isinstance(patch.get("host"), str):
            patch["host"] = patch["host"].strip()
        if isinstance(patch.get("port_path"), str):
            patch["port_path"] = patch["port_path"].strip()
        if isinstance(patch.get("extra"), dict):
            patch["extra"] = dict(patch["extra"])
        cfg = self.cfg()
        for i, d in enumerate(cfg.drivers):
            if d.id != driver_id:
                continue
            updated = DriverEntry(**d.model_copy(update=patch).model_dump())
            # The DriverEntry validator already requires host for a NETWORK
            # transport (and port_path for serial) — this extra strip-check
            # only catches a whitespace-only network host, which is truthy
            # but semantically empty. Gated on transport=="network" so it
            # doesn't reject every serial/local patch (their host is always
            # "" by design).
            if updated.transport == "network" and not updated.host.strip():
                raise ValueError("driver host must not be empty")
            cfg.drivers[i] = updated
            self.bump_and_save()
            return updated
        raise KeyError(driver_id)

    def delete_driver(self, driver_id: str) -> None:
        """Remove one driver. Raises ``KeyError`` for an unknown id (→ 404).
        The id is never reused (add_driver mints fresh hex); a profile still
        referencing it degrades per the spec's failure-honesty rules."""
        cfg = self.cfg()
        keep = [d for d in cfg.drivers if d.id != driver_id]
        if len(keep) == len(cfg.drivers):
            raise KeyError(driver_id)
        cfg.drivers = keep
        self.bump_and_save()


def validate_auth_config(auth: AuthConfig, current: AuthConfig | None = None) -> None:
    """Validate an ``AuthConfig`` before persistence. Raises ``ValueError`` (→ 400
    at the API) on any violation. Pinned rules:

    1. ``provider`` ∈ {"none", "google"}.
    2. Every role used (``default_role`` + every value in ``role_allowlist``) is
       a known role.
    3. ``default_role`` ceiling: if non-null it must be at most ``viewer`` UNLESS
       ``google_hd`` is non-empty (prevents WAN-wide auto-elevation).
    4. ``revoked_jti`` is APPEND-ONLY: a save may add jtis but never drop one
       that ``current`` already had (the deny registry cannot be shrunk).

    Multi-method (W2.3-bis): ``methods`` must be a subset of {"local","google"}.
    EMPTY ``methods`` is valid (open/admin). The legacy ``provider`` field is
    still validated for back-compat but is no longer the source of truth.

    Lazy-imports the auth role table so config.py stays import-light (no auth
    package import at module load — avoids any import cycle)."""
    from .auth.capabilities import ROLES, role_rank  # lazy: keep config import-light

    if auth.provider not in ("none", "google"):
        raise ValueError(f"unknown auth provider: {auth.provider!r}")

    for m in auth.methods:
        if m not in ("local", "google"):
            raise ValueError(f"unknown auth method: {m!r}")
    if auth.session_ttl_s <= 0:
        raise ValueError("session_ttl_s must be positive")

    roles_in_use = list(auth.role_allowlist.values())
    if auth.default_role is not None:
        roles_in_use.append(auth.default_role)
    for r in roles_in_use:
        if r not in ROLES:
            raise ValueError(f"unknown role: {r!r}")

    if auth.default_role is not None:
        # at most "viewer" unless a hosted-domain is pinned
        if role_rank(auth.default_role) > role_rank("viewer") and not auth.google_hd.strip():
            raise ValueError(
                "default_role above 'viewer' requires a pinned google_hd "
                "(refusing to auto-elevate the whole Google population)")

    if current is not None:
        missing = set(current.revoked_jti) - set(auth.revoked_jti)
        if missing:
            raise ValueError(
                "revoked_jti is append-only and cannot be shrunk "
                f"(missing: {sorted(missing)})")


def _strip_url_userinfo(url: str) -> str:
    """Strip a ``user:pass@`` userinfo component from a url before broadcast.

    A webhook/ntfy url can embed Basic-auth creds (``https://user:pass@host/...``)
    that are just as secret as a Telegram token — they must never leave the
    server (P2-12). Fail-safe: any parse failure on a *non-empty* url returns a
    placeholder rather than echoing the raw (possibly secret-bearing) string.
    Empty stays empty; a url with no userinfo is returned unchanged.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return "<redacted-url>"
    if "@" not in (parts.netloc or ""):
        return url  # no userinfo — nothing to strip
    # Rebuild netloc as host[:port], dropping the userinfo entirely.
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redacted(cfg: AppConfig) -> dict:
    """Serialize ``cfg`` for WS/REST with every secret scrubbed (P2-12).

    Redaction must be FAIL-SAFE: it previously blanked only the Telegram
    ``token`` and broadcast ``chat_id``, url-embedded ``user:pass@`` creds, and
    the ``deadman_url`` verbatim. Now every secret-bearing field is scrubbed, and
    the structure is rebuilt defensively (a malformed sink dict can't slip a
    secret through). Returns a plain dict (``model_dump`` shape) safe to
    broadcast; the source ``cfg`` is never mutated.
    """
    data = cfg.model_dump()
    for sink in data.get("alerts", []):
        if not isinstance(sink, dict):
            continue
        # Derived marker: was a secret set? (never a persisted field — the client
        # can't otherwise tell a configured Discord/Slack/email/Telegram sink
        # from an empty one once `token` is blanked below.)
        sink["token_configured"] = bool(sink.get("token"))
        # Telegram bot token — the at-rest secret. Always blanked.
        if sink.get("token"):
            sink["token"] = ""
        # Telegram chat id can be a private/identifying value — redact it but keep
        # a boolean-ish marker so the UI can still show "configured".
        if sink.get("chat_id"):
            sink["chat_id"] = ""
        # ntfy/webhook url can carry Basic-auth creds in the userinfo. Strip them
        # (keep the host/path so the UI still shows where it points).
        if sink.get("url"):
            sink["url"] = _strip_url_userinfo(sink["url"])
    # The deadman url can carry a per-ping secret in its path/query (healthchecks
    # uuids, Uptime-Kuma push tokens) AND userinfo creds. Surface only a boolean
    # "configured" marker over the wire — never the url itself.
    dm = data.get("deadman_url") or ""
    data["deadman_url"] = ""
    data["deadman_configured"] = bool(dm)
    # Stage B: AppConfig holds only the active-profile POINTER (no profile records
    # — those live in ProfileLibrary), so there is no profile-borne secret to
    # scrub here today. A profile's only at-rest secret-capable field is a
    # backend's ``ConnSpec.extra``; the profile read routes now scrub any
    # secret-bearing ``extra`` key over the wire (see ``profiles.redact_profile``),
    # so no profile-borne credential can leak through this path either.
    #
    # W2 auth block: scrub every secret-bearing field, surface "configured"
    # booleans so the UI can show state without the secret. Non-secret fields
    # (role_allowlist, revoked_jti, public keys, provider, default_role, hd)
    # pass through. Rebuilt defensively — a missing/malformed auth dict can't
    # slip a secret through, and the source cfg is never mutated.
    auth = data.get("auth")
    if isinstance(auth, dict):
        admin_tok = auth.get("admin_token") or ""
        gclient_secret = auth.get("google_client_secret") or ""
        sess_priv = auth.get("session_private_key") or ""
        auth["admin_token"] = ""
        auth["google_client_secret"] = ""
        auth["session_private_key"] = ""
        auth["admin_token_configured"] = bool(admin_tok)
        auth["google_configured"] = bool(
            auth.get("google_client_id") and gclient_secret)
        auth["session_signing_configured"] = bool(sess_priv)
        data["auth"] = auth
    # Update block: the GitHub read token (private-repo releases) is the only
    # secret here -- scrub it, surface a boolean so the UI can show "configured".
    # signing_pubkey/repo/channel are public and pass through unchanged.
    upd = data.get("update")
    if isinstance(upd, dict):
        gh_tok = upd.get("github_token") or ""
        upd["github_token"] = ""
        upd["github_token_configured"] = bool(gh_tok)
        data["update"] = upd
    # Drivers: scrub each configured driver's ADDRESSING (host/port/port_path/
    # extra) from the config surface. GET /api/config and the 'config' WS
    # broadcast are only view.status-gated, so without this a viewer could read a
    # driver's LAN host/DDNS or serial COM port straight off the config -- the
    # exact leak _redact_drivers_for closes on GET /api/drivers (this makes that
    # invariant hold system-wide, not just on the one surface). id/type/label/
    # enabled (non-secret) pass through; the source cfg is a model_dump copy, so
    # connect / resolve_driver_ids keep the real addressing.
    for d in data.get("drivers", []):
        if isinstance(d, dict):
            d["host"] = ""
            d["port"] = None
            d["port_path"] = ""
            d["extra"] = {}
    # W3 remote block: the ``device_token`` is a secret (it authenticates this home
    # to the relay). Blank it and surface a ``remote_token_configured`` boolean so
    # the UI can show 'configured' without the secret. ``relay_url`` / ``home_id``
    # / ``enabled`` are non-secret transport coordinates and pass through; a
    # convenience ``remote_configured`` marks 'enabled AND a relay_url set'.
    # Rebuilt defensively — a missing/malformed remote dict can't slip the token.
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


config_store = ConfigStore()
