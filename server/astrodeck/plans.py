"""Server-side named plan library.

The only persistence for sequence plans used to be ``localStorage`` in the
browser. This module gives plans a real home: one file per plan under
``config/plans/<uuid>.json``, keyed by an immutable uuid so a same-name save
never silently overwrites a different plan (the API detects a name collision and
prompts). A stored plan reuses ``SequencePlan`` verbatim plus an ``id`` +
``schema_version`` envelope on disk.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from .config import PLANS_DIR
from .persist import (ensure_dir, list_json, read_json, read_json_or,
                      safe_id_path, write_json_atomic)
from .sequence import SequencePlan

PLAN_SCHEMA = 1

# Soft quota: refuse new plans past this so a client can't fill the disk and
# degrade every list/name-check linearly. Upserting an existing id is always
# allowed. The API maps LibraryFull → 409.
MAX_PLANS = 500


class PlanImportError(ValueError):
    """Invalid plan import. ``code`` distinguishes the failure for UI copy."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


class LibraryFull(ValueError):
    """Raised by ``save`` when a *new* plan would exceed the store quota.

    Subclasses ``ValueError`` and carries ``code="library_full"`` so the API
    maps it to a 409 with stable UI copy."""

    def __init__(self, message: str, code: str = "library_full"):
        super().__init__(message)
        self.code = code


def _summarize(plan: SequencePlan) -> dict:
    """Lightweight headline numbers for a list row (no full target arrays)."""
    frames = plan.total_frames()
    # UX #39: INTEGRATION means light-frame exposure. Calibration steps (a
    # 120s x10 flat set) used to inflate this figure, which is the one number a
    # user compares between plans.
    integration_min = round(plan.light_seconds() / 60.0, 1)
    return {"frames": frames, "integration_min": integration_min,
            "shutter_min": round(plan.total_seconds() / 60.0, 1),
            "targets": len(plan.targets)}


#: What each moved field's default WAS before #239 stage A made it optional.
#: A stored value equal to its entry here was never a decision - the field
#: simply had no way to say "no opinion" - so the migration below turns it into
#: one. Frozen literals rather than a read off the model: the model no longer
#: knows these numbers, which is the whole point.
_PRE_MIGRATION_DEFAULTS: dict[str, object] = {
    "dither_pixels": 3.0,
    "recover_guiding": True,
    "cool_timeout_s": 600,
    "hfr_reject_factor": 0.0,
    "meridian_flip_warn_min": 15.0,
    "apply_filter_offsets": True,
    "refocus_on_temp_delta_c": 0.0,
    "min_stars": 0,
    "max_guide_rms": 0.0,
    "max_eccentricity": 0.0,
    "max_consecutive_rejects": 10,
    "max_consecutive_rejects_night": 20,
}


def migrate_plan_policy_fields(library: "PlanLibrary | None" = None) -> int:
    """Turn stored plan-policy values that were never chosen into "inherit".

    Returns the number of plans changed. Idempotent, and a plan with nothing to
    null is not rewritten at all, so a migrated library does not churn on boot.

    THIS IS LOSSY IN ONE DIRECTION AND THAT IS DELIBERATE. A `dither_pixels` of
    3.0 that someone typed on purpose is indistinguishable from the 3.0 the
    model used to supply, so both become "inherit". Until this change there was
    no way to express the difference, so the information was never captured -
    and treating every untouched default as a deliberate choice would leave the
    rig's standards unable to reach a single plan that already exists, which is
    the feature not working at all.

    SESSION-FROZEN PLANS ARE LEFT ALONE. A session's plan is the contract for
    one multi-night run - what that run was authored with, and what it resumes
    under. Rewriting it mid-campaign would change the rules between night one
    and night two, which is the one thing a frozen snapshot exists to prevent.
    """
    from .events import bus

    lib = library if library is not None else plan_library
    changed = 0
    for row in lib.list():
        pid = row["id"]
        try:
            env = lib._envelope(pid)
        except KeyError:
            continue
        if env is None:
            continue
        raw = env.get("plan") or {}
        nulled = [f for f, was in _PRE_MIGRATION_DEFAULTS.items()
                  if f in raw and raw[f] == was and raw[f] is not None]
        if not nulled:
            continue
        for f in nulled:
            raw[f] = None
        env["plan"] = raw
        write_json_atomic(lib._path(pid), env)
        changed += 1
        bus.log("info",
                f"plan '{env.get('name') or pid}': {len(nulled)} setting"
                f"{'' if len(nulled) == 1 else 's'} now follow the rig's "
                f"standards ({', '.join(sorted(nulled))}) - they held the old "
                f"built-in default, which was never a choice anyone made",
                "plans")
    return changed


