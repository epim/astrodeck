# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The wizard asks the NIGHT and RESUME questions (#196, backlog WP-100,
wave 15; orchestrator ruling for WP-100: the auto-resume answer writes DUSK
WINDOW's ``autoResume``, On or Off, default On).

Send to Flow Wizard's stepped sheet shipped in S6 with five steps and said so:
the stop condition and the auto-resume steps were "not built" (#191, #195),
because DUSK WINDOW had nowhere to put either answer. Both exist since waves
1 and 14, so the route takes six more answers, each of which lands on the ONE
DUSK node the lane opens with (``wizard._generate``'s ``lane[0]``):

  * ``stop``       "Dawn" or "Clock time" (``DUSK_STOPS``). "None" is refused:
                   the compile warns that such a run "images into daylight and
                   does not park", and the wizard's bar is the doctor at note
                   level, so the one wizard answer that makes the compile warn
                   is not offered;
  * ``stop_clock`` the time of a "Clock time" stop, strict "HH:MM";
  * ``start``      Astro, Nautical or Civil dusk, or "Clock time"
                   (``DUSK_STARTS``, the compile's own twilight table plus the
                   clock choice);
  * ``start_clock``the time of a "Clock time" start;
  * ``min_alt``    the lowest altitude a target is shot at, 0 to 90 degrees;
  * ``auto_resume`` "On" or "Off" (``AUTO_RESUME_CHOICES``), written as
                   ``autoResume``.

What is held here, by the work package's items:

  (a) A body with only the three original answers is today's answer, and the
      DUSK node is as it was created: an absent answer writes NOTHING, not
      even a value that happens to equal the node's own default
      (``TestAbsentAnswersWriteNothing``).
  (b) Each answer is written to its param and reaches the compile
      (``TestEachAnswerIsWritten``).
  (c) Every refusal is a 422 that names the answer, at the door and in the
      generator (``TestRefusals``).
  (d) The doctor bar, at note level or better, is held across the option
      matrix in ``test_flows_wizard.py``; the cases here are the ones that
      matrix cannot reach (``TestTheDoctorBar``).

Each mutant named below was applied from a byte backup inside the worktree,
the file restored byte-identically (sha256 compared) and the mutant text
grepped gone; the failure it produced is quoted.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import Optics
from astrodeck.flows import wizard
from astrodeck.flows.compile import _DUSK_START_TWILIGHT_DEG, compile_plan
from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import AUTO_RESUME_CHOICES as NODE_AUTO_RESUME
from astrodeck.flows.nodes import create_params
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import flow_store
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.wizard import (
    AUTOMATION_OPTIONS, KIND_DEEP_SKY, KIND_EAA, KIND_MOSAIC, KIND_POOL, KINDS,
    ROTATE_TO_PA, generate_answer)
from test_flows_wizard import (
    _OLD_M31, _WHEN, THREE_KINDS, WIZARD_PLANS_SHA256, _blank, _digest,
    _subsets)

#: A made-up framing centre, nobody's site: M31's neighbourhood, typed as a
#: door types it. Given, so no catalogue row stands behind it and the tests
#: here never ask the catalogue anything.
RA, DEC = "00h 43m 15.0s", "+41° 30' 00\""
NAME = "M31"

#: An IMX571 at 1000 mm: a bin-1 field of 1.346 x 0.900 deg.
OPTICS = Optics(focal_length_mm=1000.0, pixel_size_um=3.76,
                sensor_width_px=6248, sensor_height_px=4176)

#: Every NIGHT and RESUME answer at once, none of them a default.
NIGHT = dict(stop="Clock time", stop_clock="03:30", start="Clock time",
             start_clock="21:15", min_alt=42.5, auto_resume="Off")

#: The answers' own kwargs, for "absent writes nothing".
ANSWER_KEYS = ("stop", "stop_clock", "start", "start_clock", "min_alt",
               "auto_resume")

#: The DUSK params the wizard answers reach, by answer.
PARAM_OF = {"stop": "stop", "stop_clock": "stopClock", "start": "start",
            "start_clock": "startClock", "min_alt": "minAlt",
            "auto_resume": "autoResume"}


@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_store, "_dir", tmp_path / "flows", raising=False)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        yield c


def _post(c, **body):
    return c.post("/api/flows/wizard", json=body)


def _dusk(graph: FlowGraph):
    found = [n for n in graph.nodes if n.type == "dusk"]
    assert len(found) == 1, f"expected one dusk, got {len(found)}"
    return found[0]


def _answer(kind=KIND_DEEP_SKY, opts=(), target=NAME, **answers):
    kw = dict(answers)
    if kind != KIND_POOL:
        kw.setdefault("ra", RA)
        kw.setdefault("dec", DEC)
    if kind == KIND_MOSAIC:
        kw.update(rows=2, cols=2, angle_mode=ROTATE_TO_PA, pa_deg=10.0)
        kw.setdefault("rig", RigFacts(fov_deg=(0.9, 0.6), has_rotator=True))
    return generate_answer(kind, set(opts), target, **kw)


def _flat(r) -> str:
    """Everything a refusal says, whichever shape it came in: the generator's
    ``{"detail": {"detail": ..., "code": ...}}`` or the door's list of
    ``{"loc": [...], "msg": ...}``."""
    return json.dumps(r.json(), ensure_ascii=False)


# ================================================== (a) an absent answer is none

class TestAbsentAnswersWriteNothing:
    def test_the_three_answers_make_todays_plans_through_generate_answer(self):
        """The control, asked of ``generate_answer`` and not of ``generate``
        (test_flows_wizard.py's pin goes around it): the three original
        answers, every kind but Mosaic against all 64 chip subsets, compile
        to the plans ``WIZARD_PLANS_SHA256`` pins, so the six new keywords
        are inert at their defaults. The coordinates are the pre-S3 TARGET's
        own, typed as a door types them.

        RED under mutant "a new answer defaults to a value" (``generate_answer``
        taking ``min_alt=45``, written to the node when the caller gave none),
        observed verbatim (9 failed, 126 passed; this is the first of them):

            E   AssertionError: assert '43ea0e6b5012...8be159118cb4b' ==
                'd6b9797dcca8...cb6eb676ed1ae'

        GREEN, by construction, under mutant "an absent stop is written as
        Dawn" (``_door_dusk`` putting ``stop: "Dawn"`` in the params when
        ``stop`` is None): DUSK is created at Dawn, so the plan is the same.
        That is why the case after this one exists.
        """
        blobs = []
        for kind in THREE_KINDS:
            # A POOL has no TARGET to carry typed coordinates (refused), and
            # the pin's own ``coords`` never reached one.
            where = ({} if kind == KIND_POOL
                     else dict(ra=_OLD_M31[0], dec=_OLD_M31[1]))
            for opts in _subsets(AUTOMATION_OPTIONS):
                g = generate_answer(kind, opts, NAME, **where).record.graph
                plan, _ = to_sequence_plan(compile_plan(g, "w"), g,
                                           when=_WHEN)
                got = _blank(plan.model_dump(mode="json"))
                got["count_mode"] = "attempts"
                blobs.append(json.dumps(got, sort_keys=True))
        assert len(blobs) == 3 * 64
        assert _digest(blobs) == WIZARD_PLANS_SHA256

    @pytest.mark.parametrize("kind", KINDS)
    def test_an_absent_answer_leaves_the_dusk_node_as_it_was_created(
            self, kind, monkeypatch):
        """The digest above cannot tell "wrote nothing" from "wrote the
        node's own default": DUSK is created at Dawn, Astro dusk, 30 and On,
        which is what an answer that defaulted to those would write. So the
        node is created with values the answers never default to, and each
        absent answer must leave exactly what it was created with.

        RED under mutant "an absent stop is written as Dawn" (``_door_dusk``
        putting ``stop: "Dawn"`` into the params when ``stop`` is None), in
        all four kinds (4 failed, 131 passed), observed verbatim:

            E   AssertionError: absent stop wrote 'Dawn' over the created
                'Clock time'
            E   assert 'Dawn' == 'Clock time'

        and under mutant "a new answer defaults to a value" (``min_alt=45``
        in ``generate_answer``), in the same four:

            E   AssertionError: absent minAlt wrote 45 over the created 61
            E   assert 45 == 61
        """
        created = dict(create_params("dusk"), stop="Clock time",
                       stopClock="04:45", start="Civil dusk",
                       startClock="20:00", minAlt=61, autoResume="Off")
        real = wizard.create_params
        monkeypatch.setattr(
            wizard, "create_params",
            lambda t: dict(created) if t == "dusk" else real(t))
        got = _dusk(_answer(kind).record.graph).params
        for key, value in created.items():
            assert got[key] == value, (
                f"absent {key} wrote {got[key]!r} over the created {value!r}")
        # One answer given moves its own param and no other.
        one = _dusk(_answer(kind, stop="Dawn").record.graph).params
        assert one["stop"] == "Dawn"
        assert {k: v for k, v in one.items() if k != "stop"} == {
            k: v for k, v in created.items() if k != "stop"}

    def test_through_the_route_a_three_answer_body_leaves_the_dusk_as_created(
            self, client):
        """THE DOOR HOLDS NO DEFAULT EITHER. Four chip subsets through the
        route for every kind: the saved flow's DUSK is the created one,
        param for param.

        RED under mutant "the body defaults min_alt" (``FlowWizardBody``
        carrying ``min_alt: float | None = 45.0``), the only failure
        (1 failed, 134 passed), observed verbatim:

            E   AssertionError: ('Deep-sky target', []): the saved DUSK is
                {'start': 'Astro dusk', 'offset': -30, 'startClock': '',
                'stop': 'Dawn', 'stopClock': '', 'minAlt': 45, 'repeat':
                'Single night', 'autoResume': 'On'}, not the created one
            E     {'minAlt': 45} != {'minAlt': 30}
        """
        want = create_params("dusk")
        for kind in KINDS:
            for opts in (frozenset(), frozenset({"Guiding"}),
                         frozenset(AUTOMATION_OPTIONS) - {"Guiding"},
                         frozenset(AUTOMATION_OPTIONS)):
                body = dict(kind=kind, options=sorted(opts), target=NAME)
                if kind != KIND_POOL:
                    body.update(ra=RA, dec=DEC)
                if kind == KIND_POOL:
                    body["target"] = "M16, M17"
                # The Mosaic kind has no camera field in this fixture, so it
                # is answered as one target and never reads a grid.
                r = _post(client, **body)
                assert r.status_code == 200, (kind, sorted(opts), r.text)
                graph = FlowGraph.model_validate(r.json()["graph"])
                assert _dusk(graph).params == want, (
                    f"{(kind, sorted(opts))}: the saved DUSK is "
                    f"{_dusk(graph).params}, not the created one")


# ============================================================ (b) each, written

class TestEachAnswerIsWritten:
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_clock_time_stop_is_written_and_compiles_to_a_time_stop(
            self, kind):
        """Item (b): "Clock time" at "22:30" is ``stop`` and ``stopClock`` on
        the DUSK node, and the compile reads it as ``stop_mode`` "time" with
        ``stop_time`` "22:30", which is also what the plan the engine runs
        carries. Every kind, since every lane opens with the DUSK node. The
        start is the node's own, untouched.

        RED under mutant "the stop clock is not written" (``_door_dusk``
        writing ``stop`` and never ``stopClock``), in 14 of the file's cases
        (this one for all four kinds), observed verbatim:

            E   AssertionError: assert ('Clock time', '') == ('Clock time',
                '22:30')
            E     At index 1 diff: '' != '22:30'
        """
        g = _answer(kind, stop="Clock time", stop_clock="22:30").record.graph
        p = _dusk(g).params
        assert (p["stop"], p["stopClock"]) == ("Clock time", "22:30")
        assert (p["start"], p["minAlt"], p["autoResume"]) == (
            "Astro dusk", 30, "On")
        sched = compile_plan(g, "w")["schedule"]
        assert (sched["stop_mode"], sched["stop_time"]) == ("time", "22:30")
        plan, _ = to_sequence_plan(compile_plan(g, "w"), g, when=_WHEN)
        stops = {(t.schedule.stop_mode, t.schedule.stop_time)
                 for t in plan.targets}
        assert stops == {("time", "22:30")}, stops

    def test_dawn_is_a_dawn_stop_with_no_note(self):
        """The one stop the wizard offers beside the clock reads as ``dawn``
        and raises none of the compile's notes (the "Stop is None" warning
        is what ``DUSK_STOPS`` leaves "None" out for)."""
        g = _answer(stop="Dawn").record.graph
        out = compile_plan(g, "w")
        assert out["schedule"]["stop_mode"] == "dawn"
        assert not [n for n in out.get("notes", []) if n["node_id"]
                    == _dusk(g).id]

    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_MOSAIC])
    def test_a_clock_time_start_is_written_and_compiles_to_a_time_start(
            self, kind):
        """``start`` "Clock time" and its ``startClock`` are the node's, and
        the compile's ``start_mode`` is "time" with the text as ``start_time``.

        RED under mutant "the start clock goes to the stop" (``_door_dusk``
        writing ``start_clock`` as ``stopClock``), 2 here and 4 in the
        all-six case, observed verbatim:

            E   AssertionError: assert ('Clock time', '') == ('Clock time',
                '19:45')
            E     At index 1 diff: '' != '19:45'
        """
        g = _answer(kind, start="Clock time", start_clock="19:45"
                    ).record.graph
        p = _dusk(g).params
        assert (p["start"], p["startClock"]) == ("Clock time", "19:45")
        assert p["stopClock"] == ""
        sched = compile_plan(g, "w")["schedule"]
        assert (sched["start_mode"], sched["start_time"]) == (
            "time", "19:45")

    @pytest.mark.parametrize("start", [s for s in wizard.DUSK_STARTS
                                       if s != "Clock time"])
    def test_a_sun_based_start_is_its_own_twilight(self, start):
        """Each of the three compiles to its own Sun altitude (-18, -12,
        -6), which is why they are three answers and not one."""
        g = _answer(start=start).record.graph
        assert _dusk(g).params["start"] == start
        sched = compile_plan(g, "w")["schedule"]
        assert sched["twilight_deg"] == _DUSK_START_TWILIGHT_DEG[start]
        assert sched["start_mode"] == "dusk"

    @pytest.mark.parametrize("given, written",
                             [(0, 0), (45, 45), (45.0, 45), (45.5, 45.5),
                              (90, 90), (90.0, 90)])
    def test_min_alt_is_written_as_the_number_the_node_holds(
            self, given, written):
        """Whole degrees as an int, as the node's own 30 is (a float 45.0 in
        the params would be a different stored flow from a typed 45), and
        the compile's ``min_altitude_deg`` follows."""
        g = _answer(min_alt=given).record.graph
        got = _dusk(g).params["minAlt"]
        assert got == written and type(got) is type(written), (
            f"{given!r} was written as {got!r}")
        assert compile_plan(g, "w")["schedule"]["min_altitude_deg"] == written

    def test_auto_resume_off_is_the_plans_one_night_and_on_is_silent(self):
        """Off writes ``autoResume`` "Off", the compile carries
        ``resume_across_nights`` false, and the saved version is the one
        that cannot be misread by an older build (``schema_for``). On
        writes "On" and the compile says nothing, as every flow before the
        option did.

        RED under mutant "auto_resume is dropped" (``_door_dusk`` not putting
        ``autoResume`` in the params), 7 failed, observed verbatim:

            E   AssertionError: assert 'On' == 'Off'
            E     - Off
            E     + On
        """
        off = _answer(auto_resume="Off").record.graph
        assert _dusk(off).params["autoResume"] == "Off"
        assert compile_plan(off, "w")["resume_across_nights"] is False
        on = _answer(auto_resume="On").record.graph
        assert _dusk(on).params["autoResume"] == "On"
        assert "resume_across_nights" not in compile_plan(on, "w")

    @pytest.mark.parametrize("kind", KINDS)
    def test_all_six_answers_land_on_the_one_dusk_node(self, kind):
        """Together, for every kind: each param is the answer, nothing else
        on the node moved (``offset``, ``repeat``), and the graph has the
        one DUSK it always had."""
        g = _answer(kind, **NIGHT).record.graph
        created = create_params("dusk")
        want = dict(created)
        want.update({PARAM_OF[k]: v for k, v in NIGHT.items()})
        assert _dusk(g).params == want
        assert [n.type for n in g.nodes].count("dusk") == 1

    def test_a_blank_text_answer_is_no_answer(self):
        """A door that always sends its fields sends empty ones for what it
        does not know, and an empty answer is not an answer (as ``skip``
        and the coordinates are read): the graph is the one with the key
        left out."""
        blank = _answer(stop=" ", stop_clock="", start="", start_clock=" ",
                        auto_resume="  ").record.graph
        bare = _answer().record.graph
        assert blank.model_dump() == bare.model_dump()

    def test_through_the_route_the_answers_are_saved(self, client):
        """Every answer, through the route, in the saved flow: the stored
        record is what ``/compile`` reads back."""
        r = _post(client, kind=KIND_DEEP_SKY, options=["Guiding"], target=NAME,
                  ra=RA, dec=DEC, stop="Clock time", stop_clock="22:30",
                  start="Nautical dusk", min_alt=35, auto_resume="Off")
        assert r.status_code == 200, r.text
        got = client.get(f"/api/flows/{r.json()['id']}").json()
        p = _dusk(FlowGraph.model_validate(got["graph"])).params
        assert (p["stop"], p["stopClock"], p["start"], p["minAlt"],
                p["autoResume"]) == ("Clock time", "22:30", "Nautical dusk",
                                     35, "Off")
        out = client.post(f"/api/flows/{r.json()['id']}/compile").json()
        assert out["structural"] == []

    def test_through_the_route_a_clock_time_start_and_stop_are_saved(self, client):
        """The sheet's other NIGHT body: a "Clock time" START with its clock,
        beside a "Clock time" stop, posted as the sheet posts it. Every other
        positive door case here uses a sun-based start, so nothing else holds
        that ``FlowWizardBody`` carries ``start_clock`` through to the
        generator; the refusal cases above only see it from the other side.

        RED under mutant "the route drops start_clock" (the route forwarding
        ``start_clock=None`` to the generator): the generator then refuses the
        body ("start is 'Clock time', and this answer has no start_clock"), so
        this case gets a 422 where a 200 is asked for. Observed verbatim:

            E   AssertionError: (422, '{"detail":{"detail":"start is 'Clock time', and this answer has no start_clock: say the time as HH:MM","code":"invalid_wizard_answer"}}')
        """
        r = _post(client, kind=KIND_DEEP_SKY, options=[], target=NAME, ra=RA,
                  dec=DEC, start="Clock time", start_clock="21:15",
                  stop="Clock time", stop_clock="03:30")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        got = client.get(f"/api/flows/{r.json()['id']}").json()
        p = _dusk(FlowGraph.model_validate(got["graph"])).params
        assert (p["start"], p["startClock"], p["stop"], p["stopClock"]) == (
            "Clock time", "21:15", "Clock time", "03:30")
        out = client.post(f"/api/flows/{r.json()['id']}/compile").json()
        assert out["structural"] == []

    def test_a_mosaic_takes_the_night_answers_through_the_route(self, client):
        """The Mosaic kind, with a camera field, beside its grid answers: one
        TARGET block, and the DUSK node as asked."""
        client.store.set_optics(OPTICS)
        r = _post(client, kind=KIND_MOSAIC, options=["HFR watchdog"],
                  target=NAME, ra=RA, dec=DEC, rows=2, cols=3,
                  angle_mode=ROTATE_TO_PA, pa_deg=30, stop="Clock time",
                  stop_clock="02:00", auto_resume="Off")
        assert r.status_code == 200, r.text
        graph = FlowGraph.model_validate(r.json()["graph"])
        assert [n.type for n in graph.nodes].count("target") == 1
        p = _dusk(graph).params
        assert (p["stop"], p["stopClock"], p["autoResume"]) == (
            "Clock time", "02:00", "Off")


