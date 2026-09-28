"""A no-field block's anchor completes (#351, S4 orchestrator ruling 4; spec
3.3, 2.5).

THE FAULT. A single-panel TARGET anchored with no camera field (``fov_x =
fov_y = 0``, S1's shape and every block framed before MATCH CAMERA) has a
carry threshold of 0, and a 1x1's corners move away from its centre as soon
as a field is recorded. So the first save after the field was recorded
re-anchored it: a 1x1 at NGC 7331 given ``fovX`` 1.3 and ``fovY`` 0.9 moved
0.79 deg against a threshold of 0.0, and its target and step ids changed,
although the centre and the angle had not moved. Once S4 brings MATCH CAMERA
to single targets, every single-target campaign would restart the first time
its field was recorded, with frames banked.

THE RULING. Recording a field on a block that had none, with the centre, the
angle and the rest of the keyed geometry unchanged, COMPLETES the anchor: its
KEY is kept, so its ids and counts are, and the stored anchor records the
field beside the keyed field of none (``identity.RECORDED_FIELDS``).
``anchor_key`` ignores the recorded field, ``anchor_geometry`` reads it, and
``reframe_carry`` lays the anchor out with it and takes its threshold from
it. Moves, turns and field changes are then judged as usual.

Completion happens only when recording the field is ALL that changed
(``identity.completes``): a move in the same save is judged against the
anchor as it was, threshold 0, because a threshold read from the field the
same edit records would let that edit set its own allowance, which is what
spec 3.3 forbids. And only for a single panel: a grid's panels are tiled
from the field, so recording one moves every panel off the centre it was
shot at (and M1 refuses a grid with no field, so nothing was).

THE GEOMETRY. NGC 7331 at its catalogue position (22h 37m 04s, +34 24 56),
typed, one panel at PA 30, 25% overlap. Recorded at 1.3 x 0.9 deg, its
threshold is 0.5 x 0.25 x 0.9 deg = 0.1125 deg (6.75'), and a pure north
shift moves every corner by the shift. Measured with ``reframe_carry`` before
these numbers were written: 3' north moves 0.0500 deg (carries, under half
the threshold), 8' north 0.1333, a 10 deg turn 0.1378 and a change of field
to 2.0 x 1.33 deg 0.4106 (each re-anchors). No site data: the position is
the catalogue's, and nothing is computed from an observer.

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``s4-save-mut``), from byte copies of the files named,
never in the shared tree (#254); the failure each produced is quoted.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.progress import _single
from astrodeck.flows.save_rules import current_anchor, prepare_save
from astrodeck.flows.to_plan import to_sequence_plan

FID = "f-351"
RA, DEC = "22h 37m 04s", "+34 24 56"
#: The block before any test changes it: one panel, no camera field.
BLOCK = {"name": "NGC 7331", "ra": RA, "dec": DEC, "rotation": 30,
         "angle": "Rotate to PA", "counts": "Accepted subs"}
#: The field MATCH CAMERA records, and the threshold it gives.
FIELD = {"fovX": 1.3, "fovY": 0.9}
THRESHOLD_DEG = 0.5 * 0.25 * 0.9
#: A catalogue for the named case: name -> (ra_hours, dec_deg, identity).
CATALOGUE = {"NGC 7331": (22.617777777777778, 34.41555555555556, "NGC7331"),
             "Caldwell 30": (22.617777777777778, 34.41555555555556,
                             "NGC7331")}


def _flow(**over) -> FlowRecord:
    """The block wired to one CAPTURE, so ``to_plan`` mints a target and a
    step for it."""
    params = {k: v for k, v in {**BLOCK, **over}.items() if v is not None}
    target = FlowNode(id="t", type="target", params=params)
    capture = FlowNode(id="c", type="capture", params={
        "filter": "L", "exposure": 60, "count": 3})
    wire = FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                       "toPort": "run"})
    return FlowRecord(id=FID, name="351", graph=FlowGraph(
        nodes=[target, capture], edges=[wire]))


def _save(prior: FlowRecord | None, **over):
    """``(record, reanchored)`` of saving the block with ``over`` over
    ``prior``, the stored flow."""
    record, _migrated, rows = prepare_save(_flow(**over), prior,
                                           resolve=CATALOGUE.get)
    return record, rows


def _anchor(record: FlowRecord) -> str:
    return record.graph.node("t").params["frameAnchor"]


def _ids(record: FlowRecord) -> list[str]:
    """Every target and step id the run would carry, from ``to_plan``'s
    compile with the flow's id, as ``run_flow`` compiles it."""
    plan, _ = to_sequence_plan(compile_plan(record.graph), record.graph,
                               flow_id=FID)
    return sorted([t.id for t in plan.targets]
                  + [s.id for t in plan.targets for s in t.steps])


