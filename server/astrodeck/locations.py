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

from pydantic import BaseModel, Field, field_validator

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

    @field_validator("name")
    @classmethod
    def _name_trimmed_non_empty(cls, v: str) -> str:
        """Trim, then reject empty (spec §4: the name must be non-empty after
        trim). Validator-first — a min_length constraint would pass '   '
        before any trimming could run."""
        v = v.strip()
        if not v:
            raise ValueError("name must be non-empty")
        return v


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
