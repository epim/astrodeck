"""Flow persistence — one ``flows/<id>.json`` per flow, atomically written.

Mirrors ``plans.PlanLibrary`` deliberately, down to ``safe_id_path``: the flow id
is client-controllable (request body AND path param, including the ``..%5C``
backslash vector on Windows), and a value like ``"../../astrodeck"`` would
otherwise let a PUT overwrite the server's own config. That defence is not
re-derived here; it is the same call, so the two stores cannot drift apart on
the one property that matters most.

THE EXAMPLES ARE NOT ON DISK. They are code fixtures, merged into every read and
refused by every write. Writing them out at first boot would mean an operator's
edit could silently become the shipped example, and a later release "fixing" an
example would collide with a file the user believes is theirs.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..config import CONFIG_DIR
from ..persist import ensure_dir, list_json, read_json_or, safe_id_path, write_json_atomic
from .examples import examples
from .models import EXAMPLES_FOLDER, MY_FLOWS_FOLDER, FlowRecord

#: 2 -- a target's ``rotation`` of 0 used to mean "no angle constraint"; it now
#: means position angle 0. See ``_migrate`` for why the file version is the only
#: thing that can tell the two apart.
FLOW_SCHEMA = 2
FLOWS_DIR = CONFIG_DIR / "flows"

#: Soft quota, same reasoning as PlanLibrary's: a client must not be able to
#: fill the disk and make every list linear-slow. Upserting an existing id is
#: always allowed.
MAX_FLOWS = 500


class FlowLibraryFull(ValueError):
    def __init__(self, message: str, code: str = "library_full"):
        super().__init__(message)
        self.code = code


class ReadOnlyFlow(ValueError):
    """A write aimed at one of the shipped Examples."""

    def __init__(self, message: str, code: str = "readonly"):
        super().__init__(message)
        self.code = code



def _migrate(raw: dict) -> dict:
    """Bring a stored flow up to ``FLOW_SCHEMA``.

    v1 -> v2: a target's ``rotation`` of 0 meant "NO ANGLE CONSTRAINT" -- it was
    what a blank field compiled to. From v2 a negative value means unconstrained
    and 0 is a real position angle (north-up framing is something an operator
    asks for). Nothing inside the flow distinguishes the two readings, so the
    file's own version is the only evidence: a v1 file CANNOT have meant PA 0.
    Without this, every flow written under the old rule -- and every night's
    saved work -- would start commanding a physical rotator to 0 on its next run.

    Read-only: the migrated dict is returned, the file is left alone until the
    operator next saves it (which stamps the current FLOW_SCHEMA).
    """
    flow = raw.get("flow") or {}
    if int(raw.get("schema_version") or 1) >= 2:
        return flow
    for node in (flow.get("graph") or {}).get("nodes") or []:
        params = node.get("params")
        if isinstance(params, dict) and params.get("rotation") == 0:
            params["rotation"] = -1
    return flow


class FlowStore:
    def __init__(self, directory: Path | None = None):
        self._dir = directory

    @property
    def dir(self) -> Path:
        # Resolved LIVE, never bound at import: a test monkeypatches CONFIG_DIR
        # and every other store in this codebase honours that (see gallery's
        # capture_root note). Binding it here is how a maintenance path ends up
        # reading a different directory from the server.
        return self._dir if self._dir is not None else CONFIG_DIR / "flows"

    def _path(self, flow_id: str) -> Path:
        return safe_id_path(self.dir, flow_id)

    # ------------------------------------------------------------------ read

    def _on_disk(self) -> list[FlowRecord]:
        out: list[FlowRecord] = []
        for path in list_json(self.dir):
            raw = read_json_or(path)
            if not isinstance(raw, dict):
                continue
            try:
                out.append(FlowRecord(**_migrate(raw)))
            except Exception:
                # A corrupt or future-schema file is SKIPPED, not fatal: one bad
                # file must not make the whole library unopenable.
                continue
        return out

    def load_all(self) -> list[FlowRecord]:
        """Every flow: the operator's, plus the read-only Examples.

        A user flow that somehow carries an example's id wins, so a corrupted
        fixture can be shadowed rather than bricking the library."""
        mine = self._on_disk()
        taken = {r.id for r in mine}
        return mine + [e for e in examples() if e.id not in taken]

    def get(self, flow_id: str) -> FlowRecord:
        for r in self.load_all():
            if r.id == flow_id:
                return r
        raise KeyError(flow_id)

    def folders(self) -> list[dict]:
        """Folder rows with counts, for the library's section headers."""
        counts: dict[str, int] = {}
        for r in self.load_all():
            counts[r.folder] = counts.get(r.folder, 0) + 1
        for seed in (MY_FLOWS_FOLDER, EXAMPLES_FOLDER):
            counts.setdefault(seed, 0)
        # My flows first (it leads the grid and carries the + NEW FLOW card),
        # Examples last, anything the user made in between, alphabetically.
        def rank(name: str) -> tuple[int, str]:
            return ({MY_FLOWS_FOLDER: 0, EXAMPLES_FOLDER: 2}.get(name, 1), name.lower())
        return [{"name": n, "count": counts[n], "readonly": n == EXAMPLES_FOLDER}
                for n in sorted(counts, key=rank)]

    # ----------------------------------------------------------------- write

    def touch_run(self, flow_id: str, *, ts: float | None = None,
                  result: str | None = None) -> bool:
        """Record that a flow RAN. Returns True if anything was written.

        `last_run`/`last_result` have existed on FlowRecord since the library
        shipped and the cards render all four states, but nothing ever wrote
        them - so every flow said NEVER RUN forever, including the two on this
        rig whose descriptions say "First flow run on the real rig". The API
        deliberately re-derives both from the stored record on every PUT so a
        client cannot forge a green card; that closed the loop, because no
        server-side writer was added to replace it. This is that writer.

        NOT `save()`, for two reasons. `save` raises ReadOnlyFlow for the
        shipped examples, and the M16 example is REQUIRED to run on the
        simulator - an unguarded write-back would turn every example run into a
        500 *after* the engine had already started. And `save` re-stamps
        `updated_ts`, which would make running a flow look like editing it.

        Best-effort by contract: a read-only flow returns False rather than
        raising, because the caller is bookkeeping after a run that is already
        under way.
        """
        try:
            record = self.get(flow_id)
        except KeyError:
            return False
        if record.readonly or any(e.id == record.id for e in examples()):
            return False
        update: dict = {}
        if ts is not None:
            update["last_run"] = ts
        if result is not None:
            update["last_result"] = result
        if not update:
            return False
        record = record.model_copy(update=update)
        write_json_atomic(self._path(record.id),
                          {"schema_version": FLOW_SCHEMA, "id": record.id,
                           "flow": record.model_dump(by_alias=True)})
        return True

    def save(self, record: FlowRecord) -> FlowRecord:
        if record.readonly or any(e.id == record.id for e in examples()):
            raise ReadOnlyFlow("the shipped examples are read-only — "
                               "duplicate one into My flows to edit it")
        existing = {r.id for r in self._on_disk()}
        if record.id not in existing and len(existing) >= MAX_FLOWS:
            raise FlowLibraryFull(f"the flow library is full ({MAX_FLOWS})")
        errors = record.graph.validation_errors()
        if errors:
            raise ValueError("; ".join(errors))
        ensure_dir(self.dir)
        record = record.model_copy(update={"updated_ts": time.time()})
        write_json_atomic(self._path(record.id),
                          {"schema_version": FLOW_SCHEMA, "id": record.id,
                           "flow": record.model_dump(by_alias=True)})
        return record

    def delete(self, flow_id: str) -> bool:
        if any(e.id == flow_id for e in examples()):
            raise ReadOnlyFlow("the shipped examples cannot be deleted")
        path = self._path(flow_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def rename_folder(self, old: str, new: str) -> int:
        """Move every flow in ``old`` to ``new``. Returns how many moved.

        RE-PARENTING, not a rename of a directory — folders are a field on the
        record, so this is the only thing "renaming a folder" can mean. The
        Examples folder refuses, because its members are code."""
        if old == EXAMPLES_FOLDER or new == EXAMPLES_FOLDER:
            raise ReadOnlyFlow("the Examples folder is fixed")
        moved = 0
        for r in self._on_disk():
            if r.folder == old:
                self.save(r.model_copy(update={"folder": new}))
                moved += 1
        return moved

    def delete_folder(self, name: str, reparent_to: str = MY_FLOWS_FOLDER) -> int:
        """Remove a folder by re-parenting its flows. Never deletes a flow —
        losing a night's automation because a folder was tidied away is not a
        trade anybody would choose."""
        if name in (EXAMPLES_FOLDER, MY_FLOWS_FOLDER):
            raise ReadOnlyFlow(f"{name} cannot be deleted")
        return self.rename_folder(name, reparent_to)


flow_store = FlowStore()