def _drawn(**over) -> str:
    """The anchor text the block would have if its counts started now."""
    node = _flow(**over).graph.node("t")
    return current_anchor(node.with_defaults().params,
                          resolve=CATALOGUE.get)[0]


def _completed() -> tuple[FlowRecord, FlowRecord]:
    """``(anchored with no field, the save that recorded the field)``."""
    prior, _ = _save(None)
    completed, _ = _save(prior, **FIELD)
    return prior, completed


# ================================================================ the fault

class TestRecordingAFieldKeepsTheKey:
    def test_the_ids_are_kept_and_the_anchor_records_the_field(self):
        """Anchored with no field, re-saved with 1.3 x 0.9 deg at the same
        centre and angle: nothing restarts, the target and step ids equal
        the pre-save compile's, and the stored anchor records the field
        beside its keyed field of none, whose key it keeps.

        RED under mutant "no completion" (``identity.complete_anchor``
        returns None at once), #351's re-anchor reproduced, max_move 0.79
        against threshold 0.0:

            E   AssertionError: recording a field restarted the counts
            E   assert [{'max_move_d...ld_deg': 0.0}] == []
            E     Left contains one more item: {'max_move_deg': 0.7905192496863903, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.0}

        RED under mutant "complete by rewriting the keyed text"
        (``complete_anchor`` writes the field into ``fov_x`` and ``fov_y``
        instead of beside them), the ids moved:

            E   AssertionError: the ids moved
            E   assert ['335473d0d5c...3628559bbcbd'] == ['0cd34a4e007...4e08bb408152']
            E     At index 0 diff: '335473d0d5cf5bfc8dbdc653b1751d8d' != '0cd34a4e0079577b9bc3e2d7d0b24c88'
        """
        prior, _ = _save(None)
        before = _ids(prior)
        assert len(before) == 2, "premise: one target and one step"
        saved, rows = _save(prior, **FIELD)
        assert rows == [], "recording a field restarted the counts"
        assert _ids(saved) == before, "the ids moved"
        anchor = _anchor(saved)
        held = identity.anchor_geometry(anchor)
        assert (held["fov_x"], held["fov_y"]) == (0.0, 0.0), (
            "the keyed field is still none")
        assert (held["recorded_fov_x"], held["recorded_fov_y"]) == (1.3, 0.9)
        assert identity.anchor_key(anchor) == identity.anchor_key(
            _anchor(prior))

    def test_the_progress_route_finds_the_same_id(self):
        """``progress._single`` recomputes the id through ``target_key`` with
        the stored anchor; after the completing save it finds the target the
        pre-save compile minted.

        RED under mutant "complete by rewriting the keyed text", and under
        "no completion" alike: ``_single`` finds the saved plan's own target,
        whose id is not the one the frames were banked under, so the card
        would read nothing banked on a live campaign (the Target repr is
        shortened here):

            E   AssertionError: assert (Target(id='335473d0d5cf5bfc8dbdc653b1751d8d', name='NGC 7331', ...)) is not None and '335473d0d5cf...dc653b1751d8d' == '2617a6e0ef1a...04e08bb408152'
            E     - 2617a6e0ef1a5a24b7a04e08bb408152
            E     + 335473d0d5cf5bfc8dbdc653b1751d8d)
        """
        prior, saved = _completed()
        plan, _ = to_sequence_plan(compile_plan(prior.graph), prior.graph,
                                   flow_id=FID)
        (before,) = [t.id for t in plan.targets]
        compiled = compile_plan(saved.graph)
        (entry,) = compiled["targets"]
        plan, _ = to_sequence_plan(compiled, saved.graph, flow_id=FID)
        found = _single(plan, entry, flow_id=FID, node_id="t")
        assert found is not None and found.id == before

    def test_a_named_block_completes_too(self):
        """A name-keyed 1x1 (#229) anchored with no field and given one keeps
        its key; a respelling of the same object in the same save is still
        the same key, so it completes as well.

        RED under mutant "no completion":

            E   AssertionError: assert [{'max_move_d...ld_deg': 0.0}] == []
            E     Left contains one more item: {'max_move_deg': 0.7905192496863903, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.0}

        RED under mutant "complete by rewriting the keyed text":

            E   AssertionError: assert 'name:b2e1baef401f1319' == 'name:d1b0847470bee40f'
        """
        named = {"ra": "", "dec": ""}
        prior, _ = _save(None, **named)
        assert identity.anchor_key(_anchor(prior)).startswith(
            identity.NAME_KEY_PREFIX), "premise: keyed on the name"
        saved, rows = _save(prior, name="Caldwell 30", **named, **FIELD)
        assert rows == []
        assert identity.anchor_key(_anchor(saved)) == identity.anchor_key(
            _anchor(prior))
        assert identity.anchor_geometry(_anchor(saved))["recorded_fov_x"] \
            == 1.3


