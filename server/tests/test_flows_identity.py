"""Deterministic ids for a compiled flow (#189 S1 item 7, spec 3.3, D4, D5).

THE FAILURE THESE TESTS EXIST FOR. The session ledger counts frames by step id
alone, and ``SequencePlan`` minted a fresh uuid4 for every target and step on
every compile. So a flow compiled again on night two named steps the ledger
had never seen, and nothing banked on night one counted: a campaign could only
start over, never continue. ``to_sequence_plan(flow_id=)`` now derives each id
from what the operator drew, and these tests pin which edits keep an id and
which edits change it, because that split IS the design:

* keep: a second compile, a count or cycles change, reordering a pool;
* change: a different flow, a moved target, a different exposure recipe.

Every test names the mutation of the code it guards and quotes the failure
that mutation produced, run from a byte backup of the file. A test that cannot
fail is a defect, and several of these (the controls) guard against the fix
spreading further than it should.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.models import plan_identity_errors


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph(*, ra="00h 42m 44s", dec="+41 16 09", rotation=-1, count=10,
           exposure=120, cycles=3, per_cycle=1, plan="L 60, R 60, G 60",
           cap_gain=100, cap_bin="1"):
    """dusk -> TARGET -> CAPTURE -> FILTER CYCLE: one target, four steps.

    Both stage kinds on one target, so every id rule is exercised on one plan:
    the capture's single step and the cycle's expanded slot steps."""
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name="M31", ra=ra, dec=dec,
                  rotation=rotation),
               _n("c", "capture", x=200, filter="Ha", exposure=exposure,
                  gain=cap_gain, bin=cap_bin, count=count, goal=0),
               _n("y", "cycle", x=300, plan=plan, cycles=cycles,
                  perCycle=per_cycle, gain=100, bin="1")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
               _e("c", "complete", "y", "run")])


def _plan(graph, flow_id=""):
    plan, _unmapped = to_sequence_plan(compile_plan(graph, "n"), graph,
                                       flow_id=flow_id)
    return plan


def _ids(plan):
    """``[(target id, [step ids])]`` in plan order."""
    return [(t.id, [s.id for s in t.steps]) for t in plan.targets]


def _all_ids(plan):
    return {i for tid, sids in _ids(plan) for i in (tid, *sids)}


def _version(hex_id):
    return uuid.UUID(hex=hex_id).version


class TestGoldenVectors:
    """The ids are a stored contract: a banked frame is filed under one, so a
    silent change to any part of the recipe below orphans every campaign ever
    saved. S3 builds mosaic ids from the same functions, and these vectors are
    what stops it drifting.

    Each expected value was computed from a HAND-WRITTEN canonical string with
    plain ``hashlib`` / ``uuid`` (not with ``identity``), and each test also
    recomputes it from that string here, so the vectors check the recipe the
    spec states rather than snapshotting whatever the code happened to do.

    Mutant "uuid4" (``identity._id`` returns ``uuid.uuid4().hex``) turned
    every id test here red (the three geometry tests use no uuid and passed),
    for example test_group_and_target_ids:
        AssertionError: assert '368169dfb87f...f388f3e3bc819' ==
        '1f2f390ffcce...8aa42fe06103e'
    """

    SINGLE = ('{"cols":1,"dec_deg":"41.269167","fov_x":"0.00000",'
              '"fov_y":"0.00000","overlap":"0.2500","ra_hours":"0.712222",'
              '"rotation_deg":null,"rows":1}')
    GRID = ('{"cols":2,"dec_deg":"-12.500000","fov_x":"2.00000",'
            '"fov_y":"1.33000","overlap":"0.1500","ra_hours":"20.750000",'
            '"rotation_deg":"23.400","rows":3}')

    def test_the_namespace_is_pinned(self):
        """Changing NS_FLOWS re-keys every flow at once."""
        assert str(identity.NS_FLOWS) == "d68016b1-09c2-455d-8573-3286b066e565"

    def test_the_canonical_geometry_is_spelled_out(self):
        """The exact bytes the key hashes: sorted keys, no spaces, every float
        a fixed-precision string, "any angle" as null.

        Mutant "separators dropped" (``json.dumps`` with its default ", " and
        ": ") failed:
            assert '{"cols": 1, ...l, "rows": 1}' == '{"cols":1,"d...ull,"rows":1}'
        """
        assert identity.canonical_geometry(
            0.7122222222, 41.2691666667, None) == self.SINGLE
        assert identity.canonical_geometry(
            20.75, -12.5, 23.4, rows=3, cols=2, overlap=0.15,
            fov_x=2.0, fov_y=1.33) == self.GRID

    def test_the_geometry_key_is_the_first_16_hex_of_sha256(self):
        single = hashlib.sha256(self.SINGLE.encode()).hexdigest()[:16]
        grid = hashlib.sha256(self.GRID.encode()).hexdigest()[:16]
        assert single == "7aa8577027b1741c" and grid == "1857d50ead89e1b1"
        assert identity.geometry_key(0.7122222222, 41.2691666667, None) == single
        assert identity.geometry_key(
            20.75, -12.5, 23.4, rows=3, cols=2, overlap=0.15,
            fov_x=2.0, fov_y=1.33) == grid

    def test_group_and_target_ids(self):
        ns = identity.NS_FLOWS
        g = uuid.uuid5(ns, "flow-golden/t1/7aa8577027b1741c").hex
        assert g == "1f2f390ffcce54acbef8aa42fe06103e"
        assert identity.group_id("flow-golden", "t1", "7aa8577027b1741c") == g
        t = uuid.uuid5(ns, f"{g}/r0c0").hex
        assert t == "be28a6f009bc59fa9bc73e86f46aa004"
        assert identity.target_id(g, 0, 0) == t
        # A panel of a grid, for S3: row and column are 0-based.
        g2 = identity.group_id("flow-golden", "t2", "1857d50ead89e1b1")
        assert g2 == "0bede77291585bec9ef7845f076fbe13"
        assert identity.target_id(g2, 2, 1) == \
            "9eff1d57c76f5fd2867f6bbf286fc856" == \
            uuid.uuid5(ns, f"{g2}/r2c1").hex

    def test_member_ids(self):
        """The first copy of a name carries no suffix; the second is "#1"."""
        ns = identity.NS_FLOWS
        assert identity.member_id("flow-golden", "p1", "M31") == \
            "b694b516aaca52459c0765131afedecd" == \
            uuid.uuid5(ns, "flow-golden/p1/member/M31").hex
        assert identity.member_id("flow-golden", "p1", "M31", 1) == \
            "4dabb622a7ce5d30a4d9273b2f445684" == \
            uuid.uuid5(ns, "flow-golden/p1/member/M31#1").hex

    def test_step_ids(self):
        """``{exposure:g}`` for the numbers, and no filter reads as empty.

        Mutant "filter None spelled 'None'" (``str(filter)``) failed the third
        assertion:
            AssertionError: assert 'ec8914d5b68d...29b7add1fbeab' ==
            'edb4f5635d8f...fce3fbea93398'
        """
        ns = identity.NS_FLOWS
        t = "be28a6f009bc59fa9bc73e86f46aa004"
        recipe = dict(frame_type="Light", filter="Ha", exposure_s=180.0,
                      gain=100, binning=1)
        assert identity.step_id(t, "c1", **recipe) == \
            "310157f4930b524d83329d5bb10fe0a3" == \
            uuid.uuid5(ns, f"{t}/c1/Light/Ha/180/100/1/0").hex
        assert identity.step_id(t, "c1", **recipe, n=1) == \
            "a2637359d9c25aa78b4d543d7c23b589" == \
            uuid.uuid5(ns, f"{t}/c1/Light/Ha/180/100/1/1").hex
        assert identity.step_id(t, "c1", frame_type="Light", filter=None,
                                exposure_s=0.5, gain=100, binning=2) == \
            "edb4f5635d8f5fc5ac1fce3fbea93398" == \
            uuid.uuid5(ns, f"{t}/c1/Light//0.5/100/2/0").hex

    def test_a_number_and_its_spelling_are_one_recipe(self):
        """180, 180.0 and "180" are one exposure. The compile hands ints, a
        hand-edited plan may hand floats or strings, and one recipe must not
        read as two just because of how it was typed.

        Mutant "numbers keyed as typed" (``_g`` returns ``str(value).strip()``
        in place of ``format(float(value), "g")``) failed:
            AssertionError: assert 'd1cb484e1414...a93e1edf3b8b3' ==
            '2b69706b1511...f365332892b8e'
        """
        t = "be28a6f009bc59fa9bc73e86f46aa004"
        a = identity.step_id(t, "c", frame_type="Light", filter="L",
                             exposure_s=180, gain=100, binning=1)
        b = identity.step_id(t, "c", frame_type="Light", filter="L",
                             exposure_s="180", gain=100.0, binning="1")
        assert a == b


class TestGeometryNormalisation:
    """Every float in the key is a fixed-precision STRING, so a value that
    round-trips through JSON, a browser or a different parser keys the same."""

    def test_the_single_target_defaults_are_written_out(self):
        """S1 keys a TARGET as a 1x1 grid with the node's created values
        (rows = cols = 1, overlap 0.25, no camera field). S3 reads those same
        values from missing keys, so a flow saved today keys the same after S3.

        Mutant "SINGLE_OVERLAP = 0.2" failed (the default call against the
        explicit 0.25 call):
            AssertionError: assert '02fefc9ca0c9892b' == '8932f51dc95a5c71'
        """
        default = identity.geometry_key(5.5, -5.4, None)
        written = identity.geometry_key(5.5, -5.4, None, rows=1, cols=1,
                                        overlap=0.25, fov_x=0.0, fov_y=0.0)
        assert default == written
        assert '"overlap":"0.2500"' in identity.canonical_geometry(5.5, -5.4, None)

    def test_negative_zero_is_folded(self):
        """-0.0 prints as "-0.000000", and so does any negative that rounds to
        zero. Both are zero, and a key that told them apart would re-key a
        target on the equator for a sign bit.

        Mutant "no -0 fold" (``fixed`` returns the formatted text as is)
        failed:
            assert '"dec_deg":"0.000000"' in '{"cols":1,"dec_deg":"-0.000000",
            "fov_x":"0.00000","fov_y":"0.00000","overlap":"0.2500",
            "ra_hours":"1.000000","rotation_deg":null,"rows":1}'
        """
        for dec in (-0.0, -1e-9):
            text = identity.canonical_geometry(1.0, dec, None)
            assert '"dec_deg":"0.000000"' in text, text
            assert identity.geometry_key(1.0, dec, None) == \
                identity.geometry_key(1.0, 0.0, None)

    @pytest.mark.parametrize("field, base, same, differ", [
        ("ra_hours", 1.0, 1.0 + 4e-7, 1.0 + 2e-6),          # 1e-6 h
        ("dec_deg", 10.0, 10.0 + 4e-7, 10.0 + 2e-6),        # 1e-6 deg
        ("rotation_deg", 10.0, 10.0004, 10.002),            # 1e-3 deg
        ("overlap", 0.25, 0.25004, 0.2502),                 # 1e-4
        ("fov_x", 1.0, 1.000004, 1.00002),                  # 1e-5 deg
        ("fov_y", 1.0, 1.000004, 1.00002),
    ])
    def test_each_field_keys_to_its_stated_precision(self, field, base, same,
                                                     differ):
        """Below the stated precision a value is the same geometry; above it,
        a different one. Both halves, so neither a coarser rounding nor none
        at all passes.

        Mutant "repr instead of fixed precision" (``fixed`` returns
        ``repr(float(value))``) failed all six rows on the same half:
            AssertionError: ra_hours: 1.0000004 must key as 1.0
            AssertionError: dec_deg: 10.0000004 must key as 10.0
            AssertionError: rotation_deg: 10.0004 must key as 10.0
            AssertionError: overlap: 0.25004 must key as 0.25
            AssertionError: fov_x: 1.000004 must key as 1.0
            AssertionError: fov_y: 1.000004 must key as 1.0
        Mutant "RA_PLACES = 3" failed the ra_hours row on the differ half:
            AssertionError: ra_hours: 1.000002 must key apart from 1.0
            assert '79e95c064a55afc5' != '79e95c064a55afc5'
        """
        def key(value):
            geo = {"ra_hours": 5.0, "dec_deg": 20.0, "rotation_deg": 30.0,
                   "overlap": 0.25, "fov_x": 1.5, "fov_y": 1.0, field: value}
            return identity.geometry_key(
                geo["ra_hours"], geo["dec_deg"], geo["rotation_deg"],
                overlap=geo["overlap"], fov_x=geo["fov_x"], fov_y=geo["fov_y"])
        assert key(same) == key(base), f"{field}: {same!r} must key as {base!r}"
        assert key(differ) != key(base), \
            f"{field}: {differ!r} must key apart from {base!r}"

    def test_any_negative_angle_is_one_geometry_and_zero_is_a_real_one(self):
        """``to_plan`` reads every negative rotation as "any angle" and 0 as
        north up (#150), so the key has to agree: -1 and -5 are one geometry,
        and PA 0 is a different one from no angle at all.

        Mutant "negatives formatted like any other angle" (only None is null)
        failed on -1:
            AssertionError: assert '83cd8300f35b6481' == '1040593087edc594'
        Mutant "falsy rotation is no angle" (``None if not rotation_deg``)
        failed the last assertion, PA 0 keyed as "any angle":
            AssertionError: assert '1040593087edc594' != '1040593087edc594'
             +  where '1040593087edc594' = <function geometry_key ...>(3.0, 30.0, 0)
        """
        anyk = identity.geometry_key(3.0, 30.0, None)
        assert identity.geometry_key(3.0, 30.0, -1) == anyk
        assert identity.geometry_key(3.0, 30.0, -5) == anyk
        assert identity.geometry_key(3.0, 30.0, 0) != anyk

    def test_angles_and_right_ascension_wrap(self):
        """PA 360 is PA 0, and an RA a hair under 24 h rounds to 24.000000,
        which is 0 h. Without the wrap one field has two keys either side of
        the seam.

        Mutant "no RA wrap" (``fixed`` in place of ``_wrapped`` for RA)
        failed:
            assert '"ra_hours":"0.000000"' in '{"cols":1,"dec_deg":"30.000000",
            "fov_x":"0.00000","fov_y":"0.00000","overlap":"0.2500",
            "ra_hours":"24.000000","rotation_deg":"0.000","rows":1}'
        """
        zero = identity.geometry_key(0.0, 30.0, 0.0)
        assert identity.geometry_key(0.0, 30.0, 360.0) == zero
        assert identity.geometry_key(0.0, 30.0, 359.9999) == zero
        text = identity.canonical_geometry(24.0 - 1e-9, 30.0, 0.0)
        assert '"ra_hours":"0.000000"' in text, text
        assert identity.geometry_key(24.0 - 1e-9, 30.0, 0.0) == zero


class TestContinuity:
    def test_two_compiles_of_one_flow_give_the_same_ids(self):
        """THE CONTINUITY TEST: night two names the same targets and steps as
        night one, so the ledger's banked frames count.

        Mutant "uuid4" (``identity._id`` returns ``uuid.uuid4().hex``) failed:
            AssertionError: assert [('ae31d57aef...71982d1325'])] ==
            [('7fbb4f2e4e...05a354bc17'])]
        """
        g = _graph()
        first, second = _plan(g, "flow-a"), _plan(g, "flow-a")
        assert _ids(first) == _ids(second)
        assert len(first.targets[0].steps) == 4, "premise: capture + 3 slots"
        # uuid5, not a stable accident: every id is name-based.
        assert {_version(i) for i in _all_ids(first)} == {5}
        assert plan_identity_errors(first) == []

    def test_the_ids_are_the_spec_recipe_over_the_compiled_nodes(self):
        """End to end: the TARGET's node id and its 1x1 geometry make the
        target id, and each step is keyed on the node of the STAGE that
        emitted it - the capture's for its step, the cycle's for every slot.

        Mutant "cycle slots lose the stage id" (``_cycle_steps`` stops
        carrying ``node_id``) failed:
            AssertionError: assert ['ef3e51a0053...fe0f7d661fea'] ==
            ['ef3e51a0053...3db5e54b9b45']
            At index 1 diff: '8305ccd575c25af2872472c638884fdc' !=
            '1193b40c0b4b550ab3b6bbb80f25dbc3'
        """
        from astrodeck.catalog.coords import parse_dec, parse_ra
        plan = _plan(_graph(), "flow-a")
        key = identity.geometry_key(parse_ra("00h 42m 44s"),
                                    parse_dec("+41 16 09"), None)
        tid = identity.target_id(identity.group_id("flow-a", "t", key))

        def sid(stage, filt, exposure):
            return identity.step_id(tid, stage, frame_type="Light",
                                    filter=filt, exposure_s=exposure,
                                    gain=100, binning=1)
        (got_tid, got_sids), = _ids(plan)
        assert got_tid == tid
        assert got_sids == [sid("c", "Ha", 120), sid("y", "L", 60),
                            sid("y", "R", 60), sid("y", "G", 60)]


class TestWhatAnEditDoesToTheIds:
    def test_an_exposure_change_changes_only_that_steps_id(self):
        """#77: two recipes must never share a count, so R at 90 s is a new
        step. Everything else - the target and the other three steps - keeps
        its id and its banked frames.

        Mutant "exposure dropped from the step recipe" failed:
            AssertionError: R 60 -> R 90 kept its id
            assert 'd85692a94ca850a88c43386c7f29a23c' !=
            'd85692a94ca850a88c43386c7f29a23c'
        Mutant "occurrence counted on exposure alone" (``n`` keyed on
        ``(stage, exposure_s)`` instead of the whole recipe) failed on G,
        whose n moved from 2 to 1 when R left the 60 s bucket:
            AssertionError: assert ['ef3e51a0053...d24b0c7e0e0a'] ==
            ['ef3e51a0053...e4bd7728dc91']
            At index 2 diff: '768b9dc13e975b2a977bd24b0c7e0e0a' !=
            'be9d8959737d537bb24ae4bd7728dc91'
        """
        before = _ids(_plan(_graph(plan="L 60, R 60, G 60"), "flow-a"))
        after = _ids(_plan(_graph(plan="L 60, R 90, G 60"), "flow-a"))
        (t0, [cap0, l0, r0, g0]), = before
        (t1, [cap1, l1, r1, g1]), = after
        assert t1 == t0
        assert r1 != r0, "R 60 -> R 90 kept its id"
        assert [cap1, l1, g1] == [cap0, l0, g0]

    @pytest.mark.parametrize("edit", [{"cap_gain": 200}, {"cap_bin": "2"}])
    def test_a_gain_or_binning_change_changes_only_that_steps_id(self, edit):
        """Spec 3.3: "an exposure, gain or binning change starts a new count"
        (#77). The golden vectors pin gain and binning inside ``step_id``; this
        pins that ``to_plan`` actually READS them off the compiled step, which
        no other test here does, because every other graph uses gain 100 and
        bin 1 - exactly the values a misread key would fall back to.

        Mutant "binning read from a misnamed key" (``_identify`` reads
        ``step.get("bin", 1)``) failed the binning row, and every other test in
        this file passed under it:
            AssertionError: {'cap_bin': '2'} kept the capture step's id
            assert 'ef3e51a0053e5fcc828f4dbfd39909d9' !=
            'ef3e51a0053e5fcc828f4dbfd39909d9'
        Mutant "gain left out of the recipe to_plan keys" (``gain=100``) failed
        the gain row:
            AssertionError: {'cap_gain': 200} kept the capture step's id
            assert 'ef3e51a0053e5fcc828f4dbfd39909d9' !=
            'ef3e51a0053e5fcc828f4dbfd39909d9'
        """
        (t0, [cap0, *slots0]), = _ids(_plan(_graph(), "flow-a"))
        (t1, [cap1, *slots1]), = _ids(_plan(_graph(**edit), "flow-a"))
        assert t1 == t0
        assert cap1 != cap0, f"{edit} kept the capture step's id"
        assert slots1 == slots0, f"{edit} on the capture re-keyed the cycle"

    @pytest.mark.parametrize("edit", [{"count": 25}, {"cycles": 45},
                                      {"per_cycle": 2}])
    def test_a_count_or_cycles_change_changes_no_id(self, edit):
        """Raising a count is "shoot more of the same" and must keep what is
        banked. Count, cycles and per-cycle are the three knobs that set how
        many; none of them is identity.

        Mutant "count in the step id" (the step's count folded into the
        stage part of its key) failed every row:
            AssertionError: {'count': 25} changed an id
            AssertionError: {'cycles': 45} changed an id
            AssertionError: {'per_cycle': 2} changed an id
        """
        assert _ids(_plan(_graph(**edit), "flow-a")) == \
            _ids(_plan(_graph(), "flow-a")), f"{edit} changed an id"

    def test_a_different_flow_id_gives_different_ids(self):
        """A duplicated flow is a new campaign with its own ledger; sharing
        ids would let one credit the other's frames.

        Mutant "flow id dropped from the group key" failed, the target id and
        all four step ids shared:
            AssertionError: assert {'226d155af44...897ee4170f5b'} == set()
            Extra items in the left set: (five ids)
        """
        a, b = _plan(_graph(), "flow-a"), _plan(_graph(), "flow-b")
        assert _all_ids(a) & _all_ids(b) == set()

    @pytest.mark.parametrize("edit", [{"ra": "01h 42m 44s"},
                                      {"dec": "+42 16 09"},
                                      {"rotation": 30}])
    def test_moving_the_target_gives_new_ids(self, edit):
        """A target moved elsewhere is a different field, and crediting the
        old field's frames to it is the flaw D5 removes. S1 has no anchor, so
        EVERY move re-keys; the carry of a small nudge lands with the anchor in
        S3 (``reframe_carry``).

        Mutant "geometry left out of the group key" (``group_id(flow, node,
        "")``) failed every row:
            AssertionError: {'ra': '01h 42m 44s'} kept an id
            AssertionError: {'dec': '+42 16 09'} kept an id
            AssertionError: {'rotation': 30} kept an id
        """
        before, after = _plan(_graph(), "flow-a"), _plan(_graph(**edit), "flow-a")
        assert _all_ids(before) & _all_ids(after) == set(), f"{edit} kept an id"

    def test_identical_recipes_from_two_stages_differ_by_the_stage(self):
        """Two CAPTURE nodes shooting the same Ha 120 s on one target are two
        quotas the operator drew separately. One id would count every frame
        twice, and ``plan_identity_errors`` would refuse the run.

        Mutant "stage node id left out of the step id" failed:
            AssertionError: two stages with one recipe share an id
            assert '8a737fd973ee5acda5d04b17794f3659' !=
            '8a737fd973ee5acda5d04b17794f3659'
        """
        cap = dict(filter="Ha", exposure=120, gain=100, bin="1", count=5, goal=0)
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09", rotation=-1),
                   _n("c1", "capture", x=100, **cap),
                   _n("c2", "capture", x=200, **cap)],
            edges=[_e("t", "target", "c1", "run"),
                   _e("c1", "complete", "c2", "run")])
        plan = _plan(g, "flow-a")
        a, b = [s.id for s in plan.targets[0].steps]
        assert a != b, "two stages with one recipe share an id"
        assert plan_identity_errors(plan) == []

    def test_two_identical_slots_in_one_cycle_differ_by_n(self):
        """"L 60, L 60" is legal and means two L slots per pass. Inside one
        stage the only thing telling them apart is their occurrence index n,
        which must also be stable from compile to compile.

        Mutant "n pinned at 0" failed:
            AssertionError: two L 60 slots share an id
            assert '1193b40c0b4b550ab3b6bbb80f25dbc3' !=
            '1193b40c0b4b550ab3b6bbb80f25dbc3'
        """
        g = _graph(plan="L 60, L 60")
        plan = _plan(g, "flow-a")
        _cap, first, second = [s.id for s in plan.targets[0].steps]
        assert first != second, "two L 60 slots share an id"
        assert plan_identity_errors(plan) == []
        assert _ids(_plan(g, "flow-a")) == _ids(plan)


class TestPools:
    def _pool(self, members):
        return FlowGraph(
            nodes=[_n("p", "pool", members=members, minAlt=0, moonSep=0,
                      maxHA=0),
                   _n("c", "capture", x=100, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0)],
            edges=[_e("p", "target", "c", "run")])

    def test_a_repeated_member_name_stays_unique(self):
        """"M31, M31" is two members. Keyed on the name alone they would share
        a target id and every step id, and the run would be refused.

        Mutant "no occurrence suffix" (every copy keyed as occurrence 0)
        failed:
            assert ["target id '...tep id alone"] == []
            Left contains 2 more items, first extra item: "target id
            '9fbb53ce6f815f4290605ef5e8337fa7' is used by 2 targets ('M31', 'M31')"
        """
        plan = _plan(self._pool("M31, M31"), "flow-a")
        assert [t.name for t in plan.targets] == ["M31", "M31"]
        assert plan_identity_errors(plan) == []
        first, second = _ids(plan)
        assert first[0] != second[0]
        # The first copy is keyed exactly as a lone M31 would be, so adding a
        # second copy later does not re-key the one with frames banked.
        lone, = _ids(_plan(self._pool("M31"), "flow-a"))
        assert first == lone

    def test_a_name_that_looks_like_a_suffix_cannot_collide(self):
        """A member literally named "M31#1" beside two M31s would collide with
        the second copy's suffix under a plain occurrence count. The compile
        cannot produce it from the catalogue, so a hand-built compiled dict
        stands in.

        Mutant "plain occurrence count" (suffix = copies of this name seen so
        far, no check against keys already used) failed:
            assert ["target id '...tep id alone"] == []
            Left contains 2 more items, first extra item: "target id
            'ba298f93dde250ebb2665976db487e42' is used by 2 targets ('M31', 'M31#1')"
        """
        def member(name, rank):
            return {"name": name, "pool_rank": rank, "node_id": "p",
                    "ra": "00h 42m 44s", "dec": "+41 16 09",
                    "steps": [{"filter": "L", "exposure_s": 60, "gain": 100,
                               "binning": 1, "count": 5,
                               "frame_type": "Light", "node_id": "c"}]}
        compiled = {"name": "n", "schedule": {"start_mode": "now"},
                    "targets": [member("M31", 1), member("M31", 2),
                                member("M31#1", 3)],
                    "automation": {}, "instructions": []}
        plan, _ = to_sequence_plan(compiled, flow_id="flow-a")
        assert plan_identity_errors(plan) == []

    def test_a_member_keeps_its_id_when_the_pool_is_reordered(self):
        """A member is keyed on its name, not its rank: dragging M42 above
        M31 in the members box must not throw away either one's frames.

        Mutant "member keyed on its rank" failed, each name taking the
        other's id:
            AssertionError: assert {'M31': ('ec0...f24b6e9619'])} ==
            {'M31': ('d8b...8044393480'])}
        """
        def by_name(members):
            plan = _plan(self._pool(members), "flow-a")
            return {t.name: (t.id, [s.id for s in t.steps])
                    for t in plan.targets}
        assert by_name("M31, M42") == by_name("M42, M31")

    def test_one_pool_does_not_re_key_another(self):
        """The occurrence suffix is counted PER POOL NODE. Two pools in one
        flow that both hold M31 are keyed apart by their node ids already, so
        the second pool's M31 is its own first copy. Counted across pools, an
        edit to the first pool (adding or removing its M31) would move the
        second pool's M31 between "M31" and "M31#1" and orphan its frames.

        Mutant "one members_seen set for every pool" (``members_seen
        .setdefault('', set())``) passed every other test in this file and
        failed here:
            AssertionError: an edit to the first pool re-keyed the second
            pool's M31
            assert ('c43cbda8de8...5ee0fa77d39']) == ('0cb4d5b63f0...577db60a16c'])
            At index 0 diff: 'c43cbda8de885a0196f5e9cc45a5538d' !=
            '0cb4d5b63f015e2aad1518788b2c926f'
        """
        def graph(first_pool):
            common = dict(minAlt=0, moonSep=0, maxHA=0)
            return FlowGraph(
                nodes=[_n("p1", "pool", x=0, members=first_pool, **common),
                       _n("p2", "pool", x=50, members="M31", **common),
                       _n("c", "capture", x=100, filter="L", exposure=60,
                          gain=100, bin="1", count=5, goal=0)],
                edges=[_e("p1", "target", "c", "run")])
        with_m31 = _plan(graph("M31, M42"), "flow-a")
        without = _plan(graph("M42"), "flow-a")
        assert [t.name for t in with_m31.targets] == ["M31", "M42", "M31"], \
            "premise: the second pool's member comes last"
        assert plan_identity_errors(with_m31) == []
        assert _ids(with_m31)[-1] == _ids(without)[-1], \
            "an edit to the first pool re-keyed the second pool's M31"


class TestTheNodeIdIsTheKey:
    def test_two_target_nodes_on_one_field_are_two_targets(self):
        """Two TARGET blocks with the same coordinates are two things the
        operator drew, each with its own quota. The compile's ``node_id`` is
        what keeps them apart.

        Mutant "to_plan keys on '' instead of the entry's node_id" failed:
            assert ["target id '...tep id alone"] == []
            Left contains 2 more items, first extra item: "target id
            '0a8f55f6f1605011932c1c8d0337d978' is used by 2 targets ('M31', 'M31')"
        """
        coords = dict(name="M31", ra="00h 42m 44s", dec="+41 16 09", rotation=-1)
        g = FlowGraph(
            nodes=[_n("t1", "target", x=0, **coords),
                   _n("t2", "target", x=50, **coords),
                   _n("c", "capture", x=200, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0)],
            edges=[_e("t1", "target", "c", "run")])
        plan = _plan(g, "flow-a")
        assert len(plan.targets) == 2
        assert plan_identity_errors(plan) == []

    def test_an_entry_with_no_node_id_keeps_a_random_id(self):
        """A compiled dict built by hand (or by a caller older than S1) has no
        node id to key on. Keying on "" would give two such entries on one
        field the same id and refuse the run, so they keep uuid4 as before.

        Mutant "key on an empty node id" (the guard tests ``flow_id`` only)
        failed:
            assert ["target id '...tep id alone"] == []
            Left contains 2 more items, first extra item: "target id
            '44023921030d5fd0a06b41f4675babbd' is used by 2 targets ('A', 'A')"
        """
        entry = {"name": "A", "ra": "01h 00m 00s", "dec": "+10 00 00",
                 "rotation_deg": -1,
                 "steps": [{"filter": "L", "exposure_s": 60, "gain": 100,
                            "binning": 1, "count": 5, "frame_type": "Light"}]}
        compiled = {"name": "n", "schedule": {"start_mode": "now"},
                    "targets": [dict(entry), dict(entry)], "automation": {},
                    "instructions": []}
        plan, _ = to_sequence_plan(compiled, flow_id="flow-a")
        assert plan_identity_errors(plan) == []
        assert {_version(t.id) for t in plan.targets} == {4}


class TestControls:
    """What must NOT change. Each fails only if the fix spreads."""

    def _with_a_rule(self):
        g = _graph()
        return FlowGraph(
            nodes=[*g.nodes,
                   _n("k", "condition", x=400, when="HFR above", threshold=3.2),
                   _n("r", "refocus", x=500)],
            edges=[*g.edges, _e("k", "fire", "r", "do")])

    def test_instruction_ids_stay_uuid4(self):
        """The ledger counts frames, not rules, so a rule has nothing to
        continue and S1 leaves its id alone.

        Mutant "instructions keyed too" (``to_sequence_plan`` gives each
        instruction ``uuid5(NS_FLOWS, f"{flow_id}/rule/{i}")``) failed:
            AssertionError: assert '7578d4d2ff1e5802bcbc0abf24a49735' !=
            '7578d4d2ff1e5802bcbc0abf24a49735'
        """
        g = self._with_a_rule()
        a, b = _plan(g, "flow-a"), _plan(g, "flow-a")
        assert len(a.instructions) == 1, "premise: the rule reaches the plan"
        assert a.instructions[0].id != b.instructions[0].id
        assert _version(a.instructions[0].id) == 4
        assert _ids(a) == _ids(b), "premise: the targets ARE keyed"

    def test_without_a_flow_id_two_compiles_differ(self):
        """An unsaved preview has no flow to continue: its ids stay uuid4, so
        a preview can never collide with a saved flow's ledger.

        Mutant "no flow_id guard" (ids assigned whenever a node id exists)
        failed, every id shared between the two compiles:
            AssertionError: assert {'36f667be20b...de97857b9d72'} == set()
        """
        a, b = _plan(_graph()), _plan(_graph())
        assert _all_ids(a) & _all_ids(b) == set()
        assert {_version(i) for i in _all_ids(a)} == {4}


class TestTheShippedExamples:
    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_example_keys_uniquely_and_the_same_twice(self, ex):
        """The acceptance corpus again: each example, keyed by its own id,
        passes the start-path identity check and compiles to the same ids
        twice. The pool example is the one with several members.

        Mutant "member id ignores the name" (``identity.member_id`` keyed on
        the flow and the pool node only) failed example-pool and
        example-campaign:
            Left contains 8 more items, first extra item: "target id
            'b1cf078c1bab5c81882ace802442b770' is used by 4 targets ('M33',
            'NGC 7331', 'IC 1396', 'M45')"
            Left contains 2 more items, first extra item: "target id
            'b08de1022f3651ef89ddd3a1d16a3118' is used by 4 targets ('M16',
            'M17', 'M8', 'NGC 6946')"
        """
        first, second = _plan(ex.graph, ex.id), _plan(ex.graph, ex.id)
        assert plan_identity_errors(first) == []
        assert _ids(first) == _ids(second)
