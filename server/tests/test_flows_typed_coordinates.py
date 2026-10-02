# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Typed coordinates graded against one fixture on both sides (#387; spec
2026-09-23 flows mosaic, 3.1 and 3.3; S7 orchestrator ruling 6).

THE FIXTURE IS ``fixtures/typed_coordinates_cases.json``, and it has two
readers. This file grades the server's reading, ``identity.typed_coordinates``
and ``to_plan._coords``, which decide whether a TARGET is placed at the RA and
Dec typed on it or by its name through the catalogue; the Target modal's
mirror (``framingModel.ts`` ``typedCoordinates`` and ``draftCentre``) is graded
against the same file by ``typedCoordinatesFixture.test.ts``. The modal
previews a placement from its own reading and the run places the block from
this one, so the two must be one reading, and neither side copies the table.

WHAT #387 FOUND. The server tested raw truthiness, so an RA of three spaces
was typed; ``_coords`` then failed to parse it and dropped the block with no
usable coordinates, while the modal, which trims, read the same block as
placed by its name and previewed a placement the run would never make. S5
made a field that holds only whitespace blank on both sides.

WHAT WAS STILL OPEN, AND RULING 6. Two residuals read two ways after S5. A
value that is not text: the server read a falsy one as blank and any other as
its ``str``, so the number 0 was blank and ``True`` was typed, while the
modal's ``String(v).trim()`` made 0 the text "0" and ``true`` the text
"true". And the characters Python's ``str.strip()`` and JavaScript's
``trim()`` disagree on: a lone BOM was typed on the server and blank in the
modal, a lone NEL or U+001C blank on the server and typed in the modal. S7
orchestrator ruling 6 settles both: a coordinate is typed when it is text
with something in it besides Python's whitespace, or a finite number, 0
included (RA 0h and Dec 0 are real); null, a bool, NaN and an infinity are
blank. ``identity._typed`` reads it, ``compile._text`` hands a finite number
to the compiled entry as its text (so an RA of 0 is placed at 0h through
``to_plan._coords``) and blanks the rest, and the modal reads through
``pyStrip``. The fixture holds every one of those values.

The name branch is graded with ``tonight.resolve_target`` replaced by a
recorder. ``_coords`` looks the resolver up on the module at call time for
exactly this (its docstring), and what is under test is which branch a case
takes, not the catalogue: the recorder's answer comes back only when the name
branch ran, and the name it was asked about is checked.

Each mutant below was run in a private scratch copy of ``server/``
(``S7-COMPILE-mut`` under the session scratchpad; ``s5-compile-mut`` for the
S5 ones), never in the shared tree (#254), and the failure it produced is
quoted verbatim in the test it turned red.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from astrodeck.flows import identity, tonight
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import _coords

FIXTURE = Path(__file__).parent / "fixtures" / "typed_coordinates_cases.json"


def _decoded(value):
    """A fixture value as a node holds it. JSON has no NaN, so the fixture
    writes one as the marker ``{"number": "NaN"}``, which both graders decode
    into the number; any other value is itself."""
    if isinstance(value, dict) and set(value) == {"number"}:
        return float(value["number"])
    return value


CASES = [dict(c, ra=_decoded(c["ra"]), dec=_decoded(c["dec"]))
         for c in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]]

#: The shapes the fixture must hold, one kind each: #387's four and ruling
#: 6's three.
KINDS = ("whitespace only", "blank", "typed", "typed with padding",
         "a number", "neither text nor a finite number",
         "typed, does not parse")

#: Where the recorder says a name is, and the identity it answers. Far from
#: M31's typed position, so a case that took the wrong branch cannot land on
#: the right numbers by accident.
NAMED_AT = (5.0, -20.0, "NAMED-BY-THE-RECORDER")


class _Hit:
    ra_hours, dec_deg, identity = NAMED_AT


@pytest.fixture
def asked(monkeypatch):
    """The names ``_coords`` asked the catalogue about, in order."""
    names: list[str] = []

    def recorder(name, when=None):
        names.append(name)
        return _Hit()

    monkeypatch.setattr(tonight, "resolve_target", recorder)
    return names


def _entry(case: dict) -> dict:
    return {"name": "M31", "ra": case["ra"], "dec": case["dec"]}


def _placed(case: dict) -> bool:
    return "ra_hours" in case


def _by_id(fragment: str) -> dict:
    hits = [c for c in CASES if fragment in c["id"]]
    assert len(hits) == 1, f"{len(hits)} cases are {fragment!r}"
    return hits[0]


