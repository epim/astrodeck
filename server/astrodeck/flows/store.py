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

import json
import time
from pathlib import Path

from pydantic import ValidationError

from ..config import CONFIG_DIR
from ..persist import ensure_dir, list_json, read_json, safe_id_path, write_json_atomic
from .examples import examples
from .models import EXAMPLES_FOLDER, MY_FLOWS_FOLDER, FlowRecord

#: 2 -- a target's ``rotation`` of 0 used to mean "no angle constraint"; it now
#: means position angle 0.
#: 3 -- a target's ``rotation`` of 23.4 was the palette default, an angle nobody
#: chose (#150); it now reads "any angle". The writer stamps 3 on every save,
#: which is what makes a 23.4 in a v3 file evidence that somebody meant it.
#: See ``_migrate`` for why the file version is the only thing that can tell
#: either pair of readings apart.
FLOW_SCHEMA = 3
FLOWS_DIR = CONFIG_DIR / "flows"

#: The one-time note for the v2 -> v3 rewrite. It has to say that a DELIBERATE
#: 23.4 was rewritten too (a duplicated M31 example carries one), because the
#: migration cannot tell the two apart and the operator can.
ROTATION_234_NOTE = (
    'angle 23.4 was the old palette default and commanded a connected rotator '
    'to PA 23.4; it now reads "any angle". Set it again if you meant it.')

#: Longest reason an unreadable row carries. It is a card line, not a log.
_REASON_MAX = 160

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


class NewerSchemaFlow(FlowLibraryFull):
    """``save()`` refusing to overwrite a file a newer AstroDeck wrote.

    A SUBCLASS OF FlowLibraryFull FOR ONE REASON: ``_persist_flow`` answers
    that class with 409 and its ``code``, which is exactly the answer this
    refusal needs, and both mean "this write collides with what the library
    already holds". Its own class, so a caller can still tell them apart.
    """

    def __init__(self, message: str, code: str = "newer_schema"):
        super().__init__(message, code)


class FutureFlowSchema(ValueError):
    """A stored flow whose ``schema_version`` is newer than this build's.

    Its own class, not a generic ValueError, because it is not damage: the file
    is probably perfect, and the row it becomes tells the operator to update
    rather than that something broke."""

    def __init__(self, version: int):
        super().__init__(f"saved by a newer AstroDeck (schema {version}); "
                         f"update to open it")
        self.version = version


def _schema_of(raw: dict) -> int:
    """The file's ``schema_version``; absent means v1, the unversioned era."""
    try:
        return int(raw.get("schema_version") or 1)
    except (TypeError, ValueError, OverflowError):
        # int()'s own message quotes the value; a reason quotes nothing.
        # OverflowError is int(Infinity): not JSON, but Python's parser takes
        # the token, and without it a save over that id raised out as a 500.
        raise ValueError("its schema version is not a number") from None


def _migrate(raw: dict) -> dict:
    """Bring a stored flow up to ``FLOW_SCHEMA``, and say what that changed.

    A CHAIN, with no early return: each step runs for every file older than the
    version it introduces, so a v1 file takes v1 -> v2 and then v2 -> v3.

    v1 -> v2: a target's ``rotation`` of 0 meant "NO ANGLE CONSTRAINT" -- it was
    what a blank field compiled to. From v2 a negative value means unconstrained
    and 0 is a real position angle (north-up framing is something an operator
    asks for). Nothing inside the flow distinguishes the two readings, so the
    file's own version is the only evidence: a v1 file CANNOT have meant PA 0.
    Without this, every flow written under the old rule -- and every night's
    saved work -- would start commanding a physical rotator to 0 on its next run.
    No note: the meaning did not change, only its spelling.

    v2 -> v3: a target's ``rotation`` of 23.4 was the palette default (#150),
    so a v1 or v2 file holding it almost certainly never chose it. It becomes
    -1 with ``ROTATION_234_NOTE``. A v3 file is written after the default
    changed, so ITS 23.4 was set on purpose and is never rewritten. Only the
    number 23.4 matches (Python's ``==`` does not coerce): a string "23.4" was
    typed into the field, which is a choice.

    A FILE FROM THE FUTURE IS REFUSED (#153). Returning it unchanged -- what
    the old ``>= 2`` early return did -- loads a newer build's flow with this
    build's meaning, and every param whose meaning moved silently reverts.

    The notes land in ``flow["migrated"]``, REPLACING anything stored there, so
    a note is only ever this read's finding and never replayed from the file.

    Read-only: the migrated dict is returned, the file is left alone until the
    operator next saves it (which stamps the current FLOW_SCHEMA).
    """
    version = _schema_of(raw)
    if version > FLOW_SCHEMA:
        raise FutureFlowSchema(version)
    flow = raw.get("flow") or {}
    if not isinstance(flow, dict):
        raise ValueError("it holds no flow record")
    graph = flow.get("graph") or {}
    nodes = [n for n in ((graph.get("nodes") if isinstance(graph, dict) else None)
                         or []) if isinstance(n, dict)]
    notes: list[dict] = []
    if version < 2:
        for node in nodes:
            params = node.get("params")
            if isinstance(params, dict) and params.get("rotation") == 0:
                params["rotation"] = -1
    if version < 3:
        rewrote = False
        for node in nodes:
            params = node.get("params")
            if (node.get("type") == "target" and isinstance(params, dict)
                    and params.get("rotation") == 23.4):
                params["rotation"] = -1
                rewrote = True
        if rewrote:
            notes.append({"key": "rotation", "note": ROTATION_234_NOTE})
    flow["migrated"] = notes
    return flow


