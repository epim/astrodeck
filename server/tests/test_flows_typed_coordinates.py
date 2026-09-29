"""Typed coordinates graded against one fixture on both sides (#387; spec
2026-09-23 flows mosaic, 3.1 and 3.3).

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
placed by its name and previewed a placement the run would never make. Now a
field that holds only whitespace is blank on both sides: the block is placed
by its name, as it is with an empty field. A falsy value that is not text (an
RA of 0 as a number) stays blank too, as it always read
(``test_flows_doctor_s3.py::TestTheWordsAtTheEdges::
test_only_typed_coordinates_are_measured`` pins that one).

The name branch is graded with ``tonight.resolve_target`` replaced by a
recorder. ``_coords`` looks the resolver up on the module at call time for
exactly this (its docstring), and what is under test is which branch a case
takes, not the catalogue: the recorder's answer comes back only when the name
branch ran, and the name it was asked about is checked.

Each mutant below was run in a private scratch copy of ``server/``
(``s5-compile-mut``), never in the shared tree (#254), and the failure it
produced is quoted verbatim in the test it turned red.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrodeck.flows import identity, tonight
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import _coords

FIXTURE = Path(__file__).parent / "fixtures" / "typed_coordinates_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

#: The shapes #387 asks the fixture to hold, one kind each.
KINDS = ("whitespace only", "blank", "typed", "typed with padding")

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


def test_the_fixture_holds_every_shape_387_names():
    """THE PREMISE: a fixture that lost a kind would leave the cases below
    green for no reason (a harness that cannot reach the branch). Each kind
    is present, the typed ones carry where they are placed, and the modal's
    own case from #387 is here by name.

    MUTANT "no whitespace-only case" (a scratch copy of the fixture with the
    nine whitespace-only cases removed). Observed:

        E       AssertionError: the fixture no longer holds: ['whitespace only']

    MUTANT "no blank case" (the four blank cases removed). Observed:

        E       AssertionError: the fixture no longer holds: ['blank']
    """
    held = {c["kind"] for c in CASES}
    missing = [k for k in KINDS if k not in held]
    assert not missing, f"the fixture no longer holds: {missing}"
    assert all(("ra_hours" in c and "dec_deg" in c) == c["typed"]
               for c in CASES)
    assert any("#387" in c["id"] and c["kind"] == "whitespace only"
               for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_typed_coordinates_reads_every_case(case):
    """MUTANT "raw truthiness on the server" (``identity.typed_coordinates``
    back to ``bool(entry.get("ra") and entry.get("dec"))``, as #387 found
    it). Observed red on the nine whitespace-only cases of this test and on
    nothing else in it (27 failed, 30 passed across this file: the same
    nine in each of the three fixture tests), the modal's own case first:

        E       AssertionError: the modal's case from #387: RA of three spaces: ra '   ', dec '+41 16 09'
        E       assert True is False

    MUTANT "str() before the test" (see the control at the bottom) turns
    the null case red here too:

        E       AssertionError: RA null: ra None, dec '+41 16 09'
        E       assert True is False
    """
    got = identity.typed_coordinates(_entry(case))
    assert got is case["typed"], (f"{case['id']}: ra {case['ra']!r}, dec "
                                  f"{case['dec']!r}")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_coords_places_every_case_where_the_fixture_says(case, asked):
    """A typed case is placed at its typed position, padding or not, and a
    case that is not typed is placed by its NAME: whitespace is no
    coordinate. Before #387 the whitespace-only cases were dropped here
    (``None``: typed, and no parse), which is the placement the modal never
    previewed.

    MUTANT "raw truthiness on the server", observed on the same nine cases,
    e.g. the modal's own (dropped, not placed by name):

        E           AssertionError: the modal's case from #387: RA of three spaces
        E           assert None == (5.0, -20.0, 'NAMED-BY-THE-RECORDER')
    """
    got = _coords(_entry(case), None)
    if case["typed"]:
        assert got is not None, case["id"]
        assert got[0] == pytest.approx(case["ra_hours"], abs=1e-6)
        assert got[1] == pytest.approx(case["dec_deg"], abs=1e-6)
        assert got[2] is None, "a typed field keys on its geometry"
        assert asked == [], "a typed block never asks the catalogue"
    else:
        assert got == NAMED_AT, case["id"]
        assert asked == ["M31"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_compiled_entry_places_the_same(case, asked):
    """The route the run takes: the case as a TARGET's params, through
    ``compile_plan``, whose entry ``to_plan`` reads. The compile copies the
    fields as text and leaves the trimming to the one reading, so the PLAN
    tab still shows what was typed, and the entry places the block exactly
    as the raw one does.

    RED under "raw truthiness on the server" on the nine whitespace-only
    cases, at the typed test on the compiled entry, e.g. the modal's own:

        E       AssertionError: assert True is False
        E        +  where True = <function typed_coordinates at 0x000001ED7FA0BD80>({'angle': 'any', 'centre': {'attempts': 3, 'tol_arcmin': 1.2}, 'count_mode': 'attempts', 'dec': '+41 16 09', ...})
    """
    graph = FlowGraph.model_validate({"nodes": [
        {"id": "t", "type": "target",
         "params": {"name": "M31", "ra": case["ra"], "dec": case["dec"]}}]})
    (entry,) = compile_plan(graph, "n")["targets"]
    assert entry["ra"] == ("" if case["ra"] is None else case["ra"])
    assert identity.typed_coordinates(entry) is case["typed"]
    got = _coords(entry, None)
    if case["typed"]:
        assert got[:2] == pytest.approx(
            (case["ra_hours"], case["dec_deg"]), abs=1e-6)
    else:
        assert got == NAMED_AT, case["id"]


@pytest.mark.parametrize("ra", [0, 0.0, False, [], {}],
                         ids=["int-0", "float-0", "false", "list", "dict"])
def test_control_a_falsy_value_that_is_not_text_stays_blank(ra):
    """CONTROL: stripping is for text. A falsy value that is not text read
    as blank before #387 and still does, so a hand-edited RA of the number
    0 is not suddenly typed as 0h. Green on the code and on the #387 code
    alike.

    RED under mutant "str() before the test" (``identity._typed`` testing
    ``str(value).strip()`` for every value, so 0 is the text "0"), observed
    on all five, e.g. int-0:

        E       AssertionError: assert True is False
        E        +  where True = <function typed_coordinates at 0x000001F2387BDEE0>({'dec': '+41 16 09', 'name': 'M31', 'ra': 0})
    """
    assert identity.typed_coordinates(
        {"name": "M31", "ra": ra, "dec": "+41 16 09"}) is False
