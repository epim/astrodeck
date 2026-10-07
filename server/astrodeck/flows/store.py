# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

ONLY ``save()`` REWRITES A FILE (carry-over 1, #150). The bookkeeping writers --
a folder rename or delete, a run's ``touch_run`` -- change their own fields in
the raw JSON and nothing else, so a file keeps the version it was written at
until the operator saves it, and the read keeps saying what that version means.

A SAVE APPLIES THE SAVE RULES (``save_rules.prepare_save``, #189 Revision 2
rulings 2 and 3): ``counts`` becomes "Accepted subs" on every TARGET and POOL,
and each TARGET's ``frameAnchor`` is decided from the file being replaced,
never from the client. Here, in the one writer, so every door converges.
A save that CANNOT READ the file it replaces refuses (``_stored``,
``StoredFlowUnreadable``, #350): read as "no such flow", a transient read
error moved every anchor to where its block is drawn and restarted counts in
silence. The same read gives the save its bookkeeping (``_bookkeeping``,
#364), so one refusal covers both.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .. import config as _config
from ..persist import ensure_dir, list_json, read_json, safe_id_path, write_json_atomic
from .compile import NEXT_PORT, PASS_PORT, is_multi_panel
from .examples import examples
from .models import EXAMPLES_FOLDER, MY_FLOWS_FOLDER, FlowGraph, FlowRecord
from .nodes import dusk_auto_resume
from .save_rules import (ACCEPTED_SUBS, COUNTED_TYPES, counts_attempts,
                         prepare_save)

#: 2 -- a target's ``rotation`` of 0 used to mean "no angle constraint"; it now
#: means position angle 0.
#: 3 -- a target's ``rotation`` of 23.4 was the palette default, an angle nobody
#: chose (#150); it now reads "any angle". ``save()`` stamps 3 or 4, and
#: nothing else stamps anything, which is what makes a 23.4 in a file of v3 or
#: later evidence that somebody saved it on purpose.
#: 4 -- the mosaic block (#189, spec 3.6): meanings a v3 build would misread
#: (``schema_for``). The v3 -> v4 read changes nothing in the graph.
#: 5 -- DUSK WINDOW's ``autoResume`` (#195, owner ruling 7 on #189): the choice
#: that replaced ``repeat`` as the thing that decides whether a flow resumes on
#: a later night. 0.3.40 (schema 4) read ``repeat``'s own default, "Single
#: night", as "do not resume", so a file it wrote cannot say whether that was
#: a choice. The v4 -> v5 read maps every such DUSK to On and says so on every
#: read until the operator saves (``_migrate``, ``AUTO_RESUME_NOTE``).
#: See ``_migrate`` for why the file version is the only thing that can tell
#: either pair of readings apart.
#:
#: RETIRING ``repeat`` TOOK NO NUMBER (#195, WP-118). Once the ``campaign``
#: block, the doctor and Tonight keyed on ``autoResume`` and the flow's own
#: shape, nothing read ``repeat``; the vocabulary stopped declaring it and a
#: stored one is inert, read as Automatic resume On like the missing key
#: (``nodes.dusk_auto_resume``) and kept verbatim through a load and a save.
#: There is nothing for a version to say: no stored word turns Off, and a build
#: that reads such a file differently (0.3.41, which still keyed ``campaign``
#: on it) runs the same night, with the same resume.
FLOW_SCHEMA = 5

#: What ``save()`` stamps a file whose graph uses a FLOW_SCHEMA 4 meaning and
#: no FLOW_SCHEMA 5 one (``schema_for``).
V4_SCHEMA = 4

#: What ``save()`` stamps a file whose graph uses no FLOW_SCHEMA 4 meaning
#: (``schema_for``). Not 4 regardless: a build from S0 to S2 refuses a v4 file
#: as a future schema, so stamping 4 on a flow it could read correctly would
#: lock a downgrade out of it for nothing (spec 3.6's downgrade matrix).
V3_SCHEMA = 3

#: The v4 meanings of a flow-level setting: ``settings`` key -> the value a v3
#: build, which has no settings at all, would misread. ``whenWaiting``'s other
#: value is what a v3 build does anyway (it has one behaviour), so only this
#: one needs the stamp.
_V4_SETTINGS = {"whenWaiting": "Wait for the mosaic"}

#: The ``angle`` a v3 build would misread: it has no angle choice, commands
#: the rotator whenever ``rotation`` is 0 or more, and so would turn a fixed
#: camera to the planned PA (spec 3.6).
_V4_ANGLE = "Camera fixed at PA"

#: The note for the v2 -> v3 rewrite, said on every read of the file until the
#: operator saves it (only ``save()`` stamps FLOW_SCHEMA, carry-over 1). It has
#: to say that a DELIBERATE 23.4 was rewritten too (a duplicated M31 example
#: carries one), because the migration cannot tell the two apart and the
#: operator can.
ROTATION_234_NOTE = (
    'angle 23.4 was the old palette default and commanded a connected rotator '
    'to PA 23.4; it now reads "any angle". Set it again if you meant it.')

#: The note for the v4 -> v5 read (#195, owner ruling 7 on #189, VERBATIM), said
#: on every read of the file until the operator saves it, like the 23.4 note.
#: The read maps a DUSK WINDOW whose ``repeat`` says "Single night" and which
#: has no ``autoResume`` to On, and the flow shows that: nothing in the file
#: can tell a choice of "Single night" from the field's default, and 0.3.40
#: read both as "do not resume", which is not what the label ever said.
AUTO_RESUME_NOTE = (
    "'Single night' never stopped the next night's automatic resume; this "
    "flow now shows that as ON. Turn it off if you meant one night only.")

#: Ruling 2's line (#189 Revision 2, verbatim), said on every read while any
#: TARGET or POOL counts every sub taken. The read never switches it: loading
#: must not change what a flow means, so the flow keeps counting attempts
#: until the operator saves it, and the save switches it
#: (``save_rules.prepare_save``). What the line adds when the flow has a
#: dormant session is the route's to say; the store knows no sessions.
COUNTS_NOTE = (
    "This flow counts every sub taken, rejected ones included. New flows "
    "count accepted subs only, and saving this flow switches it.")

#: Longest reason an unreadable row carries. It is a card line, not a log.
_REASON_MAX = 160

#: Soft quota, same reasoning as PlanLibrary's: a client must not be able to
#: fill the disk and make every list linear-slow. Upserting an existing id is
#: always allowed. Counted in FILES, readable or not, from a listing of the
#: directory (``save_and_report``, #433).
MAX_FLOWS = 500

#: The fields a save takes from the file it replaces and never from the
#: record it is given (``_bookkeeping``, #364). The library card draws the
#: last two, and nothing but ``touch_run`` may write them: a client that sent
#: ``last_result: "ok"`` for a flow that never ran would get a green card.
#: ``created_ts`` is written once, by the save that creates the flow.
#: ``readonly``, the fourth field a client could forge, is refused, not
#: carried (``save_and_report``), and ``_persist_flow`` clears it first.
BOOKKEEPING = ("created_ts", "last_run", "last_result")


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


class StoredFlowUnreadable(FlowLibraryFull):
    """``save()`` refusing to write over a flow it could not read (#350).

    The save measures every TARGET's framing against the file it replaces
    (``save_rules.prepare_save``): that file's ``frameAnchor`` is where the
    block's counts started. A read that failed for a reason that says
    nothing about the file (a sharing violation from antivirus or an
    indexer, an interrupted read, permissions that could not be secured)
    is not "no such flow", and anchoring as if it were would move every
    block's anchor to where it is drawn now and restart counts in silence.
    So nothing is written, and the operator saves again.

    A SUBCLASS OF FlowLibraryFull FOR THE REASON ``NewerSchemaFlow`` IS ONE:
    ``_persist_flow`` answers that class with 409 and its ``code``, which is
    this refusal's answer too, with no route of its own. Its own class, so a
    caller can still tell them apart."""

    def __init__(self, message: str, code: str = "stored_unreadable"):
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


def _v4_meanings(graph: FlowGraph) -> list[str]:
    """Every FLOW_SCHEMA 4 meaning ``graph`` uses, by name; empty when a v3
    build would read the whole graph as this build does (spec 3.6).

    * a multi-panel block: a build before S3 ignores ``rows`` and ``cols``
      and shoots the centre, as ``compile.is_multi_panel`` reads the grid.
    * ``angle`` "Camera fixed at PA" (``_V4_ANGLE``).
    * a loop wire, READ AS ANY WIRE ON THE PANEL LOOP'S PORTS: out of a
      stage's ``pass`` or into a TARGET's ``next``. Wider than
      ``compile.loop_wires`` (only a wire from the lane's tail), because what
      decides the stamp is whether an older build can read the wire, and it
      has neither port; a pass wire from mid-lane (M12) is no more readable
      to it than the loop wire.
    * ``counts`` "Accepted subs" on a TARGET or POOL: a v3 build has no
      ``counts`` and would count every sub again.
    * ``settings.whenWaiting`` "Wait for the mosaic" (``_V4_SETTINGS``).

    * DUSK's ``autoResume`` "Off" (#195): spec 3.6's sixth meaning. A v3 build
      has no such key, so it would resume the flow on every later night.
    """
    found: list[str] = []
    if any(is_multi_panel(n) for n in graph.nodes):
        found.append("multi-panel block")
    if any(n.type == "target" and (n.params or {}).get("angle") == _V4_ANGLE
           for n in graph.nodes):
        found.append("camera fixed at PA")
    if any(e.fromPort == PASS_PORT or e.toPort == NEXT_PORT
           for e in graph.edges):
        found.append("loop wire")
    if any(n.type in COUNTED_TYPES
           and (n.params or {}).get("counts") == ACCEPTED_SUBS
           for n in graph.nodes):
        found.append("accepted subs")
    settings = graph.settings or {}
    if any(settings.get(k) == v for k, v in _V4_SETTINGS.items()):
        found.append("wait for the mosaic")
    if _auto_resume_off(graph):
        found.append("auto-resume off")
    return found


def _auto_resume_off(graph: FlowGraph) -> bool:
    """A DUSK WINDOW that chose Off (``nodes.dusk_auto_resume``)."""
    return any(n.type == "dusk" and not dusk_auto_resume(n.params)
               for n in graph.nodes)


def _owes_the_auto_resume_note(node_type, params) -> bool:
    """A DUSK WINDOW whose STORED ``repeat`` is "Single night" and which
    carries no ``autoResume``: the one shape 0.3.40 read as "do not resume"
    and this build reads as On. It is what the v4 -> v5 read says a note about
    (``AUTO_RESUME_NOTE``), and what ``schema_for`` stamps 5 for, which is how
    a save retires the note.

    ``repeat`` must be STORED as that word. A DUSK with no ``repeat`` key at
    all is a file older than the field (hand-written, or from before the
    palette wrote it) or one created since the vocabulary stopped declaring
    it (WP-118); the operator never chose, or was shown, "Single night" for
    it, and a note quoting it would be about a choice they did not make.
    The other words ``repeat`` held ("Nightly until pool complete", "Nightly
    x30") owe nothing: they read as On, and no build ever read them as "do
    not resume" (0.3.40 read only "Single night" that way).
    An ``autoResume`` of ANY value, a blank one included, means the key exists
    and was written by a build that knew it, so nothing here is owed."""
    if node_type != "dusk" or not isinstance(params, dict):
        return False
    if "autoResume" in params:
        return False
    return params.get("repeat") == "Single night"


def schema_for(graph: FlowGraph) -> int:
    """The version ``save()`` stamps a file holding ``graph``, in three tiers:

    * FLOW_SCHEMA (5) when DUSK's ``autoResume`` is Off, or when the graph
      holds a DUSK that owes the v4 -> v5 note (``_owes_the_auto_resume_note``).
      The second is what makes "shown on every read until the operator saves"
      true: a save writes the graph as the operator kept it, with no
      ``autoResume`` in it, so only a file version can say the note has been
      seen. It is also the right downgrade guard: 0.3.40 would read such a
      DUSK as "do not resume", so it refuses the file as a future schema
      instead. A DUSK that states both ``autoResume`` On and a ``repeat`` of
      "Single night" (every wizard flow) is NOT stamped for that: it is a
      downgrade-matrix row, not a version (spec 3.6).
    * V4_SCHEMA (4) when the graph uses any other meaning a v3 build would
      misread (``_v4_meanings``).
    * V3_SCHEMA (3) otherwise.

    Every save switches ``counts`` to "Accepted subs" (ruling 2), so in
    practice every flow with a TARGET or POOL saved on this build stamps at
    least 4, and a build from S0 to S2 refuses it loudly as a future schema.
    That is the point: such a build would count every sub again."""
    if _auto_resume_off(graph) or any(
            _owes_the_auto_resume_note(n.type, n.params) for n in graph.nodes):
        return FLOW_SCHEMA
    return V4_SCHEMA if _v4_meanings(graph) else V3_SCHEMA


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

    v3 -> v4: nothing in the graph. v4 adds meanings (``schema_for``), each
    behind a key or a value a v3 file cannot hold, so a v3 graph already
    means under v4 what it meant.

    v4 -> v5 (#195): nothing in the graph either, and a note. DUSK WINDOW's
    ``repeat`` stopped deciding whether a flow resumes on a later night and
    ``autoResume`` took over, "On" for a flow that says nothing. 0.3.40
    (schema 4) read ``repeat``'s default, "Single night", as "do not resume",
    and nothing inside the file tells a choice of it from the default, so the
    read cannot repair it and does not try: it maps the flow to On, which is
    what the missing key means, and says so with ``AUTO_RESUME_NOTE`` for
    every DUSK that owes it (``_owes_the_auto_resume_note``). A flow whose
    operator meant one night turns Off, once, on purpose; one whose operator
    did not is no longer silently stopped. Only files older than 5 say it: a
    save stamps 5 for exactly that graph (``schema_for``), which is the
    evidence the operator has seen the note and kept the flow.

    THE COUNTS NOTE IS NOT A VERSION STEP (ruling 2). While any TARGET or
    POOL counts every sub taken (``save_rules.counts_attempts``: no
    ``counts`` key, "Every sub taken", or a value this build does not offer)
    the read adds ``COUNTS_NOTE``, whatever the file's version, because the
    save is what switches it and a v4 file can hold "Every sub taken" too (a
    hand edit, or an API writer that saved around the store). The value is
    NEVER rewritten here: a flow that counted attempts yesterday counts
    attempts until the operator saves it.

    A FILE FROM THE FUTURE IS REFUSED (#153). Returning it unchanged -- what
    the old ``>= 2`` early return did -- loads a newer build's flow with this
    build's meaning, and every param whose meaning moved silently reverts.

    The notes land in ``flow["migrated"]``, REPLACING anything stored there, so
    a note is only ever this read's finding and never replayed from the file.

    READ-ONLY, AND IT STAYS READ-ONLY UNTIL THE OPERATOR SAVES. Only ``save()``
    stamps FLOW_SCHEMA; the bookkeeping writers edit the raw file, not this
    result (carry-over 1). So an unsaved v2 file reads v2 every time, and the
    note is said on every read -- the editor's, and every run's -- until a save
    makes the file current. That is the only point at which the operator has
    seen the migrated graph and kept it.

    IT REWRITES THE DICT IT IS GIVEN, in place. A caller that will write the
    raw JSON back hands it a copy (``_entries(pristine=True)``).
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
    # v3 -> v4 rewrites nothing (see above), so it has no step here. v4 -> v5
    # rewrites nothing either: it only says what a missing `autoResume` means.
    if version < 5 and any(_owes_the_auto_resume_note(n.get("type"), n.get("params"))
                           for n in nodes):
        notes.append({"key": "autoResume", "note": AUTO_RESUME_NOTE})
    if any(counts_attempts(n.get("type"), n.get("params")) for n in nodes):
        notes.append({"key": "counts", "note": COUNTS_NOTE})
    flow["migrated"] = notes
    return flow


def _newer_sentence(version: int) -> str:
    """What a save aimed at a newer build's file is refused with, wherever
    the save finds the file is one (``NewerSchemaFlow``)."""
    return (f"a newer AstroDeck saved this flow (schema {version}); update "
            f"to edit it, or save under a new name")


def _record_of(raw, *, pristine: bool = False) -> FlowRecord:
    """What this build makes of one file's parsed JSON, or an exception saying
    why it makes nothing (``FutureFlowSchema`` for a newer build's file).

    ``pristine`` migrates a copy and leaves ``raw`` exactly as the file holds
    it -- see ``FlowStore._entries``."""
    if not isinstance(raw, dict):
        raise ValueError("it holds no flow record")
    return FlowRecord(**_migrate(copy.deepcopy(raw) if pristine else raw))


def _bookkeeping(prior: FlowRecord | None, now: float) -> dict:
    """``BOOKKEEPING`` for a save whose ``_stored`` read answered ``prior``:
    the prior's own values, or a new flow's (created ``now``, never run).

    FROM THE ONE READ THAT CAN REFUSE (#364). ``_persist_flow`` used to take
    these from ``flow_store.get``, a walk of the whole library through
    ``_entries``, which turns any failure to read a file into an unreadable
    row. A transient read error there (a sharing violation from antivirus or
    an indexer) made ``get`` answer "no such flow" while ``_stored``'s read,
    a moment later, succeeded, so the save went ahead and wrote the flow as
    created now and never run: the card of a flow that had run lost its last
    run in silence. Taken from ``_stored``'s record instead, a read that
    fails refuses the whole save (``StoredFlowUnreadable``) and a read that
    works carries the history; the listing's read decides nothing here.

    ``prior`` is None for a missing file, a file the library lists as
    unreadable, and a record that carries another id (``_stored``): none of
    them is this flow's history, so each is a new flow's, as a repair or a
    first save should be."""
    if prior is None:
        return {"created_ts": now, "last_run": None, "last_result": ""}
    return {k: getattr(prior, k) for k in BOOKKEEPING}


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


def catalogue_position(name: str) -> tuple[float, float, str] | None:
    """``save_rules``' resolver: where the shipped catalogue puts a TARGET
    known only by its name, and the canonical identity it is keyed on, or
    None when the catalogue has no row.

    Through ``tonight.resolve_target``, THE one resolver (#229), which
    ``to_plan`` keys the block's ids through too, so the anchor a save writes
    names the object the compile keys. Asked at ``tonight.IDENTITY_WHEN``:
    that instant chooses the row whatever instant is passed, and placing the
    row there too keeps a save from depending on the clock. The position is
    used only to lay a named anchor out for ``reframe_carry``, which holds no
    coordinates of its own. Imported and looked up at call time, so the
    store does not load the catalogue until a name needs it, and a test that
    replaces the resolver replaces it here as well."""
    from . import tonight
    hit = tonight.resolve_target(name, tonight.IDENTITY_WHEN)
    return None if hit is None else (hit.ra_hours, hit.dec_deg, hit.identity)


class FlowStore:
    def __init__(self, directory: Path | None = None, *,
                 resolve: Callable[[str], tuple[float, float, str] | None]
                 | None = None):
        self._dir = directory
        #: How a save places a TARGET known only by its name (``save_rules``'
        #: ``Resolver``). The catalogue unless a caller injects another.
        self.resolve = resolve if resolve is not None else catalogue_position

    @property
    def dir(self) -> Path:
        # Resolved LIVE, never bound at import: a test monkeypatches CONFIG_DIR
        # and every other store in this codebase honours that (see gallery's
        # capture_root note). Binding it here is how a maintenance path ends up
        # reading a different directory from the server. THROUGH THE CONFIG
        # MODULE (#436): this comment was here while the module imported the
        # name itself, which is binding it at import by another route, so
        # repointing ``config.CONFIG_DIR`` never moved ``flow_store``.
        return self._dir if self._dir is not None else _config.CONFIG_DIR / "flows"

    def _path(self, flow_id: str) -> Path:
        return safe_id_path(self.dir, flow_id)

    # ------------------------------------------------------------------ read

    def _entries(self, *, pristine: bool = False):
        """One walk of the directory: ``(path, raw, record, reason)`` per file.

        ``record`` is what this build makes of the file, or None with the
        ``reason`` it cannot: a file a newer build wrote, JSON that does not
        parse, a record that fails validation. The judgment is ``_record_of``
        and nothing else -- this walk applies it for the list, ``get`` and the
        folder verbs, ``touch_run`` applies it to its one file -- so no two of
        them can disagree about which files are rows.

        ``pristine`` returns ``raw`` exactly as the file holds it, for a writer
        that will edit it and write it back. It costs a copy, because
        ``_migrate`` rewrites the dict it is given, so only the writers ask.
        Without it ``raw`` is the migrated dict, which is all a row needs.
        """
        for path in list_json(self.dir):
            raw = None
            try:
                raw = read_json(path)
                record = _record_of(raw, pristine=pristine)
            except FileNotFoundError:
                continue                # deleted between the listing and now
            except FutureFlowSchema as e:
                yield path, raw, None, str(e)
            except Exception as e:      # noqa: BLE001 - every failure is a row
                yield path, raw, None, _short_reason(e)
            else:
                yield path, raw, record, None

    def _scan(self) -> tuple[list[FlowRecord], list[dict], set[str]]:
        """Every file in the store: the records, a row for each file that
        could not become one, and every id a file on disk holds.

        ONE BAD FILE MUST NOT MAKE THE LIBRARY UNOPENABLE, and it must not
        vanish either (#153). The old loop honoured the first half by skipping
        the file, which made a damaged flow indistinguishable from a deleted
        one. So a failure becomes a visible, read-only row instead. None of
        them is ever returned as a record, so ``get`` -- and every route behind
        it -- answers 404.

        THE IDS ARE WHAT SHADOW THE EXAMPLES (``_examples_free``): each file's
        stem, which is what a save and a delete address, readable or not, and
        each record's own id.
        """
        records: list[FlowRecord] = []
        rows: list[dict] = []
        taken: set[str] = set()
        for path, raw, record, reason in self._entries():
            taken.add(path.stem)
            if record is None:
                rows.append(self._row(path, raw, reason))
            else:
                records.append(record)
                taken.add(record.id)
        return records, rows, taken

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

    @staticmethod
    def _examples_free(taken: set[str]) -> list[FlowRecord]:
        """The Examples no file on disk has taken the id of.

        ONE RULE, READABLE OR NOT (carry-over 7, #153). A file with an
        example's id wins, so a corrupted fixture can be shadowed rather than
        bricking the library. It used to take only a READABLE file's id, so a
        damaged ``example-m16.json`` listed twice -- the example from code and
        the file's row, one id for two entries -- and ``get`` opened the
        example while ``delete`` refused the file. Now the row is the one
        entry, ``get`` answers 404, and deleting the file brings the example
        back."""
        return [e for e in examples() if e.id not in taken]

    def listing(self) -> tuple[list[FlowRecord], list[dict]]:
        """The whole library from ONE walk of the directory: every record, the
        Examples included, and a row for each file this build cannot open.

        One walk because ``GET /api/flows`` used to take two (``load_all``,
        then ``unreadable``), parsing and validating every file twice, and the
        two halves of one response could disagree about a file written between
        them."""
        mine, rows, taken = self._scan()
        return mine + self._examples_free(taken), rows

    def unreadable(self) -> list[dict]:
        """The library rows for files this build cannot open (#153): newer
        schema, unparseable JSON, or a record that fails validation. The route
        appends them to the card list; nothing else can reach them."""
        return self._scan()[1]

    def load_all(self) -> list[FlowRecord]:
        """Every flow: the operator's, plus the read-only Examples no file on
        disk shadows (``_examples_free``)."""
        return self.listing()[0]

    def get(self, flow_id: str) -> FlowRecord:
        for r in self.load_all():
            if r.id == flow_id:
                return r
        raise KeyError(flow_id)

    def folders(self) -> list[dict]:
        """Folder rows with counts, for the library's section headers.

        Counted from ``listing()``, the same entries the library draws: an
        unreadable row counts in the folder it names, because a header reading
        0 above a visible row would be a header that lies, and a folder holding
        only damaged flows would vanish. A shadowed example is not counted,
        because it is not drawn.
        """
        counts: dict[str, int] = {}
        records, rows = self.listing()
        for r in records:
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
        """``save()``'s serialiser, and ONLY save's: stamped with the version
        its graph needs (``schema_for``: 5, 4 or 3), and WITHOUT ``migrated``.
        That note is a
        message about one read; written into the file it would be a property
        of the flow, and it would stamp a current file with an old one's
        finding.

        Nothing else may call it. It writes the MIGRATED record, and stamping
        that is right only when the operator has just seen and kept the
        migrated graph -- which is what a save is. The bookkeeping writers use
        ``_edit_raw`` (carry-over 1)."""
        write_json_atomic(self._path(record.id),
                          {"schema_version": schema_for(record.graph),
                           "id": record.id,
                           "flow": record.model_dump(by_alias=True,
                                                     exclude={"migrated"})})

    @staticmethod
    def _edit_raw(path: Path, raw: dict, fields: dict) -> None:
        """Write ``fields`` into a stored file's ``flow`` object and change
        NOTHING ELSE: not its ``schema_version``, not its graph, not a key this
        build does not model (carry-over 1, #150).

        ``raw`` is the file exactly as read (``_entries(pristine=True)``). Going
        through the record instead -- what the folder verbs and ``touch_run``
        used to do -- writes the MIGRATED graph at FLOW_SCHEMA: a v2 file's
        inherited 23.4 lands as -1 in a v3 file, and the version that let the
        next read say so is gone. Moving a flow to another folder, or running
        it, would have silently re-meant it.

        Atomic like every write here: ``write_json_atomic`` stages, fsyncs,
        replaces and keeps a ``.bak``.
        """
        flow = raw.get("flow")
        if not isinstance(flow, dict):
            # Every FlowRecord field has a default, so a file with no flow
            # object still opens; give the fields somewhere to live.
            flow = raw["flow"] = {}
        flow.update(fields)
        write_json_atomic(path, raw)

    def _newer_schema_on_disk(self, flow_id: str) -> int | None:
        """The schema of the file at ``flow_id`` when a newer build wrote it,
        else None. A missing or damaged file is None: it holds no meaning a
        save could downgrade, and saving over it is how it gets repaired.
        Damaged includes nested past the parser's depth, which ``json``
        answers with RecursionError, not a ValueError: uncaught, it escaped
        every save aimed at that id as a 500 (#363, found by S4-SAVE's test
        of ``_stored``). A read that fails is None here too, and ``_stored``,
        which reads the file again, refuses it."""
        try:
            raw = read_json(self._path(flow_id))
            version = _schema_of(raw) if isinstance(raw, dict) else 0
        except (OSError, ValueError, RecursionError):
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
        under way. A file this build cannot open (a row, #153) is not a record,
        so nothing is written into it.

        IT EDITS THE RAW FILE: ``last_run`` and ``last_result``, nothing else,
        and the file keeps its ``schema_version`` (carry-over 1). It used to
        write the migrated record back at FLOW_SCHEMA, so the first run of a
        v2 flow retired its 23.4 note for good -- said once, in a log line, and
        never again. Only ``save()`` stamps FLOW_SCHEMA; until the operator
        saves, every read of the flow, and every run, says the note.

        THE FILE AT ``flow_id``, and only when it holds that flow: the one a
        save or a delete of that id addresses. Opening one file, not scanning
        the library, because this runs at every start and every finalize.
        """
        if any(e.id == flow_id for e in examples()):
            return False
        update: dict = {}
        if ts is not None:
            update["last_run"] = ts
        if result is not None:
            update["last_result"] = result
        if not update:
            return False
        try:
            path = self._path(flow_id)
            raw = read_json(path)
            record = _record_of(raw, pristine=True)
        except Exception:           # noqa: BLE001 - missing, a row, a bad id
            return False
        if record.id != flow_id or record.readonly:
            return False
        self._edit_raw(path, raw, update)
        return True

    def _stored(self, flow_id: str) -> FlowRecord | None:
        """The flow a save of ``flow_id`` replaces, as this build reads it
        (migrated, as the compile that keyed its ids read it), or None. The
        save's anchors come from it, and so does its bookkeeping
        (``_bookkeeping``, #364): the save reads the file once.

        NONE ONLY WHEN NOTHING WAS KEYED (#350):

        * no file (``FileNotFoundError``): a new flow;
        * a file that reads but does not parse or validate: the library
          lists it as an unreadable row (``_entries`` makes the same
          judgment with the same ``_record_of``), nothing can open or run
          it, so it keyed nothing, and saving over it is its repair;
        * a record that carries another id, as a copied file does: its nodes
          are another flow's and lend this one no anchor (#353 item 5).

        EVERY OTHER FAILURE REFUSES THE SAVE. An ``OSError`` that is not
        "no such file", or ``read_json`` failing to secure the file
        (``PrivatePermissionsError``), says nothing about what the file
        holds; answered None, as this used to answer every exception, the
        save anchored every TARGET where it is drawn now and restarted
        banked counts with nothing listed (#153's family: an error path that
        returns the "nothing there" answer). ``StoredFlowUnreadable`` names
        the error's type and never its text, which is the file's full path.

        A FILE A NEWER BUILD WROTE REFUSES HERE TOO (``NewerSchemaFlow``).
        ``_newer_schema_on_disk`` asks first, but reads an ``OSError`` as
        "not newer", so when its read fails and this one succeeds the newer
        file arrives here, and must not be read as a damaged one and
        overwritten."""
        try:
            raw = read_json(self._path(flow_id))
        except FileNotFoundError:
            return None
        except OSError as e:
            raise self._unreadable(e) from e
        except (ValueError, RecursionError):
            return None             # read, but not JSON or not UTF-8
        except Exception as e:      # noqa: BLE001 - e.g. PrivatePermissionsError
            raise self._unreadable(e) from e
        try:
            record = _record_of(raw)
        except FutureFlowSchema as e:
            raise NewerSchemaFlow(_newer_sentence(e.version)) from None
        except Exception:           # noqa: BLE001 - a row: _entries' judgment
            return None
        return record if record.id == flow_id else None

    @staticmethod
    def _unreadable(e: BaseException) -> StoredFlowUnreadable:
        """The refusal for a read that failed with ``e``: its type, never
        its text (an OSError's text is the file's full path, and the answer
        reaches any operator)."""
        return StoredFlowUnreadable(
            f"the flow on disk could not be read ({type(e).__name__}), and "
            f"a save measures each TARGET's framing against it to keep its "
            f"counts; nothing was written, so save again")

    def save(self, record: FlowRecord) -> FlowRecord:
        """Store ``record`` and return it as stored. See ``save_and_report``,
        which this is with the report left out."""
        return self.save_and_report(record)[0]

    def save_and_report(self, record: FlowRecord
                        ) -> tuple[FlowRecord, list[str], list[dict]]:
        """Store ``record``: ``(record_as_stored, migrated, reanchored)``.

        The save rules run here, in the one writer (``save_rules``, rulings 2
        and 3): ``counts`` becomes "Accepted subs" on every TARGET and POOL,
        and every TARGET's ``frameAnchor`` is decided against the file this
        save replaces, never taken from ``record``. ``migrated`` and
        ``reanchored`` are ``prepare_save``'s, for the save's answer: the
        route says "now counts accepted subs only" for the first and names
        each block whose counts restart for the second.

        After the refusals and the graph's validation, so a refused save
        costs no catalogue lookup and says nothing it did not do. A file it
        replaces that cannot be read refuses the save too, from ``_stored``
        (``StoredFlowUnreadable``, 409 through ``_persist_flow``, #350).

        THE BOOKKEEPING IS THE FILE'S, NOT ``record``'s (``BOOKKEEPING``,
        #364): ``created_ts``, ``last_run`` and ``last_result`` come from the
        record that same ``_stored`` read returns, whatever ``record``
        carries, so a save cannot forge a run and a read that fails cannot
        reset one."""
        if record.readonly or any(e.id == record.id for e in examples()):
            raise ReadOnlyFlow("the shipped examples are read-only — "
                               "duplicate one into My flows to edit it")
        # A FILE A NEWER BUILD WROTE IS NEVER OVERWRITTEN (#153). It is listed
        # but cannot be opened here, so any save aimed at its id comes from
        # somewhere else -- a stale tab, a duplicated id -- and writing it
        # would silently downgrade whatever the newer build meant.
        newer = self._newer_schema_on_disk(record.id)
        if newer is not None:
            raise NewerSchemaFlow(_newer_sentence(newer))
        # THE QUOTA FROM THE FILE NAMES (#433), as PlanLibrary answers its
        # own: is the file at this id there, and how many files does the
        # directory list. It was ``{r.id for r in self._on_disk()}``, a walk
        # that parsed, migrated and validated every stored flow on every
        # save to count them (#433 measured 0.08 s a save over the first 48
        # into an empty library, 0.75 s a save averaged over 768). The only
        # file a save reads is now the one it writes, above and in
        # ``_stored``. Files, not readable records: an unreadable file takes
        # the disk and the listing's time like any other, and a save over
        # one at its own id is the upsert it is, so a full library can still
        # have a damaged flow repaired.
        if (not self._path(record.id).exists()
                and len(list_json(self.dir)) >= MAX_FLOWS):
            raise FlowLibraryFull(f"the flow library is full ({MAX_FLOWS})")
        errors = record.graph.validation_errors()
        if errors:
            raise ValueError("; ".join(errors))
        # ONE READ OF THE FILE THIS SAVE REPLACES, for the anchors and the
        # bookkeeping alike (#350, #364): it refuses, or it answers both.
        prior = self._stored(record.id)
        record, migrated, reanchored = prepare_save(
            record, prior, resolve=self.resolve)
        ensure_dir(self.dir)
        now = time.time()
        # `migrated` is cleared on the RETURNED record too, so the response to a
        # save never echoes a note, the client's or a stale read's.
        record = record.model_copy(update={"updated_ts": now, "migrated": [],
                                           **_bookkeeping(prior, now)})
        self._write(record)
        return record, migrated, reanchored

    def delete(self, flow_id: str) -> bool:
        """Remove the file at ``flow_id``. False when there is none.

        AN EXAMPLE'S ID REMOVES THE FILE THAT SHADOWS IT, when there is one
        (carry-over 7, #153). A file on disk takes its id from the Examples,
        readable or not, so a damaged ``example-m16.json`` is listed as a row
        and hides the example; ``save`` refuses an example's id, so deleting
        the file is the only way back, and the example, being code, returns.
        With no file the id IS the shipped example, and that is refused."""
        path = self._path(flow_id)
        if path.exists():
            path.unlink()
            return True
        if any(e.id == flow_id for e in examples()):
            raise ReadOnlyFlow("the shipped examples cannot be deleted")
        return False

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
        a file this build cannot open is a row, not a record, so it stays put.

        A RAW EDIT: ``folder``, nothing else, and each file keeps its
        ``schema_version`` (carry-over 1, ``_edit_raw``). Tidying a folder
        must not re-mean the flows in it.

        A FOLDER IS NOT A VERSION (#512): ``updated_ts`` is left alone, as
        ``touch_run`` leaves it for a run. The replay notice reads that field
        as "a new version of this flow was saved" (``replay_facts``,
        ``_freeze_saved_version``, spec 5.9), and this wrote it, so after a
        folder tidy-up every armed flow in the folder told the operator to
        press CONTINUE to apply edits nobody made. The library's order by
        ``updated_ts`` stays put with it: a move is not an edit there either.
        """
        if old == EXAMPLES_FOLDER or new == EXAMPLES_FOLDER:
            raise ReadOnlyFlow("the Examples folder is fixed")
        moved = 0
        for path, raw, record, _reason in self._entries(pristine=True):
            if record is not None and record.folder == old:
                self._edit_raw(path, raw, {"folder": new})
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
