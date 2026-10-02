# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The wizard's unguided sub cap ends below the doctor's line (#432; #189
spec 1.8, and Revision 2, ruling 4: every generated graph passes the doctor
at note level or better; S7).

The doctor's rule 2 warns from 120 s: "<N>s subs with no GUIDE upstream -
stars will trail at any real focal length". The wizard writes the answer's
``unguided_exposure_s`` onto an unguided lane's CAPTURE LOOP (DEVIATION 1)
and holds the door's filter rows to it (``_within_the_unguided_cap``), and
until S7 the route accepted that answer up to 3600 s. So an answer of 150
generated a first flow already warning that its stars would trail, the one
outcome DEVIATION 1 exists to prevent, and a cap of 150 let a 150 s row
through to the same warning. No matrix passed an answer that high, so
nothing saw it.

One constant now holds the line, ``doctor.UNGUIDED_SUB_LINE_S``, and every
reader asks it: the doctor's rule 2, ``FlowWizardBody.unguided_exposure_s``
(refused at the line or past it, a 422 from the door), and the wizard's
``_unguided_seconds``, which ``_within_the_unguided_cap`` reads the cap
through. An answer at the line is refused rather than shortened: the
operator typed it, and a sub quietly written shorter than they asked for is
the wrong night told as the right one.

