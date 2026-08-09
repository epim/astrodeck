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

import copy
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .config import PROFILES_DIR, PROVIDER_CAPABILITIES, Optics
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
        # W2: wire-redaction of any credential a future backend may stash in
        # ``ConnSpec.extra`` now exists (see ``redact_profile`` below); the
        # persisted profile keeps the value at-rest so the rig can still connect.

    def override_providers(self) -> dict[str, str] | None:
        """The per-capability pins this profile ACTUALLY imposes, or ``None``.

        Filtered to ``PROVIDER_CAPABILITIES`` because a profile's ``providers``
        dict is a bare ``dict`` that accepts anything (including anything an
        imported profile file carries) and only those four keys are ever read.

        ``"auto"`` is deliberately NOT filtered out. It looks like "no override"
        and is not: ``providers.override_with_layer`` returns the profile's value
        whenever it is in the vocabulary, so a profile pinned to ``auto`` beats a
        global config pinned to ``astap`` and changes which solver runs. Hiding
        it here would recreate, one layer up, the same invisible override this
        method exists to expose.
        """
        pov = self.providers
        if not isinstance(pov, dict):
            return None
        out: dict[str, str] = {}
        for cap in PROVIDER_CAPABILITIES:
            v = pov.get(cap)
            if isinstance(v, str) and v:
                out[cap] = v
        return out or None

    def row(self, active_id: str | None = None) -> dict:
        """The picker row — identity PLUS the two blocks that override the rig.

        ``providers`` and ``optics`` used to be dropped here, which made it
        structurally impossible for the Profiles tab to show that a profile pins
        a capability or a focal length: the list endpoint returns these rows, and
        the full model is only reachable through a per-profile GET nothing on
        that screen calls. That is how a ``polar_align: "sim"`` pin written
        during one session kept the polar aligner simulated for twelve days while
        every screen in the product displayed the global config's value.

        Both blocks are small and secret-free (``redact_profile`` only scrubs
        ``devices[].extra``), so they ride on the list row rather than forcing an
        N+1 fetch. ``optics`` is dumped WHOLE because the whole model is what
        wins: ``resolve_optics`` swaps the entire object, so a profile optics
        block overrides every optics field at once, including the ones the
        profile left at their own defaults."""
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "devices_count": len(self.devices),
            "site_name": self.site_name,
            "active": self.id == active_id,
            "providers": self.override_providers(),
            "optics": self.optics.model_dump() if self.optics else None,
        }


# ---------------------------------------------------------- wire redaction (W2)
#
# A device row's ``extra`` (ConnSpec options) carries NO credential in Stage B,
# but a future backend could stash one there — and ``GET /api/profiles/{id}`` is
# a VIEWER-visible read. ``redact_profile`` scrubs any secret-bearing ``extra``
# key OVER THE WIRE while the persisted profile keeps the real value at-rest so
# the rig can still connect. Redaction never mutates the source.

# Substrings (matched case-insensitively) that mark an ``extra`` key as
# secret-bearing. Benign connection metadata (``name`` / ``port_path`` /
# ``dev_type`` / ``host`` / ``pixel_scale_arcsec`` …) contains none of these and
# passes through untouched. Note ``"key"`` deliberately also matches
# ``apikey`` / ``api_key`` / ``session_key`` etc.
_SECRET_EXTRA_MARKERS = (
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "credential", "passphrase", "auth", "key",
)


def _is_secret_extra_key(key: str) -> bool:
    """True iff a device-``extra`` key name looks secret-bearing (a
    case-insensitive substring match against ``_SECRET_EXTRA_MARKERS``)."""
    lowered = (key or "").lower()
    return any(marker in lowered for marker in _SECRET_EXTRA_MARKERS)