def test_the_fixture_holds_every_shape_387_and_ruling_6_name():
    """THE PREMISE: a fixture that lost a kind would leave the cases below
    green for no reason (a harness that cannot reach the branch). Each kind
    is present, a typed case carries where it is placed unless it is one
    that does not parse, the modal's own case from #387 is here by name, and
    each of ruling 6's values decodes to what it stands for (a JSON writer
    that turned 1e999 into a finite number, or dropped the NaN marker, would
    otherwise grade nothing).

    MUTANT "no whitespace-only case" (a scratch copy of the fixture with the
    nine whitespace-only cases removed). Observed (S5):

        E       AssertionError: the fixture no longer holds: ['whitespace only']

    MUTANT "no number case" (a scratch copy of the fixture with the four
    ``a number`` cases removed). Observed:

        E       AssertionError: the fixture no longer holds: ['a number']
    """
    held = {c["kind"] for c in CASES}
    missing = [k for k in KINDS if k not in held]
    assert not missing, f"the fixture no longer holds: {missing}"
    assert len({c["id"] for c in CASES}) == len(CASES), "two cases share an id"
    for c in CASES:
        assert _placed(c) == ("dec_deg" in c), c["id"]
        assert not _placed(c) or c["typed"], c["id"]
        assert (c["typed"] and not _placed(c)) == (
            c["kind"] == "typed, does not parse"), c["id"]
    assert any("#387" in c["id"] and c["kind"] == "whitespace only"
               for c in CASES)
    zero, zero_f = _by_id("RA the number 0:"), _by_id("RA the number 0.0")
    assert type(zero["ra"]) is int and zero["ra"] == 0
    assert type(zero_f["ra"]) is float and zero_f["ra"] == 0.0
    assert _by_id("RA true")["ra"] is True
    assert _by_id("RA false")["ra"] is False
    assert math.isnan(_by_id("RA NaN")["ra"])
    assert _by_id("RA Infinity")["ra"] == math.inf
    assert _by_id("minus Infinity")["dec"] == -math.inf
    assert _by_id("400-digit")["ra"] == 10 ** 400
    # The characters #387's residual is about, built from code points so this
    # source stays ASCII: a lone BOM, NEL and file separator.
    assert _by_id("lone BOM")["ra"] == chr(0xFEFF)
    assert _by_id("lone NEL")["ra"] == chr(0x85)
    assert _by_id("lone file separator")["ra"] == chr(0x1C)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_typed_coordinates_reads_every_case(case):
    """``identity.typed_coordinates`` answers every case as the fixture
    says, so the doctor, the save's anchor and the run, which all ask it,
    place the block the way the modal previews it.

    MUTANT "falsy non-text blank" (``identity._typed`` back to S5's
    ``bool(str(value).strip()) if value else False``). Observed:

        red on ten cases in each of the three fixture tests (30 failed,
        74 passed): the three zeros, and the seven values that are neither
        text nor a finite number but truthy (``false`` stays blank either
        way), e.g.:

        E       AssertionError: RA the number 0: 0h is a real RA (ruling 6): ra 0, dec '+41 16 09'
        E       assert False is True
        E       AssertionError: RA true: ra True, dec '+41 16 09'
        E       assert True is False

    MUTANT "bool read as typed" (``identity._typed`` without its bool
    exclusion, so ``True`` and ``False`` are the finite numbers 1 and 0, as
    ``isinstance(True, int)`` says they are). Observed:

        red on RA true and RA false in each of the three fixture tests
        (6 failed, 98 passed), e.g.:

        E       AssertionError: RA true: ra True, dec '+41 16 09'
        E       assert True is False

    MUTANT "raw truthiness on the server" (``identity.typed_coordinates``
    back to ``bool(entry.get("ra") and entry.get("dec"))``, as #387 found
    it). Observed:

        red on 21 cases in each of the three fixture tests (63 failed, 41
        passed): the nine whitespace-only cases, the lone NEL and U+001C,
        the three zeros and the seven values that are neither text nor a
        finite number, the modal's own case first:

        E       AssertionError: the modal's case from #387: RA of three spaces: ra '   ', dec '+41 16 09'
        E       assert True is False
    """
    got = identity.typed_coordinates(_entry(case))
    assert got is case["typed"], (f"{case['id']}: ra {case['ra']!r}, dec "
                                  f"{case['dec']!r}")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_coords_places_every_case_where_the_fixture_says(case, asked):
    """A typed case is placed at its typed position, padding or not, and a
    case that is not typed is placed by its NAME: whitespace is no
    coordinate, and neither is a value that is no text and no finite
    number. A typed case that does not parse (a lone BOM, which Python's
    strip keeps) is dropped, never placed by its name. An RA of the number 0
    is placed at 0h (ruling 6); before, it was placed by its name.

    MUTANT "falsy non-text blank", observed:

        E       AssertionError: a typed block never asks the catalogue
        E       assert ['M31'] == []

        on the three zeros (placed by the name), and on the seven truthy
        values that are neither text nor a number (typed, so dropped), e.g.:

        E           AssertionError: RA true
        E           assert None == (5.0, -20.0, 'NAMED-BY-THE-RECORDER')
    """
    got = _coords(_entry(case), None)
    if not case["typed"]:
        assert got == NAMED_AT, case["id"]
        assert asked == ["M31"]
        return
    assert asked == [], "a typed block never asks the catalogue"
    if not _placed(case):
        assert got is None, case["id"]
        return
    assert got is not None, case["id"]
    assert got[0] == pytest.approx(case["ra_hours"], abs=1e-6)
    assert got[1] == pytest.approx(case["dec_deg"], abs=1e-6)
    assert got[2] is None, "a typed field keys on its geometry"


