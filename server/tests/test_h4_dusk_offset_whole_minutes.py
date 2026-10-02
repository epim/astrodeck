# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A DUSK WINDOW offset is a whole number of minutes, refused at the save in
the DUSK WINDOW's words, and a refused ``schedule`` field names the DUSK
WINDOW, not a TARGET (#483; #189 spec 3.2; H4).

THREE READINGS OF ONE PARAM (#483). An offset with a fractional part, -30.7,
was stored by a save (``FlowGraph.validation_errors`` said nothing), emitted
unchanged by the compile (``start_offset_min`` -30.7, so Tonight opened its
window 30.7 min before dusk), cut toward zero by the brief ("(−30 min)"), and
refused by ``/run``, because ``Schedule.start_offset_min`` is an ``int``, in
words naming a TARGET: "TARGET M31 - Andromeda: schedule.start_offset_min of
-30.7 cannot be used - input should be a valid integer, got a number with a
fractional part." A POOL's four members each got that clause, four sentences
about one card none of them owns.

THE ISSUE'S FIRST OPTION, for the #328 precedent: a param refused at the
save is never read three ways downstream. ``validation_errors`` refuses an
offset that is a finite number with a fractional part, beside
``COUNT_PARAMS`` (``WHOLE_MINUTE_PARAMS``, ``WHOLE_MINUTES_REFUSAL``), so
the save and ``/run`` answer 422 and the compile routes list it under
``structural``. An infinity or a NaN is NOT refused here: #423 settled that
a save stores one and the brief says it cannot be read, and
``test_s7_tonight_brief_blocks.py`` holds that. And ``to_plan`` names the
DUSK WINDOW for a ``schedule`` field the DUSK WINDOW wrote
(``_refused_values``' ``where``), said once for the plan however many
targets carry it; a field a POOL wrote over it (its floor, its hour angle)
still names the member, because that card holds the value.

Every test names the mutation it guards and quotes the failure observed.
Each mutant ran in a private copy of ``server/`` (scratchpad
``H4-FLOWS-mut``), the file mutated from a byte backup and restored with
its sha256 checked, never in the shared tree.
"""
from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan
from astrodeck.flows.tonight import brief, resolve_tonight
from astrodeck.hub import Hub

#: The clause pydantic's int field answers a fractional float with, as
#: ``_refused_values`` writes it.
FRACTIONAL = ("input should be a valid integer, got a number with a "
              "fractional part")


def _example(eid: str, ntype: str, **params) -> FlowGraph:
    """Example ``eid`` with every ``ntype`` node's params updated, through
    ``model_validate`` of the dumped graph, as a stored file is read."""
    ex = next(e for e in examples() if e.id == eid)
    raw = ex.graph.model_dump(by_alias=True)
    for n in raw["nodes"]:
        if n["type"] == ntype:
            n["params"].update(params)
    return FlowGraph.model_validate(raw)


def _dusk_id(graph: FlowGraph) -> str:
    (dusk,) = [n.id for n in graph.nodes if n.type == "dusk"]
    return dusk


def _refusal(graph: FlowGraph) -> str:
    """What ``to_sequence_plan`` refuses ``graph`` with, as ``/run`` and the
    compile routes read it."""
    with pytest.raises(GraphNotRunnable) as e:
        to_sequence_plan(compile_plan(graph), graph, flow_id="f1")
    return str(e.value)


# ======================================================== the save refuses

class TestTheSaveRefusesAFraction:
    #: ``offset -> (floor, ceil)`` the sentence offers.
    FRACTIONS = {-30.7: (-31, -30), "-30.7": (-31, -30), 0.5: (0, 1),
                 -0.25: (-1, 0), "12.5": (12, 13)}

    @pytest.mark.parametrize("value", list(FRACTIONS), ids=repr)
    def test_validation_names_the_dusk_window(self, value):
        """One sentence, naming the DUSK WINDOW card, its node, the value as
        stored, and the two whole minutes either side of it.

        RED under the models.py mutant "validation accepts -30.7" (the
        WHOLE_MINUTE_PARAMS loop in ``validation_errors`` made ``for key in
        ():``), on every value, observed (the -30.7 case):

            E       AssertionError: a fractional DUSK offset saved with no word
            E       assert [] == ["DUSK WINDOW...e -31 or -30"]
            E         Right contains one more item: "DUSK WINDOW 'n1': 'offset'
                      is -30.7, and the window opens a whole number of minutes
                      from dusk: use -31 or -30"

        RED under the models.py mutant "every number is refused" (the
        predicate's fraction test inverted, ``not number.is_integer()``), on
        every value here too, the same failure.
        """
        graph = _example("example-m31", "dusk", offset=value)
        lo, hi = self.FRACTIONS[value]
        want = (f"DUSK WINDOW {_dusk_id(graph)!r}: 'offset' is {value!r}, "
                f"and the window opens a whole number of minutes from dusk: "
                f"use {lo} or {hi}")
        assert graph.validation_errors() == [want], (
            "a fractional DUSK offset saved with no word")

    #: Whole offsets, and what is not validation's to judge: blank text and
    #: letters (the compile reads them as no offset), an infinity, a NaN and
    #: an integer past a float's range (#423: a save stores them and the
    #: brief says they cannot be read), and a float with no fractional part.
    WHOLE = [-30, "-30", -30.0, "-30.0", 45, 0, "", "abc", None, "inf",
             float("-inf"), "nan", json.loads("1" + "0" * 399), 1e300, True]

    @pytest.mark.parametrize("value", WHOLE, ids=repr)
    def test_control_a_whole_offset_and_an_unreadable_one_are_not_refused(
            self, value):
        """CONTROL. Nothing but a finite fraction is refused.

        RED under the models.py mutant "infinity judged fractional" (the
        predicate's ``math.isfinite`` guard removed, so ``is_integer()``,
        which is False for an infinity and a NaN, decides alone), on "inf",
        -inf and "nan": validation itself raised, on the sentence's
        ``math.floor``, observed:

            E       OverflowError: cannot convert float infinity to integer
            E       OverflowError: cannot convert float infinity to integer
            E       ValueError: cannot convert float NaN to integer

        (a stored infinity is #423's, and ``test_s7_tonight_brief_blocks``
        needs the save to store it). RED under the models.py mutant "any
        float is a fraction" (the predicate made ``if not
        isinstance(value, float) or not math.isfinite(number)``), on -30.0
        and 1e300, observed (the -30.0 case):

            E       assert ["DUSK WINDOW...e -30 or -30"] == []
            E         Left contains one more item: "DUSK WINDOW 'n1': 'offset'
                      is -30.0, and the window opens a whole number of minutes
                      from dusk: use -30 or -30"

        (and, the other way, the texts '-30.7' and '12.5' went unrefused in
        the case above). RED under "every number is refused" on -30, '-30',
        -30.0, '-30.0', 45, 0, 1e300 and True, the same shape of failure.
        """
        graph = _example("example-m31", "dusk", offset=value)
        assert graph.validation_errors() == []

    def test_every_dusk_window_is_read_not_only_the_first(self):
        """Two DUSK WINDOWs, the second holding the fraction: the compile
        reads only the first, but the card is on the canvas and a save must
        not keep a value no reader can use.

        RED under the models.py mutant "the first DUSK only" (the loop's
        nodes made ``[x for x in self.nodes if x.type == "dusk"][:1]``),
        observed:

            E       assert [] == ["DUSK WINDOW...e -31 or -30"]
            E         Right contains one more item: "DUSK WINDOW 'd2': 'offset'
                      is -30.7, and the window opens a whole number of minutes
                      from dusk: use -31 or -30"
        """
        ex = next(e for e in examples() if e.id == "example-m31")
        raw = ex.graph.model_dump(by_alias=True)
        raw["nodes"].append({"id": "d2", "type": "dusk", "x": 900.0,
                             "y": 900.0, "params": {"offset": -30.7}})
        graph = FlowGraph.model_validate(raw)
        assert graph.validation_errors() == [
            "DUSK WINDOW 'd2': 'offset' is -30.7, and the window opens a "
            "whole number of minutes from dusk: use -31 or -30"]


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """test_flows_quick's isolation: ``isolated_config`` (#341, and #587 for
    this fixture) gives the config store, CONFIG_DIR and the capture root,
    swept into every loaded ``astrodeck`` module rather than the three this
    fixture used to list by name -- nothing here reads the developer's real
    library. The flow store move stays this fixture's own."""
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
    read this test's. Switching to ``isolated_config`` sweeps every loaded
    ``astrodeck`` module's ``config_store`` together, so all of them agree.

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


class TestTheRoutesSayIt:
    def test_a_save_is_a_422_and_the_draft_compile_lists_it(self, client):
        """``POST /api/flows`` refuses the flow with the sentence, and the
        editor's draft compile lists it under ``structural`` and names the
        DUSK WINDOW in the plan's refusal, where it named the TARGET.

        RED under "validation accepts -30.7", observed:

            E       AssertionError: a fractional offset was saved
            E       assert 200 == 422

        RED under the to_plan.py mutant "refusal names the TARGET" (the
        ``where`` branch for a DUSK-written ``schedule`` field made ``if
        False:``), observed:

            E       assert 'TARGET M31 -...ctional part.' == 'DUSK
                    WINDOW:...ctional part.'
            E         - DUSK WINDOW: schedule.start_offset_min of -30.7 cannot
                      be used - input should be a valid integer, got a number
                      with a fractional part.
            E         + TARGET M31 - Andromeda: schedule.start_offset_min of
                      -30.7 cannot be used - input should be a valid integer,
                      got a number with a fractional part.
        """
        graph = _example("example-m31", "dusk", offset=-30.7)
        body = graph.model_dump(by_alias=True)
        # Written out, not read from ``validation_errors``: under a mutant
        # that refuses nothing, the route must be the thing that fails.
        sentence = (f"DUSK WINDOW {_dusk_id(graph)!r}: 'offset' is -30.7, and "
                    f"the window opens a whole number of minutes from dusk: "
                    f"use -31 or -30")
        r = client.post("/api/flows", json={"flow": {"name": "fraction",
                                                     "graph": body}})
        assert r.status_code == 422, "a fractional offset was saved"
        assert sentence in r.json()["detail"]["detail"], r.text
        assert all(c["name"] != "fraction"
                   for c in client.get("/api/flows").json())

        out = client.post("/api/flows/compile",
                          json={"graph": body, "name": "fraction"})
        assert out.status_code == 200, out.text
        answer = out.json()
        assert answer["structural"] == [sentence]
        (plan,) = [u for u in answer["unmapped"] if u["key"] == "plan"]
        assert plan["detail"] == (
            f"DUSK WINDOW: schedule.start_offset_min of -30.7 cannot be used "
            f"- {FRACTIONAL}.")


# ============================================ the plan's refusal names it

class TestTheRefusalNamesTheDuskWindow:
    @pytest.mark.parametrize("eid", ["example-m31", "example-m31-mosaic",
                                     "example-pool", "example-campaign"])
    def test_one_clause_naming_the_dusk_window(self, eid):
        """A single TARGET, a 3x2 mosaic's six panels, a POOL's four members
        and the Campaign's pool: each is one clause, naming the DUSK WINDOW
        that holds the value, not the targets that carry a copy of it.

        RED under the to_plan.py mutant "refusal names the TARGET", on
        every Example, observed (the single TARGET, then the pool):

            E       AssertionError: TARGET M31 - Andromeda:
                    schedule.start_offset_min of -30.7 cannot be used - input
                    should be a valid integer, got a number with a fractional
                    part.

            E       AssertionError: TARGET POOL member M16:
                    schedule.start_offset_min of -30.7 cannot be used - input
                    should be a valid integer, got a number with a fractional
                    part. TARGET POOL member M17: schedule.start_offset_min of
                    -30.7 cannot be used - [...] TARGET POOL member M8: [...]

        (the mosaic's is "TARGET M31: [...]", one clause already: its six
        panels share one block label).
        """
        graph = _example(eid, "dusk", offset=-30.7)
        got = _refusal(graph)
        assert got == (f"DUSK WINDOW: schedule.start_offset_min of -30.7 "
                       f"cannot be used - {FRACTIONAL}."), got

    def test_control_a_pools_own_constraint_still_names_the_member(self):
        """CONTROL. A POOL's hour angle rides in each member's ``schedule``
        too, but the POOL card holds it (``_pool_overrides``), so a value
        past ``Schedule``'s bound still names the members, as it did.

        RED under the to_plan.py mutant "every schedule field is the DUSK's"
        (the ``where`` branch asking only that the path starts with
        ``schedule``, its ``rest[1] in dusk_fields[index]`` removed),
        observed:

            E       AssertionError: DUSK WINDOW: schedule.max_hour_angle_h of
                    13 cannot be used - input should be less than or equal to
                    12.
        """
        graph = _example("example-pool", "pool", maxHA=13)
        got = _refusal(graph)
        members = ("M16", "M17", "M8", "NGC 6946")
        assert got == " ".join(
            f"TARGET POOL member {m}: schedule.max_hour_angle_h of 13 cannot "
            f"be used - input should be less than or equal to 12."
            for m in members), got

    def test_control_two_faults_in_one_schedule_each_name_their_own_card(
            self):
        """CONTROL. Each member's ``schedule`` holds keys from two cards, the
        DUSK WINDOW's and the POOL's, and with a fault in each at once, each
        clause names the card that holds its value: the offset the DUSK
        WINDOW (once), the hour angle each member.

        RED under "every schedule field is the DUSK's", observed:

            E       AssertionError: DUSK WINDOW: schedule.start_offset_min of
                    -30.7 cannot be used - input should be a valid integer,
                    got a number with a fractional part. DUSK WINDOW:
                    schedule.max_hour_angle_h of 13 cannot be used - input
                    should be less than or equal to 12.

        and under "refusal names the TARGET", observed:

            E       AssertionError: TARGET POOL member M16:
                    schedule.start_offset_min of -30.7 cannot be used - [...]
                    TARGET POOL member M16: schedule.max_hour_angle_h of 13
                    cannot be used - [...]
        """
        ex = next(e for e in examples() if e.id == "example-pool")
        raw = ex.graph.model_dump(by_alias=True)
        for n in raw["nodes"]:
            if n["type"] == "dusk":
                n["params"]["offset"] = -30.7
            if n["type"] == "pool":
                n["params"]["maxHA"] = 13
        got = _refusal(FlowGraph.model_validate(raw))
        assert got.startswith(
            f"DUSK WINDOW: schedule.start_offset_min of -30.7 cannot be used "
            f"- {FRACTIONAL}. TARGET POOL member M16: "
            f"schedule.max_hour_angle_h of 13"), got
        assert got.count("DUSK WINDOW") == 1, got


# =========================================== a whole offset reads as before

#: A synthetic site (40 N 105 W, the S7 brief test's, NOT the observatory's)
#: and an instant on its night.
SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()


@pytest.fixture
def synthetic_hub(monkeypatch):
    """The hub on the synthetic site, so nothing the answer is built from
    can come from the configured one."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))


class TestAWholeOffsetReadsAsBefore:
    def test_minus_30_saves_briefs_and_opens_the_window_30_min_early(
            self, synthetic_hub):
        """CONTROL (#483's own). -30 is stored, briefs "(−30 min)", opens
        Tonight's window 30 min before dusk, and reaches the plan as -30.

        RED under the models.py mutant "every number is refused" (the
        predicate's fraction test inverted, ``not number.is_integer()``), at
        the save, observed:

            >       assert graph.validation_errors() == []
            E       assert ["DUSK WINDOW...e -30 or -30"] == []
            E         Left contains one more item: "DUSK WINDOW 'n1': 'offset'
                      is -30, and the window opens a whole number of minutes
                      from dusk: use -30 or -30"

        The brief and the window are read by files H4 did not change
        (``tonight.py``); this pins that the refusal left them as they were.
        """
        graph = _example("example-m31", "dusk", offset=-30)
        assert graph.validation_errors() == []
        assert brief(graph).startswith(
            "This flow arms at astronomical dusk (−30 min)"), brief(graph)
        out = resolve_tonight(graph, SITE, now=JUNE, twilight_deg=-12.0)
        night = out["night"]
        assert night["window_start_unix"] == night["dusk_unix"] - 1800.0
        plan, _ = to_sequence_plan(compile_plan(graph), graph, flow_id="f1")
        assert [t.schedule.start_offset_min for t in plan.targets] == [-30]
