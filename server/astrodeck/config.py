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

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from .events import bus
from .persist import ensure_dir, read_json, write_json_atomic

# --------------------------------------------------------------------- locations

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"   # server/config/
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
    name: str = "My Backyard"
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
                     resume_when_safe=False, max_pause_min=0),
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
    # advanced (driven by the active preset unless preset == "custom")
    on_unsafe: str = "pause"               # abort_park_warm | park | pause | warn
    unsafe_consecutive: int = 3
    resume_when_safe: bool = True
    resume_safe_consecutive: int = 3
    max_pause_min: int = 120               # 0 = no cap; escalates to park on timeout


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
    # backend's ``ConnSpec.extra``, which carries no credential in Stage B.
    # TODO(W2): redact ConnSpec.extra secrets in serialized Profile records when a
    # future backend persists a credential there.
    return data


config_store = ConfigStore()