Every mutant below was applied to a private copy of ``server/`` under the
session scratchpad (``S7-ROUTES-mut``), each file restored from a byte copy
between mutants and never in the shared tree (#254); the failure each
produced is quoted as observed.
"""
from __future__ import annotations

import ast
import math
import pathlib

import annotated_types
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows import doctor, tonight
from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.store import flow_store
from astrodeck.flows.wizard import (
    KIND_DEEP_SKY, KIND_EAA, KIND_MOSAIC, KIND_POOL, OPT_GUIDING,
    UNGUIDED_EXPOSURE_DEFAULT, generate, generate_answer)

NAME = "M16"
#: The words rule 2 opens its sentence with, after the sub length.
RULE_2 = "subs with no GUIDE upstream - stars will trail"
#: The kinds whose lane is unguided with no chips lit, each of which writes
#: the answer (EAA shoots its own 4 s subs and never reads it).
UNGUIDED_KINDS = (KIND_DEEP_SKY, KIND_POOL, KIND_MOSAIC)


@pytest.fixture(autouse=True)
def _one_catalogue_search_per_name(monkeypatch):
    """The catalogue search behind ``tonight.resolve_target`` costs about 20
    ms a call; the real resolver answers each (name, instant) once per test
    and its answer is replayed, as test_flows_wizard.py does."""
    real, memo = tonight.resolve_target, {}

    def once(name, when=None):
        if (name, when) not in memo:
            memo[(name, when)] = real(name, when)
        return memo[(name, when)]

    monkeypatch.setattr(tonight, "resolve_target", once)


def _capture(graph: FlowGraph):
    found = [n for n in graph.nodes if n.type == "capture"]
    assert len(found) == 1, f"expected one capture, got {len(found)}"
    return found[0]


def _loud(graph: FlowGraph) -> list[str]:
    return [i.text for i in check(graph) if i.level in ("warn", "danger")]


def _trails(graph: FlowGraph) -> list[str]:
    return [t for t in _loud(graph) if RULE_2 in t]


def _at(secs) -> FlowGraph:
    """An unguided deep-sky graph whose CAPTURE LOOP is ``secs`` long, set
    by hand after generating, so the doctor can be asked about a sub the
    wizard itself would refuse to write."""
    graph = generate(KIND_DEEP_SKY, set(), NAME, 1)
    _capture(graph).params["exposure"] = secs
    return graph


def _door_source(source: str) -> tuple[dict[str, str], list[str]]:
    """What ``source`` writes for the door: the keyword bounds of
    ``FlowWizardBody.unguided_exposure_s``'s ``Field``, each as its source
    text, and every statement that binds the name ``UNGUIDED_SUB_LINE_S``
    (an import of it, under any module, or an assignment to it)."""
    tree = ast.parse(source)
    body = next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "FlowWizardBody")
    (field,) = [n for n in body.body if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)
                and n.target.id == "unguided_exposure_s"]
    bounds = {k.arg: ast.unparse(k.value) for k in field.value.keywords}
    binds: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            where = ("." * n.level + (n.module or "")
                     if isinstance(n, ast.ImportFrom) else "")
            binds += [f"from {where} import {a.name}"
                      + (f" as {a.asname}" if a.asname else "")
                      for a in n.names
                      if (a.asname or a.name) == "UNGUIDED_SUB_LINE_S"]
        elif (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
              and n.id == "UNGUIDED_SUB_LINE_S"):
            binds.append(f"UNGUIDED_SUB_LINE_S assigned at line {n.lineno}")
    return bounds, binds


class TestOneLine:
    def test_the_line_is_the_doctors_120_s_and_the_doctor_warns_from_it(self):
        """Rule 2's number, pinned where it is kept. The doctor is silent
        one second under the line and warns at it, so the line is where the
        warning begins and not a number beside it.

        RED under mutant "the doctor warns from 100 s" (rule 2's comparison
        made ``exp >= 100``), observed verbatim:

            E   AssertionError: the doctor warned below its own line:
                ['\\u25b8 119s subs with no GUIDE upstream - stars will
                trail at any real focal length. Add Guide, or shorten the
                subs.']
        """
        line = doctor.UNGUIDED_SUB_LINE_S
        assert line == 120
        below = _trails(_at(line - 1))
        assert not below, f"the doctor warned below its own line: {below}"
        at = _trails(_at(line))
        assert len(at) == 1 and at[0].startswith(f"\u25b8 {line:g}s "), at

    def test_every_reader_moves_with_the_one_constant(self, monkeypatch):
        """ONE CONSTANT, READ WHERE IT IS KEPT. With the line moved to 60 s
        in ``doctor`` alone, the doctor warns from 60, the wizard refuses an
        answer of 60 and writes 59, and a row of 59 is held to a cap of 59
        and passes the doctor. A second copy of the number anywhere would
        stay at 120 and answer differently here.

        RED under mutant "the doctor keeps its literal 120" (rule 2's
        comparison made ``exp >= 120``, the same answers at 120 and so
        invisible to every other case here), and the same under "the doctor
        warns from 100 s", observed verbatim:

            E   AssertionError: the doctor did not move with the line: []
            E   assert 0 == 1
            E    +  where 0 = len([])

        RED under mutant "the wizard holds its own 120"
        (``_unguided_seconds``'s ``line = doctor.UNGUIDED_SUB_LINE_S`` made
        ``line = 120``), observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        monkeypatch.setattr(doctor, "UNGUIDED_SUB_LINE_S", 60)
        warned = _trails(_at(60))
        assert len(warned) == 1, f"the doctor did not move with the line: {warned}"
        assert not _trails(_at(59))
        with pytest.raises(ValueError, match=r"unguided_exposure_s .* 60 s"):
            generate(KIND_DEEP_SKY, set(), NAME, 60)
        assert _capture(generate(KIND_DEEP_SKY, set(), NAME, 59)
                        ).params["exposure"] == 59
        ans = generate_answer(KIND_DEEP_SKY, set(), NAME, 59,
                              cycle_plan="L 59, R 59", cycles=3)
        assert not _loud(ans.record.graph)

    def test_the_door_reads_the_line(self):
        """``FlowWizardBody.unguided_exposure_s`` is bounded strictly below
        the doctor's constant and by nothing else above: the door's bound
        has the line's value, not the old 3600 s. A second literal of the
        same value passes here, because a pydantic bound is read once, when
        app.py is imported, so no monkeypatch can move it;
        ``test_the_door_names_the_constant_and_holds_no_copy`` holds that.

        RED under mutant "le=3600 restored" (the field back to ``Field(None,
        gt=0, le=3600)``), observed verbatim (and re-run by the S7-ROUTES
        verifier in ``S7-ROUTES-verify-mut``, the same):

            E   AssertionError: [Gt(gt=0), Le(le=3600)]
            E   assert [Gt(gt=0), Lt(lt=120)] == [Gt(gt=0), Le(le=3600)]

        and under mutant "the door one under the line" (``lt=`` the
        constant less 1): ``E   AssertionError: [Gt(gt=0), Lt(lt=119)]``.
        """
        meta = app_module.FlowWizardBody.model_fields[
            "unguided_exposure_s"].metadata
        want = [annotated_types.Gt(gt=0),
                annotated_types.Lt(lt=doctor.UNGUIDED_SUB_LINE_S)]
        assert want == meta, meta

    def test_the_door_names_the_constant_and_holds_no_copy(self):
        """THE DOOR READS THE LINE BY NAME. app.py writes the field's upper
        bound as ``lt=UNGUIDED_SUB_LINE_S``, and binds that name once, by
        importing it from ``..flows.doctor``. A door written ``lt=120`` has
        today's value and would stay at 120 when the doctor's line moves,
        which is how the door came to accept 3600 s while rule 2 warned from
        120 (#432). Read from app.py's source, as test_overlap_constant_one.py
        reads the UI's readers, because the bound is fixed at import and
        ``test_every_reader_moves_with_the_one_constant`` cannot reach it.

        The reader is checked on a known-good and a known-bad snippet first,
        so it cannot pass by finding nothing.

        RED under mutant "the door holds its own 120" (the field's bound
        written ``lt=120``), which every other case in this file and in
        test_flows_wizard.py, test_flows_wizard_door_answers.py and
        test_flows_wizard_route.py passes (517 passed in the private copy
        ``S7-ROUTES-verify-mut``), observed verbatim:

            E   AssertionError: {'gt': '0', 'lt': '120'}
            E   assert {'gt': '0', 'lt': '120'} == {'gt': '0', '...D_SUB_LINE_S'}
            E     Differing items:
            E     {'lt': '120'} != {'lt': 'UNGUIDED_SUB_LINE_S'}

        RED under mutant "a copy beside the import" (``UNGUIDED_SUB_LINE_S
        = 120`` added after app.py's import of it, so the field names the
        constant but reads app.py's own number), observed verbatim:

            E   AssertionError: ['from ..flows.doctor import
                UNGUIDED_SUB_LINE_S', 'UNGUIDED_SUB_LINE_S assigned at line
                157']
            E   Left contains one more item: 'UNGUIDED_SUB_LINE_S assigned
                at line 157'

        RED under mutant "le=3600 restored" too, beside
        ``test_the_door_reads_the_line``: ``E   AssertionError: {'gt': '0',
        'le': '3600'}``.
        """
        good = ("from ..flows.doctor import UNGUIDED_SUB_LINE_S, check\n"
                "class FlowWizardBody(BaseModel):\n"
                "    unguided_exposure_s: float | None = Field(\n"
                "        None, gt=0, lt=UNGUIDED_SUB_LINE_S)\n")
        assert _door_source(good) == (
            {"gt": "0", "lt": "UNGUIDED_SUB_LINE_S"},
            ["from ..flows.doctor import UNGUIDED_SUB_LINE_S"])
        bad = good.replace("lt=UNGUIDED_SUB_LINE_S)", "lt=120)")
        assert _door_source(bad)[0] == {"gt": "0", "lt": "120"}
        bounds, binds = _door_source(
            pathlib.Path(app_module.__file__).read_text(encoding="utf-8"))
        assert bounds == {"gt": "0", "lt": "UNGUIDED_SUB_LINE_S"}, bounds
        assert binds == ["from ..flows.doctor import UNGUIDED_SUB_LINE_S"], (
            binds)


class TestTheGeneratorRefusesAtTheLine:
    @pytest.mark.parametrize("secs", [120, 120.0, "120", 150, 3600,
                                      math.inf],
                             ids=["120", "120.0", "text-120", "150", "3600",
                                  "inf"])
    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_POOL, KIND_MOSAIC,
                                      KIND_EAA])
    def test_an_answer_at_or_past_the_line_is_refused_naming_it(self, kind,
                                                                secs):
        """Refused, not shortened and not written: the answer is the
        operator's, and the wizard's promise is a first flow the doctor is
        silent on. Refused for every kind, EAA included, although only an
        unguided lane reads it: the door refuses it whatever the lane
        (pydantic cannot see the lane), and a generator that took what its
        own door refuses would be a second rule.

        RED under mutant "cap compared with <=" (``_unguided_seconds``'s
        ``not secs < line`` made ``not secs <= line``), in the [120],
        [120.0] and [text-120] cases of every kind, 12 of the 24, observed
        verbatim ([Deep-sky target-120] shown):

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError) as e:
            generate(kind, set(), NAME, secs)
        said = str(e.value)
        assert said.startswith("unguided_exposure_s "), said
        assert f"{doctor.UNGUIDED_SUB_LINE_S:g} s" in said, said

    def test_a_guided_lane_is_refused_too_and_the_rows_are_not_read(self):
        """The same refusal with the Guiding chip lit, and ahead of a filter
        row's: the answer is refused as an answer, so a row the cap would
        also have refused is not what the sentence names.

        RED under mutant "cap compared with <=", observed verbatim (the
        guided 120 comes back a graph):

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match=r"^unguided_exposure_s "):
            generate(KIND_DEEP_SKY, {OPT_GUIDING}, NAME, 120)
        with pytest.raises(ValueError, match=r"^unguided_exposure_s "):
            generate_answer(KIND_DEEP_SKY, set(), NAME, 150,
                            cycle_plan="L 150", cycles=3)

    @pytest.mark.parametrize("kind", UNGUIDED_KINDS)
    @pytest.mark.parametrize("secs", [1, 30, 90, 119, 119.5])
    def test_control_an_answer_under_the_line_is_written_as_given(self, kind,
                                                                  secs):
        """Control: under the line the answer is written exactly and the
        doctor is silent, a fraction of a second under included.

        RED under mutant "the wizard refuses from a second under the line"
        (``not secs < line`` made ``not secs < line - 1``), in the [119]
        and [119.5] cases, observed verbatim ([119-Deep-sky target] shown):

            E   ValueError: unguided_exposure_s is the longest sub a lane
                with no guider is held to, and 119 s is at or past the 120 s
                the doctor warns from (rule 2: stars trail): answer less
                than 120 s
        """
        graph = generate(kind, set(), NAME, secs)
        assert _capture(graph).params["exposure"] == secs
        assert not _loud(graph), _loud(graph)


