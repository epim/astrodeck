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

ONE SWITCH, ``to_plan.DUSK_FLATS_WIRED``. The warning in the unmapped list is
present exactly when it is False, and every sentence Tonight says about the
block reads from the same constant, so the warning and the copy cannot
disagree. The UI's two static strings cannot read a Python constant, so
``test_the_ui_copy_follows_the_switch`` reads them as text.

RE-PINNED FOR BACKLOG WP-134 (#603 job B, wave 17): the switch is now True,
because ``SequenceEngine._dusk_flats`` exists. It runs ONE of the node's three
methods, the flat panel (``to_plan.DUSK_FLATS_RUNS``); the translucent lens
cap and the twilight sky are carried in the plan and still not run, so the
copy is keyed on the COMPILED PLAN's ``dusk_flats`` and its method, not on the
switch alone: a panel block is promised (the brief, the STORY row), a cap or
sky block is "drawn but not run", and a block the plan drops (an unusable
count) is not promised at all. ``TestThePreviewFollowsThePlan`` is the guard.
THE UI's two static strings (``nodeDefs.ts``, ``quickCopy.ts``) were other
files' and opened with the not-run sentence until the wave 17 integration
rewrote them (#744): ``test_the_ui_copy_follows_the_switch`` was a STRICT xfail
until then, and is an ordinary test now.

#707 rides along: the brief's CLOUD WATCH sentence printed the node's threshold
dial as "cloud cover above 40%", and ``to_plan`` reports that the dial "does not
reach the engine: this trigger fires on the detector's own verdict".

#712's server half: ``ui/.../framingApi.ts`` ``campaignLine`` needs the plan's
resume flag and has only the Tonight answer to read it from, so the answer
carries ``resume_across_nights`` (the same flag ``_story``'s budget rows read).

Site: 40 N 105 W, made up and NOT the observatory's, and the hub is pinned to
it so no path can reach the configured one.

NAMED MUTANTS. The first group was run for WP-112 (2026-10-07) and is kept
for the rows that still apply; the second group is WP-134's (2026-10-07), each
run from a byte backup inside the worktree, the bytes restored and their sha256
compared, the mutant text grepped out, the failing assertion verbatim:

WP-112, still current:

* "threshold restored" (the brief's cloud sentence put back to print
  ``cloud cover above {cw.params.get('threshold')}%``):
  ``test_the_cloud_sentence_prints_no_percentage`` RED.
* "queue named for every hold" (the ANY row's ``if automation.get(
  "calibration_queue") else ""`` made ``if True else ""``):
  ``test_the_clouds_in_row_names_darks_only`` RED.
* "resume flag constant" (the answer's ``resume_across_nights`` made ``True``):
  ``TestTheAnswerCarriesTheResumeFlag`` (two cases) RED.
* "m16 tagline restored" (examples.py's tagline back to "Dome opens at dusk,
  lens-cap flats ..."): ``test_the_m16_card_promises_neither_flats_nor_a_dome_
  opening`` RED (and ``test_flows_example_taglines`` 's control).

WP-134 (the flat-panel stage):

* "preview reads the node" (tonight.py ``_dusk_flats_runs``: ``if flats.method
  not in to_plan.DUSK_FLATS_RUNS:`` made ``if False:``, so a cap or sky block
  is promised): ``TestThePreviewFollowsThePlan`` RED, on the cap case:

      AssertionError: cap: the plan runs flats: False, but the brief promises
      them: True: This flow arms at astronomical dusk (-30 min), and shoots 3
      flats per filter with the flat panel before its first light, if a flat
      panel is connected. [...]

* "panel copy dropped (brief)" (``brief()``'s ``if runs:`` made ``if
  False:``): ``test_the_brief_promises_what_the_panel_stage_does`` RED:

      AssertionError: This flow arms at astronomical dusk (-30 min), and has a
      DUSK FLATS block that the engine does not run yet, so no flats are
      taken. [...]

* "panel copy dropped (story)" (``_story``'s ``if runs:`` made ``if False:``):
  ``test_a_panel_block_is_a_dim_row_at_the_start_of_the_run`` RED:

      AssertionError: DUSK FLATS (Sun -2 ... -8) is drawn but not run:

* "copy ignores the switch" (``_dusk_flats_runs``'s ``if not
  to_plan.DUSK_FLATS_WIRED:`` return removed): ``test_the_warning_and_the_copy_
  agree_under_both_settings[unwired]`` RED:

      AssertionError: wired=False: the brief says the block is not run: False,
      but the unmapped warning is present: True
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


def _hand(window="Sun −2° … −8°", *, extra=(), edges=(),
          method="Flat panel", count=3, **dusk) -> FlowGraph:
    """DUSK -> DUSK FLATS -> TARGET -> SLEW -> CAPTURE, hand built (the flow a
    new operator draws), with ``extra`` nodes and ``edges`` added. The DUSK
    FLATS method is the flat panel, the one method the engine runs (WP-134);
    ``method`` names another."""
    nodes = [_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30, **dusk),
             _n("f", "duskflats", x=50, window=window, adu=28500, count=count,
                method=method),
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


def _hand_cap(**kw) -> FlowGraph:
    return _hand(method="Translucent lens cap", **kw)


#: The two flows with a DUSK FLATS node the engine does NOT run: the Example
#: the library ships (translucent lens cap), and a hand-built one (so the test
#: cannot pass by reading only the Example). The flat-panel block, which it
#: does run, is ``_hand()`` and has its own tests below.
GRAPHS = {"example-m16": _m16, "hand-built": _hand_cap}


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

        RED under the named mutant "preview reads the node" (tonight.py
        ``_dusk_flats_runs``: ``if flats.method not in to_plan.DUSK_FLATS_RUNS:``
        made ``if False:``, so a lens-cap block is promised as a panel one),
        observed on the first case:

            AssertionError: example-m16's brief claims flats are shot: This
            flow arms at astronomical dusk (-30 min), and shoots 15 flats per
            filter with the flat panel before its first light, if a flat panel
            is connected. It then arms M16 - Eagle. [...]
        """
        text = brief(GRAPHS[which]())
        for word in FORBIDDEN:
            assert word not in text, (
                f"{which}'s brief claims flats are shot: {text}")
        assert ("and has a DUSK FLATS block that the engine does not run "
                "yet, so no flats are taken") in text, text

    def test_the_brief_promises_what_the_panel_stage_does(self):
        """A flat-panel block IS run (``SequenceEngine._dusk_flats``): the
        clause says what the stage does, with the plan's own count, once,
        before the first light, and only if a panel is connected. It does NOT
        say the flats are shot "in the twilight window": a panel does not wait
        for the sky.

        RED under the named mutant "panel copy dropped" (``brief()``'s
        ``if runs:`` made ``if False:``): the clause is the not-run sentence."""
        text = brief(_hand())
        assert ("and shoots 3 flats per filter with the flat panel before "
                "its first light, if a flat panel is connected") in text, text
        assert "does not run" not in text and "twilight window" not in text, text

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

        RED under "preview reads the node" (the same mutant as the brief's),
        observed:

            AssertionError: example-m16: a STORY row claims flats are shot:
            DUSK FLATS (flat panel): 15 flats per filter, exposure solved to
            28 500 ADU, taken once before the first light - skipped, with a
            line in the log, if no flat panel is connected
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
        assert ("the engine runs the flat-panel method only, and this one is "
                "translucent lens cap") in row["msg"], row["msg"]
        assert row["tone"] == "warn"
        assert row["t_unix"] == out["flats"]["start_unix"] is not None
        timed = [r["t_unix"] for r in out["story"] if r["t_unix"] is not None]
        assert timed == sorted(timed), "the story must still sort by time"

    def test_an_unresolvable_window_keeps_its_warning_row(self):
        """A window that names no two sun altitudes has no clock times, and its
        row is still a warning, at dusk, still saying why, and now also saying
        the block is not run."""
        out = _tonight(_hand_cap(window="After sunset"))
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
        out = _tonight(_hand_cap())
        row = _flats_row(out["story"])
        assert row["msg"] == (
            "DUSK FLATS (Sun −2° … −8°) is drawn but not run: the engine runs "
            "the flat-panel method only, and this one is translucent lens "
            "cap"), row["msg"]
        assert not re.search(r"\bmin\b|\bminutes\b|\d{2,}", row["msg"])
        panel = _flats_row(_tonight(_hand())["story"])["msg"]
        assert not re.search(r"\bmin\b|\bminutes\b", panel), panel
        assert re.findall(r"\d+", panel) == ["3", "28", "500"], (
            f"only the operator's own numbers, count and ADU target: {panel}")

    def test_a_panel_block_is_a_dim_row_at_the_start_of_the_run(self):
        """The panel stage does not wait for the Sun window, so its row is
        timed where the run starts and is not a warning, whether or not the
        window text resolves (it is not read).

        RED under "panel copy dropped" (the story's ``if runs:`` made ``if
        False:``): the row is the warning 'drawn but not run'."""
        for window in ("Sun −2° … −8°", "After sunset", "Now"):
            out = _tonight(_hand(window=window))
            row = _flats_row(out["story"])
            assert row["msg"] == (
                "DUSK FLATS (flat panel): 3 flats per filter, exposure solved "
                "to 28 500 ADU, taken once before the first light - skipped, "
                "with a line in the log, if no flat panel is connected"), (
                row["msg"])
            assert row["tone"] == "dim", row
            assert row["t_unix"] == out["night"]["window_start_unix"], row

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
    def test_the_switch_is_on_because_the_engine_has_the_stage(self):
        """The switch says the engine has a dusk-flats stage. It does:
        ``_dusk_flats`` is called from ``_run`` ahead of the scheduler, and
        the methods it runs are the plan-mapped set. This fails the day either
        is taken out without the other (a switch with no stage is the
        original claim nothing keeps; a stage with the switch off hides it)."""
        import inspect

        from astrodeck.sequence.engine import SequenceEngine
        run = inspect.getsource(SequenceEngine._run)
        assert to_plan.DUSK_FLATS_WIRED is True
        assert run.index("await self._dusk_flats()") < run.index(
            "await self._run_scheduled(plan)"), (
            "the stage must be called before the scheduler's first target")
        assert to_plan.DUSK_FLATS_RUNS == frozenset({"panel"})

    def test_the_unmapped_row_says_what_the_stage_does_by_method(self):
        """The panel is honoured: a ``note`` (no loss, nothing blocks a run).
        The other two methods and an unusable block are a ``warn`` (a loss)
        that says this run will not take flats."""
        def row(g):
            _, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
            rows = [u for u in unmapped if u["key"] == "automation.dusk_flats"]
            assert len(rows) == 1, unmapped
            return rows[0]

        panel = row(_hand())
        assert panel["level"] == "note" and "runs with the flat panel" in \
            panel["detail"], panel
        assert "will not take flats" not in panel["detail"]
        for method in ("Translucent lens cap", "Twilight sky"):
            r = row(_hand(method=method))
            assert r["level"] == "warn" and "will not take flats" in \
                r["detail"], r
            assert "flat-panel method only" in r["detail"], r
        for bad in ({"count": 0}, {"count": 5000}, {"method": "Dome flat"}):
            r = row(_hand(**bad))
            assert r["level"] == "warn", r
            assert "cannot run as set" in r["detail"] and \
                "will not take flats" in r["detail"], r

    @pytest.mark.parametrize("wired", [False, True], ids=["unwired", "wired"])
    def test_the_warning_and_the_copy_agree_under_both_settings(
            self, wired, monkeypatch):
        """The unmapped row, the brief's clause and the STORY row are three
        readings of one fact. With the switch off all three say the block is
        not run; with it on, a flat-panel block is honoured (a note, no loss)
        and Tonight says what the stage does. A copy that ignored the switch
        would leave them disagreeing.

        RED under "copy ignores the switch" (tonight.py ``_dusk_flats_runs``
        not reading ``DUSK_FLATS_WIRED``), on the unwired case: the brief
        promises flats beside the compiler's own 'not wired' warning."""
        monkeypatch.setattr(to_plan, "DUSK_FLATS_WIRED", wired)
        g = _hand()
        _, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
        loss = any(u["key"] == "automation.dusk_flats"
                   and u["level"] != "note" for u in unmapped)
        text = brief(g)
        out = _tonight(g)
        says_not_run = "does not run yet" in text
        assert loss == (not wired), f"wired={wired}: the loss row: {unmapped}"
        assert says_not_run == (not wired), (
            f"wired={wired}: the brief says the block is not run: "
            f"{says_not_run}, but the unmapped warning is present: {loss}")
        row = _flats_row(out["story"])
        assert ("is drawn but not run" in row["msg"]) == (not wired), (
            f"wired={wired}: the STORY row disagrees with the warning: "
            f"{row['msg']}")
        if wired:
            assert ("and shoots 3 flats per filter with the flat panel "
                    "before its first light") in text, text
            assert "exposure solved to 28 500 ADU" in row["msg"]
        else:
            assert "the engine has no dusk-flats stage yet" in row["msg"]

    def test_the_ui_copy_follows_the_switch(self):
        """The node's description and the quick sheet's DUSK FLATS card are
        static strings the switch cannot reach, so they are read as text: each
        opens with the not-run sentence exactly while the switch is off.

        With the switch on (since WP-134) they must NOT open with it, and
        they say what the stage does: the flat-panel method runs once a night
        before the first light and only with a connected panel, the Sun window
        is not waited for, and the lens-cap and twilight-sky methods are not
        run yet (#744, #603 job B; the wave 17 integration rewrote both
        strings). This was a strict xfail while the UI files were another
        package's, with the marker's own instruction to remove it the day they
        were rewritten; it is an ordinary test now.

        RED under "node desc restored" / "quick card restored" (the opening
        not-run sentence put back on a rewritten string), observed, from a
        byte backup (restored, sha256 compared):

            AssertionError: nodeDefs.ts duskflats desc opens with the not-run
            sentence: True, but DUSK_FLATS_WIRED is True

        and the same line for ``quickCopy.ts flats body``; under "switch off,
        copy as it is" (``DUSK_FLATS_WIRED = False``), from the other side:

            AssertionError: nodeDefs.ts duskflats desc opens with the not-run
            sentence: False, but DUSK_FLATS_WIRED is False

        and under "the other two methods dropped" (the lens-cap and
        twilight-sky sentence taken out of the node's description):

            AssertionError: nodeDefs.ts duskflats desc does not say the other
            two methods are not run yet: The Flat panel method runs: ..."""
        for label, text in (("nodeDefs.ts duskflats desc", _node_desc()),
                            ("quickCopy.ts flats body", _quick_flats_body())):
            opens = text.startswith(NOT_RUN)
            assert opens == (not to_plan.DUSK_FLATS_WIRED), (
                f"{label} opens with the not-run sentence: {opens}, but "
                f"DUSK_FLATS_WIRED is {to_plan.DUSK_FLATS_WIRED}")
            if not to_plan.DUSK_FLATS_WIRED:
                assert "When wired it will" in text, (
                    f"{label} no longer says what the stage is for: {text}")
                assert "shoots" not in text.replace("When wired it will", ""), (
                    f"{label} says the block shoots something: {text}")
            else:
                low = text.lower()
                assert "flat panel" in low and "once per night" in low, (
                    f"{label} does not say the flat-panel method runs once a "
                    f"night: {text}")
                assert "before the first light" in low, (
                    f"{label} does not say when in the night: {text}")
                assert "connected flat panel" in low, (
                    f"{label} does not say it needs a connected panel: {text}")
                assert ("lens cap" in low and "twilight sky" in low
                        and "not run yet" in low), (
                    f"{label} does not say the other two methods are not run "
                    f"yet: {text}")
                assert "does not wait for the" in low, (
                    f"{label} does not say the Sun window is not waited for: "
                    f"{text}")


class TestThePreviewFollowsThePlan:
    """THE GUARD (the shape of ``test_the_preview_cannot_promise_what_the_plan_
    drops``): Tonight promises flats exactly when the COMPILED PLAN carries a
    ``dusk_flats`` the engine runs, for every block the node can hold."""

    CASES = {
        "panel": dict(),
        "cap": dict(method="Translucent lens cap"),
        "sky": dict(method="Twilight sky"),
        "zero count": dict(count=0),
        "huge count": dict(count=5000),
        "unknown method": dict(method="Dome flat"),
    }

    @pytest.mark.parametrize("case", list(CASES))
    def test_the_preview_promises_only_what_the_plan_runs(self, case):
        """RED under the named mutant "preview reads the node" (tonight.py
        ``_dusk_flats_runs`` returning ``True`` for every block that compiles
        a method, the plan's method check taken out): the cap and sky flows are
        promised flats."""
        g = _hand(**self.CASES[case])
        plan, unmapped = to_sequence_plan(compile_plan(g, "n"), g)
        runs = plan.dusk_flats is not None and plan.dusk_flats.method == "panel"
        text = brief(g)
        story = " ".join(r["msg"] for r in _tonight(g)["story"])
        promised = "and shoots" in text and "flats per filter" in text
        assert promised == runs, (
            f"{case}: the plan runs flats: {runs}, but the brief promises "
            f"them: {promised}: {text}")
        assert ("exposure solved" in story) == runs, (case, story)
        losses = [u for u in unmapped if u["key"] == "automation.dusk_flats"
                  and u["level"] != "note"]
        assert bool(losses) == (not runs), (case, unmapped)

    def test_a_panel_block_reaches_the_plan_whole(self):
        plan, _ = to_sequence_plan(compile_plan(_hand(), "n"), _hand())
        df = plan.dusk_flats
        assert (df.method, df.adu_target, df.count) == ("panel", 28500, 3)
        assert (df.window_hi_deg, df.window_lo_deg) == (-2.0, -8.0)
        assert df.filters == ["Ha"], "Tonight's plan only: the lights' filters"
        assert df.panel_brightness is None


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

    def test_no_example_tagline_claims_flats_the_stage_does_not_run(self):
        """An Example whose DUSK FLATS block the engine does not run (any
        method but the flat panel) must not claim flats on its card. The
        flat-panel method is run, so a tagline may say what it does."""
        for ex in examples():
            blocks = [n for n in ex.graph.with_defaults().nodes
                      if n.type == "duskflats"]
            runs = bool(blocks) and all(
                str(n.params.get("method")).strip().lower() == "flat panel"
                for n in blocks)
            if not runs:
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