def redact_profile(profile: "Profile | dict") -> dict:
    """Return a wire-safe ``dict`` copy of ``profile`` with every device
    ``extra`` scrubbed of secret-bearing values.

    Accepts a :class:`Profile` (serialized via ``model_dump``) or an
    already-serialized ``dict`` (e.g. a picker ``row``). For each device row's
    ``extra``, any key whose name looks secret-bearing (:func:`_is_secret_extra_key`)
    has its value replaced with ``""`` and gains a sibling
    ``"<key>_configured": <bool>`` marker so the UI can still show that a
    credential is set without the value crossing the wire.

    Redaction is OVER-THE-WIRE ONLY — the at-rest profile keeps the real value
    so the rig can connect. The source object is NEVER mutated: a ``Profile`` is
    dumped to a fresh dict, and a passed-in dict is deep-copied first.
    """
    if isinstance(profile, BaseModel):
        data = profile.model_dump()
    else:
        data = copy.deepcopy(profile)
    if not isinstance(data, dict):
        return data
    devices = data.get("devices")
    if not isinstance(devices, list):
        return data  # picker rows / device-less shapes: nothing to scrub
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        extra = dev.get("extra")
        if not isinstance(extra, dict):
            continue
        scrubbed = dict(extra)
        for key, value in extra.items():
            if _is_secret_extra_key(key):
                scrubbed[key] = ""
                scrubbed[f"{key}_configured"] = bool(value)
        dev["extra"] = scrubbed
    return data


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

    def set_providers(self, profile_id: str,
                      providers: Mapping[str, str]) -> dict:
        """Write per-capability provider pins into this profile IN PLACE.

        #132, and the counterpart to :meth:`clear_overrides`. The console could
        already SAY "profile Rig1 pins the simulator for polar alignment" and
        could clear that pin — but there was no way to EDIT it, because the only
        provider write in the product (``POST /api/config/providers``) targets
        the GLOBAL block, which the profile then beats. A user who correctly read
        "pinned by profile Rig1", opened the dropdown and saved changed nothing
        that runs: the save succeeded, the toast was green, the rig kept using
        the pin. The console told the truth and then quietly ignored the user,
        which is a worse failure than the display bug it replaced.

        The rule, decided by the owner: WRITE BACK TO THE LAYER YOU ARE READING
        FROM. The client picks the route by asking which layer won (the
        ``effective`` provenance block already reports that); this method is the
        profile half of it.

        Semantics are OVERWRITE, not merge: each named capability's value is
        REPLACED. Capabilities the body does not name are untouched — that is the
        same granularity ``clear_overrides`` already has, not a patch semantic
        for the value itself.

        Why this lives on the server rather than as a client read-modify-write —
        the identical reason ``clear_overrides`` does, and it is worth restating
        because getting it wrong destroys data rather than merely annoying
        someone: ``GET /api/profiles/{id}`` is wire-REDACTED (:func:`redact_profile`
        blanks every secret-bearing device ``extra``), so a UI that fetched a
        profile, set one key and POSTed it back would persist the BLANKS and cost
        the user their stored device credentials. The mutation therefore runs
        against the at-rest record, which is the only copy that still has them.

        Unknown capability names are REJECTED (``ValueError`` → the route maps it
        to 422), where ``clear_overrides`` ignores them. The asymmetry is
        deliberate: ignoring an unknown key on a CLEAR still leaves the user with
        fewer pins than they started with, but ignoring one on a WRITE is a save
        that reports success and changes nothing — the exact failure mode this
        method exists to end. Values are validated against the SAME live
        vocabulary ``ConfigStore.set_providers`` uses, so a typo'd or deleted
        driver id cannot be parked in a profile where it would later be silently
        discarded at resolve time with no signal anywhere.
        """
        # Imported inside the call, never at module scope: a module-level binding
        # freezes whichever store existed at import time, so a redirected
        # ``config.config_store`` (the test suite's isolation idiom) would leave
        # this validating against a DIFFERENT config than the one that resolves
        # the pin. See the long note at the bottom of this module.
        from .config import config_store

        # Resolve the profile FIRST. Validating the body first meant a refused id
        # answered 422 ("unknown capability") instead of 404, because the body
        # was judged before anyone asked whether the thing being edited exists —
        # so the caller learned about the wrong problem. The docstring promised
        # 404 and the tests only ever sent valid bodies, so nothing caught it.
        prof = self.get(profile_id)

        unknown = [k for k in providers if k not in PROVIDER_CAPABILITIES]
        if unknown:
            raise ValueError(
                f"unknown capability: {', '.join(sorted(unknown))} — valid "
                f"capabilities are {', '.join(PROVIDER_CAPABILITIES)}")
        from . import providers as _providers   # local: providers imports config
        valid = config_store.valid_override_values()
        for cap, value in providers.items():
            if not isinstance(value, str) or value not in valid:
                raise ValueError(
                    f"unknown provider for {cap}: {value!r} — valid values are "
                    f"{', '.join(sorted(valid))}")
            # Per-capability, off the same table ``resolve`` dispatches through
            # (audit finding O). A profile override is the layer that BEATS
            # global config, so an inert value stored here is the worse of the
            # two: it looks like the winning answer and does nothing.
            if not _providers.is_honourable(cap, value):
                raise ValueError(
                    f"{value!r} cannot provide {cap}: nothing resolves it, so "
                    f"pinning it would behave exactly like 'auto'. Valid "
                    f"choices for {cap} are "
                    f"{', '.join(sorted(_providers.honoured_families(cap)))} "
                    f"(or a driver id belonging to one of those families)")

        # A profile with no ``providers`` dict yet gets one; existing keys the
        # body does not name survive untouched.
        current = dict(prof.providers) if isinstance(prof.providers, dict) else {}
        current.update(providers)
        prof.providers = current or None
        return self.save(prof)

    def clear_overrides(self, profile_id: str, *,
                        providers: Iterable[str] = (),
                        optics: bool = False) -> dict:
        """Drop this profile's overrides IN PLACE and return the fresh row.

        #129. A profile override is the layer that beats global config, and
        until now nothing in the product could remove one: the Profiles tab has
        no editor, and the only write route (``POST /api/profiles``) takes a
        whole profile. So the console can now SHOW "profile X pins the
        simulator" and the user's only recourse would be to delete the profile.

        Why this lives on the server rather than as a client read-modify-write:
        ``GET /api/profiles/{id}`` is wire-REDACTED — :func:`redact_profile`
        blanks every secret-bearing device ``extra`` — so a UI that fetched a
        profile, deleted one key and POSTed it back would persist the BLANKS and
        cost the user their stored credentials. The mutation therefore runs
        against the at-rest record, which is the only copy that still has them.

        ``providers`` names capabilities to unpin; unknown names are ignored
        rather than rejected, because a profile's ``providers`` dict is a bare
        dict that can carry anything an imported profile file had in it, and
        refusing the whole request over an inert key would leave the user unable
        to clear the keys that ARE live. Clearing the last pin drops the dict to
        ``None`` so the profile reads as "no provider override" rather than as
        an empty override.

        ``optics`` is all-or-nothing on purpose: ``resolve_optics`` swaps the
        WHOLE block, so there is no such thing as clearing one optics field —
        offering a per-field clear would imply a granularity the resolver does
        not have.
        """
        prof = self.get(profile_id)
        wanted = {c for c in providers if c in PROVIDER_CAPABILITIES}
        if wanted and isinstance(prof.providers, dict):
            kept = {k: v for k, v in prof.providers.items() if k not in wanted}
            # An empty dict is not the same as None to a reader skimming the
            # JSON, and ``override_providers`` already collapses empty -> None;
            # persist the collapsed form so the file matches what the API says.
            prof.providers = kept or None
        if optics:
            prof.optics = None
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


