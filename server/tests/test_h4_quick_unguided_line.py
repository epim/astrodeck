"""An unguided quick flow stays under the unguided line: a sub at or past
``doctor.UNGUIDED_SUB_LINE_S`` is refused, 422 ``invalid_quick_flow``, in
words naming the filter and the limit (#518; #189 spec 1.8; H4
orchestrator ruling 5).

THE DEFECT (#518). ``POST /api/flows/quick`` with ``guided: false`` and
narrowband filters generated a flow the doctor warned about at once, at the
generator's own defaults: narrowband is 180 s (``QUICK_NARROWBAND_
EXPOSURE_S``), and with no GUIDE stage the doctor's rule 2 warns from 120 s,
"180s subs with no GUIDE upstream - stars will trail at any real focal
length". ``quick`` wraps ``generate()`` but sets its own exposures after the
wizard's unguided cap was chosen, so #432's cap never reached it, and
``run: true`` started the warned flow in the same request. The sheet's Guide
switch said "the subs are limited by the mount", and nothing limited them.

RULING 5: REFUSE, the issue's first option, as the wizard door's rows are
refused (``_within_the_unguided_cap``). The line is read at call time from
the doctor's one constant, so moving it moves the refusal. Narrowband is the
ruling's case, where the generator's own default lands. BROADBAND AT OR PAST
THE LINE IS REFUSED BY THE SAME RULE, and one channel with it: the doctor's
rule 2 warns on every unguided sub of that length whatever its filter, and
the issue's matrix grades all three lanes. A guided flow is never held to
it: rule 2 asks only of a stage with no GUIDE upstream.

THE MATRIX: guided and unguided x broadband, narrowband and one channel x a
typed 119 and 120. Every flow is doctor-clean (nothing above a note) or
refused with 422 and not saved; none is saved with the warning.

Each mutant ran in a private copy of ``server/`` (scratchpad
``H4-FLOWS-mut``), ``wizard.py`` (or ``doctor.py``) mutated from a byte
backup and restored with its sha256 checked, never in the shared tree.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows import doctor, wizard
from astrodeck.flows.doctor import check as flow_doctor
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import parse_cycle_plan
from astrodeck.flows.store import FlowStore

#: A real catalogue row, in the strings the TARGET node stores.
NGC6946 = {"name": "NGC 6946", "ra": "20h 34m 52s", "dec": "+60 09 14"}

#: ``lane -> (filters, the exposures key)``: the one-channel lane posts no
#: filter and keys its exposure by the sheet's label (``OSC_LABEL``).
LANES = {"broadband": (["L"], "L"), "narrowband": (["Ha"], "Ha"),
         "one channel": ([], "OSC")}


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """test_flows_quick's isolation: ``isolated_config`` (#341, and #587 for
    this fixture) gives the config store, CONFIG_DIR and the capture root,
    swept into every loaded ``astrodeck`` module rather than the three this
    fixture used to list by name -- nothing here reads the developer's real
    library, and no wheel is connected (the assumed wheel applies). The flow
    store move stays this fixture's own."""
    monkeypatch.setattr(app_module, "flow_store",
                        FlowStore(tmp_path / "flows"))
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        yield c


def test_w3_the_isolated_store_reaches_modules_outside_the_old_three(client):
    """#587 (WP-27c): this fixture used to patch ``config_store`` directly on
    only THREE modules (``astrodeck.config``, ``astrodeck.hub``,
    ``astrodeck.api.app``). ``astrodeck.planning`` binds its own
    ``from .config import config_store`` name at import time
    (``astrodeck/planning.py``), so under the old three-module patch it kept
    reading the worker's session-wide store while the three patched modules
    read this test's -- a module picked at random by which route it happens
    to serve would answer from a DIFFERENT config than the rest. Switching
    to ``isolated_config`` sweeps every loaded ``astrodeck`` module's
    ``config_store`` together, so all of them agree.

    RED under mutant (fixture reverted to the three-module patch), observed:

        >       assert planning_mod.config_store is config_mod.config_store, (
        E       AssertionError: astrodeck.planning reads a config_store
        other than astrodeck.config's: the isolation did not reach every
        module
        E       assert <astrodeck.config.ConfigStore object at 0x...> is
        <astrodeck.config.ConfigStore object at 0x...>
    """
    import astrodeck.config as config_mod
    import astrodeck.planning as planning_mod
    assert planning_mod.config_store is config_mod.config_store, (
        "astrodeck.planning reads a config_store other than astrodeck."
        "config's: the isolation did not reach every module")


