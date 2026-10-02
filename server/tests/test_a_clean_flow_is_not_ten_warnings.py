# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

#: The nine of them the quick flow still draws. S3 took SLEW + CENTER out of
#: every wizard lane (#189 U-09, spec 1.7): centring is part of the TARGET
#: block, and the stage's settings never reached the run, which is what its
#: row said. So the row went with the stage rather than being demoted, and
#: the legacy stage's own sentence (a saved flow still draws one) is graded in
#: test_flows_to_plan.py's TestTheLegacySlewNote.
THE_NINE = [k for k in THE_TEN if k != "nodes.slew"]

#: The value the operator typed that each row has to name back at them. A row
#: that says "the settings do not reach the run" is unfalsifiable; a row that
#: says "settle below 1.5 arcsec" can be checked against the card.
OWN_VALUES = {
    "nodes.safety": ["Cloud + rain sensor", "Unsafe (fail closed)"],
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

def test_the_rigs_own_flow_still_reports_the_nine_it_still_draws(compiled):
    """The premise. Demoting a row to ``note`` must not be done by deleting it:
    a setting the compile drops is still a setting the operator has to be told
    about (`test_a_dropped_setting_is_never_silent`). Nine, since S3 took the
    SLEW stage itself out of the lane (`THE_NINE`)."""
    _rec, _plan, rows = compiled
    missing = [k for k in THE_NINE if k not in rows]
    assert not missing, f"rows went silent instead of quiet: {missing}"


def test_the_slew_row_left_with_the_slew_stage(compiled):
    """The tenth row is gone because its stage is (S3, spec 1.7), not because
    it was hushed: the quick flow draws no SLEW + CENTER, and so nothing about
    one. Held here so the nine above are known to be all there is.

    RED under mutant "SLEW left in the lane" (wizard.py's lane appends
    ``"slew"`` before ``"autofocus"`` again), observed verbatim:

        E   AssertionError: assert 'slew' not in ['dusk', 'target', 'slew',
            'autofocus', 'guide', 'cycle', ...]

    The golden and ``test_s3w_switched_the_count_and_moved_nothing_else``
    stay green under it, as they must: the plan does not move.
    """
    rec, _plan, rows = compiled
    assert "slew" not in [n.type for n in rec.graph.nodes]
    assert "nodes.slew" not in rows, rows["nodes.slew"]
    assert sorted(rows) == sorted(THE_NINE), sorted(rows)


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


@pytest.mark.parametrize("key", THE_NINE)
def test_each_of_the_ten_is_a_note(compiled, key):
    _rec, _plan, rows = compiled
    assert rows[key]["level"] == "note", rows[key]


@pytest.mark.parametrize("key", THE_NINE)
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


@pytest.mark.parametrize("key", THE_NINE)
def test_each_of_the_ten_names_the_operators_own_values(compiled, key):
    """From the NODE'S params, not from the shipped defaults. The wizard's
    exposures and sub count differ from every default in `nodes.py`, so a row
    built from the table rather than from the graph would miss these."""
    _rec, _plan, rows = compiled
    row = rows[key]
    text = " ".join([row["detail"], *row["carried"], *row["ignored"]])
    for value in OWN_VALUES[key]:
        assert value in text, f"{key} never names {value!r}: {text}"


@pytest.mark.parametrize("key", THE_NINE)
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
#:
#: MOVED ONCE, ON PURPOSE (#150). The fixture's ``rotation_deg`` went from 23.4
#: to null and this hash was recomputed with it: 23.4 was the TARGET palette
#: default, which the quick wizard never overwrites, so every quick flow was
#: commanding a connected rotator to PA 23.4. The default is now -1 ("any
#: angle"), which ``to_plan`` compiles to None. No other field moved. RED
#: under mutant "restore the 23.4 palette default" (nodes.py):
#:
#:     AssertionError: assert {'apply_filte...s': None, ...} == {'apply_filte...s': None, ...}
#:       Differing items:
#:       {'targets': [{'acquisition': 'cycle', ...}]} != {'targets': [{'acquisition': 'cycle', ...}]}
#:
#: MOVED A SECOND TIME, ON PURPOSE (S1: #170 U-03, #189 U-05). ``Target``
#: gained ``center_tolerance_arcmin``, ``center_attempts`` and
#: ``autofocus_skip_if_fresh``, so the dump grew three keys, and the fixture
#: gained exactly those three on ``targets[0]`` at their defaults (null, null,
#: false). Those are the values that keep today's ``goto_and_center`` call and
#: today's sweep at every target start, so the night itself did not move.
#: ``test_s1_added_three_keys_and_moved_nothing_else`` holds that against the
#: #150 hash. RED against the fixture as #150 left it, before it was edited
#: (the long line wrapped here):
#:
#:     AssertionError: assert {'apply_filte...s': None, ...} == {'apply_filte...s': None, ...}
#:
#:       Omitting 26 identical items, use -vv to show
#:       Differing items:
#:       {'targets': [{'acquisition': 'cycle', 'autofocus_first': True,
#:           'autofocus_skip_if_fresh': False, 'calibration': False, ...}]}
#:           != {'targets': [{'acquisition': 'cycle', 'autofocus_first': True,
#:           'calibration': False, 'center': True, ...}]}
#:       Use -v to get more diff
#:
#: MOVED A THIRD TIME, ON PURPOSE (S2: #189, spec 5.9 and 5.10). The plan
#: gained ``groups`` (the mosaic groups a rotating mosaic is shot by) and
#: ``Target`` gained ``panel_row``, ``panel_col`` and ``after_group``, so the
#: dump grew four keys, and the fixture gained exactly those four at their
#: defaults (``[]`` on the plan; null, null, null on ``targets[0]``). A plan
#: with no groups is the plan the engine ran before S2, so the night did not
#: move. ``test_s2_added_four_keys_and_moved_nothing_else`` holds that against
#: the S1 hash. RED against the S1 hash before it was re-pinned, observed
#: verbatim:
#:
#:     AssertionError: assert '0a20172d4b9e...4de7ec32ff4fc' == 'd9ce9109734a...c1bb76100b0c3'
#:
#: MOVED A FOURTH TIME, ON PURPOSE (S3, the compile task: #170, #189 U-09).
#: Every TARGET's centring now reaches the run (spec 3.3), so
#: ``targets[0]`` carries ``center_tolerance_arcmin`` 1.2 and
#: ``center_attempts`` 3 where S1 left null and null, and the fixture moved
#: by exactly those two values (a diff of the regenerated dump against the S2
#: fixture shows those two lines and nothing else). They are the TARGET's
#: missing-key ``centerTol`` and ``centerTries``, which are the hub's own
#: 0.02 deg and 3 attempts, the numbers every flow already centred to, so the
#: night did not move. ``test_s3_set_the_centring_and_moved_nothing_else``
#: holds that against the S2 hash. RED against the fixture as S2 left it,
#: before it was edited, observed verbatim (the S1 and S2 cases failed with
#: it, on the two values and on the S1 hash):
#:
#:     AssertionError: assert {'apply_filte...s': None, ...} == {'apply_filte...s': None, ...}
#:       Omitting 27 identical items, use -vv to show
#:       Differing items:
#:       {'targets': [{'acquisition': 'cycle', 'after_group': None,
#:           'autofocus_first': True, 'autofocus_skip_if_fresh': False,
#:           ...}]} != {'targets': [{'acquisition': 'cycle', 'after_group':
#:           None, 'autofocus_first': True, 'autofocus_skip_if_fresh': False,
#:           ...}]}
#:
#: MOVED A FIFTH TIME, ON PURPOSE (S3, the wizard task: #189 U-09, Revision 2
#: ruling 2, spec 1.7). The quick wizard now CREATES its nodes
#: (``nodes.create_params``) and draws no SLEW + CENTER. A created TARGET
#: counts accepted subs only, so the plan's ``count_mode`` went from
#: "attempts" to "accepted", and the fixture moved by exactly that one value
#: (a diff of the regenerated dump against the fixture as the compile task
#: left it shows that line and nothing else). Dropping SLEW moved nothing in
#: the plan, because nothing it held ever reached the run (its unmapped row
#: went with it: `test_the_slew_row_left_with_the_slew_stage`). Every sub the
#: grader rejects no longer fills a quota, which is the change ruling 2
#: asked for; every frame and every rule is the same.
#: ``test_s3w_switched_the_count_and_moved_nothing_else`` holds that against
#: the compile task's hash. RED against the fixture as the compile task left
#: it, before it was edited, observed verbatim:
#:
#:     AssertionError: assert {'apply_filte...s': None, ...} == {'apply_filte...s': None, ...}
#:       Omitting 27 identical items, use -vv to show
#:       Differing items:
#:       {'count_mode': 'accepted'} != {'count_mode': 'attempts'}
#: RE-PINNED IN BACKLOG WP-09 (#191, 2026-09-30): the quick flow's DUSK
#: WINDOW compiles its own ``schedule.twilight_deg`` (-18, "Astro dusk"'s
#: own Sun altitude), a field ``Schedule`` did not have when every hash
#: below was last captured, so all five move together - the field is
#: untouched by every ``_undo_*``/key-removal step below, so it rides
#: through the whole chain down to ``GOLDEN_SHA256_BEFORE_S1`` unchanged.
#: Regenerated from the SAME code path, with no other change.
#:
#: RE-PINNED AGAIN FOR BACKLOG WP-34 (#195, 2026-09-30): the quick flow's
#: DUSK WINDOW has no explicit ``repeat`` param, so it compiles to "Single
#: night", and ``SequencePlan`` now carries ``resume_across_nights: false``
#: for that (#195: "Single night" means auto-resume does not arm across
#: nights). The new field is on every ``model_dump`` regardless of the
#: ``_undo_*``/key-removal steps below (they touch none of its keys), so,
#: like ``schedule.twilight_deg`` above, it rides through the whole chain
#: unchanged and all five hashes move together again. Regenerated from the
#: SAME code path, with no other change.
GOLDEN_SHA256 = \
    "1492700944d4dd6f1b76a278913fc3acfebbbffc3f88f39066205dc21c3529c8"

#: The hash as the S3 compile task left it. With the count mode set back to
#: "attempts", the compiled plan must hash to exactly this.
GOLDEN_SHA256_BEFORE_S3W = \
    "b348979b25f3cb76b01837bcb373dfd8f0613d93e9d9c35cb563cf74012d1d21"

#: The one value the S3 wizard task moved on the plan, and what it was.
S3W_COUNT = {"count_mode": "accepted"}
S3W_COUNT_BEFORE = {"count_mode": "attempts"}

#: The hash as S2 left it. With the S3 centring set back to null, the
#: compiled plan must hash to exactly this.
GOLDEN_SHA256_BEFORE_S3 = \
    "dae5d3cb87265c2d378e0940c494b78b5c55e2e1dcdec6c98895421c6d16b69c"

#: The hash as S1 left it. With the four S2 keys taken off, the compiled plan
#: must hash to exactly this.
GOLDEN_SHA256_BEFORE_S2 = \
    "a167e74ca39cf15f18c7930431e6685f9dfc87d0c23771622285e4d3513bd03c"

#: The hash as #150 left it. With the S2 keys and the three S1 keys taken off,
#: the compiled plan must hash to exactly this.
GOLDEN_SHA256_BEFORE_S1 = \
    "4d53fa6e933caf67bc75f74318559f77c1f66f2697b8f5fd54c1f0c05c87f2cc"

#: The S1 keys and the defaults the fixture carries them at.
S1_KEYS = {"center_tolerance_arcmin": None, "center_attempts": None,
           "autofocus_skip_if_fresh": False}

#: The S2 keys and the defaults the fixture carries them at: one on the plan,
#: three on ``targets[0]``.
S2_PLAN_KEYS = {"groups": []}
S2_TARGET_KEYS = {"panel_row": None, "panel_col": None, "after_group": None}


#: The two values S3 sets on ``targets[0]``, and what S1 and S2 left there.
S3_CENTRING = {"center_tolerance_arcmin": 1.2, "center_attempts": 3}
S3_CENTRING_BEFORE = {"center_tolerance_arcmin": None,
                      "center_attempts": None}


def _undo_s3w_count(got: dict) -> dict:
    """The S3 wizard task's count mode, set back in place to what the compile
    task left ("attempts"), with what it held. Every older case undoes it
    first, so each still bounds its own keys and nothing else."""
    taken = {k: got.get(k, "<absent>") for k in S3W_COUNT}
    got.update(S3W_COUNT_BEFORE)
    return taken


def _undo_s3_centring(got: dict) -> dict:
    """The S3 centring on ``targets[0]``, set back in place to what S2 left
    (null, null), with what each held (``"<absent>"`` for a key the dump did
    not carry). The S1 and S2 cases undo it first, so each still bounds its
    own keys and nothing else."""
    target = got["targets"][0]
    taken = {k: target.get(k, "<absent>") for k in S3_CENTRING}
    target.update(S3_CENTRING_BEFORE)
    return taken


def _take_off_s2_keys(got: dict) -> dict:
    """The four S2 keys, popped from the dump in place, with what each held
    (``"<absent>"`` for a key the dump did not carry)."""
    taken = {k: got.pop(k, "<absent>") for k in S2_PLAN_KEYS}
    target = got["targets"][0]
    taken.update({k: target.pop(k, "<absent>") for k in S2_TARGET_KEYS})
    return taken


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


def test_s1_added_three_keys_and_moved_nothing_else(compiled):
    """THE SECOND MOVE, BOUNDED FROM THE OTHER SIDE. Regenerating a golden is
    how a changed night gets waved through: the fixture and its hash move
    together, and the comparison above goes green on whatever the code now
    does. So the S1 move is also pinned against the hash it moved away from:
    the three keys sit at their defaults, and with them taken off the plan is
    the #150 plan exactly, every other field and every other target included.

    UPDATED FOR S2 (#189), which moved the dump on purpose: the S2 keys are
    taken off first (``test_s2_added_four_keys_and_moved_nothing_else`` grades
    them), so this case still bounds S1's own three. Its original mutant,
    "panel_row rides along", is now the real code and no longer a mutant.

    UPDATED FOR S3 (the compile task), which moved the dump on purpose: the
    S3 centring is set back to null first (``_undo_s3_centring``;
    ``test_s3_set_the_centring_and_moved_nothing_else`` grades it). And for
    the S3 wizard task: its count mode is set back first
    (``_undo_s3w_count``; ``test_s3w_switched_the_count_and_moved_nothing_
    else`` grades it).

    RED under mutant "panel_label rides along" (``panel_label: str | None =
    None`` added to ``Target``, a field neither slice added, with the fixture
    and ``GOLDEN_SHA256`` regenerated to match, so the test above stays
    green), observed verbatim:

        E   AssertionError: assert '12a24bc0b154...2e7faade746c9' ==
            '4bb0e667cc78...20103899d5076'

    Mutants "center_attempts default 3" and "autofocus_skip_if_fresh default
    True" turn it red on the first assertion instead:

        E     Differing items:
        E     {'center_attempts': 3} != {'center_attempts': None}
    """
    _rec, plan, _rows = compiled
    got = _blank_ids(plan.model_dump(mode="json"))
    _undo_s3w_count(got)
    _undo_s3_centring(got)
    _take_off_s2_keys(got)
    target = got["targets"][0]
    assert {k: target.pop(k, "<absent>") for k in S1_KEYS} == S1_KEYS
    blob = json.dumps(got, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == \
        GOLDEN_SHA256_BEFORE_S1


def test_s2_added_four_keys_and_moved_nothing_else(compiled):
    """THE THIRD MOVE, BOUNDED THE SAME WAY (S2, #189). The plan's ``groups``
    and the target's ``panel_row``, ``panel_col`` and ``after_group`` sit at
    their defaults, the values that keep a plan with no groups the plan the
    engine ran before S2; with them taken off the plan is the S1 plan exactly.
    A quick flow compiles no group before S3, so any other value here would
    be a changed night. (Nor after it: the quick flow has no grid. S3's
    centring is set back to null first, as in the S1 case.)

    RED under mutant "panel_label rides along" (``panel_label: str | None =
    None`` added to ``Target``, with the fixture and ``GOLDEN_SHA256``
    regenerated to match, so the golden comparison stays green), observed
    verbatim:

        E   AssertionError: assert 'fa7ee6b8e206...cb44bcdc6286d' ==
            'd9ce9109734a...c1bb76100b0c3'

    RED under mutant "a panel row by default" (``panel_row: int | None = 0``
    on ``Target``, the 0-based first row where "not a panel" belongs),
    observed verbatim:

        E   AssertionError: assert {'after_group...panel_row': 0} ==
            {'after_group...el_row': None}
        E     Differing items:
        E     {'panel_row': 0} != {'panel_row': None}
    """
    _rec, plan, _rows = compiled
    got = _blank_ids(plan.model_dump(mode="json"))
    _undo_s3w_count(got)
    _undo_s3_centring(got)
    assert _take_off_s2_keys(got) == {**S2_PLAN_KEYS, **S2_TARGET_KEYS}
    blob = json.dumps(got, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == \
        GOLDEN_SHA256_BEFORE_S2


def test_s3_set_the_centring_and_moved_nothing_else(compiled):
    """THE FOURTH MOVE, BOUNDED THE SAME WAY (S3, the compile task; #170).
    The quick flow's TARGET tells the run its centring, 1.2 arcmin and 3
    tries, and with those two set back to null the plan is the S2 plan
    exactly: every other field, the steps and the rules included. The quick
    flow has no grid, so the mosaic path must add nothing else to it: not a
    group, not a panel field, not a count mode.

    RED under mutant "one more field on every Target" (the plain target
    also gets ``autofocus_skip_if_fresh = True``), observed verbatim (the
    golden and the S1 and S2 cases went red with it):

        E   AssertionError: assert 'ff8fb7f9fbdc...8151b58517525' ==
            '0a20172d4b9e...4de7ec32ff4fc'

    RED under mutant "centring never reaches the Target"
    (``to_plan._centring`` reads no ``centre``), observed on the first
    assertion:

        E   AssertionError: assert {'center_atte...arcmin': None} ==
            {'center_atte..._arcmin': 1.2}
        E     Differing items:
        E     {'center_attempts': None} != {'center_attempts': 3}
        E     {'center_tolerance_arcmin': None} != {'center_tolerance_arcmin': 1.2}

    UPDATED FOR THE S3 WIZARD TASK, which moved the count mode on purpose:
    it is set back first (``_undo_s3w_count``), so this case still bounds
    the centring and nothing else. Its "not a count mode" above still holds
    of the mosaic path; the count mode came from the created TARGET.
    """
    _rec, plan, _rows = compiled
    got = _blank_ids(plan.model_dump(mode="json"))
    _undo_s3w_count(got)
    assert _undo_s3_centring(got) == S3_CENTRING
    blob = json.dumps(got, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == \
        GOLDEN_SHA256_BEFORE_S3


def test_s3w_switched_the_count_and_moved_nothing_else(compiled):
    """THE FIFTH MOVE, BOUNDED THE SAME WAY (S3, the wizard task; #189 U-09,
    Revision 2 ruling 2, spec 1.7). The quick flow's TARGET is created, so it
    counts accepted subs, and its lane has no SLEW + CENTER. With the count
    mode set back to "attempts" the plan is the compile task's plan exactly:
    every frame, every step id's shape, the centring and every rule included.
    Dropping the stage moved nothing, because nothing it held reached the
    run.

    RED under mutant "the quick flow reads the missing-key defaults"
    (wizard.py's ``_Canvas.add`` takes ``default_params`` again), observed
    verbatim on the first assertion (the golden went red with it):

        E   AssertionError: assert {'count_mode': 'attempts'} ==
            {'count_mode': 'accepted'}
        E     Differing items:
        E     {'count_mode': 'attempts'} != {'count_mode': 'accepted'}

    Under mutant "SLEW left in the lane" this case stays GREEN, as it must:
    the plan does not move, and `test_the_slew_row_left_with_the_slew_stage`
    is the case that goes red for it.
    """
    _rec, plan, _rows = compiled
    got = _blank_ids(plan.model_dump(mode="json"))
    assert _undo_s3w_count(got) == S3W_COUNT
    blob = json.dumps(got, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == \
        GOLDEN_SHA256_BEFORE_S3W


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