# ------------------------------------------------------- the optics layer rule
#
# PROFILE > GLOBAL, in one place. Two seams need the answer and only one of them
# has a hub: ``Hub.effective_optics`` (which owns a cached active-profile read
# for the 2s status poll) and the native guider, which is constructed inside a
# backend session that has no hub handle at all. That second seam read GLOBAL
# config directly for ``guide_focal_length_mm`` while its siblings came from the
# profile, so a profile optics override was honoured for imaging and silently
# ignored for guiding: the imaging scale moved, the guide scale did not, and the
# guider fell back to reporting RMS in pixels at an assumed 1"/px next to an
# Optics panel showing a guide focal length that looked set. Both seams now call
# the function below, so there is exactly one answer to "whose optics run".
#
# Both functions import ``config_store`` INSIDE the call rather than at module
# scope, and that is load-bearing. A module-level ``from .config import
# config_store`` freezes the object this module will use forever, so a caller
# that redirects ``config.config_store`` (the idiom the whole test suite and the
# factory-reset path use) would leave these two reading a DIFFERENT store than
# the code that called them — the exact split that let a route test write its
# fixtures into the developer's real config file while passing. Looking the name
# up per call costs a dict lookup and keeps every reader on one store.


def resolve_optics(profile: "Profile | None") -> Optics:
    """The ``Optics`` block that ACTUALLY RUNS for ``profile``.

    The active profile's block wins WHOLE when it has one — this is a model
    swap, not a field-by-field merge, so a profile that sets only a focal length
    also imposes its own (default) pixel size and sensor dimensions. Callers that
    report provenance must say so; callers that just want the numbers get the
    same numbers the rig uses. Never writes: the global block is untouched."""
    from .config import config_store
    if profile is not None and profile.optics is not None:
        return profile.optics
    return config_store.cfg().optics


def active_profile() -> "Profile | None":
    """The active profile read straight from disk — the hub-free reader.

    ``Hub._active_profile`` is the CACHED one (the status poll must not touch the
    disk every 2s); this exists for seams that have no hub, notably the native
    backend building its guider. Defensive on purpose: a unit test's stub config
    store has no ``active_profile_id`` and a missing/corrupt profile file must
    degrade to "no active profile" rather than raise into a connect path, where
    the failure would surface as a device that mysteriously would not connect."""
    from .config import config_store
    try:
        active_id = getattr(config_store.cfg(), "active_profile_id", None)
    except Exception:
        return None
    if not active_id:
        return None
    try:
        return profiles.active(active_id)
    except Exception:
        return None
