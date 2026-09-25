"""ADOPT carries a pre-S1 step's frames only onto the SAME FIELD (#189 A4, spec
5.9, D5; #190).

ADOPT re-keys a pre-S1 session's frames onto tonight's compile by matching a
step on (target name, frame type, filter, exposure, gain, binning). The name
is a label, not a place. #190 is the case that proves it: the guided wizard
wrote the typed name "M16" onto M31's coordinates, so a session filed under
"M16" holds frames of Andromeda. Once the flow is corrected to M16's real
coordinates, name and recipe still match, and ADOPT credited the Andromeda
frames to M16 - 103 degrees away. That is the flaw D5 removes for a re-frame,
reached through the one door S1 left open.

So a key match counts only when the two targets are within
``ADOPT_MAX_SEPARATION_ARCMIN`` (10 arcmin, inclusive) of each other on the
sky, by great circle, RA in hours. A match further apart is listed as
unmatched, with the separation in its reason and in ``separation_arcmin``, and
nothing is re-keyed.

A MOVING BODY IS MEASURED WHERE IT WAS (#229, #234). A body leaves the 10
arcmin bound behind in days (median daily motion from the shipped ephemeris,
sampled over two years from September 2026: Mars 39 arcmin, Jupiter 7.4,
Neptune 1.4; the Moon some 13 deg), so measured against tonight's target the
bound refused a pre-S1 session of one as soon as the body had moved on. A
name the shared resolver (``tonight.resolve_target``) finds as a moving row
is matched on its canonical body name, in any case, and tonight's position
is not compared; the old target is held to the bound against the body at the
instants its frames were taken instead (H3 orchestrator ruling 7, whose own
tests are ``test_flows_adopt_body_pointing.py``). So the body cases here
carry capture times. Deep-sky names keep the bound and the name as typed.

Every test names the mutation of ``flows/continuation.py`` (or of
``flows/tonight.py``, for the resolver) it guards and quotes the failure it
produced, run from a byte backup of the file and restored byte-identical
after. The separation mutants below ("no coordinate check", "separation left
in degrees", "RA read as degrees", "flat RA difference") were first run on the
law of cosines, and run again after H2 moved the separation to the atan2 form:
each failed the same tests with the same numbers. "No coordinate check" and
"'<=' to '<'" were run a third time on H3's restructured match (#234), and
failed the same tests in the same way. The existing ADOPT tests in
``test_flows_continue.py`` all use sessions within 10 arcmin of their compile,
and stay green.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.flows import continuation
from astrodeck.flows.continuation import (ADOPT_MAX_SEPARATION_ARCMIN,
                                          AMBIGUOUS, NO_MATCH, adopt_matches,
                                          apply_adoption)
from astrodeck.flows.tonight import NameResolution
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_flows_continue import _bytes, _compiled, _stored, rig  # noqa: F401


def _step(filt="L", exposure=60.0, **kw):
    return ExposureStep(filter=filt, exposure_s=exposure, count=5, **kw)


#: When the body cases' frames were taken: 2026-09-10 04:00 UTC.
CAPTURE_TS = 1_789_012_800.0


def _session(ra, dec, steps, frames_on, *, name="M16", ts=0.0):
    """A pre-S1 session: one target named ``name`` at (``ra`` h, ``dec``
    deg), uuid4 ids, and ``frames_on[i]`` frames on step ``i``, each taken
    at ``ts`` on one night (0.0, never stamped, unless a case needs a
    capture time)."""
    t = Target(name=name, ra_hours=ra, dec_deg=dec, steps=steps)
    s = Session(status="dormant", plan=SequencePlan(name="p", targets=[t]))
    for i, n in frames_on.items():
        s.frames.extend(SessionFrame(target_id=t.id, step_id=steps[i].id,
                                     night="n1", ts=ts)
                        for _ in range(n))
    return s


def _plan(ra, dec, *steps, name="M16"):
    return SequencePlan(targets=[Target(name=name, ra_hours=ra, dec_deg=dec,
                                        steps=list(steps))])


def _ra_offset_h(arcmin, dec):
    """The RA step, in hours, that is ``arcmin`` of great circle at ``dec``
    (small-angle, which at these sizes is exact to under 0.001 arcmin; each
    test checks the premise against the separation ADOPT reports)."""
    return arcmin / 60.0 / 15.0 / math.cos(math.radians(dec))


def test_the_limit_is_ten_arcmin():
    assert ADOPT_MAX_SEPARATION_ARCMIN == 10.0


class TestSameNameElsewhere:
    def test_a_match_25_arcmin_away_is_listed_and_nothing_is_rekeyed(self):
        """Same name, same recipe, 25 arcmin of sky apart: not the same field.
        Listed with the separation, mapped to nothing, and apply_adoption
        moves no frame.

        Mutant "no coordinate check" (the separation gate removed from
        ``adopt_matches``, every unique match mapped as in S1) failed:
            AssertionError: assert ({'4c9c647bfc1...954879007a9')} == {}
              Left contains 1 more item:
              {'4c9c647bfc1a4c26a98db7ab16ded555': (
                  'b848f89d4cfd4cc59f45b83aac6803d0',
                  '4c11b204ccf24eef93122954879007a9')}
        Mutants "separation left in degrees" and "RA read as degrees" (below)
        failed here the same way; "flat RA difference" failed at the reported
        separation:
            assert 26.604 == 25.0 ± 0.01
        """
        old = _step("L")
        s = _session(5.0, 20.0, [old], {0: 3})
        plan = _plan(5.0 + _ra_offset_h(25.0, 20.0), 20.0, _step("L"))
        before = [(f.target_id, f.step_id) for f in s.frames]

        m = adopt_matches(s, plan)

        assert m.mapping == {} and m.frames_matched == 0
        assert m.ambiguous == []
        entry, = m.unmatched
        assert entry["step_id"] == old.id and entry["frames"] == 3
        assert entry["separation_arcmin"] == pytest.approx(25.0, abs=0.01)
        assert "25.0 arcmin" in entry["reason"], entry["reason"]
        assert "10 arcmin" in entry["reason"], entry["reason"]
        assert apply_adoption(s, m) == 0
        assert [(f.target_id, f.step_id) for f in s.frames] == before
        assert s.plan.targets[0].steps[0].id == old.id

    @pytest.mark.parametrize("axis", ["ra", "dec"])
    @pytest.mark.parametrize("arcmin, maps", [(9.9, True), (10.1, False)])
    def test_the_boundary_on_each_axis(self, axis, arcmin, maps):
        """9.9 arcmin maps and 10.1 does not, whether the offset is in RA or
        in Dec. At Dec 20 an RA offset is in hours and shrinks by cos(dec), so
        a mistake in either unit lands on the wrong side of the line.

        Mutant "separation left in degrees" (the arcmin conversion dropped, so
        0.168 deg is compared with 10) failed both 10.1 rows:
            AssertionError: 10.1 arcmin in ra was adopted
            assert {'415429b24c8...f4894519570')} == {}
            AssertionError: 10.1 arcmin in dec was adopted
            assert {'0bb19e27433...020dd2c05e4')} == {}
        Mutant "RA read as degrees" (``ra_hours`` handed to the separation
        divided by 15, so an hour counts as a degree) failed the RA 10.1 row:
            AssertionError: 10.1 arcmin in ra was adopted
            assert {'a69de7a7aed...3cf7ee7e995')} == {}
        Mutant "flat RA difference" (``hypot(dRA x 15, dDec) x 60``, no
        cos(dec), no wrap) failed both RA rows, 9.9 arcmin read as 10.5 and
        10.1 as 10.7:
            AssertionError: [{'binning': 1, 'exposure_s': 60.0, 'filter': 'L',
            'frame_type': 'Light', ...}]
            assert [] == ['bba89f4deea...c2b803c572cd']
            assert 10.748 == 10.1 ± 0.001
        """
        dec = 20.0
        old = _step("L")
        s = _session(5.0, dec, [old], {0: 2})
        if axis == "ra":
            plan = _plan(5.0 + _ra_offset_h(arcmin, dec), dec, _step("L"))
        else:
            plan = _plan(5.0, dec + arcmin / 60.0, _step("L"))
        m = adopt_matches(s, plan)
        if maps:
            assert list(m.mapping) == [old.id], m.unmatched
            assert m.frames_matched == 2
        else:
            assert m.mapping == {}, f"{arcmin} arcmin in {axis} was adopted"
            entry, = m.unmatched
            assert entry["separation_arcmin"] == pytest.approx(arcmin,
                                                               abs=0.001)

    def test_right_ascension_wraps_at_24h(self):
        """23.998 h and 0.002 h are 3.4 arcmin apart at Dec 20, not 24 h.

        Mutant "flat RA difference" failed (and so did "RA read as
        degrees"):
            AssertionError: [{'binning': 1, 'exposure_s': 60.0, 'filter': 'L',
            'frame_type': 'Light', ...}]
            assert [] == ['d591d052586...cdf072b0e6dd']
        """
        old = _step("L")
        s = _session(23.998, 20.0, [old], {0: 1})
        m = adopt_matches(s, _plan(0.002, 20.0, _step("L")))
        assert list(m.mapping) == [old.id], m.unmatched

    def test_high_declination_shrinks_the_ra_step(self):
        """At Dec 80, 0.04 h of RA is 36 arcmin flat but 6.3 arcmin of sky:
        the same field. 0.08 h is 12.5 arcmin of sky and is not.

        Mutant "flat RA difference" failed the first half:
            AssertionError: assert [] == ['392c7a52de2...86f19f12ccaf']
        Mutants "no coordinate check", "separation left in degrees" and "RA
        read as degrees" failed the second half, 12.5 arcmin adopted (the
        first of them shown):
            AssertionError: assert {'c309f6da285...44c8b030de2')} == {}
        """
        near, far = _step("L"), _step("R")
        s = _session(3.0, 80.0, [near], {0: 1})
        assert list(adopt_matches(
            s, _plan(3.04, 80.0, _step("L"))).mapping) == [near.id]
        s2 = _session(3.0, 80.0, [far], {0: 1})
        m = adopt_matches(s2, _plan(3.08, 80.0, _step("R")))
        assert m.mapping == {}
        assert m.unmatched[0]["separation_arcmin"] == pytest.approx(12.5,
                                                                    abs=0.01)


class TestControls:
    def test_control_the_same_place_still_maps(self):
        """Identical coordinates map, as every S1 ADOPT did."""
        old = _step("L")
        s = _session(5.0, 20.0, [old], {0: 2})
        plan = _plan(5.0, 20.0, _step("L"))
        m = adopt_matches(s, plan)
        assert m.mapping == {old.id: (plan.targets[0].id,
                                      plan.targets[0].steps[0].id)}

    def test_control_a_frameless_moved_step_is_neither_listed_nor_mapped(self):
        """A step with no frames has nothing to carry: not put in front of
        the operator, and not re-keyed either.

        Mutant "frameless moved steps listed" (the moved branch lists a step
        whatever its frame count) failed:
            AssertionError: assert ['L', 'R'] == ['L']
              Left contains one more item: 'R'
        Mutant "no coordinate check" failed the other half, both mapped:
            AssertionError: assert {'bccd1ac2fb7...ee87fd6683d')} == {}
              Left contains 2 more items:
        """
        s = _session(5.0, 20.0, [_step("L"), _step("R")], {0: 1})
        m = adopt_matches(s, _plan(5.0, 21.0, _step("L"), _step("R")))
        assert m.mapping == {}
        assert [e["filter"] for e in m.unmatched] == ["L"]

    def test_control_the_other_reasons_carry_no_separation(self):
        """A step with no match, or with two, has no one target to measure
        against, so its ``separation_arcmin`` is null and its reason is the
        one it always was."""
        s = _session(5.0, 20.0, [_step("L"), _step("Ha")], {0: 1, 1: 1})
        m = adopt_matches(s, _plan(5.0, 20.0, _step("L"), _step("L")))
        assert [(e["filter"], e["reason"], e["separation_arcmin"])
                for e in m.rest()] == [("L", AMBIGUOUS, None),
                                       ("Ha", NO_MATCH, None)]


# ------------------------------------------------------------ the route (#190)

#: M16's real coordinates, and the M31 coordinates the wizard wrote under its
#: name (``nodes.py``'s TARGET default).
M16_RA, M16_DEC = "18h 18m 48s", "-13 49 00"
M31_RA_H, M31_DEC = 0.712222, 41.269167


def _m16_flow() -> dict:
    """TARGET "M16" at M16, feeding L then R captures."""
    def capture(nid, filt, count):
        return {"id": nid, "type": "capture", "x": 0, "y": 0,
                "params": {"filter": filt, "exposure": 0.05, "gain": 100,
                           "bin": "1", "count": count, "goal": 0}}
    nodes = [{"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": "M16", "ra": M16_RA, "dec": M16_DEC}},
             capture("c1", "L", 3), capture("c2", "R", 2)]
    edges = [{"from": "t", "fromPort": "target", "to": "c1", "toPort": "run"},
             {"from": "c1", "fromPort": "complete", "to": "c2",
              "toPort": "run"}]
    return {"nodes": nodes, "edges": edges}


async def test_m31_frames_filed_as_m16_are_never_adopted_onto_m16(rig):
    """#190's session: saved before S1 under the name "M16", its target at
    M31's coordinates, two L subs and one R sub banked. The flow now points
    at the real M16. Name and recipe match; the sky does not.

    The adopt question lists both steps as unmatched, 6210 arcmin away, and
    counts none as matched. ADOPT with ``accept_dropped`` then starts the
    session with every frame still on its own step: none counts toward
    tonight's M16.

    Mutant "no coordinate check" failed at the first answer, all three
    Andromeda subs offered to M16 as matched:
        AssertionError: {'frames': 3, 'matched': 3, 'session_id':
        '0fc1205b5493438fb9d8254e0d01cec1', 'unmatched': []}
        assert (3 == 3 and 3 == 0)
    Mutant "separation left in degrees" failed at the reported separation,
    the right distance in the wrong unit:
        assert 103.509 == 6210.6 ± 0.5
    """
    fid = await rig.save_flow(_m16_flow())
    old_l = ExposureStep(filter="L", exposure_s=0.05, count=3)
    old_r = ExposureStep(filter="R", exposure_s=0.05, count=2)
    t = Target(name="M16", ra_hours=M31_RA_H, dec_deg=M31_DEC,
               steps=[old_l, old_r])
    old = Session(name="pre-S1", created_ts=1.0, status="dormant",
                  auto_resume=True, origin="flow", origin_id=fid,
                  plan=SequencePlan(name="pre-S1", targets=[t]))
    for st, n in ((old_l, 2), (old_r, 1)):
        old.frames.extend(SessionFrame(target_id=t.id, step_id=st.id)
                          for _ in range(n))
    _stored(old)
    original = _bytes(old.id)
    tonight = _compiled(_m16_flow(), fid)
    tonight_steps = {st.id for tt in tonight.targets for st in tt.steps}

    r = await rig.run(fid)

    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "adopt", body
    assert body["adopt"]["frames"] == 3 and body["adopt"]["matched"] == 0, \
        body["adopt"]
    listed = {u["step_id"]: u for u in body["adopt"]["unmatched"]}
    assert set(listed) == {old_l.id, old_r.id}
    for u in listed.values():
        assert u["separation_arcmin"] == pytest.approx(6210.6, abs=0.5), u
        assert "arcmin" in u["reason"], u
    assert _bytes(old.id) == original, "a refusal wrote the session"

    r = await rig.run(fid, adopt=True, accept_dropped=True)

    assert r.status_code == 200, r.text
    start = rig.starts[-1]
    assert start.won and start.session_id == old.id
    assert start.frames == [(t.id, old_l.id), (t.id, old_l.id),
                            (t.id, old_r.id)], start.frames
    assert r.json()["session"]["adopted"]["matched"] == 0
    assert not set(session_store.load(old.id).accepted_by_step()) \
        & tonight_steps, "Andromeda's frames count toward M16"


# ------------------------------------------------- the bound is inclusive

#: Two targets on one RA, the second 10/60 deg north of the first on the
#: equator. The atan2 separation of these two is EXACTLY 10.0 arcmin, which
#: the test asserts with ``==`` rather than assumes; the law of cosines
#: cannot produce 10.0 for any pair (``continuation._separation_arcmin``).
EXACT_RA, EXACT_DEC_OLD, EXACT_DEC_NEW = 5.0, 0.0, 10.0 / 60.0


class TestTheBoundIsInclusive:
    def test_a_match_exactly_ten_arcmin_away_maps(self):
        """Spec 5.9: the bound is inclusive. Two fields exactly
        ``ADOPT_MAX_SEPARATION_ARCMIN`` apart are the same field, and the
        frames are re-keyed.

        Mutant "'<=' to '<'" (``apart < ADOPT_MAX_SEPARATION_ARCMIN`` in
        ``adopt_matches``) failed:
            AssertionError: assert {} == {'b5174731285...37a8f84265c')}
              Right contains 1 more item:
              {'b5174731285241c6a452d6d44ea52a4c': (
                  '86dd9944bf074c29be03ba0892e2f7b0',
                  '0be02fb1aede48008bcab37a8f84265c')}
        Mutant "law of cosines" (``_separation_arcmin`` returns
        ``angular_sep_deg(...) * 60.0`` again) failed the premise, the
        separation 10.0 cannot be computed by it (and the control below
        failed its premise the same way; every other ADOPT test here and in
        test_flows_continue.py passed under it):
            AssertionError: premise: the separation is exactly 10.0
            (9.999999999955872)
            assert False
        """
        old = _step("L")
        s = _session(EXACT_RA, EXACT_DEC_OLD, [old], {0: 2})
        plan = _plan(EXACT_RA, EXACT_DEC_NEW, _step("L"))
        apart = continuation._separation_arcmin(s.plan.targets[0],
                                                plan.targets[0])
        exact = apart == ADOPT_MAX_SEPARATION_ARCMIN == 10.0
        assert exact, f"premise: the separation is exactly 10.0 ({apart!r})"
        m = adopt_matches(s, plan)
        assert m.mapping == {old.id: (plan.targets[0].id,
                                      plan.targets[0].steps[0].id)}
        assert m.frames_matched == 2 and m.unmatched == []

    def test_control_a_hair_past_ten_arcmin_is_unmatched(self):
        """The first representable Dec north of the exact pair whose
        separation is more than 10.0 arcmin (one step of Dec is too small to
        move 10.0 off itself, so the test walks north a step at a time) is
        not the same field: the bound is 10.0, not "about 10".

        Mutant "bound widened by a rounding tolerance" (``apart <=
        ADOPT_MAX_SEPARATION_ARCMIN + 1e-9``) failed:
            AssertionError: assert {'6691c6a41e1...e997548767b')} == {}
              Left contains 1 more item:
              {'6691c6a41e144864ac1114a265164704': (
                  '7e6e89acd12848b6bd4808c4a3e2bebd',
                  'c4ad98da4c9c43388387ce997548767b')}
        """
        old = _step("L")
        s = _session(EXACT_RA, EXACT_DEC_OLD, [old], {0: 2})
        dec, apart = EXACT_DEC_NEW, 10.0
        for _ in range(64):
            if apart > 10.0:
                break
            dec = math.nextafter(dec, math.inf)
            apart = continuation._separation_arcmin(
                s.plan.targets[0], Target(name="M16", ra_hours=EXACT_RA,
                                          dec_deg=dec))
        plan = _plan(EXACT_RA, dec, _step("L"))
        apart = continuation._separation_arcmin(s.plan.targets[0],
                                                plan.targets[0])
        hair = 10.0 < apart < 10.0 + 1e-12
        assert hair, f"premise: a hair past 10.0 arcmin ({apart!r})"
        m = adopt_matches(s, plan)
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["step_id"] == old.id


# ------------------------------------------------------- a moving body (#229)

def _catalogue(name, when=None):
    """A catalogue that knows three names, whatever their case: Jupiter and
    Mars, moving bodies, and M16, a fixed deep-sky row. A body is at (5.0 h,
    +20 deg), the old sessions' pointing, when their frames were taken
    (``CAPTURE_TS``), and at (5.0 h, +22 deg), tonight's, at every other
    instant; M16's position is never read."""
    dec = 20.0 if when == CAPTURE_TS else 22.0
    rows = {"jupiter": NameResolution(ra_hours=5.0, dec_deg=dec,
                                      identity="Jupiter", moves=True),
            "mars": NameResolution(ra_hours=5.0, dec_deg=dec,
                                   identity="Mars", moves=True),
            "m16": NameResolution(ra_hours=18.3, dec_deg=-13.8,
                                  identity="M16", moves=False)}
    return rows.get(str(name).strip().lower())