def _mine(c) -> list[dict]:
    """The library's cards that are not Examples: what the route saved."""
    return [card for card in c.get("/api/flows").json()
            if not card.get("readonly")]


def _loud(graph: FlowGraph) -> list[str]:
    """The doctor's issues above a note, the acceptance bar's measure."""
    return [i.text for i in flow_doctor(graph) if i.level != "note"]


def _refusal(line, subs: list[str]) -> str:
    """The sentence ``quick`` refuses with, for ``subs`` like "Ha 180 s"."""
    one = len(subs) == 1
    return (f"an unguided quick flow holds every sub under {line:g} s, where "
            f"the doctor warns that stars trail with no GUIDE stage (rule 2), "
            f"and {', '.join(subs)} {'is' if one else 'are'} at or past it: "
            f"turn Guide on, or shorten {'it' if one else 'them'}")


class TestTheMatrix:
    @pytest.mark.parametrize("typed", [119, 120])
    @pytest.mark.parametrize("lane", list(LANES))
    @pytest.mark.parametrize("guided", [True, False],
                             ids=["guided", "unguided"])
    def test_doctor_clean_or_refused_never_saved_with_the_warning(
            self, client, guided, lane, typed):
        """Refused exactly when the lane is unguided and the sub is at or
        past the doctor's line; otherwise saved, and the saved graph, read
        back from the library, draws nothing above a note.

        RED under the wizard.py mutant "quick applies no unguided check" (the
        ``_within_the_unguided_line`` calls made ``pass``), on the three
        unguided 120 cases, observed (narrowband):

            E   AssertionError: an unguided 120 s sub was saved: 200
                {"flow":{"id":"091a4241c4dc42adab72b6589ebb3ef0","name":"Quick:
                NGC 6946","folder":"My flows","tagline":"3 subs each of Ha on
                NGC 6946: 3 frames, unguided","graph":{"nodes":[{"id":"n1",[...]
            E   assert 200 == 422

        RED under the wizard.py mutant "the line read as a literal"
        (``_within_the_unguided_line``'s ``line = doctor.UNGUIDED_SUB_LINE_S``
        made ``line = 120``) with ``doctor.py``'s ``UNGUIDED_SUB_LINE_S =
        120`` moved to 100 in the same copy: on the three unguided 119
        cases, observed (broadband):

            E   AssertionError: an unguided 119 s sub was saved: 200
                {"flow":{"id":"35a1095c5c6a4c5684dd3fcdab03f60b",[...]
            E   assert 200 == 422

        and on the three unguided 120 cases, refused in the literal's words,
        "- ub under 100 s, wher / + ub under 120 s, wher". With the constant
        moved to 100 and ``wizard.py`` as built, all 22 cases of this file
        passed: the refusal moved with the line. RED under "the line
        refuses from half of it" on the three unguided 119 cases (``assert
        422 == 200``), and a comment added to ``wizard.py`` left every case
        green (the control on the harness).
        """
        filters, key = LANES[lane]
        line = doctor.UNGUIDED_SUB_LINE_S
        before = len(_mine(client))
        r = client.post("/api/flows/quick", json={
            "target": NGC6946, "subs": 3, "filters": filters,
            "exposures": {key: typed}, "guided": guided})
        if not guided and typed >= line:
            assert r.status_code == 422, (
                f"an unguided {typed} s sub was saved: {r.status_code} "
                f"{r.text[:200]}")
            detail = r.json()["detail"]
            assert detail["code"] == "invalid_quick_flow"
            assert detail["detail"] == _refusal(line, [f"{key} {typed} s"])
            assert len(_mine(client)) == before, "a refused flow was saved"
            return
        assert r.status_code == 200, r.text
        fid = r.json()["flow"]["id"]
        graph = FlowGraph.model_validate(
            client.get(f"/api/flows/{fid}").json()["graph"])
        assert _loud(graph) == [], (
            f"saved with the doctor's warning: {_loud(graph)}")


