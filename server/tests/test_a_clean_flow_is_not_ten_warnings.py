"""A flow with nothing wrong with it printed ten amber warnings (2026-09-11).

THE INCIDENT. On the rig, ``POST /api/flows/{id}/compile`` for "NGC 7129 -
LRGB+SHO cycle" answered ``structural: []`` and ``issues: []`` - a clean graph
by both of the checks that exist to find broken ones - and ten ``unmapped``
entries at level ``warn``, each a variation on "the X node's settings do not
reach the run - the compiler does not carry them into the plan". The editor
renders a non-``note`` entry as a loss, so the operator read a working flow as
broken and went looking for the fault.

TWO OF THE TEN SENTENCES WERE ALSO FALSE, which is the part that makes this a
correctness bug and not a tone complaint:

* the CONDITION node's threshold IS carried. The compiled plan for that same
  flow holds ``on_hfr_above`` with the operator's own number on it; only the
  window and the once-per-run setting are dropped.
* the REFOCUS node's PRESENCE is the refocus instruction - a rule wired to it
  becomes the plan's ``refocus`` action. Only its boundary setting is dropped,
  and the engine runs every rule at a frame boundary anyway.

So the class became ``note`` (the level this module has always used for "you
drew this and it happens, just not from here"), and every entry now carries
``carried`` / ``ignored`` / ``source``: which half the plan honours, which half
it does not, and where the number the run actually obeys is set. Both halves
are graded below against the PLAN rather than against the sentence, so a future
edit that rewires one of them has to move the row with it.

WHAT MUST NOT MOVE. ``losses()`` is what ``/api/flows/{id}/run`` refuses on, so
demoting a genuine loss would let a night start quietly doing less than the
canvas shows - the failure this whole seam exists to prevent. A real warn is
held up here and at the route (``test_flows_routes``), and the compiled PLAN is
pinned byte for byte: the notes changed, the night did not.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astrodeck.flows import wizard as flow_wizard
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import default_params
from astrodeck.flows.to_plan import losses, to_sequence_plan

#: The rig's own shape, and every filter it owns: the flow the incident was
#: reported on is a FILTER CYCLE over LRGB+SHO with the watchdog and the guider
#: on, which is exactly what the quick wizard builds.
WHEEL = ["L", "R", "G", "B", "Ha", "OIII", "SII"]
EXPOSURES = {"L": 60, "R": 60, "G": 60, "B": 60,
             "Ha": 180, "OIII": 180, "SII": 180}

#: The ten rows the rig printed, all of them at ``warn``.
THE_TEN = [
    "nodes.safety", "nodes.slew", "nodes.autofocus", "nodes.guide",
    "nodes.report", "nodes.condition", "nodes.refocus", "nodes.abort",
    "nodes.cycle.reject", "instructions[*].message",
]

#: The value the operator typed that each row has to name back at them. A row
#: that says "the settings do not reach the run" is unfalsifiable; a row that
#: says "settle below 1.5 arcsec" can be checked against the card.
OWN_VALUES = {
    "nodes.safety": ["Cloud + rain sensor", "Unsafe (fail closed)"],
    "nodes.slew": ["0.5", "ASTAP"],
    "nodes.autofocus": ["12", "9", "V-curve sweep"],
    "nodes.guide": ["1.5", "3", "PHD2"],
    "nodes.report": ["captures/sessions/", "JSON + FITS index"],
    "nodes.condition": ["1.3", "3 frames"],
    "nodes.refocus": ["Next frame boundary"],
    "nodes.abort": ["Safety monitor unsafe"],
    "nodes.cycle.reject": ["3.5"],
    "instructions[*].message": ["NOTIFY", "ABORT"],
}


def _quick():
    """The flow the fault was reported on, as the wizard builds it."""
    return flow_wizard.quick(
        {"name": "NGC 7331", "ra": "22h 37m 05s", "dec": "+34 24 56"},
        subs_per_filter=15, filters=WHEEL, exposures_s=EXPOSURES, guided=True,
        name="NGC 7331 - LRGB+SHO quick (SN 2026aaiv)", wheel=WHEEL)


def _compile():
    rec = _quick()
    plan, unmapped = to_sequence_plan(compile_plan(rec.graph, rec.name),
                                      rec.graph)
    return rec, plan, {u["key"]: u for u in unmapped}


@pytest.fixture(scope="module")
def compiled():
    return _compile()


# --------------------------------------------------------------- the ten rows

def test_the_rigs_own_flow_still_reports_all_ten(compiled):
    """The premise. Demoting a row to ``note`` must not be done by deleting it:
    a setting the compile drops is still a setting the operator has to be told
    about (`test_a_dropped_setting_is_never_silent`)."""
    _rec, _plan, rows = compiled
    missing = [k for k in THE_TEN if k not in rows]
    assert not missing, f"rows went silent instead of quiet: {missing}"


def test_a_clean_flow_prints_no_losses_at_all(compiled):
    """THE FIX, as one assertion. Structural [], issues [] and now no loss
    either - so the PLAN tab has nothing amber on it and `/run` does not ask
    the operator to accept that parts of this flow do not survive a compile
    that they do survive."""
    _rec, _plan, rows = compiled
    assert losses(list(rows.values())) == [], (
        "a flow with a clean graph still prints losses:\n"
        + "\n".join(f"  {r['level']}: {r['key']}"
                    for r in losses(list(rows.values()))))


@pytest.mark.parametrize("key", THE_TEN)
def test_each_of_the_ten_is_a_note(compiled, key):
    _rec, _plan, rows = compiled
    assert rows[key]["level"] == "note", rows[key]


@pytest.mark.parametrize("key", THE_TEN)
def test_each_of_the_ten_splits_the_card_three_ways(compiled, key):
    """``carried`` / ``ignored`` / ``source``, all three, all non-empty. A row
    with only a sentence is a row the operator cannot act on: it does not say
    which half of the card works, and it does not say where to go and set the
    half that does not."""
    _rec, _plan, rows = compiled
    row = rows[key]
    assert isinstance(row.get("carried"), list) and row["carried"], \
        f"{key} does not say what the plan DOES honour: {row}"
    assert isinstance(row.get("ignored"), list) and row["ignored"], \
        f"{key} does not say what the plan drops: {row}"
    assert isinstance(row.get("source"), str) and row["source"], \
        f"{key} does not say where the real value lives: {row}"


@pytest.mark.parametrize("key", THE_TEN)
def test_each_of_the_ten_names_the_operators_own_values(compiled, key):
    """From the NODE'S params, not from the shipped defaults. The wizard's
    exposures and sub count differ from every default in `nodes.py`, so a row
    built from the table rather than from the graph would miss these."""
    _rec, _plan, rows = compiled
    row = rows[key]
    text = " ".join([row["detail"], *row["carried"], *row["ignored"]])
    for value in OWN_VALUES[key]:
        assert value in text, f"{key} never names {value!r}: {text}"


@pytest.mark.parametrize("key", THE_TEN)
def test_none_of_the_ten_still_carries_the_blanket_sentence(compiled, key):
    """The sentence itself, retired. It was wrong twice and unhelpful eight
    times, and a row that still says it has not been rewritten - it has been
    relabelled."""
    _rec, _plan, rows = compiled
    assert "do not reach the run - the compiler does not carry them" \
        not in rows[key]["detail"], rows[key]


# ------------------------------------------- the two sentences that were false

def test_the_condition_note_is_graded_against_the_plan_not_its_own_words(
        compiled):
    """THE FIRST FALSE SENTENCE. The threshold reaches the run - here is the
    plan holding it - so the row must list it under `carried` and must NOT list
    it under `ignored`. Only the window and the fire-once setting are lost."""
    _rec, plan, rows = compiled
    rule = next(i for i in plan.instructions if i.action == "refocus")
    assert rule.threshold == 1.3, (
        "premise: the CONDITION node's threshold reaches the compiled plan")
    row = rows["nodes.condition"]
    assert any("1.3" in c for c in row["carried"]), (
        f"the plan carries the threshold and the row does not say so: {row}")
    assert not any("1.3" in i for i in row["ignored"]), (
        f"the row calls the carried threshold a loss: {row}")
    assert any("window" in i for i in row["ignored"]), row
    assert any("fire" in i.lower() for i in row["ignored"]), row


def test_the_refocus_note_is_graded_against_the_plan_too(compiled):
    """THE SECOND. The node's PRESENCE is the refocus instruction, so "its
    settings do not reach the run" described a node that does its whole job.
    Only `boundary` is dropped."""
    _rec, plan, rows = compiled
    assert any(i.action == "refocus" for i in plan.instructions), (
        "premise: a rule wired to the REFOCUS node reaches the plan")
    row = rows["nodes.refocus"]
    assert any("refocus action" in c for c in row["carried"]), row
    assert row["ignored"] == ["its 'Next frame boundary' setting"], (
        f"the only thing REFOCUS loses is its boundary: {row['ignored']}")


def test_the_guide_note_still_says_the_presence_decides(compiled):
    """#239 stage C, unchanged by this work: the node's presence is what sets
    `plan.guide`. The row has to keep saying so, because the opposite sentence
    would send someone to add a guider that is already running."""
    _rec, plan, rows = compiled
    assert plan.guide is True, "premise: the GUIDE node turns guiding on"
    row = rows["nodes.guide"]
    assert any("the night guides" in c for c in row["carried"]), row
    assert row["source"] == "Rig > Guider", row


def test_the_abort_note_does_not_claim_a_park_the_operator_turned_off():
    """The trap in saying "park and warm are what the engine does anyway": with
    'Park mount: No' on the card, that IS the operator's setting being
    overridden, and listing it under `carried` would call an override an
    honoured request."""
    params = {**default_params("abort"), "park": "No", "warm": "No"}
    graph = FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={**default_params("target"), "name": "M31",
                                "ra": "00h 42m 44s", "dec": "+41 16 09"}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={**default_params("capture"), "exposure": 60,
                                "count": 5}),
               FlowNode(id="a", type="abort", x=200, y=0, params=params)],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])
    _plan, unmapped = to_sequence_plan(compile_plan(graph, "n"), graph)
    row = next(u for u in unmapped if u["key"] == "nodes.abort")
    assert not any("parks the mount" in c for c in row["carried"]), (
        f"'Park mount: No' is listed as honoured: {row['carried']}")
    assert any("parks anyway" in i for i in row["ignored"]), (
        f"the override is not reported at all: {row['ignored']}")


# ------------------------------------------------- the losses that MUST remain

def _notify_flow() -> FlowGraph:
    """A NOTIFY node: its sink, channel and severity reach NOTHING, so an alert
    the operator routed to their phone does not go there. A real loss."""
    return FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={**default_params("target"), "name": "M31",
                                "ra": "00h 42m 44s", "dec": "+41 16 09"}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={**default_params("capture"), "exposure": 60,
                                "count": 5}),
               FlowNode(id="n", type="notify", x=200, y=0,
                        params=default_params("notify"))],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])


def test_a_real_loss_is_still_a_warn_and_still_holds_the_run():
    """The other direction, and the one that costs frames if it slips. Nothing
    about this change may make a genuinely dropped capability quieter."""
    graph = _notify_flow()
    _plan, unmapped = to_sequence_plan(compile_plan(graph, "n"), graph)
    row = next(u for u in unmapped if u["key"] == "nodes.notify")
    assert row["level"] == "warn", row
    assert row in losses(unmapped), (
        "a NOTIFY node's sink and channel reach nothing, so /run must still "
        "refuse until the operator says they know")


def test_an_out_of_range_hfr_factor_is_still_a_warn():
    """A second real one, on the other table: a relative HFR factor outside
    (1.0, 5.0] is a rule that will not run at all, and `Instruction` would
    reject it at /run with an unhandled ValidationError if this did not."""
    graph = FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={**default_params("target"), "name": "M31",
                                "ra": "00h 42m 44s", "dec": "+41 16 09"}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={**default_params("capture"), "exposure": 60,
                                "count": 5}),
               FlowNode(id="k", type="condition", x=200, y=0,
                        params={**default_params("condition"),
                                "when": "HFR above (x focus)",
                                "threshold": 9.0}),
               FlowNode(id="r", type="refocus", x=300, y=0,
                        params=default_params("refocus"))],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"}),
               FlowEdge(**{"from": "k", "fromPort": "fire",
                           "to": "r", "toPort": "do"})])
    _plan, unmapped = to_sequence_plan(compile_plan(graph, "n"), graph)
    row = next(u for u in unmapped if u["key"].endswith("].threshold"))
    assert row["level"] == "warn", row
    assert row in losses(unmapped)


# -------------------------------------------------------- the plan itself, pinned

#: ``server/tests/fixtures/flow_plan_golden/ngc7331_quick.json``, captured from
#: the code as it stood BEFORE the notes were rewritten.
GOLDEN = Path(__file__).parent / "fixtures" / "flow_plan_golden" / \
    "ngc7331_quick.json"

#: sha256 of ``json.dumps(plan, sort_keys=True)`` with the minted ids blanked.
GOLDEN_SHA256 = \
    "1ec94e5b01d915e2ef510fd58849d890fbed016438672e69d33b519bd055487b"


def _blank_ids(node):
    """Every ``id`` emptied. ``SequencePlan`` mints a fresh uuid4 for each
    target, step and instruction on every ``model_validate``, so they differ
    between two runs of the SAME code - pinning them would pin the random
    number generator and nothing else."""
    if isinstance(node, dict):
        return {k: ("" if k == "id" else _blank_ids(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_blank_ids(v) for v in node]
    return node


def test_the_compiled_plan_did_not_move(compiled):
    """THE NIGHT IS THE SAME NIGHT. This whole change is to the second return
    value of `to_sequence_plan`; the first one - the thing the engine actually
    runs - must be identical field for field, because a rewrite of the
    reporting that quietly changed a frame count or a trigger would be the
    exact defect the reporting exists to catch, committed by the fix.

    The fixture was captured from the pre-change code and is compared as
    OBJECTS so a failure prints the field that moved, then hashed so a failure
    cannot be papered over by regenerating the file.
    """
    _rec, plan, _rows = compiled
    got = _blank_ids(plan.model_dump(mode="json"))
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == want
    blob = json.dumps(got, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == GOLDEN_SHA256


def test_the_golden_is_the_flow_this_file_is_about(compiled):
    """A fixture nobody can read is a fixture nobody can check. These four
    facts are the ones the incident report named, so a golden regenerated
    against the wrong graph fails here rather than passing silently."""
    _rec, plan, _rows = compiled
    assert plan.guide is True
    assert [t.name for t in plan.targets] == ["NGC 7331"]
    steps = plan.targets[0].steps
    assert [s.filter for s in steps] == WHEEL
    assert all(s.count == 15 and s.per_visit == 1 for s in steps)
    assert plan.targets[0].acquisition == "cycle"
