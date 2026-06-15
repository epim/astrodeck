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
    name: str = "[SITE-LABEL]"
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


class AppConfig(BaseModel):
    version: int = 1                   # bumped on every save (optimistic-concurrency token)
    site: Site = Field(default_factory=Site)
    optics: Optics = Field(default_factory=Optics)
    active_profile_id: str | None = None


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


config_store = ConfigStore()