class TestTheGeneratorsOwnDefaults:
    def test_unguided_narrowband_at_its_default_is_refused_naming_each(self):
        """Ruling 5's case: nothing typed, and the generator's 180 s
        narrowband default lands past the line. Every such filter is named,
        in wheel order, and the broadband one under the line is not.

        RED under "quick applies no unguided check", observed:

            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError) as e:
            wizard.quick(NGC6946, 10, ["SII", "L", "Ha", "OIII"],
                         guided=False)
        assert str(e.value) == _refusal(
            doctor.UNGUIDED_SUB_LINE_S, ["Ha 180 s", "OIII 180 s", "SII 180 s"])

    @pytest.mark.parametrize("filters", [["L"], ["L", "R", "G", "B"], []],
                             ids=["L", "LRGB", "one channel"])
    def test_control_unguided_broadband_and_one_channel_defaults_are_clean(
            self, filters):
        """CONTROL. At their defaults (60 s) an unguided broadband flow and
        an unguided one-channel flow are under the line: generated, and
        silent above a note.

        RED under the wizard.py mutant "the line refuses from half of it"
        (the comparison made ``secs >= line / 2``), observed (the L case):

            ValueError: an unguided quick flow holds every sub under 120 s,
            [...] and L 60 s is at or past it: turn Guide on, or shorten it
        """
        rec = wizard.quick(NGC6946, 8, filters, guided=False)
        assert "guide" not in [n.type for n in rec.graph.nodes]
        assert _loud(rec.graph) == []

    @pytest.mark.parametrize("secs", [120, 180, 600])
    def test_control_a_guided_flow_is_never_held_to_it(self, secs):
        """CONTROL. Guided, any sub the FILTER CYCLE takes is generated as
        typed, and the doctor is silent: rule 2 asks only of a stage with
        no GUIDE upstream.

        RED under the wizard.py mutant "guided is held too" (the ``if
        guided: return`` removed), observed (the 120 case):

            ValueError: an unguided quick flow holds every sub under 120 s,
            [...] and L 120 s, Ha 120 s are at or past it: turn Guide on, or
            shorten them
        """
        rec = wizard.quick(NGC6946, 4, ["L", "Ha"], {"L": secs, "Ha": secs})
        plan = next(n for n in rec.graph.nodes if n.type == "cycle")
        assert parse_cycle_plan(plan.params["plan"]) == [("L", secs),
                                                         ("Ha", secs)]
        assert _loud(rec.graph) == []


class TestTheWrittenSubIsTheOneJudged:
    @pytest.mark.parametrize("filters, key", [(["Ha"], "Ha"), ([], "OSC")],
                             ids=["cycle", "one channel"])
    def test_a_typed_119_6_is_written_120_and_refused(self, filters, key):
        """The generator rounds a typed exposure to whole seconds, so 119.6
        is written as 120, where the doctor warns. The sub judged is the one
        the flow would hold, not the one typed.

        RED under the wizard.py mutant "the typed value, not the written
        one" (the cycle lane judged on ``float(exposures_s[f])`` and the
        one channel on the raw value, before rounding), observed (cycle):

            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError) as e:
            wizard.quick(NGC6946, 3, filters, {key: 119.6}, guided=False)
        assert str(e.value) == _refusal(doctor.UNGUIDED_SUB_LINE_S,
                                        [f"{key} 120 s"])


class TestTheLineIsReadAtCallTime:
    def test_moving_the_doctors_line_moves_the_refusal(self, monkeypatch):
        """ONE CONSTANT, READ WHERE IT IS KEPT. With the line moved to 60 s
        in ``doctor`` alone, an unguided L of 60 s is refused naming 60 s,
        and one of 59 s is generated and passes the doctor, which reads the
        same moved line.

        RED under "the line read as a literal" (``line = 120``), observed:

            Failed: DID NOT RAISE <class 'ValueError'>
        """
        monkeypatch.setattr(doctor, "UNGUIDED_SUB_LINE_S", 60)
        with pytest.raises(ValueError) as e:
            wizard.quick(NGC6946, 3, ["L"], {"L": 60}, guided=False)
        assert str(e.value) == _refusal(60, ["L 60 s"])
        rec = wizard.quick(NGC6946, 3, ["L"], {"L": 59}, guided=False)
        assert _loud(rec.graph) == []
        at_60 = wizard.quick(NGC6946, 3, ["L"], {"L": 60}).graph
        assert _loud(at_60) == [], "premise: guided, 60 s is clean"