class TestAMovingBody:
    """A pre-S1 session of a planet or a comet is more than 10 arcmin from
    tonight's position once the body has moved on, which takes days
    (Jupiter's median daily motion is 7.4 arcmin), so the bound refused it
    (#229). A body is matched on its canonical name instead, and tonight's
    position is not compared; the old target is held to the bound against
    the body where it was when the frames were taken (#234). Every fixed row
    keeps the bound and the name as typed."""

    def test_a_body_two_degrees_away_typed_in_another_case_maps(self):
        """The session was shot as "jupiter" at (5.0 h, +20 deg), where
        Jupiter was at the time; tonight's "Jupiter" is at (5.0 h, +22 deg),
        120 arcmin away. It is the same body, so the frames are re-keyed.

        Mutant "the bound applies to bodies" (a body's old target measured
        against tonight's target, as H1 had it, in place of ``_pointing``;
        ``test_flows_adopt_body_pointing.py`` calls it "check against
        tonight's ephemeris instead of capture time") failed, re-run on H3's
        code with this case carrying its capture time:
            AssertionError: assert {} == {'ffdc626d8f8...b01b126701f')}
              Right contains 1 more item:
              {'ffdc626d8f894674b7548b661519fdcc': (
                  'db2bf22d650b4542835af3449625f143',
                  '539a38cac417450d86d0cb01b126701f')}
        Mutant "a body is keyed on its name as typed" (``_step_key`` keys
        ``target.name`` whatever ``body`` is) failed:
            AssertionError: assert {} == {'baa3948aa93...ce60b3782ef')}
              Right contains 1 more item:
              {'baa3948aa93240709b142cdea63300c0': (
                  '55535882d5d1478fa5e61a4921be9ca4',
                  'f3bab7b7c8c9403da896cce60b3782ef')}
        """
        old = _step("L")
        s = _session(5.0, 20.0, [old], {0: 3}, name="jupiter",
                     ts=CAPTURE_TS)
        plan = _plan(5.0, 22.0, _step("L"), name="Jupiter")
        apart = continuation._separation_arcmin(s.plan.targets[0],
                                                plan.targets[0])
        far = apart > 10 * ADOPT_MAX_SEPARATION_ARCMIN
        assert far, "premise: tonight's position is far past the bound"
        m = adopt_matches(s, plan, resolve=_catalogue)
        assert m.mapping == {old.id: (plan.targets[0].id,
                                      plan.targets[0].steps[0].id)}
        assert m.frames_matched == 3 and m.rest() == []

    def test_control_a_deep_sky_name_keeps_the_bound(self):
        """M16 is a fixed row, so the bound still holds for it with the same
        resolver that exempts Jupiter: 25 arcmin apart is listed, not mapped
        (the #190 case at the route stays green too:
        ``test_m31_frames_filed_as_m16_are_never_adopted_onto_m16``).

        Mutant "every resolved name is a body" (``_body`` returns the
        identity whether or not the row moves) failed. Re-run on H3's code,
        where a body is checked at its capture times and these frames carry
        none, M16 is listed as unplaced instead of measured:
            assert None == 25.0 ± 0.01
              comparison failed
              Obtained: None
              Expected: 25.0 ± 0.01
        and so did ten other tests in this file whose names the shipped
        catalogue resolves (every "M16" test, within the bound or beyond
        it), among them the #190 route test:
            AssertionError: {'binning': 1, 'exposure_s': 0.05, 'filter': 'L',
            'frame_type': 'Light', ...}
            assert None == 6210.6 ± 0.5
        """
        old = _step("L")
        s = _session(5.0, 20.0, [old], {0: 3}, name="M16")
        plan = _plan(5.0 + _ra_offset_h(25.0, 20.0), 20.0, _step("L"),
                     name="M16")
        m = adopt_matches(s, plan, resolve=_catalogue)
        assert m.mapping == {}
        entry, = m.unmatched
        assert entry["separation_arcmin"] == pytest.approx(25.0, abs=0.01)

    def test_control_two_different_bodies_do_not_match(self):
        """Mars frames are not Jupiter's: a body key carries the body's
        name, so two bodies are two keys.

        Mutant "a body key drops the name" (``_step_key`` writes
        ``("body",)`` for every body) failed. Re-run on H3's code, which
        reads the body's name back out of the key to check it at capture
        time, the key without one no longer maps; it raises:
            IndexError: tuple index out of range
        """
        s = _session(5.0, 20.0, [_step("L")], {0: 1}, name="Mars",
                     ts=CAPTURE_TS)
        m = adopt_matches(s, _plan(5.0, 22.0, _step("L"), name="Jupiter"),
                          resolve=_catalogue)
        assert m.mapping == {}
        assert [e["reason"] for e in m.unmatched] == [NO_MATCH]

    def test_the_default_resolver_is_the_shipped_catalogue(self):
        """With no ``resolve`` handed in, ADOPT asks ``tonight.resolve_target``,
        the resolver ``to_plan`` points a name with: the shipped catalogue
        says Jupiter moves and where it was at ``CAPTURE_TS``, where the old
        session pointed, so the route's ADOPT (``api/app.py`` passes no
        resolver) maps it across the sky too.

        Mutant "no resolver by default" (``adopt_evidence`` uses ``resolve
        or (lambda name, when=None: None)``) failed, re-run on H3's code:
            AssertionError: [{'binning': 1, 'exposure_s': 60.0, 'filter': 'L',
            'frame_type': 'Light', ...}]
            assert [] == ['60eb0ca1582...d77942aa960c']
              Right contains one more item: '60eb0ca1582146a3a79ed77942aa960c'
        Mutant "solar_system not a moving kind" (in ``tonight``) failed here
        the same way, and so did "the bound applies to bodies" and "a body is
        keyed on its name as typed".
        """
        from astrodeck.flows import tonight
        then = tonight.resolve_target("Jupiter", CAPTURE_TS)
        old = _step("L")
        s = _session(then.ra_hours, then.dec_deg, [old], {0: 1},
                     name="jupiter", ts=CAPTURE_TS)
        plan = _plan(5.0, 22.0, _step("L"), name="Jupiter")
        m = adopt_matches(s, plan)
        assert list(m.mapping) == [old.id], m.unmatched