class TestTheRowsAtTheCap:
    @pytest.mark.parametrize("kind", UNGUIDED_KINDS)
    @pytest.mark.parametrize("cap", [30, 90, 119])
    def test_a_row_the_cap_holds_is_one_the_doctor_is_silent_on(self, kind,
                                                                cap):
        """``_within_the_unguided_cap`` reads its cap through
        ``_unguided_seconds``, so the longest row it takes is under the
        line: rows at the cap pass the doctor, and a row one second past
        the cap is refused naming it.

        RED under mutant "the doctor warns from 100 s", in the three [119]
        cases, observed verbatim ([119-Deep-sky target] shown):

            E   AssertionError: ['\\u25b8 119s subs with no GUIDE upstream -
                stars will trail at any real focal length. Add Guide, or
                shorten the subs.']

        RED in the same three under "the wizard refuses from a second under
        the line", the cap of 119 itself refused (the ValueError quoted in
        ``test_control_an_answer_under_the_line_is_written_as_given``). RED
        in all nine under mutant "a row one past the cap is taken"
        (``_within_the_unguided_cap``'s ``s > cap`` made ``s > cap + 1``),
        observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        target = "M16, M17" if kind == KIND_POOL else NAME
        ans = generate_answer(kind, set(), target, cap,
                              cycle_plan=f"L {cap}, R {cap}", cycles=3)
        graph = ans.record.graph
        assert any(n.type == "cycle" for n in graph.nodes)
        assert not _loud(graph), _loud(graph)
        with pytest.raises(ValueError, match=rf"\['R {cap + 1}'\] run longer "
                                             rf"than the {cap} s"):
            generate_answer(kind, set(), target, cap,
                            cycle_plan=f"L {cap}, R {cap + 1}", cycles=3)


# ============================================================== the route

@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """test_flows_wizard_route.py's client: the flow library under the
    test's own directory and the config isolated."""
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        yield c