# ======================================================= then as usual

class TestAfterCompletionMovesAreJudgedAsUsual:
    def test_a_later_nudge_under_half_the_new_threshold_carries(self):
        """3' north moves every corner 0.0500 deg, under half of the
        recorded field's 0.1125 deg threshold: kept, ids and all.

        RED under mutant "threshold from the anchor's zero field after
        completion" (``reframe_carry`` reads its threshold from the keyed
        field, 0, where the corners are laid out with the recorded one):

            E   AssertionError: a nudge under the threshold restarted
            E   assert [{'max_move_d...ld_deg': 0.0}] == []
            E     Left contains one more item: {'max_move_deg': 0.049999130569393875, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.0}

        (The three re-anchor cases below go red under it too, each row
        carrying ``threshold_deg`` 0.0 where 0.1125 is asked.)
        """
        _prior, completed = _completed()
        nudged, rows = _save(completed, dec="+34 27 56", **FIELD)
        assert rows == [], "a nudge under the threshold restarted"
        assert _anchor(nudged) == _anchor(completed)
        assert _ids(nudged) == _ids(completed)

    @pytest.mark.parametrize("over, move_deg", [
        pytest.param({"dec": "+34 32 56", **FIELD}, 0.13333, id="8' north"),
        pytest.param({"rotation": 40, **FIELD}, 0.13779, id="a 10 deg turn"),
        pytest.param({"fovX": 2.0, "fovY": 1.33}, 0.41064,
                     id="a field change"),
    ])
    def test_past_the_threshold_it_reanchors_as_a_move(self, over, move_deg):
        """Each moves the corners past 0.1125 deg: re-anchored at the new
        geometry, listed with reason ``move`` and the recorded field's
        threshold, and the ids restart.

        RED under mutant "threshold from the anchor's zero field after
        completion", every row, for example [8' north]:

            E   AssertionError: assert [{'max_move_d...ld_deg': 0.0}] == [{'max_move_d...25 +- 1.1e-07}]
        """
        _prior, completed = _completed()
        saved, rows = _save(completed, **over)
        assert rows == [{"node_id": "t",
                         "max_move_deg": pytest.approx(move_deg, abs=1e-5),
                         "threshold_deg": pytest.approx(THRESHOLD_DEG),
                         "reason": "move"}]
        assert _anchor(saved) == _drawn(**over)
        assert not set(_ids(saved)) & set(_ids(completed))


# ================================================================ controls