def _short_reason(exc: BaseException) -> str:
    """Why a file could not be read, in words that never carry a path.

    The library is viewer-readable and an OSError's own text is the full path
    of the file, so no exception's ``str()`` is used unless its type is known
    to be path-free. The operator needs to know THAT the flow is damaged and
    roughly how; the filename is already the row's id.
    """
    if isinstance(exc, json.JSONDecodeError):
        reason = f"not valid JSON (line {exc.lineno}, column {exc.colno})"
    elif isinstance(exc, UnicodeDecodeError):
        reason = "not UTF-8 text"
    elif isinstance(exc, ValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        msg = str(first.get("msg", "invalid")).removeprefix("Value error, ")
        reason = f"{where}: {msg}" if where else msg
        if len(errors) > 1:
            reason += f" (and {len(errors) - 1} more)"
    elif isinstance(exc, OSError):
        reason = "the file could not be read"
    elif type(exc) is ValueError:
        # Only ever raised by this module, with a sentence and no path.
        reason = str(exc)
    else:
        reason = type(exc).__name__
    return "unreadable: " + reason[:_REASON_MAX]


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

    def _scan(self) -> tuple[list[FlowRecord], list[dict]]:
        """Every file in the store: the records, and a row for each file that
        could not become one.

        ONE BAD FILE MUST NOT MAKE THE LIBRARY UNOPENABLE, and it must not
        vanish either (#153). The old loop honoured the first half by skipping
        the file, which made a damaged flow indistinguishable from a deleted
        one. So a failure becomes a visible, read-only row instead: a file a
        newer build wrote, JSON that does not parse, a record that fails
        validation (an unknown node type, say). None of them is ever returned
        as a record, so ``get`` -- and every route behind it -- answers 404.
        """
        records: list[FlowRecord] = []
        rows: list[dict] = []
        for path in list_json(self.dir):
            raw = None
            try:
                raw = read_json(path)
                if not isinstance(raw, dict):
                    raise ValueError("it holds no flow record")
                records.append(FlowRecord(**_migrate(raw)))
            except FileNotFoundError:
                continue                # deleted between the listing and now
            except FutureFlowSchema as e:
                rows.append(self._row(path, raw, str(e)))
            except Exception as e:      # noqa: BLE001 - every failure is a row
                rows.append(self._row(path, raw, _short_reason(e)))
        return records, rows

    @staticmethod
    def _row(path: Path, raw, reason: str) -> dict:
        """A library row for a file that is not a flow this build can open.

        The CARD SHAPE plus ``unreadable``, so the library draws it with the
        same component, filed in the folder the file names. Read-only and
        runnable by nothing. It claims no run (``last_run`` None): nothing this
        build could check backs one. The id is the FILE's stem, because that is
        what a save or a delete addresses; the name inside is used when there
        is one.
        """
        flow = raw.get("flow") if isinstance(raw, dict) else None
        flow = flow if isinstance(flow, dict) else {}
        graph = flow.get("graph") if isinstance(flow.get("graph"), dict) else {}
        name = flow.get("name")
        name = name.strip()[:120] if isinstance(name, str) and name.strip() else path.stem
        folder = MY_FLOWS_FOLDER
        if isinstance(flow.get("folder"), str):
            try:
                # Through the model's own folder rule rather than a copy of it.
                folder = FlowRecord(folder=flow["folder"]).folder
            except ValidationError:
                pass
        updated = flow.get("updated_ts")
        if not isinstance(updated, (int, float)) or isinstance(updated, bool):
            try:
                updated = path.stat().st_mtime
            except OSError:
                updated = None
        nodes, edges = graph.get("nodes"), graph.get("edges")
        return {
            "id": path.stem, "name": name, "folder": folder,
            "tagline": "", "readonly": True,
            "stages": len(nodes) if isinstance(nodes, list) else 0,
            "wires": len(edges) if isinstance(edges, list) else 0,
            "last_run": None, "last_result": "", "updated_ts": updated,
            "unreadable": reason,
        }

    def _on_disk(self) -> list[FlowRecord]:
        return self._scan()[0]

    def unreadable(self) -> list[dict]:
        """The library rows for files this build cannot open (#153): newer
        schema, unparseable JSON, or a record that fails validation. The route
        appends them to the card list; nothing else can reach them."""
        return self._scan()[1]

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
        """Folder rows with counts, for the library's section headers.

        An unreadable row counts in the folder it names, because the library
        lists it there: a header reading 0 above a visible row would be a
        header that lies, and a folder holding only damaged flows would vanish.
        """
        counts: dict[str, int] = {}
        mine, rows = self._scan()
        taken = {r.id for r in mine}
        for r in mine + [e for e in examples() if e.id not in taken]:
            counts[r.folder] = counts.get(r.folder, 0) + 1
        for row in rows:
            counts[row["folder"]] = counts.get(row["folder"], 0) + 1
        for seed in (MY_FLOWS_FOLDER, EXAMPLES_FOLDER):
            counts.setdefault(seed, 0)
        # My flows first (it leads the grid and carries the + NEW FLOW card),
        # Examples last, anything the user made in between, alphabetically.
        def rank(name: str) -> tuple[int, str]:
            return ({MY_FLOWS_FOLDER: 0, EXAMPLES_FOLDER: 2}.get(name, 1), name.lower())
        return [{"name": n, "count": counts[n], "readonly": n == EXAMPLES_FOLDER}
                for n in sorted(counts, key=rank)]

    # ----------------------------------------------------------------- write

    def _write(self, record: FlowRecord) -> None:
        """The one serialiser for a stored flow: stamped with FLOW_SCHEMA, and
        WITHOUT ``migrated``. That note is a message about one read; written
        into the file it would be a property of the flow, and it would stamp a
        v3 file with a v2 file's finding."""
        write_json_atomic(self._path(record.id),
                          {"schema_version": FLOW_SCHEMA, "id": record.id,
                           "flow": record.model_dump(by_alias=True,
                                                     exclude={"migrated"})})

    def _newer_schema_on_disk(self, flow_id: str) -> int | None:
        """The schema of the file at ``flow_id`` when a newer build wrote it,
        else None. A missing or damaged file is None: it holds no meaning a
        save could downgrade, and saving over it is how it gets repaired."""
        try:
            raw = read_json(self._path(flow_id))
            version = _schema_of(raw) if isinstance(raw, dict) else 0
        except (OSError, ValueError):
            return None
        return version if version > FLOW_SCHEMA else None

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
        under way. A file this build cannot read is not a record, so ``get``
        misses and nothing is written over it.

        It writes the MIGRATED record (a v2 23.4 lands as -1 at schema 3), and
        that is the angle the run it records actually used.
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
        self._write(record.model_copy(update=update))
        return True

    def save(self, record: FlowRecord) -> FlowRecord:
        if record.readonly or any(e.id == record.id for e in examples()):
            raise ReadOnlyFlow("the shipped examples are read-only — "
                               "duplicate one into My flows to edit it")
        # A FILE A NEWER BUILD WROTE IS NEVER OVERWRITTEN (#153). It is listed
        # but cannot be opened here, so any save aimed at its id comes from
        # somewhere else -- a stale tab, a duplicated id -- and writing it
        # would silently downgrade whatever the newer build meant.
        newer = self._newer_schema_on_disk(record.id)
        if newer is not None:
            raise NewerSchemaFlow(
                f"a newer AstroDeck saved this flow (schema {newer}); update "
                f"to edit it, or save under a new name")
        existing = {r.id for r in self._on_disk()}
        if record.id not in existing and len(existing) >= MAX_FLOWS:
            raise FlowLibraryFull(f"the flow library is full ({MAX_FLOWS})")
        errors = record.graph.validation_errors()
        if errors:
            raise ValueError("; ".join(errors))
        ensure_dir(self.dir)
        # `migrated` is cleared on the RETURNED record too, so the response to a
        # save never echoes a note, the client's or a stale read's.
        record = record.model_copy(update={"updated_ts": time.time(),
                                           "migrated": []})
        self._write(record)
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
        Examples folder refuses, because its members are code.

        NOT THROUGH ``save()``: re-parenting does not touch the graph, and
        save's validation would refuse a flow that was legal when it was stored
        -- a loop drawn before loops became a structural error (#149) -- in the
        middle of the folder, leaving some flows moved and the route answering
        500. A graph is refused where it would RUN. Everything this walks is a
        file on disk, the operator's by construction (the Examples are code);
        a file this build cannot read is not a record, so it stays put."""
        if old == EXAMPLES_FOLDER or new == EXAMPLES_FOLDER:
            raise ReadOnlyFlow("the Examples folder is fixed")
        moved = 0
        for r in self._on_disk():
            if r.folder == old:
                self._write(r.model_copy(update={"folder": new,
                                                 "updated_ts": time.time()}))
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
