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
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .events import bus
# The temperature-compensation model lives with the code that USES it
# (focus/tempcomp.py is pure: pydantic + a dataclass + arithmetic, no imports
# back into this module), so it is imported rather than re-declared here. Every
# other block below is declared HERE precisely because its module imports the
# config store -- ``dew.py`` and ``planning.py`` both do, so declaring their
# models in those files and importing them from here would close a cycle.
from .focus.tempcomp import TempCompConfig
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
    #: Clear aperture in millimetres. 0 = not set, which is the same "nobody
    #: filled this in" convention pixel_size_um and the sensor dimensions use
    #: (provenance.py's _CAMERA_FILLED note). No camera fallback exists and
    #: none is possible: a camera knows nothing about the telescope in front of
    #: it, so an unset aperture stays unset and f_ratio stays null rather than
    #: being computed from a guess.
    aperture_mm: float = Field(0.0, ge=0, le=5000)
    #: Focal reducer / extender factor, e.g. 0.8 for a 0.8x reducer, 2.0 for a
    #: Barlow. 1.0 means none.
    #:
    #: THIS DOES NOT CHANGE WHAT THE RIG FRAMES, and that is a deliberate
    #: refusal rather than an omission. focal_length_mm stays EXPLICIT: the
    #: number the framing maths, the plate solve hint and the FITS header all
    #: use is the one the operator typed, so nothing can silently multiply it
    #: behind their back. The reducer is recorded so the UI can offer "USE THE
    #: REDUCED FOCAL LENGTH", which writes focal_length_mm and is the ONLY
    #: thing that changes framing. Multiplying on save was considered and
    #: rejected: it turns a label into a control the user does not know they
    #: are operating.
    reducer: float = Field(1.0, gt=0, le=10)


# ------------------------------------------------------- automation (Batch 4b)
#
# Unattended-safety + alerting config. APPENDED to the existing AppConfig — the
# in-tree ``ConfigStore`` is reused verbatim (its ``_load`` already tolerates
# missing keys, so old config files deserialize unchanged). Named presets are
# the primary UX; the per-knob numerics are Advanced-only. The only secret kept
# at rest is an optional Telegram bot token, which ``redacted()`` blanks before
# the config is sent over WS/REST.

# Named safety presets. Each entry is the exact subset of SafetyConfig's
# numeric/advanced fields that DEFINES membership in that preset -- not a
# template that gets applied to a config, but the match criteria used to LABEL
# one. SafetyConfig._derive_preset_label (below) is the only code that reads
# this dict: on every construction (disk load, API body, direct construction)
# it compares the instance's fields named here against each entry and sets
# ``preset`` to the first name whose values all match, or "custom" if none do.
# There is deliberately no reverse direction -- nothing ever copies THESE
# values onto a SafetyConfig -- so editing this dict changes what future reads
# are LABELED, never any already-saved numerics (see the hazard note on
# _derive_preset_label for why a write-time patch was rejected).
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
    #: FALL BACK TO THE SKY ITSELF when safety is armed and no monitor is
    #: assigned - the shipped default, and the state this rig has run in for
    #: months. The engine already takes a cloud verdict off every frame (it logs
    #: "sky verdict: clear (200 bright stars, 17x noise)"), and until now that
    #: measurement fed only rule-driven holds. A Plan-built night with no
    #: instructions therefore had NOTHING watching the sky once it started: the
    #: weather veto guards the START, not the running night.
    #:
    #: A cloudy verdict engages the existing self-releasing hold - stand down
    #: the guider, probe, resume on a clear streak, and only abort+park if it
    #: runs past its bound - rather than the unsafe/park path. That is
    #: deliberately gentler than what `on_unsafe` would do, because the detector
    #: has known false modes and parking on one costs a re-slew and a re-solve
    #: for weather that may pass in ten minutes.
    #:
    #: NOT the forecast. On 2026-08-17 Open-Meteo said 100% cloud while the
    #: frames showed 200 bright stars at 17x noise; a forecast that wrong must
    #: never be allowed to park a mount. This reads the sky, not a prediction.
    #:
    #: Inert unless safety is armed AND no monitor is assigned, so a rig with a
    #: real monitor is untouched.
    sky_fallback_hold: bool = True
    #: Lead time for the live meridian-flip ETA chip (#239 stage A: moved off
    #: SequencePlan). Operator preference about how much warning they want, not
    #: a property of any one night. NOT part of SAFETY_PRESETS, so the preset
    #: detector below ignores it and a rig stays on its named preset.
    meridian_flip_warn_min: float = Field(15.0, ge=0, le=240)
    # floor is OFF until a SafetyMonitor or a custom horizon is configured (C1-4);
    # 0 = disabled. The UI sets 10 when the user enables the floor.
    min_alt_deg: float = 0.0
    #: Zenith keep-out: the altitude a target must stay BELOW. 90 = no
    #: ceiling (the zenith), which is the default so existing rigs are
    #: unchanged. Exists because a mount can foul its own tripod at HIGH
    #: altitude while still pointing at open sky, and every other limit
    #: here is a minimum (observed on the AM5N, 2026-07-30).
    max_alt_deg: float = Field(90.0, ge=0, le=90)
    horizon: list[tuple[float, float]] | None = None  # sorted (az,alt) control pts
    nogo_box: list[dict] | None = None     # optional [{az_min,az_max,alt_max}] pier guard
    enforce_pier_limits: bool = False      # only settable if mount reports pier side
    #: Minutes the engine will refuse to expose, waiting for a meridian flip it
    #: can prove has not happened, before handing the night on to the next
    #: target. 0 disables the invariant entirely.
    #:
    #: Twenty minutes because that is roughly what one failed flip plus one
    #: retry costs on this rig, and because the alternative on 2026-09-10/11
    #: was four hours of subs taken across the pier with the field walking 65
    #: arcsec/min. Every one of those frames was thrown away; twenty minutes of
    #: waiting would have been the cheapest outcome available that night.
    flip_owed_hold_min: float = Field(20.0, ge=0, le=240)
    twilight_deg: float = -12.0            # nautical default (C1-26)
    # Sun-exclusion cone (W1.10). ON by default to protect deep-sky gear; a
    # deliberate solar-astronomy session disarms it via solar_avoidance=False
    # (route-gated behind config.solar_override). RA/Dec-based => site-independent
    # (works on a default site with no lat/lon). Additive: legacy configs without
    # these keys deserialize with the protective defaults (avoidance ON).
    solar_avoidance: bool = True           # master enable; True = cone armed (deep-sky default)
    solar_exclusion_deg: float = Field(30.0, ge=0, le=90)  # cone half-angle; <=0 = inert
    # advanced numeric knobs. These VALUES are the source of truth; ``preset``
    # above is a READ-DERIVED label, not a switch that drives them (see
    # _derive_preset_label below) -- it reports the name of whichever
    # SAFETY_PRESETS entry these five fields match exactly, or "custom" if
    # none do. Editing them directly (the Advanced UI) needs no companion edit
    # to ``preset``; the label corrects itself on the next read.
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

    @model_validator(mode="after")
    def _derive_preset_label(self) -> "SafetyConfig":
        """READ-TIME derivation ONLY -- this NEVER writes a preset's numerics
        onto ``self``; it only ever corrects the ``preset`` string. Runs on
        every construction (disk load via ``AppConfig(**raw)``, an API request
        body, a bare ``SafetyConfig(...)`` in a test), so a stale/incoming
        label can't survive a read.

        An earlier draft of this fix did the opposite -- on write, if
        ``preset != "custom"``, copy ``SAFETY_PRESETS[preset]`` onto the
        instance. That is rejected: ``SAFETY_PRESETS["backyard"]`` equals this
        class's own field defaults exactly, so the patch is a silent no-op for
        an untouched config and a silent DESTRUCTIVE overwrite for anyone who
        hand-tuned ``on_unsafe``/``max_pause_min`` while leaving the label at
        "backyard" -- these numbers decide whether the roof closes over a
        running sequence. Deriving the label instead can never lose a user's
        values; the worst it does is report "custom" honestly.
        """
        for name, values in SAFETY_PRESETS.items():
            if all(getattr(self, field) == want for field, want in values.items()):
                if self.preset != name:
                    object.__setattr__(self, "preset", name)
                return self
        if self.preset != "custom":
            object.__setattr__(self, "preset", "custom")
        return self


class EscalationConfig(BaseModel):
    # all default to the gentle "warn" — never silently downgrade, never abort by default
    require_cooling: bool = False
    cooling_action: str = "warn"           # warn | abort | skip
    require_guiding: bool = False
    guiding_action: str = "warn"           # warn | abort | skip
    af_failure_action: str = "warn"        # warn | abort | skip
    hfr_reject_action: str = "warn"        # warn | discard | retake (retake = Advanced)
    hfr_retake_limit_per_target: int = 4   # cap per target (C1-7)
    #: The threshold `hfr_reject_action` ACTS ON (#239 stage A: moved off
    #: SequencePlan). It lived on the plan while its action lived here, which is
    #: one setting split across two layers for no reason. 0 = off.
    hfr_reject_factor: float = Field(0.0, ge=0, le=10)
    no_progress_watchdog_s: int = 0        # 0 = off
    reconnect_resume: bool = False         # Alpaca-only; off by default (C2-15)
    reconnect_retries: int = 1
    # Armed safety with NO monitor assigned. A registered-but-disconnected
    # monitor already fails CLOSED; an ABSENT one used to fail open, and the two
    # are the same situation to an operator. Default False because a monitor is
    # not part of a working rig and the shipped default is safety.enabled=True
    # with nothing assigned — failing closed here would refuse to image out of
    # the box. Off, the gap is now SAID (per run, and in pre-flight) instead of
    # silently permitted. On, an absent monitor is treated exactly like a
    # disconnected one, which is what an unattended night wants.
    require_safety_monitor: bool = False


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


