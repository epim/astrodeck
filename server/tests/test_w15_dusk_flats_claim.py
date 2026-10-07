# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No surface claims DUSK FLATS takes flats, and one switch drives the copy
(WP-112, backlog wave 15: #192's copy sweep, #603 job A, #707, #712's
server half).

THE CLASS: a claim nothing keeps. A flow with a DUSK FLATS node compiles, and
``to_plan`` says "the dusk-flats stage is not wired into the engine yet - this
run will not take flats", but Tonight's brief said "shoots 15 flats per filter
(...) in the twilight window", its STORY had a "Flats window (...): ... flats,
exposure solved to 28 500 ADU per filter" row, the node's own description and
the quick sheet's DUSK FLATS card said it "shoots the flat set", and the M16
Example's library card promised "Dome opens at dusk, lens-cap flats in the
twilight window". The engine has no such stage (``_run_calibration`` is reached
only from a cloud hold's darks and the wind-down's day darks).

ONE SWITCH, ``to_plan.DUSK_FLATS_WIRED`` (False today). The warning in the
unmapped list is present exactly when it is False, and every sentence Tonight
says about the block reads from the same constant, so the warning and the copy
cannot disagree and #603 job B flips ONE constant. The UI's two static strings
cannot read a Python constant, so ``test_the_ui_copy_follows_the_switch`` reads
them as text and fails the day the constant moves without them.

#707 rides along: the brief's CLOUD WATCH sentence printed the node's threshold
dial as "cloud cover above 40%", and ``to_plan`` reports that the dial "does not
reach the engine: this trigger fires on the detector's own verdict".