class TestTheDoor:
    @pytest.mark.parametrize("secs", [120, 120.0, 3600])
    def test_the_door_refuses_an_answer_at_or_past_the_line(self, client,
                                                            secs):
        """A 422 from the DOOR, pydantic's, naming the field and the bound:
        the body never reaches the generator. The generator refuses the
        same answer in its own words (``invalid_wizard_answer``), so the
        shape of the 422 is what says which one answered.

        RED under mutant "le=3600 restored", in all three cases, the
        generator's refusal answering where the door's was due, observed
        verbatim ([120] shown, the repeated dict elided):

            E   AssertionError: {'code': 'invalid_wizard_answer', 'detail':
                'unguided_exposure_s is the longest sub a lane with no guider
                is held to, and 120 s is at or past the 120 s the doctor
                warns from (rule 2: stars trail): answer less than 120 s'}
            E   assert False
            E    +  where False = isinstance({'code':
                'invalid_wizard_answer', 'detail': ...}, list)

        RED under mutant "the door one under the line", all three, the
        bound named being 119 ([120] shown):

            E   AssertionError: {'ctx': {'lt': 119.0}, 'input': 120, 'loc':
                ['body', 'unguided_exposure_s'], 'msg': 'Input should be
                less than 119', ...}
            E   assert {'lt': 119.0} == {'lt': 120}
        """
        r = client.post("/api/flows/wizard", json={
            "kind": KIND_DEEP_SKY, "options": [], "target": NAME,
            "unguided_exposure_s": secs})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, list), detail
        (err,) = detail
        assert err["loc"][-1] == "unguided_exposure_s", err
        assert err["type"] == "less_than", err
        assert err["ctx"] == {"lt": doctor.UNGUIDED_SUB_LINE_S}, err

    def test_control_an_answer_under_the_line_is_written_and_clean(self,
                                                                   client):
        """Control: 119 s through the route is written onto the CAPTURE LOOP
        and the saved graph passes the doctor; no answer is the default.

        RED under mutant "the door one under the line", observed verbatim:

            E   AssertionError: {"detail":[{"type":"less_than","loc":["body",
                "unguided_exposure_s"],"msg":"Input should be less than
                119","input":119,"ctx":{"lt":119.0}}],"code":
                "invalid_request"}
            E   assert 422 == 200

        and under "the wizard refuses from a second under the line", the
        same ``assert 422 == 200`` with the generator's
        ``invalid_wizard_answer`` for 119 s.
        """
        r = client.post("/api/flows/wizard", json={
            "kind": KIND_DEEP_SKY, "options": [], "target": NAME,
            "unguided_exposure_s": 119})
        assert r.status_code == 200, r.text
        graph = FlowGraph.model_validate(r.json()["graph"])
        assert _capture(graph).params["exposure"] == 119
        assert not _loud(graph), _loud(graph)
        r = client.post("/api/flows/wizard", json={
            "kind": KIND_DEEP_SKY, "options": [], "target": NAME})
        assert r.status_code == 200, r.text
        graph = FlowGraph.model_validate(r.json()["graph"])
        assert _capture(graph).params["exposure"] == UNGUIDED_EXPOSURE_DEFAULT