#: The capability keys that route a provider, DERIVED from the model above so the
#: list can never drift from it. Three places need this exact tuple and each used
#: to spell it out by hand: ``providers.resolve_all`` (what status reports),
#: ``ConfigStore.set_providers`` (what a write validates), and ``Profile.row``
#: (which of a profile's ``providers`` keys are real overrides). A profile may
#: carry ANY key in its ``providers`` dict — anything outside this tuple is
#: inert: stored, exported, and read by nothing. Showing such a key as an
#: override would be a lie, so the tuple is the filter, not a suggestion.
PROVIDER_CAPABILITIES: tuple[str, ...] = tuple(ProvidersConfig.model_fields)


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
    #: #239 stage A, moved off SequencePlan. Dither distance is a function of
    #: this guide scope's image scale and recovery is standing behaviour; both
    #: are the same on every night this rig ever runs.
    dither_pixels: float = Field(3.0, ge=0, le=100)
    recover_guiding: bool = True
    # Guide-camera frame settings (2026-08-07; appended — old configs load
    # fine). The native guider reads these PER EXPOSURE, so a change applies
    # from the next guide frame; before this the loop's exposure was a
    # constructor-frozen 2.0 s no UI could reach — the same no-move-at-all trap
    # the polar solve settings closed. Defaults match the historical values.
    exposure_s: float = Field(2.0, gt=0, le=15)
    gain: int = Field(100, ge=0, le=1000)
    binning: int = Field(1, ge=1, le=4)
    #: #187 (2026-08-08): the guide loop has ALWAYS applied an offset —
    #: ``guide/native.py`` reads ``cfg["offset"]`` and passes it to every
    #: ``cam.expose`` — but ``GuideConfig`` had no field for it, so the
    #: constructor default was the only value it could ever have and the route
    #: answered with a LITERAL 30 that nothing could change. Same shape as the
    #: constructor-frozen exposure above; same fix.
    offset: int = Field(30, ge=0, le=255)
    #: GN-01 (2026-09-06). After a pier-side change, RE-MEASURE the calibration
    #: instead of mirroring the stored one. On the AM5N both sessions that
    #: started from a mirrored calibration ran the field away within minutes
    #: (00:43, 03:30); the fresh calibrations at 01:05 and 03:42 guided (the
    #: 02:47 one ran away too, under default loop params, which is the pulse
    #: overrun class fixed in the driver, not a flip). A harmonic-drive
    #: mount's flip is evidently not the clean geometric mirror the transform
    #: assumes. The mirror path stays reachable (set this False) for a mount
    #: that is known to want it; the cost of the default is one calibration
    #: walk per flip.
    recalibrate_after_pier_change: bool = True
    #: PHD2's ``CalFlipRequiresDecFlip``: this mount's meridian flip reverses
    #: the Dec sense as well as RA. ``guide/native.py`` has read it out of its
    #: config dict since the flip path was written, but nothing DECLARED it, so
    #: no rig could ever set it (the same shape as the offset above).
    flip_requires_dec_flip: bool = False
    #: GN-03 (2026-09-06). How many guide-star RE-LOCKS inside
    #: ``relock_window_min`` mean the field is walking rather than the star
    #: flickering. A re-lock resets the guide error to zero around a new star,
    #: so the RMS cannot see the jump — 2.3 arcsec was reported over 40 arcmin
    #: of walk. At this many the sequence engine stops shooting, re-centres by
    #: plate solve and recalibrates. 0 turns the gate off, the same convention
    #: the other thresholds here use; the cost of a false positive is one
    #: re-centre plus one calibration walk.
    relock_limit: int = Field(3, ge=0, le=100)
    relock_window_min: float = Field(10.0, gt=0, le=120)
    #: 2026-09-11. The re-lock gates above are the SEQUENCE ENGINE's, and they
    #: run from its per-frame loop -- so a paused run has none of them. On the
    #: night of 09-10/11 the guider re-locked twelve times inside 33 minutes,
    #: each onto a star 530 to 6141 arcsec away, accumulating 64 DEGREES, and
    #: walked the mount to 10 degrees altitude. Nothing was watching, because
    #: no frames were being taken.
    #:
    #: These two are the GUIDER's own limits, enforced in its own loop with no
    #: reference to any run, so they hold while paused, while idle, and for
    #: standalone guiding with no sequence at all. Either one stops guiding.
    #: 0 disables, the convention used throughout this file.
    #:
    #: Both are in ARCSEC and are inert unless the guide scope's image scale is
    #: known -- otherwise the displacements are pixels and a threshold in
    #: arcsec would mean nothing.
    relock_arcsec_limit: float = Field(300.0, ge=0, le=100000)
    relock_jump_arcsec: float = Field(120.0, ge=0, le=100000)
    #: 2026-09-10. How many CONSECUTIVE dither settle failures mean the field
    #: is walking. A settle failure means the guide error did not converge
    #: inside the 90 s settle window, where a stationary field converges in
    #: about 5 — so it very nearly measures "the field is moving and the loop
    #: is not winning". On the night the mount walked 3.19 deg the separation
    #: was total: 0 failures in 31 healthy dithers, and 14 of 14 during the
    #: walk. Two in a row is the gate because ONE can be a cloud crossing.
    #: Reaches the same hold as ``relock_limit`` — re-centre and recalibrate —
    #: and would have fired 55 minutes before the operator noticed. 0 is off.
    dither_settle_fail_limit: int = Field(2, ge=0, le=20)


# ------------------------------------------------- frame settings, by PURPOSE
#
# THE FOUR SCOPES. Until 2026-08-08 every camera setting had one home per
# SCREEN — four React ``useState``s seeded from hardcoded constants, plus a
# server-side polar pin — so "what filter is the rig using?" had a different
# answer on the Align screen (a pin nothing had acted on) and the Capture
# screen (the wheel). The operator set R on one and shot Oiii.
#
# The fix is not one global setting: a guide frame's exposure legitimately is
# not a light frame's, and a 0.3 s plate-solve frame is not either. What was
# missing was anything SAYING which is which. So the axis is PURPOSE, not
# screen — every surface that shoots a frame for a given purpose reads and
# writes the same scope, and a surface that needs a different number has to
# name a different scope rather than growing a private copy:
#
#   capture  imaging camera, the light frame the operator is composing
#   focus    imaging camera, thrown-away frames (higher gain is deliberate)
#   solve    imaging camera, plate-solve frames (polar TPPA + centring)
#   guide    the GUIDE camera — lives in ``GuideConfig`` above, because the
#            native guider already reads it per exposure and that model works.
#            Projected into the same ``frames`` payload so every client has one
#            vocabulary; see ``api/app.py::_frames_payload``.
#
# Plan steps stay per-step (``store.plan``): those are genuinely per-row.


class FrameSettings(BaseModel):
    """One purpose's frame settings on the IMAGING camera.

    ``filter`` is deliberately not like the others. Exposure/gain/offset/
    binning are settings; the wheel is a single physical resource, so a
    scope's ``filter`` is an INTENT to move it, and None means "leave the
    wheel wherever it is" — which is what every one of these code paths did
    before there was a field at all. The UI must render that intent as a
    transition (``Oiii → R``) and never as the wheel's position; see
    ``ui/src/components/ui/CameraPickers.tsx``.
    """
    exposure_s: float = Field(2.0, gt=0, le=3600)
    gain: int = Field(120, ge=0, le=1000)
    offset: int = Field(30, ge=0, le=255)
    binning: int = Field(1, ge=1, le=4)
    filter: str | None = None


class FrameSettingsConfig(BaseModel):
    """The imaging camera's three purpose scopes. Every default is the value
    the corresponding screen used to hardcode, so an existing rig's first load
    behaves exactly as it did — the change is where the number LIVES, not what
    it is."""
    #: CaptureView's old useState seeds ("2"/"120"/"30"/"1").
    capture: FrameSettings = Field(
        default_factory=lambda: FrameSettings(
            exposure_s=2.0, gain=120, offset=30, binning=1))
    #: FocusView's old capExposure/capGain/capBin ("2"/"200"/"1"). The higher
    #: gain is deliberate (lib/focusCapture.ts): a focus frame is thrown away.
    focus: FrameSettings = Field(
        default_factory=lambda: FrameSettings(
            exposure_s=2.0, gain=200, offset=30, binning=1))
    #: PolarAlignSession.SOLVE_DEFAULTS, which were themselves the values
    #: hardcoded at ``polar/native.py``'s capture call until 2026-08-07.
    solve: FrameSettings = Field(
        default_factory=lambda: FrameSettings(
            exposure_s=0.3, gain=200, offset=30, binning=1))


#: The scopes a client may name on ``/api/camera/frame-settings``. "guide" is
#: included and is NOT in ``FrameSettingsConfig``: it is stored in
#: ``GuideConfig`` (which the native guider already reads per exposure) and
#: projected into the same payload, so the client has one vocabulary and the
#: server keeps one home per value.
FRAME_SCOPES: tuple[str, ...] = ("capture", "focus", "solve", "guide")
#: The scopes that live in ``AppConfig.frames``.
IMAGING_FRAME_SCOPES: tuple[str, ...] = tuple(FrameSettingsConfig.model_fields)


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


class SyncPushConfig(BaseModel):
    """Where this rig pushes frames to, while the night is still going
    (file-sync Phase 2 — see ``astrodeck/sync/push.py``).

    OFF by default and inert with an empty ``path``: the runner reads this block
    every tick and does nothing at all until someone names a destination, so an
    upgrade adds no traffic and no disk cost to a rig nobody configures.

    ``path`` is whatever this machine can write to — the realistic Windows target
    is a mapped drive or a UNC share exported by the box that runs PixInsight.
    It is NOT redacted over the wire: a local path carries no credential, and the
    panel cannot honestly report where frames are going if it may not say where.

    ``limit_per_pass`` bounds one pass, so a first run against a 9 GB backlog is
    spread over several passes instead of holding the disk for twenty minutes.
    **0 means "use the runner's own cap"** (``runner.DEFAULT_PASS_LIMIT``), NOT
    unbounded: a genuinely unbounded first pass would hold the disk for the whole
    backlog with a guider running, and what a pass does not send this time it
    simply still owes next time — no bookkeeping, no lost frames.
    """
    enabled: bool = False
    kind: Literal["local_dir"] = "local_dir"
    path: str = ""
    label: str = ""                              # optional, for logs and the UI
    limit_per_pass: int = Field(0, ge=0, le=100000)


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


class CoolingConfig(BaseModel):
    """Camera cooler warm-down policy (2026-08-04 warm-ramp fix; appended — old
    configs load fine and get the protective defaults).

    Before this block existed, every warm path was a bare ``set_cooler(False)``
    and the sensor equalised with the room at ~5 °C/min — thermal shock plus
    in-chamber condensation, unattended, on the safety path. See
    ``astrodeck/cooling.py`` for where 2 °C/min comes from.

    ``warm_ambient_c = None`` means "work it out" (a rig-measured ambient if any
    backend reports one, else the assumed fallback). Setting it is for someone
    who KNOWS their observatory runs at, say, 8 °C in winter and wants the ramp
    to stop climbing there instead of assuming a heated room.

    ``warm_ramp = False`` restores the pre-fix cut-it-dead behaviour for a camera
    whose driver mishandles setpoint changes mid-warm. The hub logs loudly when
    it takes that path — an unannounced fallback to the bug is worse than the
    bug, because the product goes on claiming a safe ramp."""
    warm_ramp: bool = True
    warm_rate_c_per_min: float = Field(2.0, gt=0, le=20)
    warm_ambient_c: float | None = Field(None, ge=-50, le=60)
    #: THE STANDING COOLING REQUEST. None = nobody has asked for cooling (or the
    #: last thing that happened was a warm). A number is the operator's target,
    #: and it OUTLIVES the process — which is the point.
    #:
    #: Deliberately persisted rather than derived, unlike almost everything else
    #: here. "Is the cooler on?" is a question to ask the camera; "what did the
    #: operator ask for?" is not knowable from any device, and on 2026-08-09 a
    #: reconnect took the camera from -10 °C to cooler-off/target 0.0 °C with
    #: nothing logged and nothing left to restore from. The camera forgot, and
    #: so did we.
    #:
    #: Written by ``hub.cool_camera`` and cleared by every path that turns the
    #: cooler off, so it tracks INTENT and never drifts into a stale order to
    #: re-cool at dawn.
    setpoint_c: float | None = Field(None, ge=-60, le=40)
    #: How long to wait for the sensor to reach `cool_to` before the escalation
    #: policy decides (#239 stage A: moved off SequencePlan). A property of this
    #: camera and this ambient, not of tonight's target.
    cool_timeout_s: int = Field(600, ge=0, le=7200)


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


