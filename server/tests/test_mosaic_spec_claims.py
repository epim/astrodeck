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

The third hardening round (H3) added Revision 5 and nine orchestrator
rulings, recorded as owner list items 10 to 18, and brought the claims above
that H3 made stale back into line: 5.7's pre-flip record, kept all run
(#237); 5.8's refused re-point that keeps holding (#240), its one setup per
acquisition (#241) and its re-point after any stop (#248); 5.9's ADOPT
measured against a body where it was (#234), asked off the event loop
(#249), the teardown routes that stop the ladder (#238) and the no-light
backoff (#251); 6.9's words-only hold and the route that withholds its
numbers (#233); 6.15's teardown routes (#238); and 6.17's two waits that now
watch (#236) and the run's end that completes the idle stop (#247). Two
checks hold the record itself: Revision 5's table against the sections that
carry H3's edits, and every ruling a server module or test cites by label
against the spec entry with that label (#239).

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


def _app_def(name: str) -> ast.AST:
    """The one function named ``name`` anywhere in ``api/app.py`` (a route
    inside ``create_app`` or a module-level helper), as a syntax tree, read
    through ``APP`` at call time as ``_app_function`` is."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    hits = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == name]
    assert len(hits) == 1, f"app.py has {len(hits)} functions named {name}"
    return hits[0]


def _app_constant(name: str) -> str:
    """The string ``api/app.py`` assigns to the module global ``name``, its
    adjacent literals joined as Python joins them."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    hits = [n.value for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name
                    for t in n.targets)]
    assert len(hits) == 1, f"app.py assigns {name} {len(hits)} times"
    return ast.literal_eval(hits[0])


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    """Every call under ``node`` whose callee is named ``attr``, as a plain
    name or as an attribute, in source order."""
    hits = [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == attr)
                 or (isinstance(n.func, ast.Name) and n.func.id == attr))]
    return sorted(hits, key=lambda n: (n.lineno, n.col_offset))


def _handlers(node: ast.Try, name: str) -> list[ast.ExceptHandler]:
    """The handlers of ``node`` that catch the exception class ``name``."""
    return [h for h in node.handlers
            if isinstance(h.type, ast.Name) and h.type.id == name]


# ------------------------------------------------------------------ control

#: One sentence per section that this round did NOT edit, so finding it
#: proves the section reader returned the real section and not an empty
#: string, against which every "not in" below would pass.
_ANCHORS = {
    "1.2": "The type id stays `target`, category SOURCE, label TARGET.",
    "3.3": "**Identity.** `NS_FLOWS` is a fixed uuid constant in `to_plan`.",
    "3.6": "`_migrate` **refuses** `schema_version > FLOW_SCHEMA`.",
    "5.6": "Every hop is a slew through the one motion path.",
    "5.7": "**Hysteresis.**",
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


def test_5_9_says_adopt_measures_a_body_where_it_was_and_off_the_loop():
    """5.9's ADOPT row says the bound is inclusive (a match exactly 10 arcmin
    apart is re-keyed); that a moving body matches on its canonical name,
    learned through ``tonight.resolve_target`` (#229), and is then measured
    between the OLD target and the body where the catalogue places it at the
    step's capture times, the first and the last frame of each night (H3
    orchestrator ruling 7, #234); and that the catalogue is asked off the
    event loop, before the store's write lock (#249). The code does each:
    ``continuation.adopt_matches`` maps two fields exactly
    ``ADOPT_MAX_SEPARATION_ARCMIN`` apart; maps a body step whose frames were
    taken on the body although tonight's body is 120 arcmin away, and asks
    the catalogue at exactly the night's first and last frame; refuses one
    whose last frame was 120 arcmin off the body, naming it; and, handed
    ``adopt_evidence``'s answer, asks the catalogue nothing. ``run_flow``
    builds that answer with ``asyncio.to_thread`` before its recovering
    check, and the locked section passes it and asks nothing itself.

    H2's version of this test held a body matched by name with no bound
    (#229). T4 of the third hardening round built ruling 7, which turned it
    red by design; it now holds the ruling.

    Each spec mutant ran in place on the spec, from a byte backup, which was
    restored byte-identical (sha256 compared) after each; each code mutant
    was rebound in the test process only (a pytest plugin), and an app.py
    mutant pointed this module's ``APP`` at a scratch copy. continuation.py
    and app.py were never edited.

    RED under mutant "5.9 back to H2's moving-body sentence" (the bound "does
    not apply to it"):

        AssertionError: 5.9's ADOPT row must say: '**A moving body matches on
        its canonical name, and the bound is measured against the body where it
        was**'
        assert False

    RED under mutant "5.9 without the #249 sentence":

        AssertionError: 5.9's ADOPT row must say: '**The catalogue is asked off
        the event loop** (#249)'
        assert False

    RED under the app.py mutant "evidence built inside the lock"
    (``adopt_matches(s, plan, evidence=adopt_evidence(s, plan))``):

        AssertionError: the locked section no longer matches on the evidence
        alone (catalogue calls ['adopt_evidence(s, plan)']); 5.9 says it asks
        the catalogue nothing (#249)
        assert (True and ['adopt_evidence(s, plan)'] == []
        Left contains one more item: 'adopt_evidence(s, plan)'
        Use -v to get more diff)

    RED under the app.py mutant "the catalogue asked on the loop" (``evidence =
    adopt_evidence(latest, plan)``, no to_thread):

        AssertionError: run_flow no longer asks adopt_evidence on a worker
        thread before its recovering check; 5.9 says the catalogue is asked off
        the loop, before the lock (#249)
        assert False

    RED under the code mutant "the body check skipped"
    (``continuation._pointing`` rebound to answer "within the bound"):

        AssertionError: a body step whose last frame was 120 arcmin off the
        body was not listed with the body and that separation: mapping
        ['0d834902cf21435b895aac61fc6ca6d9'], unmatched []
        assert ({'0d834902cf2...0b6d11e3daf')} == {}
        Left contains 1 more item:
        {'0d834902cf21435b895aac61fc6ca6d9':
        ('d052bfc796654da0a19a5e60416d19bb',
        '707501007d7a405c8f3700b6d11e3daf')}
        Use -v to get more diff)

    RED under the code mutant "the match ignores the evidence"
    (``adopt_matches`` rebound to drop ``evidence=``):

        AssertionError: adopt_matches asked the catalogue although it was
        handed adopt_evidence's answer (#249)

    RED under the code mutant "every frame sampled"
    (``continuation._capture_times`` rebound to every frame's time):

        AssertionError: ADOPT asked the catalogue at [1790000000.0,
        1790001800.0, 1790003600.0]; 5.9 says it checks the first and the last
        frame of each night, [1790000000.0, 1790003600.0]
        assert [1790000000.0... 1790003600.0] == [1790000000.0, 1790003600.0]
        At index 1 diff: 1790001800.0 != 1790003600.0
        Left contains one more item: 1790003600.0
        Use -v to get more diff
    """
    from astrodeck.flows import tonight
    from astrodeck.flows.tonight import NameResolution
    from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
    from astrodeck.sequence.session import Session, SessionFrame
    row = _line(_section("5.9"), "| the dormant session shares **no** step")
    bound = continuation.ADOPT_MAX_SEPARATION_ARCMIN
    _says(row, ("inclusive", f"a match exactly {bound:g} arcmin apart is "
                "re-keyed", "**A moving body matches on its canonical name, "
                "and the bound is measured against the body where it was**",
                "`tonight.resolve_target`", "#229", "#234",
                "H3 orchestrator ruling 7", "`_capture_times`",
                "the first and the last frame of each night", "`created_ts`",
                "A deep-sky or star name keeps the name as typed in its key, "
                "and the bound against tonight's target",
                "**The catalogue is asked off the event loop** (#249)",
                "`adopt_evidence`", "`asyncio.to_thread`",
                "before the store's write lock", "`adopt_matches(evidence=)`",
                "press ADOPT again", "`tonight.IDENTITY_WHEN`"),
          "5.9's ADOPT row")
    for stale in ("the bound does not apply to it", "is open as #234"):
        kept = stale in row
        assert not kept, (f"5.9's ADOPT row still says {stale!r}; H3 "
                          f"orchestrator ruling 7 measures a body step (#234)")

    t0 = 1_790_000_000.0                  # 2026-09-21, one night of frames

    def session(name, dec, stamps=((0.0, ""),)):
        step = ExposureStep(filter="L", exposure_s=60.0, count=5)
        t = Target(name=name, ra_hours=5.0, dec_deg=dec, steps=[step])
        s = Session(status="dormant", plan=SequencePlan(name="p",
                                                        targets=[t]))
        for ts, night in stamps:
            s.frames.append(SessionFrame(target_id=t.id, step_id=step.id,
                                         ts=ts, night=night))
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

    # A body, typed in another case, shot at dec 20 on one night; tonight it
    # is 120 arcmin away, at dec 22. ``where`` is the body at each instant.
    night = [(t0, "n1"), (t0 + 1800.0, "n1"), (t0 + 3600.0, "n1")]
    asked: list[float] = []

    def catalogue_for(where):
        def catalogue(name, when=None):
            if str(name).strip().lower() != "jupiter":
                return None
            if when is not None:
                asked.append(when)
            return NameResolution(
                ra_hours=5.0, dec_deg=22.0 if when is None else where(when),
                identity="Jupiter", moves=True)
        return catalogue

    on_it = catalogue_for(lambda when: 20.1)          # 6 arcmin off, always
    old, old_step = session("jupiter", 20.0, night)
    plan = tonight_plan("Jupiter", 22.0)
    assert list(continuation.adopt_matches(
        old, plan, resolve=on_it).mapping) == [old_step.id], (
        "a body step whose frames were taken on the body was not re-keyed")
    assert sorted(asked) == [t0, t0 + 3600.0], (
        f"ADOPT asked the catalogue at {sorted(asked)}; 5.9 says it checks "
        f"the first and the last frame of each night, {[t0, t0 + 3600.0]}")
    off_it = catalogue_for(lambda when: 22.0 if when > t0 + 3000 else 20.1)
    refused = continuation.adopt_matches(old, plan, resolve=off_it)
    reasons = [(u["step_id"], "Jupiter" in u["reason"],
                u["separation_arcmin"]) for u in refused.unmatched]
    assert (refused.mapping == {}
            and reasons == [(old_step.id, True, 120.0)]), (
        f"a body step whose last frame was 120 arcmin off the body was not "
        f"listed with the body and that separation: mapping "
        f"{list(refused.mapping)}, unmatched {reasons}")

    # #249: with the evidence in hand, the match asks the catalogue nothing.
    evidence = continuation.adopt_evidence(old, plan, resolve=on_it)

    def refuse(name, when=None):
        raise AssertionError("adopt_matches asked the catalogue although it "
                             "was handed adopt_evidence's answer (#249)")
    assert list(continuation.adopt_matches(
        old, plan, evidence=evidence, resolve=refuse).mapping) == [
        old_step.id]
    assert "IDENTITY_WHEN" in inspect.getsource(tonight.resolve_target), (
        "resolve_target no longer picks its row at tonight.IDENTITY_WHEN; 5.9 "
        "says to_plan, progress and ADOPT resolve identity at that instant")
    run = _app_def("run_flow")
    threaded = [c for c in _calls(run, "to_thread")
                if c.args and isinstance(c.args[0], ast.Name)
                and c.args[0].id == "adopt_evidence"]
    checked = _calls(run, "_refuse_while_resume_recovers")
    early = (bool(threaded) and bool(checked)
             and threaded[0].lineno < checked[0].lineno)
    assert early, ("run_flow no longer asks adopt_evidence on a worker thread "
                   "before its recovering check; 5.9 says the catalogue is "
                   "asked off the loop, before the lock (#249)")
    locked = _app_def("_continue_flow_session")
    matched = _calls(locked, "adopt_matches")
    asks = [ast.unparse(c) for c in _calls(locked, "adopt_evidence")
            + _calls(locked, "resolve_target")]
    handed = bool(matched) and all(_keyword_node(c, "evidence") is not None
                                   for c in matched)
    assert handed and asks == [], (
        f"the locked section no longer matches on the evidence alone "
        f"(catalogue calls {asks}); 5.9 says it asks the catalogue nothing "
        f"(#249)")


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
    angle. And a refused re-point keeps holding (#240, H3 orchestrator
    ruling 3): ``_hold_repoint_refusal`` asks only the gate's PROJECTION
    first, and when the slew gate's pier guard or the Sun check refuses the
    slew itself, it raises ``SlewRefused``, which ``_hold_repoint`` catches
    by type, stopping the hold (``_hold_park``) and returning its words,
    never raising; a bound's expiry is a plain SafetyAbort, re-raised, and
    ends the run under P0-2.

    Under H2 this test held the opposite: the pier guard and the Sun check
    raised the SafetyAbort any slew raises and ended the run, and a check
    that the gates sat before the stopping ``try`` stood for "#240 still
    open". T7 of the third hardening round fixed #240 by catching the
    refusal before that ``try``, where the H2 check could not see it, so the
    check stayed green over a fixed #240 (a test that could no longer fail);
    it now requires the ``except SlewRefused`` itself. The engine:
    ``_hold_for_clear``'s ``if elsewhere:`` arm sets ``_flip_armed`` False
    before it asks the refusal; ``_hold_repoint`` asks
    ``_arm_meridian_flip(target)`` under its own ``if elsewhere:``; it
    awaits ``tel.unpark()`` before ``tel.slew``; it asks the slew gate and
    the Sun check inside a ``try`` whose ``except SlewRefused`` stops and
    returns; the Sun check raises ``SlewRefused``; every refusal
    ``_enforce_mount_floor`` raises is a ``SlewRefused``; and the mount
    calls' ``except SafetyAbort`` re-raises.

    Mutants as in the tests above (scratch spec, rebound method), and the
    third round's engine mutants in place on engine.py from a byte backup,
    restored byte-identical (sha256 compared) after each.

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

    RED under mutant "5.8 says a pier-guard or Sun-cone refusal ends the run"
    (H2's #240 sentence put back):

        AssertionError: 5.8's re-point bullet must say: '**A refused re-point
        keeps holding** (#240, H3 orchestrator ruling 3)'
        assert False

    RED under the engine mutant "the refusal ends the run again" (``raise``
    first in ``_hold_repoint``'s ``except SlewRefused``):

        AssertionError: _hold_repoint's except SlewRefused no longer stops the
        hold and returns (raises ['raise']); 5.8 says a refused re-point keeps
        holding and never ends the run (#240)
        assert False

    RED under the engine mutant "caught as any SafetyAbort" (``except
    SlewRefused`` made ``except SafetyAbort``):

        AssertionError: _hold_repoint no longer asks the slew gate and the Sun
        check inside a try that catches SlewRefused by type; 5.8 says a refused
        re-point keeps holding (#240)
        assert []

    RED under the engine mutant "the Sun cone raises a plain SafetyAbort" (its
    ``raise SlewRefused(`` made ``raise SafetyAbort(``):

        AssertionError: the Sun check inside _hold_repoint raises
        ['SafetyAbort']; 5.8 says a Sun-cone refusal is a SlewRefused the hold
        keeps holding on
        assert ['SafetyAbort'] == ['SlewRefused']
        At index 0 diff: 'SafetyAbort' != 'SlewRefused'
        Use -v to get more diff

    RED under the engine mutant "the pier guard raises a plain SafetyAbort"
    (``_enforce_mount_floor``'s pier ``raise SlewRefused(`` made ``raise
    SafetyAbort(``):

        AssertionError: _enforce_mount_floor raises ['SafetyAbort',
        'SlewRefused']; 5.8 says every refusal of the slew gate's limits is a
        SlewRefused, which the hold catches
        assert ['SafetyAbort', 'SlewRefused'] == ['SlewRefused']
        At index 0 diff: 'SafetyAbort' != 'SlewRefused'
        Left contains one more item: 'SlewRefused'
        Use -v to get more diff

    RED under the engine mutant "a bound's expiry kept holding" (the mount
    calls' ``except SafetyAbort: raise`` made a return):

        AssertionError: _hold_repoint no longer re-raises a bound's expiry from
        its mount calls; 5.8 says a mount call past its bound still ends the
        run (P0-2)
        assert False
    """
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, ("unparking a parked mount first",
                   "The last target's flip latch is disarmed while the mount "
                   "is elsewhere, and the re-point arms B's own",
                   "When the gate's projection would refuse"),
          "5.8's re-point bullet")
    broad = "When the gate would refuse" in bullet
    assert not broad, (
        "5.8 says any refusal of the slew gate is asked first; only its "
        "projection is, and its pier guard and the Sun check refuse the slew "
        "itself")
    _says(bullet, ("**A refused re-point keeps holding** (#240, H3 "
                   "orchestrator ruling 3)", "The gate's pier guard and the "
                   "Sun check are not asked first", "`SlewRefused`",
                   "catches it by type", "never ends the run",
                   "A mount call past its bound", "(P0-2)"),
          "5.8's re-point bullet")
    stale = "that ends the run (#240)" in bullet
    assert not stale, ("5.8 still says a pier-guard or Sun-cone refusal ends "
                       "the run; since H3 it keeps holding (#240)")
    # The pre-ask is still the projection alone: the pier side and the Sun
    # are the gate's to know, and a refusal of either is caught from the
    # slew (below), not asked for first.
    asked = {n.func.attr for n in ast.walk(
        _tree(SequenceEngine._hold_repoint_refusal))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    pier_or_sun = sorted(asked & {"_check_solar", "destination_pier_side",
                                  "_enforce_mount_floor", "_safety_gate"})
    assert pier_or_sun == [], (
        f"_hold_repoint_refusal now asks {pier_or_sun} first; 5.8 says the "
        f"gate's pier guard and the Sun check are not asked first")
    repoint = _tree(SequenceEngine._hold_repoint)
    guarded = [t for t in ast.walk(repoint) if isinstance(t, ast.Try)
               and _handlers(t, "SlewRefused")
               and {"_safety_gate", "_check_solar"} <= {
                   c.func.attr for s in t.body for c in ast.walk(s)
                   if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Attribute)}]
    assert guarded, (
        "_hold_repoint no longer asks the slew gate and the Sun check inside "
        "a try that catches SlewRefused by type; 5.8 says a refused re-point "
        "keeps holding (#240)")
    handler = _handlers(guarded[0], "SlewRefused")[0]
    stops = any(_is_self_call(c, "_hold_park") for c in ast.walk(handler))
    returns = any(isinstance(s, ast.Return) for s in handler.body)
    raises = [ast.unparse(n) for n in ast.walk(handler)
              if isinstance(n, ast.Raise)]
    holds = stops and returns and raises == []
    assert holds, (
        f"_hold_repoint's except SlewRefused no longer stops the hold and "
        f"returns (raises {raises}); 5.8 says a refused re-point keeps "
        f"holding and never ends the run (#240)")
    sun = [n for n in ast.walk(guarded[0])
           if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
           and isinstance(n.exc.func, ast.Name)]
    sun_kinds = sorted({n.exc.func.id for n in sun})
    assert sun_kinds == ["SlewRefused"], (
        f"the Sun check inside _hold_repoint raises {sun_kinds}; 5.8 says a "
        f"Sun-cone refusal is a SlewRefused the hold keeps holding on")
    floor = _tree(SequenceEngine._enforce_mount_floor)
    kinds = sorted({n.exc.func.id for n in ast.walk(floor)
                    if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
                    and isinstance(n.exc.func, ast.Name)})
    assert kinds == ["SlewRefused"], (
        f"_enforce_mount_floor raises {kinds}; 5.8 says every refusal of the "
        f"slew gate's limits is a SlewRefused, which the hold catches")
    assert issubclass(engine_mod.SlewRefused, engine_mod.SafetyAbort)
    mount = [h for t in ast.walk(repoint) if isinstance(t, ast.Try)
             for h in _handlers(t, "SafetyAbort")]
    reraised = bool(mount) and all(
        len(h.body) == 1 and isinstance(h.body[0], ast.Raise)
        and h.body[0].exc is None for h in mount)
    assert reraised, (
        "_hold_repoint no longer re-raises a bound's expiry from its mount "
        "calls; 5.8 says a mount call past its bound still ends the run "
        "(P0-2)")
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
    detail after a re-point from elsewhere is published only under ``if why
    is None``, where ``why`` is the re-point's own answer (H2 asked ``if
    self._tracked_target is target``, which T7 of the third hardening round
    changed for #248, turning this red by design), with the stop in the
    other arm, and
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
    target:`` made ``if True:``; H2's record):

        AssertionError: _hold_for_clear no longer publishes its opening detail
        only when the re-point put the mount on the held target; 5.8 says a
        stopped mount never claims the sky is checked (#228)
        assert []

    RED under the engine mutant "the opening detail published whatever the
    re-point answered" (``if why is None:`` before the publish made ``if
    True:``):

        AssertionError: _hold_for_clear no longer publishes its opening detail
        only when the re-point put the mount on the held target; 5.8 says a
        stopped mount never claims the sky is checked (#228)
        assert []

    RED under the engine mutant "the opening detail keyed on _tracked_target
    again" (that ``if why is None:`` made H2's ``if self._tracked_target is
    target:``):

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
    # THE RE-POINT'S OWN ANSWER decides, since H3 (#248): ``why`` is what
    # ``_hold_repoint(target, elsewhere=True)`` returned, None once the mount
    # is on the target and tracking. H2 asked ``self._tracked_target is
    # target``, which a mount stopped on this very target already is, so a
    # refused re-point of it would have published the opening detail over a
    # stopped mount (#228 by the #248 door).
    answered = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and [ast.unparse(t) for t in n.targets] == ["why"]
                and isinstance(n.value, ast.Await)
                and _is_self_call(n.value.value, "_hold_repoint")
                and _keyword(n.value.value, "elsewhere") is True]
    gated = [n for n in ast.walk(tree)
             if isinstance(n, ast.If) and ast.unparse(n.test) == "why is None"
             and any(_publishes_detail(s) for s in n.body)
             and any(_is_self_call(c, "_hold_park")
                     for s in n.orelse for c in ast.walk(s))
             and answered and answered[0].lineno < n.lineno]
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


# ------------- 5.8, one setup per acquisition and a re-point after any stop

#: 5.8's own sentences for H3 orchestrator rulings 4 and 6. Owner list items
#: 13 and 15 record the rulings; these are the section's account of what the
#: engine does, which the test below holds to the engine.
_ONE_SETUP = "**One setup per acquisition** (#241, H3 orchestrator ruling 4)"
_AFTER_STOP = ("**After any stop of the mount, a hold re-points** (#248, H3 "
               "orchestrator ruling 6)")
#: What ``_acquisition_behind_gate`` is asked as, wherever a release decides.
_BEHIND = "self._acquisition_behind_gate is target"


def _asks(node: ast.AST, text: str) -> bool:
    """Is there a comparison that reads ``text`` anywhere under ``node``?"""
    return any(isinstance(n, ast.Compare) and ast.unparse(n) == text
               for n in ast.walk(node))


def _assigns(node: ast.AST, target: str,
             value: str | None = None) -> list[ast.Assign]:
    """The assignments under ``node`` to ``target`` (spelled as the source
    spells it, ``self._x``), of ``value`` when one is given."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Assign)
            and [ast.unparse(t) for t in n.targets] == [target]
            and (value is None or ast.unparse(n.value) == value)]


def _within(block: list[ast.stmt], node: ast.AST) -> bool:
    return any(node is n for stmt in block for n in ast.walk(stmt))


def _marks_its_gate(fn) -> bool:
    """Does ``fn`` name its target as the acquisition behind its pre-slew
    gate: ``self._acquisition_behind_gate = target`` directly before a
    ``try`` whose body awaits ``self._safety_gate(context="slew", ...)`` and
    whose ``finally`` puts the flag back?"""
    for block in _statements(_tree(fn)):
        for stmt, nxt in zip(block, block[1:]):
            if not (isinstance(stmt, ast.Assign) and ast.unparse(stmt)
                    == "self._acquisition_behind_gate = target"
                    and isinstance(nxt, ast.Try)):
                continue
            gated = any(_keyword(c, "context") == "slew" for s in nxt.body
                        for c in _self_calls(s, "_safety_gate"))
            restored = any(_assigns(s, "self._acquisition_behind_gate")
                           for s in nxt.finalbody)
            if gated and restored:
                return True
    return False


def _setups_not_held_back(fn) -> tuple[int, list[int]]:
    """How many ``self._setup_target(...)`` calls ``fn`` makes, and the
    source lines of those a setup waiting behind the gate does NOT hold
    back. A call is held back when it sits in the body of an ``if`` that
    asks ``not behind``, where ``behind`` is read from `_BEHIND`, or in the
    ``else`` (an ``elif`` included) of an ``if`` that asks `_BEHIND` itself
    and not in that ``if``'s own body."""
    tree = _tree(fn)
    read = any(_asks(a.value, _BEHIND) for a in _assigns(tree, "behind"))
    ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)]

    def held(call: ast.Call) -> bool:
        for n in ifs:
            not_behind = read and any(
                isinstance(u, ast.UnaryOp) and isinstance(u.op, ast.Not)
                and isinstance(u.operand, ast.Name) and u.operand.id == "behind"
                for u in ast.walk(n.test))
            if not_behind and _within(n.body, call):
                return True
            if (_asks(n.test, _BEHIND) and _within(n.orelse, call)
                    and not _within(n.body, call)):
                return True
        return False

    calls = _self_calls(tree, "_setup_target")
    return len(calls), [c.lineno for c in calls if not held(c)]


def test_5_8_says_one_setup_per_acquisition_and_a_repoint_after_any_stop():
    """5.8 says a hold opened by a setup's pre-slew gate returns, on release,
    to the setup it interrupted, which makes the one acquisition, and that a
    safety pause and a roof reopen such a gate opens follow the same rule
    (#241, H3 orchestrator ruling 4); and that a stop the engine made since
    the mount was last pointed counts as elsewhere, so a hold re-points
    instead of resuming in place, with ``_mount_stopped_since`` set by every
    stop and cleared only by a fresh pointing (#248, H3 orchestrator ruling
    6). The module docstring and Revision 5 say this file holds every claim
    H3 edited, and until this test none held these two sentences: owner list
    items 13 and 15 name the rulings, and the published-detail test above
    reaches #248 only through the re-point's answer.

    The code half, from the syntax trees, since the docstrings of these
    methods name every call the claims are about:

    * ``_setup_target`` and ``_hold_repoint`` set ``_acquisition_behind_gate``
      to the target directly before the ``try`` that awaits their
      ``_safety_gate(context="slew")``, and put it back in its ``finally``;
    * ``_hold_for_clear``'s release runs ``_setup_target`` only under ``not
      behind``, ``behind`` read from ``self._acquisition_behind_gate is
      target``; the safety pause's release (``_park_hold_pause``) and the
      roof reopen (``_await_safe_and_reopen``) run it only in the ``elif`` of
      an ``if`` that asks the same. Each makes at least one such call, so
      "every call is held back" cannot pass on none;
    * ``_hold_for_clear`` counts the mount as elsewhere when
      ``self._mount_stopped_since is not None``, at the open and in the
      stopped-tracking branch, which re-points (``_hold_repoint(...,
      elsewhere=True)``); ``_stop_tracking_quietly``, the stop primitive
      under the pause, the roof close, the hold's own stop and the idle
      stop, records the stop (``_note_mount_stopped``), which writes the
      field; a setup's slew and a successful re-point clear it.

    Mutants: the spec read from a scratch copy through this module's
    ``SPEC``, and the engine methods rebound, each compiled from its own
    source with one edit, all inside the test process only (a pytest
    plugin); the spec and ``engine.py`` were never written, and their
    SHA-256 was the same before and after the runs.

    RED under mutant "5.8 without the #241 sentence" (its bold head
    deleted):

        AssertionError: 5.8's re-point bullet must say: '**One setup per
        acquisition** (#241, H3 orchestrator ruling 4)'
        assert False

    RED under the code mutant "the release runs a setup behind the gate"
    (``_hold_for_clear`` with ``and not behind):`` made ``):``):

        AssertionError: _hold_for_clear runs _setup_target at line(s) [347]
        of its source whether or not a setup waits behind the gate; 5.8
        says the hold never runs _setup_target for it (#241)
        assert [347] == []

    RED under the code mutant "the setup does not mark its gate"
    (``_setup_target`` with ``self._acquisition_behind_gate = target`` made
    ``self._acquisition_behind_gate = behind``):

        AssertionError: _setup_target no longer names its target as the
        acquisition behind its pre-slew gate; 5.8 says a hold that gate opens
        returns to it (#241)
        assert False

    RED under the code mutant "the pause re-acquires behind the gate"
    (``_park_hold_pause`` with ``and self._acquisition_behind_gate is
    target):`` made ``and False):``):

        AssertionError: _park_hold_pause runs _setup_target at line(s) [67]
        of its source whether or not a setup waits behind the gate; 5.8
        says a safety pause follows the same rule (#241)
        assert [67] == []

    RED under the code mutant "a stopped mount is not elsewhere" (the
    ``or self._mount_stopped_since is not None`` taken out of
    ``_hold_for_clear``'s ``elsewhere``):

        AssertionError: _hold_for_clear no longer counts a mount the engine
        stopped as elsewhere at the open; 5.8 says a hold re-points after any
        stop (#248)
        assert False

    RED under the code mutant "the stop is not recorded"
    (``_stop_tracking_quietly`` without ``self._note_mount_stopped()``):

        AssertionError: _stop_tracking_quietly no longer records the stop;
        5.8 says every stop the engine makes sets _mount_stopped_since (#248)
        assert False

    RED under the code mutant "a re-point leaves the mount counted as
    stopped" (``_hold_repoint`` without ``self._mount_stopped_since =
    None``):

        AssertionError: _hold_repoint no longer clears
        _mount_stopped_since; 5.8 says a fresh pointing clears it (#248)
        assert False

    Unchanged under the controls "an unrelated edit in section 7" (its
    Engine heading reworded) and "the release's log line reworded"
    (``_hold_for_clear`` with "returning to the setup the hold" made "going
    back to the setup the hold"): 1 passed on each.
    """
    bullet = _line(_section("5.8"), _REPOINT)
    _says(bullet, (_ONE_SETUP, "`_acquisition_behind_gate`",
                   "The hold never runs `_setup_target` for it",
                   "A safety pause and a roof reopen that such a gate opens "
                   "follow the same rule", "#263", _AFTER_STOP,
                   "`_mount_stopped_since`", "only a fresh pointing clears "
                   "it"), "5.8's re-point bullet")
    stale = "its release goes through `_setup_target`" in bullet
    assert not stale, ("5.8 still says every hold's release goes through "
                       "_setup_target; since H3 one opened by a setup's gate "
                       "returns to that setup (#241)")
    # #241, the gates: each asker names the acquisition behind it.
    for fn in (SequenceEngine._setup_target, SequenceEngine._hold_repoint):
        marked = _marks_its_gate(fn)
        assert marked, (
            f"{fn.__name__} no longer names its target as the acquisition "
            f"behind its pre-slew gate; 5.8 says a hold that gate opens "
            f"returns to it (#241)")
    # #241, the releases: none runs a setup while one waits behind the gate.
    for fn, which in ((SequenceEngine._hold_for_clear, "the hold never runs "
                       "_setup_target for it"),
                      (SequenceEngine._park_hold_pause, "a safety pause "
                       "follows the same rule"),
                      (SequenceEngine._await_safe_and_reopen, "a roof reopen "
                       "follows the same rule")):
        made, loose = _setups_not_held_back(fn)
        assert made >= 1, (
            f"premise: {fn.__name__} re-acquires through _setup_target when "
            f"no setup waits behind the gate")
        assert loose == [], (
            f"{fn.__name__} runs _setup_target at line(s) {loose} of its "
            f"source whether or not a setup waits behind the gate; 5.8 says "
            f"{which} (#241)")
    # #248: a mount the engine stopped is elsewhere, at the open and when
    # the tracking read finds it stopped, and that branch re-points.
    hold = _tree(SequenceEngine._hold_for_clear)
    opened = any(_asks(a.value, "self._mount_stopped_since is not None")
                 for a in _assigns(hold, "elsewhere"))
    assert opened, ("_hold_for_clear no longer counts a mount the engine "
                    "stopped as elsewhere at the open; 5.8 says a hold "
                    "re-points after any stop (#248)")
    repointed = [n for n in ast.walk(hold) if isinstance(n, ast.If)
                 and ast.unparse(n.test)
                 == "self._mount_stopped_since is not None"
                 and any(_keyword(c, "elsewhere") is True for s in n.body
                         for c in _self_calls(s, "_hold_repoint"))]
    assert repointed, ("_hold_for_clear no longer re-points a mount the "
                       "engine stopped when its tracking read finds it "
                       "stopped; 5.8 says it re-points instead of resuming "
                       "in place (#248)")
    recorded = bool(_self_calls(_tree(SequenceEngine._stop_tracking_quietly),
                                "_note_mount_stopped"))
    assert recorded, ("_stop_tracking_quietly no longer records the stop; "
                      "5.8 says every stop the engine makes sets "
                      "_mount_stopped_since (#248)")
    written = bool(_assigns(_tree(SequenceEngine._note_mount_stopped),
                            "self._mount_stopped_since"))
    assert written, ("_note_mount_stopped no longer writes "
                     "_mount_stopped_since; 5.8 names the field (#248)")
    for fn in (SequenceEngine._setup_target, SequenceEngine._hold_repoint):
        cleared = bool(_assigns(_tree(fn), "self._mount_stopped_since",
                                "None"))
        assert cleared, (f"{fn.__name__} no longer clears "
                         f"_mount_stopped_since; 5.8 says a fresh pointing "
                         f"clears it (#248)")


# --------------------------------- 6.17, the idle stop and the cooling wait

def test_6_17_says_the_first_stop_attempt_is_on_its_task_and_cooling_watches():
    """6.17 says the idle park-hold's stop is asked on its own task, the
    first attempt as well as every retry (#216); that the run-start cooling
    wait watches a target ``start(tracking=)`` hands the run (#202); that
    the two waits which did not watch now do, the cooler gate's wait when a
    cloud hold releases and the camera-lane wait at run start (H3, #236);
    and that a run's end completes a stop the idle watch decided, bounded by
    ``IDLE_STOP_FINISH_S`` (H3, #247, H3 orchestrator ruling 5). 5.8's watch
    bullet says the first half too. ``_idle_park_hold`` talks to no device
    and starts ``_idle_stop_retry``, whose first act is the stop; ``_run``
    waits for the cooler with ``watch=True`` and ``_cool_and_wait`` looks at
    the mount under ``if watch:``; ``_hold_for_clear`` asks ``_cooler_gate``
    with ``watch=True``; ``_await_camera_lane`` takes ``_idle_hold_tick``
    in its polling loop; ``_run`` completes the stop in a ``finally`` and
    ``abort`` completes it too (``_finish_idle_stop``), which polls the task
    against ``IDLE_STOP_FINISH_S`` and never awaits it.

    Under H2 the last check held "the cooler gate's wait still does not
    watch", and T2 of the third hardening round reported it stayed green
    over the fix: it read only a constant ``watch=True`` in ``_cooler_gate``,
    which now forwards its own ``watch``. It now holds the fix where the
    watch is asked for, the hold's call.

    Mutants as in the tests above; the third round's engine mutants in place
    on engine.py from a byte backup, restored byte-identical (sha256
    compared) after each.

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

    RED under mutant "6.17 back to 'Two waits still do not watch'":

        AssertionError: 6.17 must say: '**The two waits that did not watch now
        do** (H3, #236)'
        assert False

    RED under mutant "6.17 without the #247 sentence":

        AssertionError: 6.17 must say: "**A run's end completes a stop the idle
        watch decided** (H3, #247, H3 orchestrator ruling 5)"
        assert False

    RED under the engine mutant "the hold's cooler gate unwatched"
    (``_hold_for_clear``'s ``watch=True`` made ``watch=False``):

        AssertionError: a cloud hold's release no longer waits for the cooler
        with the mount watched (_hold_for_clear's _cooler_gate(...,
        watch=True), passed on to _cool_and_wait); 6.17 says it does (#236)
        assert ([])

    RED under the engine mutant "the gate drops its watch" (``_cooler_gate``
    passes ``watch=False``):

        AssertionError: a cloud hold's release no longer waits for the cooler
        with the mount watched (_hold_for_clear's _cooler_gate(...,
        watch=True), passed on to _cool_and_wait); 6.17 says it does (#236)
        assert ([<ast.Call object at 0x0000024A6D7F0FD0>] and [])

    RED under the engine mutant "the camera-lane wait unwatched" (its
    ``_idle_hold_tick`` made ``pass``):

        AssertionError: _await_camera_lane no longer takes the idle look while
        it polls; 6.17 says the camera-lane wait watches (#236)
        assert []

    RED under the engine mutant "the run's end cancels the stop again"
    (``_finish_idle_stop(...)`` in ``_run``'s finally made
    ``_cancel_idle_stop_retry()``):

        AssertionError: _run's finally or abort no longer completes the idle
        stop (_finish_idle_stop); 6.17 says a run's end completes it (#247)
        assert ([])

    RED under the engine mutant "the wind-down awaits the task" (the bounded
    poll made ``await task``):

        AssertionError: _complete_idle_stop no longer polls the first attempt
        against IDLE_STOP_FINISH_S without awaiting it (awaits ['await task']);
        6.17 says the wind-down's wait is bounded and shielded (#247)
        assert (False)

    RED under the code mutant "engine.IDLE_STOP_FINISH_S = 120.0" (rebound in
    the test process):

        AssertionError: 6.17 must say: '`IDLE_STOP_FINISH_S` (120 s)'
        assert False
    """
    row = _line(_spec(), "| 6.17 |")
    finish = f"`IDLE_STOP_FINISH_S` ({engine_mod.IDLE_STOP_FINISH_S:g} s)"
    lane_s = f"up to {engine_mod._CAMERA_LANE_WAIT_S:g} s"
    _says(row, ("the first attempt as well as every retry (H2, #216)",
                "The run-start cooling wait watches the same way (H2, #202)",
                "**The two waits that did not watch now do** (H3, #236)",
                "`_cooler_gate(..., watch=True)`", "`_await_camera_lane`",
                lane_s,
                "**A run's end completes a stop the idle watch decided** "
                "(H3, #247, H3 orchestrator ruling 5)",
                "`_finish_idle_stop`", finish,
                "whether or not the wind-down parks"), "6.17")
    stale = "Two waits still do not watch" in row
    assert not stale, ("6.17 still says two waits do not watch; since H3 "
                       "both do (#236)")
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
    # #236, first half: the hold's release asks the cooler gate to watch,
    # and the gate passes its ``watch`` on to the wait that looks.
    released = [c for c in _self_calls(_tree(SequenceEngine._hold_for_clear),
                                       "_cooler_gate")
                if _keyword(c, "watch") is True]
    forwarded = [c for c in _self_calls(_tree(SequenceEngine._cooler_gate),
                                        "_cool_and_wait")
                 if ast.unparse(_keyword_node(c, "watch") or
                                ast.Constant(None)) == "watch"]
    assert released and forwarded, (
        "a cloud hold's release no longer waits for the cooler with the "
        "mount watched (_hold_for_clear's _cooler_gate(..., watch=True), "
        "passed on to _cool_and_wait); 6.17 says it does (#236)")
    # #236, second half: every poll of the camera-lane wait takes the look.
    lane = [n for n in ast.walk(_tree(SequenceEngine._await_camera_lane))
            if isinstance(n, ast.While)
            and any(_is_self_call(c, "_idle_hold_tick")
                    for s in n.body for c in ast.walk(s))]
    assert lane, ("_await_camera_lane no longer takes the idle look while it "
                  "polls; 6.17 says the camera-lane wait watches (#236)")
    # #247: the run's end and an abort complete the stop, polled against
    # the bound and never awaited, so a cancel cannot land on the task.
    ending = [t for t in ast.walk(_tree(SequenceEngine._run))
              if isinstance(t, ast.Try)
              and any(_is_self_call(c, "_finish_idle_stop")
                      for s in t.finalbody for c in ast.walk(s))]
    aborted = _self_calls(_tree(SequenceEngine.abort), "_finish_idle_stop")
    assert ending and aborted, (
        "_run's finally or abort no longer completes the idle stop "
        "(_finish_idle_stop); 6.17 says a run's end completes it (#247)")
    work = _tree(SequenceEngine._complete_idle_stop)
    bounded = any(isinstance(n, ast.Name) and n.id == "IDLE_STOP_FINISH_S"
                  for n in ast.walk(work))
    awaited = [ast.unparse(n) for n in ast.walk(work)
               if isinstance(n, ast.Await) and isinstance(n.value, ast.Name)]
    assert bounded and awaited == [], (
        f"_complete_idle_stop no longer polls the first attempt against "
        f"IDLE_STOP_FINISH_S without awaiting it (awaits {awaited}); 6.17 "
        f"says the wind-down's wait is bounded and shielded (#247)")


# ------------------------------------------- 5.9 and 6.15, the ladder's stop

def test_5_9_and_6_15_say_abort_and_a_disarm_stop_the_ladder_and_it_says_so():
    """5.9 and 6.15 say Abort and a disarm stop ResumeArm's recovery ladder,
    and ``GET /api/sequence/resume-arm`` says it is running (#220): Abort
    stops it before its next step, cancels the step it is awaiting and
    disarms the session it was recovering; a PATCH that disarms or abandons
    that session, or arms another in its place, and a DELETE of it, stop it
    too, each naming that session so that withdrawing any other leaves it
    running; the route reports ``recovering`` and ``recovery``, whose step
    is a word from ``LADDER_STEPS``. ``api/app.py`` and ``ResumeArm`` do
    each.

    SINCE H3 (#238): the routes that tear the rig down stop the ladder too.
    ``/api/disconnect`` stops it with its session disarmed before anything
    else; profile apply, profile activate and ``/api/connect/rig`` refuse
    unforced with 409 ``running`` while it runs (``_teardown_busy_detail``)
    and, forced, stop it with the session disarmed; each then waits for it
    (``_wait_for_the_ladder``, ``ResumeArm.wait_stopped`` bounded by
    ``LADDER_STOP_WAIT_S``) before it aborts or tears down. And the refused
    start's sentence names the Monitor, not the route (#246). H2's version
    ended by holding "the other callers of ``engine.abort`` do not stop it
    yet"; T1 of the third round fixed #238 and turned it red by design, and
    T8 changed the sentence, which H2's checks did not read.

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

    RED under mutant "6.15 back to 'do not stop it yet'" (H2's #238 sentence
    put back):

        AssertionError: 6.15 must say: '**So do the routes that tear the rig
        down** (H3, #238)'
        assert False

    RED under mutant "5.9 back to 'the 409 sends the operator there'":

        AssertionError: 5.9 must say: 'the Monitor shows the re-centring and
        the step it is on'
        assert False

    RED under the app.py mutant "disconnect without the ladder stop":

        AssertionError: disconnect no longer stops the recovery ladder with its
        session disarmed and waits for it before it aborts or tears the rig
        down; 6.15 says it does (#238)
        assert False

    RED under the app.py mutant "a forced apply does not wait for the ladder":

        AssertionError: apply_profile no longer stops the recovery ladder with
        its session disarmed and waits for it before it aborts or tears the rig
        down; 6.15 says it does (#238)
        assert False

    RED under the app.py mutant "a forced connect keeps the session armed"
    (``disarm=False``):

        AssertionError: connect_rig no longer stops the recovery ladder with
        its session disarmed and waits for it before it aborts or tears the rig
        down; 6.15 says it does (#238)
        assert False

    RED under the app.py mutant "activate refuses nothing" (its unforced
    ``_teardown_busy_detail`` refusal removed):

        AssertionError: activate_profile no longer refuses unforced while the
        ladder runs; 6.15 says profile apply, activate and a rig connect refuse
        and a disconnect does not
        assert False == True

    RED under the app.py mutant "the refused start names the route again":

        AssertionError: the resume_recovering sentence no longer says 'the
        Monitor shows the re-centring and the step it is on'; 5.9 quotes it
        assert 'the Monitor shows the re-centring and the step it is on' in
        'Auto-resume is re-centring the mount after a restart and will start
        its armed session when that is done; GET /api/seq...esume off, which
        stops the re-centring before its next step (an abort does the same);
        start again once it has stopped.'
    """
    s59 = _section("5.9")
    _says(s59, ("**Abort and a disarm stop the ladder, and the route says it "
                "is running** (H2, #220)", "`ResumeArm.stop_recovery`",
                "A PATCH that disarms or abandons that session, or arms "
                "another in its place, and a DELETE of it, stop the ladder "
                "too", "withdrawing any other session leaves it running",
                "`GET /api/sequence/resume-arm` reports `recovering`",
                "`recovery`", "`LADDER_STEPS`",
                "the Monitor shows the re-centring and the step it is on",
                "**The routes that tear the rig down stop it too** (H3, "
                "#238)"), "5.9")
    sent = "the 409's sentence sends the operator there" in s59
    assert not sent, ("5.9 still says the 409 sends the operator to the "
                      "route; since #246 it names the Monitor")
    row = _line(_spec(), "| 6.15 |")
    unchanged = "Unchanged" in row
    assert not unchanged, ("6.15 still says Abort is unchanged; since H2 it "
                           "stops the recovery ladder (#220)")
    wait_s = resume_arm_mod.LADDER_STOP_WAIT_S
    _says(row, ("recovery ladder", "(H2, #220)",
                "**So do the routes that tear the rig down** (H3, #238)",
                "`/api/disconnect`", "409 `running`",
                "`ResumeArm.wait_stopped`",
                f"`LADDER_STOP_WAIT_S` = {wait_s:g} s", "The disarm is "
                "deliberate", "#256", "#257"), "6.15")
    stale = "neither stop the ladder nor see it yet" in row
    assert not stale, ("6.15 still says the teardown routes do not stop the "
                       "ladder; since H3 they do (#238)")
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
    # #238: each route that tears the rig down stops the ladder, disarms
    # its session, and waits for it before it aborts or tears anything
    # down; the three that can be forced refuse unforced while it runs.
    for other in ("disconnect", "apply_profile", "activate_profile",
                  "connect_rig"):
        tree = _app_def(other)
        stops = [c for c in _calls(tree, "stop_recovery")
                 if _keyword(c, "disarm") is True]
        waits = [n for n in ast.walk(tree) if isinstance(n, ast.Await)
                 and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name)
                 and n.value.func.id == "_wait_for_the_ladder"]
        after = [c.lineno for name in ("abort", "disconnect_all",
                                       "apply_profile", "connect_profile_id",
                                       "connect_rigspec")
                 for c in _calls(tree, name)
                 if isinstance(c.func, ast.Attribute)
                 and isinstance(c.func.value, ast.Name)
                 and c.func.value.id in ("engine", "hub")]
        ordered = (bool(stops) and bool(waits) and bool(after)
                   and stops[0].lineno < waits[0].lineno < min(after))
        assert ordered, (
            f"{other} no longer stops the recovery ladder with its session "
            f"disarmed and waits for it before it aborts or tears the rig "
            f"down; 6.15 says it does (#238)")
        asked = _calls(tree, "_teardown_busy_detail")
        refuses = bool(asked) and asked[0].lineno < stops[0].lineno
        forceable = other != "disconnect"
        assert refuses == forceable, (
            f"{other} {'no longer refuses' if forceable else 'now refuses'} "
            f"unforced while the ladder runs; 6.15 says profile apply, "
            f"activate and a rig connect refuse and a disconnect does not")
    waited = _app_def("_wait_for_the_ladder")
    gives_up = (bool(_calls(waited, "wait_stopped"))
                and "'code': 'running'" in ast.unparse(waited))
    assert gives_up, ("_wait_for_the_ladder no longer waits on "
                      "ResumeArm.wait_stopped and answers 409 running; 6.15 "
                      "says a ladder that does not stop is refused")
    bound = [n for n in ast.walk(_tree(ResumeArm.wait_stopped))
             if isinstance(n, ast.Name) and n.id == "LADDER_STOP_WAIT_S"]
    assert bound, ("ResumeArm.wait_stopped is no longer bounded by "
                   "LADDER_STOP_WAIT_S; 6.15 names that bound")
    # The refused start's sentence names the screen, not the route (#246).
    sentence = _app_constant("_RESUME_RECOVERING")
    monitor = "the Monitor shows the re-centring and the step it is on"
    assert monitor in sentence, (
        f"the resume_recovering sentence no longer says {monitor!r}; 5.9 "
        f"quotes it")
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


def test_the_owner_list_records_the_h2_orchestrator_rulings(tmp_path,
                                                            monkeypatch):
    """The owner list records H2 orchestrator rulings 2, 1, 8, 9 and 12 as
    items 5 to 9, each labelled the orchestrator's and binding until the
    owner overturns it, with a note that their numbers are not Revision 2's
    (whose own rulings 1, 2, 8 and 9 are on other subjects, so the note is
    needed). Each ruling's text is held to the code where it names
    something the code keeps: rulings 1 and 2 by the 5.8 tests above, ruling
    8 by the catalogue's answer for the two spellings it quotes and by
    ``tonight.MOVING_KINDS``, ruling 9 by ``identity``, and ruling 12 by
    ``session._session_from_file``, the one place every ``SessionStore``
    reader builds a session (its sweep, crash count and rewrite are
    test_session_without_status.py's), by ``SessionStore.list``, which shows
    such a file as an unreadable row, and by the DELETE route, which
    tolerates it (#242; test_unreadable_sessions_listed_and_deletable.py
    holds their behaviour).

    SINCE H3. Item 7 says "a moving body (a planet, the Moon, a comet or a
    satellite)" where it said "a solar-system body", because the code has
    always exempted every row of ``tonight.MOVING_KINDS`` (H3 orchestrator
    ruling 9), and it names ruling 7's pointing check where it said "what
    that trusts is #234": T4 built that check, which made the old item stale
    by design. Item 9 no longer says every reader "reports" such a file,
    which overclaimed: ``load`` reports it and the scans skip it, and since
    #242 the list shows it and DELETE removes it. T8 built #242 and reported
    that this test stayed green over it, because it read only
    ``_session_from_file``; the list and the route are read now.

    Mutants as in the tests above; the session mutants rebind a
    ``session`` function in the test process only.

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

    RED under mutant "drop 'satellite' from item 7":

        AssertionError: owner list item 7 must say: 'a moving body (a planet,
        the Moon, a comet or a satellite)'
        assert False

    RED under mutant "item 9 back to 'every reader reports it'":

        AssertionError: owner list item 9 must say: '`load` reports it as
        unreadable, with the reason'
        assert False

    RED under the code mutant "tonight.MOVING_KINDS without satellites"
    (rebound in the test process):

        AssertionError: tonight.MOVING_KINDS is ['comet', 'solar_system'];
        owner list item 7 says a moving body is a planet, the Moon, a comet or
        a satellite
        assert ['comet', 'solar_system'] == ['comet', 'sa...solar_system']
        At index 1 diff: 'solar_system' != 'satellite'
        Right contains one more item: 'solar_system'
        Use -v to get more diff

    RED under the code mutant "an unreadable file is not listed"
    (``session._unreadable_row`` rebound to answer None):

        AssertionError: GET /api/sessions' rows are [('session', 'dormant',
        None)]; owner list item 9 says a file with no status is listed as an
        unreadable row with its reason
        assert [('session', 'dormant', None)] == [('session', ...s no status')]
        Right contains one more item: ('unstated', 'unreadable', 'it has no
        status')
        Use -v to get more diff

    RED under the app.py mutant "DELETE refuses an unreadable file again" (its
    first ``except SessionUnreadable`` removed):

        AssertionError: DELETE /api/sessions/{id} catches SessionUnreadable at
        1 of its two reads; owner list item 9 says it removes such a file
        assert 1 == 2
        +  where 1 = len([<ast.ExceptHandler object at 0x0000022D46819610>])
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
    # Ruling 8: the two spellings it quotes are one row, and every moving
    # kind is a body, satellites included (H3 orchestrator ruling 9).
    from astrodeck.flows import tonight
    bound = continuation.ADOPT_MAX_SEPARATION_ARCMIN
    item7 = _line(owner, "7. ")
    _says(item7, ('"M 31" and "M31" are one key',
                  f"ADOPT's {bound:g} arcmin bound", "#229",
                  "a moving body (a planet, the Moon, a comet or a "
                  "satellite)", "H3 orchestrator ruling 9",
                  "H3 orchestrator ruling 7"), "owner list item 7")
    for stale in ("what that trusts is #234", "solar-system body"):
        kept = stale in item7
        assert not kept, (f"owner list item 7 still says {stale!r}; H3 "
                          f"orchestrator rulings 7 and 9 changed it")
    rows = sorted({tonight.resolve_target(n).identity
                   for n in ("M 31", "M31")})
    assert rows == ["M31"], (f"'M 31' and 'M31' resolve to {rows}; owner "
                             f"list item 7 says they are one key")
    assert tonight.resolve_target("Jupiter").moves
    kinds = sorted(tonight.MOVING_KINDS)
    assert kinds == ["comet", "satellite", "solar_system"], (
        f"tonight.MOVING_KINDS is {kinds}; owner list item 7 says a moving "
        f"body is a planet, the Moon, a comet or a satellite")
    # Ruling 9: the places identity spells, and the three keys it quotes.
    _says(_line(owner, "8. "), (f"`STEP_PLACES` = {identity.STEP_PLACES}",
                                "0.0001 s, 0.0005 s and 0.001 s are three "
                                "keys"), "owner list item 8")
    keys = {identity.step_signature(frame_type="Bias", filter="",
                                    exposure_s=e, gain=100, binning=1)
            for e in (0.0001, 0.0005, 0.001)}
    assert len(keys) == 3, ("identity spells two of 0.0001 s, 0.0005 s and "
                            "0.001 s alike; owner list item 8 says three keys")
    # Ruling 12: a file that states no status is unreadable, and says why;
    # the list shows it as an unreadable row, and DELETE can remove it.
    item9 = _line(owner, "9. ")
    _says(item9, ("#218", "`load` reports it as unreadable, with the reason",
                  "never swept by the boot sweep", "never counted as a crash",
                  "never rewritten", "#242", "`GET /api/sessions` lists it "
                  "as an unreadable row with its reason",
                  "`DELETE /api/sessions/{id}` removes it"),
          "owner list item 9")
    over = "Every `SessionStore` reader treats it the same way" in item9
    assert not over, ("owner list item 9 still says every reader reports "
                      "such a file; the scans skip it")
    session_mod._session_from_file(_session_file(), "claims")   # premise
    with pytest.raises(session_mod.SessionUnreadable) as refused:
        session_mod._session_from_file(_session_file(status=None), "claims")
    assert refused.value.reason == session_mod.NO_STATUS
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    (tmp_path / "sessions").mkdir()
    for stem, raw in (("stated", _session_file()),
                      ("unstated", _session_file(status=None))):
        (tmp_path / "sessions" / f"{stem}.json").write_text(
            json.dumps(raw), encoding="utf-8")
    listed = sorted((r["id"] if r["status"] == "unreadable" else "session",
                     r["status"], r.get("unreadable"))
                    for r in SessionStore().list())
    assert listed == [("session", "dormant", None),
                      ("unstated", "unreadable", session_mod.NO_STATUS)], (
        f"GET /api/sessions' rows are {listed}; owner list item 9 says a "
        f"file with no status is listed as an unreadable row with its reason")
    tolerated = [h for t in ast.walk(_app_def("delete_session"))
                 if isinstance(t, ast.Try)
                 for h in _handlers(t, "SessionUnreadable")]
    assert len(tolerated) == 2, (
        f"DELETE /api/sessions/{{id}} catches SessionUnreadable at "
        f"{len(tolerated)} of its two reads; owner list item 9 says it "
        f"removes such a file")


# --------------------------------------- the owner list's H3 rulings

#: Owner list items 10 to 18: the H3 orchestrator ruling each records, and
#: the issue it decides (ruling 9, satellites count, was decided on the
#: spec's own wording and has none).
_H3_RULINGS = {10: (1, "#233"), 11: (2, "#237"), 12: (3, "#240"),
               13: (4, "#241"), 14: (5, "#247"), 15: (6, "#248"),
               16: (7, "#234"), 17: (8, "#251"), 18: (9, None)}


def test_the_owner_list_records_the_h3_orchestrator_rulings():
    """The owner list records H3 orchestrator rulings 1 to 9 as items 10 to
    18, each labelled the orchestrator's, binding until the owner overturns
    it and naming the issue it decides, with a closing note that they are
    not the owner's and that their numbers are neither Revision 2's nor
    H2's. Which numbers collide is computed, not typed: all nine with
    Revision 2's rulings, and 1, 2, 8 and 9 with H2's, which the note names.
    Where an item quotes a value, the value is taken from the code: the
    idle-stop finish bound (ruling 5), ADOPT's bound (ruling 7), the retry
    clocks and the no-light words (ruling 8), and the moving kinds (ruling
    9). The behaviour of each ruling is its section's test above and its
    own suite's.

    Mutants as in the tests above.

    RED under mutant "label H3 ruling 5 an owner ruling":

        AssertionError: owner list item 14 must record H3 orchestrator ruling
        5, labelled as the orchestrator's
        assert False

    RED under mutant "drop the note on items 10 to 18":

        AssertionError: expected exactly one line starting "Items 10 to 18 are
        not the owner's rulings.", found 0
        assert 0 == 1
        +  where 0 = len([])

    RED under the code mutant "engine.IDLE_STOP_FINISH_S = 120.0" (rebound in
    the test process):

        AssertionError: owner list item 14 must say: '`IDLE_STOP_FINISH_S` (120
        s)'
        assert False

    RED under the code mutant "resume_arm.NO_LIGHT_RETRY_S = 1800.0" (rebound
    in the test process):

        AssertionError: owner list item 17 must say: '`NO_LIGHT_RETRY_S` (30
        min)'
        assert False

    RED under the code mutant "tonight.MOVING_KINDS without satellites"
    (rebound in the test process):

        AssertionError: tonight.MOVING_KINDS no longer holds satellites; owner
        list item 18 says a satellite counts as a moving body
        assert 'satellite' in frozenset({'comet', 'solar_system'})
    """
    from astrodeck.flows import tonight
    from astrodeck.solve import light
    owner = _section("Still")
    for item, (ruling, issue) in _H3_RULINGS.items():
        line = _line(owner, f"{item}. ")
        labelled = line.startswith(f"{item}. **H3 orchestrator ruling "
                                   f"{ruling}:")
        assert labelled, (f"owner list item {item} must record H3 "
                          f"orchestrator ruling {ruling}, labelled as the "
                          f"orchestrator's")
        _says(line, ("Binding until the owner overturns it.",)
              + ((issue,) if issue else ()), f"owner list item {item}")
    note = _line(owner, "Items 10 to 18 are not the owner's rulings.")
    h3 = {ruling for ruling, _issue in _H3_RULINGS.values()}
    revision_2 = {int(m.group(1)) for m in re.finditer(
        r"^### Ruling (\d+):", _spec(), re.MULTILINE)}
    both = sorted(h3 & set(_H2_RULINGS.values()))
    assert h3 <= revision_2 and both == [1, 2, 8, 9], (
        f"premise: every H3 number is one of Revision 2's, and H3 shares "
        f"{both} with H2")
    _says(note, ("binding until the owner overturns it", "(Revision 5)",
                 "not Revision 2's numbering, nor H2's",
                 "H2 orchestrator rulings "
                 f"{', '.join(map(str, both[:-1]))} and {both[-1]}"),
          "the note on items 10 to 18")
    finish = f"`IDLE_STOP_FINISH_S` ({engine_mod.IDLE_STOP_FINISH_S:g} s)"
    bound = (f"`ADOPT_MAX_SEPARATION_ARCMIN` "
             f"({continuation.ADOPT_MAX_SEPARATION_ARCMIN:g} arcmin)")
    retry = (f"`RETRY_INTERVAL_S` "
             f"({resume_arm_mod.RETRY_INTERVAL_S / 60:g} min)")
    hourly = (f"`NO_LIGHT_RETRY_S` "
              f"({resume_arm_mod.NO_LIGHT_RETRY_S / 60:g} min)")
    for item, phrases in (
            (10, ("`hold.site_detail`", "`CAP_VIEW_SITE_DERIVED`")),
            (11, ("`setdefault`",)),
            (12, ("`SlewRefused`", "(P0-2)")),
            (13, ("`_setup_target`",)),
            (14, (finish,)),
            (15, ("`_mount_stopped_since`",)),
            (16, (bound,)),
            (17, (retry, hourly, f'"{light.NO_LIGHT_WORDS}"',
                  "`solve/light.py`")),
            (18, ("`tonight.MOVING_KINDS`", "satellite"))):
        _says(_line(owner, f"{item}. "), phrases, f"owner list item {item}")
    assert "satellite" in tonight.MOVING_KINDS, (
        "tonight.MOVING_KINDS no longer holds satellites; owner list item 18 "
        "says a satellite counts as a moving body")


# ------------------------------------------------ 5.7, the pre-flip record

def test_5_7_says_the_pre_flip_record_is_kept_all_run_and_the_engine_does():
    """5.7 says the pre-flip side is written with ``setdefault`` only and
    kept all run, a flip included (#237, H3 orchestrator ruling 2): at the
    first sighting east of the meridian, or by a flip the flip gate measured
    before any sighting (the #222 case), and cleared at run start and
    nowhere else. The engine writes ``_pre_flip_side`` in exactly two ways:
    a ``setdefault``, in ``_enforce_flip_owed`` and ``_maybe_meridian_flip``,
    and a whole new dict in ``__init__`` and ``start``. Nothing pops,
    deletes, assigns an item, clears or updates it, and H2's closed-cycle
    mark is gone.

    Mutants: the spec in place from a byte backup; engine.py in place from a
    byte backup; each restored byte-identical (sha256 compared) afterwards.

    RED under mutant "drop the record paragraph from 5.7":

        AssertionError: expected exactly one line starting '**The record is
        kept all run**', found 0
        assert 0 == 1
        +  where 0 = len([])

    RED under the engine mutant "the flip gate clears the record again"
    (``self._pre_flip_side.pop(key, None)`` after its setdefault):

        AssertionError: _pre_flip_side is written by {'rebind': ['__init__',
        'start'], 'setdefault': ['_enforce_flip_owed', '_maybe_meridian_flip'],
        'pop': ['_maybe_meridian_flip']}; 5.7 says only a setdefault in
        _enforce_flip_owed and _maybe_meridian_flip writes it, and only run
        start clears it (#237)
        assert {'pop': ['_ma...ridian_flip']} == {'rebind': ['...ridian_flip']}
        Omitting 2 identical items, use -vv to show
        Left contains 1 more item:
        {'pop': ['_maybe_meridian_flip']}
        Use -v to get more diff

    RED under the engine mutant "a sighting refreshes the record"
    (``_enforce_flip_owed``'s setdefault made ``self._pre_flip_side[key] =
    side``):

        AssertionError: _pre_flip_side is written by {'rebind': ['__init__',
        'start'], 'setdefault': ['_maybe_meridian_flip'], 'item':
        ['_enforce_flip_owed']}; 5.7 says only a setdefault in
        _enforce_flip_owed and _maybe_meridian_flip writes it, and only run
        start clears it (#237)
        assert {'item': ['_e...ridian_flip']} == {'rebind': ['...ridian_flip']}
        Omitting 1 identical items, use -vv to show
        Differing items:
        {'setdefault': ['_maybe_meridian_flip']} != {'setdefault':
        ['_enforce_flip_owed', '_maybe_meridian_flip']}
        Left contains 1 more item:
        {'item': ['_enforce_flip_owed']}
    """
    text = _section("5.7")
    record = _line(text, "**The record is kept all run**")
    _says(record, ("(#237, H3 orchestrator ruling 2)", "`setdefault`",
                   "`_enforce_flip_owed`", "`_maybe_meridian_flip`",
                   "#222", "a flip included",
                   "cleared at run start and nowhere else"), "5.7")
    engine_src = inspect.getsource(engine_mod)
    assert "_flip_cycle_closed" not in engine_src, (
        "engine.py has a closed-cycle mark again; 5.7 says nothing marks a "
        "target so its record is not written again (#237)")
    # One parse of the module, every method of the class by name: a write
    # is booked to the method it is written in.
    module = ast.parse(engine_src)
    cls = [n for n in module.body if isinstance(n, ast.ClassDef)
           and n.name == "SequenceEngine"]
    assert len(cls) == 1
    writers: dict[str, list[str]] = {}
    for fn in cls[0].body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = fn.name
        for n in ast.walk(fn):
            use = None
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr == "_pre_flip_side"
                    and n.func.attr != "get"):
                use = n.func.attr
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                                ast.Delete)):
                targets = (n.targets if isinstance(n, (ast.Assign, ast.Delete))
                           else [n.target])
                for t in targets:
                    if (isinstance(t, ast.Attribute)
                            and t.attr == "_pre_flip_side"):
                        use = "rebind"
                    elif (isinstance(t, ast.Subscript)
                          and isinstance(t.value, ast.Attribute)
                          and t.value.attr == "_pre_flip_side"):
                        use = "item"
            if use is not None:
                writers.setdefault(use, []).append(name)
    found = {k: sorted(v) for k, v in writers.items()}
    assert found == {"setdefault": ["_enforce_flip_owed",
                                    "_maybe_meridian_flip"],
                     "rebind": ["__init__", "start"]}, (
        f"_pre_flip_side is written by {found}; 5.7 says only a setdefault "
        f"in _enforce_flip_owed and _maybe_meridian_flip writes it, and "
        f"only run start clears it (#237)")


# ------------------------------------ 6.9, a resume-arm hold's numbers

def test_6_9_says_hold_lines_are_words_and_the_route_withholds_the_numbers():
    """6.9 says the resume-arm hold's reason and the engine's hold,
    idle-watch and flip-watch lines are words (#233, H3 orchestrator ruling
    1); that ``GET /api/sequence/resume-arm`` carries the numbers in
    ``hold.site_detail``, which ``_redact_resume_arm_for`` removes, absent
    and not null, for a principal without ``CAP_VIEW_SITE_DERIVED``; that
    the slew gate's numbers ride ``SlewRefused.site_detail``; that no log
    line keeps them; and that the flip-point detail no longer counts its
    minutes. The code: the route passes its payload through the redactor; a
    viewer, and no principal at all, get the hold without ``site_detail``
    and an operator with it, while the service's own hold keeps it; the
    gate's refusal and its verdict carry ``site_detail``; and every detail
    ``_wait_for_flip_point`` publishes is a constant.

    Mutants: the spec in place from a byte backup; code rebound in the test
    process only (a pytest plugin); engine.py in place from a byte backup;
    each restored byte-identical (sha256 compared) afterwards.

    RED under mutant "6.9 without the #233 sentence":

        AssertionError: 6.9 must say: '(H3, #233, H3 orchestrator ruling 1)'
        assert False

    RED under mutant "6.9 back to 'The night log file keeps everything.'":

        AssertionError: 6.9 still says 'The night log file keeps everything.';
        since H3 (#233) it is not so
        assert not True

    RED under the code mutant "the redactor passes site_detail to anyone"
    (``_redact_resume_arm_for`` rebound to return its payload):

        AssertionError: hold.site_detail reaches {'viewer': True, 'nobody':
        True, 'operator': True}; 6.9 says only a holder of
        CAP_VIEW_SITE_DERIVED reads it
        assert {'nobody': Tr...viewer': True} == {'nobody': Fa...iewer': False}
        Omitting 1 identical items, use -vv to show
        Differing items:
        {'nobody': True} != {'nobody': False}
        {'viewer': True} != {'viewer': False}
        Use -v to get more diff

    RED under the code mutant "the redactor strips the live hold" (rebound to
    pop ``site_detail`` from the hold it was handed):

        AssertionError: hold.site_detail reaches {'viewer': False, 'nobody':
        False, 'operator': False}; 6.9 says only a holder of
        CAP_VIEW_SITE_DERIVED reads it
        assert {'nobody': Fa...iewer': False} == {'nobody': Fa...iewer': False}
        Omitting 2 identical items, use -vv to show
        Differing items:
        {'operator': False} != {'operator': True}
        Use -v to get more diff

    RED under the app.py mutant "the route skips the redactor":

        AssertionError: GET /api/sequence/resume-arm no longer passes its
        payload through _redact_resume_arm_for; 6.9 says it withholds
        site_detail
        assert []
        +  where [] = _calls(<ast.AsyncFunctionDef object at
        0x0000022322ED9710>, '_redact_resume_arm_for')

    RED under the engine mutant "the flip-point detail counts minutes again"
    (its constant made an f-string):

        AssertionError: _wait_for_flip_point publishes a computed detail
        (["f'holding for the meridian flip point ({0:.0f} min)'"]); 6.9 says
        the flip-point detail no longer carries its minutes
        assert ([<ast.Call object at 0x000002B3297FA810>] and ["f'holding
        f...0:.0f} min)'"] == []
        Left contains one more item: "f'holding for the meridian flip point
        ({0:.0f} min)'"
        Use -v to get more diff)

    AND RESUMEARM FILES THE GATE'S NUMBERS (H3 integration, the T11
    verifier's required follow-up). 6.9 said ResumeArm filed the gate's
    message, now words, as a slew-limit hold's detail, which gave an
    operator the words twice and the numbers never; ``_recover`` now files
    ``SlewRefused.site_detail`` and 6.9 says so.

    RED under mutant "6.9 back to 'files the gate's message, now words'"
    (the spec in place from a byte backup):

        AssertionError: 6.9 must say: "ResumeArm files them as a slew-limit hold's `hold.site_detail`"
        assert False

    RED under the resume_arm.py mutant "the limits branch files the words"
    (``getattr(e, "site_detail", None) or str(e)`` made ``str(e)``, in a
    scratch copy of the tree):

        AssertionError: ResumeArm._recover no longer reads the refusal's site_detail; 6.9 says ResumeArm files the gate's numbers as a slew-limit hold's hold.site_detail
        assert []
    """
    from astrodeck.api import redact as redact_mod
    from astrodeck.auth.capabilities import CAP_VIEW_SITE_DERIVED
    from astrodeck.auth.principal import principal_for_role
    row = _line(_spec(), "| 6.9 |")
    _says(row, ("(H3, #233, H3 orchestrator ruling 1)", "`hold.site_detail`",
                "`_redact_resume_arm_for`", "absent, not null",
                "`CAP_VIEW_SITE_DERIVED`", "`SlewRefused.site_detail`",
                "test_resume_arm_hold_is_site_free.py",
                "test_engine_logs_carry_no_site_numbers.py",
                "no longer carries its minutes",
                "ResumeArm files them as a slew-limit hold's "
                "`hold.site_detail`"), "6.9")
    for stale in ('"holding for the meridian flip point (N min)"',
                  "The night log file keeps everything.",
                  "ResumeArm files the gate's message, now words"):
        kept = stale in row
        assert not kept, (f"6.9 still says {stale!r}; since H3 (#233) it is "
                          f"not so")
    hold = {"reason": "M42 is below its start floor; not slewing yet",
            "site_detail": "M42 is at A deg, below its F deg start floor"}
    payload = {"armed": None, "hold": hold, "recovering": False,
               "recovery": None}
    viewer, operator = (principal_for_role(r) for r in ("viewer", "operator"))
    ruled = (not viewer.has(CAP_VIEW_SITE_DERIVED)
             and operator.has(CAP_VIEW_SITE_DERIVED))
    assert ruled, ("premise: the owner's ruling of 2026-09-22 gives an "
                   "operator the site-derived view and a viewer not")
    seen = {who: redact_mod._redact_resume_arm_for(payload, p)["hold"]
            for who, p in (("viewer", viewer), ("nobody", None),
                           ("operator", operator))}
    kept = {who: "site_detail" in h for who, h in seen.items()}
    assert kept == {"viewer": False, "nobody": False, "operator": True}, (
        f"hold.site_detail reaches {kept}; 6.9 says only a holder of "
        f"CAP_VIEW_SITE_DERIVED reads it")
    assert seen["viewer"]["reason"] == hold["reason"] and "site_detail" in hold
    route = _app_def("sequence_resume_arm")
    assert _calls(route, "_redact_resume_arm_for"), (
        "GET /api/sequence/resume-arm no longer passes its payload through "
        "_redact_resume_arm_for; 6.9 says it withholds site_detail")
    refused = engine_mod.SlewRefused("words", words="words",
                                     site_detail="numbers")
    assert (refused.site_detail == "numbers"
            and "site_detail" in engine_mod.LimitVerdict._fields)
    filed = [c for c in _calls(_tree(ResumeArm._recover), "getattr")
             if len(c.args) >= 2 and isinstance(c.args[1], ast.Constant)
             and c.args[1].value == "site_detail"]
    assert filed, (
        "ResumeArm._recover no longer reads the refusal's site_detail; 6.9 "
        "says ResumeArm files the gate's numbers as a slew-limit hold's "
        "hold.site_detail")
    flip = [c for c in _self_calls(_tree(SequenceEngine._wait_for_flip_point),
                                   "_set_state")
            if _keyword_node(c, "detail") is not None]
    counted = [ast.unparse(_keyword_node(c, "detail")) for c in flip
               if not isinstance(_keyword_node(c, "detail"), ast.Constant)]
    assert flip and counted == [], (
        f"_wait_for_flip_point publishes a computed detail ({counted}); 6.9 "
        f"says the flip-point detail no longer carries its minutes")


# ------------------------------------------ 5.9, the no-light backoff

def test_5_9_says_a_no_light_solve_backs_off_and_resume_arm_does(monkeypatch):
    """5.9 says a recovery solve that finds no light backs off (#251, H3
    orchestrator ruling 8): the classifier in ``solve/light.py`` judges a
    failed solve frame by its light level; on a no-light verdict ResumeArm
    holds in words, sends one alert per session per spell, and retries once
    after ``RETRY_INTERVAL_S`` and then every ``NO_LIGHT_RETRY_S`` (both
    taken from ``resume_arm``); with no reference the frame gets no verdict
    (#262). ``ResumeArm._no_light_backoff`` answers the first retry, then
    the hourly one, and says the alert once, as an error line (the level a
    default alert sink delivers); ResumeArm branches on ``NoLightError``,
    the type, which is a ``FailedSolveError``.

    Mutants: the spec in place from a byte backup; code rebound in the test
    process only (a pytest plugin).

    RED under mutant "5.9 without the no-light sentence":

        AssertionError: 5.9 must say: '**A recovery solve that finds no light
        backs off** (#251, H3 orchestrator ruling 8)'
        assert False

    RED under the code mutant "resume_arm.NO_LIGHT_RETRY_S = 1800.0" (rebound
    in the test process):

        AssertionError: 5.9 must say: '`NO_LIGHT_RETRY_S` (30 min)'
        assert False

    RED under the code mutant "no backoff" (``ResumeArm._no_light_backoff``
    rebound to answer RETRY_INTERVAL_S):

        AssertionError: a no-light spell waits [600.0, 600.0, 600.0]; 5.9 says
        one retry after RETRY_INTERVAL_S, then every NO_LIGHT_RETRY_S
        assert [600.0, 600.0, 600.0] == [600.0, 3600.0, 3600.0]
        At index 1 diff: 600.0 != 3600.0
        Use -v to get more diff

    RED under the code mutant "an alert on every retry" (``_no_light_backoff``
    rebound to say the alert each time):

        AssertionError: a no-light spell said its alert as ['error', 'error',
        'error', 'error']; 5.9 says one alert per session per spell
        assert ['error', 'er...ror', 'error'] == ['error']
        Left contains 3 more items, first extra item: 'error'
        Use -v to get more diff
    """
    from astrodeck.solve import light
    s59 = _section("5.9")
    retry = (f"`RETRY_INTERVAL_S` "
             f"({resume_arm_mod.RETRY_INTERVAL_S / 60:g} min)")
    hourly = (f"`NO_LIGHT_RETRY_S` "
              f"({resume_arm_mod.NO_LIGHT_RETRY_S / 60:g} min)")
    _says(s59, ("**A recovery solve that finds no light backs off** (#251, "
                "H3 orchestrator ruling 8)", "`solve/light.py`", retry,
                hourly, "one alert per session per spell", "#262",
                f'"{light.NO_LIGHT_WORDS}"'), "5.9")
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(resume_arm_mod.bus, "log",
                        lambda level, msg, *a, **k: said.append((level, msg)))
    arm = object.__new__(ResumeArm)
    arm._no_light_spell = None
    session = SimpleNamespace(id="s1", name="claims")
    waits = [arm._no_light_backoff(session) for _ in range(3)]
    assert waits == [resume_arm_mod.RETRY_INTERVAL_S,
                     resume_arm_mod.NO_LIGHT_RETRY_S,
                     resume_arm_mod.NO_LIGHT_RETRY_S], (
        f"a no-light spell waits {waits}; 5.9 says one retry after "
        f"RETRY_INTERVAL_S, then every NO_LIGHT_RETRY_S")
    alerts = [lvl for lvl, msg in said if light.NO_LIGHT_WORDS in msg]
    assert alerts == ["error"], (
        f"a no-light spell said its alert as {alerts}; 5.9 says one alert "
        f"per session per spell")
    caught = [h for t in ast.walk(ast.parse(inspect.getsource(resume_arm_mod)))
              if isinstance(t, ast.Try) for h in _handlers(t, "NoLightError")]
    assert caught and issubclass(light.NoLightError, light.FailedSolveError), (
        "ResumeArm no longer branches on NoLightError by type; 5.9 says the "
        "ladder acts on the no-light verdict")


# ------------------------------------------ #239, the rulings code cites

#: A ruling cited by its label: the owner's (Revision 2's "### Ruling N"),
#: or an orchestrator's (the owner list's "**H2 orchestrator ruling N:" or
#: "**H3 orchestrator ruling N:"). ``issues`` is the run of issue numbers
#: written just before the label in the same parenthesis, "(#224, #225,
#: owner ruling 2 ...)", which is what the entry must be about.
_CITE = re.compile(
    r"(?P<issues>(?:#\d+[,;] ?)*)"
    r"(?<![\w'])(?P<whose>[Oo]wner|H2 orchestrator|H3 orchestrator) "
    r"rulings? (?P<nums>\d+(?:(?:, and |, | and )\d+)*)\b")
#: Where a citation says which owner-list item records it.
_ITEM = re.compile(r'[ ,;(]*(?:spec[ ,;]*)?"?(?:Still waiting on the owner'
                   r'|owner list)"?[ ,;]*items? (?P<items>\d+(?:(?:, and |, '
                   r'| and )\d+)*)')
#: Labels no document defines: a round's own item and A numbering. "A6"
#: already means #189 A6, the S1 hardening round's step-id spelling (3.3).
_UNDEFINED = re.compile(r"\bH\d+ (?:A\d+|items? \d+)\b")
#: A ruling cited by number without whose it is.
_UNOWNED = re.compile(r"(?:\bH\d+|#\d+|\bmosaic spec|\bspec) rulings? \d+")


def _prose(path: Path) -> str:
    """``path``'s text with each line break, and the indent and comment
    marker after it, made one space, and adjacent string literals joined,
    so a citation wrapped across lines or literals reads as one phrase. A
    ``#`` followed by a digit is an issue number and is kept."""
    text = re.sub(r"[ \t]*\r?\n[ \t]*(?:#:?[ \t]+)?", " ",
                  path.read_text(encoding="utf-8"))
    return re.sub(r'"\s*f?"', "", text)


def _ruling_entries() -> dict[tuple[str, int], tuple[str, int | None]]:
    """(whose, N) -> (the text of the spec's entry, its owner-list item, or
    None for an owner's ruling)."""
    entries: dict[tuple[str, int], tuple[str, int | None]] = {}
    for line in _section("Still").splitlines():
        m = re.match(r"(\d+)\. \*\*(H[23] orchestrator) ruling (\d+):", line)
        if m:
            key = (m.group(2), int(m.group(3)))
            assert key not in entries, f"the owner list records {key} twice"
            entries[key] = (line, int(m.group(1)))
    blocks = _blocks()
    rev2 = [text for name, text in blocks if name.startswith("Revision 2")]
    rows = {r[0]: " | ".join(r) for r in _rows(rev2[0])}
    for name, text in blocks:
        m = re.match(r"Ruling (\d+):", name)
        if m:
            entries[("owner", int(m.group(1)))] = (
                f"{name}\n{text}\n{rows.get(m.group(1), '')}", None)
    return entries


def test_every_ruling_the_server_cites_is_the_spec_entry_it_names():
    """#239: engine.py cited H2's orchestrator rulings as "owner ruling 1"
    and "owner ruling 2 of 2026-09-24", numbers the spec gives to the
    owner's own rulings on other subjects, and other modules and tests used
    labels no document defines ("H2 A6", "H2 item 13"). A reader who follows
    such a citation lands on the wrong ruling, or on nothing.

    So every "owner ruling N", "H2 orchestrator ruling N" and "H3
    orchestrator ruling N" under server/astrodeck and server/tests must
    resolve to a spec entry with that label and that attribution: an owner
    list item that opens with that label for an orchestrator's, Revision 2's
    "### Ruling N" for the owner's. Where issue numbers stand just before
    the label, the entry must name one of them, which is what catches a
    label that resolves to an entry on another subject (the #239 case,
    whose issues #224 and #225 Revision 2's Ruling 2 never names). Where the
    citation names its owner-list item, the ruling must be that item. And
    no round-internal label ("H2 A6", "H2 item 13") and no ruling cited
    without whose it is ("mosaic spec ruling 12", "#189 rulings 1 and 2")
    remains.

    This file is not scanned: its docstrings quote the mutants below, which
    are citations that must not resolve. Issue numbers themselves cannot be
    checked offline (#239's second comment records two tests citing #233
    for an earlier numbering); this checks rulings only.

    Mutants in place on engine.py from a byte backup, restored
    byte-identical (sha256 compared) after each.

    RED under the engine mutant "re-add 'owner ruling 2 of 2026-09-24' to
    engine.py" (``_hold_for_clear``'s docstring as #239 found it):

        AssertionError: astrodeck/sequence/engine.py: '#224, #225, owner ruling
        2' cites owner ruling 2 beside ['#224', '#225'], and the spec's entry
        names none of them
        assert ['astrodeck/s...none of them'] == []
        Left contains one more item: "astrodeck/sequence/engine.py: '#224,
        #225, owner ruling 2' cites owner ruling 2 beside ['#224', '#225'], and
        the spec's entry names none of them"
        Use -v to get more diff

    RED under the engine mutant "re-add 'H2 A6'" (``_run``'s cooling-wait
    comment as #239 found it):

        AssertionError: astrodeck/sequence/engine.py: 'H2 A6' is a label no
        document defines
        assert ['astrodeck/s...ment defines'] == []
        Left contains one more item: "astrodeck/sequence/engine.py: 'H2 A6' is
        a label no document defines"
        Use -v to get more diff

    RED under the engine mutant "cite 'H3 orchestrator ruling 10'" (the
    ``_acquisition_behind_gate`` comment):

        AssertionError: astrodeck/sequence/engine.py: '#241, H3 orchestrator
        ruling 10' names no H3 orchestrator ruling 10 the spec records
        assert ['astrodeck/s...spec records'] == []
        Left contains one more item: "astrodeck/sequence/engine.py: '#241, H3
        orchestrator ruling 10' names no H3 orchestrator ruling 10 the spec
        records"
        Use -v to get more diff

    RED under the engine mutant "a citation at the wrong item"
    (``IDLE_STOP_FINISH_S``'s comment placing ruling 5 at item 13):

        AssertionError: astrodeck/sequence/engine.py: '#247; H3 orchestrator
        ruling 5' places H3 orchestrator ruling 5 at owner list item 13; the
        spec records it as item 14
        assert ['astrodeck/s...t as item 14'] == []
        Left contains one more item: "astrodeck/sequence/engine.py: '#247; H3
        orchestrator ruling 5' places H3 orchestrator ruling 5 at owner list
        item 13; the spec records it as item 14"
        Use -v to get more diff

    Unchanged under the control "a correct citation added" (``_run``'s
    comment citing "#240, H3 orchestrator ruling 3, spec "Still waiting
    on the owner" item 12", wrapped over three comment lines): 1
    passed.
    """
    entries = _ruling_entries()
    # Premise: the spec's entries were read, all three kinds of them.
    assert {("owner", 2), ("H2 orchestrator", 2), ("H2 orchestrator", 12),
            ("H3 orchestrator", 1), ("H3 orchestrator", 9)} <= set(entries)
    here = Path(__file__).resolve()
    problems: list[str] = []
    found: set[tuple[str, str, int]] = set()
    for root in (_SERVER / "astrodeck", _SERVER / "tests"):
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == here:
                continue
            rel = path.relative_to(_SERVER).as_posix()
            text = _prose(path)
            for m in _CITE.finditer(text):
                whose = ("owner" if m.group("whose").lower() == "owner"
                         else m.group("whose"))
                nums = [int(n) for n in re.findall(r"\d+", m.group("nums"))]
                issues = re.findall(r"#\d+", m.group("issues"))
                im = _ITEM.match(text, m.end())
                items = ([int(n) for n in re.findall(r"\d+",
                                                     im.group("items"))]
                         if im else [])
                cite = f"{rel}: {m.group(0).strip()!r}"
                for i, n in enumerate(nums):
                    found.add((rel, whose, n))
                    entry = entries.get((whose, n))
                    if entry is None:
                        problems.append(f"{cite} names no {whose} ruling {n} "
                                        f"the spec records")
                        continue
                    body, item = entry
                    if issues and not any(re.search(rf"{iss}(?!\d)", body)
                                          for iss in issues):
                        problems.append(
                            f"{cite} cites {whose} ruling {n} beside "
                            f"{issues}, and the spec's entry names none of "
                            f"them")
                    if (len(items) == len(nums) and item is not None
                            and items[i] != item):
                        problems.append(
                            f"{cite} places {whose} ruling {n} at owner list "
                            f"item {items[i]}; the spec records it as item "
                            f"{item}")
            for pattern, why in ((_UNDEFINED, "a label no document defines"),
                                 (_UNOWNED, "a ruling cited without whose it "
                                            "is")):
                problems += [f"{rel}: {m.group(0)!r} is {why}"
                             for m in pattern.finditer(text)]
    # Premise: the scan reads wrapped citations; this one is wrapped in
    # engine.py (the IDLE_STOP_FINISH_S comment) and in resume_arm.py.
    assert {("astrodeck/sequence/engine.py", "H3 orchestrator", 5),
            ("astrodeck/sequence/resume_arm.py", "H3 orchestrator", 1),
            ("astrodeck/sequence/engine.py", "H2 orchestrator", 2)} <= found
    assert problems == [], "\n".join(problems)


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
_H3 = re.compile(r"\bH3\b")


def _carried(round_mark: re.Pattern) -> set[str]:
    """The sections whose text names a round (``round_mark``): every
    heading's text, and every row of the section 6 table, that says it. The
    revision records themselves are not sections, so they are skipped: a
    later revision names the round before it (Revision 5's preamble names
    Revision 4's rows, which record H2), and that is no edit of the body."""
    carried: set[str] = set()
    for name, text in _blocks():
        if re.match(r"Revision \d+\b", name):
            continue
        if name.startswith("6. "):
            stray = [ln for ln in text.splitlines()
                     if round_mark.search(ln) and not ln.startswith("| 6.")]
            assert stray == [], (f"section 6 carries {round_mark.pattern} "
                                 f"text outside its rows")
            carried |= {ln.split("|")[1].strip() for ln in text.splitlines()
                        if ln.startswith("| 6.") and round_mark.search(ln)}
        elif round_mark.search(text):
            carried.add(_label(name))
    return carried


def test_revision_4_lists_every_section_h2_edited():
    """Revision 4's table has one row per section that carries H2's edits,
    and no other: every heading's text, and every row of the section 6
    table, that says "H2" is listed, and every section listed says it. So a
    later edit marked H2 in a section the table does not list goes red, and
    so does a row whose section carries none. Its preamble names the rows
    of Revision 3 that its own rows supersede, computed here from the two
    tables.

    SINCE H3 the scan skips every revision record (``_carried``), not only
    Revision 4: Revision 5's preamble names the H2 rows it supersedes, and
    a revision record is not a section of the body. Both mutants below were
    run again on the H3 code and are still RED with the same failure.

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
    carried = _carried(_H2)
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


def test_revision_5_lists_every_section_h3_edited():
    """Revision 5, "the third hardening round, H3", has one row per section
    that carries H3's edits, and no other, by the same scan as Revision 4's
    (``_carried``): every heading's text, and every row of the section 6
    table, that says "H3" is listed, and every section listed says it. Its
    rows are numbered from 1, and its preamble names the rows of Revision 4
    that its own rows supersede, computed from the two tables.

    Mutants as in the tests above.

    RED under mutant "drop a Revision 5 row" (the 6.9 row):

        AssertionError: Revision 5 lists ['5.7', '5.8', '5.9', '6.15', '6.17',
        'owner list']; the sections carrying H3's edits are ['5.7', '5.8',
        '5.9', '6.15', '6.17', '6.9', 'owner list']
        assert ['5.7', '5.8'...', '6.9', ...] == ['5.7', '5.8'... 'owner list']
        At index 5 diff: '6.9' != 'owner list'
        Left contains one more item: 'owner list'
        Use -v to get more diff

    RED under mutant "an H3 edit in 5.10 with no Revision 5 row":

        AssertionError: Revision 5 lists ['5.7', '5.8', '5.9', '6.15', '6.17',
        '6.9', 'owner list']; the sections carrying H3's edits are ['5.10',
        '5.7', '5.8', '5.9', '6.15', '6.17', '6.9', 'owner list']
        assert ['5.10', '5.7..., '6.17', ...] == ['5.7', '5.8'...', '6.9', ...]
        At index 0 diff: '5.10' != '5.7'
        Left contains one more item: 'owner list'
        Use -v to get more diff

    RED under mutant "Revision 5's preamble without the rows it supersedes":

        AssertionError: Revision 5's preamble must say "Revision 4's rows 2, 3,
        4, 5 and 6" are superseded
        assert False

    Unchanged under the control "an unrelated edit in section 7"
    (all 29 tests in this file passed on it).
    """
    blocks = _blocks()
    rev5 = [(name, text) for name, text in blocks
            if name.startswith("Revision 5")]
    assert [name for name, _text in rev5] == [
        "Revision 5 (the third hardening round, H3)"], (
        "the spec must have one Revision 5, the third hardening round")
    rows = _rows(rev5[0][1])
    listed = set().union(*(_where(r[1]) for r in rows))
    carried = _carried(_H3)
    # Premise: the sections H3 is known to have edited are found.
    assert {"5.7", "5.8", "5.9", "6.9", "6.15", "6.17",
            "owner list"} <= carried
    assert sorted(carried) == sorted(listed), (
        f"Revision 5 lists {sorted(listed)}; the sections carrying H3's "
        f"edits are {sorted(carried)}")
    assert [r[0] for r in rows] == [str(i) for i in range(1, len(rows) + 1)]
    rev4 = [text for name, text in blocks if name.startswith("Revision 4")]
    superseded = [r[0] for r in _rows(rev4[0]) if _where(r[1]) & listed]
    assert superseded == ["2", "3", "4", "5", "6"], superseded
    preamble = _line(rev5[0][1], "2026-09-25. A third hardening round")
    named = (f"Revision 4's rows {', '.join(superseded[:-1])} and "
             f"{superseded[-1]}")
    said = named in preamble
    assert said, f"Revision 5's preamble must say {named!r} are superseded"


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
    left them. Since H3, Revision 5 likewise: its sections describe the code
    after H3, and those only Revision 4 lists are as H2 left them.

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

    Since H3 it names Revision 5 the same way.

    RED under mutant "the status line without Revision 5":

        AssertionError: the status line must say: '(Revision 5)'
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
                   "(H2)", "except in the sections Revision 4 lists too",
                   "(Revision 5)", "The sections Revision 5 lists describe "
                   "the code as it stands after the third hardening round "
                   "(H3)", "except in the sections Revision 5 lists too"),
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