# ============================================================ the constants

class TestTheChoicesAreTheNodesOwn:
    def test_the_starts_are_the_compiles_three_twilights_and_the_clock(self):
        """``DUSK_STARTS`` is the compile's own twilight table plus "Clock
        time", in the order the editor offers them: a fourth twilight added
        to the compile is a wizard answer the day it is added, or this goes
        red."""
        assert wizard.DUSK_STARTS == (*_DUSK_START_TWILIGHT_DEG,
                                      "Clock time")

    def test_the_stops_are_exactly_those_the_compile_does_not_warn_on(self):
        """``DUSK_STOPS`` is the stops whose compile raises no note, and
        "None" is the one whose compile does: that is the whole reason the
        wizard does not offer it. Asked of the compile itself, so a stop it
        began to warn on, or stopped warning on, moves this."""
        quiet = []
        for stop in ("Dawn", "Clock time", "None"):
            p = dict(create_params("dusk"), stop=stop, stopClock="03:30")
            g = FlowGraph.model_validate({
                "nodes": [{"id": "n1", "type": "dusk", "x": 0, "y": 0,
                           "params": p}], "edges": []})
            notes = compile_plan(g, "w").get("notes", [])
            if not [n for n in notes if "Stop is None" in n["text"]]:
                quiet.append(stop)
        assert tuple(quiet) == wizard.DUSK_STOPS
        assert "None" not in wizard.DUSK_STOPS

    def test_auto_resume_choices_are_the_nodes(self):
        assert wizard.AUTO_RESUME_CHOICES == NODE_AUTO_RESUME == ("On", "Off")