class CloudmapConfig(BaseModel):
    """GOES cloud-occlusion model (cloud-occlusion stage 6a design §7).

    DEFAULT OFF, and that is a decision rather than caution. The two ABI
    products cost 4.4 MB a cycle; a feature that silently started pulling 26 MB
    an hour on somebody's metered connection because they took an update is a
    bad citizen, and the operator who wants it can find one switch.

    IT DOES NOT GATE ANYTHING. Nothing in the sequence engine, the safety gate
    or auto-resume consults this model, by design and by a named test. The
    frames decide whether tonight is worth exposing; this says where in the sky
    the cloud is, which is a thing no scalar forecast can express and still not
    a reason to refuse to open.
    """
    enabled: bool = False
    #: Which GOES satellite to fetch. ``auto`` picks it from the site's own
    #: longitude and is the default; ``G18``/``G19`` remain as a manual
    #: override and always win.
    #:
    #: The numbers that make the choice matter, kept from when this was a bare
    #: G18 default: from the western United States GOES-18's zenith angle is
    #: 46.2 degrees against GOES-19's 64.7, which is a 2.9 km smeared pixel
    #: against 4.7 and a 9.4 km parallax error against 19.1 (design 2 §2.1).
    #: Those three metrics are one geometry read three ways, so they never
    #: disagree about which satellite is better -- see
    #: ``cloudmap/platform.py``, which owns the arithmetic and the crossover.
    #:
    #: WHY IT IS NO LONGER A BARE KNOB. G18 is right for the west and produces
    #: NOTHING for most of the country: east of the crossover the site falls
    #: outside GOES-West's CONUS sector, every cell reads no_data, and the only
    #: cure was a field an operator had no reason to know existed.
    #: ``test_cloudmap_service`` names that exact case. The site longitude was
    #: already sitting two blocks up in this same file.
    #:
    #: THE CROSSOVER IS 106.1 W, NOT THE "roughly 100 W" THIS COMMENT USED TO
    #: CLAIM. It is the midpoint of the two sub-satellite longitudes (-137.0
    #: and -75.2) and, because both satellites are on the equator, it is the
    #: same at every latitude. The old wording was wrong by 6.1 degrees --
    #: 520 km at latitude 40 -- and contradicted by the geometry design's own
    #: worked example, which has 105 W already preferring GOES-EAST (55.5 deg
    #: against 56.7). 100 W was folklore borrowed from US regional geography.
    #:
    #: MIGRATION -- READ BEFORE ASSUMING THIS REACHES ANYBODY. ``_save`` writes
    #: ``cfg.model_dump()`` with no ``exclude_defaults``, so every config file
    #: already on disk contains a literal ``"platform": "G18"``. That is the
    #: safe direction (no rig silently changes satellite on upgrade) and the
    #: useless one: an existing install stays pinned to a value it never chose
    #: and never sees ``auto``. Worse, the stored ``"G18"`` is byte-identical
    #: whether it was a written-out default or a deliberate operator override,
    #: and ``AppConfig`` carries no schema_version to tell them apart, so a
    #: read-time migration cannot be written safely after the fact. Reaching
    #: existing rigs needs a deliberate one-shot -- ask, or bump a version --
    #: not a quiet rewrite of a value that might be somebody's choice.
    platform: Literal["auto", "G18", "G19"] = "auto"
    #: Ten minutes, not five. The products refresh every five and the cloud
    #: pattern's own lifetime is under forty (design 2 §2.6), so a ten-minute
    #: cadence loses nothing a five-minute one would have caught and halves the
    #: traffic on a rig that is also carrying the relay tunnel and, on an
    #: imaging night, uploading frames. Below five is REFUSED rather than
    #: clamped: it cannot produce fresher data than the satellite publishes, so
    #: a caller asking for it has misunderstood something and should be told.
    poll_minutes: int = Field(10, ge=5, le=60)
    #: Half-width of the fetched window, in grid cells. 100 is a 201-cell box,
    #: which on the 2 km mask reaches 200 km either way -- past the 151 km the
    #: occlusion ladder walks at its 5 degree floor -- and is stage 5's default
    #: correlation window.
    half_px: int = Field(100, ge=16, le=400)


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
#: ``asiair`` is the ASIAIR's MAIN JSON-RPC port. The client opens 4700/4400/4801
#: itself from the host — this entry only lets an ASIAIR driver be added with a
#: bare IP and gives the driver row an honest number to display.
DRIVER_DEFAULT_PORTS: dict[str, int] = {"nina": 1888, "alpaca": 11111,
                                        "phd2": 4400, "asiair": 4700}


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


#: The shipped eccentricity ceiling, named because the schema-2 migration has
#: to write the SAME number the model defaults to. See StandardsConfig below.
DEFAULT_MAX_ECCENTRICITY = 0.65


class StandardsConfig(BaseModel):
    """The operator's standards for a usable frame, and the focus policy that
    keeps frames usable (#239 stage A).

    These were plan fields, which meant a flow-driven night could not have them
    at all: `flows/to_plan.py` sets eight plan fields and every quality gate
    took the model default of "off". So a graph-built night ran with no star
    floor, no guide-RMS ceiling, no eccentricity ceiling and no HFR rejection,
    silently, and there was nowhere to say otherwise.

    Every default below is the value `SequencePlan` carried before the move, so
    a rig that never opens Settings behaves exactly as it did.

    NAMING HAZARD: `WcsStampConfig.min_stars` is a DIFFERENT setting - the floor
    below which a saved light is not WCS-stamped. This one is the floor below
    which a frame is REJECTED. Do not consolidate them.
    """
    #: Shift the focuser by the per-filter offset on a filter change. A property
    #: of the filter set.
    apply_filter_offsets: bool = True
    #: Refocus when the focuser temperature has drifted this far since the last
    #: focus run. A property of the OTA and focuser. 0 = off.
    refocus_on_temp_delta_c: float = Field(0.0, ge=0, le=50)
    #: Reject a light frame with fewer than this many stars. 0 = off.
    #: NOT `wcs_stamp.min_stars` - see the class docstring.
    min_stars: int = Field(0, ge=0, le=100000)
    #: Reject a light frame taken while guide RMS exceeded this, arcsec. 0 = off.
    max_guide_rms: float = Field(0.0, ge=0, le=60)
    #: Reject a light frame whose median star eccentricity exceeds this, 0..1,
    #: or whose elongated-star fraction exceeds the companion limit derived
    #: from it (see `sequence.policy.ECC_ELONGATED_FRACTION`). 0 = off.
    #:
    #: 0.65 is MEASURED, not chosen: over every full frame of 2026-09-06 the
    #: clean subs read median <= 0.56 and the trailed ones 0.61 upward. It
    #: shipped at 0 -- off -- and so every staircase-trailed sub of that night
    #: was accepted at HFR 3.10 and stacked in. A stored 0 written before the
    #: schema-2 migration is raised to this default exactly once (see `_load`);
    #: after that a 0 is the operator saying "off".
    max_eccentricity: float = Field(DEFAULT_MAX_ECCENTRICITY, ge=0, le=1)
    #: Give up on a STEP after this many consecutive rejects. 0 = off.
    max_consecutive_rejects: int = Field(10, ge=0, le=1000)
    #: End the NIGHT after this many consecutive rejects across all targets.
    #: 0 = off.
    max_consecutive_rejects_night: int = Field(20, ge=0, le=1000)


class FocusConfig(BaseModel):
    """How the focuser is DRIVEN -- in a sweep and outside one (2026-09-08).

    Not what the sweep measures and not how wide it is -- those are the
    engine's geometry and the measured span. This is the mechanical half: which
    way the drawtube is moving when it arrives.

    WHY IT EXISTS, in numbers. The EAF on this rig has backlash measured in the
    tens of steps. The sweep walks DOWNWARD through its points, so every point
    of the curve is reached moving IN and the slack sits on one side -- but the
    validation frame and the final settle go UP to the fitted vertex, so they
    are reached moving OUT and the tube stops that many steps short of the
    position the curve named. On the virtual-clock replay of 2026-09-08 that
    put the run's final physical position 39 steps below its commanded one, and
    made the validation frame read 4.78 px where a swept neighbour one step
    away had measured 3.50 -- 36 percent worse, which trips the
    confirm-the-fit override, which then moves ONE step in: a reversal shorter
    than the slack, so it turns the motor and not the tube.

    Overshooting an OUT target and returning to it makes the last leg of every
    move an IN move, so every frame of the sweep and the final position are
    reached the same way the curve was measured. It is the astro-focus
    ``Backlash`` Overshoot model with only OUT backlash set, applied by the
    host (the engine's own backlash layer is left at its defaults, because
    turning it on there would also reverse the sweep direction).

    IT COVERS THE PER-FILTER OFFSET MOVE TOO, and that is the move it matters
    most to. The sequence engine shifts focus by the offset delta on every
    filter change (``engine._apply_filter``), and on this rig those deltas are
    18 to 20 steps against about 40 steps of slack -- so an OUTWARD offset move
    turns the motor and never moves the tube, and L and G shoot at R and B's
    focus while the log says the offset was applied. A sweep at least measures
    itself afterwards; a 20-step offset move measures nothing, so a knob that
    only the sweep obeyed would have left the worse half uncorrected.

    WHAT OBEYS IT, exactly: the native sweep (``focus.native``) and the
    engine's filter-offset move, both through ``focus.approach.approach``. What
    does NOT: the legacy numpy sweep, and ``POST /api/focuser/move`` — an
    operator driving the focuser by hand is watching it, and a move they did
    not ask for would be the surprise.

    0 disables it: a focuser with no measurable backlash pays two moves per
    outward step for nothing.

    WHERE IT IS SET. Through ``POST /api/config {"focus": ...}``, gated on
    ``config.safety`` beside the other blocks that protect the hardware and
    the run, and READ back on every ``/api/config`` so what the rig is doing
    is always visible. It can still be edited in ``astrodeck.json`` with the
    server stopped (the store loads the file once and rewrites it on every
    save, so an edit made while it is running is lost at the next write).

    THIS BLOCK USED TO SAY there was deliberately no write route, on the
    reasoning that an endpoint no screen calls is a feature no user can
    reach. That reasoning was right and the conclusion was backwards: the
    answer to a knob with no screen is a screen, and ``temp_comp`` below
    arrived needing one nightly (#D-RIG-2). The route is a fix, not a
    widening.
    """
    #: How far past an OUT target to travel before returning to it, in focuser
    #: steps. Comfortably larger than the tens of steps measured on the EAF,
    #: and small enough to cost well under a second of travel.
    approach_overshoot_steps: int = Field(200, ge=0, le=5000)
    #: Move the focuser between frames as the tube cools (#D-RIG-2). The model
    #: lives in ``focus/tempcomp.py`` next to the rule table that reads it; only
    #: the persistence is here. It is NOT
    #: ``standards.refocus_on_temp_delta_c`` -- that one is a TRIGGER that
    #: spends minutes on a sweep, this is an OFFSET that spends one short move.
    #: See ``TEMP_COMP_PRECEDENCE`` in that module for which runs first (both
    #: do).
    temp_comp: TempCompConfig = Field(default_factory=TempCompConfig)


