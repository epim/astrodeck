"""The save rules: ruling 2's counts switch and ruling 3's server-owned
anchor (#189 Revision 2, spec 3.1, 3.3, S3 tests).

``save_rules.prepare_save(record, prior)`` is pure, so most of this file
drives it directly with the stored flow handed in as ``prior``; the last
class drives it through ``FlowStore.save``, the one writer, which reads the
prior from the file it is about to replace.

THE GEOMETRY is the spec's own case: a 3x2 of 2.0 x 1.33 deg panels at 25%
overlap, laid out at PA 30 at Dec +41. Its carry threshold is half the
narrower overlap strip, 0.5 x 0.25 x 1.33 deg = 9.975' (A.5), and a pure
north shift moves every corner by the shift, so 6' carries, 9.9' carries,
10.1' does not, and 12' does not (checked with ``framing.reframe_carry``
before these numbers were written down). No site data: RA and Dec here are
M31's catalogue position, and nothing is computed from an observer.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import identity
from astrodeck.flows.models import FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.nodes import COUNT_MODES, NODE_DEFS
from astrodeck.flows.save_rules import (
    ACCEPTED_SUBS, counts_attempts, current_anchor, prepare_save)
from astrodeck.flows.store import FlowStore

RA = "00h 42m 44s"
#: The 3x2 block's params, before a test moves it.
BLOCK = {"name": "M31 3x2", "ra": RA, "rotation": 30, "angle": "Rotate to PA",
         "rows": 2, "cols": 3, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
         "counts": "Accepted subs"}
#: A catalogue for the TARGETs known only by a name: name -> (ra_hours,
#: dec_deg, canonical identity). Two spellings of one object, and another.
CATALOGUE = {"M 31": (0.7123, 41.269, "M31"), "Andromeda": (0.7123, 41.269, "M31"),
             "M33": (1.5641, 30.66, "M33")}


def _target(dec: str = "+41 00 00", *, node_id: str = "t", **over) -> FlowNode:
    params = {**BLOCK, "dec": dec}
    params.update(over)
    return FlowNode(id=node_id, type="target", x=0, y=0, params=params)


def _record(*nodes: FlowNode, fid: str = "f1") -> FlowRecord:
    return FlowRecord(id=fid, name="rules", graph=FlowGraph(nodes=list(nodes)))


def _geometry(dec: str, **over) -> str:
    """The anchor text of the block drawn at ``dec``: what a save writes when
    its counts start there."""
    return current_anchor(_target(dec, **over).with_defaults().params)[0]


def _anchor(record: FlowRecord, node_id: str = "t") -> str:
    return record.graph.node(node_id).params["frameAnchor"]


# ================================================================== counts

class TestTheCountsSwitch:
    def test_accepted_subs_is_what_a_new_block_is_created_with(self):
        """The save writes the value a new TARGET and POOL are CREATED with,
        and it is one of the offered modes. A literal in save_rules, so a
        reword in nodes.py must be caught here, not by a night that counts
        attempts because every save wrote a value the compile does not read.

        RED under mutant "reword the save's value" (``ACCEPTED_SUBS =
        "Accepted frames"``), observed:

            >       assert ACCEPTED_SUBS == NODE_DEFS["target"].created_as["counts"]
            E       AssertionError: assert 'Accepted frames' == 'Accepted subs'
            E         - Accepted subs
            E         + Accepted frames
        """
        assert ACCEPTED_SUBS == NODE_DEFS["target"].created_as["counts"]
        assert ACCEPTED_SUBS == NODE_DEFS["pool"].created_as["counts"]
        assert ACCEPTED_SUBS in COUNT_MODES

    @pytest.mark.parametrize("params, attempts", [
        ({}, True),                                    # the missing key
        ({"counts": "Every sub taken"}, True),
        ({"counts": "every sub"}, True),               # not an offered value
        ({"counts": None}, True),
        ({"counts": "Accepted subs"}, False),
    ])
    def test_what_counts_every_sub_taken(self, params, attempts):
        """Anything but "Accepted subs" counts attempts: the plan counts
        accepted subs only when a block asks for them in those words (spec
        3.3), so the note and the switch must treat an unknown value as the
        old meaning too."""
        assert counts_attempts("target", params) is attempts
        assert counts_attempts("pool", params) is attempts

    def test_only_targets_and_pools_carry_counts(self):
        for node_type in ("capture", "cycle", "dusk", "report"):
            assert counts_attempts(node_type, {}) is False

    def test_every_target_and_pool_switches_and_the_save_says_so(self):
        """A TARGET with no ``counts`` key and a POOL at "Every sub taken"
        both become "Accepted subs", and ``migrated`` says ``["counts"]``.
        A node that carries no counts gains no key.

        RED under mutant "only TARGET switches" (the switch guarded by
        ``node.type == "target"``), observed:

            E       AssertionError: a POOL gets TARGET's treatment (ruling 2)
            E       assert 'Every sub taken' == 'Accepted subs'
            E         - Accepted subs
            E         + Every sub taken
        """
        target = _target()
        target.params.pop("counts")
        pool = FlowNode(id="p", type="pool", params={
            "members": "M16, M17", "counts": "Every sub taken"})
        capture = FlowNode(id="c", type="capture", params={"filter": "L"})
        saved, migrated, _ = prepare_save(_record(target, pool, capture), None)
        assert migrated == ["counts"]
        assert saved.graph.node("t").params["counts"] == ACCEPTED_SUBS
        assert saved.graph.node("p").params["counts"] == ACCEPTED_SUBS, (
            "a POOL gets TARGET's treatment (ruling 2)")
        assert "counts" not in saved.graph.node("c").params
        # A POOL's members are keyed by name, never by a geometry, so it has
        # no anchor to write. RED under mutant "anchor on pools too" (the
        # anchor written for every COUNTED_TYPES node), observed (the
        # ``where`` lines left out):
        #     E       AssertionError: a POOL is keyed by its members' names
        #     E       assert 'frameAnchor' not in {'counts': 'Accepted subs',
        #             'frameAnchor': '', 'members': 'M16, M17'}
        assert "frameAnchor" not in saved.graph.node("p").params, (
            "a POOL is keyed by its members' names")

    def test_silent_when_every_block_already_counts_accepted_subs(self):
        """``migrated`` reports what THIS save changed: nothing, here.

        RED under mutant "migrated always says counts" (``["counts"]``
        returned whether or not anything switched), observed:

            E       AssertionError: nothing switched, so the save must not say so
            E       assert ['counts'] == []
            E         Left contains one more item: 'counts'
        """
        pool = FlowNode(id="p", type="pool", params={
            "members": "M16", "counts": ACCEPTED_SUBS})
        saved, migrated, _ = prepare_save(_record(_target(), pool), None)
        assert migrated == [], "nothing switched, so the save must not say so"
        assert saved.graph.node("p").params["counts"] == ACCEPTED_SUBS

    def test_the_given_record_is_not_changed_and_no_default_is_filled(self):
        """Pure: the record handed in is untouched. Only ``counts`` and
        ``frameAnchor`` are written, so a param the client left out keeps
        meaning its missing-key default rather than today's default frozen
        into the file."""
        target = FlowNode(id="t", type="target", params={
            "name": "NGC 7129", "ra": "21h 42m 30s", "dec": "+66 06 00"})
        record = _record(target)
        before = record.model_dump()
        saved, _, _ = prepare_save(record, None)
        assert record.model_dump() == before
        assert set(saved.graph.node("t").params) == {
            "name", "ra", "dec", "counts", "frameAnchor"}


# ============================================================== the anchor

class TestTheAnchorIsMeasuredFromWhereTheCountsStarted:
    def test_a_first_save_anchors_the_current_geometry(self):
        """No prior flow: the anchor is the block as drawn, and nothing is
        announced, because nothing was keyed before."""
        saved, _, reanchored = prepare_save(_record(_target()), None)
        assert _anchor(saved) == _geometry("+41 00 00")
        assert reanchored == []

    def test_three_saves_of_6_arcmin_the_same_way_reanchor_on_the_second(self):
        """Spec S3 tests (ruling 3): each save is measured against the
        ANCHOR, so the second 6' step is 12' from where the counts started
        and re-anchors, and the third, 6' from the new anchor, carries. The
        ids follow the anchor: kept, restarted, kept.

        RED under mutant "compare with the previous save" (the base is the
        prior node's geometry, never its stored anchor), observed:

            >       assert [len(r) for r in rows] == [0, 1, 0], rows
            E       AssertionError: [[], [], []]
            E       assert [0, 0, 0] == [0, 1, 0]
            E         At index 1 diff: 0 != 1
        """
        saved, _, _ = prepare_save(_record(_target("+41 00 00")), None)
        anchors, rows = [], []
        for dec in ("+41 06 00", "+41 12 00", "+41 18 00"):
            saved, _, reanchored = prepare_save(_record(_target(dec)), saved)
            anchors.append(_anchor(saved))
            rows.append(reanchored)
        assert [len(r) for r in rows] == [0, 1, 0], rows
        g0, g2 = _geometry("+41 00 00"), _geometry("+41 12 00")
        assert anchors == [g0, g2, g2]
        (row,) = rows[1]
        assert set(row) == {"node_id", "max_move_deg", "threshold_deg"}
        assert row["node_id"] == "t"
        assert row["max_move_deg"] * 60 == pytest.approx(12.0, abs=0.01)
        assert row["threshold_deg"] * 60 == pytest.approx(9.975, abs=1e-6)
        keys = [identity.anchor_key(a) for a in anchors]
        assert keys[0] == identity.anchor_key(g0), "a carried nudge keeps the ids"
        assert keys[1] != keys[0] and keys[2] == keys[1]

    @pytest.mark.parametrize("dec, carries", [
        ("+41 09 54", True), ("+41 10 06", False)])
    def test_9_9_arcmin_carries_and_10_1_does_not(self, dec, carries):
        """The spec's pair on the 3x2 (threshold 9.975'). Through the save,
        so the verdict the modal previews is the one the file gets."""
        prior, _, _ = prepare_save(_record(_target()), None)
        saved, _, reanchored = prepare_save(_record(_target(dec)), prior)
        assert (_anchor(saved) == _geometry("+41 00 00")) is carries
        assert (reanchored == []) is carries

    def test_a_grid_change_reanchors_with_no_move_measured(self):
        """Another grid is other panels: re-anchored and listed, with
        ``max_move_deg`` None because no move was measured."""
        prior, _, _ = prepare_save(_record(_target()), None)
        saved, _, reanchored = prepare_save(_record(_target(rows=3)), prior)
        assert _anchor(saved) == _geometry("+41 00 00", rows=3)
        assert reanchored == [{"node_id": "t", "max_move_deg": None,
                               "threshold_deg": pytest.approx(9.975 / 60)}]

    def test_an_angle_that_becomes_any_reanchors(self):
        prior, _, _ = prepare_save(_record(_target()), None)
        saved, _, reanchored = prepare_save(
            _record(_target(angle="Any angle")), prior)
        assert [r["max_move_deg"] for r in reanchored] == [None]
        assert _anchor(saved) == _geometry("+41 00 00", angle="Any angle")


class TestTheClientNeverSetsTheAnchor:
    """``frameAnchor`` is read from the PRIOR stored node with the same id.
    A client that could send it would choose which campaign a moved field's
    frames are credited to.

    RED under mutant "trust the client" (the node the client sent is used as
    its own prior, so its ``frameAnchor`` is the base), observed:

        test_a_sent_anchor_cannot_make_a_move_carry (the sent 6' anchor
        kept; the diff's last lines):

            E         - {"cols":3,"dec_deg":"41.200000","f
            E         + {"cols":3,"dec_deg":"41.100000","f

        test_a_blanked_anchor_cannot_restart_a_carried_nudge (the nudge
        became the anchor):

            E         - {"cols":3,"dec_deg":"41.000000","f
            E         + {"cols":3,"dec_deg":"41.100000","f

        test_a_new_block_gets_its_own_geometry_whatever_it_sends (the forged
        anchor measured as a move of 4 degrees):

            E       AssertionError: assert [{'max_move_d...eg': 0.16625}] == []
            E         Left contains one more item: {'max_move_deg':
                      3.999932634146843, 'node_id': 't', 'threshold_deg':
                      0.16625}
    """

    def test_a_sent_anchor_cannot_make_a_move_carry(self):
        """Stored at G0, moved 12' north, and sent with an anchor 6' from
        where it is now: the save measures from G0 and re-anchors."""
        prior, _, _ = prepare_save(_record(_target("+41 00 00")), None)
        sent = _target("+41 12 00", frameAnchor=_geometry("+41 06 00"))
        saved, _, reanchored = prepare_save(_record(sent), prior)
        assert _anchor(saved) == _geometry("+41 12 00")
        assert [r["node_id"] for r in reanchored] == ["t"]

    def test_a_blanked_anchor_cannot_restart_a_carried_nudge(self):
        """Stored at G0, nudged 6', and sent with the anchor blanked: still
        measured from G0, so it carries and the ids stay."""
        prior, _, _ = prepare_save(_record(_target("+41 00 00")), None)
        saved, _, reanchored = prepare_save(
            _record(_target("+41 06 00", frameAnchor="")), prior)
        assert _anchor(saved) == _geometry("+41 00 00")
        assert reanchored == []

    def test_a_new_block_gets_its_own_geometry_whatever_it_sends(self):
        forged = _geometry("+45 00 00")
        saved, _, reanchored = prepare_save(
            _record(_target("+41 00 00", frameAnchor=forged)), None)
        assert _anchor(saved) == _geometry("+41 00 00")
        assert reanchored == []


class TestAFlowSavedBeforeTheAnchor:
    """A stored node with no ``frameAnchor`` (every flow saved before S3)
    was keyed on the geometry it is stored at: with no anchor
    ``identity.target_key`` keys the current geometry. So that geometry is
    its anchor, implicitly, and the first save measures from it (S3-G's
    finding). Taking the NEW geometry instead would restart the counts of a
    flow nudged in its first save and announce nothing.

    RED under mutant "absent means the new geometry" (no stored anchor makes
    the base empty), observed:

        test_a_nudge_in_the_first_save_keeps_the_stored_geometry (the diff's
        last lines: the 6' nudge became the anchor, and the ids moved):

            E         - {"cols":3,"dec_deg":"41.000000","f
            E         + {"cols":3,"dec_deg":"41.100000","f

        test_a_move_in_the_first_save_is_announced (restarted in silence):

            E       AssertionError: assert [] == ['t']
            E         Right contains one more item: 't'
    """

    def test_a_nudge_in_the_first_save_keeps_the_stored_geometry(self):
        prior = _record(_target("+41 00 00"))          # no frameAnchor key
        saved, _, reanchored = prepare_save(_record(_target("+41 06 00")), prior)
        assert _anchor(saved) == _geometry("+41 00 00")
        assert reanchored == []

    def test_a_move_in_the_first_save_is_announced(self):
        prior = _record(_target("+41 00 00"))
        saved, _, reanchored = prepare_save(_record(_target("+41 12 00")), prior)
        assert _anchor(saved) == _geometry("+41 12 00")
        assert [r["node_id"] for r in reanchored] == ["t"]

    def test_the_first_anchor_of_a_single_target_keys_its_s1_id(self):
        """A single TARGET saved before S3 carries no mosaic keys. Its first
        anchor must key exactly what S1 keyed (``target_key`` with no anchor
        and no grid), or every campaign restarts the day S3 ships.

        RED under mutant "overlap kept as a percent" (``_fraction`` returns
        the percent, clamped to 0.5 rather than divided by 100), observed:

            >       assert identity.anchor_key(_anchor(saved)) == s1
            E       AssertionError: assert '52a7481e44e07aeb' == '8d68acc55be9d618'
            E         - 8d68acc55be9d618
            E         + 52a7481e44e07aeb
        """
        old = FlowNode(id="t", type="target", params={
            "name": "NGC 7129", "ra": "21h 42m 30s", "dec": "+66 06 00",
            "rotation": 12.5})
        saved, _, reanchored = prepare_save(_record(old), _record(old))
        entry = {"name": "NGC 7129", "ra": "21h 42m 30s", "dec": "+66 06 00"}
        s1 = identity.target_key(entry, 21.708333333333332, 66.1, 12.5,
                                 canonical=None)
        assert identity.anchor_key(_anchor(saved)) == s1
        assert reanchored == []

    def test_a_stored_anchor_that_is_not_one_is_treated_as_absent(self):
        """The server alone writes anchors, so a malformed one is a damaged
        file; ``target_key`` refuses it, so it keyed nothing. The save
        repairs it rather than raising."""
        prior = _record(_target("+41 00 00", frameAnchor="{not json"))
        saved, _, reanchored = prepare_save(_record(_target("+41 00 00")), prior)
        assert _anchor(saved) == _geometry("+41 00 00")
        assert reanchored == []

    @pytest.mark.parametrize("over, moves", [
        pytest.param({"rows": 1, "cols": 1, "fovX": None, "fovY": None},
                     False, id="single panel, no field: S1's shape"),
        pytest.param({"rows": 1, "cols": 1}, True,
                     id="single panel with a camera field"),
        pytest.param({"rows": 1, "cols": 1, "fovX": None, "fovY": None,
                      "overlap": 40}, True, id="single panel at 40% overlap"),
        pytest.param({}, False, id="the 3x2, keyed on its grid"),
    ])
    def test_a_first_save_never_moves_the_ids_in_silence(self, over, moves):
        """THROUGH THE COMPILE, because the implicit anchor is a claim about
        what ``to_plan`` keyed. With no anchor it keys a multi-panel block
        on its grid but a single panel on NO grid (``to_plan._block_key``:
        S1's shape, 25% overlap and no camera field), so a single panel's
        ``overlap``, ``fovX`` and ``fovY`` were never in its key. A first
        save that moves the ids must list the block; one that lists nothing
        must keep them. The two single panels with a field or an overlap
        of their own do move (the anchor now carries them, and a threshold
        of half the overlap of no field is 0), so they are listed.

        RED under mutant "a single panel's implicit anchor keeps its field"
        (the implicit anchor read from the node's own params, as first
        built), observed on the two single-panel cases that move:

            E       AssertionError: the ids moved and the save listed nothing
            E       assert [] == ['t']
            E         Right contains one more item: 't'

        RED under mutant "every implicit anchor takes S1's shape" (the
        single-panel shape used for a mosaic too), observed on the 3x2,
        with the first-save nudge and the malformed-anchor cases above red
        beside it:

            E       AssertionError: listed a restart that kept every id
            E       assert ['t'] == []
            E         Left contains one more item: 't'
        """
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.models import FlowEdge
        from astrodeck.flows.to_plan import to_sequence_plan

        params = {**BLOCK, "dec": "+41 00 00", **over}
        params = {k: v for k, v in params.items() if v is not None}
        capture = FlowNode(id="c", type="capture", params={
            "filter": "L", "exposure": 60, "count": 3})
        wire = FlowEdge(**{"from": "t", "fromPort": "target", "to": "c",
                           "toPort": "run"})

        def flow(target: FlowNode) -> FlowRecord:
            return FlowRecord(id="f1", name="rules", graph=FlowGraph(
                nodes=[target, capture], edges=[wire]))

        def ids(record: FlowRecord) -> list[str]:
            plan, _ = to_sequence_plan(compile_plan(record.graph),
                                       flow_id="f1")
            return sorted(t.id for t in plan.targets)

        prior = flow(FlowNode(id="t", type="target", params=params))
        saved, _, reanchored = prepare_save(prior, prior)
        listed = [r["node_id"] for r in reanchored]
        if ids(saved) == ids(prior):
            assert listed == [], "listed a restart that kept every id"
        else:
            assert listed == ["t"], "the ids moved and the save listed nothing"
        assert (ids(saved) != ids(prior)) is moves


class TestABlockKnownByItsName:
    """A TARGET with a name and no typed coordinates is keyed on the
    catalogue's canonical identity (#229), so its anchor names that identity
    and holds no position. The catalogue is injected (``resolve``), so the
    rules stay pure; the store injects ``tonight.resolve_target``."""

    @staticmethod
    def _named(name: str, **over) -> FlowNode:
        params = {"name": name, "ra": "", "dec": "", "rotation": -1,
                  "fovX": 2.0, "fovY": 1.33, "counts": ACCEPTED_SUBS}
        params.update(over)
        return FlowNode(id="t", type="target", params=params)

    def test_the_anchor_is_the_canonical_identity(self):
        saved, _, _ = prepare_save(_record(self._named("M 31")), None,
                                   resolve=CATALOGUE.get)
        assert identity.anchor_key(_anchor(saved)) == identity.name_key(
            "M31", None, fov_x=2.0, fov_y=1.33)

    def test_a_respelling_of_the_same_object_carries(self):
        prior, _, _ = prepare_save(_record(self._named("M 31")), None,
                                   resolve=CATALOGUE.get)
        saved, _, reanchored = prepare_save(
            _record(self._named("Andromeda")), prior, resolve=CATALOGUE.get)
        assert _anchor(saved) == _anchor(prior)
        assert reanchored == []

    def test_another_object_reanchors(self):
        prior, _, _ = prepare_save(_record(self._named("M 31")), None,
                                   resolve=CATALOGUE.get)
        saved, _, reanchored = prepare_save(
            _record(self._named("M33")), prior, resolve=CATALOGUE.get)
        assert identity.anchor_key(_anchor(saved)) == identity.name_key(
            "M33", None, fov_x=2.0, fov_y=1.33)
        assert [r["max_move_deg"] for r in reanchored] == [None]

    def test_a_turn_of_a_named_block_is_measured_at_its_corners(self):
        """Placed where the catalogue puts it, a 90 degree turn of a single
        named panel moves its corners far past the threshold."""
        prior, _, _ = prepare_save(
            _record(self._named("M 31", rotation=0, angle="Rotate to PA")),
            None, resolve=CATALOGUE.get)
        saved, _, reanchored = prepare_save(
            _record(self._named("M 31", rotation=90, angle="Rotate to PA")),
            prior, resolve=CATALOGUE.get)
        assert len(reanchored) == 1 and reanchored[0]["max_move_deg"] > 0.5

    def test_without_a_catalogue_nothing_is_invented(self):
        """No ``resolve``: a name-only block has no layout here. A new one is
        written no anchor (the compile keys its current geometry), and one
        with an anchor keeps it for a save that can place it to measure."""
        saved, _, _ = prepare_save(_record(self._named("M 31")), None)
        assert _anchor(saved) == ""
        prior, _, _ = prepare_save(_record(self._named("M 31")), None,
                                   resolve=CATALOGUE.get)
        saved, _, reanchored = prepare_save(_record(self._named("M 31")), prior)
        assert _anchor(saved) == _anchor(prior) and reanchored == []


class TestABlockWithNoLayout:
    def test_coordinates_that_do_not_parse_keep_the_anchor(self):
        """``to_plan`` drops such a block and keys nothing, so the anchor is
        kept for the save that fixes the coordinates to be measured from."""
        prior, _, _ = prepare_save(_record(_target()), None)
        saved, _, reanchored = prepare_save(
            _record(_target("north of M31")), prior)
        assert _anchor(saved) == _anchor(prior)
        assert reanchored == []
        fixed, _, reanchored = prepare_save(_record(_target("+41 06 00")), saved)
        assert _anchor(fixed) == _anchor(prior) and reanchored == []

    def test_a_new_block_with_no_layout_gets_no_anchor(self):
        saved, _, _ = prepare_save(_record(_target("+95 00 00")), None)
        assert _anchor(saved) == ""


# ============================================================ in the store

class TestTheStoreAppliesTheRules:
    """``FlowStore.save`` is the one writer, so the rules run there, with the
    file it replaces as the prior, and every door converges."""

    @pytest.fixture
    def store(self, tmp_path):
        return FlowStore(tmp_path / "flows", resolve=CATALOGUE.get)

    def test_a_forged_anchor_never_reaches_the_file(self, store):
        """The prior is the FILE, not the record: stored at G0, re-sent at
        G2 with an anchor 6' from G2, and the file ends at G2, announced.

        RED under mutant "the store skips the rules" (``save_and_report``
        writes the record as sent, reporting nothing), observed:

            >       assert _anchor(on_file) == _anchor(record) == _geometry("+41 12 00")
            E       assert '{"cols":3,"d...00","rows":2}' == '{"cols":3,"d...00","rows":2}'
            E         - {"cols":3,"dec_deg":"41.200000","f
            E         + {"cols":3,"dec_deg":"41.100000","f
        """
        store.save(_record(_target("+41 00 00")))
        sent = _record(_target("+41 12 00", frameAnchor=_geometry("+41 06 00")))
        record, migrated, reanchored = store.save_and_report(sent)
        on_file = store.get("f1")
        assert _anchor(on_file) == _anchor(record) == _geometry("+41 12 00")
        assert [r["node_id"] for r in reanchored] == ["t"]
        assert migrated == []

    def test_the_save_switches_counts_and_reports_it(self, store):
        target = _target()
        target.params.pop("counts")
        record, migrated, _ = store.save_and_report(_record(target))
        assert migrated == ["counts"]
        assert store.get("f1").graph.node("t").params["counts"] == ACCEPTED_SUBS
        assert store.get("f1").migrated == []

    def test_save_returns_what_it_stored(self, store):
        saved = store.save(_record(_target("+41 06 00")))
        assert saved.graph == store.get("f1").graph

    def test_the_default_resolver_is_the_catalogue(self, tmp_path, monkeypatch):
        """No ``resolve`` given: the store asks ``tonight.resolve_target``,
        looked up at call time, so a test that replaces it replaces it here."""
        from astrodeck.flows import tonight
        asked: list = []

        def fake(name, when=None):
            asked.append((name, when))
            return tonight.NameResolution(ra_hours=0.7123, dec_deg=41.269,
                                          identity="M31", moves=False)

        monkeypatch.setattr(tonight, "resolve_target", fake)
        store = FlowStore(tmp_path / "flows")
        node = TestABlockKnownByItsName._named("M 31")
        store.save(_record(node))
        assert asked and asked[0] == ("M 31", tonight.IDENTITY_WHEN)
        assert identity.anchor_key(_anchor(store.get("f1"))) == \
            identity.name_key("M31", None, fov_x=2.0, fov_y=1.33)