class PlanLibrary:
    """uuid-keyed plan store; one ``plans/<id>.json`` per plan."""

    def __init__(self, directory: Path = PLANS_DIR):
        self._dir = directory

    def _path(self, plan_id: str) -> Path:
        """Resolve ``plans/<id>.json`` and refuse to escape the directory.

        ``id`` is client-controllable (request body + path param, incl. the
        ``..%5C`` backslash vector on Windows). A value like ``"../../pwned"`` or
        an absolute path would otherwise read/write/delete arbitrary ``*.json``
        files (e.g. the server's own ``astrodeck.json``). ``safe_id_path`` refuses
        any non-bare-filename id on *any* platform (separators of either OS,
        drive prefixes, ``..``) → ``KeyError`` (the routes map ``KeyError`` → 404).
        """
        return safe_id_path(self._dir, plan_id)

    def _envelope(self, plan_id: str) -> dict | None:
        raw = read_json_or(self._path(plan_id))
        return raw if isinstance(raw, dict) else None

    def list(self) -> list[dict]:
        """Lightweight rows: ``{id, name, frames, integration_min, targets, mtime}``."""
        rows: list[dict] = []
        for path in list_json(self._dir):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            try:
                plan = SequencePlan(**(raw.get("plan") or {}))
            except Exception:
                continue
            rows.append({
                "id": raw.get("id", path.stem),
                "name": raw.get("name") or plan.name,
                **_summarize(plan),
                "mtime": path.stat().st_mtime,
            })
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return rows

    def get(self, plan_id: str) -> SequencePlan:
        env = self._envelope(plan_id)
        if env is None:
            raise KeyError(plan_id)
        return SequencePlan(**(env.get("plan") or {}))

    def save(self, plan: SequencePlan, plan_id: str | None = None) -> dict:
        """Upsert by id; atomic write. Returns the list row.

        Refuses a *new* plan once the store is at ``MAX_PLANS`` (raises
        :class:`LibraryFull`); upserting an existing id is always allowed.
        """
        ensure_dir(self._dir)
        pid = plan_id or str(uuid4())
        path = self._path(pid)
        if not path.exists() and len(list_json(self._dir)) >= MAX_PLANS:
            raise LibraryFull(
                f"plan limit reached ({MAX_PLANS}); delete some first")
        envelope = {
            "id": pid,
            "schema_version": PLAN_SCHEMA,
            "name": plan.name,
            "plan": plan.model_dump(),
        }
        write_json_atomic(path, envelope)
        return {"id": pid, "name": plan.name, **_summarize(plan),
                "mtime": path.stat().st_mtime}

    def name_exists(self, name: str, exclude_id: str | None = None) -> bool:
        for path in list_json(self._dir):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            if raw.get("name") == name and raw.get("id") != exclude_id:
                return True
        return False

    def delete(self, plan_id: str) -> None:
        path = self._path(plan_id)
        if path.exists():
            path.unlink()

    def export_bytes(self, plan_id: str) -> bytes:
        """Serialized on-disk envelope, ready for a download response."""
        env = self._envelope(plan_id)
        if env is None:
            raise KeyError(plan_id)
        return json.dumps(env, indent=2, ensure_ascii=False).encode("utf-8")

    def import_plan(self, raw: dict) -> dict:
        """Import an exported envelope. Version-too-new is checked by the API
        before this call; here we validate the embedded plan and re-key it with a
        fresh uuid (so an import never clobbers an existing plan by id)."""
        if not isinstance(raw, dict):
            raise PlanImportError("not a valid AstroDeck plan file", "invalid")
        if int(raw.get("schema_version", 1) or 1) > PLAN_SCHEMA:
            raise PlanImportError(
                "This plan was exported by a newer AstroDeck version",
                "version_too_new")
        plan_data = raw.get("plan", raw)
        try:
            plan = SequencePlan(**plan_data)
        except Exception as e:
            raise PlanImportError(str(e), "invalid") from e
        return self.save(plan, plan_id=None)


plan_library = PlanLibrary()