# ------------------------------------------------------------ dew (#D-RIG-3)

class DewConfig(BaseModel):
    """Drive the dew heaters from the margin between air temperature and dew
    point (#D-RIG-3).

    THE MARGIN, NOT THE HUMIDITY. Relative humidity says how close the air is
    to saturation at ITS OWN temperature; the glass is colder than the air, so
    what actually decides whether it fogs is how many degrees the surface has
    left before it reaches the dew point. `weather.now` already publishes both
    numbers (weather.py surface_now), and their difference is the whole input.

    TWO THRESHOLDS, NOT ONE. A single "turn on below N degrees" makes the
    heater a switch, and a switch that flaps around one number is how a heater
    spends a night at 0 and 100 and never at 40. `margin_full_c` is where the
    heater reaches `max_power` and `margin_off_c` is where it reaches
    `min_power`; in between the power ramps linearly. The gap between them IS
    the hysteresis.
    """
    enabled: bool = False
    #: Margin (air temperature minus dew point, degrees C) at or below which
    #: the heater runs at ``max_power``. Negative is legal: the air can already
    #: be at its own dew point.
    margin_full_c: float = Field(1.0, ge=-5, le=20)
    #: Margin at or above which the heater drops to ``min_power``. Must be
    #: ABOVE ``margin_full_c`` -- the gap between them is the ramp, and the ramp
    #: is what keeps the heater off the two rails.
    margin_off_c: float = Field(5.0, ge=-5, le=30)
    #: Floor the ramp never goes below while the loop is running.
    min_power: int = Field(0, ge=0, le=100)
    #: Ceiling the ramp never goes above.
    max_power: int = Field(100, ge=0, le=100)
    #: Also heat the camera window, not only the objective.
    camera_window: bool = True
    #: How long a hand-set power level suppresses the loop before it takes the
    #: heaters back. 0 = the override never expires on its own.
    manual_override_s: int = Field(7200, ge=0, le=86400)
    #: How often the loop re-reads the weather and re-computes the ramp.
    interval_s: float = Field(120.0, ge=10, le=3600)

    @model_validator(mode="after")
    def _check_ramp(self) -> "DewConfig":
        """The two relational rules pydantic cannot express per-field.

        Both are ordering rules and both have the same failure mode if left
        unchecked: the ramp inverts silently, so the heater does the OPPOSITE
        of what the panel says, all night, with nothing to look at.
        """
        if self.margin_off_c <= self.margin_full_c:
            raise ValueError("dew.margin_off_c must be above margin_full_c")
        if self.max_power < self.min_power:
            raise ValueError("dew.max_power must be at least min_power")
        return self


# ----------------------------------------------------- planning (#D-PLAN-1/2)

#: How many targets the pool may hold. Named because the model constraint and
#: any message about a rejected save have to be the same number.
MAX_POOL = 200

#: The ceiling on every remembered per-slot map, and on the length of one key
#: in one. A wheel has eight slots; the cap exists so a malformed client cannot
#: grow the config file without bound, not to express a real limit.
_MAX_QUICK_KEYS = 64
_MAX_QUICK_KEY_LEN = 64


class QuickDefaults(BaseModel):
    """What the quick-plan sheet was left set to, so the next night opens where
    the last one did (#D-PLAN-1).

    Remembered rather than defaulted because the answer is a property of the
    rig and the operator, not of the software: which filters this wheel
    actually has, how long this f/5 refractor needs per sub, whether this
    operator dithers every three frames or not at all. A shipped default is a
    guess about all four.
    """
    model_config = ConfigDict(extra="forbid")
    #: How long the quick plan runs, in hours. Ignored when ``dawn`` is set.
    hours: float = Field(2.0, gt=0, le=24)
    #: "until dawn" was CHOSEN, and it is a different thing from a number of
    #: hours -- dawn is a different length every night, so remembering the
    #: hours it happened to work out to last time would silently shorten or
    #: overrun tonight. The flag is stored beside the number, never folded into
    #: it.
    dawn: bool = False
    #: Per-slot "shoot this filter", keyed by WHEEL SLOT NAME. ABSENT MEANS
    #: CHECKED: a wheel that gains a slot, or a rig whose slot names are
    #: re-typed, must not silently drop the new filter out of every plan.
    on: dict[str, bool] = Field(default_factory=dict)
    #: Per-slot exposure in seconds, keyed the same way. Finite positives only.
    exp: dict[str, float] = Field(default_factory=dict)
    #: The sheet's other toggles (dither, autofocus, and whatever the sheet
    #: grows), keyed by name so this block does not have to move when it does.
    extras: dict[str, bool] = Field(default_factory=dict)
    #: Dither every N frames. 0 = never.
    dither_n: int = Field(3, ge=0, le=100)
    #: Has anything ever been learned here? Without this an operator who
    #: genuinely wants every filter unchecked is indistinguishable from a rig
    #: that has never had a quick plan built on it, and the sheet cannot tell
    #: whether to seed itself from the wheel or to honour the empty maps.
    learned: bool = False

    @model_validator(mode="after")
    def _check_maps(self) -> "QuickDefaults":
        for name in ("on", "exp", "extras"):
            m = getattr(self, name)
            if len(m) > _MAX_QUICK_KEYS:
                raise ValueError(
                    f"planning.quick.{name} holds at most {_MAX_QUICK_KEYS} "
                    "keys")
            for key in m:
                if not key or len(key) > _MAX_QUICK_KEY_LEN:
                    raise ValueError(
                        f"planning.quick.{name} keys must be 1.."
                        f"{_MAX_QUICK_KEY_LEN} characters")
        for key, seconds in self.exp.items():
            # NaN and the infinities survive float() and every ge/le bound
            # pydantic can express, and a NaN exposure reaches the camera as a
            # NaN. `not (seconds > 0)` catches NaN as well as zero/negative.
            if not (seconds > 0) or seconds == float("inf"):
                raise ValueError(
                    f"planning.quick.exp[{key!r}] must be a positive, finite "
                    "number of seconds")
        return self


class PlanningConfig(BaseModel):
    """The planning surface's own persisted state (#D-PLAN-1/2)."""
    model_config = ConfigDict(extra="forbid")
    quick: QuickDefaults = Field(default_factory=QuickDefaults)
    #: The operator's shortlist of target ids, in THEIR order. A list and not a
    #: set: the order is the shortlist's running order, and re-sorting it would
    #: throw away the only thing the user actually did.
    pool: list[str] = Field(default_factory=list, max_length=MAX_POOL)

    @model_validator(mode="after")
    def _clean_pool(self) -> "PlanningConfig":
        """Trim, bound and de-duplicate, preserving first-seen order.

        De-duplication is SILENT because a double-tap on "add" is not an error
        the user needs told about. An empty or over-long id is NOT silent,
        because it is a client bug, and swallowing it would put a target in the
        pool that no lookup can ever resolve.
        """
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in self.pool:
            tid = (raw or "").strip()
            if not tid:
                raise ValueError("planning.pool entries must not be empty")
            if len(tid) > 64:
                raise ValueError(
                    "planning.pool entries must be at most 64 characters")
            if tid in seen:
                continue
            seen.add(tid)
            cleaned.append(tid)
        if cleaned != self.pool:
            # Written through __dict__: assigning through the model inside an
            # "after" validator re-enters validation.
            self.__dict__["pool"] = cleaned
        return self


#: On-disk shape of ``astrodeck.json``. Bump when a change needs a MIGRATION --
#: not for an added field, which pydantic already tolerates in both directions
#: (``_load`` fills defaults for keys an old file lacks).
#:
#: WHAT THE MARKER IS ACTUALLY FOR, because it is easy to expect too much of
#: it. It does NOT let you tell a written-out default from a deliberate
#: operator choice -- ``_save`` writes ``model_dump()`` with no
#: ``exclude_defaults``, so both look identical on disk, and no version number
#: recovers information the format never stored. See CloudmapConfig.platform
#: for the case that proves it.
#:
#: What it DOES buy is the two things that were impossible without it:
#:   - a migration can run EXACTLY ONCE per config, because afterwards the
#:     stamp says it already ran;
#:   - a file written by a NEWER build is recognisable as such, instead of
#:     being silently stripped of every field this build has never heard of.
#: A config with no stamp at all reads back as version 0, which is itself the
#: evidence "this predates the marker" that a future migration will want.
#:
#: 2 (2026-09-06, GN-04): `standards.max_eccentricity` gained a real default.
#: The stamp is what lets the migration tell a 0 that was the old built-in
#: from a 0 the operator typed, which is the exact distinction the note above
#: says a version number cannot usually recover -- it works here only because
#: the raise happens ONCE, on the way past 1, and never again.
CONFIG_SCHEMA = 2


def _stored_schema(raw: dict) -> int:
    """The stamp on disk, or 0 for a file written before the marker existed.

    Deliberately total. A stamp that is missing, null, a string, or nonsense
    all mean the same thing operationally -- we cannot trust it to say the file
    is current -- and the safe reading of "cannot trust" is the OLDEST version,
    because that makes a future migration run rather than skip. Guessing high
    would silently skip it.
    """
    try:
        return max(0, int(raw.get("schema_version") or 0))
    except (TypeError, ValueError):
        return 0


