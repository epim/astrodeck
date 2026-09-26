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
* ``SequenceGroupState`` has no model yet (the engine publishes it later in
  S2), so it is compared with the shape spec 5.10 gives ``state.group``, plus
  the ``meridian_wait`` flag. When ``_set_state`` publishes ``group``, that is
  the source to compare with instead.

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

from astrodeck.sequence.models import SequencePlan, Target, TargetGroup
from astrodeck.sequence.session import Session

_ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = _ROOT / "ui" / "src" / "types.ts"
SPEC = (_ROOT / "docs" / "superpowers" / "specs"
        / "2026-09-23-flows-mosaic-target-block-design.md")

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

def _spec_group_shape() -> tuple[set[str], set[str]]:
    """The top-level names of 5.10's ``group = {...}`` and the names inside
    its ``set_aside: [{...}]``, read from the spec itself."""
    text = SPEC.read_text(encoding="utf-8")
    m = re.search(r"gains `group = (\{.*?\})`", text)
    assert m, "spec 5.10 no longer spells `group = {...}`"
    body = m.group(1)[1:-1]
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"{": 1, "[": 1, "}": -1, "]": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    names = {p.split(":", 1)[0].strip() for p in parts}
    inner = next(p for p in parts if p.strip().startswith("set_aside"))
    inner_names = {n.strip() for n in
                   re.search(r"\{(.*)\}", inner).group(1).split(",")}
    return names, inner_names


@pytest.mark.skipif(not SPEC.exists(), reason="docs/ not present")
def test_the_group_state_type_has_the_5_10_shape():
    """5.10's fields, plus ``meridian_wait``: the flag that tells a viewer's
    screen why ``panel`` and ``pass`` are withheld (6.9), so both of those are
    optional. ``mode`` spells the group's modes.

    RED under mutant "drop meridian_wait from types.ts":

        E   AssertionError: assert {'id', 'mode'...s_total', ...} ==
            {'id', 'merid...ls_done', ...}
        E     Extra items in the right set:
        E     'meridian_wait'

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
    names, inner = _spec_group_shape()
    ts = _interface("SequenceGroupState")
    assert set(ts) == names | {"meridian_wait"}
    assert set(_members(ts["set_aside"].strip()[1:-3])) == inner
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
