"""``ui/src/types.ts`` mirrors the S2 group data model (#189 S2 T1; spec 3.4,
3.7, 5.10, Revision 2 ruling 9).

The UI hand-copies the server's pydantic models, and a comment saying "keep in
sync" is not a mechanism: S1 added three Target fields and the TS ``Target``
never gained them. This file parses ``types.ts`` and compares field names
with the models, so the copy that drifts is the one that goes red.

What is compared, and against what:

* ``TargetGroup`` whole: the TS field set equals the model's, every Literal
  union spells the model's values, and every field the model lets be None
  admits ``null`` in TS (the server sends an explicit null).
* ``Target``, ``SequencePlan`` and ``Session`` are older, partial mirrors, so
  for them the S1 and S2 fields must be present, and no TS field may name a
  field the model does not have.
* The set-aside record and the locked angle are plain dicts on ``Session``,
  so their TS types are compared with the keys the ``Session`` helpers
  actually write.
* ``SequenceGroupState`` has no model: it is a dict `_group_state` builds and
  `_set_state` publishes. So it is compared with a real ``state.group``,
  captured from the bus while the fixture mosaic runs on the clocked harness
  (tests/_group_harness.py), in both directions, its set-aside record too,
  and each published value against the TS type it is declared with (#318).
  Until #318 it was compared with the spec text's ``group = {...}``, and
  test_group_rotation.py kept a second hand-written copy of the keys, so the
  engine, the spec and the mirror were tied only through two copies. Now the
  engine is the one source: this file ties the mirror to what the engine
  publishes, and test_mosaic_spec_claims.py's 5.10 case ties the top-level
  keys of the spec's ``group = {...}`` to the dict `_group_state` builds.
  That case strips the spec's ``set_aside: [{panel, reason}]`` before it
  compares the top-level keys, and since S3-SPEC5 it checks the record's own
  keys separately. The capture night always has a current panel, so a
  second night, one that waits before its first visit, grades the ``panel:
  null`` a group publishes with no current member (the integration of S3).

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed in a private copy of the tree (a copy
of ``server/`` beside a copy of ``ui/src/types.ts``), from a byte-for-byte
backup of the file under test, with the copy's sha256 compared against the
backup afterwards.
"""
from __future__ import annotations

import re
import types as pytypes
import typing
from pathlib import Path

import pytest

