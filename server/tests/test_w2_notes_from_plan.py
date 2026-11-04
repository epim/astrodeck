# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight's notes are a function of the compiled plan (#1112, WP 2.5).

The CAMPAIGN note, the brief's night-end sentence, the STORY tab's dawn row
and the run button's `owes_more` each used to re-read the flow graph with a
rule of their own, and they disagreed with each other and with the plan: day
darks the plan funds and the note denied (#819), a DUSK WINDOW the flow does
not have (#775), a dome the brief said closes and the plan does not promise
(#819), a retired `repeat` override on the campaign Example (#773).

THE TABLE IS OVER EVERY EXAMPLE in `flows/examples.py`. Each is compiled, the
real `SequencePlan` is built from the compile, and every note is read against
that plan: no expectation is written per example. `_agree` is the one
comparison, and the three variant tests below run it on graphs the Examples do
not cover (an Off flow, a pool with no DUSK WINDOW, a calibration queue that is
not wired to the shutdown), so the table's own reach is not the only thing
holding it.

NAMED MUTANT. In `tonight._story`, the dawn row's day-dark count read off the
CALIBRATION QUEUE nodes' `quota` (a graph read, whether or not the queue is
wired to SHUTDOWN COMPLETE) instead of `facts.day_darks`. The cycle and M16
Examples draw a queue that only feeds the cloud hold, so the row then says
"20 day darks are taken" where the plan funds none, and
`test_every_note_agrees_with_the_compiled_plan[example-cycle]` and
`[example-m16]` go red.
"""
from __future__ import annotations

import re

import pytest

from astrodeck.flows import examples as ex
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.tonight import _campaign, brief, resolve_tonight
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import SessionFrame
from test_flows_continue import _pure_session, _step

#: A made-up site: the real one never appears in a test.
SITE = {"latitude": 34.0, "longitude": -116.0, "elevation_m": 100.0}
WHEN = 1755000000.0

_TAKEN = re.compile(r"(\d+) day darks? (?:is|are) taken")
_NONE = "no day darks are queued"
_SAFETY = "Settings > Safety"


def _claimed_day_darks(text: str) -> int | None:
    """What a sentence says about day darks: their count, 0 when it says none
    are queued, None when it says nothing."""
    m = _TAKEN.search(text)
    if m:
        return int(m.group(1))
    return 0 if _NONE in text else None


def _night_end(brief_text: str) -> str | None:
    i = brief_text.find("When astronomical night ends")
    return None if i < 0 else brief_text[i:].split(". ")[0]


def _dawn_row(out: dict) -> str | None:
    rows = [r["msg"] for r in out["story"] if r["msg"].startswith("Dawn:")]
    return rows[-1] if rows else None


def _surfaces(graph, name: str) -> dict[str, str]:
    """Every note that speaks of how the night ends, by surface."""
    got: dict[str, str] = {}
    camp = _campaign(graph, None)["note"]
    if "Dawn " in camp:
        got["campaign note"] = camp
    sentence = _night_end(brief(graph))
    if sentence is not None:
        got["brief night-end sentence"] = sentence
    out = resolve_tonight(graph, SITE, name=name, now=WHEN)
    if out.get("ok") and _dawn_row(out) is not None:
        got["story dawn row"] = _dawn_row(out)
    return got


def _agree(graph, name: str) -> tuple[SequencePlan, dict[str, str]]:
    """Assert every note about ``graph`` agrees with its compiled plan, and
    return the plan and the notes that spoke."""
    compiled = compile_plan(graph, name)
    plan, _unmapped = to_sequence_plan(compiled, graph, flow_id="w2-5")
    window = (compiled.get("schedule") or {}).get("start_mode") != "now"
    spoke = _surfaces(graph, name)

    for surface, text in spoke.items():
        claimed = _claimed_day_darks(text)
        if claimed is None:
            assert plan.day_darks == 0, (
                f"the {surface} says nothing of the {plan.day_darks} day "
                f"darks the plan funds: {text}")
        else:
            assert claimed == plan.day_darks, (
                f"the {surface} says {claimed} day darks and the plan funds "
                f"{plan.day_darks}: {text}")
        for sentence in re.split(r"(?<=[.;]) ", text):
            if "dome" in sentence:
                assert _SAFETY in sentence, (
                    f"the {surface} says what the dome does without the "
                    f"Settings condition the plan leaves it to: {sentence}")

    note = spoke.get("campaign note", "")
    if note:
        assert ("Each dusk resumes" in note) == plan.resume_across_nights, (
            f"the campaign note and the plan disagree on resuming: {note}")
    sentence = spoke.get("brief night-end sentence", "")
    if sentence:
        assert ("re-arms at the next dusk" in sentence) == (
            "campaign" in compiled), (
            f"the brief and the compile disagree on a campaign: {sentence}")

    campaign = _campaign(graph, None)
    assert campaign["is_campaign"] == ("campaign" in compiled), campaign
    if not window:
        assert "(DUSK WINDOW)" not in campaign["note"], (
            f"the note names a DUSK WINDOW the compile has none of: "
            f"{campaign['note']}")
    return plan, spoke


@pytest.mark.parametrize("rec", ex.examples(), ids=lambda r: r.id)
def test_every_note_agrees_with_the_compiled_plan(rec):
    """RED under the named mutant (module docstring): the story's dawn row
    for the cycle and M16 Examples says 20 day darks are taken, and the plan
    funds none."""
    _agree(rec.graph, rec.name)


def test_the_table_is_not_vacuous():
    """The comparison above would pass on a build that said nothing at all.
    The campaign Example is the one flow that funds day darks, and all three
    surfaces must say so, in the plan's own count; the others fund none."""
    funded = {}
    for rec in ex.examples():
        plan, spoke = _agree(rec.graph, rec.name)
        funded[rec.id] = (plan.day_darks, sorted(spoke))
    darks = {k: v[0] for k, v in funded.items()}
    assert darks["example-campaign"] == 20, darks
    assert [k for k, v in darks.items() if v] == ["example-campaign"], darks
    assert funded["example-campaign"][1] == [
        "brief night-end sentence", "campaign note", "story dawn row"], funded
    assert sum(1 for v in funded.values() if v[1]) >= 4, funded


def test_no_example_stores_the_retired_repeat_override():
    """#773: `repeat` is read by nothing, and the campaign Example still
    stored it, so a reader looking for what makes it a campaign found a dead
    param."""
    for rec in ex.examples():
        for node in rec.graph.nodes:
            assert "repeat" not in node.params, (rec.id, node.id, node.params)


def test_the_campaign_example_is_a_campaign_without_it():
    rec = next(r for r in ex.examples() if r.id == "example-campaign")
    assert "campaign" in compile_plan(rec.graph, rec.name)


def test_an_off_flow_promises_no_later_night():
    rec = next(r for r in ex.examples() if r.id == "example-campaign")
    dusk = next(n for n in rec.graph.nodes if n.type == "dusk")
    dusk.params["autoResume"] = "Off"
    plan, spoke = _agree(rec.graph, rec.name)
    assert plan.resume_across_nights is False
    note = _campaign(rec.graph, None)["note"]
    assert "does not resume by itself" in note, note
    assert "Each dusk resumes" not in " ".join(spoke.values())
    assert "re-arms" not in " ".join(spoke.values())


def test_a_pool_with_no_dusk_window_says_it_has_none():
    """#775: the note said 'Automatic resume is on (DUSK WINDOW)' of a flow
    that has no DUSK WINDOW, and `engine.start` arms such a flow with no
    schedule to resume on."""
    rec = next(r for r in ex.examples() if r.id == "example-pool")
    dusk_ids = {n.id for n in rec.graph.nodes if n.type == "dusk"}
    rec.graph.nodes = [n for n in rec.graph.nodes if n.id not in dusk_ids]
    rec.graph.edges = [e for e in rec.graph.edges
                       if e.from_ not in dusk_ids and e.to not in dusk_ids]
    _agree(rec.graph, rec.name)
    note = _campaign(rec.graph, None)["note"]
    assert "has no DUSK WINDOW" in note, note


def test_a_queue_not_wired_to_the_shutdown_funds_no_day_darks():
    rec = next(r for r in ex.examples() if r.id == "example-campaign")
    rec.graph.edges = [e for e in rec.graph.edges
                       if not (e.from_ == "n21" and e.fromPort == "closed")]
    plan, spoke = _agree(rec.graph, rec.name)
    assert plan.day_darks == 0
    assert _claimed_day_darks(spoke["campaign note"]) == 0, spoke


def test_no_campaign_note_names_a_nights_bound():
    """#772: `campaign_block` writes one stop condition, `pool_complete`, so a
    note for a bound on NIGHTS ("Nothing counts NIGHTS", `nights_30`) is a claim
    kept for a state the compile can no longer reach. Every Example's campaign
    note, and the note for a hand-built compile still carrying the retired
    `until`, say the one thing a campaign does."""
    compiled = []
    for rec in ex.examples():
        c = compile_plan(rec.graph, rec.name)
        if "campaign" in c:
            compiled.append(c)
    assert compiled, "premise: an Example is a campaign"
    retired = {**compiled[0], "campaign": {**compiled[0]["campaign"],
                                           "until": "nights_30"}}
    for c in (*compiled, retired):
        _plan, unmapped = to_sequence_plan(c, None)
        notes = [u["detail"] for u in unmapped if u["key"] == "campaign"]
        assert len(notes) == 1, unmapped
        assert "NIGHTS" not in notes[0] and "nights_30" not in notes[0], notes
        assert "requested frame count" in notes[0], notes


def _floor_sentence(min_alt, on_floor):
    rec = next(r for r in ex.examples() if r.id == "example-pool")
    pool = next(n for n in rec.graph.nodes if n.type == "pool")
    pool.params["minAlt"] = min_alt
    pool.params["onFloor"] = on_floor
    sentences = re.split(r"(?<=[.]) ", brief(rec.graph))
    return [s for s in sentences if "sinks to the" in s]


def test_the_floor_sentence_quotes_the_plans_floor_as_g():
    """The brief's floor sentence reads the plan's `min_altitude_deg` and the
    plan's `on_floor`, so an odd stored value reads as the compile reads it and
    prints with `:g`: 27.5 as "27.5" and a whole number with no ".0", and a
    stored "ADVANCE" in any case or padding still advances."""
    got = _floor_sentence(27.5, " ADVANCE now ")
    assert len(got) == 1 and "27.5\u00b0 floor" in got[0], got
    got = _floor_sentence(30, "Advance now; retry it next night")
    assert len(got) == 1 and "30\u00b0 floor" in got[0], got
    assert "30.0" not in got[0], got
    got = _floor_sentence("27.25", "advance")
    assert len(got) == 1 and "27.25\u00b0 floor" in got[0], got
    for keeps in ("Keep imaging (not recommended)", "", "sometimes"):
        assert _floor_sentence(27.5, keeps) == [], keeps


class TestOwesMore:
    """`Session.owes_more(plan)`: counted by `plan`, does the session still
    owe frames. The run button reads it (runCopy, WP 2.5) so a finished
    session whose flow was edited to owe more says CONTINUE before the press.
    It is `Session.owed()` on the plan swapped in, the question
    `continuation.reopen_report` already asks, said once."""

    def _finished(self):
        steps = [_step("L"), _step("R")]
        s = _pure_session(steps, {0: 5, 1: 5})
        s.status = "complete"
        return s, steps

    def _plan(self, s, *steps):
        return s.plan.model_copy(update={
            "targets": [s.plan.targets[0].model_copy(
                update={"steps": list(steps)})]})

    def test_a_raised_count_owes_more(self):
        s, (l, r) = self._finished()
        assert s.owed() == 0, "premise: the session is finished"
        raised = self._plan(s, l.model_copy(update={"count": 8}), r)
        assert s.owes_more(raised) is True

    def test_an_unchanged_or_lowered_flow_owes_nothing(self):
        s, (l, r) = self._finished()
        assert s.owes_more(self._plan(s, l, r)) is False
        lowered = self._plan(s, l.model_copy(update={"count": 2}), r)
        assert s.owes_more(lowered) is False

    def test_it_counts_by_the_plans_mode_not_the_sessions(self):
        steps = [_step("L")]
        s = _pure_session(steps, {0: 6}, count_mode="accepted", rejected=1)
        s.status = "complete"
        assert s.owed() == 0, "premise: five accepted fill the count of five"
        assert isinstance(s.frames[0], SessionFrame)
        again = self._plan(s, steps[0].model_copy(update={"count": 6}))
        assert s.owes_more(again) is True, "one of six was rejected"
        attempts = again.model_copy(update={"count_mode": "attempts"})
        assert s.owes_more(attempts) is False, "six were taken"

    def test_it_does_not_touch_the_session(self):
        s, (l, r) = self._finished()
        before = s.model_dump()
        s.owes_more(self._plan(s, l.model_copy(update={"count": 8}), r))
        assert s.model_dump() == before
