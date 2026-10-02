# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Server-side named plan library.

The only persistence for sequence plans used to be ``localStorage`` in the
browser. This module gives plans a real home: one file per plan under
``config/plans/<uuid>.json``, keyed by an immutable uuid so a same-name save
never silently overwrites a different plan (the API detects a name collision and
prompts). A stored plan reuses ``SequencePlan`` verbatim plus an ``id`` +
``schema_version`` envelope on disk.

A FILE THAT IS NO LONGER A PLAN IS LISTED, NOT SKIPPED (#378): a row with
``status: "unreadable"`` and the first thing wrong with it, and ``GET`` and
its export answer 422 with the same sentence. Skipping it made a damaged
plan look deleted, and asking for it by id was a 500. The flow library
(#153) and the session store (#242) keep the same rule.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

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


#: Why a plan file that does not parse, or is not UTF-8 text, is unreadable:
#: a write cut short, or a file copied in by hand. Writes are atomic
#: (``write_json_atomic``), so the library itself should never leave one.
#: The one spelling of the sentence: ``_read_plan`` raises with it and the
#: tests import it, and the recorded list the UI tests read holds its words
#: (``tests/fixtures/plan_list_unreadable.json``, #478), so rewording it
#: means re-recording that file on purpose.
NOT_JSON = "not valid JSON"

#: Why JSON that is not an object is: there is no envelope to hold a plan.
NO_PLAN = "it holds no plan"

#: The start of the reason for an envelope whose plan ``SequencePlan`` does
#: not validate; ``_invalid_reason`` says where and why after it.
INVALID = "fails validation"

#: Longest name an unreadable row carries: a list line, read from a file
#: nothing validated. The session store's bound.
_NAME_MAX = 120

#: Longest quoted value a reason carries, for the same reason.
_VALUE_MAX = 40

#: Longest rule a reason carries. Pydantic's own words for a bound are far
#: shorter; the cut is for a validator's sentence that quotes the value.
_RULE_MAX = 160


class PlanUnreadable(ValueError):
    """A plan file on disk that this build cannot read as a plan (#378).

    ``reason`` is the sentence the list row and the 422 carry, the same
    words in both places. ``name`` is the name the file gives, when it
    gives one a person can read, else None. A ``ValueError`` like the other
    refusals here, and never a ``KeyError``: the routes answer that 404,
    and "not found" is false for a plan that is on disk and damaged."""

    def __init__(self, plan_id: str, reason: str, name: str | None = None,
                 code: str = "unreadable"):
        super().__init__(reason)
        self.plan_id = plan_id
        self.reason = reason
        self.name = name
        self.code = code


_MISSING = object()


def _at(value, loc: tuple):
    """What the raw plan holds at a validation error's ``loc``, or
    ``_MISSING`` when the path runs out (a field the file leaves out)."""
    for key in loc:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif (isinstance(value, list) and type(key) is int
              and 0 <= key < len(value)):
            value = value[key]
        else:
            return _MISSING
    return value


def _quoted(value) -> str:
    """``repr(value)``, cut to ``_VALUE_MAX`` characters."""
    text = repr(value)
    return text if len(text) <= _VALUE_MAX else text[:_VALUE_MAX - 3] + "..."


def _invalid_reason(body, exc: ValidationError) -> str:
    """``INVALID`` and the first error pydantic reports, named by its path
    in the plan and the value the file holds there, with the rest counted:
    ``fails validation: targets.0.steps.0.frame_type: frame type 'Snapshot'
    is not one of Light, Dark, Bias, Flat (in any case)``.

    THE PATH, BECAUSE IT IS WHAT TO REPAIR. #334 made a step's frame type one
    of four spellings, and a plan saved before it with any other word stopped
    loading; the operator has a file to edit and needs the field.

    THE VALUE ONLY WHEN IT IS ONE PLAIN VALUE, read off the file at the
    error's own path, never pydantic's rendering of the error, which quotes
    its input from wherever it sits. A list or an object is named by its
    path alone. ``SequencePlan`` ignores keys it does not model, so every
    path is a field the plan's own editor writes, and a plan that loads
    hands a viewer every such value anyway (``GET /api/plans/{id}``). A
    rule whose own words already quote the value (#334's) is not made to
    say it twice. A missing field is said to be missing."""
    errors = exc.errors()
    if not errors:
        return INVALID
    first = errors[0]
    loc = tuple(first.get("loc", ()))
    # An error on the plan as a whole (an envelope whose plan is a list, say)
    # has no path of its own.
    where = ".".join(str(p) for p in loc) or "the plan"
    rule = str(first.get("msg", "")).removeprefix("Value error, ")
    rule = rule[:1].lower() + rule[1:]
    value = _at(body, loc)
    plain = value is not _MISSING and (value is None or isinstance(
        value, (str, int, float)))
    says_it = plain and repr(value) in rule
    if len(rule) > _RULE_MAX:
        # #334's rule quotes the value whole, and the value is whatever the
        # file holds.
        rule = rule[:_RULE_MAX - 3] + "..."
    if first.get("type") == "missing":
        what = f"{where} is missing"
    elif plain and not says_it:
        what = f"{where} {_quoted(value)}: {rule}"
    else:
        what = f"{where}: {rule}"
    more = len(errors) - 1
    if more:
        what += f" (and {more} more error{'s' if more != 1 else ''})"
    return f"{INVALID}: {what}"


def _raw_name(raw) -> str | None:
    """The name a damaged file gives its plan, the envelope's first and the
    plan's own second, cut to ``_NAME_MAX``; None when it gives neither."""
    if not isinstance(raw, dict):
        return None
    plan = raw.get("plan")
    for name in (raw.get("name"),
                 plan.get("name") if isinstance(plan, dict) else None):
        if isinstance(name, str) and name.strip():
            return name.strip()[:_NAME_MAX]
    return None


def _read_plan(path: Path) -> tuple[dict, SequencePlan]:
    """The envelope a plan file holds and its plan, or raise:
    ``PlanUnreadable`` for a file that is there and is not a plan this build
    reads, and ``OSError`` (``FileNotFoundError`` among them) for one that
    is gone or that the OS will not open.

    THE ONE JUDGMENT (#378): ``list`` makes a row of the refusal, and
    ``get`` and the export answer 422 with it, so the three cannot disagree
    about which files are plans. Nested past the parser's depth, ``json``
    raises RecursionError, not a ValueError (#363's lesson in the flow
    store), and it is damage like any other."""
    try:
        raw = read_json(path)
    except (ValueError, RecursionError) as e:  # not JSON, or not UTF-8
        raise PlanUnreadable(path.stem, NOT_JSON) from e
    if not isinstance(raw, dict):
        raise PlanUnreadable(path.stem, NO_PLAN)
    body = raw.get("plan") or {}
    try:
        plan = SequencePlan.model_validate(body)
    except ValidationError as e:
        raise PlanUnreadable(path.stem, _invalid_reason(body, e),
                             _raw_name(raw)) from e
    return raw, plan


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
        if row.get("status") == "unreadable":
            # A file this build cannot read as a plan is listed (#378) and
            # left exactly as it is, as it was when the list skipped it: its
            # meaning is unknown, so nothing in it is "a default nobody
            # chose", and a rewrite would stamp over the evidence the
            # operator repairs it from.
            continue
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
        """Lightweight rows, newest first: ``{id, name, frames,
        integration_min, shutter_min, targets, mtime}`` for each plan, and
        ``{id, name, status: "unreadable", unreadable, mtime}`` for each file
        that is not one (#378).

        AN UNREADABLE ROW IS SEEN, AND NOTHING TO MISTAKE FOR A PLAN. It
        used to be skipped (``continue``), so a damaged plan looked deleted
        and the library looked healthy. It carries no ``frames``,
        ``integration_min``, ``shutter_min`` or ``targets``, because none of
        them was read from anything and a 0 would be a count of a plan that
        does not exist, and ``status`` is a key no plan row has. The id is
        the FILE's stem, which ``GET`` and ``DELETE`` address; ``name`` is left
        out when the file gives none a person could read (``_raw_name``).
        ``unreadable`` is ``_read_plan``'s reason, which the 422 of ``GET``
        repeats word for word.

        A FILE THE OS WILL NOT OPEN RIGHT NOW is left out, as before: a
        sharing violation from antivirus or an indexer says nothing about
        what the file holds, and a row calling a good plan unreadable would
        offer it for deletion. The next list shows it. A file that has gone
        between the listing and its read is left out too."""
        rows: list[dict] = []
        for path in list_json(self._dir):
            try:
                raw, plan = _read_plan(path)
                mtime = path.stat().st_mtime
            except PlanUnreadable as e:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                row = {"id": path.stem}
                if e.name is not None:
                    row["name"] = e.name
                rows.append({**row, "status": "unreadable",
                             "unreadable": e.reason, "mtime": mtime})
                continue
            except OSError:
                continue
            rows.append({
                "id": raw.get("id", path.stem),
                "name": raw.get("name") or plan.name,
                **_summarize(plan),
                "mtime": mtime,
            })
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return rows

    def get(self, plan_id: str) -> SequencePlan:
        """The stored plan. ``KeyError`` when there is no such file (or the
        OS will not open it, as ``list`` leaves such a file out), and
        ``PlanUnreadable`` when the file is there and is not a plan (#378):
        it used to raise pydantic's ``ValidationError``, which no route
        caught, so asking for a damaged plan by id was a 500."""
        try:
            return _read_plan(self._path(plan_id))[1]
        except OSError:
            raise KeyError(plan_id) from None

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