def _compiled_text_is_right(value, text) -> bool:
    """What ``compile._text`` must make of one field: text exactly as typed,
    untrimmed; a finite number as text that reads back as that number; and
    anything else blank."""
    if isinstance(value, str):
        return text == value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(float(value))
        except OverflowError:       # the 400-digit integer
            finite = False
        if finite:
            try:
                return isinstance(text, str) and float(text) == value
            except ValueError:      # "" is no number at all
                return False
    return text == ""


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_compiled_entry_places_the_same(case, asked):
    """The route the run takes: the case as a TARGET's params, through
    ``compile_plan``, whose entry ``to_plan`` reads. The compile copies text
    as typed and leaves the trimming to the one reading, so the PLAN tab
    still shows what was typed; it hands a finite number on as its text and
    blanks what is neither (``compile._text``, ruling 6). The entry then
    places the block exactly as the raw one does.

    MUTANT "_text blanks 0" (``compile._text`` blanking a 0 as S5 did and
    nothing else, ``str(v) if finite_number(v) and v else ""``). Observed:

        E       AssertionError: RA the number 0: 0h is a real RA (ruling 6): ra 0 compiled to ''
        E       assert False
        E        +  where False = _compiled_text_is_right(0, '')

        on the three zeros and nothing else (3 failed, 101 passed).

    MUTANT "_text as S5" (the whole S5 reading, ``str(v) if v else ""``;
    re-run by the S7-COMPILE verifier). Red on the three zeros and on the
    seven truthy values that are neither text nor a finite number (10
    failed, 94 passed), e.g.:

        E       AssertionError: Dec an object: dec {'deg': 41} compiled to "{'deg': 41}"
    """
    graph = FlowGraph.model_validate({"nodes": [
        {"id": "t", "type": "target",
         "params": {"name": "M31", "ra": case["ra"], "dec": case["dec"]}}]})
    (entry,) = compile_plan(graph, "n")["targets"]
    assert _compiled_text_is_right(case["ra"], entry["ra"]), (
        f"{case['id']}: ra {case['ra']!r} compiled to {entry['ra']!r}")
    assert _compiled_text_is_right(case["dec"], entry["dec"]), (
        f"{case['id']}: dec {case['dec']!r} compiled to {entry['dec']!r}")
    assert identity.typed_coordinates(entry) is case["typed"], case["id"]
    assert _coords(entry, None) == _coords(_entry(case), None), case["id"]


def test_control_the_compiled_text_reader_is_not_a_copy_of_the_rule():
    """CONTROL on the grader above: ``_compiled_text_is_right`` refuses the
    readings the mutants give, so the entry test is not true by
    construction. A trimmed text, a blanked 0, a NaN kept as "nan" and a
    ``True`` kept as "True" are each refused; the right reading of each is
    taken."""
    assert not _compiled_text_is_right("  5h ", "5h")
    assert not _compiled_text_is_right(0, "")
    assert not _compiled_text_is_right(math.nan, "nan")
    assert not _compiled_text_is_right(True, "True")
    assert _compiled_text_is_right("  5h ", "  5h ")
    assert _compiled_text_is_right(0, "0")
    assert _compiled_text_is_right(math.nan, "")
    assert _compiled_text_is_right(True, "")