#712's server half: ``ui/.../framingApi.ts`` ``campaignLine`` needs the plan's
resume flag and has only the Tonight answer to read it from, so the answer
carries ``resume_across_nights`` (the same flag ``_story``'s budget rows read).

Site: 40 N 105 W, made up and NOT the observatory's, and the hub is pinned to
it so no path can reach the configured one.

NAMED MUTANTS, each run from a byte backup inside this worktree, the bytes
restored and their sha256 compared, the mutant text counted back to what it was
(2026-10-07). The failing assertion is quoted in the docstring of the test that
catches it, or here:

* "brief clause restored" (tonight.py ``brief()``'s DUSK FLATS branch,
  ``if _dusk_flats_wired():`` made ``if True:``, so ", and shoots N flats per
  filter (...)" is back for every flow): ``test_the_brief_says_the_block_is_not_run``
  (both flows) and ``test_the_warning_and_the_copy_agree_under_both_settings
  [unwired]`` RED.
* "switch ignored" (to_plan.py ``DUSK_FLATS_WIRED = False`` made ``True``,
  nothing wired): the brief and STORY tests, ``test_the_switch_ships_off``
  (``assert True is False``), ``test_the_unmapped_list_still_carries_the_note``
  (``assert (0 == 1)``) and ``test_the_ui_copy_follows_the_switch`` RED.
* "copy ignores the switch" (tonight.py ``_dusk_flats_wired`` made ``return
  False``): ``test_the_warning_and_the_copy_agree_under_both_settings[wired]``
  RED.
* "threshold restored" (the brief's cloud sentence put back to print
  ``cloud cover above {cw.params.get('threshold')}%``):
  ``test_the_cloud_sentence_prints_no_percentage`` RED.
* "story row's old text restored" (``_story``'s ``wired = _dusk_flats_wired()``
  made ``wired = True``): the STORY tests and the unwired agreement case RED.
* "queue named for every hold" (the ANY row's ``if automation.get(
  "calibration_queue") else ""`` made ``if True else ""``):
  ``test_the_clouds_in_row_names_darks_only`` RED.
* "resume flag constant" (the answer's ``resume_across_nights`` made ``True``):
  ``TestTheAnswerCarriesTheResumeFlag`` (two cases) RED.
* "node desc restored", "quick card restored" (the opening sentence of
  nodeDefs.ts's duskflats ``desc`` or of quickCopy.ts's ``flats`` body taken
  off): ``test_the_ui_copy_follows_the_switch`` RED.
* "m16 tagline restored" (examples.py's tagline back to "Dome opens at dusk,
  lens-cap flats ..."): ``test_the_m16_card_promises_neither_flats_nor_a_dome_
  opening`` RED (and ``test_flows_example_taglines`` 's control).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pytest

from astrodeck.flows import to_plan
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import brief, resolve_tonight
from astrodeck.hub import Hub

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
NODE_DEFS_TS = ROOT / "ui" / "src" / "components" / "flows" / "nodeDefs.ts"
QUICK_COPY_TS = (ROOT / "ui" / "src" / "next" / "hubs" / "sky" / "sheets"
                 / "quickCopy.ts")

#: The sentence both UI strings open with while the stage is not wired. It is
#: pinned word for word: it is what an operator reads before the intended
#: behaviour, and the test below compares its presence to the switch.
NOT_RUN = ("Not run yet: the engine has no dusk-flats stage, so this block "
           "takes no flats.")

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

#: What no surface may say about a block the engine does not run.
FORBIDDEN = ("shoots", "flats per filter", "exposure solved", "Flats window",
             "flats-if-panel")


@pytest.fixture(autouse=True)
def synthetic_hub(monkeypatch):
    """The hub on the synthetic site, so nothing the answer is built from can
    come from the configured one."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _hand(window="Sun −2° … −8°", *, extra=(), edges=(), **dusk) -> FlowGraph:
    """DUSK -> DUSK FLATS -> TARGET -> SLEW -> CAPTURE, hand built (the flow a
    new operator draws), with ``extra`` nodes and ``edges`` added."""
    nodes = [_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30, **dusk),
             _n("f", "duskflats", x=50, window=window, adu=28500, count=3,
                method="Flat panel"),
             _n("t", "target", x=100, name="M16 — Eagle",
                ra="18h 18m 48s", dec="−13° 49′ 00″"),
             _n("s", "slew", x=200),
             _n("c", "capture", x=300, filter="Ha", exposure=180, gain=100,
                bin="1", count=20, goal=12),
             *extra]
    return FlowGraph(nodes=nodes,
                     edges=[_e("d", "window", "f", "run"),
                            _e("f", "done", "t", "arm"),
                            _e("t", "target", "s", "run"),
                            _e("s", "centered", "c", "run"), *edges])


def _m16() -> FlowGraph:
    return next(e for e in examples() if e.id == "example-m16").graph


#: The two flows with a DUSK FLATS node: the Example the library ships, and a
#: hand-built one (so the test cannot pass by reading only the Example).
GRAPHS = {"example-m16": _m16, "hand-built": _hand}


def _tonight(graph: FlowGraph) -> dict:
    return resolve_tonight(graph, SITE, now=JUNE, twilight_deg=-12.0)


def _flats_row(story: list[dict]) -> dict:
    """The one STORY row about the DUSK FLATS block, in whichever wording it
    has (so a mutant that brings the old text back fails on what the row SAYS,
    not on a missing row)."""
    rows = [r for r in story
            if re.search(r"DUSK FLATS|Flats window|[Dd]usk flats", r["msg"])]
    assert len(rows) == 1, (
        f"the story carries {len(rows)} DUSK FLATS rows: "
        f"{[r['msg'] for r in story]}")
    return rows[0]


# ---------------------------------------------------------------- the brief

class TestTheBrief:
    @pytest.mark.parametrize("which", list(GRAPHS))
    def test_the_brief_says_the_block_is_not_run(self, which):
        """The brief says the flow HAS a DUSK FLATS block, that the engine does
        not run it yet, and that no flats are taken: none of 'shoots', 'flats
        per filter' or 'exposure solved' survives.

        RED under the named mutant "brief clause restored" (tonight.py
        ``brief()``'s DUSK FLATS branch, ``if _dusk_flats_wired():`` made
        ``if True:``, so the old ``t += f", and shoots {count} flats per
        filter (...) in the twilight window"`` is the clause every flow gets),
        observed on the first case:

            AssertionError: example-m16's brief claims flats are shot: This
            flow arms at astronomical dusk (−30 min), and shoots 15 flats per
            filter (translucent lens cap) in the twilight window. It then arms
            M16 - Eagle. [...]
        """
        text = brief(GRAPHS[which]())
        for word in FORBIDDEN:
            assert word not in text, (
                f"{which}'s brief claims flats are shot: {text}")
        assert ("and has a DUSK FLATS block that the engine does not run "
                "yet, so no flats are taken") in text, text

    def test_the_cloud_sentence_names_darks_only(self):
        """A cloud hold shoots darks, matched to the step it interrupts. The
        sentence said the queue banks "darks -> bias -> flats-if-panel": the
        bias and flats legs are not run (``to_plan``'s calibration-queue
        note)."""
        text = brief(_m16())
        assert "flats-if-panel" not in text and "darks → bias" not in text, text
        assert ("the calibration queue takes darks for whatever the library "
                "lacks (its bias and flat legs are not run yet)") in text, text

    def test_the_cloud_sentence_prints_no_percentage(self):
        """#707: the CLOUD WATCH threshold dial never reaches the engine
        (``to_plan``: "this trigger fires on the detector's own verdict"), so
        the sentence names the verdict, not a number the run never acts on.
        The dial is set to 63 here, a number nothing else in the brief holds.

        RED under the named mutant "threshold restored" (the sentence's
        opening put back to ``f"If cloud cover above
        {cw.params.get('threshold')}% is detected (this trigger fires ...``),
        observed:

            AssertionError: the cloud sentence prints the dial: If cloud
            cover above 63% is detected (this trigger fires on the cloud
            detector's own verdict), imaging pauses at the frame boundary;
            once the sky holds clear for 4 min it re-cools [...]
        """
        g = _hand(extra=[_n("cw", "cloudwatch", x=400, threshold=63,
                            clearFor=4),
                         _n("h", "holdresume", x=500)],
                  edges=[_e("cw", "in", "h", "pause")])
        sentence = next(s for s in re.split(r"(?<=\.) ", brief(g))
                        if "imaging pauses" in s)
        assert "63" not in sentence and "%" not in sentence, (
            f"the cloud sentence prints the dial: {sentence}")
        assert ("this trigger fires on the cloud detector's own verdict"
                in sentence), sentence

    def test_the_dial_it_stopped_printing_is_still_reported_as_unread(self):
        """PREMISE of the sentence above: the compile does report the dial as
        not reaching the engine, so 'the detector's own verdict' is the
        engine's, not a second claim nothing keeps."""
        g = _hand(extra=[_n("cw", "cloudwatch", x=400, threshold=63),
                         _n("h", "holdresume", x=500)],
                  edges=[_e("cw", "in", "h", "pause")])
        _, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
        notes = [u for u in unmapped if u["key"].endswith(".threshold")]
        assert notes and "own verdict" in notes[0]["detail"], unmapped


# ---------------------------------------------------------------- the story

class TestTheStory:
    @pytest.mark.parametrize("which", list(GRAPHS))
    def test_every_story_row_says_the_block_is_not_run(self, which):
        """No STORY row claims flats are shot, one row names the block and
        says it is not run, it is a warning, and it is still timed at the
        window's start (so the story still sorts by it).

        RED under "story row's old text restored" (``_story``'s ``wired =
        _dusk_flats_wired()`` made ``wired = True``, so the row's old f-string,
        ``f"Flats window ({flats['window']}, about {mins} min): ..."``, is the
        row), observed:

            AssertionError: example-m16: a STORY row claims flats are shot:
            Flats window (Sun −2° … −8°, about 39 min): translucent lens cap
            flats, exposure solved to 28 500 ADU per filter
        """
        out = _tonight(GRAPHS[which]())
        assert out["ok"], out["reason"]
        for row in out["story"]:
            for word in FORBIDDEN:
                assert word not in row["msg"], (
                    f"{which}: a STORY row claims flats are shot: "
                    f"{row['msg']}")
        row = _flats_row(out["story"])
        assert "is drawn but not run" in row["msg"], row["msg"]
        assert "the engine has no dusk-flats stage yet" in row["msg"]
        assert row["tone"] == "warn"
        assert row["t_unix"] == out["flats"]["start_unix"] is not None
        timed = [r["t_unix"] for r in out["story"] if r["t_unix"] is not None]
        assert timed == sorted(timed), "the story must still sort by time"

    def test_an_unresolvable_window_keeps_its_warning_row(self):
        """A window that names no two sun altitudes has no clock times, and its
        row is still a warning, at dusk, still saying why, and now also saying
        the block is not run."""
        out = _tonight(_hand(window="After sunset"))
        row = _flats_row(out["story"])
        assert row["tone"] == "warn"
        assert "does not name two sun altitudes" in row["msg"], row["msg"]
        assert "is drawn but not run" in row["msg"], row["msg"]
        assert row["t_unix"] == out["night"]["dusk_unix"]

    def test_the_new_sentences_carry_no_site_derived_value(self):
        """The Tonight answer is built from f(latitude, longitude) and a
        sentence that printed the window's length in minutes would print a
        function of the latitude (#19's class). The row carries the node's own
        window text and no figure the site decides."""
        out = _tonight(_hand())
        row = _flats_row(out["story"])
        assert row["msg"] == (
            "DUSK FLATS (Sun −2° … −8°) is drawn but not run: the engine has "
            "no dusk-flats stage yet"), row["msg"]
        assert not re.search(r"\bmin\b|\bminutes\b|\d{2,}", row["msg"])

    def test_the_clouds_in_row_names_darks_only(self):
        """The ANY row for a cloud hold listed "(black slot -> darks -> bias ->
        flats-if-panel)" for every flow that holds, queue or none. A hold
        shoots darks, and only when a CALIBRATION QUEUE gives it a quota.

        RED under "queue named for every hold" (``_story``'s ``if
        automation.get("calibration_queue") else ""`` made ``if True else
        ""``, so a hold with no queue on the canvas names one), observed on
        the bare case:

            AssertionError: IF clouds in → hold the loop · the calibration
            queue takes darks only (bias and flats are not run yet) · clean
            resume when it clears
        """
        queued = _tonight(_m16())
        (row,) = [r for r in queued["story"] if r["msg"].startswith("IF clouds")]
        assert row["msg"] == (
            "IF clouds in → hold the loop · the calibration queue takes "
            "darks only (bias and flats are not run yet) · clean resume when "
            "it clears"), row["msg"]
        # The same hold with no queue on the canvas has no darks to name.
        g = _hand(extra=[_n("cw", "cloudwatch", x=400),
                         _n("h", "holdresume", x=500)],
                  edges=[_e("cw", "in", "h", "pause")])
        (bare,) = [r for r in _tonight(g)["story"]
                   if r["msg"].startswith("IF clouds")]
        assert bare["msg"] == ("IF clouds in → hold the loop · clean resume "
                               "when it clears"), bare["msg"]


# --------------------------------------------------------------- the switch

class TestOneSwitch:
    def test_the_switch_ships_off(self):
        """Nothing runs a DUSK FLATS block today. Flipping this is #603 job B's
        act, with the stage that justifies it; this fails the day it is flipped
        without one."""
        assert to_plan.DUSK_FLATS_WIRED is False

    def test_the_unmapped_list_still_carries_the_note(self):
        g = _hand()
        _, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
        notes = [u for u in unmapped if u["key"] == "automation.dusk_flats"]
        assert len(notes) == 1 and "will not take flats" in notes[0]["detail"]

    @pytest.mark.parametrize("wired", [False, True], ids=["unwired", "wired"])
    def test_the_warning_and_the_copy_agree_under_both_settings(
            self, wired, monkeypatch):
        """The unmapped note, the brief's clause and the STORY row are three
        readings of one fact. With the switch off all three say the block is
        not run; with it on the note is gone and the old wording (the stage
        shoots flats) is what Tonight says, because that is what the switch
        means. A copy that ignored the switch would leave them disagreeing.

        RED under "copy ignores the switch" (tonight.py ``_dusk_flats_wired``
        made ``return False``), on the wired case, observed:

            AssertionError: wired=True: the brief says the block is not run:
            True, but the unmapped warning is present: False
        """
        monkeypatch.setattr(to_plan, "DUSK_FLATS_WIRED", wired)
        g = _hand()
        _, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
        has_note = any(u["key"] == "automation.dusk_flats" for u in unmapped)
        text = brief(g)
        out = _tonight(g)
        says_not_run = "does not run yet" in text
        assert has_note == (not wired), f"wired={wired}: the note: {unmapped}"
        assert says_not_run == (not wired), (
            f"wired={wired}: the brief says the block is not run: "
            f"{says_not_run}, but the unmapped warning is present: {has_note}")
        row = _flats_row(out["story"])
        assert ("is drawn but not run" in row["msg"]) == (not wired), (
            f"wired={wired}: the STORY row disagrees with the warning: "
            f"{row['msg']}")
        if wired:
            assert ("and shoots 3 flats per filter (flat panel) in the "
                    "twilight window") in text, text
            assert "exposure solved to 28 500 ADU per filter" in row["msg"]

    def test_the_ui_copy_follows_the_switch(self):
        """The node's description and the quick sheet's DUSK FLATS card are
        static strings the switch cannot reach, so they are read as text: each
        opens with the not-run sentence exactly while the switch is off, and
        the day it is flipped this fails until someone has written what the
        stage does. They go on to the intended behaviour in the conditional.

        RED under "switch ignored" (``DUSK_FLATS_WIRED = True``, nothing
        wired), observed:

            AssertionError: nodeDefs.ts duskflats desc opens with the not-run
            sentence: True, but DUSK_FLATS_WIRED is True

        RED under "node desc restored" (nodeDefs.ts's duskflats ``desc``
        with its opening not-run sentence taken off), observed:

            AssertionError: nodeDefs.ts duskflats desc opens with the not-run
            sentence: False, but DUSK_FLATS_WIRED is False

        RED under "quick card restored" (quickCopy.ts's ``flats`` body with
        its opening not-run sentence taken off), observed:

            AssertionError: quickCopy.ts flats body opens with the not-run
            sentence: False, but DUSK_FLATS_WIRED is False
        """
        for label, text in (("nodeDefs.ts duskflats desc", _node_desc()),
                            ("quickCopy.ts flats body", _quick_flats_body())):
            opens = text.startswith(NOT_RUN)
            assert opens == (not to_plan.DUSK_FLATS_WIRED), (
                f"{label} opens with the not-run sentence: {opens}, but "
                f"DUSK_FLATS_WIRED is {to_plan.DUSK_FLATS_WIRED}")
            assert "When wired it will" in text, (
                f"{label} no longer says what the stage is for: {text}")
            assert "shoots" not in text.replace("When wired it will", ""), (
                f"{label} says the block shoots something: {text}")


def _node_desc() -> str:
    src = NODE_DEFS_TS.read_text(encoding="utf-8")
    m = re.search(r'duskflats: \{.*?\n    desc: ((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+),'
                  r'\s*\n', src, re.S)
    assert m, "the duskflats node's desc is not in nodeDefs.ts: it moved"
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))


def _quick_flats_body() -> str:
    src = QUICK_COPY_TS.read_text(encoding="utf-8")
    # Comment lines may sit between the title and the body.
    m = re.search(r'\n  flats: \{\s*t: "DUSK FLATS",\s*(?://[^\n]*\n\s*)*b: '
                  r'((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+),\s*\n  \},', src, re.S)
    assert m, "the quick sheet's flats entry is not in quickCopy.ts: it moved"
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))


# ------------------------------------------------------------ the Examples

class TestTheExamplesTagline:
    def test_the_m16_card_promises_neither_flats_nor_a_dome_opening(self):
        """The library card is where an operator chooses a flow. It said "Dome
        opens at dusk, lens-cap flats in the twilight window, lights until
        clouds - then black slot, darks->bias->flats until quota". Nothing
        opens the dome, nothing shoots dusk flats, and a hold shoots darks
        only.

        RED under "m16 tagline restored" (examples.py's tagline put back to
        "Dome opens at dusk, lens-cap flats in the twilight window, lights
        until clouds - then darks while the hold lasts, ..."), observed:

            AssertionError: the M16 tagline still says 'dome opens'
        """
        tagline =next(e for e in examples() if e.id == "example-m16").tagline
        low = tagline.lower()
        for word in ("dome opens", "flats", "bias", "black slot", "until quota",
                     "lens-cap"):
            assert word not in low, f"the M16 tagline still says {word!r}"
        assert tagline.startswith("Full-service night:"), tagline
        assert "—" not in tagline and "–" not in tagline, \
            "an Example's strings carry no em or en dash (examples.py)"

    def test_no_example_tagline_claims_flats_while_the_stage_is_unwired(self):
        if to_plan.DUSK_FLATS_WIRED:
            pytest.skip("the stage is wired: a tagline may say what it does")
        for ex in examples():
            assert "flats" not in ex.tagline.lower(), (ex.id, ex.tagline)


# ----------------------------------------------------- the resume flag (#712)

class TestTheAnswerCarriesTheResumeFlag:
    def test_it_is_true_for_a_flow_that_says_nothing(self):
        assert _tonight(_hand())["resume_across_nights"] is True

    def test_it_is_false_only_for_an_explicit_off(self):
        """DUSK WINDOW's Automatic resume Off compiles ``resume_across_nights:
        False`` (``compile_plan``) and is the only value that does. The Tonight
        answer repeats it, so the Target modal's campaign line, which reads
        nothing but this answer, can say a later night is CONTINUEd by hand.

        RED under "resume flag constant" (the answer's ``resume_across_nights``
        made ``True``), observed:

            assert True is False
        """
        assert _tonight(_hand(autoResume="Off"))["resume_across_nights"] is False
        assert _tonight(_hand(autoResume="On"))["resume_across_nights"] is True

    def test_it_agrees_with_the_plan_the_run_is_built_from(self):
        """The answer's flag is the compiled plan's, for every setting the
        DUSK node offers (and a blank one).

        RED under the same "resume flag constant" mutant, observed on the Off
        case:

            AssertionError: Off
              assert True == (False is not False)
        """
        for auto in ("On", "Off", ""):
            g = _hand(autoResume=auto)
            plan = compile_plan(g, "n")
            assert (_tonight(g)["resume_across_nights"]
                    == (plan.get("resume_across_nights") is not False)), auto
