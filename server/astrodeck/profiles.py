"""Equipment profiles — named, replayable connection intents.

A profile records *which backend + which device at which address* (not live
handles) so a user never re-enters Alpaca/NINA hosts on every reconnect. Profiles
are keyed by an immutable uuid so a rename never orphans the active-profile
pointer or the on-disk file; the display ``name`` is mutable metadata that may
collide (the API prompts to overwrite).

Per-device backend supports mixed rigs (some Alpaca, some NINA); the rig
``mode`` is derived, never stored. A per-profile ``optics`` override is resolved
at read time by the hub (it never stomps the global config).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .config import PROFILES_DIR, Optics
from .persist import (ensure_dir, list_json, read_json_or, safe_id_path,
                      write_json_atomic)

if TYPE_CHECKING:  # avoid an import cycle at runtime (backend imports are lazy)
    from .devices.backend import RigSpec

# Soft quota: refuse new records past this (a client can otherwise fill the disk
# and degrade every list/name-check linearly). Upserting an existing id is always
# allowed so an at-cap library can still be edited. The API maps LibraryFull → 409.
MAX_PROFILES = 500

# Legacy-migration alias (Stage B / spec T4(5)): the on-disk schema used the
# label ``"alpaca"`` for the direct-Alpaca path; the pluggable registry names that
# backend ``"native"``. A legacy ``ProfileDevice(backend="alpaca")`` must map to
# the ``native`` backend through ``to_rigspec`` rather than KeyError in
# ``get_backend``. This is the single most upgrade-day-breaking case, pinned here.
_BACKEND_ALIAS: dict[str, str] = {"alpaca": "native"}


class LibraryFull(ValueError):
    """Raised by ``save`` when a *new* record would exceed the store quota.

    Subclasses ``ValueError`` and carries ``code="library_full"`` so the API
    maps it to a 409 with stable UI copy."""

    def __init__(self, message: str, code: str = "library_full"):
        super().__init__(message)
        self.code = code


class ProfileDevice(BaseModel):
    role: str                    # camera|telescope|focuser|guider|filterwheel|switch|safety|rotator|guide_camera
    backend: str = "alpaca"      # "alpaca"|"native"|"nina"|... (PER-DEVICE — mixed rigs)
    host: str = ""
    port: int = 0
    dev_type: str = ""
    dev_num: int = 0
    name: str = ""
    # Phase 2: reference to a configured driver (AppConfig.drivers[].id). ""
    # (old rows) → the raw host/port above stays authoritative. Saved by the
    # Equipment surface; resolved by drivers.resolve_driver_ids at connect.
    driver_id: str = ""
    transport: str = "network"   # "network" | "serial"
    port_path: str = ""          # serial device path, e.g. "COM3" / "/dev/ttyACM0"
    # Stage B: carries ``ConnSpec.extra`` (backend-specific options, e.g. a PHD2
    # pixel scale). Additive + JSON-able only — a non-serializable runtime
    # injection (the NINA ``build_rig`` callable) is NEVER persisted here; see
    # ``to_rigspec`` which copies ``extra`` verbatim plus a derived ``name``.
    extra: dict = Field(default_factory=dict)


class Profile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))  # immutable identity
    name: str = "New Profile"    # display only; mutable; may collide → prompt
    # Stage B: the RigSpec primary (default backend for any role with no explicit
    # per-device override). Old profiles on disk lack this key → the empty
    # default is falsy so ``to_rigspec`` derives the primary for legacy shapes
    # (nina_host-only → "nina", any device row → "native", empty rig → "sim").
    # A truthy default here would silently resolve every unlisted role of a REAL
    # rig to the simulator backend — including a fail-open sim SafetyMonitor.
    primary_backend: str = ""
    devices: list[ProfileDevice] = []
    nina_host: str | None = None
    nina_port: int = 1888
    phd2_host: str | None = None
    phd2_port: int = 4400
    optics: Optics | None = None       # per-rig override; resolved at read time
    site_name: str | None = None
    # Per-rig capability routing override (native parity). JSON-able dict keyed by
    # capability ("autofocus" | "polar_align") -> "auto" | "backend" | "astrodeck".
    # Read at resolve time by ``providers.resolve()`` (which reads the ACTIVE
    # profile), so a NINA rig can pin native autofocus without touching global
    # config. Additive + backward-compatible: old profiles lack the key -> None,
    # which resolve() treats as "no per-rig override, fall through to config".
    # Not threaded into ``to_rigspec`` — it is a capability preference, not a
    # connection intent, so it never affects which devices connect.
    providers: dict | None = None

    @model_validator(mode="after")
    def _heal_sim_primary(self) -> "Profile":
        """Heal profiles written while ``primary_backend`` defaulted to ``"sim"``.

        A ``"sim"`` primary combined with REAL device rows is (near-certainly) an
        artifact of that old default, and it is dangerous: connecting such a
        profile fills every unlisted role — including ``safety`` — with a
        simulator, so the safety gate reads a fail-open always-SAFE monitor while
        a real mount runs. Coerce back to the derived primary; a genuinely all-sim
        profile (no real rows) keeps ``"sim"``."""
        if self.primary_backend == "sim" and any(
                d.backend not in ("", "sim") for d in self.devices):
            self.primary_backend = self._derived_primary()
        return self

    @property
    def mode(self) -> str:
        """Derived rig mode — never stored."""
        backends = {d.backend for d in self.devices}
        if self.nina_host and not backends:
            return "nina"
        if backends == {"alpaca"}:
            return "alpaca"
        if backends:
            return "mixed"
        return "empty"

    def _derived_primary(self) -> str:
        """Best-guess RigSpec primary for a legacy profile that predates
        ``primary_backend``. A ``nina_host``-only rig → ``"nina"``; any per-device
        row → ``"native"`` (the post-alias direct-Alpaca name); otherwise the
        empty-rig fallback ``"sim"``."""
        if self.nina_host and not self.devices:
            return "nina"
        if self.devices:
            return "native"
        return "sim"

    def to_rigspec(self) -> "RigSpec":
        """Map this persisted profile onto a live ``RigSpec`` (the single
        Profile→RigSpec mapper the hub + tests both call).

        Per-device rows become per-role ``ConnSpec`` overrides on the registry
        backend (with the ``alpaca``→``native`` migration alias applied), and the
        display ``name`` is folded into ``ConnSpec.extra['name']`` so the native
        session can label the device. The non-serializable NINA ``build_rig``
        runtime injection is NEVER read from a profile, so nothing unsafe leaks
        into ``extra``. A legacy ``nina_host``-only profile (no device rows) is
        expressed as ``primary="nina"`` with no overrides; primary-derived
        resolution then fills NINA's roles.
        """
        # Imported lazily to keep ``profiles`` import-light and cycle-free.
        from .devices.backend import ConnSpec, RigSpec

        primary = self.primary_backend or self._derived_primary()
        roles: dict[str, ConnSpec] = {}
        for d in self.devices:
            backend = _BACKEND_ALIAS.get(d.backend, d.backend)   # alpaca -> native
            extra = dict(d.extra or {})
            if d.name:
                extra.setdefault("name", d.name)
            roles[d.role] = ConnSpec(
                backend=backend,
                host=d.host or None,
                port=d.port or None,
                dev_type=d.dev_type or None,
                dev_num=d.dev_num,
                role=d.role,
                driver_id=d.driver_id or None,
                transport=d.transport,
                port_path=(d.port_path or None),
                extra=extra,
            )
        # Legacy nina_host-only profile (no per-device rows): the rig IS NINA, so
        # the primary is nina and the roles resolve from primary at connect time.
        if self.nina_host and not self.devices:
            primary = "nina"
        return RigSpec(primary=primary, roles=roles)
        # TODO(W2): redact ConnSpec.extra secrets at-rest if a future backend
        # stores a credential there; no profile-borne secret exists in Stage B.

    def row(self, active_id: str | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "devices_count": len(self.devices),
            "site_name": self.site_name,
            "active": self.id == active_id,
        }


class ProfileLibrary:
    """uuid-keyed profile store; one ``profiles/<id>.json`` per profile."""

    def __init__(self, directory: Path = PROFILES_DIR):
        self._dir = directory

    def _path(self, profile_id: str) -> Path:
        """Resolve ``profiles/<id>.json`` and refuse to escape the directory.

        ``id`` is client-controllable (request body + path param); a value like
        ``"../../pwned"`` or an absolute path would otherwise read/write/delete
        arbitrary ``*.json`` files. ``safe_id_path`` refuses any non-bare-filename
        id on *any* platform (separators of either OS, drive prefixes, ``..``) →
        ``KeyError`` (the routes map ``KeyError`` → 404, so traversal looks like a
        plain not-found).
        """
        return safe_id_path(self._dir, profile_id)

    def _all(self) -> list[Profile]:
        out: list[Profile] = []
        for p in list_json(self._dir):
            raw = read_json_or(p)
            if not isinstance(raw, dict):
                continue
            try:
                out.append(Profile(**raw))
            except Exception:
                continue
        return out

    def list(self, active_id: str | None = None) -> list[dict]:
        """Lightweight rows (no full device intent) for the picker."""
        return [p.row(active_id) for p in self._all()]

    def get(self, profile_id: str) -> Profile:
        raw = read_json_or(self._path(profile_id))
        if not isinstance(raw, dict):
            raise KeyError(profile_id)
        return Profile(**raw)

    def save(self, profile: Profile) -> dict:
        """Upsert by id; atomic write of ``profiles/<id>.json``. Returns the row.

        Refuses a *new* record once the store is at ``MAX_PROFILES`` (raises
        :class:`LibraryFull`); upserting an existing id is always allowed.
        """
        ensure_dir(self._dir)
        path = self._path(profile.id)
        if not path.exists() and len(list_json(self._dir)) >= MAX_PROFILES:
            raise LibraryFull(
                f"profile limit reached ({MAX_PROFILES}); delete some first")
        write_json_atomic(path, profile.model_dump())
        return profile.row()

    def name_exists(self, name: str, exclude_id: str | None = None) -> bool:
        """Stream files and return on the first name match (no eager parse of
        every profile via ``_all()``)."""
        for path in list_json(self._dir):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            if raw.get("name") == name and raw.get("id") != exclude_id:
                return True
        return False

    def rename(self, profile_id: str, name: str) -> dict:
        """Mutate ``name`` in place — the id (slug/filename) never changes."""
        prof = self.get(profile_id)
        prof.name = name
        return self.save(prof)

    def delete(self, profile_id: str) -> None:
        path = self._path(profile_id)
        if path.exists():
            path.unlink()

    def active(self, profile_id: str | None) -> Profile | None:
        if not profile_id:
            return None
        try:
            return self.get(profile_id)
        except (KeyError, ValueError):
            return None


profiles = ProfileLibrary()