class TestControls:
    def test_a_block_anchored_with_a_field_behaves_as_before(self):
        """Anchored WITH a field: no recorded pair is written, a change of
        field is judged by ``reframe_carry`` exactly as it was (the row is
        its verdict), and a nudge still carries. A control: green under
        every completion mutant, red only under "row drops reason"."""
        from astrodeck.catalog.framing import reframe_carry
        big = {"fovX": 2.0, "fovY": 1.33}
        prior, _ = _save(None, **big)
        assert "recorded_fov_x" not in identity.anchor_geometry(
            _anchor(prior))
        saved, rows = _save(prior, **FIELD)
        verdict = reframe_carry(_anchor(prior), _drawn(**FIELD))
        assert rows == [{"node_id": "t", "reason": "move",
                         "max_move_deg": verdict["max_move_deg"],
                         "threshold_deg": verdict["threshold_deg"]}]
        assert _anchor(saved) == _drawn(**FIELD)
        nudged, rows = _save(prior, dec="+34 27 56", **big)
        assert rows == [] and _anchor(nudged) == _anchor(prior)

    def test_a_grid_change_still_reanchors(self):
        """A grid change is other panels, before completion and after it:
        reason ``grid``, no move measured. A control: green on the code and
        under every completion mutant above (another grid is another key,
        so nothing completes), and red only under "row drops reason"."""
        prior, completed = _completed()
        for stored in (prior, completed):
            _saved, rows = _save(stored, rows=2, **FIELD)
            assert [(r["reason"], r["max_move_deg"]) for r in rows] == [
                ("grid", None)]

    def test_a_grid_with_no_field_does_not_complete(self):
        """A 3x2 anchored with no field and given one is laid out from the
        field: every panel leaves the centre it would have been shot at, so
        it re-anchors as before, threshold 0.

        RED under mutant "complete a grid too" (``completes`` without its
        single-panel test), the only test that mutant turned red:

            E   AssertionError: assert [] == [('move', 0.0)]
            E     Right contains one more item: ('move', 0.0)
        """
        grid = {"rows": 2, "cols": 3}
        prior, _ = _save(None, **grid)
        _saved, rows = _save(prior, **grid, **FIELD)
        assert [(r["reason"], r["threshold_deg"]) for r in rows] == [
            ("move", 0.0)]

    def test_a_move_in_the_completing_save_is_judged_against_no_field(self):
        """Recording the field and nudging 1' in the same save does not
        complete: the edit may not set its own allowance (spec 3.3), so the
        nudge is measured against the anchor as it was, threshold 0.

        RED under mutant "complete whatever else changed" (``completes``
        answers True once the fields qualify, its key comparison gone), the
        nudge carried on the allowance the same save recorded:

            E   AssertionError: assert [] == [('move', 0.0)]
            E     Right contains one more item: ('move', 0.0)
        """
        prior, _ = _save(None)
        _saved, rows = _save(prior, dec="+34 25 56", **FIELD)
        assert [(r["reason"], r["threshold_deg"]) for r in rows] == [
            ("move", 0.0)]

    def test_clearing_the_field_again_keeps_the_key_and_says_nothing(self):
        """After completion the keyed field is none, so a save that clears
        the field again (fovX and fovY back to 0) keeps the key: every
        corner moves back to the centre, which ``reframe_carry`` measures
        as a move past the recorded threshold, but the ids do not move and
        nothing restarts, so nothing is listed. The anchor is KEPT, the
        field it records included, as on a carry (see the next case for
        why).

        RED under mutant "a re-anchor that keeps the key is listed" (the
        same-key test in ``save_rules._anchor_on_save`` removed), a restart
        announced that kept every id:

            E   AssertionError: listed a restart that kept every id
            E   assert [{'max_move_d...deg': 0.1125}] == []
            E     Left contains one more item: {'max_move_deg': 0.7905192496863903, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.1125}

        RED under mutant "the cleared save forgets the recorded field" (the
        same-key branch returns the cleared geometry, as first built),
        observed:

            E   AssertionError: the cleared save forgot the field the anchor records
            E   assert '{"cols":1,"d...00","rows":1}' == '{"cols":1,"d...00","rows":1}'
            E     Skipping 102 identical leading characters in diff, use -v to show
            E     - 617778","recorded_fov_x":"1.30000","recorded_fov_y":"0.90000","rotation_deg":"30.000","rows":1}
            E     + 617778","rotation_deg":"30.000","rows":1}
        """
        prior, completed = _completed()
        cleared, rows = _save(completed, fovX=0, fovY=0)
        assert rows == [], "listed a restart that kept every id"
        assert _ids(cleared) == _ids(prior)
        assert _anchor(cleared) == _anchor(completed), (
            "the cleared save forgot the field the anchor records")

    def test_clearing_the_field_does_not_let_another_field_complete(self):
        """#390, found by S4-SAVE's verifier. Recorded at 1.3 x 0.9 deg, a
        change of field to 2.0 x 1.33 deg re-anchors as a move (0.4106 deg
        against 0.1125, above). Made in TWO saves, clearing the field and then
        setting the new one, it must re-anchor the same way: had the
        clearing save put the cleared geometry in the anchor's place, the
        anchor would have forgotten its field, the second save would have
        recorded 2.0 x 1.33 as though it were the block's first (ruling 4
        completes a FIRST field), and every id would have been kept with
        nothing listed. That is the anchor compared with the previous save,
        which spec 3.3 forbids.

        Control: clearing and then recording the SAME field again carries,
        with the ids kept and the anchor where it was.

        RED under mutant "the cleared save forgets the recorded field" (the
        same-key branch returns the cleared geometry, as first built),
        observed (pytest's plus-minus sign written ``+-``), the change of
        field carried in silence:

            E   AssertionError: a change of field made in two saves was not judged
            E   assert [] == [{'max_move_d...25 +- 1.1e-07}]
            E     Right contains one more item: {'max_move_deg': 0.41064 +- 1.0e-05, 'node_id': 't', 'reason': 'move', 'threshold_deg': 0.1125 +- 1.1e-07}
        """
        prior, completed = _completed()
        cleared, _rows = _save(completed, fovX=0, fovY=0)
        big = {"fovX": 2.0, "fovY": 1.33}
        changed, rows = _save(cleared, **big)
        assert rows == [{"node_id": "t",
                         "max_move_deg": pytest.approx(0.41064, abs=1e-5),
                         "threshold_deg": pytest.approx(THRESHOLD_DEG),
                         "reason": "move"}], (
            "a change of field made in two saves was not judged")
        assert _anchor(changed) == _drawn(**big)
        assert not set(_ids(changed)) & set(_ids(prior))
        again, rows = _save(cleared, **FIELD)
        assert rows == [] and _anchor(again) == _anchor(completed)
        assert _ids(again) == _ids(prior)