from _group_harness import (GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.sequence.models import (Instruction, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session

_ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = _ROOT / "ui" / "src" / "types.ts"

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")

#: The Target fields S1 and S2 added, which the TS mirror must carry.
TARGET_NEW = ("center_tolerance_arcmin", "center_attempts",
              "autofocus_skip_if_fresh", "panel_row", "panel_col",
              "after_group")


# ------------------------------------------------------------ the parser

def _strip_comments(src: str) -> str:
    """``src`` with every comment replaced by spaces of the same length, so
    positions still line up. String literals are skipped whole, so a ``//``
    inside a quoted type is not read as a comment."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in src[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


def _mask_strings(src: str) -> str:
    """The contents of every string literal replaced by ``_``, the quotes
    kept: brace counting and the field scan then never see a character that
    lives inside a quoted type."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 1
            out[i + 1:j] = "_" * (j - i - 1)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _top_level(masked: str) -> str:
    """``masked`` with everything nested inside ``{}``, ``[]`` or ``()``
    blanked, so only the members of the outermost body remain readable."""
    out, depth = [], 0
    for ch in masked:
        if ch in "{[(":
            depth += 1
            out.append(" ")
        elif ch in "}])":
            depth -= 1
            out.append(" ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


_MEMBER = re.compile(r"(?:^|;)\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)\??\s*:",
                     re.M)


def _members(body: str) -> dict[str, str]:
    """Top-level members of an interface or object-type body: name -> type
    text as written (comments removed)."""
    text = _strip_comments(body)
    flat = _top_level(_mask_strings(text))
    out: dict[str, str] = {}
    for m in _MEMBER.finditer(flat):
        end = flat.find(";", m.end())
        out[m.group(1)] = text[m.end():len(text) if end < 0 else end].strip()
    return out


def _interface_body(name: str, src: str | None = None) -> str:
    """The text between the braces of ``export interface <name> {...}`` in
    ``types.ts``, comments included, and nothing after its closing brace."""
    src = TYPES_TS.read_text(encoding="utf-8") if src is None else src
    masked = _mask_strings(_strip_comments(src))
    m = re.search(rf"\bexport interface {re.escape(name)}\b[^{{]*\{{", masked)
    assert m, f"types.ts has no 'export interface {name}'"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(masked[i], 0)
        i += 1
    return src[m.end():i - 1]


def _interface(name: str, src: str | None = None) -> dict[str, str]:
    """The members of ``export interface <name> {...}`` in ``types.ts``."""
    return _members(_interface_body(name, src))


_OPTIONAL = re.compile(r"(?:^|;)\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)\?\s*:",
                       re.M)


def _optional(name: str, src: str | None = None) -> set[str]:
    """The top-level members of the interface written ``name?:``."""
    flat = _top_level(_mask_strings(_strip_comments(
        _interface_body(name, src))))
    return set(_OPTIONAL.findall(flat))


def _quoted(type_text: str) -> set[str]:
    return set(re.findall(r'"([^"]*)"', type_text))


def _admits_none(annotation) -> bool:
    return type(None) in typing.get_args(annotation) or (
        isinstance(annotation, pytypes.UnionType)
        and type(None) in annotation.__args__)


def _literals(annotation) -> set[str]:
    if typing.get_origin(annotation) is typing.Literal:
        return set(typing.get_args(annotation))
    return set()


# --------------------------------------------------- guard the guard

#: Each trap is one a broken parser falls into: a member named after a ``;``
#: inside a line comment, a member at the start of a line inside a block
#: comment, a ``;`` and a ``:`` inside a quoted type, a ``//`` inside a quoted
#: type, and the members of a nested object.
GUARD_SRC = '''
export interface Probe {
  // an old field; ghost: number
  /*
   phantom: string;
  */
  plain: string;
  optional?: number | null;   // trailing: comment
  nested: { inner: string; deeper: { deepest: number } }[];
  quoted: "a; b: c" | "d // e";
  multi:
    | "x"
    | { kind: "y" };
  readonly frozen: boolean;
  fn: (arg: number) => void;
}
'''


def test_the_parser_reads_members_and_nothing_else():
    """If the parser swept up names from comments, strings or nested objects,
    or silently returned {}, every comparison below could pass however far
    the two sides drifted.

    RED under mutant "comments not stripped" (``_strip_comments`` returns its
    input):

        E   AssertionError: assert {'fn', 'froze...ptional', ...} ==
            {'fn', 'froze... 'plain', ...}
        E     Extra items in the left set:
        E     'ghost'
        E     'phantom'

    RED under mutant "strings not masked" (``_mask_strings`` returns its
    input):

        E   AssertionError: assert {'b', 'fn', '...ptional', ...} ==
            {'fn', 'froze... 'plain', ...}
        E     Extra items in the left set:
        E     'b'

    RED under mutant "nested not blanked" (``_top_level`` returns its input):

        E   AssertionError: assert {'deeper', 'f...ptional', ...} ==
            {'fn', 'froze... 'plain', ...}
        E     Extra items in the left set:
        E     'deeper'
    """
    got = _interface("Probe", GUARD_SRC)
    assert set(got) == {"plain", "optional", "nested", "quoted", "multi",
                        "frozen", "fn"}
    assert _quoted(got["quoted"]) == {"a; b: c", "d // e"}
    assert "null" in got["optional"]
    assert set(_members(got["nested"].strip()[1:-3])) == {"inner", "deeper"}


#: The traps for ``_optional``: an optional member in a comment, one inside a
#: nested object, and, in the NEXT interface, an optional member that shares
#: a required member's name. Reading past the closing brace falls into that
#: last one, which is how the first version of the group-state check read.
OPTIONAL_SRC = '''
export interface Probe {
  /*
   gone?: number;
  */
  kept?: string;
  needed: number;
  nested: { first: string; inner?: string }[];
}
export interface After {
  needed?: number;
}
'''


def test_the_optional_reader_reads_one_interface_and_its_top_level():
    """``_optional`` decides that ``panel`` and ``pass`` are withheld and
    nothing else is, so its misreadings are guarded like the member parser's.

    RED under mutant "optional reads to the end of the file"
    (``_interface_body`` returns ``src[m.end():]``):

        E   AssertionError: assert {'kept', 'needed'} == {'kept'}
        E     Extra items in the left set:
        E     'needed'

    RED under mutant "optional reads nested members" (``_optional`` without
    ``_top_level``):

        E   AssertionError: assert {'inner', 'kept'} == {'kept'}
        E     Extra items in the left set:
        E     'inner'

    RED under mutant "optional reads comments" (``_optional`` without
    ``_strip_comments``):

        E   AssertionError: assert {'gone', 'kept'} == {'kept'}
        E     Extra items in the left set:
        E     'gone'
    """
    assert _optional("Probe", OPTIONAL_SRC) == {"kept"}
    assert _optional("After", OPTIONAL_SRC) == {"needed"}


def test_the_parser_reads_the_real_file():
    """The real file: fields every version of ``Target`` has had, and in
    ``RigStatus`` the nested ``focuser`` object is one member whose own
    fields stay out of the top level.

    RED under mutant "nested not blanked":

        E   AssertionError: assert not ({'max', 'position', 'temperature'} &
            {'active', 'backend_links', 'bahtinov_active', 'basis',
            'bayer_pattern', 'boot_connect_failed', ...})
    """
    target = _interface("Target")
    assert {"name", "ra_hours", "dec_deg", "steps", "mosaic_group"} <= set(
        target)
    rig = _interface("RigStatus")
    assert "focuser" in rig and "looping" in rig
    assert not {"position", "max", "temperature"} & set(rig)


# ------------------------------------------------------------ TargetGroup

def test_the_ts_target_group_has_the_model_fields():
    """RED before the TS type existed:

        E   AssertionError: types.ts has no 'export interface TargetGroup'

    RED under mutant "rename a TS field" (``visit_min_s`` spelled
    ``visit_min`` in types.ts):

        E   AssertionError: types.ts TargetGroup drifted: missing
            ['visit_min_s'], extra ['visit_min']
    """
    ts = set(_interface("TargetGroup"))
    model = set(TargetGroup.model_fields)
    assert ts == model, (f"types.ts TargetGroup drifted: missing "
                         f"{sorted(model - ts)}, extra {sorted(ts - model)}")


def test_the_ts_target_group_spells_every_choice_and_every_null():
    """A Literal is a closed set on both sides, and a field the server may
    send as null must say so in TS, or the UI reads a number that is not
    there.

    RED under mutant "pa_deg not nullable in types.ts" (``pa_deg: number``):

        E   AssertionError: pa_deg may be None on the server and types.ts
            says 'number'
        E   assert 'null' in 'number'
    """
    ts = _interface("TargetGroup")
    for name, field in TargetGroup.model_fields.items():
        if _literals(field.annotation):
            assert _quoted(ts[name]) == _literals(field.annotation), name
        if _admits_none(field.annotation):
            assert "null" in ts[name], (
                f"{name} may be None on the server and types.ts says "
                f"{ts[name]!r}")


# ----------------------------------------- Target, SequencePlan, Session

@pytest.mark.parametrize("ts_name, model, must", [
    pytest.param("Target", Target, TARGET_NEW, id="Target"),
    pytest.param("SequencePlan", SequencePlan, ("groups",), id="SequencePlan"),
    pytest.param("Session", Session, ("set_aside", "locked_angles"),
                 id="Session"),
])
def test_the_older_mirrors_carry_the_new_fields(ts_name, model, must):
    """The S1 and S2 fields are present, and nothing in TS names a field the
    model does not have (a misspelt mirror reads undefined forever).

    RED under mutant "delete groups from types.ts" (the ``groups?:`` line
    removed from ``SequencePlan``), on ``SequencePlan`` only:

        E   AssertionError: types.ts SequencePlan lacks ['groups']
        E   assert not ['groups']

    RED against the TS ``Target`` as S1 left it (never mirrored), on
    ``Target``:

        E   AssertionError: types.ts Target lacks ['after_group',
            'autofocus_skip_if_fresh', 'center_attempts',
            'center_tolerance_arcmin', 'panel_col', 'panel_row']
    """
    ts = _interface(ts_name)
    lacks = sorted(set(must) - set(ts))
    assert not lacks, f"types.ts {ts_name} lacks {lacks}"
    invented = sorted(set(ts) - set(model.model_fields))
    assert not invented, (
        f"types.ts {ts_name} names {invented}, which the model does not have")
    for name in must:
        if _admits_none(model.model_fields[name].annotation):
            assert "null" in ts[name], (ts_name, name, ts[name])


def test_the_new_fields_are_typed_as_their_records():
    """``groups`` is a list of the TS ``TargetGroup``, and the two session
    fields are typed as the record interfaces the next test pins.

    RED under mutant "delete groups from types.ts":

        E   KeyError: 'groups'

    RED under mutant "locked angles as a bare record" (``locked_angles?:
    Record<string, unknown>;`` in types.ts):

        E   AssertionError: assert 'Record<string, unknown>' ==
            'Record<string, LockedAngle>'
    """
    assert _interface("SequencePlan")["groups"] == "TargetGroup[]"
    session = _interface("Session")
    assert session["set_aside"] == "SetAsideRecord[]"
    assert session["locked_angles"] == "Record<string, LockedAngle>"


# ------------------------------------------------ the session records

def test_the_record_types_are_the_keys_the_helpers_write():
    """Compared with what ``note_set_aside`` and ``lock_angle`` actually put
    in the session, not with a list copied here, so a helper that changes
    its record changes what this test demands.

    RED under mutant "exposed_at not nullable in types.ts"
    (``exposed_at: number``):

        E   AssertionError: assert 'null' in 'number'
    """
    s = Session()
    s.note_set_aside("t1", "floor", night="2026-09-24")
    s.lock_angle("t1", 12.5, solved_at=1.0, exposed_at=None, source="solve")
    record = s.set_aside[0]
    lock = s.locked_angle("t1")

    set_aside = _interface("SetAsideRecord")
    assert set(set_aside) == set(record)
    assert "null" in set_aside["step_id"]      # a whole panel has no step
    locked = _interface("LockedAngle")
    assert set(locked) == set(lock)
    assert "null" in locked["exposed_at"]


# ------------------------------------------------ the published state

def _capture_plan() -> SequencePlan:
    """The fixture 2x2 of L and R, one frame a filter, with a
    ``skip_target`` rule that drops panel 2-1 at the night's first frame
    (the harness reports an HFR of 2.0, over the rule's 1.0). Every publish
    after it carries a ``set_aside`` record, so the record's own keys are
    graded as well as the state's."""
    return grid_plan(panel_kw={"count": 1}, instructions=[Instruction(
        id="skip-rule", trigger="on_hfr_above", threshold=1.0, once=True,
        action="skip_target", target_arg=f"{GROUP_NAME} 2-1")])


async def _published_group_states(hub, monkeypatch) -> list[dict]:
    """Every ``state.group`` the engine published over one clocked night of
    the capture plan, taken from the bus as it fanned each publish out: the
    payload `_set_state` hands `bus.publish`, built by `_group_state`.

    GET /api/sequence/state, the monitor snapshot and the WS lane serve that
    dict whole to a principal who may see the site.
    `api.redact._withhold_group_timing` takes ``panel`` and ``pass`` out
    only across a meridian wait, which is why types.ts marks those two
    optional; this night has no meridian wait, so nothing is taken out."""
    night = Night(hub, monkeypatch)
    try:
        done = await night.run(_capture_plan())
    finally:
        await night.close()
    assert done, f"premise: the capture night ended: {night.trace[-3:]}"
    return [e["data"]["group"] for e in night.events
            if e["type"] == "sequence"
            and isinstance(e["data"].get("group"), dict)]


def _ts_admits(value, ts_type: str) -> bool:
    """Does the TS type text ``ts_type`` admit ``value`` as JSON carries it?
    A string passes ``string`` or a quoted choice that spells it, a list an
    array type and a dict an object type; None needs ``null``."""
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _mask_strings(ts_type)))
    if value is None:
        return "null" in words
    if isinstance(value, bool):
        return "boolean" in words
    if isinstance(value, (int, float)):
        return "number" in words
    if isinstance(value, str):
        return "string" in words or value in _quoted(ts_type)
    if isinstance(value, list):
        return ts_type.strip().endswith("[]")
    if isinstance(value, dict):
        return ts_type.strip().startswith("{")
    return False


def _drift(what: str, published: set[str], declared: set[str]) -> str:
    return (f"{what} drifted: the engine publishes "
            f"{sorted(published - declared)}, which types.ts does not "
            f"declare, and types.ts declares {sorted(declared - published)}, "
            f"which the engine never publishes")


def test_the_type_check_reads_json_as_ts_types_it():
    """`_ts_admits` decides whether a published value fits its TS type, so
    it is guarded like the parser: if it admitted everything, the value
    check below could not fail. A bool is not a number (Python says it is),
    a quoted choice admits only what it spells, and null needs ``null``.

    RED under mutant "a bool reads as a number" (the ``bool`` arm of
    `_ts_admits` deleted, so True falls to the ``(int, float)`` arm; the
    capture test below goes red with it, on ``meridian_wait``):

        E   AssertionError: assert (False)
        E    +  where False = _ts_admits(True, 'boolean')

    RED under mutant "any string passes a quoted choice" (the str arm
    answering True):

        E   assert not True
        E    +  where True = _ts_admits('mosaic', '"rotate" | "sequential"')
    """
    assert _ts_admits(True, "boolean") and not _ts_admits(True, "number")
    assert _ts_admits(3, "number") and not _ts_admits(3, "string")
    assert _ts_admits("rotate", '"rotate" | "sequential"')
    assert not _ts_admits("mosaic", '"rotate" | "sequential"')
    assert _ts_admits(None, "string | null") and not _ts_admits(None, "string")
    assert _ts_admits([], "{ panel: string }[]")
    assert not _ts_admits([], "string")
    assert _ts_admits({}, "{ panel: string }") and not _ts_admits({}, "number")


async def test_the_group_state_type_is_what_the_engine_publishes(
        group_hub, monkeypatch):
    """Every ``state.group`` the engine published on a real night of the
    fixture mosaic has exactly the keys of types.ts's
    ``SequenceGroupState``, and every set-aside record in it exactly the
    keys of its ``set_aside`` element, both ways round: a key the engine
    adds that the mirror lacks is data no screen can read, and a key the
    mirror declares that the engine never sends reads undefined for ever.
    Each published value is also one its TS type admits (#318).

    RED under the engine mutant "add a key to _group_state" (``"visits_owed":
    0`` added to the dict `_group_state` returns, in a scratch copy of
    server/):

        E   AssertionError: SequenceGroupState drifted: the engine publishes
            ['visits_owed'], which types.ts does not declare, and types.ts
            declares [], which the engine never publishes

    RED under the UI mutant "drop a key from the TS interface" (the
    ``visit_elapsed_s: number;`` line deleted from ``SequenceGroupState``,
    in a scratch copy of ui/src/types.ts):

        E   AssertionError: SequenceGroupState drifted: the engine publishes
            ['visit_elapsed_s'], which types.ts does not declare, and types.ts
            declares [], which the engine never publishes

    The other way round, RED under "drop a key from _group_state" (its
    ``"visit_elapsed_s"`` entry deleted):

        E   AssertionError: SequenceGroupState drifted: the engine publishes
            [], which types.ts does not declare, and types.ts declares
            ['visit_elapsed_s'], which the engine never publishes

    and under "add a key to the TS interface" (``eta_s: number;`` added to
    ``SequenceGroupState``):

        E   AssertionError: SequenceGroupState drifted: the engine publishes
            [], which types.ts does not declare, and types.ts declares
            ['eta_s'], which the engine never publishes

    The set-aside record, RED under "a set-aside record gains a key"
    (``"since": 0`` added to each record `_group_state` builds):

        E   AssertionError: SequenceGroupState.set_aside[] drifted: the engine
            publishes ['since'], which types.ts does not declare, and types.ts
            declares [], which the engine never publishes

    and under "the TS set-aside record loses reason" (``set_aside: { panel:
    string }[];``):

        E   AssertionError: SequenceGroupState.set_aside[] drifted: the engine
            publishes ['reason'], which types.ts does not declare, and
            types.ts declares [], which the engine never publishes

    The values, RED under "visit_elapsed_s typed string in types.ts":

        E   AssertionError: types.ts types these otherwise:
            {'visit_elapsed_s': (0, 'string')}

    and under the engine mutant "meridian_wait published as the wait's
    phase" (``self._meridian_wait.get(gid)``, None while there is no wait,
    for ``gid in self._meridian_wait``):

        E   AssertionError: types.ts types these otherwise: {'meridian_wait':
            (None, 'boolean')}

    The premise, RED under "the capture reads no publishes" (the capture
    keeping ``e["type"] == "state"``, a type the bus never fans out):

        E   AssertionError: premise: 0 group publishes carrying 0 set-aside
            records
    """
    published = await _published_group_states(group_hub, monkeypatch)
    records = [r for g in published for r in g["set_aside"]]
    assert published and records, (
        f"premise: {len(published)} group publishes carrying "
        f"{len(records)} set-aside records")
    ts = _interface("SequenceGroupState")
    ts_record = _members(ts["set_aside"].strip()[1:-3])
    for g in published:
        assert set(g) == set(ts), _drift("SequenceGroupState", set(g), set(ts))
        wrong = {k: (g[k], ts[k]) for k in g if not _ts_admits(g[k], ts[k])}
        assert not wrong, f"types.ts types these otherwise: {wrong}"
    for r in records:
        assert set(r) == set(ts_record), _drift(
            "SequenceGroupState.set_aside[]", set(r), set(ts_record))
        wrong = {k: (r[k], ts_record[k]) for k in r
                 if not _ts_admits(r[k], ts_record[k])}
        assert not wrong, f"types.ts types these otherwise: {wrong}"


async def test_a_group_waiting_before_its_first_panel_types_what_it_publishes(
        group_store, group_hub, monkeypatch):
    """The capture night above always has a current panel, so it never
    reaches `_group_state`'s other arm: with no member current (a group
    entered through a wait, here a one-panel mosaic behind a horizon mask it
    clears 15 min into the night, test_group_reach's night) the engine
    publishes ``panel: None``. types.ts typed it ``panel?: string``, which a
    screen reading it would take for a label; since the integration of S3
    it is ``string | null`` (#318's residual, found by S3-R318's verifier,
    who ran this night through `_ts_admits` and saw 15 of 25 publishes red).

    RED under the engine mutant "the no-current-member arm publishes panel
    as a number" (``else None`` made ``else 12345`` in `_group_state`, in a
    private scratch copy of server/), which the capture test above cannot
    see (the file's other 14 tests passed under it), observed:

        E   AssertionError: types.ts types these otherwise: {'panel':
            (12345, 'string | null')}

    RED under the UI mutant "panel typed string" (``panel?: string;`` put
    back in a scratch copy of ui/src/types.ts), observed:

        E   AssertionError: types.ts types these otherwise: {'panel':
            (None, 'string')}
    """
    from _group_harness import LAT, LON, T0, ra_at
    from astrodeck.catalog.coords import altaz
    from astrodeck.config import SafetyConfig
    ra, dec = ra_at(-2.0), 40.0
    clear_at = T0 + 900.0
    mask, _az = altaz(ra, dec, LAT, LON, clear_at)
    assert altaz(ra, dec, LAT, LON, T0)[0] < mask, (
        "premise: the panel starts behind the mask")
    group_store.set_safety(SafetyConfig(enabled=False,
                                        horizon=[(0.0, mask), (360.0, mask)]))
    night = Night(group_hub, monkeypatch)
    try:
        done = await night.run(grid_plan(rows=1, cols=1,
                                         panel_kw={"count": 2}))
    finally:
        await night.close()
    assert done, f"premise: the mask night ended: {night.trace[-3:]}"
    published = [e["data"]["group"] for e in night.events
                 if e["type"] == "sequence"
                 and isinstance(e["data"].get("group"), dict)]
    # The premise asks for publishes with no panel LABEL, not for None: a
    # premise that looked for None would fail first under the mutant below,
    # and the type check would then grade nothing.
    waiting = [g for g in published if not isinstance(g["panel"], str)]
    assert waiting and len(waiting) < len(published), (
        f"premise: {len(waiting)} of {len(published)} publishes with no "
        f"current panel; the night must wait and then shoot")
    ts = _interface("SequenceGroupState")
    for g in published:
        assert set(g) == set(ts), _drift("SequenceGroupState", set(g), set(ts))
        wrong = {k: (g[k], ts[k]) for k in g if not _ts_admits(g[k], ts[k])}
        assert not wrong, f"types.ts types these otherwise: {wrong}"


def test_the_group_state_type_withholds_panel_and_pass_and_nothing_else():
    """``panel`` and ``pass`` are optional, because they are withheld from a
    principal without the site-derived view across a meridian wait (6.9),
    and nothing else is. ``meridian_wait``, the flag that tells a viewer's
    screen why they are missing, is a plain required boolean, and ``mode``
    spells the group's modes. (Which keys there are is the capture test's.)

    RED under mutant "mode loses sequential in types.ts" (``mode:
    "rotate";`` in ``SequenceGroupState``):

        E   AssertionError: assert {'rotate'} == {'rotate', 'sequential'}

    RED under mutant "drop meridian_wait from types.ts" (its line deleted;
    the capture test above goes red with it):

        E   KeyError: 'meridian_wait'

    RED under mutant "panel required, a later interface declares panel?"
    (``panel: string`` in ``SequenceGroupState``, and an interface after it
    carrying ``panel?`` and ``pass?``). The first version of this check read
    the rest of the file after the interface's name, found the later
    interface's ``panel?``, and let this mutant through (11 passed); it now
    reads the interface's own top level:

        E   AssertionError: panel and pass must be optional (withheld), and
            nothing else
        E   assert {'pass'} == {'panel', 'pass'}
        E     Extra items in the right set:
        E     'panel'

    RED under mutant "meridian_wait optional" (``meridian_wait?: boolean``: a
    screen reading it undefined would take a withheld panel for a stale one):

        E   AssertionError: panel and pass must be optional (withheld), and
            nothing else
        E   assert {'meridian_wa...anel', 'pass'} == {'panel', 'pass'}
        E     Extra items in the left set:
        E     'meridian_wait'
    """
    ts = _interface("SequenceGroupState")
    assert _quoted(ts["mode"]) == _literals(
        TargetGroup.model_fields["mode"].annotation)
    assert ts["meridian_wait"] == "boolean"
    assert _optional("SequenceGroupState") == {"panel", "pass"}, (
        "panel and pass must be optional (withheld), and nothing else")


def test_the_sequence_state_carries_the_group():
    """RED under mutant "no group on SequenceState" (the ``group?:`` line
    removed):

        E   KeyError: 'group'
    """
    assert _interface("SequenceState")["group"] == "SequenceGroupState"
