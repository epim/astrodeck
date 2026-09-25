"""The mosaic spec says what S1 and its hardening round built (#189 A10, A11).

``docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md`` is
what every later slice is planned from, and S1 moved under it. The card chip
it promised pointed at a "section 3.8" that was never written. The step id it
gave was the spelling S1's hardening round replaced. CONTINUE picked its
session by a rule the code no longer uses. And the rulings made while
hardening S1 lived only in code comments. A spec that disagrees with the code
is a claim nothing keeps, and the next agent to plan from it "fixes" the code
back.

So each test below holds one edited claim to the code it describes, and takes
the claim's numbers and spellings FROM that code wherever it can: the step
spelling from ``identity.step_signature``, the name-key prefix from
``identity``, the canonical identity of a name from ``tonight.resolve_target``,
the ADOPT bound from ``continuation``, the retry clock and the
hop seed from the engine, the route from its decorator in ``api/app.py``, and
the chip's keys from a real ``flow_progress`` answer. When either side moves,
the test goes red and says which side to fix.

The second hardening round (H2) added Revision 4 and five orchestrator
rulings, and the tests at the end hold those: the cloud hold's re-point, its
latch, the refusals it does not pre-ask (#240), its target-less wait and its
published detail (5.8, #221, #224, #228), the flips-off hold (5.8), the
idle stop's first attempt and the watched cooling wait (6.17), the recovery
ladder's stop (5.9, 6.15, #220), the rulings in the owner list, and
Revision 4's table against the sections that carry H2's edits. Their code
halves are read from the syntax tree where a docstring could otherwise
answer for the code, since H2's docstrings name the very calls the claims
are about.

WHAT THIS CANNOT DO. It reads words, so it proves the spec SAYS a thing, not
that the thing holds everywhere the spec implies. Where a claim describes
code, the code half is asserted too, as the shape of that code (a call made,
a predicate asked, a flag read); whether the code then behaves is its own
suite's question (the cloud hold's is test_cloud_hold_watch.py).

Every test names the mutation of the spec it guards and quotes the failure
that mutation produced, each run from a byte backup of the spec and the spec
checked byte-identical afterwards. Where a claim takes a value from code, a
code mutant is quoted as well: that module global, or that engine method,
rebound inside the test process only, never an edit of the source file. The
control shows that every section a test reads is really there, so an
assertion that a phrase is ABSENT cannot pass on an empty section; and an
edit to the spec that no claim covers (section 7's engine heading) left every
test green.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrodeck.config as config_mod
from astrodeck.flows import continuation, identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence import engine as engine_mod
from astrodeck.sequence import resume_arm as resume_arm_mod
from astrodeck.sequence import session as session_mod
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import SessionStore

# server/tests/<this> -> parents[1] is server/, parents[2] the git root.
_SERVER = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[2]
SPEC = (_ROOT / "docs" / "superpowers" / "specs"
        / "2026-09-23-flows-mosaic-target-block-design.md")
APP = _SERVER / "astrodeck" / "api" / "app.py"
UI_SRC = _ROOT / "ui" / "src"


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _section(label: str) -> str:
    """The text under the ``### <label> ...`` heading, up to the next ``##``
    or ``###`` heading. ``label`` is the heading's first word, matched
    exactly, so "5.1" never finds "5.10" and "S1:" never finds "S1b"."""
    lines = _spec().splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.startswith("### ")
              and line[4:].split(" ", 1)[0] == label]
    assert len(starts) == 1, (
        f"the spec has {len(starts)} '### {label}' headings; the claims below "
        f"are read from exactly one")
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## ") or line.startswith("### "):
            break
        body.append(line)
    return "\n".join(body)


def _line(text: str, prefix: str) -> str:
    """The one line of ``text`` that starts with ``prefix``. The spec writes a
    bullet or a numbered item as one line, so a claim is one line."""
    hits = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(hits) == 1, (
        f"expected exactly one line starting {prefix!r}, found {len(hits)}")
    return hits[0]


def _says(text: str, phrases, where: str) -> None:
    """Every phrase is in ``text``. A bool per phrase, not ``phrase in text``
    inside the assert: pytest would print the whole paragraph, and the
    phrase is what the reader needs."""
    for phrase in phrases:
        said = phrase in text
        assert said, f"{where} must say: {phrase!r}"


def _tree(fn) -> ast.AST:
    """``fn``'s syntax tree. H2's docstrings name the calls its claims are
    about ("the re-point arms this target's own latch", "`_hold_park`"), so
    a text search of the source can be answered by a docstring; the tree
    holds only code."""
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _is_self_call(node: ast.AST, name: str | None = None) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and (name is None or node.func.attr == name))


def _self_calls(node: ast.AST, name: str | None = None) -> list[ast.Call]:
    """The ``self.<name>(...)`` calls under ``node``, in source order."""
    hits = [n for n in ast.walk(node) if _is_self_call(n, name)]
    return sorted(hits, key=lambda n: (n.lineno, n.col_offset))


def _keyword(call: ast.Call, key: str):
    """The constant a call passes as ``key=``, or None."""
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _keyword_node(call: ast.Call, key: str) -> ast.expr | None:
    """The expression a call passes as ``key=``, whatever it is, or None."""
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


def _app_function(name: str) -> str:
    """The source of route function ``name`` in ``api/app.py``, from its
    ``async def`` to the next route decorator. Read through the module's
    ``APP`` at call time, so a code mutant can point it at a scratch copy."""
    text = APP.read_text(encoding="utf-8")
    start = text.index(f"    async def {name}(")
    end = text.find("\n    @app.", start)
    return text[start:] if end == -1 else text[start:end]


# ------------------------------------------------------------------ control

#: One sentence per section that this round did NOT edit, so finding it
#: proves the section reader returned the real section and not an empty
#: string, against which every "not in" below would pass.
_ANCHORS = {
    "1.2": "The type id stays `target`, category SOURCE, label TARGET.",
    "3.3": "**Identity.** `NS_FLOWS` is a fixed uuid constant in `to_plan`.",
    "3.6": "`_migrate` **refuses** `schema_version > FLOW_SCHEMA`.",
    "5.6": "Every hop is a slew through the one motion path.",
    "5.8": "Holds are not pauses",
    "5.9": "**The critical section.**",
    "5.10": "`_set_state`",
    "S1:": "`_hop_focus_is_owed` (5.6 step 6)",
    "Still": "4. #192: the end-of-night roof close default",
}


def test_control_every_section_the_claims_read_is_the_real_section():
    """Not a mutation target: a reader that returned nothing would make the
    absence checks below pass on any spec. Each section is found once, holds
    a sentence this round left alone, and is more than a line long."""
    for label, anchor in _ANCHORS.items():
        text = _section(label)
        assert anchor in text, (label, anchor)
        assert len(text.splitlines()) > 3, label


# ------------------------------------------------------------ 1.2, the chip

_REF = re.compile(r"\bsection (\d+(?:\.\d+)?)\b", re.IGNORECASE)
_HEADING = re.compile(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", re.MULTILINE)
#: Section 6 is a table, and "section 6.9" names its row "| 6.9 |".
_ROW = re.compile(r"^\| (\d+\.\d+) \|", re.MULTILINE)


def test_every_section_reference_names_a_heading_that_exists():
    """1.2 sent the reader to "section 3.8" for the progress route, and the
    spec has no 3.8: section 3 stops at 3.7. Every "section N" or "section
    N.M" in the spec must name a heading, or a numbered row of the section 6
    table, that is there.

    RED under mutant "restore section 3.8" (the 1.2 chip line put back as
    it was):

        AssertionError: the spec sends the reader to section(s) ['3.8'],
        which it does not have
        assert ['3.8'] == []
          Left contains one more item: '3.8'

    Unchanged under the control "unrelated edit in section 7" (all pass).
    """
    text = _spec()
    headings = set(_HEADING.findall(text)) | set(_ROW.findall(text))
    refs = set(_REF.findall(text))
    # The readers read something: the headings run to 3.7 and stop, the
    # table rows include 6.9, and the references include ones this round
    # never touched.
    assert {"0", "3.7", "5.10", "10", "6.9"} <= headings
    assert "3.8" not in headings
    assert {"1.5", "5.6", "9"} <= refs and len(refs) >= 10
    missing = sorted(refs - headings)
    assert missing == [], (
        f"the spec sends the reader to section(s) {missing}, which it does "
        f"not have")


def _real_progress() -> dict:
    """A real ``flow_progress`` answer for one TARGET and one CAPTURE, so the
    keys the spec names are checked against what the route sends."""
    graph = FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={"name": "M31", "ra": "00h 42m 44s",
                                "dec": "+41 16 09", "rotation": -1}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={"filter": "L", "exposure": 60, "gain": 100,
                                "bin": "1", "count": 5, "goal": 0})],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"})])
    compiled = compile_plan(graph, "claims")
    plan, _unmapped = to_sequence_plan(compiled, graph, flow_id="spec-claims")
    return flow_progress(compiled, plan, None, flow_id="spec-claims")


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_the_1_2_chip_reads_the_progress_route_s1_built():
    """The chip line names the route ``api/app.py`` declares, the module and
    function whose answer it is, and only keys that answer really carries.

    RED under mutant "restore section 3.8" as well, on the route:

        AssertionError: 1.2 must name the route app.py declares
        assert '`GET /api/flows/{flow_id}/progress`' in '- A one-line state
        chip from the progress route (section 3.8): `4/6 panels done`, or
        `212/315 subs` for a single target.'
    """
    found = re.search(r'@app\.get\(\s*"(/api/flows/\{flow_id\}/progress)"',
                      APP.read_text(encoding="utf-8"))
    assert found, "app.py no longer declares the progress route this reads"
    chip = _line(_section("1.2"), "- A one-line state chip")
    assert f"`GET {found.group(1)}`" in chip, (
        "1.2 must name the route app.py declares")
    assert "`flow_progress`" in chip and "`flows/progress.py`" in chip
    emitted = _keys(_real_progress())
    for key in ("blocks", "panels", "banked", "owed", "total"):
        assert f"`{key}`" in chip, key
        assert key in emitted, (
            f"1.2 says the chip reads `{key}`, and flow_progress does not "
            f"send it")


# ------------------------------------------------------- 3.3, the identity

#: A recipe whose exposure the old spellings and the new one write
#: differently: ``{:g}`` keeps six significant digits and drops the
#: millisecond, and three places drop the fourth decimal.
_RECIPE = {"frame_type": "Light", "filter": "L", "exposure_s": 1234.5678,
           "gain": 100, "binning": 1}


def test_the_3_3_step_id_line_computes_the_ids_identity_mints():
    """The step_id line's own formula, evaluated, gives ``identity.step_id``'s
    id; it quotes the signature ``identity.step_signature`` spells for a
    1234.5678 s sub, and the gain spelling it gives for 100.5; and it names
    ``STEP_PLACES`` at the value identity uses. Nothing here is typed out: a
    change of spelling in identity turns this red until the spec follows.
    The "Why the microsecond" bullet under it names the three sub-millisecond
    exposures that must stay three recipes, and identity keeps them apart.

    Each spec mutant below ran against a scratch copy of the spec, with this
    module's ``SPEC`` pointed at it; the spec itself was never edited, and
    its sha256 was the same before and after. Each code mutant was rebound
    in the test process only; identity.py was never edited.

    RED under mutant "revert the 3.3 step_id line" (H1's line, with
    ``STEP_PLACES`` = 3 and ``Light/L/1234.568/100/1``):

        assert '`Light/L/1234.5678/100/1`' in '- `step_id = uuid5(NS_FLOWS,
        f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where
        `signature` is `identity.ste...1234.5678 s L sub at gain 100 and bin
        1 has the signature `Light/L/1234.568/100/1`, and gain 100.5 is
        spelled `100.5`.'

    RED under mutant "3.3 STEP_PLACES typed as 3":

        assert '`STEP_PLACES` = 6' in '- `step_id = uuid5(NS_FLOWS,
        f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where
        `signature` is `identity.ste...234.5678 s L sub at gain 100 and bin
        1 has the signature `Light/L/1234.5678/100/1`, and gain 100.5 is
        spelled `100.5`.'

    RED under mutant "drop the Why the microsecond bullet":

        AssertionError: expected exactly one line starting '  - Why the
        microsecond', found 0
        assert 0 == 1
         +  where 0 = len([])

    RED under the code mutant "identity.STEP_PLACES = 3", where the
    evaluated formula agrees because both sides use identity, and the quoted
    signature does not:

        assert '`Light/L/1234.568/100/1`' in '- `step_id = uuid5(NS_FLOWS,
        f"{target_id}/{stage_node_id}/{signature}/{n}").hex`, where
        `signature` is `identity.ste...234.5678 s L sub at gain 100 and bin
        1 has the signature `Light/L/1234.5678/100/1`, and gain 100.5 is
        spelled `100.5`.'

    Unchanged under the control "an unrelated edit in section 7" (all 15
    tests then in this file passed on that copy, and all 22 after H2's T10).
    """
    signature = identity.step_signature(**_RECIPE)
    line = _line(_section("3.3"), '- `step_id = uuid5(NS_FLOWS, f"')
    template = re.search(r'f"([^"]+)"', line).group(1)
    named = template.format(target_id="T", stage_node_id="S",
                            signature=signature, n=0, **_RECIPE)
    assert (uuid.uuid5(identity.NS_FLOWS, named).hex
            == identity.step_id("T", "S", n=0, **_RECIPE)), (
        "the spec's step_id formula names another step than identity.step_id")
    assert f"`{signature}`" in line
    gain = identity.step_signature(frame_type="Light", filter="L",
                                   exposure_s=60, gain=100.5,
                                   binning=1).split("/")[3]
    assert f"`{gain}`" in line
    assert f"`STEP_PLACES` = {identity.STEP_PLACES}" in line
    assert "keyed to the microsecond" in line
    assert "{exposure_s:g}" not in line
    why = _line(_section("3.3"), "  - Why the microsecond")
    assert "0.1 ms, 0.5 ms and 1 ms three recipes" in why
    # The code half of the "Why" bullet: identity spells the three apart.
    spelled = {identity.step_signature(frame_type="Light", filter="L",
                                       exposure_s=e, gain=100, binning=1)
               for e in (0.0001, 0.0005, 0.001)}
    assert len(spelled) == 3, (
        "identity spells two of 0.1 ms, 0.5 ms and 1 ms alike; 3.3 says "
        "they are three recipes")
    # Last, so a spelling change in identity is reported above as the spec
    # and the code disagreeing: the example must still tell the old spellings
    # from the new, or every check above would pass on either.
    exposure = signature.split("/")[2]
    assert f"{_RECIPE['exposure_s']:g}" != exposure != \
        f"{_RECIPE['exposure_s']:.3f}", (
            "the example exposure spells the same under an old spelling and "
            "under identity, so this test cannot tell them apart; choose "
            "another")


def _named_target_plan(name: str):
    """A compiled plan of one TARGET known only by ``name``, keyed as a
    saved flow is, against the shipped catalogue."""
    graph = FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={"name": name, "ra": "", "dec": "",
                                "rotation": -1}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={"filter": "L", "exposure": 60, "gain": 100,
                                "bin": "1", "count": 5, "goal": 0})],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"})])
    plan, _unmapped = to_sequence_plan(compile_plan(graph, "claims"), graph,
                                       flow_id="spec-claims")
    return [(t.id, [s.id for s in t.steps]) for t in plan.targets]


def test_the_3_3_name_keyed_target_rule_is_the_rule_identity_keeps():
    """3.3 says a TARGET with a name and no typed coordinates is keyed on the
    canonical identity ``tonight.resolve_target`` resolves the name to (the
    catalogue id of a fixed row, the canonical body name of a moving one),
    with the prefix identity writes, and that a rename to a spelling of the
    same row keeps the ids. The code does each: the spellings the bullet
    quotes resolve to the id it quotes, compile to the same ids, and a typed
    target is still a geometry key.

    Each spec mutant ran against a scratch copy of the spec, as above; each
    code mutant was rebound in the test process only (tonight.py and
    identity.py never edited).

    RED under mutant "restore H1's name-keyed bullet" (keyed on "the
    stripped name"):

        AssertionError: expected exactly one line starting '- **A TARGET
        with a name and no typed coordinates is keyed on the canonical
        identity the catalogue resolves its name to**', found 0
        assert 0 == 1
         +  where 0 = len([])

    RED under mutant "drop the rename sentence":

        AssertionError: **A rename keeps the ids when the new name resolves
        to the same row**
        assert '**A rename keeps the ids when the new name resolves to the
        same row**' in "- **A TARGET with a name and no typed coordinates is
        keyed on the canonical identity the catalogue resolves its name ...1
        has no grid on a TARGET, so both keys take the single-target shape;
        S3 passes the block's grid to the same two keys."

    RED under the code mutant "identity.NAME_KEY_PREFIX = 'named:'":

        assert '`"named:"`' in "- **A TARGET with a name and no typed
        coordinates is keyed on the canonical identity the catalogue
        resolves its name ...1 has no grid on a TARGET, so both keys take
        the single-target shape; S3 passes the block's grid to the same two
        keys."

    RED under the code mutant "resolve_target answers the name as typed"
    (``tonight.resolve_target`` rebound to a wrapper that returns the real
    answer with ``identity`` replaced by the stripped name):

        AssertionError: M 31
        assert ('M 31', False) == ('M31', False)
          At index 0 diff: 'M 31' != 'M31'

    RED under the code mutant "tonight.MOVING_KINDS = frozenset()":

        AssertionError: assert ('Jupiter', False) == ('Jupiter', True)
          At index 1 diff: False != True
    """
    from astrodeck.flows import tonight
    bullet = _line(_section("3.3"), "- **A TARGET with a name and no typed "
                                    "coordinates is keyed on the canonical "
                                    "identity the catalogue resolves its "
                                    "name to**")
    for word in ("`identity.target_key`", "`name_key`",
                 "`identity.typed_coordinates`", "`canonical_name`",
                 "`tonight.resolve_target`", "the catalogue id for a fixed "
                 "row", "the canonical body name for a time-dependent one",
                 "#229", "**A rename keeps the ids when the new name "
                 "resolves to the same row**", "re-keys"):
        assert word in bullet, word
    prefix = identity.NAME_KEY_PREFIX
    assert f'`"{prefix}"`' in bullet
    # The code half of the same sentences. Every spelling the bullet quotes
    # finds the row id it quotes, and the body it names moves.
    for spelling in ("M 31", "M31", "m31", "Andromeda"):
        assert f'"{spelling}"' in bullet, spelling
        hit = tonight.resolve_target(spelling)
        assert (hit.identity, hit.moves) == ("M31", False), spelling
    assert '"jupiter" is `Jupiter`' in bullet
    body = tonight.resolve_target("jupiter")
    assert (body.identity, body.moves) == ("Jupiter", True)
    # A rename between spellings of one row keeps every id; a typed target
    # is keyed on its geometry, and a named one carries the prefix.
    assert _named_target_plan("M 31") == _named_target_plan("m31"), (
        "renaming 'M 31' to 'm31' re-keyed the target; 3.3 says a rename to "
        "the same row keeps the ids")
    here = identity.target_key({"name": "Jupiter"}, 1.0, 2.0, None,
                               canonical="Jupiter")
    later = identity.target_key({"name": "jupiter"}, 1.5, 3.0, None,
                                canonical="Jupiter")
    typed = identity.target_key({"name": "M31", "ra": "00h 42m 44s",
                                 "dec": "+41 16 09"}, 0.71, 41.27, None,
                                canonical=None)
    assert here == later and here.startswith(prefix)
    assert not typed.startswith(prefix)


# ------------------------------------------------ 3.6, the migration note

def _v2_file(directory: Path, fid: str) -> None:
    """A flow file as a v2 build left it, holding the 23.4 palette default."""
    directory.mkdir(parents=True, exist_ok=True)
    graph = {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                        "params": {"name": "M31", "ra": "00h 42m 44s",
                                   "dec": "+41 16 09", "rotation": 23.4}},
                       {"id": "c", "type": "capture", "x": 100, "y": 0,
                        "params": {"filter": "L", "exposure": 60,
                                   "gain": 100, "bin": "1", "count": 5,
                                   "goal": 0}}],
             "edges": [{"from": "t", "fromPort": "target", "to": "c",
                        "toPort": "run"}]}
    (directory / f"{fid}.json").write_text(json.dumps(
        {"schema_version": 2, "id": fid,
         "flow": {"id": fid, "name": "old", "folder": "My flows",
                  "graph": graph}}), encoding="utf-8")


def test_3_6_says_the_note_is_shown_on_every_read_until_a_save(
        tmp_path, monkeypatch):
    """The store says the 23.4 note on every read until the operator saves
    (only ``save()`` stamps FLOW_SCHEMA), and the spec said "shown once". It
    must say what the store does, nowhere "once", and the store must still
    do it.

    RED under mutant "restore 'shown once' in 3.6":

        AssertionError: the spec still says the migration note is said once
        ('shown once'); the store says it on every read until the operator
        saves
        assert not True
    """
    text = _spec().lower()
    for stale in ("shown once", "shows once", "one-time note"):
        # A bool, not ``stale not in text``: pytest would print the spec.
        said = stale in text
        assert not said, (
            f"the spec still says the migration note is said once "
            f"({stale!r}); the store says it on every read until the "
            f"operator saves")
    assert "every read until the operator saves" in _section("3.6")
    # The code half: two reads both carry the note, and a save retires it.
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    store = FlowStore(tmp_path / "flows")
    _v2_file(store.dir, "f1")
    first, second = store.get("f1"), store.get("f1")
    assert first.migrated and second.migrated
    store.save(second)
    assert store.get("f1").migrated == []


# ----------------------------------------------------- 5.6, the stand-down

def test_5_6_and_s1_record_the_plan_guide_ruling_the_engine_keeps():
    """The stand-down before a slew happens only when ``plan.guide`` is set: a
    guider the operator started under a guiding-off plan is theirs. 5.6 step
    2 records it as a RULING, S1 item 2 says it, and ``_setup_target`` keeps
    it.

    RED under mutant "drop the plan.guide ruling from 5.6 step 2":

        AssertionError: 5.6 step 2 must record the plan.guide ruling
        assert ('RULING' in '2. **Stand the guider down first**, bounded,
        via `_stand_down_guider` (`engine.py:4394`). The native
        `start_guiding` ...ive.py:871`). This is PLAUSIBLE and unverified on
        the rig. A simulator test must demonstrate it before the fix
        (I-02).')
    """
    step2 = _line(_section("5.6"), "2. ")
    assert "RULING" in step2 and "`plan.guide`" in step2, (
        "5.6 step 2 must record the plan.guide ruling")
    assert "`plan.guide`" in _line(_section("S1:"), "2. ")
    source = inspect.getsource(SequenceEngine._setup_target)
    assert re.search(r"if self\.plan\.guide:\s*\n\s*await "
                     r"self\._stand_down_guider\(\)", source), (
        "_setup_target no longer stands the guider down only under "
        "plan.guide; the ruling in 5.6 step 2 needs revisiting")


# ------------------------------------------------------ 5.9, CONTINUE

def test_5_9_names_the_session_continue_reads_and_the_rules_around_it():
    """CONTINUE's session is the flow's newest by ``created_ts`` of any
    status, read through ``current_for_flow``; ADOPT also needs the two
    fields within ``ADOPT_MAX_SEPARATION_ARCMIN`` (the value from
    ``continuation``); a continued plan keeps the session's ``cool_to``; and
    starts are refused, 409 ``resume_recovering``, while
    ``ResumeArm.recovering`` (#189 A7), which ``api/app.py`` answers.

    RED under mutant "revert the 5.9 session sentence" (S1's "newest session
    with ... status == dormant"):

        AssertionError: 5.9 must say which session CONTINUE reads:
        '`SessionStore.current_for_flow`'
        assert '`SessionStore.current_for_flow`' in '**Across nights
        (CONTINUE).** `run_flow` compiles with `flow_id` and looks for the
        newest session with `origin == "flow"`, `origin_id == flow_id` and
        `status == "dormant"`. If that session\\'s plan shares step ids with
        the new compile:'

    RED under mutant "5.9 ADOPT bound typed as 5":

        AssertionError: 5.9 must give ADOPT's bound as continuation has it:
        `ADOPT_MAX_SEPARATION_ARCMIN = 10`
        assert False

    RED under the code mutant "continuation.ADOPT_MAX_SEPARATION_ARCMIN =
    5.0" (rebound in the test process; continuation.py never edited):

        AssertionError: 5.9 must give ADOPT's bound as continuation has it:
        `ADOPT_MAX_SEPARATION_ARCMIN = 5`
        assert False

    RED under mutant "5.9 without the resume_recovering refusal":

        assert '409 `resume_recovering`' in '\\n**Within a session** (a
        crash, `/recover`, or auto-resume):\\n\\n- Every banked frame is an
        atomic ledger save (`engi...
    """
    s59 = _section("5.9")
    which = _line(s59, "**Across nights (CONTINUE).**")
    for phrase in ("`SessionStore.current_for_flow`", "`created_ts`",
                   "of any status", "abandoned"):
        assert phrase in which, (
            f"5.9 must say which session CONTINUE reads: {phrase!r}")
    bound = (f"`ADOPT_MAX_SEPARATION_ARCMIN = "
             f"{continuation.ADOPT_MAX_SEPARATION_ARCMIN:g}`")
    found = bound in s59
    assert found, (
        f"5.9 must give ADOPT's bound as continuation has it: {bound}")
    assert "`cool_to`" in s59 and "`ResumeArm.recovering`" in s59
    assert "409 `resume_recovering`" in s59
    # The code halves the sentences name.
    assert callable(SessionStore.current_for_flow)
    assert isinstance(inspect.getattr_static(ResumeArm, "recovering"),
                      property)
    assert '"code": "resume_recovering"' in APP.read_text(encoding="utf-8"), (
        "app.py no longer refuses a start with resume_recovering; 5.9 says "
        "it does")


def test_5_9_says_the_adopt_bound_is_inclusive_and_a_body_matches_by_name():
    """5.9's ADOPT row says the bound is inclusive (a match exactly 10 arcmin
    apart is re-keyed) and that a moving body matches on its canonical name,
    learned through ``tonight.resolve_target``, with no bound (#229).
    ``continuation.adopt_matches`` does both: two fields exactly
    ``ADOPT_MAX_SEPARATION_ARCMIN`` apart map, and a body typed in another
    case maps across 120 arcmin.

    Each spec mutant ran against a scratch copy of the spec, as above; each
    code mutant was rebuilt from a scratch copy of the function's source and
    rebound onto ``continuation`` in the test process only (continuation.py
    never edited).

    RED under mutant "5.9 without the inclusive sentence":

        AssertionError: 5.9's ADOPT row must say: 'a match exactly 10 arcmin
        apart is re-keyed'
        assert False

    RED under mutant "5.9 without the moving-body sentence":

        AssertionError: 5.9's ADOPT row must say: '**A moving body matches
        on its canonical name, and the bound does not apply to it**'
        assert False

    RED under the code mutant "'<=' to '<'" (``adopt_matches`` rebuilt with
    ``apart < ADOPT_MAX_SEPARATION_ARCMIN``):

        AssertionError: a match exactly at the bound was not re-keyed
        assert [] == ['502d9242e4b...10778aaef3d9']
          Right contains one more item: '502d9242e4be44f0be9f10778aaef3d9'

    RED under the code mutant "the bound applies to bodies"
    (``adopt_matches`` rebuilt measuring and comparing every match):

        AssertionError: a body 120 arcmin from its old position was not
        re-keyed
        assert [] == ['8a46f391ba4...5dff2308a328']
          Right contains one more item: '8a46f391ba4c4cf092755dff2308a328'
    """
    from astrodeck.flows.tonight import NameResolution
    from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
    from astrodeck.sequence.session import Session, SessionFrame
    row = _line(_section("5.9"), "| the dormant session shares **no** step")
    bound = continuation.ADOPT_MAX_SEPARATION_ARCMIN
    for phrase in ("inclusive", f"a match exactly {bound:g} arcmin apart is "
                   "re-keyed", "**A moving body matches on its canonical "
                   "name, and the bound does not apply to it**",
                   "`tonight.resolve_target`", "#229",
                   "A deep-sky or star name keeps the name as typed in its "
                   "key, and the bound"):
        said = phrase in row
        assert said, f"5.9's ADOPT row must say: {phrase!r}"

    def session(name, dec):
        step = ExposureStep(filter="L", exposure_s=60.0, count=5)
        t = Target(name=name, ra_hours=5.0, dec_deg=dec, steps=[step])
        s = Session(status="dormant", plan=SequencePlan(name="p",
                                                        targets=[t]))
        s.frames.append(SessionFrame(target_id=t.id, step_id=step.id))
        return s, step

    def tonight_plan(name, dec):
        return SequencePlan(targets=[Target(
            name=name, ra_hours=5.0, dec_deg=dec,
            steps=[ExposureStep(filter="L", exposure_s=60.0, count=5)])])

    # Inclusive: 10/60 deg north on the equator is exactly the bound.
    old, old_step = session("M16", 0.0)
    plan = tonight_plan("M16", 10.0 / 60.0)
    apart = continuation._separation_arcmin(old.plan.targets[0],
                                            plan.targets[0])
    exact = apart == bound
    assert exact, "premise: the two fields are exactly the bound apart"
    assert list(continuation.adopt_matches(old, plan).mapping) == [
        old_step.id], "a match exactly at the bound was not re-keyed"

    # A body: typed in another case, 120 arcmin from where it was.
    def catalogue(name, when=None):
        if str(name).strip().lower() == "jupiter":
            return NameResolution(ra_hours=5.0, dec_deg=22.0,
                                  identity="Jupiter", moves=True)
        return None
    old, old_step = session("jupiter", 20.0)
    plan = tonight_plan("Jupiter", 22.0)
    assert list(continuation.adopt_matches(
        old, plan, resolve=catalogue).mapping) == [old_step.id], (
        "a body 120 arcmin from its old position was not re-keyed")


def test_s1_item_8_carries_the_skipped_panel_exemption_as_debt():
    """S1 promised CONTINUE with "skipped panels exempt", and S1 has no groups:
    no ``TargetGroup.skipped_ids`` to read and no ``skip`` param to set one.
    Item 8 says it is debt for S2/S3, and the code agrees there is none yet.

    WHEN THIS GOES RED ON THE CODE HALF, S2 or S3 has built the exemption:
    rewrite item 8 to say so, and this test with it.

    RED under mutant "restore S1 item 8's 'skipped panels exempt'":

        AssertionError: S1 item 8 must name the debt and where it lands
        assert ('`TargetGroup.skipped_ids`' in '8. `run_flow` CONTINUE,
        dormant sessions only (5.9): the factored plan replace plus
        `engine.start(session=)` inside o...parately; `accept_dropped`
        (skipped panels exempt), `accept_recount`, `fresh`, and the ADOPT
        path for pre-S1 sessions.')
    """
    item8 = _line(_section("S1:"), "8. ")
    assert ("`TargetGroup.skipped_ids`" in item8 and "`skip`" in item8
            and "debt carried to S2/S3" in item8), (
        "S1 item 8 must name the debt and where it lands")
    assert "(skipped panels exempt)" not in item8
    built = sorted(str(p.relative_to(_SERVER))
                   for p in (_SERVER / "astrodeck").rglob("*.py")
                   if "skipped_ids" in p.read_text(encoding="utf-8"))
    assert built == [], (
        f"skipped_ids now exists in {built}: the exemption is no longer debt, "
        f"so S1 item 8 is stale")


# ----------------------------------------------------- 5.10, the finish clock

def test_5_10_says_hops_costed_is_returned_and_not_yet_rendered():
    """``compute_eta`` returns ``hops_costed`` and prices the hops from a seed
    of ``HOP_COST_S`` until one is measured, and no UI file reads the flag
    yet: saying "hops not yet costed" is S5's. When a UI starts rendering it,
    the code half goes red and the spec line needs updating.

    RED under mutant "restore the 5.10 compute_eta line" (S1's
    "remaining_visits x hop EMA" line):

        AssertionError: 5.10 must say hops_costed is returned and not yet
        rendered
        assert False
    """
    line = _line(_section("5.10"), "- `compute_eta`")
    said = "`hops_costed`" in line and "not yet rendered" in line
    assert said, "5.10 must say hops_costed is returned and not yet rendered"
    assert "S5" in line
    assert f"`HOP_COST_S` = {engine_mod.HOP_COST_S:g} s" in line
    assert '"hops_costed"' in inspect.getsource(SequenceEngine.compute_eta)
    assert UI_SRC.is_dir(), "the UI tree this reads is missing"
    rendered = sorted(str(p.relative_to(UI_SRC)) for p in UI_SRC.rglob("*")
                      if p.suffix in {".ts", ".tsx"}
                      and "hops_costed" in p.read_text(encoding="utf-8",
                                                       errors="replace"))
    assert rendered == [], (
        f"{rendered} now read hops_costed: 5.10's 'not yet rendered' is stale")


# ---------------------------------------------- 5.8 and 6.17, the clocks

def test_5_8_and_6_17_put_the_hold_checks_and_the_stop_retry_on_own_clocks():
    """The cloud hold keeps tracking on with no frame loop, so its floor, flip
    and tracking checks run on the hold's own clock (#203, #205); an idle stop
    the mount did not confirm is asked again on its own clock, at most once
    per ``IDLE_STOP_RETRY_S`` (the engine's value). Both 5.8 and 6.17 say
    both.

    RED under mutant "drop the cloud-hold bullet from 5.8":

        AssertionError: expected exactly one line starting '- **A cloud hold
        watches the mount on its own clock**', found 0
        assert 0 == 1

    RED under mutant "6.17 row without the stop retry":

        AssertionError: ('6.17', '`IDLE_STOP_RETRY_S` (60 s)')
        assert '`IDLE_STOP_RETRY_S` (60 s)' in "| 6.17 | Idle mount | A mount
        left tracking with no frame loop watching it is park-held on the idle
        clock (`WAIT_TEAR...ich keeps tracking on by design, runs its floor,
        flip and tracking checks on the hold's own clock (5.8, #203, #205). |"

    RED under the code mutant "engine.IDLE_STOP_RETRY_S = 30.0" (rebound in
    the test process; engine.py never edited):

        AssertionError: ('5.8', '`IDLE_STOP_RETRY_S` (30 s)')
        assert '`IDLE_STOP_RETRY_S` (30 s)' in "- **A cloud hold watches the
        mount on its own clock** (#203, #205). The hold keeps tracking on by
        design, for up to `...t does not confirm is asked again at most once
        per `IDLE_STOP_RETRY_S` (60 s), never from the wait loop's tick
        (6.17)."
    """
    retry = f"`IDLE_STOP_RETRY_S` ({engine_mod.IDLE_STOP_RETRY_S:g} s)"
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    row = _line(_spec(), "| 6.17 |")
    for where, text in (("5.8", hold), ("6.17", row)):
        for word in ("floor", "flip", "tracking", "hold's own clock",
                     "#203", "#205", retry):
            assert word in text, (where, word)


def test_5_8_says_the_hold_watch_is_built_and_the_engine_has_it():
    """5.8 says this round built the hold's watch (``_hold_watch``), and the
    engine has it: ``_hold_for_clear`` looks at the mount between probes, the
    looks ask the two predicates 5.8 names, and the mount's tracking is read
    before the check frame. The first draft of this revision said "Today the
    hold runs none of the three" while the round was building it (#203,
    #205), so the claim is held to the code in both directions: a spec that
    says "not built" over a hold that watches, or "built" over one that does
    not, goes red.

    RED under mutant "5.8 says the hold is not yet built" (the first draft's
    sentence put back):

        AssertionError: 5.8 must say the hold's watch is built, as
        _hold_for_clear has it
        assert False

    Each code mutant below is one engine method rebuilt from a scratch copy
    and rebound onto SequenceEngine inside the test process; engine.py was
    never edited.

    RED under the code mutant "_hold_for_clear as HEAD had it" (S1's body,
    with no watch and no tracking read):

        AssertionError: _hold_for_clear no longer looks at the mount between
        probes; 5.8 says it does (#203)
        assert 'self._hold_watch' in 'async def _hold_for_clear(self,
        reason: str, target: Target | None) -> None:\\n    \"\"\"Hold the
        run until the sky clear...sumed after {held_min:.0f} min of
        cloud")\\n            return\\n    finally:\\n
        self._holding_for_clear = False\\n'

    RED under the code mutant "no tracking read before the probe" (today's
    ``_hold_for_clear`` with ``await self._tracking_now() is False`` made
    ``False``):

        AssertionError: _hold_for_clear no longer reads tracking before its
        check frame; 5.8 says it does (#205)
        assert 0 <= -1

    RED under the code mutant "the flip watch never asks _idle_flip_due"
    (today's ``_hold_flip_watch`` with that call made ``False``):

        AssertionError: the hold's looks no longer ask _idle_flip_due; 5.8
        names it
        assert 'self._idle_flip_due(' in '    async def
        _hold_for_clear(self, reason: str, target: Target | None) -> None:...

    Unchanged under the control "a sentence appended after the Revision 3
    table" (all 12 then in this file passed).
    """
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    # "none of the three" may follow "Before it,", which is history; before
    # it, it would be a claim about today.
    said = ("`_hold_watch`" in hold and "none of the three" not in
            hold.split("Before it,")[0] and "not yet built" not in hold)
    assert said, (
        "5.8 must say the hold's watch is built, as _hold_for_clear has it")
    for word in ("`_altitude_limit_verdict`", "`_idle_flip_due`"):
        assert word in hold, word
    # The code half. The watch's helpers are the engine's ``_hold_*``
    # methods; the predicates may be asked from any of them.
    source = inspect.getsource(SequenceEngine._hold_for_clear)
    assert "self._hold_watch" in source, (
        "_hold_for_clear no longer looks at the mount between probes; 5.8 "
        "says it does (#203)")
    helpers = "".join(inspect.getsource(fn)
                      for name, fn in vars(SequenceEngine).items()
                      if name.startswith("_hold_") and inspect.isfunction(fn))
    for predicate in ("_altitude_limit_verdict", "_idle_flip_due"):
        assert f"self.{predicate}(" in helpers, (
            f"the hold's looks no longer ask {predicate}; 5.8 names it")
    tracked = source.find("self._tracking_now()")
    probed = source.find("self._cloud_probe(")
    assert 0 <= tracked < probed, (
        "_hold_for_clear no longer reads tracking before its check frame; "
        "5.8 says it does (#205)")


# ------------------------------ 5.8, the hold's re-point (H2, ruling 2)

#: The re-point bullet of 5.8, as H2 orchestrator ruling 2 rewrote it.
_REPOINT = ("- **A hold points the mount at its own target before it judges "
            "the sky**")


def test_5_8_says_a_stopped_hold_slews_back_and_the_engine_does():
    """5.8 says a cloud hold points the mount at its own target before it
    judges the sky: back at it after the zenith keep-out, and at it for the
    first time when a target setup's pre-slew gate opened the hold while the
    mount tracked, or was stopped on, the last target (#224, #225). It slews
    through the slew gate and the Sun check when the target is above its
    floor, and otherwise stops tracking and says why. That is H2
    orchestrator ruling 2, which the owner list records as item 5 in place
    of the #224/#225 question the item used to ask. The engine does each
    part: ``_hold_for_clear`` asks ``_hold_repoint_refusal`` and then
    re-points or stops (``_hold_park("elsewhere", ...)``); the refusal asks
    the slew gate's projection and the target's own floor;
    ``_hold_repoint`` asks the slew gate and the Sun check before it moves
    anything, and makes the mount the target's; and ``_hold_watch``
    re-points after the keep-out and retries a refused re-point.

    H1's version of this test held a bullet that said #224 was open and
    waited on the owner. H2 decided it (the orchestrator's ruling, binding
    until the owner overturns it), so the test now holds the decision and
    refuses the old sentence.

    Each spec mutant below ran against a scratch copy of the spec, with this
    module's ``SPEC`` pointed at it; the spec itself was never edited, and
    its sha256 was the same before and after. Each code mutant is one method
    rebuilt from a scratch copy of its source and rebound in the test
    process only; engine.py was never edited.

    RED under mutant "restore H1's 5.8 re-point bullet":

        AssertionError: expected exactly one line starting '- **A hold points
        the mount at its own target before it judges the sky**', found 0
        assert 0 == 1
         +  where 0 = len([])

    The #221 and #228 tests below, and the latch test, go red on the same
    line.

    RED under mutant "put owner item 5's #224/#225 question back":

        AssertionError: owner list item 5 must record H2 orchestrator ruling 2
        in place of the #224/#225 question
        assert False

    The #221 test and the owner-list test below go red under it too.

    RED under the code mutant "_hold_repoint without the slew gate" (its
    ``_safety_gate`` line made ``pass``):

        AssertionError: _hold_repoint no longer asks the slew gate before it
        moves the mount; 5.8 says each slew goes through it
        assert 'self._safety_gate(context="slew"' in 'async def
        _hold_repoint(self, target: Target, *,\\n
        elsewhere: bool = False) -> None:\\n    \"\"\"P...ld for cloud -
        back on the target after the "\\n
        "zenith keep-out; checking the sky again")\\n'

    The latch test below goes red under it too, at its #240 check ("no
    longer asks the slew gate and the Sun check before the try").

    RED under the code mutant "the hold resumes in place whatever the mount
    is on" (``_hold_for_clear`` with ``elsewhere=True`` removed from its
    ``_hold_repoint`` call):

        AssertionError: _hold_for_clear no longer asks _hold_repoint_refusal
        and then slews a mount on another target to the held target; 5.8 says
        it does (#224, #225)
        assert False

    RED under the code mutant "the refusal ignores the target's own floor"
    (``_hold_repoint_refusal`` with ``floor = self._own_floor(target)``
    made ``floor = None``):

        AssertionError: _hold_repoint_refusal no longer asks the target's own
        floor; 5.8 says the target must be above its floor
        assert False

    RED under the code mutant "a re-point leaves _tracked_target alone"
    (``_hold_repoint`` without ``self._tracked_target = target``):

        AssertionError: _hold_repoint no longer makes the mount the held
        target's (_tracked_target); 5.8 says every re-point does
        assert []

    Unchanged under the control "unrelated edit in section 7" (every test in
    this file passes on that copy).
    """
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, ("`_hold_repoint`", "`_hold_repoint_refusal`",
                   "`_hold_park`", "zenith keep-out", "slew gate",
                   "Sun check", "above its floor", "says why",
                   "`_tracked_target`", "#224", "#225",
                   "H2 orchestrator ruling 2",
                   "A slew under cloud is allowed"), "5.8's re-point bullet")
    for stale in ("#224, open", "waits on the owner", "for the owner"):
        handed = stale in bullet
        assert not handed, (
            f"5.8 still hands #224 to the owner ({stale!r}); H2 orchestrator "
            f"ruling 2 decided it")
    owner = _section("Still")
    item5 = _line(owner, "5. ")
    ruled = item5.startswith("5. **H2 orchestrator ruling 2:")
    assert ruled, ("owner list item 5 must record H2 orchestrator ruling 2 "
                   "in place of the #224/#225 question")
    _says(item5, ("#224", "#225", "5.8"), "owner list item 5")
    asked = "may a cloud hold" in owner.lower()
    assert not asked, ("the owner list still asks the #224/#225 question "
                       "that H2 orchestrator ruling 2 answered")
    # The code halves: the gates, the refusal, the stop, and the two call
    # sites 5.8 names.
    repoint = inspect.getsource(SequenceEngine._hold_repoint)
    for gate, what in (('self._safety_gate(context="slew"', "the slew gate"),
                       ("self.hub._check_solar(", "the Sun check")):
        assert gate in repoint, (
            f"_hold_repoint no longer asks {what} before it moves the "
            f"mount; 5.8 says each slew goes through it")
    owned = [n for n in ast.walk(_tree(SequenceEngine._hold_repoint))
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Attribute)
                     and t.attr == "_tracked_target" for t in n.targets)
             and isinstance(n.value, ast.Name) and n.value.id == "target"]
    assert owned, ("_hold_repoint no longer makes the mount the held "
                   "target's (_tracked_target); 5.8 says every re-point does")
    hold = _tree(SequenceEngine._hold_for_clear)
    refusal = _self_calls(hold, "_hold_repoint_refusal")
    elsewhere = [c for c in _self_calls(hold, "_hold_repoint")
                 if _keyword(c, "elsewhere") is True]
    ordered = (bool(refusal) and bool(elsewhere)
               and refusal[0].lineno < elsewhere[0].lineno)
    assert ordered, (
        "_hold_for_clear no longer asks _hold_repoint_refusal and then slews "
        "a mount on another target to the held target; 5.8 says it does "
        "(#224, #225)")
    stops = [c for c in _self_calls(hold, "_hold_park")
             if c.args and isinstance(c.args[0], ast.Constant)
             and c.args[0].value == "elsewhere"]
    assert stops, ("_hold_for_clear no longer stops a mount it may not "
                   "point at the held target; 5.8 says it does")
    asks = _self_calls(_tree(SequenceEngine._hold_repoint_refusal))
    projected = [c for c in asks if c.func.attr == "_altitude_limit_verdict"
                 and _keyword(c, "projected") is True]
    assert projected, ("_hold_repoint_refusal no longer asks the slew gate's "
                       "projection; 5.8 says a re-point waits for it")
    floor = any(c.func.attr == "_own_floor" for c in asks)
    assert floor, ("_hold_repoint_refusal no longer asks the target's own "
                   "floor; 5.8 says the target must be above its floor")
    watch = _self_calls(_tree(SequenceEngine._hold_watch), "_hold_repoint")
    kinds = sorted(bool(_keyword(c, "elsewhere")) for c in watch)
    assert kinds == [False, True], (
        "_hold_watch no longer re-points after the keep-out (a plain "
        "_hold_repoint) and retries a refused re-point (elsewhere=True); "
        "5.8 says it does both")


def test_5_8_says_a_repoint_unparks_and_arms_only_the_held_targets_latch():
    """The rest of 5.8's re-point bullet, which the test above does not
    reach. The hold unparks a parked mount before it slews. The last
    target's flip latch is disarmed while the mount is elsewhere, and the
    re-point arms B's own, so a look never flips B on the last target's hour
    angle. And the stop follows a refusal of the gate's PROJECTION, which is
    all ``_hold_repoint_refusal`` asks first: the slew gate's pier guard and
    the Sun check are asked only by the slew itself, and they raise the
    SafetyAbort any slew raises instead of stopping the hold, which ends the
    run where H2 orchestrator ruling 2 says the hold stops tracking (#240,
    filed by T10's verifier). The bullet first said "When the gate would
    refuse", which claimed the stop for those two as well; it now names
    #240, and the check that the refusal still asks neither goes red when
    #240 is fixed. The engine: ``_hold_for_clear``'s ``if elsewhere:``
    arm sets ``_flip_armed`` False before it asks the refusal;
    ``_hold_repoint`` asks ``_arm_meridian_flip(target)`` under its own ``if
    elsewhere:``; it awaits ``tel.unpark()`` before ``tel.slew``; and it
    asks the slew gate and the Sun check before the ``try`` whose ``except
    Exception`` turns a failed slew into a stop.

    Mutants as in the tests above (scratch spec, rebound method).

    RED under mutant "5.8 says any gate refusal stops the hold" (the
    bullet's first wording put back):

        AssertionError: 5.8's re-point bullet must say: "When the gate's
        projection would refuse"
        assert False

    RED under mutant "drop the latch sentence from 5.8":

        AssertionError: 5.8's re-point bullet must say: "The last target's
        flip latch is disarmed while the mount is elsewhere, and the re-point
        arms B's own"
        assert False

    RED under the code mutant "the last target's latch left armed"
    (``_hold_for_clear`` without ``self._flip_armed = False``):

        AssertionError: _hold_for_clear no longer disarms the last target's
        flip latch before it asks whether it may re-point; 5.8 says a look
        never flips B on the last target's hour angle
        assert []

    RED under the code mutant "a re-point from elsewhere arms no latch"
    (``_hold_repoint`` with ``self._arm_meridian_flip(target)`` made
    ``pass``):

        AssertionError: _hold_repoint no longer arms the held target's own
        flip latch after a re-point from elsewhere; 5.8 says it does
        assert []

    RED under the code mutant "a re-point slews a parked mount"
    (``_hold_repoint`` with its ``tel.unpark()`` await made ``None``):

        AssertionError: _hold_repoint no longer unparks before it slews
        (mount calls ['is_parked', 'set_tracking', 'slew']); 5.8 says it does
        assert False

    RED under mutant "drop the #240 sentence from 5.8":

        AssertionError: 5.8's re-point bullet must say: "The gate's pier guard
        and the Sun check are not asked first"
        assert False

    RED under the code mutant "the refusal asks the Sun cone" (the shape of
    a #240 fix: ``self.hub._check_solar(...)`` added to
    ``_hold_repoint_refusal``):

        AssertionError: _hold_repoint_refusal now asks ['_check_solar'], so
        #240 may be fixed and 5.8's 'not asked first' is stale
        assert ['_check_solar'] == []

    RED under the code mutant "the slew gate asked inside the stopping try"
    (``_hold_repoint`` with its ``_safety_gate`` call moved into the ``try``
    ahead of the unpark, so the other test's text check still finds it):

        AssertionError: _hold_repoint no longer asks the slew gate and the Sun
        check before the try that turns a failed slew into a stop, so a
        refusal may no longer end the run; 5.8 says it does (#240)
        assert False

    Unchanged under the control "unrelated edit in section 7" (all 23 in
    this file pass on that copy).
    """
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, ("unparking a parked mount first",
                   "The last target's flip latch is disarmed while the mount "
                   "is elsewhere, and the re-point arms B's own",
                   "When the gate's projection would refuse"),
          "5.8's re-point bullet")
    broad = "When the gate would refuse" in bullet
    assert not broad, (
        "5.8 says any refusal of the slew gate stops the hold; only its "
        "projection is asked first, and its pier guard and the Sun check "
        "raise a SafetyAbort from the slew")
    _says(bullet, ("The gate's pier guard and the Sun check are not asked "
                   "first", "that ends the run (#240)"),
          "5.8's re-point bullet")
    # #240 STILL OPEN: the refusal asks neither the pier guard nor the Sun
    # cone, and `_hold_repoint` asks both before the ``try`` whose ``except``
    # turns a failed slew into a stop. When #240 is fixed this goes red, and
    # 5.8's sentence and Revision 4's row 2 are stale.
    asked = {n.func.attr for n in ast.walk(
        _tree(SequenceEngine._hold_repoint_refusal))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    pier_or_sun = sorted(asked & {"_check_solar", "destination_pier_side",
                                  "_enforce_mount_floor", "_safety_gate"})
    assert pier_or_sun == [], (
        f"_hold_repoint_refusal now asks {pier_or_sun}, so #240 may be "
        f"fixed and 5.8's 'not asked first' is stale")
    body = _tree(SequenceEngine._hold_repoint).body[0].body
    tried = min(s.lineno for s in body if isinstance(s, ast.Try)
                and any(isinstance(h.type, ast.Name)
                        and h.type.id == "Exception" for h in s.handlers))
    gates = [c.lineno for c in ast.walk(_tree(SequenceEngine._hold_repoint))
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr in ("_safety_gate", "_check_solar")]
    before = len(gates) == 2 and all(line < tried for line in gates)
    assert before, (
        "_hold_repoint no longer asks the slew gate and the Sun check before "
        "the try that turns a failed slew into a stop, so a refusal may no "
        "longer end the run; 5.8 says it does (#240)")
    hold = _tree(SequenceEngine._hold_for_clear)
    refusal = _self_calls(hold, "_hold_repoint_refusal")
    arms = [n for n in ast.walk(hold) if isinstance(n, ast.If)
            and isinstance(n.test, ast.Name) and n.test.id == "elsewhere"]
    disarmed = [s for n in arms for s in n.body
                if isinstance(s, ast.Assign)
                and any(isinstance(t, ast.Attribute)
                        and t.attr == "_flip_armed" for t in s.targets)
                and isinstance(s.value, ast.Constant)
                and s.value.value is False
                and refusal and s.lineno < refusal[0].lineno]
    assert disarmed, (
        "_hold_for_clear no longer disarms the last target's flip latch "
        "before it asks whether it may re-point; 5.8 says a look never "
        "flips B on the last target's hour angle")
    repoint = _tree(SequenceEngine._hold_repoint)
    own = [n for n in ast.walk(repoint) if isinstance(n, ast.If)
           and isinstance(n.test, ast.Name) and n.test.id == "elsewhere"
           and any(_is_self_call(c, "_arm_meridian_flip")
                   and c.args and isinstance(c.args[0], ast.Name)
                   and c.args[0].id == "target"
                   for s in n.body for c in ast.walk(s))]
    assert own, ("_hold_repoint no longer arms the held target's own flip "
                 "latch after a re-point from elsewhere; 5.8 says it does")
    tel = {n.func.attr: n.lineno for n in ast.walk(repoint)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and isinstance(n.func.value, ast.Name) and n.func.value.id == "tel"}
    unparks = "unpark" in tel and "slew" in tel and tel["unpark"] < tel["slew"]
    assert unparks, (f"_hold_repoint no longer unparks before it slews "
                     f"(mount calls {sorted(tel)}); 5.8 says it does")


def test_5_8_says_a_wait_opens_no_target_less_hold_and_the_engine_opens_none():
    """5.8 says a scheduler wait opens no target-less hold (#221, part of H2
    orchestrator ruling 2): a cloudy verdict with no target is said once per
    wait spell and published in ``sky.hold_deferred``, the wait goes on
    under the idle park-hold, and the next target's pre-slew gate opens a
    hold with a target. The engine's sky fallback, asked with no target,
    notes the verdict (``_note_hold_deferred``) and returns before the line
    that opens a hold, and the sky state publishes the note.

    Mutants as in the test above (scratch spec, rebound method).

    RED under mutant "drop the target-less hold sentence from 5.8":

        AssertionError: 5.8's re-point bullet must say: '**A scheduler wait
        opens no target-less hold** (#221)'
        assert False

    RED under the code mutant "a wait opens a target-less hold again"
    (``_no_safety_source`` without its ``if target is None:`` return):

        AssertionError: _no_safety_source no longer notes a cloudy sky with no
        target and returns before it opens a hold; 5.8 says a wait opens no
        target-less hold (#221)
        assert []
    """
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, ("**A scheduler wait opens no target-less hold** (#221)",
                   "`sky.hold_deferred`", "`_note_hold_deferred`",
                   "the wait goes on under the idle park-hold",
                   "the next target's pre-slew gate opens a hold with a "
                   "target"), "5.8's re-point bullet")
    _says(_line(_section("Still"), "5. "),
          ("A scheduler wait opens no target-less hold",),
          "owner list item 5")
    # The code half: with no target, the fallback notes the verdict and
    # returns above the one line that opens a hold.
    tree = _tree(SequenceEngine._no_safety_source)
    held = _self_calls(tree, "_hold_for_clear")
    assert held, ("_no_safety_source no longer opens a cloud hold at all, so "
                  "5.8's sentence about when it does not needs revisiting")
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
              and isinstance(n.test.left, ast.Name)
              and n.test.left.id == "target"
              and isinstance(n.test.ops[0], ast.Is)
              and isinstance(n.test.comparators[0], ast.Constant)
              and n.test.comparators[0].value is None
              and any(_is_self_call(c, "_note_hold_deferred")
                      for s in n.body for c in ast.walk(s))
              and isinstance(n.body[-1], ast.Return)
              and n.lineno < held[0].lineno]
    assert guards, (
        "_no_safety_source no longer notes a cloudy sky with no target and "
        "returns before it opens a hold; 5.8 says a wait opens no "
        "target-less hold (#221)")
    published = ('"hold_deferred": self._hold_deferred'
                 in inspect.getsource(SequenceEngine._sky_state))
    assert published, ("the sky state no longer publishes hold_deferred; 5.8 "
                       "says a wait's cloudy verdict is published there")


def _statements(tree: ast.AST):
    """Every statement list in ``tree``, so a test can ask what follows
    what."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0],
                                                                ast.stmt):
                yield block


def _publishes_detail(stmt: ast.stmt) -> bool:
    """Is ``stmt`` the call that publishes the hold's opening detail,
    ``self._set_state(detail=detail)``?"""
    return (isinstance(stmt, ast.Expr) and _is_self_call(stmt.value,
                                                         "_set_state")
            and any(k.arg == "detail" and isinstance(k.value, ast.Name)
                    and k.value.id == "detail" for k in stmt.value.keywords))


def test_5_8_says_the_published_detail_says_what_the_hold_is_doing():
    """5.8 says the hold's published detail says what the hold is doing
    (#228, part of H2 orchestrator ruling 2): it says the sky is being
    checked only once the mount is on the held target, and after a stop only
    once tracking is back; a stopped mount's detail says "the mount is
    stopped", why, and "The sky is not judged until it tracks again", words
    this test takes from ``_hold_park``. In ``_hold_for_clear`` the opening
    detail after a re-point from elsewhere is published only under ``if
    self._tracked_target is target``, with the stop in the other arm, and
    after a lost-tracking resume only as the statement after
    ``_enforce_tracking``, which raises when tracking does not come back.
    ONLY there: no other statement publishes it, and the open publishes it
    only when the mount is not elsewhere. Without that half, both arms above
    held beside a third publish of the detail (the verifier's mutants
    below), and a mount still stopped was said to be checking the sky.

    Mutants as in the tests above.

    RED under mutant "drop the published-detail sentence from 5.8":

        AssertionError: 5.8's re-point bullet must say: '**The published detail
        says what the hold is doing** (#228)'
        assert False

    RED under the code mutant "the opening detail published whatever the
    re-point did" (``_hold_for_clear`` with ``if self._tracked_target is
    target:`` made ``if True:``):

        AssertionError: _hold_for_clear no longer publishes its opening detail
        only when the re-point put the mount on the held target; 5.8 says a
        stopped mount never claims the sky is checked (#228)
        assert []

    RED under the code mutant "the detail restored before tracking is back"
    (``_hold_for_clear`` with ``self._set_state(detail=detail)`` added
    before ``await self._enforce_tracking(...)``, the one after it kept):

        AssertionError: _hold_for_clear publishes its opening detail at
        line(s) [230] of its source, outside the re-point's success arm and
        the line after _enforce_tracking; 5.8 says it is published only once
        the mount is on the held target and tracking (#228)
        assert [230] == []

    RED under the code mutant "the opening says the sky is checked whatever
    the mount is on" (the open's ``if elsewhere else detail`` made ``if
    False else detail``):

        AssertionError: _hold_for_clear's opening no longer keeps the opening
        detail for a mount that is already on the held target; 5.8 says a
        mount that is elsewhere is not said to be checking the sky (#228)
        assert False
    """
    park = inspect.getsource(SequenceEngine._hold_park)
    words = ("the mount is stopped", "The sky is not judged until it tracks "
             "again")
    for said in words:
        assert said in park, (
            f"_hold_park no longer publishes {said!r}; 5.8 quotes it")
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, ("**The published detail says what the hold is doing** "
                   "(#228)",) + tuple(f'"{w}"' for w in words),
          "5.8's re-point bullet")
    tree = _tree(SequenceEngine._hold_for_clear)
    gated = [n for n in ast.walk(tree)
             if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
             and isinstance(n.test.left, ast.Attribute)
             and n.test.left.attr == "_tracked_target"
             and isinstance(n.test.ops[0], ast.Is)
             and any(_publishes_detail(s) for s in n.body)
             and any(_is_self_call(c, "_hold_park")
                     for s in n.orelse for c in ast.walk(s))]
    assert gated, (
        "_hold_for_clear no longer publishes its opening detail only when "
        "the re-point put the mount on the held target; 5.8 says a stopped "
        "mount never claims the sky is checked (#228)")
    resumed = [block[i + 1] for block in _statements(tree)
               for i, s in enumerate(block[:-1])
               if isinstance(s, ast.Expr) and isinstance(s.value, ast.Await)
               and _is_self_call(s.value.value, "_enforce_tracking")]
    assert resumed and all(_publishes_detail(s) for s in resumed), (
        "_hold_for_clear no longer restores its opening detail only after "
        "_enforce_tracking has returned; 5.8 says it waits for tracking")
    # ONLY those two. The arms above would still pass beside a third publish
    # of the opening detail (before `_enforce_tracking`, or at the open over
    # a mount that is elsewhere), which is #228 again: so every statement
    # that publishes it is one of the two, and the open publishes it only
    # when the mount is not elsewhere.
    allowed = {id(s) for n in gated for s in n.body if _publishes_detail(s)}
    allowed |= {id(s) for s in resumed}
    stray = sorted(s.lineno for block in _statements(tree) for s in block
                   if _publishes_detail(s) and id(s) not in allowed)
    assert stray == [], (
        f"_hold_for_clear publishes its opening detail at line(s) {stray} of "
        f"its source, outside the re-point's success arm and the line after "
        f"_enforce_tracking; 5.8 says it is published only once the mount "
        f"is on the held target and tracking (#228)")
    opening = [k.value for c in _self_calls(tree, "_set_state")
               for k in c.keywords
               if k.arg == "detail" and isinstance(k.value, ast.IfExp)]
    only_on_target = bool(opening) and all(
        isinstance(v.test, ast.Name) and v.test.id == "elsewhere"
        and not any(isinstance(n, ast.Name) and n.id == "detail"
                    for n in ast.walk(v.body))
        for v in opening)
    assert only_on_target, (
        "_hold_for_clear's opening no longer keeps the opening detail for a "
        "mount that is already on the held target; 5.8 says a mount that is "
        "elsewhere is not said to be checking the sky (#228)")


def _flip_look(*, meridian_flip: bool, armed: bool) -> list[str]:
    """What one look's flip watch does at a flip point that is due, on an
    engine reduced to what ``_hold_flip_watch`` reads: the plan's flip
    switch, the latch, and four collaborators recorded as they are asked.
    With flips off ``_enforce_flip_owed`` is the real one, which returns at
    once; with flips on it is recorded too."""
    eng = object.__new__(SequenceEngine)   # no hub: one method is asked
    # ``_plan``, under the ``plan`` property: its setter resolves a policy
    # from the rig's config, which this look never reads.
    eng._plan = SimpleNamespace(meridian_flip=meridian_flip)
    eng._flip_armed = armed
    eng._hold_parked = None
    acts: list[str] = []

    async def due(target, now, *, ahead_s):
        return True

    async def flip(target, lead_s):
        acts.append("flip")

    async def park(kind, why):
        acts.append(f"park:{kind}")

    async def owed(target):
        acts.append("owed")

    eng._idle_flip_due = due
    eng._maybe_meridian_flip = flip
    eng._hold_park = park
    if meridian_flip:
        eng._enforce_flip_owed = owed
    asyncio.run(eng._hold_flip_watch(SimpleNamespace(name="T"), 60.0))
    return acts


def test_5_8_says_a_flips_off_hold_takes_no_flip_point_action_and_takes_none():
    """5.8 says that with flips off a cloud hold takes no flip-point action
    (H2 orchestrator ruling 1, owner list item 6): the frame loop never
    stops at the flip point when flips are off, and a mount that then stops
    at its own limit is found by the tracking read, whose resume-then-recover
    runs. ``_hold_flip_watch`` at a due flip point, with flips off and the
    latch as a flips-off run leaves it (never armed), flips nothing, stops
    nothing and is owed nothing. The control, flips on with the latch armed,
    shows the same harness sees each of those when they happen.

    RED under mutant "drop the ruling 1 sentence from 5.8":

        AssertionError: 5.8's watch bullet must say: '**With flips off, the
        hold takes no flip-point action** (H2 orchestrator ruling 1)'
        assert False

    RED under the code mutant "park at the flip point regardless" (H1's
    condition put back: ``_hold_flip_watch`` with ``if due and
    self._flip_armed:`` made ``if due and (not plan.meridian_flip or
    self._flip_armed):``):

        AssertionError: a flips-off hold acted at the flip point
        (['park:flip']); 5.8 says it takes no flip-point action
        assert ['park:flip'] == []
          Left contains one more item: 'park:flip'
          Use -v to get more diff
    """
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    _says(hold, ("**With flips off, the hold takes no flip-point action** "
                 "(H2 orchestrator ruling 1)", "resume-then-recover"),
          "5.8's watch bullet")
    _says(_line(_section("Still"), "6. "),
          ("with flips off, a cloud hold takes no flip-point action",),
          "owner list item 6")
    acts = _flip_look(meridian_flip=False, armed=False)
    assert acts == [], (
        f"a flips-off hold acted at the flip point ({acts}); 5.8 says it "
        f"takes no flip-point action")
    control = _flip_look(meridian_flip=True, armed=True)
    assert control == ["flip", "park:flip", "owed"], (
        f"control: with flips on and the latch armed the look should flip, "
        f"stop at the point and ask the owed-flip guard, and did {control}")


# --------------------------------- 6.17, the idle stop and the cooling wait

def test_6_17_says_the_first_stop_attempt_is_on_its_task_and_cooling_watches():
    """6.17 says the idle park-hold's stop is asked on its own task, the
    first attempt as well as every retry (#216), and that the run-start
    cooling wait watches a target ``start(tracking=)`` hands the run (#202),
    while the cooler gate's wait after a cloud hold still does not (#236);
    5.8's watch bullet says the first half too. ``_idle_park_hold`` talks to
    no device and starts ``_idle_stop_retry``, whose first act is the stop;
    ``_run`` waits for the cooler with ``watch=True``, ``_cool_and_wait``
    looks at the mount under ``if watch:``, and ``_cooler_gate`` does not
    pass it.

    Mutants as in the tests above.

    RED under mutant "6.17 without the first attempt":

        AssertionError: 6.17 must say: 'the first attempt as well as every
        retry (H2, #216)'
        assert False

    RED under mutant "6.17 without the cooling wait":

        AssertionError: 6.17 must say: 'The run-start cooling wait watches the
        same way (H2, #202)'
        assert False

    RED under the code mutant "the first attempt inline again"
    (``_idle_park_hold`` with ``await self._park_hold()`` put back before
    the task is created):

        AssertionError: _idle_park_hold talks to the mount itself
        (['_park_hold']); 6.17 says the first attempt is made on the retry's
        task (#216)
        assert ['_park_hold'] == []
          Left contains one more item: '_park_hold'
          Use -v to get more diff

    RED under the code mutant "the run's cooling wait does not watch"
    (``_run`` with ``watch=True`` made ``watch=False``):

        AssertionError: _run's cooling wait no longer watches the mount; 6.17
        says it does (#202)
        assert []
    """
    row = _line(_spec(), "| 6.17 |")
    _says(row, ("the first attempt as well as every retry (H2, #216)",
                "The run-start cooling wait watches the same way (H2, #202)",
                "#236"), "6.17")
    hold = _line(_section("5.8"),
                 "- **A cloud hold watches the mount on its own clock**")
    _says(hold, ("the first attempt and every retry", "#216"),
          "5.8's watch bullet")
    decide = {c.func.attr for c in _self_calls(_tree(
        SequenceEngine._idle_park_hold))}
    assert "_idle_stop_retry" in decide, (
        "_idle_park_hold no longer hands the stop to _idle_stop_retry; 6.17 "
        "says the stop runs on its own task")
    device = sorted(decide & {"_park_hold", "_stop_tracking_quietly",
                              "_tracking_now"})
    assert device == [], (
        f"_idle_park_hold talks to the mount itself ({device}); 6.17 says "
        f"the first attempt is made on the retry's task (#216)")
    body = _tree(SequenceEngine._idle_stop_retry).body[0].body
    first = [s for s in body if not (isinstance(s, ast.Expr)
                                     and isinstance(s.value, ast.Constant))][0]
    stops = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Await)
             and _is_self_call(first.value.value, "_park_hold"))
    assert stops, ("_idle_stop_retry no longer opens with the first stop "
                   "attempt; 6.17 says the task makes it")
    run = [c for c in _self_calls(_tree(SequenceEngine._run), "_cool_and_wait")
           if _keyword(c, "watch") is True]
    assert run, ("_run's cooling wait no longer watches the mount; 6.17 says "
                 "it does (#202)")
    looks = [n for n in ast.walk(_tree(SequenceEngine._cool_and_wait))
             if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
             and n.test.id == "watch"
             and any(_is_self_call(c, "_idle_hold_tick")
                     for s in n.body for c in ast.walk(s))]
    assert looks, ("_cool_and_wait no longer takes the idle look when asked "
                   "to watch; 6.17 says the run's cooling wait watches")
    gate = _self_calls(_tree(SequenceEngine._cooler_gate), "_cool_and_wait")
    unwatched = bool(gate) and all(_keyword(c, "watch") is not True
                                   for c in gate)
    assert unwatched, (
        "_cooler_gate's cooling wait now watches the mount, so the first half "
        "of #236 is fixed and 6.17's 'still do not watch' is stale")


# ------------------------------------------- 5.9 and 6.15, the ladder's stop

def test_5_9_and_6_15_say_abort_and_a_disarm_stop_the_ladder_and_it_says_so():
    """5.9 and 6.15 say Abort and a disarm stop ResumeArm's recovery ladder,
    and ``GET /api/sequence/resume-arm`` says it is running (#220): Abort
    stops it before its next step, cancels the step it is awaiting and
    disarms the session it was recovering; a PATCH that disarms or abandons
    that session, or arms another in its place, and a DELETE of it, stop it
    too, each naming that session so that withdrawing any other leaves it
    running; the route reports ``recovering`` and ``recovery``, whose step
    is a word from ``LADDER_STEPS``. 6.15 also says the other callers of
    ``engine.abort`` do not stop it yet (#238). ``api/app.py`` and
    ``ResumeArm`` do each.

    Mutants as in the tests above. The app.py mutants point this module's
    ``APP`` at a scratch copy with the mutation, in the test process only;
    app.py was never edited.

    RED under mutant "restore the 5.9 ladder sentence" (H1's, with no
    #220):

        AssertionError: 5.9 must say: '**Abort and a disarm stop the ladder,
        and the route says it is running** (H2, #220)'
        assert False

    RED under mutant "6.15 back to 'Unchanged'":

        AssertionError: 6.15 still says Abort is unchanged; since H2 it stops
        the recovery ladder (#220)
        assert not True

    The Revision 4 test below goes red under it too, at its premise: "Extra
    items in the left set: '6.15'".

    RED under the code mutant "abort ignores the ladder" (``sequence_abort``
    without its ``stop_recovery`` call):

        AssertionError: sequence_abort no longer stops the recovery ladder
        before it aborts the engine; 5.9 and 6.15 say Abort does (#220)
        assert 0 <= -1

    RED under the code mutant "the route does not say it is recovering"
    (``sequence_resume_arm`` without its ``recovering`` and ``recovery``
    keys):

        AssertionError: GET /api/sequence/resume-arm no longer reports
        "recovering"; 5.9 says it does (#220)
        assert False

    RED under the code mutant "stop_recovery never cancels the ladder"
    (``ResumeArm.stop_recovery`` with ``ladder.cancel()`` made ``pass``):

        AssertionError: ResumeArm.stop_recovery no longer cancels the step the
        ladder is awaiting; 5.9 says Abort does
        assert []

    The PATCH half first read only the disarm branch, so the abandon and
    the arm-another clauses were held by nothing here (their behaviour is
    test_resume_ladder_stops.py's). The verifier's mutants:

    RED under mutant "5.9 without abandon and arm-another" (the sentence cut
    to "A PATCH that disarms that session, and a DELETE of it"):

        AssertionError: 5.9 must say: 'A PATCH that disarms or abandons that
        session, or arms another in its place, and a DELETE of it, stop the
        ladder too'
        assert False

    RED under the code mutant "abandon does not stop the ladder" (app.py
    scratch copy, PATCH's condition reduced to ``body.auto_resume is
    False``):

        AssertionError: a PATCH that disarms or abandons the recovered
        session no longer stops the ladder; 5.9 says both do
        assert []

    RED under the code mutant "arming another does not stop the ladder"
    (app.py scratch copy, the singleton loop's ``stop_recovery`` call made
    a no-op):

        AssertionError: a PATCH that arms another session no longer stops the
        ladder recovering the one it disarms; 5.9 says it does
        assert []

    RED under the code mutant "delete stops the ladder for any session"
    (app.py scratch copy, DELETE's ``session_id=session_id`` removed):

        AssertionError: a PATCH or DELETE stops the ladder without naming its
        session (["resume_arm.stop_recovery('it was deleted while the
        recovery ladder was working, so the ladder stopped before its next
        step')"]), so withdrawing any other session stops it too; 5.9 says
        that leaves it running
        assert ["resume_arm.... next step')"] == []
    """
    s59 = _section("5.9")
    _says(s59, ("**Abort and a disarm stop the ladder, and the route says it "
                "is running** (H2, #220)", "`ResumeArm.stop_recovery`",
                "A PATCH that disarms or abandons that session, or arms "
                "another in its place, and a DELETE of it, stop the ladder "
                "too", "withdrawing any other session leaves it running",
                "`GET /api/sequence/resume-arm` reports `recovering`",
                "`recovery`", "`LADDER_STEPS`"), "5.9")
    row = _line(_spec(), "| 6.15 |")
    unchanged = "Unchanged" in row
    assert not unchanged, ("6.15 still says Abort is unchanged; since H2 it "
                           "stops the recovery ladder (#220)")
    _says(row, ("recovery ladder", "(H2, #220)", "#238"), "6.15")
    # The code halves, from app.py's route functions.
    abort = _app_function("sequence_abort")
    stop = abort.find("resume_arm.stop_recovery(")
    ends = abort.find("await engine.abort()")
    assert 0 <= stop < ends, (
        "sequence_abort no longer stops the recovery ladder before it aborts "
        "the engine; 5.9 and 6.15 say Abort does (#220)")
    disarms = "disarm=True" in abort[stop:ends]
    assert disarms, ("sequence_abort no longer disarms the session the ladder "
                     "was recovering; 6.15 says it does")
    # PATCH, from its syntax tree: the one condition that stops the ladder
    # for a disarm and for an abandon, and the singleton's loop that stops it
    # for the session another arm disarms. Each names its session, so that
    # withdrawing any other session leaves the ladder running.
    patch_tree = ast.parse(textwrap.dedent(_app_function("patch_session")))

    def _stops(node: ast.AST) -> list[ast.Call]:
        return [n for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "stop_recovery"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "resume_arm"]

    def _compares(test: ast.AST) -> set[str]:
        return {ast.unparse(n) for n in ast.walk(test)
                if isinstance(n, ast.Compare)}

    withdrawn = [n for n in ast.walk(patch_tree) if isinstance(n, ast.If)
                 and {"body.status == 'abandoned'",
                      "body.auto_resume is False"} <= _compares(n.test)
                 and any(_keyword_node(c, "session_id") is not None
                         for s in n.body for c in _stops(s))]
    assert withdrawn, ("a PATCH that disarms or abandons the recovered "
                       "session no longer stops the ladder; 5.9 says both do")
    singleton = [n for n in ast.walk(patch_tree) if isinstance(n, ast.For)
                 and isinstance(n.target, ast.Name)
                 and any(ast.unparse(_keyword_node(c, "session_id") or
                                     ast.Constant(None))
                         == f"{n.target.id}.id"
                         for c in _stops(n))]
    assert singleton, ("a PATCH that arms another session no longer stops "
                       "the ladder recovering the one it disarms; 5.9 says "
                       "it does")
    delete_tree = ast.parse(textwrap.dedent(_app_function("delete_session")))
    scoped = [c for t in (patch_tree, delete_tree) for c in _stops(t)]
    assert _stops(delete_tree), ("a DELETE of the recovered session no "
                                 "longer stops the ladder; 5.9 says it does")
    unscoped = [ast.unparse(c) for c in scoped
                if _keyword_node(c, "session_id") is None]
    assert unscoped == [], (
        f"a PATCH or DELETE stops the ladder without naming its session "
        f"({unscoped}), so withdrawing any other session stops it too; 5.9 "
        f"says that leaves it running")
    route = _app_function("sequence_resume_arm")
    for key in ('"recovering": resume_arm.recovering',
                '"recovery": resume_arm.recovery'):
        said = key in route
        assert said, (f"GET /api/sequence/resume-arm no longer reports "
                      f"{key.split(':')[0]}; 5.9 says it does (#220)")
    declared = ('@app.get("/api/sequence/resume-arm"'
                in APP.read_text(encoding="utf-8"))
    assert declared, "app.py no longer declares the route 5.9 names"
    for other in ("disconnect", "apply_profile", "activate_profile",
                  "connect_rig"):
        reached = "stop_recovery(" in _app_function(other)
        assert not reached, (
            f"{other} now stops the recovery ladder: #238 is fixed, so "
            f"6.15's 'do not stop it yet' is stale")
    # And ResumeArm: the stop cancels the awaited step and can disarm.
    stopper = _tree(ResumeArm.stop_recovery)
    cancels = [n for n in ast.walk(stopper) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "cancel"]
    assert cancels, ("ResumeArm.stop_recovery no longer cancels the step the "
                     "ladder is awaiting; 5.9 says Abort does")
    assert _self_calls(stopper, "_disarm_stopped"), (
        "ResumeArm.stop_recovery can no longer disarm; 6.15 says Abort does")
    assert isinstance(inspect.getattr_static(ResumeArm, "recovery"), property)
    steps = resume_arm_mod.LADDER_STEPS
    words = bool(steps) and all(isinstance(s, str) and s.isalpha()
                                for s in steps)
    assert words, ("LADDER_STEPS is no longer a vocabulary of words; 5.9 says "
                   "the route's step is one")


# --------------------------------------- the owner list's H2 rulings

#: Owner list items 5 to 9, and the H2 orchestrator ruling each records.
_H2_RULINGS = {5: 2, 6: 1, 7: 8, 8: 9, 9: 12}


def _session_file(**over) -> dict:
    """A session file as the store writes one, with ``over`` applied; a key
    given as None is removed."""
    from astrodeck.sequence.models import SequencePlan
    raw = session_mod.Session(
        status="dormant", plan=SequencePlan(name="p", targets=[])
    ).model_dump(mode="json")
    for key, value in over.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


def test_the_owner_list_records_the_h2_orchestrator_rulings():
    """The owner list records H2 orchestrator rulings 2, 1, 8, 9 and 12 as
    items 5 to 9, each labelled the orchestrator's and binding until the
    owner overturns it, with a note that their numbers are not Revision 2's
    (whose own rulings 1, 2, 8 and 9 are on other subjects, so the note is
    needed). Each ruling's text is held to the code where it names
    something the code keeps: rulings 1 and 2 by the 5.8 tests above, ruling
    8 by the catalogue's answer for the two spellings it quotes, ruling 9 by
    ``identity``, and ruling 12 by ``session._session_from_file``, the one
    place every ``SessionStore`` reader builds a session (its sweep, crash
    count and rewrite are test_session_without_status.py's).

    Mutants as in the tests above; the session mutant rebinds
    ``session._session_from_file`` in the test process only.

    RED under mutant "label ruling 12 an owner ruling":

        AssertionError: owner list item 9 must record H2 orchestrator ruling
        12, labelled as the orchestrator's
        assert False

    RED under mutant "drop the note on items 5 to 9":

        AssertionError: expected exactly one line starting "Items 5 to 9 are
        not the owner's rulings.", found 0
        assert 0 == 1
         +  where 0 = len([])

    RED under the code mutant "a file with no status loads" (the
    ``_stated_status`` refusal removed from ``_session_from_file``):

        Failed: DID NOT RAISE <class
        'astrodeck.sequence.session.SessionUnreadable'>

    RED under the code mutant "identity.STEP_PLACES = 3":

        AssertionError: owner list item 8 must say: '`STEP_PLACES` = 3'
        assert False

    The 3.3 step-id test above goes red under it too, as its own docstring
    records.

    RED under the code mutant "resolve_target answers the name as typed":

        AssertionError: 'M 31' and 'M31' resolve to ['M 31', 'M31']; owner list
        item 7 says they are one key
        assert ['M 31', 'M31'] == ['M31']
          At index 0 diff: 'M 31' != 'M31'
          Left contains one more item: 'M31'
          Use -v to get more diff

    The 3.3 name-key test above goes red under it too, as its own docstring
    records.
    """
    owner = _section("Still")
    for item, ruling in _H2_RULINGS.items():
        line = _line(owner, f"{item}. ")
        labelled = line.startswith(f"{item}. **H2 orchestrator ruling "
                                   f"{ruling}:")
        assert labelled, (f"owner list item {item} must record H2 "
                          f"orchestrator ruling {ruling}, labelled as the "
                          f"orchestrator's")
        _says(line, ("Binding until the owner overturns it.",),
              f"owner list item {item}")
    note = _line(owner, "Items 5 to 9 are not the owner's rulings.")
    _says(note, ("binding until the owner overturns it",
                 "not Revision 2's numbering"), "the note on items 5 to 9")
    numbers = {int(m.group(1)) for m in re.finditer(r"^### Ruling (\d+):",
                                                    _spec(), re.MULTILINE)}
    shared = sorted(numbers & set(_H2_RULINGS.values()))
    assert shared == [1, 2, 8, 9], (
        f"Revision 2's rulings share the numbers {shared} with H2's; the "
        f"note on items 5 to 9 names 1, 2, 8 and 9")
    # Ruling 8: the two spellings it quotes are one row, and a body moves.
    from astrodeck.flows import tonight
    bound = continuation.ADOPT_MAX_SEPARATION_ARCMIN
    _says(_line(owner, "7. "), ('"M 31" and "M31" are one key',
                                f"ADOPT's {bound:g} arcmin bound", "#229",
                                "#234"), "owner list item 7")
    rows = sorted({tonight.resolve_target(n).identity
                   for n in ("M 31", "M31")})
    assert rows == ["M31"], (f"'M 31' and 'M31' resolve to {rows}; owner "
                             f"list item 7 says they are one key")
    assert tonight.resolve_target("Jupiter").moves
    # Ruling 9: the places identity spells, and the three keys it quotes.
    _says(_line(owner, "8. "), (f"`STEP_PLACES` = {identity.STEP_PLACES}",
                                "0.0001 s, 0.0005 s and 0.001 s are three "
                                "keys"), "owner list item 8")
    keys = {identity.step_signature(frame_type="Bias", filter="",
                                    exposure_s=e, gain=100, binning=1)
            for e in (0.0001, 0.0005, 0.001)}
    assert len(keys) == 3, ("identity spells two of 0.0001 s, 0.0005 s and "
                            "0.001 s alike; owner list item 8 says three keys")
    # Ruling 12: a file that states no status is unreadable, and says why.
    _says(_line(owner, "9. "), ("#218", "reported as unreadable, with the "
                                "reason", "never swept by the boot sweep",
                                "never counted as a crash",
                                "never rewritten"), "owner list item 9")
    session_mod._session_from_file(_session_file(), "claims")   # premise
    with pytest.raises(session_mod.SessionUnreadable) as refused:
        session_mod._session_from_file(_session_file(status=None), "claims")
    assert refused.value.reason == session_mod.NO_STATUS


# ------------------------------------------------- Revision 4 and the status

def _blocks() -> list[tuple[str, str]]:
    """Every ``##`` and ``###`` heading with its own text, up to the next
    such heading, in order."""
    out: list[tuple[str, str]] = []
    name, body = None, []
    for line in _spec().splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if name is not None:
                out.append((name, "\n".join(body)))
            name, body = line.lstrip("#").strip(), []
        elif name is not None:
            body.append(line)
    if name is not None:
        out.append((name, "\n".join(body)))
    return out


def _label(heading: str) -> str:
    """The name a revision table gives a section: its number, or "owner
    list" for the list of what waits on the owner."""
    if heading.startswith("Still waiting on the owner"):
        return "owner list"
    first = heading.split(" ", 1)[0].rstrip(".")
    return first if re.fullmatch(r"\d+(\.\d+)?", first) else heading


def _rows(text: str) -> list[list[str]]:
    """The numbered rows of the revision table in ``text``, as cells."""
    return [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in text.splitlines() if re.match(r"^\| \d+ \|", line)]


def _where(cell: str) -> set[str]:
    """The sections a revision row's "Where" names, one per comma; "owner
    list item 5" is the owner list."""
    return {re.sub(r" item \d+$", "", part.strip())
            for part in cell.split(",")}


_H2 = re.compile(r"\bH2\b")


def test_revision_4_lists_every_section_h2_edited():
    """Revision 4's table has one row per section that carries H2's edits,
    and no other: every heading's text, and every row of the section 6
    table, that says "H2" is listed, and every section listed says it. So a
    later edit marked H2 in a section the table does not list goes red, and
    so does a row whose section carries none. Its preamble names the rows
    of Revision 3 that its own rows supersede, computed here from the two
    tables.

    Mutants as in the tests above.

    RED under mutant "drop the 6.15 row from Revision 4":

        AssertionError: Revision 4 lists ['3.3', '5.8', '5.9', '6.17', 'owner
        list']; the sections carrying H2's edits are ['3.3', '5.8', '5.9',
        '6.15', '6.17', 'owner list']
        assert ['3.3', '5.8'... 'owner list'] == ['3.3', '5.8'... 'owner list']
          At index 3 diff: '6.15' != '6.17'
          Left contains one more item: 'owner list'
          Use -v to get more diff

    RED under mutant "an H2 edit in 5.10 with no Revision 4 row":

        AssertionError: Revision 4 lists ['3.3', '5.8', '5.9', '6.15', '6.17',
        'owner list']; the sections carrying H2's edits are ['3.3', '5.10',
        '5.8', '5.9', '6.15', '6.17', 'owner list']
        assert ['3.3', '5.10..., '6.17', ...] == ['3.3', '5.8'... 'owner list']
          At index 1 diff: '5.10' != '5.8'
          Left contains one more item: 'owner list'
          Use -v to get more diff
    """
    blocks = _blocks()
    rev4 = [text for name, text in blocks if name.startswith("Revision 4")]
    assert len(rev4) == 1, "the spec must have one Revision 4"
    rows = _rows(rev4[0])
    listed = set().union(*(_where(r[1]) for r in rows))
    carried: set[str] = set()
    for name, text in blocks:
        if name.startswith("Revision 4"):
            continue
        if name.startswith("6. "):
            stray = [ln for ln in text.splitlines()
                     if _H2.search(ln) and not ln.startswith("| 6.")]
            assert stray == [], "section 6 carries H2 text outside its rows"
            carried |= {ln.split("|")[1].strip() for ln in text.splitlines()
                        if ln.startswith("| 6.") and _H2.search(ln)}
        elif _H2.search(text):
            carried.add(_label(name))
    # Premise: the scan finds the sections this round is known to have
    # edited, so an empty scan cannot agree with an empty table.
    assert {"3.3", "5.8", "5.9", "6.15", "6.17", "owner list"} <= carried
    assert sorted(carried) == sorted(listed), (
        f"Revision 4 lists {sorted(listed)}; the sections carrying H2's "
        f"edits are {sorted(carried)}")
    assert [r[0] for r in rows] == [str(i) for i in range(1, len(rows) + 1)]
    rev3 = [text for name, text in blocks if name.startswith("Revision 3")]
    superseded = [r[0] for r in _rows(rev3[0]) if _where(r[1]) & listed]
    assert superseded == ["2", "5", "6", "9"], superseded
    preamble = _line(rev4[0], "2026-09-24. A second hardening round")
    named = (f"Revision 3's rows {', '.join(superseded[:-1])} and "
             f"{superseded[-1]}")
    said = named in preamble
    assert said, f"Revision 4's preamble must say {named!r} are superseded"


def test_the_status_line_scopes_what_describes_the_code_as_built():
    """The status line said that wherever the body describes S0 and S1, it
    describes the code after the hardening round. Revision 3 edited the
    sections in its table and no others, so 5.6 step 3 still called the
    fabricated ``centered: True`` a live defect, 5.6 step 4 the silent
    dropped rotator, 5.6 step 9 the dither counter that spans targets, and
    section 9 the uuid4 ids, all four removed by S0 or S1. The line must
    scope its claim to the revisions' sections and say what "today" means
    elsewhere, and the Revision 3 preamble must not claim the whole body.
    Since H2 it names Revision 4 as well: the sections Revision 4 lists
    describe the code after H2, and those only Revision 3 lists are as H1
    left them.

    Found by the spec-fidelity review of the hardening round. The mutants
    ran against a scratch copy of the spec, as in the tests above.

    RED under mutant "restore the status line's blanket claim":

        AssertionError: the status line claims the whole body describes the
        code as built; Revision 3 edited only the sections in its table
        assert not True

    RED under mutant "restore 'This revision brings the body in line'":

        AssertionError: the Revision 3 preamble claims the whole body; it
        edited only the sections in its table
        assert not True

    RED under mutant "restore the status line's H1-only scoping" (the line
    as it stood before Revision 4):

        AssertionError: the status line must say: '(Revision 4)'
        assert False
    """
    status = _line(_spec(), "- Status:")
    blanket = "where the text below describes them, it describes the code" \
        in status
    assert not blanket, (
        "the status line claims the whole body describes the code as built; "
        "Revision 3 edited only the sections in its table")
    assert "The sections Revision 3 lists" in status
    assert "before S0" in status
    _says(status, ("(Revision 4)", "The sections Revision 4 lists describe "
                   "the code as it stands after the second hardening round "
                   "(H2)", "except in the sections Revision 4 lists too"),
          "the status line")
    preamble = _line(_spec(), "2026-09-24. S0 and S1 are built")
    whole = "brings the body in line" in preamble
    assert not whole, (
        "the Revision 3 preamble claims the whole body; it edited only the "
        "sections in its table")
    # The four sentences the status line warns about are still there, so the
    # warning is still needed; when a later revision rewrites them, drop the
    # examples from the status line and this check with them.
    s56 = _section("5.6")
    for stale in ('fabricated `{"centered": True', "Today that case is silent",
                  "Today the counter spans targets"):
        assert stale in s56, stale
    assert "Today every compile mints uuid4" in _spec()