# ================================================================ (c) refusals

#: ``(kwargs, the answer the refusal must name)``. The generator reads each
#: with ``ValueError``; the door answers 422 for the same body.
REFUSED = [
    pytest.param(dict(stop="None"), "stop", id="stop-None"),
    pytest.param(dict(stop="Dusk"), "stop", id="stop-unknown"),
    pytest.param(dict(stop="dawn"), "stop", id="stop-wrong-case"),
    pytest.param(dict(stop=True), "stop", id="stop-bool"),
    pytest.param(dict(stop=1), "stop", id="stop-number"),
    pytest.param(dict(stop=["Dawn"]), "stop", id="stop-list"),
    pytest.param(dict(stop="Clock time"), "stop_clock", id="clock-stop-no-clock"),
    pytest.param(dict(stop="Clock time", stop_clock=" "), "stop_clock",
                 id="clock-stop-blank-clock"),
    pytest.param(dict(stop="Clock time", stop_clock="25:00"), "stop_clock",
                 id="stop-clock-hour-25"),
    pytest.param(dict(stop="Clock time", stop_clock="22:60"), "stop_clock",
                 id="stop-clock-minute-60"),
    pytest.param(dict(stop="Clock time", stop_clock="7:30"), "stop_clock",
                 id="stop-clock-one-digit-hour"),
    pytest.param(dict(stop="Clock time", stop_clock="22:30:00"), "stop_clock",
                 id="stop-clock-seconds"),
    pytest.param(dict(stop="Clock time", stop_clock="2230"), "stop_clock",
                 id="stop-clock-no-colon"),
    pytest.param(dict(stop="Clock time", stop_clock="22.30"), "stop_clock",
                 id="stop-clock-dot"),
    pytest.param(dict(stop="Clock time", stop_clock="noon"), "stop_clock",
                 id="stop-clock-word"),
    pytest.param(dict(stop="Clock time", stop_clock="\u0662\u0662:\u0663\u0660"),
                 "stop_clock", id="stop-clock-non-ascii-digits"),
    pytest.param(dict(stop="Clock time", stop_clock=True), "stop_clock",
                 id="stop-clock-bool"),
    pytest.param(dict(stop="Clock time", stop_clock=2230), "stop_clock",
                 id="stop-clock-number"),
    pytest.param(dict(stop_clock="22:30"), "stop_clock",
                 id="stop-clock-without-a-stop"),
    pytest.param(dict(stop="Dawn", stop_clock="22:30"), "stop_clock",
                 id="stop-clock-beside-dawn"),
    pytest.param(dict(start="Civil"), "start", id="start-unknown"),
    pytest.param(dict(start="None"), "start", id="start-None"),
    pytest.param(dict(start="astro dusk"), "start", id="start-wrong-case"),
    pytest.param(dict(start=True), "start", id="start-bool"),
    pytest.param(dict(start=2), "start", id="start-number"),
    pytest.param(dict(start="Clock time"), "start_clock",
                 id="clock-start-no-clock"),
    pytest.param(dict(start="Clock time", start_clock="24:00"), "start_clock",
                 id="start-clock-hour-24"),
    pytest.param(dict(start="Clock time", start_clock=True), "start_clock",
                 id="start-clock-bool"),
    pytest.param(dict(start_clock="21:00"), "start_clock",
                 id="start-clock-without-a-start"),
    pytest.param(dict(start="Astro dusk", start_clock="21:00"), "start_clock",
                 id="start-clock-beside-a-sun-start"),
    pytest.param(dict(min_alt=95), "min_alt", id="min-alt-95"),
    pytest.param(dict(min_alt=90.5), "min_alt", id="min-alt-90.5"),
    pytest.param(dict(min_alt=-1), "min_alt", id="min-alt-negative"),
    pytest.param(dict(min_alt=-0.001), "min_alt", id="min-alt-just-below-0"),
    pytest.param(dict(min_alt=True), "min_alt", id="min-alt-bool"),
    pytest.param(dict(min_alt="45"), "min_alt", id="min-alt-string"),
    pytest.param(dict(min_alt=[30]), "min_alt", id="min-alt-list"),
    pytest.param(dict(min_alt=10 ** 400), "min_alt", id="min-alt-huge-int"),
    pytest.param(dict(auto_resume="Maybe"), "auto_resume", id="resume-unknown"),
    pytest.param(dict(auto_resume="on"), "auto_resume", id="resume-wrong-case"),
    pytest.param(dict(auto_resume="ON"), "auto_resume", id="resume-upper"),
    pytest.param(dict(auto_resume=True), "auto_resume", id="resume-bool-true"),
    pytest.param(dict(auto_resume=False), "auto_resume", id="resume-bool-false"),
    pytest.param(dict(auto_resume=1), "auto_resume", id="resume-number"),
]