class AppConfig(BaseModel):
    #: See CONFIG_SCHEMA. 0 on a file written before the marker existed.
    schema_version: int = CONFIG_SCHEMA
    version: int = 1                   # bumped on every save (optimistic-concurrency token)
    site: Site = Field(default_factory=Site)
    optics: Optics = Field(default_factory=Optics)
    active_profile_id: str | None = None
    #: Which entry of the locations library the site currently came from, or
    #: None when the coordinates were typed in rather than applied. An id, not
    #: a copy: the coordinates themselves stay in ``site``, which is what every
    #: sky calculation reads.
    #:
    #: IT SITS OUT HERE, next to active_profile_id, and NOT inside
    #: PlanningConfig. A block a settings panel replaces WHOLESALE cannot hold
    #: a pointer the panel has never heard of -- the client would echo the
    #: block back without it and erase which location is applied, which is the
    #: same shape as cooling.setpoint_c being cancelled by a panel that had no
    #: field for it. Written by ``apply_location`` inside its existing single
    #: mutation (so the site and the pointer move in one atomic save) and
    #: cleared by the matching delete.
    active_location_id: str | None = None
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
    # --- per-purpose imaging-camera frame settings (#176, 2026-08-08; appended —
    #     old configs load fine and every default is the constant the screen it
    #     replaces used to hardcode) ---
    frames: FrameSettingsConfig = Field(default_factory=FrameSettingsConfig)
    # --- backend drivers (equipment-drivers spec; appended — old configs load fine) ---
    drivers: list[DriverEntry] = Field(default_factory=list)
    # --- rotator ROM/tolerance (rotator/CAA spec §3.2; appended — old configs load fine) ---
    rotator: RotatorConfig = Field(default_factory=RotatorConfig)
    # --- Sky-Atlas survey source (offline-pack spec §4; appended — old configs load fine) ---
    survey: SurveyConfig = Field(default_factory=SurveyConfig)
    # --- weather integration (sub-project C spec §2; appended — old configs load fine) ---
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    # --- GOES cloud-occlusion model (cloud-occlusion stage 6a §7; appended —
    #     old configs load fine and the default is OFF, so a rig that takes an
    #     update starts no new outbound traffic) ---
    cloudmap: CloudmapConfig = Field(default_factory=CloudmapConfig)
    # --- cooler warm-down ramp (2026-08-04; appended — old configs load fine and
    #     inherit the ramp, which is the whole point: the rigs that need it most
    #     are the ones nobody is going to go and enable it on) ---
    cooling: CoolingConfig = Field(default_factory=CoolingConfig)
    #: Could the camera this rig LAST CONNECTED cool? Server-owned and derived,
    #: never operator-edited — which is why it sits out here rather than inside
    #: CoolingConfig, whose whole block is wholesale-replaced by the settings
    #: panel. A field a client can erase by omission is not a durable record.
    #:
    #: WHY IT IS PERSISTED. The Plan tab's "this run has no target temperature"
    #: advisory needs to know whether there is a TEC to talk about, and
    #: ``Camera.can_cool`` only exists while a camera is connected. Reading it
    #: live meant the advisory appeared only if you happened to compile with the
    #: rig plugged in, which is not the state a rig is in at 19:00 — the exact
    #: hour the advisory exists to serve. False also means "no camera has ever
    #: connected", so a rig that has genuinely never had a cooler still says
    #: nothing rather than nagging. LAST-SEEN and not sticky-true: swapping to an
    #: uncooled camera and connecting it corrects the record.
    camera_can_cool_seen: bool = False
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
    # --- the rig's imaging standards (#239 stage A; appended - old configs load
    #     fine and every default is the value SequencePlan used to carry) ---
    standards: StandardsConfig = Field(default_factory=StandardsConfig)
    # --- file-sync push destination (Phase 2; appended — old configs load fine
    #     and the default is OFF, so nothing changes for anyone who ignores it) ---
    sync_push: SyncPushConfig = Field(default_factory=SyncPushConfig)
    # --- how the focuser is driven during a sweep (autofocus-efficiency lane 3,
    #     2026-09-08; appended — old configs load fine and INHERIT the overshoot,
    #     which is the point: the rigs that need it are the ones nobody is going
    #     to go and enable it on) ---
    focus: FocusConfig = Field(default_factory=FocusConfig)
    # --- dew heaters (#D-RIG-3; appended - old configs load fine and the
    #     default is OFF, so a rig that takes an update starts driving nothing
    #     it was not already driving) ---
    dew: DewConfig = Field(default_factory=DewConfig)
    # --- the planning surface's remembered state (#D-PLAN-1/2; appended - old
    #     configs load fine and `quick.learned` stays False, which is how the
    #     sheet tells "nothing was ever learned here" from "everything was
    #     deliberately unchecked") ---
    planning: PlanningConfig = Field(default_factory=PlanningConfig)


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


