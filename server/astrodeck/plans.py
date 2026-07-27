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