class TestRefusals:
    @pytest.mark.parametrize("answers, named", REFUSED)
    def test_the_generator_refuses_and_names_the_answer(self, answers, named):
        """A ValueError that names the answer it refuses, for every shape
        in ``REFUSED``: "None" is not a stop; a clock that does not read as
        HH:MM, or a "Clock time" with no clock, or a clock with no "Clock
        time" to belong to; an altitude outside 0 to 90 or that is not a
        number; a bool, a number or a list where text is the answer.

        RED under mutant "stop None is accepted" (``checked_stop``'s refusal
        of "None" switched off and ``DUSK_STOPS`` gaining "None"), 3 failed
        (this case, the door's and the compile-table case above), observed
        verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>

        RED under mutant "a clock beside the wrong choice is taken"
        (``_clock_pair``'s second refusal switched off), 8 failed (every
        ``stop-clock-without-a-stop``, ``stop-clock-beside-dawn``,
        ``start-clock-without-a-start`` and ``start-clock-beside-a-sun-start``
        case here and at the door):

            E   Failed: DID NOT RAISE <class 'ValueError'>

        RED under mutant "a Clock time with no clock is taken"
        (``_clock_pair``'s first refusal switched off), 7 failed (the
        ``clock-stop-no-clock``, ``clock-stop-blank-clock`` and
        ``clock-start-no-clock`` cases here and at the door, and the 422
        code case below):

            E   Failed: DID NOT RAISE <class 'ValueError'>

        RED under mutant "a bool is an altitude" (``checked_min_alt`` no
        longer refusing ``bool``), 2 failed, observed verbatim:

            E   Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError) as e:
            _answer(**answers)
        assert named in str(e.value), (
            f"the refusal does not name {named!r}: {e.value}")

    @pytest.mark.parametrize("answers, named", REFUSED)
    def test_the_door_answers_422_and_names_the_answer(
            self, client, answers, named):
        """The same bodies through the route: a 422 whose body names the
        answer, and nothing saved. (A bool or a string for ``min_alt`` is
        refused BEFORE pydantic's float coercion, which would read ``true``
        as 1.0; a bool for a text answer is refused as text.)

        RED under mutant "the door coerces" (the ``_min_alt`` validator
        without ``mode="before"``), 2 failed (``min-alt-bool`` and
        ``min-alt-string``): ``{"min_alt": true}`` comes back 200 as a floor
        of one degree nobody asked for, and ``"45"`` as 45, observed
        verbatim for the first:

            E   AssertionError: ({'min_alt': True}, 200, '{"id":"e596...",
                "name":"M31",...,"params":{"start":"Astro dusk","offset":-30,
                "startClock":"","stop":"Dawn","stopClock":"","minAlt":1,...')
            E   assert 200 == 422
        """
        before = len(client.get("/api/flows").json())
        r = _post(client, kind=KIND_DEEP_SKY, options=[], target=NAME,
                  ra=RA, dec=DEC, **answers)
        assert r.status_code == 422, (answers, r.status_code, r.text[:300])
        assert named in _flat(r), (
            f"the 422 does not name {named!r}: {_flat(r)[:300]}")
        assert len(client.get("/api/flows").json()) == before, (
            "a refused answer saved a flow")

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_altitude_is_refused_at_the_door(self, client, raw):
        """JSON has no NaN, but Python's parser reads one: the door must not
        hand a NaN floor to a node (every comparison with it is false, so
        every target would pass or none would)."""
        body = ('{"kind": "Deep-sky target", "options": [], "target": "M31", '
                '"ra": "00h 43m 15.0s", "dec": "+41 30 00", "min_alt": '
                + raw + "}")
        r = client.post("/api/flows/wizard", content=body,
                        headers={"content-type": "application/json"})
        assert r.status_code == 422, (raw, r.status_code, r.text[:200])
        assert "min_alt" in _flat(r)

    def test_a_refusal_through_the_door_is_not_a_500(self, client):
        """A refusal in the generator (a clock with no stop to belong to) is
        the route's own ``invalid_wizard_answer``, the code the sheet reads."""
        r = _post(client, kind=KIND_DEEP_SKY, options=[], target=NAME,
                  ra=RA, dec=DEC, stop="Clock time")
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "invalid_wizard_answer"
        assert "stop_clock" in r.json()["detail"]["detail"]

    @pytest.mark.parametrize("edge", [0, 90, 0.0, 90.0, 0.5, 89.99])
    def test_the_altitude_range_is_closed_at_both_ends(self, edge):
        """0 and 90 are answers (a target may be shot at the horizon, and
        the zenith is the highest there is); the refusals above are the
        first values outside them.

        RED under mutant "the altitude range is open" (``checked_min_alt``
        comparing ``MIN_ALT_MIN_DEG < min_alt < MIN_ALT_MAX_DEG``), 7
        failed (this case for 0, 90, 0.0 and 90.0, and three of the
        written-as-a-number cases), observed verbatim:

            E   ValueError: min_alt is the lowest altitude, in degrees, a
                target is shot at: a number from 0 to 90, not 0
        """
        g = _answer(min_alt=edge).record.graph
        assert _dusk(g).params["minAlt"] == edge


# ============================================================== (d) the doctor

class TestTheDoctorBar:
    @pytest.mark.parametrize("kind", [KIND_DEEP_SKY, KIND_POOL, KIND_EAA,
                                      KIND_MOSAIC])
    def test_a_clock_time_stop_is_a_real_boundary_for_the_accepted_count(
            self, kind):
        """Doctor rule M9: a plan that counts accepted subs with both reject
        guards off is refused at Run unless a stop boundary ends it. A
        "Clock time" stop WITH ITS CLOCK is such a boundary, so the wizard's
        output stays quiet on a rig with the guards off; the same stop with
        no clock (which the wizard refuses to make) would not be.

        RED under mutant "the stop clock is not written" (``stop`` written,
        ``stopClock`` left blank), in all four kinds, observed verbatim:

            E   AssertionError: ['\\u25b8 this plan counts accepted subs with
                no stop time and both reject guards off, so Run will refuse
                it: a sub the grader never accepts would be retried without
                end. Stop the night at Dawn (DUSK WINDOW), or set Settings >
                Standards > 'Give up on a step after'.']
        """
        rig = RigFacts(fov_deg=(0.9, 0.6), has_rotator=True,
                       reject_guards_off=True)
        g = _answer(kind, stop="Clock time", stop_clock="03:30",
                    rig=rig).record.graph
        loud = [i.text for i in check(g, rig=rig)
                if i.level in ("warn", "danger")]
        assert loud == [], loud