def _opt_num(value: object, cast) -> object | None:
    """``cast(value)``, or None when the slot carries no pin.

    Per-filter exposure and gain are TRI-STATE, unlike offsets: 0 is a real
    focuser offset meaning "no shift", but a 0-second exposure and a 0 gain are
    not "unset" — they are values, and a broadband slot legitimately wants
    gain 0. So "not pinned" has to be its own state, and it is ``None``
    end-to-end: in the file, on the device, over the API, and in the picker."""
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def save_filter_config(profile_id: str | None, names: list[str],
                       offsets: list[int],
                       opaque: list[bool] | None = None,
                       narrowband: list[bool] | None = None,
                       exposures: list | None = None,
                       gains: list | None = None) -> None:
    """Persist filter slot names + offsets (+ blackout and narrowband flags, and
    per-filter capture settings) for a profile (best-effort merge into the shared
    store; other profiles' entries are preserved). Every argument after
    ``offsets`` is optional so an older caller keeps working; when one is None
    its key is omitted and ``load_filter_config`` reports no such slot, exactly
    as before the field existed. All of them are user-assigned — no wheel
    reports any of them.

    ``exposures``/``gains`` hold ``None`` per unpinned slot rather than a
    sentinel number (see ``_opt_num``)."""
    data = read_json_or(FILTER_CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    entry = {
        "names": [str(n) for n in names],
        "offsets": [int(o) for o in offsets],
    }
    if opaque is not None:
        entry["opaque"] = [bool(o) for o in opaque]
    if narrowband is not None:
        entry["narrowband"] = [bool(n) for n in narrowband]
    if exposures is not None:
        entry["exposures"] = [_opt_num(e, float) for e in exposures]
    if gains is not None:
        entry["gains"] = [_opt_num(g, int) for g in gains]
    data[profile_id or _FILTER_DEFAULT_KEY] = entry
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


# --------------------------------------------------- focuser position reference
# A stepper focuser's position is a COUNT, not a measurement: nothing on the
# device knows where the drawtube physically is. On 2026-07-31 an EAF came back
# from a reconnect reporting position 0 when it had been at 30000 — the tube had
# not moved an inch, but every stored focus position silently stopped meaning
# what it used to. Nothing said so.
#
# So: remember what we last saw, and compare on connect. This CANNOT repair the
# reference (only the user can, by re-anchoring against something physical) —
# its whole job is to turn a silent lie into a stated one.
FOCUSER_STATE_FILE = CONFIG_DIR / "focuser_state.json"
_FOCUSER_DEFAULT_KEY = "__default__"


def load_focuser_position(key: str | None) -> int | None:
    """Last position we saw for this focuser, or None if we've never seen one."""
    data = read_json_or(FOCUSER_STATE_FILE, {})
    if not isinstance(data, dict):
        return None
    entry = data.get(key or _FOCUSER_DEFAULT_KEY)
    if not isinstance(entry, dict):
        return None
    try:
        return int(entry["position"])
    except (KeyError, TypeError, ValueError):
        return None


def save_focuser_position(key: str | None, position: int) -> None:
    """Record the focuser's position (best-effort merge; never raises).

    Called on every completed move, so it must stay cheap and must never be
    able to fail a move that actually happened."""
    try:
        data = read_json_or(FOCUSER_STATE_FILE, {})
        if not isinstance(data, dict):
            data = {}
        data[key or _FOCUSER_DEFAULT_KEY] = {"position": int(position)}
        write_json_atomic(FOCUSER_STATE_FILE, data)
    except Exception:  # noqa: BLE001 - bookkeeping must not break the focuser
        pass


# ------------------------------------------------- measured defocus response
# How wide a sweep has to be is a MEASUREMENT of the optical train, not a
# constant — see focus/span.py for the physics and the numbers. A successful
# sweep writes what it learned here; the next one reads it.
#
# ITS OWN FILE, NOT AN EXTRA KEY IN focuser_state.json. `save_focuser_position`
# above REPLACES its entry wholesale on every completed move, so a calibration
# living beside `position` would be erased by the very next focuser move — and
# erased silently, which is the worst version of it.
#
# A FUNCTION, NOT A MODULE CONSTANT. `CONFIG_DIR` is REBOUND per test (see
# `tests/conftest.py::_isolate_config_store`, and the three-week-old failure its
# docstring describes), so a path computed at import time captures the
# developer's real `server/config/` and writes there for the whole session. The
# neighbouring `FOCUSER_STATE_FILE` predates that fixture and has exactly this
# shape; this one does not repeat it.
def focus_calibration_path() -> Path:
    return CONFIG_DIR / "focus_calibration.json"


def load_focus_calibration(key: str | None):
    """This focuser's measured defocus response, or None if never measured.

    Returns a ``focus.span.FocusCalibration``. Imported lazily so `config` — which
    every module imports — does not pull the focus package in behind it.
    """
    from .focus.span import FocusCalibration
    data = read_json_or(focus_calibration_path(), {})
    if not isinstance(data, dict):
        return None
    return FocusCalibration.from_json(data.get(key or _FOCUSER_DEFAULT_KEY))


def save_focus_calibration(key: str | None, cal) -> None:
    """Record what a sweep measured (best-effort merge; never raises).

    A calibration is a by-product of a focus run that has already succeeded, so
    failing to store it must never turn that success into a failure."""
    try:
        data = read_json_or(focus_calibration_path(), {})
        if not isinstance(data, dict):
            data = {}
        data[key or _FOCUSER_DEFAULT_KEY] = cal.to_json()
        write_json_atomic(focus_calibration_path(), data)
    except Exception:  # noqa: BLE001 - bookkeeping must not break a focus run
        pass


def clear_focus_calibration(key: str | None) -> None:
    """Forget this focuser's measured span.

    Called when a sweep fails the ONE way a too-narrow span fails — a flat
    curve with no spread to fit. Without this a single bad calibration would
    narrow every subsequent sweep of the night into the same failure, with the
    rig getting further from focus each time. Forgetting sends the next attempt
    back to the shipped default, which is the geometry every successful focus
    run in this project's history used."""
    try:
        data = read_json_or(focus_calibration_path(), {})
        if not isinstance(data, dict):
            return
        if data.pop(key or _FOCUSER_DEFAULT_KEY, None) is None:
            return
        write_json_atomic(focus_calibration_path(), data)
    except Exception:  # noqa: BLE001 - bookkeeping must not break a focus run
        pass


# --------------------------------------------------------------------- pure math

def image_scale_arcsec_px(focal_mm: float, pixel_um: float, binning: int = 1) -> float:
    """Image scale in arcsec/pixel. ``ARCSEC_PER_RAD * px * bin / fl``."""
    if focal_mm <= 0:
        return 0.0
    return ARCSEC_PER_RAD * (pixel_um * binning) / focal_mm


def f_ratio(focal_mm: float, aperture_mm: float) -> float | None:
    """The focal ratio, or None when the aperture was never filled in.

    None IS THE POINT. There is no camera fallback and none is possible -- a
    camera knows nothing about the telescope in front of it -- so an unset
    aperture has to read as "we do not know", never as a plausible number
    derived from a guess. An f/5.3 printed next to a frame is taken as a fact
    about the rig, and a wrong one silently mis-sizes every exposure estimate
    built on it.

    THE REDUCER IS NOT APPLIED HERE, deliberately. ``Optics.reducer`` is a
    record, not a multiplier: if the operator pressed "use the reduced focal
    length" then ``focal_length_mm`` already carries it, and applying it again
    here would double-count. If they did not, the rig is genuinely framing at
    the explicit focal length and that is the ratio they are shooting at.
    """
    if focal_mm <= 0 or aperture_mm <= 0:
        return None
    return round(focal_mm / aperture_mm, 2)


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

def usable_login_methods(auth: "AuthConfig") -> list[str]:
    """Which enabled methods could ACTUALLY sign somebody in, right now.

    Not "which are ticked" — which would work if you loaded the login page. The
    difference is the whole of #205: ``methods: ["google"]`` with a blank
    ``google_client_secret`` is a ticked method that resolves to nothing, and
    the login page correctly renders no form at all.

    A break-glass ``admin_token`` counts wherever it is set — it bypasses the
    provider entirely and is exactly the escape hatch this situation calls for.
    """
    out: list[str] = []
    enabled = set(auth.methods_effective() or ())
    # Local is usable whenever it is enabled: either a user exists, or the
    # first-run form creates one. Both end with somebody signed in.
    if "local" in enabled:
        out.append("local")
    # Google needs BOTH halves of the client credential. An id with no secret
    # is the exact state that locked the rig out.
    if "google" in enabled and (auth.google_client_id or "").strip() \
            and (auth.google_client_secret or "").strip():
        out.append("google")
    if (auth.admin_token or "").strip():
        out.append("admin_token")
    return out


REQUIRE_AUTH_ENV = "ASTRODECK_REQUIRE_AUTH"
DIRECT_TOKEN_ENV = "ASTRODECK_TOKEN"
MIN_BEARER_TOKEN_BYTES = 32


def deployment_auth_required() -> bool:
    """Whether this process must remain authenticated for its whole lifetime."""
    raw = os.environ.get(REQUIRE_AUTH_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{REQUIRE_AUTH_ENV} must be a boolean value")


def _validate_bearer_token(value: str, name: str) -> str:
    token = (value or "").strip()
    if token and len(token.encode("utf-8")) < MIN_BEARER_TOKEN_BYTES:
        raise ValueError(
            f"{name} must contain at least {MIN_BEARER_TOKEN_BYTES} bytes of "
            "independently generated secret material"
        )
    return token


def network_auth_ready(auth: "AuthConfig", *, strict: bool) -> bool:
    """Whether auth is a real boundary for an exposed/managed listener.

    Strict mode is deliberately stronger than the loopback development posture:
    it rejects weak bearer tokens, legacy password records whose strength was
    never established, and an unauthenticated local first-run window.
    """
    direct_token = (os.environ.get(DIRECT_TOKEN_ENV) or "").strip()
    admin_token = (auth.admin_token or "").strip()
    # A configured bearer credential is never accepted below the minimum, even
    # on loopback.  ``strict`` controls the additional account/first-run checks,
    # not whether guessable tokens become valid credentials.
    direct_token = _validate_bearer_token(direct_token, DIRECT_TOKEN_ENV)
    admin_token = _validate_bearer_token(admin_token, "admin_token")
    methods = set(auth.methods_effective() or ())

    local_ready = False
    if methods:
        # Both local and Google authorization consult this database. A corrupt
        # store must never be treated as empty or bypassed by an old allowlist.
        from .auth.users import user_store

        users = user_store.list()
        if strict and "local" in methods:
            from .auth.passwords import CURRENT_PASSWORD_POLICY

            if auth.local_enabled_first_run:
                raise ValueError(
                    "local first-run setup must be disabled before an exposed "
                    "or managed listener can start; run create-admin"
                )
            legacy = [
                user for user in users
                if user.enabled and user.password_hash
                and user.password_policy_version < CURRENT_PASSWORD_POLICY
            ]
            if legacy:
                raise ValueError(
                    "local password records predate the current password policy; "
                    "reset them with create-admin before exposed startup"
                )
            local_ready = any(
                user.enabled and bool(user.password_hash)
                and user.password_policy_version >= CURRENT_PASSWORD_POLICY
                for user in users
            )
        elif "local" in methods:
            # Loopback development retains the explicit first-run workflow.
            local_ready = True

    google_ready = (
        "google" in methods
        and bool((auth.google_client_id or "").strip())
        and bool((auth.google_client_secret or "").strip())
    )
    return bool(direct_token or admin_token or local_ready or google_ready)


def enforce_required_auth(auth: "AuthConfig") -> None:
    """Reject a transition that would open a managed listener at runtime."""
    if deployment_auth_required() and not network_auth_ready(auth, strict=True):
        raise ValueError(
            f"{REQUIRE_AUTH_ENV} is enabled but no strong authentication "
            "method is ready"
        )


def _refuse_lockout(auth: "AuthConfig") -> None:
    """Refuse a config that would leave NOBODY able to sign in.

    #205, and the reason it is enforced HERE rather than at one route: the rig
    locked itself out twice, and both times the write that did it was a
    perfectly ordinary save that happened to carry a blanked secret. No caller
    intended it, so no caller was going to check for it. This is the one place
    every auth write funnels through.

    THE EMPTY SET IS NOT A LOCKOUT. ``methods == []`` means authentication is
    OFF — every caller resolves to the open-default admin — which is the
    shipped default for a LAN rig with no exposure. Turning auth off is a
    decision someone can make; being unable to sign in after turning it ON is
    never one. So the guard fires only when auth is ENFORCED and nothing behind
    it works.

    The error names the three ways out, because the person reading it is by
    definition looking at a screen that will not let them in.
    """
    if not auth.methods_effective():
        return                       # auth off: open by design, not locked out
    if usable_login_methods(auth):
        return
    raise ValueError(
        "this would leave nobody able to sign in: authentication is enabled "
        "but no method can actually complete a login. Fix one of: enable the "
        "'local' method (existing accounts, or the first-run form), supply "
        "BOTH google_client_id and google_client_secret, or set a break-glass "
        "admin_token. (A blank google_client_secret with an id set is the "
        "usual cause — the UI only ever shows a redacted secret, so echoing "
        "that block back used to wipe it.)")



#: How far two sets of coordinates may differ and still be the same place:
#: 1e-6 degrees, about 0.1 m. Far below any GPS fix, so it survives a round trip
#: through JSON and never matches two genuinely different sites. The same
#: tolerance ``api/app.py:_location_is_active_site`` compares with, deliberately
#: -- one idea of "the rig is standing here".
_SAME_PLACE_DEG = 1e-6


def _site_moved(old: "Site", new: "Site") -> bool:
    """Did the rig MOVE, as opposed to having its description edited?

    Latitude and longitude only. A rename, a corrected elevation or a new
    horizon floor are edits to how the same spot is described; a changed
    latitude is a different spot, and it is the only thing that can invalidate
    which saved location the site came from."""
    try:
        return (abs(float(old.latitude) - float(new.latitude)) > _SAME_PLACE_DEG
                or abs(float(old.longitude) - float(new.longitude))
                > _SAME_PLACE_DEG)
    except (TypeError, ValueError):     # a site without usable coordinates
        return True


def _unknown_keys(raw: dict) -> dict:
    """Top-level keys in the file that this build's AppConfig has no field for.

    Defined after the model because it reads ``AppConfig.model_fields``.
    """
    known = set(AppConfig.model_fields)
    return {k: v for k, v in raw.items() if k not in known}


class ConfigStore:
    """Module singleton (like ``hub``) owning the persisted ``AppConfig``.

    Loaded once from disk; a genuinely new store gets defaults + an immediate
    save. A missing or corrupt primary is restored from a valid backup. Existing
    but unreadable/invalid state never becomes an open-auth default: startup
    fails closed and leaves the evidence intact for operator recovery. Every
    mutating helper bumps ``version`` and writes atomically.
    """

    def __init__(self, path: Path = CONFIG_FILE):
        self._path = path
        self._cfg: AppConfig | None = None
        #: Top-level keys belonging to a NEWER build than this one, held so a
        #: downgrade does not silently delete them. See `_load`. Empty in every
        #: normal case.
        self._foreign: dict = {}
        # One thread at a time may materialise or persist this store. The store
        # is a process-wide singleton read from worker threads (every route that
        # does asyncio.to_thread -> hub.site lands here), and both of its disk
        # paths were unsynchronised:
        #   * cfg() lazy-loaded outside any lock, so N threads could each see
        #     ``_cfg is None`` and each run _load() -> _save();
        #   * write_json_atomic stages every write at ONE fixed "<path>.tmp", so
        #     the first os.replace consumed that tmp and every other writer
        #     raised FileNotFoundError.
        # Together, concurrent readers of a never-written store blew up.
        #
        # WHERE that is reachable, corrected: commit 5b2cd54 claimed a fresh
        # install hits this on its first concurrent requests. It cannot. The
        # SERVED app materialises the store single-threaded before it can accept
        # anything -- __main__._cmd_run calls _security_banner() -> cfg() on the
        # main thread before create_app(), api/app.py::_lifespan reads cfg()
        # twice before its yield, and uvicorn awaits lifespan startup before it
        # creates a listener (uvicorn/server.py Server.startup) -- and nothing in
        # a running server ever puts _cfg back to None. The only harness that
        # reaches the cold path concurrently is one with no lifespan at all: the
        # bare FastAPI() client fixture in tests/test_framing.py. So the OBSERVED
        # severity is CI-only (an intermittent red under xdist, presenting as
        # mosaic panels silently missing their transit_alt), not lost user data.
        #
        # The lock stays regardless: nothing ENFORCES that boot warm-up, so the
        # class is safe only by accident of call order today -- one new entry
        # point, script or reload() and it is a user's site being written twice
        # at once. Do not read the narrow blast radius as "this was fine".
        # RLock, not Lock: the load path re-enters through
        # _save()/_restore_from_bak() on the SAME thread.
        self._lock = threading.RLock()

    # -- loading ---------------------------------------------------------------

    def _bak_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".bak")

    def _restore_from_bak(self) -> AppConfig | None:
        """Try to load a valid ``AppConfig`` from the ``.bak`` copy. Returns the
        recovered config (already re-persisted as the primary) or ``None`` only
        when the backup is absent. An unreadable or invalid backup is a hard
        failure: silently replacing security state with defaults can disable
        authentication on a reverse-proxied deployment.

        The corrupt primary is deliberately left untouched when recovery fails.
        """
        bak = self._bak_path()
        try:
            raw = read_json(bak)
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            raise RuntimeError("configuration backup is unreadable or corrupt") from exc
        try:
            if not isinstance(raw, dict):
                raise ValueError("configuration backup must contain a JSON object")
            stored = _stored_schema(raw)
            cfg = AppConfig(**{**raw, "schema_version": stored})
        except Exception as exc:
            raise RuntimeError("configuration backup is invalid") from exc
        bus.log("warning", "config restored from backup (.bak)", "config")
        self._cfg = cfg
        # The backup came from a newer build too, if the primary did. Recovering
        # from corruption is not licence to also delete the settings this build
        # does not understand -- see `_load`, and note this write goes out
        # directly rather than through `_save`, so the merge has to be here as
        # well or the restore path quietly undoes the preservation.
        self._foreign = (_unknown_keys(raw) if stored > CONFIG_SCHEMA else {})
        body = cfg.model_dump()
        if self._foreign:
            body = {**self._foreign, **body}
        # Re-establish the primary from the good backup WITHOUT taking a fresh
        # backup: the (possibly corrupt) primary still on disk must not be copied
        # over the known-good ``.bak`` we just recovered from.
        ensure_dir(self._path.parent)
        write_json_atomic(self._path, body, backup=False)
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
        except OSError as exc:
            raise RuntimeError("configuration file cannot be read safely") from exc
        except ValueError as exc:
            recovered = self._restore_from_bak()
            if recovered is not None:
                return recovered
            raise RuntimeError(
                "configuration is corrupt and no valid backup is available"
            ) from exc
        try:
            if not isinstance(raw, dict):
                raise ValueError("configuration must contain a JSON object")
            # tolerate missing keys (forward/back-compat with the automation
            # surface, which appends keys later) — pydantic fills defaults.
            #
            # THE STAMP IS READ BEFORE THE MODEL IS BUILT, and it has to be:
            # `schema_version` defaults to CONFIG_SCHEMA, so `AppConfig(**raw)`
            # on a file that has no stamp would invent one and destroy the only
            # evidence that the file predates the marker.
            stored = _stored_schema(raw)
            cfg = AppConfig(**{**raw, "schema_version": stored})
        except Exception as exc:  # invalid shape
            recovered = self._restore_from_bak()
            if recovered is not None:
                return recovered
            raise RuntimeError(
                "configuration is invalid and no valid backup is available"
            ) from exc

        # A FILE FROM A NEWER BUILD IS NOT OURS TO REWRITE. `AppConfig` is a
        # plain BaseModel, so pydantic's default `extra="ignore"` drops every
        # key this build has never heard of -- and the next `_save` writes the
        # stripped version back, permanently deleting settings that belong to
        # the build the operator is about to return to. Downgrading used to
        # cost them silently. Their keys are held here and merged back on save.
        # ONLY for a newer stamp: on an equal or older one an unknown key is a
        # field we deliberately removed, and resurrecting those for ever is a
        # different bug.
        self._foreign = (_unknown_keys(raw) if stored > CONFIG_SCHEMA else {})
        if self._foreign:
            bus.log("warning",
                    "config was written by a newer AstroDeck (schema "
                    + str(stored) + " against this build's "
                    + str(CONFIG_SCHEMA) + "); "
                    + str(len(self._foreign))
                    + " setting(s) this build does not understand are being "
                    "preserved untouched",
                    "config")

        # MIGRATIONS. Each is keyed off `stored` -- the stamp read off disk
        # before the model was built -- never off `cfg.schema_version`, which
        # pydantic would have invented for an unstamped file. There was no 0->1
        # change worth making (everything before the marker was additive); the
        # stamp existed so this one would have a floor.
        #
        # 1 -> 2 (GN-04, 2026-09-06). `standards.max_eccentricity` shipped at 0
        # = no eccentricity gate at all, and on the night of 2026-09-06 every
        # staircase-trailed sub was accepted at HFR 3.10 and stacked in because
        # of it. The default is now 0.65 (measured; see StandardsConfig), but a
        # rig that already has a config on disk would go on running with the
        # gate off for ever -- the fix shipped to nobody. A 0 written under
        # schema 1 was the built-in, not a decision, because there was no UI
        # state and no default that could mean anything else, so it is raised
        # ONCE here. From schema 2 on, a 0 is the operator turning the gate off
        # and is left alone.
        if stored < 2 and cfg.standards.max_eccentricity == 0:
            cfg.standards.max_eccentricity = DEFAULT_MAX_ECCENTRICITY
            bus.log("info",
                    "frame eccentricity rejection is now on by default at "
                    + f"{DEFAULT_MAX_ECCENTRICITY:.2f}"
                    + " - this config had it off, which was the old built-in "
                      "rather than a setting anyone chose. Trailed subs will "
                      "now be rejected; set it back to 0 in Settings to shoot "
                      "without the gate.",
                    "config")

        if cfg.schema_version < CONFIG_SCHEMA:
            cfg.schema_version = CONFIG_SCHEMA
            self._cfg = cfg
            self._save()
        return cfg

    def cfg(self) -> AppConfig:
        if self._cfg is None:
            with self._lock:
                # Re-check inside the lock: whoever held it may already have
                # materialised the store, and a second _load() would write the
                # file a second time (the collision described in __init__).
                if self._cfg is None:
                    self._cfg = self._load()
        return self._cfg

    def _save(self) -> None:
        with self._lock:
            cfg = self._cfg
            if cfg is None:
                return
            ensure_dir(self._path.parent)
            body = cfg.model_dump()
            # See `_load`: a newer build's keys ride through untouched, and
            # ours win on any collision -- they are the ones this build just
            # edited.
            if self._foreign:
                body = {**self._foreign, **body}
            write_json_atomic(self._path, body)

    def reload(self) -> AppConfig:
        """Force a re-read from disk (used by tests)."""
        with self._lock:
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
        enforce_required_auth(cfg.auth)
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
        # TYPED-IN COORDINATES POINT AT NO SAVED LOCATION. ``active_location_id``
        # means "the site came from THIS row of the library" (see AppConfig), and
        # this route is the one that types coordinates in by hand. Leaving the
        # pointer where it was left the Sky hub naming a saved site while the rig
        # stood somewhere else -- a label that contradicts the numbers beside it,
        # and a "re-apply" that would silently move the mount back.
        #
        # ONLY ON A MOVE. A save that renames the site or corrects its elevation
        # while the coordinates hold is still the same place, and clearing the
        # pointer there would deselect a location for a typo fix.
        if _site_moved(cfg.site, site):
            cfg.active_location_id = None
        cfg.site = site
        return self.bump_and_save()

    def clear_active_location(self, loc_id: str) -> bool:
        """Forget the active-location pointer if it names ``loc_id``.

        For the caller that MOVED that row: ``PUT /api/locations/{id}`` can
        rewrite the latitude and longitude of the very row the site was applied
        from, and it does not push them into ``config.site`` (a library edit is
        not a request to move the mount). The pointer then names a location
        whose coordinates are not the ones the rig is using, which is the same
        broken claim ``set_site`` clears above.

        Returns whether anything changed, so a caller can skip the version bump
        -- a save that changes nothing costs every open client its field-level
        write token."""
        cfg = self.cfg()
        if cfg.active_location_id != loc_id:
            return False
        cfg.active_location_id = None
        self.bump_and_save()
        return True

    def set_site_and_safety(self, site: Site, safety: SafetyConfig,
                            expected_version: int | None = None) -> AppConfig:
        """Move the site AND its safety floor in ONE save.

        Applying a saved location changes two blocks that only make sense
        together: the coordinates the engine plans from, and the drawn horizon
        it gates slews with. Written as ``set_site`` then ``set_safety`` that is
        two ``bump_and_save`` calls, and a failure between them leaves the new
        coordinates live against the PREVIOUS site's horizon — a tree line from
        somewhere else, applied to tonight, with a version number claiming the
        config is whole. One mutation, one atomic file write, one version bump:
        the partial state is not reachable."""
        self._check_version(expected_version)
        cfg = self.cfg()
        # a user-applied site is, by definition, no longer the default.
        cfg.site = site.model_copy(update={"is_default": False})
        cfg.safety = safety
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

    def set_standards(self, standards: "StandardsConfig") -> AppConfig:
        """Persist the rig's imaging standards (#239 stage A).

        Wholesale-replace, like set_safety and set_cooling: the panel echoes the
        full block back with its edit applied. Nothing here is exempt - unlike
        cooling's live `setpoint_c`, every field in this block is policy the
        panel owns.
        """
        cfg = self.cfg()
        cfg.standards = standards
        return self.bump_and_save()

    def set_focus(self, focus: "FocusConfig") -> AppConfig:
        """Persist how the focuser is DRIVEN -- the approach overshoot and the
        temperature-compensation block.

        Wholesale-replace, like set_safety and set_standards: the panel echoes
        the whole block back with its edit applied. Nothing here is exempt from
        that, and the one field that looks like it should be --
        ``temp_comp.reference_temp_c`` / ``reference_position``, which the run
        re-anchors -- deliberately is not: the reference is only meaningful
        with the coefficient it was measured against, so an operator who
        retypes ``steps_per_c`` MUST invalidate the reference in the same save.
        Carrying it forward would compensate tonight's drift off a reference
        taken under a coefficient nobody uses any more.
        """
        cfg = self.cfg()
        cfg.focus = focus
        return self.bump_and_save()

    def set_dew(self, dew: "DewConfig") -> AppConfig:
        """Persist the dew-heater policy (#D-RIG-3). Wholesale-replace, like
        set_safety -- every field in this block is policy the panel owns, and
        the live heater power is not stored here at all (it is a device
        reading, re-derived from the margin on every tick)."""
        cfg = self.cfg()
        cfg.dew = dew
        return self.bump_and_save()

    def set_planning(self, planning: "PlanningConfig") -> AppConfig:
        """Persist the planning surface's remembered state (#D-PLAN-1/2).

        Wholesale-replace, and it is safe to be: ``planning`` has its own route
        rather than riding ``POST /api/config``, so the only client that sends
        this block is the one that owns both halves of it. That is also why
        ``active_location_id`` is NOT in here -- see AppConfig.
        """
        cfg = self.cfg()
        cfg.planning = planning
        return self.bump_and_save()

    def set_cooling(self, cooling: "CoolingConfig") -> AppConfig:
        """Persist the cooler warm-down POLICY (rate / assumed ambient / whether
        the ramp runs at all). Wholesale-replace, like set_safety — the UI echoes
        the full block back with its edit applied.

        ``setpoint_c`` IS SET ONLY WHEN IT WAS ACTUALLY SENT, and that
        distinction is the whole of this method.

        Absent must mean unchanged. The settings panel POSTs the entire
        CoolingConfig block and its client type has no setpoint field, so a
        plain replace cancels the standing cooling request every time somebody
        nudges the warm rate at 21:00 — the same shape as the alert-token guard
        in the config route: a client that never had the value must not be able
        to erase it by omission.

        But the carry-over used to be UNCONDITIONAL, which made the setpoint
        unwritable through this route at all: ``POST /api/config`` with
        ``{"cooling": {"setpoint_c": -15}}`` answered 200 and threw the value
        away, with no way for the caller to tell "saved" from "discarded". On
        2026-08-22 that cost 19 light frames at +23 °C against a -10 °C dark
        library, because the only remaining writer was a side effect of
        ``POST /api/camera/cooler`` and nobody had pressed it.

        ``model_fields_set`` is what tells an omitted field from an explicitly
        sent one, so an explicit ``null`` still clears the setpoint and still
        means "no cooling intent" — a rig with no cooler is a real rig — while
        a block that never mentions it leaves the operator's number alone.
        """
        cfg = self.cfg()
        if "setpoint_c" not in cooling.model_fields_set:
            cooling = cooling.model_copy(
                update={"setpoint_c": cfg.cooling.setpoint_c})
        cfg.cooling = cooling
        return self.bump_and_save()

    def remember_camera_can_cool(self, can_cool: bool) -> None:
        """Record whether the just-connected camera has a TEC
        (``AppConfig.camera_can_cool_seen``).

        Writes ONLY on a change. Every connect would otherwise bump the config
        version and rebroadcast the whole config to every client for a fact that
        almost never moves. Never raises: this is a derived convenience for an
        editor advisory, and it must not be able to fail a connect."""
        try:
            if self.cfg().camera_can_cool_seen == bool(can_cool):
                return
            self.cfg().camera_can_cool_seen = bool(can_cool)
            self.bump_and_save()
        except Exception:       # noqa: BLE001 - an advisory hint, never a fault
            pass

    def set_cooling_setpoint(self, setpoint_c: float | None) -> AppConfig:
        """Record (or clear) the standing cooling request — see
        ``CoolingConfig.setpoint_c``. Separate from ``set_cooling`` precisely so
        the two cannot overwrite each other."""
        cfg = self.cfg()
        cfg.cooling = cfg.cooling.model_copy(
            update={"setpoint_c": None if setpoint_c is None else float(setpoint_c)})
        return self.bump_and_save()

    def set_cloudmap(self, cloudmap: "CloudmapConfig") -> AppConfig:
        """Persist the GOES cloud-occlusion block. Wholesale-replace, like
        set_safety.

        IT HAD NO WRITER AT ALL until 2026-08-24. Stage 6a shipped the model,
        the service, the poller and three read routes, and CloudmapConfig
        defaults to ``enabled=False`` on the reasoning that a feature pulling
        26 MB an hour should be opt-in -- but nothing anywhere could set it.
        ConfigPatchBody is ``extra="forbid"``, so POST /api/config with a
        cloudmap block 422'd at binding, and there is no /api/config/cloudmap.
        4,357 lines and seven test files behind a switch that did not exist:
        the model had never fetched a granule on the rig.

        The same defect class as cooling.setpoint_c, one step earlier -- there
        the route discarded the value, here there was no route to discard it.
        """
        cfg = self.cfg()
        cfg.cloudmap = cloudmap
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
        _refuse_lockout(auth)
        enforce_required_auth(auth)
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
        resolving to auto forever.

        Then validated AGAIN, per capability (audit finding O). The vocabulary
        above is capability-BLIND: it answers "is this a driver we know?", not
        "would pinning it here change anything?". So ``solve = "astrodeck"``,
        ``autofocus = "astap"`` and ``guide = "sim"`` used to be accepted and
        stored, and then resolved exactly as ``auto`` — a control that silently
        does nothing. ``providers.is_honourable`` answers the second question
        off the same table ``resolve`` dispatches through."""
        from . import providers as _providers   # local: providers imports config
        valid = self.valid_override_values()
        for cap in PROVIDER_CAPABILITIES:
            v = getattr(providers, cap, "auto")
            if v not in valid:
                raise ValueError(
                    f"unknown provider for {cap}: {v!r} — valid values are "
                    f"auto, backend (legacy), an implicit driver id "
                    f"({', '.join(IMPLICIT_DRIVER_IDS)}), or a configured "
                    f"driver id")
            if not _providers.is_honourable(cap, v):
                raise ValueError(
                    f"{v!r} cannot provide {cap}: nothing resolves it, so "
                    f"pinning it would behave exactly like 'auto'. Valid "
                    f"choices for {cap} are "
                    f"{', '.join(sorted(_providers.honoured_families(cap)))} "
                    f"(or a driver id belonging to one of those families)")
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

    # -- per-purpose frame settings (#176) -------------------------------------

    def set_frames(self, frames: "FrameSettingsConfig") -> AppConfig:
        """Persist the imaging camera's three purpose scopes.

        Every numeric bound is declared on ``FrameSettings`` (pydantic
        ``Field`` ge/le), so the route 422s an out-of-range value before it
        reaches here. What is left is the FILTER, which pydantic cannot check:
        it is a name, and a name that no wheel slot carries is a pin that will
        silently do nothing on the next frame — the exact class of failure this
        whole model exists to close. The route validates it against the
        connected wheel (it is the only thing that knows the slot names); this
        setter's job is to reject the shapes that are wrong with no wheel at
        all, so a scripted caller cannot store ``""`` and have it read back as
        "a filter is pinned"."""
        for scope in IMAGING_FRAME_SCOPES:
            fs: FrameSettings = getattr(frames, scope)
            if fs.filter is not None and not fs.filter.strip():
                raise ValueError(
                    f"{scope}: a blank filter name is not 'no filter' — send "
                    "null to leave the wheel where it is")
        cfg = self.cfg()
        cfg.frames = frames
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

    def set_calibration(self, calibration: "CalibrationConfig") -> AppConfig:
        """Persist the master-library matching + stacking tolerances.

        Every bound is already declared on the model (pydantic Field ge/le), so
        an out-of-range value 422s at the route before it reaches here. The
        remaining rule is a RELATIONAL one pydantic cannot express: a temp bin
        narrower than the temp match tolerance means two frames can match each
        other yet land in different stacking buckets, which silently halves the
        depth of every master. Rejected rather than quietly reconciled."""
        if calibration.temp_bin_c < calibration.temp_tol_c:
            raise ValueError(
                f"the temperature bin ({calibration.temp_bin_c}°C) must be at "
                f"least the match tolerance ({calibration.temp_tol_c}°C) — a "
                "narrower bin splits frames that matched into separate stacks")
        cfg = self.cfg()
        cfg.calibration = calibration
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

    # -- file-sync push destination (Phase 2) ----------------------------------

    def set_sync_push(self, sync_push: "SyncPushConfig") -> AppConfig:
        """Persist the push destination.

        ONE write-time rule, and it is the honesty rule: **enabled with no path
        is refused** (route maps ValueError -> 422). A destination that is on and
        goes nowhere is the exact shape this feature is supposed to remove — the
        panel would show "syncing", the runner would find nothing to write to,
        and the night would sit on the rig anyway. Better to make it impossible
        to save than to explain it in a tooltip.

        The path is NOT checked for existence here. A NAS that is asleep at
        06:00 and awake at 22:00 is normal, and a config write that fails
        because the far end happens to be down would teach the operator to
        configure this at exactly the wrong time of day. Reachability is a fact
        the runner reports every pass, not a precondition for saving.
        """
        if sync_push.enabled and not sync_push.path.strip():
            raise ValueError("a sync destination path is required to enable push")
        cfg = self.cfg()
        cfg.sync_push = sync_push
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
    _validate_bearer_token(auth.admin_token, "admin_token")

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


#: The AlertSink fields ``redacted()`` BLANKS outbound (sets to ""), as opposed
#: to rewrites (``url``, whose userinfo is stripped but which keeps a real
#: value). Every field in here is one a client cannot echo back, so the config
#: route MUST treat an empty incoming value as "unchanged" for each of them
#: (see ``_merge_alert_verified``).
#:
#: THIS EXISTS SO THE TWO LISTS CANNOT DRIFT. They already had: ``chat_id`` was
#: added to the redaction and never added to the restore, so a plain
#: GET -> POST echo of the alerts array erased every telegram sink's chat_id and
#: reset ``verified`` to False on the way past. The sink stayed in the config,
#: could no longer deliver, and nothing reported it. A new redaction that
#: forgets its restore is a new eraser; deriving one from the other is what
#: makes that impossible rather than merely unlikely.
REDACTED_SINK_FIELDS: tuple[str, ...] = ("token", "chat_id")


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
        # The at-rest secrets: the Telegram bot token, and the chat id (a
        # private/identifying value). Blanked from REDACTED_SINK_FIELDS so the
        # config route's "empty means unchanged" restore is derived from the
        # same tuple and cannot fall behind a newly-redacted field.
        for _field in REDACTED_SINK_FIELDS:
            if sink.get(_field):
                sink[_field] = ""
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


# ------------------------------------------------- frame settings: one seam
#
# Declared AFTER the singleton because that is what they operate on. Both the
# REST route and ``PolarAlignSession`` go through these, so there is exactly
# one place that knows how a scope is merged, where it is stored and who is
# told about it. Two writers with two merge rules is how the Align screen and
# the Capture screen came to answer the same question differently.


def frames_payload(guider: object = None) -> dict[str, dict]:
    """Every scope's EFFECTIVE settings, in one dict.

    The shape carried by the WS ``hello`` (``hub.summary()``), by the ``frames``
    bus event and by ``GET /api/camera/frame-settings`` — one shape, so a client
    has one thing to apply from three doors.

    ``guider``, when given, overlays the guide scope with the RUNNING guider's
    live values. They can legitimately differ from the file: a binning change
    is refused mid-session, so the file may promise what the loop refused, and
    the loop is the thing that decides what the next frame looks like.
    """
    cfg = config_store.cfg()
    out: dict[str, dict] = {
        scope: getattr(cfg.frames, scope).model_dump()
        for scope in IMAGING_FRAME_SCOPES
    }
    g = cfg.guide
    # The guide camera has no wheel; ``filter`` is present and always null so
    # every scope has the same keys and no client needs a special case.
    out["guide"] = {"exposure_s": g.exposure_s, "gain": g.gain,
                    "offset": g.offset, "binning": g.binning, "filter": None}
    live = None
    if guider is not None and hasattr(guider, "camera_settings"):
        try:
            live = guider.camera_settings()
        except Exception:  # pragma: no cover - defensive; the file still answers
            live = None
    if isinstance(live, dict):
        for k in ("exposure_s", "gain", "offset", "binning"):
            if live.get(k) is not None:
                out["guide"][k] = live[k]
    return out


def publish_frames(guider: object = None) -> dict[str, dict]:
    """Announce the current frame settings to every connected client.

    Its OWN event, not a field on ``polar``. Piggybacking the solve settings on
    the polar event is what made ``PolarAlignSession.start()`` — which resets
    ``state`` to ``_idle()`` — wipe them off every Align screen the instant an
    alignment began, while the engine went on solving at the operator's values.
    A setting's lifetime is not a session's.
    """
    payload = frames_payload(guider)
    bus.publish("frames", **payload)
    return payload


def set_frame_settings(scope: str, patch: dict) -> dict:
    """Merge ``patch`` into one scope, persist it, and return that scope's
    effective settings. A ``None`` value CLEARS the field back to its default
    (the contract ``/api/polar/solve-settings`` has always had). Unknown keys
    are ignored — the route validates, this is the second wall.

    Does NOT publish: the caller decides, because the route has to apply a
    guide change to the live guider first and must not announce a value the
    guider is about to refuse.
    """
    if scope not in FRAME_SCOPES:
        raise ValueError(f"unknown frame scope: {scope!r} — valid scopes are "
                         f"{', '.join(FRAME_SCOPES)}")
    fields = ("exposure_s", "gain", "offset", "binning", "filter")
    if scope == "guide":
        # The guide camera has no wheel. Accepting a filter here would store an
        # intent nothing will ever act on — the shipped-dead-setting shape.
        if patch.get("filter") is not None:
            raise ValueError("the guide camera has no filter wheel")
        gc = config_store.cfg().guide
        update = {k: v for k, v in patch.items()
                  if k in ("exposure_s", "gain", "offset", "binning")
                  and v is not None}
        config_store.set_guide(gc.model_copy(update=update))
        return frames_payload()["guide"]
    defaults = FrameSettingsConfig()
    current: FrameSettings = getattr(config_store.cfg().frames, scope)
    update: dict = {}
    for key in fields:
        if key not in patch:
            continue
        value = patch[key]
        if value is None:
            update[key] = getattr(getattr(defaults, scope), key)
        else:
            update[key] = value
    merged = FrameSettings.model_validate(
        {**current.model_dump(), **update})
    frames = config_store.cfg().frames.model_copy(update={scope: merged})
    config_store.set_frames(frames)
    return getattr(config_store.cfg().frames, scope).model_dump()
